# -*- coding: utf-8 -*-
"""journey.artifacts 自测：批次状态对象、类型化产物信封、presence-derived stepper。"""
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os

import pytest

from server.journey import artifacts


def _append_run_process(root, batch_id, run_id, owner, start_event, result_queue):
    start_event.wait(10)
    try:
        artifacts.append_run_id(
            batch_id, run_id, owner=owner, root=root)
        result_queue.put((run_id, "ok"))
    except Exception as exc:  # pragma: no cover - parent reports child detail
        result_queue.put((run_id, f"{type(exc).__name__}: {exc}"))


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(artifacts, "BATCHES_ROOT", str(tmp_path / "batches"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", str(tmp_path / "runs"))
    return artifacts


def test_create_and_load_batch(store):
    meta = store.create_batch("白月SMS 一期", base_url="http://127.0.0.1:8047",
                              source_ref="https://www.byhy.net/py/django/req_1/")
    loaded = store.load_batch(meta["batch_id"])
    assert loaded["title"] == "白月SMS 一期"
    assert loaded["owner"] == ""  # 0009 预留字段在场
    assert loaded["run_ids"] == []


def test_concurrent_run_registration_keeps_every_run(store):
    owner = "thread-owner"
    meta = store.create_batch("并发执行", owner=owner)
    run_ids = [f"r-20260813-thread{i:02d}" for i in range(16)]

    with ThreadPoolExecutor(max_workers=len(run_ids)) as pool:
        list(pool.map(
            lambda rid: store.append_run_id(
                meta["batch_id"], rid, owner=owner),
            run_ids,
        ))

    loaded = store.load_batch(meta["batch_id"], owner=owner)
    assert set(loaded["run_ids"]) == set(run_ids)
    assert len(loaded["run_ids"]) == len(run_ids)
    leftovers = [name for name in os.listdir(store.batch_dir(
        meta["batch_id"], owner=owner)) if name.endswith(".tmp")]
    assert leftovers == []


def test_concurrent_process_run_registration_keeps_every_run(tmp_path):
    root = str(tmp_path)
    owner = "process-owner"
    meta = artifacts.create_batch("跨进程并发执行", owner=owner, root=root)
    run_ids = [f"r-20260813-proc{i:02d}" for i in range(6)]
    ctx = multiprocessing.get_context("spawn")
    start_event = ctx.Event()
    result_queue = ctx.Queue()
    workers = [ctx.Process(
        target=_append_run_process,
        args=(root, meta["batch_id"], rid, owner, start_event, result_queue),
    ) for rid in run_ids]
    for worker in workers:
        worker.start()
    start_event.set()
    results = [result_queue.get(timeout=20) for _ in workers]
    for worker in workers:
        worker.join(timeout=20)
        assert worker.exitcode == 0

    assert all(status == "ok" for _rid, status in results), results
    loaded = artifacts.load_batch(meta["batch_id"], owner=owner, root=root)
    assert set(loaded["run_ids"]) == set(run_ids)
    assert len(loaded["run_ids"]) == len(run_ids)


def test_batch_id_path_escape_blocked(store):
    for bad in ("../../etc", "b-20260810-ABC!", "b-x", "..\\..\\x", ""):
        with pytest.raises(store.ArtifactError):
            store.batch_dir(bad)
    with pytest.raises(store.ArtifactError):
        store.run_dir("../escape")


def test_artifact_envelope_stamped_and_checked(store):
    meta = store.create_batch("t")
    bid = meta["batch_id"]
    saved = store.save_artifact(bid, "intake_profile",
                                {"tier": "standard", "artifact": "伪造类型",
                                 "batch_id": "b-99999999-fake00"})
    # 信封字段由本方盖章，payload 抢注无效
    assert saved["artifact"] == "intake_profile"
    assert saved["batch_id"] == bid
    loaded = store.load_artifact(bid, "intake_profile")
    assert loaded["tier"] == "standard"


def test_load_artifact_type_mismatch_rejected(store):
    meta = store.create_batch("t")
    bid = meta["batch_id"]
    store.save_artifact(bid, "intake_profile", {"tier": "checklist"})
    import os
    import shutil
    src = store.artifact_path(bid, "intake_profile")
    dst = store.artifact_path(bid, "business_frame")
    shutil.copy(src, dst)  # 冒充另一类型
    with pytest.raises(store.ArtifactError):
        store.load_artifact(bid, "business_frame")
    os.remove(dst)


def test_save_artifact_requires_existing_batch(store):
    with pytest.raises(store.ArtifactError):
        store.save_artifact("b-20260810-nothere", "intake_profile", {})


def test_unknown_artifact_kind_rejected(store):
    meta = store.create_batch("t")
    with pytest.raises(store.ArtifactError):
        store.save_artifact(meta["batch_id"], "wizard_state", {})


def test_stepper_presence_derived(store):
    meta = store.create_batch("t")
    bid = meta["batch_id"]
    st0 = {s["artifact"]: s["present"] for s in store.stepper(bid)}
    assert not any(st0.values())
    store.save_artifact(bid, "intake_profile", {"tier": "standard"})
    store.save_artifact(bid, "business_frame", {"rules": []})
    st1 = {s["artifact"]: s["present"] for s in store.stepper(bid)}
    assert st1["intake_profile"] and st1["business_frame"]
    assert not st1["approved_caseset"] and not st1["automation_bundle"]
    # stepper 顺序 = 产物账本顺序，automation_bundle 位于 caseset 之后
    order = [s["artifact"] for s in store.stepper(bid)]
    assert order.index("approved_caseset") < order.index("automation_bundle")
    assert order.index("automation_bundle") < order.index("coverage_ledger")


def test_events_append_only(store):
    meta = store.create_batch("t")
    bid = meta["batch_id"]
    store.append_event(bid, {"type": "adopt_confirm", "by": "manager",
                             "note": "self-derived-pending-audit"})
    store.append_event(bid, {"type": "write_confirm", "case_id": "x/R1-C001"})
    rows = store.read_events(bid)
    assert [r["type"] for r in rows] == ["adopt_confirm", "write_confirm"]
    assert all("at" in r for r in rows)


def test_list_batches_includes_stepper(store):
    store.create_batch("a")
    store.create_batch("b")
    rows = store.list_batches()
    assert len(rows) == 2
    assert all("stepper" in r for r in rows)
