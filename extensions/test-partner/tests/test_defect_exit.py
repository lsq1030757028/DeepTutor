# -*- coding: utf-8 -*-
"""缺陷根因出口判据（工程稿 §7 / 0027 §缺陷提单）。

重点在 `write_release_gate`：0027 把「写工具与确认闸同批放行」标成
**硬拦不能靠自觉**，所以它必须是一道会判红的闸，而不是文档里的一句话。
"""
from __future__ import annotations

import pytest

from server.journey import defect_exit
from server.journey.oracle import READ_TOOLS


# ── 放权次序闸 ─────────────────────────────────────────────────────────────
def test_read_only_whitelist_passes_without_confirm_gate():
    """只放只读工具时不需要确认闸——这是当前（第一步）的合法状态。"""
    r = defect_exit.write_release_gate(list(READ_TOOLS), confirm_gate_present=False)
    assert r["ok"] and r["write_tools"] == []


def test_write_tool_without_confirm_gate_is_blocked():
    """先放写权限、承诺后续补约束 = 0027 明令禁止的中间态。"""
    r = defect_exit.write_release_gate(
        list(READ_TOOLS) + ["create_bug"], confirm_gate_present=False)
    assert not r["ok"]
    assert r["code"] == defect_exit.E_NO_CONFIRM_GATE
    assert "create_bug" in r["write_tools"]
    assert "硬拦不能靠自觉" in r["problems"][0]


def test_write_tool_with_confirm_gate_passes():
    """同批放行才准过——闸不是禁止写，是禁止**先**写。"""
    r = defect_exit.write_release_gate(
        list(READ_TOOLS) + ["create_bug"], confirm_gate_present=True)
    assert r["ok"] and r["write_tools"] == ["create_bug"]


def test_unknown_tool_counts_as_write():
    """不在只读白名单里的一律按写处理。

    官方 server 约 43 个工具，我们没逐个核过读写性。判据故意偏严：
    少给=麻烦，多给=静默改别人的数据，两种错的代价不对称。
    """
    r = defect_exit.write_release_gate(["get_stories_or_tasks", "some_new_tool"],
                                       confirm_gate_present=False)
    assert not r["ok"] and r["write_tools"] == ["some_new_tool"]


def test_wildcard_is_treated_as_full_write_access():
    """`*` 通配等于放开全部工具，必须判红而不是当成"没列具体的写工具"。"""
    r = defect_exit.write_release_gate(["*"], confirm_gate_present=False)
    assert not r["ok"]
    assert any("通配" in w for w in r["write_tools"])


def test_current_shipped_config_is_read_only(tmp_path):
    """**对当前真实配置跑一次**：仓里钉的 enabled_tools 必须仍是只读的。

    这条不是重复上面的单测——上面验的是闸的逻辑，这条验的是**闸管到了真配置**。
    本线五例栽在"机制建成但没作用到谁"，所以每道闸都要有一条打真对象的判据。
    """
    from server.gateway.config import default_config
    tools = (default_config().service_settings("tapd") or {}).get("enabled_tools") or []
    r = defect_exit.write_release_gate(tools, confirm_gate_present=False)
    assert r["ok"], (
        f"仓内 TAPD 配置已放行非只读工具 {r['write_tools']}，而确认闸尚未就位。"
        f"若确实要放行，必须与确认闸同批，并把本测试的 confirm_gate_present 改成"
        f"由运行时事实计算——**不许直接改成常量 True**。")


# ── 草稿生成 ───────────────────────────────────────────────────────────────
def test_root_cause_is_left_for_a_human():
    """归因**不自动生成**。

    自动写出来的根因读起来像结论，实际是把"我们没查"包装成"我们查过了"——
    比没有根因更糟。所以草稿只给方向（hint），并把状态标成 pending_human。
    """
    hint = defect_exit._root_cause_hint([{"kind": "db_value:x"}])
    assert "查" in hint
    assert "第二处真相" in hint


def test_hint_distinguishes_layers():
    assert "路由不存在" in defect_exit._root_cause_hint([{"kind": "status"}])
    assert "字段" in defect_exit._root_cause_hint([{"kind": "json_path:code"}])


def test_layers_match_bug_bank_vocabulary():
    """归属层词表与 bug-bank 的 `layer` 同源——换词表就得在提交前翻译，而翻译层丢信息。"""
    assert set(defect_exit.LAYERS) == {
        "体验", "功能", "工具容错", "诚实", "流程", "测试方法", "安全"}


def test_only_failed_cases_become_drafts(monkeypatch, tmp_path):
    """blocked/observed/skipped 不出草稿，但**逐类计数留痕**。

    排除是有代价的（真缺陷可能伪装成 blocked），所以"这轮为什么只有 N 条草稿"
    必须能被追问——只排除不计数就没法追。
    """
    from server.journey import artifacts

    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(artifacts, "BATCHES_ROOT", str(tmp_path / "batches"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", str(tmp_path / "runs"))
    batch = artifacts.create_batch("t", base_url="http://x")
    bid = batch["batch_id"]
    artifacts.save_artifact(bid, "approved_caseset", {"cases": [
        {"case_id": "s/R1-C001", "title": "会红的", "steps": ["a"]},
        {"case_id": "s/R1-C002", "title": "被挡的", "steps": ["b"]},
    ]})
    run_id = artifacts.new_run_id()
    rd = artifacts.run_dir(run_id, create=True)
    import json
    with open(rd + "/results.jsonl", "w", encoding="utf-8") as fh:
        for row in [
            {"case_id": "s/R1-C001", "outcome": "failed",
             "assertions": [{"kind": "db_value:n", "expected": 2, "actual": 1,
                             "passed": False}],
             "http_transcript": [{"method": "GET", "url": "http://x/a", "status": 200}]},
            {"case_id": "s/R1-C002", "outcome": "blocked", "assertions": []},
            {"case_id": "s/R1-C003", "outcome": "observed", "assertions": []},
        ]:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    out = defect_exit.build_drafts(bid, run_id)
    assert out["count"] == 1
    assert out["excluded_counts"]["blocked"] == 1
    assert out["excluded_counts"]["observed"] == 1
    d = out["defect_draft"]["drafts"][0]
    assert d["root_cause"] == "" and d["root_cause_status"] == "pending_human"
    assert d["submission"] == {"state": "draft", "confirmed_by": "", "tapd_ref": ""}
    # 复现路径要含真打过的请求，不是"见日志"
    assert any("GET http://x/a" in s for s in d["repro"])


def test_draft_does_not_become_a_tenth_ledger_cell(monkeypatch, tmp_path):
    """`defect_draft` **刻意不进** ARTIFACT_FILES —— 那张表同时是九格账本的来源。

    进了就等于把"缺陷草稿"变成旅程的第十个必经步骤，而没有失败用例的 run
    根本不该有这一格。
    """
    from server.journey import artifacts
    assert "defect_draft" not in artifacts.ARTIFACT_FILES
