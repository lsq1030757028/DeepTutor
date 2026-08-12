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
                            coverage, digest, draft_cases, execute_run, ingest,
                            project_verdicts, pw_harness, pw_runtime)

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


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_runtime_derives_write_risk_from_mutating_http_method(method):
    meta = {
        "case_id": "C-risk",
        "writes": False,
        "actions": [{"op": "request", "method": method, "path": "/x"}],
    }
    assert pw_runtime.effective_write_risk(meta) is True
    with pytest.raises(pw_runtime.CaseSkip) as caught:
        pw_runtime.CaseRunner(
            {"done_cases": set(), "write_authorized": set()}, meta
        )
    assert caught.value.code == "SKIP_WRITE_UNCONFIRMED"


def test_runtime_keeps_provable_get_read_only():
    assert pw_runtime.effective_write_risk({
        "writes": False,
        "actions": [{"op": "request", "method": "GET", "path": "/x"}],
    }) is False


def test_runtime_encodes_non_ascii_request_path_and_query_once():
    encoded = pw_runtime.ascii_request_url(
        "http://127.0.0.1/api/中文?language=한국어&ready=%E6%98%AF"
    )
    assert encoded == (
        "http://127.0.0.1/api/%E4%B8%AD%E6%96%87"
        "?language=%ED%95%9C%EA%B5%AD%EC%96%B4&ready=%E6%98%AF"
    )


def test_runtime_blocks_unconfirmed_click_before_page_interaction():
    meta = {
        "case_id": "C-ui-risk",
        "writes": False,
        "actions": [{"op": "click", "selector": "#open-details"}],
    }
    assert pw_runtime.effective_write_risk(meta) is True
    with pytest.raises(pw_runtime.CaseSkip) as caught:
        pw_runtime.CaseRunner(
            {"done_cases": set(), "write_authorized": set()}, meta, page=object()
        )
    assert caught.value.code == "SKIP_WRITE_UNCONFIRMED"


def test_ui_visual_capture_is_disabled_even_for_dynamic_target_credentials():
    assert pw_harness.visual_capture_allowed({"scrub_pairs": []}) is False
    assert pw_harness.visual_capture_allowed(
        {"scrub_pairs": [("known-secret", "{{password}}")]}
    ) is False


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


def build_batch(store, target, cases, *, owner="unit-test-owner"):
    partition = owner
    with artifacts.trusted_owner(partition):
        r = ingest.ingest("执行侧自测", target, source_kind="requirement_doc",
                          source_ref="local", requirement_text="接口需求正文",
                          tier="standard", tier_confirmed_via="test", owner=owner)
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
              "_redlines.py", "_runtime.py", "_harness.py", "_pid_ledger.py"):
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


def test_batch_artifact_gate_blocks_planted_secret_before_compile(store, target):
    bad = api_case("d1")
    bad["title"] = "内联凭据 kJ8x2Qw9zR4tYv7uB3nM5pL6sD1fG0hA"
    with pytest.raises(store.ArtifactError, match="落盘前拒绝"):
        build_batch(store, target, [bad, api_case("d2")])
    assert not any("case_draft.json" in filenames
                   for _dir, _subdirs, filenames in os.walk(store.batches_root()))


@pytest.mark.parametrize("returncode", [1, 2, 3, 4, 5])
def test_compile_gate_rejects_nonzero_collect_even_with_complete_nodes(
        store, target, monkeypatch, returncode):
    bid = build_batch(store, target, [api_case("d1")])
    real_collect = compile_bundle._collect_only

    def failed_collect(bundle_dir):
        names, output, _rc = real_collect(bundle_dir)
        return names, output + "\nINTERNALERROR after collection", returncode

    monkeypatch.setattr(compile_bundle, "_collect_only", failed_collect)
    result = compile_bundle.compile_bundle(bid)
    assert result["ok"] is False
    assert any(f"rc={returncode}" in problem for problem in result["problems"])
    assert not os.path.isdir(os.path.join(store.batch_dir(bid), "bundle"))


# ── execute：API 轨端到端 + 红线 ───────────────────────────────────────────


@pytest.mark.parametrize("returncode", [1, 2, 3, 4, 5])
def test_nonzero_pytest_exit_with_partial_rows_can_never_pass(returncode):
    rows = [{"case_id": "exectest/R1-C001", "outcome": "passed"}]
    verdict, integrity = execute_run._execution_verdict(
        rows, returncode, ["exectest/R1-C001", "exectest/R1-C002"])
    assert verdict == "BLOCK"
    assert integrity["ok"] is False
    assert integrity["missing_case_ids"] == ["exectest/R1-C002"]


def test_complete_unique_results_with_zero_exit_can_pass():
    rows = [
        {"case_id": "exectest/R1-C001", "outcome": "passed"},
        {"case_id": "exectest/R1-C002", "outcome": "passed"},
    ]
    verdict, integrity = execute_run._execution_verdict(
        rows, 0, ["exectest/R1-C001", "exectest/R1-C002"])
    assert verdict == "PASS" and integrity["ok"] is True


def test_complete_business_failure_with_pytest_exit_one_is_projectable():
    rows = [
        {"case_id": "exectest/R1-C001", "outcome": "failed"},
        {"case_id": "exectest/R1-C002", "outcome": "passed"},
    ]
    verdict, integrity = execute_run._execution_verdict(
        rows, 1, ["exectest/R1-C001", "exectest/R1-C002"])
    assert verdict == "FAIL"
    assert integrity["ok"] is True


@pytest.mark.parametrize("returncode", [2, 3, 4, 5])
def test_complete_rows_with_infrastructure_exit_still_block(returncode):
    rows = [
        {"case_id": "exectest/R1-C001", "outcome": "failed"},
        {"case_id": "exectest/R1-C002", "outcome": "passed"},
    ]
    verdict, integrity = execute_run._execution_verdict(
        rows, returncode, ["exectest/R1-C001", "exectest/R1-C002"])
    assert verdict == "BLOCK"
    assert integrity["ok"] is False


def test_duplicate_or_unknown_result_rows_block_projection():
    rows = [
        {"case_id": "exectest/R1-C001", "outcome": "passed"},
        {"case_id": "exectest/R1-C001", "outcome": "passed"},
        {"case_id": "exectest/R9-C999", "outcome": "passed"},
    ]
    verdict, integrity = execute_run._execution_verdict(
        rows, 0, ["exectest/R1-C001", "exectest/R1-C002"])
    assert verdict == "BLOCK"
    assert integrity["duplicate_case_ids"] == ["exectest/R1-C001"]
    assert integrity["unexpected_case_ids"] == ["exectest/R9-C999"]


@pytest.mark.parametrize("timeout_s", [-1, 0, 1201])
def test_invalid_timeout_is_rejected_before_batch_or_run_creation(store, timeout_s):
    before = list(store.list_owners())
    result = execute_run.execute("b-20260813-missing", timeout_s=timeout_s)
    assert result["ok"] is False and result["error"] == "INVALID_TIMEOUT"
    assert store.list_owners() == before

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
    assert rec["verdict"] == "PASS", json.dumps(
        rec, ensure_ascii=False, sort_keys=True)
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
    assert rec["result_integrity"]["ok"] is True
    projected = project_verdicts.project(r["run_id"])
    assert projected["ok"] is True, projected
    assert projected["projection"]["distribution"] == {"FAIL": 1, "PASS": 1}


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


def test_execute_write_confirmed_runs(store, target, monkeypatch):
    cases = [api_case("dw", writes=True)]
    bid = build_batch(store, target, cases)
    assert compile_bundle.compile_bundle(bid)["ok"]
    # 走**生产写入口**，不再手搓事件（2026-08-11，0028）。
    # 手搓的那份当年测的是一条生产代码根本产不出的事件形态——
    # 它绿着，而真实链路上写确认压根没有写入口，卡答完照样被拦。
    from server.journey import tools as _jt
    from tests.journey_decision_helper import SECRET, decision_kwargs
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)
    assert _jt.write_confirm(batch_id=bid, case_ids=["exectest/R1-C001"],
                             owner="unit-test-owner",
                             decided_by="manager(self-derived-pending-audit)",
                             caller_surface="capability",
                             **decision_kwargs(
                                 bid, ["exectest/R1-C001"],
                                 owner="unit-test-owner",
                                 decided_by="manager(self-derived-pending-audit)"))["ok"]
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


def test_execute_omits_pytest_secret_and_final_scan_covers_receipt(
        store, target, monkeypatch):
    bid = build_batch(store, target, [api_case("d1"), api_case("d2")])
    assert compile_bundle.compile_bundle(bid)["ok"]
    secret = "tail-secret-" + "7Qx9" + "Lm2P"
    real_run = execute_run.subprocess.run
    real_scan = execute_run.credential_scan.scan_tree
    receipt_seen = []

    def stdout_with_secret(*args, **kwargs):
        proc = real_run(*args, **kwargs)
        proc.stdout = (proc.stdout or "") + "\n" + secret
        return proc

    def scan_after_receipt(root, **kwargs):
        receipt_seen.append(os.path.isfile(os.path.join(root, "receipt.json")))
        return real_scan(root, **kwargs)

    monkeypatch.setattr(execute_run.subprocess, "run", stdout_with_secret)
    monkeypatch.setattr(execute_run.credential_scan, "scan_tree", scan_after_receipt)
    r = execute_run.execute(bid, variables={"runtime_password": secret})
    assert r["ok"], r
    assert r["receipt"]["credential_scan_ok"] is True
    assert r["receipt"]["credential_scan_passes"] == 2
    assert r["receipt"]["pytest_tail_omitted"] is True
    assert secret not in r["receipt"]["pytest_tail"]
    assert receipt_seen == [True, True]
    needle = secret.encode("utf-8")
    for dirpath, _dirs, files in os.walk(r["run_dir"]):
        for name in files:
            with open(os.path.join(dirpath, name), "rb") as fh:
                assert needle not in fh.read(), name


def test_execute_security_scan_failure_blocks_receipt_and_projection(
        store, target, monkeypatch):
    bid = build_batch(store, target, [api_case("d1"), api_case("d2")])
    assert compile_bundle.compile_bundle(bid)["ok"]
    monkeypatch.setattr(execute_run.credential_scan, "scan_tree", lambda *_a, **_kw: {
        "ok": False, "known_hits": [],
        "entropy_hits": [{"file": "x.json", "token": "opaque",
                          "token_preview": "opaq…aque", "length": 32,
                          "entropy": 4.2}],
        "allowlisted_hits": [], "scanned_files": 1, "note": "blocked",
    })
    r = execute_run.execute(bid)
    assert r["receipt"]["counts"] == {"passed": 2}
    assert r["receipt"]["credential_scan_ok"] is False
    assert r["receipt"]["verdict"] == "BLOCK"
    p = project_verdicts.project(r["run_id"])
    assert not p["ok"] and p["stage"] == "credential_scan"


def test_project_rejects_run_from_superseded_caseset(store, target):
    bid = build_batch(store, target, [api_case("d1")])
    assert compile_bundle.compile_bundle(bid)["ok"]
    run = execute_run.execute(bid)
    assert run["ok"]
    caseset = store.load_artifact(bid, "approved_caseset")
    caseset["caseset_id"] = "acs-superseded"
    store.save_artifact(bid, "approved_caseset", caseset)
    projected = project_verdicts.project(run["run_id"])
    assert projected["ok"] is False
    assert projected["stage"] == "run_identity"
    assert "caseset_id" in projected["mismatches"]


def test_execute_resume_skips_done(store, target):
    cases = [api_case("d1"), api_case("d2")]
    bid = build_batch(store, target, cases)
    assert compile_bundle.compile_bundle(bid)["ok"]
    r1 = execute_run.execute(bid, case_ids=["exectest/R1-C001"])
    r2 = execute_run.execute(bid, resume_run_id=r1["run_id"])
    rows = execute_run._read_results(r2["run_dir"])
    assert len([x for x in rows if x["case_id"] == "exectest/R1-C001"]) == 1
    assert {x["case_id"] for x in rows} == {"exectest/R1-C001", "exectest/R1-C002"}


def test_execute_resume_unknown_run_fails_without_creating_directory(store, target):
    bid = build_batch(store, target, [api_case("d1"), api_case("d2")])
    assert compile_bundle.compile_bundle(bid)["ok"]
    unknown = "r-20260812-abcdef"
    owner = store.load_batch(bid)["partition"]
    expected = store.run_dir(unknown, owner=owner)
    assert not os.path.exists(expected)
    r = execute_run.execute(bid, resume_run_id=unknown)
    assert not r["ok"] and r["error"] == "RESUME_RUN_MISMATCH"
    assert not os.path.exists(expected)


def test_execute_resume_cross_batch_fails_without_touching_old_run(store, target):
    cases = [api_case("d1"), api_case("d2")]
    first = build_batch(store, target, cases)
    second = build_batch(store, target, cases)
    assert compile_bundle.compile_bundle(first)["ok"]
    assert compile_bundle.compile_bundle(second)["ok"]
    old = execute_run.execute(first)
    assert old["ok"]

    def snapshot(path):
        return {os.path.relpath(os.path.join(d, f), path):
                open(os.path.join(d, f), "rb").read()
                for d, _dirs, files in os.walk(path) for f in files}

    before = snapshot(old["run_dir"])
    r = execute_run.execute(second, resume_run_id=old["run_id"])
    assert not r["ok"] and r["error"] == "RESUME_RUN_MISMATCH"
    assert snapshot(old["run_dir"]) == before
    assert store.load_batch(second)["run_ids"] == []


def test_execute_resume_cross_owner_fails_closed(store, target):
    cases = [api_case("d1"), api_case("d2")]
    first = build_batch(store, target, cases, owner="owner-a")
    second = build_batch(store, target, cases, owner="owner-b")
    assert compile_bundle.compile_bundle(first)["ok"]
    assert compile_bundle.compile_bundle(second)["ok"]
    old = execute_run.execute(first)
    assert old["ok"]
    owner_b_target = store.run_dir(old["run_id"], owner="owner-b")
    assert not os.path.exists(owner_b_target)
    r = execute_run.execute(second, resume_run_id=old["run_id"])
    assert not r["ok"] and r["error"] == "RESUME_RUN_MISMATCH"
    assert not os.path.exists(owner_b_target)
    assert store.load_batch(second, owner="owner-b")["run_ids"] == []


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
    assert ledger["business_result"]["status"] == "PASS"
    assert ledger["business_result"]["ready_for_acceptance"]


def test_coverage_design_done_does_not_masquerade_as_business_done(store, target):
    bid, r = executed_run(store, target)
    # 未走正式投影：规则映射可以完整，但业务结果必须明确 PENDING。
    c = coverage.build_coverage(bid, r["run_id"])
    assert c["ok"]
    ledger = store.load_artifact(bid, "coverage_ledger")
    assert ledger["done"] is True
    assert ledger["summary"]["official_verdicts"] == 0
    assert ledger["business_result"]["status"] == "PENDING"
    assert ledger["business_result"]["conclusive"] is False
    assert ledger["business_result"]["ready_for_acceptance"] is False


def test_coverage_declared_gap_cannot_masquerade_as_business_pass(store, target):
    bid, run = executed_run(store, target)
    assert project_verdicts.project(run["run_id"])["ok"]
    frame = store.load_artifact(bid, "business_frame")
    frame["rules"].append({
        "rule_id": "R9", "statement": "关键退款规则", "source_quote": "退款原文"})
    store.save_artifact(bid, "business_frame", frame)
    draft = store.load_artifact(bid, "case_draft")
    draft["uncovered_rules"] = [{
        "rule_id": "R9", "reason": "由下一轮负责；否则退款错误会逃逸到验收"}]
    store.save_artifact(bid, "case_draft", draft)

    result = coverage.build_coverage(bid, run["run_id"])
    assert result["ok"], result
    business = store.load_artifact(bid, "coverage_ledger")["business_result"]
    assert business["status"] == "PENDING"
    assert business["coverage_complete"] is False
    assert business["incomplete_rules"] == ["R9"]
    assert business["ready_for_acceptance"] is False


def test_coverage_blocks_when_write_entity_was_never_human_confirmed(store, target):
    bid = build_batch(store, target, [api_case("dw", writes=True), api_case("dr")])
    assert compile_bundle.compile_bundle(bid)["ok"]
    run = execute_run.execute(bid)
    assert run["ok"]
    project_verdicts.project(run["run_id"])
    result = coverage.build_coverage(bid, run["run_id"])
    assert result["ok"], result
    business = store.load_artifact(bid, "coverage_ledger")["business_result"]
    assert business["status"] == "BLOCKED"
    assert business["entity_scope_ok"] is False
    assert business["ready_for_acceptance"] is False


def test_compile_rejects_caseset_that_tampers_with_rule_probing(store, target):
    bid = build_batch(store, target, [
        api_case("d1", "/api/fake200", json_path="ret", equals=0),
        api_case("d2"),
    ])
    caseset = store.load_artifact(bid, "approved_caseset")
    caseset["cases"][0]["source_anchor"]["probing"] = True
    caseset["cases"] = [digest.stamp_case_digests(c) for c in caseset["cases"]]
    store.save_artifact(bid, "approved_caseset", caseset)
    result = compile_bundle.compile_bundle(bid)
    assert result["ok"] is False
    assert result["gate"] == "compile-gate#1-schema"
    assert any("probing 与业务规则不一致" in p for p in result["problems"])
    assert not os.path.isdir(os.path.join(store.batch_dir(bid), "bundle"))


def test_coverage_rejects_a_run_from_another_batch_without_writing(store, target):
    first, _first_run = executed_run(store, target)
    second, second_run = executed_run(store, target)
    assert project_verdicts.project(second_run["run_id"])["ok"]
    assert not store.has_artifact(first, "coverage_ledger")

    result = coverage.build_coverage(first, second_run["run_id"])
    assert result["ok"] is False
    assert result["code"] == "E_RUN_BATCH_MISMATCH"
    assert not store.has_artifact(first, "coverage_ledger")


def test_coverage_rejects_run_after_caseset_content_changes(store, target):
    bid, run = executed_run(store, target)
    assert project_verdicts.project(run["run_id"])["ok"]
    caseset = store.load_artifact(bid, "approved_caseset")
    caseset["cases"][0]["title"] = "采纳集已变化"
    store.save_artifact(bid, "approved_caseset", caseset)

    result = coverage.build_coverage(bid, run["run_id"])
    assert result["ok"] is False
    assert result["code"] == "E_RUN_STATE_MISMATCH"
    assert not store.has_artifact(bid, "coverage_ledger")


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
    # track-purity 要求单批次单轨；浏览器集成用两条 UI case，不能拿 API case
    # 只为凑标准档最小用例数，否则测试本身构造的是生产编译器明确拒绝的输入。
    bid, r = run_chain(store, target, [ui_case("d1"), ui_case("d2")],
                       timeout_s=180)
    rec = r["receipt"]
    assert rec["verdict"] == "PASS", json.dumps(
        rec, ensure_ascii=False, sort_keys=True)
    assert rec["target_identity"]["track"] == "ui"
    assert rec["target_identity"]["page_title"].startswith("本地靶")
    slug_dir = os.path.join(r["run_dir"], "exectest__r1__c001")
    assert os.path.isfile(os.path.join(slug_dir, "trace.zip"))
    assert not os.path.isfile(os.path.join(slug_dir, "final.png"))
    # 没有可机械证明已去敏的像素证据时，不得把要求 screenshot 的 UI 结论投成正式 PASS。
    p = project_verdicts.project(r["run_id"])
    assert p.get("projection"), p
    verdicts = project_verdicts.read_verdicts(r["run_id"])
    assert verdicts and all(row["verdict"] != "PASS" for row in verdicts)
