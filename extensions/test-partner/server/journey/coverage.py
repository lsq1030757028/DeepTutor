# -*- coding: utf-8 -*-
"""coverage — 原子工具 9：覆盖收口。

输入：verdicts + test_analysis → 输出 `coverage_ledger`（覆盖图数据）。
牙（挂产物）：gap 无解释不 done——每条规则要么有用例（附 verdict），要么有
带「谁覆盖+后果」的不覆盖声明；两者皆无 = ledger 判 not_done，产物照落
（覆盖图必须能显示缺口）但 ok=False。
"""
from __future__ import annotations

import json
import os
from typing import Any

from server.journey import artifacts
from server.journey.digest import sha256_digest
from server.journey.gates import capability_ladder, entity_scope
from server.journey.project_verdicts import read_verdicts


def build_coverage(batch_id: str, run_id: str = "") -> dict[str, Any]:
    caseset = None
    if artifacts.has_artifact(batch_id, "approved_caseset"):
        caseset = artifacts.load_artifact(batch_id, "approved_caseset")
    batch = artifacts.load_batch(batch_id)
    run_id = run_id or (batch.get("run_ids") or [""])[-1]
    receipt: dict[str, Any] = {}
    if run_id:
        if run_id not in (batch.get("run_ids") or []):
            return {"ok": False, "code": "E_RUN_BATCH_MISMATCH",
                    "problems": ["run_id 不属于当前批次，覆盖账本未写入"]}
        try:
            with open(os.path.join(artifacts.run_dir(run_id), "receipt.json"),
                      encoding="utf-8") as fh:
                receipt = json.load(fh)
        except (OSError, ValueError) as exc:
            return {"ok": False, "code": "E_RUN_RECEIPT_INVALID",
                    "problems": [f"run receipt 不可读，覆盖账本未写入：{exc}"]}
        expected_owner = artifacts.safe_owner(
            batch.get("partition") or batch.get("owner"))
        expected_caseset_id = str((caseset or {}).get("caseset_id") or "")
        expected_caseset_sha = sha256_digest(caseset) if caseset else ""
        mismatches = []
        if str(receipt.get("batch_id") or "") != batch_id:
            mismatches.append("batch_id")
        if str(receipt.get("owner_partition") or "") != expected_owner:
            mismatches.append("owner_partition")
        if str(receipt.get("caseset_id") or "") != expected_caseset_id:
            mismatches.append("caseset_id")
        if str(receipt.get("caseset_sha256") or "") != expected_caseset_sha:
            mismatches.append("caseset_sha256")
        if mismatches:
            return {"ok": False, "code": "E_RUN_STATE_MISMATCH",
                    "problems": [
                        "run 与当前批次/采纳集不一致，覆盖账本未写入："
                        + ",".join(mismatches)]}

    analysis = artifacts.load_artifact(batch_id, "test_analysis")
    frame = artifacts.load_artifact(batch_id, "business_frame")
    rules = {r["rule_id"]: r for r in frame["rules"]}

    draft = None
    if artifacts.has_artifact(batch_id, "case_draft"):
        draft = artifacts.load_artifact(batch_id, "case_draft")
    uncovered_decl = {d.get("rule_id"): d.get("reason", "")
                      for d in (draft or {}).get("uncovered_rules", [])}

    verdicts = {v["id"]: v for v in (read_verdicts(run_id) if run_id else [])}

    # 实体溯源（0030）：需求所指实体 vs 本轮实际写入实体。
    # 不符的用例**不算覆盖** —— 它证明的是另一个对象上的事实。
    profile_early = (artifacts.load_artifact(batch_id, "intake_profile")
                     if artifacts.has_artifact(batch_id, "intake_profile") else {})
    req_entity = str(profile_early.get("requirement_entity") or "")
    entity_report = entity_scope.check_caseset(caseset or {}, req_entity)
    entity_bad = {r["case_id"]: r for r in entity_report["rows"]
                  if r["verdict"] in (entity_scope.MISMATCH, entity_scope.UNKNOWN)}

    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for rid, rule in rules.items():
        cases = []
        off_scope = []
        for c in (caseset or {}).get("cases", []):
            if (c.get("source_anchor") or {}).get("rule_id") == rid:
                v = verdicts.get(c["case_id"])
                row = {
                    "case_id": c["case_id"],
                    "title": c.get("title", ""),
                    "probing": bool((c.get("source_anchor") or {}).get("probing")),
                    "verdict": (v or {}).get("verdict", "NOT_EXECUTED"),
                }
                if c["case_id"] in entity_bad:
                    row["entity_scope"] = entity_bad[c["case_id"]]
                    off_scope.append(row)
                else:
                    cases.append(row)
        if off_scope:
            # 有用例，但其中有用例写的是**另一个实体** —— 这条规则的覆盖集被污染了。
            #
            # **实体不符优先于"有用例"**（0030）：只要有一条本打算覆盖它的用例
            # 打错了对象，这条规则就不许判 covered，哪怕另有几条只读用例还在。
            # 理由是那几条只读用例证明的是**别的断面**（不变量、引用完整性），
            # 不是这条规则要的那件事（写进去再读回来）——把它们当覆盖，
            # 等于用"顺手验到的东西"顶替"要验的东西"。
            #
            # 判据方向固定为偏少：宁可把一条其实覆盖到的规则标成缺口（代价=多测一轮），
            # 也不要把一条打错对象的规则标成 covered（代价=整轮结论指错东西）。
            status = "declared_uncovered"
            bad = off_scope[0].get("entity_scope") or {}
            gap_reason = entity_scope.gap_reason(
                req_entity, bad.get("actual_entities") or [])
            if cases:
                gap_reason += ("；本规则另有 %d 条只读用例通过（%s），但它们验的是"
                               "不变量/引用完整性，不是本规则要的写入-回读闭环，"
                               "不计作覆盖" % (len(cases), ",".join(
                                   c["case_id"].split("/")[-1] for c in cases)))
        elif cases:
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
            # 打错对象的用例单列，不混进 cases —— 混进去等于把它算成了覆盖
            "off_scope_cases": off_scope,
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
    decision_cases = [c for r in rows for c in r["cases"] if not c["probing"]]
    pending_cases = [c["case_id"] for c in decision_cases
                     if c["verdict"] not in ("PASS", "FAIL")]
    failed_cases = [c["case_id"] for c in decision_cases if c["verdict"] == "FAIL"]
    security_ok = receipt.get("credential_scan_ok") is True
    official_complete = bool(decision_cases) and not pending_cases
    if not security_ok:
        business_status = "BLOCKED"
        business_reason = "凭据零落盘扫描未通过或缺失"
    elif not official_complete:
        business_status = "PENDING"
        business_reason = "仍有决策用例没有正式 PASS/FAIL 结论"
    elif failed_cases:
        business_status = "FAIL"
        business_reason = "存在正式 FAIL 结论"
    else:
        business_status = "PASS"
        business_reason = "全部决策用例已有正式 PASS，且凭据扫描通过"
    ledger_payload = {
        "run_id": run_id,
        "rules": rows,
        "capability_ladder": ladder,
        # 0030：覆盖图必须显式说明本轮有没有触达需求所指的真实路径
        "entity_scope": {
            "requirement_entity": req_entity,
            "ok": entity_report["ok"],
            "banner": entity_report["banner"],
            "offenders": [r["case_id"] for r in entity_report["offenders"]],
        },
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
        # `done` 只回答覆盖设计有没有无解释缺口，不能再被上层当作业务验收完成。
        # 业务是否可交付由这一块单独给出，避免“覆盖 done + 官方结论 0”的假绿表述。
        "business_result": {
            "status": business_status,
            "reason": business_reason,
            "conclusive": business_status in ("PASS", "FAIL"),
            "ready_for_acceptance": business_status == "PASS",
            "passed": business_status == "PASS",
            "decision_case_count": len(decision_cases),
            "official_complete": official_complete,
            "pending_cases": pending_cases,
            "failed_cases": failed_cases,
            "credential_scan_ok": security_ok,
        },
        "done": not problems,
        "problems": problems,
    }
    art = artifacts.save_artifact(batch_id, "coverage_ledger", ledger_payload)
    return {"ok": not problems, "coverage_ledger": art, "problems": problems}
