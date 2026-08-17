# -*- coding: utf-8 -*-
"""mcp_payload — 「错误当数据」防线（M2 总则 7 / ADR-M2-01 第 4 条 + G1）。

DT 的 MCP 适配器在**未连接 / 超时 / 半截**三种失败下返回的是**普通字符串**且
`success=true`（`deeptutor/services/mcp/manager.py:416-417,429,431,438,441`）。
即调用方拿到的东西长得像成功，内容却是一句人话。凡从 DT / TAPD / 外部通道取回的
内容，**落盘或参与 digest 之前必须先过本模块判一次**，判不出就 fail-closed。

判别顺序（先形态后内容，任何一步命中即判错，绝不返回业务对象）：

1. 非字符串 / 空 → `E_MCP_UNAVAILABLE`（通道没给东西）。
2. 以 `(MCP ` 开头 → `E_MCP_UNAVAILABLE`（适配器的错误话术，见上锚）。
3. 不是合法 JSON → `E_MCP_UNAVAILABLE`（半截字符串、纯人话、被截断的 JSON）。
4. 解析出来不是 dict / list → `E_MCP_UNAVAILABLE`（裸标量不构成业务载荷）。

**这里刻意不做"看着像 JSON 就先信一半"的宽容**：宽容正是让错误串混进 oracle 快照
的那条路，而 oracle 一旦被污染，`content_digest` 会算在一份错误文本上且全链无人报警。
"""
from __future__ import annotations

import json
from typing import Any

#: 适配器错误串的固定前缀。锚：`deeptutor/services/mcp/manager.py:416-417,429,431,438,441`
#: 形如 `(MCP server 'tapd' is not connected)` / `(MCP tool 'x' timed out)`。
MCP_ERROR_PREFIX = "(MCP "

E_MCP_UNAVAILABLE = "E_MCP_UNAVAILABLE"


class McpPayloadError(RuntimeError):
    """载荷判别失败。`code` 给调用方分支，`raw_head` 是给人看的前 200 字。"""

    def __init__(self, message: str, *, code: str = E_MCP_UNAVAILABLE,
                 raw_head: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.raw_head = raw_head


def looks_like_mcp_error(raw: Any) -> bool:
    """只判「是不是适配器错误串」这一件事，供调用方在别处复用。"""
    return isinstance(raw, str) and raw.lstrip().startswith(MCP_ERROR_PREFIX)


def parse_mcp_payload(raw: Any, *, source: str = "mcp") -> Any:
    """把 MCP 返回体解析成业务对象；判不出即抛 `McpPayloadError`。

    返回值只可能是 dict 或 list——标量与 None 一律视为「通道没给业务数据」。
    """
    head = (raw if isinstance(raw, str) else repr(raw))[:200]
    if not isinstance(raw, str) or not raw.strip():
        raise McpPayloadError(
            f"{source} 没有返回任何内容（通道可能未连接）。", raw_head=head)
    text = raw.strip()
    if text.startswith(MCP_ERROR_PREFIX):
        raise McpPayloadError(
            f"{source} 返回的是 MCP 适配器错误串而不是业务数据：{head}", raw_head=head)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise McpPayloadError(
            f"{source} 返回的不是合法 JSON（可能是半截字符串或一句人话）：{head}",
            raw_head=head) from None
    if not isinstance(data, (dict, list)):
        raise McpPayloadError(
            f"{source} 返回的 JSON 是裸标量（{type(data).__name__}），不构成业务载荷：{head}",
            raw_head=head)
    return data


def try_parse_mcp_payload(raw: Any, *, source: str = "mcp") -> dict[str, Any]:
    """不抛异常的封装：`{ok, code, data|message, raw_head}`。

    给 MCP 工具面用——工具面的契约是「首字段固定 `{ok, code}`」（ADR-M2-01 第 3 条），
    不能靠异常穿透。
    """
    try:
        return {"ok": True, "code": "OK", "data": parse_mcp_payload(raw, source=source)}
    except McpPayloadError as exc:
        return {"ok": False, "code": exc.code, "message": str(exc),
                "raw_head": exc.raw_head}
