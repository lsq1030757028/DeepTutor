# -*- coding: utf-8 -*-
"""adopt — 原子工具 5：采纳冻结。

输入：case_draft + 工作台勾选 → 输出 `ApprovedCaseSet`（双 digest 冻结）。
牙（挂产物）：schema 全量校验 + digest 盖章复算 + cases_gate（对批次目录物化视图），
任一不过 = 产物不落盘（fail-closed）。

稳定 case_id：<caseset_slug>/R<规则号>-C<序号>，采纳时刻分配，永不复用；
重采纳同一 draft_id = 同 case_id 升 case_version（M1 首版一律 v1）。
探测性（probing）用例物化到 cases.md 时预期标「探测:」，其余标「业务:」——
判决权分层是 cases_gate 分层族的闸点。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from typing import Any

from server.journey import artifacts, digest, schema
from server.journey.gates import cases_gate


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", str(text or "").lower()).strip("-")
    return s or "caseset"


def _draft_digest(case: dict[str, Any]) -> str:
    """Digest the business content while excluding draft-only identity fields."""
    payload = dict(case)
    payload.pop("draft_id", None)
    payload.pop("kind", None)
    for key in ("case_id", "case_version", "source_case_digest", "oracle_digest"):
        payload.pop(key, None)
    return digest.sha256_digest(payload)


def _identity_history(batch_id: str) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Recover the latest stable identity for every previously adopted draft.

    New receipts carry an explicit mapping.  For an existing M1 receipt, infer
    the mapping once from its selected order and the matching current caseset.
    """
    latest: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    events = artifacts.read_events(batch_id)
    for event in events:
        if event.get("type") != "adopt_confirm":
            continue
        for identity in event.get("identities") or []:
            draft_id = str(identity.get("draft_id") or "")
            case_id = str(identity.get("case_id") or "")
            version = identity.get("case_version")
            draft_digest = str(identity.get("draft_digest") or "")
            if draft_id and case_id and isinstance(version, int) and draft_digest:
                latest[draft_id] = {
                    "case_id": case_id,
                    "case_version": version,
                    "draft_digest": draft_digest,
                }
                used.add(case_id)

    if artifacts.has_artifact(batch_id, "approved_caseset"):
        current = artifacts.load_artifact(batch_id, "approved_caseset")
        current_cases = current.get("cases") or []
        used.update(str(case.get("case_id") or "") for case in current_cases)
        matching = next((
            event for event in reversed(events)
            if event.get("type") == "adopt_confirm"
            and event.get("caseset_id") == current.get("caseset_id")
        ), None)
        selected = matching.get("selected") if matching else None
        if isinstance(selected, list) and len(selected) == len(current_cases):
            for draft_id, case in zip(selected, current_cases):
                if str(draft_id) not in latest:
                    latest[str(draft_id)] = {
                        "case_id": str(case.get("case_id") or ""),
                        "case_version": int(case.get("case_version") or 1),
                        "draft_digest": _draft_digest(case),
                    }
    used.discard("")
    return latest, used


def _next_case_id(slug: str, rule_number: str, used: set[str]) -> str:
    pattern = re.compile(
        rf"^{re.escape(slug)}/R{re.escape(rule_number)}-C([0-9]{{3}})$")
    highest = max(
        (int(match.group(1)) for case_id in used
         if (match := pattern.match(case_id))),
        default=0,
    )
    return f"{slug}/R{rule_number}-C{highest + 1:03d}"


def _materialize(batch_id: str, caseset: dict[str, Any]) -> None:
    """派生视图：cases.md（人读）+ cases_exec.json + cases_index.json（机器读）。
    全部只带 digest 回指，禁反写（派生物可随时删除重生成）。"""
    d = artifacts.batch_dir(batch_id)
    rows = ["# 用例集（派生视图，源=approved_caseset.json，禁手改）",
            "", "| 用例ID | 场景 | 步骤 | 预期结果 |", "|---|---|---|---|"]
    exec_cases = []
    for c in caseset["cases"]:
        layer = "探测" if (c.get("source_anchor") or {}).get("probing") else "业务"
        steps = " → ".join(c["steps"])
        rows.append(f"| {c['case_id']} | {c.get('title', '')} | {steps} "
                    f"| {layer}:{c['expected']} |")
        exec_cases.append({
            "id": c["case_id"], "case_version": c["case_version"],
            "expect": f"{layer}:{c['expected']}",
            "source_case_digest": c["source_case_digest"],
            "oracle_digest": c["oracle_digest"],
            "schema_version": caseset["schema_version"],
        })
    with open(os.path.join(d, "cases.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
    with open(os.path.join(d, "cases_exec.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": {"caseset_id": caseset["caseset_id"], "derived": True},
                   "cases": exec_cases}, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(d, "cases_index.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema_version": "1.0",
                   "generated_by": "server/journey/adopt.py",
                   "total": len(exec_cases),
                   "cases": [{"id": c["id"]} for c in exec_cases]},
                  fh, ensure_ascii=False, indent=1)


def adopt(batch_id: str, *, selected_draft_ids: list[str],
          caseset_slug: str = "", adopted_via: str = "workbench_selection",
          confirmed_by: str = "", owner: str = "") -> dict[str, Any]:
    draft_art = artifacts.load_artifact(batch_id, "case_draft")
    intake = artifacts.load_artifact(batch_id, "intake_profile")
    by_draft = {c.get("draft_id"): c for c in draft_art["cases"]}
    missing = [d for d in selected_draft_ids if d not in by_draft]
    if missing:
        return {"ok": False, "problems": [f"勾选的 draft_id 不存在：{missing}"]}
    if not selected_draft_ids:
        return {"ok": False, "problems": ["未勾选任何用例——采纳集不能为空"]}

    slug = _slugify(caseset_slug or "bysms")
    identities, used_case_ids = _identity_history(batch_id)
    cases: list[dict[str, Any]] = []
    receipt_identities: list[dict[str, Any]] = []
    for did in selected_draft_ids:
        src = dict(by_draft[did])
        content_digest = _draft_digest(src)
        src.pop("draft_id", None)
        src.pop("kind", None)
        rid = src["source_anchor"]["rule_id"]
        rnum = re.sub(r"[^0-9]", "", rid) or "0"
        previous = identities.get(did)
        if previous:
            case_id = previous["case_id"]
            case_version = previous["case_version"] + (
                previous["draft_digest"] != content_digest)
        else:
            case_id = _next_case_id(slug, rnum, used_case_ids)
            case_version = 1
        used_case_ids.add(case_id)
        src["case_id"] = case_id
        src["case_version"] = case_version
        cases.append(digest.stamp_case_digests(src))
        receipt_identities.append({
            "draft_id": did,
            "draft_digest": content_digest,
            "case_id": case_id,
            "case_version": case_version,
        })

    source = dict(intake["source"])
    if not source.get("content_digest"):
        source.pop("content_digest", None)
    caseset = {
        "schema_version": schema.SCHEMA_VERSION,
        "caseset_id": f"acs-{time.strftime('%Y%m%d')}-{secrets.token_hex(4)}",
        "created_at": artifacts.now_iso(),
        "source": source,
        "approval": {"adopted_at": artifacts.now_iso(), "adopted_via": adopted_via,
                     **({"owner": owner} if owner else {})},
        "cases": cases,
    }
    check = schema.validate_caseset(caseset)
    if not check["ok"]:
        return {"ok": False, "problems": [f"{e['code']} {e['where']}: {e['problem']}"
                                          for e in check["errors"]]}
    for c in cases:
        errs = digest.verify_case_digests(c)
        if errs:
            return {"ok": False, "problems": [f"{c['case_id']} digest 复算不一致"] + errs}

    art = artifacts.save_artifact(batch_id, "approved_caseset", caseset,
                                  schema_version=schema.SCHEMA_VERSION)
    _materialize(batch_id, caseset)
    gate = cases_gate.check(artifacts.batch_dir(batch_id))
    if gate["verdict"] != "PASS":
        # fail-closed：闸不过撤产物与派生视图
        for f in ("approved_caseset.json", "cases.md", "cases_exec.json",
                  "cases_index.json"):
            p = os.path.join(artifacts.batch_dir(batch_id), f)
            if os.path.exists(p):
                os.remove(p)
        return {"ok": False,
                "problems": ["cases_gate: " + p for p in gate["problems"]]}
    artifacts.append_event(batch_id, {
        "type": "adopt_confirm", "via": adopted_via,
        "by": confirmed_by or "unspecified",
        "caseset_id": caseset["caseset_id"],
        "selected": selected_draft_ids,
        "identities": receipt_identities,
    })
    return {"ok": True, "approved_caseset": art, "cases_gate": gate,
            "case_ids": [c["case_id"] for c in cases]}
