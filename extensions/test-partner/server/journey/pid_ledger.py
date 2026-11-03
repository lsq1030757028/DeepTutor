# -*- coding: utf-8 -*-
"""Self-contained child-process PID ledger used inside compiled bundles.

The generated pytest project runs with its bundle directory as ``cwd`` and
must not import the surrounding Test Partner source tree.  This module keeps
the small registration half of process_registry dependency-free so it can be
embedded byte-for-byte as ``_pid_ledger.py``.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any


def _windows_instance_id(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                     wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_time),
                ctypes.byref(kernel), ctypes.byref(user)):
            return None
        ticks = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return f"win:{ticks}"
    finally:
        kernel32.CloseHandle(handle)


def _process_instance_id(pid: int) -> str | None:
    if os.name == "nt":
        return _windows_instance_id(pid)
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="ascii") as fh:
            raw = fh.read()
        rest = raw[raw.rfind(")") + 2:].split()
        return f"proc:{rest[19]}"
    except (OSError, IndexError, ValueError):
        return None


def _append(path: str, row: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _workbench_root(run_dir: str) -> str | None:
    absolute = os.path.abspath(run_dir)
    runs_dir = os.path.dirname(absolute)
    if os.path.basename(runs_dir) != "runs":
        return None
    return os.path.dirname(os.path.dirname(runs_dir))


def _mirror(run_dir: str, row: dict[str, Any]) -> None:
    root = _workbench_root(run_dir)
    if not root:
        return
    try:
        _append(os.path.join(root, "_active", "registered-pids.jsonl"), {
            "run_dir": os.path.abspath(run_dir), **row,
        })
    except OSError:
        # The run-local ledger remains authoritative for normal reap_run.
        pass


def _latest_instance(run_dir: str, pid: int) -> str | None:
    path = os.path.join(run_dir, "pids.json")
    if not os.path.isfile(path):
        return None
    latest = None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("pid") == pid:
                    latest = row.get("instance_id")
    except OSError:
        return None
    return latest


def register_pid(run_dir: str, pid: int, kind: str) -> None:
    row = {"pid": int(pid), "instance_id": _process_instance_id(pid),
           "kind": str(kind), "at": time.time()}
    _append(os.path.join(run_dir, "pids.json"), row)
    _mirror(run_dir, row)


def deregister_pid(run_dir: str, pid: int) -> None:
    row = {"pid": int(pid), "instance_id": _latest_instance(run_dir, pid),
           "kind": "closed", "at": time.time()}
    _append(os.path.join(run_dir, "pids.json"), row)
    _mirror(run_dir, row)


__all__ = ["register_pid", "deregister_pid"]
