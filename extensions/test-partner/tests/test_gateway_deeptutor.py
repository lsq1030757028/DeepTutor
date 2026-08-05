"""DeepTutor 注册表客户端的离线测试：条目形状、幂等、开关拨动、错误翻译。

一个真实 HTTP 包都不发：`_request` 被换成一台假 DeepTutor（记录每一次调用）。
这里要钉住的是**写进注册表的字段形状**——它是 2026-08-05 对着现网条目实测抄的，
改坏了表现为"连不上但不知道为什么"。
"""

from __future__ import annotations

import pytest

from server.gateway.config import GatewayConfig
from server.gateway.deeptutor_client import (
    DEFAULT_ENABLED_TOOLS,
    DeepTutorClient,
    DeepTutorError,
)

MCP_URL = "http://host.docker.internal:3795/mcp"


class FakeDeepTutor(DeepTutorClient):
    """把传输层替换掉的客户端：servers/status 就是内存里两个字典。"""

    def __init__(self, config, servers=None, status=None):
        super().__init__(config=config)
        self.servers = dict(servers or {})
        self.status_rows = list(status or [])
        self.calls: list[tuple[str, str, dict | None]] = []

    def _request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET":
            return {"servers": self.servers, "status": self.status_rows}
        if method == "PUT":
            name = path.rsplit("/", 1)[-1]
            self.servers[name] = dict(body or {})
            return {"ok": True}
        if method == "DELETE":
            self.servers.pop(path.rsplit("/", 1)[-1], None)
            return {"ok": True}
        raise AssertionError(method)


def connected(name, tools=("get_stories_or_tasks", "get_stories_fields_info")):
    return {"name": name, "status": "connected",
            "tools": [{"name": t} for t in tools]}


@pytest.fixture()
def cfg(tmp_path):
    return GatewayConfig(str(tmp_path / "config"))


# ── 条目形状 ────────────────────────────────────────────────────────────────

def test_new_entry_has_the_ten_fields_from_production():
    entry = DeepTutorClient.build_tapd_entry(None, MCP_URL)
    assert entry["type"] == "streamableHttp"
    assert entry["url"] == MCP_URL
    assert entry["enabled"] is True
    assert entry["tool_timeout"] == 30
    assert entry["enabled_tools"] == DEFAULT_ENABLED_TOOLS
    # auth / catalog_entry 是实测才发现的两个字段，缺了会连不上
    assert "auth" in entry and "catalog_entry" in entry
    assert entry["command"] == "" and entry["args"] == [] and entry["env"] == {}


def test_existing_narrowed_enabled_tools_are_respected():
    existing = {"enabled_tools": ["get_stories_or_tasks"], "auth": "custom",
                "某个新版本才有的字段": 42}
    entry = DeepTutorClient.build_tapd_entry(existing, MCP_URL)
    assert entry["enabled_tools"] == ["get_stories_or_tasks"]
    assert entry["auth"] == "custom"
    # 不认识的字段原样带回去，不因为一次保存被抹掉
    assert entry["某个新版本才有的字段"] == 42


def test_star_enabled_tools_is_reset_to_readonly_defaults():
    entry = DeepTutorClient.build_tapd_entry({"enabled_tools": ["*"]}, MCP_URL)
    assert entry["enabled_tools"] == DEFAULT_ENABLED_TOOLS


def test_custom_enabled_tools_apply_to_new_entries_only():
    entry = DeepTutorClient.build_tapd_entry(None, MCP_URL, ["get_tcases"])
    assert entry["enabled_tools"] == ["get_tcases"]


# ── 注册 ────────────────────────────────────────────────────────────────────

def test_register_creates_entry_and_reads_status_back(cfg):
    client = FakeDeepTutor(cfg, status=[connected("tapd")])
    status = client.register_tapd(MCP_URL)
    assert status["state"] == "connected"
    assert status["tool_count"] == 2
    assert status["created"] is True
    methods = [c[0] for c in client.calls]
    # GET（读现状）→ PUT（单条 upsert）→ GET（回读稳定态）
    assert methods == ["GET", "PUT", "GET"]
    assert client.calls[1][1].endswith("/servers/tapd")


def test_register_is_idempotent_and_marks_update(cfg):
    client = FakeDeepTutor(cfg, servers={"tapd": {"enabled_tools": ["get_tcases"]}},
                           status=[connected("tapd", ("get_tcases",))])
    status = client.register_tapd(MCP_URL)
    assert status["created"] is False
    assert status["enabled_tools"] == ["get_tcases"]


def test_register_only_touches_its_own_entry(cfg):
    other = {"type": "streamableHttp", "url": "http://x/mcp", "enabled": True}
    client = FakeDeepTutor(cfg, servers={"test-partner": dict(other)},
                           status=[connected("tapd")])
    client.register_tapd(MCP_URL)
    assert client.servers["test-partner"] == other


def test_register_reports_not_connected_state(cfg):
    client = FakeDeepTutor(cfg, status=[{"name": "tapd", "status": "error",
                                         "tools": [], "error": "connect timeout"}])
    status = client.register_tapd(MCP_URL)
    assert status["state"] == "error"
    assert status["error"] == "connect timeout"


def test_empty_put_response_is_an_error(cfg):
    class Empty(FakeDeepTutor):
        def _request(self, method, path, body=None):
            if method == "PUT":
                return {}
            return super()._request(method, path, body)

    with pytest.raises(DeepTutorError) as excinfo:
        Empty(cfg).register_tapd(MCP_URL)
    assert excinfo.value.code == "DEEPTUTOR_EMPTY_PUT"


# ── 状态读取 ────────────────────────────────────────────────────────────────

def test_overview_reports_missing_entries(cfg):
    client = FakeDeepTutor(cfg, status=[connected("test-partner", ("ping",) * 6)])
    overview = client.overview(["test-partner", "tapd"])
    assert overview["entries"]["test-partner"]["state"] == "connected"
    assert overview["entries"]["test-partner"]["tool_count"] == 6
    assert overview["entries"]["tapd"]["present"] is False
    assert overview["entries"]["tapd"]["state"] == "missing"


def test_api_base_comes_from_settings(cfg):
    cfg.save_settings({"deeptutor": {"api_base": "http://127.0.0.1:9999/"}})
    assert DeepTutorClient(config=cfg).api_base == "http://127.0.0.1:9999"


# ── 拨开关刷会话 ────────────────────────────────────────────────────────────

def test_toggle_entry_flips_off_then_on(cfg):
    existing = {"type": "streamableHttp", "url": "http://x/mcp", "enabled": True,
                "enabled_tools": ["ping"]}
    client = FakeDeepTutor(cfg, servers={"test-partner": dict(existing)},
                           status=[connected("test-partner", ("ping",))])
    result = client.toggle_entry("test-partner")
    puts = [c for c in client.calls if c[0] == "PUT"]
    assert [p[2]["enabled"] for p in puts] == [False, True]
    # 除了 enabled，别的字段一个都不许动
    assert puts[1][2] == existing
    assert result["state"] == "connected"


def test_toggle_missing_entry_raises(cfg):
    with pytest.raises(DeepTutorError) as excinfo:
        FakeDeepTutor(cfg).toggle_entry("test-partner")
    assert excinfo.value.code == "ENTRY_MISSING"


# ── 错误翻译 ────────────────────────────────────────────────────────────────

def test_unreachable_deeptutor_gets_a_readable_chinese_error(cfg):
    cfg.save_settings({"deeptutor": {"api_base": "http://127.0.0.1:1"}})
    client = DeepTutorClient(config=cfg)
    with pytest.raises(DeepTutorError) as excinfo:
        client.get_settings()
    assert excinfo.value.code == "DEEPTUTOR_UNREACHABLE"
    assert "连不上 DeepTutor" in str(excinfo.value)


def test_admin_token_is_sent_only_when_configured(cfg, monkeypatch):
    sent: dict = {}

    class FakeResponse:
        def read(self):
            return b'{"servers": {}, "status": []}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        sent["headers"] = dict(request.headers)
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    DeepTutorClient(config=cfg).get_settings()
    assert "Authorization" not in sent["headers"]

    cfg.set_secret("DEEPTUTOR_TOKEN", "dt-admin-xyz")
    DeepTutorClient(config=cfg).get_settings()
    assert sent["headers"]["Authorization"] == "Bearer dt-admin-xyz"
