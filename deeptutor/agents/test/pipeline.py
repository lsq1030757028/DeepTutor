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

from dataclasses import replace
import copy
import json
import os
import re
from typing import Any

from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
from deeptutor.capabilities.protocol import PromptBlock
from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream_bus import StreamBus
from deeptutor.runtime.providers.scope import ToolScope
from deeptutor.services.prompt.manager import PromptManager
from deeptutor.services.tapd_field_baseline import (
    candidate_query_template,
    render_candidate_shape_block,
)
from deeptutor.services.tapd_business import (
    SUPPORTED_BUSINESS_QUESTION,
    TapdStory,
    baseline_is_ready,
    is_supported_business_question,
    journey_ingest_arguments,
    parse_business_query,
    render_baseline_block,
    render_business_outcome,
    render_semantic_block,
    semantic_gate,
    story_from_answer,
)
from deeptutor.services.tapd_context import (
    TapdProject,
    TapdSessionContext,
    accessible_project_names,
    answer_map,
    parse_public_payload,
    project_from_answer,
    project_from_resolve_payload,
    project_hint_from_message,
    render_context_briefing,
    require_visible_project_confirmation,
    resolve_shared_context,
    validate_context_save_payload,
    validate_context_status_payload,
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
JOURNEY_PROMPT_KEYS = (
    "journey_discipline",
    "human_gates",
    "answer_discipline",
    "workbench_pointer",
)

#: yaml 里**不拼进系统块**的那一段：`loop.finish_exhausted` 是轮次预算耗尽时
#: 追加的 user 消息，不是 system prompt 的一节，所以它写成嵌套 mapping 而不是
#: 顶层字符串（顶层字符串会被 `test_every_declared_prompt_section_reaches_the_
#: rendered_prompt` 要求进 JOURNEY_PROMPT_KEYS，那会把它错挂进系统块）。
LOOP_PROMPT_SECTION = "loop"

#: Test 模式一轮的**最低**轮次预算。
#:
#: 基线是 `DEFAULT_MAX_ROUNDS = 8`（`agents/chat/agentic_pipeline.py`），
#: 对普通聊天够用，对本模式不够：2026-08-29「帮我拉一下 TAPD 的需求」这一句
#: 冷启动要走完 项目上下文 → 字段语义确认 → 候选查询 → 交叉验证 → 取条目，
#: 实测一轮 8-10 次工具调用、每次占一轮，于是**正常任务必然触顶**，
#: 收尾话术把用户变成点火键（"回复继续"），一次请求被切成 N 次人肉续跑。
#:
#: 24 的取法：实测上界 10 轮，留一倍余量给交叉验证与澄清卡，再加收尾一轮。
#: 它是**兜底**不是目标——没有工具调用的一轮照旧提前结束循环，
#: 正常任务根本走不到这个数；走到了就说明真失控，那时才该触顶。
TEST_MODE_MIN_LOOP_ROUNDS = 24

#: 上面那个数的运行时开关。fork 侧刻意走环境变量而不是 `capabilities.chat`
#: 的配置键：后者要改 `services/config/loader.py` 的 `DEFAULT_CHAT_PARAMS`
#: （未知键会被 `get_chat_params` 过滤掉），那是**新增一个上游触点**，
#: 而登记表只剩 1 行硬余量（`extensions/UPSTREAM-TOUCHPOINTS.md`）。
TEST_MODE_MAX_ROUNDS_ENV = "DEEPTUTOR_TEST_MODE_MAX_ROUNDS"

#: 轮次预算抬升走的那个键。宿主侧的通用缝，subagent capability 用的也是它
#: （`capabilities/subagent/capability.py`），所以这里只写值不改循环。
MIN_LOOP_ROUNDS_KEY = "_min_loop_rounds"


def journey_round_floor() -> int:
    """本模式这一轮的轮次下限。环境变量能调，非法值按默认走。"""
    raw = os.getenv(TEST_MODE_MAX_ROUNDS_ENV)
    if raw is None or not str(raw).strip():
        return TEST_MODE_MIN_LOOP_ROUNDS
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return TEST_MODE_MIN_LOOP_ROUNDS
    return value if value >= 1 else TEST_MODE_MIN_LOOP_ROUNDS

#: 本模式**主用**的旅程工具前缀。带前缀而不是列名字：扩展侧新增一个
#: `journey_*` 工具不该在这边变成「连上了但够不着」。
#:
#: 为什么必须直接进窗口，而不是留在 `load_tools` 后面：纪律段（`test_journey`
#: 提示词块）通篇按 `journey_ingest` / `journey_adopt` 这种**裸名**指挥模型，
#: 而唯一能派发的名字是带服务器前缀的 `mcp_test-partner_journey_ingest`——
#: `DeferredToolLoader.load()` 只认后者。于是提示词点名的工具，模型照着点名
#: 去加载必然拿到 `unknown`，而真名只在 extended_tools 清单的末尾出现过一次。
#: 把 schema 直接放进窗口，模型看到的就是能调的那个名字，两步舞和名字错配
#: 一起消失。
#:
#: 实测代价（真实会话 `unified_1787233269913_fd5966af` 的 header 侧车）：
#: 该部署放行 13 个 journey 工具，schema 2,704 tokens + system prompt 的
#: `## tools` 提示行 1,070 tokens；同时它们从 extended_tools 清单里退出，
#: 净增约 1.9k tokens。
JOURNEY_PRELOAD_PREFIXES = frozenset({"journey_"})

#: TAPD **只读**主用集：进模型窗口的那几个。
#:
#: 2026-08-22 全表重写。上一版写的是**已退役的第三方 mcp-server-tapd** 的名字
#: （get_stories_or_tasks / get_bug / get_workspace_info …）。服务换成自有的
#: tapd-capability 之后，这份清单与真实工具面**零重合**：3796 上 19 个工具连着，
#: 一个都进不了窗口，轨迹里是 `窗口=34 | tapd类=0`，模型于是照实说"没连通"。
#: 这就是本轮反复出现的那个形状——一侧改了、另一侧的名字清单没跟着改，
#: 而且**离线测试发现不了**，因为常量和真实服务之间此前没有任何对拍。
#: 下面 `_preload_names_resolved()` 与 MCP_PRELOAD_NAMES_STALE 就是补这个洞。
#:
#: 选取依据仍是纪律段真正点名的动作，不是把服务端整个搬进来：
#:   ·「本轮就去查」候选需求      → tapd_find（拿句柄与命中数）→ tapd_cite（取具体条目）
#:   ·「可信业务结论」            → tapd_semantic_status / tapd_business_query
#:   ·「不要靠状态名字去猜」      → tapd_semantic_review / tapd_semantic_confirm
#:                                （状态语义的人核环节，模型要能走完，否则永远卡在
#:                                 SEMANTIC_UNCONFIRMED）
#:   ·「字段号不许写死」          → tapd_field_config
#:   ·「TAPD 能不能访问」        → tapd_projects / tapd_workspace / tapd_probe
#:
#: **`tapd_context_*` 刻意不进窗口。** 它们是宿主的闸：`_tapd_context_briefing`
#: 在首次模型调用之前就把项目上下文解析完了。放进窗口只会诱导模型再去问用户
#: 「哪个项目 / 你是谁 / 什么角色」，而纪律段明令禁止问这些。
#: 不进窗口**不影响宿主取用**——`_logical_mcp_tool_name` 找的是 `_deferred_pool`，
#: 预载只改变 schema 到达窗口的时机，不改变可见性（见 `_tool_scope` 的说明）。
#:
#: 基线那一套（tapd_baseline_*/watchlist/field_evidence）同样留在 extended_tools：
#: `tapd_baseline_status` 由宿主自己调，其余按需 `load_tools`，一次也没变得够不着。
#:
#: 这一整条仍是**一个可撤的杠杆**：清空这个 frozenset，窗口立刻退回基线大小，
#: journey 那半分文不动。要加要减，先量再改，别凭手感。
#:
#: 只放**读**动作。写类工具连出现在这里都不该出现；
#: `tapd_semantic_confirm` 与 `tapd_context_save` 虽然带 workspace-write，
#: 但写的是本地确认记录、不写 TAPD（服务端 `tapd_write=false`），
#: 前者是人核环节必需所以进，后者按上面的理由不进。
TAPD_PRELOAD_READ_TOOLS = frozenset(
    {
        "tapd_projects",
        "tapd_workspace",
        "tapd_probe",
        "tapd_field_config",
        "tapd_find",
        "tapd_cite",
        "tapd_semantic_status",
        "tapd_semantic_review",
        "tapd_semantic_confirm",
        "tapd_business_query",
    }
)


def journey_system_block(language: str = "zh") -> str:
    """旅程纪律系统块。加载失败**如实返回空串**，不塞一段兜底文案。

    塞兜底文案会让「prompt 模块没注册」这个配置错误看起来像正常工作——
    正是 MODULES 那条漏改不报错的放大器。空串至少会让人发现模型没按纪律走。
    """
    prompts = _load_journey_prompts(language)
    parts = [str(prompts.get(key) or "").strip() for key in JOURNEY_PROMPT_KEYS]
    return "\n\n".join(p for p in parts if p)


def _load_journey_prompts(language: str) -> dict[str, Any]:
    try:
        return PromptManager().load_prompts(PROMPT_MODULE, PROMPT_AGENT, language=language) or {}
    except Exception:  # noqa: BLE001 - 提示词读不到不该让整轮崩
        return {}


def journey_loop_prompt(key: str, language: str = "zh") -> str:
    """`loop.<key>` 那一段。读不到返回空串，由调用方回落到宿主默认。"""
    section = _load_journey_prompts(language).get(LOOP_PROMPT_SECTION)
    if not isinstance(section, dict):
        return ""
    return str(section.get(key) or "").strip()


def enforce_adoption_card(
    args: dict[str, Any],
    cards: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """把模型的 ask_user 参数按服务端签发的采纳卡校回（BB-715 补修）。

    纯函数。`cards` 是本轮 journey_prepare_adoption 返回的 `card`/`disclosure_md`，
    按卡 id 索引。对 `questions` 里 id 命中的问题：选项、多选、自由文本三项以卡为
    准（卡内容服务端验签，改了采纳就对不上）；intro 必须以 `disclosure_md` 原文
    打头——模型自己写的导语保留在披露之后，不命中任何卡时原样返回。
    """
    questions = args.get("questions")
    if not isinstance(questions, list) or not cards:
        return args
    matched_disclosures: list[str] = []
    new_questions: list[Any] = []
    for question in questions:
        if not isinstance(question, dict):
            new_questions.append(question)
            continue
        issued = cards.get(str(question.get("id") or "").strip())
        if not issued:
            new_questions.append(question)
            continue
        card = issued.get("card") or {}
        fixed = dict(question)
        if isinstance(card.get("options"), list):
            fixed["options"] = copy.deepcopy(card["options"])
        fixed["multi_select"] = bool(card.get("multi_select", True))
        fixed["allow_free_text"] = bool(card.get("allow_free_text", False))
        fixed.pop("multiSelect", None)
        new_questions.append(fixed)
        disclosure = str(issued.get("disclosure_md") or "").strip()
        if disclosure and disclosure not in matched_disclosures:
            matched_disclosures.append(disclosure)
    if new_questions == questions and not matched_disclosures:
        return args
    fixed_args = dict(args)
    fixed_args["questions"] = new_questions
    if matched_disclosures:
        own_intro = str(args.get("intro") or "").strip()
        required = "\n\n".join(matched_disclosures)
        if required not in own_intro:
            fixed_args["intro"] = required + ("\n\n" + own_intro if own_intro else "")
    return fixed_args


class TestJourneyPipeline(AgenticChatPipeline):
    """主聊 pipeline + 旅程纪律 + TAPD 的宿主级轻上下文。"""

    _tapd_selected_project: TapdProject | None = None
    _tapd_project_options: tuple[str, ...] = ()
    _tapd_identity: str = ""
    _tapd_role: str = ""
    _tapd_session_context: str = ""
    _tapd_story_candidates: tuple[TapdStory, ...] = ()
    _tapd_selected_story: TapdStory | None = None

    def _tool_scope(self, context: UnifiedContext) -> ToolScope:
        """主用工具直接进窗口，不留在两步舞后面。

        只声明「哪些是主用的」，不碰授权：`build_tool_view` 拿这个集合去和
        本轮真实允许的池子求交，没连上的不会凭空出现，没授权的也进不来。
        所以这一步只能改变 schema 到达的**时机**，改变不了可见性。
        """
        return replace(
            super()._tool_scope(context),
            preload_original_prefixes=JOURNEY_PRELOAD_PREFIXES,
            preload_original_names=TAPD_PRELOAD_READ_TOOLS,
        )

    def _capability_system_blocks(self, context: UnifiedContext) -> list[PromptBlock]:
        # 先拿父类的（用户另外开着的 loop capability 照常生效），再追加自己的。
        # 追加而不是替换：这一模式不排他，它只是多一段纪律。
        blocks = list(super()._capability_system_blocks(context))
        content = journey_system_block(self.language)
        if content:
            blocks.append(PromptBlock(JOURNEY_BLOCK_NAME, content))
        return blocks

    def _finish_exhausted_instruction(self) -> str:
        """轮次触顶时追加的收尾指令 —— 本模式覆盖宿主那句。

        宿主默认只说「停止调用工具、基于已有材料作答、简短说明不确定的部分」。
        它没禁掉两件真发生过的事（2026-08-29 会话 unified_1787911414542_1ab2ebda
        与同日 TAPD 拉需求那两轮）：把回复写成「当前进度小结 / 已完成 / 还没完成 /
        下一步」的工作汇报，以及让用户回一句"继续"当点火键——**一次请求被切成
        N 次人肉续跑**。所以这里换成一句交付优先的指令，读不到就回落宿主的。
        """
        own = journey_loop_prompt("finish_exhausted", self.language)
        return own or super()._finish_exhausted_instruction()

    def _apply_round_floor(self, context: UnifiedContext) -> int:
        """把本模式的轮次下限写进宿主的通用缝，取较大值不覆盖别人的。

        `effective_max_rounds` 读的就是这个键；写而不是改循环，是为了让
        subagent 之类同时活跃的能力各自的下限都还算数。
        """
        try:
            current = int(context.metadata.get(MIN_LOOP_ROUNDS_KEY) or 0)
        except (TypeError, ValueError):
            current = 0
        floor = max(current, journey_round_floor())
        context.metadata[MIN_LOOP_ROUNDS_KEY] = floor
        return floor

    async def _capability_pre_loop_briefings(
        self,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> str:
        """Resolve TAPD project scope before the first Test-mode model call.

        The credential stays inside the user's MCP connection. This adapter
        consumes the shared context status/resolve contract and keeps only an
        explicit current-session override inside DeepTutor.
        """
        # 第一句就抬轮次预算：后面任何一段简报失败都不该把这一轮打回 8 轮，
        # 那正是「正常任务必然触顶」的形态。
        self._apply_round_floor(context)
        await self._announce_turn_language(context, stream)
        inherited = await super()._capability_pre_loop_briefings(context, stream)
        tapd = await self._tapd_context_briefing(context)
        return "\n\n".join(part for part in (inherited.strip(), tapd.strip()) if part)

    async def _announce_turn_language(self, context: UnifiedContext, stream: StreamBus) -> None:
        """把本轮语种写进事件流，卡片外壳按它渲染（BB-698 的前端半边）。

        `ask_user` 卡的 Pick an option / Next question、四张富卡的标题与页脚是
        前端 i18n 串，默认跟界面语言。用户中文提问、界面仍是 en 时，正文中文而
        外壳英文——同一张卡两种语言。事件里带 `turn_language`，前端对本轮的卡片
        改用这个语种取文案；不带时前端照旧跟界面（普通聊天零变化）。
        """
        language = "zh" if str(context.language or "").lower().startswith("zh") else "en"
        context.metadata["turn_language"] = language
        try:
            await stream.progress(
                "",
                source="chat",
                stage="exploring",
                metadata={"trace_kind": "turn_language", "turn_language": language},
            )
        except Exception:  # noqa: BLE001 - 外壳语种只是锦上添花，不能沉掉这一轮
            return

    async def _tapd_context_briefing(self, context: UnifiedContext) -> str:
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
        if oracle_mode == "unknown":
            context.metadata["tapd_context"] = {"status": "not_selected", "missing": []}
            return (
                "[需求来源尚未确定]\n不要默认启用 TAPD，也不要询问项目、身份或角色。"
                "只用一句业务问题确认：从 TAPD 取需求，还是使用当前提供的正文或附件？"
                if context.language.lower().startswith("zh")
                else "[Requirement source not selected]\nDo not enable TAPD by default or ask for project, "
                "identity, or role. Ask one business question: fetch from TAPD, or use the supplied "
                "text or attachment?"
            )

        session = await self._load_session_context(context.session_id)
        status_name = self._logical_mcp_tool_name("tapd_context_status")
        resolve_name = self._logical_mcp_tool_name("tapd_context_resolve")
        duplicate = any(
            self._logical_mcp_tool_count(name) > 1
            for name in ("tapd_context_status", "tapd_context_resolve")
        )
        # Three different situations, and the model has to be able to tell the
        # user which one it is. They used to share one message --
        # "TAPD MCP is not connected or missing shared context operations" --
        # which fused "nothing is connected" with "something is connected but
        # it is the wrong server". The model then relayed whichever half it
        # picked, and told the user TAPD was unreachable while a TAPD server
        # was in fact answering, just without the contract tools. A message
        # that is true of two mutually exclusive states cannot help the reader
        # act on either.
        any_tapd_tool = any(
            self._logical_mcp_tool_count(name) > 0
            for name in (
                "tapd_context_status",
                "tapd_context_resolve",
                "tapd_business_query",
                "tapd_baseline_status",
            )
        )
        if duplicate or not status_name or not resolve_name:
            resolution = resolve_shared_context(None, None, session=session)
            if duplicate:
                code = "DUPLICATE_PROVIDER"
                message = "检测到重复的 TAPD MCP 提供方"
            elif not any_tapd_tool:
                code = "MCP_NOT_CONNECTED"
                message = (
                    "TAPD MCP 服务没有连上：本轮工具里找不到任何 TAPD 工具。"
                    "请在 MCP Services 里检查该服务是否已注册并启用"
                )
            else:
                code = "MCP_CONTRACT_TOOLS_MISSING"
                message = (
                    "TAPD MCP 服务连上了，但缺少共享上下文契约工具"
                    "（tapd_context_status / tapd_context_resolve）。"
                    "多半是连到了只提供裸查询的旧服务，请改连 tapd-capability"
                )
            resolution = resolution.__class__(status="unavailable", error_code=code, error=message)
        else:
            try:
                status_raw = await self.tool_lookup.execute(status_name)
                status = parse_public_payload(status_raw)
                if not validate_context_status_payload(status):
                    resolution = resolve_shared_context(None, None, session=session)
                else:
                    names = accessible_project_names(status)
                    hint = project_hint_from_message(
                        context.user_message,
                        names,
                        session.project,
                    )
                    resolve_kwargs = {"project_hint": hint}
                    profile = status.get("profile")
                    identity = session.tapd_identity
                    role = session.business_role
                    if isinstance(profile, dict):
                        identity = identity or str(profile.get("tapd_identity") or "")
                        role = role or str(profile.get("business_role") or "")
                    if hint and identity and role:
                        resolve_kwargs.update(
                            tapd_identity=identity,
                            business_role=role,
                        )
                    resolve_raw = await self.tool_lookup.execute(resolve_name, **resolve_kwargs)
                    resolved = parse_public_payload(resolve_raw)
                    resolution = resolve_shared_context(status, resolved, session=session)
                    # BB-716：档案默认在冷会话里要让用户看见，不许静默沿用。
                    resolution = require_visible_project_confirmation(
                        resolution, session=session
                    )
            except Exception as exc:  # noqa: BLE001 - normalize provider failures
                resolution = resolve_shared_context(None, None, session=session)
                resolution = resolution.__class__(
                    status="unavailable",
                    error_code="MCP_CALL_FAILED",
                    error=f"TAPD context call failed: {type(exc).__name__}",
                )

        self._tapd_project_options = resolution.project_options
        self._tapd_selected_project = resolution.selected_project
        self._tapd_identity = resolution.tapd_identity
        self._tapd_role = resolution.role
        self._tapd_session_context = resolution.session_context
        context.metadata["tapd_context"] = {
            "status": resolution.status,
            "project_name": (
                resolution.selected_project.name if resolution.selected_project else ""
            ),
            "project_source": resolution.project_source,
            "missing": list(resolution.missing),
        }
        if resolution.project_source == "explicit" and resolution.selected_project is not None:
            await self._save_session_context(
                context.session_id,
                TapdSessionContext(
                    project=resolution.selected_project,
                    tapd_identity=resolution.tapd_identity,
                    business_role=resolution.role,
                    session_context=resolution.session_context,
                    session_context_expires_at=resolution.session_context_expires_at,
                ),
            )
        briefing = render_context_briefing(resolution, language=context.language)
        if resolution.status == "ready":
            # 候选清单口径无条件带上（AC1）：探索段的开口五花八门（"拉一下需求"
            # "有什么要测的"），用一条问句正则去 gate 它，正是 F-3 那场审讯的
            # 上游成因——匹配不上就当没有这个能力，模型只好回头追问 ID。
            # 口径块本身只读、无副作用，带上的代价是几行文字。
            shape = render_candidate_shape_block(
                candidate_query_template(
                    resolution.selected_project.id if resolution.selected_project else ""
                ),
                language=context.language,
            )
            if shape:
                briefing = f"{briefing}\n\n{shape}"
        if resolution.status == "ready" and is_supported_business_question(context.user_message):
            # 宿主级可信查询这条更强的通道保持原样：它对那一个固定问题
            # 已经按「字段基线 → 语义 → 可信查询」失败关闭过一遍。
            business = await self._tapd_business_briefing(context, resolution.selected_project)
            if business:
                briefing = f"{briefing}\n\n{business}"
        stale = self._stale_preload_notice(context)
        if stale:
            briefing = f"{briefing}\n\n{stale}" if briefing else stale
        return briefing

    def _stale_preload_notice(self, context: UnifiedContext) -> str:
        """Say so when TAPD is connected but no preloaded name matches it.

        This check sits OUTSIDE the context gate on purpose. A first attempt
        put it inside, where it could never have fired: that gate only runs
        when the contract tools are MISSING, whereas the failure it is meant
        to catch had them present -- context_status and context_resolve both
        resolved, the host briefing succeeded, and the drift stayed invisible
        until a real deployment showed `window=34 | tapd-class=0`.

        The symptom is indistinguishable from a dead connection unless someone
        names it, and last time nobody did: the model said "not connected",
        which sent the search toward the network.
        """
        if not TAPD_PRELOAD_READ_TOOLS or self._preload_names_resolved() > 0:
            return ""
        connected = any(
            self._logical_mcp_tool_count(name) > 0
            for name in (
                "tapd_context_status",
                "tapd_context_resolve",
                "tapd_business_query",
                "tapd_baseline_status",
                "tapd_projects",
            )
        )
        if not connected:
            # Nothing is connected at all -- the context gate already said so,
            # and a second voice saying it differently only muddies the report.
            return ""
        context.metadata["tapd_preload"] = {"status": "stale", "resolved": 0}
        return (
            "[TAPD 工具预载清单已过期（宿主检测）]\n"
            "TAPD 服务连上了，但宿主的预载清单里没有一个名字与该服务实际暴露的工具"
            "对得上，所以这一轮**没有任何 TAPD 工具进入你的可用工具**。\n"
            "这不是连接问题，也不是权限问题，是宿主侧 TAPD_PRELOAD_READ_TOOLS 与"
            "服务端工具名不一致（多半是服务换了、清单没跟着改）。\n"
            "请如实告诉用户：TAPD 是连着的，但工具没挂上，需要开发改宿主的预载清单；"
            "不要说“连不上 TAPD”，那会把人引到网络上白查。"
        )

    async def _tapd_business_briefing(
        self,
        context: UnifiedContext,
        project: TapdProject | None,
    ) -> str:
        """Run the fixed baseline -> semantic -> business-query trust chain."""
        self._tapd_story_candidates = ()
        self._tapd_selected_story = None
        if project is None:
            return render_business_outcome(parse_business_query(None), language=context.language)
        logical_names = (
            "tapd_baseline_status",
            "tapd_semantic_status",
            "tapd_business_query",
        )
        if any(self._logical_mcp_tool_count(name) != 1 for name in logical_names):
            return render_business_outcome(parse_business_query(None), language=context.language)
        tool_names = {name: self._logical_mcp_tool_name(name) for name in logical_names}
        try:
            baseline_raw = await self.tool_lookup.execute(
                tool_names["tapd_baseline_status"], workspace_id=project.id
            )
            baseline = parse_public_payload(baseline_raw)
            if not baseline_is_ready(baseline):
                return render_baseline_block(language=context.language)

            identity_kwargs = (
                {"session_context": self._tapd_session_context}
                if self._tapd_session_context
                else {}
            )
            semantic_raw = await self.tool_lookup.execute(
                tool_names["tapd_semantic_status"],
                workspace_id=project.id,
                **identity_kwargs,
            )
            semantic = parse_public_payload(semantic_raw)
            expected_identity_source = (
                "session_context" if self._tapd_session_context else "persistent_profile"
            )
            gate = semantic_gate(
                semantic,
                expected_identity_source=expected_identity_source,
            )
            if not gate.ready:
                return render_semantic_block(
                    language=context.language,
                    error_code=gate.error_code,
                )

            query_raw = await self.tool_lookup.execute(
                tool_names["tapd_business_query"],
                workspace_id=project.id,
                question=SUPPORTED_BUSINESS_QUESTION,
                limit=20,
                **identity_kwargs,
            )
            outcome = parse_business_query(
                parse_public_payload(query_raw),
                expected_workspace_id=project.id,
                expected_identity_source=expected_identity_source,
            )
        except Exception:  # noqa: BLE001 - provider details must not enter the conversation
            outcome = parse_business_query(None)
        self._tapd_story_candidates = outcome.stories
        context.metadata["tapd_business"] = {
            "status": outcome.status,
            "matched_count": outcome.matched_count,
            "returned_count": len(outcome.stories),
        }
        return render_business_outcome(outcome, language=context.language)

    # ---- 采纳卡：导语与选项以服务端签发为准（BB-715 补修，2026-09-05） ----------
    #
    # 采纳卡由 journey_prepare_adoption 签发：`card`（id/选项/多选/禁自由文本）
    # 和 `disclosure_md`（逐条 写什么/怎么清理/凭什么授权）。提示词要求模型把卡
    # 原样传给 ask_user、intro 用 disclosure_md 原文——UAT 实测模型照贴时顺手删掉了
    # 值为空的行，卡上 d3~d8 就没有"写什么"。**用户看到的披露不能取决于模型抄得
    # 认不认真**：宿主记住本轮签发过的卡，模型调 ask_user 时把命中的问题按卡
    # 校回、intro 以 disclosure_md 原文打头。模型自己的话保留在披露之后。

    _adoption_cards: dict[str, dict[str, Any]] | None = None

    def _remember_adoption_cards(self, tool_messages: list[dict[str, Any]]) -> None:
        for message in tool_messages or []:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or "disclosure_md" not in content:
                continue
            start = content.find("{")
            if start < 0:
                continue
            try:
                data = json.loads(content[start:])
            except ValueError:
                continue
            card = data.get("card") if isinstance(data, dict) else None
            if not isinstance(card, dict) or not str(card.get("id") or "").strip():
                continue
            if self._adoption_cards is None:
                self._adoption_cards = {}
            self._adoption_cards[str(card["id"])] = {
                "card": copy.deepcopy(card),
                "disclosure_md": str(data.get("disclosure_md") or ""),
            }

    async def _dispatch_tool_calls(
        self,
        *,
        tool_calls: list[dict[str, Any]],
        context: UnifiedContext,
        stream: StreamBus,
        iteration_index: int,
        stage: str = "exploring",
    ) -> Any:
        outcome = await super()._dispatch_tool_calls(
            tool_calls=tool_calls,
            context=context,
            stream=stream,
            iteration_index=iteration_index,
            stage=stage,
        )
        self._remember_adoption_cards(list(getattr(outcome, "tool_messages", []) or []))
        return outcome

    def _augment_tool_kwargs(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: UnifiedContext,
    ) -> dict[str, Any]:
        kwargs = super()._augment_tool_kwargs(tool_name, args, context)
        if tool_name == "ask_user" and self._adoption_cards:
            kwargs = enforce_adoption_card(kwargs, self._adoption_cards)
        return kwargs

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
                    if self._tapd_selected_story is not None:
                        args = journey_ingest_arguments(selected, self._tapd_selected_story)
                        context.metadata["tapd_journey_handoff"] = {
                            "status": "prepared_not_executed",
                            "reason": "journey_ingest remains model-dispatched after the depth gate",
                        }
                        tool_message["content"] = (
                            f"{tool_message.get('content', '')}\n\n"
                            "[Host-prepared TAPD requirement; journey ingestion is not yet executed] "
                            f"workspace_id={args['workspace_id']}; story_id={args['story_id']}; "
                            f"title={args['title']}; source_kind=tapd. "
                            "Call journey_ingest with these internal arguments after the depth gate; "
                            "never repeat the ids in an ordinary answer."
                        )
                    else:
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
        story_answer = answers.get("tapd_story_selection", "")
        selected_story = story_from_answer(story_answer, self._tapd_story_candidates)
        if selected_story is not None:
            self._tapd_selected_story = selected_story
        relevant = {key: value for key, value in answers.items() if key.startswith("tapd_context_")}
        if not relevant:
            return self._tapd_selected_project if selected_story is not None else None

        selected = project_from_answer(
            relevant.get("tapd_context_project", ""),
            (self._tapd_selected_project,) if self._tapd_selected_project else (),
            self._tapd_project_options,
        )
        if selected is None:
            selected = self._tapd_selected_project
        previous = await self._load_session_context(context.session_id)
        # 身份与角色的回落顺序：这张卡的答复 → 本会话已存的 → 宿主简报刚从共享
        # 档案读到的（`_tapd_context_briefing` 写进 self._tapd_identity/_role）。
        # 缺了第三级，冷会话里确认项目就会因"身份未知"而拒绝签发会话上下文，
        # 而档案里明明有身份（BB-716 的确认卡走的正是这条路）。
        identity = (
            relevant.get("tapd_context_identity", "").strip()
            or previous.tapd_identity
            or self._tapd_identity
        )
        role = (
            relevant.get("tapd_context_role", "").strip()
            or previous.business_role
            or self._tapd_role
        )
        remember = relevant.get("tapd_context_remember_project", "")
        persist_default = wants_default(remember)
        needs_session_claim = bool(selected is not None and not persist_default)
        if selected is not None and (not selected.id or needs_session_claim):
            resolve_name = self._logical_mcp_tool_name("tapd_context_resolve")
            if not resolve_name:
                return None
            resolve_kwargs = {"project_hint": selected.name}
            if needs_session_claim:
                if not identity or not role:
                    return None
                resolve_kwargs.update(tapd_identity=identity, business_role=role)
            try:
                resolved = await self.tool_lookup.execute(resolve_name, **resolve_kwargs)
            except Exception:  # noqa: BLE001 - an unverified project cannot enter the session
                return None
            resolved_payload = parse_public_payload(resolved)
            selected = project_from_resolve_payload(resolved_payload)
            if selected is None:
                return None
            resolution = resolved_payload or {}
            claim = str(resolution.get("session_context") or "")
            claim_expires = str(resolution.get("session_context_expires_at") or "")
            if needs_session_claim and (not claim or not claim_expires):
                return None
        else:
            claim = previous.session_context if not persist_default else ""
            claim_expires = previous.session_context_expires_at if not persist_default else ""
        session_context = TapdSessionContext(
            project=selected or previous.project,
            tapd_identity=identity or self._tapd_identity,
            business_role=role or self._tapd_role,
            session_context=claim,
            session_context_expires_at=claim_expires,
        )
        await self._save_session_context(context.session_id, session_context)
        self._tapd_selected_project = session_context.project
        self._tapd_identity = session_context.tapd_identity
        self._tapd_role = session_context.business_role
        self._tapd_session_context = session_context.session_context

        if selected is not None and persist_default:
            save_name = self._logical_mcp_tool_name("tapd_context_save")
            if save_name:
                try:
                    saved = await self.tool_lookup.execute(
                        save_name,
                        default_project=selected.name,
                        tapd_identity=session_context.tapd_identity,
                        business_role=session_context.business_role,
                    )
                    saved_payload = parse_public_payload(saved)
                    if not validate_context_save_payload(saved_payload):
                        return None
                except Exception:  # noqa: BLE001 - session remains valid; default was not proven saved
                    return None
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
        # Two configured servers exposing the same logical capability are not
        # interchangeable: picking one by sort order could use the wrong token.
        return matches[0] if len(matches) == 1 else ""

    def _preload_names_resolved(self) -> int:
        """How many of TAPD_PRELOAD_READ_TOOLS actually exist in this turn's pool.

        Exists because a stale constant is invisible offline. When the TAPD
        service was swapped for tapd-capability the preload set still named the
        retired third-party tools; 19 tools were connected and **zero** reached
        the model window, so the model truthfully reported "not connected" while
        the connection was fine. No offline test could catch it: the constant
        and the live tool surface had nothing comparing them.

        This is that comparison, done at the only moment both are knowable.
        """
        return sum(
            1 for name in TAPD_PRELOAD_READ_TOOLS if self._logical_mcp_tool_count(name) > 0
        )

    def _logical_mcp_tool_count(self, logical_name: str) -> int:
        return sum(
            1
            for tool in getattr(self, "_deferred_pool", ())
            if str(getattr(tool, "original_name", "") or "") == logical_name
            or str(tool.get_definition().name or "") == logical_name
            or str(tool.get_definition().name or "").endswith(f"_{logical_name}")
        )

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
        if is_supported_business_question(text):
            return "tapd"
        if (
            "tapd" in folded
            or "workspace_id" in folded
            or "story_id" in folded
            or re.search(r"(?<!\d)#?\d{5,}(?!\d)", folded)
        ):
            return "tapd"
        return "unknown"

    @staticmethod
    async def _load_session_context(session_id: str) -> TapdSessionContext:
        if not session_id:
            return TapdSessionContext()
        try:
            from deeptutor.services.session import get_session_store

            session = await get_session_store().get_session(session_id)
        except Exception:  # noqa: BLE001 - absence means no session override
            return TapdSessionContext()
        preferences = session.get("preferences") if isinstance(session, dict) else None
        tapd = preferences.get("tapd_context") if isinstance(preferences, dict) else None
        raw = tapd.get("project") if isinstance(tapd, dict) else None
        project = None
        if isinstance(raw, dict):
            project_id = str(raw.get("id") or "").strip()
            name = str(raw.get("name") or "").strip()
            if name:
                project = TapdProject(id=project_id, name=name)
        return TapdSessionContext(
            project=project,
            tapd_identity=(
                str(tapd.get("tapd_identity") or "").strip() if isinstance(tapd, dict) else ""
            ),
            business_role=(
                str(tapd.get("business_role") or "").strip() if isinstance(tapd, dict) else ""
            ),
            session_context=(
                str(tapd.get("session_context") or "").strip()
                if isinstance(tapd, dict) and isinstance(tapd.get("session_context"), str)
                else ""
            ),
            session_context_expires_at=(
                str(tapd.get("session_context_expires_at") or "").strip()
                if isinstance(tapd, dict)
                and isinstance(tapd.get("session_context_expires_at"), str)
                else ""
            ),
        )

    @staticmethod
    async def _save_session_context(session_id: str, value: TapdSessionContext) -> None:
        if not session_id:
            return
        try:
            from deeptutor.services.session import get_session_store

            await get_session_store().update_session_preferences(
                session_id,
                {
                    "tapd_context": {
                        "project": value.project.to_dict() if value.project else None,
                        "tapd_identity": value.tapd_identity,
                        "business_role": value.business_role,
                        "session_context": value.session_context,
                        "session_context_expires_at": value.session_context_expires_at,
                    }
                },
            )
        except Exception:  # noqa: BLE001 - one turn may continue without sticky override
            return
