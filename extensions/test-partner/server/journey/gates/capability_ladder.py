# -*- coding: utf-8 -*-
"""capability_ladder — L1–L6 能力锁（设计稿 §6.1，DoD#12/#12b 的「显式」那一半）。

## 它回答的问题

「这次到底跑到了几档，没跑的那几档是**没授权**还是**忘了测**。」

黑盒线最贵的假绿是这一类：拿不到只读库，于是守恒类断言写不出来，于是覆盖图上
那条规则看起来只是"没有用例"——与"我们懒得测"长得一模一样。护栏 3 要的是
**显式不覆盖**：没授权就白纸黑字写「capability:L3 未授予」，让它在覆盖图上
以一个**有名有姓的缺口**出现，而不是一个空格。

## 一条硬规则：口头授予不算授予

`{"L3": {"granted": true}}` 而没有 `evidence`，本模块判它**未授予**并单独报一条
问题。理由是这把锁的全部价值在于「它挡得住」——一个可以靠写 `true` 打开的锁，
在赶工期的第三天一定会被写成 `true`。授予证据必须是机检得到的东西：连接成功的
收据路径、可 clone 的 commit、读得到的 CI run id。

失败方向也定死：**证据不足 = 未授予**（少给），不是「疑似给了就当给了」。
"""
from __future__ import annotations

from typing import Any

#: 档位与它买到的能力（SSOT `workflow-comparison-and-target-flow.md:219-222`）。
LEVELS: dict[str, str] = {
    "L1": "被测系统可达（base_url）",
    "L2": "可登录（测试账号）",
    "L3": "数据层只读（DB 只读账号）——三层断言、守恒/分母类断言",
    "L4": "隔离环境 + 造数许可——可恢复写用例、回归重放",
    "L5": "源码仓读权限——需求↔代码反向对账、部署指纹 commit 锚",
    "L6": "CI/发布信息——构建锚定、回归入 CI",
}

#: 每档未授予时**必然做不了**的断言族。写进 coverage 的 gap 说明里，
#: 让"少了什么"具体到能被读懂，而不是一句"能力不足"。
WITHHELD_COSTS: dict[str, tuple[str, ...]] = {
    "L1": ("端到端可达性断言",),
    "L2": ("鉴权后行为断言", "权限反例"),
    "L3": ("守恒类断言", "分母类断言", "数据层三层断言"),
    "L4": ("可恢复写用例", "故障注入", "回归重放"),
    "L5": ("需求↔代码反向对账", "部署指纹 commit 锚"),
    "L6": ("构建锚定", "回归入 CI"),
}


def _entry(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def normalize(profile: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """把 `intake_profile.capability_ladder` 归一成六档齐全的形态。

    缺档不是"未知"，是**未授予**：一个没写的档位与一个写了 granted=false 的档位，
    在"这次跑不到那么深"这件事上没有区别，而把它留成"未知"会让覆盖图上出现
    第三种状态，谁也说不清该不该收口。
    """
    raw = _entry((profile or {}).get("capability_ladder"))
    out: dict[str, dict[str, Any]] = {}
    for level in LEVELS:
        row = _entry(raw.get(level))
        evidence = str(row.get("evidence") or "").strip()
        granted = bool(row.get("granted")) and bool(evidence)
        out[level] = {
            "granted": granted,
            "evidence": evidence,
            "reason": str(row.get("reason") or "").strip(),
            "declared_gap": list(row.get("declared_gap")
                                 or WITHHELD_COSTS.get(level, ())),
            "claimed_without_evidence": bool(row.get("granted")) and not evidence,
        }
    return out


def granted(profile: dict[str, Any] | None, level: str) -> bool:
    return normalize(profile).get(level, {}).get("granted", False)


def problems(profile: dict[str, Any] | None) -> list[str]:
    """只报一种问题：**声明授予却拿不出机检证据**。

    没授予不是问题（它是现实，按显式 gap 走）；**假装授予**才是问题，
    因为下游会据此以为守恒断言真的跑过了。
    """
    out: list[str] = []
    for level, row in normalize(profile).items():
        if row["claimed_without_evidence"]:
            out.append(
                f"{level} 声明 granted=true 但没有 evidence——**口头授予不算授予**，"
                f"按未授予处理。授予证据必须机检得到（连接成功的收据路径 / "
                f"可 clone 的 commit / 读得到的 CI run id），不是一句话。")
    return out


def gap_notes(profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    """未授予档位的显式缺口说明，进 coverage_ledger。

    每条都带 `gap_reason="capability:Lx 未授予"` 这个**固定前缀**——
    交互稿 §6f 覆盖卡按它识别「这不是漏测，是没授权」，前缀变了卡就认不出来。
    """
    notes = []
    for level, row in normalize(profile).items():
        if row["granted"]:
            continue
        notes.append({
            "level": level,
            "buys": LEVELS[level],
            "gap_reason": f"capability:{level} 未授予",
            "why": row["reason"] or "未声明授予",
            "cannot_assert": row["declared_gap"],
            "claimed_without_evidence": row["claimed_without_evidence"],
        })
    return notes


def summary(profile: dict[str, Any] | None) -> dict[str, Any]:
    rows = normalize(profile)
    return {
        "granted": sorted(k for k, v in rows.items() if v["granted"]),
        "withheld": gap_notes(profile),
        "problems": problems(profile),
    }


__all__ = ["LEVELS", "WITHHELD_COSTS", "gap_notes", "granted", "normalize",
           "problems", "summary"]
