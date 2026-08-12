# -*- coding: utf-8 -*-
"""写确认生产入口自测（0028，BB-511）。

## 补的是什么洞

`pw_runtime.py:88` 执行时查 `events.jsonl` 里该用例的 `write_confirm`，查不到就
`SKIP_WRITE_UNCONFIRMED`。而生产侧**从来没有任何地方写过这种事件**——只产出
adopt_confirm / tier_confirm / tool_call / gate_token_used 四种。
即：卡能弹、用户能答「4 条都允许」，**写用例照样被拦，用户无感知**。
与 BB-502 同形：挂载面与运行时能力面不一致，中间那段是哑的。

而当年唯一"证明写确认可用"的测试，是**手搓一条生产代码产不出的事件**再断言它生效
（`test_journey_exec.py::test_execute_write_confirmed_runs`，已改走生产入口）。
那条测试一直是绿的——它测的是一个不存在的链路。
"""
import pytest

from server.journey import artifacts, execute_run, tools
from server.journey import compile_bundle

from tests.test_journey_exec import (  # noqa: F401 - fixtures 经 import 生效
    api_case, build_batch, store, target)

TRUSTED_OWNER = "unit-test-owner"


def _confirm(**kwargs):
    return tools.write_confirm(owner=TRUSTED_OWNER, **kwargs)


def _batch_with_one_write_case(store, target):
    return build_batch(store, target, [api_case("dw", writes=True),
                                       api_case("dr")])


# ── 落账 ───────────────────────────────────────────────────────────────────


def test_confirm_writes_a_real_event_the_runtime_can_find(store, target):
    bid = _batch_with_one_write_case(store, target)
    out = _confirm(batch_id=bid, case_ids=["exectest/R1-C001"],
                              decided_by="manager", caller_surface="capability")
    assert out["ok"], out
    types = [e["type"] for e in artifacts.read_events(bid)]
    assert "write_confirm" in types
    assert execute_run.write_authorization(bid)["authorized"] == {"exectest/R1-C001"}


def test_empty_selection_is_a_legal_answer_not_an_error(store, target):
    """交互稿卡四的默认就是一条都不勾，「都跳过」是三个选项之一。

    空选要落一条**显式的**「什么都不授权」——它与「没答过」在账本上必须可分：
    前者是用户看过并拒绝了，后者是这道闸还没走。
    """
    bid = _batch_with_one_write_case(store, target)
    out = _confirm(batch_id=bid, case_ids=[], caller_surface="capability")
    assert out["ok"]
    event = next(e for e in artifacts.read_events(bid) if e["type"] == "write_confirm")
    assert event["decision"] == "authorized_none"
    assert event["declined"] == ["exectest/R1-C001"]
    assert execute_run.write_authorization(bid)["authorized"] == set()


def test_never_confirmed_and_explicitly_declined_are_distinguishable(store, target):
    """没走过闸 vs 走过并拒绝——两种状态在账本上长得不一样。"""
    bid = _batch_with_one_write_case(store, target)
    assert not [e for e in artifacts.read_events(bid) if e["type"] == "write_confirm"]
    _confirm(batch_id=bid, case_ids=[], caller_surface="capability")
    assert [e for e in artifacts.read_events(bid) if e["type"] == "write_confirm"]


# ── 拼错与勾错：判红，不静默忽略 ───────────────────────────────────────────


def test_unknown_case_id_is_red(store, target):
    """静默忽略的症状是「我明明点了允许，它还是跳过」——最难查的那一类。"""
    bid = _batch_with_one_write_case(store, target)
    out = _confirm(batch_id=bid, case_ids=["exectest/R1-C999"],
                              caller_surface="capability")
    assert not out["ok"] and out["code"] == "E_UNKNOWN_CASE"


def test_authorizing_a_read_only_case_is_red(store, target):
    bid = _batch_with_one_write_case(store, target)
    read_only = [c["case_id"] for c in
                 artifacts.load_artifact(bid, "approved_caseset")["cases"]
                 if not (c.get("side_effects") or {}).get("writes")]
    out = _confirm(batch_id=bid, case_ids=read_only[:1],
                              caller_surface="capability")
    assert not out["ok"] and out["code"] == "E_NOT_A_WRITE_CASE"


def test_confirm_before_adoption_is_red(store, target):
    """没有采纳集就没有「这批用例」，写确认无从谈起。"""
    from server.journey import ingest
    r = ingest.ingest("未采纳", target, source_kind="requirement_doc",
                      source_ref="local", requirement_text="正文",
                      tier="standard", tier_confirmed_via="test",
                      owner=TRUSTED_OWNER)
    out = _confirm(batch_id=r["batch_id"], case_ids=[],
                              caller_surface="capability")
    assert not out["ok"] and out["code"] == "E_NO_CASESET"


def test_confirm_requires_an_existing_batch(store):
    out = _confirm(batch_id="b-does-not-exist", case_ids=[],
                              caller_surface="capability")
    assert not out["ok"] and out["code"] == "E_NO_BATCH"


# ── 授权的是内容不是 id ────────────────────────────────────────────────────


def test_authorization_dies_when_the_case_content_changes(store, target):
    """设计稿 §5.2 第 2 条的落点：**写确认不因重生成而复用上一次的确认**。

    用户在卡上看到「新建一笔订单」才点的允许。用例内容换了以后 id 还一样，
    但同意的东西已经不是同一个了——旧确认必须失效。
    """
    bid = _batch_with_one_write_case(store, target)
    assert _confirm(batch_id=bid, case_ids=["exectest/R1-C001"],
                               caller_surface="capability")["ok"]
    assert execute_run.write_authorization(bid)["authorized"]

    caseset = artifacts.load_artifact(bid, "approved_caseset")
    for c in caseset["cases"]:
        if c["case_id"] == "exectest/R1-C001":
            c["source_case_digest"] = "sha256:" + "0" * 64
    artifacts.save_artifact(bid, "approved_caseset", caseset)

    auth = execute_run.write_authorization(bid)
    assert auth["authorized"] == set()
    assert any("digest 不符" in d["reason"] for d in auth["dropped"])


def test_an_event_without_digests_authorizes_nothing(store, target):
    """认不出它同意的是什么内容，就不能拿它当同意。失败方向是少给。

    这条同时挡住「绕开工具手搓一条事件」这个后门——手搓的那份没有 digests。
    """
    bid = _batch_with_one_write_case(store, target)
    artifacts.append_event(bid, {"type": "write_confirm",
                                 "case_ids": ["exectest/R1-C001"]})
    auth = execute_run.write_authorization(bid)
    assert auth["authorized"] == set()
    assert any("没记 digest" in d["reason"] for d in auth["dropped"])


def test_latest_confirmation_can_revoke_all_prior_writes(store, target):
    bid = _batch_with_one_write_case(store, target)
    assert _confirm(
        batch_id=bid, case_ids=["exectest/R1-C001"],
        caller_surface="capability")["ok"]
    assert _confirm(
        batch_id=bid, case_ids=[], caller_surface="capability")["ok"]
    assert execute_run.write_authorization(bid)["authorized"] == set()


def test_latest_confirmation_replaces_prior_subset(store, target):
    bid = build_batch(store, target, [api_case("a", writes=True),
                                      api_case("b", writes=True)])
    write_ids = [case["case_id"] for case in
                 artifacts.load_artifact(bid, "approved_caseset")["cases"]]
    assert _confirm(
        batch_id=bid, case_ids=write_ids,
        caller_surface="capability")["ok"]
    assert _confirm(
        batch_id=bid, case_ids=[write_ids[1]],
        caller_surface="capability")["ok"]
    assert execute_run.write_authorization(bid)["authorized"] == {write_ids[1]}


def test_dropped_authorizations_land_in_the_run_receipt(store, target):
    """失效必须说出口：作废后的症状与「压根没确认」一模一样（0021 红线六）。"""
    bid = _batch_with_one_write_case(store, target)
    assert compile_bundle.compile_bundle(bid)["ok"]
    artifacts.append_event(bid, {"type": "write_confirm",
                                 "case_ids": ["exectest/R1-C001"]})
    r = execute_run.execute(bid)
    assert r["ok"]
    assert r["receipt"]["write_confirm_dropped"], "作废的授权没进收据，无从区分"


# ── 端到端：卡答完，写用例真的跑得起来 ─────────────────────────────────────


def test_confirmed_write_case_actually_executes(store, target):
    """这是本文件的存在理由：**从生产入口确认，写用例真的执行**。"""
    bid = _batch_with_one_write_case(store, target)
    assert compile_bundle.compile_bundle(bid)["ok"]
    assert _confirm(batch_id=bid, case_ids=["exectest/R1-C001"],
                               decided_by="manager",
                               caller_surface="capability")["ok"]
    r = execute_run.execute(bid, case_ids=["exectest/R1-C001"])
    assert r["ok"]
    assert r["receipt"]["counts"].get("passed") == 1, r["receipt"]["counts"]


def test_unconfirmed_write_case_is_still_skipped(store, target):
    """闸没被这次改动放松：不确认照旧跳过。"""
    bid = _batch_with_one_write_case(store, target)
    assert compile_bundle.compile_bundle(bid)["ok"]
    r = execute_run.execute(bid, case_ids=["exectest/R1-C001"])
    assert r["receipt"]["counts"].get("passed") is None


# ── 调用面声明对拍 ─────────────────────────────────────────────────────────


def test_write_confirm_is_declared_as_a_gate_tool_not_a_tenth_atom():
    """它归人闸类，不进九原子。

    九原子是旅程的**业务工序**；写确认是人闸的服务端半边（记下用户的答案）。
    混进 JUDGEMENT_TOOLS 会让「九原子一个都不能少」那条断言从
    "业务工序齐不齐"退化成"工具总数对不对"——后者随便加个工具就红，
    红几次就没人看了。
    """
    assert "write_confirm" in tools.GATE_TOOLS
    assert "write_confirm" not in tools.JUDGEMENT_TOOLS
    assert "write_confirm" in tools.MCP_TOOL_NAMES


def test_write_confirm_is_not_an_idempotency_guarded_tool():
    """它刻意不过幂等闸：**重新确认是正当动作**（改主意、内容变了要重确认），
    而它的"副作用"只是往账本追加一条决策记录，不打被测系统。"""
    assert "write_confirm" not in tools.SIDE_EFFECT_TOOLS
