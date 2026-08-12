# -*- coding: utf-8 -*-
"""旅程调用面自测：MCP 面对拍、门票闸、错误载荷判别、幂等闸。

对应判据：
- ADR-M2-01 G1（`parse_mcp_payload` 三类错误载荷）→ DoD#2
- ADR-M2-01 G2（MCP 面工具数与声明对拍）→ DoD#1 的 CI 部分
- ADR-M2-01 G5（误调无后果：无门票零落盘 / 无 batch_id 被拒）→ DoD#8e-2
- ADR-M2-03 G5（幂等，两条重放路径各一条）→ DoD#8f
"""
import json
import multiprocessing
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from server.journey import artifacts, gate, idempotency, tools

TRUSTED_OWNER = "unit-test-owner"
from server.journey.mcp_payload import (
    E_MCP_UNAVAILABLE, McpPayloadError, parse_mcp_payload, try_parse_mcp_payload)


def _reservation_process(root, batch_id, marker_path, start_event, result_queue):
    """spawn 子进程入口：验证 OS 锁，不依赖 pytest monkeypatch。"""
    start_event.wait(10)
    with idempotency.reservation(
            batch_id, "execute", {"scope": "all"}, "shared-key",
            root=root) as (key, replay):
        if replay is not None:
            result_queue.put("replay")
            return
        with open(marker_path, "a", encoding="utf-8") as fh:
            fh.write("side-effect\n")
        time.sleep(0.1)
        idempotency.record(batch_id, key, "execute", {"ok": True},
                           params={"scope": "all"}, root=root)
        result_queue.put("first")


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


def test_ingest_without_trusted_owner_persists_nothing(store, tmp_path):
    """生产 facade 之外无可信 owner：拒绝且零落盘。"""
    before = _count_persisted(str(tmp_path))
    r = tools.ingest(title="t", base_url="http://127.0.0.1:9",
                     requirement_text="随便写点")
    assert r["ok"] is False and r["code"] == "E_OWNER_REQUIRED"
    assert _count_persisted(str(tmp_path)) == before
    assert not os.path.isdir(os.path.join(str(tmp_path), "_local", "batches"))


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
    r = fn(batch_id="b-20260811-deadbe", owner=TRUSTED_OWNER, **kwargs)
    assert r["ok"] is False and r["code"] == gate.E_NO_BATCH


def test_trusted_owner_lets_ingest_through(store, monkeypatch):
    from server.journey import ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "probe_target", lambda url, timeout_s=10: {
        "reachable": True, "status": 200, "page_title": "", "body_head_sha256": "a" * 64})
    r = tools.ingest(title="韩语角色", base_url="http://127.0.0.1:8047",
                     owner=TRUSTED_OWNER, source_kind="doc",
                     source_ref="local", requirement_text="新增韩语语音角色，支持男女音色",
                     tier="standard", tier_confirmed_via="test")
    assert r["ok"] is True, r
    assert r["batch_id"].startswith("b-")
    events = artifacts.read_events(r["batch_id"])
    # DoD#8e-3：调用面落进事件流（信号，不当闸）
    assert any(e.get("caller_surface") for e in events if e["type"] == "tool_call")


def test_injected_root_reaches_legacy_downstream_modules(tmp_path, monkeypatch):
    """Facade 的 root 要贯穿到仍使用 artifacts 默认参数的旧九原子实现。"""
    default_root = tmp_path / "default"
    injected_root = tmp_path / "injected"
    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(default_root))

    with artifacts.trusted_owner(TRUSTED_OWNER, root=str(injected_root)):
        batch = artifacts.create_batch("root contract", owner=TRUSTED_OWNER)

    from server.journey import clarify as clarify_mod

    def fake_clarify(*_args, **_kwargs):
        return {
            "ok": True,
            "business_frame": {"resolved_root": artifacts.workbench_root()},
        }

    monkeypatch.setattr(clarify_mod, "clarify", fake_clarify)
    result = tools.clarify(
        batch_id=batch["batch_id"],
        rules=[],
        confirmed_facts_md="test",
        owner=TRUSTED_OWNER,
        root=str(injected_root),
    )

    assert result["ok"] is True
    assert result["business_frame"]["resolved_root"] == os.path.abspath(injected_root)
    assert not default_root.exists()


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


def test_atomic_reservation_allows_one_thread_side_effect(store):
    bid = artifacts.create_batch("thread-race")["batch_id"]
    barrier = threading.Barrier(8)
    side_effects = []

    # Windows cannot flush a byte that another thread already locked.  Seed the
    # lock file before releasing the barrier so this test exercises reservation,
    # not concurrent first-time lock-file initialization.
    lock_path = idempotency._reservation_path(  # noqa: SLF001 - regression fixture
        bid, "execute", "thread-key")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "wb") as fh:
        fh.write(b"\0")

    def invoke():
        barrier.wait()
        with idempotency.reservation(
                bid, "execute", {"scope": "all"}, "thread-key") as (key, replay):
            if replay is not None:
                return "replay"
            side_effects.append("ran")
            time.sleep(0.05)
            idempotency.record(bid, key, "execute", {"ok": True, "run_id": "r-1"},
                               params={"scope": "all"})
            return "first"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _i: invoke(), range(8)))
    assert results.count("first") == 1
    assert results.count("replay") == 7
    assert side_effects == ["ran"]


def test_execute_tool_holds_reservation_across_real_side_effect_boundary(
        store, monkeypatch):
    bid = artifacts.create_batch("tool-thread-race", owner=TRUSTED_OWNER)["batch_id"]
    barrier = threading.Barrier(8)
    calls = []

    def fake_execute(*_args, **_kwargs):
        calls.append("ran")
        time.sleep(0.05)
        return {"ok": True, "run_id": "r-1", "receipt": {"verdict": "PASS"}}

    monkeypatch.setattr(tools._execute, "execute", fake_execute)
    monkeypatch.setattr(tools, "_bundle_state", lambda *_args: {
        "bundle_sha256": "sha256:test", "caseset_id": "acs-test",
        "compiler_version": "test",
    })

    def invoke():
        barrier.wait()
        return tools.execute(batch_id=bid, idempotency_key="tool-thread-key",
                             owner=TRUSTED_OWNER)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _i: invoke(), range(8)))
    assert calls == ["ran"]
    assert sum(result.get("replayed") is True for result in results) == 7
    assert all(result["ok"] for result in results)


def test_write_execute_crash_requires_reconciliation_without_second_effect(
        store, monkeypatch):
    """A crash after the target boundary must never auto-repeat a write."""
    bid = artifacts.create_batch("write-crash", owner=TRUSTED_OWNER)["batch_id"]
    calls = []

    def crash_after_boundary(*_args, on_effect_boundary=None, **_kwargs):
        calls.append("target-write")
        assert on_effect_boundary is not None
        on_effect_boundary()
        raise RuntimeError("injected crash after target write")

    monkeypatch.setattr(tools._execute, "execute", crash_after_boundary)
    monkeypatch.setattr(tools, "_selected_authorized_write_risk",
                        lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tools, "_bundle_state", lambda *_args: {
        "bundle_sha256": "sha256:test", "caseset_id": "acs-test",
        "compiler_version": "test",
    })

    with pytest.raises(RuntimeError, match="injected crash"):
        tools.execute(batch_id=bid, idempotency_key="write-crash-key",
                      owner=TRUSTED_OWNER)
    retry = tools.execute(batch_id=bid, idempotency_key="write-crash-key",
                          owner=TRUSTED_OWNER)
    assert retry["code"] == "E_EXECUTION_RECONCILIATION_REQUIRED"
    assert calls == ["target-write"]


def test_receipt_after_crash_is_recovered_without_second_execute(
        store, monkeypatch):
    """Crash after final receipt but before ledger record reuses that receipt."""
    bid = artifacts.create_batch("receipt-crash", owner=TRUSTED_OWNER)["batch_id"]
    calls = []

    def finish_receipt(*_args, reserved_run_id="", on_effect_boundary=None,
                       **_kwargs):
        calls.append(reserved_run_id)
        assert on_effect_boundary is not None
        on_effect_boundary()
        run_dir = artifacts.run_dir(
            reserved_run_id, create=True, owner=TRUSTED_OWNER)
        receipt = {
            "run_id": reserved_run_id,
            "batch_id": bid,
            "owner_partition": TRUSTED_OWNER,
            "credential_scan_passes": 2,
            "credential_scan_ok": True,
            "verdict": "PASS",
        }
        artifacts._write_json(  # noqa: SLF001 - fault-injection fixture
            os.path.join(run_dir, "receipt.json"), receipt)
        return {"ok": True, "run_id": reserved_run_id, "receipt": receipt}

    monkeypatch.setattr(tools._execute, "execute", finish_receipt)
    monkeypatch.setattr(tools, "_selected_authorized_write_risk",
                        lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tools, "_bundle_state", lambda *_args: {
        "bundle_sha256": "sha256:test", "caseset_id": "acs-test",
        "compiler_version": "test",
    })
    real_update = idempotency.update_execution_intent
    crash_once = {"armed": True}

    def fail_before_complete(*args, status, **kwargs):
        if status == "complete" and crash_once["armed"]:
            crash_once["armed"] = False
            raise RuntimeError("injected crash before ledger")
        return real_update(*args, status=status, **kwargs)

    monkeypatch.setattr(idempotency, "update_execution_intent",
                        fail_before_complete)
    with pytest.raises(RuntimeError, match="before ledger"):
        tools.execute(batch_id=bid, idempotency_key="receipt-crash-key",
                      owner=TRUSTED_OWNER)
    recovered = tools.execute(
        batch_id=bid, idempotency_key="receipt-crash-key",
        owner=TRUSTED_OWNER)
    assert recovered["ok"] and recovered["recovered"] is True
    assert recovered["run_id"] == calls[0]
    assert calls == [calls[0]]
    assert artifacts.load_batch(
        bid, owner=TRUSTED_OWNER)["run_ids"] == [calls[0]]


def test_same_batch_compile_and_adopt_are_resource_serialized(store, monkeypatch):
    """Different tools/keys still mutate one batch as one ordered resource."""
    bid = artifacts.create_batch("resource-race", owner=TRUSTED_OWNER)["batch_id"]
    barrier = threading.Barrier(2)
    state_lock = threading.Lock()
    active = 0
    max_active = 0

    def effect(result):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with state_lock:
            active -= 1
        return result

    # Both public tool functions retain their real shared batch decorator; only
    # deeper artifact prerequisites are replaced by deterministic boundaries.
    monkeypatch.setattr(tools, "_guarded", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tools, "_artifact_state_digest",
                        lambda *_args, **_kwargs: "sha256:" + "a" * 64)
    monkeypatch.setattr(tools, "_batch_partition",
                        lambda *_args, **_kwargs: TRUSTED_OWNER)
    monkeypatch.setattr(
        tools._compile, "compile_bundle",
        lambda *_args, **_kwargs: effect({"ok": True}))
    monkeypatch.setattr(
        tools._adopt, "adopt",
        lambda *_args, **_kwargs: effect({
            "ok": True, "approved_caseset": {}, "cases_gate": {}}))

    def compile_one():
        barrier.wait()
        return tools.compile_bundle(
            batch_id=bid, idempotency_key="compile-k",
            owner=TRUSTED_OWNER)

    def adopt_one():
        barrier.wait()
        return tools.adopt(
            batch_id=bid, selected_draft_ids=["d1"],
            idempotency_key="adopt-k", skip_drift_check=True,
            owner=TRUSTED_OWNER)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(compile_one)
        second = pool.submit(adopt_one)
        results = [first.result(timeout=5), second.result(timeout=5)]
    assert all(result["ok"] for result in results)
    assert max_active == 1


def test_compile_replays_only_for_the_same_caseset_version(store, monkeypatch):
    bid = artifacts.create_batch("compile-version", owner=TRUSTED_OWNER)["batch_id"]
    state = {"digest": "sha256:" + "a" * 64}
    calls = []
    monkeypatch.setattr(tools, "_guarded", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tools, "_batch_partition",
                        lambda *_args, **_kwargs: TRUSTED_OWNER)
    monkeypatch.setattr(tools, "_artifact_state_digest",
                        lambda *_args, **_kwargs: state["digest"])
    monkeypatch.setattr(
        tools._compile, "compile_bundle",
        lambda *_args, **_kwargs: calls.append(state["digest"]) or {"ok": True})
    first = tools.compile_bundle(batch_id=bid, owner=TRUSTED_OWNER)
    replay = tools.compile_bundle(batch_id=bid, owner=TRUSTED_OWNER)
    state["digest"] = "sha256:" + "b" * 64
    second_version = tools.compile_bundle(batch_id=bid, owner=TRUSTED_OWNER)
    assert first["ok"] and replay["replayed"] is True and second_version["ok"]
    assert calls == ["sha256:" + "a" * 64, "sha256:" + "b" * 64]


def test_adopt_replays_only_for_the_same_draft_version(store, monkeypatch):
    bid = artifacts.create_batch("adopt-version", owner=TRUSTED_OWNER)["batch_id"]
    state = {"digest": "sha256:" + "a" * 64}
    calls = []
    monkeypatch.setattr(tools, "_guarded", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tools, "_batch_partition",
                        lambda *_args, **_kwargs: TRUSTED_OWNER)
    monkeypatch.setattr(tools, "_artifact_state_digest",
                        lambda *_args, **_kwargs: state["digest"])
    monkeypatch.setattr(
        tools._adopt, "adopt",
        lambda *_args, **_kwargs: calls.append(state["digest"]) or {
            "ok": True, "approved_caseset": {}, "cases_gate": {}})
    first = tools.adopt(
        batch_id=bid, selected_draft_ids=["d1"], skip_drift_check=True,
        owner=TRUSTED_OWNER)
    replay = tools.adopt(
        batch_id=bid, selected_draft_ids=["d1"], skip_drift_check=True,
        owner=TRUSTED_OWNER)
    state["digest"] = "sha256:" + "b" * 64
    second_version = tools.adopt(
        batch_id=bid, selected_draft_ids=["d1"], skip_drift_check=True,
        owner=TRUSTED_OWNER)
    assert first["ok"] and replay["replayed"] is True and second_version["ok"]
    assert calls == ["sha256:" + "a" * 64, "sha256:" + "b" * 64]


def test_successful_readoption_invalidates_the_previous_bundle(store, monkeypatch):
    bid = artifacts.create_batch("adopt-invalidates", owner=TRUSTED_OWNER)["batch_id"]
    bundle = os.path.join(artifacts.batch_dir(
        bid, owner=TRUSTED_OWNER), "bundle")
    os.makedirs(bundle, exist_ok=True)
    with open(os.path.join(bundle, "bundle.json"), "w", encoding="utf-8") as fh:
        json.dump({"caseset_id": "old"}, fh)
    monkeypatch.setattr(tools, "_guarded", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tools, "_batch_partition",
                        lambda *_args, **_kwargs: TRUSTED_OWNER)
    monkeypatch.setattr(tools, "_artifact_state_digest",
                        lambda *_args, **_kwargs: "sha256:" + "a" * 64)
    monkeypatch.setattr(
        tools._adopt, "adopt", lambda *_args, **_kwargs: {
            "ok": True, "approved_caseset": {"caseset_id": "new"},
            "cases_gate": {}})
    result = tools.adopt(
        batch_id=bid, selected_draft_ids=["d1"], skip_drift_check=True,
        owner=TRUSTED_OWNER)
    assert result["ok"]
    assert not os.path.exists(bundle)


def test_execute_replays_only_for_the_same_bundle_version(store, monkeypatch):
    bid = artifacts.create_batch("execute-version", owner=TRUSTED_OWNER)["batch_id"]
    state = {"digest": "sha256:" + "a" * 64}
    calls = []
    monkeypatch.setattr(tools, "_guarded", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tools, "_batch_partition",
                        lambda *_args, **_kwargs: TRUSTED_OWNER)
    monkeypatch.setattr(tools, "_selected_authorized_write_risk",
                        lambda *_args, **_kwargs: False)
    monkeypatch.setattr(tools, "_bundle_state", lambda *_args, **_kwargs: {
        "bundle_sha256": state["digest"], "caseset_id": "acs-test",
        "compiler_version": "test",
    })

    def fake_execute(*_args, reserved_run_id="", **_kwargs):
        calls.append((state["digest"], reserved_run_id))
        return {"ok": True, "run_id": reserved_run_id,
                "receipt": {"verdict": "PASS"}}

    monkeypatch.setattr(tools._execute, "execute", fake_execute)
    first = tools.execute(batch_id=bid, owner=TRUSTED_OWNER)
    replay = tools.execute(batch_id=bid, owner=TRUSTED_OWNER)
    state["digest"] = "sha256:" + "b" * 64
    second_version = tools.execute(batch_id=bid, owner=TRUSTED_OWNER)
    assert first["ok"] and replay["replayed"] is True and second_version["ok"]
    assert [digest for digest, _run in calls] == [
        "sha256:" + "a" * 64, "sha256:" + "b" * 64]
    assert calls[0][1] != calls[1][1]


def test_atomic_reservation_allows_one_process_side_effect(tmp_path):
    root = str(tmp_path / "process-root")
    bid = artifacts.create_batch("process-race", root=root)["batch_id"]
    marker = str(tmp_path / "side-effects.txt")
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    workers = [ctx.Process(target=_reservation_process,
                           args=(root, bid, marker, start, results))
               for _ in range(4)]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0
    outcomes = [results.get(timeout=2) for _ in workers]
    assert outcomes.count("first") == 1
    assert outcomes.count("replay") == 3
    with open(marker, encoding="utf-8") as fh:
        assert fh.read().splitlines() == ["side-effect"]


def test_same_explicit_key_is_scoped_by_tool(store):
    bid = artifacts.create_batch("tool-scope")["batch_id"]
    with idempotency.reservation(
            bid, "adopt", {}, "same-key") as (key, replay):
        assert replay is None
        idempotency.record(bid, key, "adopt", {"ok": True, "from": "adopt"},
                           params={})
    with idempotency.reservation(
            bid, "execute", {}, "same-key") as (key, replay):
        assert replay is None
        idempotency.record(bid, key, "execute", {"ok": True, "from": "execute"},
                           params={})
    assert idempotency.lookup(
        bid, "same-key", tool="adopt")["result"]["from"] == "adopt"
    assert idempotency.lookup(
        bid, "same-key", tool="execute")["result"]["from"] == "execute"


def test_failed_reservation_without_record_is_released_for_retry(store):
    bid = artifacts.create_batch("retry-after-failure")["batch_id"]
    with idempotency.reservation(
            bid, "execute", {}, "retry-key") as (_key, replay):
        assert replay is None
        # 模拟副作用入口返回失败：不 record，离开 context 即释放 reservation。
    with idempotency.reservation(
            bid, "execute", {}, "retry-key") as (key, replay):
        assert replay is None
        idempotency.record(bid, key, "execute", {"ok": True}, params={})


def test_explicit_key_with_different_input_is_a_conflict(store):
    bid = artifacts.create_batch("explicit-conflict")["batch_id"]
    with idempotency.reservation(
            bid, "execute", {"case_ids": ["a"]}, "operator-key") as (key, replay):
        assert replay is None
        idempotency.record(
            bid, key, "execute", {"ok": True, "run_id": "r-a"},
            params={"case_ids": ["a"]})

    with idempotency.reservation(
            bid, "execute", {"case_ids": ["b"]}, "operator-key") as (_key, replay):
        assert replay is not None
        assert replay["ok"] is False
        assert replay["code"] == "E_IDEMPOTENCY_CONFLICT"
