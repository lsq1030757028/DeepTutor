# -*- coding: utf-8 -*-
"""Journey batch existence and caller-surface guards.

Capability and owner authorization no longer lives in a public ticket store.
DeepTutor signs every Journey call and ``bridge_auth`` verifies it before any
artifact lookup. This module retains only business prerequisites shared by the
trusted tool implementations.
"""
from __future__ import annotations

from typing import Any

E_NO_BATCH = "E_NO_BATCH"

#: 调用面标记的合法取值（落 events.jsonl 的 `caller_surface`，DoD#8e-3 的信号源）。
CALLER_SURFACES = ("capability", "workbench", "unknown")


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
                "message": f"批次不存在：{batch_id}。journey 工具只在既存批次上工作。"}
    return {"ok": True, "code": "OK"}


def normalize_surface(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in CALLER_SURFACES else "unknown"
