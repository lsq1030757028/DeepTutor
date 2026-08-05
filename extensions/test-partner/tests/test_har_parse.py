"""parse_har 的离线测试：正常体检、坏损输入、脱敏硬约束。"""

from __future__ import annotations

import json
import os

import pytest

from server import har_parse

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
SAMPLE_HAR = os.path.join(FIXTURES, "sample.har")

#: 样例 HAR 里真实存在的凭证值——它们一个字都不许出现在报告里
SECRETS = (
    "eyJhbGciOiJIUzI1NiJ9.SUPERSECRETTOKENVALUE0123456789.sigsigsig",
    "SUPERSECRETTOKENVALUE0123456789",
    "P@ssw0rd-secret-12345",
    "tk-9f8e7d6c5b4a39281706",
    "ABCDEF0123456789SESSIONVAL",
    "ab12cd34ef56ab78",
    "aG9sZFRoaXNTZWNyZXRUaWNrZXQxMjM0NQ",
)


@pytest.fixture(scope="module")
def report():
    return har_parse.parse_har_report(har_path=SAMPLE_HAR)


def _endpoint(report, method, path):
    for ep in report["endpoints"]:
        if ep["method"] == method and ep["path"] == path:
            return ep
    return None


# ── 正常体检 ────────────────────────────────────────────────────────────────

def test_report_ok_and_counts(report):
    assert report["ok"] is True
    assert report["schema"] == har_parse.REPORT_SCHEMA
    assert report["summary"]["entry_count"] == 13
    # 13 条请求里 2 条是噪声（静态 js + /collect/beacon）
    assert report["summary"]["noise_entries_filtered"] == 2
    assert report["summary"]["endpoint_count"] == 9


def test_source_fingerprint_present(report):
    assert report["source"]["kind"] == "path"
    assert report["source"]["name"] == "sample.har"
    assert report["source"]["fingerprint"].startswith("sha256:")


def test_path_params_normalized(report):
    """数字 id 与 32 位十六进制归并成同一个 :id 端点，计数合并。"""
    ep = _endpoint(report, "GET", "/api/v1/orders/:id")
    assert ep is not None and ep["count"] == 2
    assert ep["statuses"] == {"200": 1, "404": 1}


def test_high_entropy_segment_becomes_opaque(report):
    """长不透明段（一次性券形态）不透传，归一化成 :opaque。"""
    assert _endpoint(report, "GET", "/api/v1/share/:opaque") is not None


def test_query_stripped_and_status_distribution(report):
    """带 query 的两次请求归并成一个端点，状态码分布保留。"""
    ep = _endpoint(report, "GET", "/api/v1/orders")
    assert ep is not None and ep["count"] == 2
    assert ep["statuses"] == {"200": 1, "500": 1}
    assert "sign" not in json.dumps(ep, ensure_ascii=False)


def test_noise_paths_filtered(report):
    paths = [ep["path"] for ep in report["endpoints"]]
    assert not any("app.bundle.js" in p for p in paths)
    assert not any("/collect/" in p for p in paths)


def test_business_hosts_resolved(report):
    assert report["hosts"]["business"] == ["api.shop.example.com"]
    assert "third.example.net" in report["hosts"]["other"]
    assert report["hosts"]["business_resolved"] is True


def test_replay_grade_a(report):
    """login 带口令输入 + token 被后续复用 → 档A可重登录。"""
    assert report["replay"]["replayable"] is True
    assert report["replay"]["grade"] == "A"
    assert report["replay"]["grade_label"] == "档A可重登录"


def test_auth_methods_identified_without_values(report):
    kinds = {m["kind"] for m in report["auth"]["methods"]}
    assert {"bearer_token", "custom_token_header", "cookie_session",
            "credential_in_query"} <= kinds
    bearer = next(m for m in report["auth"]["methods"] if m["kind"] == "bearer_token")
    assert bearer["scheme"] == "Bearer" and bearer["requests"] == 4


def test_domains_grouped(report):
    domains = {d["domain"]: d for d in report["domains"]}
    assert domains["orders"]["endpoint_count"] == 3
    assert domains["orders"]["request_count"] == 5
    assert "cart" in domains and "auth" in domains


def test_unauthenticated_har_grades_not_replayable():
    har = {"log": {"entries": [{
        "request": {"method": "GET", "url": "https://x.example.com/api/v1/ping-pong",
                    "headers": [], "cookies": []},
        "response": {"status": 200, "headers": [], "cookies": [], "content": {}},
    }]}}
    rep = har_parse.parse_har_report(har_content=json.dumps(har))
    assert rep["ok"] is True
    assert rep["replay"]["replayable"] is False
    assert rep["replay"]["grade"] is None
    assert rep["replay"]["missing"]


# ── 脱敏硬约束 ──────────────────────────────────────────────────────────────

def test_no_secret_leaks_anywhere_in_report(report):
    blob = json.dumps(report, ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in blob, f"凭证值泄漏进报告：{secret[:12]}..."


def test_sensitive_values_collected(report):
    assert report["redaction"]["sensitive_values_detected"] > 0
    assert "凭证值" in report["redaction"]["policy"] or "脱敏" in report["redaction"]["policy"] \
        or "凭证" in report["redaction"]["policy"]


def test_forced_substitution_when_credential_sits_in_path():
    """凭证值恰好长成普通路径段（归一化收不到）时，收尾哨兵扫描强制替换并计数。"""
    leak = "userordersx"           # 纯字母、短于不透明阈值，归一化不会动它
    har = {"log": {"entries": [{
        "request": {"method": "GET",
                    "url": f"https://api.example.com/api/{leak}",
                    "headers": [{"name": "Authorization", "value": leak}],
                    "cookies": []},
        "response": {"status": 200, "headers": [], "cookies": [], "content": {}},
    }]}}
    rep = har_parse.parse_har_report(har_content=json.dumps(har))
    blob = json.dumps(rep, ensure_ascii=False)
    assert leak not in blob
    assert "<redacted>" in blob
    assert rep["redaction"]["enforced_substitutions"] > 0


def test_userinfo_stripped_from_host():
    har = {"log": {"entries": [{
        "request": {"method": "GET",
                    "url": "https://admin:hunter2secret@api.example.com/api/v1/items",
                    "headers": [{"name": "X-Token", "value": "tok-abcdefgh12345678"}],
                    "cookies": []},
        "response": {"status": 200, "headers": [], "cookies": [], "content": {}},
    }]}}
    rep = har_parse.parse_har_report(har_content=json.dumps(har))
    blob = json.dumps(rep, ensure_ascii=False)
    assert "hunter2secret" not in blob
    assert rep["endpoints"][0]["host"] == "api.example.com"


# ── 请求样例（M2.5：给用例填 request 块的素材，脱敏是硬约束） ───────────────

def test_sample_attached_to_endpoints(report):
    ep = _endpoint(report, "POST", "/api/v1/auth/login")
    sample = ep["sample"]
    assert sample["method"] == "POST"
    assert sample["url"] == "https://api.shop.example.com/api/v1/auth/login"
    assert sample["response_status"] == 200
    assert report["summary"]["endpoints_with_sample"] == report["summary"]["endpoint_count"]


def test_sample_url_uses_normalized_path(report):
    """样例 URL 用归一化 path，不带抓包里的具体 id。"""
    ep = _endpoint(report, "GET", "/api/v1/orders/:id")
    assert ep["sample"]["url"] == "https://api.shop.example.com/api/v1/orders/:id"
    assert "10086" not in json.dumps(ep, ensure_ascii=False)


def test_sample_body_masks_credential_keys(report):
    """请求体样例保留业务字段，凭证键的值换成 <redacted>。"""
    body = _endpoint(report, "POST", "/api/v1/auth/login")["sample"]["body"]
    assert body["mode"] == "raw" and body["language"] == "json"
    payload = json.loads(body["raw"])
    assert payload["username"] == "alice"            # 业务字段留着，模型才知道怎么填
    assert payload["password"] == har_parse.REDACTED
    assert "P@ssw0rd-secret-12345" not in body["raw"]


def test_sample_headers_whitelist_and_credential_masking(report):
    login = _endpoint(report, "POST", "/api/v1/auth/login")["sample"]
    assert login["headers"] == [{"key": "Content-Type", "value": "application/json"}]
    orders = _endpoint(report, "GET", "/api/v1/orders")["sample"]
    assert orders["headers"] == [{"key": "Authorization", "value": har_parse.REDACTED}]
    cart = _endpoint(report, "GET", "/api/v1/cart/items")["sample"]
    assert cart["headers"] == [{"key": "Cookie", "value": har_parse.REDACTED}]


def test_sample_query_keys_only_and_credential_keys_dropped(report):
    """query 只报非凭证键名，值一律不进报告（sign 这种凭证键连键名都不给）。"""
    sample = _endpoint(report, "GET", "/api/v1/orders")["sample"]
    assert sample["query_keys"] == ["page"]
    assert "sign" not in json.dumps(sample, ensure_ascii=False)


def test_sample_without_body_reports_none(report):
    assert _endpoint(report, "GET", "/api/v1/orders")["sample"]["body"] == {"mode": "none"}


def test_sample_form_body_masked():
    har = {"log": {"entries": [{
        "request": {"method": "POST", "url": "https://api.example.com/api/v1/login",
                    "headers": [{"name": "Content-Type",
                                 "value": "application/x-www-form-urlencoded"}],
                    "cookies": [],
                    "postData": {"mimeType": "application/x-www-form-urlencoded",
                                 "text": "user=bob&password=hunter2secret"}},
        "response": {"status": 200, "headers": [], "cookies": [],
                     "content": {"mimeType": "application/json",
                                 "text": "{\"token\":\"tok-abcdefgh12345678\"}"}},
    }]}}
    rep = har_parse.parse_har_report(har_content=json.dumps(har))
    body = rep["endpoints"][0]["sample"]["body"]
    assert body["raw"] == "user=bob&password=<redacted>"
    assert "hunter2secret" not in json.dumps(rep, ensure_ascii=False)


def test_sample_non_json_body_not_taken():
    har = {"log": {"entries": [{
        "request": {"method": "POST", "url": "https://api.example.com/api/v1/upload",
                    "headers": [{"name": "Content-Type", "value": "application/octet-stream"}],
                    "cookies": [],
                    "postData": {"mimeType": "application/octet-stream",
                                 "text": "BINARYPAYLOAD-hunter2secret-BLOB"}},
        "response": {"status": 200, "headers": [], "cookies": [], "content": {}},
    }]}}
    rep = har_parse.parse_har_report(har_content=json.dumps(har))
    body = rep["endpoints"][0]["sample"]["body"]
    assert body["mode"] == "none" and "不取样" in body["note"]
    assert "BINARYPAYLOAD" not in json.dumps(rep, ensure_ascii=False)


def test_sample_secrets_still_absent_from_whole_report(report):
    """样例进报告后，凭证哨兵这条硬约束不许松——SECRETS 一个字都不许出现。"""
    blob = json.dumps(report, ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in blob, f"样例把凭证值带进报告：{secret[:12]}..."


def test_sample_sentinel_catches_value_the_mask_misses():
    """凭证值长在**非凭证键**下（结构化掩码收不到）时，收尾哨兵扫描兜底。"""
    leak = "eyJhbGciOiJIUzI1NiJ9.LEAKED-IN-A-PLAIN-FIELD.zzz"
    har = {"log": {"entries": [{
        "request": {"method": "POST", "url": "https://api.example.com/api/v1/echo",
                    "headers": [{"name": "Content-Type", "value": "application/json"},
                                {"name": "Authorization", "value": f"Bearer {leak}"}],
                    "cookies": [],
                    "postData": {"mimeType": "application/json",
                                 "text": json.dumps({"note": leak})}},
        "response": {"status": 200, "headers": [], "cookies": [], "content": {}},
    }]}}
    rep = har_parse.parse_har_report(har_content=json.dumps(har))
    blob = json.dumps(rep, ensure_ascii=False)
    assert leak not in blob
    assert "<redacted>" in rep["endpoints"][0]["sample"]["body"]["raw"]
    assert rep["redaction"]["enforced_substitutions"] > 0


def test_samples_can_be_switched_off():
    rep = har_parse.parse_har_report(har_path=SAMPLE_HAR, include_samples=False)
    assert all("sample" not in ep for ep in rep["endpoints"])
    assert rep["summary"]["endpoints_with_sample"] == 0


def test_raw_har_entry_never_leaks_into_report(report):
    """内部字段 `_entry` 是原始 HAR 条目，绝不能留在报告里。"""
    assert all("_entry" not in ep for ep in report["endpoints"])
    assert "_entry" not in json.dumps(report, ensure_ascii=False)


# ── 输入错误：一律返回带 error 字段的可读结果，不抛裸异常 ──────────────────

def test_missing_file_returns_error():
    rep = har_parse.parse_har_report(har_path=os.path.join(FIXTURES, "nope.har"))
    assert rep["ok"] is False
    assert rep["error"] == "HAR_FILE_NOT_FOUND"
    assert rep["hint"]


def test_broken_json_returns_error(tmp_path):
    bad = tmp_path / "broken.har"
    bad.write_text('{"log": {"entries": [ {', encoding="utf-8")
    rep = har_parse.parse_har_report(har_path=str(bad))
    assert rep["ok"] is False and rep["error"] == "HAR_JSON_BROKEN"


def test_not_a_har_returns_error():
    rep = har_parse.parse_har_report(har_content='{"hello": "world"}')
    assert rep["ok"] is False and rep["error"] == "HAR_NOT_A_HAR"


def test_empty_entries_returns_error():
    rep = har_parse.parse_har_report(har_content='{"log": {"entries": []}}')
    assert rep["ok"] is False and rep["error"] == "HAR_EMPTY"


def test_no_input_returns_error():
    rep = har_parse.parse_har_report()
    assert rep["ok"] is False and rep["error"] == "HAR_INPUT_MISSING"
    assert "inbox" in rep["inbox_dir"].lower()


# ── inbox 约定 ──────────────────────────────────────────────────────────────

def test_relative_path_resolves_under_inbox(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "demo.har").write_text(
        json.dumps({"log": {"entries": [{
            "request": {"method": "GET", "url": "https://a.example.com/api/v1/x",
                        "headers": [{"name": "X-Token", "value": "tok-abcdefgh12345678"}],
                        "cookies": []},
            "response": {"status": 200, "headers": [], "cookies": [], "content": {}}}]}}),
        encoding="utf-8")
    monkeypatch.setattr(har_parse, "INBOX_DIR", str(inbox))
    rep = har_parse.parse_har_report(har_path="demo.har")
    assert rep["ok"] is True and rep["source"]["name"] == "demo.har"


def test_relative_path_cannot_escape_inbox(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setattr(har_parse, "INBOX_DIR", str(inbox))
    rep = har_parse.parse_har_report(har_path="../secret.har")
    assert rep["ok"] is False and rep["error"] == "HAR_PATH_ESCAPES_INBOX"


# ── 输出体积控制 ────────────────────────────────────────────────────────────

def test_max_endpoints_truncates(report):
    small = har_parse.parse_har_report(har_path=SAMPLE_HAR, max_endpoints=3)
    assert len(small["endpoints"]) == 3
    assert small["truncated"]["endpoints_total"] == report["summary"]["endpoint_count"]
    # 业务域分组的计数仍覆盖全部端点
    assert sum(d["endpoint_count"] for d in small["domains"]) == \
        report["summary"]["endpoint_count"]


def test_max_samples_caps_samples_and_explains(report):
    """样例是体积大头，超出 max_samples 的端点只留端点行并说明原因。"""
    capped = har_parse.parse_har_report(har_path=SAMPLE_HAR, max_samples=2)
    with_sample = [ep for ep in capped["endpoints"] if "sample" in ep]
    assert len(with_sample) == 2
    assert capped["summary"]["endpoints_with_sample"] == 2
    assert capped["truncated"]["samples_shown"] == 2
    assert "分批体检" in capped["truncated"]["samples_note"]


def test_sample_body_truncated_at_size_cap():
    """超长请求体按字符上限截断并留标记，报告体积不被单条样例撑爆。"""
    payload = {"remark": "长" * 2000}
    har = {"log": {"entries": [{
        "request": {"method": "POST", "url": "https://api.example.com/api/v1/notes",
                    "headers": [{"name": "Content-Type", "value": "application/json"}],
                    "cookies": [],
                    "postData": {"mimeType": "application/json",
                                 "text": json.dumps(payload, ensure_ascii=False)}},
        "response": {"status": 200, "headers": [], "cookies": [], "content": {}},
    }]}}
    rep = har_parse.parse_har_report(har_content=json.dumps(har))
    body = rep["endpoints"][0]["sample"]["body"]
    assert body["truncated"] is True
    assert len(body["raw"]) <= har_parse.MAX_SAMPLE_BODY_CHARS + 8


def test_report_volume_stays_bounded_on_a_big_har():
    """端点上限 + 样例上限双闸：大 HAR 的报告体积仍在原量级（不随流量线性膨胀）。"""
    entries = []
    for i in range(400):
        entries.append({
            "startedDateTime": "2026-08-04T09:00:00.000Z",
            "request": {"method": "POST",
                        "url": f"https://api.example.com/api/v1/orders/res{i}/submit",
                        "headers": [{"name": "Content-Type", "value": "application/json"},
                                    {"name": "X-Token", "value": f"tok-{i:0>20}"}],
                        "cookies": [],
                        "postData": {"mimeType": "application/json",
                                     "text": json.dumps({"payload": "x" * 3000})}},
            "response": {"status": 200, "headers": [], "cookies": [], "content": {}},
        })
    rep = har_parse.parse_har_report(har_content=json.dumps({"log": {"entries": entries}}))
    assert len(rep["endpoints"]) == har_parse.DEFAULT_MAX_ENDPOINTS
    assert rep["summary"]["endpoints_with_sample"] == har_parse.DEFAULT_MAX_SAMPLES
    # 单条体封顶 600 字符 + 全报告体预算 8000 字符：后面的样例只剩 method/url/头
    bodies = [ep["sample"]["body"] for ep in rep["endpoints"] if "sample" in ep]
    assert sum(len(b.get("raw") or "") for b in bodies) <= har_parse.MAX_SAMPLE_BODY_BUDGET \
        + har_parse.MAX_SAMPLE_BODY_CHARS
    assert any("体积闸" in (b.get("note") or "") for b in bodies)
    # 上限随 0.8 从 40000 抬到 41000：新增的脱敏声明段与 replay.auth_refresh 都是
    # **定长**开销（约 200 字符），不随 entry 数增长——这条断言钉的是"不随流量膨胀"。
    assert len(json.dumps(rep, ensure_ascii=False)) < 41000


def test_domains_section_stays_bounded_when_domains_do_not_converge():
    """域数闸：400 个互不相同的业务域只列前 MAX_DOMAINS_LISTED 个，总数照报。"""
    entries = []
    for i in range(400):
        entries.append({
            "startedDateTime": "2026-08-04T09:00:00.000Z",
            "request": {"method": "GET",
                        "url": f"https://api.example.com/api/v1/res{i}/list",
                        "headers": [], "cookies": []},
            "response": {"status": 200, "headers": [], "cookies": [], "content": {}},
        })
    rep = har_parse.parse_har_report(har_content=json.dumps({"log": {"entries": entries}}))
    assert rep["summary"]["domain_count"] == 400          # 总域数不随截断变小
    assert len(rep["domains"]) == har_parse.MAX_DOMAINS_LISTED
    assert rep["truncated"]["domains_total"] == 400
    assert rep["truncated"]["domains_shown"] == har_parse.MAX_DOMAINS_LISTED
    assert "略去 360 个" in rep["truncated"]["domains_note"]


# ── 登录换新：登录请求的可执行描述 + token 取值规则（0.8） ───────────────────
#
# 这一段钉两件事：**能重放的说得出怎么重放**（method/url/占位/取值规则齐全），
# **重放不了的如实说不支持**（带原因码，绝不给半成品让执行层去猜）。
# 每条都顺带问一句"真账密漏没漏"——占位符是位置信息，值一个字都不许出报告。

USERNAME = "alice"
PASSWORD = "P@ssw0rd-secret-12345"


def login_har(post_text=None, mime="application/json", resp_text=None,
              resp_headers=None, reuse=True, url=None):
    """造一份「登录 + 带 token 复用」的两条 HAR。默认就是档A形态。"""
    resp_text = (resp_text if resp_text is not None
                 else json.dumps({"code": 0, "data": {
                     "token": "eyJhbGciOiJIUzI1NiJ9.FRESHTOKENVALUE0123456789.sig"}}))
    post_text = (post_text if post_text is not None
                 else json.dumps({"username": USERNAME, "password": PASSWORD}))
    login = {
        "startedDateTime": "2026-08-04T09:00:00.000Z",
        "request": {"method": "POST",
                    "url": url or "https://api.shop.example.com/api/v1/auth/login",
                    "headers": [{"name": "Content-Type", "value": mime},
                                {"name": "User-Agent", "value": "Mozilla/5.0"}],
                    "cookies": [],
                    "postData": {"mimeType": mime, "text": post_text}},
        "response": {"status": 200,
                     "headers": ([{"name": "Content-Type", "value": "application/json"}]
                                 + list(resp_headers or [])),
                     "cookies": [],
                     "content": {"mimeType": "application/json", "text": resp_text}},
    }
    hit = har_parse._walk_token(json.loads(resp_text) if resp_text.strip() else None)
    token = hit[1] if hit else ""
    after = {
        "startedDateTime": "2026-08-04T09:00:05.000Z",
        "request": {"method": "GET",
                    "url": "https://api.shop.example.com/api/v1/user/profile",
                    "headers": ([{"name": "Authorization", "value": "Bearer " + token}]
                                if reuse and token else []),
                    "cookies": []},
        "response": {"status": 200, "headers": [], "cookies": [],
                     "content": {"mimeType": "application/json",
                                 "text": json.dumps({"ok": 1})}},
    }
    return {"log": {"entries": [login, after]}}


def replay_of(har):
    return har_parse.parse_har_report(har_content=json.dumps(har))["replay"]


def test_grade_a_har_yields_an_executable_login_request():
    replay = replay_of(login_har())
    assert replay["grade"] == "A"
    assert replay["auth_refresh"]["supported"] is True
    assert replay["auth_refresh"]["login_endpoint"] == "POST /api/v1/auth/login"
    spec = replay["login_request"]
    assert spec["method"] == "POST"
    # URL 打的是**执行时选的那个环境**，不是抓包那台机器（跨 host 红线天然成立）
    assert spec["url"] == "{{baseUrl}}/api/v1/auth/login"
    assert spec["recorded_host"] == "api.shop.example.com"
    assert spec["credential_fields"] == {"username": "username", "password": "password"}
    assert spec["credential_variables"] == {"username": "login_username",
                                            "password": "login_password"}


def test_login_body_marks_credential_positions_with_placeholders_not_values():
    """账密的**位置**要标出来（不然执行层不知道往哪填），但值一个字都不许出报告。"""
    spec = replay_of(login_har())["login_request"]
    raw = spec["body"]["raw"]
    assert "{{login_username}}" in raw and "{{login_password}}" in raw
    assert USERNAME not in raw and PASSWORD not in raw
    assert json.loads(raw) == {"username": "{{login_username}}",
                               "password": "{{login_password}}"}


def test_no_credential_value_anywhere_in_a_grade_a_report():
    text = json.dumps(har_parse.parse_har_report(har_content=json.dumps(login_har())),
                      ensure_ascii=False)
    for secret in (PASSWORD, "FRESHTOKENVALUE0123456789"):
        assert secret not in text


def test_the_recorded_account_is_scrubbed_out_of_the_endpoint_sample_too():
    """账号 0.8 起也是凭据（保留变量 login_username）——它不像凭证键，凭证词表收不到，
    于是原先会原样留在那条登录端点的请求样例里。这条钉住那个洞。"""
    account = "smoke-user@example.com"                 # 够长，会进哨兵集
    report = har_parse.parse_har_report(har_content=json.dumps(login_har(
        post_text=json.dumps({"username": account, "password": PASSWORD}))))
    text = json.dumps(report, ensure_ascii=False)
    assert account not in text
    sample = report["endpoints"][0]["sample"]
    assert har_parse.REDACTED in sample["body"]["raw"]
    assert report["redaction"]["enforced_substitutions"] >= 1
    # 但登录换新的描述里仍是**占位符**（位置信息），不是 <redacted>——不然执行层没法填
    assert "{{login_username}}" in report["replay"]["login_request"]["body"]["raw"]


def test_a_short_account_is_left_alone():
    """`alice` 这种短账号不进哨兵集：满文本替换会把无关内容改坏。"""
    report = har_parse.parse_har_report(har_content=json.dumps(login_har()))
    assert report["replay"]["auth_refresh"]["supported"] is True
    assert USERNAME in json.dumps(report["endpoints"], ensure_ascii=False)


def test_login_request_headers_keep_only_shape_headers():
    """白名单头留原值，浏览器噪声头（User-Agent）与凭证头都不进可执行描述。"""
    spec = replay_of(login_har())["login_request"]
    keys = [h["key"].lower() for h in spec["headers"]]
    assert "content-type" in keys
    assert "user-agent" not in keys
    assert all(h["value"] != har_parse.REDACTED for h in spec["headers"])


def test_form_urlencoded_login_is_supported_too():
    spec = replay_of(login_har(
        post_text="userName=" + USERNAME + "&passwd=" + PASSWORD + "&remember=1",
        mime="application/x-www-form-urlencoded"))["login_request"]
    assert spec["body"]["language"] == "text"
    assert spec["body"]["raw"] == \
        "userName={{login_username}}&passwd={{login_password}}&remember=1"
    assert spec["credential_fields"] == {"username": "userName", "password": "passwd"}
    assert PASSWORD not in json.dumps(spec, ensure_ascii=False)


def test_grade_b_har_says_unsupported_with_a_reason():
    """档B（cookie 会话、没有带口令的登录）：不给 login_request，说清为什么。"""
    har = {"log": {"entries": [{
        "startedDateTime": "2026-08-04T09:00:00.000Z",
        "request": {"method": "GET",
                    "url": "https://api.shop.example.com/api/v1/orders",
                    "headers": [{"name": "Cookie",
                                 "value": "SESSIONID=ABCDEF0123456789VAL"}],
                    "cookies": []},
        "response": {"status": 200,
                     "headers": [{"name": "Set-Cookie",
                                  "value": "SESSIONID=ABCDEF0123456789VAL; Path=/"}],
                     "cookies": [], "content": {}},
    }]}}
    replay = replay_of(har)
    assert replay["grade"] == "B"
    assert replay["auth_refresh"]["supported"] is False
    assert replay["auth_refresh"]["code"] == har_parse.LOGIN_NO_REQUEST
    assert "login_request" not in replay


def test_sso_style_login_without_password_field_is_unsupported():
    """扫码/验证码登录：认得出是登录请求，但请求体里没有口令字段——不猜。"""
    replay = replay_of(login_har(
        post_text=json.dumps({"qrTicket": "scan-ticket-0001", "deviceId": "d-1"})))
    assert replay["auth_refresh"]["supported"] is False
    assert replay["auth_refresh"]["code"] == har_parse.LOGIN_NO_PASSWORD_FIELD
    assert "qrTicket" in replay["auth_refresh"]["reason"]
    assert "login_request" not in replay


def test_password_without_a_recognizable_username_field_is_unsupported():
    replay = replay_of(login_har(
        post_text=json.dumps({"credentialX": "zzz", "password": PASSWORD})))
    assert replay["auth_refresh"]["supported"] is False
    assert replay["auth_refresh"]["code"] == har_parse.LOGIN_NO_USERNAME_FIELD
    assert "login_request" not in replay


def test_login_response_without_a_token_rule_is_unsupported():
    """登录能重放，但响应里既没有 token 形字段也没有会话 cookie → 不支持，不猜。"""
    har = login_har(resp_text=json.dumps({"code": 0, "msg": "ok"}), reuse=False)
    # 让它仍被判成可回放（靠后续请求头的鉴权在场），才测得到"取值规则"这一关
    har["log"]["entries"][1]["request"]["headers"] = [
        {"name": "X-Token", "value": "static-token-0123456789"}]
    replay = replay_of(har)
    assert replay["auth_refresh"]["supported"] is False
    assert "login_request" not in replay


# ── token 取值规则推断 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    ({"code": 0, "data": {"token": "TOKENVALUE0123456789abc"}}, "$.data.token"),
    ({"token": "TOKENVALUE0123456789abc"}, "$.token"),
    ({"access_token": "TOKENVALUE0123456789abc"}, "$.access_token"),
    ({"result": {"token": "TOKENVALUE0123456789abc"}}, "$.result.token"),
])
def test_token_path_is_inferred_from_common_shapes(body, expected):
    spec = replay_of(login_har(resp_text=json.dumps(body)))["login_request"]
    rule = spec["token_extract"]
    assert rule["source"] == "json_body" and rule["path"] == expected
    assert rule["inferred"] is True
    assert rule["override_variable"] == har_parse.AUTH_TOKEN_PATH_VAR


def test_preferred_path_wins_and_candidates_are_listed_for_editing():
    """多个候选时按优先级挑一个，但**把备选全列出来**——推断结果要人可见可改。"""
    rule = replay_of(login_har(resp_text=json.dumps({
        "data": {"token": "AAAAAAAAAAAAAAAAAAAAAAA", "jwt": "BBBBBBBBBBBBBBBBBBBBBBB"},
    })))["login_request"]["token_extract"]
    assert rule["path"] == "$.data.token"
    assert set(rule["candidates"]) == {"$.data.token", "$.data.jwt"}
    assert har_parse.AUTH_TOKEN_PATH_VAR in rule["note"]


def test_cookie_only_login_is_not_identified_as_a_login_at_all():
    """会话 cookie 型登录（响应体里没有 token）压根不会被认成登录请求。

    这正是体检不推断 Set-Cookie 取值规则的原因：`_find_login` 是按"响应里产出了
    token"认登录的，所以能走到取值规则那一步的登录，其响应体里必有 token 形字段。
    这种 HAR 判档B，执行时走环境变量表；真要按 cookie 取值就在环境里显式写
    `auth_token_path=cookie:名字`（执行层认这种规则）。
    """
    har = login_har(
        resp_text=json.dumps({"code": 0}),
        resp_headers=[{"name": "Set-Cookie",
                       "value": "SESSIONID=SESSIONVALUE0123456789; Path=/; HttpOnly"}],
        reuse=False)
    har["log"]["entries"][1]["request"]["headers"] = [
        {"name": "Cookie", "value": "SESSIONID=SESSIONVALUE0123456789"}]
    replay = replay_of(har)
    assert replay["grade"] == "B"
    assert replay["auth_refresh"]["code"] == har_parse.LOGIN_NO_REQUEST
    assert "login_request" not in replay


def test_override_variable_is_advertised_in_the_rule_note():
    rule = replay_of(login_har())["login_request"]["token_extract"]
    assert rule["override_variable"] == har_parse.AUTH_TOKEN_PATH_VAR
    assert "cookie:" in rule["note"]        # 两种写法都说给用户听


def test_reserved_variable_names_are_advertised_once():
    refresh = replay_of(login_har())["auth_refresh"]
    assert refresh["reserved_variables"] == ["login_username", "login_password",
                                             "auth_token_path"]
    assert refresh["token_variable_default"] == "token"
