# -*- coding: utf-8 -*-
"""redlines 单源自测 + 与 execute.py 既有红线的防漂对拍（红线不许因拓扑改写静默失效）。"""
import pytest

from server import execute
from server.journey import redlines as rl


# ── 红线 1：跨 host 等价类（字面地址一致，零映射层）───────────────────────

@pytest.mark.parametrize("base,url,ok", [
    ("http://127.0.0.1:8047", "http://127.0.0.1:8047/mgr/sign.html", True),
    ("http://127.0.0.1:8047", "http://127.0.0.1/mgr", False),          # 端口不同
    ("http://127.0.0.1:8047", "http://localhost:8047/", False),        # 字面不同,无别名映射
    # 显式同端口跨 scheme：host_key 相同 → 同类（沿用 execute._host_key 语义，见防漂对拍）
    ("http://127.0.0.1:8047", "https://127.0.0.1:8047/", True),
    ("http://127.0.0.1", "https://127.0.0.1/", False),                 # 缺省端口 80≠443
    ("http://127.0.0.1", "http://127.0.0.1:80/x", True),               # http 补 80
    ("https://a.com", "https://a.com:443/y", True),                    # https 补 443
    ("http://127.0.0.1:8047", "http://evil.example.com/", False),
    ("http://127.0.0.1:8047", "", False),
    ("", "http://127.0.0.1:8047", False),
])
def test_same_host_literal_equivalence(base, url, ok):
    assert rl.same_host(base, url) is ok


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8047/a/b?c=d", "https://x.example.com/", "http://h",
    "http://h:99999/bad", "not-a-url", "", "ftp://h/x",
])
def test_host_key_parity_with_execute(url):
    # 防漂对拍：journey 单源与 execute.py 既有 `_host_key` 语义逐字一致
    assert rl.host_key(url) == execute._host_key(url)


def test_navigation_and_route_policy():
    base = "http://127.0.0.1:8047"
    assert rl.navigation_allowed(base, base + "/mgr/")["allowed"]
    denied = rl.navigation_allowed(base, "http://tracker.example.com/x")
    assert not denied["allowed"] and "等价类" in denied["reason"]
    assert rl.route_decision(base, base + "/api/x") == "continue"
    assert rl.route_decision(base, "https://cdn.example.com/lib.js") == "abort"


@pytest.mark.parametrize("url", [
    "https://user:secret@example.com",
    "https://example.com/path?token=secret",
    "https://example.com/path#secret",
    "ftp://example.com/file",
])
def test_persisted_target_url_rejects_secret_bearing_or_non_http_shapes(url):
    result = rl.safe_target_url(url)
    assert result["ok"] is False
    assert "secret" not in result["error"]


def test_persisted_target_url_normalizes_default_port_and_trailing_slash():
    assert rl.safe_target_url("HTTPS://Example.COM:443/app/") == {
        "ok": True, "url": "https://example.com/app",
    }


# ── 红线 2：变量残留 ───────────────────────────────────────────────────────

def test_unresolved_vars_and_render():
    text = "Bearer {{token}} to {{ baseUrl }}/x with {{token}}"
    assert rl.unresolved_vars(text) == ["token", "baseUrl"]
    rendered = rl.render(text, {"token": "abc123"})
    assert "abc123" in rendered and rl.unresolved_vars(rendered) == ["baseUrl"]
    assert rl.unresolved_vars(rl.render(text, {"token": "t", "baseUrl": "u"})) == []


# ── 红线 3：凭据零回显（scrub 门）─────────────────────────────────────────

def test_scrub_replaces_long_values_only():
    pairs = rl.scrub_map({"password": "88888888", "pin": "12", "baseUrl": "http://x"})
    # 短值(pin)与 baseUrl 不参与
    assert [p[1] for p in pairs] == ["{{password}}"]
    assert rl.scrub("login with 88888888 done", pairs) == "login with {{password}} done"
    assert rl.scrub(12345, pairs) == 12345


def test_scrub_longest_value_first():
    pairs = rl.scrub_map({"a": "secretvalue", "b": "secretvaluelonger"})
    out = rl.scrub("x secretvaluelonger y", pairs)
    assert out == "x {{b}} y"


def test_scrub_parity_with_execute():
    variables = {"token": "tok_abcdef123456", "login_password": "88888888"}
    pairs_new = rl.scrub_map(variables)
    pairs_old = execute._scrub_map(variables)
    sample = "Authorization: Bearer tok_abcdef123456; pwd=88888888"
    assert rl.scrub(sample, pairs_new) == execute.scrub(sample, pairs_old)
