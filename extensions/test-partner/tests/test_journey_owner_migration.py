from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_journey_owner.py"
SPEC = importlib.util.spec_from_file_location("migrate_journey_owner", SCRIPT)
assert SPEC and SPEC.loader
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def _legacy_batch(root: Path, partition: str, ident: str) -> tuple[Path, bytes]:
    base = root / "batches" if partition == "flat" else root / partition / "batches"
    batch = base / ident
    batch.mkdir(parents=True)
    raw = json.dumps(
        {
            "artifact": "batch",
            "batch_id": ident,
            "owner": "",
            "partition": "_local",
            "run_ids": [],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    (batch / "batch.json").write_bytes(raw)
    return batch, raw


def test_dry_run_is_read_only_and_covers_flat_and_local(tmp_path: Path) -> None:
    flat, _ = _legacy_batch(tmp_path, "flat", "b-20260812-aaaaaa")
    local, _ = _legacy_batch(tmp_path, "_local", "b-20260812-bbbbbb")

    result = migration.plan(str(tmp_path), "alice")

    assert result["ok"] is True and result["dry_run"] is True
    assert {entry["source_partition"] for entry in result["entries"]} == {"flat", "_local"}
    assert flat.is_dir() and local.is_dir()
    assert not (tmp_path / "alice").exists()
    assert not (tmp_path / "migrations").exists()


def test_apply_rewrites_owner_and_rollback_restores_exact_bytes(tmp_path: Path) -> None:
    source, original = _legacy_batch(tmp_path, "_local", "b-20260812-cccccc")

    applied = migration.apply_migration(str(tmp_path), "alice")

    destination = tmp_path / "alice" / "batches" / source.name
    assert applied["status"] == "applied"
    assert not source.exists() and destination.is_dir()
    metadata = json.loads((destination / "batch.json").read_text(encoding="utf-8"))
    assert metadata["owner"] == metadata["partition"] == "alice"
    manifest = Path(applied["manifest_path"])
    assert manifest.is_file()

    rolled_back = migration.rollback(str(manifest))

    assert rolled_back["status"] == "rolled_back"
    assert source.is_dir() and not destination.exists()
    assert (source / "batch.json").read_bytes() == original


def test_conflict_blocks_before_any_move(tmp_path: Path) -> None:
    source, _ = _legacy_batch(tmp_path, "_local", "b-20260812-dddddd")
    target = tmp_path / "alice" / "batches" / source.name
    target.mkdir(parents=True)

    result = migration.apply_migration(str(tmp_path), "alice")

    assert result["ok"] is False and result["conflicts"]
    assert source.is_dir() and target.is_dir()
    assert not (tmp_path / "migrations").exists()


def test_rollback_refuses_changed_migrated_data_without_moving_it(tmp_path: Path) -> None:
    source, _ = _legacy_batch(tmp_path, "flat", "b-20260812-eeeeee")
    applied = migration.apply_migration(str(tmp_path), "alice")
    destination = tmp_path / "alice" / "batches" / source.name
    (destination / "new-evidence.txt").write_text("changed", encoding="utf-8")

    with pytest.raises(RuntimeError, match="data changed"):
        migration.rollback(applied["manifest_path"])

    assert destination.is_dir() and not source.exists()


def test_target_owner_must_be_explicit_production_owner(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="production owner"):
        migration.plan(str(tmp_path), "_local")
    assert list(os.scandir(tmp_path)) == []
