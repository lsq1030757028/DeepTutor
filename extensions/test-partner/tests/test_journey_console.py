# -*- coding: utf-8 -*-
"""批次工作台面自测：数据 API + 覆盖图 + trace 打开双路径 + 越界防护。"""
import json
import os

import pytest

from server.gateway import journey_console as jc
from server.journey import artifacts


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(artifacts, "BATCHES_ROOT", str(tmp_path / "batches"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", str(tmp_path / "runs"))
    return artifacts


@pytest.fixture(autouse=True)
def no_real_gui_subprocess(monkeypatch):
    """BB-501：测试里**绝不真起 GUI 子进程**。

    原 `test_trace_open_returns_downgrade_command` 直接调真 `jc.open_trace()`，
    于是每跑一次测试套就起一个 `playwright show-trace` 窗口进程；再叠加
    "run 目录被 pytest 清掉后 PID 无人回收"，实测遗留 9 个 chromium 主进程。
    这个 autouse 桩把整条测试文件的这条路堵死，并把"起了几次"记下来供断言。
    """
    calls: list[list[str]] = []

    class _FakeProc:
        pid = 990001

    def _fake_popen(cmd, **kwargs):
        calls.append(list(cmd))
        return _FakeProc()

    monkeypatch.setattr(jc.subprocess, "Popen", _fake_popen)
    return calls


def test_list_empty(store):
    assert jc.list_batches_payload() == {"ok": True, "batches": []}


def test_batch_detail_with_stepper_and_coverage(store):
    meta = store.create_batch("白月一期", base_url="http://127.0.0.1:8047")
    bid = meta["batch_id"]
    store.save_artifact(bid, "intake_profile", {"tier": "standard"})
    store.save_artifact(bid, "coverage_ledger", {
        "run_id": "", "rules": [
            {"rule_id": "R1", "statement": "登录", "status": "covered",
             "gap_reason": "", "cases": [{"case_id": "bysms/R1-C001",
                                          "verdict": "PASS", "probing": False}]},
            {"rule_id": "R9", "statement": "无人认领", "status": "gap_unexplained",
             "gap_reason": "", "cases": []}],
        "summary": {"total_rules": 2, "covered": 1, "declared_uncovered": 0,
                    "gap_unexplained": 1, "official_verdicts": 1, "pass": 1},
        "done": False, "problems": ["R9 无用例且无声明"]})
    d = jc.batch_detail_payload(bid)
    assert d["ok"]
    assert d["batch"]["base_url_host"] == "127.0.0.1:8047"
    steps = {s["artifact"]: s["present"] for s in d["batch"]["stepper"]}
    assert steps["intake_profile"] and not steps["approved_caseset"]
    assert d["coverage"]["summary"]["gap_unexplained"] == 1


def test_batch_detail_missing(store):
    d = jc.batch_detail_payload("b-20260810-nothere")
    assert not d["ok"]


def test_run_result_reads_verdicts_and_receipt(store):
    meta = store.create_batch("t", base_url="http://127.0.0.1:8047")
    bid = meta["batch_id"]
    run_id = store.new_run_id()
    rd = store.run_dir(run_id, create=True)
    with open(os.path.join(rd, "receipt.json"), "w", encoding="utf-8") as fh:
        json.dump({"verdict": "PASS", "counts": {"passed": 1},
                   "credential_scan_ok": True}, fh)
    with open(os.path.join(rd, "verdicts.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "bysms/R1-C001", "verdict": "PASS",
                             "evidence": [{"ref": "x/result.json"}], "note": ""}) + "\n")
    meta["run_ids"] = [run_id]
    store.save_batch(meta)
    d = jc.batch_detail_payload(bid)
    assert d["runs"][0]["receipt"]["verdict"] == "PASS"
    assert d["runs"][0]["verdicts"][0]["id"] == "bysms/R1-C001"


def test_trace_open_path_escape_blocked(store):
    run_id = store.new_run_id()
    store.run_dir(run_id, create=True)
    r = jc.open_trace(run_id, "../../../etc/passwd")
    assert not r["ok"]


def test_trace_open_missing_file(store):
    run_id = store.new_run_id()
    store.run_dir(run_id, create=True)
    r = jc.open_trace(run_id, "nope/trace.zip")
    assert not r["ok"] and "不存在" in r["error"]


def test_trace_open_returns_downgrade_command(store, no_real_gui_subprocess):
    run_id = store.new_run_id()
    rd = store.run_dir(run_id, create=True)
    os.makedirs(os.path.join(rd, "bysms__r1__c001"))
    tp = os.path.join(rd, "bysms__r1__c001", "trace.zip")
    with open(tp, "w") as fh:
        fh.write("PK\x03\x04 fake trace")
    r = jc.open_trace(run_id, "bysms__r1__c001/trace.zip")
    assert r["ok"]
    # 降级路径：始终给可复制命令（ADR-M1-02 §2.3 路径2）
    assert "playwright show-trace" in r["command"]
    assert r["command"].endswith('trace.zip"')
    # BB-501 断言：起的是桩，**真 Popen 调用数 == 0**
    assert len(no_real_gui_subprocess) == 1
    assert "show-trace" in no_real_gui_subprocess[0]


def test_open_trace_never_spawns_when_file_missing(store, no_real_gui_subprocess):
    """文件不在就该在起进程**之前**返回——起了再发现没文件等于白漏一个进程。"""
    run_id = store.new_run_id()
    store.run_dir(run_id, create=True)
    r = jc.open_trace(run_id, "nope/trace.zip")
    assert not r["ok"]
    assert no_real_gui_subprocess == []


def test_page_served():
    import asyncio

    class Req:
        pass
    resp = asyncio.run(jc.JourneyConsole().page(Req()))
    body = resp.body.decode("utf-8")
    assert "批次工作台" in body and "覆盖图" in body and "打开 trace" in body
