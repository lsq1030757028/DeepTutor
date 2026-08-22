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
    assert BUILTIN_CAPABILITY_CLASSES["test"] == "deeptutor.agents.test.capability:TestCapability"


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
def test_the_gate_topology_is_prompted(language: str) -> None:
    """闸拓扑（三轻确认 + 唯一硬闸采纳）都得在提示词里。

    **批次 4 有意更新**：原名 test_all_four_human_gates_are_prompted，断旧的
    「四道人闸」拓扑。ADR-2 之后写确认不再以独立卡存在（写授权由采纳派生，
    `via="adopt_card"`），`decided_by` 不再是聊天旅程的参数——锚随闸的真实
    拓扑走，不随历史命名走。更新的判据本身经 0036 号决定同步过 Queenie 线。

    锚仍全部选**接口身份**不选人话：文案怎么润色它们都在；
    真消失了说明闸接的东西变了，那时候就该红。
    """
    from deeptutor.agents.test.pipeline import PROMPT_AGENT, PROMPT_MODULE

    prompts = PromptManager().load_prompts(PROMPT_MODULE, PROMPT_AGENT, language=language)
    gates = str(prompts.get("human_gates") or "")
    for anchor in (
        "ask_user",  # 各闸统一的提问机制
        "tier_confirmed_via",  # 轻确认 定档：ingest 的记录参数
        "requirement_entity_confirmed_via",  # 写实体也必须由用户落定
        "journey_ingest",  # 实体卡两步走的载体
        "clarifications",  # 轻确认 澄清：clarify 的记录参数
        "prepare_adoption",  # 硬闸 采纳：两步走的 prepare 参数
        "disclosure",  # 硬闸 采纳：AC6 写用例披露
        "authorized_write_draft_ids",  # 硬闸 采纳：用户勾选的写授权集
        "confirmed_by",  # 硬闸 采纳：adopt 的记录参数
        "write_confirm",  # 派生写授权：events.jsonl 里的事件类型（复用）
        "source_case_digest",  # 决定收据绑定用例内容而非裸 id
    ):
        assert anchor in gates, f"{language} 闸拓扑段缺锚 {anchor}"


def test_journey_prompt_loads_in_both_languages() -> None:
    """注册了还要真能**经 PromptManager 读出来**。

    结构层只验文件在场且可解析；这里验的是那条加载链（模块名 → 目录解析 →
    语言回落）真的通。两条都要：文件在而链断，与链通而文件缺，是两种病。
    """
    from deeptutor.agents.test.capability import PROMPT_AGENT, PROMPT_MODULE

    for language in ("zh", "en"):
        prompts = PromptManager().load_prompts(PROMPT_MODULE, PROMPT_AGENT, language=language)
        assert str(prompts.get("journey_discipline") or "").strip(), language


#: 纪律段的锚串。可信身份由服务端注入，模型既不能领取门票，也不能自己声明 owner。
#: 这里锚定新的安全语义；如果它从 Test prompt 消失，普通模型参数就可能再次被当成
#: 权限依据。中英两版都保留同一个协议名，文案仍可独立润色。
JOURNEY_PROMPT_ANCHOR = "E_TRUST_CONTEXT_REQUIRED"


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
                UnifiedContext(session_id="s1", user_message="hi"),
                None,  # type: ignore[arg-type]
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


@pytest.mark.parametrize("language", ["zh", "en"])
def test_prompt_tier_change_promise_matches_the_tool(language: str) -> None:
    """提示词的改档承诺与能力状态一致（批次 3 有意翻面）。

    批次 1 曾在没有改档工具时承诺「随时可以改档」，被独立复核判为阻断（B2），
    当时本测试钉住「不许承诺」。批次 3 落地了 `journey_change_tier`
    （extensions 侧 `server/journey/retier.py`，工具面契约由 test-partner 的
    call-surface 测试守），承诺与能力同时翻面——**这是有意动作**：
    翻的不是断言方向，是「提示词只许说真话」这条判据下真话本身变了。

    仍然守住的：采纳后不可改档（那时 = 新批次）必须同时写明，
    不许只报能力不报边界。
    """
    from deeptutor.agents.test.pipeline import PROMPT_AGENT, PROMPT_MODULE

    prompts = PromptManager().load_prompts(PROMPT_MODULE, PROMPT_AGENT, language=language)
    body = "\n".join(str(v or "") for v in prompts.values() if isinstance(v, str))
    if language == "zh":
        assert "采纳之前档位可以改" in body, "zh 没有如实说明改档能力已存在"
        assert "journey_change_tier" in body
        assert "采纳之后改档 = 开新批次" in body, "zh 只报能力没报边界"
        assert "supersedes" in body, "zh 丢了换需求的替代关系纪律（AC17）"
        assert "没有改档工具" not in body, "zh 还留着能力不存在时的旧话术"
    else:
        assert "changed any time before adoption" in body
        assert "journey_change_tier" in body
        assert "a tier change means a new batch" in body
        assert "supersedes" in body
        assert "no change-tier tool" not in body


@pytest.mark.parametrize("language", ["zh", "en"])
def test_prompt_keeps_the_host_trusted_query_guardrails(language: str) -> None:
    """宿主可信查询那条通道的提示词护栏必须在（独立复核阻断项 B3）。

    服务端那一半（字段基线 → 语义确认 → 可信查询，逐层失败关闭）本次没动，
    但保护它的提示词那一半一度被整段删掉，同时新纪律又在鼓励模型自己查、
    自己交叉验证。**闸的两半只剩一半，等于没有闸**：模型会绕过宿主的结果
    自己拉原始列表，再靠状态名字猜「未开始测试」——而状态名到业务语义的对应
    是 per-project 的，猜错了它不会知道。
    """
    from deeptutor.agents.test.pipeline import PROMPT_AGENT, PROMPT_MODULE

    prompts = PromptManager().load_prompts(PROMPT_MODULE, PROMPT_AGENT, language=language)
    body = "\n".join(str(v or "") for v in prompts.values() if isinstance(v, str))
    anchors = (
        ("宿主已核验", "Trusted TAPD query result"),
        ("不要转述原始列表", "do not relay a raw list"),
        ("不要靠状态名字去猜", "never infer"),
        ("tapd_story_selection", "tapd_story_selection"),
        ("不在普通答复里复述", "never repeat them in an ordinary reply"),
    )
    for zh_anchor, en_anchor in anchors:
        anchor = zh_anchor if language == "zh" else en_anchor
        assert anchor in body, f"{language} 丢了宿主可信查询护栏：{anchor}"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_prompt_carries_the_clarify_convergence_discipline(language: str) -> None:
    """澄清收敛纪律的提示词锚（批次 2 / AC10-AC14 提示词半边）。

    判断在模型 ⇒ 收敛纪律必须真的进 system prompt。锚各钉一句不同的话：
    攒批一轮（AC14）、查疑点账本（AC11）、skip 语义（AC13）、
    clarifications 结构（AC10 的落账形状）、非法答案不原样重问（AC12）。
    """
    from deeptutor.agents.test.pipeline import PROMPT_AGENT, PROMPT_MODULE

    prompts = PromptManager().load_prompts(PROMPT_MODULE, PROMPT_AGENT, language=language)
    body = "\n".join(str(v or "") for v in prompts.values() if isinstance(v, str))
    anchors = (
        ("整个澄清段用户最多作答一轮", "answering round"),
        ("question_ledger", "question_ledger"),
        ("skipped=true", "skipped=true"),
        ("question_id", "question_id"),
        ("不要把同一个问题原样再问一遍", "Do not re-ask the same question verbatim"),
    )
    for zh_anchor, en_anchor in anchors:
        anchor = zh_anchor if language == "zh" else en_anchor
        assert anchor in body, f"{language} 丢了澄清收敛锚：{anchor}"


# --- D1：纪律段点名的工具必须在窗口里，不在两步舞后面 -------------------------


def test_the_capability_declares_its_primary_tools_as_window_tools() -> None:
    """本模式的主用工具走「直接进窗口」，不是「先 load_tools」。

    这条盯的是一个具体的错配，不是风格：纪律段按 `journey_ingest` 这种**裸名**
    指挥模型，而唯一能派发的名字是 `mcp_test-partner_journey_ingest`，
    `DeferredToolLoader.load()` 只认后者。裸名去加载必然 unknown，
    于是「工具在手」这件事对模型不可发现。声明为主用集之后，schema 直接
    进窗口，模型看到的就是能调的那个名字。
    """
    from deeptutor.agents.test.pipeline import (
        JOURNEY_PRELOAD_PREFIXES,
        TAPD_PRELOAD_READ_TOOLS,
        TestJourneyPipeline,
    )
    from deeptutor.core.context import UnifiedContext

    pipeline = TestJourneyPipeline(language="zh")
    # 这两个属性由 run() 在轮内建立；这里只取工具面这一段，直接给中性值。
    pipeline._pageindex_docs = {}
    scope = pipeline._tool_scope(
        UnifiedContext(session_id="s", user_message="hi", active_capability="test")
    )

    assert scope.active_capability == "test"
    assert scope.preload_original_prefixes == JOURNEY_PRELOAD_PREFIXES
    assert scope.preload_original_names == TAPD_PRELOAD_READ_TOOLS
    # 前缀而不是名单：扩展侧加一个 journey_* 工具不该在这边变成够不着。
    assert "journey_" in JOURNEY_PRELOAD_PREFIXES


def test_plain_chat_declares_no_primary_tools() -> None:
    """加法只加在本模式身上，普通聊天的工具面一个字节都不变。"""
    from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
    from deeptutor.core.context import UnifiedContext

    pipeline = AgenticChatPipeline(language="zh")
    pipeline._pageindex_docs = {}
    scope = pipeline._tool_scope(UnifiedContext(session_id="s", user_message="hi"))
    assert scope.preload_original_prefixes == frozenset()
    assert scope.preload_original_names == frozenset()


def test_the_tapd_primary_set_is_read_only() -> None:
    """主用集是「先去查」的手，不是绕过写确认闸的门。

    写类工具一旦进了免加载窗口，模型就能在没过任何人闸的情况下直接改 TAPD。
    这条按名字机械挡住，而不是靠挑名单的人当时想着这件事。

    2026-08-22：原来还断言 `all(name.startswith("get_"))`。那是**第三方服务的
    命名习惯**，被当成了普遍规律写进断言；换到 tapd-capability 之后全都叫
    `tapd_*`，这条断言随即失效。留个记号：给某一家供应商的命名约定写机械断言，
    等于把供应商钉进了自己的测试。
    """
    from deeptutor.agents.test.pipeline import TAPD_PRELOAD_READ_TOOLS

    write_prefixes = ("create_", "update_", "delete_", "add_", "send_", "remove_")
    offenders = [
        name for name in TAPD_PRELOAD_READ_TOOLS if name.startswith(write_prefixes)
    ]
    assert offenders == []
    assert all(name.startswith("tapd_") for name in TAPD_PRELOAD_READ_TOOLS)
    # tapd_semantic_confirm 带 workspace-write，但写的是本地确认记录、不写 TAPD
    # （服务端返回 tapd_write=false），它是状态语义的人核环节，必须能被模型走完。
    assert "tapd_context_save" not in TAPD_PRELOAD_READ_TOOLS


def _declared_service_tools() -> set[str]:
    import json
    from pathlib import Path

    manifest = Path(__file__).with_name("tapd_capability_tool_surface.json")
    return set(json.loads(manifest.read_text(encoding="utf-8"))["tools"])


def test_every_preloaded_name_exists_on_the_tapd_service() -> None:
    """预载清单里的每个名字，服务端真的有。

    这条是 2026-08-22 那次线上缺陷的机械闸。当时把 TAPD 服务从第三方
    mcp-server-tapd 换成了自有的 tapd-capability，但宿主这份常量还写着退役的
    `get_stories_or_tasks` / `get_bug` / `get_workspace_info`……与服务端实际
    暴露的 `tapd_*` **零重合**：19 个工具连着，一个都没进模型窗口，
    轨迹是 `窗口=34 | tapd类=0`，模型于是照实回答"没连通"。

    **离线测试当时全绿**，因为常量和真实工具面之间没有任何东西在对拍。
    这就是那个对拍：清单来自对 3796 真实 list_tools 的抓取（见 json 里的出处），
    不是手写的期望值。
    """
    from deeptutor.agents.test.pipeline import TAPD_PRELOAD_READ_TOOLS

    declared = _declared_service_tools()
    assert declared, "服务端工具清单快照不该为空"
    missing = sorted(TAPD_PRELOAD_READ_TOOLS - declared)
    assert missing == [], (
        f"预载了服务端没有的工具：{missing}。"
        "要么服务端改了名而这份常量没跟着改，要么快照过期——"
        "重新对 3796 抓一次 list_tools 再对。"
    )


def test_the_preloaded_set_is_not_empty_and_actually_overlaps_the_service() -> None:
    """空集或零重合都不许当"通过"。

    上一条只断言"没有多余的名字"——一个空的预载清单能轻松满足它，
    而空清单正是那次线上故障的**表现形态**（有效重合为 0）。
    所以重合必须被正面断言，不能靠"没有反例"推出来。
    """
    from deeptutor.agents.test.pipeline import TAPD_PRELOAD_READ_TOOLS

    overlap = TAPD_PRELOAD_READ_TOOLS & _declared_service_tools()
    assert overlap, "预载清单与服务端工具面零重合——这正是那次线上故障的形态"
    assert len(overlap) == len(TAPD_PRELOAD_READ_TOOLS)


def test_the_query_and_business_paths_are_both_reachable_in_window() -> None:
    """两条答复路径都得在窗口里，否则模型只能走其中一条还不知道另一条存在。"""
    from deeptutor.agents.test.pipeline import TAPD_PRELOAD_READ_TOOLS

    # 引用路径：先拿句柄，再取条目——少一个都走不完
    assert {"tapd_find", "tapd_cite"} <= TAPD_PRELOAD_READ_TOOLS
    # 业务路径：可答性检查 + 可信查询
    assert {"tapd_semantic_status", "tapd_business_query"} <= TAPD_PRELOAD_READ_TOOLS
    # 语义人核环节：不给这两个，业务路径永远停在 SEMANTIC_UNCONFIRMED
    assert {"tapd_semantic_review", "tapd_semantic_confirm"} <= TAPD_PRELOAD_READ_TOOLS


@pytest.mark.parametrize("language", ["zh", "en"])
def test_every_journey_tool_the_prompt_names_is_covered_by_the_primary_prefix(
    language: str,
) -> None:
    """提示词点名的每个 `journey_*`，都落在主用前缀里。

    这是错配的机械闸：纪律段里新写一个 `journey_xxx`，而主用声明没覆盖到，
    就会重现「提示词命令模型用一个够不着的工具」这个原始缺陷。
    """
    import re

    from deeptutor.agents.test.pipeline import (
        JOURNEY_PRELOAD_PREFIXES,
        PROMPT_AGENT,
        PROMPT_MODULE,
    )

    prompts = PromptManager().load_prompts(PROMPT_MODULE, PROMPT_AGENT, language=language)
    body = "\n".join(str(v or "") for v in prompts.values() if isinstance(v, str))
    named = set(re.findall(r"journey_[a-z_]+", body))
    assert named, f"{language} 提示词里一个 journey_ 工具都没点到，判据失去意义"
    uncovered = [
        name
        for name in named
        if not any(name.startswith(prefix) for prefix in JOURNEY_PRELOAD_PREFIXES)
    ]
    assert uncovered == [], f"{language} 提示词点名但主用声明没覆盖：{uncovered}"


class _FakeDefinition:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeTool:
    """Minimal stand-in for a pooled MCP tool: a server-prefixed public name."""

    def __init__(self, original: str, server: str = "tapd-capability") -> None:
        self.original_name = original
        self._definition = _FakeDefinition(f"mcp_{server}_{original}")

    def get_definition(self):
        return self._definition


def _pipeline_with_pool(names):
    from deeptutor.agents.test.pipeline import TestJourneyPipeline

    pipeline = TestJourneyPipeline(language="zh")
    pipeline._pageindex_docs = {}
    pipeline._deferred_pool = tuple(_FakeTool(n) for n in names)
    return pipeline


def test_stale_preload_is_reported_as_stale_not_as_disconnected() -> None:
    """连着但一个预载名都对不上时，必须说清是清单过期，不是连不上。

    这是 2026-08-22 线上那次的确切形态：19 个工具连着、契约工具齐全、
    宿主上下文解析成功，唯独预载清单还写着退役服务的名字，于是
    `窗口=34 | tapd类=0`，模型照实说"没连通"，把所有人引去查网络。
    症状与断线**无法区分**，除非有人明确说出来——上次没人说。
    """
    from deeptutor.core.context import UnifiedContext

    # 服务连着（契约工具在池子里），但池子里的名字与预载清单零重合
    pipeline = _pipeline_with_pool(
        ["tapd_context_status", "tapd_context_resolve", "tapd_projects"]
    )
    # 预载清单临时换成退役的第三方名字 = 复现当时的漂移
    import deeptutor.agents.test.pipeline as mod

    original = mod.TAPD_PRELOAD_READ_TOOLS
    mod.TAPD_PRELOAD_READ_TOOLS = frozenset({"get_stories_or_tasks", "get_bug"})
    try:
        notice = pipeline._stale_preload_notice(
            UnifiedContext(session_id="s", user_message="帮我拉一下TAPD的需求",
                           active_capability="test")
        )
    finally:
        mod.TAPD_PRELOAD_READ_TOOLS = original

    assert notice, "零重合必须被说出来，不能静默"
    assert "预载" in notice
    assert "不是连接问题" in notice
    assert "不要说" in notice and "连不上" in notice


def test_no_stale_notice_when_the_preloaded_names_do_resolve() -> None:
    """正常情况下不加噪：对得上就什么都不说。"""
    from deeptutor.agents.test.pipeline import TAPD_PRELOAD_READ_TOOLS
    from deeptutor.core.context import UnifiedContext

    pipeline = _pipeline_with_pool(sorted(TAPD_PRELOAD_READ_TOOLS))
    notice = pipeline._stale_preload_notice(
        UnifiedContext(session_id="s", user_message="hi", active_capability="test")
    )
    assert notice == ""


def test_nothing_connected_stays_the_context_gates_story() -> None:
    """一个 TAPD 工具都没有时不重复报警——那句话该由上下文闸去说。

    两处各说一遍会让读的人以为是两个问题。
    """
    from deeptutor.core.context import UnifiedContext

    pipeline = _pipeline_with_pool(["journey_ingest", "kb_search"])
    notice = pipeline._stale_preload_notice(
        UnifiedContext(session_id="s", user_message="hi", active_capability="test")
    )
    assert notice == ""

