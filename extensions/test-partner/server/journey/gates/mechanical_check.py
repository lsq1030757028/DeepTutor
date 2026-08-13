# -*- coding: utf-8 -*-
"""mechanical_check — 收口机械判据闸（移植件，M1 范围：M1/M3/M5 + M4 降级）。

原件：test_agent/kit/f9/mechanical_check.js（只读采石场）。
对已落盘的最终账本重新机械体检（全部只看磁盘事实，零模型）：

  M1 evidence-resolvable  每条证据 ref 在 run 内可引开且非空
  M3 scope-anchor         PASS/FAIL 的 claim 含全称量词时必须带 claimed/observed
                          广度锚且 claimed<=observed
  M4 instance-fingerprint 【M1 降级版】bundle 须声明非空 build_fingerprint
                          （黑盒靶无 origin commit，降级为「被测实例指纹在场」，
                          内容 = 靶机身份串，如 banner+地址+二进制哈希）
  M5 projection-integrity verdicts.jsonl 每条须带 projected_from 且 claim 与
                          bundle 原文逐字一致（= 账本是投影派生，不是手写/事后改过）

未蒸馏声明（护栏 3）：原件 M2（claim 自称等级 vs 证据类别复检）本移植不含——
其 D1 判据已由 verdict_projection 在落账关口执行；后果 = 「落账后 bundle 被改出
定位类证据」这一窄面在收口处只由 M5(claim 一致性) 部分兜住，完整 M2 收口复检留 M2 期。

词表 import 自 verdict_projection（单点定义，BB-297 防漂——本文件禁再写一份）。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from server.journey import artifacts
from server.journey.gates.verdict_projection import BUNDLE_NAME, OBSERVED  # noqa: F401  词表单点

UNIVERSAL = re.compile(r"(全部|所有|任何|一律|必然|全量|每一?台|无一例外)")
MAX_DEPTH = 3
_SKIP = {"node_modules", ".git", "__pycache__"}


def _read_jsonl(f: str) -> list[dict[str, Any]]:
    try:
        with open(f, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh.read().splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


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
            if name not in _SKIP:
                _find_bundles(full, depth + 1, acc)
        elif name == BUNDLE_NAME:
            acc.append(full)
    return acc


def _resolve_evidence(run_dir: str, rec: dict[str, Any], ref: str) -> dict[str, Any]:
    cands = [os.path.abspath(os.path.join(run_dir, ref))]
    if rec.get("projected_from"):
        cands.append(os.path.abspath(os.path.join(
            run_dir, os.path.dirname(rec["projected_from"]), ref)))
    for p in cands:
        if os.path.isfile(p):
            if os.path.getsize(p) > 0:
                return {"ok": True, "at": p}
            return {"ok": False, "at": p, "why": "文件存在但为空"}
    return {"ok": False, "at": cands[0], "why": "在 run 内引不开"}


def check_run(run_dir: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "tool": "journey/gates/mechanical_check.py", "version": 1,
        "scope": "M1/M3/M5 + M4-degraded(instance-fingerprint)",
        "run_dir": run_dir,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "checked": [], "run_level": [], "blocking": [], "advisory": [],
    }
    verdicts_path = os.path.join(run_dir, "verdicts.jsonl")
    if not os.path.exists(verdicts_path):
        out["blocking"].append("[run] verdicts.jsonl 不存在 —— 没有结论账本可体检(fail-closed)")
        out["verdict"] = "BLOCK"
        out["blocking_count"] = 1
        return out
    recs = _read_jsonl(verdicts_path)
    if not recs:
        out["blocking"].append("[run] verdicts.jsonl 为空 —— 无结论可收口")

    # M4（降级版）：实例指纹在场
    bundles = _find_bundles(run_dir, 0, [])
    bundle_index: dict[str, dict[str, Any]] = {}
    if not bundles:
        out["blocking"].append("[M4] run 内无 evidence-bundle.json —— 账本 SoT 缺失,实例身份无从核")
    for bf in bundles:
        rel = os.path.relpath(bf, run_dir).replace("\\", "/")
        try:
            with open(bf, encoding="utf-8") as fh:
                b = json.load(fh)
        except (OSError, ValueError) as exc:
            out["blocking"].append(f"[M4] {rel} 解析失败: {exc}")
            continue
        fp = b.get("build_fingerprint")
        if not isinstance(fp, str) or not fp.strip():
            out["blocking"].append(
                f"[M4] {rel} 未声明非空 build_fingerprint —— 被测实例身份未锚"
                "(M1 降级口径:黑盒靶填实例指纹,如 banner+地址+二进制哈希)")
        else:
            out["run_level"].append({"check": "M4", "bundle": rel, "build_fingerprint": fp})
        surface = os.path.dirname(rel)
        surface = "" if surface in (".", "") else surface.replace("\\", "/")
        for c in b.get("conclusions") or []:
            bundle_index[(surface + "/" if surface else "") + str(c.get("id"))] = {
                "claim": c.get("claim"), "bundle": rel}

    for r in recs:
        item: dict[str, Any] = {"id": r.get("id"), "verdict": r.get("verdict"),
                                "checks": {}, "blocking": [], "advisory": []}
        ev = r.get("evidence") or []
        official = r.get("verdict") in ("PASS", "FAIL")

        # M1
        unresolved = []
        for e in ev:
            res = _resolve_evidence(run_dir, r, e.get("ref") or "")
            if not res["ok"]:
                unresolved.append((e.get("ref") or "(空 ref)") + " (" + res["why"] + ")")
        item["checks"]["M1"] = "FAIL" if unresolved else ("PASS" if ev else "N/A")
        if unresolved:
            item["blocking"].append("[M1] 证据引不开: " + "; ".join(unresolved))

        # M3
        if official and UNIVERSAL.search(r.get("claim") or ""):
            anchored = [e for e in ev
                        if isinstance(e.get("claimed"), (int, float))
                        and isinstance(e.get("observed"), (int, float))]
            if not anchored:
                item["blocking"].append(
                    "[M3] claim 含全称量词却无 claimed/observed 广度锚 —— 声称广度无实测背书")
            elif any(e["claimed"] > e["observed"] for e in anchored):
                item["blocking"].append("[M3] 覆盖越证据: claimed>observed")
            item["checks"]["M3"] = ("FAIL" if any(b.startswith("[M3]")
                                                  for b in item["blocking"]) else "PASS")
        else:
            item["checks"]["M3"] = "N/A"

        # M5
        if not r.get("projected_from"):
            item["blocking"].append(
                "[M5] 缺 projected_from —— 无法证明本条是由 bundle 机械投影而来(疑手写)")
            item["checks"]["M5"] = "FAIL"
        else:
            src = bundle_index.get(str(r.get("id")))
            if not src:
                item["blocking"].append(f"[M5] bundle 内找不到同 id 结论: {r.get('id')}")
                item["checks"]["M5"] = "FAIL"
            elif (src["claim"] or "") != (r.get("claim") or ""):
                item["blocking"].append("[M5] claim 与 bundle 原文不一致 —— 账本落盘后被改过")
                item["checks"]["M5"] = "FAIL"
            else:
                item["checks"]["M5"] = "PASS"

        item["not_mechanically_verifiable"] = (
            ["claim 的语义是否被证据内容真正支撑(需读证据文件内容并理解业务口径)"]
            if official else [])
        out["checked"].append(item)
        for b in item["blocking"]:
            out["blocking"].append(str(r.get("id")) + " " + b)

    out["official_count"] = sum(1 for c in out["checked"]
                                if c["verdict"] in ("PASS", "FAIL"))
    out["blocking_count"] = len(out["blocking"])
    out["verdict"] = "BLOCK" if out["blocking"] else "PASS"
    out["coverage_note"] = (
        "本闸只判机械可判项(M1/M3/M5+M4降级)。每条官方结论的语义支撑度未在此复核;"
        "M1 不设判官,LLM 复核只作信号不作闸(护栏 4)。")
    return out


def check_and_write(run_dir: str, out_path: str | None = None) -> dict[str, Any]:
    res = check_run(run_dir)
    path = out_path or os.path.join(run_dir, "f9_mechanical.json")
    artifacts.atomic_write_json(path, res, indent=1)
    res["out_path"] = path
    return res
