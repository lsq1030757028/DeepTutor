# -*- coding: utf-8 -*-
"""AC26 · 写风险 curl 先说明后确认（`free_surface_curl_write_confirm`）。

四个正例（POST / DELETE / 无 `-X` 但带 `-d` / 自定义方法）都必须出现说明 + 请一次
轻确认，且**确认前出站请求计数 = 0**；两个否例（用户沉默 / 答"随便"）出站计数
同样为 0。

否例在这里是**机械**判的，不是只靠提示词纪律：`bool("随便")` 在 Python 里是 True，
如果服务端拿 `confirmed` 直接当真值用，"答得含糊"就会被读成"用户同意了"。
`is_confirmed` 把这一刀挡在服务端，所以否例有可执行样例而不只有一句纪律。

证据③（只读方法白名单只有一处定义）由 `tests/test_journey_free_surface.py` 的
`test_write_risk_delegates_to_the_single_source_of_truth` 与
`test_all_safe_method_sets_in_the_repo_still_agree` 承担。
"""
from __future__ import annotations

import pytest

from server.journey import free_surface as fs


class _ExplodingOpener:
    def open(self, *args, **kwargs):
        raise AssertionError("确认前出站请求计数必须为 0")


def _exploding_resolver(host, port):
    raise AssertionError("确认前连 DNS 都不该发")


@pytest.fixture(autouse=True)
def observation_log(tmp_path, monkeypatch):
    monkeypatch.setenv(fs.OBSERVATION_LOG_ENV, str(tmp_path / "obs.jsonl"))


#: 四个正例，各是一种写风险形态。
WRITE_RISK_CURLS = [
    ("curl -X POST https://api.example.com/v1/orders -d '{\"sku\":1}'", "POST"),
    ("curl -X DELETE https://api.example.com/v1/orders/9", "DELETE"),
    ("curl https://api.example.com/v1/orders -d 'sku=1&qty=2'", "POST"),
    ("curl -X PURGE https://api.example.com/v1/cache", "PURGE"),
]


@pytest.mark.parametrize("curl_text,method", WRITE_RISK_CURLS)
def test_write_risk_curls_disclose_before_sending_anything(curl_text, method):
    result = fs.probe_http(curl=curl_text, resolver=_exploding_resolver,
                           opener=_ExplodingOpener())

    assert result["code"] == "REQUIRES_CONFIRM"
    assert result["requires_confirm"] is True
    disclosure = result["disclosure"]
    # 「向哪个 host 的哪个路径、发什么方法、带哪类 body」——三样都要在卡上。
    assert disclosure["host"] == "api.example.com"
    assert disclosure["path"].startswith("/v1/")
    assert disclosure["method"] == method
    assert disclosure["body_kind"]
    assert disclosure["why_risky"]
    # 说明里要写清"没同意就不发"，模型才知道下一步是 ask_user 而不是硬发或硬拒。
    assert "同意" in result["message"] and "不发" in result["message"]


@pytest.mark.parametrize("curl_text,_method", WRITE_RISK_CURLS)
@pytest.mark.parametrize("vague_answer", [
    "",          # 沉默
    "随便",       # 含糊
    "whatever",
    "maybe",
    "不确定",
    None,
    0,
])
def test_a_vague_answer_is_not_consent(curl_text, _method, vague_answer):
    """否例：用户没明确点头时，`confirmed` 无论传成什么都不许发出去。"""
    result = fs.probe_http(curl=curl_text, confirmed=vague_answer,
                           resolver=_exploding_resolver, opener=_ExplodingOpener())
    assert result["code"] == "REQUIRES_CONFIRM"


@pytest.mark.parametrize("consent", [True, "true", "YES", "ok"])
def test_an_explicit_yes_lets_exactly_one_request_out(consent):
    class _Opener:
        def __init__(self):
            self.calls: list = []

        def open(self, request, timeout=None):
            self.calls.append(request.get_method())

            class _R:
                status = 201
                code = 201

                def read(self, size=-1):
                    return b"{}"

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False

                @property
                def headers(self):
                    import email

                    return email.message_from_string("Content-Type: application/json\r\n")

            return _R()

    opener = _Opener()
    result = fs.probe_http(curl=WRITE_RISK_CURLS[0][0], confirmed=consent,
                           resolver=lambda h, p: ["93.184.216.34"], opener=opener)
    assert result["ok"] is True
    assert opener.calls == ["POST"], "一次同意只该放一条请求出去"


def test_the_confirmation_grants_nothing_beyond_this_one_request(prompt_body):
    """轻确认不是采纳硬闸：不签令牌、不跨轮、不是旅程写授权（spec Delta 四）。"""
    zh = "只管这一次请求" in prompt_body and "不跨轮" in prompt_body
    en = ("covers this one request only" in prompt_body
          and "does not carry across" in prompt_body)
    assert zh or en, "「一次一授权、不跨轮」的纪律不在提示词里"


def test_silence_and_vagueness_are_named_as_non_consent_in_the_prompt(prompt_body):
    zh = "沉默" in prompt_body and "随便" in prompt_body and "都不是同意" in prompt_body
    en = ("Silence" in prompt_body and "whatever" in prompt_body
          and "are not agreement" in prompt_body)
    assert zh or en, "沉默/含糊不算同意的纪律不在提示词里"
