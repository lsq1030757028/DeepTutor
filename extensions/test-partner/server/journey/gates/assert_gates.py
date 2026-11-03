# -*- coding: utf-8 -*-
"""assert_gates — 断言分层执行器（移植件，防假绿机械支撑）。

原件：test_agent/kit/tools/assert_layer.py（只读采石场；语义重实现，向量逐条过）。
五闸对应五类已被实证抓到的假绿：
  retcode        失败响应参与相等判定（null==0 恒真）→ BLOCKED
  control        无对照组断言"过滤生效"（数据恰好单一时恒真）→ INCONCLUSIVE
  discriminating 无判别样本断言"取整方向/边界口径"→ INCONCLUSIVE
  conservation   跨记录守恒不成立 → FAIL
  denominator    「N 条都怎样」无分母 → INCONCLUSIVE
分层不变式：业务层是唯一有判决权的层；取证/探测层恒 OBSERVED。
护栏 2 点名必入 M1 的是 retcode 闸与 discriminating 闸；本移植五闸全带（不稀释）。
"""
from __future__ import annotations

from typing import Any

LAYERS = ("business", "evidence", "probe")
TERMINAL = ("PASS", "FAIL", "BLOCKED", "INCONCLUSIVE", "OBSERVED")


def _base(case_id: str, layer: str, **kw: Any) -> dict[str, Any]:
    r: dict[str, Any] = {"id": case_id, "layer": layer,
                         "gates_passed": [], "gates_failed": []}
    r.update({k: v for k, v in kw.items() if v is not None})
    return r


def business(case_id: str, expected: Any = None, actual: Any = None,
             retcode: Any = 0, ok_retcode: Any = 0,
             control_group: dict | None = None,
             discriminating_samples: int | None = None,
             denominator: int | None = None,
             conservation: dict | None = None,
             evidence: list | None = None, caveat: str | None = None,
             trust: str = "anchored",
             expected_desc: str | None = None) -> dict[str, Any]:
    """业务层主判：唯一有判决权的层。五闸按传入的事实自动裁决。"""
    r = _base(case_id, "business", expected=expected, actual=actual,
              expected_desc=expected_desc, denominator=denominator,
              control_group=control_group,
              discriminating_samples=discriminating_samples,
              conservation=conservation, evidence=evidence or [],
              caveat=caveat, trust=trust)

    # 闸1 retcode：失败响应一律 BLOCKED，绝不参与相等判定
    if retcode != ok_retcode:
        r["gates_failed"].append("retcode")
        r["verdict"] = "BLOCKED"
        r["why"] = f"返回码 {retcode} != {ok_retcode}，失败响应不得参与相等判定"
        return r
    r["gates_passed"].append("retcode")

    # 闸2 control：给了对照组就必须有区分力
    if control_group is not None:
        distinct = control_group.get("distinct") or []
        if len(distinct) <= 1:
            r["gates_failed"].append("control")
            r["verdict"] = "INCONCLUSIVE"
            r["why"] = ("对照组取值单一，'过滤组只含目标值'在此数据下恒真，"
                        "不构成过滤生效的证据")
            return r
        r["gates_passed"].append("control")

    # 闸3 discriminating：给了判别样本数就必须 > 0
    if discriminating_samples is not None:
        if discriminating_samples <= 0:
            r["gates_failed"].append("discriminating")
            r["verdict"] = "INCONCLUSIVE"
            r["why"] = "判别样本为 0，各候选口径给出相同结果，命中率没有区分力"
            return r
        r["gates_passed"].append("discriminating")

    # 闸4 conservation：跨记录守恒
    if conservation is not None:
        agg = conservation.get("aggregate")
        parts = conservation.get("sum_of_parts")
        residual = None if (agg is None or parts is None) else agg - parts
        r["conservation_residual"] = residual
        if residual:
            r["gates_failed"].append("conservation")
            r["verdict"] = "FAIL"
            r["why"] = (f"跨记录守恒不成立：聚合值 {agg} 与分批合计 {parts} 差 {residual}")
            return r
        r["gates_passed"].append("conservation")

    # 闸5 denominator：全量断言必须有分母
    if denominator is not None:
        if denominator <= 0:
            r["gates_failed"].append("denominator")
            r["verdict"] = "INCONCLUSIVE"
            r["why"] = "分母为 0，无样本可判"
            return r
        r["gates_passed"].append("denominator")

    if expected is None and actual is None:
        r["verdict"] = "INCONCLUSIVE"
        r["why"] = "未提供可比对的期望/实际值"
        return r
    r["verdict"] = "PASS" if expected == actual else "FAIL"
    return r


def evidence_row(case_id: str, finding: str, evidence: list | None = None,
                 supports: str | None = None, trust: str = "anchored") -> dict[str, Any]:
    r = _base(case_id, "evidence", finding=finding, evidence=evidence or [],
              supports=supports, trust=trust)
    r["verdict"] = "OBSERVED"
    r["note"] = "取证辅助层无判决权，用于支撑业务层主判"
    return r


def probe(case_id: str, observed: str, gap: str | None = None,
          evidence: list | None = None, trust: str = "anchored") -> dict[str, Any]:
    r = _base(case_id, "probe", observed=observed, gap=gap,
              evidence=evidence or [], trust=trust)
    r["verdict"] = "OBSERVED"
    r["note"] = "探测层不判 PASS/FAIL：需求未定义预期，判决权在产品"
    return r


class Ledger:
    """判定记录账本。validate() 校验不变式，违规清单非空即不许落盘。"""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, row: dict[str, Any]) -> dict[str, Any]:
        self.rows.append(row)
        return row

    def validate(self) -> list[str]:
        errs: list[str] = []
        seen: set[Any] = set()
        for r in self.rows:
            rid = r.get("id")
            if rid in seen:
                errs.append(f"{rid}: id 重复")
            seen.add(rid)
            if r.get("layer") not in LAYERS:
                errs.append(f"{rid}: layer 非法 {r.get('layer')}")
            if r.get("verdict") not in TERMINAL:
                errs.append(f"{rid}: verdict 非法 {r.get('verdict')}")
            if r.get("layer") in ("evidence", "probe") and r.get("verdict") in ("PASS", "FAIL"):
                errs.append(f"{rid}: {r['layer']} 层无判决权，不得出 {r['verdict']}")
            if (r.get("layer") == "business" and r.get("verdict") in ("PASS", "FAIL")
                    and "retcode" not in r.get("gates_passed", [])):
                errs.append(f"{rid}: 业务层判 {r['verdict']} 但未过 retcode 闸")
        return errs

    def summary(self) -> dict[str, int]:
        s: dict[str, int] = {}
        for r in self.rows:
            k = f"{r['layer']}/{r['verdict']}"
            s[k] = s.get(k, 0) + 1
        return s
