from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deeptutor.agents.test.pipeline import TestJourneyPipeline, journey_system_block
from deeptutor.core.context import UnifiedContext
from deeptutor.core.tool_protocol import ToolDefinition, ToolResult
from deeptutor.services.tapd_context import TapdProject


class _McpTool:
    def __init__(self, logical_name: str, provider: str = "personal-tapd") -> None:
        self.original_name = logical_name
        self.provider_id = provider
        self.name = f"mcp_{provider}_{logical_name}"

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(name=self.name, description=self.original_name)


class _Registry:
    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name: str, **kwargs):  # noqa: ANN003
        logical_name = name.rsplit("_tapd_", 1)[-1]
        logical_name = f"tapd_{logical_name}"
        self.calls.append((logical_name, kwargs))
        payload = self.payloads[logical_name]
        if isinstance(payload, list):
            payload = payload.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))


class _SessionStore:
    def __init__(self) -> None:
        self.preferences: dict = {}

    async def get_session(self, session_id: str):  # noqa: ANN201
        return {"id": session_id, "preferences": self.preferences}

    async def update_session_preferences(self, session_id: str, preferences: dict):  # noqa: ANN201
        self.preferences.update(preferences)
        return True


def _ready_status(
    *,
    default: str = "DeepTutor",
    projects: tuple[str, ...] = ("DeepTutor",),
) -> dict:
    return {
        "status": "ok",
        "profile_state": "ready",
        "profile": {
            "default_project_name": default,
            "tapd_identity": "张三",
            "business_role": "QA 负责人",
        },
        "accessible_project_names": list(projects),
        "error": None,
    }


def _resolved(
    project_id: str = "1",
    name: str = "DeepTutor",
    source: str = "saved_default",
    *,
    session_claim: str = "",
):
    payload = {
        "status": "ok",
        "resolution": {
            "workspace_id": project_id,
            "project_name": name,
            "source": source,
        },
        "error": None,
    }
    if session_claim:
        payload.update(
            {
                "session_context": session_claim,
                "session_context_expires_at": "2026-08-14T08:15:00Z",
                "session_context_ttl_seconds": 900,
                "session_context_source": "user_confirmed",
            }
        )
    return payload


def _pipeline(payloads: dict[str, dict], *, providers: tuple[str, ...] = ("personal-tapd",)):
    pipeline = TestJourneyPipeline.__new__(TestJourneyPipeline)
    pipeline.language = "zh"
    pipeline.registry = _Registry(payloads)
    pipeline._tool_view = None
    logical_names = tuple(payloads)
    pipeline._deferred_pool = [
        _McpTool(logical_name, provider) for provider in providers for logical_name in logical_names
    ]
    pipeline._tapd_selected_project = None
    pipeline._tapd_story_candidates = ()
    pipeline._tapd_session_context = ""
    return pipeline


@pytest.mark.asyncio
async def test_u1_ready_shared_profile_continues_as_qa_without_host_profile_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _SessionStore()
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)
    monkeypatch.setattr("deeptutor.multi_user.paths.SYSTEM_ROOT", tmp_path / "system")
    pipeline = _pipeline(
        {
            "tapd_context_status": _ready_status(),
            "tapd_context_resolve": _resolved(),
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(
            session_id="s1",
            user_message="从 TAPD 开始验收登录需求",
            language="zh",
        )
    )

    # 身份名词在 briefing 与 system prompt 里各写一份，两处必须同名——不同名等于
    # 让模型自己挑一个信。这条已经漂过两次（ADR-1 改名、批次 7 改名），所以除了
    # 钉住字面，再对拍一次 prompt 那份。
    assert "测试助手（QA）" in briefing
    assert "测试助手" in journey_system_block("zh")
    assert "上下文已齐" in briefing
    assert "tapd_context_project:" not in briefing
    assert [call[0] for call in pipeline.registry.calls] == [
        "tapd_context_status",
        "tapd_context_resolve",
    ]
    assert not (tmp_path / "system" / "tapd-context").exists()


@pytest.mark.asyncio
async def test_u2_first_use_asks_once_with_business_names_and_keeps_answers_session_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStore()
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)
    pipeline = _pipeline(
        {
            "tapd_context_status": {
                "status": "needs_input",
                "profile_state": "missing",
                "profile": None,
                "accessible_project_names": ["DeepTutor", "Mengban Web"],
                "error": None,
            },
            "tapd_context_resolve": [
                {
                    "status": "needs_confirmation",
                    "project_options": ["DeepTutor", "Mengban Web"],
                    "error": {"code": "PROJECT_CONFIRMATION_REQUIRED", "message": "请选择项目"},
                },
                _resolved(
                    "2",
                    "Mengban Web",
                    "explicit",
                    session_claim="tapdsc1.opaque.signature",
                ),
            ],
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(session_id="s2", user_message="从 TAPD 开始验收登录需求", language="zh")
    )

    assert briefing.count("只调用一次 ask_user") == 1
    assert "DeepTutor / Mengban Web" in briefing
    assert "tapd_context_identity" in briefing
    assert "tapd_context_role" in briefing
    assert "workspace_id" not in briefing

    selected = await pipeline._persist_tapd_answers(
        UnifiedContext(session_id="s2"),
        {
            "answers": [
                {"questionId": "tapd_context_project", "text": "Mengban Web"},
                {"questionId": "tapd_context_remember_project", "text": "仅本次"},
                {"questionId": "tapd_context_identity", "text": "李四"},
                {"questionId": "tapd_context_role", "text": "一线测试人员"},
            ]
        },
    )

    assert selected == TapdProject("2", "Mengban Web")
    assert session.preferences["tapd_context"] == {
        "project": {"id": "2", "name": "Mengban Web"},
        "tapd_identity": "李四",
        "business_role": "一线测试人员",
        "session_context": "tapdsc1.opaque.signature",
        "session_context_expires_at": "2026-08-14T08:15:00Z",
    }
    assert pipeline.registry.calls[-1] == (
        "tapd_context_resolve",
        {
            "project_hint": "Mengban Web",
            "tapd_identity": "李四",
            "business_role": "一线测试人员",
        },
    )
    assert [call[0] for call in pipeline.registry.calls].count("tapd_context_save") == 0


@pytest.mark.asyncio
async def test_u3_explicit_project_is_a_session_override_and_does_not_change_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStore()
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)
    pipeline = _pipeline(
        {
            "tapd_context_status": _ready_status(projects=("DeepTutor", "Mengban Web")),
            "tapd_context_resolve": _resolved(
                "2",
                "Mengban Web",
                "explicit",
                session_claim="tapdsc1.explicit.signature",
            ),
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(
            session_id="s3",
            user_message="这次从 TAPD 测 Mengban Web 的登录需求",
            language="zh",
        )
    )

    assert "本次项目: Mengban Web" in briefing
    assert pipeline.registry.calls[1] == (
        "tapd_context_resolve",
        {
            "project_hint": "Mengban Web",
            "tapd_identity": "张三",
            "business_role": "QA 负责人",
        },
    )
    assert session.preferences["tapd_context"]["project"] == {"id": "2", "name": "Mengban Web"}
    assert all(call[0] != "tapd_context_save" for call in pipeline.registry.calls)


@pytest.mark.asyncio
async def test_local_text_or_attachment_skips_every_tapd_tool() -> None:
    pipeline = _pipeline({})
    context = UnifiedContext(
        session_id="local",
        user_message="需求正文：正确登录后显示欢迎语。验收标准：欢迎语可见。",
        language="zh",
    )

    briefing = await pipeline._tapd_context_briefing(context)

    assert "本地测试需求" in briefing
    assert pipeline.registry.calls == []
    assert context.metadata["tapd_context"]["status"] == "not_required"


@pytest.mark.asyncio
async def test_unknown_source_asks_only_tapd_or_local_and_skips_context_tools() -> None:
    pipeline = _pipeline(
        {
            "tapd_context_status": _ready_status(),
            "tapd_context_resolve": _resolved(),
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(session_id="unknown", user_message="帮我验收登录功能", language="zh")
    )

    assert "从 TAPD 取需求，还是使用当前提供的正文或附件" in briefing
    assert "tapd_context_project" not in briefing
    assert pipeline.registry.calls == []


@pytest.mark.asyncio
async def test_duplicate_context_provider_fails_closed_without_calling_either() -> None:
    pipeline = _pipeline(
        {
            "tapd_context_status": _ready_status(),
            "tapd_context_resolve": _resolved(),
        },
        providers=("team-a", "team-b"),
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(session_id="dup", user_message="从 TAPD 里拿需求开始测", language="zh")
    )

    assert "TAPD 上下文：不可用" in briefing
    assert "重复" in briefing
    assert pipeline.registry.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "value"),
    (
        ("ok_with_error", {"code": "CONTRADICTORY", "message": "must fail closed"}),
        ("project_names_not_list", "DeepTutor"),
        ("profile_identity_not_string", True),
    ),
)
async def test_malformed_context_status_stops_before_resolve(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    value: object,
) -> None:
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: _SessionStore())
    status = _ready_status()
    if mutation == "ok_with_error":
        status["error"] = value
    elif mutation == "project_names_not_list":
        status["accessible_project_names"] = value
    else:
        status["profile"]["tapd_identity"] = value
    pipeline = _pipeline(
        {
            "tapd_context_status": status,
            "tapd_context_resolve": _resolved(),
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(
            session_id=f"invalid-{mutation}", user_message="从 TAPD 取需求", language="zh"
        )
    )

    assert "不可用" in briefing
    assert [call[0] for call in pipeline.registry.calls] == ["tapd_context_status"]


@pytest.mark.asyncio
async def test_malformed_context_resolve_is_blocked_and_not_saved_to_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStore()
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)
    resolved = _resolved()
    resolved["error"] = {"code": "CONTRADICTORY", "message": "must fail closed"}
    pipeline = _pipeline(
        {
            "tapd_context_status": _ready_status(),
            "tapd_context_resolve": resolved,
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(session_id="invalid-resolve", user_message="从 TAPD 取需求", language="zh")
    )

    assert "不可用" in briefing
    assert session.preferences == {}


@pytest.mark.asyncio
async def test_malformed_session_claim_fields_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStore()
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)
    resolved = _resolved(
        "1",
        "DeepTutor",
        "explicit",
        session_claim="tapdsc1.opaque.signature",
    )
    resolved["session_context_ttl_seconds"] = True
    pipeline = _pipeline(
        {
            "tapd_context_status": _ready_status(),
            "tapd_context_resolve": resolved,
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(session_id="invalid-claim", user_message="从 TAPD 取需求", language="zh")
    )

    assert "不可用" in briefing
    assert session.preferences == {}


@pytest.mark.asyncio
async def test_context_resolve_project_must_be_in_status_accessible_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStore()
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)
    pipeline = _pipeline(
        {
            "tapd_context_status": _ready_status(projects=("DeepTutor",)),
            "tapd_context_resolve": _resolved(
                "2",
                "Mengban Web",
                "explicit",
                session_claim="tapdsc1.stale.signature",
            ),
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(session_id="cross-context", user_message="从 TAPD 取需求", language="zh")
    )

    assert "不可用" in briefing
    assert session.preferences == {}


@pytest.mark.asyncio
async def test_contradictory_context_save_ack_is_not_accepted_as_persisted_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStore()
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)
    pipeline = _pipeline(
        {
            "tapd_context_resolve": _resolved("2", "Mengban Web", "explicit"),
            "tapd_context_save": {
                "status": "ok",
                "effect": "workspace-write",
                "tapd_write": False,
                "error": {"code": "CONTRADICTORY", "message": "must fail closed"},
            },
        }
    )
    pipeline._tapd_project_options = ("DeepTutor", "Mengban Web")

    selected = await pipeline._persist_tapd_answers(
        UnifiedContext(session_id="bad-save"),
        {
            "answers": [
                {"questionId": "tapd_context_project", "text": "Mengban Web"},
                {"questionId": "tapd_context_remember_project", "text": "设为默认"},
                {"questionId": "tapd_context_identity", "text": "王五"},
                {"questionId": "tapd_context_role", "text": "QA 负责人"},
            ]
        },
    )

    assert selected is None
    assert session.preferences["tapd_context"]["project"] == {"id": "2", "name": "Mengban Web"}


@pytest.mark.asyncio
async def test_explicit_set_default_calls_only_the_shared_profile_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStore()
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)
    pipeline = _pipeline(
        {
            "tapd_context_resolve": _resolved("2", "Mengban Web", "explicit"),
            "tapd_context_save": {
                "status": "ok",
                "effect": "workspace-write",
                "tapd_write": False,
                "error": None,
            },
        }
    )
    pipeline._tapd_project_options = ("DeepTutor", "Mengban Web")

    selected = await pipeline._persist_tapd_answers(
        UnifiedContext(session_id="save-default"),
        {
            "answers": [
                {"questionId": "tapd_context_project", "text": "Mengban Web"},
                {"questionId": "tapd_context_remember_project", "text": "设为默认"},
                {"questionId": "tapd_context_identity", "text": "王五"},
                {"questionId": "tapd_context_role", "text": "QA 负责人"},
            ]
        },
    )

    assert selected == TapdProject("2", "Mengban Web")
    assert pipeline.registry.calls == [
        ("tapd_context_resolve", {"project_hint": "Mengban Web"}),
        (
            "tapd_context_save",
            {
                "default_project": "Mengban Web",
                "tapd_identity": "王五",
                "business_role": "QA 负责人",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_revoked_default_fails_closed_in_business_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: _SessionStore())
    pipeline = _pipeline(
        {
            "tapd_context_status": {
                "status": "blocked",
                "profile_state": "stale",
                "profile": {
                    "default_project_name": "DeepTutor",
                    "tapd_identity": "张三",
                    "business_role": "QA 负责人",
                },
                "accessible_project_names": ["Mengban Web"],
                "error": {
                    "code": "SAVED_DEFAULT_OUT_OF_SCOPE",
                    "message": "已保存的默认项目不在当前可访问范围",
                },
            },
            "tapd_context_resolve": {
                "status": "blocked",
                "project_options": ["Mengban Web"],
                "error": {
                    "code": "SAVED_DEFAULT_OUT_OF_SCOPE",
                    "message": "已保存的默认项目不在当前可访问范围",
                },
            },
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(session_id="revoked", user_message="从 TAPD 开始验收", language="zh")
    )

    assert "不可用" in briefing
    assert "不在当前可访问范围" in briefing
    assert "自动改用" not in briefing


@pytest.mark.asyncio
async def test_explicit_project_can_override_a_revoked_default_for_this_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStore()
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)
    pipeline = _pipeline(
        {
            "tapd_context_status": {
                "status": "blocked",
                "profile_state": "stale",
                "profile": {
                    "default_project_name": "DeepTutor",
                    "tapd_identity": "张三",
                    "business_role": "QA 负责人",
                },
                "accessible_project_names": ["Mengban Web"],
                "error": {
                    "code": "SAVED_DEFAULT_OUT_OF_SCOPE",
                    "message": "已保存的默认项目不在当前可访问范围",
                },
            },
            "tapd_context_resolve": _resolved(
                "2",
                "Mengban Web",
                "explicit",
                session_claim="tapdsc1.stale.signature",
            ),
        }
    )

    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(
            session_id="stale-explicit",
            user_message="这次从 TAPD 测 Mengban Web 的登录需求",
            language="zh",
        )
    )

    assert "上下文已齐" in briefing
    assert "本次项目: Mengban Web" in briefing
    assert session.preferences["tapd_context"]["project"] == {"id": "2", "name": "Mengban Web"}
    assert session.preferences["tapd_context"]["session_context"] == "tapdsc1.stale.signature"
    assert all(call[0] != "tapd_context_save" for call in pipeline.registry.calls)


def test_test_mode_prompt_keeps_its_identity_and_forbids_internal_exposure() -> None:
    """身份不是学习教练；内部实现不外泄。

    ADR-1 之后措辞由「测试执行 / QA 助手」改为「测试搭档」——改的是定位
    （表单收集器 → 边聊边做的搭档）。**守的两条不变量没动。**

    第一版改这条时把 `"普通答复"` 换成了 `"workspace_id"`，被独立复核判为空换：
    新锚与相邻那条断言命中同一句话，零新增覆盖，而它替下来的那条纪律
    （宿主给的 id 只交给接入工具、不在普通答复里复述）当时是真的被删了。
    该纪律已随宿主可信查询段一并恢复，锚也一并恢复——**锚要各自钉住一条不同的话**。

    批次 7（spec Delta 2026-08-19）再改一次名词：「测试搭档」→「测试助手」。
    改的还是定位而不是不变量——用户三次原话要的是"能随时解决测试问题的测试助手"，
    而上一版把"搭档"落成了"旅程的搭档"。**守的两条不变量仍然没动**，
    所以这里只换身份锚，下面四条一个字不改。
    """
    prompt = journey_system_block("zh")
    assert "测试助手" in prompt
    assert "不是学习教练" in prompt
    assert "不是表单收集器" in prompt
    assert "不得暴露 `journey_*`" in prompt
    assert "不在普通答复里复述" in prompt


@pytest.mark.asyncio
async def test_existing_ask_user_resume_resolves_business_name_into_session_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStore()
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)

    async def fake_parent_resume(self, *, context, stream, dispatch):  # noqa: ANN001, ANN202
        assert await context.metadata["wait_for_user_reply"]()
        dispatch.tool_messages[0]["content"] = "User answered"
        return True

    monkeypatch.setattr(
        "deeptutor.agents.chat.agentic_pipeline.AgenticChatPipeline._await_user_reply_and_resolve",
        fake_parent_resume,
    )
    pipeline = _pipeline(
        {
            "tapd_context_resolve": _resolved(
                "2",
                "Mengban Web",
                "explicit",
                session_claim="tapdsc1.resume.signature",
            )
        }
    )
    pipeline._tapd_project_options = ("DeepTutor", "Mengban Web")

    async def waiter():  # noqa: ANN202
        return {
            "answers": [
                {"questionId": "tapd_context_project", "text": "Mengban Web"},
                {"questionId": "tapd_context_remember_project", "text": "仅本次"},
                {"questionId": "tapd_context_identity", "text": "张三"},
                {"questionId": "tapd_context_role", "text": "QA 负责人"},
            ]
        }

    context = UnifiedContext(session_id="resume-context", metadata={"wait_for_user_reply": waiter})
    dispatch = SimpleNamespace(
        pause_tool_call_id="ask-context",
        tool_messages=[{"tool_call_id": "ask-context", "content": "pending"}],
    )

    resumed = await pipeline._await_user_reply_and_resolve(
        context=context,
        stream=SimpleNamespace(),
        dispatch=dispatch,
    )

    assert resumed is True
    assert context.metadata["wait_for_user_reply"] is waiter
    assert session.preferences["tapd_context"]["project"] == {"id": "2", "name": "Mengban Web"}
    assert "internal workspace_id=2" in dispatch.tool_messages[0]["content"]
    assert all(call[0] != "tapd_context_save" for call in pipeline.registry.calls)


# ── BB-716（2026-09-05）：冷会话里档案默认要让用户看见，不许静默沿用 ───────────
#
# UAT 判据 2 残留 2：用户冷启动说"帮我拉一下TAPD的需求"，没出任何确认卡，直接查了
# 共享档案里 0828 存下的默认项目（用户早已不做的那个）。档案默认是**上一次**的选择，
# 这一次用不用，得让他看一眼——确认卡不算追问（用户 2026-09-05 拍板）。


def _cold_saved_default_pipeline(projects=("DeepTutor", "Mengban Web")):
    return _pipeline(
        {
            "tapd_context_status": _ready_status(projects=projects),
            "tapd_context_resolve": [
                _resolved("1", "DeepTutor", "saved_default"),
                _resolved(
                    "1",
                    "DeepTutor",
                    "explicit",
                    session_claim="tapdsc1.confirmed.signature",
                ),
            ],
        }
    )


@pytest.mark.asyncio
async def test_a_cold_session_with_a_saved_default_asks_before_using_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStore()
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)
    pipeline = _cold_saved_default_pipeline()

    context = UnifiedContext(session_id="cold", user_message="帮我拉一下TAPD的需求", language="zh")
    briefing = await pipeline._tapd_context_briefing(context)

    # 不是"上下文已齐"，是一张卡：档案默认放第一个并标明来源，其余项目跟在后面。
    assert "上下文已齐" not in briefing
    assert briefing.count("只调用一次 ask_user") == 1
    assert "tapd_context_project:" in briefing
    assert "选项=DeepTutor / Mengban Web" in briefing
    assert "档案里已保存的默认" in briefing
    assert "workspace_id" not in briefing
    # 身份与角色档案里有，卡上不该再问。
    assert "tapd_context_identity" not in briefing
    assert "tapd_context_role" not in briefing
    assert context.metadata["tapd_context"]["status"] == "needs_input"
    assert context.metadata["tapd_context"]["project_source"] == "saved_default"
    # 还没确认，宿主不许把档案默认写成会话项目。
    assert "tapd_context" not in session.preferences

    # 用户点了推荐项（沿用档案默认）：宿主拿档案里的身份/角色去换会话上下文并落会话。
    selected = await pipeline._persist_tapd_answers(
        context,
        {
            "answers": [
                {"questionId": "tapd_context_project", "text": "DeepTutor"},
                {"questionId": "tapd_context_remember_project", "text": "仅本次"},
            ]
        },
    )
    assert selected == TapdProject("1", "DeepTutor")
    assert pipeline.registry.calls[-1] == (
        "tapd_context_resolve",
        {"project_hint": "DeepTutor", "tapd_identity": "张三", "business_role": "QA 负责人"},
    )
    assert session.preferences["tapd_context"]["project"] == {"id": "1", "name": "DeepTutor"}
    assert session.preferences["tapd_context"]["session_context"] == "tapdsc1.confirmed.signature"
    assert all(call[0] != "tapd_context_save" for call in pipeline.registry.calls)


@pytest.mark.asyncio
async def test_a_confirmed_project_stays_silent_for_the_rest_of_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确认卡只弹一次：同一会话后续轮次带着会话项目走 explicit，不再问。"""
    session = _SessionStore()
    session.preferences["tapd_context"] = {
        "project": {"id": "1", "name": "DeepTutor"},
        "tapd_identity": "张三",
        "business_role": "QA 负责人",
        "session_context": "tapdsc1.confirmed.signature",
        "session_context_expires_at": "2026-08-14T08:15:00Z",
    }
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)
    pipeline = _pipeline(
        {
            "tapd_context_status": _ready_status(projects=("DeepTutor", "Mengban Web")),
            "tapd_context_resolve": _resolved("1", "DeepTutor", "explicit"),
        }
    )
    context = UnifiedContext(session_id="warm", user_message="看看 TAPD 里已提测的", language="zh")
    briefing = await pipeline._tapd_context_briefing(context)
    assert "上下文已齐" in briefing
    assert "tapd_context_project:" not in briefing
    assert pipeline.registry.calls[-1][1]["project_hint"] == "DeepTutor"


@pytest.mark.asyncio
async def test_a_single_accessible_project_needs_no_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只有一个项目可选时没有第二个选项，卡片就是噪音——照旧静默继续。"""
    session = _SessionStore()
    monkeypatch.setattr("deeptutor.services.session.get_session_store", lambda: session)
    pipeline = _cold_saved_default_pipeline(projects=("DeepTutor",))
    briefing = await pipeline._tapd_context_briefing(
        UnifiedContext(session_id="cold-1", user_message="帮我拉一下TAPD的需求", language="zh")
    )
    assert "上下文已齐" in briefing
    assert "tapd_context_project:" not in briefing


def test_require_visible_project_confirmation_is_a_pure_gate() -> None:
    from dataclasses import replace

    from deeptutor.services.tapd_context import (
        TapdContextResolution,
        TapdSessionContext,
        require_visible_project_confirmation,
    )

    ready = TapdContextResolution(
        status="ready",
        selected_project=TapdProject("1", "DeepTutor"),
        project_source="saved_default",
        tapd_identity="张三",
        role="QA 负责人",
        project_options=("DeepTutor", "Mengban Web"),
    )
    gated = require_visible_project_confirmation(ready)
    assert gated.status == "needs_input"
    assert gated.missing == ("project",)
    assert gated.selected_project == TapdProject("1", "DeepTutor")
    assert gated.project_source == "saved_default"
    # 显式点名 / 唯一可访问 / 会话里已有项目 / 只有一个选项：四种情况都不降级。
    explicit = replace(ready, project_source="explicit")
    assert require_visible_project_confirmation(explicit) is explicit
    unique = replace(ready, project_source="unique_accessible")
    assert require_visible_project_confirmation(unique) is unique
    warm = TapdSessionContext(project=TapdProject("1", "DeepTutor"))
    assert require_visible_project_confirmation(ready, session=warm) is ready
    single = replace(ready, project_options=("DeepTutor",))
    assert require_visible_project_confirmation(single) is single
