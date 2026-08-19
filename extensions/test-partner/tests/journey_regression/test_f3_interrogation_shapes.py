# -*- coding: utf-8 -*-
"""F-3「审讯感」三个症状形态的防复发回放（AC11 / AC13 / AC14）。

F-3 原始证据：用户「拉一下需求」在测试模式 158 秒三轮 ask_user 零工具调用，
同一疑点被问了三轮、skip 被无视。这里把三个症状形态逐一打在服务端契约上——
契约不拒绝，症状就有了复发通道。
"""
import pytest

from server.journey import analyze, artifacts, clarify, ingest

from tests.test_journey_exec import (  # noqa: F401 - fixtures 经 import 生效
    GOOD_ANALYSIS, GOOD_FACTS, store, target)

OWNER = "unit-test-owner"
RULES = [{"rule_id": "R1", "statement": "接口规则", "source_quote": "q"}]


def _batch(store, target):
    with artifacts.trusted_owner(OWNER):
        r = ingest.ingest("F-3 回放", target, source_kind="requirement_doc",
                          source_ref="local", requirement_text="需求正文",
                          tier="standard", tier_confirmed_via="test", owner=OWNER)
        assert r["ok"], r
        return r["batch_id"]


def _clarify(bid, clarifications):
    with artifacts.trusted_owner(OWNER):
        return clarify.clarify(bid, rules=RULES, clarifications=clarifications,
                               confirmed_facts_md=GOOD_FACTS)


def _q(qid, status="assumed", *, recommended="含 0", answer=None, skipped=False):
    return {"question_id": qid, "question": "边界值含不含 0？",
            "recommended": recommended, "status": status,
            "answer": answer if answer is not None else (
                "用户答复" if status == "answered" else recommended),
            "skipped": skipped}


def test_ac11_the_same_question_cannot_appear_twice(store, target):
    """AC11「三轮同题」：同一疑点 id 在一卡内出现两次 → 契约拒绝。"""
    bid = _batch(store, target)
    out = _clarify(bid, [_q("Q1"), _q("Q1")])
    assert not out["ok"]
    assert any("question_id 重复" in p for p in out["problems"])


def test_ac11_a_handled_question_cannot_silently_vanish(store, target):
    """AC11 的另一半：已处理疑点在重澄清时静默消失 = 可以再问而没人发现。"""
    bid = _batch(store, target)
    assert _clarify(bid, [_q("Q1", "answered")])["ok"]
    out = _clarify(bid, [_q("Q2")])
    assert not out["ok"]
    assert any("Q1" in p and "静默" in p for p in out["problems"])


def test_ac13_skip_is_recorded_distinguishably_and_survives(store, target):
    """AC13 skip 语义：跳过 ≠ 没答，且 skip 状态跨重澄清存续。"""
    bid = _batch(store, target)
    assert _clarify(bid, [_q("Q1", skipped=True), _q("Q2")])["ok"]
    ledger = artifacts.load_artifact(bid, "business_frame")["question_ledger"]
    assert ledger["Q1"] == {"status": "assumed", "skipped": True}
    assert ledger["Q2"] == {"status": "assumed", "skipped": False}
    # skip 的项按推荐处理并可数地标 assumed（AC13 产物判据）
    with artifacts.trusted_owner(OWNER):
        assert analyze.analyze(bid, example_map=[
            {"rule_id": "R1", "charter": "接口", "examples": [{"id": "e1"}]}],
            analysis_md=GOOD_ANALYSIS)["ok"]
    analysis = artifacts.load_artifact(bid, "test_analysis")
    assert "Q1" in analysis["skipped_clarifications"]
    assert "Q1" in analysis["assumed_clarifications"]


def test_ac14_one_batched_card_shape_is_the_contract(store, target):
    """AC14 clarify 段攒批：一卡 N 条、每条必带推荐——契约层的那半边。

    「该段用户作答轮次 ≤1」是行为判据（UAT 证）；这里钉死它的机械前提：
    没有推荐值就没有「未答按推荐处理」这条路，攒批卡就立不起来。
    """
    bid = _batch(store, target)
    # 一卡三条一次落账：合法
    assert _clarify(bid, [_q("Q1", "answered"), _q("Q2"), _q("Q3")])["ok"]
    bid2 = _batch(store, target)
    # 缺推荐：拒绝（逐字段分轮追问的复发通道之一就是"这条没推荐，得单独问"）
    out = _clarify(bid2, [{"question_id": "Q1", "question": "含 0 吗",
                           "status": "assumed", "answer": ""}])
    assert not out["ok"]
    assert any("recommended" in p for p in out["problems"])
