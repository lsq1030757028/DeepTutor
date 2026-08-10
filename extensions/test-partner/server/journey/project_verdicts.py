# -*- coding: utf-8 -*-
"""project_verdicts — 原子工具 8：结论投影。

输入：evidence-bundle → 输出 `verdicts.jsonl`（派生，禁手写）+ f9_mechanical.json。
闸链（顺序即依赖）：
  闸1 evidence_gate     账本预检（假绿/构建指纹/硬锚两路/证据可引开）→ blocked 即停
  闸2 verdict_projection 唯一投影器（record 牙逐条咬）→ 结构错/被拒如实报
  闸3 mechanical_check  收口机械体检（M1/M3/M5 + M4 降级）
LLM 复核只作信号不作闸（护栏 4）——本工具零模型调用。
"""
from __future__ import annotations

import json
import os
from typing import Any

from server.journey import artifacts
from server.journey.gates import evidence_gate, mechanical_check, verdict_projection


def project(run_id: str) -> dict[str, Any]:
    run_dir = artifacts.run_dir(run_id)
    if not os.path.isdir(run_dir):
        return {"ok": False, "error": f"run 不存在:{run_id}"}
    bundle_path = os.path.join(run_dir, "evidence-bundle.json")
    gate1 = evidence_gate.check_bundle_file(bundle_path, run_dir)
    if gate1["status"] != "ready-for-judge":
        return {"ok": False, "stage": "evidence_gate", "evidence_gate": gate1}

    proj = verdict_projection.project_and_write(run_dir)
    if proj["code"] == 4:
        return {"ok": False, "stage": "projection", "error": "NO_LEDGER"}

    mech = mechanical_check.check_and_write(run_dir)
    ok = proj["code"] in (0, 3) and mech["verdict"] == "PASS"
    summary: dict[str, int] = {}
    for l in proj["lines"]:
        summary[l["verdict"]] = summary.get(l["verdict"], 0) + 1
    return {
        "ok": ok,
        "evidence_gate": gate1,
        "projection": {"code": proj["code"], "written": len(proj["lines"]),
                       "rejected": proj["rejected"], "errs": proj["errs"],
                       "distribution": summary},
        "mechanical_check": {"verdict": mech["verdict"],
                             "blocking": mech["blocking"],
                             "official_count": mech.get("official_count", 0)},
        "verdicts_file": os.path.join(run_dir, "verdicts.jsonl"),
    }


def read_verdicts(run_id: str) -> list[dict[str, Any]]:
    path = os.path.join(artifacts.run_dir(run_id), "verdicts.jsonl")
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows
