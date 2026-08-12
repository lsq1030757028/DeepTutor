# -*- coding: utf-8 -*-
"""Verify DeepTutor's per-call Journey trust assertion.

The shared MCP transport has no user identity of its own.  DeepTutor therefore
signs the authenticated owner and the exact effective tool arguments immediately
before dispatch.  This module only verifies assertions; it intentionally has no
public issuer.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any, Mapping

BRIDGE_SECRET_ENV = "TEST_JOURNEY_BRIDGE_SECRET"
BRIDGE_AUDIENCE = "test-partner-journey"
MIN_SECRET_BYTES = 32
MAX_TTL_S = 60
MAX_CLOCK_SKEW_S = 5
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
READ_TOOLS = frozenset(
    {
        "journey_list_batches",
        "journey_get_batch",
        "journey_open_trace",
    }
)
_OWNER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")


class BridgeAuthError(ValueError):
    """The call has no valid, authorized DeepTutor identity assertion."""


@dataclass(frozen=True, slots=True)
class BridgeClaims:
    owner: str
    session: str
    turn: str
    capability: str
    surface: str
    tool: str
    jti: str
    expires_at: int


def canonical_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value for key, value in arguments.items() if str(key) not in RESERVED_ARGUMENTS
    }


def arguments_sha256(arguments: Mapping[str, Any]) -> str:
    try:
        raw = json.dumps(
            canonical_arguments(arguments),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BridgeAuthError("arguments are not canonical JSON") from exc
    return hashlib.sha256(raw).hexdigest()


def _b64url_decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise BridgeAuthError("bridge context is malformed")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise BridgeAuthError("bridge context is malformed") from exc


def _secret() -> bytes:
    raw = os.environ.get(BRIDGE_SECRET_ENV, "").encode("utf-8")
    if len(raw) < MIN_SECRET_BYTES:
        raise BridgeAuthError(
            f"{BRIDGE_SECRET_ENV} must contain at least {MIN_SECRET_BYTES} UTF-8 bytes"
        )
    return raw


def verify_context(
    bridge_context: Any,
    *,
    tool: str,
    effective_args: Mapping[str, Any],
    now: int | None = None,
) -> BridgeClaims:
    """Verify signature, freshness, call binding and surface policy.

    The token format is ``payload_b64.signature_b64``.  The JSON payload carries
    ``v=1``; there is deliberately no unsigned version prefix.
    """
    if not isinstance(bridge_context, str) or not bridge_context.strip():
        raise BridgeAuthError("trusted bridge context is required")
    parts = bridge_context.strip().split(".")
    if len(parts) != 2:
        raise BridgeAuthError("bridge context is malformed")
    payload_raw = _b64url_decode(parts[0])
    supplied_sig = _b64url_decode(parts[1])
    expected_sig = hmac.new(_secret(), payload_raw, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied_sig, expected_sig):
        raise BridgeAuthError("bridge signature does not match")
    try:
        payload = json.loads(payload_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeAuthError("bridge payload is malformed") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise BridgeAuthError("unsupported bridge payload version")

    expected_tool = str(tool or "").strip()
    owner = str(payload.get("owner_id") or "").strip()
    session = str(payload.get("session_id") or "").strip()
    turn = str(payload.get("turn_id") or "").strip()
    capability = str(payload.get("capability") or "").strip()
    surface = str(payload.get("surface") or "").strip()
    signed_tool = str(payload.get("tool") or "").strip()
    jti = str(payload.get("jti") or "").strip()
    digest = str(payload.get("args_sha256") or "").strip()
    if payload.get("iss") != "deeptutor" or payload.get("aud") != BRIDGE_AUDIENCE:
        raise BridgeAuthError("bridge issuer or audience does not match")
    if not all((owner, session, turn, capability, surface, signed_tool, jti, digest)):
        raise BridgeAuthError("bridge identity claims are incomplete")
    if owner == "_local" or not _OWNER_RE.fullmatch(owner):
        raise BridgeAuthError("bridge owner is not a production owner")
    if signed_tool != expected_tool:
        raise BridgeAuthError("bridge tool does not match")
    if not hmac.compare_digest(digest, arguments_sha256(effective_args)):
        raise BridgeAuthError("bridge arguments do not match")

    current = int(time.time()) if now is None else int(now)
    try:
        issued = int(payload["iat"])
        expires = int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BridgeAuthError("bridge timestamps are invalid") from exc
    if issued > current + MAX_CLOCK_SKEW_S:
        raise BridgeAuthError("bridge assertion is not valid yet")
    if expires < current:
        raise BridgeAuthError("bridge assertion has expired")
    if expires <= issued or expires - issued > MAX_TTL_S:
        raise BridgeAuthError("bridge assertion lifetime is invalid")

    if surface == "workbench":
        if expected_tool not in READ_TOOLS:
            raise BridgeAuthError("workbench bridge is read-only")
    elif surface == "capability":
        if expected_tool == "journey_open_trace":
            raise BridgeAuthError("trace viewer is only available from workbench")
        if capability != "test":
            raise BridgeAuthError("Journey mutation requires Test capability")
    else:
        raise BridgeAuthError("bridge surface is not allowed")
    return BridgeClaims(
        owner=owner,
        session=session,
        turn=turn,
        capability=capability,
        surface=surface,
        tool=signed_tool,
        jti=jti,
        expires_at=expires,
    )


__all__ = [
    "BRIDGE_AUDIENCE",
    "BRIDGE_SECRET_ENV",
    "BridgeAuthError",
    "BridgeClaims",
    "RESERVED_ARGUMENTS",
    "arguments_sha256",
    "canonical_arguments",
    "verify_context",
]
