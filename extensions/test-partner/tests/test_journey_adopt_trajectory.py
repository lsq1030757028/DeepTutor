# -*- coding: utf-8 -*-
"""BB-714：采纳卡出现之前，轨迹里不得出现 `journey_adopt`。

2026-09-05 UAT（判据 6 残留 4）：草稿卡之后、采纳卡之前，过程面板已显示
「Using test-partner journey_adopt」（15:12:27）。事后核实那是 `prepare_adoption=true`
的空调用——但用户看不到参数，看到的就是"还没勾选就调了采纳"。

机制层修法：签发采纳卡从 `journey_adopt` 拆成 `journey_prepare_adoption`；
`journey_adopt` 只剩"带经验签的用户决定落账"一条路，没有决定直接拒
（E_USER_DECISION_REQUIRED），且**不留 tool_call 事件**。

三条判据（改前都红）：
1. 走完 prepare → 卡 → adopt 之后，events.jsonl 里 `journey_adopt` 的 tool_call
   只有一条，且在 adopt_confirm 落账那一次；采纳卡签发那一步记的是
   `journey_prepare_adoption`；
2. MCP 面 `journey_adopt` 没有 `prepare_adoption` 参数——prepare 语义在工具面上不存在；
3. 可信 Test 会话里无决定调 `journey_adopt`：拒绝 + 零产物 + 账本里没有
   `journey_adopt` 的 tool_call。
"""
from __future__ import annotations

import inspect

import pytest

from server.journey import artifacts, tools

from tests.test_journey_exec import (  # noqa: F401 - fixtures 经 import 生效
    GOOD_ANALYSIS, GOOD_FACTS, api_case, store, target)
from tests.journey_decision_helper import SECRET, trusted_claims
from tests.test_journey_adopt_decision import _draft_batch, _load_draft
from tests.test_journey_adopt_wiring import OWNER, _signed_decision


@pytest.fixture(autouse=True)
def _decision_secret(monkeypatch):
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)


def _tool_calls(bid: str) -> list[str]:
    return [str(e.get("tool") or "") for e in artifacts.read_events(bid)
            if e.get("type") == "tool_call"]


def test_no_journey_adopt_in_the_trajectory_before_the_adoption_card(store, target):
    bid = _draft_batch(store, target, [api_case("dw1", writes=True),
                                       api_case("dr1")])
    before = _tool_calls(bid)
    assert "journey_adopt" not in before

    # 第一步：签发采纳卡——这一步不是采纳，轨迹里不该出现 journey_adopt
    prepared = tools.prepare_adoption(
        owner=OWNER, batch_id=bid, selected_draft_ids=["dw1", "dr1"],
        _bridge_claims=trusted_claims("journey_prepare_adoption", owner=OWNER),
        caller_surface="capability")
    assert prepared["ok"] and prepared["needs_gate"] == "adoption", prepared
    assert prepared["card"]["id"].startswith(f"journey_adopt:{bid}:")
    after_card = _tool_calls(bid)
    assert "journey_adopt" not in after_card, after_card
    assert after_card[len(before):] == ["journey_prepare_adoption"]
    assert not artifacts.has_artifact(bid, "approved_caseset")

    # 第二步：用户答复采纳卡之后，journey_adopt 才第一次出现在轨迹里
    token = _signed_decision(bid, _load_draft(bid), ["dw1", "dr1"], ["dw1"])
    out = tools.adopt(
        owner=OWNER, batch_id=bid, selected_draft_ids=["dw1", "dr1"],
        caseset_slug="adoptwire", adopted_via="chat_ask_user",
        confirmed_by="chat", authorized_write_draft_ids=["dw1"],
        decision_context=token,
        _bridge_claims=trusted_claims("journey_adopt", owner=OWNER),
        caller_surface="capability")
    assert out["ok"], out
    calls = _tool_calls(bid)
    assert calls.count("journey_adopt") == 1
    assert calls.index("journey_adopt") > calls.index("journey_prepare_adoption")


def test_the_mcp_surface_has_no_prepare_mode_on_journey_adopt():
    import server.main as main_mod

    adopt_params = set(inspect.signature(main_mod.journey_adopt).parameters)
    assert "prepare_adoption" not in adopt_params
    prepare_params = set(inspect.signature(main_mod.journey_prepare_adoption).parameters)
    assert {"batch_id", "selected_draft_ids"} <= prepare_params
    assert "prepare_adoption" not in inspect.signature(tools.adopt).parameters
    assert "prepare_adoption" in tools.GATE_TOOLS


def test_adopt_without_a_decision_is_refused_and_leaves_no_trace(store, target):
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    before = _tool_calls(bid)
    out = tools.adopt(
        owner=OWNER, batch_id=bid, selected_draft_ids=["dw1"],
        caseset_slug="adoptwire", adopted_via="chat_ask_user",
        confirmed_by="chat", authorized_write_draft_ids=[],
        _bridge_claims=trusted_claims("journey_adopt", owner=OWNER),
        caller_surface="capability")
    assert not out["ok"] and out["code"] == "E_USER_DECISION_REQUIRED", out
    assert "journey_prepare_adoption" in out["machine"]
    assert not artifacts.has_artifact(bid, "approved_caseset")
    assert _tool_calls(bid) == before, "无决定的采纳不该在轨迹里留下 journey_adopt"


def test_prepare_disclosure_text_is_complete_and_untruncated(store, target):
    """BB-715 的服务端半边：披露文本由服务端渲染，三字段逐条完整。"""
    long_cleanup = "清理：" + "删除本用例创建的测试订单并回滚账户余额；" * 12
    case = api_case("dw1", writes=True)
    case["side_effects"]["cleanup"] = long_cleanup
    bid = _draft_batch(store, target, [case, api_case("dr1")])
    prepared = tools.prepare_adoption(
        owner=OWNER, batch_id=bid, selected_draft_ids=["dw1", "dr1"],
        _bridge_claims=trusted_claims("journey_prepare_adoption", owner=OWNER),
        caller_surface="capability")
    md = prepared["disclosure_md"]
    assert long_cleanup in md, "清理披露被截断或改写"
    assert "…" not in md
    assert "dw1" in md and "dr1" in md
    assert "凭什么授权：events#write_confirm" in md
    assert "写什么：测试面" in md


def test_prepare_disclosure_never_leaves_a_line_blank():
    """BB-715 补修：老批次里写用例可能没填 write_scope（闸收紧之前起草的），
    披露文本三行必须仍然齐全，空值要在卡上看得见是空的，而不是一行空白。"""
    md = tools._render_adopt_disclosure_md(
        {"cases": [{"draft_id": "d3", "title": "自助开台页下单"}]},
        ["d3"],
        [{"draft_id": "d3", "title": "自助开台页下单", "write_scope": "",
          "cleanup": "测试环境下单后作废", "authorization": "仅限测试环境"}],
    )
    lines = md.splitlines()
    assert lines[0] == "- d3 自助开台页下单：**会写数据**"
    assert lines[1].startswith("  - 写什么：") and lines[1] != "  - 写什么："
    assert tools.BLANK_DISCLOSURE_FIELD in lines[1]
    assert lines[2] == "  - 怎么清理：测试环境下单后作废"
    assert lines[3] == "  - 凭什么授权：仅限测试环境"
