"""[fork] 「测试」capability 的 pipeline —— 只为把旅程纪律真送进 system prompt。

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

**这里刻意只 override 一个钩子**，不复制父类任何逻辑。自建流水线才是复刻 BB-502。

## 判据在哪

`tests/core/test_fork_capabilities_runtime.py`——断的是**渲染出来的 system prompt
里含纪律段的锚串**，不是断 `run()` 的源码文本里出现过某个类名。旧那条断源码的测试
（`test_capability_reuses_main_chat_pipeline`）九条全绿也没挡住这个空转：
源码文本与运行时行为之间没有蕴含关系，它断的是「作者写了这个词」不是「模型收到了这段话」。
"""

from __future__ import annotations

from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
from deeptutor.capabilities.protocol import PromptBlock
from deeptutor.core.context import UnifiedContext
from deeptutor.services.prompt.manager import PromptManager

#: prompt 模块名。必须同时出现在 `PromptManager.MODULES` 里，
#: 否则 `load_prompts` 查不到本模块而**静默回落**——那是登记表里唯一
#: 「漏改不报错」的一处，所以有测试守。
PROMPT_MODULE = "test"
PROMPT_AGENT = "test_journey"

#: system prompt 里这个块的名字，渲染成 `## test_journey` 一节
#: （`prompt_blocks.py:52` 的 `f"## {block.name}"`）。
JOURNEY_BLOCK_NAME = "test_journey"


def journey_system_block(language: str = "zh") -> str:
    """旅程纪律系统块。加载失败**如实返回空串**，不塞一段兜底文案。

    塞兜底文案会让「prompt 模块没注册」这个配置错误看起来像正常工作——
    正是 MODULES 那条漏改不报错的放大器。空串至少会让人发现模型没按纪律走。
    """
    try:
        prompts = PromptManager().load_prompts(
            PROMPT_MODULE, PROMPT_AGENT, language=language)
    except Exception:  # noqa: BLE001 - 提示词读不到不该让整轮崩
        return ""
    parts = [str(prompts.get(key) or "").strip()
             for key in ("journey_discipline", "workbench_pointer")]
    return "\n\n".join(p for p in parts if p)


class TestJourneyPipeline(AgenticChatPipeline):
    """主聊 pipeline + 一段旅程纪律。除该块外与 chat 逐字节相同。"""

    def _capability_system_blocks(self, context: UnifiedContext) -> list[PromptBlock]:
        # 先拿父类的（用户另外开着的 loop capability 照常生效），再追加自己的。
        # 追加而不是替换：这一模式不排他，它只是多一段纪律。
        blocks = list(super()._capability_system_blocks(context))
        content = journey_system_block(self.language)
        if content:
            blocks.append(PromptBlock(JOURNEY_BLOCK_NAME, content))
        return blocks
