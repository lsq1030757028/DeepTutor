"""User-safe adapter for TAPD baseline, semantic, and business-query results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from deeptutor.services.tapd_context import TapdProject

SUPPORTED_BUSINESS_QUESTION = "最近分配给我且未开始测试的需求"


@dataclass(frozen=True, slots=True)
class TapdStory:
    id: str
    title: str
    status: str
    assignee: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class TapdBusinessOutcome:
    status: str
    stories: tuple[TapdStory, ...] = ()
    matched_count: int = 0
    truncated: bool = False
    error_code: str = ""


@dataclass(frozen=True, slots=True)
class TapdSemanticGate:
    ready: bool
    error_code: str = ""


def is_supported_business_question(message: str) -> bool:
    folded = str(message or "").casefold()
    if SUPPORTED_BUSINESS_QUESTION in folded:
        return True
    entity = any(cue in folded for cue in ("需求", "故事"))
    assignment_cues = ("分配给我", "分给我", "分派给我", "指派给我", "派给我")
    explicit_assignment = any(cue in folded for cue in assignment_cues)
    owned_collection = bool(
        re.search(
            r"(?:我的|我负责的|我名下的|我手上的)(?:需求|故事)"
            r"|我(?:这边|这里|手上|名下)?(?:最近|近期|现在|目前)?"
            r"还?(?:有|剩)(?:哪些|哪几|多少)?(?:个|条)?(?:需求|故事)",
            folded,
        )
    )
    personal = explicit_assignment or owned_collection
    pending = any(
        cue in folded
        for cue in (
            "未开始测试",
            "还没开始测",
            "没开始测试",
            "没开始测",
            "还没测",
            "待测",
            "未测",
        )
    )
    recency = any(cue in folded for cue in ("最近", "近期", "最新"))
    return entity and personal and pending and (recency or explicit_assignment)


def baseline_is_ready(payload: Mapping[str, Any] | None) -> bool:
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"基线", "建立于", "各工作项", "下一步"}
        or payload.get("基线") != "可用"
    ):
        return False
    established = payload.get("建立于")
    domains = payload.get("各工作项")
    next_action = payload.get("下一步")
    return (
        isinstance(established, str)
        and bool(established.strip())
        and isinstance(domains, Mapping)
        and bool(domains)
        and all(
            isinstance(key, str)
            and bool(key.strip())
            and isinstance(value, str)
            and bool(value.strip())
            for key, value in domains.items()
        )
        and (next_action is None or isinstance(next_action, str))
    )


def semantic_is_ready(payload: Mapping[str, Any] | None) -> bool:
    return semantic_gate(payload).ready


def semantic_gate(
    payload: Mapping[str, Any] | None,
    *,
    expected_identity_source: str = "",
) -> TapdSemanticGate:
    if not isinstance(payload, Mapping):
        return TapdSemanticGate(False, "MCP_RESULT_INVALID")
    status = payload.get("status")
    if not isinstance(status, str) or status not in {"ok", "blocked", "needs_review"}:
        return TapdSemanticGate(False, "MCP_RESULT_INVALID")
    if not _valid_read_markers(payload):
        return TapdSemanticGate(False, "MCP_RESULT_INVALID")
    for key in ("现在能答", "现在不能答"):
        if not _string_list(payload.get(key), allow_empty=True):
            return TapdSemanticGate(False, "MCP_RESULT_INVALID")
    for key in ("已确认谓词", "失效谓词"):
        value = payload.get(key)
        if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
            return TapdSemanticGate(False, "MCP_RESULT_INVALID")
    if status == "ok":
        if (
            not _empty_error(payload.get("error"))
            or payload.get("语义层可用") is not True
            or SUPPORTED_BUSINESS_QUESTION not in payload["现在能答"]
            or SUPPORTED_BUSINESS_QUESTION in payload["现在不能答"]
            or payload.get("身份上下文") not in {"session_context", "persistent_profile"}
            or (expected_identity_source and payload.get("身份上下文") != expected_identity_source)
        ):
            return TapdSemanticGate(False, "MCP_RESULT_INVALID")
        return TapdSemanticGate(True)
    error = _error_code(payload.get("error"))
    if (
        not error
        or payload.get("语义层可用") is not False
        or SUPPORTED_BUSINESS_QUESTION not in payload["现在不能答"]
    ):
        return TapdSemanticGate(False, "MCP_RESULT_INVALID")
    return TapdSemanticGate(False, error)


def parse_business_query(
    payload: Mapping[str, Any] | None,
    *,
    expected_workspace_id: str = "",
    expected_identity_source: str = "",
) -> TapdBusinessOutcome:
    if not isinstance(payload, Mapping):
        return TapdBusinessOutcome("blocked", error_code="MCP_RESULT_INVALID")
    status = payload.get("status")
    if not isinstance(status, str) or not _valid_read_markers(payload):
        return TapdBusinessOutcome("blocked", error_code="MCP_RESULT_INVALID")
    if status != "ok":
        error_code = _error_code(payload.get("error"))
        if not _valid_blocked_business_payload(payload) or not error_code:
            return TapdBusinessOutcome("blocked", error_code="MCP_RESULT_INVALID")
        return TapdBusinessOutcome("blocked", error_code=error_code)
    if not _empty_error(payload.get("error")) or payload.get("可答") is not True:
        return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
    rows = payload.get("条目")
    rubric = payload.get("口径")
    evidence = payload.get("证据")
    if (
        not isinstance(rows, list)
        or not isinstance(rubric, list)
        or any(not isinstance(item, Mapping) for item in rubric)
        or not isinstance(evidence, Mapping)
        or not isinstance(evidence.get("工作区"), str)
        or any(not isinstance(value, str) for value in evidence.values())
        or evidence.get("身份上下文") not in {"session_context", "persistent_profile"}
        or (expected_identity_source and evidence.get("身份上下文") != expected_identity_source)
        or (expected_workspace_id and evidence.get("工作区") != expected_workspace_id)
    ):
        return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
    stories: list[TapdStory] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
        if set(row) != {"标题", "链接", "状态", "指派人", "最近更新"}:
            return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
        if any(
            not isinstance(row.get(key), str) or not row[key].strip()
            for key in ("标题", "链接", "状态", "指派人", "最近更新")
        ):
            return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
        link_workspace, story_id = _story_reference_from_link(row.get("链接"))
        title = _clean(row.get("标题"))
        if (
            not story_id
            or not title
            or story_id in seen
            or (expected_workspace_id and link_workspace != expected_workspace_id)
        ):
            return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
        seen.add(story_id)
        stories.append(
            TapdStory(
                id=story_id,
                title=title,
                status=_clean(row.get("状态")),
                assignee=_clean(row.get("指派人")),
                updated_at=_clean(row.get("最近更新")),
            )
        )
    count = payload.get("计数")
    truncated = payload.get("是否截断")
    if (
        not isinstance(count, Mapping)
        or set(count) != {"符合", "返回"}
        or not isinstance(truncated, bool)
    ):
        return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
    matched = count.get("符合")
    returned = count.get("返回")
    if (
        isinstance(matched, bool)
        or not isinstance(matched, int)
        or isinstance(returned, bool)
        or not isinstance(returned, int)
    ):
        return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
    matched_count = matched
    returned_count = returned
    if (
        matched_count < 0
        or returned_count != len(stories)
        or matched_count < returned_count
        or truncated != (matched_count > returned_count)
    ):
        return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
    modified = [_modified_timestamp(story.updated_at) for story in stories]
    if any(value is None for value in modified):
        return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
    timestamps = [value for value in modified if value is not None]
    if len({value.tzinfo is None for value in timestamps}) > 1:
        return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
    if timestamps != sorted(timestamps, reverse=True):
        return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
    return TapdBusinessOutcome(
        "empty" if not stories else "ok",
        stories=tuple(stories),
        matched_count=matched_count,
        truncated=truncated,
    )


def render_baseline_block(*, language: str) -> str:
    return (
        "[TAPD 可信查询：暂不可用]\n这个项目的字段口径尚未建立或已经变化；请先完成一次字段口径核对。"
        if str(language).lower().startswith("zh")
        else "[Trusted TAPD query: unavailable]\nThe project's field baseline is missing or changed; complete one field review first."
    )


def render_semantic_block(*, language: str, error_code: str = "") -> str:
    zh = str(language).lower().startswith("zh")
    if zh:
        return {
            "USER_PROFILE_REQUIRED": "[TAPD 可信查询：需要补全身份]\n请先在共享上下文中明确 TAPD 身份；本轮不猜指派人。",
            "PROFILE_WORKSPACE_MISMATCH": "[TAPD 可信查询：项目不一致]\n已确认身份与当前项目不匹配；请重新确认本次项目后再查。",
            "SEMANTIC_UNCONFIRMED": "[TAPD 可信查询：需要确认]\n“未开始测试”的含义尚未确认。请按 TAPD 页面显示的业务状态核对一次并确认；本轮不猜状态。",
            "STATUS_WORKFLOW_DRIFT": "[TAPD 可信查询：口径已失效]\n项目的工作流状态已变化；请重新对照 TAPD 页面确认“未开始测试”。",
            "WORKFLOW_SCHEMA_UNSUPPORTED": "[TAPD 可信查询：格式未核验]\n当前工作流返回格式尚未核验；核实状态图格式后再试。",
            "SEMANTIC_RECORD_INVALID": "[TAPD 可信查询：口径记录无效]\n已保存的业务口径无法验证；请重新对照 TAPD 页面确认。",
            "SESSION_CONTEXT_INVALID": "[TAPD 可信查询：本次身份无效]\n请重新确认本次身份、角色和项目后再查。",
            "SESSION_CONTEXT_EXPIRED": "[TAPD 可信查询：本次身份已过期]\n请重新确认本次身份、角色和项目。",
            "SESSION_CONTEXT_WORKSPACE_MISMATCH": "[TAPD 可信查询：项目不一致]\n本次身份不属于当前项目；请重新确认项目。",
            "SESSION_CONTEXT_REVOKED": "[TAPD 可信查询：权限已变化]\n当前项目权限已撤销；请重新确认可访问项目。",
            "SESSION_CONTEXT_SCOPE_UNAVAILABLE": "[TAPD 可信查询：范围无法核实]\n当前项目范围暂时无法核实；恢复项目范围读取后重试。",
            "SESSION_CONTEXT_SCOPE_INVALID": "[TAPD 可信查询：范围格式无效]\n项目范围返回格式无法核实；核实格式后重试。",
        }.get(
            error_code,
            "[TAPD 可信查询：失败关闭]\n语义检查没有返回可信结果；本轮不猜状态。",
        )
    return {
        "USER_PROFILE_REQUIRED": "[Trusted TAPD query: identity required]\nConfirm the TAPD identity in shared context first; this turn will not guess the assignee.",
        "PROFILE_WORKSPACE_MISMATCH": "[Trusted TAPD query: project mismatch]\nThe confirmed identity does not match the current project; confirm this session's project again.",
        "SEMANTIC_UNCONFIRMED": "[Trusted TAPD query: confirmation required]\nReview and confirm which exact visible business statuses mean not started; this turn will not guess.",
        "STATUS_WORKFLOW_DRIFT": "[Trusted TAPD query: semantics expired]\nThe workflow statuses changed; review and confirm the not-started meaning again.",
        "WORKFLOW_SCHEMA_UNSUPPORTED": "[Trusted TAPD query: unverified format]\nVerify the workflow response format before retrying.",
        "SEMANTIC_RECORD_INVALID": "[Trusted TAPD query: invalid semantics]\nReview and confirm the saved business meaning again before retrying.",
        "SESSION_CONTEXT_INVALID": "[Trusted TAPD query: invalid session identity]\nConfirm this session's identity, role, and project again.",
        "SESSION_CONTEXT_EXPIRED": "[Trusted TAPD query: session identity expired]\nConfirm this session's identity, role, and project again.",
        "SESSION_CONTEXT_WORKSPACE_MISMATCH": "[Trusted TAPD query: project mismatch]\nThe session identity does not belong to the current project; confirm the project again.",
        "SESSION_CONTEXT_REVOKED": "[Trusted TAPD query: access changed]\nProject access was revoked; confirm an accessible project again.",
        "SESSION_CONTEXT_SCOPE_UNAVAILABLE": "[Trusted TAPD query: scope unavailable]\nRestore project-scope access and retry.",
        "SESSION_CONTEXT_SCOPE_INVALID": "[Trusted TAPD query: invalid scope format]\nVerify the project-scope response format and retry.",
    }.get(
        error_code,
        "[Trusted TAPD query: failed closed]\nSemantic readiness was not trustworthy, so this turn will not guess.",
    )


def render_business_outcome(outcome: TapdBusinessOutcome, *, language: str) -> str:
    zh = str(language).lower().startswith("zh")
    if outcome.status == "blocked":
        if zh:
            reason = {
                "PAGINATION_UNVERIFIED": "分页完整性无法验证；请核实分页元数据后重试。",
                "MODIFIED_TIME_UNVERIFIED": "需求的最近更新时间无法验证；请核实时间字段后重试。",
                "USER_PROFILE_REQUIRED": "请先在共享上下文中明确 TAPD 身份；本轮不猜指派人。",
                "PROFILE_WORKSPACE_MISMATCH": "已确认身份与当前项目不匹配；请重新确认本次项目。",
                "SEMANTIC_UNCONFIRMED": "“未开始测试”尚未确认；请按 TAPD 页面的业务状态核对一次。",
                "WORKFLOW_SCHEMA_UNSUPPORTED": "工作流返回格式尚未核验；请核实格式后重试。",
                "STATUS_WORKFLOW_DRIFT": "工作流状态已变化；请重新确认业务口径。",
                "SEMANTIC_RECORD_INVALID": "已保存的业务口径无法验证；请重新对照 TAPD 页面确认。",
                "SESSION_CONTEXT_INVALID": "本次身份无效；请重新确认本次身份、角色和项目。",
                "SESSION_CONTEXT_EXPIRED": "本次身份已过期；请重新确认身份、角色和项目。",
                "SESSION_CONTEXT_WORKSPACE_MISMATCH": "本次身份不属于当前项目；请重新确认项目。",
                "SESSION_CONTEXT_REVOKED": "当前项目权限已撤销；请重新确认可访问项目。",
                "SESSION_CONTEXT_SCOPE_UNAVAILABLE": "项目范围暂时无法核实；恢复项目范围读取后重试。",
                "SESSION_CONTEXT_SCOPE_INVALID": "项目范围返回格式无法核实；核实格式后重试。",
            }.get(outcome.error_code, "暂时无法完成可信查询。")
            return f"[TAPD 可信查询：失败关闭]\n{reason}未返回原始列表，也不会猜测结果。"
        reason = {
            "PAGINATION_UNVERIFIED": "Pagination completeness could not be verified; verify page metadata and retry. ",
            "MODIFIED_TIME_UNVERIFIED": "Recent-update timestamps could not be verified; verify the time field and retry. ",
            "USER_PROFILE_REQUIRED": "Confirm the TAPD identity in shared context first. ",
            "PROFILE_WORKSPACE_MISMATCH": "The confirmed identity does not match the current project; confirm the project again. ",
            "SEMANTIC_UNCONFIRMED": "Review and confirm the exact visible statuses for not-started testing. ",
            "STATUS_WORKFLOW_DRIFT": "Workflow statuses changed; review and confirm the business meaning again. ",
            "WORKFLOW_SCHEMA_UNSUPPORTED": "Verify the workflow response format before retrying. ",
            "SEMANTIC_RECORD_INVALID": "Review and confirm the saved business meaning again. ",
            "SESSION_CONTEXT_INVALID": "Confirm this session's identity, role, and project again. ",
            "SESSION_CONTEXT_EXPIRED": "The session identity expired; confirm it again. ",
            "SESSION_CONTEXT_WORKSPACE_MISMATCH": "The session identity does not belong to the current project; confirm the project again. ",
            "SESSION_CONTEXT_REVOKED": "Project access was revoked; confirm an accessible project again. ",
            "SESSION_CONTEXT_SCOPE_UNAVAILABLE": "Restore project-scope access and retry. ",
            "SESSION_CONTEXT_SCOPE_INVALID": "Verify the project-scope response format and retry. ",
        }.get(outcome.error_code, "The trusted query could not complete. ")
        return f"[Trusted TAPD query: failed closed]\n{reason}No raw list or guessed result is returned."
    if outcome.status == "empty":
        return (
            "[TAPD 可信查询结果]\n当前没有符合条件的需求。"
            if zh
            else "[Trusted TAPD query result]\nThere are currently no matching requirements."
        )
    lines = [
        "[TAPD 可信查询结果（宿主已核验）]" if zh else "[Trusted TAPD query result (host verified)]"
    ]
    for index, story in enumerate(outcome.stories, start=1):
        details = " / ".join(value for value in (story.status, story.updated_at) if value)
        lines.append(
            f"- {index}. {story.title}"
            + (f"（{details}）" if zh and details else f" ({details})" if details else "")
        )
    if outcome.truncated:
        lines.append(
            f"共找到 {outcome.matched_count} 条；这里只显示前 {len(outcome.stories)} 条。"
            if zh
            else f"Found {outcome.matched_count}; showing the first {len(outcome.stories)}."
        )
    lines.append(
        "如要进入测试，用一次 ask_user 让用户按标题选择，问题 id=tapd_story_selection；普通答复只显示标题，不显示内部编号。"
        if zh
        else "To start testing, use ask_user once with id=tapd_story_selection and title-only options; never expose internal ids."
    )
    return "\n".join(lines)


def story_from_answer(answer: str, stories: tuple[TapdStory, ...]) -> TapdStory | None:
    value = _clean(answer).casefold()
    exact = [story for story in stories if story.title.casefold() == value]
    if len(exact) == 1:
        return exact[0]
    match = re.fullmatch(r"(?:第\s*)?(\d+)(?:\s*条)?", value)
    if match:
        index = int(match.group(1)) - 1
        if 0 <= index < len(stories):
            return stories[index]
    return None


def journey_ingest_arguments(project: TapdProject, story: TapdStory) -> dict[str, str]:
    """Internal-only handoff to the existing Journey ingest tool."""
    return {
        "workspace_id": project.id,
        "story_id": story.id,
        "source_kind": "tapd",
        "title": story.title,
    }


def _story_reference_from_link(value: Any) -> tuple[str, str]:
    try:
        path = urlparse(str(value or "")).path.rstrip("/")
    except ValueError:
        return "", ""
    parts = [part for part in path.split("/") if part]
    if len(parts) < 5 or parts[-3:-1] != ["stories", "view"]:
        return "", ""
    story_id = parts[-1]
    workspace_id = parts[0]
    if not re.fullmatch(r"\d+", story_id):
        return "", ""
    return workspace_id, story_id


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())[:240]


def _empty_error(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, Mapping) and not value)


def _error_code(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    code = value.get("code")
    message = value.get("message")
    if (
        not isinstance(code, str)
        or not code.strip()
        or not isinstance(message, str)
        or not message.strip()
    ):
        return ""
    return code.strip()


def _valid_read_markers(payload: Mapping[str, Any]) -> bool:
    return payload.get("effect") == "read" and payload.get("tapd_write") is False


def _string_list(value: Any, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _valid_blocked_business_payload(payload: Mapping[str, Any]) -> bool:
    count = payload.get("计数")
    return (
        payload.get("可答") is False
        and payload.get("条目") == []
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count == 0
        and payload.get("是否截断") is False
        and _string_list(payload.get("当前还能回答"), allow_empty=True)
        and payload.get("口径") == []
        and isinstance(payload.get("原因"), str)
        and bool(payload["原因"].strip())
        and isinstance(payload.get("下一步"), str)
        and bool(payload["下一步"].strip())
    )


def _modified_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


__all__ = [
    "SUPPORTED_BUSINESS_QUESTION",
    "TapdBusinessOutcome",
    "TapdSemanticGate",
    "TapdStory",
    "baseline_is_ready",
    "is_supported_business_question",
    "journey_ingest_arguments",
    "parse_business_query",
    "render_baseline_block",
    "render_business_outcome",
    "render_semantic_block",
    "semantic_gate",
    "semantic_is_ready",
    "story_from_answer",
]
