"""[fork] 「测试」模式的**回复语种**判据（BB-698 reopen，2026-09-05）。

## 起因

UAT 判据 4：全程中文提问，三条回复正文全英（S1 汉字 636/拉丁 1138、S2 135/827、
S3 采纳回复 0/1330），清单表头 Title/Status/Assignee(s)/Link，卡片外壳
Pick an option / Next question。0829 的修复 ff2802df 把「回复语言跟着用户走」
写进了提示词，但提示词目录本身按 `context.language`（= 界面语言，默认 en）选：
模型收到的是整段英文纪律 + 英文宿主简报 + 英文工具提示，那一句请求淹没其中。

## 判据

语种是**机制**不是恳求：在建 pipeline 之前按用户消息判定，下游全部跟随。
这里断的是运行时真实发生的三件事，不断源码里写了什么：

1. `TestCapability.run` 真的把 `context.language` 改成了用户消息的语种，且
   pipeline 是按这个语种建的（monkeypatch `TestJourneyPipeline.run` 抓真值）；
2. 渲染出来的 system prompt 的汉字占比过 `ZH_RATIO_THRESHOLD`——同一个门槛
   既用于判定输入，也用于断言输出，没有第二套口径；
3. 事件流里带 `turn_language`，前端卡片外壳据此选语种。

模型最终的回复语种离线断不了（要真调模型），这里断到"模型收到的全部材料都是
中文"为止；UAT 走查再看回复本身。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from deeptutor.agents.test.language import (
    ZH_RATIO_THRESHOLD,
    cjk_ratio,
    detect_message_language,
    resolve_turn_language,
)
from deeptutor.core.context import UnifiedContext

#: 2026-09-05 UAT 里用户真实敲过的三句话 + 采纳卡答复。
UAT_CHINESE_INPUTS = (
    "帮我拉一下TAPD的需求",
    "已提测且处理人是我的需求有哪些",
    "正式测一下这条需求：https://www.tapd.cn/44773172/prong/stories/view/1144773172001034975",
    "先不采纳",
)


# --- 判定 ---------------------------------------------------------------------


@pytest.mark.parametrize("text", UAT_CHINESE_INPUTS)
def test_every_uat_input_is_detected_as_chinese(text: str) -> None:
    assert detect_message_language(text) == "zh", text


@pytest.mark.parametrize(
    "text",
    [
        "pull the TAPD stories assigned to me",
        "test this requirement: https://www.tapd.cn/44773172/prong/stories/view/1144773172001034975",
        "Adopt d1 and d2 only",
    ],
)
def test_english_prose_is_detected_as_english(text: str) -> None:
    assert detect_message_language(text) == "en", text


@pytest.mark.parametrize("text", ["", "   ", "https://www.tapd.cn/44773172/prong/stories/view/1", "#1140342", "d1, d2"])
def test_inputs_without_prose_are_undecidable(text: str) -> None:
    """链接、编号、只点了选项：这些不是"英文提问"，判不出就交给回落。"""
    assert detect_message_language(text) is None, repr(text)


def test_undecidable_input_falls_back_to_the_last_decidable_user_turn() -> None:
    history = [
        {"role": "user", "content": "帮我拉一下TAPD的需求"},
        {"role": "assistant", "content": "Here is the list"},
    ]
    assert resolve_turn_language("d1, d2", conversation_history=history, fallback="en") == "zh"
    assert resolve_turn_language("d1, d2", conversation_history=[], fallback="en") == "en"
    assert resolve_turn_language("d1, d2", conversation_history=[], fallback="zh-CN") == "zh"


def test_the_user_message_beats_the_ui_language_in_both_directions() -> None:
    assert resolve_turn_language("帮我拉一下TAPD的需求", fallback="en") == "zh"
    assert resolve_turn_language("pull the TAPD stories for me", fallback="zh") == "en"


# --- 运行时：capability 真的按用户语种建 pipeline --------------------------------


class _Stream:
    def __init__(self) -> None:
        self.progress_calls: list[dict[str, Any]] = []

    async def progress(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.progress_calls.append({"message": message, **kwargs})


def _run_capability(context: UnifiedContext, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from deeptutor.agents.test.capability import TestCapability
    from deeptutor.agents.test.pipeline import TestJourneyPipeline

    seen: dict[str, Any] = {}

    async def _capture(self, ctx, stream):  # noqa: ANN001, ANN202
        seen["pipeline_language"] = self.language
        seen["context_language"] = ctx.language

    monkeypatch.setattr(TestJourneyPipeline, "run", _capture)
    asyncio.run(TestCapability().run(context, _Stream()))  # type: ignore[arg-type]
    return seen


def test_chinese_question_under_english_ui_runs_the_chinese_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UAT 的形态：界面 en（默认），用户中文提问。改前这里两个值都是 "en"。"""
    context = UnifiedContext(
        session_id="s-lang",
        user_message="帮我拉一下TAPD的需求",
        active_capability="test",
        language="en",
    )
    seen = _run_capability(context, monkeypatch)
    assert seen == {"pipeline_language": "zh", "context_language": "zh"}


def test_english_question_under_chinese_ui_runs_the_english_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反向也成立：英文提问才英文，界面设成中文不能把英文用户拽成中文。"""
    context = UnifiedContext(
        session_id="s-lang",
        user_message="pull the TAPD stories assigned to me",
        active_capability="test",
        language="zh",
    )
    seen = _run_capability(context, monkeypatch)
    assert seen == {"pipeline_language": "en", "context_language": "en"}


# --- 模型收到的材料：整段按语种走，用汉字占比断 ----------------------------------


def _rendered_prompt(language: str) -> str:
    from deeptutor.agents.test.pipeline import TestJourneyPipeline

    context = UnifiedContext(session_id="s-lang", user_message="帮我拉一下TAPD的需求")
    return TestJourneyPipeline(language=language)._build_system_prompt([], context)


def test_the_resolved_language_selects_a_chinese_system_prompt() -> None:
    """断渲染产物的汉字占比，不断 yaml 目录名——目录在而链断，占比就会红。"""
    from deeptutor.agents.test.capability import TestCapability

    context = UnifiedContext(
        session_id="s-lang",
        user_message="帮我拉一下TAPD的需求",
        active_capability="test",
        language="en",
    )
    language = TestCapability.turn_language(context)
    rendered = _rendered_prompt(language)
    assert cjk_ratio(rendered) >= ZH_RATIO_THRESHOLD, (
        f"中文提问却渲染出汉字占比 {cjk_ratio(rendered):.2f} 的 system prompt"
    )
    # 对照：界面语言本身（en）渲染出来的那份过不了同一门槛——说明是判定在起作用。
    assert cjk_ratio(_rendered_prompt(context.language)) < ZH_RATIO_THRESHOLD


def test_the_host_briefing_follows_the_resolved_language() -> None:
    """宿主简报（TAPD 上下文那段）也按同一语种渲染，不是只有纪律段换了。"""
    from deeptutor.services.tapd_context import (
        TapdContextResolution,
        TapdProject,
        render_context_briefing,
    )

    resolution = TapdContextResolution(
        status="ready",
        selected_project=TapdProject("1", "AI手办"),
        project_source="explicit",
        tapd_identity="张三",
        role="QA",
    )
    zh = render_context_briefing(resolution, language=resolve_turn_language("帮我拉一下TAPD的需求"))
    en = render_context_briefing(resolution, language=resolve_turn_language("pull TAPD stories"))
    assert cjk_ratio(zh) >= ZH_RATIO_THRESHOLD
    assert cjk_ratio(en) < ZH_RATIO_THRESHOLD


# --- 事件流：外壳语种进事件 ------------------------------------------------------


def test_the_turn_language_is_announced_on_the_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    from deeptutor.agents.test.pipeline import TestJourneyPipeline

    async def _no_tapd(self, context):  # noqa: ANN001, ANN202, ARG001
        return ""

    monkeypatch.setattr(TestJourneyPipeline, "_tapd_context_briefing", _no_tapd)
    context = UnifiedContext(
        session_id="s-lang",
        user_message="帮我拉一下TAPD的需求",
        active_capability="test",
        language="zh",
    )
    stream = _Stream()
    asyncio.run(TestJourneyPipeline(language="zh")._capability_pre_loop_briefings(context, stream))  # type: ignore[arg-type]
    announced = [c for c in stream.progress_calls if (c.get("metadata") or {}).get("turn_language")]
    assert announced, "事件流里没有 turn_language，卡片外壳无从跟随"
    assert announced[0]["metadata"]["turn_language"] == "zh"
    assert context.metadata["turn_language"] == "zh"
