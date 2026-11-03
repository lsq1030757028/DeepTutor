"""The sidecar's two jobs: write each body once, and stay findable afterwards.

The dedup assertions are counted, not "roughly". Per-part hashing is the whole
reason this file can be kept always-on, and the failure mode it guards against
is silent: hashing the header as a unit still works, it just rewrites the
system prompt every time a tool schema moves, and nobody notices until a
session is megabytes.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

import pytest

from deeptutor.core.observability import trace_sidecar
from deeptutor.core.observability.trace_sidecar import (
    MODE_RECORDED,
    MODE_UNAVAILABLE,
    MODE_UNCHANGED,
    SessionTraceSidecar,
    get_sidecar,
    record_header_async,
    reset_registry,
    sidecar_root,
    unavailable_ref,
)

SYSTEM = "You are DeepTutor. " + "Follow the rules. " * 200
TOOLS = json.dumps([{"function": {"name": f"tool_{index}"}} for index in range(30)])
CONFIG = json.dumps({"model": "gpt-test", "temperature": 0.2})


def _records(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _of_type(path: Path, kind: str) -> list[dict[str, Any]]:
    return [record for record in _records(path) if record.get("type") == kind]


@pytest.fixture
def sidecar(tmp_path: Path) -> SessionTraceSidecar:
    reset_registry()
    return SessionTraceSidecar("sess-1", root=tmp_path)


def _write(sidecar: SessionTraceSidecar, *, tools: str = TOOLS, step: int = 0):
    return sidecar.record_header(
        parts={"system": SYSTEM, "tools": tools, "config": CONFIG},
        turn_index=0,
        step_index=step,
    )


# --- criterion 1: a body is written once, a header every time ---------------


def test_five_identical_calls_write_three_bodies_and_five_headers(sidecar) -> None:
    for step in range(5):
        _write(sidecar, step=step)

    assert len(_of_type(sidecar.path, "header/part")) == 3
    assert len(_of_type(sidecar.path, "header")) == 5


def test_an_unchanged_call_says_so_in_its_mode(sidecar) -> None:
    first = _write(sidecar, step=0)
    second = _write(sidecar, step=1)

    assert first.mode == MODE_RECORDED
    assert second.mode == MODE_UNCHANGED
    # Same inputs, same combined hash — the ref is comparable across calls.
    assert first.hash == second.hash
    assert first.seq != second.seq


# --- criterion 2: the parts are independent --------------------------------


def test_a_tools_change_rewrites_only_tools(sidecar) -> None:
    _write(sidecar, step=0)
    before = _of_type(sidecar.path, "header/part")

    changed = json.dumps([{"function": {"name": "tool_new"}}])
    _write(sidecar, tools=changed, step=1)

    after = _of_type(sidecar.path, "header/part")
    added = after[len(before) :]
    assert len(added) == 1
    assert added[0]["part"] == "tools"
    # The system prompt did not move, so it must not have been written again.
    assert [record["part"] for record in after].count("system") == 1


def test_the_ref_reports_which_parts_changed(sidecar) -> None:
    first = _write(sidecar, step=0)
    second = _write(sidecar, tools=json.dumps([{"function": {"name": "x"}}]), step=1)

    assert first.parts["system"] == second.parts["system"]
    assert first.parts["config"] == second.parts["config"]
    assert first.parts["tools"] != second.parts["tools"]


# --- criterion 3: the reference resolves, order-independently ---------------


def test_a_header_ref_resolves_to_all_three_bodies(sidecar) -> None:
    _write(sidecar, step=0)
    ref = _write(sidecar, tools=json.dumps([{"function": {"name": "x"}}]), step=1)

    resolved = sidecar.resolve(ref.as_dict())

    assert resolved is not None
    assert resolved["system"] == SYSTEM
    assert resolved["config"] == CONFIG
    assert json.loads(resolved["tools"])[0]["function"]["name"] == "x"


def test_resolution_does_not_depend_on_record_order(sidecar, tmp_path: Path) -> None:
    """The bodies a ref needs may sit anywhere in the file — including before
    several other headers. Binding by hash rather than by "the nearest
    preceding record" is what makes the ref survive the frontend regrouping
    events by call_id."""
    ref = _write(sidecar, step=0)
    for step in range(1, 4):
        _write(sidecar, tools=json.dumps([{"function": {"name": f"t{step}"}}]), step=step)

    # Shuffle the file into reverse order; resolution must be unaffected.
    records = _records(sidecar.path)
    shuffled = tmp_path / "shuffled" / "trajectory.jsonl"
    shuffled.parent.mkdir(parents=True)
    shuffled.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in reversed(records)) + "\n",
        encoding="utf-8",
    )
    reader = SessionTraceSidecar("shuffled", root=tmp_path)

    resolved = reader.resolve(ref.as_dict())
    assert resolved is not None and resolved["system"] == SYSTEM


def test_an_unknown_ref_resolves_to_nothing_rather_than_raising(sidecar) -> None:
    _write(sidecar, step=0)
    assert sidecar.resolve({"seq": 9999}) is None
    assert sidecar.resolve({}) is None
    assert sidecar.resolve(unavailable_ref()) is None


# --- criterion 5: the model cannot read its own prompt back -----------------


def test_the_sidecar_root_is_not_under_the_workspace() -> None:
    # ``sidecar_root`` was bound at import time, so the suite-wide isolation
    # fixture (which patches the module attribute) does not reach it: this
    # checks the real production path, not the tmp one.
    from deeptutor.services.path_service import get_path_service

    workspace = get_path_service().get_workspace_dir().resolve()
    root = sidecar_root().resolve()

    assert not str(root).startswith(str(workspace)), (
        "the sidecar holds the system prompt verbatim; under the workspace the "
        "sandbox and file tools could read it back to the model"
    )


# --- durability + retention -------------------------------------------------


def test_dedup_state_survives_a_restart(tmp_path: Path) -> None:
    first = SessionTraceSidecar("sess-1", root=tmp_path)
    _write(first, step=0)

    reborn = SessionTraceSidecar("sess-1", root=tmp_path)
    ref = _write(reborn, step=1)

    assert ref.mode == MODE_UNCHANGED
    assert len(_of_type(reborn.path, "header/part")) == 3
    assert len(_of_type(reborn.path, "header")) == 2


def test_a_stale_session_file_is_evicted_when_a_new_one_arrives(tmp_path: Path) -> None:
    reset_registry()
    stale = SessionTraceSidecar("old", root=tmp_path)
    _write(stale, step=0)
    ancient = time.time() - (trace_sidecar.MAX_AGE_DAYS + 1) * 86400
    import os

    os.utime(stale.path, (ancient, ancient))

    get_sidecar("fresh", root=tmp_path).record_header(
        parts={"system": "s", "tools": "t", "config": "c"}, turn_index=0, step_index=0
    )

    assert not stale.path.exists()
    reset_registry()


def test_an_oversized_session_file_is_evicted(tmp_path: Path, monkeypatch) -> None:
    reset_registry()
    monkeypatch.setattr(trace_sidecar, "MAX_SESSION_BYTES", 10)
    fat = SessionTraceSidecar("fat", root=tmp_path)
    _write(fat, step=0)

    get_sidecar("fresh", root=tmp_path)

    assert not fat.path.exists()
    reset_registry()


# --- never sinks a turn -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_write_failure_is_reported_as_unavailable_not_raised(tmp_path, monkeypatch) -> None:
    reset_registry()

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(SessionTraceSidecar, "record_header", _boom)

    ref = await record_header_async(
        session_id="sess-1",
        parts={"system": "s", "tools": "t", "config": "c"},
        turn_index=0,
        step_index=0,
        root=tmp_path,
    )

    # Explicitly unavailable, not a missing field: a reader must be able to
    # tell "could not be written" from "this build does not record".
    assert ref["mode"] == MODE_UNAVAILABLE
    assert ref["seq"] == -1
    reset_registry()
