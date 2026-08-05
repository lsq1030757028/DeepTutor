"""「测试伙伴」一键创建/修复的测试：`partner_setup` 单元 + 配置页两个端点。

假 DeepTutor 客户端把 partners API 的**行为**照实机复刻了三条关键语义，
这三条正是本次缺陷的根因所在，写死在假件里当回归护栏：

1. `PATCH /<id>` **收不到 soul、收不到 assets**——只认 `UpdatePartnerRequest` 里的键。
   假件对多余的键直接 `AssertionError`，防止哪天有人图省事往 PATCH 里塞 soul。
2. `mcp_tools=None` = 不限制（落盘 `["*"]`），`[]` = 全关。两者含义相反。
3. 技能是**复制进伙伴工作区**，不是全局可见——不绑就是没有。

本文件不打网络、不起进程。
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from server.gateway.config import GatewayConfig
from server.gateway.deeptutor_client import DeepTutorError
from server.gateway.partner_setup import (DEFAULT_PARTNER_NAME, REQUIRED_SKILLS,
                                          SOUL_PATH, ensure_partner,
                                          load_soul_body, merge_mcp_tools,
                                          partner_overview, plan_mcp_tools,
                                          split_soul_body)
from server.gateway.webapp import CSRF_COOKIE, CSRF_HEADER, create_app

#: `UpdatePartnerRequest` 的全部字段（实机 2026-08-05 抄）。soul / assets 不在其中。
PATCHABLE = frozenset({
    "name", "description", "channels", "llm_selection", "backup_llm_selection",
    "language", "emoji", "color", "avatar", "enabled_tools", "builtin_tools",
    "mcp_tools",
})

PRIMARY_TOOLS = ["ping", "server_info", "parse_har", "validate_cases",
                 "save_delivery", "execute_cases"]

#: tapd 那个包 43 个工具，这里取几个有代表性的：两个只读的 + 一个写操作。
TAPD_TOOLS = ["get_stories_or_tasks", "get_stories_fields_info", "create_story"]


def tool_options(primary=True, tapd=True) -> dict:
    rows = []
    if primary:
        rows += [{"name": f"mcp_test-partner_{t}", "kind": "mcp",
                  "provider_id": "test-partner", "server": "test-partner",
                  "description": f"[test-partner] {t}"} for t in PRIMARY_TOOLS]
    if tapd:
        rows += [{"name": f"mcp_tapd_{t}", "kind": "mcp", "provider_id": "tapd",
                  "server": "tapd", "description": f"[tapd] {t}"}
                 for t in TAPD_TOOLS]
    return {"tools": [], "builtin_tools": [], "mcp_tools": rows}


#: 用户手工建出来的那个坏伙伴，字段逐个照 `GET /api/v1/partners` 的实机响应抄。
BROKEN_PARTNER = {
    "partner_id": "test-guy", "name": "test guy", "description": "测试",
    "channels": [], "llm_selection": {"profile_id": "p", "model_id": "m"},
    "backup_llm_selection": None, "model": None, "language": "zh", "emoji": "🐸",
    "color": "", "avatar": "", "soul_origin": {"type": "library", "id": "hermes"},
    "enabled_tools": None, "builtin_tools": None, "mcp_tools": [],
    "running": True, "started_at": "2026-08-04T09:43:10", "last_reload_error": None,
}

HERMES_SOUL = "# SOUL — Hermes 第一性原理\n\n不是我们的人格。\n"

#: 伙伴工作区默认自带的几份技能（实机上 test-guy 就带着这些）。
DEFAULT_WORKSPACE_SKILLS = ["docx", "pdf", "pptx", "skill-creator", "xlsx"]


class FakeDeepTutorPartners:
    """partners API 的行为替身。`raise_on` 指定在哪一步抛 `DeepTutorError`。"""

    api_base = "http://127.0.0.1:3782"

    def __init__(self, partners=None, options=None, raise_on: str = "",
                 skill_error: str = "") -> None:
        self.rows: dict[str, dict] = {}
        self.souls: dict[str, str] = {}
        self.skills: dict[str, list[str]] = {}
        for row in partners or []:
            pid = row["partner_id"]
            self.rows[pid] = dict(row)
            self.souls[pid] = HERMES_SOUL
            self.skills[pid] = list(DEFAULT_WORKSPACE_SKILLS)
        self.options = options if options is not None else tool_options()
        self.raise_on = raise_on
        self.skill_error = skill_error
        self.calls: list[tuple[str, str]] = []
        self.patches: list[tuple[str, dict]] = []

    def _guard(self, step: str) -> None:
        self.calls.append((step, ""))
        if self.raise_on == step:
            raise DeepTutorError(f"假件在 {step} 上被要求失败", code="DEEPTUTOR_FORBIDDEN")

    # ── 读 ──────────────────────────────────────────────────────────────

    def list_partners(self):
        self._guard("list")
        return [dict(row) for row in self.rows.values()]

    def get_partner(self, partner_id):
        self._guard("get")
        if partner_id not in self.rows:
            raise DeepTutorError("伙伴不存在", code="DEEPTUTOR_HTTP_ERROR")
        return dict(self.rows[partner_id])

    def partner_tool_options(self):
        self._guard("options")
        return self.options

    def get_partner_soul(self, partner_id):
        self._guard("get_soul")
        return self.souls.get(partner_id, "")

    def get_partner_assets(self, partner_id):
        self._guard("get_assets")
        return {"knowledge_bases": [],
                "skills": [{"name": n} for n in sorted(self.skills.get(partner_id, []))],
                "notebooks": []}

    # ── 写 ──────────────────────────────────────────────────────────────

    def create_partner(self, payload):
        self._guard("create")
        # 中文名在实机上 slug 成 partner-<sha1 前 8 位>；这里只要"不可预测"这一点像。
        pid = payload.get("partner_id") or "partner-a1b2c3d4"
        assert pid not in self.rows, "重复创建——ensure_partner 应该先查再建"
        soul = (payload.get("soul") or {})
        assert soul.get("source") == "custom", "人格必须用 custom 正文，不能引人格库"
        self.rows[pid] = {
            "partner_id": pid, "name": payload["name"],
            "description": payload.get("description", ""),
            "language": payload.get("language", ""),
            "emoji": payload.get("emoji", ""), "color": "", "avatar": "",
            "soul_origin": {"type": "custom", "id": ""},
            "enabled_tools": None, "builtin_tools": None,
            "mcp_tools": payload.get("mcp_tools", []),
            "llm_selection": None, "backup_llm_selection": None, "model": None,
            "channels": [], "running": bool(payload.get("start")),
            "started_at": None, "last_reload_error": None,
        }
        self.souls[pid] = soul.get("content") or ""
        self.skills[pid] = list(DEFAULT_WORKSPACE_SKILLS)
        report = self._provision(pid, (payload.get("assets") or {}).get("skills") or [])
        result = dict(self.rows[pid])
        result["provisioning"] = report
        return result

    def update_partner(self, partner_id, payload):
        self._guard("patch")
        unknown = set(payload) - PATCHABLE
        assert not unknown, f"PATCH 收不了这些键（实机会静默忽略）：{sorted(unknown)}"
        self.patches.append((partner_id, dict(payload)))
        self.rows[partner_id].update(payload)
        return dict(self.rows[partner_id])

    def put_partner_soul(self, partner_id, content):
        self._guard("put_soul")
        self.souls[partner_id] = content
        return {"partner_id": partner_id, "saved": True}

    def add_partner_assets(self, partner_id, skills=None):
        self._guard("add_assets")
        return {"partner_id": partner_id, **self._provision(partner_id, skills or [])}

    def _provision(self, pid, skills):
        copied, errors = [], []
        for name in skills:
            if self.skill_error and name == self.skill_error:
                errors.append({"type": "skill", "name": name,
                               "error": f"Skill '{name}' not found or not accessible"})
                continue
            if name not in self.skills[pid]:
                self.skills[pid].append(name)
            copied.append(name)
        return {"copied": {"knowledge_bases": [], "skills": copied, "notebooks": []},
                "errors": errors}


@pytest.fixture()
def soul_file(tmp_path):
    """一份跟仓库 SOUL.md 同构的小人格文件（分隔线 + 名字占位）。"""
    path = tmp_path / "SOUL.md"
    path.write_text(
        "# 测试伙伴 · 灵魂（SOUL）\n\n> 说明，不该粘进去。\n\n---\n\n"
        "## 我是谁\n\n我是 **[伙伴名字：待用户起名]**，一名资深测试工程师。\n",
        encoding="utf-8")
    return str(path)


def step(result, name):
    for row in result["steps"]:
        if row["name"] == name:
            return row
    raise AssertionError(f"没有名为 {name} 的步骤：{[r['name'] for r in result['steps']]}")


# ── 人格正文 ────────────────────────────────────────────────────────────────

def test_split_soul_body_takes_text_below_the_rule():
    body = split_soul_body("标题\n\n> 用法说明\n\n---\n\n## 我是谁\n\n正文\n")
    assert body.startswith("## 我是谁")
    assert "用法说明" not in body


def test_split_soul_body_without_rule_keeps_everything():
    """没有分隔线时宁可多带说明，也不要静默给出空人格。"""
    assert split_soul_body("只有正文\n").strip() == "只有正文"


def test_split_soul_body_ignores_indented_rule():
    """代码块里缩进的 `---` 不是分隔线。"""
    body = split_soul_body("头\n\n    ---\n\n---\n\n真正的正文\n")
    assert body.strip() == "真正的正文"


def test_load_soul_body_substitutes_the_name(soul_file):
    body = load_soul_body("测试伙伴", soul_file)
    assert "[伙伴名字：待用户起名]" not in body
    assert "我是 **测试伙伴**" in body


def test_repo_soul_file_is_loadable():
    """仓库里那份 SOUL.md 必须真能读出正文——这是整条链路的地基。"""
    body = load_soul_body("测试伙伴", SOUL_PATH)
    assert "## 我是谁" in body
    assert "灵魂（SOUL）" not in body        # 分隔线以上的标题没被带进来
    assert "[伙伴名字" not in body


# ── 工具清单 ────────────────────────────────────────────────────────────────

def test_plan_mcp_tools_takes_all_primary_and_only_readonly_tapd():
    plan = plan_mcp_tools(tool_options())
    assert plan["primary"] == [f"mcp_test-partner_{t}" for t in PRIMARY_TOOLS]
    assert plan["tapd"] == ["mcp_tapd_get_stories_or_tasks",
                            "mcp_tapd_get_stories_fields_info"]
    assert "mcp_tapd_create_story" not in plan["tools"]   # 写操作不放行


def test_plan_mcp_tools_without_tapd():
    plan = plan_mcp_tools(tool_options(tapd=False))
    assert plan["tapd_present"] is False
    assert plan["tools"] == plan["primary"]


def test_plan_mcp_tools_falls_back_to_name_prefix():
    """服务端没给 `server` 字段时从名字前缀推——别整段罢工。"""
    plan = plan_mcp_tools({"mcp_tools": [{"name": "mcp_test-partner_ping"}]})
    assert plan["tools"] == ["mcp_test-partner_ping"]


def test_merge_mcp_tools_is_a_union_preserving_user_extras():
    merged = merge_mcp_tools(["mcp_other_thing"], ["mcp_test-partner_ping"])
    assert merged == ["mcp_other_thing", "mcp_test-partner_ping"]


def test_merge_mcp_tools_leaves_unrestricted_alone():
    """`None` = 全放行。写一份白名单是收窄权限，不是修复。"""
    assert merge_mcp_tools(None, ["mcp_test-partner_ping"]) is None


# ── 创建 ────────────────────────────────────────────────────────────────────

def test_creates_partner_when_absent(soul_file):
    client = FakeDeepTutorPartners()
    result = ensure_partner(client=client, soul_path=soul_file)

    assert result["ok"] is True
    assert result["created"] is True
    pid = result["partner_id"]
    row = client.rows[pid]
    assert row["name"] == DEFAULT_PARTNER_NAME
    assert row["language"] == "zh"
    assert row["soul_origin"] == {"type": "custom", "id": ""}
    assert "我是 **测试伙伴**" in client.souls[pid]
    assert row["mcp_tools"] == plan_mcp_tools(tool_options())["tools"]
    assert set(REQUIRED_SKILLS) <= set(client.skills[pid])
    assert result["summary"]["soul_ok"] is True


def test_create_never_sends_a_model_selection(soul_file):
    """猜一个 profile_id 就是把伙伴钉在可能已被删的模型上；服务端 None = 系统默认。"""
    client = FakeDeepTutorPartners()
    ensure_partner(client=client, soul_path=soul_file)
    assert client.rows[list(client.rows)[0]]["llm_selection"] is None


def test_create_uses_the_given_name(soul_file):
    client = FakeDeepTutorPartners()
    result = ensure_partner(client=client, name="小测", soul_path=soul_file)
    assert client.rows[result["partner_id"]]["name"] == "小测"
    assert "我是 **小测**" in client.souls[result["partner_id"]]


# ── 修复既有 ────────────────────────────────────────────────────────────────

def test_repairs_the_broken_partner(soul_file):
    """用户手工建的那个：hermes 人格 + 零工具 + 没绑技能，三样一次补齐。"""
    client = FakeDeepTutorPartners([BROKEN_PARTNER])
    result = ensure_partner(client=client, partner_id="test-guy", soul_path=soul_file)

    assert result["ok"] is True
    assert result["created"] is False
    assert result["partner_id"] == "test-guy"
    assert client.souls["test-guy"].startswith("## 我是谁")
    assert client.rows["test-guy"]["mcp_tools"] == plan_mcp_tools(tool_options())["tools"]
    assert set(REQUIRED_SKILLS) <= set(client.skills["test-guy"])


def test_repair_never_renames_or_deletes(soul_file):
    client = FakeDeepTutorPartners([BROKEN_PARTNER])
    ensure_partner(client=client, name="测试伙伴", partner_id="test-guy",
                   soul_path=soul_file)
    assert client.rows["test-guy"]["name"] == "test guy"      # 名字没被动
    assert set(client.patches[0][1]) <= {"mcp_tools", "language"}
    assert "name" not in client.patches[0][1]
    assert set(DEFAULT_WORKSPACE_SKILLS) <= set(client.skills["test-guy"])  # 原技能还在


def test_repair_keeps_user_added_tools(soul_file):
    client = FakeDeepTutorPartners([dict(BROKEN_PARTNER,
                                         mcp_tools=["mcp_other_server_thing"])])
    ensure_partner(client=client, partner_id="test-guy", soul_path=soul_file)
    assert client.rows["test-guy"]["mcp_tools"][0] == "mcp_other_server_thing"


def test_repair_does_not_narrow_an_unrestricted_partner(soul_file):
    client = FakeDeepTutorPartners([dict(BROKEN_PARTNER, mcp_tools=None)])
    result = ensure_partner(client=client, partner_id="test-guy", soul_path=soul_file)
    assert client.rows["test-guy"]["mcp_tools"] is None
    assert "不限制" in step(result, "更新伙伴配置")["detail"]
    assert result["ok"] is True


def test_repair_matches_by_display_name(soul_file):
    """没给 id 时按显示名找——中文名的 partner_id 是服务端算的，这边不复刻算法。"""
    client = FakeDeepTutorPartners([dict(BROKEN_PARTNER, partner_id="partner-9f8e",
                                         name=DEFAULT_PARTNER_NAME)])
    result = ensure_partner(client=client, soul_path=soul_file)
    assert result["created"] is False
    assert result["partner_id"] == "partner-9f8e"


def test_repair_reports_soul_origin_stays_stale(soul_file):
    """PATCH 改不了 soul_origin，页面上那个「人格来源」标签会继续显示 hermes——
    不解释一句，用户会以为没生效。"""
    client = FakeDeepTutorPartners([BROKEN_PARTNER])
    result = ensure_partner(client=client, partner_id="test-guy", soul_path=soul_file)
    detail = step(result, "写入人格正文")["detail"]
    assert "hermes" in detail
    assert "人格来源" in detail


def test_missing_partner_id_is_a_clean_failure(soul_file):
    client = FakeDeepTutorPartners([BROKEN_PARTNER])
    result = ensure_partner(client=client, partner_id="gone", soul_path=soul_file)
    assert result["ok"] is False
    assert result["code"] == "PARTNER_NOT_FOUND"
    assert not client.patches


# ── 降级 ────────────────────────────────────────────────────────────────────

def test_degrades_when_tapd_is_absent(soul_file):
    client = FakeDeepTutorPartners(options=tool_options(tapd=False))
    result = ensure_partner(client=client, soul_path=soul_file)
    assert result["ok"] is True
    row = step(result, "清点可挂载的 MCP 工具")
    assert row["ok"] is True
    assert "tapd 未注册" in row["detail"]
    assert all("tapd" not in t for t in client.rows[result["partner_id"]]["mcp_tools"])


def test_skips_tools_when_the_gateway_itself_is_not_registered(soul_file):
    """test-partner 都看不到时，不能把既有伙伴的工具改成空——那是帮倒忙。"""
    client = FakeDeepTutorPartners([dict(BROKEN_PARTNER,
                                         mcp_tools=["mcp_other_server_thing"])],
                                   options=tool_options(primary=False, tapd=False))
    result = ensure_partner(client=client, partner_id="test-guy", soul_path=soul_file)
    assert step(result, "清点可挂载的 MCP 工具")["ok"] is False
    assert client.rows["test-guy"]["mcp_tools"] == ["mcp_other_server_thing"]
    assert client.souls["test-guy"].startswith("## 我是谁")   # 人格照样修好了


def test_skill_provisioning_error_is_surfaced(soul_file):
    client = FakeDeepTutorPartners([BROKEN_PARTNER], skill_error="req-testing")
    result = ensure_partner(client=client, partner_id="test-guy", soul_path=soul_file)
    assert result["ok"] is False
    assert step(result, "绑定技能")["ok"] is False
    assert "req-testing" in step(result, "绑定技能")["detail"]


def test_missing_soul_file_fails_before_touching_deeptutor(tmp_path):
    client = FakeDeepTutorPartners()
    result = ensure_partner(client=client, soul_path=str(tmp_path / "nope.md"))
    assert result["ok"] is False
    assert result["code"] == "SOUL_FILE_MISSING"
    assert client.calls == []


def test_deeptutor_failure_is_reported_with_its_code(soul_file):
    client = FakeDeepTutorPartners(raise_on="options")
    result = ensure_partner(client=client, soul_path=soul_file)
    assert result["ok"] is False
    assert result["code"] == "DEEPTUTOR_FORBIDDEN"


# ── 幂等 ────────────────────────────────────────────────────────────────────

def test_idempotent_across_repeated_calls(soul_file):
    client = FakeDeepTutorPartners()
    first = ensure_partner(client=client, soul_path=soul_file)
    before = (json.dumps(client.rows, ensure_ascii=False, sort_keys=True),
              dict(client.souls), {k: sorted(v) for k, v in client.skills.items()})

    second = ensure_partner(client=client, soul_path=soul_file)
    after = (json.dumps(client.rows, ensure_ascii=False, sort_keys=True),
             dict(client.souls), {k: sorted(v) for k, v in client.skills.items()})

    assert second["ok"] is True
    assert second["created"] is False          # 第二次认出了第一次建的那个
    assert second["partner_id"] == first["partner_id"]
    assert before == after                     # 落盘状态逐字节没变
    assert not client.patches                  # 没有要改的就一个 PATCH 都不发
    assert "已是最新" in step(second, "更新伙伴配置")["detail"]
    assert "无需改写" in step(second, "写入人格正文")["detail"]


def test_idempotent_after_repair(soul_file):
    client = FakeDeepTutorPartners([BROKEN_PARTNER])
    ensure_partner(client=client, partner_id="test-guy", soul_path=soul_file)
    patches_after_first = len(client.patches)
    second = ensure_partner(client=client, partner_id="test-guy", soul_path=soul_file)
    assert second["ok"] is True
    assert len(client.patches) == patches_after_first


# ── 状态投影 ────────────────────────────────────────────────────────────────

def test_overview_flags_the_broken_partner(soul_file):
    """坏伙伴要能在页面上被一眼看出坏在哪。"""
    client = FakeDeepTutorPartners([dict(BROKEN_PARTNER,
                                         mcp_tools=["mcp_test-partner_ping"])])
    view = partner_overview(client, soul_path=soul_file)
    assert view["ok"] is True
    assert [p["partner_id"] for p in view["partners"]] == ["test-guy"]
    broken = view["candidates"][0]
    assert broken["soul_ok"] is False
    assert broken["missing_skills"] == list(REQUIRED_SKILLS)
    assert view["mcp"]["primary_count"] == len(PRIMARY_TOOLS)


def test_overview_skips_unrelated_partners(soul_file):
    """别人的学习助手不该被我们体检（也不该为它多打两次请求）。"""
    other = dict(BROKEN_PARTNER, partner_id="study-buddy", name="学习助手",
                 mcp_tools=[])
    client = FakeDeepTutorPartners([other])
    view = partner_overview(client, soul_path=soul_file)
    assert [p["partner_id"] for p in view["partners"]] == ["study-buddy"]
    assert view["candidates"] == []


def test_overview_always_checks_the_selected_partner(soul_file):
    """用户手工建的坏伙伴两条自动规则都不命中——它恰恰是最该被看见的那个。"""
    client = FakeDeepTutorPartners([BROKEN_PARTNER])
    assert partner_overview(client, soul_path=soul_file)["candidates"] == []
    view = partner_overview(client, soul_path=soul_file, include=["test-guy"])
    assert view["candidates"][0]["partner_id"] == "test-guy"
    assert view["candidates"][0]["soul_ok"] is False
    assert view["candidates"][0]["tool_count"] == 0


def test_overview_survives_a_dead_deeptutor(soul_file):
    client = FakeDeepTutorPartners(raise_on="list")
    view = partner_overview(client, soul_path=soul_file)
    assert view["ok"] is False
    assert view["code"] == "DEEPTUTOR_FORBIDDEN"


# ── 配置页端点 ──────────────────────────────────────────────────────────────

@pytest.fixture()
def page(tmp_path):
    """带假 partners 客户端的配置页。子服务管家也是假的，不起进程。"""
    from tests.test_gateway_webapp import FakeSupervisor

    deeptutor = FakeDeepTutorPartners([BROKEN_PARTNER])
    app = create_app(config=GatewayConfig(str(tmp_path / "config")),
                     supervisor=FakeSupervisor(), deeptutor=deeptutor)
    return TestClient(app, base_url="http://localhost:3789"), deeptutor


def csrf_of(client) -> dict[str, str]:
    assert client.get("/").status_code == 200
    return {CSRF_HEADER: client.cookies[CSRF_COOKIE]}


def test_page_has_the_partner_section(page):
    client, _ = page
    html = client.get("/").text
    assert "测试伙伴" in html
    assert 'id="btn-partner-ensure"' in html
    assert "/api/partner/ensure" in html


def test_partner_status_endpoint(page):
    client, _ = page
    body = client.get("/api/partner/status").json()
    assert body["ok"] is True
    assert body["default_name"] == DEFAULT_PARTNER_NAME
    assert [p["partner_id"] for p in body["partners"]] == ["test-guy"]
    assert body["candidates"] == []       # 没选中谁，就不体检
    # 状态是活的，缓存住就成了骗人的灯。
    assert client.get("/api/partner/status").headers["cache-control"] == "no-store"


def test_partner_status_checks_the_selected_partner(page):
    client, _ = page
    body = client.get("/api/partner/status?partner_id=test-guy").json()
    assert [c["partner_id"] for c in body["candidates"]] == ["test-guy"]
    assert body["candidates"][0]["soul_ok"] is False


def test_partner_ensure_creates(page):
    client, deeptutor = page
    body = client.post("/api/partner/ensure", json={"name": "测试伙伴"},
                       headers=csrf_of(client)).json()
    assert body["ok"] is True
    assert body["created"] is True
    assert len(deeptutor.rows) == 2                 # 原来的 test-guy 还在
    assert "test-guy" in deeptutor.rows


def test_partner_ensure_repairs_selected_partner(page):
    client, deeptutor = page
    body = client.post("/api/partner/ensure", json={"partner_id": "test-guy"},
                       headers=csrf_of(client)).json()
    assert body["ok"] is True
    assert body["created"] is False
    assert len(deeptutor.rows) == 1                 # 没有新建，也没有删除
    assert deeptutor.rows["test-guy"]["name"] == "test guy"


def test_partner_ensure_requires_csrf(page):
    client, deeptutor = page
    client.get("/")
    response = client.post("/api/partner/ensure", json={"name": "x"})
    assert response.status_code == 403
    assert response.json()["code"] == "BAD_CSRF"
    assert len(deeptutor.rows) == 1


def test_partner_endpoints_reject_foreign_host(page):
    client, _ = page
    for method, path in (("get", "/api/partner/status"), ("post", "/api/partner/ensure")):
        response = getattr(client, method)(path, headers={"Host": "evil.com"})
        assert response.status_code == 403
        assert response.json()["code"] == "BAD_HOST"


def test_partner_status_reports_a_dead_deeptutor(tmp_path):
    from tests.test_gateway_webapp import FakeSupervisor

    app = create_app(config=GatewayConfig(str(tmp_path / "config")),
                     supervisor=FakeSupervisor(),
                     deeptutor=FakeDeepTutorPartners(raise_on="list"))
    client = TestClient(app, base_url="http://localhost:3789")
    body = client.get("/api/partner/status").json()
    assert body["ok"] is False
    assert body["partners"] == []
