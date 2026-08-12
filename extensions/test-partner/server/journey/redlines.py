# -*- coding: utf-8 -*-
"""redlines — 执行层安全红线的纯函数单源（UI 轨 + API 轨共用）。

五条红线（护栏 5，口径 = 设计稿 §2.2 / ADR-M1-01）：
  1. 跨 host 拒发：有效 host 等价类默认单元素——渲染后 URL 的 host:effective_port
     必须与批次 base_url 字面相等（http 补 80 / https 补 443，沿用 execute.py
     `_host_key` 语义；tests 有与 execute._host_key 的防漂对拍）。禁止代码内置隐式别名。
  2. 变量残留 skip：渲染后仍残留 {{var}} → 不发/不导航。
  3. 凭据零回显：出站文本过 scrub 门（够长的变量值换回 {{名字}}）。
  4. 无断言不算过：断言计数为 0 的 case 不得 PASS（由 runtime 计数 + 投影闸兜底）。
  5. 不跟随重定向：API 轨 follow_redirects=False；UI 轨浏览器重定向不可禁 →
     重定向落点 host 仍须 ∈ 等价类，否则中止判 skip。

本模块【纯标准库、零仓内依赖】——编译器会把本文件逐字嵌入 AutomationBundle
（bundle 需可脱离批次独立重跑），修改本文件 = 需重编译存量 bundle。
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

MIN_SCRUB_LEN = 6
VAR_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def host_key(url: str) -> str:
    """URL → `host:effective_port`（http 补 80，https 补 443）。解析不出返回空串。"""
    parts = urlsplit(str(url or "").strip())
    scheme = (parts.scheme or "").lower()
    try:
        host, port = parts.hostname, parts.port
    except ValueError:  # 畸形端口
        return ""
    if not host:
        return ""
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else 0
    return f"{host.lower()}:{port}"


def safe_target_url(url: str) -> dict[str, Any]:
    """Validate a persistable target URL without echoing rejected secrets."""
    raw = str(url or "").strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return {"ok": False, "error": "目标地址格式非法"}
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return {"ok": False, "error": "目标地址必须是 http(s) URL"}
    if parts.username is not None or parts.password is not None:
        return {"ok": False, "error": "目标地址不能内嵌用户名或密码"}
    if parts.query or parts.fragment:
        return {"ok": False, "error": "目标地址不能包含 query 或 fragment；敏感值请用变量"}
    hostname = parts.hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    default_port = 443 if parts.scheme.lower() == "https" else 80
    netloc = hostname if port in (None, default_port) else f"{hostname}:{port}"
    path = parts.path or ""
    normalized = f"{parts.scheme.lower()}://{netloc}{path}".rstrip("/")
    return {"ok": True, "url": normalized}


def same_host(base_url: str, url: str) -> bool:
    """等价类判定（默认单元素）：两边 host_key 非空且字面相等。"""
    a, b = host_key(base_url), host_key(url)
    return bool(a) and a == b


def unresolved_vars(text: str) -> list[str]:
    """渲染后文本里仍残留的 {{var}} 变量名（去重保序）。"""
    out: list[str] = []
    for m in VAR_PATTERN.finditer(str(text or "")):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def render(text: str, variables: dict[str, Any]) -> str:
    """{{var}} 替换；未提供的变量原样保留（由 unresolved_vars 拦）。"""
    def _sub(m: re.Match) -> str:
        name = m.group(1)
        return str(variables[name]) if name in variables else m.group(0)
    return VAR_PATTERN.sub(_sub, str(text or ""))


def scrub_map(variables: dict[str, Any],
              exclude: tuple[str, ...] = ("baseUrl", "base_url")) -> list[tuple[str, str]]:
    """出站替换表 [(变量值, "{{变量名}}")]，长值优先；短值不参与（换回反而指纹化）。"""
    pairs = [(str(v), "{{%s}}" % k) for k, v in variables.items()
             if str(v) and len(str(v)) >= MIN_SCRUB_LEN and k not in exclude]
    return sorted(pairs, key=lambda kv: len(kv[0]), reverse=True)


def scrub(text: Any, pairs: list[tuple[str, str]]) -> Any:
    """出站门：把够长的变量值换回 {{名字}}。非字符串原样返回。"""
    if not isinstance(text, str) or not pairs:
        return text
    for value, placeholder in pairs:
        if value in text:
            text = text.replace(value, placeholder)
    return text


def navigation_allowed(base_url: str, target_url: str) -> dict[str, Any]:
    """UI 轨主文档导航/重定向落点判定。{allowed, reason}。"""
    if same_host(base_url, target_url):
        return {"allowed": True, "reason": ""}
    return {"allowed": False,
            "reason": f"跨 host 拒发/中止:目标 {host_key(target_url) or target_url!r} "
                      f"∉ 等价类 {{{host_key(base_url)}}}"}


def route_decision(base_url: str, request_url: str) -> str:
    """页面子资源/XHR 出站策略：同等价类 continue_，第三方域 abort（并记录）。"""
    return "continue" if same_host(base_url, request_url) else "abort"
