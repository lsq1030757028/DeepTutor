"""Per-call trust assertions for the shared test-partner Journey MCP service.

The MCP transport is deployment-scoped, while Journey artifacts are user-scoped.
Consequently neither the model nor a browser-supplied ``owner`` argument is an
identity. DeepTutor signs the authenticated turn identity and the exact effective
tool arguments immediately before dispatch; test-partner verifies the assertion
before reading or writing any artifact.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Iterator, Mapping

BRIDGE_SECRET_ENV = "TEST_JOURNEY_BRIDGE_SECRET"
BRIDGE_AUDIENCE = "test-partner-journey"
MIN_SECRET_BYTES = 32
DEFAULT_TTL_S = 60
TEST_PARTNER_SERVER = "test-partner"
RESERVED_ARGUMENTS = frozenset(
    {
        "bridge_context",
        "owner",
        "caller_surface",
        "gate_token",
        "session_ref",
        "_trusted_context",
        "decision_context",
    }
)


class JourneyTrustError(RuntimeError):
    """The bridge cannot produce a trustworthy assertion."""


@dataclass(frozen=True, slots=True)
class TrustedJourneyContext:
    owner_id: str
    session_id: str
    turn_id: str
    capability: str
    surface: str = "capability"


@dataclass(frozen=True, slots=True)
class ResolvedUserDecision:
    ask_user_tool_call_id: str
    ask_user_payload: dict[str, Any]
    answers: tuple[dict[str, str], ...]
    resolved_at: int
    jti: str


_CURRENT_CONTEXT: ContextVar[TrustedJourneyContext | None] = ContextVar(
    "deeptutor_trusted_journey_context", default=None
)
_CURRENT_DECISION: ContextVar[ResolvedUserDecision | None] = ContextVar(
    "deeptutor_resolved_user_decision", default=None
)
_TRUSTED_DECISION_QUESTION_PREFIXES = (
    "journey_write_confirm:",
    "journey_requirement_entity",
)


@contextmanager
def bind_trusted_journey_context(
    context: TrustedJourneyContext,
) -> Iterator[TrustedJourneyContext]:
    token = _CURRENT_CONTEXT.set(context)
    decision_token = _CURRENT_DECISION.set(None)
    try:
        yield context
    finally:
        _CURRENT_DECISION.reset(decision_token)
        _CURRENT_CONTEXT.reset(token)


def current_trusted_journey_context() -> TrustedJourneyContext | None:
    return _CURRENT_CONTEXT.get()


def record_resolved_user_decision(
    *,
    ask_user_tool_call_id: str,
    ask_user_payload: Mapping[str, Any],
    answers: list[dict[str, str]] | None,
) -> bool:
    """Remember only a structured Journey gate decision from a real pause/resume.

    Ordinary clarification replies are deliberately discarded: they must never
    be repackaged as write or intake authority by a later model tool call.
    """
    context = current_trusted_journey_context()
    questions = ask_user_payload.get("questions")
    if context is None or not isinstance(questions, list) or len(questions) != 1:
        _CURRENT_DECISION.set(None)
        return False
    question = questions[0]
    qid = str(question.get("id") or "") if isinstance(question, Mapping) else ""
    matching = [
        {"questionId": str(entry.get("questionId") or ""),
         "text": str(entry.get("text") or "")}
        for entry in (answers or [])
        if str(entry.get("questionId") or "") == qid
    ]
    if not any(qid.startswith(prefix) for prefix in _TRUSTED_DECISION_QUESTION_PREFIXES) \
            or len(matching) != 1:
        _CURRENT_DECISION.set(None)
        return False
    # JSON round-trip freezes a plain-data snapshot; no later model mutation can
    # alter what the user actually saw.
    payload = json.loads(json.dumps(
        dict(ask_user_payload), ensure_ascii=False, allow_nan=False))
    _CURRENT_DECISION.set(ResolvedUserDecision(
        ask_user_tool_call_id=str(ask_user_tool_call_id or ""),
        ask_user_payload=payload,
        answers=tuple(matching),
        resolved_at=int(time.time()),
        jti=secrets.token_urlsafe(16),
    ))
    return True


def current_resolved_user_decision() -> ResolvedUserDecision | None:
    return _CURRENT_DECISION.get()


def _secret() -> bytes:
    raw = os.environ.get(BRIDGE_SECRET_ENV, "").encode("utf-8")
    if len(raw) < MIN_SECRET_BYTES:
        raise JourneyTrustError(
            f"{BRIDGE_SECRET_ENV} must contain at least {MIN_SECRET_BYTES} UTF-8 bytes"
        )
    return raw


def bridge_configured() -> bool:
    try:
        _secret()
    except JourneyTrustError:
        return False
    return True


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def canonical_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Return the identity-free argument object covered by the signature."""
    return {
        str(key): value for key, value in arguments.items() if str(key) not in RESERVED_ARGUMENTS
    }


def effective_arguments(
    arguments: Mapping[str, Any],
    input_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Expand schema defaults so omitted and Python-defaulted args hash equally."""
    effective = canonical_arguments(arguments)
    properties = (input_schema or {}).get("properties")
    if isinstance(properties, Mapping):
        for raw_name, raw_spec in properties.items():
            name = str(raw_name)
            if name in RESERVED_ARGUMENTS or name in effective:
                continue
            if isinstance(raw_spec, Mapping) and "default" in raw_spec:
                effective[name] = raw_spec["default"]
    return effective


def arguments_sha256(arguments: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        canonical_arguments(arguments),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def sign_bridge_context(
    context: TrustedJourneyContext,
    *,
    tool: str,
    arguments: Mapping[str, Any],
    ttl_s: int = DEFAULT_TTL_S,
    now: int | None = None,
) -> str:
    """Create an opaque, short-lived assertion bound to one exact tool call."""
    owner = context.owner_id.strip()
    session = context.session_id.strip()
    capability = context.capability.strip()
    surface = context.surface.strip()
    tool_name = str(tool or "").strip()
    if not owner or not session or not capability or not surface or not tool_name:
        raise JourneyTrustError("owner, session, capability, surface and tool are required")
    issued = int(time.time()) if now is None else int(now)
    payload: dict[str, Any] = {
        "v": 1,
        "iss": "deeptutor",
        "aud": BRIDGE_AUDIENCE,
        **asdict(context),
        "tool": tool_name,
        "args_sha256": arguments_sha256(arguments),
        "iat": issued,
        "exp": issued + max(1, min(int(ttl_s), DEFAULT_TTL_S)),
        "jti": secrets.token_urlsafe(12),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    signature = hmac.new(_secret(), raw, hashlib.sha256).digest()
    return f"{_b64url(raw)}.{_b64url(signature)}"


def sign_user_decision_context(
    context: TrustedJourneyContext,
    decision: ResolvedUserDecision,
    *,
    tool: str = "journey_write_confirm",
    arguments: Mapping[str, Any] | None = None,
    now: int | None = None,
) -> str:
    """Sign the exact card and answer produced by a real ``ask_user`` resume."""
    issued = int(time.time()) if now is None else int(now)
    if not decision.ask_user_tool_call_id or not decision.jti:
        raise JourneyTrustError("resolved user decision is incomplete")
    tool_name = str(tool or "").strip()
    if tool_name not in {"journey_ingest", "journey_write_confirm"}:
        raise JourneyTrustError("unsupported Journey user-decision tool")
    payload: dict[str, Any] = {
        "v": 1,
        "iss": "deeptutor",
        "aud": "test-partner-user-decision",
        **asdict(context),
        "tool": tool_name,
        "args_sha256": arguments_sha256(arguments or {}),
        "ask_user_tool_call_id": decision.ask_user_tool_call_id,
        "ask_user": decision.ask_user_payload,
        "answers": list(decision.answers),
        "resolved_at": decision.resolved_at,
        "iat": issued,
        "exp": issued + 300,
        "jti": decision.jti,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return f"{_b64url(raw)}.{_b64url(hmac.new(_secret(), raw, hashlib.sha256).digest())}"


def is_test_journey_tool(tool: Any) -> bool:
    """Identify the protected provider from adapter identity, not its display name."""
    return bool(
        getattr(tool, "provider_kind", "") == "mcp"
        and getattr(tool, "provider_id", "") == TEST_PARTNER_SERVER
        and str(getattr(tool, "original_name", "")).startswith("journey_")
    )


__all__ = [
    "BRIDGE_AUDIENCE",
    "BRIDGE_SECRET_ENV",
    "JourneyTrustError",
    "ResolvedUserDecision",
    "RESERVED_ARGUMENTS",
    "TrustedJourneyContext",
    "arguments_sha256",
    "bind_trusted_journey_context",
    "bridge_configured",
    "canonical_arguments",
    "current_trusted_journey_context",
    "current_resolved_user_decision",
    "effective_arguments",
    "is_test_journey_tool",
    "record_resolved_user_decision",
    "sign_bridge_context",
    "sign_user_decision_context",
]
