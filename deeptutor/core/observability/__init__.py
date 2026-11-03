"""Observability primitives shared by the trace/diagnostics surfaces.

Three concerns live here because all three are consumed by *both* the stream
event layer and the on-disk trace sidecar, and neither of those may own the
other:

* :mod:`redaction` — write-side masking. Secrets are masked where they are
  recorded, never where they are read, so a reader that forgets to redact
  cannot leak what was never written.
* :mod:`stop_reason` — the structured ``stop`` envelope for a turn's terminal
  state, mapped one-to-one onto the three statuses ``turns.status`` already
  records (``completed`` / ``cancelled`` / ``failed``).
* :mod:`diagnostics` — the session/turn/step diagnostic log helper, which puts
  ``session_id`` / ``turn_id`` on every line it writes.

Everything here is informational: nothing in this package may raise into a
turn. The helpers are written so that the failure mode is a missing field,
never an exception.
"""

from __future__ import annotations

from deeptutor.core.observability.redaction import redact_obj, redact_text
from deeptutor.core.observability.stop_reason import (
    STOP_CODE_OK,
    STOP_KIND_COMPLETED,
    STOP_KIND_ERROR,
    STOP_KIND_INTERRUPTED,
    TURN_STATUS_TO_STOP_KIND,
    classify_exception,
    stop_completed,
    stop_interrupted,
)

__all__ = [
    "stop_interrupted",
    "STOP_CODE_OK",
    "STOP_KIND_COMPLETED",
    "STOP_KIND_ERROR",
    "STOP_KIND_INTERRUPTED",
    "TURN_STATUS_TO_STOP_KIND",
    "classify_exception",
    "redact_obj",
    "redact_text",
    "stop_completed",
    "stop_interrupted",
]
