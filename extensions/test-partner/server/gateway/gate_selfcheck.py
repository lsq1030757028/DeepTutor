# -*- coding: utf-8 -*-
"""gate_selfcheck — 人闸挂载面 vs 运行时能力面的一致性自检（DoD#24 / BB-502 治复发）。

## 为什么只修一次不够

BB-502 的**根因**不是"partner 挂了 ask_user"这件具体的事，bank 记的原话是
「**工具挂载面与运行时能力面无一致性校验**」。案甲（收窄 partner 的 builtin_tools）
治的是本次这一处；没有这个自检，下一个通道接进来时同样的错会原样再犯一遍——
挂上一个人闸工具，而那条通道根本没有 waiter，于是问题被拍平成最终回复、
本轮结束、用户无感知。

## 判据

一条通道要能挂人闸工具，必须**同时**满足：
1. 挂载面上有人闸工具（`ask_user`）；
2. 运行时能力面上有等待器（`wait_for_user_reply`）。

只满足 1 = 半接通 = 静默降级。本模块把这种组合判成 `MISMATCH` 并**要求不挂**，
同时给出显式告警——不是"记个 warning 继续跑"，是把工具从挂载面上摘掉。

## 边界（如实说）

本自检跑在 **extensions 侧**，看的是我们自己声明的挂载面与我们已知的通道能力表。
它**不能**证明容器里那一侧真的注入了 waiter——那需要上游的运行态自省接口，
当前没有。所以 `KNOWN_CHANNELS` 是一张**按当版上游实证填的表**，每条带锚；
上游改了而我们没跟，这张表就会过期。**过期的方向是"我们以为有 waiter 其实没有"**，
所以表里任何一条改成 True 都必须附新的代码锚，不许凭印象填。
"""
from __future__ import annotations

from typing import Any

#: 人闸工具与其运行时依赖。两者是一对，拆开就是半接通。
HUMAN_GATE_TOOL = "ask_user"
REQUIRED_RUNTIME_CAPABILITY = "wait_for_user_reply"

#: 当版上游实证的通道能力表。**每条必须带锚**。
KNOWN_CHANNELS: dict[str, dict[str, Any]] = {
    "main_chat": {
        "has_waiter": True,
        "anchor": "deeptutor/services/session/turn_runtime.py:1620 注入 wait_for_user_reply",
    },
    "capability_test": {
        # 「测试」capability 跑在主聊 turn 上，与主聊同一条 waiter。
        "has_waiter": True,
        "anchor": "同 main_chat；hasInlineAskUser（ChatMessages.tsx:395-397）不以 "
                  "capability 为条件，故人闸卡在 capability 轮零改动可用",
    },
    "partner": {
        "has_waiter": False,
        "anchor": "deeptutor/services/partners/runtime.py:396-397 显式不注入并注明 IM 语义；"
                  "被 tests/services/partners/test_partner_runtime.py:348 锁死。"
                  "这是上游的**设计意图**，不是缺陷——所以正解是别在这条通道上挂人闸，"
                  "而不是想办法把 waiter 塞进去",
    },
}


def channel_capability(channel: str) -> dict[str, Any]:
    """查一条通道的能力。**未知通道按「没有 waiter」处理**（fail-closed）。

    未知就当没有，是因为反过来的默认值会让"新接一条通道"这个动作自动获得
    挂人闸的资格——那正是 BB-502 的复发路径。
    """
    known = KNOWN_CHANNELS.get(channel)
    if known is None:
        return {"has_waiter": False, "anchor": "",
                "unknown": True,
                "note": f"通道 {channel!r} 不在已实证表内，按没有 waiter 处理（fail-closed）。"
                        "要把它标成有 waiter，先去上游找到注入点并把锚写进 KNOWN_CHANNELS。"}
    return dict(known, unknown=False)


def check_mount_surface(channel: str, mounted_tools: Any) -> dict[str, Any]:
    """校验一条通道的挂载面。返回 `{ok, code, message, safe_tools, warning}`。

    `safe_tools` 是**建议实际挂载**的清单——不匹配时人闸工具已被摘掉。
    调用方应当直接用它，而不是用自己那份原始清单。
    """
    tools = [str(t) for t in (mounted_tools or [])]
    cap = channel_capability(channel)
    mounts_gate = HUMAN_GATE_TOOL in tools
    has_waiter = bool(cap.get("has_waiter"))

    if mounts_gate and not has_waiter:
        safe = [t for t in tools if t != HUMAN_GATE_TOOL]
        return {
            "ok": False,
            "code": "E_HUMAN_GATE_MISMATCH",
            "channel": channel,
            "message": (
                f"通道 {channel!r} 挂了人闸工具 {HUMAN_GATE_TOOL!r}，"
                f"但它没有 {REQUIRED_RUNTIME_CAPABILITY} 等待器。"
                "这种组合不会报错——它会把问题拍平成本轮最终回复然后结束本轮，"
                "结构化答复与同轮上下文全部丢失，而用户看不出发生了什么。"
                f"已把 {HUMAN_GATE_TOOL!r} 从挂载面摘掉；这条通道要问问题就走文字提问 + 等下一轮。"),
            "safe_tools": safe,
            "warning": True,
            "anchor": cap.get("anchor", ""),
            "channel_unknown": cap.get("unknown", False),
        }
    if not mounts_gate and has_waiter:
        # 不是错误：有能力而不用是合法选择。如实记一条信息，不当告警。
        return {"ok": True, "code": "GATE_AVAILABLE_UNUSED", "channel": channel,
                "message": f"通道 {channel!r} 具备人闸能力但本次没挂 {HUMAN_GATE_TOOL!r}。",
                "safe_tools": tools, "warning": False}
    return {"ok": True, "code": "OK", "channel": channel,
            "message": f"通道 {channel!r} 的挂载面与运行时能力面一致。",
            "safe_tools": tools, "warning": False}


def startup_report(channels: dict[str, Any] | None = None) -> dict[str, Any]:
    """启动期自检：逐通道核一遍，返回汇总。

    默认核的是我们自己会去挂工具的两条：partner（案甲收窄后应当已无 ask_user）
    与 capability_test（应当有且合法）。
    """
    from server.gateway import partner_setup

    surfaces = channels or {
        "partner": list(partner_setup.PARTNER_BUILTIN_TOOLS),
        "capability_test": [HUMAN_GATE_TOOL],
    }
    results = {name: check_mount_surface(name, tools)
               for name, tools in surfaces.items()}
    problems = [r for r in results.values() if not r["ok"]]
    return {"ok": not problems, "results": results,
            "problem_count": len(problems),
            "message": ("人闸挂载面自检通过。" if not problems else
                        "人闸挂载面自检发现不匹配：" +
                        "；".join(r["channel"] for r in problems))}
