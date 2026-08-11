# -*- coding: utf-8 -*-
"""process_registry — 浏览器/viewer 子进程登记与回收（ADR-M1-02 资源纪律）。

纪律：登记表 append-only（pids.json，kind="closed" 行 = 销记）；**只杀登记过的
PID，绝不按进程名广杀**；周期 reap 由每次新 execute 触发 reap_stale（假设清单 #2
的降级路径：supervisor 不适配时 reap 不依赖常驻定时器）。

## BB-501 修复（M2，设计稿 §7.6 / DoD#23）

原缺口：`reap_stale` 只在 `os.path.isdir(run_dir)` 成立时才回收（旧 `:110`）。
pytest 用完删掉 tmp 目录后，`pids.json` 随目录消失 ⇒ **登记过的 PID 从此无人回收**
（实测遗留 9 个 chromium 主进程，最早可溯到 08-08）。

修复是两层，缺一不可：

1. **登记时就镜像一份到根级台账** `<root>/_active/registered-pids.jsonl`。
   run 目录会被删，这份不会——「兜底回收」要有东西可兜，前提是记录活得比目录长。
2. **`reap_stale` 增「目录消失」分支**：run_dir 不在了就改读镜像台账，
   仍然**只杀该 run 自己登记过的 PID**。禁按进程名广杀（工作区既有纪律：曾误杀无关进程）。

顺带修一个比 bank 记录更尖的事实：`journey_console.open_trace` 调的是
`preg.register_pid(...)`，而本模块**此前根本没有这个函数**——调用抛 AttributeError
被它自己的 `except Exception` 吞掉，于是 viewer 进程**一次都没被登记过**。
即泄漏不是"登记了没回收"，是"压根没登记"。本模块现在补上 `register_pid` /
`deregister_pid`（与 `pw_runtime` 同格式，双写 run 目录 + 镜像台账）。
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

#: 根级镜像台账文件名。**它是「run 目录被删」之后唯一还认得那些 PID 的东西。**
MIRROR_FILE = "registered-pids.jsonl"


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


# ── 登记（run 目录 + 根级镜像双写）──────────────────────────────────────────


def _mirror_path(root: str | None = None) -> str:
    return os.path.join(_active_dir(root), MIRROR_FILE)


def _mirror_append(run_dir: str, pid: int, kind: str,
                   root: str | None = None) -> None:
    try:
        with open(_mirror_path(root), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_dir": os.path.abspath(run_dir), "pid": pid,
                                 "kind": kind, "at": time.time()}) + "\n")
    except OSError:
        # 镜像写失败不该让主流程崩——但也不静默：run 目录那份仍在，
        # 只是"目录被删后还能回收"这条兜底本次不成立。
        pass


def register_pid(run_dir: str, pid: int, kind: str,
                 root: str | None = None) -> None:
    """登记一个子进程 PID。与 `pw_runtime.register_pid` 同格式，另写根级镜像。"""
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "pids.json"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"pid": pid, "kind": kind, "at": time.time()}) + "\n")
    _mirror_append(run_dir, pid, kind, root)


def deregister_pid(run_dir: str, pid: int, root: str | None = None) -> None:
    """销记。两处都要写，否则镜像会把已经正常关掉的 PID 当成遗留去杀。"""
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "pids.json"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"pid": pid, "kind": "closed", "at": time.time()}) + "\n")
    _mirror_append(run_dir, pid, "closed", root)


def mirrored_live_pids(run_dir: str, root: str | None = None) -> list[int]:
    """镜像台账里该 run 仍未销记的 PID。**run 目录已删时的唯一线索。**"""
    path = _mirror_path(root)
    if not os.path.isfile(path):
        return []
    target = os.path.abspath(run_dir)
    opened: dict[int, float] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if os.path.abspath(str(row.get("run_dir") or "")) != target:
                continue
            pid = row.get("pid")
            if not isinstance(pid, int):
                continue
            if row.get("kind") == "closed":
                opened.pop(pid, None)
            else:
                opened[pid] = row.get("at", 0)
    return list(opened)


def forget_mirrored(run_dir: str, pids: list[int], root: str | None = None) -> None:
    """回收后把这些 PID 在镜像里标记销记，避免下轮重复尝试。"""
    for pid in pids:
        _mirror_append(run_dir, pid, "closed", root)


def reap_orphans(run_dir: str, root: str | None = None) -> dict[str, Any]:
    """**目录已消失时的兜底回收**：只按镜像台账里该 run 登记过的 PID 回收。

    这就是 BB-501 的机制侧修复。注意两条边界：
    - 只认 `run_dir` 完全相等的登记行——不做前缀匹配，避免误伤同名前缀的别的 run；
    - 只杀登记过的 PID，**不按进程名扫**。
    """
    reaped, gone = [], []
    pids = mirrored_live_pids(run_dir, root)
    for pid in pids:
        if _kill_registered(pid):
            reaped.append(pid)
        else:
            gone.append(pid)
    if pids:
        forget_mirrored(run_dir, pids, root)
    return {"reaped": reaped, "already_gone": gone, "source": "mirror"}


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


def _active_dir(root: str | None = None) -> str:
    """活跃标记与镜像台账的落点。挂在工作台根下、**不在任何单个 run 目录里**——
    放进 run 目录就是 BB-501 的原始形态（目录一删线索全无）。"""
    d = os.path.join(artifacts.workbench_root(root), ACTIVE_DIR_NAME)
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


def reap_stale(root: str | None = None) -> list[str]:
    """清理宿主进程已死或超 TTL 的活跃标记，并回收其 run 的登记 PID。

    **BB-501 修复点**：run 目录还在 → 照旧读 `pids.json`；run 目录已被删 →
    改走镜像台账（`reap_orphans`）。旧版在这里直接跳过，PID 就此无人认领。
    """
    cleaned = []
    d = _active_dir(root)
    for name in os.listdir(d):
        if name == MIRROR_FILE:
            continue
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
            elif run_dir:
                reap_orphans(run_dir, root)
            os.remove(path)
            cleaned.append(name)
    return cleaned


def acquire_slot(run_id: str, run_dir: str, max_concurrent: int = 2,
                 root: str | None = None) -> dict[str, Any]:
    reap_stale(root)
    d = _active_dir(root)
    active = os.listdir(d)
    active = [a for a in active if a != MIRROR_FILE]
    if len(active) >= max_concurrent:
        return {"ok": False, "error": "BUSY_MAX_CONCURRENT_RUNS",
                "active": active,
                "hint": f"同时运行 run ≤ {max_concurrent}（ADR-M1-02），请等其完成后重试"}
    with open(os.path.join(d, run_id + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "run_dir": run_dir, "at": time.time()}, fh)
    return {"ok": True}


def release_slot(run_id: str, root: str | None = None) -> None:
    path = os.path.join(_active_dir(root), run_id + ".json")
    if os.path.isfile(path):
        os.remove(path)
