# -*- coding: utf-8 -*-
"""process_registry — 浏览器/viewer 子进程登记与回收（ADR-M1-02 资源纪律）。

纪律：登记表 append-only（pids.json，kind="closed" 行 = 销记）；**只杀登记过的
PID，绝不按进程名广杀**；周期 reap 由每次新 execute 触发 reap_stale（假设清单 #2
的降级路径：supervisor 不适配时 reap 不依赖常驻定时器）。
"""
from __future__ import annotations

import json
import os
import signal
import time
from typing import Any

from server.journey import artifacts

ACTIVE_DIR_NAME = "_active"
STALE_TTL_S = 30 * 60


def _read_ledger(run_dir: str) -> list[dict[str, Any]]:
    path = os.path.join(run_dir, "pids.json")
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def live_registered_pids(run_dir: str) -> list[int]:
    """登记未销记的 PID 清单。"""
    opened: dict[int, float] = {}
    for row in _read_ledger(run_dir):
        pid = row.get("pid")
        if not isinstance(pid, int):
            continue
        if row.get("kind") == "closed":
            opened.pop(pid, None)
        else:
            opened[pid] = row.get("at", 0)
    return list(opened)


def _kill_registered(pid: int) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, PermissionError):
        return False


def reap_run(run_dir: str) -> dict[str, Any]:
    """run 结束/中止后的兜底回收：只处理该 run 登记表里的 PID。"""
    reaped, gone = [], []
    for pid in live_registered_pids(run_dir):
        if _kill_registered(pid):
            reaped.append(pid)
        else:
            gone.append(pid)
    if reaped or gone:
        with open(os.path.join(run_dir, "reap.log"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.time(), "reaped": reaped,
                                 "already_gone": gone}) + "\n")
    return {"reaped": reaped, "already_gone": gone}


# ── 并发闸（同时 run ≤ 2；ADR-M1-02 纪律 1）─────────────────────────────────


def _active_dir() -> str:
    d = os.path.join(artifacts.RUNS_ROOT, ACTIVE_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def reap_stale() -> list[str]:
    """清理宿主进程已死或超 TTL 的活跃标记，并回收其 run 的登记 PID。"""
    cleaned = []
    d = _active_dir()
    for name in os.listdir(d):
        path = os.path.join(d, name)
        try:
            with open(path, encoding="utf-8") as fh:
                mark = json.load(fh)
        except (OSError, ValueError):
            os.remove(path)
            continue
        pid = mark.get("pid")
        expired = time.time() - mark.get("at", 0) > STALE_TTL_S
        if (isinstance(pid, int) and not _pid_alive(pid)) or expired:
            run_dir = mark.get("run_dir")
            if run_dir and os.path.isdir(run_dir):
                reap_run(run_dir)
            os.remove(path)
            cleaned.append(name)
    return cleaned


def acquire_slot(run_id: str, run_dir: str, max_concurrent: int = 2) -> dict[str, Any]:
    reap_stale()
    d = _active_dir()
    active = os.listdir(d)
    if len(active) >= max_concurrent:
        return {"ok": False, "error": "BUSY_MAX_CONCURRENT_RUNS",
                "active": active,
                "hint": f"同时运行 run ≤ {max_concurrent}（ADR-M1-02），请等其完成后重试"}
    with open(os.path.join(d, run_id + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "run_dir": run_dir, "at": time.time()}, fh)
    return {"ok": True}


def release_slot(run_id: str) -> None:
    path = os.path.join(_active_dir(), run_id + ".json")
    if os.path.isfile(path):
        os.remove(path)
