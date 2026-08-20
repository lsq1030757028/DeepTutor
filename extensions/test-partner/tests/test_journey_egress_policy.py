# -*- coding: utf-8 -*-
"""egress_policy 单测（批次 7 / R6 先行阻断项）。

**这套测试先于任何出站代码存在**：自由能力面的目标由用户当场给，策略不绿就不许接线。
覆盖面按 R6 的五条逐条对：协议、网段（v4 + v6 + mapped + 元数据）、端口白名单、
重定向落点重判、DNS 解析后重校（rebinding 形态用注入解析器造）。
"""
from __future__ import annotations

import pytest

from server.journey import egress_policy as ep


def _resolver(mapping: dict[str, list[str]]):
    """假解析器：域名 → 地址列表。没配的域名当解析失败。"""
    def resolve(host: str, port: int) -> list[str]:
        if host not in mapping:
            raise OSError("NXDOMAIN")
        return mapping[host]
    return resolve


_PUBLIC = _resolver({"api.example.com": ["93.184.216.34"]})


# ── 策略 1：协议 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://127.0.0.1:11211/_stats",
    "ftp://example.com/x",
    "//example.com/x",
    "example.com/x",
])
def test_only_http_schemes_pass(url):
    verdict = ep.check_url_shape(url)
    assert verdict["allowed"] is False
    assert verdict["code"] == "E_EGRESS_SCHEME"


def test_http_and_https_shapes_pass():
    for url in ("http://api.example.com/v1/ping", "https://api.example.com/v1/ping"):
        assert ep.check_url_shape(url)["allowed"] is True


def test_embedded_credentials_are_refused():
    verdict = ep.check_url_shape("https://user:pass@api.example.com/v1")
    assert verdict["code"] == "E_EGRESS_URL_USERINFO"


# ── 策略 2：网段（IPv4） ──────────────────────────────────────────────────
@pytest.mark.parametrize("address", [
    "127.0.0.1", "127.1.2.3", "0.0.0.0",
    "10.0.0.5", "192.168.1.1", "172.16.3.4",
    "169.254.1.1", "169.254.169.254", "100.100.100.200",
    "224.0.0.1", "240.0.0.1",
])
def test_blocked_v4_networks(address):
    assert ep.classify_address(address), f"{address} 应被拒但放行了"


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "1.1.1.1"])
def test_public_v4_passes(address):
    assert ep.classify_address(address) == ""


# ── 策略 2：RFC 特殊 / 保留网段全表（治本：漏某个特殊段这里就红） ────────────
# 这一族是「`is_private` 判 False 但非公网可路由」的整类缝——Python 的布尔属性
# 各有盲区（CGNAT 连 is_reserved 都不占，6to4 relay 甚至 is_global=True）。
# 逐段钉住，将来标准库或代码回退漏掉任意一段，对应用例立刻转红。
@pytest.mark.parametrize("address,why", [
    # —— 批次 7c 主缺口：CGNAT / 运营商级 NAT（RFC 6598），云 / k8s 内网段 ——
    ("100.64.0.0", "CGNAT 段首"),
    ("100.64.0.1", "CGNAT 典型 Pod 地址"),
    ("100.100.100.100", "CGNAT 段内"),
    ("100.127.255.255", "CGNAT 段尾"),
    # —— 6to4 relay anycast（RFC 7526，已废弃）：is_private=False 且 is_global=True ——
    ("192.88.99.0", "6to4 relay 段首"),
    ("192.88.99.1", "6to4 relay 典型"),
    ("192.88.99.255", "6to4 relay 段尾"),
    # —— 同构 IANA 特殊用途 /24：一并堵住整类 ——
    ("192.31.196.1", "AS112-v4（RFC 7535）"),
    ("192.52.193.1", "AMT（RFC 7450）"),
    ("192.175.48.1", "AS112 直接委派（RFC 7534）"),
    # —— 以下几类 Python 布尔属性已覆盖，但同属「非私网 API 判定」大类，一并钉住防回退 ——
    ("198.18.0.1", "benchmarking（RFC 2544）"),
    ("198.19.255.255", "benchmarking 段尾"),
    ("240.0.0.1", "保留段 240/4（RFC 1112）"),
    ("255.255.255.255", "受限广播"),
    ("0.0.0.0", "本网络 / 未指定"),
    ("192.0.0.1", "IETF 协议分配（RFC 6890）"),
    ("192.0.2.1", "TEST-NET-1（RFC 5737）"),
    ("198.51.100.1", "TEST-NET-2（RFC 5737）"),
    ("203.0.113.1", "TEST-NET-3（RFC 5737）"),
    ("::", "IPv6 未指定"),
    ("2001:db8::1", "IPv6 文档段（RFC 3849）"),
    ("2001:2::1", "IPv6 benchmarking（RFC 5180）"),
])
def test_blocked_special_use_networks(address, why):
    assert ep.classify_address(address), f"{address}（{why}）应被拒但放行了"


@pytest.mark.parametrize("address", [
    # CGNAT 段的紧邻公网边界——补齐后不许误伤真正的公网地址
    "100.63.255.255",   # CGNAT 段前一个地址
    "100.128.0.0",      # CGNAT 段后一个地址
    "192.88.98.255",    # 6to4 relay 段前
    "192.88.100.0",     # 6to4 relay 段后
])
def test_special_use_neighbours_still_public(address):
    assert ep.classify_address(address) == "", f"{address} 是公网地址，不该被误拦"


# ── 策略 2：网段（IPv6 等价段 + v4-mapped） ───────────────────────────────
@pytest.mark.parametrize("address", [
    "::1",                     # 环回
    "fe80::1",                 # 链路本地
    "fc00::1", "fd12:3456::1", # 唯一本地（私网等价）
    "::",                      # 未指定
    "ff02::1",                 # 组播
    "fd00:ec2::254",           # AWS IMDSv6
])
def test_blocked_v6_networks(address):
    assert ep.classify_address(address), f"{address} 应被拒但放行了"


@pytest.mark.parametrize("address", [
    "::ffff:127.0.0.1",
    "::ffff:169.254.169.254",
    "::ffff:10.0.0.1",
    "[::ffff:192.168.0.1]",
])
def test_v4_mapped_v6_is_unwrapped_before_judging(address):
    """只拦 v4 等于没拦：mapped 写法必须还原成 v4 再判。"""
    verdict = ep.classify_address(address)
    assert verdict, f"{address} 是内网地址的 v6 写法，应被拒"
    assert "IPv6 映射写法" in verdict


def test_public_v6_passes():
    assert ep.classify_address("2606:4700:4700::1111") == ""


def test_ip_literal_host_is_blocked_without_dns():
    """host 本身是内网 IP 时，一次 DNS 都不该发就已经被拒。"""
    def exploding_resolver(host, port):
        raise AssertionError("IP 字面量不该走解析")

    verdict = ep.check_target("http://127.0.0.1:8080/admin", resolver=exploding_resolver)
    assert verdict["allowed"] is False and verdict["code"] == "E_EGRESS_BLOCKED_NET"


# ── 策略 3：端口白名单 ────────────────────────────────────────────────────
@pytest.mark.parametrize("port", [22, 3306, 6379, 5432, 11211, 3789, 3790, 9200])
def test_ports_outside_whitelist_are_refused(port):
    verdict = ep.check_url_shape(f"http://api.example.com:{port}/x")
    assert verdict["code"] == "E_EGRESS_PORT"


@pytest.mark.parametrize("url", [
    "http://api.example.com/x",            # 隐式 80
    "https://api.example.com/x",           # 隐式 443
    "http://api.example.com:8080/x",
    "https://api.example.com:8443/x",
])
def test_whitelisted_ports_pass(url):
    assert ep.check_url_shape(url)["allowed"] is True


def test_malformed_port_does_not_raise():
    verdict = ep.check_url_shape("http://api.example.com:notaport/x")
    assert verdict["allowed"] is False and verdict["code"] == "E_EGRESS_URL_MALFORMED"


# ── 策略 5：DNS 解析后重校（rebinding 形态） ──────────────────────────────
def test_domain_resolving_to_loopback_is_refused():
    """字面 host 无辜、解析结果是环回——只看字面的策略会整条穿过去。"""
    verdict = ep.check_target(
        "https://evil.example.com/x",
        resolver=_resolver({"evil.example.com": ["127.0.0.1"]}),
    )
    assert verdict["allowed"] is False and verdict["code"] == "E_EGRESS_BLOCKED_NET"


def test_domain_resolving_to_metadata_is_refused():
    verdict = ep.check_target(
        "http://meta.example.com/latest/meta-data/",
        resolver=_resolver({"meta.example.com": ["169.254.169.254"]}),
    )
    assert verdict["allowed"] is False
    assert "元数据" in verdict["reason"]


def test_any_bad_address_in_the_answer_set_refuses_the_whole_target():
    """多解析结果里混一个内网地址就整体拒——不许挑一个能用的去连。"""
    verdict = ep.check_target(
        "https://mixed.example.com/x",
        resolver=_resolver({"mixed.example.com": ["93.184.216.34", "10.1.2.3"]}),
    )
    assert verdict["allowed"] is False and verdict["code"] == "E_EGRESS_BLOCKED_NET"


@pytest.mark.parametrize("addr,seg", [
    ("100.64.0.1", "CGNAT / RFC 6598"),
    ("192.88.99.1", "6to4 relay / RFC 7526"),
])
def test_special_use_segment_blocked_on_all_three_paths(addr, seg):
    """批次 7c 主缺口回归：CGNAT（及 6to4 relay）经 literal / DNS 解析 / 302 落点
    三条路径都必须被拒——三路径都汇聚到 `classify_address` 这个解析后统一校验点。"""
    # 路径 1：URL 字面就是该段 IP（DNS 都不该发）
    def exploding(host, port):
        raise AssertionError("IP 字面量不该走解析")
    literal = ep.check_target(f"http://{addr}/x", resolver=exploding)
    assert literal["allowed"] is False and literal["code"] == "E_EGRESS_BLOCKED_NET"

    # 路径 2：无辜域名 DNS rebinding 解析到该段
    rebind = ep.check_target(
        "https://evil.example.com/x",
        resolver=_resolver({"evil.example.com": [addr]}),
    )
    assert rebind["allowed"] is False and rebind["code"] == "E_EGRESS_BLOCKED_NET"

    # 路径 3：302 落点是该段（调用方把 Location 重新丢进闸）
    landing = ep.check_redirect(f"http://{addr}/x", resolver=exploding)
    assert landing["allowed"] is False and landing["code"] == "E_EGRESS_BLOCKED_NET"


def test_v6_answer_is_judged_too():
    verdict = ep.check_target(
        "https://six.example.com/x",
        resolver=_resolver({"six.example.com": ["::1"]}),
    )
    assert verdict["allowed"] is False and verdict["code"] == "E_EGRESS_BLOCKED_NET"


def test_resolution_failure_is_a_channel_error_not_a_business_answer():
    verdict = ep.check_target("https://nope.example.com/x", resolver=_resolver({}))
    assert verdict["code"] == "E_EGRESS_DNS"
    assert "通道问题" in verdict["reason"]


def test_public_target_passes_and_reports_resolution():
    verdict = ep.check_target("https://api.example.com/v1/ping", resolver=_PUBLIC)
    assert verdict["allowed"] is True
    assert verdict["host"] == "api.example.com" and verdict["port"] == 443
    assert verdict["resolved"] == ["93.184.216.34"]


# ── 策略 4：重定向落点重判 ────────────────────────────────────────────────
def test_redirect_landing_goes_through_the_same_policy():
    verdict = ep.check_redirect("http://169.254.169.254/latest/meta-data/",
                                resolver=_PUBLIC)
    assert verdict["allowed"] is False and verdict["code"] == "E_EGRESS_BLOCKED_NET"


def test_redirect_to_public_target_is_allowed_to_be_offered():
    verdict = ep.check_redirect("https://api.example.com/v2/ping", resolver=_PUBLIC)
    assert verdict["allowed"] is True


def test_relative_redirect_must_be_absolutized_by_the_caller():
    verdict = ep.check_redirect("/v2/ping")
    assert verdict["code"] == "E_EGRESS_REDIRECT_RELATIVE"


def test_empty_redirect_is_reported_not_swallowed():
    assert ep.check_redirect("")["code"] == "E_EGRESS_REDIRECT_EMPTY"


# ── 超时夹取 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("given,expected", [
    (None, ep.DEFAULT_TIMEOUT_S), ("", ep.DEFAULT_TIMEOUT_S), ("abc", ep.DEFAULT_TIMEOUT_S),
    (0, 1), (-5, 1), (3, 3), (999, ep.MAX_TIMEOUT_S),
])
def test_timeout_is_clamped(given, expected):
    assert ep.clamp_timeout(given) == expected


# ── 单源纪律：策略不许在 redlines 里长出第二份 ────────────────────────────
def test_policy_does_not_touch_redlines_module():
    """redlines 被逐字内嵌进 bundle，改它要重编译存量 bundle——所以本策略是新文件。"""
    from server.journey import redlines

    source = (redlines.__doc__ or "") + "".join(dir(redlines))
    assert "egress" not in source.lower(), "出网策略混进了 redlines：bundle 会被牵连重编译"
