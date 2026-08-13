#!/usr/bin/env python3
"""Explicitly assign legacy Journey data to one authenticated owner.

Dry-run is the default. ``--apply`` writes a resumable manifest before moving
anything; ``--rollback`` consumes that manifest and refuses to overwrite or
roll back data that changed after migration.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import time
from typing import Any

EXTENSION_ROOT = Path(__file__).resolve().parents[1]
if str(EXTENSION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXTENSION_ROOT))

from server.journey import artifacts  # noqa: E402


def _tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with item.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def plan(root: str, target_owner: str) -> dict[str, Any]:
    base = Path(root).resolve()
    owner = artifacts.safe_owner(target_owner)
    if owner == artifacts.DEFAULT_OWNER:
        raise ValueError("target owner must be an authenticated production owner")

    entries: list[dict[str, Any]] = []
    conflicts: list[dict[str, str]] = []
    claimed_destinations: dict[str, str] = {}
    for source_partition in ("flat", artifacts.DEFAULT_OWNER):
        for kind in ("batches", "runs"):
            source_root = (
                base / kind if source_partition == "flat" else base / source_partition / kind
            )
            if not source_root.is_dir():
                continue
            for source in sorted(p for p in source_root.iterdir() if p.is_dir()):
                destination = base / owner / kind / source.name
                destination_key = str(destination).casefold()
                reason = ""
                if destination.exists():
                    reason = "target already exists"
                elif destination_key in claimed_destinations:
                    reason = f"duplicate legacy id from {claimed_destinations[destination_key]}"
                if reason:
                    conflicts.append(
                        {
                            "kind": kind,
                            "id": source.name,
                            "source": str(source),
                            "destination": str(destination),
                            "reason": reason,
                        }
                    )
                    continue
                claimed_destinations[destination_key] = str(source)
                entry: dict[str, Any] = {
                    "kind": kind,
                    "id": source.name,
                    "source_partition": source_partition,
                    "source": str(source),
                    "destination": str(destination),
                    "before_sha256": _tree_digest(source),
                    "status": "planned",
                }
                metadata = source / "batch.json"
                if kind == "batches" and metadata.is_file():
                    raw = metadata.read_bytes()
                    entry["batch_json_before_b64"] = base64.b64encode(raw).decode("ascii")
                entries.append(entry)
    return {
        "schema_version": 1,
        "root": str(base),
        "target_owner": owner,
        "entries": entries,
        "conflicts": conflicts,
        "ok": not conflicts,
        "dry_run": True,
    }


def apply_migration(root: str, target_owner: str) -> dict[str, Any]:
    migration = plan(root, target_owner)
    if not migration["ok"]:
        return migration
    migration_id = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(4)
    manifest_path = Path(migration["root"]) / "migrations" / f"journey-owner-{migration_id}.json"
    migration.update(
        {
            "migration_id": migration_id,
            "manifest_path": str(manifest_path),
            "dry_run": False,
            "status": "running",
            "created_at": artifacts.now_iso(),
        }
    )
    _write_manifest(manifest_path, migration)

    for entry in migration["entries"]:
        source = Path(entry["source"])
        destination = Path(entry["destination"])
        if source.exists() and destination.exists():
            raise RuntimeError(f"migration conflict appeared: {destination}")
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        elif not destination.exists():
            raise RuntimeError(f"migration source disappeared: {source}")

        if entry["kind"] == "batches":
            metadata_path = destination / "batch.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["owner"] = migration["target_owner"]
            metadata["partition"] = migration["target_owner"]
            temporary = metadata_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            os.replace(temporary, metadata_path)

        entry["after_sha256"] = _tree_digest(destination)
        entry["status"] = "moved"
        _write_manifest(manifest_path, migration)

    migration["status"] = "applied"
    migration["completed_at"] = artifacts.now_iso()
    _write_manifest(manifest_path, migration)
    return migration


def rollback(manifest: str) -> dict[str, Any]:
    manifest_path = Path(manifest).resolve()
    migration = json.loads(manifest_path.read_text(encoding="utf-8"))
    if migration.get("status") not in {"running", "applied"}:
        raise ValueError("manifest is not an active applied migration")

    # Verify the whole rollback before moving one byte.
    for entry in migration.get("entries", []):
        if entry.get("status") != "moved":
            continue
        source = Path(entry["source"])
        destination = Path(entry["destination"])
        if source.exists() or not destination.is_dir():
            raise RuntimeError(f"rollback path conflict: {source}")
        if _tree_digest(destination) != entry.get("after_sha256"):
            raise RuntimeError(f"migrated data changed; rollback refused: {destination}")

    for entry in reversed(migration.get("entries", [])):
        if entry.get("status") != "moved":
            continue
        source = Path(entry["source"])
        destination = Path(entry["destination"])
        if entry["kind"] == "batches" and entry.get("batch_json_before_b64"):
            (destination / "batch.json").write_bytes(
                base64.b64decode(entry["batch_json_before_b64"])
            )
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(destination), str(source))
        if _tree_digest(source) != entry["before_sha256"]:
            raise RuntimeError(f"rollback digest mismatch: {source}")
        entry["status"] = "rolled_back"
        _write_manifest(manifest_path, migration)

    migration["status"] = "rolled_back"
    migration["rolled_back_at"] = artifacts.now_iso()
    _write_manifest(manifest_path, migration)
    return migration


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="Journey workbench root")
    parser.add_argument("--target-owner", help="Authenticated target owner id")
    parser.add_argument("--apply", action="store_true", help="Apply after a clean plan")
    parser.add_argument("--rollback", metavar="MANIFEST", help="Rollback one manifest")
    args = parser.parse_args(argv)
    if args.rollback:
        result = rollback(args.rollback)
    else:
        if not args.root or not args.target_owner:
            parser.error("--root and --target-owner are required")
        result = (
            apply_migration(args.root, args.target_owner)
            if args.apply
            else plan(args.root, args.target_owner)
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
