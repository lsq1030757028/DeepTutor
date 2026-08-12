# -*- coding: utf-8 -*-
"""process_registry 自测 —— BB-501 闭环（设计稿 §7.6 / DoD#23 / ADR-M2-01 G6）。

缺陷原文：`reap_stale` 只在 `os.path.isdir(run_dir)` 成立时才回收；pytest 清掉 tmp
目录后登记标记随目录被删，**PID 从此无人回收**（实测遗留 9 个 chromium 主进程）。

本文件盯三件事：
1. 登记时写了根级镜像（run 目录被删后还认得出那些 PID）；
2. 「目录消失」分支真的回收，且**只回收自己登记的 PID**（禁按名广杀）；
3. `open_trace` 在测试里**不得真起进程**。
"""
import json
import os

import pytest

from server.journey import artifacts
from server.journey import process_registry as preg


def test_pid_alive_probe_is_non_destructive_for_current_process():
    """Windows 回归：存活探针不能用 os.kill(pid, 0) 把被检查进程终止。"""
    assert preg._pid_alive(os.getpid()) is True


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(preg, "_process_instance_id", lambda pid: f"test:{pid}")
    return str(tmp_path)


def _mirror_rows(root):
    path = os.path.join(root, preg.ACTIVE_DIR_NAME, preg.MIRROR_FILE)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


# ── 1. 登记双写 ─────────────────────────────────────────────────────────────


def test_register_writes_both_run_ledger_and_root_mirror(root, tmp_path):
    rd = str(tmp_path / "runs" / "r-1")
    preg.register_pid(rd, 424242, "trace-viewer")
    assert os.path.isfile(os.path.join(rd, "pids.json"))
    rows = _mirror_rows(root)
    assert [r["pid"] for r in rows] == [424242]


def test_deregister_marks_closed_in_mirror_too(root, tmp_path):
    """销记两处都要写，否则镜像会把已正常关掉的 PID 当遗留去杀。"""
    rd = str(tmp_path / "runs" / "r-1")
    preg.register_pid(rd, 424242, "trace-viewer")
    preg.deregister_pid(rd, 424242)
    assert preg.mirrored_live_pids(rd) == []


def test_mirror_survives_run_dir_deletion(root, tmp_path):
    """**这就是 BB-501 的核心**：run 目录没了，线索还在。"""
    import shutil
    rd = str(tmp_path / "runs" / "r-1")
    preg.register_pid(rd, 424242, "chromium")
    shutil.rmtree(rd)
    assert not os.path.isdir(rd)
    assert preg.mirrored_live_pids(rd) == [424242]


# ── 2. 兜底回收：只杀自己登记的 PID ─────────────────────────────────────────


def test_orphan_reap_kills_only_its_own_registered_pids(root, tmp_path, monkeypatch):
    """构造「run_dir 已删但 PID 仍在」，断言该 PID 被回收且**只有它**被回收。"""
    killed = []
    monkeypatch.setattr(preg, "_kill_registered",
                        lambda pid, instance_id: (killed.append(pid), True)[1])
    mine = str(tmp_path / "runs" / "r-mine")
    other = str(tmp_path / "runs" / "r-other")
    preg.register_pid(mine, 111, "chromium")
    preg.register_pid(other, 222, "chromium")

    out = preg.reap_orphans(mine)
    assert out["reaped"] == [111]
    assert killed == [111], "回收越界：动了别的 run 登记的 PID"


def test_orphan_reap_does_not_prefix_match_run_dirs(root, tmp_path, monkeypatch):
    """`r-1` 与 `r-11` 是两个 run。前缀匹配会误伤——这里钉死是全等匹配。"""
    killed = []
    monkeypatch.setattr(preg, "_kill_registered",
                        lambda pid, instance_id: (killed.append(pid), True)[1])
    preg.register_pid(str(tmp_path / "runs" / "r-1"), 111, "chromium")
    preg.register_pid(str(tmp_path / "runs" / "r-11"), 222, "chromium")
    preg.reap_orphans(str(tmp_path / "runs" / "r-1"))
    assert killed == [111]


def test_reap_stale_falls_back_to_mirror_when_dir_is_gone(root, tmp_path, monkeypatch):
    """旧版在这里直接跳过（`if run_dir and os.path.isdir(run_dir)`），PID 就此无人认领。"""
    killed = []
    monkeypatch.setattr(preg, "_kill_registered",
                        lambda pid, instance_id: (killed.append(pid), True)[1])
    rd = str(tmp_path / "runs" / "r-gone")
    preg.register_pid(rd, 999, "chromium")
    # 活跃标记指向一个已经不存在的目录，且宿主 PID 已死
    active = os.path.join(root, preg.ACTIVE_DIR_NAME, "r-gone.json")
    with open(active, "w", encoding="utf-8") as fh:
        json.dump({"pid": 2 ** 31 - 1, "run_dir": rd, "at": 0}, fh)

    cleaned = preg.reap_stale()
    assert "r-gone.json" in cleaned
    assert killed == [999]


def test_reaped_pids_are_not_retried_next_round(root, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(preg, "_kill_registered",
                        lambda pid, instance_id: (calls.append(pid), True)[1])
    rd = str(tmp_path / "runs" / "r-1")
    preg.register_pid(rd, 555, "chromium")
    preg.reap_orphans(rd)
    preg.reap_orphans(rd)
    assert calls == [555]


def test_pid_reuse_is_never_killed(root, tmp_path, monkeypatch):
    """登记后 PID 被复用时，当前无关进程不得进入终止函数。"""
    rd = str(tmp_path / "runs" / "r-reused")
    preg.register_pid(rd, 777, "chromium")
    monkeypatch.setattr(preg, "_process_instance_id", lambda pid: "test:new-instance")
    killed = []
    monkeypatch.setattr(preg, "_kill_registered",
                        lambda pid, instance_id: (killed.append(pid), True)[1])

    out = preg.reap_orphans(rd)

    assert out["identity_mismatch"] == [777]
    assert out["reaped"] == []
    assert killed == []


def test_legacy_pid_without_instance_identity_fails_closed(root, tmp_path, monkeypatch):
    """旧台账只有 PID 时宁可少回收，也不能猜测并终止当前进程。"""
    rd = str(tmp_path / "runs" / "r-legacy")
    os.makedirs(rd, exist_ok=True)
    with open(os.path.join(rd, "pids.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"pid": 888, "kind": "chromium", "at": 1}) + "\n")
    killed = []
    monkeypatch.setattr(preg, "_kill_registered",
                        lambda pid, instance_id: (killed.append(pid), True)[1])

    out = preg.reap_run(rd)

    assert out["identity_unverified"] == [888]
    assert out["reaped"] == []
    assert killed == []


def test_mirror_file_is_not_counted_as_an_active_slot(root, tmp_path):
    """镜像台账与活跃标记同住 `_active/`，别把它数成一个占位的 run。"""
    preg.register_pid(str(tmp_path / "runs" / "r-1"), 1, "chromium")
    a = preg.acquire_slot("r-20260811-aaaaaa", str(tmp_path / "runs" / "r-a"))
    b = preg.acquire_slot("r-20260811-bbbbbb", str(tmp_path / "runs" / "r-b"))
    assert a["ok"] and b["ok"]


def test_no_kill_by_process_name_anywhere():
    """工作区既有纪律：禁按进程名广杀（曾误杀无关进程）。

    盯源码而不是行为——按名广杀这种事一旦写进去，行为测试很难覆盖到。
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "server", "journey", "process_registry.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    for banned in ("taskkill", "pkill", "killall", "/IM ", "Get-Process"):
        assert banned not in source, f"出现按名杀进程的痕迹：{banned}"


# ── 3. open_trace 在测试里不得真起进程 ──────────────────────────────────────


def test_open_trace_does_not_spawn_real_process_in_tests(root, tmp_path, monkeypatch):
    """BB-501 的测试侧：改打桩 + 断言真 Popen 调用数 == 0。

    原用例直接调真 `jc.open_trace()` → 真起 `playwright show-trace` GUI 子进程，
    跑一次测试套就漏一批。
    """
    from server.gateway import journey_console as jc

    spawned = []

    class _FakeProc:
        pid = 31337

    def _fake_popen(cmd, **kwargs):
        spawned.append(cmd)
        return _FakeProc()

    real_popen_calls = []
    monkeypatch.setattr(jc.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(preg, "register_pid",
                        lambda rd, pid, kind, root=None: real_popen_calls.append(pid))

    run_id = artifacts.new_run_id()
    rd = artifacts.run_dir(run_id, create=True)
    os.makedirs(os.path.join(rd, "case__001"))
    with open(os.path.join(rd, "case__001", "trace.zip"), "w") as fh:
        fh.write("PK\x03\x04 fake")

    r = jc.open_trace(run_id, "case__001/trace.zip")
    assert r["ok"] and r["started"] is True
    assert len(spawned) == 1                    # 起的是桩
    assert real_popen_calls == [31337]          # 且**登记发生了**
    assert "playwright show-trace" in r["command"]


def test_open_trace_registers_the_pid_it_spawns(root, tmp_path, monkeypatch):
    """比 bank 记录更尖的一条：此前 `preg.register_pid` 根本不存在，
    调用抛 AttributeError 被大 except 吞掉——于是 viewer **一次都没被登记过**。
    泄漏不是"登记了没回收"，是"压根没登记"。"""
    from server.gateway import journey_console as jc

    class _FakeProc:
        pid = 4242

    monkeypatch.setattr(jc.subprocess, "Popen", lambda cmd, **kw: _FakeProc())
    run_id = artifacts.new_run_id()
    rd = artifacts.run_dir(run_id, create=True)
    os.makedirs(os.path.join(rd, "case__001"))
    with open(os.path.join(rd, "case__001", "trace.zip"), "w") as fh:
        fh.write("PK")

    jc.open_trace(run_id, "case__001/trace.zip")
    assert 4242 in preg.live_registered_pids(rd)
    assert 4242 in preg.mirrored_live_pids(rd)


def test_register_pid_exists_on_the_module_the_caller_uses():
    """`journey_console` 调的是 `preg.register_pid`——这个名字必须真在这个模块上。"""
    assert callable(getattr(preg, "register_pid", None))
    assert callable(getattr(preg, "deregister_pid", None))
