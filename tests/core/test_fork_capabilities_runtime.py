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

## 本文件是 fork 断言的**唯一落点**（python 侧）

上游 `tests/` 根那份 `test_fork_capabilities.py` 已删（它躺在回归闸够不到的
路径上，当时并没有在保护任何东西——**假保护比没保护更危险，因为它让人以为有**）；
扩展层也不留（扩展 venv 刻意不依赖 DT 运行时，放进去等于把扩展测试环境绑上
DT 依赖集）。所以五个登记触点（#7 常量表 / #8 MODULES / #9 picker /
#10 富卡 / #11 i18n）里凡能在 python 侧断的，都在这里断，没有第二处。

#9 picker 与 #10 富卡在前端，判据也只能在前端——**本层跑在 DT 生产镜像里，
镜像不带 node**，把前端断言写进这里只会得到一条永远 skip 的测试，
而那正是本文件开头骂的那种假保护。两条各自的落点（都在回归闸第 3 层
`npm run test:node` 里真跑）：

- #9 picker：`web/tests/capability-picker.test.ts` —— 2026-08-12 建成，此前为零。
  它 import `page.tsx` 真导出的 `CAPABILITIES`，喂给真的 `ChatComposer`
  渲染展开态 picker，断「测试」那一行在一级菜单里（不在 More 二级浮层）。
  同文件带一条反例：把该项摘掉后判据必须红。
  **残余缺口（如实记）**：page 自己有没有把这张表传给 composer 不在断言范围内，
  那要渲染整个 page（依赖 router/context/网络），只能走浏览器级 e2e。
- #10 富卡：`web/tests/test-journey-cards.test.ts`。
- #11 的前端一半（薄壳与富卡的 UI 文案是否都走了 `t()`）：
  `web/tests/test-journey-i18n.test.ts`；后端一半在本文件下面那条。
"""

from __future__ import annotations

import pytest

from deeptutor.runtime.bootstrap.builtin_capabilities import BUILTIN_CAPABILITY_CLASSES
from deeptutor.services.prompt.manager import PromptManager


def test_capability_manifest_name_matches_registry_key() -> None:
    """触点 #7。不在册则 picker 选中后服务端无实现可派。"""
    from deeptutor.agents.test.capability import TestCapability

    assert TestCapability.manifest.name == "test"
    assert (
        BUILTIN_CAPABILITY_CLASSES["test"]
        == "deeptutor.agents.test.capability:TestCapability"
    )


def test_capability_description_is_reachable_in_both_languages() -> None:
    """触点 #11。picker 那一行显示的就是这两句，缺了那一行没文案。

    **不断私有表、断公开取数口**：`capability_description_i18n` 对查不到的
    名字返回 `{"en": fallback, "zh": fallback}` —— 又一个静默回落。只断
    `_CAPABILITY_DESCRIPTIONS["test"]` 有值，证明不了 picker 真取得到；
    用一个不可能与真文案相同的哨兵当 fallback，取回来还等于哨兵就是没接上。
    """
    from deeptutor.i18n.metadata_i18n import (
        _CAPABILITY_DESCRIPTIONS,
        capability_description_i18n,
    )

    sentinel = "<<unreached>>"
    # 探测器的探测器：先证明这套哨兵法真能认出「没接上」，
    # 否则下面那圈断言可能只是恰好通过。
    assert capability_description_i18n("no-such-capability", sentinel) == {
        "en": sentinel,
        "zh": sentinel,
    }
    entry = capability_description_i18n("test", sentinel)
    for language in ("en", "zh"):
        value = str(entry.get(language) or "").strip()
        assert value, f"{language} 文案为空"
        assert value != sentinel, f"{language} 走到了 fallback —— 取数口没接上这一项"
    # 中英不得是同一串：两边填一样等于有一个语种其实没写，而非空检查看不出来。
    assert entry["en"] != entry["zh"]
    # 取数口给的就是表里那份，不是复制品漂移后的另一份。
    assert entry == dict(_CAPABILITY_DESCRIPTIONS["test"])


def test_capability_description_zh_slot_is_actually_written_in_chinese() -> None:
    """触点 #11 的另一半：**中文槽里装的真是中文**。

    上一条已经守住了「有值 / 没走 fallback / 中英不同」。它漏掉的那种形态是
    **zh 槽里填的是英文**——两串不相等，非空检查也过，但中文用户在 picker 上
    读到的是一句英文。这在补文案时很容易发生（先把 en 复制过去打算回头再翻）。

    判据取字符类而不是具体文案：文案怎么改都在，语种真错了才红。
    en 槽反向也断一次——不然这条只能发现单向的漏法。
    """
    from deeptutor.i18n.metadata_i18n import _CAPABILITY_DESCRIPTIONS

    def has_cjk(text: str) -> bool:
        return any("一" <= ch <= "鿿" for ch in text)

    entry = _CAPABILITY_DESCRIPTIONS["test"]
    assert has_cjk(entry["zh"]), f"zh 槽里没有中文：{entry['zh']!r}"
    assert not has_cjk(entry["en"]), f"en 槽里混进了中文：{entry['en']!r}"
    # 不是把键名或占位串当文案交差。
    for language, value in entry.items():
        assert value.strip() not in {"test", "TODO", "-", ""}, language


def test_prompt_constants_match_the_on_disk_module_layout() -> None:
    """结构层写的是字面量 "test"/"test_journey"，这里对拍它们没漂。"""
    from deeptutor.agents.test.capability import PROMPT_AGENT, PROMPT_MODULE

    assert PROMPT_MODULE == "test"
    assert PROMPT_AGENT == "test_journey"
    assert PROMPT_MODULE in PromptManager.MODULES


@pytest.mark.parametrize("language", ["zh", "en"])
def test_every_declared_prompt_section_reaches_the_rendered_prompt(language: str) -> None:
    """写了一段提示词但忘了挂进拼接列表 —— 与 BB-508 同一个形状（内容有、通道没接上）。

    **遍历的是 yaml 里实际有哪些段，不是代码里声明了哪些段**：遍历
    `JOURNEY_PROMPT_KEYS` 只能发现"声明了但没渲染"，发现不了"写了但没声明"，
    而后者才是这个坑的常见形态。真相在 yaml，判据就得从 yaml 出发。
    """
    from deeptutor.agents.test.pipeline import (
        JOURNEY_PROMPT_KEYS,
        PROMPT_AGENT,
        PROMPT_MODULE,
        TestJourneyPipeline,
    )

    prompts = PromptManager().load_prompts(PROMPT_MODULE, PROMPT_AGENT, language=language)
    rendered = _rendered_system_prompt(TestJourneyPipeline, language)
    # `labels` 不是提示词正文，是前端阶段名，本来就不该进 system prompt。
    sections = {k: v for k, v in prompts.items() if k != "labels" and isinstance(v, str)}
    assert sections, f"{language} 一段提示词都没读到"
    for key, raw in sections.items():
        section = raw.strip()
        assert section, f"{language}/{key} 在 yaml 里是空的"
        assert key in JOURNEY_PROMPT_KEYS, (
            f"{language}/{key} 写在 yaml 里但没进 JOURNEY_PROMPT_KEYS —— 模型收不到它"
        )
        # 取首行当锚：整段全等会因为 yaml 折行与渲染缩进的差异误红。
        head = section.splitlines()[0].strip()
        assert head in rendered, f"{language}/{key} 没进 system prompt"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_all_four_human_gates_are_prompted(language: str) -> None:
    """四道人闸（定档 / 澄清 / 采纳流转 / 写确认）都得在提示词里。

    锚全部选**接口身份**不选人话：四道闸各自对应的工具参数与事件类型。
    文案怎么润色它们都在；真消失了说明闸接的东西变了，那时候就该红。
    """
    from deeptutor.agents.test.pipeline import PROMPT_AGENT, PROMPT_MODULE

    prompts = PromptManager().load_prompts(PROMPT_MODULE, PROMPT_AGENT, language=language)
    gates = str(prompts.get("human_gates") or "")
    for anchor in (
        "ask_user",              # 四道闸统一的提问机制
        "tier_confirmed_via",    # 闸 1 定档：ingest 的记录参数
        "journey_ingest",        # 闸 1 的时序：定档必须在接入之前
        "clarifications",        # 闸 2 澄清：clarify 的记录参数
        "confirmed_by",          # 闸 3 采纳：adopt 的记录参数
        "write_confirm",         # 闸 4 写确认：events.jsonl 里的事件类型
    ):
        assert anchor in gates, f"{language} 人闸段缺锚 {anchor}"


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
