"""Per-session sidecar holding what each LLM call was actually given.

The question this exists to answer is "what did the model have in front of it
on the turn that went wrong", asked *after* the turn went wrong. Answering it
has to cost nothing up front, because the moment you would want to switch a
capture flag on is always a moment that has already passed: the model is
non-deterministic, the MCP surface has moved, and the upstream data has
changed, so "reproduce it with logging enabled" reproduces a different turn.

Three decisions follow from that, and each one is load-bearing:

**Always on, never a flag.** See above. The escape hatch is retention, not
capture.

**Off the business database.** Every stream event is written twice — once to
``turn_events`` and once into the assistant message's ``events_json``
(``turn_runtime``) — so a system prompt on the event stream is a system prompt
stored twice, per call. The events carry a *reference*; the bodies live here.

**Per-part deduplication, not per-header.** Within a session the system prompt
is typically byte-identical for its whole life while the tool schemas change a
handful of times. Hashing the header as a unit means one changed tool schema
rewrites the system prompt too. Hashing ``system`` / ``tools`` / ``config``
separately and writing only the parts that actually changed is what keeps a
long session in the hundreds of kilobytes instead of the megabytes.

**Not under the workspace.** The sandbox and file tools can read the workspace.
Writing the system prompt there would hand the model its own instructions back.

Two record shapes, one file, append-only::

    {"type":"header/part","seq":N,"time":…,"part":"system","hash":…,"bytes":M,"data":"…"}
    {"type":"header","seq":N,"time":…,"turn":T,"step":S,"parts":{"system":…,"tools":…,"config":…}}

A ``header`` is written for *every* call even when nothing changed, and the
event's ``header_ref`` points at it by ``seq``. That is deliberate: the
reference has to survive the frontend regrouping events by ``call_id``, after
which "the nearest preceding header" is no longer reconstructible.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

#: The three parts of one request header, hashed and stored independently.
PART_SYSTEM = "system"
PART_TOOLS = "tools"
PART_CONFIG = "config"
HEADER_PARTS: tuple[str, ...] = (PART_SYSTEM, PART_TOOLS, PART_CONFIG)

MODE_RECORDED = "recorded"
MODE_UNCHANGED = "unchanged"
MODE_UNAVAILABLE = "unavailable"

RECORD_HEADER = "header"
RECORD_HEADER_PART = "header/part"

SIDECAR_DIRNAME = "llm-trace"
SIDECAR_FILENAME = "trajectory.jsonl"

#: Retention skeleton. A multi-user deployment must not grow an unbounded
#: second store beside ``turn_events`` (which has no retention of its own), so
#: the bounds exist from the first commit; wiring them to a settings key is the
#: next batch's job.
MAX_SESSION_BYTES = 20 * 1024 * 1024
MAX_AGE_DAYS = 30

_registry_lock = threading.Lock()
_registry: dict[str, "SessionTraceSidecar"] = {}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_component(value: str) -> str:
    """A directory name that cannot escape the sidecar root."""
    cleaned = "".join(char if char.isalnum() or char in "-_." else "_" for char in str(value))
    cleaned = cleaned.strip("._") or "unknown"
    return cleaned[:120]


def sidecar_root() -> Path:
    """Root of the sidecar tree — under the user data dir, NOT the workspace."""
    from deeptutor.services.path_service import get_path_service

    return get_path_service().get_user_root() / SIDECAR_DIRNAME


@dataclass(frozen=True)
class HeaderRef:
    """Pointer from a stream event into this session's sidecar."""

    seq: int
    hash: str
    mode: str
    parts: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "hash": self.hash,
            "mode": self.mode,
            # The per-part hashes ride along so a reader can tell *what*
            # changed between two calls (prompt? tools? both?) without opening
            # the sidecar; the combined hash alone cannot answer that.
            "parts": dict(self.parts),
        }


def unavailable_ref(reason: str = "") -> dict[str, Any]:
    """The ref to emit when nothing could be recorded.

    Explicitly unavailable, never a missing field: "no header_ref" and "the
    header_ref says it could not be written" are different facts and a reader
    must not have to guess which one it is looking at.
    """
    ref: dict[str, Any] = {
        "seq": -1,
        "hash": "",
        "mode": MODE_UNAVAILABLE,
        "parts": {},
    }
    if reason:
        ref["reason"] = reason
    return ref


class SessionTraceSidecar:
    """One session's append-only trajectory file."""

    def __init__(self, session_id: str, root: Path | None = None) -> None:
        self.session_id = str(session_id or "unknown")
        base = root if root is not None else sidecar_root()
        self.dir = Path(base) / _safe_component(self.session_id)
        self.path = self.dir / SIDECAR_FILENAME
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        self._next_seq = 0
        self._loaded = False

    # -- reading existing state ------------------------------------------

    def _load(self) -> None:
        """Recover dedup state from the file, so a restart does not rewrite.

        Only the hashes and the sequence counter are read back; the bodies stay
        on disk.
        """
        if self._loaded:
            return
        self._loaded = True
        try:
            if not self.path.exists():
                return
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    seq = int(record.get("seq") or 0)
                    self._next_seq = max(self._next_seq, seq + 1)
                    if record.get("type") == RECORD_HEADER_PART:
                        digest = str(record.get("hash") or "")
                        if digest:
                            self._seen.add(digest)
        except Exception:
            logger.debug("trace sidecar state could not be recovered", exc_info=True)

    # -- writing ----------------------------------------------------------

    def record_header(
        self,
        *,
        parts: dict[str, str],
        turn_index: int,
        step_index: int,
    ) -> HeaderRef:
        """Persist one call's header and return the event's reference.

        Synchronous on purpose — the async wrapper below hands this to a thread
        so nothing here sits on the turn's critical path.
        """
        with self._lock:
            self._load()
            self.dir.mkdir(parents=True, exist_ok=True)
            digests = {name: _sha256(parts.get(name) or "") for name in HEADER_PARTS}
            lines: list[str] = []
            wrote_body = False
            for name in HEADER_PARTS:
                digest = digests[name]
                if digest in self._seen:
                    continue
                body = parts.get(name) or ""
                lines.append(
                    _dumps(
                        {
                            "type": RECORD_HEADER_PART,
                            "seq": self._next_seq + len(lines),
                            "time": time.time(),
                            "part": name,
                            "hash": digest,
                            "bytes": len(body.encode("utf-8")),
                            "data": body,
                        }
                    )
                )
                wrote_body = True
            header_seq = self._next_seq + len(lines)
            lines.append(
                _dumps(
                    {
                        "type": RECORD_HEADER,
                        "seq": header_seq,
                        "time": time.time(),
                        "turn": int(turn_index),
                        "step": int(step_index),
                        "parts": digests,
                    }
                )
            )
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            # Marked as seen only after the write landed: a failed write must
            # not convince the next call that the body is already on disk.
            for name in HEADER_PARTS:
                self._seen.add(digests[name])
            self._next_seq = header_seq + 1
            return HeaderRef(
                seq=header_seq,
                hash=combined_hash(digests),
                mode=MODE_RECORDED if wrote_body else MODE_UNCHANGED,
                parts=digests,
            )

    # -- reading back ------------------------------------------------------

    def resolve(self, header_ref: dict[str, Any]) -> dict[str, str] | None:
        """Full text of the three parts a ``header_ref`` points at.

        Resolution goes ``seq`` -> ``header`` record -> three hashes -> three
        ``header/part`` bodies, all by identity. Nothing depends on where the
        records sit relative to each other in the file.
        """
        try:
            wanted_seq = int((header_ref or {}).get("seq", -1))
        except (TypeError, ValueError):
            return None
        if wanted_seq < 0 or not self.path.exists():
            return None
        header: dict[str, Any] | None = None
        bodies: dict[str, str] = {}
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == RECORD_HEADER and int(record.get("seq", -1)) == wanted_seq:
                    header = record
                elif record.get("type") == RECORD_HEADER_PART:
                    bodies[str(record.get("hash") or "")] = str(record.get("data") or "")
        if header is None:
            return None
        digests = header.get("parts") or {}
        resolved: dict[str, str] = {}
        for name in HEADER_PARTS:
            digest = str(digests.get(name) or "")
            if digest not in bodies:
                return None
            resolved[name] = bodies[digest]
        return resolved


def combined_hash(digests: dict[str, str]) -> str:
    return _sha256("|".join(f"{name}:{digests.get(name, '')}" for name in HEADER_PARTS))


def get_sidecar(session_id: str, root: Path | None = None) -> SessionTraceSidecar:
    """The (cached) sidecar for *session_id*.

    Cached because the dedup set is the whole point: a fresh instance per call
    would re-read the file every time or, worse, re-write every body.
    """
    key = f"{root}::{session_id}"
    with _registry_lock:
        sidecar = _registry.get(key)
        if sidecar is None:
            sidecar = SessionTraceSidecar(session_id, root=root)
            _registry[key] = sidecar
            _evict_cold(sidecar.dir.parent, keep=sidecar.dir.name)
        return sidecar


def reset_registry() -> None:
    """Drop cached sidecars (tests; also a restart-equivalent)."""
    with _registry_lock:
        _registry.clear()


async def record_header_async(
    *,
    session_id: str,
    parts: dict[str, str],
    turn_index: int,
    step_index: int,
    root: Path | None = None,
) -> dict[str, Any]:
    """Record one call's header without blocking the turn.

    Never raises: a sidecar that cannot be written costs the reader a body, not
    the user a turn. The failure is reported as ``mode="unavailable"``.
    """
    try:
        sidecar = get_sidecar(session_id, root=root)
        ref = await asyncio.to_thread(
            sidecar.record_header,
            parts=parts,
            turn_index=turn_index,
            step_index=step_index,
        )
        return ref.as_dict()
    except Exception as exc:
        logger.debug("trace sidecar write failed", exc_info=True)
        return unavailable_ref(type(exc).__name__)


def _evict_cold(root: Path, keep: str) -> None:
    """Retention skeleton: drop sessions that are too old or too large.

    Lazy, like the MCP scope eviction it is modelled on: this only matters when
    a new session shows up, and a background sweeper would have to be owned and
    cancelled across the app lifespan for no extra benefit.
    """
    try:
        if not root.exists():
            return
        cutoff = time.time() - MAX_AGE_DAYS * 86400
        for child in root.iterdir():
            if not child.is_dir() or child.name == keep:
                continue
            trajectory = child / SIDECAR_FILENAME
            if not trajectory.exists():
                continue
            stat = trajectory.stat()
            if stat.st_mtime < cutoff or stat.st_size > MAX_SESSION_BYTES:
                trajectory.unlink(missing_ok=True)
    except Exception:
        logger.debug("trace sidecar eviction skipped", exc_info=True)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


__all__ = [
    "HEADER_PARTS",
    "HeaderRef",
    "MAX_AGE_DAYS",
    "MAX_SESSION_BYTES",
    "MODE_RECORDED",
    "MODE_UNAVAILABLE",
    "MODE_UNCHANGED",
    "PART_CONFIG",
    "PART_SYSTEM",
    "PART_TOOLS",
    "RECORD_HEADER",
    "RECORD_HEADER_PART",
    "SessionTraceSidecar",
    "combined_hash",
    "get_sidecar",
    "record_header_async",
    "reset_registry",
    "sidecar_root",
    "unavailable_ref",
]
