"""Capability assembly is stated, not left to be inferred.

Two things are asserted here and they are different things:

* the **event** — what a reader sees in the turn's trace;
* the **rendered system prompt** — what the model was actually told.

The second is asserted against the rendered string, never against source text,
for the reason written up in ``deeptutor/agents/test/pipeline.py``: a test that
greps the source proves the author typed a word, not that the model received a
sentence.

And a degradation is *not* a turn ending. Every degraded case here also asserts
the turn still completed with ``stop.kind == "completed"`` — a non-fatal
assembly problem being promoted to a turn-terminal state would be a worse bug
than the silence it replaced.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream import StreamEvent, StreamEventType
from deeptutor.core.stream_bus import StreamBus
from deeptutor.core.tool_protocol import BaseTool, ToolDefinition, ToolResult
from deeptutor.runtime.providers import view as view_module
from deeptutor.runtime.registry.tool_registry import ToolRegistry

pytestmark = pytest.mark.asyncio


# --- scaffolding ------------------------------------------------------------


class _McpTool(BaseTool):
    deferred = True

    def __init__(self, name: str, provider: str = "gh") -> None:
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
        self.scope_delay = 0.0
        self.rows: list[dict[str, Any]] = []

    async def ensure_started(self) -> None:
        return None

    async def ensure_scope(self, _owner: str) -> list[_McpTool]:
        if self.scope_delay:
            await asyncio.sleep(self.scope_delay)
        return []

    def status(self, _owner: str = "") -> list[dict[str, Any]]:
        return list(self.rows)


def _llm_chunk(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )


async def _one_chunk_stream():
    yield _llm_chunk("An answer.")


class _Client:
    def __init__(self) -> None:
        class _Completions:
            async def create(self, **_kwargs):
                return _one_chunk_stream()

        self.chat = SimpleNamespace(completions=_Completions())


@pytest.fixture(autouse=True)
def _stub_providers(monkeypatch) -> _FakeManager:
    manager = _FakeManager()
    monkeypatch.setattr("deeptutor.services.mcp.get_mcp_manager", lambda: manager)
    monkeypatch.setattr("deeptutor.services.mcp.load_loaded_tools", lambda session_id: set())
    monkeypatch.setattr("deeptutor.multi_user.tool_access.allowed_mcp_tools", lambda: None)
    monkeypatch.setattr(
        "deeptutor.agents.chat.agentic_pipeline.get_llm_config",
        lambda: SimpleNamespace(
            binding="openai",
            model="gpt-test",
            api_key="k",
            base_url="u",
            api_version=None,
            extra_headers={},
            reasoning_effort=None,
        ),
    )
    return manager


def _pipeline(monkeypatch) -> AgenticChatPipeline:
    registry = ToolRegistry()
    registry.register(_McpTool("mcp_gh_search"))
    pipeline = AgenticChatPipeline(language="en")
    pipeline.registry = registry
    monkeypatch.setattr(pipeline, "_compose_enabled_tools", lambda _c: [])
    monkeypatch.setattr(pipeline, "_build_openai_client", lambda: _Client())
    monkeypatch.setattr(pipeline, "_current_owner_id", staticmethod(lambda: "u_ada"))
    return pipeline


async def _run(pipeline: AgenticChatPipeline, context: UnifiedContext) -> list[StreamEvent]:
    bus = StreamBus()
    events: list[StreamEvent] = []

    async def _consume() -> None:
        async for event in bus.subscribe():
            events.append(event)

    consumer = asyncio.create_task(_consume())
    await asyncio.sleep(0)
    await pipeline.run(context, bus)
    await asyncio.sleep(0)
    await bus.close()
    await consumer
    return events


def _health_event(events: list[StreamEvent]) -> StreamEvent:
    return [
        event
        for event in events
        if event.type == StreamEventType.PROGRESS
        and event.metadata.get("trace_kind") == "channel_health"
    ][0]


def _result(events: list[StreamEvent]) -> StreamEvent:
    return [event for event in events if event.type == StreamEventType.RESULT][-1]


def _system_prompt(pipeline: AgenticChatPipeline, context: UnifiedContext) -> str:
    return pipeline._build_system_prompt([], context)


# --- criterion 1 + 3: a degraded scope is stated and is NOT a turn ending ----


async def test_a_timed_out_owner_scope_is_stated_without_ending_the_turn(
    monkeypatch, _stub_providers
) -> None:
    monkeypatch.setattr(view_module, "_OWNER_SCOPE_TIMEOUT_S", 0.01)
    _stub_providers.scope_delay = 1.0
    pipeline = _pipeline(monkeypatch)
    context = UnifiedContext(session_id="s1", user_message="hi")

    events = await _run(pipeline, context)

    metadata = _health_event(events).metadata
    assert metadata["assembly_state"] == "degraded"
    assert metadata["degradations"][0]["stage"] == "owner_scope"
    assert metadata["degradations"][0]["reason"] == "timeout"
    assert metadata["owner_scope_timeout_s"] == 0.01
    assert metadata["status_key"] == "channel_health_degraded"

    # The degradation is non-fatal, and stays that way.
    result = _result(events)
    assert result.metadata["completed"] is True
    assert result.metadata["stop"]["kind"] == "completed"
    assert result.metadata["stop"]["code"] == "OK"


# --- criterion 2: the model is actually told -------------------------------


async def test_the_degraded_assembly_reaches_the_rendered_system_prompt(
    monkeypatch, _stub_providers
) -> None:
    monkeypatch.setattr(view_module, "_OWNER_SCOPE_TIMEOUT_S", 0.01)
    _stub_providers.scope_delay = 1.0
    pipeline = _pipeline(monkeypatch)
    context = UnifiedContext(session_id="s1", user_message="hi")

    await _run(pipeline, context)
    prompt = _system_prompt(pipeline, context)

    assert "## channel_health" in prompt
    assert "did NOT complete in full" in prompt
    assert "`owner_scope`" in prompt
    # States what assembly observed; does not hand the model a verdict about
    # whether the servers are reachable.
    assert "not a finding about whether those servers are reachable" in prompt


# --- criterion 4: the healthy turn is stated too ---------------------------


async def test_a_healthy_assembly_is_stated_on_the_normal_path(monkeypatch) -> None:
    pipeline = _pipeline(monkeypatch)
    context = UnifiedContext(session_id="s1", user_message="hi")

    events = await _run(pipeline, context)

    metadata = _health_event(events).metadata
    assert metadata["assembly_state"] == "ok"
    assert metadata["degradations"] == []
    assert metadata["status_key"] == "channel_health_ok"

    prompt = _system_prompt(pipeline, context)
    assert "## channel_health" in prompt
    assert "completed normally" in prompt


async def test_the_event_carries_no_content_so_it_stays_out_of_the_conversation(
    monkeypatch,
) -> None:
    """``progress`` with non-empty content and a non-``call_status`` trace_kind
    is treated as conversation-worthy by the trace panel. This event is
    diagnostics; the readable line rides ``status_key`` instead."""
    pipeline = _pipeline(monkeypatch)

    events = await _run(pipeline, UnifiedContext(session_id="s1", user_message="hi"))

    event = _health_event(events)
    assert event.content == ""
    assert event.metadata["status_key"]
    assert event.metadata["call_id"]  # trace panel drops events without one


async def test_the_pipelines_own_fallback_also_reports_a_degradation(monkeypatch) -> None:
    """The fifth site: the pipeline's defence-in-depth catch.

    ``build_tool_view`` is contractually non-raising, so this catch should be
    unreachable — which is exactly why it must not be the one place that goes
    back to reporting nothing.
    """

    async def _boom(**_kwargs):
        raise RuntimeError("assembly blew up before returning")

    monkeypatch.setattr("deeptutor.agents.chat.agentic_pipeline.build_tool_view", _boom)
    pipeline = _pipeline(monkeypatch)

    events = await _run(pipeline, UnifiedContext(session_id="s1", user_message="hi"))

    metadata = _health_event(events).metadata
    assert metadata["assembly_state"] == "degraded"
    assert [item["stage"] for item in metadata["degradations"]] == ["tool_view"]
    assert _result(events).metadata["stop"]["kind"] == "completed"


# --- criterion 5: write-side masking ---------------------------------------


async def test_a_server_error_string_is_masked_before_it_reaches_the_event(
    monkeypatch, _stub_providers
) -> None:
    _stub_providers.rows = [
        {
            "name": "fastmcp",
            "transport": "http",
            "status": "error",
            "error": "handshake rejected (token=abc123)",
            "tools": [{"name": "x", "description": "y"}],
        }
    ]
    pipeline = _pipeline(monkeypatch)

    events = await _run(pipeline, UnifiedContext(session_id="s1", user_message="hi"))

    metadata = _health_event(events).metadata
    assert metadata["mcp_servers"][0]["name"] == "fastmcp"
    assert metadata["mcp_servers"][0]["status"] == "error"
    assert "abc123" not in json.dumps(metadata, ensure_ascii=False, default=str)


# --- criterion 6: the new block does not pollute the existing token account --


async def test_the_new_block_gets_its_own_segment_and_leaves_capability_alone(
    monkeypatch,
) -> None:
    pipeline = _pipeline(monkeypatch)
    context = UnifiedContext(session_id="s1", user_message="hi")
    await _run(pipeline, context)

    from deeptutor.agents.chat.context_budget import LLMRequestSnapshot, build_context_budget

    blocks = pipeline._last_prompt_blocks
    assert any(block.name == "channel_health" for block in blocks)

    request = LLMRequestSnapshot(messages=[], tool_schemas=[])
    with_block = build_context_budget(blocks=blocks, request=request, model="gpt-test")
    without_block = build_context_budget(
        blocks=[block for block in blocks if block.name != "channel_health"],
        request=request,
        model="gpt-test",
    )

    def _segment(budget: dict[str, Any], key: str) -> int:
        for segment in budget["segments"]:
            if segment["key"] == key:
                return int(segment["tokens"])
        return 0

    assert _segment(with_block, "channel_health") > 0
    # An unregistered block name would have been summed under ``capability``,
    # silently moving a number the composer already shows.
    assert _segment(with_block, "capability") == _segment(without_block, "capability")
