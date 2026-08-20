"""Session / turn / step diagnostic log lines.

The problem this addresses is measurable rather than theoretical:
``data/user/logs/deeptutor.jsonl`` held 134 lines across sixteen days, and a
grep for one full session's identifiers returned nothing at all. The log
existed and was useless — during an incident there was no line to search for.

Two things were missing and both are fixed here.

**Nothing bound the identifiers.** ``session_id`` and ``turn_id`` were already
declared as log-context fields, but nothing ever put a value in them, so even
the lines that did get written could not be tied to a conversation. The turn
runtime now binds them for the whole turn, which means *every* log line any
code emits during that turn — including third-party and pre-existing ones —
carries them.

**Nothing wrote the lifecycle.** A turn's shape (which rounds ran, what each
call was offered, why each one stopped, which tools were called, what the
capability assembly managed) reached the stream events and stopped there.
:func:`diag` writes that same shape to the log, so the question "what happened
in this session" is answerable with ``grep`` and no database.

Structure is deliberately unchanged — ``timestamp/level/logger/message/context``
— with the structured fields riding in ``context`` beside the identifiers. Two
rules apply to what goes in:

* **shape, never bodies.** Sizes, names, counts, durations. A tool result's
  length, not its text; a message's character count, not its content. The log
  is grep-able by support engineers and copied around more freely than the
  database is.
* **masked on the way in**, via :mod:`redaction`, for the same reason as
  everywhere else on this path.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from deeptutor.core.observability.redaction import redact_obj

#: One logger for the whole lifecycle so ``logger=deeptutor.diag`` selects it.
logger = logging.getLogger("deeptutor.diag")

#: Cap on any single string field. Diagnostics are for shape; a field that
#: wants more room than this is trying to be a body.
_FIELD_MAX_CHARS = 300


def diag(event: str, /, level: int = logging.INFO, **fields: Any) -> None:
    """Write one structured diagnostic line.

    ``event`` is a dotted lifecycle name (``turn.start``, ``llm.call.end``, …)
    and doubles as the log message, so the plain-text console rendering stays
    readable. Never raises: diagnostics must not be able to fail a turn.
    """
    try:
        payload: dict[str, Any] = {"event": event}
        payload.update(redact_obj(_prune(fields), max_chars=_FIELD_MAX_CHARS))
        logger.log(level, event, extra={"log_extra": payload})
    except Exception:  # pragma: no cover - a log line is never worth a turn
        pass


def _prune(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}


class Elapsed:
    """Monotonic stopwatch in milliseconds, for the ``*_ms`` fields."""

    __slots__ = ("_started",)

    def __init__(self) -> None:
        self._started = time.monotonic()

    @property
    def ms(self) -> int:
        return max(0, int((time.monotonic() - self._started) * 1000))


__all__ = ["Elapsed", "diag", "logger"]
