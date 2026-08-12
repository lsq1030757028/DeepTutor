# -*- coding: utf-8 -*-
"""entity_scope — 被测**实体**溯源闸（0030；本线「作用域没人核」第七例）。

## 它补的那一层

第 0 步溯源一直只做到**系统级**：确认"测的是不是这个系统"。
M2 主证据栽在下一层——系统对了，**实体错了**：

- 需求说的是「新建**自定义角色**」（`custom_characters`，App 用户态写入）；
- 我们实际写的是「**推荐角色**」（`recommended_characters`，后台 admin API 写入）。

两者不同表、不同写入路径、不同调用方。而那一轮的结论**看起来完全成立**：
接口 200、守恒 +1、断言逐条有据、覆盖图 5 条 covered ——
**唯一错的是它指向了另一个东西**。

这是七例里唯一一种会让整轮结论"自洽但指错"的作用域错误，所以判据必须机械化：
写进文档的"记得核对被测对象"，与本次栽的那次之间没有任何差别（那次也"记得"了）。

## 判据形态

`需求所指实体 = X，本轮实际写入实体 = Y，X == Y？`

**Y 不取声明，取配方**：从用例真正打的写请求路径反推实体。取声明的话，
这道闸就退化成"我们说我们测的是自定义角色"——而上一轮如果有这么一栏，
填的多半也是"自定义角色"，因为写的人以为自己在测那个。

X 来自 `intake_profile.requirement_entity`，由接入阶段落定（人给，机器不猜）。
**X 缺失不是"没问题"，是判 unknown 并要求补**——缺省放行等于这道闸不存在。
"""
from __future__ import annotations

import re
from typing import Any

#: 路径 → 实体。**按被测系统登记**，一条一锚。
#: 匹配不到的写路径判 `unknown`（不默认放行——与 E22 未知 op 同一条道理）。
PATH_ENTITY_MAP: tuple[tuple[str, str], ...] = (
    (r"/admin/v[0-9]+/characters/recommended", "recommended_character"),
    (r"/admin/v[0-9]+/characters/custom", "custom_character"),
    (r"/api/v[0-9]+/characters/custom", "custom_character"),
    (r"/api/v[0-9]+/characters(?!/)", "character"),
    (r"/admin/v[0-9]+/voice-presets", "voice_preset"),
    (r"/admin/v[0-9]+/voices", "voice_asset"),
)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

MATCH = "match"
MISMATCH = "mismatch"
UNKNOWN = "unknown"


def entity_of_path(path: str) -> str | None:
    for pattern, entity in PATH_ENTITY_MAP:
        if re.search(pattern, str(path or "")):
            return entity
    return None


def written_entities(case: dict[str, Any]) -> list[str | None]:
    """这条用例**实际**会写哪些实体 —— 从配方里的非 GET 请求反推，不看声明。"""
    recipe = ((case.get("automation") or {}).get("recipe")) or {}
    out: list[str | None] = []
    for a in recipe.get("actions") or []:
        if str((a or {}).get("op") or "") != "request":
            continue
        if str((a or {}).get("method") or "GET").upper() in _SAFE_METHODS:
            continue
        out.append(entity_of_path((a or {}).get("path") or ""))
    return out


def check_case(case: dict[str, Any], requirement_entity: str) -> dict[str, Any]:
    """单条用例的实体对齐。只读用例不判（它不写，谈不上写错对象）。"""
    cid = str(case.get("case_id") or "?")
    writes = bool((case.get("side_effects") or {}).get("writes"))
    actual = [e for e in written_entities(case)]
    if not writes and not actual:
        return {"case_id": cid, "verdict": MATCH, "writes": False,
                "requirement_entity": requirement_entity, "actual_entities": []}
    if not requirement_entity:
        return {"case_id": cid, "verdict": UNKNOWN, "writes": writes,
                "requirement_entity": "", "actual_entities": actual,
                "problem": "intake_profile.requirement_entity 缺失——需求所指实体没落定，"
                           "无从判断本轮写对了没有。**缺省不放行**：这道闸一旦对缺失"
                           "默认过，就等于不存在。"}
    if any(e is None for e in actual):
        return {"case_id": cid, "verdict": UNKNOWN, "writes": writes,
                "requirement_entity": requirement_entity, "actual_entities": actual,
                "problem": "有写请求的路径反推不出实体（不在 PATH_ENTITY_MAP 里）。"
                           "未知按不通过处理：默认放行会让每个新写入路径自动获得豁免。"}
    bad = sorted({e for e in actual if e != requirement_entity})
    if bad:
        return {"case_id": cid, "verdict": MISMATCH, "writes": writes,
                "requirement_entity": requirement_entity, "actual_entities": actual,
                "problem": f"需求所指实体={requirement_entity}，本轮实际写入实体={bad}。"
                           f"不同实体=不同表、不同写入路径、不同调用方；"
                           f"用另一个实体的同能力面顶替，结论会自洽但指错东西。"}
    return {"case_id": cid, "verdict": MATCH, "writes": writes,
            "requirement_entity": requirement_entity, "actual_entities": actual}


def check_caseset(caseset: dict[str, Any], requirement_entity: str) -> dict[str, Any]:
    rows = [check_case(c, requirement_entity) for c in (caseset.get("cases") or [])]
    offenders = [r for r in rows if r["verdict"] in (MISMATCH, UNKNOWN)]
    return {
        "ok": not offenders,
        "requirement_entity": requirement_entity,
        "rows": rows,
        "offenders": offenders,
        # 覆盖图上要显示的那一行（0030 要求显式标注）
        "banner": ("本轮未触达需求所指的真实用户路径：需求所指实体=%s，"
                   "存在实际写入实体不符的用例 %s"
                   % (requirement_entity or "(未落定)",
                      [r["case_id"] for r in offenders])) if offenders else "",
    }


def gap_case_ids(caseset: dict[str, Any], requirement_entity: str) -> set[str]:
    """实体不符的用例 id —— 覆盖图据此**不把它们算作覆盖**。"""
    return {r["case_id"] for r in check_caseset(caseset, requirement_entity)["rows"]
            if r["verdict"] in (MISMATCH, UNKNOWN)}


GAP_PREFIX = "entity:被测对象不符"


def gap_reason(requirement_entity: str, actual: list[str]) -> str:
    return (f"{GAP_PREFIX}——需求所指实体={requirement_entity}，"
            f"本轮实际写入实体={sorted(set(a for a in actual if a))}；"
            f"路径不同源，不得以同能力面顶替")


__all__ = ["GAP_PREFIX", "MATCH", "MISMATCH", "PATH_ENTITY_MAP", "UNKNOWN",
           "check_case", "check_caseset", "entity_of_path", "gap_case_ids",
           "gap_reason", "written_entities"]
