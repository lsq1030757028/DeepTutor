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
    # 只给 token 不给理由仍然拦；逐条 token→reason 才能放行。
    assert not cs.scan_tree(str(tmp_path), allowlist=[token])["ok"]
    r = cs.scan_tree(str(tmp_path), allowlist={token: "公开测试夹具 id"})
    assert r["ok"]
    assert r["allowlisted_hits"][0]["allowlist_reason"] == "公开测试夹具 id"


def test_public_response_ids_are_allowlisted_with_machine_reason(tmp_path):
    public_ids = [
        "req_5d9a11aa-e12a-4a1b-af01-123456789abc",
        "custom_98765432-123a-4bd2-b5bc-abcdef123456",
        "mm_76db97c3d2b54a4f89cdb7d8ae9",
        "5-flash-qiqiuser-4a1b68fc1234567890abcdef12345678",
    ]
    (tmp_path / "response.json").write_text(
        json.dumps({"ids": public_ids}), encoding="utf-8")
    r = cs.scan_tree(str(tmp_path))
    assert r["ok"], r["entropy_hits"]
    assert len(r["allowlisted_hits"]) == len(public_ids)
    assert all(h["allowlist_reason"] for h in r["allowlisted_hits"])


def test_public_id_shape_does_not_hide_a_known_secret(tmp_path):
    secret = "req_5d9a11aa-e12a-4a1b-af01-123456789abc"
    (tmp_path / "leak.json").write_text(secret, encoding="utf-8")
    r = cs.scan_tree(str(tmp_path), known_secrets=[secret])
    assert not r["ok"] and r["known_hits"]


def test_db_snapshot_evidence_path_is_not_a_secret(tmp_path):
    path = "queenie__ko__custom__r5__c001/db_snapshot.json"
    (tmp_path / "bundle.json").write_text(
        json.dumps({"evidence": [path]}), encoding="utf-8")
    r = cs.scan_tree(str(tmp_path))
    assert r["ok"]
    assert r["allowlisted_hits"][0]["allowlist_reason"] == "journey evidence path"


def test_arbitrary_high_entropy_slash_token_is_not_hidden_as_a_path(tmp_path):
    token = "ab12CD34ef56GH78/ij90KL12mn34OP56"
    (tmp_path / "x.txt").write_text(token, encoding="utf-8")
    assert not cs.scan_tree(str(tmp_path))["ok"]


def test_plain_prose_not_flagged(tmp_path):
    (tmp_path / "notes.md").write_text(
        "ThisIsAPerfectlyNormalSentenceAboutTesting the login journey "
        "playwright_trace screenshot http_transcript", encoding="utf-8")
    r = cs.scan_tree(str(tmp_path))
    assert r["ok"], r["entropy_hits"]


# ── case_id 误报（L3 主证据实测撞上）─────────────────────────────────────────


def test_case_id_with_hyphenated_slug_is_not_flagged():
    """`<带连字符的 slug>/R4-C001` 不许被当成高熵凭据。

    实测撞法：既有测试用的 slug 是纯字母 `exectest`，而真实批次用了
    `queenie-ko-main`——URL 路径排除规则要求 `/` 两侧有纯字母词段，
    带连字符的 slug 不满足，于是整个 bundle 因"凭据扫描命中"被拒编译。
    """
    from server.journey.gates import credential_scan as cs
    for cid in ("queenie-ko-main/R4-C001", "queenie-ko-control/R7-C099",
                "a1-b2-c3/R10-C123", "exectest/R1-C001"):
        assert cs._is_allowlisted(cid), cid


def test_case_id_form_matches_the_schema_definition():
    """豁免形态与 schema 的 CASE_ID_RE 同源，不是另抄一份。"""
    from server.journey import schema
    from server.journey.gates import credential_scan as cs
    for cid in ("queenie-ko-main/R4-C001", "x/R1-C001"):
        assert bool(schema.CASE_ID_RE.match(cid)) == bool(cs.CASE_ID_FORM.match(cid))


def test_allowlist_does_not_shield_a_known_secret_shaped_like_a_case_id(tmp_path):
    """豁免只对启发式生效：**known-secret 全量精确匹配不受任何豁免影响**。

    这条单列，因为"加了豁免"最怕的后果不是漏一个高熵串，
    是把一条真凭据顺带放行——那会让扫描从防线变成背书。
    """
    from server.journey.gates import credential_scan as cs
    secret = "queenie-ko-main/R4-C001"
    (tmp_path / "leak.json").write_text(
        '{"note": "%s"}' % secret, encoding="utf-8")
    res = cs.scan_tree(str(tmp_path), known_secrets=[secret])
    assert not res["ok"]
    assert res["known_hits"], "已知凭据即使形似 case_id 也必须被抓到"
