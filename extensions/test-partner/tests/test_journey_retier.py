# -*- coding: utf-8 -*-
"""改档与回头路（批次 3 / ADR-3 / AC5、AC17）。

## 这组测试守什么

0028 §二禁改档的理由是「中途改档使下游产物档位前提失效」。ADR-3 不否认前提，
把「失效 → 重走」自动化。所以这里守的全部是**账本一致性**：

1. 作废范围恰好三格（intake_profile 保留、身份不变）；
2. 已答澄清项跨改档不失忆（question_ledger 随事件保存，clarify 接续）；
3. 失败整体回退，不留半新半旧（AC5 的注入判据）；
4. 采纳后改档判 E_TIER_LOCKED（0028 语义保留）；
5. 换需求 = 新批次 + 双向替代事件，旧批次产物一个不删（AC17）。
"""
import os

import pytest

from server.journey import analyze, artifacts, clarify, ingest, retier

from tests.test_journey_exec import (  # noqa: F401 - fixtures 经 import 生效
    GOOD_ANALYSIS, GOOD_FACTS, api_case, build_batch, store, target)

OWNER = "unit-test-owner"

RULES = [{"rule_id": "R1", "statement": "接口规则", "source_quote": "q"}]


def _batch(store, target, *, tier="standard"):
    with artifacts.trusted_owner(OWNER):
        r = ingest.ingest("改档自测", target, source_kind="requirement_doc",
                          source_ref="local", requirement_text="需求正文",
                          tier=tier, tier_confirmed_via="test", owner=OWNER)
        assert r["ok"], r
        return r["batch_id"]


def _clarified(store, target):
    bid = _batch(store, target)
    with artifacts.trusted_owner(OWNER):
        assert clarify.clarify(bid, rules=RULES, clarifications=[
            {"question_id": "Q1", "question": "含 0 吗", "recommended": "含",
             "status": "answered", "answer": "含 0", "skipped": False},
            {"question_id": "Q2", "question": "并发上限", "recommended": "10",
             "status": "assumed", "answer": "10", "skipped": True},
        ], confirmed_facts_md=GOOD_FACTS)["ok"]
        assert analyze.analyze(bid, example_map=[
            {"rule_id": "R1", "charter": "接口", "examples": [{"id": "e1"}]}],
            analysis_md=GOOD_ANALYSIS)["ok"]
    return bid


def _retier(bid, new_tier="deep", via="chat_ask_user"):
    with artifacts.trusted_owner(OWNER):
        return retier.change_tier(bid, new_tier=new_tier, via=via)


# ── 作废范围与身份保留（AC5）────────────────────────────────────────────────


def test_change_tier_invalidates_exactly_the_three_judgement_artifacts(store, target):
    bid = _clarified(store, target)
    before = artifacts.load_artifact(bid, "intake_profile")
    out = _retier(bid)
    assert out["ok"], out
    assert out["prior_tier"] == "standard" and out["tier"] == "deep"
    assert set(out["invalidated"]) == {"business_frame", "test_analysis"}
    for kind in ("business_frame", "test_analysis", "case_draft"):
        assert not artifacts.has_artifact(bid, kind), kind
    after = artifacts.load_artifact(bid, "intake_profile")
    assert after["tier"] == "deep"
    assert after["tier_confirmed_via"] == "chat_ask_user"
    # 同一份接入：batch_id 与 created_at 都不变（AC5「intake_profile id 不变」）
    assert after["batch_id"] == before["batch_id"]
    assert after["created_at"] == before["created_at"]
    assert after["source"] == before["source"]


def test_change_tier_removes_derived_views_with_their_sources(store, target):
    """物化视图（confirmed_facts.md / test_analysis.md）不许比源产物活得久。"""
    bid = _clarified(store, target)
    d = artifacts.batch_dir(bid)
    assert os.path.isfile(os.path.join(d, "confirmed_facts.md"))
    assert os.path.isfile(os.path.join(d, "test_analysis.md"))
    assert _retier(bid)["ok"]
    assert not os.path.isfile(os.path.join(d, "confirmed_facts.md"))
    assert not os.path.isfile(os.path.join(d, "test_analysis.md"))


def test_tier_confirm_event_carries_its_source(store, target):
    """tier_confirm 增来源字段（AC23 允许的新增字段类）：intake 与 retier 可分。"""
    bid = _clarified(store, target)
    assert _retier(bid)["ok"]
    events = [e for e in artifacts.read_events(bid) if e["type"] == "tier_confirm"]
    assert [e.get("source") for e in events] == ["intake", "retier"]
    assert events[1]["prior_tier"] == "standard"
    assert events[1]["tier"] == "deep"
    assert events[1]["via"] == "chat_ask_user"


# ── 已答澄清项跨改档不失忆（ADR-3 第 3 条）──────────────────────────────────


def test_answered_clarifications_survive_a_tier_change(store, target):
    bid = _clarified(store, target)
    out = _retier(bid)
    assert out["ok"]
    assert out["preserved_question_ledger"] == {
        "Q1": {"status": "answered", "skipped": False},
        "Q2": {"status": "assumed", "skipped": True},
    }
    assert [c["question_id"] for c in out["preserved_clarifications"]] == ["Q1", "Q2"]


def test_reclarify_after_tier_change_must_not_drop_a_handled_question(store, target):
    """改档不是让疑点账本失忆的后门：重澄清丢掉已跳过的 Q2 要被打回。"""
    bid = _clarified(store, target)
    assert _retier(bid)["ok"]
    with artifacts.trusted_owner(OWNER):
        out = clarify.clarify(bid, rules=RULES, clarifications=[
            {"question_id": "Q1", "question": "含 0 吗", "recommended": "含",
             "status": "answered", "answer": "含 0", "skipped": False},
        ], confirmed_facts_md=GOOD_FACTS)
    assert not out["ok"]
    assert any("Q2" in p and "静默" in p for p in out["problems"])


def test_reclarify_after_tier_change_reusing_all_answers_is_legal(store, target):
    bid = _clarified(store, target)
    assert _retier(bid)["ok"]
    with artifacts.trusted_owner(OWNER):
        out = clarify.clarify(bid, rules=RULES, clarifications=[
            {"question_id": "Q1", "question": "含 0 吗", "recommended": "含",
             "status": "answered", "answer": "含 0", "skipped": False},
            {"question_id": "Q2", "question": "并发上限", "recommended": "10",
             "status": "assumed", "answer": "10", "skipped": True},
            # 新档位引入的新疑点走攒批卡（ADR-3 第 3 条后半）
            {"question_id": "Q3", "question": "深档并发窗口", "recommended": "5",
             "status": "assumed", "answer": "5", "skipped": False},
        ], confirmed_facts_md=GOOD_FACTS)
    assert out["ok"], out


def test_two_consecutive_tier_changes_keep_the_ledger(store, target):
    """连续两次改档（business_frame 一直没重生成）也不许弄丢账本。"""
    bid = _clarified(store, target)
    assert _retier(bid, "deep")["ok"]
    out = _retier(bid, "checklist")
    assert out["ok"]
    assert set(out["preserved_question_ledger"]) == {"Q1", "Q2"}


# ── 触发窗口与失败回退 ──────────────────────────────────────────────────────


def test_change_tier_after_adoption_is_locked(store, target):
    bid = build_batch(store, target, [api_case("d1")])   # 已采纳
    out = _retier(bid)
    assert not out["ok"] and out["code"] == "E_TIER_LOCKED"
    # 锁住 = 什么都没动
    assert artifacts.load_artifact(bid, "intake_profile")["tier"] == "standard"


def test_same_tier_is_rejected(store, target):
    bid = _batch(store, target)
    out = _retier(bid, "standard")
    assert not out["ok"] and out["code"] == "E_SAME_TIER"


def test_missing_source_is_rejected(store, target):
    """改档必须带来源——账本完整性靠记录来源保证，不靠不可变（ADR-3）。"""
    bid = _batch(store, target)
    out = _retier(bid, via="")
    assert not out["ok"] and out["code"] == "E_SOURCE_REQUIRED"


def test_injected_failure_rolls_back_to_a_consistent_state(store, target):
    """AC5 注入判据：中途失败 → 无半新半旧。

    注入点选在最后一步（append_event）：那时 intake_profile 已改、三格已删——
    正是「半新半旧」风险最大的时刻。回退后一切如初。
    """
    bid = _clarified(store, target)
    frame_before = artifacts.load_artifact(bid, "business_frame")
    analysis_before = artifacts.load_artifact(bid, "test_analysis")

    real_append = artifacts.append_event

    def exploding_append(batch_id, event, **kwargs):
        if event.get("type") == "tier_confirm":
            raise OSError("disk full (injected)")
        return real_append(batch_id, event, **kwargs)

    import unittest.mock as mock
    with mock.patch.object(artifacts, "append_event", exploding_append):
        out = _retier(bid)
    assert not out["ok"] and out["code"] == "E_RETIER_ROLLED_BACK"
    assert "disk full" in out["error"]
    # 全部如初：档位、三格、派生视图
    assert artifacts.load_artifact(bid, "intake_profile")["tier"] == "standard"
    assert artifacts.load_artifact(bid, "business_frame") == frame_before
    assert artifacts.load_artifact(bid, "test_analysis") == analysis_before
    d = artifacts.batch_dir(bid)
    assert os.path.isfile(os.path.join(d, "confirmed_facts.md"))
    # 失败事件没有半条落账
    assert [e for e in artifacts.read_events(bid)
            if e["type"] == "tier_confirm" and e.get("source") == "retier"] == []


def test_bad_tier_is_rejected(store, target):
    bid = _batch(store, target)
    out = _retier(bid, "ultra")
    assert not out["ok"] and out["code"] == "E_BAD_TIER"


# ── 换需求实体（AC17）───────────────────────────────────────────────────────


def test_supersede_records_the_relation_on_both_ledgers(store, target):
    old_bid = _clarified(store, target)
    with artifacts.trusted_owner(OWNER):
        r = ingest.ingest("换一个需求", target, source_kind="requirement_doc",
                          source_ref="local", requirement_text="另一个需求正文",
                          tier="standard", tier_confirmed_via="test",
                          supersedes=old_bid, owner=OWNER)
    assert r["ok"], r
    new_bid = r["batch_id"]
    # 新批次记来源（产物 + 事件）
    assert artifacts.load_artifact(new_bid, "intake_profile")["supersedes"] == old_bid
    assert any(e["type"] == "batch_supersedes" and e["supersedes"] == old_bid
               for e in artifacts.read_events(new_bid))
    # 旧批次标被替代
    superseded = [e for e in artifacts.read_events(old_bid)
                  if e["type"] == "batch_superseded"]
    assert len(superseded) == 1 and superseded[0]["superseded_by"] == new_bid


def test_superseded_batch_keeps_every_artifact(store, target):
    """「被替代」是状态不是销毁：旧批次产物一个不删，_PREREQ 链不断（AC17）。"""
    old_bid = _clarified(store, target)
    with artifacts.trusted_owner(OWNER):
        r = ingest.ingest("换一个需求", target, source_kind="requirement_doc",
                          source_ref="local", requirement_text="另一个需求正文",
                          tier="standard", tier_confirmed_via="test",
                          supersedes=old_bid, owner=OWNER)
        assert r["ok"]
        for kind in ("intake_profile", "business_frame", "test_analysis"):
            assert artifacts.has_artifact(old_bid, kind), kind
        # 旧批次账本仍可查（stepper 不炸）
        assert artifacts.stepper(old_bid)


def test_superseding_a_ghost_batch_is_rejected(store, target):
    with artifacts.trusted_owner(OWNER):
        r = ingest.ingest("换需求", target, source_kind="requirement_doc",
                          source_ref="local", requirement_text="正文",
                          tier="standard", tier_confirmed_via="test",
                          supersedes="no-such-batch", owner=OWNER)
    assert not r["ok"] and r.get("code") == "E_NO_BATCH"


def test_change_tier_works_through_the_production_mcp_wrapper(store, target, monkeypatch):
    """独立复核 B-1 的回归钉：MCP 包装必须把 owner 传下去。

    main.py 其余 journey 包装都传了 `owner=trusted.owner`，唯独 change_tier
    初版漏了——`@_requires_trusted_owner` 从 kwargs 取 owner，于是生产面
    每一次调用都返回 E_OWNER_REQUIRED，而 call-surface 对拍与 15 条直调
    tools 层的测试全是绿的。**工具面契约必须有一条真穿 main 包装的测试。**
    """
    import server.main as main_mod
    from tests.journey_decision_helper import trusted_claims

    bid = _clarified(store, target)
    claims = trusted_claims("journey_change_tier", owner=OWNER)
    monkeypatch.setattr(main_mod, "_journey_bridge",
                        lambda tool, ctx, args: claims)
    out = main_mod.journey_change_tier(batch_id=bid, new_tier="deep",
                                       via="chat_ask_user")
    assert out["ok"], out
    assert out["tier"] == "deep"


def test_partial_event_write_is_truncated_back_on_rollback(store, target, monkeypatch):
    """独立复核 A-1：事件行写一半失败 → 回退把 events.jsonl 裁回原长。

    不裁的话残行会让该批次此后所有 read_events 抛 JSONDecodeError——
    一次失败的改档不该把整本账变成废纸。
    """
    bid = _clarified(store, target)
    events_path = os.path.join(artifacts.batch_dir(bid), "events.jsonl")
    with open(events_path, "rb") as fh:
        before = fh.read()

    real_append = artifacts.append_event

    def partial_write_then_die(batch_id, event, **kwargs):
        if event.get("type") == "tier_confirm":
            with open(events_path, "ab") as fh:
                fh.write(b'{"type": "tier_conf')   # OS 级部分写
            raise OSError("disk full mid-line (injected)")
        return real_append(batch_id, event, **kwargs)

    import unittest.mock as mock
    with mock.patch.object(artifacts, "append_event", partial_write_then_die):
        out = _retier(bid)
    assert not out["ok"] and out["code"] == "E_RETIER_ROLLED_BACK"
    with open(events_path, "rb") as fh:
        assert fh.read() == before, "残行没被裁掉"
    # 账本仍可读
    assert isinstance(artifacts.read_events(bid), list)
