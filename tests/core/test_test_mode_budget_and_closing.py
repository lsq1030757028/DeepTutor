"""[fork] 「测试」模式的**轮次预算**与**触顶收尾话术**判据。

## 起因（2026-08-29，用户内测）

用户在 Test 模式说「帮我拉一下 TAPD 的需求」，连着两轮都以

    这一轮的工具调用额度已经用完，回复"继续"

收尾，正文是「当前进度小结 / 已完成 / 还没完成 / 下一步」的三段工作汇报，
满篇内部行话（语义口径、谓词、句柄、分页快照、闸）。用户的原话是"像个智障"。

两个独立的洞，各有各的判据：

1. **预算**：宿主基线 `DEFAULT_MAX_ROUNDS = 8`。这一句冷启动要走
   项目上下文 → 字段语义确认 → 候选查询 → 交叉验证 → 取条目，实测 8-10 次
   工具调用、每次占一轮，于是**正常任务必然触顶**——一次请求被切成 N 次
   人肉续跑。抬到 24 之后触顶重新变成异常路径。
2. **收尾**：宿主那句 `finish_exhausted` 只说「停止调用工具、基于已有材料作答」，
   没禁掉汇报体，也没禁掉"让用户回'继续'"。模式自己的 system prompt 明明写着
   「不要描述内部阶段」——**没被违反的规则和没有规则，只有在触顶那一刻才区分得开**，
   因为那一刻追加的 user 消息才是模型最后读到的指令。

## 判据落这里的理由

与 `test_fork_capabilities_runtime.py` 同一层：回归闸第 4 层跑的是
`pytest tests/api tests/core`，`tests/` 根跑不到。
"""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.core.context import UnifiedContext


def _context() -> UnifiedContext:
    return UnifiedContext(
        session_id="s-budget",
        user_message="帮我拉一下TAPD的需求",
        active_capability="test",
    )


# --- 轮次预算 ---------------------------------------------------------------


def test_test_mode_lifts_the_round_budget_well_above_the_chat_baseline() -> None:
    """抬的是**生效值**，不是某个常量的字面量。

    只断 `TEST_MODE_MIN_LOOP_ROUNDS == 24` 会在「常量改了但没人读它」时照样绿——
    那正是 BB-508 的形状。所以断 `effective_max_rounds` 这个循环真正读的数。
    """
    from deeptutor.agents.chat.agentic_pipeline import DEFAULT_MAX_ROUNDS
    from deeptutor.agents.test.pipeline import (
        TEST_MODE_MIN_LOOP_ROUNDS,
        TestJourneyPipeline,
    )

    context = _context()
    pipeline = TestJourneyPipeline(language="zh")
    pipeline._apply_round_floor(context)

    assert pipeline.effective_max_rounds(context) >= TEST_MODE_MIN_LOOP_ROUNDS
    assert TEST_MODE_MIN_LOOP_ROUNDS >= 24
    # 实测一轮 8-10 次工具调用；预算不高于基线就等于没改。
    assert pipeline.effective_max_rounds(context) > DEFAULT_MAX_ROUNDS * 2


def test_the_floor_never_lowers_another_capabilitys_minimum() -> None:
    """走的是宿主的通用缝，不是自己那一份。

    subagent capability 用同一个键声明"我至少要这么多轮"。直接赋值会把它的
    预算悄悄砍掉，而砍掉的表现是 subagent 咨询做到一半被强制收尾——
    与本轮要修的缺陷同一个形状。
    """
    from deeptutor.agents.test.pipeline import MIN_LOOP_ROUNDS_KEY, TestJourneyPipeline

    context = _context()
    context.metadata[MIN_LOOP_ROUNDS_KEY] = 99
    pipeline = TestJourneyPipeline(language="zh")
    pipeline._apply_round_floor(context)

    assert context.metadata[MIN_LOOP_ROUNDS_KEY] == 99
    assert pipeline.effective_max_rounds(context) == 99


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("40", 40), ("", 24), ("   ", 24), ("abc", 24), ("0", 24), ("-3", 24)],
)
def test_the_floor_is_runtime_configurable_and_ignores_junk(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
) -> None:
    """环境变量能调；非法值回落默认而不是让一轮只剩 0 轮。"""
    from deeptutor.agents.test.pipeline import (
        TEST_MODE_MAX_ROUNDS_ENV,
        journey_round_floor,
    )

    monkeypatch.setenv(TEST_MODE_MAX_ROUNDS_ENV, raw)
    assert journey_round_floor() == expected


def test_unset_env_falls_back_to_the_default_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    from deeptutor.agents.test.pipeline import (
        TEST_MODE_MAX_ROUNDS_ENV,
        TEST_MODE_MIN_LOOP_ROUNDS,
        journey_round_floor,
    )

    monkeypatch.delenv(TEST_MODE_MAX_ROUNDS_ENV, raising=False)
    assert journey_round_floor() == TEST_MODE_MIN_LOOP_ROUNDS


def test_plain_chat_keeps_the_host_budget() -> None:
    """加法只加在本模式身上。普通聊天轮的预算一个字节都不变。"""
    from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
    from deeptutor.agents.test.pipeline import MIN_LOOP_ROUNDS_KEY

    context = UnifiedContext(session_id="s-chat", user_message="hi")
    pipeline = AgenticChatPipeline(language="zh")

    assert MIN_LOOP_ROUNDS_KEY not in context.metadata
    assert pipeline.effective_max_rounds(context) == pipeline.max_rounds


def test_the_floor_is_applied_on_the_real_pre_loop_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """常量对了还得**真的在这一轮被写进去**。

    `effective_max_rounds` 只在循环开始时读一次 metadata；没人调
    `_apply_round_floor` 的话，上面那些断言全绿而线上照旧 8 轮。
    这条走真实的 pre-loop 钩子（`agent_loop.py` 里循环前唯一那个 await 点）。
    TAPD 简报要连 MCP，与本条无关，桩掉。
    """
    from deeptutor.agents.test.pipeline import TEST_MODE_MIN_LOOP_ROUNDS, TestJourneyPipeline

    async def _no_tapd(self, context):  # noqa: ANN001, ANN202, ARG001
        return ""

    monkeypatch.setattr(TestJourneyPipeline, "_tapd_context_briefing", _no_tapd)

    context = _context()
    pipeline = TestJourneyPipeline(language="zh")
    asyncio.run(pipeline._capability_pre_loop_briefings(context, None))  # type: ignore[arg-type]

    assert pipeline.effective_max_rounds(context) >= TEST_MODE_MIN_LOOP_ROUNDS


# --- 触顶收尾话术 -----------------------------------------------------------

#: 中英各自的锚。锚的是**禁令与形状**，不是整段文案：润色随便改，
#: 这几条消失就说明缺陷可以原样复发。
_CLOSING_ANCHORS = {
    "zh": ("可交付", "不要写进度小结", "点火键", "不要让用户回复"),
    "en": ("deliverable", "progress-report summary", "ignition key", 'reply "continue"'),
}


@pytest.mark.parametrize("language", ["zh", "en"])
def test_the_exhausted_closing_instruction_is_delivery_first(language: str) -> None:
    """触顶那一刻追加的 user 消息 = 模型最后读到的指令，所以纪律必须写在这里。

    宿主默认那句没禁汇报体、也没禁"回复继续"；本模式覆盖它。
    """
    from deeptutor.agents.test.pipeline import TestJourneyPipeline

    instruction = TestJourneyPipeline(language=language)._finish_exhausted_instruction()
    assert instruction.strip(), f"{language} 收尾指令是空的 —— 那会回落宿主默认"
    for anchor in _CLOSING_ANCHORS[language]:
        assert anchor in instruction, f"{language} 收尾指令缺锚：{anchor}"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_the_closing_instruction_actually_differs_from_the_host_default(
    language: str,
) -> None:
    """覆盖真的生效了。

    读不到 yaml 时实现会**回落宿主默认**（刻意如此，不塞兜底文案）——
    那条回落路径与"忘了写这段 yaml"在行为上一模一样，只有这条断言能分开它们。
    """
    from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
    from deeptutor.agents.test.pipeline import TestJourneyPipeline

    host = AgenticChatPipeline(language=language)._finish_exhausted_instruction()
    own = TestJourneyPipeline(language=language)._finish_exhausted_instruction()
    assert own != host


@pytest.mark.parametrize("language", ["zh", "en"])
def test_plain_chat_closing_instruction_is_untouched(language: str) -> None:
    """作用域边界：普通聊天轮的收尾指令不该带上本模式的纪律。"""
    from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline

    host = AgenticChatPipeline(language=language)._finish_exhausted_instruction()
    for anchor in _CLOSING_ANCHORS[language]:
        assert anchor not in host


# --- 回复形态与提问收敛（B2/B3 的 system prompt 半边）------------------------

_ANSWER_ANCHORS = {
    "zh": (
        "一轮里只弹一张卡",  # B3：连环 ask_user 收敛
        "同一次 `ask_user`",  # 合并成一张卡的机制名
        "答案先行",  # B2：交付优先
        "不许写进度体",  # B2：禁三段汇报
        "内部术语禁出",  # B2：禁行话
        "不许把用户当点火键",  # B2：禁"回复继续"
    ),
    "en": (
        "One card per turn",
        "a single `ask_user`",
        "answer first",
        "No progress-report shape",
        "No internal vocabulary",
        "ignition key",
    ),
}

#: 第二轮 UAT（2026-08-29 VERIFY-REPORT）新增的四组锚。
#:
#: 每组对着一条**实测发生过**的行为，不是对着一条设计意图：
#: 栏位不稳（V1 只有三列 / V2·V3 四列）、中文提问英文作答、行话翻成英文照样泄漏、
#: 原始错误码与写给模型的指令直出界面。
_UAT2_ANCHORS = {
    "zh": (
        # 清单四列钉死（V1 缺"处理人"列）
        "标题 / 状态 / 处理人 / 链接 四列缺一不可",
        "不是把整列删掉",
        # 回复语言跟随用户（中文提问英文作答）
        "用用户最后一条消息的语言作答",
        # 中英双语禁词表（英文行话是等价泄漏）
        "禁词表（两种语言各一份）",
        "baseline-confirmed",
        "status taxonomy",
        "字段基线",
        # 原始错误码禁直出
        "原始错误串禁止出现在回复正文",
        "`machine` 字段是**写给你的操作指令**",
    ),
    "en": (
        "title / status / assignee / link are all four",
        "a missing column reads as",
        "Answer in the language of the user's last message",
        "Banned vocabulary, in both languages",
        "baseline-confirmed",
        "status taxonomy",
        "字段基线",
        "Raw error strings never appear in a reply",
        "`machine` field is **an instruction to you**",
    ),
}

#: 环境挂起那条诚实出口的锚（V5：闸把诚实变成死路，模型编了个假地址）。
_ENV_PENDING_ANCHORS = {
    "zh": (
        "出用例**不需要环境**",
        "环境还没好，先出用例（推荐）",
        'environment_status="pending"',
        "绝不编造地址",
    ),
    "en": (
        "does not need an environment",
        "draft the cases first",
        'environment_status="pending"',
        "Never invent an address",
    ),
}


@pytest.mark.parametrize("language", ["zh", "en"])
def test_the_answer_discipline_reaches_the_rendered_system_prompt(language: str) -> None:
    """断**渲染出来的 system prompt**，不断 yaml 里写过这几个字。

    这一段写在 yaml 里但没挂进 `JOURNEY_PROMPT_KEYS` 的话，模型一个字也收不到，
    而 yaml 层的断言照样绿——BB-508 那个洞的原样重演。
    """
    from deeptutor.agents.test.pipeline import TestJourneyPipeline

    context = UnifiedContext(session_id="s-answer", user_message="帮我拉一下TAPD的需求")
    rendered = TestJourneyPipeline(language=language)._build_system_prompt([], context)
    for anchor in _ANSWER_ANCHORS[language]:
        assert anchor in rendered, f"{language} 回复纪律缺锚：{anchor}"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_plain_chat_does_not_carry_the_answer_discipline(language: str) -> None:
    """同样只作用到本模式。"""
    from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline

    context = UnifiedContext(session_id="s-answer", user_message="hi")
    rendered = AgenticChatPipeline(language=language)._build_system_prompt([], context)
    for anchor in _ANSWER_ANCHORS[language]:
        assert anchor not in rendered


@pytest.mark.parametrize("language", ["zh", "en"])
def test_second_uat_round_fixes_reach_the_rendered_system_prompt(language: str) -> None:
    """V1 FAIL 与 V5 PARTIAL 的四条提示词半边，断渲染产物。

    锚全部取**行为禁令**而不是段落标题：文案怎么润色它们都在，
    真被删掉说明对应缺陷可以原样复发。
    """
    from deeptutor.agents.test.pipeline import TestJourneyPipeline

    context = UnifiedContext(session_id="s-uat2", user_message="帮我拉一下TAPD的需求")
    rendered = TestJourneyPipeline(language=language)._build_system_prompt([], context)
    for anchor in _UAT2_ANCHORS[language]:
        assert anchor in rendered, f"{language} 缺锚：{anchor}"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_the_environment_pending_road_is_prompted(language: str) -> None:
    """环境未就绪时有一条诚实的路，且编地址被明令禁止。

    这一条与 gateway 侧的 `environment_status="pending"` 是同一道闸的两半：
    服务端开了路，提示词不说，模型仍然只会在"停摆"和"编地址"之间二选一。
    """
    from deeptutor.agents.test.pipeline import TestJourneyPipeline

    context = UnifiedContext(session_id="s-env", user_message="我要正式测这个需求")
    rendered = TestJourneyPipeline(language=language)._build_system_prompt([], context)
    for anchor in _ENV_PENDING_ANCHORS[language]:
        assert anchor in rendered, f"{language} 缺环境挂起锚：{anchor}"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_plain_chat_carries_none_of_the_second_round_rules(language: str) -> None:
    """作用域边界照旧：这些纪律只作用到「测试」模式。"""
    from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline

    context = UnifiedContext(session_id="s-uat2", user_message="hi")
    rendered = AgenticChatPipeline(language=language)._build_system_prompt([], context)
    for anchor in _UAT2_ANCHORS[language] + _ENV_PENDING_ANCHORS[language]:
        assert anchor not in rendered
