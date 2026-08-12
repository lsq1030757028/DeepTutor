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


def _live_processes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 append-only 台账还原仍未销记的进程实例。"""
    opened: dict[tuple[int, str | None], dict[str, Any]] = {}
    for row in rows:
        pid = row.get("pid")
        if not isinstance(pid, int):
            continue
        instance_id = row.get("instance_id")
        instance_id = instance_id if isinstance(instance_id, str) and instance_id else None
        key = (pid, instance_id)
        if row.get("kind") == "closed":
            if instance_id is None:
                opened = {k: v for k, v in opened.items() if k[0] != pid}
            else:
                opened.pop(key, None)
        else:
            opened[key] = {"pid": pid, "instance_id": instance_id,
                           "at": row.get("at", 0), "kind": row.get("kind")}
    return list(opened.values())


def live_registered_processes(run_dir: str) -> list[dict[str, Any]]:
    """登记未销记的进程实例；新记录必须带实例标识才允许被回收。"""
    return _live_processes(_read_ledger(run_dir))


def live_registered_pids(run_dir: str) -> list[int]:
    """兼容旧调用方：只返回登记未销记的 PID 清单。"""
    return [row["pid"] for row in live_registered_processes(run_dir)]


def _windows_instance_id_from_handle(kernel32: Any, handle: Any) -> str | None:
    """从已打开的 Windows 进程句柄读取创建时间（句柄绑定具体进程实例）。"""
    import ctypes
    from ctypes import wintypes

    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE,
                                         ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME)]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    if not kernel32.GetProcessTimes(handle, ctypes.byref(creation),
                                    ctypes.byref(exit_time), ctypes.byref(kernel),
                                    ctypes.byref(user)):
        return None
    ticks = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    return f"win:{ticks}"


def _process_instance_id(pid: int) -> str | None:
    """返回 PID 当前对应进程的稳定实例标识；无法确认时返回 None。"""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                         wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return None
        try:
            return _windows_instance_id_from_handle(kernel32, handle)
        finally:
            kernel32.CloseHandle(handle)

    try:
        with open(f"/proc/{int(pid)}/stat", encoding="ascii") as fh:
            raw = fh.read()
        rest = raw[raw.rfind(")") + 2:].split()
        return f"proc:{rest[19]}"
    except (OSError, IndexError, ValueError):
        return None


def _windows_kill_matching_instance(pid: int, expected_instance_id: str) -> bool:
    """在同一个 Windows 句柄上复核创建时间并终止，消除 PID 检查/终止竞态。"""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                     wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x0001 | 0x1000, False, int(pid))
    if not handle:
        return False
    try:
        if _windows_instance_id_from_handle(kernel32, handle) != expected_instance_id:
            return False
        return bool(kernel32.TerminateProcess(handle, int(signal.SIGTERM)))
    finally:
        kernel32.CloseHandle(handle)


def _instance_state(pid: int, expected_instance_id: str | None) -> str:
    """match / gone / mismatch / unverified；失败方向始终是不杀。"""
    if not expected_instance_id:
        return "unverified"
    current = _process_instance_id(pid)
    if current is None:
        return "unverified" if _pid_alive(pid) else "gone"
    return "match" if current == expected_instance_id else "mismatch"


def _kill_registered(pid: int, expected_instance_id: str | None) -> bool:
    """仅终止登记时的同一进程实例；旧台账或实例不一致一律不杀。"""
    if _instance_state(pid, expected_instance_id) != "match":
        return False
    if os.name == "nt":
        return _windows_kill_matching_instance(pid, str(expected_instance_id))
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, PermissionError):
        return False


# ── 登记（run 目录 + 根级镜像双写）──────────────────────────────────────────


def _mirror_path(root: str | None = None) -> str:
    return os.path.join(_active_dir(root), MIRROR_FILE)


def _mirror_append(run_dir: str, pid: int, kind: str,
                   root: str | None = None,
                   instance_id: str | None = None) -> None:
    try:
        with open(_mirror_path(root), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_dir": os.path.abspath(run_dir), "pid": pid,
                                 "instance_id": instance_id, "kind": kind,
                                 "at": time.time()}) + "\n")
    except OSError:
        # 镜像写失败不该让主流程崩——但也不静默：run 目录那份仍在，
        # 只是"目录被删后还能回收"这条兜底本次不成立。
        pass


def register_pid(run_dir: str, pid: int, kind: str,
                 root: str | None = None) -> None:
    """登记一个子进程 PID。与 `pw_runtime.register_pid` 同格式，另写根级镜像。"""
    instance_id = _process_instance_id(pid)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "pids.json"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"pid": pid, "instance_id": instance_id,
                             "kind": kind, "at": time.time()}) + "\n")
    _mirror_append(run_dir, pid, kind, root, instance_id)


def deregister_pid(run_dir: str, pid: int, root: str | None = None) -> None:
    """销记。两处都要写，否则镜像会把已经正常关掉的 PID 当成遗留去杀。"""
    opened = [row for row in live_registered_processes(run_dir) if row["pid"] == pid]
    instance_id = opened[-1].get("instance_id") if opened else None
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "pids.json"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"pid": pid, "instance_id": instance_id,
                             "kind": "closed", "at": time.time()}) + "\n")
    _mirror_append(run_dir, pid, "closed", root, instance_id)


def mirrored_live_processes(run_dir: str,
                            root: str | None = None) -> list[dict[str, Any]]:
    """镜像台账里该 run 仍未销记的进程实例。"""
    path = _mirror_path(root)
    if not os.path.isfile(path):
        return []
    target = os.path.abspath(run_dir)
    rows: list[dict[str, Any]] = []
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
            rows.append(row)
    return _live_processes(rows)


def mirrored_live_pids(run_dir: str, root: str | None = None) -> list[int]:
    """兼容旧调用方：镜像台账里该 run 仍未销记的 PID。"""
    return [row["pid"] for row in mirrored_live_processes(run_dir, root)]


def forget_mirrored(run_dir: str, processes: list[dict[str, Any]],
                    root: str | None = None) -> None:
    """回收后按进程实例销记，避免 PID 复用时关闭错误记录。"""
    for row in processes:
        _mirror_append(run_dir, row["pid"], "closed", root,
                       row.get("instance_id"))


def reap_orphans(run_dir: str, root: str | None = None) -> dict[str, Any]:
    """**目录已消失时的兜底回收**：只按镜像台账里该 run 登记过的 PID 回收。

    这就是 BB-501 的机制侧修复。注意两条边界：
    - 只认 `run_dir` 完全相等的登记行——不做前缀匹配，避免误伤同名前缀的别的 run；
    - 只杀登记过的 PID，**不按进程名扫**。
    """
    reaped, gone, mismatched, unverified = [], [], [], []
    processes = mirrored_live_processes(run_dir, root)
    for process in processes:
        pid, instance_id = process["pid"], process.get("instance_id")
        state = _instance_state(pid, instance_id)
        if state == "mismatch":
            mismatched.append(pid)
        elif state == "unverified":
            unverified.append(pid)
        elif state == "gone":
            gone.append(pid)
        elif _kill_registered(pid, instance_id):
            reaped.append(pid)
        else:
            gone.append(pid)
    if processes:
        forget_mirrored(run_dir, processes, root)
    return {"reaped": reaped, "already_gone": gone,
            "identity_mismatch": mismatched, "identity_unverified": unverified,
            "source": "mirror"}


def reap_run(run_dir: str) -> dict[str, Any]:
    """run 结束/中止后的兜底回收：只处理该 run 登记表里的 PID。"""
    reaped, gone, mismatched, unverified = [], [], [], []
    for process in live_registered_processes(run_dir):
        pid, instance_id = process["pid"], process.get("instance_id")
        state = _instance_state(pid, instance_id)
        if state == "mismatch":
            mismatched.append(pid)
        elif state == "unverified":
            unverified.append(pid)
        elif state == "gone":
            gone.append(pid)
        elif _kill_registered(pid, instance_id):
            reaped.append(pid)
        else:
            gone.append(pid)
    if reaped or gone or mismatched or unverified:
        with open(os.path.join(run_dir, "reap.log"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.time(), "reaped": reaped,
                                 "already_gone": gone,
                                 "identity_mismatch": mismatched,
                                 "identity_unverified": unverified}) + "\n")
    return {"reaped": reaped, "already_gone": gone,
            "identity_mismatch": mismatched, "identity_unverified": unverified}


# ── 并发闸（同时 run ≤ 2；ADR-M1-02 纪律 1）─────────────────────────────────


def _active_dir(root: str | None = None) -> str:
    """活跃标记与镜像台账的落点。挂在工作台根下、**不在任何单个 run 目录里**——
    放进 run 目录就是 BB-501 的原始形态（目录一删线索全无）。"""
    d = os.path.join(artifacts.workbench_root(root), ACTIVE_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        # Windows 的 os.kill(pid, 0) 不是 POSIX 的无副作用存活探针：0 会被当成
        # 进程退出码并终止目标。并发闸每次 reap_stale 都调用它，实测会把正在跑的
        # pytest/工具进程直接打掉，再把标记当“已死”清走，既误杀又放穿 ≤2 的闸。
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                         wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                                ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(process_query_limited_information,
                                      False, int(pid))
        if not handle:
            # 权限不足不能证明进程已死，按存活处理（失败方向是少回收、不误杀）。
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
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
        expected = mark.get("instance_id")
        state = (_instance_state(pid, expected)
                 if isinstance(pid, int) else "gone")
        if state in ("gone", "mismatch") or expired:
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
        pid = os.getpid()
        json.dump({"pid": pid, "instance_id": _process_instance_id(pid),
                   "run_dir": run_dir, "at": time.time()}, fh)
    return {"ok": True}


def release_slot(run_id: str, root: str | None = None) -> None:
    path = os.path.join(_active_dir(root), run_id + ".json")
    if os.path.isfile(path):
        os.remove(path)
