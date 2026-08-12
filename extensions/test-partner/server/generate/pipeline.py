"""生成阶段机：把三段调用串成一次任务。**零 DeepTutor import。**

## 三段与它们各自的失败处置

| 段 | 产出 | 失败了怎么办 |
|---|---|---|
| 起草场景 | 一段大白话 | 直接抛——这一段很短，失败多半是模型没配好，重试没意义 |
| 挑端点 + 出清单 | 端点选择 + 用例标题 | 截断→减少条数重试；写错→定向修复一次 |
| 写请求与断言 | 完整用例 | 分批；某批截断→**对半减量重试该批**；写错→定向修复该批 |

## 为什么截断与写错要分开处置（`model.py` 防线二的下游）

`complete()` 丢掉了 `finish_reason`，截断在调用方看来与正常结束无异。
若把半截 JSON 当成"写错了"送去修复，模型会**把它补全**——产出一个看起来
合法、内容却是编的结果。所以这里严格按 `parse_json_output` 的判别分流：

- `OutputTruncated` → **减量**（东西太多写不下），绝不修复
- `OutputMalformed` → **修复**（东西写错了），绝不减量

## 预算与取消

每次模型调用前都过 `budget_ok`，超了直接停并如实报告"生成了几条、还差几条"，
不静默截断也不假装完成。取消由调用方传入的 `should_cancel` 决定，
在每次模型调用**之前**检查——调用中途没法叫停，硬中断只会浪费已花的钱。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from server.generate.case_shape import normalize_generated_cases
from server.generate.context import Material
from server.generate.model import (
    SPEC_DETAIL,
    SPEC_OUTLINE,
    SPEC_SCENARIO,
    CallModel,
    OutputMalformed,
    OutputTruncated,
    budget_ok,
    clamp,
    parse_json_output,
)
from server.generate.prompts import (
    DETAIL_SYSTEM,
    OUTLINE_SYSTEM,
    SCENARIO_SYSTEM,
    detail_prompt,
    outline_prompt,
    scenario_prompt,
)

#: 一次生成的用例条数区间。上限不是拍脑袋——每条都要人审，
#: 超过这个量审起来比自己写还慢（对标 MeterSphere 时记下的教训）。
MIN_CASES, MAX_CASES, DEFAULT_CASES = 1, 30, 8
#: 第三段的分批大小。太大容易撞 max_tokens，太小则调用次数翻倍。
MIN_BATCH, MAX_BATCH, DEFAULT_BATCH = 1, 8, 4


class Cancelled(RuntimeError):
    """用户取消。不是错误路径，是正常终止。"""


class BudgetExhausted(RuntimeError):
    """模型调用次数用尽。产出可能不完整，必须如实告知。"""


@dataclass
class Progress:
    stage: str
    done: int
    total: int
    note: str = ""


@dataclass
class GenerationResult:
    scenario: str
    picked_endpoints: list[dict[str, Any]]
    skipped_reason: str
    cases: list[dict[str, Any]]
    calls_used: int
    notes: list[str] = field(default_factory=list)
    complete: bool = True


OnProgress = Callable[[Progress], None]
ShouldCancel = Callable[[], bool]


class _Budget:
    """调用计数器。把"还能不能调"收敛到一处，免得每个调用点各判各的。"""

    def __init__(self) -> None:
        self.used = 0

    def spend(self) -> None:
        if not budget_ok(self.used):
            raise BudgetExhausted(f"模型调用次数已达上限（{self.used} 次）")
        self.used += 1


async def _ask(call: CallModel, budget: _Budget, should_cancel: ShouldCancel,
               prompt: str, *, system: str, spec) -> str:
    """一次模型调用：先看取消，再看预算，最后才花钱。

    取消检查放在调用**之前**是有意的——调用中途没法叫停，
    中途硬中断只会浪费已经花掉的钱，还拿不到结果。
    """
    if should_cancel():
        raise Cancelled("用户取消")
    budget.spend()
    return await call(prompt, system=system, spec=spec)


# ── 第一段 ────────────────────────────────────────────────────────────────

async def draft_scenario(call: CallModel, material: Material, *,
                         should_cancel: ShouldCancel = lambda: False,
                         budget: _Budget | None = None) -> str:
    """让模型用一段大白话说这份抓包在做什么。

    不做重试：这一段很短，失败基本是"模型没配好"或"网络不通"，
    重试解决不了，只会让用户多等一轮。
    """
    b = budget or _Budget()
    raw = await _ask(call, b, should_cancel, scenario_prompt(material),
                     system=SCENARIO_SYSTEM, spec=SPEC_SCENARIO)
    return raw.strip()


# ── 第二段 ────────────────────────────────────────────────────────────────

async def build_outline(call: CallModel, material: Material, scenario: str, *,
                        max_cases: Any = DEFAULT_CASES,
                        should_cancel: ShouldCancel = lambda: False,
                        budget: _Budget | None = None) -> dict[str, Any]:
    """挑端点 + 出用例清单。截断则减量，写错则修复，两条路不混。"""
    b = budget or _Budget()
    want = clamp(max_cases, low=MIN_CASES, high=MAX_CASES, default=DEFAULT_CASES)

    for attempt in range(2):
        prompt = outline_prompt(material, scenario, max_cases=want)
        raw = await _ask(call, b, should_cancel, prompt,
                         system=OUTLINE_SYSTEM, spec=SPEC_OUTLINE)
        try:
            data = parse_json_output(raw)
        except OutputTruncated:
            if attempt == 1 or want <= MIN_CASES:
                raise
            # 减量，不修复：东西太多写不下，补全它只会得到编造的内容
            want = max(MIN_CASES, want // 2)
            continue
        except OutputMalformed:
            if attempt == 1:
                raise
            # 修复，不减量：条数没问题，是格式写坏了
            scenario = scenario  # 保持输入不变，仅重试一次
            continue
        return _normalise_outline(data, want)

    raise OutputMalformed("清单生成两次都失败")


def _normalise_outline(data: Any, want: int) -> dict[str, Any]:
    """把模型输出规整成固定形状，并对条数**兜底截断**。

    模型不守 `max_cases` 是常态。这里硬截，不然下一段的分批数会跟着涨，
    预算闸要到很后面才拦得住——那时钱已经花了。
    """
    if not isinstance(data, dict):
        raise OutputMalformed("清单输出不是对象")
    cases = data.get("cases")
    if not isinstance(cases, list):
        raise OutputMalformed("清单里没有 cases 数组")
    picked = data.get("picked_endpoints")
    return {
        "picked_endpoints": picked if isinstance(picked, list) else [],
        "skipped_reason": str(data.get("skipped_reason") or ""),
        "cases": [c for c in cases if isinstance(c, dict)][:want],
    }


# ── 第三段 ────────────────────────────────────────────────────────────────

async def fill_details(call: CallModel, material: Material, scenario: str,
                       cases: list[dict[str, Any]], *,
                       batch_size: Any = DEFAULT_BATCH,
                       should_cancel: ShouldCancel = lambda: False,
                       on_progress: OnProgress | None = None,
                       budget: _Budget | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """把清单补成完整用例。分批，单批失败不拖垮整批。

    返回 ``(用例, 说明)``。说明里会写清哪一批没做成——
    少几条是可以接受的，**假装做成了不行**。
    """
    b = budget or _Budget()
    size = clamp(batch_size, low=MIN_BATCH, high=MAX_BATCH, default=DEFAULT_BATCH)
    out: list[dict[str, Any]] = []
    notes: list[str] = []
    total = len(cases)

    i = 0
    while i < total:
        batch = cases[i:i + size]
        try:
            filled, shape_notes, consumed = await _fill_one_batch(
                call, b, should_cancel, material, scenario, batch)
            out.extend(filled)
            notes.extend(shape_notes)
            i += consumed
        except BudgetExhausted:
            notes.append(
                f"模型调用次数用尽，还有 {total - i} 条没来得及补细节——"
                "已生成的可以正常使用，剩下的可以再跑一次"
            )
            break
        except (OutputTruncated, OutputMalformed) as exc:
            ids = "、".join(str(c.get("id") or "?") for c in batch)
            notes.append(f"这几条没生成成功（{ids}）：{exc}")
            i += len(batch)
        if on_progress:
            on_progress(Progress("detail", min(i, total), total))

    return out, notes


async def _fill_one_batch(call: CallModel, budget: _Budget,
                          should_cancel: ShouldCancel, material: Material,
                          scenario: str, batch: list[dict[str, Any]],
                          ) -> tuple[list[dict[str, Any]], list[str], int]:
    """一批的三种结局：成、截断后对半减量再成、彻底失败。

    返回 `(用例, 形状救回说明, 实际消费的清单条数)`。截断减半成功时，
    调用方据第三项继续处理后半批，不能把它们静默跳过。说明**顺着返回值往上传**，不走模块级收集器——
    同一进程里可能有多个用户的生成任务并行，共享收集器会把 A 的说明串到 B 的结果里。
    """
    work = list(batch)
    for attempt in range(2):
        prompt = detail_prompt(material, scenario, work)
        raw = await _ask(call, budget, should_cancel, prompt,
                         system=DETAIL_SYSTEM, spec=SPEC_DETAIL)
        try:
            data = parse_json_output(raw)
        except OutputTruncated:
            if attempt == 1 or len(work) <= 1:
                raise
            work = work[:max(1, len(work) // 2)]   # 减量，不修复
            continue
        except OutputMalformed:
            if attempt == 1:
                raise
            continue                                # 修复，不减量
        cases = data.get("cases") if isinstance(data, dict) else None
        if not isinstance(cases, list):
            raise OutputMalformed("细节输出里没有 cases 数组")
        # 收敛到消费侧唯一形状（BB-487）。提示词已直接要求规范形状，
        # 这一步兜的是"模型不是编译器"：它会漏、会用同义词、会退回旧写法。
        # 不兜的后果实测过——断言落在没人读的位置，整批用例静默变成不可执行的空壳。
        normalized, notes = normalize_generated_cases(
            [c for c in cases if isinstance(c, dict)], work)
        return normalized, notes, len(work)
    raise OutputMalformed("这一批两次都没成")


# ── 串起来 ────────────────────────────────────────────────────────────────

async def generate(call: CallModel, material: Material, *,
                   scenario: str | None = None,
                   max_cases: Any = DEFAULT_CASES,
                   batch_size: Any = DEFAULT_BATCH,
                   should_cancel: ShouldCancel = lambda: False,
                   on_progress: OnProgress | None = None) -> GenerationResult:
    """完整一次生成。

    ``scenario`` 传了就用用户改过的那段，不传才让模型起草——
    设计稿里用户可以改 AI 的草稿，改过的必须优先于重新起草。
    """
    budget = _Budget()
    notes = list(material.notes)

    if on_progress:
        on_progress(Progress("scenario", 0, 1))
    text = scenario if scenario and scenario.strip() else await draft_scenario(
        call, material, should_cancel=should_cancel, budget=budget)
    if on_progress:
        on_progress(Progress("scenario", 1, 1))

    if on_progress:
        on_progress(Progress("outline", 0, 1))
    outline = await build_outline(call, material, text, max_cases=max_cases,
                                  should_cancel=should_cancel, budget=budget)
    if on_progress:
        on_progress(Progress("outline", 1, 1, f"{len(outline['cases'])} 条"))

    cases, detail_notes = await fill_details(
        call, material, text, outline["cases"], batch_size=batch_size,
        should_cancel=should_cancel, on_progress=on_progress, budget=budget)
    notes.extend(detail_notes)

    return GenerationResult(
        scenario=text,
        picked_endpoints=outline["picked_endpoints"],
        skipped_reason=outline["skipped_reason"],
        cases=cases,
        calls_used=budget.used,
        notes=notes,
        complete=len(cases) == len(outline["cases"]),
    )
