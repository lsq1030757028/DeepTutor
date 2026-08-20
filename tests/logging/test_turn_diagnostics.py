"""The log can answer "what happened in this session".

Before this it could not, and the numbers said so: 134 lines across sixteen
days, and zero hits when grepping the identifiers of a session that had
misbehaved. Two separate holes produced that — nothing bound ``session_id`` /
``turn_id``, and nothing wrote the turn's lifecycle — so both are covered here,
end to end through the real formatter rather than against the call sites.

The lines are asserted for what they must NOT contain as well: this log is
grepped by people and copied around more freely than the database is, so a
prompt body or an unmasked token reaching it is a defect, not an inconvenience.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream import StreamEvent, StreamEventType
from deeptutor.core.stream_bus import StreamBus
from deeptutor.core.tool_protocol import ToolResult
from deeptutor.logging import bind_log_context
from deeptutor.logging.formatters import ContextFilter, JsonlFormatter
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.session.turn_runtime import TurnRuntimeManager, _TurnExecution

pytestmark = pytest.mark.asyncio

SENTINEL = "ZZ-PROMPT-SENTINEL-4a91d7-ZZ"
LEAKY_TOKEN = "sk-live-9f2ab7c41d3e88be"


class _CapturingHandler(logging.Handler):
    """A real handler with the production filter + formatter attached.

    Deliberately not ``caplog``: the identifiers are attached by
    ``ContextFilter`` and rendered by ``JsonlFormatter``, so a test that skips
    either would pass while the file on disk stayed unusable.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[dict[str, Any]] = []
        self.addFilter(ContextFilter())
        self.setFormatter(JsonlFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


@pytest.fixture
def captured() -> Any:
    handler = _CapturingHandler()
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


# --- the plumbing ------------------------------------------------------------


def test_bound_identifiers_and_structured_fields_land_in_context(captured) -> None:
    from deeptutor.core.observability.diagnostics import diag

    with bind_log_context(session_id="s-1", turn_id="t-1"):
        diag("llm.call.end", finish_reason="stop", step_index=2)

    entry = captured.lines[-1]
    assert set(entry) >= {"timestamp", "level", "logger", "message", "context"}
    assert entry["message"] == "llm.call.end"
    assert entry["context"]["session_id"] == "s-1"
    assert entry["context"]["turn_id"] == "t-1"
    assert entry["context"]["event"] == "llm.call.end"
    assert entry["context"]["finish_reason"] == "stop"
    assert entry["context"]["step_index"] == 2


def test_a_stray_field_cannot_overwrite_the_turn_a_line_belongs_to(captured) -> None:
    from deeptutor.core.observability.diagnostics import diag

    with bind_log_context(session_id="s-1", turn_id="t-1"):
        diag("tool.result", turn_id="not-this-one", session_id="nor-this")

    entry = captured.lines[-1]
    assert entry["context"]["turn_id"] == "t-1"
    assert entry["context"]["session_id"] == "s-1"


def test_a_credential_in_a_diagnostic_field_is_masked(captured) -> None:
    from deeptutor.core.observability.diagnostics import diag

    diag("tool.error", error=f"auth failed with api_key={LEAKY_TOKEN}")

    assert LEAKY_TOKEN not in json.dumps(captured.lines[-1], ensure_ascii=False)


def test_diagnostics_never_raise(captured) -> None:
    from deeptutor.core.observability.diagnostics import diag

    class _Hostile:
        def __repr__(self) -> str:
            raise ValueError("nope")

    diag("weird", thing=_Hostile())  # must not raise


# --- the lifecycle, over one real turn --------------------------------------


def _llm_chunk(content=None, tool_calls=None, finish_reason=None) -> SimpleNamespace:
    delta: dict[str, Any] = {"content": content, "tool_calls": None}
    if tool_calls:
        delta["tool_calls"] = [
            SimpleNamespace(
                index=index,
                id=call["id"],
                function=SimpleNamespace(name=call["name"], arguments=call["arguments"]),
            )
            for index, call in enumerate(tool_calls)
        ]
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(**delta), finish_reason=finish_reason)],
        usage=None,
    )


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
        return ToolResult(content="tool answer", sources=[], metadata={}, success=True)


class _PipelineOrchestrator:
    pipeline: AgenticChatPipeline

    async def handle(self, context: UnifiedContext):
        bus = StreamBus()

        async def _run() -> None:
            try:
                await type(self).pipeline.run(context, bus)
            finally:
                await bus.emit(StreamEvent(type=StreamEventType.DONE, source="chat"))
                await bus.close()

        task = asyncio.create_task(_run())
        async for event in bus.subscribe():
            yield event
        await task


async def _drive_one_turn(tmp_path: Path, monkeypatch) -> tuple[str, str]:
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

    script = [
        [
            _llm_chunk(content="Looking it up. "),
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
        [_llm_chunk(content="Here it is.", finish_reason="stop")],
    ]

    class _Client:
        def __init__(self) -> None:
            class _Completions:
                async def create(self, **_kwargs):
                    entry = script.pop(0)

                    async def _stream():
                        for chunk in entry:
                            yield chunk

                    return _stream()

            self.chat = SimpleNamespace(completions=_Completions())

    pipeline = AgenticChatPipeline(language="en")
    pipeline.registry = _Registry()
    monkeypatch.setattr(pipeline, "_compose_enabled_tools", lambda _c: ["web_search"])
    monkeypatch.setattr(pipeline, "_build_openai_client", lambda: _Client())
    original = pipeline._build_system_prompt
    monkeypatch.setattr(
        pipeline,
        "_build_system_prompt",
        lambda *a, **k: f"{SENTINEL}\n{original(*a, **k)}",
    )
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
            "content": "look this up",
            "tools": [],
            "knowledge_bases": [],
            "attachments": [],
            "language": "en",
            "config": {},
        },
    )
    runtime._executions[turn["id"]] = execution
    await runtime._run_turn(execution)
    return session["id"], turn["id"]


async def test_one_turn_leaves_a_greppable_trail(tmp_path, monkeypatch, captured) -> None:
    session_id, turn_id = await _drive_one_turn(tmp_path, monkeypatch)

    diag_lines = [line for line in captured.lines if line["logger"] == "deeptutor.diag"]
    events = [line["context"]["event"] for line in diag_lines]

    # Session / turn boundaries, every round's boundaries, every tool call and
    # its result, and what the capability assembly managed.
    assert "turn.start" in events
    assert "turn.end" in events
    assert "assembly.result" in events
    assert events.count("llm.call.start") == 2
    assert events.count("llm.call.end") == 2
    assert events.count("tool.call") == 1
    assert events.count("tool.result") == 1

    # Every one of them is findable by the session that produced it. This is
    # the property that was missing: the fields existed and were always empty.
    for line in diag_lines:
        assert line["context"]["session_id"] == session_id
        assert line["context"]["turn_id"] == turn_id
        assert line["context"]["capability"] == "chat"


async def test_the_trail_carries_the_stop_reason_of_every_call(
    tmp_path, monkeypatch, captured
) -> None:
    await _drive_one_turn(tmp_path, monkeypatch)

    ends = [
        line["context"]
        for line in captured.lines
        if line["logger"] == "deeptutor.diag" and line["context"]["event"] == "llm.call.end"
    ]
    assert [end["finish_reason"] for end in ends] == ["tool_calls", "stop"]
    assert [end["step_outcome"] for end in ends] == ["narration", "finish"]
    assert [end["tool_calls_requested"] for end in ends] == [["web_search"], []]
    assert all("elapsed_ms" in end for end in ends)

    starts = [
        line["context"]
        for line in captured.lines
        if line["logger"] == "deeptutor.diag" and line["context"]["event"] == "llm.call.start"
    ]
    assert [start["step_index"] for start in starts] == [0, 1]
    assert all(start["tools_offered"] == 1 for start in starts)

    turn_end = [
        line["context"]
        for line in captured.lines
        if line["logger"] == "deeptutor.diag" and line["context"]["event"] == "turn.end"
    ][-1]
    assert turn_end["status"] == "completed"


async def test_the_trail_carries_shapes_not_bodies(tmp_path, monkeypatch, captured) -> None:
    await _drive_one_turn(tmp_path, monkeypatch)

    blob = json.dumps(captured.lines, ensure_ascii=False)
    assert SENTINEL not in blob, "the system prompt body reached the diagnostic log"
    assert "tool answer" not in blob, "a tool result body reached the diagnostic log"

    result = [
        line["context"]
        for line in captured.lines
        if line["logger"] == "deeptutor.diag" and line["context"]["event"] == "tool.result"
    ][0]
    # The size is what a reader needs; the text is what they must not get.
    assert result["result_chars"] == len("tool answer")
    assert result["tool"] == "web_search"
    assert result["success"] is True


async def test_a_degraded_assembly_is_in_the_trail(tmp_path, monkeypatch, captured) -> None:
    from deeptutor.runtime.providers import view as view_module

    async def _boom(**_kwargs):
        raise RuntimeError("assembly blew up")

    monkeypatch.setattr(view_module, "_OWNER_SCOPE_TIMEOUT_S", 0.01)
    monkeypatch.setattr("deeptutor.agents.chat.agentic_pipeline.build_tool_view", _boom)

    await _drive_one_turn(tmp_path, monkeypatch)

    assembly = [
        line["context"]
        for line in captured.lines
        if line["logger"] == "deeptutor.diag" and line["context"]["event"] == "assembly.result"
    ][0]
    assert assembly["state"] == "degraded"
    assert [item["stage"] for item in assembly["degradations"]] == ["tool_view"]
