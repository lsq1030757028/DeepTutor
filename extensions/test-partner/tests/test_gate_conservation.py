# -*- coding: utf-8 -*-
"""E19 守恒闸判据（设计稿 §6.2）。

除了"闸能拦"，本文件还单验两件容易漏的：
  1. **躲开闸的那条路上有账**（L3 未授予时仍出显式 gap）；
  2. **顺序判据不是形式主义**（快照排在写之后会让 delta 恒为 0 而假绿）。
"""
from __future__ import annotations

from server.journey.gates import conservation


def _case(case_id="q/R1-C001", *, writes=True, actions=None, layers=None):
    return {
        "case_id": case_id,
        "side_effects": {"writes": writes, "cleanup": "x", "authorization": "y"},
        "automation": {"admissible": True,
                       "recipe": {"track": "api", "actions": actions or []}},
        "assertion_layers": layers if layers is not None else {
            "api": {"assertions": ["a"], "required_evidence": ["http_transcript"]},
            "db": {"assertions": ["守恒：角色总数 +1"], "required_evidence": ["db_snapshot"]},
        },
    }


GOOD_ACTIONS = [
    {"op": "db_snapshot", "metric": "roles", "sql": "SELECT count(*) FROM character.custom_characters"},
    {"op": "request", "method": "POST", "path": "/api/roles"},
    {"op": "expect_status", "status": 200},
    {"op": "expect_db_delta", "metric": "roles", "delta": 1},
]


def _subs(problems):
    return sorted({p["sub"] for p in problems})


# ── 放行面 ────────────────────────────────────────────────────────────────
def test_well_formed_write_case_passes():
    assert conservation.check_case(_case(actions=GOOD_ACTIONS), l3_granted=True) == []


def test_read_only_case_is_never_judged():
    """非写用例恒过 —— 守恒闸只管写。"""
    assert conservation.check_case(
        _case(writes=False, actions=[{"op": "request", "method": "GET", "path": "/x"}],
              layers={"api": {"assertions": ["a"], "required_evidence": ["http_transcript"]}}),
        l3_granted=True) == []


# ── 拦截面 ────────────────────────────────────────────────────────────────
def test_status_only_write_case_is_rejected():
    """只断 200 的写用例 —— 本闸存在的主要理由。"""
    problems = conservation.check_case(_case(actions=[
        {"op": "request", "method": "POST", "path": "/api/roles"},
        {"op": "expect_status", "status": 200},
    ], layers={"api": {"assertions": ["a"], "required_evidence": ["http_transcript"]}}),
        l3_granted=True)
    assert "missing_conservation" in _subs(problems)


def test_writes_declared_but_no_mutating_action():
    problems = conservation.check_case(_case(actions=[
        {"op": "db_snapshot", "metric": "roles", "sql": "SELECT count(*) FROM t"},
        {"op": "request", "method": "GET", "path": "/api/roles"},
        {"op": "expect_db_delta", "metric": "roles", "delta": 0},
    ]), l3_granted=True)
    assert "no_mutation" in _subs(problems)


def test_snapshot_and_delta_on_different_metrics():
    """快照 A 却对 B 求差：两条断言各自都在，配对不成立。"""
    problems = conservation.check_case(_case(actions=[
        {"op": "db_snapshot", "metric": "roles", "sql": "SELECT count(*) FROM t"},
        {"op": "request", "method": "POST", "path": "/api/roles"},
        {"op": "expect_db_delta", "metric": "voices", "delta": 1},
    ]), l3_granted=True)
    assert "unpaired_metric" in _subs(problems)


def test_snapshot_after_mutation_is_rejected():
    """顺序判据：快照排在写之后，delta 恒为 0，而 `delta: 0` 的断言**会绿**。

    这是本闸自己最容易被绕过的形状 —— 全部 op 都在、metric 也配对，
    只有顺序错了，而顺序错的后果正好是"闸看起来过了"。
    """
    problems = conservation.check_case(_case(actions=[
        {"op": "request", "method": "POST", "path": "/api/roles"},
        {"op": "db_snapshot", "metric": "roles", "sql": "SELECT count(*) FROM t"},
        {"op": "expect_db_delta", "metric": "roles", "delta": 0},
    ]), l3_granted=True)
    assert "order" in _subs(problems)


def test_delta_before_mutation_is_rejected():
    problems = conservation.check_case(_case(actions=[
        {"op": "db_snapshot", "metric": "roles", "sql": "SELECT count(*) FROM t"},
        {"op": "expect_db_delta", "metric": "roles", "delta": 1},
        {"op": "request", "method": "POST", "path": "/api/roles"},
    ]), l3_granted=True)
    assert "order" in _subs(problems)


def test_missing_db_assertion_layer():
    problems = conservation.check_case(
        _case(actions=GOOD_ACTIONS,
              layers={"api": {"assertions": ["a"], "required_evidence": ["http_transcript"]}}),
        l3_granted=True)
    assert "no_db_layer" in _subs(problems)


def test_ui_track_click_counts_as_mutation():
    """UI 轨的写靠 click。判据不能只认 HTTP 方法。"""
    problems = conservation.check_case(_case(actions=[
        {"op": "db_snapshot", "metric": "roles", "sql": "SELECT count(*) FROM t"},
        {"op": "click", "selector": "#save"},
        {"op": "expect_db_delta", "metric": "roles", "delta": 1},
    ]), l3_granted=True)
    assert problems == []


def test_get_request_is_not_a_mutation():
    """GET 不算写 —— 否则每条读用例都会被当成写。"""
    problems = conservation.check_case(_case(actions=[
        {"op": "db_snapshot", "metric": "roles", "sql": "SELECT count(*) FROM t"},
        {"op": "request", "method": "GET", "path": "/x"},
        {"op": "expect_db_delta", "metric": "roles", "delta": 0},
    ]), l3_granted=True)
    assert "no_mutation" in _subs(problems)


# ── 纪律④：躲开闸的那条路上有没有账 ───────────────────────────────────────
def test_l3_ungranted_skips_the_gate():
    """没只读库时不判 E19 —— 否则等于要求用户拿不到的东西。"""
    problems = conservation.check_case(_case(actions=[
        {"op": "request", "method": "POST", "path": "/api/roles"},
    ]), l3_granted=False)
    assert problems == []


def test_l3_ungranted_still_records_named_gap():
    """**但躲得掉闸躲不掉账**：每条写用例在覆盖图上挂一个有名有姓的缺口。

    这条单列，不靠上一条顺带 —— 只验"未授予时不判"与"未授予时既不判也不记"
    是区分不开的，而后者正是把闸变成后门的那个版本。
    """
    caseset = {"cases": [
        _case("q/R1-C001", actions=[{"op": "request", "method": "POST", "path": "/x"}]),
        _case("q/R1-C002", writes=False),
    ]}
    res = conservation.check_caseset(caseset, l3_granted=False)
    assert res["ok"] is True
    gaps = res["declared_gaps"]
    assert [g["case_id"] for g in gaps] == ["q/R1-C001"], "只有写用例挂缺口"
    assert gaps[0]["gap_reason"] == "capability:L3 未授予"


def test_gap_reason_prefix_matches_capability_ladder():
    """前缀必须与 capability_ladder 一字不差 —— 覆盖卡按它识别。

    这条防的是"两处各写各的"：前缀在这里改一个字，交互稿 §6f 的覆盖卡
    就认不出来，缺口会退化成一个空格，而两边的测试各自都是绿的。
    """
    from server.journey.gates import capability_ladder
    ladder_reason = next(n["gap_reason"] for n in capability_ladder.gap_notes({})
                         if n["level"] == "L3")
    caseset = {"cases": [_case(actions=[{"op": "request", "method": "POST", "path": "/x"}])]}
    ours = conservation.check_caseset(caseset, l3_granted=False)["declared_gaps"][0]
    assert ours["gap_reason"] == ladder_reason


def test_l3_granted_produces_no_gap_notes():
    caseset = {"cases": [_case(actions=GOOD_ACTIONS)]}
    res = conservation.check_caseset(caseset, l3_granted=True)
    assert res["ok"] is True and res["declared_gaps"] == []
    assert res["write_cases"] == ["q/R1-C001"]


# ── 纪律④：这道闸到底作用到了谁 —— 走真 compile-gate，不只测纯函数 ─────────
#
# 本线已五例栽在「机制建成了但没作用到任何人」：写入无人读取的键、测试落在闸
# 够不到的目录、文件级扣除顺带豁免真红……所以纯函数全绿之后必须再验一次
# **它真的接在编译链上**，且拒绝时零产物落盘。

import os  # noqa: E402

from server.journey import artifacts, compile_bundle  # noqa: E402

from tests.test_journey_exec import (  # noqa: E402,F401 - fixtures 经 import 生效
    api_case, build_batch, store, target)


def _grant_l3(batch_id):
    """把 L3 标成已授予（带 evidence，否则能力锁按未授予处理）。"""
    profile = artifacts.load_artifact(batch_id, "intake_profile")
    profile["capability_ladder"] = {
        "L3": {"granted": True, "evidence": "只读库连接收据 runs/x/db-probe.json"}}
    artifacts.save_artifact(batch_id, "intake_profile", profile)


def test_compile_blocks_a_status_only_write_case_when_l3_is_granted(store, target):
    """有只读库却只断 200 的写用例 → 拒编译，且 bundle 目录**根本不出现**。

    判「目录不存在」而非「目录是空的」：闸排在 mkdir 之前才有这个性质。
    """
    bid = build_batch(store, target, [api_case("d1", writes=True)])
    _grant_l3(bid)
    bundle_dir = os.path.join(store.batch_dir(bid), "bundle")

    r = compile_bundle.compile_bundle(bid)

    assert not r["ok"]
    assert r["gate"] == "compile-gate#1c-conservation"
    assert any("E19.missing_conservation" in p for p in r["problems"]), r["problems"]
    assert not os.path.exists(bundle_dir), "拒编译却留下了 bundle 目录"


def test_compile_passes_the_same_write_case_when_l3_is_absent(store, target):
    """同一份 caseset，L3 未授予时放行 —— 证明拦它的确实是 L3 那个条件，

    而不是这条用例碰巧还有别的毛病。（一条只在单侧验过的条件判据，
    分不清「闸生效」与「用例本来就编不过」。）
    """
    bid = build_batch(store, target, [api_case("d1", writes=True)])
    r = compile_bundle.compile_bundle(bid)
    assert r["ok"], r
    assert r["manifest"]["capability_l3_granted"] is False
    gaps = r["manifest"]["conservation_declared_gaps"]
    assert [g["gap_reason"] for g in gaps] == ["capability:L3 未授予"]


def test_compile_passes_a_write_case_carrying_conservation(store, target):
    """加了闸不能把好人也拦了：带守恒配方的写用例必须编得过。"""
    case = api_case("d1", writes=True, extra_actions=[
        {"op": "expect_db_delta", "metric": "roles", "delta": 1}])
    case["automation"]["recipe"]["actions"] = (
        [{"op": "db_snapshot", "metric": "roles", "sql": "SELECT count(*) FROM t"},
         {"op": "request", "method": "POST", "path": "/api/ping"},
         {"op": "expect_status", "status": 200},
         {"op": "expect_db_delta", "metric": "roles", "delta": 1}])
    case["assertion_layers"]["db"] = {
        "assertions": ["守恒：角色总数 +1"], "required_evidence": ["db_snapshot"]}
    bid = build_batch(store, target, [case])
    _grant_l3(bid)

    r = compile_bundle.compile_bundle(bid)

    assert r["ok"], r
    assert r["manifest"]["capability_l3_granted"] is True
    assert r["manifest"]["conservation_declared_gaps"] == []


def test_bundle_embeds_the_db_module(store, target):
    """bundle 自包含：`_dbro.py` 必须被逐字嵌入。

    漏嵌的后果不是"db op 不能用"，是 `_runtime.py` 顶部 import 失败 →
    整份 bundle 一条都收集不到。所以这条同时也是那个后果的回归。
    """
    bid = build_batch(store, target, [api_case("d1")])
    r = compile_bundle.compile_bundle(bid)
    assert r["ok"], r
    assert os.path.isfile(os.path.join(r["bundle_dir"], "_dbro.py"))
    assert "_dbro.py" in r["manifest"]["embedded_sources"]
