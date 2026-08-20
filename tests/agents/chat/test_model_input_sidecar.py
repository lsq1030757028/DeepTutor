"""End to end: the bodies go to the sidecar, the events keep a reference.

The load-bearing assertion in this file is the negative one. A system prompt on
the event stream would be stored twice per call — once in ``turn_events`` and
once in the assistant message's ``events_json`` — so this drives a real turn
through the turn runtime and greps *both* stores for a sentinel planted in the
prompt. Asserting on the emitted events instead would prove the emitter, not
the stores.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
from deeptutor.core.context import UnifiedContext
from deeptutor.core.observability import trace_sidecar
from deeptutor.core.observability.trace_sidecar import SessionTraceSidecar
from deeptutor.core.stream import StreamEvent, StreamEventType
from deeptutor.core.stream_bus import StreamBus
from deeptutor.core.tool_protocol import ToolResult
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.session.turn_runtime import TurnRuntimeManager, _TurnExecution

pytestmark = pytest.mark.asyncio

#: Planted in the system prompt; must reach the sidecar and nothing else.
SENTINEL = "ZZ-PROMPT-SENTINEL-8f31c2-ZZ"


# --- scaffolding ------------------------------------------------------------


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


class _Client:
    def __init__(self) -> None:
        self.call_count = 0

        class _Completions:
            def __init__(self, parent: "_Client") -> None:
                self.parent = parent

            async def create(self, **_kwargs):
                self.parent.call_count += 1

                async def _stream():
                    yield _llm_chunk("An answer.")

                return _stream()

        self.chat = SimpleNamespace(completions=_Completions(self))


class _Registry:
    def deferred_tools(self):
        return []

    def build_prompt_text(self, _enabled, **_kwargs):
        return "- `web_search` - Search the web"

    def build_openai_schemas(self, _enabled):
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    async def execute(self, name: str, **_kwargs):
        return ToolResult(content="ok", sources=[], metadata={}, success=True)


@pytest.fixture(autouse=True)
def _fake_llm_config(monkeypatch: pytest.MonkeyPatch) -> None:
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


def _pipeline(monkeypatch, client: _Client) -> AgenticChatPipeline:
    pipeline = AgenticChatPipeline(language="en")
    pipeline.registry = _Registry()
    monkeypatch.setattr(pipeline, "_compose_enabled_tools", lambda _c: ["web_search"])
    monkeypatch.setattr(pipeline, "_build_openai_client", lambda: client)
    # Plant the sentinel inside the rendered system prompt.
    original = pipeline._build_system_prompt

    def _with_sentinel(*args, **kwargs):
        return f"{SENTINEL}\n{original(*args, **kwargs)}"

    monkeypatch.setattr(pipeline, "_build_system_prompt", _with_sentinel)
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


def _model_inputs(events: list[StreamEvent]) -> list[dict[str, Any]]:
    return [
        event.metadata
        for event in events
        if event.type == StreamEventType.PROGRESS
        and event.metadata.get("trace_kind") == "model_input"
    ]


def _completions(events: list[StreamEvent]) -> list[dict[str, Any]]:
    return [
        event.metadata
        for event in events
        if event.type == StreamEventType.PROGRESS
        and event.metadata.get("trace_kind") == "call_status"
        and event.metadata.get("call_state") == "complete"
        and event.metadata.get("call_kind") in {"agent_loop_round", "llm_final_response"}
    ]


# --- the event carries a reference, and the reference resolves ---------------


async def test_the_model_input_event_describes_the_request_without_carrying_it(
    monkeypatch,
) -> None:
    pipeline = _pipeline(monkeypatch, _Client())
    events = await _run(pipeline, UnifiedContext(session_id="sess-e2e", user_message="hi"))

    inputs = _model_inputs(events)
    assert len(inputs) == 1
    metadata = inputs[0]

    assert metadata["call_id"]  # the trace panel drops events without one
    assert metadata["tools_in_window"] == ["web_search"]
    assert metadata["turn_index"] == 0
    assert metadata["step_index"] == 0
    assert [segment["name"] for segment in metadata["prompt_segments"]]
    assert all(
        set(segment) == {"name", "tokens", "sha12"} for segment in metadata["prompt_segments"]
    )
    # Shape, never bodies.
    skeleton = metadata["messages_skeleton"]
    assert skeleton[0]["role"] == "system"
    assert skeleton[0]["chars"] > 0
    assert SENTINEL not in json.dumps(metadata, ensure_ascii=False, default=str)


async def test_the_event_carries_no_content_so_it_stays_out_of_the_conversation(
    monkeypatch,
) -> None:
    pipeline = _pipeline(monkeypatch, _Client())
    events = await _run(pipeline, UnifiedContext(session_id="sess-e2e", user_message="hi"))

    event = [
        item
        for item in events
        if item.type == StreamEventType.PROGRESS
        and item.metadata.get("trace_kind") == "model_input"
    ][0]
    assert event.content == ""
    assert event.metadata["status_key"] == "model_input"


async def test_both_events_of_a_call_point_at_the_same_header(monkeypatch) -> None:
    pipeline = _pipeline(monkeypatch, _Client())
    events = await _run(pipeline, UnifiedContext(session_id="sess-e2e", user_message="hi"))

    from_input = _model_inputs(events)[0]["header_ref"]
    from_status = _completions(events)[0]["header_ref"]

    assert from_input == from_status
    assert from_status["mode"] == "recorded"
    assert set(from_status["parts"]) == {"system", "tools", "config"}


async def test_the_header_ref_resolves_to_the_prompt_that_was_sent(monkeypatch) -> None:
    pipeline = _pipeline(monkeypatch, _Client())
    events = await _run(pipeline, UnifiedContext(session_id="sess-e2e", user_message="hi"))

    ref = _model_inputs(events)[0]["header_ref"]
    sidecar = SessionTraceSidecar("sess-e2e", root=trace_sidecar.sidecar_root())

    resolved = sidecar.resolve(ref)
    assert resolved is not None
    assert SENTINEL in resolved["system"]
    assert json.loads(resolved["tools"])[0]["function"]["name"] == "web_search"
    assert json.loads(resolved["config"])["model"] == "gpt-test"


# --- criterion 4: the bodies stay out of the business database ---------------


class _PipelineOrchestrator:
    """Runs the real pipeline and yields its events, like ChatOrchestrator."""

    pipeline: AgenticChatPipeline

    async def handle(self, context: UnifiedContext):
        bus = StreamBus()

        async def _run_pipeline() -> None:
            try:
                await type(self).pipeline.run(context, bus)
            finally:
                await bus.emit(StreamEvent(type=StreamEventType.DONE, source="chat"))
                await bus.close()

        task = asyncio.create_task(_run_pipeline())
        async for event in bus.subscribe():
            yield event
        await task


async def test_the_prompt_body_reaches_neither_turn_events_nor_the_message(
    tmp_path: Path, monkeypatch
) -> None:
    pipeline = _pipeline(monkeypatch, _Client())
    _PipelineOrchestrator.pipeline = pipeline
    monkeypatch.setattr("deeptutor.runtime.orchestrator.ChatOrchestrator", _PipelineOrchestrator)

    store = SQLiteSessionStore(tmp_path / "chat_history.db")
    runtime = TurnRuntimeManager(store)
    session = await store.ensure_session(None)
    turn = await store.create_turn(session["id"], capability="chat")
    execution = _TurnExecution(
        turn_id=turn["id"],
        session_id=session["id"],
        capability="chat",
        payload={
            "type": "start_turn",
            "session_id": session["id"],
            "capability": "chat",
            "content": "hi",
            "tools": [],
            "knowledge_bases": [],
            "attachments": [],
            "language": "en",
            "config": {},
        },
    )
    runtime._executions[turn["id"]] = execution

    await runtime._run_turn(execution)

    turn_events = await store.get_turn_events(turn["id"])
    assert turn_events, "the turn persisted no events; the assertion below would be vacuous"
    assert SENTINEL not in json.dumps(turn_events, ensure_ascii=False, default=str)

    messages = await store.get_messages(session["id"])
    assistant = [message for message in messages if message.get("role") == "assistant"]
    assert assistant, "no assistant message persisted"
    assert SENTINEL not in json.dumps(assistant, ensure_ascii=False, default=str)

    # ...and it did reach the sidecar, so the check above is about placement,
    # not about the capture having quietly stopped working.
    sidecar_file = trace_sidecar.sidecar_root() / session["id"] / "trajectory.jsonl"
    assert SENTINEL in sidecar_file.read_text(encoding="utf-8")


# --- criterion 5 (wiring leg): the written path is outside the workspace ----


async def test_the_written_path_is_outside_the_model_reachable_workspace(monkeypatch) -> None:
    from deeptutor.services.path_service import get_path_service

    pipeline = _pipeline(monkeypatch, _Client())
    await _run(pipeline, UnifiedContext(session_id="sess-e2e", user_message="hi"))

    written = (trace_sidecar.sidecar_root() / "sess-e2e" / "trajectory.jsonl").resolve()
    workspace = get_path_service().get_workspace_dir().resolve()

    assert written.exists()
    assert not str(written).startswith(str(workspace))
