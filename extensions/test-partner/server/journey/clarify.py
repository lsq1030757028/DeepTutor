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

#: 澄清项状态：answered=用户真答了；assumed=按推荐处理（含显式 skip，用
#: `skipped=true` 区分「用户看过并跳过」与「用户没答」——两者在账本上必须可分）。
CLARIFICATION_STATUSES = ("answered", "assumed")


def _check_clarifications(
    items: list[dict[str, Any]],
    prior_ledger: dict[str, Any],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """结构化澄清项校验 + 疑点账本（AC10/AC11/AC13 的服务端半边）。

    ## 为什么这里要有一个账本

    「同一疑点同批次不二次弹出」（AC11）与「skip 后不再发问」（AC13）的判断
    发生在模型侧——服务端看不见 ask_user。但模型要守这两条，前提是**有地方
    查得到哪些疑点已经问过、谁被跳过了**。business_frame 里的 question_ledger
    就是那个地方：机械派生、可数、重新澄清时不允许静默丢项。

    没有这个钩子，纪律只活在提示词里——提示词丢了它就丢了（批次 1 的 B3 刚刚
    示范过一遍）。
    """
    problems: list[str] = []
    ledger: dict[str, dict[str, Any]] = {}
    for i, c in enumerate(items or []):
        if not isinstance(c, dict):
            problems.append(f"clarifications[{i}] 必须是对象")
            continue
        qid = c.get("question_id")
        if not isinstance(qid, str) or not qid.strip():
            problems.append(f"clarifications[{i}] 缺 question_id——"
                            "疑点没有 id 就没法保证同批次不问第二次")
            continue
        qid = qid.strip()
        if qid in ledger:
            problems.append(f"question_id 重复：{qid}（同一疑点一卡内只出现一次）")
            continue
        status = c.get("status")
        if status not in CLARIFICATION_STATUSES:
            problems.append(f"{qid} status 必须是 {CLARIFICATION_STATUSES} 之一，"
                            f"实为 {status!r}")
            continue
        if not isinstance(c.get("question"), str) or not c["question"].strip():
            problems.append(f"{qid} 缺 question（疑点原文）")
        recommended = c.get("recommended")
        if not isinstance(recommended, str) or not recommended.strip():
            problems.append(f"{qid} 缺 recommended——攒批卡每条必须带推荐答案（AC10），"
                            "没有推荐就没有「未答按推荐处理」这条路")
        skipped = bool(c.get("skipped"))
        answer = c.get("answer")
        if status == "answered":
            if skipped:
                problems.append(f"{qid} answered 与 skipped 互斥——"
                                "用户答了就不是跳过")
            if not isinstance(answer, str) or not answer.strip():
                problems.append(f"{qid} status=answered 必须带用户真实答案")
        else:  # assumed
            if isinstance(answer, str) and answer.strip()                     and answer.strip() != str(recommended or "").strip():
                problems.append(
                    f"{qid} status=assumed 的生效答案必须等于推荐值——"
                    f"assumed 的含义就是「按推荐处理」，答案另写一个值是自相矛盾")
        ledger[qid] = {"status": status, "skipped": skipped}
    # 重新澄清时不许把已处理过的疑点静默丢掉：丢了 = 账本失忆 = 同一疑点
    # 可以再问一遍而没人发现（F-3「三轮同题」的复发通道）。
    for qid in prior_ledger:
        if qid not in ledger:
            problems.append(
                f"疑点 {qid} 在上一版澄清里已处理（{prior_ledger[qid]}），"
                "这次提交把它丢了。已处理的疑点必须显式带上（原状态原样保留即可），"
                "不允许静默消失。")
    return problems, ledger


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
    prior_ledger: dict[str, Any] = {}
    if artifacts.has_artifact(batch_id, "business_frame"):
        prior = artifacts.load_artifact(batch_id, "business_frame")
        prior_ledger = dict(prior.get("question_ledger") or {})
    c_problems, question_ledger = _check_clarifications(
        list(clarifications or []), prior_ledger)
    problems.extend(c_problems)
    gate = sot_gate.check(confirmed_facts_md or "")
    if gate["verdict"] != "PASS":
        problems.extend("sot_gate: " + p for p in gate["problems"])
    if problems:
        return {"ok": False, "problems": problems, "sot_gate": gate}
    frame = artifacts.save_artifact(batch_id, "business_frame", {
        "rules": rules,
        "clarifications": clarifications or [],
        # 机械派生的疑点账本：模型问之前查它（AC11 去重、AC13 skip 不再问），
        # 分析产物的 assumed 标记从它投影（AC10）。
        "question_ledger": question_ledger,
        "confirmed_facts_md": confirmed_facts_md,
        "sot_gate": {"verdict": gate["verdict"], "columns": gate["columns"]},
    })
    # 物化视图：cases_gate/工作台读文件形态
    with open(os.path.join(artifacts.batch_dir(batch_id), "confirmed_facts.md"),
              "w", encoding="utf-8") as fh:
        fh.write(confirmed_facts_md)
    return {"ok": True, "business_frame": frame, "sot_gate": gate}
