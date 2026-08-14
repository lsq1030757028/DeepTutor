from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from deeptutor.agents.test.pipeline import TestJourneyPipeline
from deeptutor.core.context import UnifiedContext
from deeptutor.core.tool_protocol import ToolDefinition, ToolResult
from deeptutor.services.tapd_business import (
    TapdStory,
    journey_ingest_arguments,
    parse_business_query,
)
from deeptutor.services.tapd_context import TapdProject

QUESTION = "最近分配给我且未开始测试的需求"


class _McpTool:
    def __init__(self, logical_name: str, provider: str = "personal") -> None:
        self.original_name = logical_name
        self.provider_id = provider
        self.name = f"mcp_{provider}_{logical_name}"

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(name=self.name, description=self.original_name)


class _Registry:
    def __init__(self, payloads: dict[str, object]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name: str, **kwargs):  # noqa: ANN003
        logical = next(key for key in self.payloads if name.endswith(f"_{key}"))
        self.calls.append((logical, kwargs))
        payload = self.payloads[logical]
        if isinstance(payload, Exception):
            raise payload
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))


class _SessionStore:
    def __init__(self) -> None:
        self.preferences = {}

    async def get_session(self, session_id: str):  # noqa: ANN201
        return {"id": session_id, "preferences": self.preferences}

    async def update_session_preferences(self, session_id: str, preferences: dict):  # noqa: ANN201
        self.preferences.update(preferences)
        return True


def _context_status() -> dict:
    return {
        "status": "ok",
        "profile_state": "ready",
        "profile": {
            "default_project_name": "DeepTutor",
            "tapd_identity": "张三",
            "business_role": "QA 负责人",
        },
        "accessible_project_names": ["DeepTutor"],
        "error": None,
    }


def _context_resolve() -> dict:
    return {
        "status": "ok",
        "resolution": {
            "workspace_id": "W1",
            "project_name": "DeepTutor",
            "source": "saved_default",
        },
        "error": None,
    }


def _baseline_ready() -> dict:
    return {"基线": "可用", "建立于": "2026-08-14T05:00:00Z", "各工作项": {"需求": "ok"}}


def _semantic_ready() -> dict:
    return {
        "status": "ok",
        "语义层可用": True,
        "现在能答": [QUESTION],
        "已确认谓词": [{"谓词": "未开始测试", "取值": ["规划中"]}],
        "error": None,
    }


def _query(*rows: tuple[str, str]) -> dict:
    items = [
        {
            "标题": title,
            "链接": f"https://www.tapd.cn/W1/prong/stories/view/{story_id}",
            "状态": "规划中",
            "指派人": "张三",
            "最近更新": "2026-08-14 12:00:00",
        }
        for story_id, title in rows
    ]
    return {
        "status": "ok",
        "可答": True,
        "条目": items,
        "计数": {"符合": len(items), "返回": len(items)},
        "是否截断": False,
        "error": None,
    }


def _pipeline(extra: dict[str, object], *, providers: tuple[str, ...] = ("personal",)):
    payloads = {
        "tapd_context_status": _context_status(),
        "tapd_context_resolve": _context_resolve(),
        **extra,
    }
    pipeline = TestJourneyPipeline.__new__(TestJourneyPipeline)
    pipeline.language = "zh"
    pipeline.registry = _Registry(payloads)
    pipeline._tool_view = None
    pipeline._deferred_pool = [
        _McpTool(logical, provider) for provider in providers for logical in payloads
    ]
    pipeline._tapd_selected_project = None
    pipeline._tapd_project_options = ()
    pipeline._tapd_identity = ""
    pipeline._tapd_role = ""
    pipeline._tapd_story_candidates = ()
    pipeline._tapd_selected_story = None
    return pipeline


@pytest.mark.asyncio
async def test_business_query_uses_fixed_trust_chain_and_never_calls_raw_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: _SessionStore())
    pipeline = _pipeline(
        {
            "tapd_baseline_status": _baseline_ready(),
            "tapd_semantic_status": _semantic_ready(),
            "tapd_business_query": _query(("1001", "登录验收"), ("1002", "权限验收")),
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(session_id="q1", user_message=QUESTION, language="zh")
    )

    assert [call[0] for call in pipeline.registry.calls] == [
        "tapd_context_status",
        "tapd_context_resolve",
        "tapd_baseline_status",
        "tapd_semantic_status",
        "tapd_business_query",
    ]
    assert "登录验收" in briefing and "权限验收" in briefing
    assert "1001" not in briefing and "W1" not in briefing
    assert "tapd_list" not in briefing
    assert "tapd_story_selection" in briefing


@pytest.mark.asyncio
async def test_baseline_block_stops_before_semantic_or_business_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: _SessionStore())
    pipeline = _pipeline(
        {
            "tapd_baseline_status": {"基线": "不可用", "原因": "missing"},
            "tapd_semantic_status": _semantic_ready(),
            "tapd_business_query": _query(("1001", "不应出现")),
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(session_id="q2", user_message=QUESTION, language="zh")
    )

    assert [call[0] for call in pipeline.registry.calls][-1] == "tapd_baseline_status"
    assert "字段口径" in briefing
    assert "tapd_baseline" not in briefing
    assert "不应出现" not in briefing


@pytest.mark.asyncio
async def test_unconfirmed_semantic_prompts_one_business_review_without_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: _SessionStore())
    pipeline = _pipeline(
        {
            "tapd_baseline_status": _baseline_ready(),
            "tapd_semantic_status": {
                "status": "needs_review",
                "语义层可用": False,
                "下一步": "运行 tapd_semantic_review",
                "error": {"code": "SEMANTIC_UNCONFIRMED", "message": "missing"},
            },
            "tapd_business_query": _query(("1001", "不应出现")),
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(session_id="q3", user_message=QUESTION, language="zh")
    )

    assert [call[0] for call in pipeline.registry.calls][-1] == "tapd_semantic_status"
    assert briefing.count("核对") == 1
    assert "页面显示的业务状态" in briefing
    assert "tapd_semantic" not in briefing
    assert "规划中" not in briefing


@pytest.mark.asyncio
async def test_business_query_empty_and_error_are_both_user_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: _SessionStore())
    empty = _pipeline(
        {
            "tapd_baseline_status": _baseline_ready(),
            "tapd_semantic_status": _semantic_ready(),
            "tapd_business_query": _query(),
        }
    )
    empty_briefing = await empty._tapd_context_briefing(
        UnifiedContext(session_id="empty", user_message=QUESTION, language="zh")
    )
    assert "当前没有符合条件的需求" in empty_briefing

    failed = _pipeline(
        {
            "tapd_baseline_status": _baseline_ready(),
            "tapd_semantic_status": _semantic_ready(),
            "tapd_business_query": RuntimeError("secret provider detail"),
        }
    )
    failed_briefing = await failed._tapd_context_briefing(
        UnifiedContext(session_id="failed", user_message=QUESTION, language="zh")
    )
    assert "暂时无法完成可信查询" in failed_briefing
    assert "secret provider detail" not in failed_briefing


@pytest.mark.asyncio
async def test_incomplete_pagination_is_blocked_without_partial_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: _SessionStore())
    pipeline = _pipeline(
        {
            "tapd_baseline_status": _baseline_ready(),
            "tapd_semantic_status": _semantic_ready(),
            "tapd_business_query": {
                "status": "blocked",
                "可答": False,
                "条目": [{"标题": "不完整的第一条", "链接": "https://example/1001"}],
                "error": {
                    "code": "PAGINATION_INCOMPLETE",
                    "message": "cannot prove all pages",
                },
            },
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(session_id="pagination", user_message=QUESTION, language="zh")
    )

    assert "失败关闭" in briefing
    assert "不完整的第一条" not in briefing
    assert pipeline._tapd_story_candidates == ()


@pytest.mark.asyncio
async def test_duplicate_business_provider_fails_before_any_business_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: _SessionStore())
    pipeline = _pipeline(
        {
            "tapd_baseline_status": _baseline_ready(),
            "tapd_semantic_status": _semantic_ready(),
            "tapd_business_query": _query(("1001", "不应出现")),
        }
    )
    pipeline._deferred_pool.append(_McpTool("tapd_business_query", "other-provider"))

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(session_id="dup-business", user_message=QUESTION, language="zh")
    )

    assert "失败关闭" in briefing
    assert [call[0] for call in pipeline.registry.calls] == [
        "tapd_context_status",
        "tapd_context_resolve",
    ]


def test_business_result_parser_rejects_missing_story_reference() -> None:
    outcome = parse_business_query(
        {
            "status": "ok",
            "可答": True,
            "条目": [{"标题": "没有链接", "状态": "规划中"}],
            "计数": {"符合": 1, "返回": 1},
            "是否截断": False,
        }
    )
    assert outcome.status == "blocked"
    assert outcome.stories == ()


def test_business_result_parser_rejects_cross_workspace_story_reference() -> None:
    outcome = parse_business_query(
        _query(("1001", "来自其他项目")),
        expected_workspace_id="W2",
    )
    assert outcome.status == "blocked"
    assert outcome.stories == ()


def test_business_result_parser_rejects_unknown_pagination_metadata() -> None:
    payload = _query(("1001", "分页未知"))
    payload.pop("计数")
    payload.pop("是否截断")

    outcome = parse_business_query(payload, expected_workspace_id="W1")

    assert outcome.status == "blocked"
    assert outcome.stories == ()


def test_business_result_parser_rejects_items_not_sorted_by_modified_desc() -> None:
    payload = _query(("1001", "较旧"), ("1002", "较新"))
    payload["条目"][0]["最近更新"] = "2026-08-13 12:00:00"
    payload["条目"][1]["最近更新"] = "2026-08-14 12:00:00"

    outcome = parse_business_query(payload, expected_workspace_id="W1")

    assert outcome.status == "blocked"
    assert outcome.stories == ()


def test_selected_story_handoff_contains_internal_ids_only_in_tool_arguments() -> None:
    args = journey_ingest_arguments(
        TapdProject("W1", "DeepTutor"),
        TapdStory("1001", "登录验收", "规划中", "张三", "2026-08-14 12:00:00"),
    )
    assert args == {
        "workspace_id": "W1",
        "story_id": "1001",
        "source_kind": "tapd",
        "title": "登录验收",
    }


@pytest.mark.asyncio
async def test_ask_user_resume_hands_selected_story_to_existing_journey_ingest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStore()
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)

    async def fake_parent_resume(self, *, context, stream, dispatch):  # noqa: ANN001, ANN202
        assert await context.metadata["wait_for_user_reply"]()
        dispatch.tool_messages[0]["content"] = "User selected 登录验收"
        return True

    monkeypatch.setattr(
        "deeptutor.agents.chat.agentic_pipeline.AgenticChatPipeline._await_user_reply_and_resolve",
        fake_parent_resume,
    )
    pipeline = _pipeline({})
    pipeline._tapd_selected_project = TapdProject("W1", "DeepTutor")
    pipeline._tapd_story_candidates = (
        TapdStory("1001", "登录验收", "规划中", "张三", "2026-08-14 12:00:00"),
    )

    async def waiter():  # noqa: ANN202
        return {"answers": [{"questionId": "tapd_story_selection", "text": "登录验收"}]}

    context = UnifiedContext(session_id="resume", metadata={"wait_for_user_reply": waiter})
    dispatch = SimpleNamespace(
        pause_tool_call_id="ask-story",
        tool_messages=[{"tool_call_id": "ask-story", "content": "pending"}],
    )
    resumed = await pipeline._await_user_reply_and_resolve(
        context=context,
        stream=SimpleNamespace(),
        dispatch=dispatch,
    )

    assert resumed is True
    content = dispatch.tool_messages[0]["content"]
    assert "journey_ingest" in content
    assert "workspace_id=W1" in content
    assert "story_id=1001" in content
