# -*- coding: utf-8 -*-
"""设计侧五原子工具（ingest/clarify/analyze/draft/adopt）自测：
产物链、闸挂产物 fail-closed、任意前缀合法交付、定档分流。"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from server.journey import adopt, analyze, artifacts, clarify, draft_cases, ingest

GOOD_FACTS = """# SOT
## 被测构建
实例指纹=bysms v1.3 @127.0.0.1:8047 二进制sha256=2945c4b3 · 探测=GET / 200 · 一致?是
## doc×code 对齐
未执行。黑盒吸收线不读被测源码（决策 0013）。
## 零access降级声明
| 缺栏 | 谁来补 | 补不到的后果 |
| doc×code 对齐 | 黑盒线不补;若转白盒由 M2 白盒增强线负责 | 补不到 → "代码做了文档没说"的分支查不出来,漏需求风险留在黑盒线之外 |
"""
GOOD_ANALYSIS = """# test_analysis
## Example Map
| R点 | Charter |
|---|---|
| R1 | 登录 |
## 下游消费面盘点
| 业务对象 | 生命周期环节 | 判别用例 | 不覆盖声明(谁覆盖·后果) |
|---|---|---|---|
| 客户记录 | 获得 | d1 | — |
| 同上 | 使用 | d2 | — |
| 同上 | 同类共存 | — | 不覆盖:由 M2 多用户线负责 · 后果=多客户并存的列表错序本轮查不出,风险留在范围外 |
| 同上 | 计量结算 | — | 不覆盖:由订单金额用例承接(本批次 R3) · 后果=金额口径错这批查不出,漏到验收才暴露 |
| 同上 | 撤销退回 | — | 不覆盖:由删除用例承接(后续批次) · 后果=删除后残留本轮发现不了,风险留档 |
| 同上 | 记账对账 | — | 不覆盖:bysms 无对账面,由需求方确认 · 后果=如有隐藏台账则漏测,风险已上浮 |
"""


def make_case(draft_id, rule_id="R1", probing=False, writes=False, track="ui"):
    recipe = ({"track": "ui", "actions": [
        {"op": "goto", "path": "/mgr/sign.html"},
        {"op": "fill", "selector": "#username", "value": "{{login_username}}"},
        {"op": "click", "selector": "button[type=submit]"},
        {"op": "expect_title_contains", "text": "白月"},
    ]} if track == "ui" else {"track": "api", "actions": [
        {"op": "request", "method": "GET", "path": "/api/mgr/customers?action=list_customer"},
        {"op": "expect_status", "status": 200},
    ]})
    side = {"writes": True, "write_scope": "客户表",
            "cleanup": "还原数据库基线 db-ori-1",
            "authorization": "events#write_confirm"} if writes else {"writes": False}
    return {
        "draft_id": draft_id, "kind": "happy",
        "title": f"用例 {draft_id}",
        "source_anchor": {"rule_id": rule_id, "probing": probing,
                          **({} if probing else {"quote": "需求原文引句"})},
        "preconditions": [{"statement": "靶机已启动", "checkable": True,
                           "check_hint": "GET / 可达"}],
        "steps": ["打开登录页", "输入账密", "提交"],
        "expected": "进入管理台",
        "failure_criteria": "停留在登录页",
        "assertion_layers": {"ui": {"assertions": ["标题含白月"],
                                    "required_evidence": ["playwright_trace"]}},
        "automation": {"admissible": True, "recipe": recipe},
        "side_effects": side,
        "open_items": {"unknowns": [], "assumptions": [], "manual_touchpoints": []},
    }


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(artifacts, "BATCHES_ROOT", str(tmp_path / "batches"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", str(tmp_path / "runs"))
    return artifacts


@pytest.fixture()
def local_target():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<title>白月SMS系统 | 登录</title>".encode("utf-8"))

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def do_ingest(store, base_url, tier="standard"):
    return ingest.ingest("白月一期", base_url, source_kind="requirement_doc",
                         source_ref="https://www.byhy.net/py/django/req_1/",
                         requirement_text="管理员登录后可新增客户、创建订单并支付。",
                         tier=tier, tier_confirmed_via="manager-self-derived-pending-audit")


# ── ingest ─────────────────────────────────────────────────────────────────

def test_ingest_without_tier_returns_card_only(store, local_target):
    r = ingest.ingest("t", local_target, source_kind="requirement_doc",
                      source_ref="x", requirement_text="短需求")
    assert not r["ok"] and r["need"] == "tier_confirmation"
    assert r["card"]["recommended"] in ingest.TIERS
    assert store.list_batches() == []  # 人闸没走完 = 无产物


def test_ingest_unreachable_target_fail_closed(store):
    r = ingest.ingest("t", "http://127.0.0.1:1", source_kind="requirement_doc",
                      source_ref="x", tier="standard", tier_confirmed_via="user")
    assert not r["ok"] and store.list_batches() == []


def test_ingest_creates_batch_with_profile(store, local_target):
    r = do_ingest(store, local_target)
    assert r["ok"], r
    bid = r["batch_id"]
    profile = store.load_artifact(bid, "intake_profile")
    assert profile["tier"] == "standard"
    assert profile["target_probe"]["reachable"]
    assert profile["source"]["content_digest"].startswith("sha256:")
    assert os.path.isfile(os.path.join(store.batch_dir(bid), "requirement.txt"))
    events = store.read_events(bid)
    assert events[0]["type"] == "tier_confirm"


def test_ingest_requires_entity_and_human_source_as_a_pair(store, local_target):
    only_entity = ingest.ingest(
        "t", local_target, source_kind="requirement_doc", source_ref="x",
        requirement_text="新增客户", tier="standard", tier_confirmed_via="user",
        requirement_entity="customer")
    only_source = ingest.ingest(
        "t", local_target, source_kind="requirement_doc", source_ref="x",
        requirement_text="新增客户", tier="standard", tier_confirmed_via="user",
        requirement_entity_confirmed_via="chat_ask_user")
    assert only_entity["ok"] is False and only_source["ok"] is False
    assert store.list_batches() == []


def test_ingest_persists_human_confirmed_requirement_entity(store, local_target):
    result = ingest.ingest(
        "t", local_target, source_kind="requirement_doc", source_ref="x",
        requirement_text="新增客户", tier="standard", tier_confirmed_via="user",
        requirement_entity="customer",
        requirement_entity_confirmed_via="chat_ask_user")
    assert result["ok"], result
    profile = store.load_artifact(result["batch_id"], "intake_profile")
    assert profile["requirement_entity"] == "customer"
    assert profile["requirement_entity_confirmed_via"] == "chat_ask_user"


def test_tier_heuristic_splits():
    small = ingest.propose_tier("查一个页面标题")
    big = ingest.propose_tier("管理员新增客户、修改订单、删除药品、支付退款、"
                              "导入导出，销售员只能看自己的订单。" * 60)
    assert small["proposed_tier"] == "checklist"
    assert big["proposed_tier"] in ("standard", "deep")


# ── clarify（sot_gate 挂产物）──────────────────────────────────────────────

def test_clarify_gate_fail_closed(store, local_target):
    bid = do_ingest(store, local_target)["batch_id"]
    bad = clarify.clarify(bid, rules=[{"rule_id": "R1", "statement": "s",
                                      "source_quote": "q"}],
                          confirmed_facts_md="# SOT\n## 被测构建\n未确认。\n")
    assert not bad["ok"] and any("sot_gate" in p for p in bad["problems"])
    assert not store.has_artifact(bid, "business_frame")  # 闸不过不落盘


def test_clarify_requires_quote_unless_probing(store, local_target):
    bid = do_ingest(store, local_target)["batch_id"]
    r = clarify.clarify(bid, rules=[{"rule_id": "R1", "statement": "无出处断言"}],
                        confirmed_facts_md=GOOD_FACTS)
    assert not r["ok"] and any("source_quote" in p for p in r["problems"])
    ok = clarify.clarify(bid, rules=[{"rule_id": "R1", "statement": "探测预期",
                                     "probing": True}],
                         confirmed_facts_md=GOOD_FACTS)
    assert ok["ok"]


def test_clarify_prefix_is_legal_delivery(store, local_target):
    # 任意前缀合法交付：走到 clarify 即止，产物齐且过闸
    bid = do_ingest(store, local_target)["batch_id"]
    r = clarify.clarify(bid, rules=[{"rule_id": "R1", "statement": "登录规则",
                                    "source_quote": "输入管理员账号"}],
                        confirmed_facts_md=GOOD_FACTS)
    assert r["ok"]
    steps = {s["artifact"]: s["present"] for s in store.stepper(bid)}
    assert steps["business_frame"] and not steps["test_analysis"]


# ── analyze（downstream_gate 挂产物）───────────────────────────────────────

def chain_to_analyze(store, base_url):
    bid = do_ingest(store, base_url)["batch_id"]
    assert clarify.clarify(bid, rules=[
        {"rule_id": "R1", "statement": "登录", "source_quote": "q1"},
    ], confirmed_facts_md=GOOD_FACTS)["ok"]
    r = analyze.analyze(bid, example_map=[
        {"rule_id": "R1", "charter": "登录能力",
         "examples": [{"id": "ex1", "desc": "正确账密", "kind": "happy"}]},
    ], analysis_md=GOOD_ANALYSIS)
    assert r["ok"], r
    return bid


def test_analyze_gate_fail_closed(store, local_target):
    bid = do_ingest(store, local_target)["batch_id"]
    clarify.clarify(bid, rules=[{"rule_id": "R1", "statement": "s",
                                "source_quote": "q"}],
                    confirmed_facts_md=GOOD_FACTS)
    bad = analyze.analyze(bid, example_map=[
        {"rule_id": "R1", "charter": "c", "examples": [{"id": "e"}]}],
        analysis_md="# 没盘点\n")
    assert not bad["ok"] and not store.has_artifact(bid, "test_analysis")


def test_analyze_rejects_unknown_rule(store, local_target):
    bid = do_ingest(store, local_target)["batch_id"]
    clarify.clarify(bid, rules=[{"rule_id": "R1", "statement": "s",
                                "source_quote": "q"}],
                    confirmed_facts_md=GOOD_FACTS)
    r = analyze.analyze(bid, example_map=[
        {"rule_id": "R99", "charter": "c", "examples": [{"id": "e"}]}],
        analysis_md=GOOD_ANALYSIS)
    assert not r["ok"] and any("R99" in p for p in r["problems"])


# ── draft（覆盖族 E16-E18/W06-W07）────────────────────────────────────────

def test_draft_coverage_gap_e16(store, local_target):
    bid = chain_to_analyze(store, local_target)
    r = draft_cases.draft(bid, cases=[])
    assert not r["ok"] and any(e["code"] == "E16" for e in r["errors"])


def test_draft_unknown_rule_e17(store, local_target):
    bid = chain_to_analyze(store, local_target)
    r = draft_cases.draft(bid, cases=[make_case("d1", rule_id="R42")])
    assert not r["ok"] and any(e["code"] == "E17" for e in r["errors"])


def test_draft_recipe_required_e18(store, local_target):
    bid = chain_to_analyze(store, local_target)
    c = make_case("d1")
    c["automation"] = {"admissible": True}
    r = draft_cases.draft(bid, cases=[c])
    assert not r["ok"] and any(e["code"] == "E18" for e in r["errors"])
    c2 = make_case("d2")
    c2["automation"]["recipe"]["actions"] = [
        {"op": "goto", "path": "/x"}]  # 零断言
    r2 = draft_cases.draft(bid, cases=[c2])
    assert not r2["ok"] and any("零断言" in e["problem"] for e in r2["errors"]
                                if e["code"] == "E18")


def test_draft_uncovered_declaration_accepted(store, local_target):
    bid = chain_to_analyze(store, local_target)
    r = draft_cases.draft(bid, cases=[],
                          uncovered_rules=[{"rule_id": "R1",
                                            "reason": "由下一批次负责,不测则登录回归漏测风险留档"}])
    assert r["ok"], r["errors"]


def test_draft_probing_warning_w06(store, local_target):
    bid = chain_to_analyze(store, local_target)
    frame = store.load_artifact(bid, "business_frame")
    frame["rules"][0]["probing"] = True
    frame["rules"][0].pop("source_quote", None)
    store.save_artifact(bid, "business_frame", frame)
    r = draft_cases.draft(bid, cases=[make_case("d1", probing=True),
                                      make_case("d2", probing=True)])
    assert r["ok"]
    assert any(w["code"] == "W06" for w in r["warnings"])


def test_draft_rejects_case_that_self_downgrades_business_rule_to_probing(
        store, local_target):
    bid = chain_to_analyze(store, local_target)
    r = draft_cases.draft(bid, cases=[make_case("d1", probing=True)])
    assert not r["ok"]
    assert any(e["code"] == "E20" for e in r["errors"])


# ── adopt（schema+digest+cases_gate 挂产物）────────────────────────────────

def chain_to_draft(store, base_url, n=2):
    bid = chain_to_analyze(store, base_url)
    cases = [make_case(f"d{i+1}") for i in range(n)]
    r = draft_cases.draft(bid, cases=cases)
    assert r["ok"], r["errors"]
    return bid


def test_adopt_full_chain(store, local_target):
    bid = chain_to_draft(store, local_target)
    r = adopt.adopt(bid, selected_draft_ids=["d1", "d2"], caseset_slug="bysms",
                    confirmed_by="manager(self-derived-pending-audit)")
    assert r["ok"], r
    assert r["case_ids"] == ["bysms/R1-C001", "bysms/R1-C002"]
    caseset = store.load_artifact(bid, "approved_caseset")
    assert all(c["source_case_digest"].startswith("sha256:")
               for c in caseset["cases"])
    d = store.batch_dir(bid)
    for f in ("cases.md", "cases_exec.json", "cases_index.json"):
        assert os.path.isfile(os.path.join(d, f))
    with open(os.path.join(d, "cases_exec.json"), encoding="utf-8") as fh:
        exec_view = json.load(fh)
    assert exec_view["cases"][0]["expect"].startswith("业务:")
    events = [e["type"] for e in store.read_events(bid)]
    assert "adopt_confirm" in events


def test_adopt_probing_materializes_probe_layer(store, local_target):
    bid = chain_to_analyze(store, local_target)
    frame = store.load_artifact(bid, "business_frame")
    frame["rules"][0]["probing"] = True
    frame["rules"][0].pop("source_quote", None)
    store.save_artifact(bid, "business_frame", frame)
    drafted = draft_cases.draft(
        bid, cases=[make_case("d1", probing=True), make_case("dp", probing=True)])
    assert drafted["ok"], drafted
    r = adopt.adopt(bid, selected_draft_ids=["d1", "dp"])
    assert r["ok"], r
    with open(os.path.join(store.batch_dir(bid), "cases.md"), encoding="utf-8") as fh:
        md = fh.read()
    assert "探测:" in md


def test_adopt_empty_selection_rejected(store, local_target):
    bid = chain_to_draft(store, local_target)
    assert not adopt.adopt(bid, selected_draft_ids=[])["ok"]


def test_adopt_unknown_draft_rejected(store, local_target):
    bid = chain_to_draft(store, local_target)
    r = adopt.adopt(bid, selected_draft_ids=["d1", "nope"])
    assert not r["ok"] and any("nope" in p for p in r["problems"])


def test_readopt_same_draft_keeps_case_id_and_version(store, local_target):
    bid = chain_to_draft(store, local_target)
    first = adopt.adopt(bid, selected_draft_ids=["d1"])
    second = adopt.adopt(bid, selected_draft_ids=["d1"])
    assert first["ok"] and second["ok"]
    case = store.load_artifact(bid, "approved_caseset")["cases"][0]
    assert second["case_ids"] == first["case_ids"]
    assert case["case_version"] == 1


def test_readopt_changed_draft_keeps_id_and_increments_version(store, local_target):
    bid = chain_to_draft(store, local_target)
    first = adopt.adopt(bid, selected_draft_ids=["d1"])
    changed = make_case("d1")
    changed["expected"] = "进入管理台并展示当前用户"
    drafted = draft_cases.draft(bid, cases=[changed])
    assert drafted["ok"]
    second = adopt.adopt(bid, selected_draft_ids=["d1"])
    case = store.load_artifact(bid, "approved_caseset")["cases"][0]
    assert second["case_ids"] == first["case_ids"]
    assert case["case_version"] == 2


def test_new_draft_never_reuses_removed_case_id(store, local_target):
    bid = chain_to_draft(store, local_target, n=2)
    first = adopt.adopt(bid, selected_draft_ids=["d1", "d2"])
    drafted = draft_cases.draft(bid, cases=[make_case("d3")])
    assert drafted["ok"]
    second = adopt.adopt(bid, selected_draft_ids=["d3"])
    assert first["case_ids"] == ["bysms/R1-C001", "bysms/R1-C002"]
    assert second["case_ids"] == ["bysms/R1-C003"]


def test_chat_ask_user_is_an_approved_human_gate(store, local_target):
    bid = chain_to_draft(store, local_target, n=1)
    result = adopt.adopt(
        bid,
        selected_draft_ids=["d1"],
        adopted_via="chat_ask_user",
        confirmed_by="turn-answer",
    )
    assert result["ok"], result
