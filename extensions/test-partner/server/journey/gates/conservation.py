# -*- coding: utf-8 -*-
"""conservation — E19 守恒闸（设计稿 §6.2「守恒闸（新牙）」）。

## 它治的假绿

写用例最常见的绿是这一种：POST 回 200，断言 `status == 200`，收工。
可是"接口没报错"和"库里真的多了一条"是两件事 —— 落库失败、写到了另一个租户、
写了两条、写进去又被回滚，**每一种都能回 200**。这一族假绿的共同形状是
**断言停在了响应层，没有跨到数据层**。

守恒闸要求写用例必须回答："这次写，让哪个量变了多少。"

## 判据（全部确定性，不看语义）

对每条 `side_effects.writes == true` 的用例：

| 码 | 判什么 |
|---|---|
| E19.no_mutation | 声明 writes=true，配方里却没有任何变更动作（非 GET 的 request / click） |
| E19.missing_conservation | 没有 `db_snapshot` + `expect_db_delta` 配对 |
| E19.unpaired_metric | 两者的 `metric` 对不上（快照 A 却对 B 求差） |
| E19.order | 快照没排在变更动作之前，或求差没排在其之后 |
| E19.no_db_layer | 没有 `db` 断言层（守恒断言的产物形态得在 caseset 上留痕） |

顺序判据不是形式主义：`db_snapshot` 排在写之后，量到的是写完的值，
`after - before` 恒等于 0，而 `delta: 0` 的断言**会绿**。这是本闸自己最容易
被绕过的地方，所以单列一码。

## L3 没授予时不判 E19 —— 以及为什么这不是个后门

缺只读库时守恒断言写不出来，此时判 E19 等于要求用户拿不到的东西。
但"不判"不能变成"不声明地放过"：本闸在 L3 未授予时**返回一条显式 gap**
（`capability:L3 未授予`，前缀与 `capability_ladder.gap_notes` 一致），
它会进 coverage_ledger，在覆盖图上以有名有姓的缺口出现。

所以靠"不声明 L3"来躲开 E19 的代价是：**每一条写用例都在覆盖图上挂一个缺口**。
躲得掉闸，躲不掉账 —— 这正是护栏 3 说的显式不覆盖而非静默降级。

（本闸的作用域边界已按纪律④单验：见 `test_gate_conservation.py` 里
`test_l3_ungranted_still_records_named_gap`，它断的是"躲开闸的那条路上有账"，
而不只断"授予时闸能拦"。）
"""
from __future__ import annotations

from typing import Any

#: 会改变被测系统状态的动作。
#: `request` 只有非 GET/HEAD 才算 —— 一条 GET 不该被当成写。
_MUTATING_UI_OPS = frozenset({"click"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

SNAPSHOT_OP = "db_snapshot"
DELTA_OP = "expect_db_delta"


def _err(where: str, sub: str, problem: str) -> dict[str, Any]:
    return {"code": "E19", "sub": sub, "where": where, "problem": problem}


def _is_mutating(action: dict[str, Any]) -> bool:
    op = str((action or {}).get("op") or "")
    if op in _MUTATING_UI_OPS:
        return True
    if op == "request":
        return str((action or {}).get("method") or "GET").upper() not in _SAFE_METHODS
    return False


def check_case(case: dict[str, Any], *, l3_granted: bool) -> list[dict[str, Any]]:
    """单条用例的守恒判据。非写用例恒过。"""
    se = case.get("side_effects") or {}
    where = str(case.get("case_id") or "?")
    recipe = ((case.get("automation") or {}).get("recipe")) or {}
    actions = list(recipe.get("actions") or [])
    if not se.get("writes"):
        unprovable_ui_ops = sorted({
            str(action.get("op") or "")
            for action in actions
            if str(action.get("op") or "") in _MUTATING_UI_OPS
        })
        mutating_methods = sorted({
            str(action.get("method") or "GET").upper()
            for action in actions
            if str(action.get("op") or "") == "request"
            and str(action.get("method") or "GET").upper() not in _SAFE_METHODS
        })
        if mutating_methods or unprovable_ui_ops:
            facts: list[str] = []
            if mutating_methods:
                facts.append(f"HTTP 方法 {mutating_methods}")
            if unprovable_ui_ops:
                facts.append(
                    f"UI 动作 {unprovable_ui_ops}（触发前无法证明只读；纯导航请用 goto）"
                )
            return [_err(
                where,
                "write_declaration_mismatch",
                "配方包含潜在写动作 " + "、".join(facts)
                + "，但 side_effects.writes=false。"
                "写风险以真实动作判定，不能靠声明降级；请修正声明后重新确认。",
            )]
        return []
    if not l3_granted:
        return []          # 不判；账由 gap_notes_for 出

    problems: list[dict[str, Any]] = []

    mut_idx = [i for i, a in enumerate(actions) if _is_mutating(a)]
    if not mut_idx:
        problems.append(_err(
            where, "no_mutation",
            "声明 side_effects.writes=true，配方里却没有任何变更动作"
            "（非 GET 的 request，或 click）。要么声明错了，要么配方漏了那一步；"
            "两种都会让写确认人闸对着一条其实不写的用例发问。"))

    snaps = {str(a.get("metric") or ""): i for i, a in enumerate(actions)
             if str(a.get("op") or "") == SNAPSHOT_OP}
    deltas = {str(a.get("metric") or ""): i for i, a in enumerate(actions)
              if str(a.get("op") or "") == DELTA_OP}

    if not snaps or not deltas:
        problems.append(_err(
            where, "missing_conservation",
            f"写用例必须带守恒断言：一个 {SNAPSHOT_OP}（写之前量基线）配一个 "
            f"{DELTA_OP}（写之后断变化量）。只断 HTTP 状态码的写用例证明不了"
            f"「库里真的变了」——落库失败、写错租户、写了两条、写完被回滚，"
            f"每一种都能回 200。"))
        return problems

    unpaired = sorted(set(snaps) ^ set(deltas))
    if unpaired:
        problems.append(_err(
            where, "unpaired_metric",
            f"守恒量没有配对：{unpaired}。快照 A 却对 B 求差，闸会全绿而守恒"
            f"根本没被验——这正是本闸要防的形状。"))

    if mut_idx:
        first_mut, last_mut = min(mut_idx), max(mut_idx)
        for metric in sorted(set(snaps) & set(deltas)):
            if snaps[metric] > first_mut:
                problems.append(_err(
                    where, "order",
                    f"守恒量 {metric!r} 的 {SNAPSHOT_OP} 排在变更动作之后"
                    f"（snapshot@{snaps[metric]} > mutate@{first_mut}）。"
                    f"这样量到的是写完的值，after-before 恒为 0，而 delta:0 "
                    f"的断言**会绿**——闸看起来过了，守恒一点没验。"))
            if deltas[metric] < last_mut:
                problems.append(_err(
                    where, "order",
                    f"守恒量 {metric!r} 的 {DELTA_OP} 排在变更动作之前"
                    f"（delta@{deltas[metric]} < mutate@{last_mut}）：写还没发生就求差。"))

    layers = case.get("assertion_layers") or {}
    if not isinstance(layers, dict) or "db" not in layers:
        problems.append(_err(
            where, "no_db_layer",
            "写用例有守恒配方却没有 db 断言层。断言层是 caseset 上唯一能让人"
            "看出「这条用例验到了数据层」的地方；配方在 automation 里，"
            "覆盖图和评审都不读它。"))
    return problems


def gap_notes_for(cases: list[dict[str, Any]], *, l3_granted: bool) -> list[dict[str, Any]]:
    """L3 未授予时，为每条写用例产一条**显式**缺口。

    前缀 `capability:L3 未授予` 与 `capability_ladder.gap_notes` 严格一致 ——
    交互稿 §6f 的覆盖卡按这个前缀识别「这不是漏测，是没授权」，
    前缀不一样卡就认不出来，缺口会退化成一个空格。
    """
    if l3_granted:
        return []
    notes = []
    for case in cases:
        if not (case.get("side_effects") or {}).get("writes"):
            continue
        notes.append({
            "case_id": case.get("case_id"),
            "gap_reason": "capability:L3 未授予",
            "why": "写用例的守恒断言（操作前后守恒量差）需要只读库通道，本次未授予",
            "cannot_assert": ["守恒类断言", "写入是否真落库"],
        })
    return notes


def check_caseset(caseset: dict[str, Any], *, l3_granted: bool) -> dict[str, Any]:
    """整份 caseset 的守恒闸。**不改 schema**（v1.0.0 冻结），只读既有字段。"""
    cases = list(caseset.get("cases") or [])
    problems: list[dict[str, Any]] = []
    for case in cases:
        problems += check_case(case, l3_granted=l3_granted)
    return {
        "ok": not problems,
        "problems": problems,
        "l3_granted": l3_granted,
        "declared_gaps": gap_notes_for(cases, l3_granted=l3_granted),
        "write_cases": [c.get("case_id") for c in cases
                        if (c.get("side_effects") or {}).get("writes")],
    }


__all__ = ["DELTA_OP", "SNAPSHOT_OP", "check_case", "check_caseset", "gap_notes_for"]
