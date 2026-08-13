# -*- coding: utf-8 -*-
"""evidence_gate — 结论账本闸 1（移植件，fail-closed）。

原件：.agents/agents/test-engineer/scripts/evidence_gate.py（只读采石场）。
对 evidence-bundle.json 做确定性预检：
  E_FAKE_GREEN          不可判(undecidable)却给 pass（假绿红线）
  E_BUILD_FINGERPRINT   有 pass 结论但无构建指纹
  E_STRENGTH_HARD       hard-anchor 少于两路可核验证据
  E_EVIDENCE_*          证据路径缺失/为空/越界（结论必须引得开产物文件）
状态：ready-for-judge / blocked。

M1 适配：expected_agent_id 参数化（原件锁 test-engineer，M1 运行时传 test-partner）；
判据本身一条不减。
"""
from __future__ import annotations

import json
import os
from typing import Any

VERDICTS = {"pass", "fail", "undecidable"}
STRENGTHS = {"hard-anchor", "single-anchor", "undecidable"}
BUNDLE_FIELDS = {"schema_version", "agent_id", "build_fingerprint", "conclusions"}

DEFAULT_AGENT_ID = "test-partner"


def _nonempty(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def check_bundle(bundle: Any, artifact_root: str,
                 expected_agent_id: str = DEFAULT_AGENT_ID) -> dict[str, Any]:
    errors: list[str] = []
    root = os.path.realpath(artifact_root)

    if not isinstance(bundle, dict):
        errors.append("E_BUNDLE_TYPE: bundle must be an object")
        bundle = {}
    unknown = sorted(set(bundle) - BUNDLE_FIELDS)
    if unknown:
        errors.append(f"E_UNKNOWN_FIELDS: {', '.join(unknown)}")
    if bundle.get("schema_version") != "1.0":
        errors.append("E_SCHEMA_VERSION: bundle schema_version must be 1.0")
    if bundle.get("agent_id") != expected_agent_id:
        errors.append(f"E_AGENT_ID: bundle agent_id must be {expected_agent_id}")

    conclusions = bundle.get("conclusions")
    has_pass = False
    if not isinstance(conclusions, list) or not conclusions:
        errors.append("E_CONCLUSIONS: conclusions must be a non-empty array")
        conclusions = []
    ids: list[str] = []
    for i, c in enumerate(conclusions):
        if not isinstance(c, dict):
            errors.append(f"E_CONCLUSION_TYPE[{i}]: must be an object")
            continue
        cid = c.get("id")
        if _nonempty(cid):
            ids.append(cid)
        else:
            errors.append(f"E_CONCLUSION_ID[{i}]: non-empty id required")
        if not _nonempty(c.get("claim")):
            errors.append(f"E_CLAIM[{i}]: non-empty claim required")
        verdict = c.get("verdict")
        strength = c.get("strength")
        if verdict not in VERDICTS:
            errors.append(f"E_VERDICT[{i}]: verdict must be pass/fail/undecidable")
        if strength not in STRENGTHS:
            errors.append(
                f"E_STRENGTH[{i}]: strength must be hard-anchor/single-anchor/undecidable")
        if verdict == "pass":
            has_pass = True
            if strength == "undecidable":
                errors.append(
                    f"E_FAKE_GREEN[{i}]: undecidable strength cannot back a pass verdict")
        evidence = c.get("evidence") or []
        if strength in {"hard-anchor", "single-anchor"}:
            minimum = 2 if strength == "hard-anchor" else 1
            valid_paths = 0
            for raw in evidence:
                if not _nonempty(raw):
                    errors.append(f"E_EVIDENCE_PATH[{i}]: path must be non-empty")
                    continue
                cand = os.path.realpath(os.path.join(root, raw))
                if os.path.commonpath([root]) != os.path.commonpath([root, cand]):
                    errors.append(f"E_EVIDENCE_ESCAPE[{i}]: {raw}")
                    continue
                if not os.path.isfile(cand):
                    errors.append(f"E_EVIDENCE_MISSING[{i}]: {raw}")
                elif os.path.getsize(cand) == 0:
                    errors.append(f"E_EVIDENCE_EMPTY[{i}]: {raw}")
                else:
                    valid_paths += 1
            if valid_paths < minimum:
                errors.append(
                    f"E_STRENGTH_HARD[{i}]: {strength} requires >={minimum} verifiable evidence paths"
                    if strength == "hard-anchor"
                    else f"E_EVIDENCE[{i}]: single-anchor requires >=1 verifiable evidence path")
    if len(ids) != len(set(ids)):
        errors.append("E_CONCLUSION_DUPLICATE: conclusion ids must be unique")
    if has_pass and not _nonempty(bundle.get("build_fingerprint")):
        errors.append("E_BUILD_FINGERPRINT: pass conclusions require a build fingerprint")

    return {
        "gate": "evidence-ledger",
        "status": "blocked" if errors else "ready-for-judge",
        "errors": errors,
    }


def check_bundle_file(bundle_path: str, artifact_root: str,
                      expected_agent_id: str = DEFAULT_AGENT_ID) -> dict[str, Any]:
    try:
        with open(bundle_path, encoding="utf-8") as fh:
            bundle = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"gate": "evidence-ledger", "status": "blocked",
                "errors": [f"E_BUNDLE: {exc}"]}
    return check_bundle(bundle, artifact_root, expected_agent_id)
