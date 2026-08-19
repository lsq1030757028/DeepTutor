# -*- coding: utf-8 -*-
"""free_surface 单测（批次 7 / spec Delta AC25-AC27、AC29）。

分四组：
1. **受控 curl 解析**（R7）：认识的形态解对，不认识的一律拒绝并说清拒什么；
2. **写风险口径**（AC26 / R8）：只读直发、写风险先说明后确认，口径不复制常量表；
3. **确认前零出站**（AC26 证据①②）：requires_confirm 那条路径上连 DNS 都没发；
4. **零账本 + 观测日志**（AC27）：产物树 diff 为空，逐次调用进另一棵目录树。

`main.py` 包装层的对应测试在 `test_journey_free_surface_surface.py`
——两侧绿而中间没接上，是本工程反复吃过的亏。
"""
from __future__ import annotations

import ast
import email
import json
import os

import pytest

from server.journey import egress_policy as ep
from server.journey import free_surface as fs


@pytest.fixture(autouse=True)
def observation_log(tmp_path, monkeypatch):
    path = tmp_path / "obs" / "observations.jsonl"
    monkeypatch.setenv(fs.OBSERVATION_LOG_ENV, str(path))
    return path


def read_observations(path) -> list[dict]:
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def public_resolver(host, port):
    return ["93.184.216.34"]


# ── AST 取证小工具 ────────────────────────────────────────────────────────
#
# 「模块里没有 X」这类断言必须落在**代码**上。文本 grep 会被自己的 docstring
# 判红（模块里正解释"这里没有 subprocess"就红），红几次就会被人放宽成没用。
def _tree(module) -> ast.Module:
    return ast.parse(open(module.__file__, encoding="utf-8").read())


def _imported_modules(module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}".strip(".") for alias in node.names)
    return names


def _referenced_names(module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


def _string_constants(module) -> set[str]:
    """代码里的字符串字面量，**掐掉各级 docstring**。"""
    tree = _tree(module)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings}


class FakeResponse:
    """够用的 urllib 响应替身：上下文管理器 + read(n) + headers + status。"""

    def __init__(self, status=200, headers="Content-Type: application/json\r\n", body=b"{}"):
        self.status = status
        self.code = status
        self.headers = email.message_from_string(headers)
        self._body = body

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.calls: list = []

    def open(self, request, timeout=None):
        self.calls.append((request.get_method(), request.full_url, timeout))
        return self.response


class ExplodingOpener:
    def open(self, *args, **kwargs):
        raise AssertionError("确认前不许有任何出站请求")


def exploding_resolver(host, port):
    raise AssertionError("确认前不许有任何 DNS 解析")


# ── 1. 受控 curl 解析（R7）───────────────────────────────────────────────
def test_plain_get_curl_parses():
    parsed = fs.parse_curl("curl https://api.example.com/v1/orders?page=1")
    assert parsed["method"] == "GET"
    assert parsed["url"] == "https://api.example.com/v1/orders?page=1"
    assert parsed["has_body"] is False


def test_headers_and_explicit_method_parse():
    parsed = fs.parse_curl(
        "curl -X GET 'https://api.example.com/v1/me' "
        "-H 'Authorization: Bearer abc123456' -H 'Accept: application/json'"
    )
    assert parsed["method"] == "GET" and parsed["method_explicit"] is True
    assert ("Authorization", "Bearer abc123456") in parsed["headers"]
    assert ("Accept", "application/json") in parsed["headers"]


def test_multiline_continuation_is_supported():
    """「复制为 cURL」贴过来就是这个形态，拒了等于劝退用户。"""
    parsed = fs.parse_curl(
        "curl 'https://api.example.com/v1/me' \\\n"
        "  -H 'Accept: application/json' \\\n"
        "  -H 'X-Trace: t-1'"
    )
    assert parsed["method"] == "GET" and len(parsed["headers"]) == 2


def test_data_without_explicit_method_defaults_to_post_like_curl():
    parsed = fs.parse_curl("curl https://api.example.com/v1/orders -d '{\"a\":1}'")
    assert parsed["method"] == "POST"
    assert parsed["method_explicit"] is False and parsed["has_body"] is True


def test_ampersand_inside_quoted_body_is_not_a_metacharacter():
    parsed = fs.parse_curl("curl https://api.example.com/x -d 'a=1&b=2'")
    assert parsed["body"] == "a=1&b=2"


@pytest.mark.parametrize("text", [
    "curl https://api.example.com/x | tee /tmp/out",
    "curl https://api.example.com/x; rm -rf /",
    "curl https://api.example.com/$(whoami)",
    "curl https://api.example.com/x > /tmp/out",
    "curl https://api.example.com/x && curl https://evil.example.com",
    "curl \"https://api.example.com/$(id)\"",
    "curl `echo https://api.example.com`",
])
def test_shell_metacharacters_are_refused(text):
    """解析器只把 curl 当请求描述读。这些形态一旦进 shell 就是 RCE。"""
    with pytest.raises(fs.CurlParseError):
        fs.parse_curl(text)


@pytest.mark.parametrize("text", [
    "curl -L https://api.example.com/x",
    "curl -s -o /dev/null https://api.example.com/x",
    "curl --proxy http://127.0.0.1:8080 https://api.example.com/x",
    "curl -k https://api.example.com/x",
    "curl --unix-socket /var/run/docker.sock https://api.example.com/x",
])
def test_unknown_flags_are_refused_not_guessed(text):
    """不做「尽力理解」：猜错 -L 就跟了重定向，猜错 --proxy 就换了出口。"""
    with pytest.raises(fs.CurlParseError) as exc:
        fs.parse_curl(text)
    assert "未识别" in str(exc.value)


def test_two_curls_in_one_text_are_refused():
    with pytest.raises(fs.CurlParseError):
        fs.parse_curl("curl https://a.example.com curl https://b.example.com")


def test_missing_url_is_refused():
    with pytest.raises(fs.CurlParseError):
        fs.parse_curl("curl -X GET -H 'Accept: */*'")


# ── 2. 写风险口径（AC26 / R8）────────────────────────────────────────────
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_read_only_methods_are_not_risky(method):
    assert fs.write_risk({"method": method, "has_body": False,
                          "method_explicit": True})["risky"] is False


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "PURGE", "FOOBAR"])
def test_write_and_unknown_methods_are_risky(method):
    assert fs.write_risk({"method": method, "has_body": False,
                          "method_explicit": True})["risky"] is True


@pytest.mark.parametrize("explicit", [True, False])
def test_a_request_body_is_ambiguous_hence_risky_even_on_a_read_only_method(explicit):
    """R8：`-X GET` 挂 `-d` 到底改不改数据，取决于服务端怎么读它——一律先问。"""
    verdict = fs.write_risk({"method": "GET", "has_body": True,
                             "method_explicit": explicit})
    assert verdict["risky"] is True and "歧义" in verdict["why"]


def test_write_risk_delegates_to_the_single_source_of_truth(monkeypatch):
    """口径唯一源：换掉 pw_runtime 的判定，free_surface 必须跟着变。

    如果这里改了 pw_runtime 而 free_surface 纹丝不动，说明它自己藏了一份方法表。
    """
    from server.journey import pw_runtime

    monkeypatch.setattr(pw_runtime, "effective_write_risk", lambda meta: True)
    assert fs.write_risk({"method": "GET", "has_body": False,
                          "method_explicit": True})["risky"] is True


def test_free_surface_defines_no_http_method_constant_table():
    """机械检查（AC26 证据③）：本模块**代码里**不许出现第二份方法常量表。

    判据落在 AST 的字符串常量上而不是文本 grep —— 散文里写"POST/PUT"是解释，
    代码里写才是第二份表。`GET`/`POST` 不在清单里：它们是 curl 的默认方法推断
    （没有 -d 发 GET、有 -d 发 POST），不是只读白名单。
    """
    for value in _string_constants(fs):
        assert value not in ("PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"), (
            f"free_surface 代码里出现了方法常量 {value!r}——"
            "只读口径必须只有 pw_runtime.effective_write_risk 一处")


def test_all_safe_method_sets_in_the_repo_still_agree():
    """既有三处安全方法集合的防漂对拍——任何一处漂了，写风险口径就分叉了。"""
    from server.journey import pw_runtime
    from server.journey.gates import conservation, entity_scope

    canonical = pw_runtime._SAFE_HTTP_METHODS
    assert conservation._SAFE_METHODS == canonical
    assert entity_scope._SAFE_METHODS == canonical


# ── 3. 确认前零出站（AC26 证据①②）───────────────────────────────────────
@pytest.mark.parametrize("curl_text", [
    "curl -X POST https://api.example.com/v1/orders -d '{\"a\":1}'",
    "curl -X DELETE https://api.example.com/v1/orders/1",
    "curl https://api.example.com/v1/orders -d 'a=1'",
    "curl -X PURGE https://api.example.com/v1/cache",
])
def test_write_risk_requests_disclose_and_stop_before_sending(curl_text, observation_log):
    result = fs.probe_http(curl=curl_text, resolver=exploding_resolver,
                           opener=ExplodingOpener())
    assert result["ok"] is False and result["code"] == "REQUIRES_CONFIRM"
    assert result["requires_confirm"] is True
    disclosure = result["disclosure"]
    assert disclosure["host"] == "api.example.com"
    assert disclosure["path"].startswith("/v1/")
    assert disclosure["method"]
    assert "body_kind" in disclosure
    records = read_observations(observation_log)
    assert records and records[-1]["outcome"] == "requires_confirm"


def test_confirmed_write_request_is_sent_once_authorized(observation_log):
    opener = FakeOpener(FakeResponse(status=201, body=b'{"id":1}'))
    result = fs.probe_http(curl="curl -X POST https://api.example.com/v1/orders -d '{}'",
                           confirmed=True, resolver=public_resolver, opener=opener)
    assert result["ok"] is True and result["response"]["status"] == 201
    assert opener.calls and opener.calls[0][0] == "POST"
    assert result["request"]["confirmed"] is True


def test_confirmed_flag_does_not_leak_into_read_only_requests(observation_log):
    """轻确认一次一授权：只读请求本来就不需要它，也不该在回执里假装被授权过。"""
    opener = FakeOpener(FakeResponse())
    result = fs.probe_http(curl="curl https://api.example.com/v1/ping",
                           confirmed=True, resolver=public_resolver, opener=opener)
    assert result["request"]["confirmed"] is False


def test_read_only_request_goes_straight_out(observation_log):
    opener = FakeOpener(FakeResponse(status=200, body=b'{"ok":true}'))
    result = fs.probe_http(curl="curl https://api.example.com/v1/ping",
                           resolver=public_resolver, opener=opener)
    assert result["ok"] is True
    assert result["response"]["status"] == 200
    assert result["response"]["body_preview"] == '{"ok":true}'
    assert isinstance(result["response"]["elapsed_ms"], int)
    assert opener.calls[0][0] == "GET"


# ── 出网策略在工具层真接上了（不是只写在 egress_policy 里）───────────────
@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:8080/admin", "E_EGRESS_BLOCKED_NET"),
    ("http://169.254.169.254/latest/meta-data/", "E_EGRESS_BLOCKED_NET"),
    ("http://[::1]/x", "E_EGRESS_BLOCKED_NET"),
    ("file:///etc/passwd", "E_EGRESS_SCHEME"),
    ("http://api.example.com:6379/x", "E_EGRESS_PORT"),
])
def test_blocked_targets_never_reach_the_opener(url, expected):
    result = fs.probe_http(url=url, resolver=public_resolver, opener=ExplodingOpener())
    assert result["ok"] is False and result["code"] == expected
    assert "业务结论" in result["hint"]


def test_dns_rebinding_shape_is_blocked_at_the_tool_layer():
    def rebinding(host, port):
        return ["127.0.0.1"]

    result = fs.probe_http(url="https://innocent.example.com/x",
                           resolver=rebinding, opener=ExplodingOpener())
    assert result["code"] == "E_EGRESS_BLOCKED_NET"


def test_redirects_are_not_followed_and_the_landing_is_judged():
    opener = FakeOpener(FakeResponse(
        status=302,
        headers="Location: http://169.254.169.254/latest/meta-data/\r\n",
        body=b""))
    result = fs.probe_http(url="https://api.example.com/x",
                           resolver=public_resolver, opener=opener)
    assert result["redirect"]["followed"] is False
    assert result["redirect"]["landing_allowed"] is False
    assert len(opener.calls) == 1, "跟了重定向就不止一次调用"


def test_relative_redirect_is_absolutised_before_being_judged():
    opener = FakeOpener(FakeResponse(status=301, headers="Location: /v2/ping\r\n", body=b""))
    result = fs.probe_http(url="https://api.example.com/v1/ping",
                           resolver=public_resolver, opener=opener)
    assert result["redirect"]["absolute"] == "https://api.example.com/v2/ping"
    assert result["redirect"]["landing_allowed"] is True


# ── 脱敏与截断 ────────────────────────────────────────────────────────────
def test_credentials_are_masked_in_the_echo_and_in_the_response_body():
    secret = "Bearer sk-live-9f8e7d6c5b4a"
    opener = FakeOpener(FakeResponse(
        status=401,
        body=('{"error":"bad token","seen":"%s"}' % secret).encode()))
    result = fs.probe_http(
        curl=f"curl https://api.example.com/v1/me -H 'Authorization: {secret}'",
        resolver=public_resolver, opener=opener)
    echoed = " ".join(result["request"]["headers"])
    assert "sk-live" not in echoed and "<redacted>" in echoed
    assert "sk-live" not in result["response"]["body_preview"], "响应体回显的凭据没脱敏"


def test_set_cookie_is_not_echoed_back():
    opener = FakeOpener(FakeResponse(headers="Set-Cookie: session=abc123456\r\n"))
    result = fs.probe_http(url="https://api.example.com/x",
                           resolver=public_resolver, opener=opener)
    assert "set-cookie" not in result["response"]["headers"]


def test_large_body_is_truncated_with_the_truncation_declared():
    opener = FakeOpener(FakeResponse(body=b"x" * (fs.MAX_BODY_BYTES * 3)))
    result = fs.probe_http(url="https://api.example.com/x",
                           resolver=public_resolver, opener=opener)
    assert result["response"]["body_truncated"] is True
    assert len(result["response"]["body_preview"]) <= fs.MAX_BODY_BYTES


def test_transport_failure_is_reported_as_a_channel_problem():
    class Failing:
        def open(self, *a, **k):
            raise OSError("connection refused")

    result = fs.probe_http(url="https://api.example.com/x",
                           resolver=public_resolver, opener=Failing())
    assert result["code"] == "E_HTTP_TRANSPORT"
    assert "通道" in result["message"]


def test_timeout_is_clamped_before_it_reaches_the_socket():
    opener = FakeOpener(FakeResponse())
    fs.probe_http(url="https://api.example.com/x", timeout_s=999,
                  resolver=public_resolver, opener=opener)
    assert opener.calls[0][2] == ep.MAX_TIMEOUT_S


# ── 4. 零账本 + 观测日志（AC27）──────────────────────────────────────────
def test_free_surface_never_imports_the_artifact_ledger():
    """机械断言：账本模块进不来，就不可能建批次或写 events.jsonl（AC27 的结构半边）。"""
    imported = _imported_modules(fs)
    assert not [m for m in imported if "artifacts" in m], f"自由面导入了账本模块：{imported}"


def test_free_surface_never_shells_out():
    """判据落在 AST 的导入与调用名上——散文里写"没有 subprocess"不算证据。"""
    names = _imported_modules(fs) | _referenced_names(fs)
    for banned in ("subprocess", "pty", "system", "popen", "execv", "spawn", "shell"):
        assert banned not in names, f"自由面出现了本地命令执行的痕迹：{banned}"


def test_observation_log_lives_outside_the_workbench_artifact_tree(monkeypatch):
    from server.journey import artifacts

    monkeypatch.delenv(fs.OBSERVATION_LOG_ENV, raising=False)
    log = os.path.abspath(fs.observation_log_path())
    for root in (artifacts.BATCHES_ROOT, artifacts.RUNS_ROOT, artifacts.WORKBENCH_ROOT):
        assert not log.startswith(os.path.abspath(root)), "观测日志落进了产物树"


def test_every_call_leaves_one_observation_line(observation_log):
    opener = FakeOpener(FakeResponse())
    fs.probe_http(url="https://api.example.com/a", resolver=public_resolver, opener=opener)
    fs.probe_http(url="http://127.0.0.1/b", resolver=public_resolver, opener=ExplodingOpener())
    fs.probe_http(curl="curl -X POST https://api.example.com/c",
                  resolver=exploding_resolver, opener=ExplodingOpener())
    records = read_observations(observation_log)
    assert len(records) == 3
    assert [r["outcome"] for r in records] == ["sent", "egress_blocked", "requires_confirm"]
    assert records[0]["host"] == "api.example.com" and records[0]["method"] == "GET"
    assert records[0]["status"] == 200


def test_observation_log_carries_no_body_no_header_values_no_query(observation_log):
    opener = FakeOpener(FakeResponse(body=b'{"secret":"leak-me-123456"}'))
    fs.probe_http(
        curl="curl 'https://api.example.com/v1/me?token=tok-abcdef123456' "
             "-H 'Authorization: Bearer sk-live-9f8e7d6c'",
        resolver=public_resolver, opener=opener)
    raw = open(observation_log, encoding="utf-8").read()
    for leak in ("leak-me-123456", "tok-abcdef123456", "sk-live-9f8e7d6c"):
        assert leak not in raw, f"观测日志泄了 {leak}"


# ── DB 只读入口（AC27 / AC29）────────────────────────────────────────────
def test_db_query_without_dsn_states_the_capability_boundary(monkeypatch, observation_log):
    from server.journey import db_readonly

    monkeypatch.delenv(db_readonly.DSN_ENV, raising=False)
    result = fs.db_query(sql="SELECT 1")
    assert result["code"] == "E_DB_CHANNEL_UNAVAILABLE"
    assert "能力边界" in result["message"]
    assert "替代路径" in result["message"]
    assert "没有符合条件的记录" in result["message"], "反例话术要具名钉在回复里"


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "WITH gone AS (DELETE FROM orders RETURNING *) SELECT * FROM gone",
    "SELECT 1; DROP TABLE orders",
    "SELECT pg_sleep(10)",
    "UPDATE users SET name='x'",
])
def test_db_query_rejects_mutating_statements_before_connecting(sql, observation_log):
    def exploding_connector():
        raise AssertionError("被拒的语句不该建连接")

    result = fs.db_query(sql=sql, connector=exploding_connector)
    assert result["code"] == "E_DB_STATEMENT_REJECTED"


def test_db_query_reads_through_the_readonly_double_lock(observation_log):
    class FakeCursor:
        def __init__(self, outer):
            self.outer = outer
            self.description = [("id",), ("name",)]

        def execute(self, sql, params=None):
            self.outer.executed.append(sql)

        def fetchone(self):
            return ("on",)

        def fetchmany(self, size):
            return [(1, "a")]

        def close(self):
            pass

    class FakeConn:
        def __init__(self):
            self.executed: list[str] = []
            self.closed = False

        def cursor(self):
            return FakeCursor(self)

        def close(self):
            self.closed = True

    conn = FakeConn()
    result = fs.db_query(sql="SELECT id, name FROM users LIMIT 1", connector=lambda: conn)
    assert result["ok"] is True
    assert result["result"]["rows"] == [[1, "a"]]
    assert "SHOW default_transaction_read_only" in conn.executed, "没验只读会话就查了数据"
    assert conn.closed is True
    assert read_observations(observation_log)[-1]["outcome"] == "queried"


def test_db_query_refuses_when_the_session_is_not_actually_readonly(observation_log):
    class NotReadonlyCursor:
        description = []

        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return ("off",)

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return NotReadonlyCursor()

        def close(self):
            pass

    result = fs.db_query(sql="SELECT 1", connector=Conn)
    assert result["code"] == "E_DB_SESSION_NOT_READONLY"
