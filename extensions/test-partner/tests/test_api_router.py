"""测试工作台 API router 的测试。

这个 router 住在 `deeptutor/api/routers/test_workbench.py`（上游目录），
但测试放在这里，因为**只有这一侧的 CI 会跑**：
上游 `tests.yml` 的路径过滤是 `deeptutor/** web/** tests/**`，
我们的 `test-partner.yml` 过滤的是 `extensions/test-partner/**`——
router 的改动能触发上游那条，但上游那条跑的是它自己的测试套件，不含本文件。
放这里，改 router 的 PR 至少还有我们这条闸看着（前提是同时改了 extensions 侧，
否则两条闸都不触发——这一点是已知缺口，记在此处）。

覆盖三件事，都是错了会真出事的：
1. 扩展装没装得上（`health`）；
2. **每用户隔离**（决策 0009）——不同用户拿到不同目录，这条错了就是串数据；
3. 批次 id 的路径穿越防线。
"""

from __future__ import annotations

from pathlib import Path
import sys
import types

import pytest

# router 在仓库根下的 deeptutor/ 里，需要把仓库根塞进 sys.path。
# conftest.py 只塞了 extensions/test-partner。
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

tw = pytest.importorskip(
    "deeptutor.api.routers.test_workbench",
    reason="只在 fork 仓（有 deeptutor/ 包）里跑；归档仓里跳过",
)


def test_extension_is_loaded():
    """扩展装上了。装不上时 health 要说清是哪条路径没找到，而不是只报 503。"""
    h = tw.health()
    assert h["extension_loaded"] is True, h["error"]
    assert h["error"] is None
    assert h["extension_path"].endswith("test-partner")


def _fake_user(user_id: str, root: Path):
    """造一个最小的 CurrentUser 替身，只要有 .scope.root 就够。"""
    return types.SimpleNamespace(scope=types.SimpleNamespace(root=root))


def test_two_users_get_different_delivery_roots(tmp_path, monkeypatch):
    """决策 0009 的硬断言：A 和 B 的批次目录不能是同一个。

    这条错了不是"不整洁"，是 A 在批次列表里看得见 B 的用例——
    而用例里带着 B 那份 HAR 的实例值（含 PII，见 BB-424）。
    """
    a_root, b_root = tmp_path / "users" / "alice", tmp_path / "users" / "bob"

    monkeypatch.setattr(tw, "get_current_user_or_none", lambda: _fake_user("alice", a_root))
    got_a = tw._deliveries_root()

    monkeypatch.setattr(tw, "get_current_user_or_none", lambda: _fake_user("bob", b_root))
    got_b = tw._deliveries_root()

    assert got_a != got_b
    assert Path(got_a).is_relative_to(a_root)
    assert Path(got_b).is_relative_to(b_root)
    # 两边都真建出来了——目录不存在时后面 scan 会走到别的分支，掩盖隔离问题
    assert Path(got_a).is_dir() and Path(got_b).is_dir()


def test_no_user_falls_back_to_admin_root_not_to_a_shared_dir(tmp_path, monkeypatch):
    """没有当前用户时（AUTH_ENABLED=false 的本机形态）落到 admin 工作区。

    注意这**不是**兜底掩盖：router 在 main.py 里带 dependencies=_auth 注册，
    正常请求一定有 user。这条只保证"没有 user 时不会落到某个所有人共享的路径"。
    """
    monkeypatch.setattr(tw, "get_current_user_or_none", lambda: None)
    monkeypatch.setattr(tw, "ADMIN_WORKSPACE_ROOT", tmp_path / "data")
    root = tw._deliveries_root()
    assert Path(root).is_relative_to(tmp_path / "data")


@pytest.mark.parametrize("bad", ["../x", "a/b", "..", "/abs", "a\\b"])
def test_delivery_id_traversal_is_rejected(bad, tmp_path, monkeypatch):
    """批次 id 非法直接 400，不做「清洗后继续」——那是路径穿越的常见入口。"""
    from fastapi import HTTPException

    monkeypatch.setattr(tw, "get_current_user_or_none", lambda: _fake_user("alice", tmp_path))
    with pytest.raises(HTTPException) as exc:
        tw.get_delivery(bad)
    assert exc.value.status_code == 400


def test_listing_an_empty_workspace_does_not_blow_up(tmp_path, monkeypatch):
    """新用户第一次进来，目录是空的，列表要正常返回空而不是报错。"""
    monkeypatch.setattr(tw, "get_current_user_or_none", lambda: _fake_user("newbie", tmp_path))
    out = tw.list_deliveries()
    assert isinstance(out, dict)
    assert out.get("deliveries") == []
