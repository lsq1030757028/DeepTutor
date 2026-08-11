# -*- coding: utf-8 -*-
"""gate — 旅程门票（M2 模式隔离的**后果层**，设计稿 §5.1.4 第二层 / ADR-M2-01 G5）。

## 这层解决什么，以及它刻意不解决什么

journey 工具上了 MCP 面之后，主聊 agent 在**任何** capability 轮里都看得见它们
（MCP 适配器一律 `deferred=True` 走渐进披露，但那只是降低概率、**不是闸**；
`owned_tools` 只治内置工具、管不到 MCP 面；管理员 grant 是 unrestricted 会短路）。
0025 §2 据此裁定 B1：**可见性层如实标"软"，后果层做硬**。

本模块就是那个硬层：把「不该被调」降级成「被调也造不出后果」。

- `journey_ingest` 是唯一能创建批次的工具，**fail-closed 于门票**：
  无 token / token 不匹配 / token 过期 → `E_GATE_REQUIRED`，
  **不创建任何批次目录、不落任何产物**（这一条是 G5 的机械判据，别在调用侧提前 mkdir）。
- 其余 journey 工具一律要求**既存** batch_id → 找不到判 `E_NO_BATCH`。
  普通聊天轮的模型手里没有合法 batch_id，编造一个则命中此码。

**不解决**：模型「看得见」这些工具。那是软约束，判据里如实标软（DoD#8e-3 只作信号）。

## 门票怎么来

「测试」capability 的服务端在开旅程时调 `issue()` 下发，并把**门票 id（不是密钥）**
写进会话事件流。门票落盘在 `data/test-workbench/gate-tokens.json`，只存
`sha256(token)`——**明文不落盘**，与凭据纪律同口径（泄漏了文件也换不出门票）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

E_GATE_REQUIRED = "E_GATE_REQUIRED"
E_NO_BATCH = "E_NO_BATCH"

#: 门票有效期。够走完一趟旅程的接入段，又不至于长期有效。
DEFAULT_TTL_S = 12 * 3600

#: 调用面标记的合法取值（落 events.jsonl 的 `caller_surface`，DoD#8e-3 的信号源）。
CALLER_SURFACES = ("capability", "workbench", "unknown")


def _store_path(root: str | None = None) -> str:
    from server.journey import artifacts
    return os.path.join(artifacts.workbench_root(root), "gate-tokens.json")


def _load(root: str | None = None) -> dict[str, Any]:
    path = _store_path(root)
    if not os.path.isfile(path):
        return {"version": 1, "tokens": {}}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"version": 1, "tokens": {}}
    if not isinstance(data, dict) or not isinstance(data.get("tokens"), dict):
        return {"version": 1, "tokens": {}}
    return data


def _save(data: dict[str, Any], root: str | None = None) -> None:
    path = _store_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(*, session_ref: str = "", caller_surface: str = "capability",
          ttl_s: int = DEFAULT_TTL_S, root: str | None = None) -> dict[str, Any]:
    """下发一张门票。返回 `{token, token_id, expires_at}`——`token` 只在此刻出现一次。"""
    token = secrets.token_urlsafe(24)
    digest = _hash(token)
    now = int(time.time())
    data = _load(root)
    data["tokens"][digest] = {
        "token_id": digest[:12],
        "session_ref": str(session_ref or ""),
        "caller_surface": (caller_surface if caller_surface in CALLER_SURFACES
                           else "unknown"),
        "issued_at": now,
        "expires_at": now + int(ttl_s),
        "used_batches": [],
    }
    _prune(data, now)
    _save(data, root)
    return {"token": token, "token_id": digest[:12], "expires_at": now + int(ttl_s)}


def _prune(data: dict[str, Any], now: int) -> None:
    """顺手清过期票，避免 store 无限长。"""
    for digest in [d for d, row in data["tokens"].items()
                   if int(row.get("expires_at") or 0) < now - 86400]:
        data["tokens"].pop(digest, None)


def verify(token: str, *, root: str | None = None) -> dict[str, Any]:
    """校验门票。返回 `{ok, code, message?, token_id?, caller_surface?}`。

    比对走 `hmac.compare_digest`——门票是能创建批次的凭物，不给时序侧信道留缝。
    """
    if not isinstance(token, str) or not token.strip():
        return {"ok": False, "code": E_GATE_REQUIRED,
                "message": "本工具需要旅程门票（gate_token）。门票由「测试」模式在开旅程时下发；"
                           "普通聊天轮没有门票，因此不能创建批次。"}
    digest = _hash(token.strip())
    data = _load(root)
    row = None
    for known, candidate in data["tokens"].items():
        if hmac.compare_digest(known, digest):
            row = candidate
            break
    if row is None:
        return {"ok": False, "code": E_GATE_REQUIRED,
                "message": "门票不匹配。它不是本机下发的，或者已经被清理。"}
    if int(row.get("expires_at") or 0) < int(time.time()):
        return {"ok": False, "code": E_GATE_REQUIRED,
                "message": "门票已过期。回「测试」模式重新开一趟旅程。"}
    return {"ok": True, "code": "OK", "token_id": row.get("token_id", ""),
            "caller_surface": row.get("caller_surface", "unknown")}


def bind_batch(token: str, batch_id: str, *, root: str | None = None) -> None:
    """把批次记到门票名下（可观测用，不是闸）。"""
    digest = _hash((token or "").strip())
    data = _load(root)
    row = data["tokens"].get(digest)
    if row is None:
        return
    used = row.setdefault("used_batches", [])
    if batch_id not in used:
        used.append(batch_id)
    _save(data, root)


def require_batch(batch_id: Any, *, root: str | None = None) -> dict[str, Any]:
    """其余 journey 工具的前置：批次必须**既存**。

    这是 G5 的第二半——编造一个 batch_id 命中 `E_NO_BATCH`，而不是被当成
    「新批次」悄悄建出来。
    """
    from server.journey import artifacts
    if not isinstance(batch_id, str) or not batch_id.strip():
        return {"ok": False, "code": E_NO_BATCH, "message": "缺 batch_id。"}
    try:
        artifacts.load_batch(batch_id.strip(), root=root)
    except artifacts.ArtifactError:
        return {"ok": False, "code": E_NO_BATCH,
                "message": f"批次不存在：{batch_id}。journey 工具只在既存批次上工作；"
                           "新批次只能由带门票的 journey_ingest 创建。"}
    return {"ok": True, "code": "OK"}


def normalize_surface(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in CALLER_SURFACES else "unknown"
