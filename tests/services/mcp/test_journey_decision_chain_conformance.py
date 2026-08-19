"""The Journey user-decision chain must agree in all three places at once.

This suite exists because the same defect shape aborted this project three times:
**a link in a cross-process chain is missing while both ends test green.**
The user-decision gate spans three declarations that live apart:

1. ``manager.py``'s ``needs_user_decision`` branches — which tool calls demand a
   signed decision before dispatch;
2. ``trust.py``'s ``_USER_DECISION_TOOLS`` — which tools are *allowed* to carry
   one (``sign_user_decision_context`` refuses anything else);
3. ``trust.py``'s ``_TRUSTED_DECISION_QUESTION_PREFIXES`` — which ``ask_user``
   cards are kept as a decision at all.

Drift in any one of them deadlocks the gate in production rather than opening it
(the reply is discarded, so the tool call can never present a decision) — which
is exactly how B-2 shipped. Unit tests on either side cannot see that; only a
three-way conformance assertion can.

Batch 7 adds the free capability surface (``journey_probe_http`` /
``journey_db_query``). Its light confirmation is deliberately **not** part of
this chain: it signs no token, emits no ``write_confirm``, and authorises one
outbound request only. This suite pins that too — if a free-surface tool ever
starts demanding a signed decision, it has quietly become a gate.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from deeptutor.services.mcp.manager import MCPToolAdapter
from deeptutor.services.test_journey import trust
from deeptutor.services.test_journey.trust import (
    JourneyTrustError,
    ResolvedUserDecision,
    TrustedJourneyContext,
    bind_trusted_journey_context,
    sign_user_decision_context,
)

SECRET = "bridge-test-secret-that-is-at-least-32-bytes"

#: The third leg, written out: which ``ask_user`` card belongs to which gate.
#: ``journey_ingest``'s card is named after the decision it carries (the
#: requirement entity), not after the tool — so the correspondence has to be
#: declared rather than derived from the tool name.
CARD_PREFIXES_BY_TOOL: dict[str, tuple[str, ...]] = {
    "journey_write_confirm": ("journey_write_confirm:",),
    "journey_ingest": ("journey_requirement_entity",),
    "journey_adopt": ("journey_adopt:",),
}

#: Arguments that put each tool on its decision-demanding branch. Tools whose
#: gate is conditional (ingest only gates an entity confirmation; adopt exempts
#: the prepare step) need the condition met, or the probe would under-report.
GATE_TRIGGERING_ARGUMENTS: dict[str, dict[str, Any]] = {
    "journey_write_confirm": {"batch_id": "b-1"},
    "journey_ingest": {"batch_id": "b-1", "requirement_entity": "订单"},
    "journey_adopt": {"batch_id": "b-1"},
}

#: Batch 7's free capability surface. Read-only probing, no ledger, no token.
FREE_SURFACE_TOOLS = ("journey_probe_http", "journey_db_query")


class _Manager:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, owner, server, tool, arguments, **kwargs):
        self.calls.append({"tool": tool, "arguments": arguments})
        return '{"ok":true,"code":"OK"}'


def _adapter(manager: _Manager, tool: str) -> MCPToolAdapter:
    return MCPToolAdapter(
        manager=manager,  # type: ignore[arg-type]
        server_name="test-partner",
        original_name=tool,
        description=tool,
        input_schema={
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "requirement_entity": {"type": "string", "default": ""},
                "prepare_adoption": {"type": "boolean", "default": False},
                "url": {"type": "string", "default": ""},
                "sql": {"type": "string", "default": ""},
                "bridge_context": {"type": "string", "default": ""},
                "decision_context": {"type": "string", "default": ""},
            },
        },
        tool_timeout=5,
    )


def _context() -> TrustedJourneyContext:
    return TrustedJourneyContext(
        owner_id="user-a",
        session_id="session-a",
        turn_id="turn-a",
        capability="test",
        surface="capability",
    )


def _manager_decision_tools() -> set[str]:
    """Tool names named inside ``manager.py``'s ``needs_user_decision`` expression.

    Read from the AST rather than from a duplicated list: a fourth branch added
    there shows up here without anyone remembering to update this file, which is
    the entire point of a conformance test.
    """
    source = Path(trust.__file__).resolve().parents[2] / "services" / "mcp" / "manager.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        targets = getattr(node, "targets", None) or []
        named = [t for t in targets if isinstance(t, ast.Name) and t.id == "needs_user_decision"]
        if not named:
            continue
        for inner in ast.walk(node.value):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                if inner.value.startswith("journey_"):
                    found.add(inner.value)
    return found


# ── The three-way lock ────────────────────────────────────────────────────
def test_the_manager_branches_and_the_allowed_tool_set_are_the_same_set():
    manager_side = _manager_decision_tools()
    assert manager_side, "no journey tool names found in needs_user_decision — parser drifted"
    assert manager_side == set(trust._USER_DECISION_TOOLS), (
        "manager.py demands a decision from a different set of tools than trust.py "
        f"will sign for: manager={sorted(manager_side)} "
        f"trust={sorted(trust._USER_DECISION_TOOLS)}"
    )


def test_the_card_prefix_table_covers_exactly_the_decision_tools():
    assert set(CARD_PREFIXES_BY_TOOL) == set(trust._USER_DECISION_TOOLS)
    declared = {prefix for prefixes in CARD_PREFIXES_BY_TOOL.values() for prefix in prefixes}
    assert declared == set(trust._TRUSTED_DECISION_QUESTION_PREFIXES), (
        "a gate's ask_user card is not kept as a decision (or a kept card has no gate) — "
        "this deadlocks the gate in production instead of opening it"
    )


@pytest.mark.parametrize("tool", sorted(trust._USER_DECISION_TOOLS))
def test_every_decision_tool_can_actually_be_signed_for(tool, monkeypatch):
    monkeypatch.setenv(trust.BRIDGE_SECRET_ENV, SECRET)
    decision = ResolvedUserDecision(
        ask_user_tool_call_id="call-1",
        ask_user_payload={"questions": [{"id": "q", "question": "?"}]},
        answers=({"id": "q", "answer": "yes"},),
        resolved_at=1_800_000_000,
        jti="jti-1",
    )
    assert sign_user_decision_context(_context(), decision, tool=tool, arguments={})


# ── Behavioural half: the branch really fires, per tool ───────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(trust._USER_DECISION_TOOLS))
async def test_each_decision_tool_refuses_to_dispatch_without_a_resolved_decision(
    tool, monkeypatch
):
    monkeypatch.setenv(trust.BRIDGE_SECRET_ENV, SECRET)
    manager = _Manager()
    with bind_trusted_journey_context(_context()):
        result = await _adapter(manager, tool).execute(**GATE_TRIGGERING_ARGUMENTS[tool])
    assert result.success is False
    assert manager.calls == [], f"{tool} dispatched without a user decision"


# ── Batch 7: the free surface is not a gate ───────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", FREE_SURFACE_TOOLS)
async def test_the_free_surface_dispatches_without_a_signed_decision(tool, monkeypatch):
    """AC26's light confirmation is a conversation, not a token.

    It authorises one outbound request, does not accumulate, does not cross
    turns, and grants no Journey write authority. If a free-surface tool starts
    demanding a signed decision it has silently become a gate — and, worse, a
    gate with no card, which is the deadlock shape from B-2.
    """
    monkeypatch.setenv(trust.BRIDGE_SECRET_ENV, SECRET)
    manager = _Manager()
    with bind_trusted_journey_context(_context()):
        result = await _adapter(manager, tool).execute(url="https://api.example.com/x")
    assert result.success is True
    assert manager.calls and manager.calls[0]["tool"] == tool
    assert "decision_context" not in manager.calls[0]["arguments"]
    assert manager.calls[0]["arguments"]["bridge_context"], "free surface skipped bridge signing"


@pytest.mark.parametrize("tool", FREE_SURFACE_TOOLS)
def test_the_free_surface_cannot_be_handed_a_signed_decision(tool, monkeypatch):
    monkeypatch.setenv(trust.BRIDGE_SECRET_ENV, SECRET)
    decision = ResolvedUserDecision(
        ask_user_tool_call_id="call-1",
        ask_user_payload={"questions": [{"id": "q", "question": "?"}]},
        answers=({"id": "q", "answer": "yes"},),
        resolved_at=1_800_000_000,
        jti="jti-1",
    )
    with pytest.raises(JourneyTrustError):
        sign_user_decision_context(_context(), decision, tool=tool, arguments={})


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", FREE_SURFACE_TOOLS)
async def test_the_free_surface_is_still_inside_the_test_capability_boundary(
    tool, monkeypatch
):
    """No ledger does not mean no boundary: a chat turn must not reach these."""
    monkeypatch.setenv(trust.BRIDGE_SECRET_ENV, SECRET)
    manager = _Manager()
    chat_turn = TrustedJourneyContext(
        owner_id="user-a",
        session_id="session-a",
        turn_id="turn-a",
        capability="chat",
        surface="capability",
    )
    with bind_trusted_journey_context(chat_turn):
        result = await _adapter(manager, tool).execute(url="https://api.example.com/x")
    assert result.success is False
    assert manager.calls == []
