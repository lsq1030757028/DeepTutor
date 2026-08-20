"""Per-step call trace: what each LLM call was given and why it ended.

These are the batch-1 criteria of the call-trace design. They assert on the
``call_status``/``complete`` events the loop already emitted before this work —
the new fields ride the same event, so a regression shows up as a missing key
rather than as a changed contract.
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
from deeptutor.core.tool_protocol import ToolResult


def _llm_chunk(
    *,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str | None = None,
) -> SimpleNamespace:
    delta_fields: dict[str, Any] = {"content": content}
    if tool_calls is not None:
        delta_fields["tool_calls"] = [
            SimpleNamespace(
                index=call.get("index", index),
                id=call.get("id"),
                function=SimpleNamespace(name=call.get("name"), arguments=call.get("arguments")),
            )
            for index, call in enumerate(tool_calls)
        ]
    else:
        delta_fields["tool_calls"] = None
    return SimpleNamespace(
        choices=[
            SimpleNamespace(delta=SimpleNamespace(**delta_fields), finish_reason=finish_reason)
        ],
        usage=None,
    )


async def _async_llm_stream(chunks: list[SimpleNamespace]):
    for chunk in chunks:
        yield chunk


class _ScriptedChatClient:
    """Replays a script of chunk lists; an ``Exception`` entry is raised."""

    def __init__(self, scripted: list[Any]) -> None:
        self._script = list(scripted)
        self.call_count = 0

        class _Completions:
            def __init__(self, parent: "_ScriptedChatClient") -> None:
                self.parent = parent

            async def create(self, **_kwargs):
                self.parent.call_count += 1
                if not self.parent._script:
                    raise RuntimeError("Scripted client exhausted")
                entry = self.parent._script.pop(0)
                if isinstance(entry, BaseException):
                    raise entry
                return _async_llm_stream(entry)

        class _Chat:
            def __init__(self, parent: "_ScriptedChatClient") -> None:
                self.completions = _Completions(parent)

        self.chat = _Chat(self)


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
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        ]

    async def execute(self, name: str, **_kwargs):
        return ToolResult(content="tool answer", sources=[], metadata={}, success=True)


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


#: ``call_kind`` values the agent loop itself emits. Other capabilities
#: (explore_context's pre-loop briefing) emit their own ``call_status`` events
#: on the same bus; filtering by call_kind keeps these assertions about the
#: loop's own steps.
_LOOP_CALL_KINDS = {"agent_loop_round", "llm_final_response"}


def _completions(events: list[StreamEvent]) -> list[dict[str, Any]]:
    return [
        event.metadata
        for event in events
        if event.type == StreamEventType.PROGRESS
        and event.metadata.get("trace_kind") == "call_status"
        and event.metadata.get("call_state") == "complete"
        and event.metadata.get("call_kind") in _LOOP_CALL_KINDS
    ]


def _result(events: list[StreamEvent]) -> StreamEvent:
    return [event for event in events if event.type == StreamEventType.RESULT][-1]


def _pipeline(monkeypatch: pytest.MonkeyPatch, client: _ScriptedChatClient) -> AgenticChatPipeline:
    pipeline = AgenticChatPipeline(language="en")
    pipeline.registry = _Registry()
    monkeypatch.setattr(pipeline, "_build_openai_client", lambda: client)
    return pipeline


# --- criterion 1: both rounds carry the full step record --------------------


@pytest.mark.asyncio
async def test_narration_and_finish_rounds_carry_the_step_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _ScriptedChatClient(
        [
            [
                _llm_chunk(content="Searching."),
                _llm_chunk(
                    tool_calls=[
                        {
                            "id": "call-1",
                            "name": "web_search",
                            "arguments": json.dumps({"query": "q"}),
                        }
                    ],
                    finish_reason="tool_calls",
                ),
            ],
            [_llm_chunk(content="Done.", finish_reason="stop")],
        ]
    )
    pipeline = _pipeline(monkeypatch, client)
    monkeypatch.setattr(pipeline, "_compose_enabled_tools", lambda _c: ["web_search"])

    events = await _run(
        pipeline,
        UnifiedContext(
            session_id="s1",
            user_message="hi",
            conversation_history=[
                {"role": "user", "content": "earlier"},
                {"role": "assistant", "content": "earlier answer"},
            ],
        ),
    )

    completions = _completions(events)
    assert len(completions) == 2
    for metadata in completions:
        for key in (
            "turn_index",
            "step_index",
            "finish_reason",
            "step_outcome",
            "tool_calls_requested",
            "tools_offered",
        ):
            assert key in metadata, f"{key} missing from {metadata}"
        # One prior user message in the history => this is turn 1.
        assert metadata["turn_index"] == 1

    assert [m["step_index"] for m in completions] == [0, 1]
    assert [m["step_outcome"]["kind"] for m in completions] == ["narration", "finish"]
    assert [m["call_role"] for m in completions] == ["narration", "finish"]
    assert [m["finish_reason"] for m in completions] == ["tool_calls", "stop"]
    assert completions[0]["tool_calls_requested"] == ["web_search"]
    assert completions[1]["tool_calls_requested"] == []
    assert completions[0]["tools_offered"] == 1


# --- criterion 2: forced finishes name themselves ---------------------------


@pytest.mark.asyncio
async def test_budget_forced_finish_is_labelled_forced_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _ScriptedChatClient(
        [
            [
                _llm_chunk(
                    tool_calls=[
                        {
                            "id": "call-1",
                            "name": "web_search",
                            "arguments": json.dumps({"query": "q"}),
                        }
                    ],
                    finish_reason="tool_calls",
                )
            ],
            [_llm_chunk(content="Answer with what I have.", finish_reason="stop")],
        ]
    )
    pipeline = _pipeline(monkeypatch, client)
    pipeline._max_rounds = 1
    monkeypatch.setattr(pipeline, "_compose_enabled_tools", lambda _c: ["web_search"])

    events = await _run(pipeline, UnifiedContext(session_id="s1", user_message="hi"))

    kinds = [m["step_outcome"]["kind"] for m in _completions(events)]
    assert kinds == ["narration", "forced_budget"]
    assert _completions(events)[-1]["step_outcome"]["detail"] == "budget"
    # The salvage round ships no tools on purpose — that is visible, not guessed.
    assert _completions(events)[-1]["tools_offered"] == 0
    stop = _result(events).metadata["stop"]
    assert stop["kind"] == "completed"
    assert stop["step_outcome"]["kind"] == "forced_budget"


@pytest.mark.asyncio
async def test_mid_loop_failure_forced_finish_is_labelled_forced_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _ScriptedChatClient(
        [
            [
                _llm_chunk(
                    tool_calls=[
                        {
                            "id": "call-1",
                            "name": "web_search",
                            "arguments": json.dumps({"query": "q"}),
                        }
                    ],
                    finish_reason="tool_calls",
                )
            ],
            RuntimeError("provider stalled"),
            [_llm_chunk(content="Partial answer.", finish_reason="stop")],
        ]
    )
    pipeline = _pipeline(monkeypatch, client)
    monkeypatch.setattr(pipeline, "_compose_enabled_tools", lambda _c: ["web_search"])

    events = await _run(pipeline, UnifiedContext(session_id="s1", user_message="hi"))

    kinds = [m["step_outcome"]["kind"] for m in _completions(events)]
    assert kinds == ["narration", "forced_error"]
    assert _completions(events)[-1]["step_outcome"]["detail"] == "error"
    assert _result(events).metadata["stop"]["step_outcome"]["kind"] == "forced_error"


@pytest.mark.asyncio
async def test_empty_finish_round_is_labelled_nudge_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool-less round whose text cleans away to nothing gets one prod."""
    client = _ScriptedChatClient(
        [
            [_llm_chunk(content="<think>planning</think>", finish_reason="stop")],
            [_llm_chunk(content="Here is the answer.", finish_reason="stop")],
        ]
    )
    pipeline = _pipeline(monkeypatch, client)
    monkeypatch.setattr(pipeline, "_compose_enabled_tools", lambda _c: [])

    events = await _run(pipeline, UnifiedContext(session_id="s1", user_message="hi"))

    kinds = [m["step_outcome"]["kind"] for m in _completions(events)]
    assert kinds == ["nudge_empty", "finish"]


# --- criterion 3 (completed leg): stop rides the result event ---------------


@pytest.mark.asyncio
async def test_a_normal_turn_stops_completed_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ScriptedChatClient([[_llm_chunk(content="A direct answer.", finish_reason="stop")]])
    pipeline = _pipeline(monkeypatch, client)
    monkeypatch.setattr(pipeline, "_compose_enabled_tools", lambda _c: [])

    events = await _run(pipeline, UnifiedContext(session_id="s1", user_message="hi"))

    stop = _result(events).metadata["stop"]
    assert stop["kind"] == "completed"
    assert stop["code"] == "OK"
    # A clean turn spent no provider-compat retries and had no loop-level
    # ending other than the plain finish.
    assert "retries" not in stop
    assert stop.get("step_outcome") is None
