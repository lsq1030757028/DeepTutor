"""Owner-scoped, non-secret TAPD context for Test turns.

Credentials stay exclusively in MCP Services.  This store contains only the
small amount of business context needed to avoid asking a tester the same
questions on every turn: their TAPD display identity, testing role, and an
optional default project.  The accessible project set is never authoritative
here; every Test turn refreshes it from the ``tapd_projects`` MCP tool.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable
import unicodedata

PROFILE_VERSION = 1
PROFILE_DIRNAME = "tapd-context"
MAX_PROFILE_TEXT = 120
_SAFE_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


@dataclass(frozen=True, slots=True)
class TapdProject:
    id: str
    name: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TapdProfile:
    tapd_identity: str = ""
    role: str = ""
    default_project: TapdProject | None = None


@dataclass(frozen=True, slots=True)
class TapdContextResolution:
    status: str
    projects: tuple[TapdProject, ...] = ()
    selected_project: TapdProject | None = None
    project_source: str = ""
    tapd_identity: str = ""
    role: str = ""
    missing: tuple[str, ...] = ()
    default_invalid: bool = False
    error: str = ""


def profile_path(owner_id: str) -> Path:
    """Return the profile path for one human owner; reject unsafe filenames."""
    if not _SAFE_OWNER_RE.fullmatch(str(owner_id or "")):
        raise ValueError("Unsafe TAPD context owner id")
    from deeptutor.multi_user.paths import SYSTEM_ROOT

    return SYSTEM_ROOT / PROFILE_DIRNAME / f"{owner_id}.json"


def load_profile(owner_id: str) -> TapdProfile:
    try:
        path = profile_path(owner_id)
    except ValueError:
        return TapdProfile()
    if not path.exists():
        return TapdProfile()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return TapdProfile()
    if not isinstance(payload, dict) or payload.get("version") != PROFILE_VERSION:
        return TapdProfile()
    default_raw = payload.get("default_project")
    default = _coerce_project(default_raw) if isinstance(default_raw, dict) else None
    return TapdProfile(
        tapd_identity=_clean_text(payload.get("tapd_identity")),
        role=_clean_text(payload.get("role")),
        default_project=default,
    )


def save_profile(owner_id: str, profile: TapdProfile) -> TapdProfile:
    """Atomically save only the allow-listed, non-secret profile fields."""
    cleaned = TapdProfile(
        tapd_identity=_clean_text(profile.tapd_identity),
        role=_clean_text(profile.role),
        default_project=(
            TapdProject(
                id=_clean_text(profile.default_project.id),
                name=_clean_text(profile.default_project.name),
            )
            if profile.default_project
            else None
        ),
    )
    path = profile_path(owner_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, stat.S_IRWXU)
    payload: dict[str, Any] = {
        "version": PROFILE_VERSION,
        "tapd_identity": cleaned.tapd_identity,
        "role": cleaned.role,
        "default_project": (cleaned.default_project.to_dict() if cleaned.default_project else None),
    }
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return cleaned


def parse_projects_payload(raw: Any) -> tuple[list[TapdProject], str]:
    """Parse the read-only ``tapd_projects`` envelope without trusting its shape."""
    if hasattr(raw, "success") and getattr(raw, "success") is False:
        return [], "TAPD MCP returned an explicit failure"
    content = getattr(raw, "content", raw)
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return [], "TAPD MCP did not return a project envelope"
    if not isinstance(content, dict):
        return [], "TAPD MCP did not return a project envelope"
    if str(content.get("status") or "").lower() not in {"ok", "success"}:
        message = content.get("message") or content.get("error") or "TAPD project discovery failed"
        return [], _clean_text(message)
    data = content.get("data")
    rows = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [], "TAPD MCP response has no project list"
    projects: list[TapdProject] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        project = _coerce_project(row)
        if project is None or (project.id, project.name.casefold()) in seen:
            continue
        seen.add((project.id, project.name.casefold()))
        projects.append(project)
    return projects, ""


def resolve_context(
    projects: Iterable[TapdProject],
    *,
    profile: TapdProfile,
    user_message: str = "",
    session_project: TapdProject | None = None,
    discovery_error: str = "",
) -> TapdContextResolution:
    """Resolve this turn using request > session > default > singleton.

    A request/session override never mutates the long-term default.  Callers may
    persist a default only after a separate, explicit user answer.
    """
    accessible = tuple(projects)
    if discovery_error:
        return TapdContextResolution(status="unavailable", error=discovery_error)
    if not accessible:
        return TapdContextResolution(status="no_projects")

    selected: TapdProject | None = None
    source = ""
    mentions = _mentioned_projects(user_message, accessible)
    if len(mentions) == 1:
        selected, source = mentions[0], "request"
    elif len(mentions) > 1:
        selected = None
    else:
        session_match = _accessible_match(session_project, accessible)
        default_match = _accessible_match(profile.default_project, accessible)
        if session_match is not None:
            selected, source = session_match, "session"
        elif default_match is not None:
            selected, source = default_match, "default"
        elif len(accessible) == 1:
            selected, source = accessible[0], "single_accessible"

    missing: list[str] = []
    if selected is None:
        missing.append("project")
    if not profile.tapd_identity:
        missing.append("tapd_identity")
    if not profile.role:
        missing.append("role")
    return TapdContextResolution(
        status="needs_input" if missing else "ready",
        projects=accessible,
        selected_project=selected,
        project_source=source,
        tapd_identity=profile.tapd_identity,
        role=profile.role,
        missing=tuple(missing),
        default_invalid=(
            profile.default_project is not None
            and _accessible_match(profile.default_project, accessible) is None
        ),
    )


def render_context_briefing(
    resolution: TapdContextResolution,
    *,
    language: str,
    tapd_required: bool = True,
) -> str:
    """Render trusted host context and exact ask_user ids for the Test agent."""
    zh = str(language or "").lower().startswith("zh")
    if not tapd_required:
        return (
            "[TAPD 上下文：尚未启用]\n本次请求尚未选择 TAPD 作为需求来源。不要询问 TAPD 项目、身份或角色；"
            "用户已给本地需求正文时直接走本地 oracle。只有需求来源确实不清楚时，才用一句业务问题确认"
            "“从 TAPD 取需求，还是使用当前提供的正文？”。用户选择 TAPD 后，才按本轮已发现的项目范围补全上下文。"
            if zh
            else "[TAPD context: not selected]\nThis request has not selected TAPD as its requirement source. "
            "Do not ask for a TAPD project, identity, or role. If local requirement text is present, "
            "continue with the local oracle. Only when the source is genuinely unclear, ask one business "
            "question: 'Fetch the requirement from TAPD, or use the text provided here?' Complete TAPD "
            "context only after the user selects TAPD."
        )
    if resolution.status == "unavailable":
        return (
            "[TAPD 上下文：不可用]\n"
            "TAPD MCP 没有返回可信项目范围。停止 TAPD 接入，不要猜项目，也不要向用户索要令牌或项目 ID。"
            "请用业务语言说明：到现有 MCP Services 检查 TAPD 连接后再试。"
            if zh
            else "[TAPD context: unavailable]\nTAPD MCP did not return a trusted project scope. "
            "Stop TAPD intake; do not guess a project or ask for a token/workspace id. Ask the "
            "user to check the existing TAPD connection in MCP Services and try again."
        )
    if resolution.status == "no_projects":
        return (
            "[TAPD 上下文：无可用项目]\n令牌没有返回可访问项目。停止 TAPD 接入，不要索要项目 ID；"
            "请用户到现有 MCP Services 检查 TAPD 权限。"
            if zh
            else "[TAPD context: no accessible projects]\nStop TAPD intake and do not ask for a "
            "workspace id. Ask the user to check TAPD access in the existing MCP Services page."
        )

    project = resolution.selected_project
    lines = ["[TAPD 测试上下文（宿主已核验）]" if zh else "[TAPD Test Context (host verified)]"]
    if project is not None:
        label = "本次项目" if zh else "Project for this session"
        lines.append(f"- {label}: {project.name} (internal workspace_id={project.id})")
    if resolution.tapd_identity:
        lines.append(f"- {'TAPD 身份' if zh else 'TAPD identity'}: {resolution.tapd_identity}")
    if resolution.role:
        lines.append(f"- {'用户角色' if zh else 'User role'}: {resolution.role}")
    if resolution.status == "ready":
        lines.append(
            "上下文已齐，直接继续测试；不要再询问项目 ID、身份或角色。项目 ID 仅作为工具参数，不向用户展示。"
            if zh
            else "Context is complete. Continue directly; do not ask for workspace id, identity, "
            "or role. Keep the workspace id inside tool arguments."
        )
        return "\n".join(lines)

    lines.append(
        "上下文缺失。只调用一次 ask_user，把下列缺项放在同一张卡；使用这些固定 id，"
        "不要先发表学习教练式自我介绍，也不要问 workspace_id。"
        if zh
        else "Context is incomplete. Call ask_user once with the missing questions on one card, "
        "using these exact ids. Do not introduce yourself as a learning coach and do not ask for a workspace id."
    )
    if "project" in resolution.missing:
        shown = resolution.projects[:8]
        names = " / ".join(project.name for project in shown)
        if len(resolution.projects) > len(shown):
            names += " / …（可输入其他项目业务名）" if zh else " / … (type another project name)"
        lines.append(
            f"- tapd_context_project: {'这次要验收哪个项目？' if zh else 'Which project are you testing this time?'} "
            f"{'选项' if zh else 'Options'}={names}"
        )
        lines.append(
            "- tapd_context_remember_project: 以后默认使用这次选择吗？选项=仅本次 / 设为默认"
            if zh
            else "- tapd_context_remember_project: Remember this as your default? Options=This time only / Set as default"
        )
    if "tapd_identity" in resolution.missing:
        lines.append(
            "- tapd_context_identity: 你在 TAPD 中显示的姓名或昵称是什么？（自由输入）"
            if zh
            else "- tapd_context_identity: What name or nickname identifies you in TAPD? (free text)"
        )
    if "role" in resolution.missing:
        lines.append(
            "- tapd_context_role: 你这次以什么角色验收？选项=一线测试人员 / QA 负责人 / 手工验收人员"
            if zh
            else "- tapd_context_role: What is your role for this acceptance? Options=Frontline tester / QA lead / Manual acceptance tester"
        )
    lines.append(
        "项目选择只覆盖当前会话；只有用户明确选择“设为默认”才允许改长期默认。"
        if zh
        else "A project selection overrides only this session; change the long-term default only after an explicit 'Set as default' answer."
    )
    return "\n".join(lines)


def answer_map(raw_reply: Any) -> dict[str, str]:
    if not isinstance(raw_reply, dict):
        return {}
    rows = raw_reply.get("answers")
    if not isinstance(rows, list):
        return {}
    answers: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        qid = _clean_text(row.get("questionId") or row.get("id"))
        if qid:
            answers[qid] = _clean_text(row.get("text"))
    return answers


def project_from_answer(answer: str, projects: Iterable[TapdProject]) -> TapdProject | None:
    value = _clean_text(answer).casefold()
    matches = [p for p in projects if value in {p.name.casefold(), p.id.casefold()}]
    return matches[0] if len(matches) == 1 else None


def wants_default(answer: str) -> bool:
    return _clean_text(answer).casefold() in {
        "设为默认",
        "set as default",
        "remember",
        "yes",
    }


def _mentioned_projects(message: str, projects: tuple[TapdProject, ...]) -> list[TapdProject]:
    text = str(message or "").casefold()
    out: list[TapdProject] = []
    for project in projects:
        name = project.name.casefold().strip()
        if len(name) < 2:
            continue
        if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text) or (
            any("\u4e00" <= char <= "\u9fff" for char in name) and name in text
        ):
            out.append(project)
    return out


def _accessible_match(
    selected: TapdProject | None, projects: tuple[TapdProject, ...]
) -> TapdProject | None:
    if selected is None:
        return None
    for project in projects:
        if project.id == selected.id or project.name.casefold() == selected.name.casefold():
            return project
    return None


def _coerce_project(raw: Any) -> TapdProject | None:
    if not isinstance(raw, dict):
        return None
    project_id = _clean_text(raw.get("id") or raw.get("workspace_id"))
    name = _clean_text(raw.get("name"))
    if not project_id or not name:
        return None
    return TapdProject(id=project_id, name=name)


def _clean_text(value: Any) -> str:
    # Project/profile values enter a trusted host briefing.  A remote MCP or a
    # hand-edited profile must not be able to add prompt lines or invisible
    # directionality/control marks through that channel.
    printable = "".join(
        " " if unicodedata.category(char).startswith("C") else char for char in str(value or "")
    )
    return " ".join(printable.split())[:MAX_PROFILE_TEXT]


__all__ = [
    "PROFILE_DIRNAME",
    "TapdContextResolution",
    "TapdProfile",
    "TapdProject",
    "answer_map",
    "load_profile",
    "parse_projects_payload",
    "profile_path",
    "project_from_answer",
    "render_context_briefing",
    "resolve_context",
    "save_profile",
    "wants_default",
]
