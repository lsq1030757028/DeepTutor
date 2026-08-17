# -*- coding: utf-8 -*-
"""clarify — 原子工具 2：澄清。

输入：intake_profile + 需求正文 → 输出 `business_frame`（R 规则 + confirmed_facts）。
牙（挂产物）：sot_gate——confirmed_facts 的「被测构建」「doc×code 对齐」两栏闸不过
= 产物不落盘（fail-closed）。

澄清纪律（0015 授权补充）：歧义答复以白月官方需求文档为准；文档撑不住的预期
一律 probing=true（不进 PASS 判据）；manager 代持答复标 self-derived-pending-audit。
"""
from __future__ import annotations

import os
from typing import Any

from server.journey import artifacts
from server.journey.gates import sot_gate


def clarify(batch_id: str, *, rules: list[dict[str, Any]],
            clarifications: list[dict[str, Any]] | None = None,
            confirmed_facts_md: str) -> dict[str, Any]:
    """落 business_frame。rules 每条：{rule_id, statement, source_quote?, probing?}。"""
    artifacts.load_artifact(batch_id, "intake_profile")  # 前序产物必须在场
    problems: list[str] = []
    seen: set[str] = set()
    for i, r in enumerate(rules or []):
        rid = r.get("rule_id")
        if not isinstance(rid, str) or not rid.strip():
            problems.append(f"rules[{i}] 缺 rule_id")
            continue
        if rid in seen:
            problems.append(f"rule_id 重复：{rid}")
        seen.add(rid)
        if not isinstance(r.get("statement"), str) or not r["statement"].strip():
            problems.append(f"{rid} 缺 statement")
        if not r.get("probing") and not (r.get("source_quote") or "").strip():
            problems.append(
                f"{rid} 非探测性规则必须带 source_quote（需求原文引句或澄清记录锚）"
                "——文档撑不住的预期标 probing=true")
    if not rules:
        problems.append("rules 为空——澄清产物必须至少一条 R 规则")
    gate = sot_gate.check(confirmed_facts_md or "")
    if gate["verdict"] != "PASS":
        problems.extend("sot_gate: " + p for p in gate["problems"])
    if problems:
        return {"ok": False, "problems": problems, "sot_gate": gate}
    frame = artifacts.save_artifact(batch_id, "business_frame", {
        "rules": rules,
        "clarifications": clarifications or [],
        "confirmed_facts_md": confirmed_facts_md,
        "sot_gate": {"verdict": gate["verdict"], "columns": gate["columns"]},
    })
    # 物化视图：cases_gate/工作台读文件形态
    with open(os.path.join(artifacts.batch_dir(batch_id), "confirmed_facts.md"),
              "w", encoding="utf-8") as fh:
        fh.write(confirmed_facts_md)
    return {"ok": True, "business_frame": frame, "sot_gate": gate}
