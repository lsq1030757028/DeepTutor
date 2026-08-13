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


def analyze(batch_id: str, *, example_map: list[dict[str, Any]],
            analysis_md: str) -> dict[str, Any]:
    """落 test_analysis。example_map 每条：{rule_id, charter, examples:[{id,desc,kind}]}
    （kind ∈ happy/boundary/negative/coexist…，只作组织标签不设闸）。"""
    frame = artifacts.load_artifact(batch_id, "business_frame")
    known_rules = {r["rule_id"] for r in frame["rules"]}
    problems: list[str] = []
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
    analysis = artifacts.save_artifact(batch_id, "test_analysis", {
        "example_map": example_map,
        "analysis_md": analysis_md,
        "downstream_gate": {"verdict": gate["verdict"], "stats": gate["stats"],
                            "warns": gate["warns"]},
    })
    with open(os.path.join(artifacts.batch_dir(batch_id), "test_analysis.md"),
              "w", encoding="utf-8") as fh:
        fh.write(analysis_md)
    return {"ok": True, "test_analysis": analysis, "downstream_gate": gate}
