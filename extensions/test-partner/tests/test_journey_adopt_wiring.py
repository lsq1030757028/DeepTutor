# -*- coding: utf-8 -*-
"""采纳唯一硬闸的接线（批次 4 / ADR-2 / AC6-AC8）。

批次 0 证了令牌链承载得住（test_journey_adopt_decision.py 管校验原语），
这里证**工具面接线**：

1. prepare 步返回服务端签发的卡 + AC6 三字段披露；
2. 采纳通过 → 派生 write_confirm 事件（via="adopt_card"）→
   `execute_run.write_authorization` **零改动**就认它（AC8 的机制闭环）；
3. 重放、篡改、越权在工具面被拒且不留半个产物；
4. 工作台面（无桥接声明）语义原样——两面各自完整，互不留洞。
"""
import copy

import pytest

from server.journey import artifacts, decision_auth, execute_run, tools

from tests.test_journey_exec import (  # noqa: F401 - fixtures 经 import 生效
    GOOD_ANALYSIS, GOOD_FACTS, api_case, store, target)
from tests.journey_decision_helper import SECRET, trusted_claims
from tests.test_journey_adopt_decision import _draft_batch, _load_draft

import base64
import hashlib
import hmac
import json
import secrets
import time

from server.journey import bridge_auth

OWNER = "unit-test-owner"


@pytest.fixture(autouse=True)
def _decision_secret(monkeypatch):
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _tool_arguments(bid, selected, authorized, slug="adoptwire"):
    """与 tools.adopt 的 `_decision_arguments` 回落字典逐键一致（签名绑定对象）。"""
    return {
        "batch_id": bid,
        "selected_draft_ids": list(selected),
        "caseset_slug": slug,
        "adopted_via": "chat_ask_user",
        "confirmed_by": "chat",
        "idempotency_key": "",
        "prepare_adoption": False,
        "authorized_write_draft_ids": list(authorized),
    }


def _signed_decision(bid, case_draft, selected, authorized, *, question=None,
                     jti=None, slug="adoptwire"):
    issued = int(time.time())
    card = question or decision_auth.expected_adopt_question(
        bid, case_draft, selected)
    payload = {
        "v": 1, "iss": "deeptutor", "aud": decision_auth.AUDIENCE,
        "owner_id": OWNER, "session_id": "session-test", "turn_id": "turn-test",
        "capability": "test", "surface": "capability",
        "tool": "journey_adopt",
        "args_sha256": bridge_auth.arguments_sha256(
            _tool_arguments(bid, selected, authorized, slug)),
        "ask_user_tool_call_id": "ask-adopt-wire-1",
        "ask_user": {"intro": None, "questions": [card]},
        "answers": [{"questionId": card["id"], "text": ",".join(authorized)}],
        "resolved_at": issued, "iat": issued, "exp": issued + 300,
        "jti": jti or secrets.token_urlsafe(16),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return f"{_b64(raw)}.{_b64(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest())}"


def _adopt(bid, selected, authorized, *, token=None, claims=None, slug="adoptwire"):
    draft = _load_draft(bid)
    token = token or _signed_decision(bid, draft, selected, authorized, slug=slug)
    claims = claims or trusted_claims("journey_adopt", owner=OWNER)
    return tools.adopt(
        owner=OWNER, batch_id=bid, selected_draft_ids=list(selected),
        caseset_slug=slug, adopted_via="chat_ask_user", confirmed_by="chat",
        authorized_write_draft_ids=list(authorized),
        decision_context=token, _bridge_claims=claims,
        caller_surface="capability")


# ── prepare 步（AC6）────────────────────────────────────────────────────────


def test_prepare_returns_the_trusted_card_and_the_disclosure(store, target):
    bid = _draft_batch(store, target, [api_case("dw1", writes=True),
                                       api_case("dr1")])
    out = tools.adopt(owner=OWNER, batch_id=bid,
                      selected_draft_ids=["dw1", "dr1"],
                      prepare_adoption=True,
                      _bridge_claims=trusted_claims("journey_adopt", owner=OWNER),
                      caller_surface="capability")
    assert out["ok"] and out["code"] == "NEEDS_GATE" and out["needs_gate"] == "adoption"
    draft = _load_draft(bid)
    assert out["card"] == decision_auth.expected_adopt_question(
        bid, draft, ["dw1", "dr1"])
    # AC6：披露源 = schema S29 强制的三字段，逐条对应，只含写用例
    assert [row["draft_id"] for row in out["disclosure"]] == ["dw1"]
    case = next(c for c in draft["cases"] if c["draft_id"] == "dw1")
    row = out["disclosure"][0]
    assert row["cleanup"] == case["side_effects"]["cleanup"]
    assert row["authorization"] == case["side_effects"]["authorization"]
    assert row["write_scope"] == case["side_effects"]["write_scope"]
    # prepare 不落任何产物
    assert not artifacts.has_artifact(bid, "approved_caseset")


def test_prepare_without_a_trusted_bridge_is_rejected(store, target):
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    with artifacts.trusted_owner(OWNER):
        out = tools.adopt(owner=OWNER, batch_id=bid, selected_draft_ids=["dw1"],
                          prepare_adoption=True, caller_surface="workbench")
    assert not out["ok"] and out["code"] == "E_USER_DECISION_REQUIRED"


def test_too_many_write_cases_get_a_split_instruction_not_a_wall(store, target):
    cases = [api_case(f"dw{i}", writes=True) for i in range(9)]
    bid = _draft_batch(store, target, cases)
    out = tools.adopt(owner=OWNER, batch_id=bid,
                      selected_draft_ids=[c["draft_id"] for c in cases],
                      prepare_adoption=True,
                      _bridge_claims=trusted_claims("journey_adopt", owner=OWNER),
                      caller_surface="capability")
    assert not out["ok"] and out["code"] == "E_TOO_MANY_WRITE_CASES"
    assert "拆成多个批次" in out["message"]


# ── 采纳 + 派生写授权（AC7/AC8）────────────────────────────────────────────


def test_adoption_derives_a_write_authorization_the_runtime_accepts(store, target):
    """AC8 的机制闭环：采纳一步，运行时零改动就认到授权。"""
    bid = _draft_batch(store, target, [api_case("dw1", writes=True),
                                       api_case("dr1")])
    out = _adopt(bid, ["dw1", "dr1"], ["dw1"])
    assert out["ok"], out
    caseset = artifacts.load_artifact(bid, "approved_caseset")
    by_draft = {}
    for event in artifacts.read_events(bid):
        if event.get("type") == "adopt_confirm":
            for identity in event.get("identities") or []:
                by_draft[identity["draft_id"]] = identity["case_id"]
    write_case_id = by_draft["dw1"]

    derived = out["write_authorization"]
    assert derived["via"] == "adopt_card"
    assert derived["decision"] == "authorized_some"
    assert derived["case_ids"] == [write_case_id]
    frozen = next(c for c in caseset["cases"] if c["case_id"] == write_case_id)
    assert derived["digests"] == {write_case_id: frozen["source_case_digest"]}
    assert derived["decision_jti"]

    # 运行时取数口零改动：write_authorization 直接认这条派生事件
    auth = execute_run.write_authorization(bid)
    assert auth["authorized"] == {write_case_id}
    assert auth["dropped"] == []


def test_empty_authorization_lands_an_explicit_authorized_none(store, target):
    bid = _draft_batch(store, target, [api_case("dw1", writes=True),
                                       api_case("dr1")])
    out = _adopt(bid, ["dw1", "dr1"], [])
    assert out["ok"], out
    derived = out["write_authorization"]
    assert derived["decision"] == "authorized_none"
    assert derived["case_ids"] == []
    assert len(derived["declined"]) == 1
    assert execute_run.write_authorization(bid)["authorized"] == set()


def test_read_only_adoption_still_lands_and_derives_an_empty_record(store, target):
    bid = _draft_batch(store, target, [api_case("dr1"), api_case("dr2")])
    out = _adopt(bid, ["dr1", "dr2"], [])
    assert out["ok"], out
    assert artifacts.has_artifact(bid, "approved_caseset")
    assert out["write_authorization"]["declined"] == []


# ── 注入在工具面被拒 ────────────────────────────────────────────────────────


def test_replaying_the_adoption_decision_is_rejected(store, target):
    """同一份决定采纳两次：第二次在工具面被拒（AC7 重放口径，工具级实证）。"""
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    draft = _load_draft(bid)
    fixed = secrets.token_urlsafe(16)
    token = _signed_decision(bid, draft, ["dw1"], ["dw1"], jti=fixed)
    assert _adopt(bid, ["dw1"], ["dw1"], token=token)["ok"]
    second = _adopt(bid, ["dw1"], ["dw1"], token=token)
    assert not second["ok"] and second["code"] == "E_USER_DECISION_REPLAYED"


def test_tampered_card_rejects_and_freezes_nothing(store, target):
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    draft = _load_draft(bid)
    forged = copy.deepcopy(
        decision_auth.expected_adopt_question(bid, draft, ["dw1"]))
    forged["options"][0]["description"] = "sha256:" + "0" * 64 + " | API dw1"
    token = _signed_decision(bid, draft, ["dw1"], ["dw1"], question=forged)
    out = _adopt(bid, ["dw1"], ["dw1"], token=token)
    assert not out["ok"] and out["code"] == "E_USER_DECISION_INVALID"
    assert not artifacts.has_artifact(bid, "approved_caseset")
    assert not [e for e in artifacts.read_events(bid)
                if e.get("type") == "write_confirm"]


def test_overreaching_tool_arguments_are_rejected(store, target):
    """签名里用户只授了 dw1，工具参数想多带 dw2 → 拒。"""
    cases = [api_case("dw1", writes=True), api_case("dw2", writes=True)]
    bid = _draft_batch(store, target, cases)
    draft = _load_draft(bid)
    token = _signed_decision(bid, draft, ["dw1", "dw2"], ["dw1"])
    out = _adopt(bid, ["dw1", "dw2"], ["dw1", "dw2"], token=token)
    assert not out["ok"] and out["code"] == "E_USER_DECISION_INVALID"
    assert not artifacts.has_artifact(bid, "approved_caseset")


def test_cross_owner_claims_are_rejected(store, target):
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    claims = trusted_claims("journey_adopt", owner="someone-else")
    out = _adopt(bid, ["dw1"], ["dw1"], claims=claims)
    assert not out["ok"] and out["code"] == "E_USER_DECISION_INVALID"
    assert "owner" in out["message"]


def test_editing_the_draft_after_the_card_invalidates_adoption(store, target):
    """AC18 工具级：卡签发后草稿被改 → 采纳拒绝。"""
    from server.journey import draft_cases
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    draft = _load_draft(bid)
    token = _signed_decision(bid, draft, ["dw1"], ["dw1"])
    edited = api_case("dw1", writes=True)
    edited["expected"] = "改成了另一件事"
    with artifacts.trusted_owner(OWNER):
        assert draft_cases.draft(bid, cases=[edited])["ok"]
    out = _adopt(bid, ["dw1"], ["dw1"], token=token)
    assert not out["ok"] and out["code"] == "E_USER_DECISION_INVALID"


# ── 工作台面语义原样 ────────────────────────────────────────────────────────


def test_workbench_adoption_stays_untouched(store, target):
    """无桥接声明的采纳照旧走通，不派生写授权（写授权走独立写确认卡）。"""
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    with artifacts.trusted_owner(OWNER):
        out = tools.adopt(owner=OWNER, batch_id=bid, selected_draft_ids=["dw1"],
                          caseset_slug="wb", caller_surface="workbench")
    assert out["ok"], out
    assert out["write_authorization"] is None
    assert artifacts.has_artifact(bid, "approved_caseset")
    assert not [e for e in artifacts.read_events(bid)
                if e.get("type") == "write_confirm"]
    # 写授权仍可走既有独立写确认（0029 口径变更 0036：卡形态不再必要，
    # 但工作台面保留它——两面各自完整）
    assert execute_run.write_authorization(bid)["authorized"] == set()


def test_workbench_cannot_smuggle_write_authorization_without_a_decision(store, target):
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    with artifacts.trusted_owner(OWNER):
        out = tools.adopt(owner=OWNER, batch_id=bid, selected_draft_ids=["dw1"],
                          authorized_write_draft_ids=["dw1"],
                          caller_surface="workbench")
    assert not out["ok"] and out["code"] == "E_USER_DECISION_REQUIRED"
    assert not artifacts.has_artifact(bid, "approved_caseset")
