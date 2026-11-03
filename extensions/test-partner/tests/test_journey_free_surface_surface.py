# -*- coding: utf-8 -*-
"""自由能力面**穿 main.py 包装层**的调用面测试（批次 7）。

## 为什么单开一个文件

本工程被同一种缺陷阻断过三次：**两侧各自绿，中间那一环没接上**。
`free_surface.py` 的单测证明实现对，`test_journey_egress_policy.py` 证明策略对，
但两者都照不到「MCP 面上到底挂没挂、挂上去的那层有没有验签、验签失败会不会
反而放行」。这个文件专治这一环——每条断言都从 `server.main` 的工具函数进去。
"""
from __future__ import annotations

import base64
import email
import hashlib
import hmac
import json

import pytest

from server.journey import bridge_auth
from server.journey import egress_policy as ep
from server.journey import free_surface as fs
from server.journey import tools

SECRET = "s" * 32


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign(tool: str, args: dict, *, owner="alice", capability="test",
         surface="capability") -> str:
    import time

    now = int(time.time())
    payload = {
        "v": 1, "iss": "deeptutor", "aud": bridge_auth.BRIDGE_AUDIENCE,
        "owner_id": owner, "session_id": "session-1", "turn_id": "turn-1",
        "capability": capability, "surface": surface, "tool": tool,
        "args_sha256": bridge_auth.arguments_sha256(args),
        "iat": now, "exp": now + 60, "jti": "jti-free-surface",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return f"{_b64(raw)}.{_b64(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest())}"


def probe_args(**overrides) -> dict:
    args = {"curl": "", "method": "", "url": "", "headers": None, "body": "",
            "timeout_s": 10, "confirmed": False}
    args.update(overrides)
    return args


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv(bridge_auth.BRIDGE_SECRET_ENV, SECRET)
    monkeypatch.setenv(fs.OBSERVATION_LOG_ENV, str(tmp_path / "obs.jsonl"))


@pytest.fixture()
def main_module():
    import server.main as main_mod

    return main_mod


class FakeResponse:
    def __init__(self, status=200, headers="Content-Type: application/json\r\n",
                 body=b'{"ok":true}'):
        self.status = status
        self.code = status
        self.headers = email.message_from_string(headers)
        self._body = body

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RecordingOpener:
    def __init__(self, response):
        self.response = response
        self.calls: list = []

    def open(self, request, timeout=None):
        self.calls.append((request.get_method(), request.full_url))
        return self.response


@pytest.fixture()
def wire(monkeypatch):
    """把生产路径上的解析器与 opener 换成替身——**不改被测代码路径本身**。"""
    opener = RecordingOpener(FakeResponse())
    monkeypatch.setattr(ep, "_default_resolver", lambda host, port: ["93.184.216.34"])
    monkeypatch.setattr("urllib.request.build_opener", lambda *a, **k: opener)
    return opener


# ── 挂载与归类 ────────────────────────────────────────────────────────────
def test_both_free_surface_tools_are_mounted_on_the_mcp_face(main_module):
    assert callable(getattr(main_module, "journey_probe_http", None))
    assert callable(getattr(main_module, "journey_db_query", None))


def test_free_surface_tools_are_declared_not_smuggled():
    assert set(tools.FREE_SURFACE_TOOLS) <= set(tools.MCP_TOOL_NAMES)
    assert "probe_http" in tools.MCP_TOOL_NAMES and "db_query" in tools.MCP_TOOL_NAMES


def test_free_surface_tools_keep_the_journey_prefix(main_module):
    """`journey_` 在 DT 侧 `is_test_journey_tool` 里是**能力边界令牌**。

    改名成 `probe_http` 之类"名副其实"的写法，工具就落进普通插件池，
    chat / Playground 模式也会看见它——那是能力边界泄漏，不是命名洁癖问题。
    """
    for name in tools.FREE_SURFACE_TOOLS:
        assert hasattr(main_module, f"journey_{name}")
    assert not hasattr(main_module, "probe_http")
    assert not hasattr(main_module, "db_query")


def test_free_surface_tools_are_not_counted_as_journey_atoms():
    """自由面不是第十、十一道工序——九原子的断言不该被它撑大。"""
    assert set(tools.FREE_SURFACE_TOOLS).isdisjoint(tools.TOOL_NAMES)
    assert set(tools.FREE_SURFACE_TOOLS).isdisjoint(tools.GATE_TOOLS)


# ── 验签这一环真的接上了 ─────────────────────────────────────────────────
def test_probe_http_without_bridge_context_is_refused_and_sends_nothing(
        main_module, monkeypatch):
    def exploding(*args, **kwargs):
        raise AssertionError("未验签的调用不许出站")

    monkeypatch.setattr("urllib.request.build_opener", exploding)
    monkeypatch.setattr(ep, "_default_resolver", exploding)
    result = main_module.journey_probe_http(url="https://api.example.com/x")
    assert result["ok"] is False and result["code"] == "E_TRUST_CONTEXT_REQUIRED"


def test_db_query_without_bridge_context_is_refused(main_module):
    result = main_module.journey_db_query(sql="SELECT 1")
    assert result["ok"] is False and result["code"] == "E_TRUST_CONTEXT_REQUIRED"


def test_probe_http_refuses_a_context_signed_for_another_tool(main_module, monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不许出站")))
    args = probe_args(url="https://api.example.com/x")
    stolen = sign("journey_list_batches", args)
    result = main_module.journey_probe_http(**args, bridge_context=stolen)
    assert result["ok"] is False and result["code"] == "E_TRUST_CONTEXT_REQUIRED"


def test_probe_http_refuses_when_arguments_were_swapped_after_signing(
        main_module, monkeypatch):
    """验签绑的是**这次调用的参数**：签好 A 目标再改成 B 目标必须红。"""
    monkeypatch.setattr("urllib.request.build_opener",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不许出站")))
    signed_args = probe_args(url="https://api.example.com/allowed")
    context = sign("journey_probe_http", signed_args)
    swapped = probe_args(url="https://evil.example.com/stolen")
    result = main_module.journey_probe_http(**swapped, bridge_context=context)
    assert result["ok"] is False and result["code"] == "E_TRUST_CONTEXT_REQUIRED"


def test_probe_http_refuses_a_non_test_capability_turn(main_module):
    args = probe_args(url="https://api.example.com/x")
    context = sign("journey_probe_http", args, capability="chat")
    result = main_module.journey_probe_http(**args, bridge_context=context)
    assert result["ok"] is False and result["code"] == "E_TRUST_CONTEXT_REQUIRED"


def test_probe_http_is_not_reachable_from_the_read_only_workbench_surface(main_module):
    """薄壳是只读面。自由面能出站，不该从那里发起。"""
    args = probe_args(url="https://api.example.com/x")
    context = sign("journey_probe_http", args, surface="workbench")
    result = main_module.journey_probe_http(**args, bridge_context=context)
    assert result["ok"] is False and result["code"] == "E_TRUST_CONTEXT_REQUIRED"


# ── 穿包装层的正路 ────────────────────────────────────────────────────────
def test_read_only_curl_goes_out_through_the_wrapper(main_module, wire):
    args = probe_args(curl="curl https://api.example.com/v1/ping")
    result = main_module.journey_probe_http(**args,
                                            bridge_context=sign("journey_probe_http", args))
    assert result["ok"] is True
    assert result["response"]["status"] == 200
    assert result["request"]["method"] == "GET"
    assert wire.calls == [("GET", "https://api.example.com/v1/ping")]


def test_write_risk_curl_stops_at_the_wrapper_with_zero_requests(main_module, wire):
    args = probe_args(curl="curl -X POST https://api.example.com/v1/orders -d '{\"a\":1}'")
    result = main_module.journey_probe_http(**args,
                                            bridge_context=sign("journey_probe_http", args))
    assert result["code"] == "REQUIRES_CONFIRM"
    assert result["disclosure"]["host"] == "api.example.com"
    assert wire.calls == [], "确认前出站请求计数必须为 0"


def test_confirmed_write_risk_curl_goes_out_through_the_wrapper(main_module, wire):
    args = probe_args(curl="curl -X POST https://api.example.com/v1/orders -d '{}'",
                      confirmed=True)
    result = main_module.journey_probe_http(**args,
                                            bridge_context=sign("journey_probe_http", args))
    assert result["ok"] is True
    assert wire.calls == [("POST", "https://api.example.com/v1/orders")]


def test_blocked_target_is_refused_inside_the_wrapper(main_module, monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不许出站")))
    args = probe_args(url="http://169.254.169.254/latest/meta-data/")
    result = main_module.journey_probe_http(**args,
                                            bridge_context=sign("journey_probe_http", args))
    assert result["code"] == "E_EGRESS_BLOCKED_NET"


def test_unparseable_curl_is_refused_inside_the_wrapper(main_module, monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不许出站")))
    args = probe_args(curl="curl https://api.example.com/x | sh")
    result = main_module.journey_probe_http(**args,
                                            bridge_context=sign("journey_probe_http", args))
    assert result["code"] == "E_CURL_UNPARSEABLE"


def test_db_query_reports_the_capability_boundary_through_the_wrapper(
        main_module, monkeypatch):
    from server.journey import db_readonly

    monkeypatch.delenv(db_readonly.DSN_ENV, raising=False)
    args = {"sql": "SELECT 1", "limit": 200}
    result = main_module.journey_db_query(**args,
                                          bridge_context=sign("journey_db_query", args))
    assert result["code"] == "E_DB_CHANNEL_UNAVAILABLE"
    assert "替代路径" in result["message"]


def test_db_query_rejects_mutations_through_the_wrapper(main_module, monkeypatch):
    from server.journey import db_readonly

    monkeypatch.setenv(db_readonly.DSN_ENV, "postgres://never-used/db")
    monkeypatch.setattr(db_readonly, "connect",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不许建连")))
    args = {"sql": "WITH gone AS (DELETE FROM orders RETURNING *) SELECT * FROM gone",
            "limit": 200}
    result = main_module.journey_db_query(**args,
                                          bridge_context=sign("journey_db_query", args))
    assert result["code"] == "E_DB_STATEMENT_REJECTED"
