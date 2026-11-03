# -*- coding: utf-8 -*-
"""L5/L6 部署锚与反向对账判据（设计稿 §6.4/§6.5）。

用**真 git 仓**建靶（临时目录 + 一个本地"远端"），不打桩 —— 这道闸的全部价值
在于它对 git 的真实状态判得对，打了桩就只验了我们对 git 的想象。
"""
from __future__ import annotations

import subprocess

import pytest

from server.journey import code_anchor


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


@pytest.fixture()
def repo_pair(tmp_path):
    """建一个 bare「远端」+ 一个克隆，返回克隆路径。"""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "clone", str(remote), str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    (work / "a.py").write_text("X = 1\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "push", "origin", "HEAD:refs/heads/main")
    _git(work, "fetch", "origin")
    return work


def test_pushed_clean_tree_anchors_at_l5(repo_pair):
    a = code_anchor.deploy_anchor(str(repo_pair))
    assert a["level"] == "L5", a
    assert a["checks"]["origin_reachable"] is True
    assert a["checks"]["dirty_worktree"] is False
    assert a["commit"] and len(a["commit"]) == 40


def test_unpushed_commit_degrades(repo_pair):
    """只存在于本机的 commit **不许**当部署锚。

    它长得和一个正常锚一模一样，所以这条不能靠人眼看 —— 事后没人复现得了。
    """
    (repo_pair / "b.py").write_text("Y = 2\n", encoding="utf-8")
    _git(repo_pair, "add", "-A")
    _git(repo_pair, "commit", "-m", "local only")

    a = code_anchor.deploy_anchor(str(repo_pair))
    assert a["level"] == code_anchor.DEGRADED_TO_L1
    assert a["checks"]["origin_reachable"] is False
    assert "origin/*" in a["reason"]
    assert "commit" not in a, "降级时不许还把 commit 递出去当锚"


def test_dirty_worktree_degrades_even_when_pushed(repo_pair):
    """工作树脏 = 跑的代码与那个 commit 不是一回事。

    这条**单列**，不靠上一条顺带：一个已 push 的 commit 配一棵脏工作树，
    origin 可达性检查会全绿，而锚照样是假的。
    """
    (repo_pair / "a.py").write_text("X = 999\n", encoding="utf-8")
    a = code_anchor.deploy_anchor(str(repo_pair))
    assert a["level"] == code_anchor.DEGRADED_TO_L1
    assert a["checks"]["origin_reachable"] is True, "前提：origin 可达这一关是过的"
    assert a["checks"]["dirty_worktree"] is True
    assert "未提交" in a["reason"]


def test_non_repo_degrades(tmp_path):
    a = code_anchor.deploy_anchor(str(tmp_path / "nowhere"))
    assert a["level"] == code_anchor.DEGRADED_TO_L1


def test_degrade_has_only_two_levels(repo_pair):
    """`level` 只有 L5 与 degraded_to_l1 两种取值，没有"大概锚住了"。"""
    a = code_anchor.deploy_anchor(str(repo_pair))
    assert a["level"] in ("L5", code_anchor.DEGRADED_TO_L1)


# ── 反向对账 ──────────────────────────────────────────────────────────────
def test_found_needle_is_implemented(repo_pair):
    (repo_pair / "svc.py").write_text(
        "def handle_korean_voice():\n    return 'ko'\n", encoding="utf-8")
    m = code_anchor.build_requirement_code_map(
        [{"rule_id": "R1", "statement": "支持韩语音色"}], str(repo_pair),
        needles_of=lambda r: ["handle_korean_voice"])
    row = m["rules"][0]
    assert row["verdict"] == "implemented"
    assert row["anchors"]["handle_korean_voice"][0].startswith("svc.py:")


def test_missing_needle_is_not_found_not_a_defect(repo_pair):
    m = code_anchor.build_requirement_code_map(
        [{"rule_id": "R9", "statement": "某个没实现的东西"}], str(repo_pair),
        needles_of=lambda r: ["definitely_absent_symbol_zzz"])
    assert m["rules"][0]["verdict"] == "not_found"
    assert "不当缺陷" in m["note"], "not_found 是 gap 不是缺陷，口径要写在产物上"


def test_no_needles_says_we_did_not_look():
    """没给检索词时的措辞必须是「我们没找」而不是「代码里没有」。

    两者在覆盖图上会导向完全不同的动作：前者去补检索词，后者去开发那边提缺陷。
    """
    m = code_anchor.build_requirement_code_map(
        [{"rule_id": "R1", "statement": "x"}], ".", needles_of=None)
    assert "我们没找" in m["rules"][0]["why"]


def test_anchor_means_disclaimer_is_on_every_row(repo_pair):
    """每行都带「命中≠行为对」的口径声明 —— `implemented` 太容易被读成"做对了"。"""
    m = code_anchor.build_requirement_code_map(
        [{"rule_id": "R1", "statement": "x"}], str(repo_pair),
        needles_of=lambda r: ["X"])
    assert all("不等于其行为符合需求" in r["anchor_means"] for r in m["rules"])
