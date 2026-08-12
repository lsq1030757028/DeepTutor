# -*- coding: utf-8 -*-
"""Verify one-time user decisions for Journey business gates.

The bridge proves which authenticated Test turn made a tool call.  This module
separately proves that the same turn really paused on an interactive ``ask_user``
card and received the exact selection now being recorded.  Model-authored
``confirmed_via`` strings are audit labels only; they carry no authority.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any

from server.journey import pw_runtime
from server.journey.bridge_auth import (
    BRIDGE_SECRET_ENV, MIN_SECRET_BYTES, BridgeClaims, arguments_sha256,
)

AUDIENCE = "test-partner-user-decision"
MAX_TTL_S = 300
MAX_CLOCK_SKEW_S = 5
MAX_WRITE_CHOICES = 8
REQUIREMENT_ENTITY_QUESTION_ID = "journey_requirement_entity"
REQUIREMENT_ENTITY_PROMPTS = frozenset({
    "需求真正要新增、修改或删除的业务实体是什么？",
    "Which business entity does this requirement actually create, update, or delete?",
})


class DecisionAuthError(ValueError):
    pass


def _secret() -> bytes:
    raw = os.environ.get(BRIDGE_SECRET_ENV, "").encode("utf-8")
    if len(raw) < MIN_SECRET_BYTES:
        raise DecisionAuthError("Journey decision signing key is not configured")
    return raw


def _decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise DecisionAuthError("user decision receipt is malformed")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (TypeError, ValueError) as exc:
        raise DecisionAuthError("user decision receipt is malformed") from exc


def _effective_write(case: dict[str, Any]) -> bool:
    recipe = ((case.get("automation") or {}).get("recipe") or {})
    return pw_runtime.effective_write_risk({
        "writes": bool((case.get("side_effects") or {}).get("writes")),
        "actions": list(recipe.get("actions") or []),
    })


def expected_question(batch_id: str, caseset: dict[str, Any]) -> dict[str, Any]:
    write_cases = [c for c in caseset.get("cases") or [] if _effective_write(c)]
    if len(write_cases) > MAX_WRITE_CHOICES:
        raise DecisionAuthError(
            f"write confirmation card supports at most {MAX_WRITE_CHOICES} cases")
    def description(case: dict[str, Any]) -> str:
        value = f"{case.get('source_case_digest', '')} | {case.get('title', '')}"
        # Match ask_user's model-facing normalizer.  Long business titles must
        # fail neither open nor closed merely because the card truncates them.
        return value if len(value) <= 200 else value[:200].rstrip() + "…"

    return {
        "id": f"journey_write_confirm:{batch_id}:{caseset.get('caseset_id', '')}",
        "multi_select": True,
        "allow_free_text": False,
        "options": [
            {
                "label": str(case.get("case_id") or ""),
                "description": description(case),
            }
            for case in write_cases
        ],
    }


def _verify_envelope(
    token: Any,
    *,
    bridge: BridgeClaims,
    tool: str,
    arguments: dict[str, Any],
    now: int | None = None,
) -> dict[str, Any]:
    if not isinstance(token, str) or not token.strip():
        raise DecisionAuthError("a verified interactive user decision is required")
    parts = token.strip().split(".")
    if len(parts) != 2:
        raise DecisionAuthError("user decision receipt is malformed")
    raw = _decode(parts[0])
    supplied = _decode(parts[1])
    if not hmac.compare_digest(supplied, hmac.new(_secret(), raw, hashlib.sha256).digest()):
        raise DecisionAuthError("user decision signature does not match")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionAuthError("user decision payload is malformed") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise DecisionAuthError("unsupported user decision version")
    if payload.get("iss") != "deeptutor" or payload.get("aud") != AUDIENCE:
        raise DecisionAuthError("user decision issuer or audience does not match")
    if payload.get("tool") != tool or bridge.tool != tool:
        raise DecisionAuthError("user decision tool does not match")
    if payload.get("args_sha256") != arguments_sha256(arguments):
        raise DecisionAuthError("user decision arguments do not match this tool call")
    identity = (
        str(payload.get("owner_id") or ""),
        str(payload.get("session_id") or ""),
        str(payload.get("turn_id") or ""),
    )
    if identity != (bridge.owner, bridge.session, bridge.turn):
        raise DecisionAuthError("user decision belongs to another owner/session/turn")
    if payload.get("capability") != "test" or payload.get("surface") != "capability":
        raise DecisionAuthError("user decision did not originate in Test capability")
    jti = str(payload.get("jti") or "").strip()
    tool_call_id = str(payload.get("ask_user_tool_call_id") or "").strip()
    if not jti or not tool_call_id:
        raise DecisionAuthError("user decision identity is incomplete")
    current = int(time.time()) if now is None else int(now)
    try:
        issued, expires = int(payload["iat"]), int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DecisionAuthError("user decision timestamps are invalid") from exc
    if issued > current + MAX_CLOCK_SKEW_S or expires < current:
        raise DecisionAuthError("user decision is not currently valid")
    if expires <= issued or expires - issued > MAX_TTL_S:
        raise DecisionAuthError("user decision lifetime is invalid")
    try:
        resolved_at = int(payload["resolved_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DecisionAuthError("user decision resolution time is invalid") from exc
    if resolved_at > issued + MAX_CLOCK_SKEW_S or issued - resolved_at > MAX_TTL_S:
        raise DecisionAuthError("user decision is stale or was signed before it resolved")
    payload["_verified_jti"] = jti
    payload["_verified_tool_call_id"] = tool_call_id
    return payload


def verify_decision_context(
    token: Any,
    *,
    bridge: BridgeClaims,
    batch_id: str,
    caseset: dict[str, Any],
    requested_case_ids: list[str],
    arguments: dict[str, Any],
    now: int | None = None,
) -> dict[str, Any]:
    payload = _verify_envelope(
        token, bridge=bridge, tool="journey_write_confirm",
        arguments=arguments, now=now)

    ask_user = payload.get("ask_user")
    questions = ask_user.get("questions") if isinstance(ask_user, dict) else None
    if not isinstance(questions, list) or len(questions) != 1 or not isinstance(questions[0], dict):
        raise DecisionAuthError("write decision must come from one structured question")
    actual = questions[0]
    expected = expected_question(batch_id, caseset)
    for field in ("id", "multi_select", "allow_free_text", "options"):
        if actual.get(field) != expected[field]:
            raise DecisionAuthError(f"write decision card {field} does not match current caseset")
    answers = payload.get("answers")
    if not isinstance(answers, list) or len(answers) != 1 or not isinstance(answers[0], dict):
        raise DecisionAuthError("write decision answer is missing")
    if str(answers[0].get("questionId") or "") != expected["id"]:
        raise DecisionAuthError("write decision answer belongs to another question")
    selected = [part.strip() for part in str(answers[0].get("text") or "").split(",")
                if part.strip()]
    allowed = {option["label"] for option in expected["options"]}
    if len(selected) != len(set(selected)) or any(case_id not in allowed for case_id in selected):
        raise DecisionAuthError("write decision contains an invalid or duplicate choice")
    if set(selected) != set(requested_case_ids):
        raise DecisionAuthError("tool case_ids do not match the user's selected choices")
    return {
        "jti": payload["_verified_jti"],
        "ask_user_tool_call_id": payload["_verified_tool_call_id"],
        "selected": selected,
        "question_id": expected["id"],
        "owner": bridge.owner,
        "session": bridge.session,
        "turn": bridge.turn,
    }


def expected_requirement_entity_question() -> dict[str, Any]:
    return {
        "id": REQUIREMENT_ENTITY_QUESTION_ID,
        "multi_select": False,
        "allow_free_text": True,
        "options": [],
    }


def verify_requirement_entity_context(
    token: Any,
    *,
    bridge: BridgeClaims,
    requirement_entity: str,
    arguments: dict[str, Any],
    now: int | None = None,
) -> dict[str, Any]:
    entity = str(requirement_entity or "").strip()
    if not entity or len(entity) > 120:
        raise DecisionAuthError("requirement entity is missing or too long")
    payload = _verify_envelope(
        token, bridge=bridge, tool="journey_ingest",
        arguments=arguments, now=now)
    ask_user = payload.get("ask_user")
    questions = ask_user.get("questions") if isinstance(ask_user, dict) else None
    if not isinstance(questions, list) or len(questions) != 1 \
            or not isinstance(questions[0], dict):
        raise DecisionAuthError(
            "requirement entity must come from one structured question")
    actual = questions[0]
    expected = expected_requirement_entity_question()
    for field in ("id", "multi_select", "allow_free_text", "options"):
        if actual.get(field) != expected[field]:
            raise DecisionAuthError(
                f"requirement entity card {field} does not match the trusted contract")
    if str(actual.get("prompt") or "") not in REQUIREMENT_ENTITY_PROMPTS:
        raise DecisionAuthError("requirement entity card prompt does not match the trusted contract")
    answers = payload.get("answers")
    if not isinstance(answers, list) or len(answers) != 1 \
            or not isinstance(answers[0], dict):
        raise DecisionAuthError("requirement entity answer is missing")
    if str(answers[0].get("questionId") or "") != expected["id"]:
        raise DecisionAuthError("requirement entity answer belongs to another question")
    selected = str(answers[0].get("text") or "").strip()
    if selected != entity:
        raise DecisionAuthError(
            "tool requirement_entity does not match the user's exact answer")
    return {
        "jti": payload["_verified_jti"],
        "ask_user_tool_call_id": payload["_verified_tool_call_id"],
        "selected": selected,
        "question_id": expected["id"],
        "owner": bridge.owner,
        "session": bridge.session,
        "turn": bridge.turn,
    }


__all__ = [
    "DecisionAuthError", "REQUIREMENT_ENTITY_PROMPTS",
    "REQUIREMENT_ENTITY_QUESTION_ID", "expected_question",
    "expected_requirement_entity_question", "verify_decision_context",
    "verify_requirement_entity_context",
]
