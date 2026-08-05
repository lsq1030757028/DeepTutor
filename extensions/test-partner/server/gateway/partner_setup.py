"""一键把 DeepTutor 里的「测试伙伴」配成能干活的样子。

## 为什么有这个模块

用户照着旧版 `partner/README.md` 的三步手工建了一个伙伴，建完实际是：
`soul_origin={"type":"library","id":"hermes"}`（用了内置人格库，不是我们的 SOUL.md）、
`mcp_tools=[]`（一个工具没挂）、技能没绑。三样全错——手工指引对不上真实界面，
而这三样恰好是"这个伙伴能不能干测试的活"的全部。

所以把这三样收成一个函数：`ensure_partner()`。不存在就建，存在就补齐。

## 三样东西各走各的路（服务端形状决定，不是这里想拆）

| 要配的东西 | 端点 | 备注 |
| --- | --- | --- |
| 人格正文 | 创建时 `soul={"source":"custom","content":...}`；之后 `PUT /<id>/soul` | PATCH 收不了 soul |
| MCP 工具 | `mcp_tools=["mcp_<服务器>_<工具>", ...]` | 名字从 `tool-options` 抄，不拼 |
| 技能 | 创建时 `assets.skills`；之后 `POST /<id>/assets` | 服务端是**复制进伙伴工作区**，不是引用 |

技能这条要特别说一句：DeepTutor 不是"全局技能对所有伙伴可见"，
`provision_assets` 会把 `skills/<名>/` 整个目录复制进伙伴自己的工作区。
所以**必须绑**，不绑伙伴就读不到 `har-testing` / `req-testing` 的剧本。

## 两条红线

1. **不删用户数据。** 本模块只有创建和增量更新，没有删除路径。
   `mcp_tools` 用并集（用户自己额外挂的工具留着），技能用追加。
2. **不改名。** 修复既有伙伴时不发 `name`——用户给伙伴起的名字是他的，
   哪怕叫 `test guy` 也不动。

## 幂等

第二次调用跟第一次结果一致：工具清单是并集且顺序稳定，技能复制到已存在就跳过，
人格正文一致时不重复写。返回的 `steps` 措辞会从"已写入"变成"已是最新"，
但 `ok` 与最终状态相同。
"""

from __future__ import annotations

import os
from typing import Any

from server.gateway.deeptutor_client import DeepTutorClient, DeepTutorError

#: 仓库根（本文件在 <root>/server/gateway/partner_setup.py）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 人格正文的出处。整个流程只认这一份文件。
SOUL_PATH = os.path.join(REPO_ROOT, "partner", "SOUL.md")

#: `SOUL.md` 里留给伙伴名字的占位，注入时替换成真实名字。
SOUL_NAME_PLACEHOLDER = "[伙伴名字：待用户起名]"

#: 默认目标伙伴名。用户已有的伙伴一律走"修复既有"，不碰这个名字。
DEFAULT_PARTNER_NAME = "测试伙伴"

DEFAULT_DESCRIPTION = "HAR 抓包与 TAPD 需求出用例的测试搭档（由能力网关配置页创建）"
DEFAULT_EMOJI = "🧪"
DEFAULT_LANGUAGE = "zh"

#: 必备 MCP 服务器：它的工具全挂。
PRIMARY_MCP_SERVER = "test-partner"

#: 可选 MCP 服务器：只挂两个只读工具，理由同 `config.py` 的 `DEFAULT_SETTINGS`
#: （这个包 43 个工具里 17 个是写操作，凭据是模块级单例，写操作追不到人）。
TAPD_MCP_SERVER = "tapd"
TAPD_READONLY_TOOLS = ("get_stories_or_tasks", "get_stories_fields_info")

#: 两条技能链路的剧本，对应仓库 `skills/` 下的目录名。
REQUIRED_SKILLS = ("har-testing", "req-testing")


class PartnerSetupError(RuntimeError):
    """带错误码的可读失败（形状对齐 `DeepTutorError`，页面同一套分支处理）。"""

    def __init__(self, message: str, code: str = "PARTNER_SETUP_ERROR") -> None:
        super().__init__(message)
        self.code = code


# ── 人格正文 ────────────────────────────────────────────────────────────────

def split_soul_body(text: str) -> str:
    """取第一条水平分隔线（顶格 `---` 独占一行）**以下**的正文。

    分隔线以上是写给我们自己看的用法说明，粘进 DeepTutor 只会污染人格。
    旧 README 让用户肉眼判断这条线在哪——那正是出错的地方之一，这里做成机器判断。

    没有分隔线时退回整份文本：宁可多带几行说明，也不要静默返回空人格。
    """
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "---" and not line.startswith(" "):
            return "\n".join(lines[index + 1:]).strip() + "\n"
    return (text or "").strip() + "\n"


def load_soul_body(partner_name: str, soul_path: str | None = None) -> str:
    """读 `partner/SOUL.md` → 取分隔线以下 → 把名字占位换成真实名字。"""
    path = soul_path or SOUL_PATH
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise PartnerSetupError(
            f"读不到人格文件 {path}：{exc}。这份文件在仓库里（partner/SOUL.md），"
            "如果丢了就从版本库恢复一次。",
            code="SOUL_FILE_MISSING") from exc
    body = split_soul_body(raw)
    if not body.strip():
        raise PartnerSetupError(
            f"{path} 的分隔线以下是空的，注入进去等于给伙伴一个空人格。",
            code="SOUL_BODY_EMPTY")
    return body.replace(SOUL_NAME_PLACEHOLDER, partner_name)


def soul_matches(current: str, expected: str) -> bool:
    """比人格正文是否已是仓库这一版。只规整行尾与首尾空白，不做模糊匹配。"""
    def norm(text: str) -> str:
        return "\n".join(line.rstrip() for line in (text or "").replace(
            "\r\n", "\n").strip().splitlines())
    return norm(current) == norm(expected)


# ── 工具清单 ────────────────────────────────────────────────────────────────

def _entry_server(entry: dict[str, Any]) -> str:
    """条目属于哪个 MCP 服务器。优先用服务端给的 `server` 字段，缺了才从名字里猜。"""
    server = str(entry.get("server") or entry.get("provider_id") or "").strip()
    if server:
        return server
    name = str(entry.get("name") or "")
    return name[4:].rsplit("_", 1)[0] if name.startswith("mcp_") else ""


def _bare_tool_name(entry: dict[str, Any], server: str) -> str:
    """`mcp_tapd_get_stories_or_tasks` + server=`tapd` → `get_stories_or_tasks`。"""
    name = str(entry.get("name") or "")
    prefix = f"mcp_{server}_"
    return name[len(prefix):] if server and name.startswith(prefix) else name


def plan_mcp_tools(tool_options: dict[str, Any]) -> dict[str, Any]:
    """从 `tool-options` 里挑出该挂的工具名。

    **不拼名字，只从服务端返回里挑。** `mcp_tools` 的元素是
    `mcp_<服务器>_<工具>`，看着像能拼，但服务器名里本身可以带下划线和连字符，
    自己拼迟早拼错一个——错了的表现是"配置保存成功但伙伴看不到工具"。

    返回 `{"tools", "primary", "tapd", "tapd_present", "primary_present"}`。
    """
    entries = [e for e in (tool_options or {}).get("mcp_tools") or []
               if isinstance(e, dict) and e.get("name")]
    primary: list[str] = []
    tapd: list[str] = []
    tapd_present = False
    for entry in entries:
        server = _entry_server(entry)
        if server == PRIMARY_MCP_SERVER:
            primary.append(str(entry["name"]))
        elif server == TAPD_MCP_SERVER:
            tapd_present = True
            if _bare_tool_name(entry, server) in TAPD_READONLY_TOOLS:
                tapd.append(str(entry["name"]))
    return {
        "tools": primary + tapd,
        "primary": primary,
        "tapd": tapd,
        "primary_present": bool(primary),
        "tapd_present": tapd_present,
    }


def merge_mcp_tools(existing: list[str] | None,
                    required: list[str]) -> list[str] | None:
    """现有清单 ∪ 必备清单，现有顺序在前。

    `existing is None` 是 DeepTutor 里"不限制、全部放行"的写法（落盘是 `["*"]`）——
    这时候写一份白名单是**收窄**权限，会把用户手工放开的别的工具关掉。
    所以返回 `None` 表示"别动"。
    """
    if existing is None:
        return None
    merged = list(existing)
    for name in required:
        if name not in merged:
            merged.append(name)
    return merged


# ── 查找既有伙伴 ────────────────────────────────────────────────────────────

def find_partner(partners: list[dict[str, Any]], *, name: str = "",
                 partner_id: str = "") -> dict[str, Any] | None:
    """按 id 精确找，找不到再按显示名精确找。

    为什么不按 id 猜：中文名的 partner_id 是服务端算的 `partner-<sha1 前 8 位>`，
    在这边复刻那个算法就是给自己埋一个"哪天服务端改了算法就静默建重复伙伴"的坑。
    """
    if partner_id:
        for row in partners:
            if str(row.get("partner_id") or "") == partner_id:
                return row
    if name:
        for row in partners:
            if str(row.get("name") or "").strip() == name.strip():
                return row
    return None


# ── 主流程 ──────────────────────────────────────────────────────────────────

class _Steps:
    """逐步回显的收集器。页面按 `{name, ok, detail}` 渲染 √/×。"""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool, detail: str, **extra: Any) -> None:
        row: dict[str, Any] = {"name": name, "ok": ok, "detail": detail}
        row.update(extra)
        self.rows.append(row)

    def failed(self, code: str, **extra: Any) -> dict[str, Any]:
        return {"ok": False, "code": code, "steps": self.rows, **extra}


def ensure_partner(*, client: Any | None = None,
                   name: str = DEFAULT_PARTNER_NAME,
                   partner_id: str = "",
                   soul_path: str | None = None) -> dict[str, Any]:
    """不存在则创建、存在则补齐配置。返回逐步结果供页面回显。

    - `name`：新建时用的显示名。修复既有伙伴时**不会**拿它去改名。
    - `partner_id`：指定就修这一个（页面下拉选已有伙伴走这条）；不指定则按 `name` 找。

    返回 `{"ok", "code", "created", "partner_id", "name", "steps", "summary"}`。
    """
    client = client or DeepTutorClient()
    steps = _Steps()
    target_name = (name or DEFAULT_PARTNER_NAME).strip() or DEFAULT_PARTNER_NAME

    # 1) 人格正文（本地文件，先读——读不到就没必要去打扰 DeepTutor）
    try:
        soul_body = load_soul_body(target_name, soul_path)
    except PartnerSetupError as exc:
        steps.add("读取人格正文", False, str(exc))
        return steps.failed(exc.code, created=False, partner_id=partner_id,
                            name=target_name)
    steps.add("读取人格正文", True,
              f"取自 partner/SOUL.md 分隔线以下，{len(soul_body)} 字，"
              f"名字占位已替换为「{target_name}」")

    # 2) 工具清单（从 DeepTutor 抄真名，不拼）
    try:
        plan = plan_mcp_tools(client.partner_tool_options())
    except DeepTutorError as exc:
        steps.add("清点可挂载的 MCP 工具", False, str(exc), code=exc.code)
        return steps.failed(exc.code, created=False, partner_id=partner_id,
                            name=target_name)
    if not plan["primary_present"]:
        steps.add("清点可挂载的 MCP 工具", False,
                  f"DeepTutor 里看不到 {PRIMARY_MCP_SERVER} 服务器的任何工具。"
                  "先确认网关在跑、且它已注册进 DeepTutor（本页顶部状态区应显示 connected），"
                  "再回来点一次。工具这一步已跳过，不会动伙伴现有的工具配置。")
    else:
        tapd_note = (f"，tapd {len(plan['tapd'])} 个只读工具"
                     if plan["tapd"] else
                     "，tapd 未注册或未连上（跳过，HAR 链路不依赖它）")
        steps.add("清点可挂载的 MCP 工具", True,
                  f"{PRIMARY_MCP_SERVER} {len(plan['primary'])} 个工具{tapd_note}")

    # 3) 找既有伙伴
    try:
        partners = client.list_partners()
    except DeepTutorError as exc:
        steps.add("查找既有伙伴", False, str(exc), code=exc.code)
        return steps.failed(exc.code, created=False, partner_id=partner_id,
                            name=target_name)
    existing = find_partner(partners, name=target_name, partner_id=partner_id)
    if partner_id and existing is None:
        steps.add("查找既有伙伴", False,
                  f"DeepTutor 里没有 id 为 {partner_id} 的伙伴。它可能刚被删掉了，"
                  "刷新页面重新选一次。")
        return steps.failed("PARTNER_NOT_FOUND", created=False,
                            partner_id=partner_id, name=target_name)

    if existing is None:
        return _create(client, steps, target_name, soul_body, plan)
    return _repair(client, steps, existing, soul_body, plan)


def _create(client: Any, steps: _Steps, target_name: str, soul_body: str,
            plan: dict[str, Any]) -> dict[str, Any]:
    """新建：人格、工具、技能、语言一次性随创建请求带过去。"""
    payload: dict[str, Any] = {
        "name": target_name,
        "description": DEFAULT_DESCRIPTION,
        "language": DEFAULT_LANGUAGE,
        "emoji": DEFAULT_EMOJI,
        "soul": {"source": "custom", "content": soul_body},
        "assets": {"knowledge_bases": [], "skills": list(REQUIRED_SKILLS),
                   "notebooks": []},
        "start": True,
    }
    # 工具没清点到就不发这个键：发 `[]` 会把伙伴建成"MCP 全关"，
    # 而这正是我们要修的那个毛病。不发则由服务端用它自己的默认值。
    if plan["primary_present"]:
        payload["mcp_tools"] = list(plan["tools"])
    # llm_selection 不发：服务端 `None` = 用系统默认模型。在这里猜一个
    # profile_id 只会把伙伴钉死在某个可能已经被删掉的模型上。

    try:
        created = client.create_partner(payload)
    except DeepTutorError as exc:
        steps.add("创建伙伴", False, str(exc), code=exc.code)
        return steps.failed(exc.code, created=False, partner_id="", name=target_name)

    new_id = str((created or {}).get("partner_id") or "")
    steps.add("创建伙伴", True,
              f"已新建「{target_name}」（id: {new_id or '未知'}），语言中文，"
              f"人格正文随创建写入，工具 {len(payload.get('mcp_tools') or [])} 个")

    provisioning = (created or {}).get("provisioning") or {}
    _report_skills(steps, provisioning, expected=list(REQUIRED_SKILLS))

    start_error = (created or {}).get("start_error")
    if start_error:
        steps.add("启动伙伴", False,
                  f"伙伴建好了但没起来：{start_error}。去 DeepTutor 的伙伴页手工点一次启动。")

    return _verify(client, steps, new_id, target_name, soul_body, plan, created=True)


def _repair(client: Any, steps: _Steps, existing: dict[str, Any], soul_body: str,
            plan: dict[str, Any]) -> dict[str, Any]:
    """修复既有：只补该补的，逐项说明改了什么、没改什么。"""
    pid = str(existing.get("partner_id") or "")
    display = str(existing.get("name") or pid)
    steps.add("查找既有伙伴", True,
              f"命中已有伙伴「{display}」（id: {pid}）。下面只补配置，不改名、不删任何东西。")

    # 3a) 配置：工具 + 语言
    patch: dict[str, Any] = {}
    tool_note = ""
    if plan["primary_present"]:
        current_tools = existing.get("mcp_tools")
        current_tools = list(current_tools) if isinstance(current_tools, list) else None
        merged = merge_mcp_tools(current_tools, plan["tools"])
        if merged is None:
            tool_note = "工具保持「不限制」（这个伙伴原本就放行全部 MCP 工具，收窄反而是退步）"
        elif merged == current_tools:
            tool_note = f"工具已是最新（{len(merged)} 个，无需改动）"
        else:
            patch["mcp_tools"] = merged
            added = [t for t in merged if t not in (current_tools or [])]
            tool_note = (f"工具 {len(current_tools or [])} → {len(merged)} 个，"
                         f"新挂：{'、'.join(added)}")
    else:
        tool_note = "工具这一步已跳过（上一步没清点到 test-partner 的工具）"

    language = str(existing.get("language") or "")
    if language != DEFAULT_LANGUAGE:
        patch["language"] = DEFAULT_LANGUAGE

    if patch:
        try:
            client.update_partner(pid, patch)
        except DeepTutorError as exc:
            steps.add("更新伙伴配置", False, str(exc), code=exc.code)
            return steps.failed(exc.code, created=False, partner_id=pid, name=display)
        lang_note = "，语言设为中文" if "language" in patch else ""
        steps.add("更新伙伴配置", True, tool_note + lang_note)
    else:
        steps.add("更新伙伴配置", True, tool_note + "；语言已是中文，没有要改的")

    # 3b) 人格正文（PATCH 收不了，只能走 PUT /<id>/soul）
    try:
        current_soul = client.get_partner_soul(pid)
    except DeepTutorError as exc:
        steps.add("写入人格正文", False, str(exc), code=exc.code)
        return steps.failed(exc.code, created=False, partner_id=pid, name=display)
    if soul_matches(current_soul, soul_body):
        steps.add("写入人格正文", True, "人格正文已是仓库这一版，无需改写")
    else:
        try:
            client.put_partner_soul(pid, soul_body)
        except DeepTutorError as exc:
            steps.add("写入人格正文", False, str(exc), code=exc.code)
            return steps.failed(exc.code, created=False, partner_id=pid, name=display)
        origin = (existing.get("soul_origin") or {})
        was = f"{origin.get('type') or '?'}/{origin.get('id') or ''}".rstrip("/")
        steps.add("写入人格正文", True,
                  f"已用 partner/SOUL.md 的正文覆盖（原来是 {was}）。"
                  "注意：伙伴详情里的「人格来源」标签仍显示原值——那只是创建时的出处记录，"
                  "DeepTutor 运行时读的是刚写进去的这份正文。")

    # 3c) 技能（服务端是复制进伙伴工作区，已存在则跳过）
    try:
        report = client.add_partner_assets(pid, skills=list(REQUIRED_SKILLS))
    except DeepTutorError as exc:
        steps.add("绑定技能", False, str(exc), code=exc.code)
        return steps.failed(exc.code, created=False, partner_id=pid, name=display)
    _report_skills(steps, report, expected=list(REQUIRED_SKILLS))

    return _verify(client, steps, pid, display, soul_body, plan, created=False)


def _report_skills(steps: _Steps, report: dict[str, Any],
                   expected: list[str]) -> None:
    """把 `provision_assets` 的报告翻译成一行人话。"""
    copied = ((report or {}).get("copied") or {}).get("skills") or []
    errors = [e for e in (report or {}).get("errors") or []
              if (e or {}).get("type") == "skill"]
    if errors:
        detail = "；".join(f"{e.get('name')}：{e.get('error')}" for e in errors)
        steps.add("绑定技能", False,
                  f"这些技能没绑上——{detail}。"
                  "技能是复制进伙伴工作区的，源目录得先在 DeepTutor 的技能库里存在"
                  "（仓库 skills/ 下的两份 SKILL.md 要先导入）。")
        return
    steps.add("绑定技能", True,
              f"{'、'.join(copied or expected)} 已在伙伴工作区（已存在的会跳过，重复点没有副作用）")


def _verify(client: Any, steps: _Steps, pid: str, display: str, soul_body: str,
            plan: dict[str, Any], *, created: bool) -> dict[str, Any]:
    """回读核对。不回读就只能相信写请求的返回码——那不叫验证。"""
    try:
        row = client.get_partner(pid)
        soul = client.get_partner_soul(pid)
        assets = client.get_partner_assets(pid)
    except DeepTutorError as exc:
        steps.add("回读核对", False, str(exc), code=exc.code)
        return steps.failed(exc.code, created=created, partner_id=pid, name=display)

    tools = row.get("mcp_tools")
    tool_count = "不限制" if tools is None else str(len(tools))
    skill_names = [str((s or {}).get("name") or "")
                   for s in (assets or {}).get("skills") or []]
    missing_skills = [s for s in REQUIRED_SKILLS if s not in skill_names]
    soul_ok = soul_matches(soul, soul_body)
    missing_tools = ([] if tools is None
                     else [t for t in plan["tools"] if t not in tools])

    ok = soul_ok and not missing_skills and not (
        missing_tools and plan["primary_present"])
    problems: list[str] = []
    if not soul_ok:
        problems.append("人格正文与仓库版本对不上")
    if missing_skills:
        problems.append(f"技能缺：{'、'.join(missing_skills)}")
    if missing_tools and plan["primary_present"]:
        problems.append(f"工具缺：{'、'.join(missing_tools)}")

    detail = (f"伙伴「{row.get('name') or display}」：人格正文"
              f"{'已是仓库版本' if soul_ok else '不是仓库版本'}，"
              f"工具 {tool_count} 个，技能 {len(skill_names)} 份"
              f"（{'、'.join(skill_names) or '无'}）")
    if problems:
        detail += "。仍有问题：" + "；".join(problems)
    steps.add("回读核对", ok, detail)

    return {
        "ok": ok,
        "code": "OK" if ok else "INCOMPLETE",
        "created": created,
        "partner_id": pid,
        "name": str(row.get("name") or display),
        "steps": steps.rows,
        "summary": partner_summary(row, soul, assets, soul_body),
    }


# ── 状态投影（页面状态区用） ────────────────────────────────────────────────

def partner_summary(row: dict[str, Any], soul: str, assets: dict[str, Any],
                    expected_soul: str) -> dict[str, Any]:
    """单个伙伴的体检投影：人格对不对、挂了几个工具、绑了哪些技能。"""
    tools = row.get("mcp_tools")
    skill_names = [str((s or {}).get("name") or "")
                   for s in (assets or {}).get("skills") or []]
    return {
        "partner_id": str(row.get("partner_id") or ""),
        "name": str(row.get("name") or ""),
        "running": bool(row.get("running")),
        "language": str(row.get("language") or ""),
        "soul_origin": row.get("soul_origin") or {},
        "soul_ok": soul_matches(soul, expected_soul),
        "mcp_unrestricted": tools is None,
        "tool_count": None if tools is None else len(tools),
        "tools": list(tools or []),
        "skills": skill_names,
        "missing_skills": [s for s in REQUIRED_SKILLS if s not in skill_names],
    }


def partner_overview(client: Any | None = None,
                     soul_path: str | None = None,
                     include: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
    """页面「测试伙伴」分区的状态区数据。

    体检只对**候选伙伴**做（默认名那个 + 已挂了 test-partner 工具的那些）——
    逐个伙伴拉 soul + assets 是 2 次请求，实例里伙伴多起来会把页面拖慢，
    而与本能力无关的伙伴（别人的学习助手）也不该被我们体检。

    `include` 是这条规则的出口：用户在下拉里选中谁，谁就一定被体检。
    没有这个出口的话，用户手工建的那个坏伙伴反而看不到——它名字不叫「测试伙伴」、
    工具也一个没挂，正好两条自动规则都不命中，而它恰恰是最需要被看见的那个。
    """
    client = client or DeepTutorClient()
    wanted = {pid for pid in include if pid}
    result: dict[str, Any] = {"ok": True, "default_name": DEFAULT_PARTNER_NAME,
                              "partners": [], "candidates": []}
    try:
        partners = client.list_partners()
    except DeepTutorError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc),
                "default_name": DEFAULT_PARTNER_NAME, "partners": [],
                "candidates": []}

    # 下拉框要的是全部伙伴（用户可能想修的是随便哪一个）。
    result["partners"] = [
        {"partner_id": str(p.get("partner_id") or ""),
         "name": str(p.get("name") or ""),
         "running": bool(p.get("running"))}
        for p in partners
    ]

    try:
        plan = plan_mcp_tools(client.partner_tool_options())
        result["mcp"] = {"primary_present": plan["primary_present"],
                         "primary_count": len(plan["primary"]),
                         "tapd_present": plan["tapd_present"],
                         "tapd_count": len(plan["tapd"])}
    except DeepTutorError as exc:
        result["mcp"] = {"primary_present": False, "primary_count": 0,
                         "tapd_present": False, "tapd_count": 0,
                         "error": str(exc)}

    for row in partners:
        pid = str(row.get("partner_id") or "")
        tools = row.get("mcp_tools")
        is_candidate = (
            pid in wanted
            or str(row.get("name") or "").strip() == DEFAULT_PARTNER_NAME
            or (isinstance(tools, list)
                and any(t.startswith(f"mcp_{PRIMARY_MCP_SERVER}_") for t in tools))
        )
        if not is_candidate or not pid:
            continue
        try:
            expected = load_soul_body(str(row.get("name") or ""), soul_path)
            summary = partner_summary(row, client.get_partner_soul(pid),
                                      client.get_partner_assets(pid), expected)
        except (DeepTutorError, PartnerSetupError) as exc:
            summary = {"partner_id": pid, "name": str(row.get("name") or ""),
                       "error": str(exc)}
        result["candidates"].append(summary)

    return result
