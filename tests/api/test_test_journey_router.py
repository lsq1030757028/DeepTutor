"""The browser Journey surface derives owner from auth and exposes reads only."""

from __future__ import annotations

import json

import pytest

from deeptutor.core.tool_protocol import ToolResult
from deeptutor.services.mcp.manager import wrapped_tool_name


class _Manager:
    async def ensure_started(self) -> None:
        return None


class _Tool:
    def __init__(self) -> None:
        self.contexts = []
        self.calls = []

    async def execute(self, **kwargs):
        from deeptutor.services.test_journey.trust import (
            current_trusted_journey_context,
        )

        self.contexts.append(current_trusted_journey_context())
        self.calls.append(kwargs)
        return ToolResult(content=json.dumps({"ok": True, "code": "OK", "batches": []}))


class _Registry:
    def __init__(self, tools):
        self.tools = tools

    def get(self, name):
        return self.tools.get(name)


@pytest.mark.asyncio
async def test_browser_routes_bind_current_owner_and_only_expose_safe_actions(monkeypatch) -> None:
    from deeptutor.api.routers import test_journey

    list_tool = _Tool()
    get_tool = _Tool()
    trace_tool = _Tool()
    registry = _Registry(
        {
            wrapped_tool_name("test-partner", "journey_list_batches"): list_tool,
            wrapped_tool_name("test-partner", "journey_get_batch"): get_tool,
            wrapped_tool_name("test-partner", "journey_open_trace"): trace_tool,
        }
    )
    owner = {"value": "user-a"}
    monkeypatch.setattr(test_journey, "get_mcp_manager", lambda: _Manager())
    monkeypatch.setattr(test_journey, "get_tool_registry", lambda: registry)
    monkeypatch.setattr(test_journey, "current_owner_id", lambda: owner["value"])

    first = await test_journey.list_batches()
    owner["value"] = "user-b"
    second = await test_journey.get_batch("b-a")
    third = await test_journey.open_trace(
        "b-a",
        "r-a",
        test_journey.TraceOpenRequest(trace_rel="case-a/trace.zip"),
    )

    assert first["ok"] and second["ok"] and third["ok"]
    assert list_tool.contexts[0].owner_id == "user-a"
    assert list_tool.contexts[0].surface == "workbench"
    assert get_tool.contexts[0].owner_id == "user-b"
    assert get_tool.calls == [{"batch_id": "b-a"}]
    assert trace_tool.contexts[0].owner_id == "user-b"
    assert trace_tool.contexts[0].surface == "workbench"
    assert trace_tool.calls == [
        {
            "batch_id": "b-a",
            "run_id": "r-a",
            "trace_rel": "case-a/trace.zip",
        }
    ]
    paths = {route.path for route in test_journey.router.routes}
    assert paths == {
        "/batches",
        "/batches/{batch_id}",
        "/batches/{batch_id}/runs/{run_id}/trace",
    }


def test_browser_routes_use_the_runtime_name_for_a_hyphenated_mcp_server() -> None:
    assert wrapped_tool_name(
        "test-partner", "journey_list_batches"
    ) == "mcp_test-partner_journey_list_batches"
