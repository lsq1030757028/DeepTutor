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


def is_supported_business_question(message: str) -> bool:
    folded = str(message or "").casefold()
    return SUPPORTED_BUSINESS_QUESTION in str(message or "") or all(
        marker in folded for marker in ("分配给我", "未开始测试", "需求")
    )


def baseline_is_ready(payload: Mapping[str, Any] | None) -> bool:
    return isinstance(payload, Mapping) and payload.get("基线") == "可用"


def semantic_is_ready(payload: Mapping[str, Any] | None) -> bool:
    return (
        isinstance(payload, Mapping)
        and str(payload.get("status") or "").casefold() == "ok"
        and payload.get("语义层可用") is True
        and SUPPORTED_BUSINESS_QUESTION in (payload.get("现在能答") or [])
    )


def parse_business_query(
    payload: Mapping[str, Any] | None,
    *,
    expected_workspace_id: str = "",
) -> TapdBusinessOutcome:
    if not isinstance(payload, Mapping):
        return TapdBusinessOutcome("blocked", error_code="MCP_RESULT_INVALID")
    error = payload.get("error")
    error = error if isinstance(error, Mapping) else {}
    if str(payload.get("status") or "").casefold() != "ok" or payload.get("可答") is not True:
        return TapdBusinessOutcome(
            "blocked",
            error_code=str(error.get("code") or "BUSINESS_QUERY_BLOCKED"),
        )
    rows = payload.get("条目")
    if not isinstance(rows, list):
        return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
    stories: list[TapdStory] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
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
    if not isinstance(count, Mapping) or not isinstance(truncated, bool):
        return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
    matched = count.get("符合")
    returned = count.get("返回")
    try:
        matched_count = int(matched)
        returned_count = int(returned)
    except (TypeError, ValueError):
        return TapdBusinessOutcome("blocked", error_code="BUSINESS_RESULT_INVALID")
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


def render_semantic_block(*, language: str) -> str:
    return (
        "[TAPD 可信查询：需要确认]\n“未开始测试”的含义尚未确认。请按 TAPD 页面显示的业务状态核对一次并确认；本轮不猜状态。"
        if str(language).lower().startswith("zh")
        else "[Trusted TAPD query: confirmation required]\nReview and confirm which exact visible business statuses mean not started; this turn will not guess."
    )


def render_business_outcome(outcome: TapdBusinessOutcome, *, language: str) -> str:
    zh = str(language).lower().startswith("zh")
    if outcome.status == "blocked":
        return (
            "[TAPD 可信查询：失败关闭]\n暂时无法完成可信查询；未返回原始列表，也不会猜测结果。"
            if zh
            else "[Trusted TAPD query: failed closed]\nThe trusted query could not complete. No raw list or guessed result is returned."
        )
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
    "TapdStory",
    "baseline_is_ready",
    "is_supported_business_question",
    "journey_ingest_arguments",
    "parse_business_query",
    "render_baseline_block",
    "render_business_outcome",
    "render_semantic_block",
    "semantic_is_ready",
    "story_from_answer",
]
