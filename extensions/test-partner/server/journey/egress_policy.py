# -*- coding: utf-8 -*-
"""egress_policy — 自由能力面出站目标策略（纯函数单源，批次 7 / spec Delta AC25-AC27 R6）。

## 为什么自由面需要一套自己的策略，而不是复用 redlines 红线 1

`redlines.same_host` 的等价类判定要一个批次 `base_url` 当锚。自由能力面**没有批次**
——用户丢来一条 curl，目标由用户当场给。于是红线 1 天然不适用，服务端等于新开了一个
**目标完全由用户控制**的出网口。这正是 SSRF 的定义形态：进程在内网，目标由外部输入决定。

所以这里换一条判据：不再问"是不是同一个 host"，改问"这个目标落在不该去的网段吗"。

## 五条策略（缺一条就有对应绕法）

1. **协议限 http(s)**——`file://` 读本地文件、`gopher://` 打内网 TCP、`ftp://` 走另一套栈。
2. **拒环回 / 私网 / 链路本地 / 保留网段**——同机服务（含 test-partner 自己的 3789 配置页
   与 3790 MCP 面）、内网管理面、`169.254.169.254` 云元数据（取 IAM 凭据的经典一跳）。
   **IPv4 与 IPv6 必须同时覆盖**：`::1`、`fc00::/7`、`fe80::/10`，以及
   `::ffff:127.0.0.1` 这种 v4-mapped 写法——只拦 v4 等于没拦。
3. **端口白名单**——把出站面收成"网页/API 常用端口"，不给内网服务端口扫描当跳板。
4. **不跟随重定向**——`302 → http://169.254.169.254/` 是绕过前三条最省事的路子。
   调用方拿到 3xx 不许自动跟；要跟就把 `Location` 重新丢进 `check_target` 再判一次。
5. **DNS 解析后重校**——策略若只看 URL 字面的 host 名，`evil.example.com` 解析成
   `127.0.0.1` 就能整条穿过去（DNS rebinding 的第一跳）。因此域名要先解析，
   **解析出来的每一个地址**都要过第 2 条。

### 第 5 条的残余窗口（如实写在这里，别当它没有）

本模块做的是"解析后校验"。解析与真正建连之间仍有一个 TOCTOU 窗口：权威 DNS 可以在
两次查询之间把记录换掉。彻底堵死要"按已校验 IP 建连 + 自带 Host 头 + TLS SNI 固定"，
那是传输层改造，不在批次 7 范围内。**本模块不声称 rebinding 免疫，只声称解析结果过闸。**

## 与 redlines 的关系：同文件族，零耦合

`redlines.py` 被编译器**逐字内嵌**进 AutomationBundle，改它就要重编译存量 bundle。
本模块因此是**新文件**，一个字都不改 redlines 的既有函数，也不被 redlines 反向导入。
本模块自己也只用标准库。
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

#: 允许的协议。大小写不敏感，判前统一小写。
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: 端口白名单。80/443 是 web 与 API 的常态；8080/8443 是测试与预发的常态。
#: 收得这么紧是刻意的：自由面的目标由用户当场给，端口面越宽越像端口扫描器。
#: 要放宽必须走判据评审，不许在调用点旁路加参数。
ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})

#: 云厂商实例元数据地址。它们在网段判定里已被链路本地/私网覆盖，
#: 这里再具名列一遍是为了**给出可读的拒绝理由**——运维看到"元数据地址"
#: 比看到"链路本地地址"更快明白自己被 SSRF 打了。
METADATA_ADDRESSES = frozenset({
    "169.254.169.254",   # AWS / GCP / Azure / 阿里云 / 腾讯云 IMDS
    "100.100.100.200",   # 阿里云 ECS 元数据
    "fd00:ec2::254",     # AWS IMDSv6
})

#: RFC 特殊/保留网段里，Python 3.12 `ipaddress` 的布尔属性**判不出**的那几条。
#: 这些段既非 `is_private`（不是 RFC1918 那类），又不是正常的公网 API 目标——
#: 正好落在"私网判定"与"公网可路由"两个定义的缝里，是 SSRF 里最容易被漏掉的一类。
#: 标准库把这类补齐前，这里按 IANA IPv4 Special-Purpose 登记表**显式列举**，
#: 判定用简单的网段包含（各段互不重叠，命中即拒）。列一遍还顺带给出可读的拒绝理由。
#:
#:   - 100.64.0.0/10  运营商级 NAT / 共享地址空间（RFC 6598）——`is_private` 返回
#:     False，却在云 / k8s 常作内网 / Pod 地址（AWS EKS 及部分 CNI 用此段），
#:     是本次批次 7c 补的**主缺口**：literal / DNS rebinding / 302 落点三路径均曾穿过。
#:   - 192.88.99.0/24 6to4 relay anycast（RFC 7526，已废弃）——`is_private` False 且
#:     `is_global` True，标准库两个口径都判不出。
#:   - 192.31.196.0/24 AS112-v4（RFC 7535）/ 192.52.193.0/24 AMT（RFC 7450）/
#:     192.175.48.0/24 AS112 直接委派（RFC 7534）——与 6to4 同构的 IANA 特殊用途
#:     /24：都不是正常 API 目标，一并按同一判据补齐，堵住整类而非只堵一个洞。
SPECIAL_USE_NETWORKS = (
    (ipaddress.ip_network("100.64.0.0/10"), "运营商级 NAT / 共享地址空间（RFC 6598）"),
    (ipaddress.ip_network("192.88.99.0/24"), "6to4 relay anycast（RFC 7526，已废弃）"),
    (ipaddress.ip_network("192.31.196.0/24"), "AS112-v4（RFC 7535）"),
    (ipaddress.ip_network("192.52.193.0/24"), "AMT（RFC 7450）"),
    (ipaddress.ip_network("192.175.48.0/24"), "AS112 直接委派（RFC 7534）"),
)

#: 超时上限（秒）。调用方给的值在这里夹取，不许无限等——
#: 一个挂住的出站请求会把 MCP 工具调用一起挂住。
MAX_TIMEOUT_S = 20
DEFAULT_TIMEOUT_S = 10

Resolver = Callable[[str, int], Iterable[str]]


def clamp_timeout(value: Any) -> int:
    """把调用方给的超时夹进 [1, MAX_TIMEOUT_S]，非数字回默认值。"""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    return max(1, min(MAX_TIMEOUT_S, seconds))


def _reject(code: str, reason: str, **rest: Any) -> dict[str, Any]:
    return {"allowed": False, "code": code, "reason": reason, **rest}


def classify_address(text: Any) -> str:
    """单个 IP 字面量的网段判定。返回**拒绝理由**；空串表示这个地址可以去。

    v4-mapped（`::ffff:10.0.0.1`）与 v4-compatible 先还原成 v4 再判——
    否则 `ipaddress` 只会说它是个普通 v6 地址，而它连的是内网。
    """
    raw = str(text or "").strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    if not raw:
        return "目标地址为空"
    try:
        addr: Any = ipaddress.ip_address(raw)
    except ValueError:
        return f"目标地址不是合法 IP：{raw}"

    if addr.version == 6:
        mapped = getattr(addr, "ipv4_mapped", None) or getattr(addr, "sixtofour", None)
        if mapped is None:
            try:  # ::a.b.c.d（v4-compatible，已废弃但仍能路由到内网）
                if int(addr) >> 32 == 0 and int(addr) & 0xFFFFFFFF:
                    mapped = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
            except ValueError:
                mapped = None
        if mapped is not None:
            inner = classify_address(str(mapped))
            return f"{inner}（IPv6 映射写法 {raw}）" if inner else ""

    if str(addr) in METADATA_ADDRESSES or raw in METADATA_ADDRESSES:
        return f"云实例元数据地址不可访问：{addr}"
    if addr.is_loopback:
        return f"环回地址不可访问：{addr}"
    if addr.is_link_local:
        return f"链路本地地址不可访问（含云元数据网段）：{addr}"
    if addr.is_private:
        return f"私有网段地址不可访问：{addr}"
    if addr.is_multicast:
        return f"组播地址不可访问：{addr}"
    if addr.is_reserved or addr.is_unspecified:
        return f"保留地址不可访问：{addr}"
    for network, label in SPECIAL_USE_NETWORKS:
        if addr in network:  # 跨版本 __contains__ 安全返回 False，无需版本守卫
            return f"特殊用途网段不可访问（{label}）：{addr}"
    return ""


def is_ip_literal(text: Any) -> bool:
    """host 字面本身就是个 IP 吗（含 `[::1]` 方括号写法）。域名返回 False。"""
    raw = str(text or "").strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        return False
    return True


def check_url_shape(url: Any) -> dict[str, Any]:
    """只看 URL 字面：协议、host 存在性、端口白名单、内嵌凭据。**不做 DNS**。

    成功返回 `{allowed: True, scheme, host, port, url}`；`url` 是归一化后的目标。
    """
    raw = str(url or "").strip()
    if not raw:
        return _reject("E_EGRESS_URL_EMPTY", "目标地址为空")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return _reject("E_EGRESS_URL_MALFORMED", "目标地址格式非法（端口不是数字？）")

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        return _reject(
            "E_EGRESS_SCHEME",
            f"只允许 http/https，收到 {scheme or '（无协议）'}——"
            "file/gopher/ftp 等协议一律不发",
        )
    if parts.username is not None or parts.password is not None:
        return _reject("E_EGRESS_URL_USERINFO", "目标地址不能内嵌用户名或密码")
    host = (parts.hostname or "").strip().lower()
    if not host:
        return _reject("E_EGRESS_URL_MALFORMED", "目标地址里解析不出 host")
    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    if effective_port not in ALLOWED_PORTS:
        return _reject(
            "E_EGRESS_PORT",
            f"端口 {effective_port} 不在白名单 {sorted(ALLOWED_PORTS)} 内",
            host=host,
            port=effective_port,
        )
    return {
        "allowed": True,
        "code": "OK",
        "scheme": scheme,
        "host": host,
        "port": effective_port,
        "url": raw,
    }


def _default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [str(info[4][0]) for info in infos]


def check_target(url: Any, *, resolver: Resolver | None = None) -> dict[str, Any]:
    """出站目标总闸：字面形态 → DNS 解析 → 每个解析结果过网段判定。

    `resolver` 只为可测性存在（注入一个假解析器就能覆盖 rebinding 形态），
    生产调用一律走默认的 `socket.getaddrinfo`。

    **解析出的地址有任意一个落在禁网段就整体拒绝**，不做"挑一个能用的去连"——
    那等于把选择权交给攻击者控制的 DNS。
    """
    shape = check_url_shape(url)
    if not shape["allowed"]:
        return shape

    host, port = shape["host"], shape["port"]

    if is_ip_literal(host):
        literal = classify_address(host)
        if literal:
            return _reject("E_EGRESS_BLOCKED_NET", literal, host=host, port=port)

    resolve = resolver or _default_resolver
    try:
        addresses = [str(a) for a in resolve(host, port) if str(a).strip()]
    except OSError as exc:
        return _reject(
            "E_EGRESS_DNS",
            f"域名解析失败：{host}（{exc.__class__.__name__}）——"
            "这是通道问题，不是被测系统的业务结论",
            host=host,
            port=port,
        )
    if not addresses:
        return _reject("E_EGRESS_DNS", f"域名解析不出任何地址：{host}", host=host, port=port)

    for address in addresses:
        verdict = classify_address(address)
        if verdict:
            return _reject(
                "E_EGRESS_BLOCKED_NET",
                f"{verdict}（{host} 解析到该地址）",
                host=host,
                port=port,
                resolved=addresses,
            )
    return {**shape, "resolved": addresses}


def check_redirect(location: Any, *, resolver: Resolver | None = None) -> dict[str, Any]:
    """重定向落点判定。语义与 `check_target` 相同，独立命名是为了让调用点读起来是

        「我拿到 3xx，我**没有**跟，我只是把落点也判了一遍」

    而不是「我顺手跟了一下」。相对 Location（`/next`）无法脱离原请求判定，
    调用方须先补全成绝对 URL 再传进来。
    """
    text = str(location or "").strip()
    if not text:
        return _reject("E_EGRESS_REDIRECT_EMPTY", "3xx 响应没有给 Location")
    if "://" not in text:
        return _reject(
            "E_EGRESS_REDIRECT_RELATIVE",
            f"相对 Location（{text}）须先补全成绝对 URL 再判",
        )
    return check_target(text, resolver=resolver)


__all__ = [
    "ALLOWED_PORTS",
    "ALLOWED_SCHEMES",
    "DEFAULT_TIMEOUT_S",
    "MAX_TIMEOUT_S",
    "METADATA_ADDRESSES",
    "SPECIAL_USE_NETWORKS",
    "check_redirect",
    "check_target",
    "check_url_shape",
    "clamp_timeout",
    "classify_address",
    "is_ip_literal",
]
