# -*- coding: utf-8 -*-
"""能力锁自测（设计稿 §6.1，DoD#12/#12b 的「显式」那一半）。

黑盒线最贵的假绿在这一族：拿不到只读库 → 守恒断言写不出来 → 覆盖图上那条规则
看起来只是"没有用例"，与"我们懒得测"长得一模一样。本模块要的是让它以一个
**有名有姓的缺口**出现（`capability:L3 未授予`），而不是一个空格。
"""
import pytest

from server.journey import artifacts, coverage
from server.journey.gates import capability_ladder as cl

from tests.test_journey_exec import (  # noqa: F401 - fixtures 经 import 生效
    GOOD_ANALYSIS, GOOD_FACTS, api_case, build_batch, store, target)


# ── 归一与「口头授予不算授予」 ─────────────────────────────────────────────


def test_absent_ladder_normalizes_to_all_withheld():
    """缺档不是"未知"是"未授予"。

    留成"未知"会在覆盖图上造出第三种状态，谁也说不清该不该收口。
    """
    rows = cl.normalize({})
    assert set(rows) == set(cl.LEVELS)
    assert all(not r["granted"] for r in rows.values())


def test_granted_without_evidence_is_treated_as_withheld():
    """**口头授予不算授予**——一个能靠写 true 打开的锁，第三天一定会被写成 true。"""
    profile = {"capability_ladder": {"L3": {"granted": True}}}
    assert cl.granted(profile, "L3") is False
    assert any("口头授予不算授予" in p for p in cl.problems(profile))


def test_granted_with_evidence_is_granted():
    profile = {"capability_ladder": {
        "L3": {"granted": True, "evidence": "runs/r-1/db_probe.json"}}}
    assert cl.granted(profile, "L3") is True
    assert cl.problems(profile) == []


def test_withheld_is_not_a_problem_only_faking_it_is():
    """没授予是现实，不是问题；**假装授予**才是问题。"""
    profile = {"capability_ladder": {
        "L3": {"granted": False, "reason": "未申请 DB 只读账号"}}}
    assert cl.problems(profile) == []
    note = next(n for n in cl.gap_notes(profile) if n["level"] == "L3")
    assert note["why"] == "未申请 DB 只读账号"


def test_gap_reason_prefix_is_fixed():
    """前缀是消费契约：交互稿 §6f 的覆盖卡按它识别「不是漏测，是没授权」。"""
    for note in cl.gap_notes({}):
        assert note["gap_reason"] == f"capability:{note['level']} 未授予"


def test_withheld_note_says_what_cannot_be_asserted():
    """「能力不足」是废话。缺口要具体到断言族，读的人才知道少了什么。"""
    note = next(n for n in cl.gap_notes({}) if n["level"] == "L3")
    assert "守恒类断言" in note["cannot_assert"]
    assert "分母类断言" in note["cannot_assert"]


@pytest.mark.parametrize("level", sorted(cl.LEVELS))
def test_every_level_has_a_cost_table_entry(level):
    """六档都要说清"少了它做不了什么"，不许有档位只有名字。"""
    assert cl.WITHHELD_COSTS.get(level)


# ── 真进 coverage_ledger：不是一个没人读的字段 ─────────────────────────────


def test_ladder_lands_in_the_coverage_ledger(store, target):
    bid = build_batch(store, target, [api_case("d1")])
    out = coverage.build_coverage(bid)
    ledger = artifacts.load_artifact(bid, "coverage_ledger")
    assert "capability_ladder" in ledger
    withheld = {n["level"] for n in ledger["capability_ladder"]["withheld"]}
    # 本地靶批次没声明任何档位 → 六档全部显式缺席
    assert withheld == set(cl.LEVELS)
    assert out["ok"] in (True, False)  # 本条不判收口，只判字段在场


def test_faked_grant_makes_the_ledger_not_done(store, target):
    """声明授予却无证据 → 覆盖收口判 not done。

    这是能力锁**唯一会骗到下游的形态**：下游会以为守恒断言真的跑过了。
    """
    bid = build_batch(store, target, [api_case("d1")])
    profile = artifacts.load_artifact(bid, "intake_profile")
    profile["capability_ladder"] = {"L3": {"granted": True}}
    artifacts.save_artifact(bid, "intake_profile", profile)

    out = coverage.build_coverage(bid)
    assert not out["ok"]
    assert any("口头授予不算授予" in p for p in out["problems"])
