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


_CURRENT_CONTEXT: ContextVar[TrustedJourneyContext | None] = ContextVar(
    "deeptutor_trusted_journey_context", default=None
)


@contextmanager
def bind_trusted_journey_context(
    context: TrustedJourneyContext,
) -> Iterator[TrustedJourneyContext]:
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_CONTEXT.reset(token)


def current_trusted_journey_context() -> TrustedJourneyContext | None:
    return _CURRENT_CONTEXT.get()


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
    "RESERVED_ARGUMENTS",
    "TrustedJourneyContext",
    "arguments_sha256",
    "bind_trusted_journey_context",
    "bridge_configured",
    "canonical_arguments",
    "current_trusted_journey_context",
    "effective_arguments",
    "is_test_journey_tool",
    "sign_bridge_context",
]
