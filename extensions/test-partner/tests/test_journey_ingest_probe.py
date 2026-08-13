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
