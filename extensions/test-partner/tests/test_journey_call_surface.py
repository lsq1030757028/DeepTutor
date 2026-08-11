# -*- coding: utf-8 -*-
"""旅程调用面自测：MCP 面对拍、门票闸、错误载荷判别、幂等闸。

对应判据：
- ADR-M2-01 G1（`parse_mcp_payload` 三类错误载荷）→ DoD#2
- ADR-M2-01 G2（MCP 面工具数与声明对拍）→ DoD#1 的 CI 部分
- ADR-M2-01 G5（误调无后果：无门票零落盘 / 无 batch_id 被拒）→ DoD#8e-2
- ADR-M2-03 G5（幂等，两条重放路径各一条）→ DoD#8f
"""
import json
import os

import pytest

from server.journey import artifacts, gate, idempotency, tools
from server.journey.mcp_payload import (
    E_MCP_UNAVAILABLE, McpPayloadError, parse_mcp_payload, try_parse_mcp_payload)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(artifacts, "BATCHES_ROOT", str(tmp_path / "batches"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", str(tmp_path / "runs"))
    return artifacts


# ── G1 / DoD#2：错误当数据的防线 ────────────────────────────────────────────


def test_parse_mcp_payload_accepts_real_json():
    assert parse_mcp_payload('{"data": [1]}')["data"] == [1]
    assert parse_mcp_payload('[{"a":1}]')[0]["a"] == 1


@pytest.mark.parametrize("raw", [
    "(MCP server 'tapd' is not connected)",       # 未连接
    "(MCP tool 'get_stories_or_tasks' timed out)",  # 超时
    '{"data": [{"Story": {"id": "1"',              # 半截 JSON
    "服务暂时不可用，请稍后重试",                    # 一句人话
    "",                                             # 空
    None,                                           # 通道什么都没给
    "42",                                           # 裸标量
])
def test_parse_mcp_payload_rejects_error_shapes(raw):
    """三类错误载荷 100% 识别且**不返回业务对象**（fail-closed，不是记 warning 继续）。"""
    with pytest.raises(McpPayloadError) as exc:
        parse_mcp_payload(raw)
    assert exc.value.code == E_MCP_UNAVAILABLE


def test_try_parse_returns_envelope_not_exception():
    bad = try_parse_mcp_payload("(MCP server 'tapd' is not connected)")
    assert bad["ok"] is False and bad["code"] == E_MCP_UNAVAILABLE
    assert "data" not in bad          # 关键：错误路径不得夹带业务对象
    good = try_parse_mcp_payload('{"x":1}')
    assert good["ok"] and good["data"] == {"x": 1}


# ── G2 / DoD#1：MCP 面与声明对拍 ────────────────────────────────────────────


def test_mcp_surface_matches_declaration():
    """挂了几个 journey 工具，就得声明几个。两向漂移都要红。"""
    import server.main as main_mod
    mounted = {n[len("journey_"):] for n in dir(main_mod) if n.startswith("journey_")}
    declared = set(tools.MCP_TOOL_NAMES)
    assert mounted == declared, (
        f"MCP 面与声明不一致：只挂未声明={sorted(mounted - declared)}，"
        f"只声明未挂={sorted(declared - mounted)}")


def test_nine_atomic_tools_all_declared():
    """九原子一个都不能少——少一个就是"闭环有缺口但没人发现"。"""
    assert tools.JUDGEMENT_TOOLS + tools.MECHANICAL_TOOLS == (
        "ingest", "clarify", "analyze", "draft_cases", "adopt",
        "compile", "execute", "project", "coverage")


# ── G5 / DoD#8e-2：误调无后果 ───────────────────────────────────────────────


def _count_persisted(root: str) -> int:
    total = 0
    for base, _dirs, files in os.walk(root):
        total += len([f for f in files if f.endswith((".json", ".jsonl"))])
    return total


@pytest.mark.parametrize("token", ["", "   ", "not-a-real-token", "x" * 32])
def test_ingest_without_valid_ticket_persists_nothing(store, tmp_path, token):
    """无票 / 错票：判 E_GATE_REQUIRED 且**批次目录与产物落盘数 == 0**。

    这条断言的重点在"零落盘"——只断返回码不够，先 mkdir 再校验一样能返回错误码，
    但目录已经建出来了。
    """
    before = _count_persisted(str(tmp_path))
    r = tools.ingest(title="t", base_url="http://127.0.0.1:9",
                     gate_token=token, requirement_text="随便写点")
    assert r["ok"] is False and r["code"] == gate.E_GATE_REQUIRED
    assert _count_persisted(str(tmp_path)) == before
    assert not os.path.isdir(os.path.join(str(tmp_path), "_local", "batches"))


def test_expired_ticket_rejected(store, monkeypatch):
    issued = gate.issue(ttl_s=-1)
    r = gate.verify(issued["token"])
    assert r["ok"] is False and r["code"] == gate.E_GATE_REQUIRED
    assert "过期" in r["message"]


@pytest.mark.parametrize("fn,kwargs", [
    (tools.clarify, {"rules": [], "confirmed_facts_md": "x"}),
    (tools.analyze, {"example_map": [], "analysis_md": "x"}),
    (tools.draft_cases, {"cases": []}),
    (tools.compile_bundle, {}),
    (tools.execute, {}),
    (tools.coverage, {}),
    (tools.get_batch, {}),
])
def test_other_tools_reject_fabricated_batch_id(store, fn, kwargs):
    """编造一个 batch_id → E_NO_BATCH，而不是被当成新批次悄悄建出来。"""
    r = fn(batch_id="b-20260811-deadbe", **kwargs)
    assert r["ok"] is False and r["code"] == gate.E_NO_BATCH


def test_valid_ticket_lets_ingest_through(store, monkeypatch):
    from server.journey import ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "probe_target", lambda url, timeout_s=10: {
        "reachable": True, "status": 200, "page_title": "", "body_head_sha256": "a" * 64})
    issued = gate.issue()
    r = tools.ingest(title="韩语角色", base_url="http://127.0.0.1:8047",
                     gate_token=issued["token"], source_kind="doc",
                     source_ref="local", requirement_text="新增韩语语音角色，支持男女音色",
                     tier="standard", tier_confirmed_via="test")
    assert r["ok"] is True, r
    assert r["batch_id"].startswith("b-")
    events = artifacts.read_events(r["batch_id"])
    assert any(e["type"] == "gate_token_used" for e in events)
    # DoD#8e-3：调用面落进事件流（信号，不当闸）
    assert any(e.get("caller_surface") for e in events if e["type"] == "tool_call")


# ── ADR-M2-03 G5 / DoD#8f：幂等（两条重放路径各一条）────────────────────────


def test_idempotency_key_is_input_derived_and_stable():
    a = idempotency.derive_key("b-20260811-aaaaaa", "execute", {"case_ids": ["x"]})
    b = idempotency.derive_key("b-20260811-aaaaaa", "execute", {"case_ids": ["x"]})
    c = idempotency.derive_key("b-20260811-aaaaaa", "execute", {"case_ids": ["y"]})
    assert a == b and a != c


def test_replay_via_regenerate_button_path_returns_first_result(store):
    """路径一：「重新生成」按钮（案 B 免疫的那条，但幂等仍要成立）。"""
    meta = artifacts.create_batch("t")
    bid = meta["batch_id"]
    key, replay = idempotency.guard(bid, "execute", {"case_ids": []})
    assert replay is None
    idempotency.record(bid, key, "execute", {"ok": True, "run_id": "r-1"})
    key2, replay2 = idempotency.guard(bid, "execute", {"case_ids": []})
    assert key2 == key
    assert replay2 is not None and replay2["replayed"] is True
    assert replay2["run_id"] == "r-1"


def test_replay_via_edit_branch_path_returns_first_result(store):
    """路径二：编辑用户消息 → 分支重跑。

    **这条路径完全不读 capability**（`canEdit` 只看 `onEdit`/`msg.id`/`isStreaming`），
    所以选正式 capability 并不免疫它。只测按钮那条等于什么都没测。
    这里模拟的是"同一批输入被第二次派发"，与触发它的 UI 入口无关——
    幂等闸本来就该在**入口无关**的层面成立。
    """
    meta = artifacts.create_batch("t")
    bid = meta["batch_id"]
    params = {"case_ids": ["bysms/R1-C001"], "base_url_override": ""}
    key, first = idempotency.guard(bid, "adopt", params)
    assert first is None
    idempotency.record(bid, key, "adopt", {"ok": True, "caseset": "v1"})
    _, second = idempotency.guard(bid, "adopt", params)
    assert second is not None and second["replayed"] is True
    assert second["code"] == "REPLAYED"


def test_different_inputs_are_not_replayed(store):
    meta = artifacts.create_batch("t")
    bid = meta["batch_id"]
    k1, _ = idempotency.guard(bid, "execute", {"case_ids": ["a"]})
    idempotency.record(bid, k1, "execute", {"ok": True, "run_id": "r-1"})
    _, replay = idempotency.guard(bid, "execute", {"case_ids": ["b"]})
    assert replay is None       # 换了输入就该真跑，不能拿旧结果糊弄


def test_ledger_is_append_only_and_last_write_wins(store):
    meta = artifacts.create_batch("t")
    bid = meta["batch_id"]
    idempotency.record(bid, "k", "execute", {"ok": True, "n": 1})
    idempotency.record(bid, "k", "execute", {"ok": True, "n": 2})
    path = os.path.join(artifacts.batch_dir(bid), idempotency.LEDGER_NAME)
    with open(path, encoding="utf-8") as fh:
        rows = [json.loads(x) for x in fh if x.strip()]
    assert len(rows) == 2                       # 追加，不覆写
    assert idempotency.lookup(bid, "k")["result"]["n"] == 2
