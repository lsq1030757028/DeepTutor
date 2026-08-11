"""[fork] 「测试」capability 的**运行时层**断言（回归闸第 4 层，容器内跑）。

## 为什么这半在这里，另一半在 extensions

拆分判据是「这条断言需不需要 DT 运行时」：本文件的每条都要真 import
`deeptutor.agents.test.capability`，它经 `AgenticChatPipeline →
deeptutor.config.settings` 拉到 `pydantic_settings` —— 宿主 python 没装，
所以这半在扩展层（跑在宿主）跑不起来，必须留在容器里。

**落 `tests/core/` 而不是 `tests/` 根，是为了让闸真跑到它**：回归闸第 4 层的
命令是 `pytest tests/api tests/core`，不含 `tests/` 根。放在根下的测试一层都
跑不到 —— **一个没有闸会跑的测试等于不存在**，与「常驻红」是一枚硬币的两面。
放这儿还有个附带好处：它就躺在 `test_capabilities_runtime.py` 旁边，
而后者那条全等断言正是本 fork 具名扣除掉的那条 —— 让出去的和补回来的相邻可见。

结构层（常量表在册 / MODULES 在册 / i18n 成对 / 提示词文件可解析）见
`extensions/test-partner/tests/test_fork_capabilities.py`，闸第 1 层每次必跑。
"""

from __future__ import annotations

import pytest

from deeptutor.runtime.bootstrap.builtin_capabilities import BUILTIN_CAPABILITY_CLASSES
from deeptutor.services.prompt.manager import PromptManager


def test_capability_manifest_name_matches_registry_key() -> None:
    from deeptutor.agents.test.capability import TestCapability

    assert TestCapability.manifest.name == "test"
    assert BUILTIN_CAPABILITY_CLASSES["test"].endswith(":TestCapability")


def test_prompt_constants_match_the_on_disk_module_layout() -> None:
    """结构层写的是字面量 "test"/"test_journey"，这里对拍它们没漂。"""
    from deeptutor.agents.test.capability import PROMPT_AGENT, PROMPT_MODULE

    assert PROMPT_MODULE == "test"
    assert PROMPT_AGENT == "test_journey"
    assert PROMPT_MODULE in PromptManager.MODULES


def test_journey_prompt_loads_in_both_languages() -> None:
    """注册了还要真能**经 PromptManager 读出来**。

    结构层只验文件在场且可解析；这里验的是那条加载链（模块名 → 目录解析 →
    语言回落）真的通。两条都要：文件在而链断，与链通而文件缺，是两种病。
    """
    from deeptutor.agents.test.capability import PROMPT_AGENT, PROMPT_MODULE

    for language in ("zh", "en"):
        prompts = PromptManager().load_prompts(
            PROMPT_MODULE, PROMPT_AGENT, language=language)
        assert str(prompts.get("journey_discipline") or "").strip(), language


#: 纪律段的锚串。选它不是随手挑的：`journey_issue_gate_token` 是**门票工具的
#: API 身份**，不是一句可以润色的话——文案怎么改它都在，真消失了就说明门票语义
#: 变了，那时候这条测试**就该红**。反过来也不选整段全等：那会让每次改提示词都红，
#: 红着红着没人看。中英两版都含这个串（`prompts/{zh,en}/test_journey.yaml`）。
JOURNEY_PROMPT_ANCHOR = "journey_issue_gate_token"


def _rendered_system_prompt(pipeline_cls, language: str) -> str:
    from deeptutor.core.context import UnifiedContext

    context = UnifiedContext(session_id="s-journey", user_message="帮我测 #1140342")
    return pipeline_cls(language=language)._build_system_prompt([], context)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_journey_discipline_reaches_the_rendered_system_prompt(language: str) -> None:
    """BB-508 的回归判据：断**模型真收到了**纪律段，不断源码里写过某个类名。

    上一版这里断的是 `inspect.getsource(TestCapability.run)` 里含字符串
    "AgenticChatPipeline"。那条绿着，而运行时行为是纯 chat —— 因为纪律段被写进
    `context.metadata["capability_system_block"]`，那个键**全仓零读取点**。
    源码文本与运行时行为之间没有蕴含关系，所以判据必须落在渲染产物上。
    """
    from deeptutor.agents.test.pipeline import JOURNEY_BLOCK_NAME, TestJourneyPipeline

    prompt = _rendered_system_prompt(TestJourneyPipeline, language)
    assert f"## {JOURNEY_BLOCK_NAME}" in prompt, language
    assert JOURNEY_PROMPT_ANCHOR in prompt, language


@pytest.mark.parametrize("language", ["zh", "en"])
def test_plain_chat_turn_does_not_carry_the_journey_block(language: str) -> None:
    """作用域边界：这段纪律只作用到「测试」模式，普通 chat 轮一个字都不该多。

    单独立一条，是因为本线已经连着四次栽在「机制建成了但没人核它作用到了谁」上。
    只验「测试模式里有」不够——那与「所有轮次都有」也一致。
    """
    from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
    from deeptutor.agents.test.pipeline import JOURNEY_BLOCK_NAME

    prompt = _rendered_system_prompt(AgenticChatPipeline, language)
    assert f"## {JOURNEY_BLOCK_NAME}" not in prompt, language
    assert JOURNEY_PROMPT_ANCHOR not in prompt, language


def test_journey_pipeline_adds_one_block_and_changes_nothing_else() -> None:
    """人闸链路的命门：`wait_for_user_reply` 等待器**只在主聊 turn 上注入**。

    自建流水线 = 自己造一条没有 waiter 的通道 = 复刻 BB-502 的静默降级
    （问题被拍平成本轮最终回复、本轮结束、用户无感知）。

    所以「复用主聊」这件事要**按产物**断而不是按源码断：把测试轮的 system prompt
    里那一节摘掉，必须与 chat 轮的 system prompt 逐字节相同。多改了别的、少继承了
    什么，都会在这里露出来。
    """
    from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
    from deeptutor.agents.test.pipeline import JOURNEY_BLOCK_NAME, TestJourneyPipeline

    sep = "\n\n---\n\n"
    chat_sections = _rendered_system_prompt(AgenticChatPipeline, "zh").split(sep)
    test_sections = _rendered_system_prompt(TestJourneyPipeline, "zh").split(sep)

    journey = [s for s in test_sections if s.startswith(f"## {JOURNEY_BLOCK_NAME}\n")]
    assert len(journey) == 1, "旅程块应恰好出现一次"
    assert [s for s in test_sections if s not in journey] == chat_sections


def test_capability_run_uses_the_journey_pipeline() -> None:
    """`TestCapability.run` 真的走子类——不是又悄悄退回裸 `AgenticChatPipeline`。

    上面那条比对能发现「块没进 prompt」，但发现不了「pipeline 建对了却没被用」。
    这条按**实例类型**断，仍不读源码文本。
    """
    import asyncio

    from deeptutor.agents.test.capability import TestCapability
    from deeptutor.agents.test.pipeline import TestJourneyPipeline
    from deeptutor.core.context import UnifiedContext

    built: list[object] = []

    class _Probe(TestJourneyPipeline):
        async def run(self, context, stream):  # noqa: ANN001, ARG002
            built.append(self)

    import deeptutor.agents.test.capability as cap_mod

    original = cap_mod.TestJourneyPipeline
    cap_mod.TestJourneyPipeline = _Probe  # type: ignore[misc]
    try:
        asyncio.run(
            TestCapability().run(
                UnifiedContext(session_id="s1", user_message="hi"), None  # type: ignore[arg-type]
            )
        )
    finally:
        cap_mod.TestJourneyPipeline = original  # type: ignore[misc]

    assert len(built) == 1
    assert isinstance(built[0], TestJourneyPipeline)


def test_missing_prompt_module_degrades_to_empty_not_to_a_fallback_blurb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提示词读不到时**返回空串**，不塞兜底文案。

    塞兜底会让「模块没注册」这个配置错误看起来像正常工作，正是 MODULES 那条
    漏改不报错的放大器。空串至少让人发现模型没按纪律走。
    """
    from deeptutor.agents.test import capability as cap

    def _boom(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("prompt module not registered")

    monkeypatch.setattr(PromptManager, "load_prompts", _boom)
    assert cap.journey_system_block("zh") == ""
