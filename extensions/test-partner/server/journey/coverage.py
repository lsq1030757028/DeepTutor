# -*- coding: utf-8 -*-
"""coverage — 原子工具 9：覆盖收口。

输入：verdicts + test_analysis → 输出 `coverage_ledger`（覆盖图数据）。
牙（挂产物）：gap 无解释不 done——每条规则要么有用例（附 verdict），要么有
带「谁覆盖+后果」的不覆盖声明；两者皆无 = ledger 判 not_done，产物照落
（覆盖图必须能显示缺口）但 ok=False。
"""
from __future__ import annotations

from typing import Any

from server.journey import artifacts
from server.journey.gates import capability_ladder
from server.journey.project_verdicts import read_verdicts


def build_coverage(batch_id: str, run_id: str = "") -> dict[str, Any]:
    analysis = artifacts.load_artifact(batch_id, "test_analysis")
    frame = artifacts.load_artifact(batch_id, "business_frame")
    rules = {r["rule_id"]: r for r in frame["rules"]}

    caseset = None
    if artifacts.has_artifact(batch_id, "approved_caseset"):
        caseset = artifacts.load_artifact(batch_id, "approved_caseset")
    draft = None
    if artifacts.has_artifact(batch_id, "case_draft"):
        draft = artifacts.load_artifact(batch_id, "case_draft")
    uncovered_decl = {d.get("rule_id"): d.get("reason", "")
                      for d in (draft or {}).get("uncovered_rules", [])}

    batch = artifacts.load_batch(batch_id)
    run_id = run_id or (batch.get("run_ids") or [""])[-1]
    verdicts = {v["id"]: v for v in (read_verdicts(run_id) if run_id else [])}

    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for rid, rule in rules.items():
        cases = []
        for c in (caseset or {}).get("cases", []):
            if (c.get("source_anchor") or {}).get("rule_id") == rid:
                v = verdicts.get(c["case_id"])
                cases.append({
                    "case_id": c["case_id"],
                    "title": c.get("title", ""),
                    "probing": bool((c.get("source_anchor") or {}).get("probing")),
                    "verdict": (v or {}).get("verdict", "NOT_EXECUTED"),
                })
        if cases:
            status = "covered"
            gap_reason = ""
        elif rid in uncovered_decl and uncovered_decl[rid].strip():
            status = "declared_uncovered"
            gap_reason = uncovered_decl[rid]
        else:
            status = "gap_unexplained"
            gap_reason = ""
            problems.append(f"{rid} 无用例且无不覆盖声明——gap 无解释不 done")
        rows.append({
            "rule_id": rid,
            "statement": rule.get("statement", ""),
            "probing_rule": bool(rule.get("probing")),
            "status": status,
            "gap_reason": gap_reason,
            "cases": cases,
        })

    # 能力锁（设计稿 §6.1）：没授权的档位要在覆盖图上**有名有姓地缺席**，
    # 而不是与"忘了测"混成同一个空格。声明授予却拿不出机检证据的，按未授予处理
    # 并单列成 problem——那是唯一会骗到下游的形态（下游会以为守恒断言真跑过了）。
    profile = (artifacts.load_artifact(batch_id, "intake_profile")
               if artifacts.has_artifact(batch_id, "intake_profile") else {})
    ladder = capability_ladder.summary(profile)
    problems += ladder["problems"]

    n_pass = sum(1 for r in rows for c in r["cases"] if c["verdict"] == "PASS")
    n_official = sum(1 for r in rows for c in r["cases"]
                     if c["verdict"] in ("PASS", "FAIL"))
    ledger_payload = {
        "run_id": run_id,
        "rules": rows,
        "capability_ladder": ladder,
        "summary": {
            "total_rules": len(rows),
            "covered": sum(1 for r in rows if r["status"] == "covered"),
            "declared_uncovered": sum(1 for r in rows
                                      if r["status"] == "declared_uncovered"),
            "gap_unexplained": sum(1 for r in rows
                                   if r["status"] == "gap_unexplained"),
            "official_verdicts": n_official,
            "pass": n_pass,
        },
        "done": not problems,
        "problems": problems,
    }
    art = artifacts.save_artifact(batch_id, "coverage_ledger", ledger_payload)
    return {"ok": not problems, "coverage_ledger": art, "problems": problems}
