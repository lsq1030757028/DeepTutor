# -*- coding: utf-8 -*-
"""draft_cases — 原子工具 4：用例生成（草稿层）。

输入：test_analysis → 输出 `case_draft`。
牙（挂产物）：validate_cases 覆盖族扩展——编号接在存量 E15/W05 之后
（`docs/HANDOFF.md` 明示 case_validate.py 的 E00-E15/W01-W05 是 HAR 轨格式族权威；
journey 轨用例形态不同（ApprovedCaseSet case），格式族由 schema.validate_case 承担，
本模块承担**覆盖族**并接续编号，不与存量语义重叠）：

  E16 规则覆盖缺口：example_map 的 rule 无用例覆盖且无带「谁覆盖+后果」的不覆盖声明
  E17 用例引用未知 rule_id
  E18 admissible=true 却无可编译 recipe 或 recipe 零断言（「无断言不算过」编译期前移）
  W06 探测性用例计数提示（probing 不进 PASS 判据）
  W07 某规则全部用例同一 kind（覆盖单薄提示）

recipe 契约（schema 工作基线扩面，记 run 目录 schema-changelog.md）：
  automation.recipe = {track: "ui"|"api", actions: [{op, ...}]}
  断言类 op：expect_title_contains / expect_url_contains / expect_text /
             expect_visible / expect_hidden / expect_status / expect_json_path
"""
from __future__ import annotations

import re
from typing import Any

from server.journey import artifacts, digest, schema
from server.journey.gates import track_purity as _track_purity

#: 合法 op 词表 —— **从 `gates/track_purity` 转出，不再手抄第三份**。
#:
#: 这里原本是一份独立维护的字面量集合，于是它成了同一件事的第三个真相
#: （另两个是 `pw_runtime` 的 `_op_*` 方法与 `track_purity` 的 UI/API 表）。
#: 三份清单里少了一份同步，症状是**生成侧写得出的 op 编译侧不认**，或者反过来。
#: L3 数据层 op 落地时就是这么被抓到的：runtime 与 track_purity 都补齐了，
#: 这里没补，于是一条完全合法的守恒用例在 draft 阶段被判 E18。
#:
#: 单一真相在 track_purity，那边与运行时逐个对拍（`vocabulary_gaps()`）。
_VOCAB = _track_purity.UI_OPS | _track_purity.API_OPS | _track_purity.DATA_OPS

#: 断言 op：产生一条判决的 op。约定是 `expect_` 前缀，由
#: `test_assert_ops_match_the_runtime` 与运行时 `_record_assert` 的实际调用面对拍——
#: 光靠前缀约定，一个忘了记断言的 `expect_x` 会让「无断言不算过」形同虚设。
ASSERT_OPS = frozenset(op for op in _VOCAB if op.startswith("expect_"))
ACTION_OPS = frozenset(_VOCAB)
#: BB-688：schema 契约必须进模型窗口。拒绝返回里带一条**可直接照抄**的最小合法
#: 用例——模型不用再靠被拒逐字段学格式（U6 复验实录：draft 被拒 23 轮）。
#: 这条示例自身必须过 schema.validate_case + recipe 校验
#: （test_draft_fix_example_is_itself_valid 钉住）——示例教错格式比没有示例更糟。
MINIMAL_CASE_EXAMPLE: dict[str, Any] = {
    "draft_id": "d1",
    "kind": "happy",
    "title": "示例：接口返回成功（把内容换成你的业务，形状照抄）",
    "source_anchor": {"rule_id": "R1", "probing": False},
    "preconditions": [{"statement": "测试环境可访问", "checkable": True}],
    "steps": ["调用目标接口", "检查返回"],
    "expected": "接口返回 200 且业务字段正确",
    "failure_criteria": "非 200 或业务字段缺失",
    "assertion_layers": {
        "api": {"assertions": ["响应状态码为 200"],
                "required_evidence": ["http_transcript"]},
    },
    "automation": {"admissible": True, "recipe": {
        "track": "api",
        "actions": [
            {"op": "request", "path": "/api/health", "method": "GET"},
            {"op": "expect_status", "status": 200},
        ],
    }},
    "side_effects": {"writes": False},
    "open_items": {"unknowns": [], "assumptions": [], "manual_touchpoints": []},
}

#: 不覆盖声明的最小合法形状（reason 必须同时含「谁来覆盖」与「不覆盖的后果」）。
UNCOVERED_EXAMPLE: dict[str, Any] = {
    "rule_id": "R2",
    "reason": "设备联调环境未开放，由人工在联调开放后覆盖；不覆盖则该规则缺陷会漏到线上才暴露。",
}

_UNCOVERED_OWNER = re.compile(r"(由|负责|owner|人工|开发|产品|测试|运维|用户|下一轮|后续|提测方|我)", re.I)
_UNCOVERED_CONSEQ = re.compile(
    r"(不作数|不担保|不保证|封顶|风险|导致|无法|漏|错|查不出|发现不了|留在|逃逸|无人接|才暴露)", re.I)


def _check_recipe(recipe: Any, where: str, errors: list[dict[str, Any]]) -> None:
    if not isinstance(recipe, dict) or recipe.get("track") not in ("ui", "api"):
        errors.append({"code": "E18", "where": where,
                       "problem": "recipe 必须是 {track: ui|api, actions:[...]}"})
        return
    actions = recipe.get("actions")
    if not isinstance(actions, list) or not actions:
        errors.append({"code": "E18", "where": where, "problem": "recipe.actions 为空"})
        return
    n_assert = 0
    for i, a in enumerate(actions):
        if not isinstance(a, dict) or a.get("op") not in ACTION_OPS:
            errors.append({"code": "E18", "where": f"{where}.actions[{i}]",
                           "problem": f"op 非法（合法：{sorted(ACTION_OPS)}）"})
            continue
        if a["op"] in ASSERT_OPS:
            n_assert += 1
    if n_assert == 0:
        errors.append({"code": "E18", "where": where,
                       "problem": "recipe 零断言 op——无断言不算过（红线四）编译期前移"})


def validate_draft(cases: list[dict[str, Any]],
                   example_map: list[dict[str, Any]],
                   uncovered_rules: list[dict[str, Any]] | None = None,
                   rule_probing: dict[str, bool] | None = None,
                   ) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    # 形状闸先行（与 analyze 的同族修，2026-08-28 实测）：模型把 source_anchor 写成
    # `["R1"]` 数组时，旧代码在 .get("rule_id") 上炸 'list' object has no attribute
    # 'get'，经 MCP 包装呈现为 E_MCP_UNAVAILABLE——模型将其读成连接问题去重试。
    # 形状错误必须是带正确形状示例的结构化拒绝，不是崩溃。
    if not isinstance(cases, list):
        return {"ok": False, "errors": [{
            "code": "E17", "where": "cases",
            "problem": f"cases 必须是数组，收到 {type(cases).__name__}"}],
            "warnings": [], "coverage": {}}
    bad = [i for i, c in enumerate(cases) if not isinstance(c, dict)]
    if bad:
        return {"ok": False, "errors": [{
            "code": "E17", "where": f"cases[{i}]",
            "problem": f"每个用例必须是对象，收到 {type(cases[i]).__name__}"}
            for i in bad], "warnings": [], "coverage": {}}
    known_rules = [row.get("rule_id") for row in example_map]
    seen_draft: set[str] = set()
    covered: dict[str, list[str]] = {}
    for i, c in enumerate(cases):
        did = c.get("draft_id") or f"d{i + 1}"
        if did in seen_draft:
            errors.append({"code": "E17", "where": did, "problem": "draft_id 重复"})
        seen_draft.add(did)
        anchor = c.get("source_anchor")
        if not isinstance(anchor, dict):
            errors.append({
                "code": "E17", "where": f"{did}.source_anchor",
                "problem": (
                    "source_anchor 必须是对象 "
                    "{\"rule_id\": \"R1\", \"probing\": false}，收到 "
                    f"{type(anchor).__name__}：{str(anchor)[:40]!r}"
                    "——不要写成数组或裸字符串")})
            anchor = {}
        rid = anchor.get("rule_id")
        if rid not in known_rules:
            errors.append({"code": "E17", "where": did,
                           "problem": f"引用未知 rule_id：{rid!r}（example_map 里没有）"})
        else:
            covered.setdefault(rid, []).append(did)
            if rule_probing is not None:
                declared_probing = bool(anchor.get("probing"))
                authoritative_probing = bool(rule_probing.get(rid, False))
                if declared_probing != authoritative_probing:
                    errors.append({
                        "code": "E20",
                        "where": f"{did}.source_anchor.probing",
                        "problem": (
                            f"探测属性与业务规则 {rid} 不一致："
                            f"case={declared_probing}, rule={authoritative_probing}。"
                            "用例不得自行把正式规则降级为探测规则。"),
                    })
        # 格式族复用 schema.validate_case：盖占位 id/digest 后全量跑
        probe = dict(c)
        probe.pop("draft_id", None)
        probe.pop("kind", None)
        probe.setdefault("case_id", "draft/R0-C000")
        probe.setdefault("case_version", 1)
        probe = digest.stamp_case_digests(probe)
        for e in schema.validate_case(probe, i):
            e = dict(e)
            e["where"] = f"{did}:{e['where']}"
            errors.append(e)
        auto = c.get("automation") or {}
        if not isinstance(auto, dict):
            errors.append({
                "code": "E18", "where": f"{did}.automation",
                "problem": ("automation 必须是对象 {\"admissible\": true, "
                            f"\"recipe\": {{...}}}}，收到 {type(auto).__name__}")})
            auto = {}
        if auto.get("admissible") is True:
            _check_recipe(auto.get("recipe"), f"{did}.automation.recipe", errors)
    # E16 覆盖缺口（uncovered_rules 的非对象元素同样只拒不炸）
    decl_by_rule = {d.get("rule_id"): d for d in (uncovered_rules or [])
                    if isinstance(d, dict)}
    for rid in known_rules:
        if covered.get(rid):
            continue
        decl = decl_by_rule.get(rid)
        reason = (decl or {}).get("reason") or ""
        if not decl or not (_UNCOVERED_OWNER.search(reason)
                            and _UNCOVERED_CONSEQ.search(reason)):
            errors.append({"code": "E16", "where": rid,
                           "problem": "规则无用例覆盖，且无带「谁覆盖+后果」的不覆盖声明"})
    probing = [c.get("draft_id") for c in cases
               if isinstance(c.get("source_anchor"), dict)
               and c["source_anchor"].get("probing")]
    if probing:
        warnings.append({"code": "W06", "where": ",".join(map(str, probing)),
                         "problem": f"{len(probing)} 条探测性用例——不进 PASS 判据,执行按探测层记录"})
    for rid, dids in covered.items():
        kinds = {next((c.get("kind") for c in cases if c.get("draft_id") == d), None)
                 for d in dids}
        kinds.discard(None)
        if len(dids) > 1 and len(kinds) == 1:
            warnings.append({"code": "W07", "where": rid,
                             "problem": f"该规则 {len(dids)} 条用例全是同一 kind——覆盖单薄提示"})
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "coverage": {rid: covered.get(rid, []) for rid in known_rules}}


def draft(batch_id: str, *, cases: list[dict[str, Any]],
          uncovered_rules: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    analysis = artifacts.load_artifact(batch_id, "test_analysis")
    frame = artifacts.load_artifact(batch_id, "business_frame")
    rule_probing = {
        str(rule.get("rule_id")): bool(rule.get("probing"))
        for rule in frame.get("rules", [])
    }
    result = validate_draft(
        cases, analysis["example_map"], uncovered_rules, rule_probing)
    if not result["ok"]:
        return {"ok": False, **result}
    # 落盘前把校验期的 draft_id 缺省值**固化**进产物(2026-08-28 U6 复验实锤):
    # validate_draft 对缺 draft_id 的用例用 d{i+1} 顶着校验,但旧实现原样落盘
    # None——于是 adopt 的 selected_draft_ids 无论传什么都匹配不上,一份校验
    # 通过的草稿在构造上**永远无法被采纳**。校验用什么名,落盘就存什么名。
    cases = [dict(c, draft_id=str(c.get("draft_id") or f"d{i + 1}"))
             for i, c in enumerate(cases)]
    art = artifacts.save_artifact(batch_id, "case_draft", {
        "cases": cases,
        "uncovered_rules": uncovered_rules or [],
        "validation": {"errors": [], "warnings": result["warnings"],
                       "coverage": result["coverage"]},
    })
    return {"ok": True, "case_draft": art, **result}
