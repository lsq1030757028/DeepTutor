"""任务台账的回归测试。

台账刻意不碰 asyncio，所以这里不需要事件循环就能验完
状态机、取消语义和按用户隔离——而这三样错了都会真出事。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

from server.generate.jobs import (
    CANCELLED,
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    JobStore,
)


# ── 按用户隔离：错了就是串数据 ───────────────────────────────────────────

def test_another_owner_cannot_read_your_job():
    """A 能用 job_id 查到 B 的结果的话，B 那份 HAR 的业务数据就漏了。"""
    store = JobStore()
    job = store.create("alice")
    assert store.get(job.id, "alice") is not None
    assert store.get(job.id, "bob") is None


def test_wrong_owner_is_indistinguishable_from_not_found():
    """不能返回 403——那等于告诉对方"这个 id 是存在的"。"""
    store = JobStore()
    job = store.create("alice")
    assert store.get(job.id, "bob") is None
    assert store.get("根本不存在的id", "bob") is None


def test_another_owner_cannot_cancel_your_job():
    store = JobStore()
    job = store.create("alice")
    assert store.request_cancel(job.id, "bob") is None
    assert store.get(job.id, "alice").cancel_requested is False


def test_job_ids_are_unguessable():
    """递增 id 的话，别人猜号就能试探出你跑过几个任务。"""
    store = JobStore()
    ids = {store.create("alice").id for _ in range(20)}
    assert len(ids) == 20
    assert all(len(i) > 12 for i in ids)
    assert not any(i.isdigit() for i in ids)


def test_listing_only_returns_your_own():
    store = JobStore()
    store.create("alice"); store.create("alice"); store.create("bob")
    assert len(store.list_for("alice")) == 2
    assert len(store.list_for("bob")) == 1


# ── 终态不可变：没有这条就会出现"我取消了它却还是跑完了" ─────────────────

def test_terminal_state_rejects_further_writes():
    """一个跑完才返回的慢协程,不能把已取消的任务改回 done。"""
    store = JobStore()
    job = store.create("alice")
    store.update(job.id, state=CANCELLED)
    store.update(job.id, state=DONE, result={"cases": [1]})
    assert store.get(job.id, "alice").state == CANCELLED
    assert store.get(job.id, "alice").result is None


def test_failed_is_also_terminal():
    store = JobStore()
    job = store.create("alice")
    store.update(job.id, state=FAILED, error="炸了")
    store.update(job.id, state=RUNNING)
    assert store.get(job.id, "alice").state == FAILED
    assert store.get(job.id, "alice").error == "炸了"


def test_progress_updates_work_while_running():
    store = JobStore()
    job = store.create("alice")
    store.update(job.id, state=RUNNING, stage="detail", done=2, total=6)
    got = store.get(job.id, "alice")
    assert (got.stage, got.done, got.total) == ("detail", 2, 6)


# ── 取消是请求不是命令 ───────────────────────────────────────────────────

def test_cancel_marks_a_request_not_an_immediate_state_change():
    """协程要在下一个检查点自己看见它才停;立刻改成 cancelled 会与

    真实情况不符——那一刻它其实还在跑。
    """
    store = JobStore()
    job = store.create("alice")
    store.update(job.id, state=RUNNING)
    store.request_cancel(job.id, "alice")
    got = store.get(job.id, "alice")
    assert got.cancel_requested is True
    assert got.state == RUNNING


def test_cancelling_a_finished_job_changes_nothing():
    store = JobStore()
    job = store.create("alice")
    store.update(job.id, state=DONE, result={"cases": []})
    store.request_cancel(job.id, "alice")
    got = store.get(job.id, "alice")
    assert got.state == DONE
    assert got.cancel_requested is False, "它已经发生过了,不该被改成取消"


# ── 淘汰：只丢已结束的 ───────────────────────────────────────────────────

def test_eviction_never_drops_a_running_job():
    """在跑的任务因为超额被丢掉的话,用户轮询会拿到 404,而它其实还在花钱。"""
    store = JobStore(max_per_owner=3)
    running = [store.create("alice") for _ in range(5)]
    for job in running:
        store.update(job.id, state=RUNNING)
    for job in running:
        assert store.get(job.id, "alice") is not None


def test_eviction_drops_oldest_finished_first():
    store = JobStore(max_per_owner=2)
    old = store.create("alice")
    store.update(old.id, state=DONE)
    for _ in range(3):
        j = store.create("alice")
        store.update(j.id, state=DONE)
    assert store.get(old.id, "alice") is None
    assert len(store.list_for("alice")) <= 2


def test_eviction_is_per_owner():
    store = JobStore(max_per_owner=2)
    for _ in range(4):
        j = store.create("alice")
        store.update(j.id, state=DONE)
    b = store.create("bob")
    assert store.get(b.id, "bob") is not None


# ── 并发计数：给"一次只让跑一个"这类闸用 ─────────────────────────────────

def test_create_if_idle_is_atomic_under_a_same_owner_thread_race(monkeypatch):
    """同一用户同时点很多次，也只能有一个请求拿到花钱任务。"""
    store = JobStore()
    workers = 16
    barrier = threading.Barrier(workers)
    count_barrier = threading.Barrier(workers)

    # 若实现退回旧的 running_count→create，两段之间的窗口会被这里稳定放大：
    # 所有线程先各自读到 0，再一起继续。原子实现根本不会调用这个替身。
    original_running_count = store.running_count

    def racing_count(owner):
        value = original_running_count(owner)
        count_barrier.wait()
        return value

    monkeypatch.setattr(store, "running_count", racing_count)

    def create():
        barrier.wait()
        return store.create_if_idle("alice")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = list(pool.map(lambda _: create(), range(workers)))

    assert sum(job is not None for job in jobs) == 1
    assert len(store.list_for("alice")) == 1


def test_create_if_idle_race_is_scoped_per_owner():
    """Alice 的原子闸不能把 Bob 也挡住；每个 owner 各有一个赢家。"""
    store = JobStore()
    owners = ["alice"] * 8 + ["bob"] * 8
    barrier = threading.Barrier(len(owners))

    def create(owner):
        barrier.wait()
        return owner, store.create_if_idle(owner)

    with ThreadPoolExecutor(max_workers=len(owners)) as pool:
        results = list(pool.map(create, owners))

    for owner in {"alice", "bob"}:
        assert sum(job is not None for got, job in results if got == owner) == 1
        assert store.running_count(owner) == 1


def test_running_count_only_counts_unfinished():
    store = JobStore()
    a = store.create("alice")
    b = store.create("alice")
    store.update(a.id, state=RUNNING)
    store.update(b.id, state=DONE)
    assert store.running_count("alice") == 1
    assert store.running_count("bob") == 0


# ── 对外投影不带内部字段 ─────────────────────────────────────────────────

def test_public_projection_hides_owner():
    """owner 回显给页面没有价值,只有风险。"""
    store = JobStore()
    job = store.create("alice")
    payload = job.public()
    assert "owner" not in payload
    assert payload["job_id"] == job.id
    assert payload["state"] == PENDING
