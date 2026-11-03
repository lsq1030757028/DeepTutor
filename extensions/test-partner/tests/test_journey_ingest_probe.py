# -*- coding: utf-8 -*-
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from server.journey import artifacts, ingest


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_probe_does_not_send_cross_host_redirect_request():
    class Sink(BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self):  # noqa: N802
            type(self).hits += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    sink = _serve(Sink)

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            # 127.0.0.1 -> localhost is deliberately a different host key.
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{sink.server_port}/sink")
            self.end_headers()

        def log_message(self, *_args):
            pass

    source = _serve(Redirect)
    try:
        result = ingest.probe_target(f"http://127.0.0.1:{source.server_port}")
        assert result["reachable"] is False
        assert "越出等价类" in result["error"]
        assert Sink.hits == 0
    finally:
        source.shutdown()
        sink.shutdown()


def test_probe_follows_same_host_redirect_only():
    class SameHost(BaseHTTPRequestHandler):
        hits = []

        def do_GET(self):  # noqa: N802
            type(self).hits.append(self.path)
            if self.path == "/":
                self.send_response(302)
                self.send_header("Location", "/health")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"healthy")

        def log_message(self, *_args):
            pass

    server = _serve(SameHost)
    try:
        result = ingest.probe_target(f"http://127.0.0.1:{server.server_port}")
        assert result["reachable"] is True
        assert result["status"] == 200
        assert SameHost.hits == ["/", "/health"]
    finally:
        server.shutdown()


def test_ingest_rejects_secret_bearing_url_before_probe_or_directory(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(ingest, "probe_target", lambda *_args, **_kwargs: called.append(True))
    secret_url = "https://user:" + "secret@example.com/?token=" + "hidden"
    result = ingest.ingest(
        "unsafe", secret_url,
        source_kind="requirement_doc", source_ref="local",
        requirement_text="requirement", tier="standard", owner="owner-a",
    )
    assert result["ok"] is False
    assert called == []
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []
    assert "secret" not in str(result) and "hidden" not in str(result)


def test_ingest_rejects_high_entropy_requirement_before_creating_batch(
        tmp_path, monkeypatch):
    monkeypatch.setenv(artifacts.ROOT_ENV, str(tmp_path))
    monkeypatch.setattr(ingest, "probe_target", lambda *_args, **_kwargs: {
        "reachable": True, "status": 200,
    })
    sentinel = "tapdToken_Z8m3Qp9Vx2Ks7Ld5Hr4Nw6Bc"
    result = ingest.ingest(
        "unsafe-requirement", "http://127.0.0.1:8047",
        source_kind="tapd", source_ref="story-1",
        requirement_text=f"需求正文误贴了 {sentinel}",
        tier="standard", owner="owner-a",
    )
    assert result["ok"] is False
    assert sentinel not in str(result)
    assert not list(tmp_path.rglob("*"))


def test_probe_failure_tells_the_user_why_and_which_address(tmp_path, monkeypatch):
    """探活失败必须带出**地址**与**原因**（AC3）。

    「接入终点不可达」这五个字对用户没有任何行动价值：域名解析不了、被重定向
    到别的站、协议不对——三种原因三条不同的下一步。糊成一句等于让用户自己猜，
    F-3 那场会话里「静默卡住」的体感就是这么来的。
    """
    from server.journey import ingest as ingest_mod
    from server.journey import tools as tools_mod
    from tests.journey_decision_helper import SECRET, trusted_claims

    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(artifacts, "BATCHES_ROOT", str(tmp_path / "batches"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)

    monkeypatch.setattr(
        ingest_mod, "probe_target",
        lambda url, timeout_s=10: {
            "reachable": False,
            "error": "GET / 重定向落点越出等价类: http://evil.example.com/",
        },
    )
    with artifacts.trusted_owner("unit-test-owner"):
        out = tools_mod.ingest(
            title="探活失败自测", base_url="http://127.0.0.1:9",
            source_kind="requirement_doc", source_ref="local",
            requirement_text="需求正文", tier="standard",
            tier_confirmed_via="test", prepare_requirement_entity=True,
            owner="unit-test-owner", caller_surface="capability",
            _bridge_claims=trusted_claims("journey_ingest", owner="unit-test-owner"),
        )
    assert not out["ok"]
    assert out["code"] == "E_INGEST_REJECTED"
    assert "127.0.0.1:9" in out["message"], f"没说清是哪个地址：{out['message']}"
    # 2026-08-29 判据有意翻面：AC3 要保的是「三种原因三条不同的下一步」，
    # 原实现把它落成「把 probe 的原始串拼进 message」，于是用户在界面上读到
    # `<urlopen error [WinError 10054] …>`（V5 实测 6 条）。归因分类留在 message，
    # 原始串搬去 machine——**可区分性没丢，泄漏没了**。
    assert "跳转" in out["message"], f"没说清失败原因：{out['message']}"
    assert "重定向落点越出等价类" not in out["message"], "原始探活串不该进用户可见文案"
    assert "重定向落点越出等价类" in out["machine"], "原始归因必须留给模型"
    assert out["detail"]["probe_error"]


def test_normal_intake_probe_failure_also_says_why(tmp_path, monkeypatch):
    """常规接入路径（非 prepare）也要说出原因（独立复核阻断项 B1）。

    第一版只修了写需求走的 `prepare_requirement_entity=True` 那条路径，
    自证件却把 AC3 整条标成「闭合」。常规接入是绝大多数会话实际走的路，
    它当时仍然只回「接入终点不可达，不建批次」，原因只在 `probe` 里——
    调用方拿到的是 error 串，会照着它对用户说话。

    只修了两条路径里的一条就宣布闭合，正是「文件存在不等于能力生效」的形态。
    """
    monkeypatch.setattr(
        ingest, "probe_target",
        lambda url, timeout_s=10: {
            "reachable": False,
            "error": "<urlopen error [Errno 11001] getaddrinfo failed>",
        },
    )
    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(artifacts, "BATCHES_ROOT", str(tmp_path / "batches"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", str(tmp_path / "runs"))
    with artifacts.trusted_owner("unit-test-owner"):
        out = ingest.ingest(
            "常规接入探活失败", "http://no-such-host.invalid",
            source_kind="requirement_doc", source_ref="local",
            requirement_text="需求正文", tier="standard",
            tier_confirmed_via="test", owner="unit-test-owner",
        )
    assert not out["ok"]
    assert "getaddrinfo failed" in out["error"], f"没说清失败原因：{out['error']}"
    assert "no-such-host.invalid" in out["error"], f"没说清是哪个地址：{out['error']}"
