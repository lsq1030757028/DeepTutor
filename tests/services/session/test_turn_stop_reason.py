"""A turn's terminal state is recorded as a structured ``stop``.

``turns.status`` already has exactly three terminal values. These tests pin the
``stop`` envelope to those three, one leg each, so the two records cannot drift
apart: a reader that trusts ``stop.kind`` and a reader that trusts
``turns.status`` must reach the same conclusion.

The ``completed`` leg lives with the loop that produces it
(``tests/agents/chat/test_call_trace_steps.py``); the two abnormal legs are
here because they are stamped by the turn runtime, not by the loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from deeptutor.core.stream import StreamEvent, StreamEventType
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.session.turn_runtime import TurnRuntimeManager, _TurnExecution

pytestmark = pytest.mark.asyncio


def _payload(session_id: str) -> dict[str, Any]:
    return {
        "type": "start_turn",
        "session_id": session_id,
        "capability": "chat",
        "content": "hello",
        "tools": [],
        "knowledge_bases": [],
        "attachments": [],
        "language": "en",
        "config": {},
    }


class _Orchestrator:
    """Stands in for ChatOrchestrator; ``handle`` raises what it was given."""

    raised: BaseException = RuntimeError("boom")

    async def handle(self, _context):
        raise type(self).raised
        yield  # pragma: no cover - makes this an async generator


async def _run_turn(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    exc: BaseException,
) -> tuple[list[StreamEvent], dict[str, Any] | None]:
    monkeypatch.setattr(
        "deeptutor.services.path_service.PathService._instance",
        None,
        raising=False,
    )
    store = SQLiteSessionStore(tmp_path / "chat_history.db")
    runtime = TurnRuntimeManager(store)
    session = await store.ensure_session(None)
    turn = await store.create_turn(session["id"], capability="chat")
    execution = _TurnExecution(
        turn_id=turn["id"],
        session_id=session["id"],
        capability="chat",
        payload=_payload(session["id"]),
    )
    runtime._executions[turn["id"]] = execution

    _Orchestrator.raised = exc
    monkeypatch.setattr("deeptutor.runtime.orchestrator.ChatOrchestrator", _Orchestrator)

    published: list[StreamEvent] = []
    original = runtime._publish_live_event

    async def _spy(execution_, event: StreamEvent):
        published.append(event)
        return await original(execution_, event)

    monkeypatch.setattr(runtime, "_publish_live_event", _spy)

    try:
        await runtime._run_turn(execution)
    except asyncio.CancelledError:
        pass
    return published, await store.get_turn(turn["id"])


def _terminal(events: list[StreamEvent]) -> StreamEvent:
    return [
        event
        for event in events
        if event.type == StreamEventType.ERROR and event.metadata.get("turn_terminal")
    ][-1]


async def test_a_cancelled_turn_stops_interrupted(tmp_path, monkeypatch) -> None:
    events, turn = await _run_turn(tmp_path, monkeypatch, asyncio.CancelledError())

    terminal = _terminal(events)
    assert terminal.metadata["status"] == "cancelled"
    assert terminal.metadata["stop"] == {"kind": "interrupted", "code": "USER_CANCELLED"}
    assert turn is not None and turn["status"] == "cancelled"


async def test_a_missing_credential_failure_is_classified(tmp_path, monkeypatch) -> None:
    exc = RuntimeError('no API key for provider route "deepseek-official"')
    events, turn = await _run_turn(tmp_path, monkeypatch, exc)

    terminal = _terminal(events)
    assert terminal.metadata["status"] == "failed"
    stop = terminal.metadata["stop"]
    assert stop["kind"] == "error"
    assert stop["code"] == "MISSING_CREDENTIAL"
    assert turn is not None and turn["status"] == "failed"


async def test_an_auth_failure_keeps_its_status_and_masks_the_key(tmp_path, monkeypatch) -> None:
    exc = RuntimeError(
        "Error code: 401 - Authentication Fails, Your api key: sk-live-9f2ab7c41d3e88be is invalid"
    )
    events, turn = await _run_turn(tmp_path, monkeypatch, exc)

    stop = _terminal(events).metadata["stop"]
    assert stop["kind"] == "error"
    assert stop["code"] == "AUTH"
    assert stop["status"] == 401
    assert "sk-live-9f2ab7c41d3e88be" not in stop["message"]
    assert turn is not None and turn["status"] == "failed"
