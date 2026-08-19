"""Trusted Journey bridge: exact-call signing and adapter enforcement."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from deeptutor.services.mcp.manager import MCPToolAdapter
from deeptutor.services.test_journey.trust import (
    JourneyTrustError,
    TrustedJourneyContext,
    arguments_sha256,
    bind_trusted_journey_context,
    current_resolved_user_decision,
    record_resolved_user_decision,
    sign_bridge_context,
    sign_user_decision_context,
)

SECRET = "bridge-test-secret-that-is-at-least-32-bytes"


def _payload(token: str) -> dict[str, Any]:
    encoded = token.split(".", 1)[0]
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    return json.loads(raw)


def test_signature_binds_identity_tool_and_exact_arguments(monkeypatch) -> None:
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)
    context = TrustedJourneyContext(
        owner_id="user-a",
        session_id="session-a",
        turn_id="turn-a",
        capability="test",
        surface="capability",
    )
    token = sign_bridge_context(
        context,
        tool="journey_execute",
        arguments={"batch_id": "b-1", "timeout_s": 900, "owner": "spoof"},
        now=100,
    )
    payload = _payload(token)
    assert payload["owner_id"] == "user-a"
    assert payload["session_id"] == "session-a"
    assert payload["capability"] == "test"
    assert payload["tool"] == "journey_execute"
    assert payload["args_sha256"] == arguments_sha256({"batch_id": "b-1", "timeout_s": 900})
    assert payload["exp"] == 160


class _Manager:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, owner, server, tool, arguments, **kwargs):
        self.calls.append({"owner": owner, "server": server, "tool": tool, "arguments": arguments})
        return '{"ok":true,"code":"OK"}'


def _adapter(manager: _Manager, tool: str = "journey_execute") -> MCPToolAdapter:
    return MCPToolAdapter(
        manager=manager,  # type: ignore[arg-type]
        server_name="test-partner",
        original_name=tool,
        description="execute",
        input_schema={
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "timeout_s": {"type": "integer", "default": 900},
                "bridge_context": {"type": "string", "default": ""},
                "decision_context": {"type": "string", "default": ""},
            },
        },
        tool_timeout=5,
    )


@pytest.mark.asyncio
async def test_adapter_overwrites_reserved_identity_and_adds_signed_context(monkeypatch) -> None:
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)
    manager = _Manager()
    adapter = _adapter(manager)
    trusted = TrustedJourneyContext(
        owner_id="user-a",
        session_id="session-a",
        turn_id="turn-a",
        capability="test",
        surface="capability",
    )
    with bind_trusted_journey_context(trusted):
        result = await adapter.execute(
            batch_id="b-1",
            owner="user-b",
            caller_surface="workbench",
            bridge_context="forged",
        )
    assert result.success
    assert len(manager.calls) == 1
    args = manager.calls[0]["arguments"]
    assert "owner" not in args and "caller_surface" not in args
    payload = _payload(args["bridge_context"])
    assert payload["owner_id"] == "user-a"
    assert payload["args_sha256"] == arguments_sha256({"batch_id": "b-1", "timeout_s": 900})


def test_adapter_hides_reserved_trust_fields_from_model_schema() -> None:
    definition = _adapter(_Manager(), "journey_write_confirm").get_definition()
    properties = (definition.raw_parameters or {}).get("properties") or {}
    assert "bridge_context" not in properties
    assert "decision_context" not in properties


@pytest.mark.asyncio
async def test_write_confirm_requires_and_injects_a_real_resolved_user_decision(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)
    manager = _Manager()
    adapter = _adapter(manager, "journey_write_confirm")
    trusted = TrustedJourneyContext(
        owner_id="user-a",
        session_id="session-a",
        turn_id="turn-a",
        capability="test",
        surface="capability",
    )
    question_id = "journey_write_confirm:b-20260813-abcdef:acs-1"
    question = {
        "id": question_id,
        "prompt": "Choose writes",
        "multi_select": True,
        "allow_free_text": False,
        "options": [{"label": "c/R1-C001", "description": "sha256:x | write"}],
    }
    with bind_trusted_journey_context(trusted):
        denied = await adapter.execute(batch_id="b-20260813-abcdef", case_ids=[])
        assert denied.success is False and manager.calls == []
        assert record_resolved_user_decision(
            ask_user_tool_call_id="ask-1",
            ask_user_payload={"questions": [question]},
            answers=[{"questionId": question_id, "text": "c/R1-C001"}],
        )
        allowed = await adapter.execute(batch_id="b-20260813-abcdef", case_ids=["c/R1-C001"])
    assert allowed.success is True and len(manager.calls) == 1
    sent = manager.calls[0]["arguments"]
    assert sent["decision_context"] and sent["bridge_context"]
    decision_payload = _payload(sent["decision_context"])
    assert decision_payload["answers"][0]["text"] == "c/R1-C001"
    assert decision_payload["owner_id"] == "user-a"
    assert decision_payload["tool"] == "journey_write_confirm"
    assert decision_payload["args_sha256"] == _payload(sent["bridge_context"])["args_sha256"]


@pytest.mark.asyncio
async def test_ingest_entity_requires_and_injects_exact_user_decision(monkeypatch) -> None:
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)
    manager = _Manager()
    adapter = MCPToolAdapter(
        manager=manager,  # type: ignore[arg-type]
        server_name="test-partner",
        original_name="journey_ingest",
        description="ingest",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "requirement_entity": {"type": "string", "default": ""},
                "decision_context": {"type": "string", "default": ""},
                "bridge_context": {"type": "string", "default": ""},
            },
        },
        tool_timeout=5,
    )
    trusted = TrustedJourneyContext("user-a", "session-a", "turn-a", "test", "capability")
    question = {
        "id": "journey_requirement_entity",
        "prompt": "Which business entity does this requirement actually create, update, or delete?",
        "multi_select": False,
        "allow_free_text": True,
        "options": [],
    }
    with bind_trusted_journey_context(trusted):
        denied = await adapter.execute(title="Add character", requirement_entity="custom_character")
        assert denied.success is False and manager.calls == []
        assert record_resolved_user_decision(
            ask_user_tool_call_id="ask-entity-1",
            ask_user_payload={"questions": [question]},
            answers=[
                {
                    "questionId": "journey_requirement_entity",
                    "text": "custom_character",
                }
            ],
        )
        allowed = await adapter.execute(
            title="Add character", requirement_entity="custom_character"
        )
    assert allowed.success is True and len(manager.calls) == 1
    sent = manager.calls[0]["arguments"]
    decision_payload = _payload(sent["decision_context"])
    bridge_payload = _payload(sent["bridge_context"])
    assert decision_payload["tool"] == "journey_ingest"
    assert decision_payload["answers"][0]["text"] == "custom_character"
    assert decision_payload["args_sha256"] == bridge_payload["args_sha256"]


@pytest.mark.asyncio
async def test_adapter_refuses_plain_chat_and_missing_secret_without_calling_server(
    monkeypatch,
) -> None:
    manager = _Manager()
    adapter = _adapter(manager)
    with bind_trusted_journey_context(
        TrustedJourneyContext("user-a", "s", "t", "chat", "capability")
    ):
        assert not (await adapter.execute(batch_id="b-1")).success
    monkeypatch.delenv("TEST_JOURNEY_BRIDGE_SECRET", raising=False)
    with bind_trusted_journey_context(
        TrustedJourneyContext("user-a", "s", "t", "test", "capability")
    ):
        assert not (await adapter.execute(batch_id="b-1")).success
    assert manager.calls == []


# ── B-2：采纳硬闸在生产聊天面的三个环节（ADR-2 的第三条腿） ──────────────
#
# 同形复发第三次：链条上有一环没接，而两侧绿测都抓不到。前两次是「提示词
# 承诺了没有的工具」与「包装层漏传 owner」；这次是 DT 侧 trust.py 的两张
# 白名单没随 ADR-2 扩容——扩展侧 `verify_adopt_context` 写好了、`manager.py`
# 的闸分支也写好了，中间的「答复能不能被记下、记下能不能签出」两环空着，
# 结果是 prepare 弹得出卡、用户答得了、确认步 100% 失败。
# 下面按环节逐个钉：记录 / 签名 / 闸分支，每个环节都配一条收紧方向的对照。

ADOPT_BATCH = "b-20260819-abcdef"
ADOPT_QID = f"journey_adopt:{ADOPT_BATCH}:{'a' * 24}"


def _adopt_card(qid: str = ADOPT_QID) -> dict[str, Any]:
    return {
        "questions": [
            {
                "id": qid,
                "prompt": "Adopt these cases?",
                "multi_select": True,
                "allow_free_text": False,
                "options": [{"label": "d/R1-C001", "description": "sha256:x"}],
            }
        ]
    }


def _adopt_decision(trusted: TrustedJourneyContext):
    assert record_resolved_user_decision(
        ask_user_tool_call_id="ask-adopt-1",
        ask_user_payload=_adopt_card(),
        answers=[{"questionId": ADOPT_QID, "text": "d/R1-C001"}],
    )
    decision = current_resolved_user_decision()
    assert decision is not None
    return decision


def _adopt_adapter(manager: _Manager) -> MCPToolAdapter:
    return MCPToolAdapter(
        manager=manager,  # type: ignore[arg-type]
        server_name="test-partner",
        original_name="journey_adopt",
        description="adopt",
        input_schema={
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "selected_draft_ids": {"type": "array", "default": []},
                "prepare_adoption": {"type": "boolean", "default": False},
                "bridge_context": {"type": "string", "default": ""},
                "decision_context": {"type": "string", "default": ""},
            },
        },
        tool_timeout=5,
    )


def test_an_adoption_card_reply_is_recorded_as_a_gate_decision() -> None:
    """环节 1（记录）：采纳卡答复必须进决定槽，否则确认步永远无卡可验。"""
    trusted = TrustedJourneyContext("user-a", "session-a", "turn-a", "test", "capability")
    with bind_trusted_journey_context(trusted):
        decision = _adopt_decision(trusted)
    assert decision.ask_user_tool_call_id == "ask-adopt-1"
    assert decision.answers[0]["text"] == "d/R1-C001"
    assert decision.ask_user_payload["questions"][0]["id"] == ADOPT_QID


def test_an_ordinary_clarification_reply_is_still_discarded() -> None:
    """收紧方向的对照：白名单放开一项 ≠ 放开全部。

    只留上一条正向断言的话，把白名单整个删掉也是绿的——普通澄清答复被
    重新包装成写授权正是这张白名单存在的理由。
    """
    trusted = TrustedJourneyContext("user-a", "session-a", "turn-a", "test", "capability")
    with bind_trusted_journey_context(trusted):
        assert not record_resolved_user_decision(
            ask_user_tool_call_id="ask-chat-1",
            ask_user_payload=_adopt_card("clarify:boundary-includes-zero"),
            answers=[{"questionId": "clarify:boundary-includes-zero", "text": "yes"}],
        )
        assert current_resolved_user_decision() is None


def test_an_adoption_decision_can_actually_be_signed(monkeypatch) -> None:
    """环节 2（签名）：journey_adopt 必须签得出，且签的是这次调用的实参。

    形状按扩展侧 `decision_auth.verify_adopt_context` 的读法钉：它先验信封
    （aud / tool / args_sha256 / jti），再照 `expected_adopt_question` 比对卡片
    本体与答复。签名里缺任何一样，验签那步报的是「伪造」，而真因是没接线。
    """
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)
    trusted = TrustedJourneyContext("user-a", "session-a", "turn-a", "test", "capability")
    arguments = {"batch_id": ADOPT_BATCH, "selected_draft_ids": ["d/R1-C001"]}
    with bind_trusted_journey_context(trusted):
        decision = _adopt_decision(trusted)
        token = sign_user_decision_context(
            trusted, decision, tool="journey_adopt", arguments=arguments, now=100
        )
    payload = _payload(token)
    assert payload["aud"] == "test-partner-user-decision"
    assert payload["tool"] == "journey_adopt"
    assert payload["args_sha256"] == arguments_sha256(arguments)
    assert payload["ask_user"]["questions"][0]["id"] == ADOPT_QID
    assert payload["answers"] == [{"questionId": ADOPT_QID, "text": "d/R1-C001"}]
    assert payload["jti"] == decision.jti
    assert payload["owner_id"] == "user-a"


def test_an_unlisted_tool_still_cannot_carry_a_user_decision(monkeypatch) -> None:
    """签名白名单的收紧对照：任意 journey_* 工具不得夹带用户决定。"""
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)
    trusted = TrustedJourneyContext("user-a", "session-a", "turn-a", "test", "capability")
    with bind_trusted_journey_context(trusted):
        decision = _adopt_decision(trusted)
        with pytest.raises(JourneyTrustError):
            sign_user_decision_context(
                trusted, decision, tool="journey_execute", arguments={}, now=100
            )


@pytest.mark.asyncio
async def test_the_adoption_confirm_step_demands_a_decision_and_injects_it(monkeypatch) -> None:
    """环节 3a（闸分支）：非 prepare 的采纳没有决定不许出门，有了就带上。"""
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)
    manager = _Manager()
    adapter = _adopt_adapter(manager)
    trusted = TrustedJourneyContext("user-a", "session-a", "turn-a", "test", "capability")
    with bind_trusted_journey_context(trusted):
        denied = await adapter.execute(batch_id=ADOPT_BATCH, selected_draft_ids=["d/R1-C001"])
        assert denied.success is False and manager.calls == []
        _adopt_decision(trusted)
        allowed = await adapter.execute(batch_id=ADOPT_BATCH, selected_draft_ids=["d/R1-C001"])
    assert allowed.success is True and len(manager.calls) == 1
    sent = manager.calls[0]["arguments"]
    decision_payload = _payload(sent["decision_context"])
    bridge_payload = _payload(sent["bridge_context"])
    assert decision_payload["tool"] == "journey_adopt"
    assert decision_payload["args_sha256"] == bridge_payload["args_sha256"]


@pytest.mark.asyncio
async def test_the_prepare_step_is_exempt_or_the_gate_deadlocks_itself(monkeypatch) -> None:
    """环节 3b：prepare 这一步就是「去拿卡」，它自己要卡就成了先有鸡还是先有蛋。"""
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)
    manager = _Manager()
    adapter = _adopt_adapter(manager)
    trusted = TrustedJourneyContext("user-a", "session-a", "turn-a", "test", "capability")
    with bind_trusted_journey_context(trusted):
        prepared = await adapter.execute(batch_id=ADOPT_BATCH, prepare_adoption=True)
    assert prepared.success is True and len(manager.calls) == 1
    sent = manager.calls[0]["arguments"]
    assert sent["bridge_context"]
    assert not sent.get("decision_context"), "prepare 步不该夹带写授权级别的用户决定"


@pytest.mark.asyncio
async def test_the_write_confirm_gate_is_unchanged_by_the_adoption_wiring(monkeypatch) -> None:
    """环节 3c：放开采纳不等于放松别人——write_confirm 仍旧无决定不放行。"""
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)
    manager = _Manager()
    adapter = _adapter(manager, "journey_write_confirm")
    trusted = TrustedJourneyContext("user-a", "session-a", "turn-a", "test", "capability")
    with bind_trusted_journey_context(trusted):
        denied = await adapter.execute(batch_id="b-1")
    assert denied.success is False and manager.calls == []
