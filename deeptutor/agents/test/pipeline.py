"""[fork] 「测试」capability 的 pipeline —— 旅程纪律与宿主级 TAPD 上下文。

## 为什么需要这个文件（BB-508）

前一版 `TestCapability.run()` 是这么挂纪律段的::

    context.metadata["capability_system_block"] = block
    await AgenticChatPipeline(...).run(context, stream)

那个键**全仓零读取点**（无截断枚举：`grep -rn capability_system_block` 只三处，
两处是 `agentic_pipeline.py` 里同名的**方法** `_capability_system_blocks`，
一处就是那次写入本身）。所以选中「测试」模式后的真实服务端行为 = 纯 chat + 一个徽章，
旅程纪律一个字进不了模型，**且静默无提示**——与 BB-502「挂载了兑现不了的能力」同族。

## 为什么是 override 而不是注册进 LOOP_CAPABILITIES

`_capability_system_blocks` 只遍历 `LOOP_CAPABILITIES`（`capabilities/registry.py:13`），
把 test 塞进那个元组是最直觉的修法，但那会**新增一个登记触点**，吃掉 M2 触点额度
最后一行硬余量；0025 §2 已为同类问题拒过一次。所以走子类：新增文件免登记，
`AgenticChatPipeline` 的一切（`ask_user` 的 waiter、MCP 工具组装、重放语义）照常继承。

这里继续只复用父 pipeline 的钩子，不复制 agent loop：系统块注入旅程纪律，pre-loop
用已有 MCP 只读发现项目，ask_user resume 只保存用户真实回答的非敏感上下文。
自建流水线才是复刻 BB-502。

## 判据在哪

`tests/core/test_fork_capabilities_runtime.py`——断的是**渲染出来的 system prompt
里含纪律段的锚串**，不是断 `run()` 的源码文本里出现过某个类名。旧那条断源码的测试
（`test_capability_reuses_main_chat_pipeline`）九条全绿也没挡住这个空转：
源码文本与运行时行为之间没有蕴含关系，它断的是「作者写了这个词」不是「模型收到了这段话」。
"""

from __future__ import annotations

import re
from typing import Any

from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
from deeptutor.capabilities.protocol import PromptBlock
from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream_bus import StreamBus
from deeptutor.services.prompt.manager import PromptManager
from deeptutor.services.tapd_context import (
    TapdProfile,
    TapdProject,
    answer_map,
    load_profile,
    parse_projects_payload,
    project_from_answer,
    render_context_briefing,
    resolve_context,
    save_profile,
    wants_default,
)

#: prompt 模块名。必须同时出现在 `PromptManager.MODULES` 里，
#: 否则 `load_prompts` 查不到本模块而**静默回落**——那是登记表里唯一
#: 「漏改不报错」的一处，所以有测试守。
PROMPT_MODULE = "test"
PROMPT_AGENT = "test_journey"

#: system prompt 里这个块的名字，渲染成 `## test_journey` 一节
#: （`prompt_blocks.py:52` 的 `f"## {block.name}"`）。
JOURNEY_BLOCK_NAME = "test_journey"

#: yaml 里哪几段拼进纪律块，**顺序即拼接顺序**。
#: 单列成常量是因为「写了一段提示词但忘了挂进来」与 BB-508 是同一个形状的错
#: （内容有、通道没接上），所以判据直接遍历这个列表逐段验，加一段自动带一条断言。
JOURNEY_PROMPT_KEYS = ("journey_discipline", "human_gates", "workbench_pointer")


def journey_system_block(language: str = "zh") -> str:
    """旅程纪律系统块。加载失败**如实返回空串**，不塞一段兜底文案。

    塞兜底文案会让「prompt 模块没注册」这个配置错误看起来像正常工作——
    正是 MODULES 那条漏改不报错的放大器。空串至少会让人发现模型没按纪律走。
    """
    try:
        prompts = PromptManager().load_prompts(PROMPT_MODULE, PROMPT_AGENT, language=language)
    except Exception:  # noqa: BLE001 - 提示词读不到不该让整轮崩
        return ""
    parts = [str(prompts.get(key) or "").strip() for key in JOURNEY_PROMPT_KEYS]
    return "\n\n".join(p for p in parts if p)


class TestJourneyPipeline(AgenticChatPipeline):
    """主聊 pipeline + 旅程纪律 + TAPD 的宿主级轻上下文。"""

    _tapd_projects: tuple[TapdProject, ...] = ()
    _tapd_selected_project: TapdProject | None = None

    def _capability_system_blocks(self, context: UnifiedContext) -> list[PromptBlock]:
        # 先拿父类的（用户另外开着的 loop capability 照常生效），再追加自己的。
        # 追加而不是替换：这一模式不排他，它只是多一段纪律。
        blocks = list(super()._capability_system_blocks(context))
        content = journey_system_block(self.language)
        if content:
            blocks.append(PromptBlock(JOURNEY_BLOCK_NAME, content))
        return blocks

    async def _capability_pre_loop_briefings(
        self,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> str:
        """Resolve TAPD project scope before the first Test-mode model call.

        The credential stays inside the user's MCP connection.  This adapter
        invokes only the read-only ``tapd_projects`` logical tool and combines
        its live scope with non-secret owner/session preferences.
        """
        inherited = await super()._capability_pre_loop_briefings(context, stream)
        tapd = await self._tapd_context_briefing(context)
        return "\n\n".join(part for part in (inherited.strip(), tapd.strip()) if part)

    async def _tapd_context_briefing(self, context: UnifiedContext) -> str:
        from deeptutor.multi_user.paths import current_owner_id

        oracle_mode = self._test_oracle_mode(context)
        if oracle_mode == "local":
            context.metadata["tapd_context"] = {"status": "not_required", "missing": []}
            return (
                "[本地测试需求（宿主已识别）]\n用户已提供本地需求正文或附件。直接使用本地 oracle，"
                "不要询问 TAPD 项目、身份、角色、workspace_id 或令牌。"
                if context.language.lower().startswith("zh")
                else "[Local test requirement (host detected)]\nThe user supplied local requirement "
                "text or an attachment. Use the local oracle directly; do not ask for a TAPD project, "
                "identity, role, workspace id, or token."
            )

        owner_id = current_owner_id()
        profile = load_profile(owner_id)
        session_project = await self._load_session_project(context.session_id)
        tool_name = self._logical_mcp_tool_name("tapd_projects")
        if not tool_name:
            resolution = resolve_context(
                (),
                profile=profile,
                discovery_error="TAPD MCP is not connected or does not expose tapd_projects",
            )
        else:
            try:
                result = await self.tool_lookup.execute(tool_name, user="")
                projects, error = parse_projects_payload(result)
            except Exception as exc:  # noqa: BLE001 - fail closed into the user-facing branch
                projects, error = [], f"TAPD project discovery failed: {type(exc).__name__}"
            resolution = resolve_context(
                projects,
                profile=profile,
                user_message=context.user_message,
                session_project=session_project,
                discovery_error=error,
            )

        self._tapd_projects = resolution.projects
        self._tapd_selected_project = resolution.selected_project
        context.metadata["tapd_context"] = {
            "status": resolution.status,
            "project_name": (
                resolution.selected_project.name if resolution.selected_project else ""
            ),
            "project_source": resolution.project_source,
            "missing": list(resolution.missing),
        }
        if resolution.project_source == "request" and resolution.selected_project is not None:
            await self._save_session_project(context.session_id, resolution.selected_project)
        return render_context_briefing(
            resolution,
            language=context.language,
            tapd_required=oracle_mode == "tapd",
        )

    async def _await_user_reply_and_resolve(
        self,
        *,
        context: UnifiedContext,
        stream: StreamBus,
        dispatch: Any,
    ) -> bool:
        """Reuse ask_user, capturing only TAPD context answers after resume."""
        waiter = context.metadata.get("wait_for_user_reply")
        captured: dict[str, Any] = {}

        if callable(waiter):

            async def _capturing_waiter() -> Any:
                raw = await waiter()
                captured["raw"] = raw
                return raw

            context.metadata["wait_for_user_reply"] = _capturing_waiter
        try:
            resumed = await super()._await_user_reply_and_resolve(
                context=context,
                stream=stream,
                dispatch=dispatch,
            )
        finally:
            if callable(waiter):
                context.metadata["wait_for_user_reply"] = waiter

        if not resumed or "raw" not in captured:
            return resumed
        selected = await self._persist_tapd_answers(context, captured["raw"])
        if selected is not None:
            for tool_message in dispatch.tool_messages:
                if tool_message.get("tool_call_id") == dispatch.pause_tool_call_id:
                    tool_message["content"] = (
                        f"{tool_message.get('content', '')}\n\n"
                        "[Host-resolved TAPD context] "
                        f"project={selected.name}; internal workspace_id={selected.id}. "
                        "Use the id only in tool arguments; do not show or ask it of the user."
                    )
                    break
        return resumed

    async def _persist_tapd_answers(
        self, context: UnifiedContext, raw_reply: Any
    ) -> TapdProject | None:
        answers = answer_map(raw_reply)
        relevant = {key: value for key, value in answers.items() if key.startswith("tapd_context_")}
        if not relevant:
            return None

        selected = project_from_answer(
            relevant.get("tapd_context_project", ""), self._tapd_projects
        )
        if selected is None:
            selected = self._tapd_selected_project
        if selected is not None and "tapd_context_project" in relevant:
            await self._save_session_project(context.session_id, selected)
            self._tapd_selected_project = selected

        from deeptutor.multi_user.paths import current_owner_id

        owner_id = current_owner_id()
        previous = load_profile(owner_id)
        identity = relevant.get("tapd_context_identity", "").strip() or previous.tapd_identity
        role = relevant.get("tapd_context_role", "").strip() or previous.role
        default_project = previous.default_project
        remember = relevant.get("tapd_context_remember_project", "")
        if selected is not None and wants_default(remember):
            default_project = selected
        if (
            identity != previous.tapd_identity
            or role != previous.role
            or default_project != previous.default_project
        ):
            save_profile(
                owner_id,
                TapdProfile(
                    tapd_identity=identity,
                    role=role,
                    default_project=default_project,
                ),
            )
        return selected

    def _logical_mcp_tool_name(self, logical_name: str) -> str:
        """Find an MCP logical name without assuming the user's server name."""
        matches: list[str] = []
        for tool in getattr(self, "_deferred_pool", ()):
            definition = tool.get_definition()
            original = str(getattr(tool, "original_name", "") or "")
            name = str(definition.name or "")
            if (
                original == logical_name
                or name == logical_name
                or name.endswith(f"_{logical_name}")
            ):
                matches.append(name)
        unique = sorted(set(matches))
        # Two configured servers exposing the same logical capability are not
        # interchangeable: picking one by sort order could use the wrong token.
        return unique[0] if len(unique) == 1 else ""

    @staticmethod
    def _test_oracle_mode(context: UnifiedContext) -> str:
        """Classify only explicit source evidence; uncertainty must not force TAPD."""
        declared = str(context.metadata.get("test_oracle_source") or "").strip().lower()
        if declared in {"tapd", "local"}:
            return declared
        text = str(context.user_message or "")
        folded = text.casefold()
        local_markers = (
            "需求正文",
            "验收标准",
            "以下需求",
            "本地需求",
            "requirement text",
            "acceptance criteria",
            "use this text",
            "local requirement",
        )
        if context.attachments or any(marker in folded for marker in local_markers):
            return "local"
        if (
            "tapd" in folded
            or "workspace_id" in folded
            or "story_id" in folded
            or re.search(r"(?<!\d)#?\d{5,}(?!\d)", folded)
        ):
            return "tapd"
        return "unknown"

    @staticmethod
    async def _load_session_project(session_id: str) -> TapdProject | None:
        if not session_id:
            return None
        try:
            from deeptutor.services.session import get_session_store

            session = await get_session_store().get_session(session_id)
        except Exception:  # noqa: BLE001 - absence means no session override
            return None
        preferences = session.get("preferences") if isinstance(session, dict) else None
        tapd = preferences.get("tapd_context") if isinstance(preferences, dict) else None
        raw = tapd.get("project") if isinstance(tapd, dict) else None
        if not isinstance(raw, dict):
            return None
        project_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        return TapdProject(id=project_id, name=name) if project_id and name else None

    @staticmethod
    async def _save_session_project(session_id: str, project: TapdProject) -> None:
        if not session_id:
            return
        try:
            from deeptutor.services.session import get_session_store

            await get_session_store().update_session_preferences(
                session_id,
                {"tapd_context": {"project": project.to_dict()}},
            )
        except Exception:  # noqa: BLE001 - one turn may continue without sticky override
            return
