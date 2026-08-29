# -*- coding: utf-8 -*-
"""作者内容那一侧的凭据闸：只拦有证据是秘密的东西（2026-08-29 Round 3）。

## 起因

终验 V5 里 `疑似包含凭据` 连拦 4 次（澄清 1 次、用例草稿 3 次），拦的是系统
**自己生成的中文用例文件**，里面一个凭据都没有。`journey_draft_cases` 因此跑了
6 次，那一轮 23 分 45 秒基本耗在这段，是全轮最费时的一处。

## 样本从哪来（如实说）

**被拒的载荷不可复原**：这道闸在落盘前拒绝，被拒内容既不落盘也不进日志，
会话里只留下一句错误文案。所以下面的误拦样本不是"从证据里取出来的原文"，
而是**在真实产物上复现出来的同类**：拿那两个批次实际落盘的
`business_frame.json` / `case_draft.json` 里出现过的字段与写法，
逐类喂进旧实现，挑出会被拦的那些（`expect_json_path_data_0_dialogue_mode`
这种长断言名、`eyJtb2RlIjoi…` 这种 base64 请求体样例）。

顺带补掉那个让分析无从下手的洞：拒绝信封现在带被判为凭据的片段预览
（`CredentialSuspectedError.findings`），下次不必再靠复现去猜。

## 判据形态：正负两组，缺一不可

只验"原误拦样本放行"的话，把这道闸整个删掉就能全绿——那是把一个缺陷换成一个
更坏的。所以真凭据样本必须同时全拦，且两组都覆盖多种形态。
"""
import json

import pytest

from server.journey.gates import credential_scan as cs

#: 误拦样本。键是"这在用例草稿里是什么东西"，值是模型真会写出来的形状。
FALSE_POSITIVES = {
    "base64 请求体样例": {"body_b64": "eyJtb2RlIjoiZnJlZS1kaWFsb2d1ZSJ9"},
    "长下划线断言名": {"op": "expect_json_path_data_0_dialogue_mode"},
    "设备序列号": {"device_sn": "UIRON2026082900137X"},
    "MQTT 客户端 id": {"client_id": "uiron-cbl-20260829-7f3a91b2"},
    "固件版本串": {"firmware": "AIVOICE-1.6.1-20260829-rc3"},
    "中文步骤里夹英文 op": {
        "steps": ["调用 /api/v1/device/dialogue-mode/switch "
                  "并断言 expect_json_path_data_0_mode"]},
    # 修误报的改动自己造出来的新误报：`ask-entity-test-1` 里那截
    # `sk-entity-test-1` 命中了 OpenAI 的 `sk-` 前缀。凭据前缀必须顶在
    # token 开头，不能是词中间的一截。
    "ask_user 调用 id": {"ask_user_tool_call_id": "ask-entity-test-1"},
    "带 sk 字样的普通标识": {"slug": "task-sk-review-20260829"},
}

#: 真凭据样本。形态与上下文两路各覆盖几种，别只测一种前缀。
REAL_SECRETS = {
    "OpenAI key": {"api_key": "sk-proj-Ab3dEf9hIjKlMnOpQrStUvWxYz012345"},
    "GitHub PAT": {"note": "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"},
    "AWS access key id": {"x": "AKIAIOSFODNN7EXAMPLE"},
    "裸口令带键名": {"password": "Tr0ub4dor-and-3-horses-xyz"},
    "Authorization 头": {"headers": {"Authorization": "Bearer aB3dEf9hIjKlMnOpQrStUvWx"}},
    "api_key 键名": {"api_key": "Zm9vYmFyYmF6cXV4MTIzNDU2"},
    "JWT": {"t": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdEFGH1234ijklMNOP5678"},
    "PEM 私钥": {"k": "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END KEY-----"},
}


def _scan(payload) -> dict:
    return cs.scan_text_content(json.dumps(payload, ensure_ascii=False, sort_keys=True),
                                label="case_draft.json")


@pytest.mark.parametrize("name", sorted(FALSE_POSITIVES))
def test_authored_content_is_not_mistaken_for_a_credential(name):
    """原误拦样本必须全放行。"""
    report = _scan(FALSE_POSITIVES[name])
    assert report["ok"], f"{name} 仍被误拦：{report['entropy_hits']}"


@pytest.mark.parametrize("name", sorted(REAL_SECRETS))
def test_real_secrets_are_still_blocked(name):
    """真凭据必须全拦，且给得出"凭什么判它是凭据"。"""
    report = _scan(REAL_SECRETS[name])
    assert not report["ok"], f"{name} 漏拦了"
    assert any(row.get("secret_reason") for row in report["entropy_hits"]), \
        f"{name} 拦了但说不出理由"


def test_the_shape_scan_does_not_depend_on_the_entropy_gate():
    """两条真实漏网的回归判据。

    第一版把形态判据挂在熵扫描的结果上，于是：
    · `AKIAIOSFODNN7EXAMPLE` 熵只有 3.6，被熵闸先丢掉，形态判据没机会看；
    · JWT 带 `.`，而 TOKEN 字符集不含 `.`，一个 JWT 被切成三段，每段都不像凭据。
    **让更严的规则去过滤更准的规则的输入**，两条都会漏。
    """
    akid = "AKIAIOSFODNN7EXAMPLE"
    assert cs.shannon_entropy(akid) < cs.ENTROPY_THRESHOLD, "样本前提变了，这条测试要重挑"
    assert cs._shape_findings(akid, "x"), "形态扫描必须独立于熵扫描"

    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdEFGH1234ijklMNOP5678"
    assert not cs.TOKEN.fullmatch(jwt), "样本前提变了：TOKEN 现在能整段匹配 JWT"
    assert cs._shape_findings(jwt, "x")


def test_passing_content_still_records_what_was_let_through():
    """放行不等于不记账：熵高但没有秘密证据的串仍进 allowlisted_hits。

    没有这一条，「这次放宽了什么」只能靠读 diff 才知道。
    """
    report = _scan(FALSE_POSITIVES["长下划线断言名"])
    assert report["ok"]
    reasons = {row.get("allowlist_reason") for row in report["allowlisted_hits"]}
    assert "no secret shape or context" in reasons


def test_the_tree_scan_keeps_its_broad_entropy_sweep(tmp_path):
    """作用域边界：放宽的只有作者内容那一侧。

    `scan_tree` 是 DoD 7 的产物证据扫描，仍对任何高熵串报警——
    只改一侧而不验另一侧没变，等于不知道自己把闸放宽了多少。
    """
    (tmp_path / "run.json").write_text(
        json.dumps({"blob": "Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA"}), encoding="utf-8")
    report = cs.scan_tree(str(tmp_path))
    assert not report["ok"], "产物侧的全量熵扫不该被这次改动放宽"


def test_a_credential_rejection_is_not_reported_as_a_permission_problem(tmp_path,
                                                                       monkeypatch):
    """错误码分开：凭据误判曾一直显示成 E_OWNER_REQUIRED（权限问题）。

    用户读到的处置完全不同——一个是"找管理员开权限"，一个是"改一下措辞"。
    """
    from server.journey import artifacts, tools

    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(artifacts, "BATCHES_ROOT", str(tmp_path / "batches"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(tools, "_guarded", lambda *a, **k: None)

    def _boom(*args, **kwargs):
        raise artifacts.CredentialSuspectedError(
            "case_draft.json 疑似包含凭据，已在落盘前拒绝；请移除或变量化后重试",
            [{"token_preview": "sk-pro…2345", "secret_reason": "known secret prefix"}])

    monkeypatch.setattr(tools._draft, "draft", _boom)
    out = tools.draft_cases(batch_id="b-x", owner="unit-test-owner", cases=[])
    assert out["code"] == "E_CREDENTIAL_SUSPECTED"
    assert "E_" not in out["message"] and ".json" not in out["message"]
    assert "sk-pro…2345" in out["machine"], "拒绝了什么必须说得出来"
