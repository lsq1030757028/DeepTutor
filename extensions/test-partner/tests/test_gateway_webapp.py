"""配置页的端点测试（starlette TestClient）：页面、四步流水、三道安全闸、零凭据外泄。

子服务管家与 DeepTutor 客户端都换成假件——本文件里不起任何进程、不发任何网络包。
真实件各有自己的测试文件（test_gateway_supervisor.py / test_gateway_deeptutor.py）。

**本文件最重要的断言不是"功能对"，是"令牌不出去"**：页面 HTML、每一个 API 响应、
logging 抓到的每一行，都逐个断言不含令牌原值。
"""

from __future__ import annotations

import json
import logging

import pytest
from starlette.testclient import TestClient

from server.gateway.config import GatewayConfig
from server.gateway.deeptutor_client import DeepTutorError
from server.gateway.webapp import (CSRF_COOKIE, CSRF_HEADER, create_app,
                                   embed_requested, host_allowed)

TOKEN = "tapd-pat-9f8e7d6c5b4a39281706abcdef012345"
ENV_TOKEN = "eyJhbGciOiJIUzI1NiJ9.env-secret-value-987654321"
ENV_SESSION = "SESSIONID=8f2c1d4e6a7b9c0d1e2f3a4b5c6d7e8f"

ENV_PAYLOAD = {
    "name": "测试环境",
    "base_url": "https://api-test.example.com",
    "note": "日常测试环境",
    "variables": [{"key": "token", "value": ENV_TOKEN},
                  {"key": "session", "value": ENV_SESSION}],
}


# ── 假件 ────────────────────────────────────────────────────────────────────

class FakeSupervisor:
    def __init__(self, ok=True, code="STARTED", message="TAPD 服务已就绪。"):
        self.ok = ok
        self.code = code
        self.message = message
        self.restarts = 0
        self.running = False
        self.log_tail_value: list[str] = []

    def status(self):
        return {"service": "tapd", "running": self.running, "managed": self.running,
                "pid": 4242 if self.running else None, "port": 3795,
                "exit_code": None, "token_configured": True,
                "log_path": "tapd-start.log"}

    def restart(self):
        self.restarts += 1
        self.running = self.ok
        payload = {"ok": self.ok, "code": self.code, "message": self.message,
                   "status": self.status()}
        if not self.ok and self.log_tail_value:
            payload["log_tail"] = list(self.log_tail_value)
        return payload

    def start(self):
        return self.restart()

    def stop(self):
        self.running = False
        return {"ok": True, "code": "STOPPED", "message": "", "status": self.status()}


class FakeDeepTutor:
    api_base = "http://127.0.0.1:3782"

    def __init__(self, state="connected", raise_on=None):
        self.state = state
        self.raise_on = raise_on
        self.registered: list[dict] = []
        self.toggles: list[str] = []

    def _entry(self, name, tools=2):
        return {"name": name, "present": True, "state": self.state,
                "tool_count": tools, "tools": ["t"] * tools, "error": ""}

    def overview(self, names):
        if self.raise_on == "overview":
            raise DeepTutorError("连不上 DeepTutor（http://127.0.0.1:3782）：拒绝连接",
                                 code="DEEPTUTOR_UNREACHABLE")
        return {"api_base": self.api_base,
                "entries": {name: self._entry(name) for name in names}}

    def register_tapd(self, mcp_url, enabled_tools=None, tool_timeout=30):
        if self.raise_on == "register":
            raise DeepTutorError("DeepTutor 拒绝了这次调用（HTTP 403）。",
                                 code="DEEPTUTOR_FORBIDDEN")
        self.registered.append({"mcp_url": mcp_url, "enabled_tools": enabled_tools})
        entry = self._entry("tapd")
        entry["created"] = True
        entry["enabled_tools"] = list(enabled_tools or [])
        return entry

    def toggle_entry(self, name):
        if self.raise_on == "toggle":
            raise DeepTutorError("注册表里没有这个条目。", code="ENTRY_MISSING")
        self.toggles.append(name)
        return self._entry(name, tools=6)


@pytest.fixture()
def cfg(tmp_path):
    return GatewayConfig(str(tmp_path / "config"))


def build(cfg, supervisor=None, deeptutor=None):
    supervisor = supervisor or FakeSupervisor()
    deeptutor = deeptutor or FakeDeepTutor()
    app = create_app(config=cfg, supervisor=supervisor, deeptutor=deeptutor,
                     version="0.7.0", mcp_host="0.0.0.0", mcp_port=3790,
                     gateway_port=3789)
    # base_url 必须是 localhost：TestClient 默认发 `Host: testserver`，
    # 而 Host 白名单会（正确地）把它 403 掉——这本身就是第 2 道闸在生效的旁证。
    return TestClient(app, base_url="http://localhost:3789"), supervisor, deeptutor


def csrf_of(client) -> dict[str, str]:
    """先 GET 一次页面拿 CSRF token，返回可直接用的请求头。"""
    response = client.get("/")
    assert response.status_code == 200
    return {CSRF_HEADER: client.cookies[CSRF_COOKIE]}


# ── 页面 ────────────────────────────────────────────────────────────────────

def test_index_renders_chinese_page(cfg):
    client, _, _ = build(cfg)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "测试伙伴 · 能力网关" in response.text
    assert "TAPD 访问令牌" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_index_issues_a_samesite_strict_csrf_cookie(cfg):
    client, _, _ = build(cfg)
    response = client.get("/")
    header = response.headers["set-cookie"]
    assert CSRF_COOKIE in header
    assert "SameSite=strict" in header or "samesite=strict" in header.lower()
    assert "HttpOnly" in header


def test_index_shows_unconfigured_state(cfg):
    client, _, _ = build(cfg)
    assert '"configured": false' in client.get("/").text.lower()


def test_index_shows_mask_not_token(cfg):
    cfg.set_secret("TAPD_ACCESS_TOKEN", TOKEN)
    client, _, _ = build(cfg)
    body = client.get("/").text
    assert TOKEN not in body
    assert TOKEN[:12] not in body
    assert "****" in body                      # 掩码在
    assert str(len(TOKEN)) in body             # 长度提示在


def test_index_has_reserved_sections(cfg):
    body = build(cfg)[0].get("/").text
    assert "数据库连接" in body
    assert "Git 凭据" in body
    assert "预留" in body
    assert "disabled" in body


def test_index_shows_mcp_and_container_urls(cfg):
    body = build(cfg)[0].get("/").text
    assert "host.docker.internal:3790/mcp" in body


# ── 分 tab 结构 ─────────────────────────────────────────────────────────────
# 分区按类型分 tab，不再堆成一个长页面。切换是纯前端的，所有面板都在首屏 HTML 里
# （所以"预留分区"「测试环境」这类断言不受当前选中哪个 tab 影响）。
# 0.7.0 加了第五个：工作台（批次 → 用例 → 执行 → 结果）。

TABS = ["overview", "credentials", "environments", "partner", "workbench"]


@pytest.mark.parametrize("slug,label", list(zip(
    TABS, ["概览", "连接与凭据", "测试环境", "测试伙伴", "工作台"])))
def test_page_has_the_four_type_tabs(cfg, slug, label):
    body = build(cfg)[0].get("/").text
    assert f'data-tab="{slug}"' in body          # tab 按钮在
    assert f'id="tabbtn-{slug}"' in body
    assert f'id="tab-{slug}"' in body            # 对应面板在
    assert label in body


def test_tab_panels_are_all_present_in_one_document(cfg):
    """四个面板一次发全：切 tab 不发请求，填了一半的表单不会因为切走而丢。"""
    body = build(cfg)[0].get("/").text
    assert body.count('class="panel"') == len(TABS)
    assert 'role="tablist"' in body


def test_tab_selection_is_recorded_in_the_url_hash(cfg):
    """#environments 这类锚点要能直达，刷新后还落在同一个 tab。"""
    body = build(cfg)[0].get("/").text
    assert "window.location.hash" in body
    assert "hashchange" in body


# ── 内嵌形态（?embed=1） ────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (None, False), ("1", True), ("", True), ("true", True), ("YES", True),
    ("on", True), ("0", False), ("false", False), ("no", False),
])
def test_embed_requested_table(raw, expected):
    assert embed_requested(raw) is expected


def test_standalone_page_keeps_its_chrome(cfg):
    body = build(cfg)[0].get("/").text
    assert 'class="page-head"' in body
    assert 'data-embed="0"' in body


def test_embed_drops_the_standalone_page_chrome(cfg):
    """大标题那一块是"独立页面才需要"的东西：内嵌时**不发给浏览器**，不是藏起来。"""
    body = build(cfg)[0].get("/?embed=1").text
    assert 'class="page-head"' not in body
    assert "本页只在你这台机器上可访问" not in body
    assert 'data-embed="1"' in body


def test_embed_still_renders_the_whole_content_body(cfg):
    """去掉的只有 chrome：tab 条与四个面板一个都不能少。"""
    body = build(cfg)[0].get("/?embed=1").text
    assert 'role="tablist"' in body
    for slug in TABS:
        assert f'id="tab-{slug}"' in body
    assert "TAPD 访问令牌" in body
    assert "创建/修复测试伙伴" in body


def test_embed_page_still_hides_every_credential(cfg):
    """内嵌是新入口，凭据硬断言跟着走一遍——新形态不是绕过投影的口子。"""
    cfg.set_secret("TAPD_ACCESS_TOKEN", TOKEN)
    cfg.upsert_environment({"name": "测试环境",
                            "base_url": "https://api-test.example.com",
                            "variables": {"token": ENV_TOKEN}})
    body = build(cfg)[0].get("/?embed=1").text
    for value in (TOKEN, ENV_TOKEN):
        assert value not in body
        assert value[:12] not in body
    assert "****" in body


def test_embed_still_issues_a_csrf_cookie(cfg):
    """内嵌不放开任何一道闸：CSRF 双提交照发照验。"""
    client, _, _ = build(cfg)
    response = client.get("/?embed=1")
    assert CSRF_COOKIE in response.headers["set-cookie"]
    assert "samesite=strict" in response.headers["set-cookie"].lower()


def test_embed_does_not_relax_the_host_whitelist(cfg):
    client, _, _ = build(cfg)
    assert client.get("/?embed=1",
                      headers={"Host": "evil.example.com"}).status_code == 403


def test_page_is_framable_by_the_deeptutor_host(cfg):
    """不加 X-Frame-Options：DeepTutor 在 :3782、配置页在 :3789，端口不同即跨源，
    补个 SAMEORIGIN 只会把正常内嵌挡死。clickjacking 由 SameSite=Strict 的
    CSRF cookie 兜底（跨站 iframe 里发不出去，写操作一律 403）。"""
    client, _, _ = build(cfg)
    headers = {k.lower(): v for k, v in client.get("/").headers.items()}
    assert "x-frame-options" not in headers
    assert "frame-ancestors" not in headers.get("content-security-policy", "")


# ── 深浅色自适应 ────────────────────────────────────────────────────────────

def test_page_adapts_to_a_dark_host(cfg):
    """DeepTutor 默认深色，页面写死浅色嵌进去是一块刺眼的白。"""
    body = build(cfg)[0].get("/").text
    assert "@media (prefers-color-scheme: dark)" in body
    assert "color-scheme: light dark" in body


def test_page_has_no_fixed_pixel_width(cfg):
    """DeepTutor 右侧 Viewer 面板宽 400-960px 可拖拽：容器只给 max-width，不给 width。"""
    body = build(cfg)[0].get("/").text
    assert "max-width: 960px" in body
    assert "width: 860px" not in body


# ── DeepTutor iframe sandbox 的硬限制 ───────────────────────────────────────
# 宿主的 iframe 是 sandbox="allow-scripts allow-same-origin allow-forms allow-popups"：
# 没有 allow-modals、没有 allow-downloads，referrerPolicy 还是 no-referrer。
# 下面几条钉的就是"页面不许用被沙箱掐掉的那些能力"。

@pytest.mark.parametrize("banned", ["window.confirm(", "window.alert(",
                                    "window.prompt(", "confirm(\"", "alert(\""])
def test_page_never_calls_a_native_modal(cfg, banned):
    """sandbox 没给 allow-modals：confirm() 被静默阻止（返回 false），
    二次确认会永远点不动，而且一点报错都没有。"""
    body = build(cfg)[0].get("/").text
    script = body[body.index("<script>"):]
    assert banned not in script


def test_delete_confirmation_is_an_inline_ui(cfg):
    """删除环境的二次确认长在页面里，不靠浏览器弹窗。"""
    cfg.upsert_environment({"name": "测试环境",
                            "base_url": "https://api-test.example.com",
                            "variables": {"token": ENV_TOKEN}})
    body = build(cfg)[0].get("/").text
    assert "data-delyes=" in body        # 行内「确认删除」
    assert "data-delno=" in body         # 行内「取消」
    assert 'class="confirm"' in body     # 确认条的样式在


def test_page_offers_copy_instead_of_download(cfg):
    """sandbox 没给 allow-downloads：文件一律给绝对路径 + 一键复制。"""
    body = build(cfg)[0].get("/").text
    assert "navigator.clipboard" in body
    assert 'data-copy="config-dir"' in body
    assert "Ctrl+C" in body              # clipboard 被拒时的回退路径
    # 触发下载的那几种写法一个都不许有（注释里提 download 无所谓）
    assert "createObjectURL" not in body
    assert "download=" not in body
    assert '"download"' not in body


def test_page_never_branches_on_the_referer(cfg):
    """iframe 是 referrerPolicy=no-referrer，这个头压根不来——不许有人依赖它。"""
    body = build(cfg)[0].get("/").text
    assert "referer" not in body.lower().replace('name="referrer"', "")


def test_entry_markdown_link_is_ready_to_paste(cfg):
    """一行可复制的 markdown 入口：贴进聊天或 SOUL，点它就在右侧面板打开本页。"""
    client, _, _ = build(cfg)
    body = client.get("/").text
    assert "[打开测试伙伴工作台](http://127.0.0.1:3789)" in body
    assert 'data-copy="entry-md"' in body
    payload = client.get("/api/status").json()
    assert payload["gateway"]["entry_markdown"] == \
        "[打开测试伙伴工作台](http://127.0.0.1:3789)"


def test_entry_markdown_follows_the_actual_gateway_port(cfg):
    app = create_app(config=cfg, supervisor=FakeSupervisor(), deeptutor=FakeDeepTutor(),
                     gateway_port=3788)
    client = TestClient(app, base_url="http://localhost:3788")
    assert "http://127.0.0.1:3788" in \
        client.get("/api/status").json()["gateway"]["entry_markdown"]


# ── 状态接口 ────────────────────────────────────────────────────────────────

def test_status_shape(cfg):
    client, _, _ = build(cfg)
    payload = client.get("/api/status").json()
    assert payload["gateway"]["version"] == "0.7.0"
    assert payload["gateway"]["mcp_port"] == 3790
    assert payload["tapd"]["port"] == 3795
    assert payload["deeptutor"]["ok"] is True
    assert payload["deeptutor"]["entries"]["test-partner"]["state"] == "connected"
    assert payload["deeptutor"]["entries"]["tapd"]["state"] == "connected"


def test_status_survives_deeptutor_being_down(cfg):
    client, _, _ = build(cfg, deeptutor=FakeDeepTutor(raise_on="overview"))
    payload = client.get("/api/status").json()
    # DeepTutor 挂了配置页照开——不然用户连改配置的入口都没有
    assert payload["deeptutor"]["ok"] is False
    assert payload["deeptutor"]["code"] == "DEEPTUTOR_UNREACHABLE"
    assert payload["tapd"]["port"] == 3795


def test_status_never_contains_the_token(cfg):
    cfg.set_secret("TAPD_ACCESS_TOKEN", TOKEN)
    client, _, _ = build(cfg)
    body = client.get("/api/status").text
    assert TOKEN not in body and TOKEN[:12] not in body


# ── 保存令牌：四步流水 ──────────────────────────────────────────────────────

def test_save_token_runs_all_four_steps(cfg):
    client, supervisor, deeptutor = build(cfg)
    headers = csrf_of(client)
    payload = client.post("/api/tapd/token", json={"token": TOKEN},
                          headers=headers).json()

    assert payload["ok"] is True
    assert [s["ok"] for s in payload["steps"]] == [True, True, True, True]
    names = [s["name"] for s in payload["steps"]]
    assert names == ["保存令牌到本机配置", "重启 TAPD 子服务",
                     "注册进 DeepTutor 部署注册表", "回读连接状态"]
    # 令牌真落盘了，子服务真重启了，DeepTutor 真注册了
    assert cfg.get_secret("TAPD_ACCESS_TOKEN") == TOKEN
    assert supervisor.restarts == 1
    assert deeptutor.registered[0]["mcp_url"] == "http://host.docker.internal:3795/mcp"
    assert deeptutor.registered[0]["enabled_tools"] == [
        "get_stories_or_tasks", "get_stories_fields_info"]


def test_save_token_response_never_echoes_the_token(cfg):
    client, _, _ = build(cfg)
    body = client.post("/api/tapd/token", json={"token": TOKEN},
                       headers=csrf_of(client)).text
    assert TOKEN not in body
    assert TOKEN[:12] not in body


def test_save_token_writes_nothing_to_logs(cfg, caplog):
    client, _, _ = build(cfg)
    with caplog.at_level(logging.DEBUG):
        client.post("/api/tapd/token", json={"token": TOKEN}, headers=csrf_of(client))
    blob = "\n".join(record.getMessage() for record in caplog.records)
    assert TOKEN not in blob
    assert TOKEN[:12] not in blob


def test_empty_token_is_rejected_before_touching_anything(cfg):
    client, supervisor, _ = build(cfg)
    response = client.post("/api/tapd/token", json={"token": "   "},
                           headers=csrf_of(client))
    assert response.status_code == 400
    assert response.json()["code"] == "EMPTY_TOKEN"
    assert supervisor.restarts == 0
    assert cfg.get_secret("TAPD_ACCESS_TOKEN") == ""


def test_save_token_stops_at_the_failing_step_and_reports_diagnosis(cfg):
    supervisor = FakeSupervisor(
        ok=False, code="TOKEN_REJECTED",
        message="TAPD 服务没起来。TAPD 把这个令牌拒了（401/403）。")
    supervisor.log_tail_value = ["HTTPError: 401 Client Error"]
    client, _, deeptutor = build(cfg, supervisor=supervisor)
    payload = client.post("/api/tapd/token", json={"token": TOKEN},
                          headers=csrf_of(client)).json()

    assert payload["ok"] is False
    assert payload["code"] == "TOKEN_REJECTED"
    assert len(payload["steps"]) == 2                 # 到失败那步为止，不假装往下走
    assert payload["steps"][1]["ok"] is False
    assert "401" in payload["steps"][1]["detail"]
    assert payload["steps"][1]["log_tail"]
    assert deeptutor.registered == []                  # 服务没起来就不去注册


def test_save_token_reports_deeptutor_refusal(cfg):
    client, _, _ = build(cfg, deeptutor=FakeDeepTutor(raise_on="register"))
    payload = client.post("/api/tapd/token", json={"token": TOKEN},
                          headers=csrf_of(client)).json()
    assert payload["ok"] is False
    assert payload["code"] == "DEEPTUTOR_FORBIDDEN"
    assert len(payload["steps"]) == 3
    # 令牌还是存下来了：下次点重启不用重贴
    assert cfg.get_secret("TAPD_ACCESS_TOKEN") == TOKEN


def test_save_token_reports_registered_but_not_connected(cfg):
    client, _, _ = build(cfg, deeptutor=FakeDeepTutor(state="error"))
    payload = client.post("/api/tapd/token", json={"token": TOKEN},
                          headers=csrf_of(client)).json()
    assert payload["ok"] is False
    assert payload["code"] == "NOT_CONNECTED"
    assert payload["steps"][3]["ok"] is False
    assert "host.docker.internal" in payload["steps"][3]["detail"]


def test_save_token_rejects_form_encoded_body(cfg):
    """端点只收 JSON：表单编码的体读不出令牌，按"没输入"报错，不会误存。

    早先这里断言的是相反的事（表单也收）。改掉的理由见 `_read_json` 的 docstring：
    兜底要多钉一个 `python-multipart` 运行时依赖，换来的只是 curl 少打一个头。
    """
    client, _, _ = build(cfg)
    response = client.post("/api/tapd/token", data={"token": TOKEN},
                           headers=csrf_of(client))
    assert response.status_code == 400
    assert response.json()["code"] == "EMPTY_TOKEN"
    assert cfg.get_secret("TAPD_ACCESS_TOKEN") == ""


# ── 维护动作 ────────────────────────────────────────────────────────────────

def test_restart_endpoint(cfg):
    client, supervisor, _ = build(cfg)
    payload = client.post("/api/tapd/restart", headers=csrf_of(client)).json()
    assert payload["ok"] is True
    assert supervisor.restarts == 1
    assert payload["state"]["tapd"]["running"] is True


def test_restart_endpoint_reports_failure_with_log_tail(cfg):
    supervisor = FakeSupervisor(ok=False, code="PORT_TAKEN", message="端口被占了。")
    supervisor.log_tail_value = ["WinError 10048"]
    client, _, _ = build(cfg, supervisor=supervisor)
    payload = client.post("/api/tapd/restart", headers=csrf_of(client)).json()
    assert payload["ok"] is False
    assert payload["code"] == "PORT_TAKEN"
    assert payload["log_tail"] == ["WinError 10048"]


def test_refresh_session_toggles_the_self_entry(cfg):
    client, _, deeptutor = build(cfg)
    payload = client.post("/api/refresh-session", headers=csrf_of(client)).json()
    assert payload["ok"] is True
    assert deeptutor.toggles == ["test-partner"]
    assert "关→开" in payload["message"]


def test_refresh_session_reports_missing_entry(cfg):
    client, _, _ = build(cfg, deeptutor=FakeDeepTutor(raise_on="toggle"))
    payload = client.post("/api/refresh-session", headers=csrf_of(client)).json()
    assert payload["ok"] is False
    assert payload["code"] == "ENTRY_MISSING"


# ── 测试环境分区 ────────────────────────────────────────────────────────────

def test_environments_empty_by_default(cfg):
    client, _, _ = build(cfg)
    payload = client.get("/api/environments").json()
    assert payload == {"ok": True, "environments": []}


def test_save_environment_persists_values_locally(cfg):
    client, _, _ = build(cfg)
    payload = client.post("/api/environments/save", json=ENV_PAYLOAD,
                          headers=csrf_of(client)).json()
    assert payload["ok"] is True and payload["code"] == "SAVED"
    # 值真落到本机配置里了（这一路是网关进程内部的，不经聊天）
    stored = cfg.get_environment("测试环境")
    assert stored["variables"] == {"token": ENV_TOKEN, "session": ENV_SESSION}
    assert stored["base_url"] == "https://api-test.example.com"


def test_save_environment_response_never_echoes_a_value(cfg):
    client, _, _ = build(cfg)
    body = client.post("/api/environments/save", json=ENV_PAYLOAD,
                       headers=csrf_of(client)).text
    for value in (ENV_TOKEN, ENV_SESSION):
        assert value not in body
        assert value[:12] not in body
    # 键名与掩码要在，不然页面上认不出配了什么
    assert "token" in body and "session" in body


def test_environments_endpoint_never_echoes_a_value(cfg):
    cfg.upsert_environment({"name": "测试环境",
                            "base_url": "https://api-test.example.com",
                            "variables": {"token": ENV_TOKEN}})
    client, _, _ = build(cfg)
    body = client.get("/api/environments").text
    assert ENV_TOKEN not in body and ENV_TOKEN[:12] not in body
    assert json.loads(body)["environments"][0]["variable_count"] == 1


def test_environment_values_never_reach_the_page_or_status(cfg):
    cfg.upsert_environment({"name": "测试环境",
                            "base_url": "https://api-test.example.com",
                            "variables": {"token": ENV_TOKEN}})
    client, _, _ = build(cfg)
    for body in (client.get("/").text, client.get("/api/status").text):
        assert ENV_TOKEN not in body
        assert ENV_TOKEN[:12] not in body
    assert "测试环境" in client.get("/").text


def test_save_environment_writes_nothing_to_logs(cfg, caplog):
    client, _, _ = build(cfg)
    with caplog.at_level(logging.DEBUG):
        client.post("/api/environments/save", json=ENV_PAYLOAD,
                    headers=csrf_of(client))
    blob = "\n".join(record.getMessage() for record in caplog.records)
    assert ENV_TOKEN not in blob and ENV_TOKEN[:12] not in blob
    assert ENV_SESSION not in blob


def test_editing_keeps_values_when_the_page_submits_blanks(cfg):
    """页面不知道原值，编辑时值输入框必然是空的——留空 = 保留原值。"""
    client, _, _ = build(cfg)
    headers = csrf_of(client)
    client.post("/api/environments/save", json=ENV_PAYLOAD, headers=headers)
    client.post("/api/environments/save", headers=headers, json={
        "name": "测试环境", "base_url": "https://api-test2.example.com",
        "original_name": "测试环境",
        "variables": [{"key": "token", "value": ""},
                      {"key": "session", "value": ""}]})
    stored = cfg.get_environment("测试环境")
    assert stored["base_url"] == "https://api-test2.example.com"
    assert stored["variables"] == {"token": ENV_TOKEN, "session": ENV_SESSION}


def test_editing_replaces_a_value_that_was_actually_typed(cfg):
    client, _, _ = build(cfg)
    headers = csrf_of(client)
    client.post("/api/environments/save", json=ENV_PAYLOAD, headers=headers)
    client.post("/api/environments/save", headers=headers, json={
        "name": "测试环境", "base_url": "https://api-test.example.com",
        "original_name": "测试环境",
        "variables": [{"key": "token", "value": "brand-new-token-0001"},
                      {"key": "session", "value": ""}]})
    stored = cfg.get_environment("测试环境")
    assert stored["variables"]["token"] == "brand-new-token-0001"
    assert stored["variables"]["session"] == ENV_SESSION


def test_dropping_a_row_drops_the_variable(cfg):
    client, _, _ = build(cfg)
    headers = csrf_of(client)
    client.post("/api/environments/save", json=ENV_PAYLOAD, headers=headers)
    client.post("/api/environments/save", headers=headers, json={
        "name": "测试环境", "base_url": "https://api-test.example.com",
        "original_name": "测试环境",
        "variables": [{"key": "token", "value": ""}]})
    assert set(cfg.get_environment("测试环境")["variables"]) == {"token"}


def test_renaming_an_environment_keeps_its_variables(cfg):
    client, _, _ = build(cfg)
    headers = csrf_of(client)
    client.post("/api/environments/save", json=ENV_PAYLOAD, headers=headers)
    client.post("/api/environments/save", headers=headers, json={
        "name": "预发环境", "base_url": "https://api-pre.example.com",
        "original_name": "测试环境",
        "variables": [{"key": "token", "value": ""},
                      {"key": "session", "value": ""}]})
    assert cfg.environment_names() == ["预发环境"]
    assert cfg.get_environment("预发环境")["variables"]["token"] == ENV_TOKEN


@pytest.mark.parametrize("payload,code", [
    ({"name": "", "base_url": "https://a.example.com"}, "ENV_NAME_REQUIRED"),
    ({"name": "x", "base_url": ""}, "ENV_BASE_URL_REQUIRED"),
    ({"name": "x", "base_url": "api.example.com"}, "ENV_BASE_URL_INVALID"),
    ({"name": "x", "base_url": "https://a.example.com",
      "variables": [{"key": "带 空格", "value": "v"}]}, "ENV_VAR_KEY_INVALID"),
])
def test_invalid_environment_is_rejected_with_a_readable_code(cfg, payload, code):
    client, _, _ = build(cfg)
    response = client.post("/api/environments/save", json=payload,
                           headers=csrf_of(client))
    assert response.status_code == 400
    assert response.json()["code"] == code
    assert response.json()["message"]
    assert cfg.load_environments() == []


def test_delete_environment(cfg):
    client, _, _ = build(cfg)
    headers = csrf_of(client)
    client.post("/api/environments/save", json=ENV_PAYLOAD, headers=headers)
    payload = client.post("/api/environments/delete",
                          json={"name": "测试环境"}, headers=headers).json()
    assert payload["ok"] is True and payload["environments"] == []
    assert cfg.load_environments() == []


def test_delete_unknown_environment_is_404_not_a_lie(cfg):
    client, _, _ = build(cfg)
    response = client.post("/api/environments/delete", json={"name": "没这个"},
                           headers=csrf_of(client))
    assert response.status_code == 404
    assert response.json()["code"] == "ENV_NOT_FOUND"


def test_page_has_the_environment_section(cfg):
    cfg.upsert_environment({"name": "测试环境",
                            "base_url": "https://api-test.example.com",
                            "variables": {"token": ENV_TOKEN}})
    body = build(cfg)[0].get("/").text
    assert "测试环境" in body
    assert "新增环境" in body
    assert "https://api-test.example.com" in body
    assert 'type="password" class="v"' in body   # 变量值输入是密码型


# ── 安全闸 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("host,ok", [
    ("localhost:3789", True),
    ("127.0.0.1:3789", True),
    ("localhost", True),
    ("127.0.0.1", True),
    ("[::1]:3789", True),
    ("evil.example.com", False),
    ("evil.example.com:3789", False),
    ("127.0.0.1.nip.io:3789", False),
    ("192.168.1.7:3789", False),
    ("", False),
])
def test_host_allowed_table(host, ok):
    assert host_allowed(host) is ok


def test_foreign_host_header_is_rejected(cfg):
    """DNS rebinding：让 evil.com 解析到 127.0.0.1，浏览器就会带着这个 Host 打进来。"""
    client, supervisor, _ = build(cfg)
    headers = csrf_of(client)
    headers["Host"] = "evil.example.com"
    response = client.post("/api/tapd/token", json={"token": TOKEN}, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "BAD_HOST"
    assert supervisor.restarts == 0
    assert cfg.get_secret("TAPD_ACCESS_TOKEN") == ""


def test_foreign_host_header_cannot_even_read_the_page(cfg):
    client, _, _ = build(cfg)
    response = client.get("/", headers={"Host": "evil.example.com"})
    assert response.status_code == 403


def test_post_without_csrf_is_rejected(cfg):
    client, supervisor, _ = build(cfg)
    client.get("/")   # 拿到 cookie，但故意不带头
    response = client.post("/api/tapd/token", json={"token": TOKEN})
    assert response.status_code == 403
    assert response.json()["code"] == "BAD_CSRF"
    assert supervisor.restarts == 0


def test_post_with_wrong_csrf_is_rejected(cfg):
    client, _, _ = build(cfg)
    client.get("/")
    response = client.post("/api/tapd/restart", headers={CSRF_HEADER: "guessed-by-someone-else"})
    assert response.status_code == 403
    assert response.json()["code"] == "BAD_CSRF"


def test_post_without_cookie_is_rejected(cfg):
    client, _, _ = build(cfg)
    response = client.post("/api/tapd/restart", headers={CSRF_HEADER: "header-without-cookie"})
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/api/tapd/token", "/api/tapd/restart",
                                  "/api/refresh-session",
                                  "/api/environments/save",
                                  "/api/environments/delete"])
def test_every_write_endpoint_is_csrf_guarded(cfg, path):
    client, _, _ = build(cfg)
    client.get("/")
    assert client.post(path, json={}).status_code == 403


@pytest.mark.parametrize("path", ["/api/environments/save",
                                  "/api/environments/delete"])
def test_environment_writes_reject_a_foreign_host_header(cfg, path):
    client, _, _ = build(cfg)
    headers = csrf_of(client)
    headers["Host"] = "evil.example.com"
    response = client.post(path, json=ENV_PAYLOAD, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "BAD_HOST"
    assert cfg.load_environments() == []


def test_environments_read_endpoint_rejects_a_foreign_host_header(cfg):
    client, _, _ = build(cfg)
    assert client.get("/api/environments",
                      headers={"Host": "evil.example.com"}).status_code == 403


def test_csrf_tokens_differ_between_page_loads(cfg):
    client, _, _ = build(cfg)
    first = client.get("/").cookies[CSRF_COOKIE]
    second = client.get("/").cookies[CSRF_COOKIE]
    assert first != second


def test_state_embedded_in_page_is_valid_json_without_secrets(cfg):
    cfg.set_secret("TAPD_ACCESS_TOKEN", TOKEN)
    client, _, _ = build(cfg)
    body = client.get("/").text
    marker = "var state = "
    start = body.index(marker) + len(marker)
    end = body.index("\n", start)
    state = json.loads(body[start:end].rstrip(";"))
    assert state["config"]["secrets"]["TAPD_ACCESS_TOKEN"]["configured"] is True
    assert TOKEN not in json.dumps(state, ensure_ascii=False)


# ── 工作台（0.7.0） ─────────────────────────────────────────────────────────
#
# 端点只做入参闸与 JSON 封装，真执行由 `server/execute.py` 干——所以这里的执行器
# 一律是假件（`tests/test_gateway_workbench.py` 里那个），**一个真实请求都不发**。
# 本段最硬的一条断言在最后：环境变量的值不出现在任何一个工作台响应里。

from tests.test_gateway_workbench import (FakeExecutor, make_batch,  # noqa: E402
                                          make_legacy_batch, wait_until)


@pytest.fixture()
def wb_root(tmp_path, monkeypatch):
    from server import delivery as delivery_module

    base = tmp_path / "deliveries"
    base.mkdir()
    monkeypatch.setattr(delivery_module, "DELIVERIES_DIR", str(base))
    return str(base)


def build_wb(cfg, wb_root, executor=None):
    executor = executor or FakeExecutor()
    app = create_app(config=cfg, supervisor=FakeSupervisor(), deeptutor=FakeDeepTutor(),
                     version="0.7.0", mcp_host="0.0.0.0", mcp_port=3790,
                     gateway_port=3789, deliveries_dir=wb_root, executor=executor)
    return TestClient(app, base_url="http://localhost:3789"), executor


def configured_env(cfg):
    cfg.upsert_environment({"name": "测试环境",
                            "base_url": "https://api-test.example.com",
                            "variables": {"token": ENV_TOKEN, "session": ENV_SESSION}})


# ── 页面结构 ────────────────────────────────────────────────────────────────

def test_workbench_tab_is_the_fifth_one(cfg):
    body = build(cfg)[0].get("/").text
    assert body.index('data-tab="partner"') < body.index('data-tab="workbench"')
    assert "交付批次" in body and "执行选中用例" in body


def test_workbench_has_the_table_skeleton(cfg):
    body = build(cfg)[0].get("/").text
    for marker in ['id="wb-list"', 'id="wb-detail"', 'id="wb-cases"', 'id="wb-env"',
                   'id="wb-confirm"', 'id="wb-progress"', 'id="wb-results"',
                   'id="wb-files"', 'class="tablewrap"']:
        assert marker in body, marker


def test_workbench_offers_bulk_selection(cfg):
    body = build(cfg)[0].get("/").text
    for marker in ['data-pick="all"', 'data-pick="none"', 'data-pick="invert"',
                   'data-pick="executable"', 'id="wb-module-pick"']:
        assert marker in body, marker


def test_the_execute_confirmation_is_an_inline_bar_not_a_native_dialog(cfg):
    """执行是有副作用的动作，点之前要二次确认——但沙箱没给 allow-modals，
    `confirm()` 会被静默阻止（直接返回 false），所以确认条只能长在页面里。"""
    body = build(cfg)[0].get("/").text
    script = body[body.index("<script>"):]
    for banned in ("window.confirm(", "confirm(\"", "window.alert(", "alert(\""):
        assert banned not in script, banned
    assert 'id="btn-wb-go"' in body                 # 行内确认条上的那个按钮
    assert "真实请求" in body and "会真改" in body   # 说清后果，不只说"确定吗"


def test_workbench_gives_paths_not_downloads(cfg):
    """沙箱没给 allow-downloads：产物一律绝对路径 + 复制按钮。"""
    body = build(cfg)[0].get("/").text
    assert "wb-file-" in body                       # 每个产物一行「路径 + 复制」
    assert "createObjectURL" not in body
    assert "download=" not in body


# ── 批次列表 ────────────────────────────────────────────────────────────────

def test_deliveries_endpoint_is_empty_state_not_error(cfg, wb_root):
    client, _ = build_wb(cfg, wb_root)
    payload = client.get("/api/deliveries").json()
    assert payload["ok"] is True and payload["deliveries"] == []
    assert payload["empty_reason"]


def test_deliveries_endpoint_lists_batches(cfg, wb_root):
    name = make_batch(wb_root)
    client, _ = build_wb(cfg, wb_root)
    payload = client.get("/api/deliveries").json()
    assert [d["id"] for d in payload["deliveries"]] == [name]
    assert payload["deliveries"][0]["structured"] is True


def test_delivery_detail_carries_cases_and_environments(cfg, wb_root):
    configured_env(cfg)
    name = make_batch(wb_root)
    client, _ = build_wb(cfg, wb_root)
    payload = client.get("/api/deliveries/" + name).json()
    assert payload["ok"] is True and payload["structured"] is True
    assert [c["case_id"] for c in payload["cases"]] == ["TC-001", "TC-002"]
    # 执行面的环境下拉直接吃这一段，页面不必再打一次 /api/environments
    assert [e["name"] for e in payload["environments"]] == ["测试环境"]


def test_delivery_detail_degrades_for_an_old_batch(cfg, wb_root):
    name = make_legacy_batch(wb_root)
    client, _ = build_wb(cfg, wb_root)
    payload = client.get("/api/deliveries/" + name).json()
    assert payload["structured"] is False
    assert payload["degraded"]["code"] == "NO_CASES_JSON"


def test_unknown_delivery_is_404(cfg, wb_root):
    client, _ = build_wb(cfg, wb_root)
    response = client.get("/api/deliveries/20260101-000000-没有这个")
    assert response.status_code == 404
    assert response.json()["code"] == "DELIVERY_NOT_FOUND"


@pytest.mark.parametrize("bad", ["..", "%2e%2e", "%2e%2e%2fconfig", "%2e%2e%5cconfig",
                                 "20260101-000000-x%2f..%2f..%2fconfig"])
def test_delivery_id_cannot_escape_the_deliveries_dir(cfg, wb_root, bad):
    """批次 id 只能是 deliveries/ 下的一级目录名。带上跳或分隔符的一律不给过——
    有的写法在路由层就匹配不上（404 纯文本），有的走到 `safe_delivery_id` 被拒（400）。
    这里只钉一件事：**永远拿不到 200**。"""
    client, _ = build_wb(cfg, wb_root)
    response = client.get("/api/deliveries/" + bad)
    assert response.status_code in (400, 404)
    if response.headers.get("content-type", "").startswith("application/json"):
        assert response.json()["ok"] is False


# ── 执行 ────────────────────────────────────────────────────────────────────

def test_execute_starts_a_run_and_polling_reports_the_result(cfg, wb_root):
    configured_env(cfg)
    name = make_batch(wb_root)
    client, fake = build_wb(cfg, wb_root)
    started = client.post("/api/deliveries/" + name + "/execute",
                          json={"env": "测试环境", "case_ids": ["TC-001"]},
                          headers=csrf_of(client))
    assert started.status_code == 200
    run_id = started.json()["run"]["run_id"]

    assert wait_until(lambda: client.get("/api/runs/" + run_id).json()
                      ["run"]["state"] == "done")
    run = client.get("/api/runs/" + run_id).json()["run"]
    assert run["environment"] == "测试环境"
    assert run["result"]["summary"]["passed"] == 1
    assert run["result"]["results"][0]["case_id"] == "TC-001"
    # 执行器拿到的是环境名，不是值
    assert fake.calls[0]["env"] == "测试环境"
    assert "variables" not in fake.calls[0]


def test_execute_refuses_without_an_environment(cfg, wb_root):
    name = make_batch(wb_root)
    client, fake = build_wb(cfg, wb_root)
    response = client.post("/api/deliveries/" + name + "/execute",
                           json={"env": "", "case_ids": ["TC-001"]},
                           headers=csrf_of(client))
    assert response.status_code == 400
    assert response.json()["code"] == "ENV_REQUIRED"
    assert fake.calls == []


def test_execute_refuses_with_nothing_selected(cfg, wb_root):
    name = make_batch(wb_root)
    client, fake = build_wb(cfg, wb_root)
    response = client.post("/api/deliveries/" + name + "/execute",
                           json={"env": "测试环境", "case_ids": []},
                           headers=csrf_of(client))
    assert response.status_code == 400
    assert response.json()["code"] == "NO_CASE_SELECTED"
    assert fake.calls == []


def test_execute_refuses_on_an_old_batch(cfg, wb_root):
    name = make_legacy_batch(wb_root)
    client, fake = build_wb(cfg, wb_root)
    response = client.post("/api/deliveries/" + name + "/execute",
                           json={"env": "测试环境", "case_ids": ["TC-001"]},
                           headers=csrf_of(client))
    assert response.status_code == 400
    assert response.json()["code"] == "NO_CASES_JSON"
    assert fake.calls == []


def test_execute_on_an_unknown_batch_is_404(cfg, wb_root):
    client, fake = build_wb(cfg, wb_root)
    response = client.post("/api/deliveries/20260101-000000-没有/execute",
                           json={"env": "测试环境", "case_ids": ["TC-001"]},
                           headers=csrf_of(client))
    assert response.status_code == 404
    assert fake.calls == []


def test_polling_an_unknown_run_is_404(cfg, wb_root):
    client, _ = build_wb(cfg, wb_root)
    response = client.get("/api/runs/run-nope")
    assert response.status_code == 404
    assert response.json()["code"] == "RUN_NOT_FOUND"


# ── 安全闸（工作台这几个端点一条都不放开） ──────────────────────────────────

def test_execute_endpoint_is_csrf_guarded(cfg, wb_root):
    name = make_batch(wb_root)
    client, fake = build_wb(cfg, wb_root)
    client.get("/")
    response = client.post("/api/deliveries/" + name + "/execute",
                           json={"env": "测试环境", "case_ids": ["TC-001"]})
    assert response.status_code == 403
    assert response.json()["code"] == "BAD_CSRF"
    assert fake.calls == []                    # CSRF 没过 = 一条请求都没发出去


def test_execute_endpoint_rejects_a_foreign_host_header(cfg, wb_root):
    name = make_batch(wb_root)
    client, fake = build_wb(cfg, wb_root)
    headers = csrf_of(client)
    headers["Host"] = "evil.example.com"
    response = client.post("/api/deliveries/" + name + "/execute",
                           json={"env": "测试环境", "case_ids": ["TC-001"]},
                           headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "BAD_HOST"
    assert fake.calls == []


@pytest.mark.parametrize("path", ["/api/deliveries", "/api/runs/run-1"])
def test_workbench_reads_reject_a_foreign_host_header(cfg, wb_root, path):
    client, _ = build_wb(cfg, wb_root)
    assert client.get(path, headers={"Host": "evil.example.com"}).status_code == 403


def test_no_workbench_response_ever_contains_a_credential_value(cfg, wb_root):
    """这是本段的底线：环境变量的值一个字符都不许出现在工作台的任何响应里。"""
    configured_env(cfg)
    cfg.set_secret("TAPD_ACCESS_TOKEN", TOKEN)
    name = make_batch(wb_root)
    client, _ = build_wb(cfg, wb_root)
    started = client.post("/api/deliveries/" + name + "/execute",
                          json={"env": "测试环境", "case_ids": ["TC-001"]},
                          headers=csrf_of(client))
    run_id = started.json()["run"]["run_id"]
    wait_until(lambda: client.get("/api/runs/" + run_id).json()["run"]["state"] == "done")

    bodies = [client.get("/").text,
              client.get("/api/deliveries").text,
              client.get("/api/deliveries/" + name).text,
              started.text,
              client.get("/api/runs/" + run_id).text]
    for body in bodies:
        for secret in (TOKEN, ENV_TOKEN, ENV_SESSION):
            assert secret not in body
            assert secret[:16] not in body
    # 而"凭据放在哪"照样看得见：请求头里的占位是原样带出的
    detail = client.get("/api/deliveries/" + name).json()
    assert detail["cases"][0]["request"]["headers"][0]["value"] == "Bearer {{token}}"


# ── 登录换新（0.8）：配置页说明 + 工作台鉴权方式 ─────────────────────────────

def test_environment_form_explains_the_three_reserved_variable_names(cfg):
    """三个保留变量名要在配置页上说清楚是干什么的，不能只在文档里。"""
    body = build(cfg)[0].get("/").text
    section = body[body.index("变量金库"):body.index("保存环境")]
    for name in ("login_username", "login_password", "auth_token_path"):
        assert name in section, name
    assert "登录换新" in section
    assert "$.data.token" in section                # 取值路径长什么样也给个例子


def test_reserved_variable_hints_are_wired_to_the_key_input(cfg):
    """行内说明跟着键名走：敲到一半就认出来，不用等保存。"""
    body = build(cfg)[0].get("/").text
    assert "RESERVED_VARS" in body
    assert "varRowHint" in body
    assert 'class="why"' in body


def test_workbench_has_an_auth_mode_select_next_to_the_environment(cfg):
    body = build(cfg)[0].get("/").text
    assert 'id="wb-auth"' in body
    assert '<option value="env">跟随环境</option>' in body
    assert '<option value="login">登录换新</option>' in body
    # 下拉挨着环境那一个（同一行 row 里，环境在前）
    assert body.index('id="wb-env"') < body.index('id="wb-auth"')
    assert body.index('id="wb-auth"') < body.index('id="btn-wb-exec"')


def test_login_mode_tells_the_user_to_configure_credentials_first(cfg):
    body = build(cfg)[0].get("/").text
    script = body[body.index("function wbRenderAuthSelect"):]
    assert "login_username" in script and "login_password" in script
    assert "测试环境" in script                       # 指到那个 tab
    assert 'id="wb-auth-why"' in body


def test_execute_endpoint_passes_the_auth_mode_through(cfg, wb_root):
    from tests.test_gateway_workbench import make_login_batch

    configured_env(cfg)
    name = make_login_batch(wb_root)
    client, executor = build_wb(cfg, wb_root)
    started = client.post("/api/deliveries/" + name + "/execute",
                          json={"env": "测试环境", "case_ids": ["TC-001"],
                                "auth": "login"},
                          headers=csrf_of(client))
    assert started.status_code == 200
    run_id = started.json()["run"]["run_id"]
    wait_until(lambda: client.get("/api/runs/" + run_id).json()["run"]["state"] == "done")
    assert executor.calls[0]["auth"] == "login"
    assert executor.calls[0]["login_request"]["method"] == "POST"


def test_execute_endpoint_defaults_to_env_when_auth_is_absent(cfg, wb_root):
    configured_env(cfg)
    name = make_batch(wb_root)
    client, executor = build_wb(cfg, wb_root)
    started = client.post("/api/deliveries/" + name + "/execute",
                          json={"env": "测试环境", "case_ids": ["TC-001"]},
                          headers=csrf_of(client))
    run_id = started.json()["run"]["run_id"]
    wait_until(lambda: client.get("/api/runs/" + run_id).json()["run"]["state"] == "done")
    assert executor.calls[0]["auth"] == "env"


def test_login_mode_on_a_batch_without_a_login_request_is_a_400(cfg, wb_root):
    configured_env(cfg)
    name = make_batch(wb_root)
    client, executor = build_wb(cfg, wb_root)
    response = client.post("/api/deliveries/" + name + "/execute",
                           json={"env": "测试环境", "case_ids": ["TC-001"],
                                 "auth": "login"},
                           headers=csrf_of(client))
    assert response.status_code == 400
    assert response.json()["code"] == "NO_LOGIN_REQUEST"
    assert executor.calls == []


def test_delivery_detail_carries_the_auth_refresh_offer(cfg, wb_root):
    from tests.test_gateway_workbench import make_login_batch

    name = make_login_batch(wb_root)
    client, _ = build_wb(cfg, wb_root)
    offer = client.get("/api/deliveries/" + name).json()["auth_refresh"]
    assert offer["available"] is True
    assert offer["endpoint"] == "POST /api/v1/auth/login"


# ── WB-001 回归：跨 tab 新建的环境，切进工作台要看得见 ──────────────────────
#
# 缺陷（2026-08-05 验收预验发现，fce515b 修复）：在「测试环境」tab 新建环境后，
# 切到「工作台」tab，环境下拉仍显示「还没有配置任何测试环境」，必须刷新整页才出现。
# 根因：环境列表只在页面加载时取一次，切 tab 不重取。
# 修复当时没带用例，本组即补上的回归闸。

def test_switching_into_the_workbench_refetches_environments(cfg):
    """WB-001：切进工作台必须重取环境——环境是在别的 tab 改的，进来得以服务端为准。"""
    body = build(cfg)[0].get("/").text
    show_tab = body[body.index("function showTab"):body.index("function wbRefreshEnvs")]
    assert 'name === "workbench"' in show_tab
    assert "wbRefreshEnvs()" in show_tab


def test_the_workbench_env_refetch_reads_the_server_and_rerenders(cfg):
    """光调用不算数：得真去服务端拿，拿回来还要重画下拉。"""
    body = build(cfg)[0].get("/").text
    fn = body[body.index("function wbRefreshEnvs"):]
    fn = fn[:fn.index("\nTABS.forEach")]
    assert '"/api/environments"' in fn
    assert "wbRenderEnvSelect()" in fn
    assert "catch" in fn                    # 取不到就沿用旧列表，不许把页面打断


def test_a_newly_saved_environment_shows_up_in_the_environments_api(cfg):
    """服务端这一侧的契约：刚存的环境，下一次 GET 就该有——重取才有意义。

    注意它**不是** WB-001 的回归闸：实测把 fce515b 反向打掉后本条照样绿，
    因为缺陷在前端只取一次，服务端契约从没坏过。真正的回归闸是上面两条。
    留着它是为了钉住重取所依赖的前提，别让人误以为三条都在守同一件事。
    """
    client = build(cfg)[0]
    headers = csrf_of(client)
    before = [e["name"] for e in client.get("/api/environments").json()["environments"]]
    assert "测试环境" not in before

    client.post("/api/environments/save", json=ENV_PAYLOAD, headers=headers)

    after = [e["name"] for e in client.get("/api/environments").json()["environments"]]
    assert ENV_PAYLOAD["name"] in after
