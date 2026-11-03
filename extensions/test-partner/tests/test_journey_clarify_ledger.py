# -*- coding: utf-8 -*-
"""澄清收敛与 skip 的服务端半边（批次 2 / AC10-AC13）。

## 这组测试守什么

「同一疑点同批次不二次弹出」（AC11）与「skip 后不再发问」（AC13）的判断发生在
模型侧，服务端看不见 ask_user。但模型守得住的前提是**有地方查得到哪些疑点已经
问过**——business_frame 的 `question_ledger` 就是那个地方。这组测试守的是：

1. 账本是机械派生的，不吃模型自报；
2. 结构糊弄不过去（缺推荐、assumed 答案另写一个值、answered 没答案）；
3. 重新澄清不许把已处理的疑点静默丢掉（丢了 = 账本失忆 = F-3 三轮同题的复发通道）；
4. 分析产物的 assumed 标记从账本投影，可数（AC10 的证据口径）。
"""
import pytest

from server.journey import analyze, artifacts, clarify, ingest

from tests.test_journey_exec import (  # noqa: F401 - fixtures 经 import 生效
    GOOD_ANALYSIS, GOOD_FACTS, store, target)

OWNER = "unit-test-owner"


def _batch(store, target):
    with artifacts.trusted_owner(OWNER):
        r = ingest.ingest("澄清账本自测", target, source_kind="requirement_doc",
                          source_ref="local", requirement_text="需求正文",
                          tier="standard", tier_confirmed_via="test", owner=OWNER)
        assert r["ok"], r
        return r["batch_id"]


RULES = [{"rule_id": "R1", "statement": "接口规则", "source_quote": "q"}]


def _clarify(bid, clarifications, rules=None):
    with artifacts.trusted_owner(OWNER):
        return clarify.clarify(bid, rules=rules or RULES,
                               clarifications=clarifications,
                               confirmed_facts_md=GOOD_FACTS)


def _q(qid, status="answered", *, question="边界值含不含 0？",
       recommended="含 0", answer=None, skipped=False):
    if answer is None:
        answer = "用户答复" if status == "answered" else recommended
    return {"question_id": qid, "question": question, "recommended": recommended,
            "status": status, "answer": answer, "skipped": skipped}


# ── 账本派生 ────────────────────────────────────────────────────────────────


def test_ledger_is_derived_mechanically_from_clarifications(store, target):
    bid = _batch(store, target)
    out = _clarify(bid, [
        _q("Q1"),
        _q("Q2", "assumed"),
        _q("Q3", "assumed", skipped=True),
    ])
    assert out["ok"], out
    frame = artifacts.load_artifact(bid, "business_frame")
    assert frame["question_ledger"] == {
        "Q1": {"status": "answered", "skipped": False},
        "Q2": {"status": "assumed", "skipped": False},
        "Q3": {"status": "assumed", "skipped": True},
    }


def test_skipped_and_merely_unanswered_are_distinguishable(store, target):
    """「用户看过并跳过」与「用户没答」在账本上必须可分（AC13）。"""
    bid = _batch(store, target)
    assert _clarify(bid, [_q("Q1", "assumed"),
                          _q("Q2", "assumed", skipped=True)])["ok"]
    ledger = artifacts.load_artifact(bid, "business_frame")["question_ledger"]
    assert ledger["Q1"]["skipped"] is False
    assert ledger["Q2"]["skipped"] is True


def test_empty_clarifications_stay_legal(store, target):
    """没有疑点的批次不该被新契约误伤。"""
    bid = _batch(store, target)
    out = _clarify(bid, [])
    assert out["ok"], out
    assert artifacts.load_artifact(bid, "business_frame")["question_ledger"] == {}


# ── 结构校验：糊弄不过去 ────────────────────────────────────────────────────


def test_duplicate_question_id_is_rejected(store, target):
    bid = _batch(store, target)
    out = _clarify(bid, [_q("Q1"), _q("Q1", "assumed")])
    assert not out["ok"]
    assert any("question_id 重复" in p for p in out["problems"])


def test_missing_recommendation_is_rejected(store, target):
    """攒批卡每条必须带推荐（AC10）——没有推荐就没有「未答按推荐」这条路。"""
    bid = _batch(store, target)
    out = _clarify(bid, [{"question_id": "Q1", "question": "含 0 吗",
                          "status": "assumed", "answer": ""}])
    assert not out["ok"]
    assert any("recommended" in p for p in out["problems"])


def test_assumed_answer_must_equal_the_recommendation(store, target):
    """assumed 的含义就是「按推荐处理」——答案另写一个值是自相矛盾。"""
    bid = _batch(store, target)
    out = _clarify(bid, [_q("Q1", "assumed", answer="另一个值")])
    assert not out["ok"]
    assert any("必须等于推荐值" in p for p in out["problems"])


def test_answered_without_a_real_answer_is_rejected(store, target):
    bid = _batch(store, target)
    out = _clarify(bid, [_q("Q1", answer="  ")])
    assert not out["ok"]
    assert any("必须带用户真实答案" in p for p in out["problems"])


def test_answered_and_skipped_are_mutually_exclusive(store, target):
    bid = _batch(store, target)
    out = _clarify(bid, [_q("Q1", skipped=True)])
    assert not out["ok"]
    assert any("互斥" in p for p in out["problems"])


def test_bogus_status_is_rejected(store, target):
    bid = _batch(store, target)
    out = _clarify(bid, [_q("Q1", "resolved")])
    assert not out["ok"]
    assert any("status" in p for p in out["problems"])


# ── 重新澄清：账本不失忆 ────────────────────────────────────────────────────


def test_reclarify_must_not_silently_drop_a_handled_question(store, target):
    """已处理的疑点在重新澄清时静默消失 = 同一疑点可以再问一遍而没人发现。"""
    bid = _batch(store, target)
    assert _clarify(bid, [_q("Q1"), _q("Q2", "assumed", skipped=True)])["ok"]
    out = _clarify(bid, [_q("Q1")])   # Q2 被丢了
    assert not out["ok"]
    assert any("Q2" in p and "静默" in p for p in out["problems"])


def test_reclarify_keeping_all_prior_questions_is_legal(store, target):
    bid = _batch(store, target)
    assert _clarify(bid, [_q("Q1"), _q("Q2", "assumed", skipped=True)])["ok"]
    # 复用原状态 + 新增一个疑点：合法（ADR-3 第 3 条「已答项按新档位复用」的形态）
    out = _clarify(bid, [_q("Q1"), _q("Q2", "assumed", skipped=True), _q("Q3", "assumed")])
    assert out["ok"], out
    ledger = artifacts.load_artifact(bid, "business_frame")["question_ledger"]
    assert set(ledger) == {"Q1", "Q2", "Q3"}


def test_reclarify_may_upgrade_an_assumed_question_to_answered(store, target):
    """用户后来主动答了一个此前 assumed 的疑点——升级合法，账本跟着走。

    禁的是「系统再去问」，不是「用户主动说」。
    """
    bid = _batch(store, target)
    assert _clarify(bid, [_q("Q1", "assumed")])["ok"]
    out = _clarify(bid, [_q("Q1", "answered")])
    assert out["ok"], out
    assert artifacts.load_artifact(
        bid, "business_frame")["question_ledger"]["Q1"]["status"] == "answered"


# ── 分析产物投影（AC10 证据口径）────────────────────────────────────────────


def test_analysis_projects_assumed_marks_from_the_ledger(store, target):
    """assumed 标记从账本机械投影——模型自报"我标了"不算数，账本里有才算。"""
    bid = _batch(store, target)
    assert _clarify(bid, [
        _q("Q1"),
        _q("Q2", "assumed"),
        _q("Q3", "assumed", skipped=True),
    ])["ok"]
    with artifacts.trusted_owner(OWNER):
        out = analyze.analyze(bid, example_map=[
            {"rule_id": "R1", "charter": "接口", "examples": [{"id": "e1"}]}],
            analysis_md=GOOD_ANALYSIS)
    assert out["ok"], out
    analysis = artifacts.load_artifact(bid, "test_analysis")
    assert analysis["assumed_clarifications"] == ["Q2", "Q3"]
    assert analysis["skipped_clarifications"] == ["Q3"]
    # 可数：AC10 的证据口径就是这两个列表的长度
    assert len(analysis["assumed_clarifications"]) == 2
