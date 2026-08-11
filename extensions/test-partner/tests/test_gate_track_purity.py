# -*- coding: utf-8 -*-
"""E22 轨道纯度闸自测（DoD#4b，0023 裁定 2 / 0025 §3.3）。

## 这道闸补的是什么

M1 只有 `execute_run.detect_track`——**反推**，不拦截，它自己的注释写着
「这里不负责拦，只负责如实说」。于是声明 `track="api"` 却混着 `click` 的 caseset
照常编译、照常执行，事后被反推成 "ui"：报告是对的，靶被打歪了。

## 词表为什么必须与运行时对拍（有具体漏项，不是「防止漏项」这种话）

`detect_track` 的判法是「命中手抄的 UI 清单 = ui，**否则 api**」。那个 else 是个
静默默认值。当版实测该清单已经漏了**两个具体的 op**：

- `wait_load`（`pw_runtime.CaseRunner._op_wait_load` → `page.wait_for_load_state`）
- `expect_hidden`（`_op_expect_hidden` → `page.locator(...).is_visible()`）

两者都要 `self.page`，铁定 UI 轨，而旧清单里没有它们 —— 一份只用这两个 op 的
UI 用例，反推结果是 "api"，`target_identity.track` 会说谎且无人报警。
所以本闸不再手抄：`vocabulary_gaps()` 枚举 `CaseRunner` 上全部 `_op_*` 与词表双向差集，
任何一边多出来都判红。
"""
import json
import os

import pytest

from server.journey import compile_bundle, execute_run
from server.journey.gates import track_purity as tp

from tests.test_journey_exec import (  # noqa: F401 - fixtures 经 import 生效
    api_case, build_batch, store, target)


# ── 词表与运行时对拍 ───────────────────────────────────────────────────────


def test_vocabulary_covers_the_runtime_in_both_directions():
    """两个方向都要报：运行时多出来 = 会被默认放行；词表多出来 = 在保护幻觉。"""
    gaps = tp.vocabulary_gaps()
    assert gaps["runtime_only"] == [], (
        f"运行时有这些 op 而词表没有：{gaps['runtime_only']}。"
        "它们会走 detect_track 的 else 分支被当成 API 轨**默认放行**。")
    assert gaps["vocab_only"] == [], (
        f"词表有这些 op 而运行时没有：{gaps['vocab_only']}。"
        "词表在保护一个不存在的 op，通常意味着运行时删过东西而词表没跟。")


def test_the_two_historically_missing_ops_are_now_classified_as_ui():
    """把当年漏掉的两个具体 op 钉住，别让它们再滑回去。"""
    assert tp.track_of_op("wait_load") == "ui"
    assert tp.track_of_op("expect_hidden") == "ui"


def test_execute_run_reads_the_single_source_instead_of_keeping_its_own_list():
    """`execute_run.UI_TRACK_OPS` 必须是转出的，不是第二份手抄。"""
    assert set(execute_run.UI_TRACK_OPS) == set(tp.UI_OPS)
    assert "wait_load" in execute_run.UI_TRACK_OPS
    assert "expect_hidden" in execute_run.UI_TRACK_OPS


def test_detect_track_now_catches_a_ui_case_built_only_from_the_missed_ops():
    """回归旧洞：只用 wait_load + expect_hidden 的 UI 用例，从前会被反推成 api。"""
    manifest = {"cases": [{"actions": [{"op": "wait_load"},
                                       {"op": "expect_hidden", "selector": "#x"}]}]}
    assert execute_run.detect_track(manifest) == "ui"


# ── 四条判据各一条 ─────────────────────────────────────────────────────────


def _case(track="api", actions=None, evidence=None, case_id="TC-001"):
    return {
        "case_id": case_id,
        "assertion_layers": {"api": {"assertions": ["x"],
                                     "required_evidence": evidence
                                     or ["http_transcript"]}},
        "automation": {"admissible": True,
                       "recipe": {"track": track,
                                  "actions": actions
                                  or [{"op": "request", "method": "GET", "path": "/"},
                                      {"op": "expect_status", "status": 200}]}},
    }


def test_clean_api_case_passes():
    assert tp.check_case(_case()) == []


def test_declared_api_but_ui_ops_is_blocked():
    problems = tp.check_case(_case(actions=[
        {"op": "request", "method": "GET", "path": "/"},
        {"op": "click", "selector": "#submit"}]))
    assert problems and all(p["code"] == "E22" for p in problems)
    joined = " ".join(p["problem"] for p in problems)
    assert "click" in joined


def test_unknown_op_is_red_not_silently_treated_as_api():
    """未知 op 判红——**这是本闸相对 detect_track 的关键差别**。

    默认放行的代价不是「这一条错了」，而是「以后每个新 UI op 都自动豁免」。
    """
    problems = tp.check_case(_case(actions=[{"op": "teleport", "to": "moon"}]))
    assert len(problems) == 1
    assert "不在轨道词表里" in problems[0]["problem"]
    assert "不默认当 API 轨放行" in problems[0]["problem"]


def test_api_case_requiring_ui_only_evidence_is_blocked():
    problems = tp.check_case(_case(evidence=["http_transcript", "playwright_trace"]))
    assert len(problems) == 1
    assert "playwright_trace" in problems[0]["problem"]
    assert "undecidable" in problems[0]["problem"]


def test_screenshot_on_api_case_is_blocked_too():
    problems = tp.check_case(_case(evidence=["screenshot"]))
    assert len(problems) == 1


def test_ui_case_may_require_trace():
    """反向不拦：UI 轨要 trace 是正当的，闸不许把它也拦了。"""
    ui = _case(track="ui",
               actions=[{"op": "goto", "url": "/"},
                        {"op": "expect_visible", "selector": "#x"}],
               evidence=["playwright_trace"])
    assert tp.check_case(ui) == []


def test_mixed_tracks_in_one_caseset_is_blocked():
    caseset = {"cases": [
        _case(case_id="TC-001"),
        _case(case_id="TC-002", track="ui",
              actions=[{"op": "goto", "url": "/"}],
              evidence=["playwright_trace"])]}
    out = tp.check_caseset(caseset)
    assert not out["ok"]
    assert any("混着两条轨" in p["problem"] for p in out["problems"])


def test_mixed_tracks_inside_one_case_is_blocked():
    problems = tp.check_case(_case(track="ui", actions=[
        {"op": "goto", "url": "/"},
        {"op": "request", "method": "GET", "path": "/"}]))
    assert problems
    assert any("两条轨的 op 并存" in p["problem"] or "隐含" in p["problem"]
               for p in problems)


def test_inadmissible_cases_are_checked_too():
    """不可编译的用例照样查：今天不编译，明天被采纳就编译，越轨是生成侧写下的。"""
    case = _case(actions=[{"op": "click", "selector": "#x"}])
    case["automation"]["admissible"] = False
    assert tp.check_case(case)


# ── 接进 compile-gate：拒绝时零产物落盘 ────────────────────────────────────


def test_compile_refuses_a_cross_track_caseset_and_leaves_nothing_on_disk(
        store, target):
    """越轨 caseset 拒编译，且**bundle 目录根本不该出现**。

    判据选「目录不存在」而不是「目录是空的」：闸排在 mkdir 之前才有这个性质，
    排在之后就只能靠失败路径记得清理——而那正是 fail-closed 最常破的地方。
    """
    dirty = api_case("d1")
    dirty["automation"]["recipe"]["actions"].append(
        {"op": "click", "selector": "#submit"})
    bid = build_batch(store, target, [dirty])
    bundle_dir = os.path.join(store.batch_dir(bid), "bundle")

    r = compile_bundle.compile_bundle(bid)

    assert not r["ok"]
    assert r["gate"] == "compile-gate#1b-track-purity"
    assert any("E22" in p for p in r["problems"])
    assert not os.path.exists(bundle_dir), "拒编译却留下了 bundle 目录"


def test_compile_refuses_api_case_demanding_a_trace(store, target):
    bad = api_case("d1")
    bad["assertion_layers"]["api"]["required_evidence"] = [
        "http_transcript", "playwright_trace"]
    bid = build_batch(store, target, [bad])
    r = compile_bundle.compile_bundle(bid)
    assert not r["ok"] and r["gate"] == "compile-gate#1b-track-purity"


def test_compile_still_passes_for_a_clean_api_caseset(store, target):
    """加了一道闸不能把好人也拦了——正例必须同批验。"""
    bid = build_batch(store, target, [api_case("d1"), api_case("d2")])
    r = compile_bundle.compile_bundle(bid)
    assert r["ok"], r
    with open(os.path.join(r["bundle_dir"], "bundle.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert {m["track"] for m in manifest["cases"]} == {"api"}
    # DoD#4b 的原文判据：编译产物里 UI 轨 op 计数 == 0。这里按 bundle 真实内容数，
    # 不按声明数——声明是被查的对象，拿它当判据就是自证。
    with open(os.path.join(r["bundle_dir"], "test_cases.py"), encoding="utf-8") as fh:
        generated = fh.read()
    assert [op for op in sorted(tp.UI_OPS) if f"'op': '{op}'" in generated] == []


@pytest.mark.parametrize("op", sorted(tp.UI_OPS))
def test_every_ui_op_is_refused_on_an_api_track_case(op):
    """逐个 UI op 都试一遍，不抽样。

    抽样在这里没有意义：闸的价值恰恰在于「哪一个都拦得住」，
    而漏的那个一定是没被抽到的那个。
    """
    problems = tp.check_case(_case(actions=[
        {"op": "request", "method": "GET", "path": "/"}, {"op": op}]))
    assert problems, f"UI op {op!r} 在 api 轨用例里没被拦下"
