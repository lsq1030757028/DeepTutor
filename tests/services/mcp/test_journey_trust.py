"""Trusted Journey bridge: exact-call signing and adapter enforcement."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from deeptutor.services.mcp.manager import MCPToolAdapter
from deeptutor.services.test_journey.trust import (
    TrustedJourneyContext,
    arguments_sha256,
    bind_trusted_journey_context,
    record_resolved_user_decision,
    sign_bridge_context,
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
        owner_id="user-a", session_id="session-a", turn_id="turn-a",
        capability="test", surface="capability")
    question_id = "journey_write_confirm:b-20260813-abcdef:acs-1"
    question = {
        "id": question_id, "prompt": "Choose writes", "multi_select": True,
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
        allowed = await adapter.execute(
            batch_id="b-20260813-abcdef", case_ids=["c/R1-C001"])
    assert allowed.success is True and len(manager.calls) == 1
    sent = manager.calls[0]["arguments"]
    assert sent["decision_context"] and sent["bridge_context"]
    decision_payload = _payload(sent["decision_context"])
    assert decision_payload["answers"][0]["text"] == "c/R1-C001"
    assert decision_payload["owner_id"] == "user-a"
    assert decision_payload["tool"] == "journey_write_confirm"
    assert decision_payload["args_sha256"] == _payload(
        sent["bridge_context"])["args_sha256"]


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
    trusted = TrustedJourneyContext(
        "user-a", "session-a", "turn-a", "test", "capability")
    question = {
        "id": "journey_requirement_entity",
        "prompt": "Which business entity does this requirement actually create, update, or delete?",
        "multi_select": False,
        "allow_free_text": True,
        "options": [],
    }
    with bind_trusted_journey_context(trusted):
        denied = await adapter.execute(
            title="Add character", requirement_entity="custom_character")
        assert denied.success is False and manager.calls == []
        assert record_resolved_user_decision(
            ask_user_tool_call_id="ask-entity-1",
            ask_user_payload={"questions": [question]},
            answers=[{
                "questionId": "journey_requirement_entity",
                "text": "custom_character",
            }],
        )
        allowed = await adapter.execute(
            title="Add character", requirement_entity="custom_character")
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
