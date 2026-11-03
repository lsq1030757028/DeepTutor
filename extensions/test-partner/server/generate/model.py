"""模型调用的契约与防线。**本模块零 DeepTutor import**（见包 docstring）。

宿主侧 router 注入一个符合 :class:`CallModel` 的可调用对象，本包只认这个协议。
这样生成逻辑能脱离 DeepTutor 单测，也让"懂模型的代码"收敛在宿主一个文件里。

## 三条防线，都是实测出来的，不是照着最佳实践抄的

### 一 · 超时必须由我们兜

`deeptutor.services.llm.complete()` 建 AsyncOpenAI 时**没传 timeout**
（`providers/openai_compat_provider.py`），而默认 ``max_retries`` 来自 settings
且带指数退避，最坏累计到 500 秒量级。HAR 这种长输入一旦卡住，HTTP 请求会挂死。

所以调用方**必须**同时做两件事：把 ``max_retries`` 压到 1、在外面套
``asyncio.wait_for``。本模块把这两件事写进 :class:`CallSpec` 的默认值，
让"忘记传"变成不可能，而不是靠文档提醒。

### 二 · 截断我们自己判

``complete()`` 的返回类型是 ``str``——**``finish_reason`` 在这一层被丢弃了**
（`factory.py` 只在 ``finish_reason == "error"`` 时抛，其余直接返回内容）。
也就是说 ``max_tokens`` 用尽导致的截断，在调用方看来和正常结束**没有区别**。

危险的不是截断本身，是"截断产生的半截 JSON 被当成模型写错了 JSON"，
于是走进修复流程、被补全成一个**看起来合法但内容是编的**结果。

:func:`parse_json_output` 因此把两种失败分开判：括号不平衡 = 截断（该减量重试），
括号平衡但解析失败 = 模型确实写错了（该定向修复）。两者的处置完全不同。

### 三 · 花钱的天花板在服务端

评审实测指出的一条：任何由客户端传入、又参与循环次数计算的参数，
都是一个可以把账单撑开的洞（传 1 让 10 次调用变 43 次，传 0 直接除零）。

所以本模块的 :func:`clamp` 是**唯一**允许把外部数字变成循环次数的入口，
且上下限写死在代码里，不接受请求参数覆盖。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol


class CallModel(Protocol):
    """宿主注入的模型调用口。

    实现方（`deeptutor/api/routers/`）负责：解析当前用户可用的模型、
    施加超时、把 ``max_retries`` 压到 1。本包只管把 spec 递过去。
    """

    async def __call__(self, prompt: str, *, system: str, spec: "CallSpec") -> str: ...


# ── 硬上限：写死在代码里，不接受请求参数覆盖 ────────────────────────────────
#: 单次调用的 token 上限。分档而不是一个数——起草场景和写断言的体量差一个量级，
#: 统一给大的等于给每一次调用都开着最贵的口子。
MAX_TOKENS_CEILING = 6000
#: 单次调用的墙钟上限（秒）。超过即放弃，不重试——重试长任务是账单放大器。
TIMEOUT_CEILING_S = 240
#: 一次生成任务允许的模型调用次数上限。防的是"分批"参数被撑开。
MAX_CALLS_PER_JOB = 12


@dataclass(frozen=True)
class CallSpec:
    """一次模型调用的全部参数。默认值即防线，见模块 docstring。"""

    max_tokens: int
    timeout_s: float
    json_mode: bool = False
    temperature: float = 0.3
    #: 压到 1：上游默认带指数退避，最坏累计 500 秒量级。
    max_retries: int = 1
    #: 关掉指数退避，避免单次重试就把墙钟吃光。
    exponential_backoff: bool = False
    retry_delay: float = 2.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_tokens <= MAX_TOKENS_CEILING:
            raise ValueError(f"max_tokens 越界：{self.max_tokens}（上限 {MAX_TOKENS_CEILING}）")
        if not 1 <= self.timeout_s <= TIMEOUT_CEILING_S:
            raise ValueError(f"timeout_s 越界：{self.timeout_s}（上限 {TIMEOUT_CEILING_S}）")
        if self.max_retries > 2:
            raise ValueError("max_retries 不得超过 2——上游带退避，重试是账单放大器")


#: 三次调用的档位。放在这里而不是散在调用点，是为了让"一共会花多少"可被一眼审计。
SPEC_SCENARIO = CallSpec(max_tokens=600, timeout_s=60)                  # 起草场景描述
SPEC_OUTLINE = CallSpec(max_tokens=1800, timeout_s=120, json_mode=True)  # 挑端点 + 用例清单
SPEC_DETAIL = CallSpec(max_tokens=3500, timeout_s=180, json_mode=True)   # 写请求与断言


class ModelUnavailable(RuntimeError):
    """没有可用模型。**不是异常路径，是正常空态**——页面要据此给出配置指引。

    单独立一个类型，是因为上游在"一个模型都没配"时**不抛错**：
    它会解析成一个默认 provider + 空 key，用户最终拿到的是对方的 401，
    而不是 DeepTutor 的"你还没配模型"。这个区分必须在我们这层做出来。
    """


class ModelTimeout(RuntimeError):
    """超过墙钟上限。可重试，但由人决定，不自动。"""


class OutputTruncated(ValueError):
    """输出被 ``max_tokens`` 截断。**处置是减量重试，不是修复。**"""


class OutputMalformed(ValueError):
    """输出完整但不是合法 JSON。**处置是定向修复，不是减量。**"""


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def _strip_fence(text: str) -> str:
    """去掉 markdown 代码围栏——json_mode 下仍有模型会加。"""
    return _FENCE.sub("", text).strip()


def _brackets_balanced(text: str) -> bool:
    """括号是否平衡（跳过字符串字面量内的括号与转义）。

    这是**区分截断与写错**的判据：截断一定在某处戛然而止、括号不平衡；
    而模型写错 JSON（少逗号、多逗号、键没加引号）通常括号是平衡的。
    """
    depth = 0
    in_str = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_str


def parse_json_output(raw: str) -> Any:
    """解析模型的 JSON 输出，并把**截断**与**写错**判成两种错。

    为什么非分不可：``complete()`` 丢掉了 ``finish_reason``，截断在调用方看来
    与正常结束无异。若把半截 JSON 当成"写错了"送去修复，模型会**把它补全**——
    产出一个看起来合法、内容却是编的结果。那比直接报错坏得多。
    """
    text = _strip_fence(raw)
    if not text:
        raise OutputMalformed("模型返回空内容")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        if not _brackets_balanced(text):
            raise OutputTruncated(
                f"输出被截断（括号不平衡，长度 {len(text)}）——应减少本批数量后重试"
            ) from exc
        raise OutputMalformed(f"输出不是合法 JSON：{exc}") from exc


def clamp(value: Any, *, low: int, high: int, default: int) -> int:
    """把任何外部来的数字夹到安全区间。**这是本包唯一允许生成循环次数的入口。**

    非数字、越界、负数、零一律回落到 ``default``——不抛错，因为这些值多半来自
    请求体，而"请求参数写错就整个失败"对用户没有价值；但也绝不照单全收，
    否则客户端传 1 就能把一次生成的调用次数放大数倍。
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n < low or n > high:
        return default
    return n


def budget_ok(calls_so_far: int) -> bool:
    """一次生成任务是否还允许再调模型。"""
    return calls_so_far < MAX_CALLS_PER_JOB
