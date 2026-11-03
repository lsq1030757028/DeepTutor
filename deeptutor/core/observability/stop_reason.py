"""Structured ``stop`` envelope for a turn's terminal state.

A turn already has exactly three terminal states, recorded on ``turns.status``
by :mod:`deeptutor.services.session.turn_runtime`: ``completed``,
``cancelled`` and ``failed``. This module does not invent a fourth. It maps
those three onto a machine-classifiable envelope::

    {"kind": "completed" | "interrupted" | "error",
     "code": "OK" | "USER_CANCELLED" | "AUTH" | …,
     "status": 401,          # optional, when the provider gave an HTTP status
     "message": "…",         # optional, redacted
     "retries": 2}           # optional, when a retry count is known

``kind`` is the coarse three-way split (one per ``turns.status`` value) so the
two records can never disagree; ``code`` is the classification a reader
actually triages on. Keeping them separate is what makes "the MCP scope timed
out" (a non-fatal degradation, reported by ``channel_health``) structurally
distinguishable from "the turn ended in an error" — the first never produces a
``stop`` at all.

Classification reuses the provider layer's own transient-error markers rather
than growing a second, silently diverging notion of "transient"
(:meth:`deeptutor.services.llm.provider_core.base.LLMProvider._is_transient_error`).
"""

from __future__ import annotations

import re
from typing import Any

from deeptutor.core.observability.redaction import redact_text

STOP_KIND_COMPLETED = "completed"
STOP_KIND_INTERRUPTED = "interrupted"
STOP_KIND_ERROR = "error"

STOP_CODE_OK = "OK"
STOP_CODE_USER_CANCELLED = "USER_CANCELLED"
STOP_CODE_MISSING_CREDENTIAL = "MISSING_CREDENTIAL"
STOP_CODE_AUTH = "AUTH"
STOP_CODE_PROVIDER_UNREACHABLE = "PROVIDER_UNREACHABLE"
STOP_CODE_PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
STOP_CODE_CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
STOP_CODE_TOOL_FATAL = "TOOL_FATAL"
STOP_CODE_UNKNOWN = "UNKNOWN"

#: ``turns.status`` value -> ``stop.kind``. One entry per status the runtime
#: can write, so a reader can assert the two agree.
TURN_STATUS_TO_STOP_KIND: dict[str, str] = {
    "completed": STOP_KIND_COMPLETED,
    "cancelled": STOP_KIND_INTERRUPTED,
    "failed": STOP_KIND_ERROR,
}

_MESSAGE_MAX_CHARS = 400

_MISSING_CREDENTIAL_RE = re.compile(
    r"(?i)("
    r"no api[ _-]?key|missing (?:an? )?(?:api[ _-]?)?key|api[ _-]?key (?:is )?(?:not |un)set"
    r"|api[ _-]?key (?:is )?required|missing credential|no credential"
    r"|not configured|api[ _-]?key must be set"
    r")"
)
_AUTH_RE = re.compile(
    r"(?i)(unauthorized|forbidden|invalid api[ _-]?key|authentication fail"
    r"|invalid[ _-]?token|permission denied|401|403)"
)
_CONTEXT_OVERFLOW_RE = re.compile(
    r"(?i)(context[ _]length|maximum context|context_length_exceeded"
    r"|too many tokens|reduce the length of the messages)"
)
_TIMEOUT_RE = re.compile(r"(?i)(timeout|timed out|deadline exceeded)")
_UNREACHABLE_RE = re.compile(
    r"(?i)(connection|unreachable|refused|dns|network|temporarily unavailable"
    r"|overloaded|rate limit|server error|bad gateway|429|500|502|503|504)"
)
# Only read a status out of prose when the prose says it is one. A bare
# three-digit number in an error message is as likely to be a token count as an
# HTTP status, and a wrong ``stop.status`` is worse than an absent one.
_STATUS_RE = re.compile(r"(?i)(?:status(?:\s*code)?|http|error code)\D{0,3}(4\d{2}|5\d{2})(?!\d)")


def _describe(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _http_status(exc: BaseException, text: str) -> int | None:
    for attribute in ("status_code", "status", "http_status"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int) and 100 <= value <= 599:
        return value
    match = _STATUS_RE.search(text)
    return int(match.group(1)) if match else None


def _classify_code(exc: BaseException, text: str, status: int | None) -> str:
    # Credential absence is checked before auth failure: "no API key for
    # provider route X" also matches the auth vocabulary, and the two are
    # triaged differently (configure vs rotate).
    if _MISSING_CREDENTIAL_RE.search(text):
        return STOP_CODE_MISSING_CREDENTIAL
    if status in {401, 403} or _AUTH_RE.search(text):
        return STOP_CODE_AUTH
    if _CONTEXT_OVERFLOW_RE.search(text):
        return STOP_CODE_CONTEXT_OVERFLOW
    if isinstance(exc, TimeoutError) or _TIMEOUT_RE.search(text):
        return STOP_CODE_PROVIDER_TIMEOUT
    if isinstance(exc, (ConnectionError, OSError)) or _UNREACHABLE_RE.search(text):
        return STOP_CODE_PROVIDER_UNREACHABLE
    return STOP_CODE_UNKNOWN


def _envelope(
    kind: str,
    code: str,
    *,
    message: str = "",
    status: int | None = None,
    retries: int | None = None,
) -> dict[str, Any]:
    stop: dict[str, Any] = {"kind": kind, "code": code}
    if status is not None:
        stop["status"] = int(status)
    if message:
        stop["message"] = redact_text(message, max_chars=_MESSAGE_MAX_CHARS)
    if retries is not None and retries > 0:
        stop["retries"] = int(retries)
    return stop


def stop_completed(
    *,
    code: str = STOP_CODE_OK,
    message: str = "",
    retries: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """``stop`` for a turn that reached its own end (``turns.status`` completed)."""
    stop = _envelope(STOP_KIND_COMPLETED, code, message=message, retries=retries)
    stop.update({key: value for key, value in extra.items() if value is not None})
    return stop


def stop_interrupted(
    *,
    code: str = STOP_CODE_USER_CANCELLED,
    message: str = "",
) -> dict[str, Any]:
    """``stop`` for a cancelled turn (``turns.status`` cancelled)."""
    return _envelope(STOP_KIND_INTERRUPTED, code, message=message)


def classify_exception(
    exc: BaseException,
    *,
    retries: int | None = None,
) -> dict[str, Any]:
    """``stop`` for a failed turn (``turns.status`` failed).

    Never raises: an unclassifiable failure is reported as ``UNKNOWN`` with a
    redacted message rather than costing the caller its own error path.
    """
    try:
        text = _describe(exc)
        status = _http_status(exc, text)
        code = _classify_code(exc, text, status)
        return _envelope(STOP_KIND_ERROR, code, message=text, status=status, retries=retries)
    except Exception:  # pragma: no cover - classification must never raise
        return {"kind": STOP_KIND_ERROR, "code": STOP_CODE_UNKNOWN}


__all__ = [
    "STOP_CODE_AUTH",
    "STOP_CODE_CONTEXT_OVERFLOW",
    "STOP_CODE_MISSING_CREDENTIAL",
    "STOP_CODE_OK",
    "STOP_CODE_PROVIDER_TIMEOUT",
    "STOP_CODE_PROVIDER_UNREACHABLE",
    "STOP_CODE_TOOL_FATAL",
    "STOP_CODE_UNKNOWN",
    "STOP_CODE_USER_CANCELLED",
    "STOP_KIND_COMPLETED",
    "STOP_KIND_ERROR",
    "STOP_KIND_INTERRUPTED",
    "TURN_STATUS_TO_STOP_KIND",
    "classify_exception",
    "stop_completed",
    "stop_interrupted",
]
