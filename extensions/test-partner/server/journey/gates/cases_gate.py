# -*- coding: utf-8 -*-
"""cases_gate — 交付形态闸（移植件）：走过设计就必须交出用例集。

原件：test_agent/loop/cases_gate.js（只读采石场；抄语义与测试向量，不抄依赖）。
三族判据：
- 在场族 presence：有 test_analysis.md 就必须有非空用例集（cases.md / cases_exec.json）；
- 可消费族 consumable：只交 markdown 必须同落机器可读 cases_index.json；
- 分层族 layering：每条用例预期须标「业务:」层或显式「探测:」（只查格式不判语义）。

M1 用法：adopt 工具落 ApprovedCaseSet 后对批次目录的物化视图跑本闸。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

ANALYSIS = "test_analysis.md"
CASES_FILES = ["cases_exec.json", "cases.md"]
CASES_INDEX = "cases_index.json"
SKIP_DIRS = {"evidence", "node_modules", "__pycache__", ".git"}

CASE_ID = re.compile(
    r"(?:^|[|\s(（【])((?:[A-Za-z]{1,6}[-_])+[A-Za-z0-9]*\d+[A-Za-z0-9_-]*"
    r"|用例\s*\d+|CASE\s*\d+)(?=[|\s)）】:：,，.。]|$)", re.I)
SEP_ROW = re.compile(r"^\s*\|[\s:|.\-—–]+\|?\s*$")
HEADER_HINT = re.compile(r"(用例\s*(ID|编号|号)|case\s*id)", re.I)

LAYER_BIZ = re.compile(r"(业务层?|用户可见|商家可见|业务可观测)\s*[::]|【\s*业务")
LAYER_PROBE = re.compile(r"(探测层?|探测性|无判决权|不作判决依据)\s*[::]?|【\s*探测")
EXPECT_COL = re.compile(r"预期|expect|结果", re.I)


def _list_dirs(run_dir: str) -> list[str]:
    out = [run_dir]
    try:
        subs = os.listdir(run_dir)
    except OSError:
        return out
    for d in subs:
        if d in SKIP_DIRS:
            continue
        p = os.path.join(run_dir, d)
        if os.path.isdir(p):
            out.append(p)
    return out


def count_json_cases(text: str) -> dict[str, Any]:
    try:
        j = json.loads(text)
    except ValueError:
        return {"n": 0, "why": "cases_exec.json 解析失败"}
    if isinstance(j, list):
        return {"n": len(j)}
    if isinstance(j, dict) and isinstance(j.get("cases"), list):
        return {"n": len(j["cases"])}
    return {"n": 0, "why": "cases_exec.json 里没有 cases 数组"}


def _cells(l: str) -> list[str]:
    return [c.strip() for c in l.removeprefix("|").removesuffix("|").split("|")]


def count_md_cases(text: str) -> dict[str, Any]:
    ids: set[str] = set()
    for raw in re.split(r"\r?\n", text):
        l = raw.strip()
        if not l or SEP_ROW.match(l):
            continue
        if l.startswith("|"):
            cells = _cells(l)
            if not cells:
                continue
            if HEADER_HINT.search(cells[0]) and not CASE_ID.search(cells[0]):
                continue
            m = CASE_ID.search(cells[0])
            if m:
                ids.add(m.group(1).upper())
            continue
        if re.match(r"^#{2,6}\s", l):
            m = CASE_ID.search(re.sub(r"^#{2,6}\s*", "", l))
            if m:
                ids.add(m.group(1).upper())
    return {"n": len(ids)}


def layering_md(text: str) -> dict[str, Any]:
    expect_idx = -1
    missing: list[str] = []
    total = 0
    for raw in re.split(r"\r?\n", text):
        l = raw.strip()
        if not l or SEP_ROW.match(l):
            continue
        if l.startswith("|"):
            cells = _cells(l)
            if HEADER_HINT.search(cells[0]) and not CASE_ID.search(cells[0]):
                expect_idx = next(
                    (i for i, c in enumerate(cells) if EXPECT_COL.search(c)), -1)
                continue
            m = CASE_ID.search(cells[0])
            if not m:
                continue
            total += 1
            cell = cells[expect_idx] if 0 <= expect_idx < len(cells) else l
            if not LAYER_BIZ.search(cell) and not LAYER_PROBE.search(cell):
                missing.append(m.group(1).upper())
            continue
        if re.match(r"^#{2,6}\s", l):
            expect_idx = -1
    return {"total": total, "missing": missing}


def layering_json(text: str) -> dict[str, Any]:
    try:
        j = json.loads(text)
    except ValueError:
        return {"total": 0, "missing": []}
    arr = j if isinstance(j, list) else (j.get("cases") if isinstance(j, dict) else [])
    if not isinstance(arr, list):
        arr = []
    missing: list[str] = []
    for c in arr:
        if not isinstance(c, dict):
            continue
        blob = json.dumps(
            c.get("assert") or c.get("expect") or c.get("expected") or c,
            ensure_ascii=False)
        if not LAYER_BIZ.search(blob) and not LAYER_PROBE.search(blob):
            missing.append(str(c.get("id") or "(无id)"))
    return {"total": len(arr), "missing": missing}


def check(run_dir: str) -> dict[str, Any]:
    out: dict[str, Any] = {"verdict": "PASS", "applicable": False, "surfaces": [],
                           "problems": [], "problem_kinds": []}
    for d in _list_dirs(run_dir):
        if not os.path.exists(os.path.join(d, ANALYSIS)):
            continue
        out["applicable"] = True
        rel = os.path.relpath(d, run_dir)
        rel = "." if rel == "." else rel.replace("\\", "/")
        found = [f for f in CASES_FILES if os.path.exists(os.path.join(d, f))]
        if not found:
            out["surfaces"].append({"surface": rel, "cases_artifact": None, "case_rows": 0})
            out["problems"].append(
                f"「{rel}」走过设计(有 {ANALYSIS})却没有用例集产物 —— 分析不是用例集:"
                f"{' / '.join(CASES_FILES)} 一个都没有,这趟交不出\"用例集\"这个交付形态")
            out["problem_kinds"].append("presence")
            continue
        for f in found:
            with open(os.path.join(d, f), encoding="utf-8") as fh:
                text = fh.read()
            is_json = f.endswith(".json")
            r = count_json_cases(text) if is_json else count_md_cases(text)
            lay = layering_json(text) if is_json else layering_md(text)
            out["surfaces"].append({
                "surface": rel, "cases_artifact": f, "case_rows": r["n"],
                "layered_checked": lay["total"], "layer_missing": len(lay["missing"])})
            if r["n"] <= 0:
                out["problems"].append(
                    f"「{rel}/{f}」零用例行 —— {r.get('why') or '数不出任何带用例 id 的行'};"
                    "有文件不等于有用例")
                out["problem_kinds"].append("presence")
                continue
            if not is_json and not os.path.exists(os.path.join(d, CASES_INDEX)):
                out["problems"].append(
                    f"「{rel}/{f}」有用例集但没有 {CASES_INDEX} —— "
                    "人读的 markdown 下游机器消费不了(回归圈定会收成 0 条)。")
                out["problem_kinds"].append("consumable")
            if lay["missing"]:
                shown = ",".join(lay["missing"][:8])
                more = "…" if len(lay["missing"]) > 8 else ""
                out["problems"].append(
                    f"「{rel}/{f}」{len(lay['missing'])}/{lay['total']} 条用例的预期结果"
                    f"没标判决权分层({shown}{more})"
                    " —— 每条须有「业务:」层(不看代码就能判对错),"
                    "或显式标「探测:」(实现推导的预期,无判决权)。"
                    "预期源自实现 = 拿实现验证实现,实现错时必然判通过")
                out["problem_kinds"].append("layering")
    if out["problems"]:
        out["verdict"] = "REJECT"
    return out
