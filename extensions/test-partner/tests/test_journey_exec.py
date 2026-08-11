# -*- coding: utf-8 -*-
"""执行侧原子工具（compile/execute/project/coverage）自测：
真起本地靶（线程 HTTP 服务）走 API 轨端到端；红线逐条；闸链变异被拦（AC-B4 类2 CI 面）。
UI 轨（真 chromium）单独一条集成测试，环境缺 playwright 时跳过。
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from server.journey import (adopt, analyze, artifacts, clarify, compile_bundle,
                            coverage, draft_cases, execute_run, ingest,
                            project_verdicts)

GOOD_FACTS = """# SOT
## 被测构建
实例指纹=local-target GET / 200 · 一致?是 · 指纹在 intake_profile.target_probe
## doc×code 对齐
未执行。黑盒吸收线不读被测源码（决策 0013）。
## 零access降级声明
| 缺栏 | 谁来补 | 补不到的后果 |
| doc×code 对齐 | 黑盒线不补;转白盒由 M2 白盒增强线负责 | 补不到 → 代码做了文档没说的分支查不出来,漏需求风险留在黑盒线之外 |
"""
GOOD_ANALYSIS = """# test_analysis
## Example Map
| R点 | Charter |
|---|---|
| R1 | 接口 |
## 下游消费面盘点
| 业务对象 | 生命周期环节 | 判别用例 | 不覆盖声明(谁覆盖·后果) |
|---|---|---|---|
| 接口响应 | 获得 | d1 | — |
| 同上 | 使用 | d2 | — |
| 同上 | 同类共存 | — | 不覆盖:由 M2 并发线负责 · 后果=并发读的错序本轮查不出,风险留档 |
| 同上 | 计量结算 | — | 不覆盖:无计费面,由需求方确认 · 后果=如有隐藏计费则漏测,风险已上浮 |
| 同上 | 撤销退回 | — | 不覆盖:无撤销面,由需求方确认 · 后果=如有撤销逻辑则漏测,风险已上浮 |
| 同上 | 记账对账 | — | 不覆盖:无台账面,由需求方确认 · 后果=如有台账则漏测,风险已上浮 |
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        if self.path == "/":
            self._send(200, "<title>本地靶 | 登录</title><h1 id='hello'>你好</h1>",
                       "text/html")
        elif self.path.startswith("/api/ping"):
            self._send(200, json.dumps({"ret": 0, "data": {"n": 3}}))
        elif self.path.startswith("/api/fake200"):
            # 类3 反例素材：200 但业务未生效（ret 非 0）
            self._send(200, json.dumps({"ret": 5001, "msg": "业务失败"}))
        elif self.path.startswith("/redirect-out"):
            self._send(302, "", headers={"Location": "http://evil.example.com/"})
        elif self.path.startswith("/api/secret-echo"):
            self._send(200, json.dumps({"ret": 0, "token_echo": "s3cr3tPW999"}))
        else:
            self._send(404, json.dumps({"ret": 404}))

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def target():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(artifacts, "BATCHES_ROOT", str(tmp_path / "batches"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", str(tmp_path / "runs"))
    return artifacts


def api_case(draft_id, path="/api/ping", expect_status=200, json_path=None,
             equals=None, probing=False, writes=False, extra_actions=None):
    actions = [{"op": "request", "method": "GET", "path": path},
               {"op": "expect_status", "status": expect_status}]
    if json_path:
        actions.append({"op": "expect_json_path", "path": json_path,
                        "equals": equals})
    actions += extra_actions or []
    side = {"writes": True, "write_scope": "测试面",
            "cleanup": "无持久写,重启即清", "authorization": "events#write_confirm"} \
        if writes else {"writes": False}
    return {
        "draft_id": draft_id, "kind": "happy",
        "title": f"API {draft_id}",
        "source_anchor": {"rule_id": "R1", "probing": probing,
                          **({} if probing else {"quote": "需求原文"})},
        "preconditions": [{"statement": "靶可达", "checkable": True}],
        "steps": [f"请求 {path}", "断言状态与业务字段"],
        "expected": "接口按需求返回",
        "failure_criteria": "状态或业务字段不符",
        "assertion_layers": {"api": {"assertions": ["状态码与 ret 符合预期"],
                                     "required_evidence": ["http_transcript"]}},
        "automation": {"admissible": True,
                       "recipe": {"track": "api", "actions": actions}},
        "side_effects": side,
        "open_items": {"unknowns": [], "assumptions": [], "manual_touchpoints": []},
    }


def build_batch(store, target, cases):
    r = ingest.ingest("执行侧自测", target, source_kind="requirement_doc",
                      source_ref="local", requirement_text="接口需求正文",
                      tier="standard", tier_confirmed_via="test")
    assert r["ok"], r
    bid = r["batch_id"]
    assert clarify.clarify(bid, rules=[{"rule_id": "R1", "statement": "接口规则",
                                        "source_quote": "q"}],
                           confirmed_facts_md=GOOD_FACTS)["ok"]
    assert analyze.analyze(bid, example_map=[
        {"rule_id": "R1", "charter": "接口", "examples": [{"id": "e1"}]}],
        analysis_md=GOOD_ANALYSIS)["ok"]
    dr = draft_cases.draft(bid, cases=cases)
    assert dr["ok"], dr["errors"]
    ad = adopt.adopt(bid, selected_draft_ids=[c["draft_id"] for c in cases],
                     caseset_slug="exectest")
    assert ad["ok"], ad
    return bid


# ── compile-gate ───────────────────────────────────────────────────────────

def test_compile_produces_bundle_and_passes_gate(store, target):
    bid = build_batch(store, target, [api_case("d1"), api_case("d2", "/api/ping",
                                                              json_path="data.n",
                                                              equals=3)])
    r = compile_bundle.compile_bundle(bid)
    assert r["ok"], r
    d = r["bundle_dir"]
    for f in ("bundle.json", "test_cases.py", "conftest.py", "pytest.ini",
              "_redlines.py", "_runtime.py", "_harness.py"):
        assert os.path.isfile(os.path.join(d, f))
    with open(os.path.join(d, "bundle.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert len(manifest["cases"]) == 2
    assert all(m["source_case_digest"].startswith("sha256:")
               for m in manifest["cases"])


def test_compile_gate_blocks_digest_tamper(store, target):
    bid = build_batch(store, target, [api_case("d1"), api_case("d2")])
    # 禁反写：直接改 approved_caseset 的业务字段（模拟反写）→ digest 复算不一致
    path = store.artifact_path(bid, "approved_caseset")
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["cases"][0]["expected"] = "被派生物反写的预期"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)
    r = compile_bundle.compile_bundle(bid)
    assert not r["ok"] and any("digest 复算不一致" in p for p in r["problems"])
    assert not os.path.isdir(os.path.join(store.batch_dir(bid), "bundle"))


def test_compile_gate_credscan_blocks_planted_secret(store, target):
    bad = api_case("d1")
    bad["title"] = "内联凭据 kJ8x2Qw9zR4tYv7uB3nM5pL6sD1fG0hA"
    bid = build_batch(store, target, [bad, api_case("d2")])
    r = compile_bundle.compile_bundle(bid)
    assert not r["ok"] and any("凭据扫描" in p for p in r["problems"])


# ── execute：API 轨端到端 + 红线 ───────────────────────────────────────────

def run_chain(store, target, cases, **exec_kw):
    bid = build_batch(store, target, cases)
    assert compile_bundle.compile_bundle(bid)["ok"]
    r = execute_run.execute(bid, **exec_kw)
    assert r["ok"], r
    return bid, r


def test_execute_api_track_end_to_end(store, target):
    bid, r = run_chain(store, target,
                       [api_case("d1"), api_case("d2", json_path="data.n", equals=3)])
    rec = r["receipt"]
    assert rec["verdict"] == "PASS", rec
    assert rec["counts"] == {"passed": 2}
    assert rec["credential_scan_ok"]
    run_dir = r["run_dir"]
    assert os.path.isfile(os.path.join(run_dir, "evidence-bundle.json"))
    assert os.path.isfile(os.path.join(run_dir, "receipt.json"))
    # 收据只记 host 不记完整 URL
    assert rec["base_url_host"].startswith("127.0.0.1:")
    # 证据增量账在场
    rows = execute_run._read_results(run_dir)
    assert {row["case_id"] for row in rows} == {"exectest/R1-C001", "exectest/R1-C002"}


def test_execute_business_fail_not_fake_green(store, target):
    # 类3 反例：接口 200 但业务未生效 → 业务层断言判 FAIL，不被 retcode 假绿
    bid, r = run_chain(store, target,
                       [api_case("d1", "/api/fake200", expect_status=200,
                                 json_path="ret", equals=0),
                        api_case("d2")])
    rec = r["receipt"]
    assert rec["verdict"] == "FAIL"
    assert rec["counts"].get("failed") == 1 and rec["counts"].get("passed") == 1


def test_execute_redirect_not_followed(store, target):
    # 红线 5（API 轨）：302 不跟随——落账状态就是 302，请求没被带去别的 host
    bid, r = run_chain(store, target,
                       [api_case("d1", "/redirect-out", expect_status=302),
                        api_case("d2")])
    assert r["receipt"]["verdict"] == "PASS"


def test_execute_write_unconfirmed_skipped(store, target):
    cases = [api_case("d1"), api_case("dw", writes=True)]
    bid = build_batch(store, target, cases)
    assert compile_bundle.compile_bundle(bid)["ok"]
    r = execute_run.execute(bid)
    counts = r["receipt"]["counts"]
    assert counts.get("skipped") == 1 and counts.get("passed") == 1
    rows = execute_run._read_results(r["run_dir"])
    skip = next(x for x in rows if x["outcome"] == "skipped")
    assert skip["skip_code"] == "SKIP_WRITE_UNCONFIRMED"


def test_execute_write_confirmed_runs(store, target):
    cases = [api_case("dw", writes=True)]
    bid = build_batch(store, target, cases)
    assert compile_bundle.compile_bundle(bid)["ok"]
    # 走**生产写入口**，不再手搓事件（2026-08-11，0028）。
    # 手搓的那份当年测的是一条生产代码根本产不出的事件形态——
    # 它绿着，而真实链路上写确认压根没有写入口，卡答完照样被拦。
    from server.journey import tools as _jt
    assert _jt.write_confirm(batch_id=bid, case_ids=["exectest/R1-C001"],
                             decided_by="manager(self-derived-pending-audit)",
                             caller_surface="capability")["ok"]
    r = execute_run.execute(bid)
    assert r["receipt"]["counts"] == {"passed": 1}


def test_execute_missing_vars_skipped(store, target):
    c = api_case("d1", "/api/ping")
    c["automation"]["recipe"]["actions"][0]["path"] = "/api/ping?tok={{token}}"
    bid, r = run_chain(store, target, [c, api_case("d2")])
    rows = execute_run._read_results(r["run_dir"])
    skip = next(x for x in rows if x["outcome"] == "skipped")
    assert skip["skip_code"] == "SKIP_MISSING_VARS" and "token" in skip["skip_reason"]


def test_execute_cross_host_skipped(store, target):
    c = api_case("d1")
    c["automation"]["recipe"]["actions"][0]["path"] = "http://evil.example.com/x"
    bid, r = run_chain(store, target, [c, api_case("d2")])
    rows = execute_run._read_results(r["run_dir"])
    skip = next(x for x in rows if x["outcome"] == "skipped")
    assert skip["skip_code"] == "SKIP_CROSS_HOST"


def test_execute_sut_down_blocked(store, target):
    # 类1 素材：停服务 → BLOCK（对不可达端口执行）
    cases = [api_case("d1"), api_case("d2")]
    bid = build_batch(store, target, cases)
    assert compile_bundle.compile_bundle(bid)["ok"]
    r = execute_run.execute(bid, base_url_override="http://127.0.0.1:9")
    assert r["receipt"]["verdict"] == "BLOCK"
    assert r["receipt"]["counts"].get("blocked") == 2


def test_execute_credscan_catches_echoed_secret(store, target):
    # 靶把凭据值回显进响应体 → transcript 落盘前须被 scrub；known-secret 扫描兜底
    c = api_case("d1", "/api/secret-echo", json_path="ret", equals=0)
    bid = build_batch(store, target, [c, api_case("d2")])
    assert compile_bundle.compile_bundle(bid)["ok"]
    r = execute_run.execute(bid, variables={"login_password": "s3cr3tPW999"})
    assert r["receipt"]["credential_scan_ok"], "回显凭据未被 scrub 门拦住"
    # 变量值确实不在 run 目录任何文件里
    scan = json.load(open(os.path.join(r["run_dir"], "credscan.json"),
                          encoding="utf-8"))
    assert scan["known_hits"] == []


def test_execute_resume_skips_done(store, target):
    cases = [api_case("d1"), api_case("d2")]
    bid = build_batch(store, target, cases)
    assert compile_bundle.compile_bundle(bid)["ok"]
    r1 = execute_run.execute(bid, case_ids=["exectest/R1-C001"])
    r2 = execute_run.execute(bid, resume_run_id=r1["run_id"])
    rows = execute_run._read_results(r2["run_dir"])
    assert len([x for x in rows if x["case_id"] == "exectest/R1-C001"]) == 1
    assert {x["case_id"] for x in rows} == {"exectest/R1-C001", "exectest/R1-C002"}


def test_concurrency_slot_limit(store, target, monkeypatch):
    from server.journey import process_registry as preg
    a = preg.acquire_slot("r-20260810-aaaaaaaa", str(store.RUNS_ROOT))
    b = preg.acquire_slot("r-20260810-bbbbbbbb", str(store.RUNS_ROOT))
    c = preg.acquire_slot("r-20260810-cccccccc", str(store.RUNS_ROOT))
    assert a["ok"] and b["ok"] and not c["ok"]
    assert c["error"] == "BUSY_MAX_CONCURRENT_RUNS"
    preg.release_slot("r-20260810-aaaaaaaa")
    assert preg.acquire_slot("r-20260810-cccccccc", str(store.RUNS_ROOT))["ok"]


# ── project：闸链 + 变异被拦（AC-B4 类2 的 CI 判面）────────────────────────

def executed_run(store, target):
    return run_chain(store, target,
                     [api_case("d1"), api_case("d2", json_path="data.n", equals=3)])


def test_project_produces_verdicts_and_mechanical_pass(store, target):
    bid, r = executed_run(store, target)
    p = project_verdicts.project(r["run_id"])
    assert p["ok"], p
    v = project_verdicts.read_verdicts(r["run_id"])
    assert len(v) == 2 and all(x["verdict"] == "PASS" for x in v)
    assert all(x["projected_from"] == "evidence-bundle.json" for x in v)
    assert p["mechanical_check"]["verdict"] == "PASS"


def test_mutation_handwritten_verdict_blocked(store, target):
    bid, r = executed_run(store, target)
    assert project_verdicts.project(r["run_id"])["ok"]
    # 变异①：手写补一条 verdict（无 projected_from）
    with open(os.path.join(r["run_dir"], "verdicts.jsonl"), "a",
              encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "hand/X-C001", "claim": "手写的绿",
                             "verdict": "PASS",
                             "evidence": [{"type": "api", "ref": "nowhere.json"}]})
                 + "\n")
    from server.journey.gates import mechanical_check
    mech = mechanical_check.check_run(r["run_dir"])
    assert mech["verdict"] == "BLOCK"
    assert any("projected_from" in b for b in mech["blocking"])


def test_mutation_tampered_claim_blocked(store, target):
    bid, r = executed_run(store, target)
    assert project_verdicts.project(r["run_id"])["ok"]
    path = os.path.join(r["run_dir"], "verdicts.jsonl")
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    rows[0]["claim"] = "落账后被改成更好听的结论"
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    from server.journey.gates import mechanical_check
    mech = mechanical_check.check_run(r["run_dir"])
    assert mech["verdict"] == "BLOCK"
    assert any("[M5]" in b and "不一致" in b for b in mech["blocking"])


def test_mutation_deleted_evidence_blocked(store, target):
    bid, r = executed_run(store, target)
    assert project_verdicts.project(r["run_id"])["ok"]
    # 变异③：删证据文件
    import shutil
    victim = next(d for d in os.listdir(r["run_dir"])
                  if os.path.isdir(os.path.join(r["run_dir"], d))
                  and d.startswith("exectest"))
    shutil.rmtree(os.path.join(r["run_dir"], victim))
    from server.journey.gates import mechanical_check
    mech = mechanical_check.check_run(r["run_dir"])
    assert mech["verdict"] == "BLOCK"
    assert any("[M1]" in b for b in mech["blocking"])


def test_projection_is_deterministic_rewrite_not_append(store, target):
    bid, r = executed_run(store, target)
    p1 = project_verdicts.project(r["run_id"])
    p2 = project_verdicts.project(r["run_id"])
    assert p1["ok"] and p2["ok"]
    assert len(project_verdicts.read_verdicts(r["run_id"])) == 2


# ── coverage ───────────────────────────────────────────────────────────────

def test_coverage_ledger_built(store, target):
    bid, r = executed_run(store, target)
    assert project_verdicts.project(r["run_id"])["ok"]
    c = coverage.build_coverage(bid, r["run_id"])
    assert c["ok"], c
    ledger = store.load_artifact(bid, "coverage_ledger")
    assert ledger["summary"]["covered"] == 1
    assert ledger["summary"]["pass"] == 2
    assert ledger["done"]


def test_coverage_gap_unexplained_not_done(store, target):
    bid = build_batch(store, target, [api_case("d1"), api_case("d2")])
    # 加一条没有用例也没有声明的规则（直接改 business_frame 再看 coverage）
    frame = store.load_artifact(bid, "business_frame")
    frame["rules"].append({"rule_id": "R9", "statement": "无人认领的规则",
                           "source_quote": "q"})
    store.save_artifact(bid, "business_frame", frame)
    c = coverage.build_coverage(bid)
    assert not c["ok"] and any("R9" in p for p in c["problems"])
    ledger = store.load_artifact(bid, "coverage_ledger")
    assert ledger["summary"]["gap_unexplained"] == 1 and not ledger["done"]


# ── UI 轨真浏览器集成（playwright 缺席时跳过）──────────────────────────────

def ui_case(draft_id):
    return {
        "draft_id": draft_id, "kind": "happy",
        "title": "UI 首页可见",
        "source_anchor": {"rule_id": "R1", "quote": "需求原文"},
        "preconditions": [{"statement": "靶可达", "checkable": True}],
        "steps": ["打开首页", "断言标题与元素"],
        "expected": "首页标题与欢迎语可见",
        "failure_criteria": "标题不符或元素缺失",
        "assertion_layers": {"ui": {"assertions": ["标题含本地靶", "hello 可见"],
                                    "required_evidence": ["playwright_trace",
                                                          "screenshot"]}},
        "automation": {"admissible": True, "recipe": {"track": "ui", "actions": [
            {"op": "goto", "path": "/"},
            {"op": "expect_title_contains", "text": "本地靶"},
            {"op": "expect_visible", "selector": "#hello"},
        ]}},
        "side_effects": {"writes": False},
        "open_items": {"unknowns": [], "assumptions": [], "manual_touchpoints": []},
    }


def test_ui_track_real_browser(store, target):
    pytest.importorskip("playwright")
    bid, r = run_chain(store, target, [ui_case("d1"), api_case("d2")],
                       timeout_s=180)
    rec = r["receipt"]
    assert rec["verdict"] == "PASS", rec
    slug_dir = os.path.join(r["run_dir"], "exectest__r1__c001")
    assert os.path.isfile(os.path.join(slug_dir, "trace.zip"))
    assert os.path.isfile(os.path.join(slug_dir, "final.png"))
    # 投影后 UI 结论为 PASS（live 证据 + 实例指纹锚）
    p = project_verdicts.project(r["run_id"])
    assert p["ok"], p
