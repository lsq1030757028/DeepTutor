# -*- coding: utf-8 -*-
"""schema — ApprovedCaseSet 结构校验（工作基线：approved-caseset-schema-draft.json v0.1.0-draft）。

schema 暂未冻结（决策 0015：首条用例走通采纳链后由 manager 自决冻结）。字段级变更须记
run 目录 schema-changelog.md；digest 规则/禁反写等语义级变更禁自改（上浮 manager）。

依赖纪律：零第三方（无 jsonschema），手写校验与仓内 case_validate.py 同风格。
错误码族 S01-S99（S=schema），与 validate_cases 的 E/W 族不冲突。
"""
from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = "0.1.0-draft"

CASESET_ID_RE = re.compile(r"^acs-[0-9]{8}-[a-z0-9]{6,}$")
CASE_ID_RE = re.compile(r"^[a-z0-9-]+/R[0-9]+-C[0-9]{3}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SOURCE_KINDS = {"requirement_doc", "tapd", "pasted_text", "har_fastlane"}
ADOPTED_VIA = {"workbench_selection", "chat_confirm"}
EVIDENCE_KINDS = {"playwright_trace", "screenshot", "http_transcript",
                  "db_snapshot", "console_log"}

_CASE_REQUIRED = (
    "case_id", "case_version", "source_anchor", "preconditions", "steps",
    "expected", "failure_criteria", "assertion_layers", "automation",
    "side_effects", "open_items", "source_case_digest", "oracle_digest",
)
_CASE_ALLOWED = set(_CASE_REQUIRED) | {"title"}


def _err(errors: list[dict[str, Any]], code: str, where: str, problem: str) -> None:
    errors.append({"code": code, "where": where, "problem": problem})


def _need_str(errors, code, where, value, pattern: re.Pattern | None = None) -> bool:
    if not isinstance(value, str) or not value.strip():
        _err(errors, code, where, "必须是非空字符串")
        return False
    if pattern and not pattern.match(value):
        _err(errors, code, where, f"形态非法：{value!r}")
        return False
    return True


def _check_assertion_layer(errors, where, layer: Any) -> None:
    if not isinstance(layer, dict):
        _err(errors, "S20", where, "断言层必须是对象")
        return
    extra = set(layer) - {"assertions", "required_evidence"}
    if extra:
        _err(errors, "S21", where, f"未知字段：{sorted(extra)}")
    asserts = layer.get("assertions")
    if not isinstance(asserts, list) or not asserts or not all(
            isinstance(a, str) and a.strip() for a in asserts):
        _err(errors, "S22", where + ".assertions", "必须是非空字符串数组（无断言不算过）")
    req = layer.get("required_evidence")
    if not isinstance(req, list) or not req:
        _err(errors, "S23", where + ".required_evidence", "必须是非空数组")
    else:
        bad = [x for x in req if x not in EVIDENCE_KINDS]
        if bad:
            _err(errors, "S24", where + ".required_evidence",
                 f"证据类别不在词表 {sorted(EVIDENCE_KINDS)}：{bad}")


def validate_case(case: Any, index: int) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    where = f"cases[{index}]"
    if not isinstance(case, dict):
        _err(errors, "S10", where, "用例必须是对象")
        return errors
    cid = case.get("case_id")
    label = cid if isinstance(cid, str) and cid else where

    missing = [k for k in _CASE_REQUIRED if k not in case]
    if missing:
        _err(errors, "S11", label, f"缺必填字段：{missing}")
    extra = set(case) - _CASE_ALLOWED
    if extra:
        _err(errors, "S12", label, f"未知字段：{sorted(extra)}")

    _need_str(errors, "S13", f"{label}.case_id", cid, CASE_ID_RE)
    if not isinstance(case.get("case_version"), int) or isinstance(
            case.get("case_version"), bool) or case.get("case_version", 0) < 1:
        _err(errors, "S14", f"{label}.case_version", "必须是 >=1 的整数")

    anchor = case.get("source_anchor")
    if not isinstance(anchor, dict) or not isinstance(anchor.get("rule_id"), str) \
            or not anchor.get("rule_id", "").strip():
        _err(errors, "S15", f"{label}.source_anchor", "必须含非空 rule_id（R 规则锚）")
    elif set(anchor) - {"rule_id", "quote", "clarification_ref", "probing"}:
        _err(errors, "S15", f"{label}.source_anchor",
             f"未知字段：{sorted(set(anchor) - {'rule_id', 'quote', 'clarification_ref', 'probing'})}")

    pres = case.get("preconditions")
    if not isinstance(pres, list):
        _err(errors, "S16", f"{label}.preconditions", "必须是数组")
    else:
        for j, p in enumerate(pres):
            pwhere = f"{label}.preconditions[{j}]"
            if not isinstance(p, dict) or not isinstance(p.get("statement"), str) \
                    or not isinstance(p.get("checkable"), bool):
                _err(errors, "S16", pwhere, "必须含 statement(str) 与 checkable(bool)")
            elif set(p) - {"statement", "checkable", "check_hint"}:
                _err(errors, "S16", pwhere, "含未知字段")

    steps = case.get("steps")
    if not isinstance(steps, list) or not steps or not all(
            isinstance(s, str) and s.strip() for s in steps):
        _err(errors, "S17", f"{label}.steps", "必须是非空字符串数组（业务语义步骤）")

    _need_str(errors, "S18", f"{label}.expected", case.get("expected"))
    _need_str(errors, "S18", f"{label}.failure_criteria", case.get("failure_criteria"))

    layers = case.get("assertion_layers")
    if not isinstance(layers, dict):
        _err(errors, "S19", f"{label}.assertion_layers", "必须是对象")
    else:
        extra_layers = set(layers) - {"ui", "api", "db"}
        if extra_layers:
            _err(errors, "S19", f"{label}.assertion_layers", f"未知层：{sorted(extra_layers)}")
        if "ui" not in layers and "api" not in layers:
            _err(errors, "S25", f"{label}.assertion_layers",
                 "ui/api 至少一层在场（只给 db 不满足——anyOf 机械判）")
        for lname in ("ui", "api", "db"):
            if lname in layers:
                _check_assertion_layer(errors, f"{label}.assertion_layers.{lname}", layers[lname])

    auto = case.get("automation")
    if not isinstance(auto, dict) or not isinstance(auto.get("admissible"), bool):
        _err(errors, "S26", f"{label}.automation", "必须含 admissible(bool)")
    else:
        # recipe：工作基线扩面字段（可编译执行配方），变更记录见 run 目录 schema-changelog.md
        if set(auto) - {"admissible", "exclusion_reason", "recipe"}:
            _err(errors, "S26", f"{label}.automation", "含未知字段")
        if auto.get("admissible") is False and not (
                isinstance(auto.get("exclusion_reason"), str)
                and auto["exclusion_reason"].strip()):
            _err(errors, "S27", f"{label}.automation",
                 "admissible=false 时 exclusion_reason 必填（禁自行补业务预期）")

    se = case.get("side_effects")
    if not isinstance(se, dict) or not isinstance(se.get("writes"), bool):
        _err(errors, "S28", f"{label}.side_effects", "必须含 writes(bool)")
    else:
        allowed_se = {"writes", "write_scope", "cleanup", "authorization", "environment_ref"}
        if set(se) - allowed_se:
            _err(errors, "S28", f"{label}.side_effects", f"未知字段：{sorted(set(se) - allowed_se)}")
        if se.get("writes") is True:
            for req_field in ("cleanup", "authorization"):
                if not (isinstance(se.get(req_field), str) and se[req_field].strip()):
                    _err(errors, "S29", f"{label}.side_effects.{req_field}",
                         "写用例必填（写确认授权/清理策略）")

    oi = case.get("open_items")
    if not isinstance(oi, dict):
        _err(errors, "S30", f"{label}.open_items", "必须是对象")
    else:
        allowed_oi = {"unknowns", "assumptions", "manual_touchpoints"}
        if set(oi) - allowed_oi:
            _err(errors, "S30", f"{label}.open_items", f"未知字段：{sorted(set(oi) - allowed_oi)}")
        for k in allowed_oi & set(oi):
            if not isinstance(oi[k], list) or not all(isinstance(x, str) for x in oi[k]):
                _err(errors, "S30", f"{label}.open_items.{k}", "必须是字符串数组")

    for dfield in ("source_case_digest", "oracle_digest"):
        _need_str(errors, "S31", f"{label}.{dfield}", case.get(dfield), DIGEST_RE)
    return errors


def validate_caseset(doc: Any) -> dict[str, Any]:
    """校验整个 ApprovedCaseSet。返回 {ok, errors:[{code,where,problem}]}。"""
    errors: list[dict[str, Any]] = []
    if not isinstance(doc, dict):
        return {"ok": False, "errors": [
            {"code": "S01", "where": "$", "problem": "必须是 JSON 对象"}]}

    required = {"schema_version", "caseset_id", "created_at", "source", "approval", "cases"}
    missing = sorted(required - set(doc))
    if missing:
        _err(errors, "S02", "$", f"缺必填字段：{missing}")
    extra = sorted(set(doc) - required - {"artifact", "batch_id"})  # 信封字段放行
    if extra:
        _err(errors, "S03", "$", f"未知字段：{extra}")

    if doc.get("schema_version") != SCHEMA_VERSION:
        _err(errors, "S04", "$.schema_version",
             f"必须是 {SCHEMA_VERSION!r}（消费方显式校验），实为 {doc.get('schema_version')!r}")
    _need_str(errors, "S05", "$.caseset_id", doc.get("caseset_id"), CASESET_ID_RE)
    _need_str(errors, "S06", "$.created_at", doc.get("created_at"))

    src = doc.get("source")
    if not isinstance(src, dict):
        _err(errors, "S07", "$.source", "必须是对象")
    else:
        if set(src) - {"kind", "ref", "content_digest"}:
            _err(errors, "S07", "$.source", "含未知字段")
        if src.get("kind") not in SOURCE_KINDS:
            _err(errors, "S07", "$.source.kind", f"必须是 {sorted(SOURCE_KINDS)} 之一")
        _need_str(errors, "S07", "$.source.ref", src.get("ref"))
        if "content_digest" in src:
            _need_str(errors, "S07", "$.source.content_digest",
                      src.get("content_digest"), DIGEST_RE)

    appr = doc.get("approval")
    if not isinstance(appr, dict):
        _err(errors, "S08", "$.approval", "必须是对象")
    else:
        if set(appr) - {"adopted_at", "adopted_via", "owner"}:
            _err(errors, "S08", "$.approval", "含未知字段")
        _need_str(errors, "S08", "$.approval.adopted_at", appr.get("adopted_at"))
        if appr.get("adopted_via") not in ADOPTED_VIA:
            _err(errors, "S08", "$.approval.adopted_via",
                 f"必须是 {sorted(ADOPTED_VIA)} 之一")

    cases = doc.get("cases")
    if not isinstance(cases, list) or not cases:
        _err(errors, "S09", "$.cases", "必须是非空数组")
    else:
        seen: set[str] = set()
        for i, case in enumerate(cases):
            errors.extend(validate_case(case, i))
            cid = case.get("case_id") if isinstance(case, dict) else None
            if isinstance(cid, str):
                if cid in seen:
                    _err(errors, "S32", cid, "case_id 重复（稳定 id 永不复用）")
                seen.add(cid)

    return {"ok": not errors, "errors": errors}
