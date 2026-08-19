# -*- coding: utf-8 -*-
"""AC29 · 能力边界如实告知（`free_surface_capability_boundary`）。

用户在自由排查里问"看下代码为什么这样""改哪一行"——这一版只到只读层结论。
要说的是三件事：**做不到 + 边界在哪 + 替代路径**，缺一即不合格。
最要防的是第四种：**假装已经定位到代码**（编一个 `文件:行号` 出来）。

与 AC16 不重叠：AC16 判「连接不通不许用业务话术掩盖」，AC29 判「能力边界不许含糊
冒充」。两条的注入点不同，锚也刻意不钉在同一句话上。

诚实分级：答复三要素齐不齐由 UAT 的 judge 判；本文件钉纪律在不在提示词里，
以及服务端那条能力不可用的回复自己就带齐三要素。
"""
from __future__ import annotations

import pytest

from server.journey import free_surface as fs


# ── 纪律半边（提示词锚）──────────────────────────────────────────────────
def test_the_read_only_ceiling_is_stated(prompt_body):
    zh = "这一版只到只读层结论" in prompt_body
    en = "only reaches read-only-layer conclusions" in prompt_body
    assert zh or en, "「本版只到只读层结论」不在提示词里"


def test_source_level_root_cause_is_declared_out_of_scope(prompt_body):
    zh = "源码层根因不在本版能力内" in prompt_body and "直说做不到" in prompt_body
    en = ("Source-level root cause is not in this version" in prompt_body
          and "say you cannot do it" in prompt_body)
    assert zh or en, "「源码层根因不在本版能力内 + 直说做不到」不在提示词里"


def test_an_alternative_path_is_required_not_optional(prompt_body):
    """三要素的第三件。锚句刻意与 AC16 的「替代路径」那句分开，不共用一句话。"""
    zh = "然后给替代路径" in prompt_body and "把相关代码贴过来" in prompt_body
    en = ("offer the alternative path" in prompt_body
          and "the relevant code" in prompt_body)
    assert zh or en, "边界告知之后的替代路径不在提示词里"


def test_faking_a_code_location_is_explicitly_banned(prompt_body):
    zh = "绝不假装已经定位到代码" in prompt_body and "文件:行号" in prompt_body
    en = ("Never pretend to have located the code" in prompt_body
          and "file:line" in prompt_body)
    assert zh or en, "「不许编造代码位置」的纪律不在提示词里"


def test_a_plausible_fake_location_is_named_as_worse_than_admitting_the_limit(prompt_body):
    """把"编一个看着专业的位置"点名成更有害的那一边，否则模型会选看起来更专业的。"""
    zh = "比说" in prompt_body and "有害得多" in prompt_body
    en = "far worse than saying you" in prompt_body
    assert zh or en, "「编造比承认更有害」这句权衡不在提示词里"


def test_the_db_boundary_is_not_dressed_up_as_a_business_answer(prompt_body):
    """与 AC16/BB-668 同族但不同注入点：这里是能力边界，那里是通道断连。"""
    zh = "这一版查不了库" in prompt_body and "没有符合条件的记录" in prompt_body
    en = ("this version cannot" in prompt_body and "no matching records" in prompt_body)
    assert zh or en, "查不了库要说能力边界、不许说成业务结论的纪律不在提示词里"


# ── 服务端半边：能力不可用的回复自己就带齐三要素 ─────────────────────────
def test_the_unavailable_db_channel_answers_with_all_three_elements(monkeypatch, tmp_path):
    from server.journey import db_readonly

    monkeypatch.setenv(fs.OBSERVATION_LOG_ENV, str(tmp_path / "obs.jsonl"))
    monkeypatch.delenv(db_readonly.DSN_ENV, raising=False)
    result = fs.db_query(sql="SELECT 1")

    message = result["message"]
    assert result["code"] == "E_DB_CHANNEL_UNAVAILABLE"
    assert "查不了" in message, "缺第一要素：做不到"
    assert "能力边界" in message, "缺第二要素：边界在哪"
    assert "替代路径" in message, "缺第三要素：替代路径"
    assert "没有符合条件的记录" in message, "误导性话术没有被具名当反例"


def test_an_egress_refusal_is_labelled_a_channel_conclusion(tmp_path, monkeypatch):
    """边界告知的另一面：策略拦下不是被测系统的业务结论。"""
    monkeypatch.setenv(fs.OBSERVATION_LOG_ENV, str(tmp_path / "obs.jsonl"))

    class _Exploding:
        def open(self, *a, **k):
            raise AssertionError("被策略拦下的目标不该出站")

    result = fs.probe_http(url="http://169.254.169.254/latest/meta-data/",
                           resolver=lambda h, p: ["93.184.216.34"], opener=_Exploding())
    assert result["ok"] is False
    assert "业务结论" in result["hint"]


@pytest.mark.parametrize("sql", ["SELECT * FROM orders", "SHOW server_version"])
def test_a_rejected_statement_is_not_reported_as_an_empty_result(sql, tmp_path, monkeypatch):
    """能力/校验层的拒绝，绝不能长成"查出来是空的"这种业务外观。"""
    from server.journey import db_readonly

    monkeypatch.setenv(fs.OBSERVATION_LOG_ENV, str(tmp_path / "obs.jsonl"))
    monkeypatch.delenv(db_readonly.DSN_ENV, raising=False)
    result = fs.db_query(sql=sql)
    assert result["ok"] is False
    assert "result" not in result, "不可用路径夹带了业务结果对象"
