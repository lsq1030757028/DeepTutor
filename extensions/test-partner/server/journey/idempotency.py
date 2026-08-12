# -*- coding: utf-8 -*-
"""idempotency — 副作用工具的重放防线（BB-504 / 设计稿 §5.2 / ADR-M2-03 G5）。

## 为什么这条**与载体无关**，不因为选了正式 capability 就能免

DT 有两条重放路径，只有一条读 capability：

| 路径 | 判据锚 | 读 capability？ |
|---|---|---|
| 「重新生成」按钮 | `ChatMessages.tsx:1381-1386` 的 `showRegenerate` 含 `capability === "chat"` 判断 | **读** → 选案 B 后免疫 |
| 编辑用户消息 → 分支重跑 | `canEdit`（`:906-907`）= `Boolean(onEdit) && typeof msg.id === "number" && !editDisabled`；`editDisabled={isStreaming}`（`:1368`） | **不读** → **B 同样中招** |

而 `_regenerate` 在 `turn_runtime.py` 的全部出现点只影响持久化与元信息，
**无一处阻断工具派发**——即拿同一条 user 消息重跑完整 agentic loop，副作用工具会再跑一遍。

所以「我们选了正式 capability」**不构成豁免理由**。只测按钮那条路等于什么都没测，
因为按钮恰恰是 B 免疫的那条。两条路径各要一条测试（DoD#8f）。

## 三层处置里的第一层（主防线）

`journey_execute` / `journey_adopt` / `journey_compile` 接 `idempotency_key`：
同 key 重复调用**返回上次结果**并带 `replayed: true`，不产生第二个 run。
key 由「batch_id + 输入 digest」派生，调用方不给也能自动算——
**让调用方自己想起来传 key 是不现实的，默认就得是幂等的。**

另两层不在本模块：写确认人闸不因重放而跳过（在 `execute_run`），
`run_receipt.triggered_by` 可辨识（`fresh` / `regenerate-replay`）。
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import time
from typing import Any, Iterator

from server.journey import artifacts
from server.journey.digest import sha256_digest

LEDGER_NAME = "idempotency.jsonl"
LOCK_DIR = ".idempotency-locks"
EXECUTION_INTENT_DIR = ".execution-intents"


@contextmanager
def _file_lock(path: str, *, timeout_s: float = 1200.0) -> Iterator[None]:
    """跨线程/跨进程独占一个字节锁，进程退出时由 OS 自动释放。

    锁文件本身可以长期保留；它不承载状态。真正的完成状态仍只认 append-only
    ledger，因此崩溃在副作用完成前会释放 reservation，允许调用方重试。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a+b") as fh:
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + timeout_s
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"等待幂等锁超时：{os.path.basename(path)}")
                    time.sleep(0.05)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def derive_key(batch_id: str, tool: str, params: dict[str, Any]) -> str:
    """从批次 + 工具 + 入参派生 key。JCS 规整后 sha256，取前 32 位。

    入参里刻意**不含时间戳与 run_id**：含了就每次都不同，幂等无从谈起。
    """
    body = {"batch_id": batch_id, "tool": tool, "params": params}
    return sha256_digest(body)[:32]


def _ledger_path(batch_id: str, *, owner: str | None = None,
                 root: str | None = None) -> str:
    return os.path.join(artifacts.batch_dir(batch_id, owner=owner, root=root),
                        LEDGER_NAME)


def lookup(batch_id: str, key: str, *, tool: str | None = None,
           owner: str | None = None,
           root: str | None = None) -> dict[str, Any] | None:
    """查这个 ``(tool, key)`` 有没有跑过。tool 为空只为兼容台账诊断。"""
    path = _ledger_path(batch_id, owner=owner, root=root)
    if not os.path.isfile(path):
        return None
    hit = None
    with _file_lock(path + ".lock"):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("key") == key and (tool is None or row.get("tool") == tool):
                    hit = row      # 后写的覆盖先写的（append-only 台账的正常读法）
    return hit


def record(batch_id: str, key: str, tool: str, result: dict[str, Any], *,
           params: dict[str, Any] | None = None,
           owner: str | None = None, root: str | None = None) -> None:
    """把一次成功的副作用调用记进台账。**只记成功**——失败的调用重试是合理的。"""
    path = _ledger_path(batch_id, owner=owner, root=root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _file_lock(path + ".lock"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": key, "tool": tool, "at": time.time(),
                                 "request_digest": (
                                     sha256_digest(params) if params is not None else ""),
                                 "result": result}, ensure_ascii=False) + "\n")


def _replay_payload(hit: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    if hit is None:
        return None
    payload = dict(hit.get("result") or {})
    payload["replayed"] = True
    payload["idempotency_key"] = key
    payload["first_run_at"] = hit.get("at")
    payload.setdefault("code", "REPLAYED")
    payload["message"] = (
        "这次调用与之前某次的输入完全相同，直接返回上次的结果，**没有产生新的执行**。"
        "如果你确实想重跑，改一处输入或显式换一个 idempotency_key。")
    return payload


def _reservation_path(batch_id: str, tool: str, key: str, *,
                      owner: str | None = None, root: str | None = None) -> str:
    lock_id = sha256_digest(
        {"tool": tool, "key": key}).removeprefix("sha256:")[:40]
    return os.path.join(artifacts.batch_dir(batch_id, owner=owner, root=root),
                        LOCK_DIR, lock_id + ".lock")


def _execution_intent_path(batch_id: str, key: str, *,
                           owner: str | None = None,
                           root: str | None = None) -> str:
    """Return a non-secret, owner-bound path for one execute request."""
    intent_id = sha256_digest(
        {"tool": "execute", "key": key}).removeprefix("sha256:")[:40]
    return os.path.join(artifacts.batch_dir(
        batch_id, owner=owner, root=root), EXECUTION_INTENT_DIR,
        intent_id + ".json")


def load_execution_intent(batch_id: str, key: str, *,
                          owner: str | None = None,
                          root: str | None = None) -> dict[str, Any] | None:
    path = _execution_intent_path(
        batch_id, key, owner=owner, root=root)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            row = json.load(fh)
    except (OSError, ValueError):
        return {"status": "corrupt"}
    return row if isinstance(row, dict) else {"status": "corrupt"}


def begin_execution_intent(batch_id: str, key: str,
                           request_digest: str, run_id: str, *,
                           write_risk: bool,
                           owner: str | None = None,
                           root: str | None = None) -> tuple[dict[str, Any], bool]:
    """Persist a stable run identity before execute can reach the target.

    The surrounding :func:`reservation` owns serialization.  Only digests and
    opaque ids are stored here; variables and target credentials never are.
    """
    existing = load_execution_intent(
        batch_id, key, owner=owner, root=root)
    if existing is not None:
        return existing, False
    row = {
        "schema_version": "1.0",
        "tool": "execute",
        "key_sha256": sha256_digest(key),
        "request_digest": request_digest,
        "run_id": run_id,
        "status": "prepared",
        "write_risk": bool(write_risk),
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    artifacts._write_json(  # noqa: SLF001 - package-level atomic primitive
        _execution_intent_path(batch_id, key, owner=owner, root=root), row)
    return row, True


def update_execution_intent(batch_id: str, key: str, *, status: str,
                            result: dict[str, Any] | None = None,
                            owner: str | None = None,
                            root: str | None = None) -> dict[str, Any]:
    row = load_execution_intent(
        batch_id, key, owner=owner, root=root)
    if row is None or row.get("status") == "corrupt":
        raise RuntimeError("execution intent missing or corrupt")
    row = dict(row)
    row["status"] = status
    row["updated_at"] = time.time()
    if result is not None:
        row["result"] = result
    artifacts._write_json(  # noqa: SLF001 - package-level atomic primitive
        _execution_intent_path(batch_id, key, owner=owner, root=root), row)
    return row


@contextmanager
def reservation(batch_id: str, tool: str, params: dict[str, Any],
                idempotency_key: str = "", *, owner: str | None = None,
                root: str | None = None) -> Iterator[tuple[str, dict[str, Any] | None]]:
    """原子 reservation：锁覆盖 ``lookup → 副作用 → record`` 整段。

    唯一域是批次真实 owner 目录下的 ``(tool, key)``。调用失败时调用方不记 ledger，
    离开 context 后锁自然释放；下一次可以重试。调用成功必须在 context 内 ``record``。
    """
    explicit_key = (idempotency_key or "").strip()
    key = explicit_key or derive_key(batch_id, tool, params)
    request_digest = sha256_digest(params)
    path = _reservation_path(batch_id, tool, key, owner=owner, root=root)
    with _file_lock(path):
        hit = lookup(batch_id, key, tool=tool, owner=owner, root=root)
        if hit is not None and explicit_key and \
                hit.get("request_digest") != request_digest:
            yield key, {
                "ok": False,
                "code": "E_IDEMPOTENCY_CONFLICT",
                "message": (
                    "这个 idempotency_key 已绑定另一组输入；本次没有执行。"
                    "请复用原输入，或换一个新 key。"),
                "idempotency_key": key,
            }
        else:
            yield key, _replay_payload(hit, key)


def guard(batch_id: str, tool: str, params: dict[str, Any],
          idempotency_key: str = "", *, owner: str | None = None,
          root: str | None = None) -> tuple[str, dict[str, Any] | None]:
    """兼容旧测试/诊断的单次查询；生产副作用必须用 :func:`reservation`。

    返回 `(key, replay_result_or_None)`：
    - `replay_result` 非 None → 直接把它带 `replayed=True` 回给调用方，**别再执行**；
    - 为 None → 只表示查询当刻未命中；本函数不持锁，不能包围真实副作用。
    """
    explicit_key = (idempotency_key or "").strip()
    key = explicit_key or derive_key(batch_id, tool, params)
    hit = lookup(batch_id, key, tool=tool, owner=owner, root=root)
    if hit is None:
        return key, None
    if explicit_key and hit.get("request_digest") != sha256_digest(params):
        return key, {
            "ok": False,
            "code": "E_IDEMPOTENCY_CONFLICT",
            "message": "这个 idempotency_key 已绑定另一组输入；本次没有执行。",
            "idempotency_key": key,
        }
    return key, _replay_payload(hit, key)
