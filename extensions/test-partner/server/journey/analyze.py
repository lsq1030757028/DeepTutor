# -*- coding: utf-8 -*-
"""analyze — 原子工具 3：测试分析。

输入：business_frame → 输出 `test_analysis`（Example Map + 消费面盘点）。
牙（挂产物）：downstream_gate——analysis_md 里的「下游消费面盘点」节闸不过
= 产物不落盘（fail-closed）。

M1 裁剪（HANDOFF §三 阶段3）：Example Map + 消费面盘点保「同类共存」必答档 +
至少一环实证用例，其余允许合并"不覆盖声明"；feature 中间层工作台面 OUT（M2）。
"""
from __future__ import annotations

import os
from typing import Any

from server.journey import artifacts
from server.journey.gates import downstream_gate

#: `analysis_md` 的**可照抄最小合法骨架**（BB-688 同族，2026-08-29）。
#:
#: 2026-08-29 UAT 实测（V5）：analyze 连被拒 5 次，单轮 24 分钟。0828 已经把契约
#: 写进工具描述了，但**描述里只有散文式的规则、没有一份能照抄的东西**——盘点表
#: 要六档齐、每行要么给判别用例要么给「谁覆盖+后果」，这两条在散文里各是一句话，
#: 在骨架里是六行可复制的表格。draft 那条修好之后没再反复被拒，形态照它抄。
#:
#: 示例自身过真实 downstream_gate（`tests/test_journey_tools_design.py` 钉住）。
ANALYSIS_MD_EXAMPLE = """# test_analysis
## Example Map
| R点 | Charter |
|---|---|
| R1 | <这条规则要验什么> |
## 下游消费面盘点
| 业务对象 | 生命周期环节 | 判别用例 | 不覆盖声明(谁覆盖·后果) |
|---|---|---|---|
| <业务对象> | 获得 | d1 | — |
| 同上 | 使用 | d2 | — |
| 同上 | 同类共存 | — | 不覆盖:由并发专项负责 · 后果=并发下的错序本轮查不出,风险留档 |
| 同上 | 计量结算 | — | 不覆盖:无计费面,由需求方确认 · 后果=如有隐藏计费则漏测,风险已上浮 |
| 同上 | 撤销退回 | — | 不覆盖:无撤销面,由需求方确认 · 后果=如有撤销逻辑则漏测,风险已上浮 |
| 同上 | 记账对账 | — | 不覆盖:无台账面,由需求方确认 · 后果=如有台账则漏测,风险已上浮 |
"""

#: `example_map` 一条的可照抄形状。rule_id 必须来自 business_frame。
EXAMPLE_MAP_EXAMPLE = [{
    "rule_id": "R1",
    "charter": "<这条规则要探的方向>",
    "examples": [{"id": "E1", "desc": "<一个具体判别场景>", "kind": "happy"}],
}]


def analyze(batch_id: str, *, example_map: list[dict[str, Any]],
            analysis_md: str) -> dict[str, Any]:
    """落 test_analysis。example_map 每条：{rule_id, charter, examples:[{id,desc,kind}]}
    （kind ∈ happy/boundary/negative/coexist…，只作组织标签不设闸）。"""
    frame = artifacts.load_artifact(batch_id, "business_frame")
    known_rules = {r["rule_id"] for r in frame["rules"]}
    problems: list[str] = []
    # 形状闸先行（2026-08-28 实测）：模型把 example_map 包成 {story, rules:[...]}
    # 对象时，下面的 row.get 会直接炸成 'str' object has no attribute 'get'，
    # 经 MCP 包装后呈现为 E_MCP_UNAVAILABLE——模型会把它读成**连接问题**去重试，
    # 而不是改参数形状。形状错误必须是结构化拒绝 + 正确形状示例，不是崩溃。
    if not isinstance(example_map, list):
        return {"ok": False, "problems": [
            f"example_map 必须是数组，收到 {type(example_map).__name__}。"
            "每个元素：{\"rule_id\": \"R1\", \"charter\": \"...\", "
            "\"examples\": [{\"id\": \"E1\", \"desc\": \"...\", \"kind\": \"happy\"}]}"
            "——不要包一层 {story, rules} 对象，也不要改键名。"],
            "downstream_gate": {"verdict": "REJECT", "problems": [],
                                "stats": {}, "warns": []}}
    bad_rows = [i for i, row in enumerate(example_map) if not isinstance(row, dict)]
    if bad_rows:
        return {"ok": False, "problems": [
            f"example_map[{i}] 必须是对象（{{rule_id, charter, examples}}），"
            f"收到 {type(example_map[i]).__name__}" for i in bad_rows],
            "downstream_gate": {"verdict": "REJECT", "problems": [],
                                "stats": {}, "warns": []}}
    if not example_map:
        problems.append("example_map 为空——分析产物必须逐规则给判别 Example")
    for i, row in enumerate(example_map or []):
        rid = row.get("rule_id")
        if rid not in known_rules:
            problems.append(f"example_map[{i}] 引用未知 rule_id：{rid!r}"
                            f"（business_frame 里只有 {sorted(known_rules)}）")
        if not row.get("examples"):
            problems.append(f"example_map[{i}]({rid}) 没有任何 Example")
    gate = downstream_gate.check(analysis_md or "")
    if gate["verdict"] != "PASS":
        problems.extend("downstream_gate: " + p for p in gate["problems"])
    if problems:
        return {"ok": False, "problems": problems, "downstream_gate": gate}
    # AC10 的「分析产物含 assumed 标记且可数」：从 business_frame 的疑点账本
    # 机械投影，不吃模型自报——模型说"我标了 assumed"不算数，账本里有才算。
    ledger = frame.get("question_ledger") or {}
    assumed_clarifications = sorted(
        qid for qid, row in ledger.items()
        if isinstance(row, dict) and row.get("status") == "assumed")
    skipped_clarifications = sorted(
        qid for qid, row in ledger.items()
        if isinstance(row, dict) and row.get("skipped"))
    analysis = artifacts.save_artifact(batch_id, "test_analysis", {
        "example_map": example_map,
        "assumed_clarifications": assumed_clarifications,
        "skipped_clarifications": skipped_clarifications,
        "analysis_md": analysis_md,
        "downstream_gate": {"verdict": gate["verdict"], "stats": gate["stats"],
                            "warns": gate["warns"]},
    })
    with open(os.path.join(artifacts.batch_dir(batch_id), "test_analysis.md"),
              "w", encoding="utf-8") as fh:
        fh.write(analysis_md)
    return {"ok": True, "test_analysis": analysis, "downstream_gate": gate}
