# -*- coding: utf-8 -*-
"""retier — 改档（ADR-3 / AC5，批次 3）。

0028 §二原裁定「定档前置、不补改档工具」，其理由是中途改档会让下游产物的档位
前提失效。ADR-3 推翻的方式不是否认该前提，而是把「失效 → 重走」**自动化**：

- 触发窗口：`approved_caseset` 产出**之前**。采纳之后改档 = 新批次（0028 语义保留）。
- 作废范围：按 `_PREREQ` 链，`business_frame` / `test_analysis` / `case_draft`
  三格作废；`intake_profile` 保留（同一需求实体，只更新档位字段）。
- 已答澄清项按新档位复用：作废前把 `question_ledger` 与 `clarifications`
  存进改档事件，`clarify()` 从那里接续「不许静默丢项」的账本（AC11/AC13 跨改档不断）。
- 失败回退：任一步失败 → 恢复原样，不留半新半旧（AC5 的注入判据）。

「重生成」本身是模型的活（clarify/analyze/draft 是判断产物，服务端造不出来）；
这里负责的是**作废与账本一致性**这半边。
"""
from __future__ import annotations

import os
from typing import Any

from server.journey import artifacts
from server.journey.ingest import TIERS

#: 改档作废的三格（_PREREQ 链上 intake_profile 之后、采纳之前的判断产物）。
INVALIDATED_KINDS = ("business_frame", "test_analysis", "case_draft")

#: 三格各自的派生视图文件（物化视图随源一起作废，禁半新半旧）。
_DERIVED_FILES = {
    "business_frame": ("confirmed_facts.md",),
    "test_analysis": ("test_analysis.md",),
    "case_draft": (),
}


def _artifact_file_snapshot(bid: str, root: str | None) -> dict[str, bytes | None]:
    """把会被本次改档触碰的文件全部读进内存（None = 原本不存在）。"""
    d = artifacts.batch_dir(bid, root=root)
    paths: list[str] = [artifacts.artifact_path(bid, "intake_profile", root=root)]
    for kind in INVALIDATED_KINDS:
        paths.append(artifacts.artifact_path(bid, kind, root=root))
        paths.extend(os.path.join(d, name) for name in _DERIVED_FILES[kind])
    snapshot: dict[str, bytes | None] = {}
    for path in paths:
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                snapshot[path] = fh.read()
        else:
            snapshot[path] = None
    return snapshot


def _restore(snapshot: dict[str, bytes | None]) -> None:
    for path, blob in snapshot.items():
        if blob is None:
            if os.path.isfile(path):
                os.remove(path)
        else:
            with open(path, "wb") as fh:
                fh.write(blob)


def preserved_question_ledger(batch_id: str, *,
                              root: str | None = None) -> dict[str, Any]:
    """最近一次改档事件里保存的疑点账本（business_frame 已被作废时的接续点）。"""
    for event in reversed(artifacts.read_events(batch_id, root=root)):
        if event.get("type") == "tier_confirm" and event.get("source") == "retier":
            ledger = event.get("preserved_question_ledger")
            return dict(ledger) if isinstance(ledger, dict) else {}
    return {}


def change_tier(batch_id: str, *, new_tier: str, via: str,
                root: str | None = None) -> dict[str, Any]:
    """改档。返回 {ok, prior_tier, tier, invalidated, preserved_clarifications}。"""
    if new_tier not in TIERS:
        return {"ok": False, "code": "E_BAD_TIER",
                "error": f"tier 必须是 {TIERS} 之一，实为 {new_tier!r}"}
    if not str(via or "").strip():
        return {"ok": False, "code": "E_SOURCE_REQUIRED",
                "error": "改档必须带来源（via）——账本完整性靠记录改档来源保证，"
                         "不靠不可变（ADR-3）。"}
    profile = artifacts.load_artifact(batch_id, "intake_profile", root=root)
    prior_tier = str(profile.get("tier") or "")
    if artifacts.has_artifact(batch_id, "approved_caseset", root=root):
        return {"ok": False, "code": "E_TIER_LOCKED",
                "error": "采纳集已冻结，本批次档位不再可改；"
                         "换档位请开新批次（采纳后改档 = 新批次，0028 语义）。"}
    if new_tier == prior_tier:
        return {"ok": False, "code": "E_SAME_TIER",
                "error": f"当前已是 {prior_tier} 档，无档可改。"}

    preserved_ledger: dict[str, Any] = {}
    preserved_clarifications: list[dict[str, Any]] = []
    if artifacts.has_artifact(batch_id, "business_frame", root=root):
        frame = artifacts.load_artifact(batch_id, "business_frame", root=root)
        preserved_ledger = dict(frame.get("question_ledger") or {})
        preserved_clarifications = list(frame.get("clarifications") or [])
    else:
        # business_frame 还没有（或上次改档后还没重生成）：接续上一份保存的账本，
        # 连续两次改档也不许把已答疑点弄丢。
        preserved_ledger = preserved_question_ledger(batch_id, root=root)

    snapshot = _artifact_file_snapshot(batch_id, root)
    d = artifacts.batch_dir(batch_id, root=root)
    invalidated: list[str] = []
    try:
        # 1. intake_profile 保留身份，只动档位字段（created_at 原样 = 同一份接入）
        updated = dict(profile)
        updated["tier"] = new_tier
        updated["tier_confirmed_via"] = via
        artifacts.save_artifact(batch_id, "intake_profile", updated, root=root)
        # 2. 三格 + 派生视图作废
        for kind in INVALIDATED_KINDS:
            path = artifacts.artifact_path(batch_id, kind, root=root)
            if os.path.isfile(path):
                os.remove(path)
                invalidated.append(kind)
            for name in _DERIVED_FILES[kind]:
                derived = os.path.join(d, name)
                if os.path.isfile(derived):
                    os.remove(derived)
        # 3. 改档事件（tier_confirm 只增字段：source/prior_tier/preserved_*）
        event = artifacts.append_event(batch_id, {
            "type": "tier_confirm",
            "tier": new_tier,
            "via": via,
            "source": "retier",
            "prior_tier": prior_tier,
            "invalidated": invalidated,
            "preserved_question_ledger": preserved_ledger,
        }, root=root)
    except Exception as exc:  # noqa: BLE001 - 任何失败都必须整体回退
        _restore(snapshot)
        return {"ok": False, "code": "E_RETIER_ROLLED_BACK",
                "error": f"改档中途失败，已整体回退到 {prior_tier} 档，"
                         f"账本无半新半旧状态。失败原因：{exc}",
                "failed_at": "event" if invalidated else "invalidate"}
    return {"ok": True, "prior_tier": prior_tier, "tier": new_tier,
            "invalidated": invalidated, "event": event,
            "preserved_clarifications": preserved_clarifications,
            "preserved_question_ledger": preserved_ledger}
