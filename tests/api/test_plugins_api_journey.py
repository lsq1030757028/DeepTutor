"""Generic plugin surfaces must never expose or execute Journey primitives."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
import pytest

from deeptutor.api.routers import plugins_api
from deeptutor.core.tool_protocol import BaseTool, ToolDefinition, ToolResult


class _JourneyTool(BaseTool):
    deferred = True
    provider_kind = "mcp"
    provider_id = "test-partner"
    original_name = "journey_ingest"

    def __init__(self) -> None:
        self.calls = 0

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="mcp_test_partner_journey_ingest",
            description="protected",
            raw_parameters={"type": "object", "properties": {}},
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(content="should not run")


class _Registry:
    def __init__(self, tool: _JourneyTool) -> None:
        self.tool = tool

    def get(self, name: str):
        return self.tool if name == self.tool.name else None

    def get_definitions(self):
        return [self.tool.get_definition()]


class _Capabilities:
    def get_manifests(self):
        return [{"name": "chat"}, {"name": "test"}]


@pytest.mark.asyncio
async def test_direct_and_streaming_plugin_execution_refuse_journey(monkeypatch) -> None:
    tool = _JourneyTool()
    monkeypatch.setattr(plugins_api, "get_tool_registry", lambda: _Registry(tool))
    body = plugins_api.ToolExecuteRequest(params={"owner": "user-b"})
    with pytest.raises(HTTPException) as exc:
        await plugins_api.execute_tool(tool.name, body)
    assert exc.value.status_code == 403

    events = [event async for event in plugins_api._execute_stream(tool.name, body.params)]
    assert len(events) == 1
    assert "Journey tools require Test mode" in events[0]
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_playground_listing_and_capability_runner_hide_test_mode(monkeypatch) -> None:
    tool = _JourneyTool()
    monkeypatch.setattr(plugins_api, "get_tool_registry", lambda: _Registry(tool))
    monkeypatch.setattr(plugins_api, "get_capability_registry", lambda: _Capabilities())
    monkeypatch.setattr(plugins_api, "_discover_plugins", lambda: [])

    listing = await plugins_api.list_plugins()
    assert listing["tools"] == []
    assert listing["capabilities"] == [{"name": "chat"}]

    body = plugins_api.CapabilityExecuteRequest(content="try unsafe test mode")
    events = [event async for event in plugins_api._execute_capability_stream("test", body)]
    assert len(events) == 1
    assert 'status_code":403' in events[0].replace(" ", "")
    assert "authenticated chat workflow" in events[0]
