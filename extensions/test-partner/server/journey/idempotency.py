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

import json
import os
import time
from typing import Any

from server.journey import artifacts
from server.journey.digest import sha256_digest

LEDGER_NAME = "idempotency.jsonl"


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


def lookup(batch_id: str, key: str, *, owner: str | None = None,
           root: str | None = None) -> dict[str, Any] | None:
    """查这个 key 有没有跑过。返回上次的结果体，没有则 None。"""
    path = _ledger_path(batch_id, owner=owner, root=root)
    if not os.path.isfile(path):
        return None
    hit = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("key") == key:
                hit = row          # 后写的覆盖先写的（append-only 台账的正常读法）
    return hit


def record(batch_id: str, key: str, tool: str, result: dict[str, Any], *,
           owner: str | None = None, root: str | None = None) -> None:
    """把一次成功的副作用调用记进台账。**只记成功**——失败的调用重试是合理的。"""
    path = _ledger_path(batch_id, owner=owner, root=root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"key": key, "tool": tool, "at": time.time(),
                             "result": result}, ensure_ascii=False) + "\n")


def guard(batch_id: str, tool: str, params: dict[str, Any],
          idempotency_key: str = "", *, owner: str | None = None,
          root: str | None = None) -> tuple[str, dict[str, Any] | None]:
    """副作用工具的统一入口守卫。

    返回 `(key, replay_result_or_None)`：
    - `replay_result` 非 None → 直接把它带 `replayed=True` 回给调用方，**别再执行**；
    - 为 None → 正常执行，执行成功后调 `record(batch_id, key, ...)`。
    """
    key = (idempotency_key or "").strip() or derive_key(batch_id, tool, params)
    hit = lookup(batch_id, key, owner=owner, root=root)
    if hit is None:
        return key, None
    payload = dict(hit.get("result") or {})
    payload["replayed"] = True
    payload["idempotency_key"] = key
    payload["first_run_at"] = hit.get("at")
    payload.setdefault("code", "REPLAYED")
    payload["message"] = (
        "这次调用与之前某次的输入完全相同，直接返回上次的结果，**没有产生新的执行**。"
        "如果你确实想重跑，改一处输入或显式换一个 idempotency_key。")
    return key, payload
