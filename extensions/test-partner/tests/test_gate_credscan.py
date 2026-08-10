# -*- coding: utf-8 -*-
"""credential_scan 金标验收（DoD 7：扫描器自身+金标样本离线判）。"""
import json
import zipfile

from server.journey.gates import credential_scan as cs


def test_clean_tree_passes(tmp_path):
    (tmp_path / "receipt.json").write_text(
        json.dumps({"summary": "ok", "digest": "sha256:" + "a" * 64}),
        encoding="utf-8")
    r = cs.scan_tree(str(tmp_path), known_secrets=["88888888"])
    assert r["ok"], r


def test_known_secret_in_text_caught(tmp_path):
    (tmp_path / "report.md").write_text("登录用 byhy / 88888888 成功", encoding="utf-8")
    r = cs.scan_tree(str(tmp_path), known_secrets=["88888888"])
    assert not r["ok"] and r["known_hits"][0]["file"] == "report.md"
    # 红线 3：报告本身不回显凭据值
    assert "88888888" not in json.dumps(r)


def test_known_secret_in_binary_caught(tmp_path):
    with zipfile.ZipFile(tmp_path / "trace.zip", "w") as z:
        z.writestr("network.txt", "POST body: password=88888888", zipfile.ZIP_STORED)
    r = cs.scan_tree(str(tmp_path), known_secrets=["88888888"])
    assert not r["ok"] and any(h["file"] == "trace.zip" for h in r["known_hits"])


def test_custom_high_entropy_string_caught(tmp_path):
    # DoD 7 点名：自定义高熵串（不在已知清单里）也要能被指出
    token = "kJ8x2Qw9zR4tYv7uB3nM5pL6sD1fG0hA"
    (tmp_path / "log.txt").write_text(f"auth header = {token}", encoding="utf-8")
    r = cs.scan_tree(str(tmp_path), known_secrets=[])
    assert not r["ok"]
    assert any(h["token"] == token for h in r["entropy_hits"])


def test_digest_and_placeholder_allowlisted(tmp_path):
    (tmp_path / "bundle.json").write_text(json.dumps({
        "oracle_digest": "sha256:" + "3f" * 32,
        "commit": "49414819651be5fa9f6c888f50a3985042d79632",
        "auth": "Bearer {{token}}",
    }), encoding="utf-8")
    r = cs.scan_tree(str(tmp_path))
    assert r["ok"], r["entropy_hits"]


def test_explicit_allowlist_fail_closed(tmp_path):
    token = "kJ8x2Qw9zR4tYv7uB3nM5pL6sD1fG0hA"
    (tmp_path / "x.txt").write_text(token, encoding="utf-8")
    assert not cs.scan_tree(str(tmp_path))["ok"]           # 默认拦
    assert cs.scan_tree(str(tmp_path), allowlist=[token])["ok"]  # 逐条放行后过


def test_plain_prose_not_flagged(tmp_path):
    (tmp_path / "notes.md").write_text(
        "ThisIsAPerfectlyNormalSentenceAboutTesting the login journey "
        "playwright_trace screenshot http_transcript", encoding="utf-8")
    r = cs.scan_tree(str(tmp_path))
    assert r["ok"], r["entropy_hits"]
