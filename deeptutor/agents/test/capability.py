"""[fork] 「测试」capability —— req 测试旅程的载体（决策 0019 案 B / ADR-M2-03）。

## 为什么它这么薄

它**复用主聊的 agentic pipeline**，而不是像 visualize 那样自建一条流水线。
这不是省事，是必需：四类人闸卡（定档 / 澄清 / 采纳流转 / 写确认）走的是
`ask_user`，而 `wait_for_user_reply` 等待器**只在主聊 turn 上注入**
（`deeptutor/services/session/turn_runtime.py:1620`）。自建流水线 = 自己造一条
没有 waiter 的通道 = 复刻 BB-502 那个静默降级。

所以这里的形态照抄 `ChatCapability`（同样 27 行量级）：把 pipeline 让给主聊，
本能力只贡献两样东西——**旅程纪律的系统提示块**，与**开旅程时的门票**。

## 与 chat 的实质差别只有三点

1. 模式徽章与 picker 里能选中它（前端侧）；
2. 系统提示里多一段旅程纪律（本文件加载的 `test_journey.yaml`）；
3. `journey_*` 工具在这一轮里被明确指引使用。

**工具可见性不是差别**：MCP 工具在任何 capability 轮都看得见（渐进披露只降低
概率、不构成闸，0025 §2 已裁）。硬保证在后果层——门票 + 既存 batch_id + 写确认，
全在 extensions 侧（`server/journey/gate.py`）。这里**刻意不假装**自己能挡住误调。
"""

from __future__ import annotations

from deeptutor.agents.chat.agentic_pipeline import CHAT_OPTIONAL_TOOLS, AgenticChatPipeline
from deeptutor.core.capability_protocol import BaseCapability, CapabilityManifest
from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream_bus import StreamBus
from deeptutor.runtime.request_contracts import get_capability_request_schema
from deeptutor.services.prompt.manager import PromptManager

#: prompt 模块名。必须同时出现在 `PromptManager.MODULES` 里，
#: 否则 `load_prompts` 查不到本模块而**静默回落**——那是登记表里唯一
#: 「漏改不报错」的一处，所以有测试守（`tests/test_fork_capabilities.py`）。
PROMPT_MODULE = "test"
PROMPT_AGENT = "test_journey"


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


class TestCapability(BaseCapability):
    manifest = CapabilityManifest(
        name="test",
        description=(
            "Drive a requirement through a full test journey: intake, clarify, "
            "analyze, draft cases, adopt, compile, execute, and coverage close-out."
        ),
        stages=["exploring", "responding"],
        tools_used=CHAT_OPTIONAL_TOOLS,
        cli_aliases=["test", "journey"],
        request_schema=get_capability_request_schema("test"),
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        # 把旅程纪律挂进本轮上下文，然后原样交给主聊 pipeline。
        # 走主聊这条路，`ask_user` 的 waiter、MCP 工具组装、重放语义全部免费继承。
        block = journey_system_block(context.language)
        if block:
            context.metadata["capability_system_block"] = block
        pipeline = AgenticChatPipeline(language=context.language)
        await pipeline.run(context, stream)
