"""Every assembly stage that drops out says so in the returned value.

There are four ``return []`` sites in the provider view plus one
defence-in-depth fallback in the pipeline, and the point of this file is that
each of them is covered separately: fixing one and leaving three is the shape
this change exists to prevent.

The other half of the contract is that nothing here changes the fault
tolerance — ``build_tool_view`` still never raises and the turn still runs on a
smaller tool surface. Each test asserts both halves.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from deeptutor.core.tool_protocol import BaseTool, ToolDefinition, ToolResult
from deeptutor.runtime.providers import view as view_module
from deeptutor.runtime.providers.scope import ToolScope
from deeptutor.runtime.providers.view import ProviderToolView, ToolViewHealth, build_tool_view
from deeptutor.runtime.registry.tool_registry import ToolRegistry


class _McpTool(BaseTool):
    deferred = True

    def __init__(self, name: str, provider: str) -> None:
        self._name = name
        self.provider_id = provider
        self.provider_kind = "mcp"

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self._name,
            description=f"desc {self._name}",
            raw_parameters={"type": "object", "properties": {}},
        )

    async def execute(self, **_kwargs: Any) -> ToolResult:
        return ToolResult(content="ok")


class _FakeManager:
    def __init__(self) -> None:
        self.scopes: dict[str, list[_McpTool]] = {}
        self.scope_delay = 0.0
        self.scope_error: Exception | None = None

    async def ensure_started(self) -> None:
        return None

    async def ensure_scope(self, owner: str) -> list[_McpTool]:
        if self.scope_error is not None:
            raise self.scope_error
        if self.scope_delay:
            await asyncio.sleep(self.scope_delay)
        return self.scopes.get(owner, [])


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_McpTool("mcp_gh_search", "gh"))
    return reg


@pytest.fixture(autouse=True)
def _stub_providers(monkeypatch) -> _FakeManager:
    manager = _FakeManager()
    monkeypatch.setattr("deeptutor.services.mcp.get_mcp_manager", lambda: manager)
    monkeypatch.setattr("deeptutor.services.mcp.load_loaded_tools", lambda session_id: set())
    monkeypatch.setattr("deeptutor.multi_user.tool_access.allowed_mcp_tools", lambda: None)
    return manager


pytestmark = pytest.mark.asyncio


async def test_a_healthy_assembly_reports_ok(registry) -> None:
    view = await build_tool_view(base_registry=registry, scope=ToolScope(session_id="s"))

    assert view.health.state == "ok"
    assert view.health.as_dicts() == []


# --- degradation point 1: owner scope timeout (view.py owner-scope wait) ----


async def test_a_slow_owner_scope_is_reported_as_a_timeout(
    registry, monkeypatch, _stub_providers
) -> None:
    monkeypatch.setattr(view_module, "_OWNER_SCOPE_TIMEOUT_S", 0.01)
    _stub_providers.scope_delay = 1.0
    _stub_providers.scopes["u_ada"] = [_McpTool("mcp_slow_thing", "slow")]

    view = await build_tool_view(
        base_registry=registry,
        scope=ToolScope(owner_id="u_ada", session_id="s"),
    )

    assert view.health.state == "degraded"
    degradation = view.health.as_dicts()[0]
    assert degradation["stage"] == "owner_scope"
    assert degradation["reason"] == "timeout"
    assert degradation["detail"] == "0.01s"
    # Fault tolerance unchanged: the turn keeps the tools it could get.
    assert {tool.get_definition().name for tool in view.pool} == {"mcp_gh_search"}


# --- degradation point 2: owner scope raises -------------------------------


async def test_a_failing_owner_scope_is_reported_as_an_error(registry, _stub_providers) -> None:
    _stub_providers.scope_error = RuntimeError("handshake refused")

    view = await build_tool_view(
        base_registry=registry,
        scope=ToolScope(owner_id="u_ada", session_id="s"),
    )

    degradation = view.health.as_dicts()[0]
    assert degradation["stage"] == "owner_scope"
    assert degradation["reason"] == "error"
    assert "handshake refused" in degradation["detail"]
    assert {tool.get_definition().name for tool in view.pool} == {"mcp_gh_search"}


async def test_an_owner_scope_error_detail_is_masked(registry, _stub_providers) -> None:
    _stub_providers.scope_error = RuntimeError("401 from host, token=abc123")

    view = await build_tool_view(
        base_registry=registry,
        scope=ToolScope(owner_id="u_ada", session_id="s"),
    )

    assert "abc123" not in view.health.as_dicts()[0]["detail"]


# --- degradation point 3: CLI apps -----------------------------------------


async def test_a_failing_cli_app_probe_is_reported(registry, monkeypatch) -> None:
    def _boom(**_kwargs):
        raise RuntimeError("cli catalog unreadable")

    monkeypatch.setattr("deeptutor.services.cli_apps.provider.authorized_apps", _boom)

    view = await build_tool_view(
        base_registry=registry,
        scope=ToolScope(owner_id="u_ada", session_id="s"),
    )

    stages = [item["stage"] for item in view.health.as_dicts()]
    assert stages == ["cli_apps"]
    assert view.health.as_dicts()[0]["reason"] == "error"
    assert {tool.get_definition().name for tool in view.pool} == {"mcp_gh_search"}


# --- degradation point 4: the whole assembly ------------------------------


async def test_a_failed_assembly_is_reported_and_still_never_raises(registry, monkeypatch) -> None:
    monkeypatch.setattr(
        "deeptutor.services.mcp.get_mcp_manager",
        lambda: (_ for _ in ()).throw(RuntimeError("registry exploded")),
    )

    view = await build_tool_view(base_registry=registry, scope=ToolScope(session_id="s"))

    assert isinstance(view, ProviderToolView)  # contract: never raises
    stages = [item["stage"] for item in view.health.as_dicts()]
    assert stages == ["tool_view"]
    assert view.pool == ()


# --- the default keeps every existing construction site valid ---------------


def test_health_defaults_to_ok_so_existing_construction_sites_still_work() -> None:
    view = ProviderToolView.empty(ToolRegistry())
    assert view.health == ToolViewHealth()
    assert view.health.state == "ok"
