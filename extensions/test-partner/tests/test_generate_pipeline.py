"""阶段机的回归测试。全部用假模型，不发真请求、不花钱。

重点不在"顺利时能跑通"，而在**失败时的处置是不是分对了路**：
截断该减量、写错该修复、预算用尽该如实说少了几条。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from server.generate.context import Material
from server.generate.model import (
    MAX_CALLS_PER_JOB,
    OutputMalformed,
    OutputTruncated,
)
from server.generate.pipeline import (
    BudgetExhausted,
    Cancelled,
    MAX_CASES,
    build_outline,
    draft_scenario,
    fill_details,
    generate,
)


def run(coro):
    """跑一个协程。

    刻意不引入 pytest-asyncio：`server/generate/` 是零依赖设计，
    为几条测试加一个 CI 当前还验不了的依赖不划算。`asyncio.run` 够用。
    """
    return asyncio.run(coro)


@pytest.fixture()
def material():
    return Material(
        endpoints=[{"method": "POST", "path": "/api/order/create", "calls": 14,
                    "statuses": {"200": 12, "400": 2},
                    "sample_body": {"skuId": "SKU-1", "qty": 2}}],
        login=None, stats={"requests": 12, "noise_filtered": 3, "endpoints": 1},
        notes=["端点太多，只给了 40 个"], scrub_hits={})


class FakeModel:
    """按脚本依次返回。记录每次收到的 prompt，便于断言"减量了没有"。"""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def __call__(self, prompt, *, system, spec):
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError("假模型被多调了一次——说明调用次数超出预期")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _outline(n=2, **extra):
    d = {"picked_endpoints": [{"method": "POST", "path": "/api/order/create"}],
         "skipped_reason": "其余与场景无关",
         "cases": [{"id": f"TC-{i:03d}", "title": f"用例{i}", "intent": "正常"}
                   for i in range(1, n + 1)]}
    d.update(extra)
    return json.dumps(d, ensure_ascii=False)


def _detail(ids):
    return json.dumps({"cases": [
        {"id": i, "title": "t", "intent": "正常",
         "request": {"method": "POST", "url": "/api/order/create", "body": {}},
         "assertions": [{"kind": "status", "expect": 200}]} for i in ids]},
        ensure_ascii=False)


# ── 顺利路径 ──────────────────────────────────────────────────────────────

def test_happy_path_runs_three_stages(material):
    fake = FakeModel("登录后下单这条链路", _outline(2), _detail(["TC-001", "TC-002"]))
    res = run(generate(fake, material))
    assert res.scenario == "登录后下单这条链路"
    assert len(res.cases) == 2
    assert res.calls_used == 3
    assert res.complete is True


def test_user_edited_scenario_skips_the_draft_call(material):
    """用户改过的场景必须优先——重新起草会把他的修改冲掉，还多花一次钱。"""
    fake = FakeModel(_outline(1), _detail(["TC-001"]))
    res = run(generate(fake, material, scenario="我自己写的场景"))
    assert res.scenario == "我自己写的场景"
    assert res.calls_used == 2, "起草那次不该发生"


def test_material_notes_are_carried_into_the_result(material):
    """裁剪层说的"少给了什么"要一路带到结果里，不能在中间层被吞掉。"""
    fake = FakeModel("s", _outline(1), _detail(["TC-001"]))
    res = run(generate(fake, material))
    assert any("端点太多" in n for n in res.notes)


# ── 截断 → 减量，绝不修复 ────────────────────────────────────────────────

def test_truncated_outline_retries_with_fewer_cases(material):
    """截断意味着"要的太多写不下"。补全它只会得到编造的内容，所以必须减量。"""
    fake = FakeModel('{"cases": [{"id": "TC-001"', _outline(2))
    out = run(build_outline(fake, material, "s", max_cases=8))
    assert len(out["cases"]) == 2
    assert "不超过 4 条" in fake.prompts[1], "第二次应当把条数减半"


def test_truncated_detail_batch_halves_the_batch(material):
    cases = [{"id": f"TC-{i:03d}"} for i in range(1, 5)]
    fake = FakeModel('{"cases": [{"id": "TC-001"', _detail(["TC-001", "TC-002"]))
    out, notes = run(fill_details(fake, material, "s", cases, batch_size=4))
    assert len(out) == 2
    assert "TC-003" not in fake.prompts[1], "第二次应当只带前半批"


def test_truncation_twice_gives_up_instead_of_looping(material):
    fake = FakeModel('{"a": [', '{"a": [')
    with pytest.raises(OutputTruncated):
        run(build_outline(fake, material, "s", max_cases=8))


# ── 写错 → 修复，绝不减量 ────────────────────────────────────────────────

def test_malformed_outline_retries_at_the_same_size(material):
    """括号平衡但解析失败 = 条数没问题、格式写坏了。减量是错的处置。"""
    fake = FakeModel('{"cases": [1,]}', _outline(3))
    out = run(build_outline(fake, material, "s", max_cases=6))
    assert len(out["cases"]) == 3
    assert "不超过 6 条" in fake.prompts[1], "写错不该减量"


def test_malformed_twice_raises(material):
    fake = FakeModel("不是 JSON", "还不是 JSON")
    with pytest.raises(OutputMalformed):
        run(build_outline(fake, material, "s"))


# ── 分批：单批失败不拖垮整批 ─────────────────────────────────────────────

def test_one_bad_batch_does_not_kill_the_rest(material):
    """少几条可以接受，假装做成了不行——失败的批次必须出现在 notes 里。"""
    cases = [{"id": f"TC-{i:03d}"} for i in range(1, 5)]
    fake = FakeModel("坏输出", "还是坏输出", _detail(["TC-003", "TC-004"]))
    out, notes = run(fill_details(fake, material, "s", cases, batch_size=2))
    assert [c["id"] for c in out] == ["TC-003", "TC-004"]
    assert any("TC-001" in n for n in notes), "没成的那批要点名"


def test_progress_is_reported_per_batch(material):
    seen = []
    cases = [{"id": f"TC-{i:03d}"} for i in range(1, 5)]
    fake = FakeModel(_detail(["TC-001", "TC-002"]), _detail(["TC-003", "TC-004"]))
    run(fill_details(fake, material, "s", cases, batch_size=2,
                     on_progress=seen.append))
    assert [(p.done, p.total) for p in seen] == [(2, 4), (4, 4)]


# ── 预算与取消 ────────────────────────────────────────────────────────────

def test_budget_exhaustion_reports_what_is_missing(material):
    """超预算要如实说"还差几条"，不静默截断也不假装完成。"""
    cases = [{"id": f"TC-{i:03d}"} for i in range(1, 40)]
    fake = FakeModel(*[_detail([f"TC-{i:03d}"]) for i in range(1, 40)])
    out, notes = run(fill_details(fake, material, "s", cases, batch_size=1))
    assert any("次数用尽" in n and "没来得及" in n for n in notes)
    assert len(out) == MAX_CALLS_PER_JOB


def test_cancel_is_checked_before_spending(material):
    """取消检查在花钱之前——调用中途没法叫停，硬中断只会浪费已花的钱。"""
    fake = FakeModel("不该被调到")
    with pytest.raises(Cancelled):
        run(draft_scenario(fake, material, should_cancel=lambda: True))
    assert fake.prompts == []


def test_cancel_midway_stops_further_calls(material):
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 2          # 前两次放行，之后取消

    fake = FakeModel("s", _outline(4), _detail(["TC-001"]))
    with pytest.raises(Cancelled):
        run(generate(fake, material, should_cancel=cancel))


# ── 模型不守规矩时的兜底 ─────────────────────────────────────────────────

def test_model_returning_more_cases_than_asked_is_truncated(material):
    """模型不守 max_cases 是常态。这里硬截，否则下一段的分批数跟着涨，

    等预算闸拦住时钱已经花了。
    """
    fake = FakeModel(_outline(20))
    out = run(build_outline(fake, material, "s", max_cases=3))
    assert len(out["cases"]) == 3


def test_hostile_max_cases_falls_back_to_default(material):
    fake = FakeModel(_outline(2))
    out = run(build_outline(fake, material, "s", max_cases=99999))
    assert f"不超过 8 条" in fake.prompts[0], "越界值应回落默认,不能照单全收"
    assert len(out["cases"]) == 2


def test_outline_without_cases_array_is_malformed(material):
    fake = FakeModel('{"picked_endpoints": []}', '{"picked_endpoints": []}')
    with pytest.raises(OutputMalformed, match="cases"):
        run(build_outline(fake, material, "s"))


def test_non_dict_entries_are_dropped_not_crashed(material):
    """模型偶尔会在数组里塞字符串。丢掉即可，不该整个任务崩。"""
    payload = json.dumps({"picked_endpoints": [], "skipped_reason": "",
                          "cases": [{"id": "TC-001"}, "垃圾", 42]})
    fake = FakeModel(payload)
    out = run(build_outline(fake, material, "s"))
    assert out["cases"] == [{"id": "TC-001"}]


def test_incomplete_result_is_flagged(material):
    """少生成了就要 complete=False，界面据此提示"可以再跑一次"。"""
    fake = FakeModel("s", _outline(4), "坏", "还是坏")
    res = run(generate(fake, material, batch_size=8))
    assert res.complete is False
    assert res.cases == []
