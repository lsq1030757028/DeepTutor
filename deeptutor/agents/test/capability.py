"""[fork] 「测试」capability —— req 测试旅程的载体（决策 0019 案 B / ADR-M2-03）。

## 为什么它这么薄

它**复用主聊的 agentic pipeline**，而不是像 visualize 那样自建一条流水线。
这不是省事，是必需：四类人闸卡（定档 / 澄清 / 采纳流转 / 写确认）走的是
`ask_user`，而 `wait_for_user_reply` 等待器**只在主聊 turn 上注入**
（`deeptutor/services/session/turn_runtime.py:1620`）。自建流水线 = 自己造一条
没有 waiter 的通道 = 复刻 BB-502 那个静默降级。

所以这里的形态照抄 `ChatCapability`：把 pipeline 让给主聊，本能力只贡献两样
东西——**旅程纪律的系统提示块**，与可信 Journey 调用所需的 Test 模式上下文。

## 与 chat 的实质差别只有三点

1. 模式徽章与 picker 里能选中它（前端侧）；
2. 系统提示里多一段旅程纪律（`pipeline.TestJourneyPipeline` 追加的 PromptBlock）；
3. `journey_*` 工具在这一轮里被明确指引使用。

**工具可见性现在也是差别**：普通 chat 的 manifest、加载与 dispatch 都看不到
Journey；Test 轮由宿主注入短时签名，绑定 owner/session/turn/tool/args，扩展侧验签后
才触碰产物目录。既存 batch_id、写确认和产物牙仍在 extensions 侧继续兜住业务后果。

## BB-508：纪律段怎么进 prompt（别再改回去）

上一版把纪律段写进 `context.metadata["capability_system_block"]`，**那个键全仓
无人读取**，等于选了模式什么也没发生。现在走 `TestJourneyPipeline`——
它 override `_capability_system_blocks`，是 pipeline 真正读取的那个钩子。
判据也随之换成断渲染后的 system prompt，见 `tests/core/test_fork_capabilities_runtime.py`。
"""

from __future__ import annotations

from deeptutor.agents.chat.agentic_pipeline import CHAT_OPTIONAL_TOOLS
from deeptutor.agents.test.pipeline import (
    PROMPT_AGENT,
    PROMPT_MODULE,
    TestJourneyPipeline,
    journey_system_block,
)
from deeptutor.core.capability_protocol import BaseCapability, CapabilityManifest
from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream_bus import StreamBus
from deeptutor.runtime.request_contracts import get_capability_request_schema

__all__ = [
    "PROMPT_AGENT",
    "PROMPT_MODULE",
    "TestCapability",
    "journey_system_block",
]


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
        # 主聊 pipeline 的子类：`ask_user` 的 waiter、MCP 工具组装、重放语义
        # 全部原样继承，唯一的加法是 system prompt 里那段旅程纪律。
        pipeline = TestJourneyPipeline(language=context.language)
        await pipeline.run(context, stream)
