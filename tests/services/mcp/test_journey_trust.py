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


def _adapter(manager: _Manager) -> MCPToolAdapter:
    return MCPToolAdapter(
        manager=manager,  # type: ignore[arg-type]
        server_name="test-partner",
        original_name="journey_execute",
        description="execute",
        input_schema={
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "timeout_s": {"type": "integer", "default": 900},
                "bridge_context": {"type": "string", "default": ""},
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
