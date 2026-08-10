# -*- coding: utf-8 -*-
"""verdict_projection — 结论账本闸 2 + 唯一投影器（移植件）。

原件：test_agent/kit/tools/bundle_to_verdicts.js + test_agent/loop/record.js
（只读采石场；抄语义与测试向量，不抄依赖）。

账本 SoT = evidence-bundle.json；verdicts.jsonl 是【派生投影】，只能由本模块
确定性重写，禁手写。牙一条不减：
- 词表牙（BB-297）：证据类别/ui_surface 不在词表 = 结构错当场报（不掉进 D1 误诊）；
- D1：PASS/FAIL 证据全是定位类（source/spec/file/code）→ 拒，不落账；
- 覆盖越证据：claimed > observed → 拒；
- UI 部署锚：dom/screenshot 观测须 surface=deployed + origin 可达 commit，
  仅 local_build → 封顶 PENDING:needs-deploy-anchor；未声明 surface → 封顶
  PENDING:needs-ui-surface（封顶留痕，不许整条消失）；
- 高风险 PASS 未过 F9 → 封顶 PENDING:needs-f9；
- 未声明 evidence_types → PASS/FAIL 封顶 PENDING:needs-evidence-type（fail-closed 不猜）；
- 账本不在场 ≠ 0 条结论：fail-closed（NO_LEDGER），且不许落空 verdicts.jsonl。

证据类别词表【单点定义 · 本包唯一 SoT】：消费者（mechanical_check 等）一律 import
本模块取词表，禁再写一份字面量（BB-297 防漂）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

# ── 词表（单点定义）────────────────────────────────────────────────────────
OBSERVED = ["api", "http", "db", "dom", "log", "screenshot", "live"]
LOCATOR = ["source", "spec", "file", "code"]
UI_OBSERVED = ["dom", "screenshot"]
EVIDENCE_TYPES = OBSERVED + LOCATOR
UI_SURFACES = ["deployed", "local_build"]
VERDICTS = ["PASS", "FAIL", "PENDING", "BLOCKED"]

_MAP = {"pass": "PASS", "fail": "FAIL", "undecidable": "PENDING"}
MAX_DEPTH = 3
BUNDLE_NAME = "evidence-bundle.json"


def is_known_evidence_type(t: Any) -> bool:
    return str(t) in EVIDENCE_TYPES


# ── record 牙：出结论必经此（原件 record.js recordVerdict）─────────────────


def record_verdict(rec: dict[str, Any]) -> dict[str, Any]:
    """闸2 的牙。返回 {ok, recorded, reason}；拒 = 不落账。"""
    errs: list[str] = []
    if rec.get("verdict") not in VERDICTS:
        errs.append("verdict 非法(限 PASS/FAIL/PENDING/BLOCKED)")
    ev = rec.get("evidence") or []
    need_deploy_anchor = False
    missing_ui_surface = False
    for e in ev:
        if e and e.get("type") is not None and not is_known_evidence_type(e["type"]):
            errs.append(
                f"证据类别 {json.dumps(e['type'], ensure_ascii=False)} 不在词表 —— "
                f"观测类限 {'/'.join(OBSERVED)};定位类限 {'/'.join(LOCATOR)}")
    if rec.get("verdict") in ("PASS", "FAIL"):
        if not ev:
            errs.append("PASS/FAIL 必须挂证据 —— 无证据=假结论,拒")
        elif not any(e.get("type") in OBSERVED for e in ev):
            errs.append(
                "证据只有源码/规格(定位用),缺[部署实测]观测类(api/db/dom/log/screenshot)—— D1,拒")
        for e in ev:
            if (e.get("claimed") is not None and e.get("observed") is not None
                    and e["claimed"] > e["observed"]):
                errs.append(f"覆盖越证据:声称 {e['claimed']} > 实测 {e['observed']} —— 拒")
        ui_items = [e for e in ev if e.get("type") in UI_OBSERVED]
        has_deployed_ui = False
        for e in ui_items:
            surface = e.get("surface")
            if not surface:
                missing_ui_surface = True
                continue
            if surface not in UI_SURFACES:
                errs.append(
                    f"ui_surface 取值 {json.dumps(surface, ensure_ascii=False)} 非法 "
                    f"—— 限 {'|'.join(UI_SURFACES)}")
                continue
            if surface == "deployed":
                ci = e.get("code_identity") or {}
                if not ci.get("origin_commit") or ci.get("origin_reachable") is not True:
                    errs.append(
                        "证据称 surface=deployed 却无 origin 可达 commit 锚"
                        "(需 code_identity.origin_commit + origin_reachable=true)"
                        "—— 部署身份未证,拒")
                else:
                    has_deployed_ui = True
        if ui_items and (not has_deployed_ui or missing_ui_surface):
            need_deploy_anchor = True
    if errs:
        return {"ok": False, "recorded": None, "reason": errs}
    v = rec["verdict"]
    note = rec.get("note") or ""
    if rec.get("high_risk") and v == "PASS" and not rec.get("f9_passed"):
        v = "PENDING:needs-f9"
        note = "高风险结论自动等独立复判;" + note
    if need_deploy_anchor:
        if missing_ui_surface:
            v = "PENDING:needs-ui-surface"
            note = ("UI 观测未声明 ui_surface(deployed|local_build),来源面不可考,封顶假设;"
                    "补 conclusion.ui_surface 后重投影;") + note
        else:
            v = "PENDING:needs-deploy-anchor"
            note = "UI 观测仅本地构建/未绑定 origin 可达 commit,封顶假设,须部署指纹锚定才升级;" + note
    line = {"id": rec["id"], "claim": rec.get("claim"), "verdict": v, "evidence": ev,
            "high_risk": bool(rec.get("high_risk")), "note": note,
            "ts": int(time.time() * 1000)}
    return {"ok": True, "recorded": line, "reason": None}


# ── 投影器：bundle → verdicts（原件 bundle_to_verdicts.js）─────────────────


def _find_bundles(d: str, depth: int, acc: list[str]) -> list[str]:
    if depth > MAX_DEPTH:
        return acc
    try:
        entries = os.listdir(d)
    except OSError:
        return acc
    for name in entries:
        full = os.path.join(d, name)
        if os.path.isdir(full):
            _find_bundles(full, depth + 1, acc)
        elif name == BUNDLE_NAME:
            acc.append(full)
    return acc


def _to_records(bundle_file: str, run_dir: str) -> dict[str, Any]:
    errs: list[str] = []
    try:
        with open(bundle_file, encoding="utf-8") as fh:
            b = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"recs": [], "errs": [f"解析失败: {exc}"]}
    if b.get("schema_version") != "1.0":
        errs.append('schema_version 必须是 "1.0"(闸1 同口径)')
    if not isinstance(b.get("conclusions"), list) or not b["conclusions"]:
        errs.append("conclusions 必须是非空数组")
    if errs:
        return {"recs": [], "errs": errs}
    rel = os.path.relpath(os.path.dirname(bundle_file), run_dir).replace("\\", "/")
    surface = rel if rel and rel != "." else ""
    recs = []
    for c in b["conclusions"]:
        cid = (surface + "/" if surface else "") + str(c.get("id") or "(无id)")
        verdict = _MAP.get(c.get("verdict"))
        if not verdict:
            errs.append(
                f"[{cid}] verdict 必须是 pass/fail/undecidable"
                f"(实为 {json.dumps(c.get('verdict'), ensure_ascii=False)})")
            continue
        paths = c.get("evidence") if isinstance(c.get("evidence"), list) else []
        etypes = c.get("evidence_types") if isinstance(c.get("evidence_types"), list) else []
        bad_types = sorted({str(t) for t in etypes if not is_known_evidence_type(t)})
        if bad_types:
            errs.append(
                f"[{cid}] evidence_types 含词表外类别 {json.dumps(bad_types, ensure_ascii=False)}"
                f" —— 观测类限 {'/'.join(OBSERVED)};定位类限 {'/'.join(LOCATOR)}。"
                "UI 观测请用 dom/screenshot。")
            continue
        if c.get("ui_surface") is not None and c["ui_surface"] not in UI_SURFACES:
            errs.append(
                f"[{cid}] ui_surface 取值 {json.dumps(c.get('ui_surface'), ensure_ascii=False)}"
                f" 非法 —— 限 {'|'.join(UI_SURFACES)}")
            continue
        declared = bool(etypes) and len(etypes) == len(paths)
        note = ""
        if declared:
            evidence = []
            for i, p in enumerate(paths):
                item: dict[str, Any] = {"type": etypes[i], "ref": p}
                if item["type"] in UI_OBSERVED:
                    if c.get("ui_surface"):
                        item["surface"] = c["ui_surface"]
                    if c.get("code_identity"):
                        item["code_identity"] = c["code_identity"]
                evidence.append(item)
            if (isinstance(c.get("claimed"), (int, float))
                    and isinstance(c.get("observed"), (int, float)) and evidence):
                evidence[0]["claimed"] = c["claimed"]
                evidence[0]["observed"] = c["observed"]
        else:
            evidence = [{"type": "file", "ref": p} for p in paths]
            if verdict in ("PASS", "FAIL"):
                note = ("needs-evidence-type: bundle 未声明 evidence_types,"
                        f"闸2 无法认证证据类别(不猜),原判 {verdict} 封顶;"
                        "补 conclusion.evidence_types(与 evidence 等长同序)后重投影。")
                verdict = "PENDING"
        recs.append({
            "rec": {"id": cid, "claim": c.get("claim"), "verdict": verdict,
                    "evidence": evidence, "high_risk": bool(c.get("high_risk")),
                    "f9_passed": bool(c.get("f9_passed")), "note": note},
            "src": os.path.relpath(bundle_file, run_dir).replace("\\", "/"),
            "bundle_verdict": c.get("verdict"),
            "bundle_strength": c.get("strength"),
        })
    return {"recs": recs, "errs": errs}


def project(run_dir: str) -> dict[str, Any]:
    """投影一个 run 目录：{lines, rejected, errs, bundles, no_ledger}。"""
    bundles = _find_bundles(run_dir, 0, [])
    errs: list[str] = []
    rejected: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for bf in bundles:
        r = _to_records(bf, run_dir)
        rel = os.path.relpath(bf, run_dir).replace("\\", "/")
        errs.extend(rel + ": " + e for e in r["errs"])
        items.extend(r["recs"])
    seen: set[str] = set()
    for it in items:
        if it["rec"]["id"] in seen:
            errs.append("结论 id 重复(投影后仍冲突): " + it["rec"]["id"])
            continue
        seen.add(it["rec"]["id"])
    lines: list[dict[str, Any]] = []
    for it in items:
        r = record_verdict(it["rec"])
        if not r["ok"]:
            rejected.append({"id": it["rec"]["id"], "src": it["src"],
                             "reason": r["reason"]})
            continue
        rec = dict(r["recorded"])
        rec["projected_from"] = it["src"]
        rec["bundle_verdict"] = it["bundle_verdict"]
        rec["bundle_strength"] = it["bundle_strength"]
        lines.append(rec)
    return {"lines": lines, "rejected": rejected, "errs": errs,
            "bundles": bundles, "no_ledger": not bundles}


def project_and_write(run_dir: str) -> dict[str, Any]:
    """投影并落盘（派生视图，确定性重写，禁手补）。

    退出语义对齐原件：no_ledger 时不写任何文件（空 verdicts.jsonl 会被下游读成
    「投影过了」）；有结构错 code=2；有被拒 code=3；全落账 code=0；无账本 code=4。
    """
    r = project(run_dir)
    if r["no_ledger"]:
        r["code"] = 4
        return r
    out_file = os.path.join(run_dir, "verdicts.jsonl")
    with open(out_file, "w", encoding="utf-8") as fh:
        for l in r["lines"]:
            fh.write(json.dumps(l, ensure_ascii=False) + "\n")
    if r["rejected"]:
        with open(os.path.join(run_dir, "verdicts.rejected.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(r["rejected"], fh, ensure_ascii=False, indent=2)
    r["code"] = 2 if r["errs"] else (3 if r["rejected"] else 0)
    r["out_file"] = out_file
    return r
