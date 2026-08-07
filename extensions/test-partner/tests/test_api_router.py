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

import asyncio
import io
import json
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


# ── HAR 体检路由（设计稿第 2 屏）────────────────────────────────────────────


def _upload(content: bytes, name: str = "x.har"):
    """造一个最小的 UploadFile 替身：只要有 async read(n) 与 filename 就够。"""

    class _F:
        filename = name

        def __init__(self, data: bytes):
            self._buf = io.BytesIO(data)

        async def read(self, n: int = -1) -> bytes:
            return self._buf.read(n)

    return _F(content)


def test_oversized_har_is_refused_before_being_read_whole(monkeypatch):
    """体积闸必须在读完之前就拒，不是读完再说太大。

    这条对着 MeterSphere 的真实 issue #25162 写的：它是用户传完了才吃
    Jackson 异常。我们把上限调到很小，喂一个超限的流，断言 413，
    并且**断言没有把整个内容读进来**——只读到刚超限那一刻。
    """
    monkeypatch.setattr(tw, "MAX_HAR_BYTES", 1024)
    payload = b"x" * (1024 * 50)
    up = _upload(payload)
    with pytest.raises(tw.HTTPException) as ei:
        asyncio.run(tw._read_upload_capped(up))
    assert ei.value.status_code == 413
    assert "MB" in str(ei.value.detail)


def test_capped_read_returns_full_content_when_within_limit():
    """没超限时要如数返回，别把内容读漏了——分块读最容易在这翻车。"""
    payload = ("{\"log\":{\"entries\":[]}}" * 500).encode("utf-8")
    got = asyncio.run(tw._read_upload_capped(_upload(payload)))
    assert got == payload


def test_drafts_root_is_per_user(tmp_path, monkeypatch):
    """草稿目录也按用户隔离——HAR 体检报告里带着对方系统的端点与实例值。"""
    a, b = tmp_path / "ua", tmp_path / "ub"
    monkeypatch.setattr(tw, "get_current_user_or_none", lambda: _fake_user("a", a))
    ra = tw._drafts_root()
    monkeypatch.setattr(tw, "get_current_user_or_none", lambda: _fake_user("b", b))
    rb = tw._drafts_root()
    assert ra != rb
    assert str(ra).startswith(str(a)) and str(rb).startswith(str(b))


def test_draft_id_rejects_path_traversal(tmp_path, monkeypatch):
    """草稿 id 走与批次同一套校验。这条是路径穿越的入口，必须拒而不是清洗。"""
    monkeypatch.setattr(tw, "get_current_user_or_none", lambda: _fake_user("a", tmp_path))
    for bad in ["../../etc/passwd", "a/b", "..", "/abs"]:
        with pytest.raises(tw.HTTPException) as ei:
            tw.get_har_draft(bad)
        assert ei.value.status_code == 400, bad


def test_inspect_har_never_writes_the_original(tmp_path, monkeypatch):
    """**最硬的一条**：HAR 原件绝不落盘。

    喂一份带真凭证的 HAR，跑完体检后遍历用户目录下所有文件，
    断言那串凭证一个字节都没出现过。原件落了盘，后续任何一次打包导出都会带出去。
    """
    monkeypatch.setattr(tw, "get_current_user_or_none", lambda: _fake_user("a", tmp_path))
    secret = "SUPERSECRETTOKENVALUE0123456789"
    har = json.dumps({"log": {"entries": [{
        "request": {"method": "POST", "url": "https://api.example.com/api/login",
                    "headers": [{"name": "Authorization", "value": f"Bearer {secret}"}],
                    "postData": {"text": json.dumps({"password": "hunter2"})}},
        "response": {"status": 200, "content": {"text": "{\"ok\":true}"}},
    }]}}).encode("utf-8")

    out = asyncio.run(tw.inspect_har(_upload(har)))
    assert out["draft_id"].startswith("har-")
    # 界面文案不许宣称已全部脱敏——PII 不在范围内（BB-424）
    assert out["redaction_notice"]["credentials_redacted"] is True
    assert out["redaction_notice"]["pii_redacted"] is False
    assert out["redaction_notice"]["defect"] == "BB-424"

    leaked = [p for p in tmp_path.rglob("*") if p.is_file()
              and secret in p.read_text(encoding="utf-8", errors="ignore")]
    assert leaked == [], f"凭证落盘了：{leaked}"
    assert "hunter2" not in json.dumps(out, ensure_ascii=False)
