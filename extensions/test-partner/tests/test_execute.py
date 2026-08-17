"""用例执行层的离线测试：断言判定、安全红线、编号过滤、变量值不出流。

全部用 httpx 的 MockTransport 拦在传输层，一个真实网络包都不发——测试自己就得
守住「只向 base_url 发请求」这条红线，不然测试跑起来会去打真实站点。
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from server import execute
from server.execute import httpx      # 与被测模块用同一个 httpx（环境里是 httpx2）
from server.gateway.config import GatewayConfig

TOKEN = "eyJhbGciOiJIUzI1NiJ9.super-secret-token-value"
BASE = "https://api.example.com"


# ── 脚手架 ──────────────────────────────────────────────────────────────────

def case(case_id="TC-001", title="分页查询订单列表成功", request=None, **extra):
    c = {
        "编号": case_id,
        "标题": title,
        "前置条件": "持有效 token",
        "操作步骤": ["调用 GET /api/v1/orders"],
        "预期结果": "状态码 200",
        "优先级": "高",
        "所属模块": "订单",
    }
    if request is not None:
        c["request"] = request
    c.update(extra)
    return c


def req(method="GET", url="{{baseUrl}}/api/v1/orders", headers=None, body=None,
        assertions=None):
    r = {"method": method, "url": url,
         "assertions": assertions if assertions is not None
         else [{"type": "status", "expected": 200}]}
    if headers is not None:
        r["headers"] = headers
    if body is not None:
        r["body"] = body
    return r


class Recorder:
    """MockTransport 的处理器：记下收到的请求，按需给不同响应。"""

    def __init__(self, responder=None):
        self.requests = []
        self._responder = responder or (lambda request: httpx.Response(
            200, json={"code": 0, "data": {"total": 7, "items": [{"id": "A1"}]}}))

    def __call__(self, request):
        self.requests.append(request)
        return self._responder(request)

    @property
    def urls(self):
        return [str(r.url) for r in self.requests]


def run(cases, tmp_path=None, recorder=None, **kwargs):
    """跑一轮执行，默认把报告落进 tmp_path（不污染仓库 deliveries/）。"""
    recorder = recorder or Recorder()
    client = httpx.Client(transport=httpx.MockTransport(recorder),
                          follow_redirects=False)
    kwargs.setdefault("base_url", BASE)
    if tmp_path is not None:
        execute.DELIVERIES_DIR = str(tmp_path)
    try:
        result = execute.execute_cases(cases, client=client, **kwargs)
    finally:
        client.close()
    return result, recorder


@pytest.fixture(autouse=True)
def _isolate_deliveries(tmp_path, monkeypatch):
    """默认把落盘根指到 tmp_path，任何用例都不会往仓库 deliveries/ 写。"""
    monkeypatch.setattr(execute, "DELIVERIES_DIR", str(tmp_path / "deliveries"))
    os.makedirs(tmp_path / "deliveries", exist_ok=True)


# ── 断言三类型：通过 ────────────────────────────────────────────────────────

def test_status_assertion_pass():
    result, rec = run([case(request=req())])
    assert result["ok"] is True
    assert result["summary"] == {"total": 1, "executed": 1, "passed": 1, "failed": 0,
                                 "skipped": 0, "verdict": "1 过 0 败；全部通过"}
    one = result["results"][0]
    assert one["passed"] is True
    assert one["status_code"] == 200
    assert one["assertions"][0] == {"type": "status", "expected": 200,
                                    "actual": 200, "passed": True}
    assert isinstance(one["elapsed_ms"], int)
    assert rec.urls == ["https://api.example.com/api/v1/orders"]


def test_json_path_assertion_pass():
    result, _ = run([case(request=req(assertions=[
        {"type": "json_path", "path": "$.data.total", "expected": 7},
        {"type": "json_path", "path": "$.data.items[0].id", "expected": "A1"},
    ]))])
    assert result["summary"]["passed"] == 1
    assert [a["passed"] for a in result["results"][0]["assertions"]] == [True, True]


def test_body_contains_assertion_pass():
    result, _ = run([case(request=req(assertions=[
        {"type": "body_contains", "expected": "\"code\""}]))])
    assert result["results"][0]["assertions"][0]["passed"] is True
    assert result["results"][0]["assertions"][0]["actual"] == "命中"


# ── 断言三类型：失败（如实记录，不替被测系统找借口） ────────────────────────

def test_status_assertion_fail_records_actual():
    rec = Recorder(lambda request: httpx.Response(500, json={"code": 500}))
    result, _ = run([case(request=req())], recorder=rec)
    one = result["results"][0]
    assert one["passed"] is False
    assert one["assertions"][0]["expected"] == 200
    assert one["assertions"][0]["actual"] == 500
    assert result["summary"]["failed"] == 1
    assert result["ok"] is True             # 用例失败不影响工具本身跑通


def test_json_path_assertion_fail_and_missing_path():
    result, _ = run([case(request=req(assertions=[
        {"type": "json_path", "path": "$.data.total", "expected": 99},
        {"type": "json_path", "path": "$.data.nope", "expected": 1},
    ]))])
    checked = result["results"][0]["assertions"]
    assert checked[0]["passed"] is False and checked[0]["actual"] == 7
    assert checked[1]["passed"] is False
    assert "不存在" in checked[1]["actual"]


def test_body_contains_fail_shows_snippet():
    result, _ = run([case(request=req(assertions=[
        {"type": "body_contains", "expected": "订单不存在"}]))])
    one = result["results"][0]["assertions"][0]
    assert one["passed"] is False
    assert "未命中" in one["actual"]


def test_json_path_on_non_json_response():
    rec = Recorder(lambda request: httpx.Response(200, text="<html>502</html>"))
    result, _ = run([case(request=req(assertions=[
        {"type": "json_path", "path": "$.code", "expected": 0}]))], recorder=rec)
    one = result["results"][0]["assertions"][0]
    assert one["passed"] is False
    assert "不是合法 JSON" in one["actual"]


def test_partial_assertion_failure_fails_the_case():
    result, _ = run([case(request=req(assertions=[
        {"type": "status", "expected": 200},
        {"type": "json_path", "path": "$.code", "expected": 999},
    ]))])
    one = result["results"][0]
    assert [a["passed"] for a in one["assertions"]] == [True, False]
    assert one["passed"] is False


def test_transport_error_counts_as_failed_not_skipped():
    def boom(request):
        raise httpx.ConnectError("connection refused")

    result, _ = run([case(request=req())], recorder=Recorder(boom))
    one = result["results"][0]
    assert one["skipped"] is False and one["passed"] is False
    assert "ConnectError" in one["error"]
    assert result["summary"] == {"total": 1, "executed": 1, "passed": 0, "failed": 1,
                                 "skipped": 0, "verdict": "0 过 1 败"}


# ── 红线 1：跨 host 拒发 ────────────────────────────────────────────────────

def test_cross_host_case_is_skipped_and_never_sent():
    cases = [case("TC-001", request=req(url="{{baseUrl}}/api/v1/orders")),
             case("TC-002", request=req(url="https://evil.example.net/api/v1/steal"))]
    result, rec = run(cases)
    assert rec.urls == ["https://api.example.com/api/v1/orders"]   # 第二条一个字节都没发
    bad = result["results"][1]
    assert bad["skipped"] is True
    assert bad["skip_code"] == execute.SKIP_CROSS_HOST
    assert "evil.example.net" in bad["reason"] and "api.example.com" in bad["reason"]
    assert result["summary"] == {"total": 2, "executed": 1, "passed": 1, "failed": 0,
                                 "skipped": 1, "verdict": "1 过 0 败，1 条跳过未执行"}


def test_same_host_absolute_url_is_allowed():
    result, rec = run([case(request=req(url=f"{BASE}/api/v1/orders"))])
    assert result["summary"]["executed"] == 1
    assert rec.urls == ["https://api.example.com/api/v1/orders"]


def test_scheme_downgrade_is_cross_host():
    """同名主机但 http/https 不同 = 不同 host:port，照拦。"""
    result, rec = run([case(request=req(url="http://api.example.com/api/v1/orders"))])
    assert rec.requests == []
    assert result["results"][0]["skip_code"] == execute.SKIP_CROSS_HOST


def test_different_port_is_cross_host():
    result, rec = run([case(request=req(url="https://api.example.com:8443/api/v1/x"))])
    assert rec.requests == []
    assert result["results"][0]["skip_code"] == execute.SKIP_CROSS_HOST


def test_bare_path_url_joins_base_url():
    result, rec = run([case(request=req(url="/api/v1/orders"))])
    assert rec.urls == ["https://api.example.com/api/v1/orders"]
    assert result["summary"]["passed"] == 1


def test_redirects_are_not_followed():
    """302 到别的 host 会绕开红线，所以整轮不跟随重定向。"""
    rec = Recorder(lambda request: httpx.Response(
        302, headers={"Location": "https://evil.example.net/"}))
    result, recorder = run([case(request=req(assertions=[
        {"type": "status", "expected": 200}]))], recorder=rec)
    assert recorder.urls == ["https://api.example.com/api/v1/orders"]
    assert result["results"][0]["assertions"][0]["actual"] == 302


# ── 红线 2：变量残留不发 ────────────────────────────────────────────────────

def test_missing_variable_skips_without_sending():
    cases = [case("TC-001", request=req(
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"}]))]
    result, rec = run(cases)                     # 没传 variables
    assert rec.requests == []
    one = result["results"][0]
    assert one["skipped"] is True
    assert one["skip_code"] == execute.SKIP_MISSING_VARS
    assert "{{token}}" in one["reason"]


def test_missing_variable_in_body_and_url_listed_by_name():
    cases = [case(request=req(
        method="POST", url="{{baseUrl}}/api/v1/orders?uid={{userId}}",
        body={"mode": "raw", "raw": "{\"note\": \"{{memo}}\"}"},
        assertions=[{"type": "status", "expected": 201}]))]
    result, rec = run(cases)
    assert rec.requests == []
    reason = result["results"][0]["reason"]
    assert "{{userId}}" in reason and "{{memo}}" in reason


def test_provided_variables_are_substituted_into_the_real_request():
    cases = [case(request=req(
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"}]))]
    result, rec = run(cases, variables={"token": TOKEN})
    assert result["summary"]["passed"] == 1
    assert rec.requests[0].headers["authorization"] == f"Bearer {TOKEN}"


# ── 红线 4：无 request / 无断言不发 ─────────────────────────────────────────

def test_case_without_request_block_is_skipped():
    result, rec = run([case()])
    assert rec.requests == []
    one = result["results"][0]
    assert one["skipped"] is True
    assert one["skip_code"] == execute.SKIP_NO_REQUEST
    assert "人执行用例" in one["reason"]


def test_case_without_assertions_is_skipped():
    result, rec = run([case(request=req(assertions=[]))])
    assert rec.requests == []
    assert result["results"][0]["skip_code"] == execute.SKIP_NO_ASSERTION


def test_invalid_method_is_skipped():
    result, rec = run([case(request=req(method="FETCH"))])
    assert rec.requests == []
    assert result["results"][0]["skip_code"] == execute.SKIP_BAD_REQUEST


# ── case_ids 过滤（用户可选择的接口面） ─────────────────────────────────────

def test_case_ids_filters_execution():
    cases = [case("TC-001", request=req(url="{{baseUrl}}/api/v1/a")),
             case("TC-002", request=req(url="{{baseUrl}}/api/v1/b")),
             case("TC-003", request=req(url="{{baseUrl}}/api/v1/c"))]
    result, rec = run(cases, case_ids=["TC-001", "TC-003"])
    assert rec.urls == ["https://api.example.com/api/v1/a",
                        "https://api.example.com/api/v1/c"]
    assert result["summary"]["total"] == 2
    assert [r["case_id"] for r in result["results"]] == ["TC-001", "TC-003"]


def test_case_ids_accepts_comma_string():
    cases = [case("TC-001", request=req(url="{{baseUrl}}/api/v1/a")),
             case("TC-002", request=req(url="{{baseUrl}}/api/v1/b"))]
    result, rec = run(cases, case_ids="TC-002")
    assert rec.urls == ["https://api.example.com/api/v1/b"]
    assert result["summary"]["total"] == 1


def test_unknown_case_id_is_reported_not_silently_dropped():
    result, _ = run([case("TC-001", request=req())], case_ids="TC-001, TC-404")
    assert result["unknown_case_ids"] == ["TC-404"]
    assert result["warnings"] and "TC-404" in result["warnings"][0]


# ── 变量值不出流（硬断言：返回结果 + 落盘报告） ─────────────────────────────

def _blob(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def test_variable_values_never_appear_in_result_or_reports(tmp_path):
    """凭据用于真实请求，但不进返回值、不进 json 报告、不进 md 报告。"""
    echo = Recorder(lambda request: httpx.Response(
        200, json={"code": 0, "echo": {"auth": request.headers.get("authorization")}}))
    cases = [case(request=req(
        url="{{baseUrl}}/api/v1/orders?access_token={{token}}",
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"}],
        assertions=[{"type": "status", "expected": 200},
                    {"type": "body_contains", "expected": "订单不存在"},
                    {"type": "json_path", "path": "$.echo.auth", "expected": "x"}]))]
    result, rec = run(cases, variables={"token": TOKEN})

    # 真发请求时确实带了明文凭证
    assert TOKEN in str(rec.requests[0].url)
    assert rec.requests[0].headers["authorization"] == f"Bearer {TOKEN}"

    # 返回值里一个字节都不许有——URL、断言 actual（响应体回显的）都不行
    assert TOKEN not in _blob(result)
    assert "{{token}}" in result["results"][0]["url"]

    # 落盘的两份报告同样不许有
    for path in result["files"]:
        text = open(path, encoding="utf-8").read()
        assert TOKEN not in text
        assert "super-secret-token-value" not in text

    # 报告只记 host，不记完整 base_url、不记变量值
    report = json.load(open(result["files"][0], encoding="utf-8"))
    assert report["base_url_host"] == "api.example.com:443"
    assert report["variable_names"] == ["token"]
    assert report["variable_values_recorded"] is False


def test_assertions_run_against_raw_body_but_output_is_scrubbed():
    """脱敏只在出站那一步做——否则响应体里带凭证的用例会被判成假失败。"""
    echo = Recorder(lambda request: httpx.Response(
        200, json={"echo": request.headers.get("authorization")}))
    cases = [case(request=req(
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"}],
        assertions=[{"type": "body_contains", "expected": TOKEN},
                    {"type": "json_path", "path": "$.echo",
                     "expected": f"Bearer {TOKEN}"}]))]
    result, _ = run(cases, recorder=echo, variables={"token": TOKEN})
    assert [a["passed"] for a in result["results"][0]["assertions"]] == [True, True]
    assert TOKEN not in _blob(result)
    assert result["results"][0]["assertions"][0]["expected"] == "{{token}}"


def test_error_message_is_scrubbed_of_variable_values():
    def boom(request):
        raise httpx.ConnectError(f"failed to connect while sending {TOKEN}")

    result, _ = run([case(request=req(
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"}]))],
        recorder=Recorder(boom), variables={"token": TOKEN})
    assert TOKEN not in _blob(result)
    assert "{{token}}" in result["results"][0]["error"]


def test_short_variable_values_are_not_leaked_via_url():
    """短变量值不参与出站文本替换，所以展示 URL 里就不能带它。"""
    result, rec = run([case(request=req(url="{{baseUrl}}/api/v1/orders/{{oid}}"))],
                      variables={"oid": "42"})
    assert rec.urls == ["https://api.example.com/api/v1/orders/42"]
    assert result["results"][0]["url"] == "https://api.example.com/api/v1/orders/{{oid}}"


# ── 报告落盘 ────────────────────────────────────────────────────────────────

def test_reports_written_to_new_dir_with_table(tmp_path):
    cases = [case("TC-001", request=req()),
             case("TC-002", "人执行用例")]
    result, _ = run(cases, title="订单域执行")
    assert len(result["files"]) == 2
    assert os.path.basename(result["files"][0]) == "execution_report.json"
    assert os.path.basename(result["files"][1]) == "execution_report.md"
    md = open(result["files"][1], encoding="utf-8").read()
    assert "| 编号 | 用例 | 结果 | 状态码 | 耗时ms | 断言摘要 |" in md
    assert "| TC-001 |" in md and "通过" in md
    assert "跳过" in md
    assert "订单域执行" in md
    assert result["report_dir"] in result["report_hint"]


def test_reports_merge_into_existing_delivery_dir(tmp_path):
    delivery = tmp_path / "deliveries" / "20260805-120000-订单域"
    delivery.mkdir(parents=True)
    (delivery / "receipt.json").write_text("{}", encoding="utf-8")
    result, _ = run([case(request=req())], delivery_dir=str(delivery))
    assert result["report_dir"] == str(delivery)
    assert (delivery / "execution_report.json").exists()
    assert (delivery / "receipt.json").exists()          # 没覆盖原有产物


def test_delivery_dir_outside_deliveries_falls_back(tmp_path):
    outside = tmp_path / "somewhere-else"
    outside.mkdir()
    result, _ = run([case(request=req())], delivery_dir=str(outside))
    assert result["report_dir"] != str(outside)
    assert not (outside / "execution_report.json").exists()
    assert any("不是 deliveries/ 下的既有目录" in n for n in result["normalized"])


def test_custom_deliveries_root_admits_user_scoped_delivery_dir(tmp_path):
    """工作台传每用户批次根时，该根下的批次目录必须被接受。

    没有这个参数之前的实际缺陷：宿主部署里批次在用户 scope 下，
    落盘闸按 MCP 线的模块常量判定，把合法目录当"任意路径"拒掉，
    报告 fallback 到镜像内只读路径直接 PermissionError。
    """
    user_root = tmp_path / "user-scope" / "deliveries"
    batch = user_root / "20260808-010000-批次"
    batch.mkdir(parents=True)
    result, _ = run([case(request=req())], delivery_dir=str(batch),
                    deliveries_root=str(user_root))
    assert result["report_dir"] == str(batch)
    assert (batch / "execution_report.json").exists()


def test_custom_deliveries_root_still_rejects_outside_dirs(tmp_path):
    """给了自定义根，闸还在：根之外的目录照样拒，fallback 落在该根下。"""
    user_root = tmp_path / "user-scope" / "deliveries"
    user_root.mkdir(parents=True)
    outside = tmp_path / "somewhere-else"
    outside.mkdir()
    result, _ = run([case(request=req())], delivery_dir=str(outside),
                    deliveries_root=str(user_root))
    assert not (outside / "execution_report.json").exists()
    assert result["report_dir"].startswith(str(user_root))


# ── 入参宽容与入参错误 ──────────────────────────────────────────────────────

def test_base_url_required():
    result = execute.execute_cases([case(request=req())], base_url="")
    assert result["ok"] is False
    assert result["error"] == "BASE_URL_REQUIRED"


def test_base_url_must_be_absolute():
    result = execute.execute_cases([case(request=req())], base_url="api.example.com")
    assert result["ok"] is False
    assert result["error"] == "BASE_URL_INVALID"


def test_cases_as_json_string_is_rescued():
    payload = json.dumps([case(request=req())], ensure_ascii=False)
    result, rec = run(payload)
    assert result["ok"] is True and result["summary"]["passed"] == 1
    assert any("json.loads" in n for n in result["normalized"])


def test_envelope_carries_base_url_and_variables():
    envelope = {"cases": [case(request=req(
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"}]))],
        "base_url": BASE, "variables": {"token": TOKEN}, "case_ids": "TC-001"}
    recorder = Recorder()
    client = httpx.Client(transport=httpx.MockTransport(recorder))
    try:
        result = execute.execute_cases(envelope, client=client)
    finally:
        client.close()
    assert result["ok"] is True and result["summary"]["executed"] == 1
    assert TOKEN not in _blob(result)


def test_variables_accept_key_value_array():
    cases = [case(request=req(
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"}]))]
    result, rec = run(cases, variables=[{"key": "token", "value": TOKEN}])
    assert result["summary"]["passed"] == 1
    assert rec.requests[0].headers["authorization"] == f"Bearer {TOKEN}"


# ── 测试环境（env）：凭据的正门 ──────────────────────────────────────────────
#
# 这一段钉的是那条红线漏洞的修法：凭据从本机配置中心来，**不从聊天来**。
# 所以除了"解析对不对"，每条都还要问一句"值有没有漏出去"。

ENV_TOKEN = "eyJhbGciOiJIUzI1NiJ9.env-vault-secret-0987654321"
ENV_SESSION = "SESSIONID=1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d"
OTHER_BASE = "https://api-other.example.com"


@pytest.fixture()
def store(tmp_path):
    """带两个环境的真配置中心（临时目录，不碰用户的 config/）。"""
    cfg = GatewayConfig(str(tmp_path / "envcfg"))
    cfg.upsert_environment({"name": "测试环境", "base_url": BASE,
                            "variables": {"token": ENV_TOKEN,
                                          "session": ENV_SESSION}})
    cfg.upsert_environment({"name": "预发环境", "base_url": OTHER_BASE,
                            "variables": {"token": "pre-token-0123456789"}})
    return cfg


def auth_case():
    return [case(request=req(
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"},
                 {"key": "Cookie", "value": "{{session}}"}]))]


def test_env_supplies_both_base_url_and_variables(store):
    result, rec = run(auth_case(), base_url="", env="测试环境", env_store=store)
    assert result["ok"] is True and result["summary"]["passed"] == 1
    assert rec.urls == ["https://api.example.com/api/v1/orders"]
    assert rec.requests[0].headers["authorization"] == f"Bearer {ENV_TOKEN}"
    assert rec.requests[0].headers["cookie"] == ENV_SESSION
    assert result["environment"] == "测试环境"


def test_env_name_is_case_insensitive(store):
    result, _ = run(auth_case(), base_url="", env="  测试环境 ", env_store=store)
    assert result["ok"] is True and result["summary"]["passed"] == 1


def test_explicit_base_url_overrides_the_environment(store):
    """临时打另一台机器，不用为此新建一个环境。"""
    only_token = [case(request=req(
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"}]))]
    result, rec = run(only_token, base_url=BASE, env="预发环境", env_store=store)
    # 环境「预发环境」配的是 OTHER_BASE，显式传入的 BASE 赢了；变量仍来自该环境
    assert rec.urls == ["https://api.example.com/api/v1/orders"]
    assert rec.requests[0].headers["authorization"] == "Bearer pre-token-0123456789"
    assert any("覆盖" in n for n in result.get("normalized", []))


def test_explicit_variables_override_env_per_key_others_survive(store):
    result, rec = run(auth_case(), base_url="", env="测试环境",
                      variables={"token": "temp-override-token-0001"},
                      env_store=store)
    assert rec.requests[0].headers["authorization"] == \
        "Bearer temp-override-token-0001"
    # 没被点名的环境变量原样生效——不是"传了 variables 就整份替换"
    assert rec.requests[0].headers["cookie"] == ENV_SESSION
    assert any("覆盖了环境里的同名变量" in n for n in result.get("normalized", []))


def test_unknown_env_lists_only_the_names(store):
    result = execute.execute_cases([case(request=req())], base_url="",
                                   env="生产环境", env_store=store)
    assert result["ok"] is False and result["error"] == "ENV_NOT_FOUND"
    assert "测试环境" in result["message"] and "预发环境" in result["message"]
    # 只出名字：地址与变量键值一律不出现
    assert ENV_TOKEN not in _blob(result)
    assert "session" not in _blob(result)
    assert BASE not in result["message"]
    assert "localhost:3789" in result["hint"]


def test_unknown_env_when_nothing_configured(tmp_path):
    empty = GatewayConfig(str(tmp_path / "empty"))
    result = execute.execute_cases([case(request=req())], base_url="",
                                   env="测试环境", env_store=empty)
    assert result["error"] == "ENV_NOT_FOUND"
    assert "还没有配置任何测试环境" in result["message"]


def test_env_without_explicit_base_url_still_needs_one(tmp_path):
    """环境不存在与环境没配地址是两回事，别混成一个错。"""
    result = execute.execute_cases([case(request=req())], base_url="",
                                   env="", env_store=GatewayConfig(str(tmp_path)))
    assert result["error"] == "BASE_URL_REQUIRED"


def test_env_values_never_appear_in_result_or_reports(store, tmp_path):
    """env 解析出来的凭据与聊天里传进来的走同一道出站门，一个字节都不许出。"""
    echo = Recorder(lambda request: httpx.Response(200, json={
        "code": 0, "echo": {"auth": request.headers.get("authorization"),
                            "cookie": request.headers.get("cookie")}}))
    cases = [case(request=req(
        url="{{baseUrl}}/api/v1/orders?access_token={{token}}",
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"},
                 {"key": "Cookie", "value": "{{session}}"}],
        assertions=[{"type": "status", "expected": 200},
                    {"type": "json_path", "path": "$.echo.auth", "expected": "x"},
                    {"type": "json_path", "path": "$.echo.cookie", "expected": "x"}]))]
    result, rec = run(cases, base_url="", env="测试环境", env_store=store,
                      recorder=echo)

    # 真请求带了明文（不然这个环境功能就是白配的）
    assert ENV_TOKEN in str(rec.requests[0].url)
    assert rec.requests[0].headers["cookie"] == ENV_SESSION

    blob = _blob(result)
    for value in (ENV_TOKEN, ENV_SESSION):
        assert value not in blob
        assert value[:16] not in blob
    assert "{{token}}" in result["results"][0]["url"]

    for path in result["files"]:
        text = open(path, encoding="utf-8").read()
        assert ENV_TOKEN not in text and ENV_SESSION not in text
        assert "env-vault-secret" not in text

    report = json.load(open(result["files"][0], encoding="utf-8"))
    assert report["environment"] == "测试环境"
    assert report["variable_names"] == ["session", "token"]   # 键名可以，值不行
    assert report["variable_values_recorded"] is False


def test_env_report_records_the_environment_name(store):
    result, _ = run(auth_case(), base_url="", env="测试环境", env_store=store)
    text = open(result["files"][1], encoding="utf-8").read()
    assert "环境「测试环境」" in text


# ── 执行范围对账 ────────────────────────────────────────────────────────────

def test_received_case_count_reports_what_actually_arrived():
    cases = [case(case_id=f"TC-00{i}", request=req()) for i in range(1, 4)]
    result, _ = run(cases)
    assert result["received_case_count"] == 3
    assert result["selected_case_count"] == 3
    assert result["summary"]["total"] == 3


def test_received_case_count_exposes_a_narrowed_range():
    """模型只挑了一部分编号来跑时，两个数对不上——对账靠的就是这个。"""
    cases = [case(case_id=f"TC-00{i}", request=req()) for i in range(1, 4)]
    result, _ = run(cases, case_ids=["TC-001"])
    assert result["received_case_count"] == 3
    assert result["selected_case_count"] == 1
    assert result["summary"]["total"] == 1


def test_received_case_count_is_in_both_reports(tmp_path):
    cases = [case(case_id=f"TC-00{i}", request=req()) for i in range(1, 3)]
    result, _ = run(cases)
    report = json.load(open(result["files"][0], encoding="utf-8"))
    assert report["received_case_count"] == 2
    assert "本次收到用例 2 条" in open(result["files"][1], encoding="utf-8").read()


def test_timeout_is_clamped():
    result, _ = run([case(request=req())], timeout_s=9999)
    report = json.load(open(result["files"][0], encoding="utf-8"))
    assert report["timeout_s"] == execute.MAX_TIMEOUT_S


def test_empty_cases_rejected():
    result = execute.execute_cases([], base_url=BASE)
    assert result["ok"] is False and result["error"] == "CASES_EMPTY"


# ── 体积限幅 ────────────────────────────────────────────────────────────────

def test_long_actual_is_truncated():
    long_text = "x" * 5000
    rec = Recorder(lambda request: httpx.Response(200, json={"note": long_text}))
    result, _ = run([case(request=req(assertions=[
        {"type": "json_path", "path": "$.note", "expected": "y"}]))], recorder=rec)
    actual = result["results"][0]["assertions"][0]["actual"]
    assert len(actual) < 300 and "已截断" in actual


def test_post_body_is_sent_and_rendered():
    seen = {}

    def responder(request):
        seen["body"] = request.content.decode("utf-8")
        return httpx.Response(201, json={"code": 0})

    cases = [case(request=req(
        method="POST", url="{{baseUrl}}/api/v1/orders",
        body={"mode": "raw", "raw": "{\"sku\": \"S-1\", \"note\": \"{{memo}}\"}",
              "language": "json"},
        assertions=[{"type": "status", "expected": 201}]))]
    result, _ = run(cases, recorder=Recorder(responder),
                    variables={"memo": "回归执行备注"})
    assert result["summary"]["passed"] == 1
    assert json.loads(seen["body"]) == {"sku": "S-1", "note": "回归执行备注"}


# ── 真 socket 一轮（MockTransport 绕过了真实 client 的构造与超时设置） ──────

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):                               # noqa: N802 - stdlib 约定
        if self.path.startswith("/api/v1/orders"):
            body = json.dumps({"code": 0, "data": {"total": 3}}).encode("utf-8")
            self.send_response(200)
        else:
            body = b"{\"code\": 404}"
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):                   # noqa: D102 - 别把日志刷进测试输出
        return


@pytest.fixture
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_real_http_round_trip(local_server):
    """不注入 client，走 execute_cases 自己建的 httpx.Client 打真实本地端口。"""
    cases = [
        case("TC-001", request=req(url="{{baseUrl}}/api/v1/orders", assertions=[
            {"type": "status", "expected": 200},
            {"type": "json_path", "path": "$.data.total", "expected": 3},
            {"type": "body_contains", "expected": "\"code\""}])),
        case("TC-002", "查不存在的资源", request=req(
            url="{{baseUrl}}/api/v1/nope",
            assertions=[{"type": "status", "expected": 200}])),
        case("TC-003", "跨 host 用例", request=req(
            url="https://evil.example.net/api/v1/steal")),
    ]
    result = execute.execute_cases(cases, base_url=local_server, timeout_s=5,
                                   title="真 socket 冒烟")
    assert result["ok"] is True
    assert result["summary"] == {"total": 3, "executed": 2, "passed": 1, "failed": 1,
                                 "skipped": 1, "verdict": "1 过 1 败，1 条跳过未执行"}
    assert result["results"][1]["assertions"][0]["actual"] == 404
    assert result["results"][2]["skip_code"] == execute.SKIP_CROSS_HOST
    assert os.path.exists(os.path.join(result["report_dir"], "execution_report.md"))


# ── 纯函数单测 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://api.example.com/x", "api.example.com:443"),
    ("http://api.example.com/x", "api.example.com:80"),
    ("https://api.example.com:8443/x", "api.example.com:8443"),
    ("/api/v1/orders", ""),
])
def test_host_key(url, expected):
    assert execute._host_key(url) == expected


def test_json_path_get():
    data = {"data": {"items": [{"id": "A1"}], "total": 7}}
    assert execute.json_path_get(data, "$.data.total") == (True, 7)
    assert execute.json_path_get(data, "$.data.items[0].id") == (True, "A1")
    assert execute.json_path_get(data, "$.data.items[3]") == (False, None)
    assert execute.json_path_get(data, "$.nope") == (False, None)


def test_scrub_only_replaces_long_values():
    pairs = execute._scrub_map({"token": TOKEN, "page": "1"})
    assert [p[1] for p in pairs] == ["{{token}}"]
    assert execute.scrub(f"got {TOKEN} on page 1", pairs) == "got {{token}} on page 1"


# ── 进度回调（0.7.0，工作台的进度条靠它） ───────────────────────────────────
# 执行是同步逐条的，所以"第 N/M 条"是真的，不是估出来的。

def test_progress_is_reported_per_case():
    frames = []
    result, _ = run([case("TC-001", "第一条", request=req()),
                     case("TC-002", "第二条",
                          request=req(url="{{baseUrl}}/api/v1/orders"))],
                    progress=frames.append)
    assert result["ok"] is True
    assert [(f["done"], f["current_case_id"]) for f in frames] == [
        (0, "TC-001"), (1, "TC-002"), (2, "")]
    assert all(f["total"] == 2 for f in frames)
    assert frames[0]["current_title"] == "第一条"


def test_progress_counts_skipped_cases_too():
    """跳过的用例也要往前走一格——否则进度条会卡在那儿看着像死了。"""
    frames = []
    run([case("TC-001", "没有请求块"), case("TC-002", "有请求块", request=req())],
        progress=frames.append)
    assert [f["done"] for f in frames] == [0, 1, 2]


def test_a_broken_progress_callback_does_not_break_the_run():
    def boom(_state):
        raise RuntimeError("回调自己炸了")

    result, _ = run([case(request=req())], progress=boom)
    assert result["ok"] is True
    assert result["summary"]["passed"] == 1


def test_no_progress_callback_is_fine():
    result, _ = run([case(request=req())])
    assert result["ok"] is True


# ── 登录换新（auth="login"，0.8） ───────────────────────────────────────────
#
# 这一段钉四件事：
# 1. 换来的新 token 真的进了后续请求（不然"换新"就是句空话）；
# 2. 换不来就**整轮不执行**（带着坏 token 硬跑一堆 401 没有信息量，还会污染报告）；
# 3. 账密与新 token **一个字节都不出流**——返回值、两份报告、错误串里都不许有；
# 4. 登录请求本身受同一条跨 host 红线约束：不把账密发去 base_url 之外的地方。

LOGIN_USER = "alice@example.com"
LOGIN_PWD = "P@ssw0rd-not-in-any-output-9911"
FRESH_TOKEN = "eyJhbGciOiJIUzI1NiJ9.FRESH-TOKEN-FROM-LOGIN-0001.sig"
LOGIN_PATH = "/api/v1/auth/login"


def login_spec(url=None, body=None, token_extract=None, headers=None, method="POST"):
    """parse_har 产出的 `replay.login_request` 那个形状（账密位置是占位符）。"""
    return {
        "method": method,
        "url": url if url is not None else "{{baseUrl}}" + LOGIN_PATH,
        "path": LOGIN_PATH,
        "recorded_host": "api.shop.example.com",
        "headers": (headers if headers is not None
                    else [{"key": "Content-Type", "value": "application/json"}]),
        "body": body if body is not None else {
            "mode": "raw", "language": "json",
            "raw": json.dumps({"username": "{{login_username}}",
                               "password": "{{login_password}}"})},
        "credential_fields": {"username": "username", "password": "password"},
        "token_extract": token_extract if token_extract is not None else {
            "source": "json_body", "path": "$.data.token"},
    }


class LoginRecorder(Recorder):
    """登录端点回一个新 token，其余端点要求带上那个新 token 才判 200。"""

    def __init__(self, login_status=200, login_body=None, token=FRESH_TOKEN,
                 login_headers=None):
        super().__init__()
        self.login_status = login_status
        self.login_body = (login_body if login_body is not None
                           else {"code": 0, "data": {"token": token}})
        self.login_headers = login_headers or {}
        self.token = token

    def __call__(self, request):
        self.requests.append(request)
        if request.url.path == LOGIN_PATH:
            return httpx.Response(self.login_status, json=self.login_body,
                                  headers=self.login_headers)
        got = request.headers.get("authorization", "")
        ok = got == "Bearer " + self.token
        return httpx.Response(200 if ok else 401,
                              json={"code": 0 if ok else 401,
                                    "data": {"total": 7}})

    @property
    def login_requests(self):
        return [r for r in self.requests if r.url.path == LOGIN_PATH]


@pytest.fixture()
def login_store(tmp_path):
    """配好账号口令的环境（真配置中心，临时目录）。"""
    cfg = GatewayConfig(str(tmp_path / "logincfg"))
    cfg.upsert_environment({"name": "测试环境", "base_url": BASE,
                            "variables": {"login_username": LOGIN_USER,
                                          "login_password": LOGIN_PWD}})
    return cfg


def bearer_case(case_id="TC-001"):
    return case(case_id, request=req(
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"}]))


# ── auth 三模式 ─────────────────────────────────────────────────────────────

def test_auth_defaults_to_env_which_is_the_previous_behaviour(store):
    result, rec = run(auth_case(), base_url="", env="测试环境", env_store=store)
    assert result["auth"]["mode"] == "env"
    assert result["auth"]["refreshed"] is False
    assert rec.requests[0].headers["authorization"] == f"Bearer {ENV_TOKEN}"


def test_auth_none_injects_no_environment_variables(store):
    """跑公开接口：环境的变量表一个都不注入，用例里的 {{token}} 就成了缺失变量。"""
    result, rec = run(auth_case(), base_url="", env="测试环境", auth="none",
                      env_store=store)
    assert result["auth"]["mode"] == "none"
    assert rec.requests == []                       # 缺变量闸拦住，一条都没发
    assert result["summary"]["skipped"] == 1
    assert result["results"][0]["skip_code"] == execute.SKIP_MISSING_VARS
    assert any("不注入" in n for n in result["normalized"])


def test_auth_none_still_takes_base_url_from_the_environment(store):
    """base_url 不是凭据，auth="none" 照样从环境里取。"""
    public = [case(request=req(url="{{baseUrl}}/api/v1/public/ping"))]
    result, rec = run(public, base_url="", env="测试环境", auth="none", env_store=store)
    assert result["ok"] is True and result["summary"]["passed"] == 1
    assert rec.urls == ["https://api.example.com/api/v1/public/ping"]


def test_an_unknown_auth_mode_falls_back_to_env_and_says_so(store):
    result, rec = run(auth_case(), base_url="", env="测试环境", auth="oauth2",
                      env_store=store)
    assert result["auth"]["mode"] == "env"
    assert any("oauth2" in n for n in result["normalized"])
    assert rec.requests[0].headers["authorization"] == f"Bearer {ENV_TOKEN}"


# ── 登录换新：成功路径 ──────────────────────────────────────────────────────

def test_login_refresh_gets_a_token_and_injects_it_into_the_cases(login_store):
    rec = LoginRecorder()
    result, _ = run([bearer_case()], base_url="", env="测试环境", auth="login",
                    login_request=login_spec(), env_store=login_store, recorder=rec)
    assert result["ok"] is True and result["summary"]["passed"] == 1
    # 登录发了一次，账密真的填进了请求体
    assert len(rec.login_requests) == 1
    sent = json.loads(rec.login_requests[0].content.decode("utf-8"))
    assert sent == {"username": LOGIN_USER, "password": LOGIN_PWD}
    # 后续用例带的是**新** token，不是环境里存的那个（环境里压根没存 token）
    assert rec.requests[1].headers["authorization"] == "Bearer " + FRESH_TOKEN


def test_login_receipt_records_the_endpoint_and_variable_but_no_values(login_store):
    result, _ = run([bearer_case()], base_url="", env="测试环境", auth="login",
                    login_request=login_spec(), env_store=login_store,
                    recorder=LoginRecorder())
    receipt = result["auth"]
    assert receipt["mode"] == "login" and receipt["refreshed"] is True
    assert receipt["login_endpoint"] == "POST " + LOGIN_PATH
    assert receipt["token_variable"] == "token"
    assert "登录换新" in receipt["note"]
    assert receipt["credential_values_recorded"] is False


def test_the_injected_token_variable_name_is_configurable(login_store):
    cases = [case(request=req(
        headers=[{"key": "X-Auth-Token", "value": "{{jwt}}"}]))]

    def responder(request):
        if request.url.path == LOGIN_PATH:
            return httpx.Response(200, json={"code": 0, "data": {"token": FRESH_TOKEN}})
        ok = request.headers.get("x-auth-token") == FRESH_TOKEN
        return httpx.Response(200 if ok else 401, json={"ok": ok})

    rec = Recorder(responder)
    result, _ = run(cases, base_url="", env="测试环境", auth="login",
                    login_request=login_spec(), auth_token_variable="jwt",
                    env_store=login_store, recorder=rec)
    assert result["summary"]["passed"] == 1
    assert result["auth"]["token_variable"] == "jwt"


def test_login_request_accepts_a_json_string(login_store):
    """小模型会把对象序列化成字符串传进来——救得回来就救。"""
    rec = LoginRecorder()
    result, _ = run([bearer_case()], base_url="", env="测试环境", auth="login",
                    login_request=json.dumps(login_spec()),
                    env_store=login_store, recorder=rec)
    assert result["ok"] is True and result["summary"]["passed"] == 1
    assert any("login_request" in n for n in result.get("normalized", []))


# ── token 取值规则：推断 / 显式覆盖 ─────────────────────────────────────────

def test_token_extract_follows_the_rule_from_the_report(login_store):
    """体检推断的规则说 $.data.access_token，就按它取。"""
    rec = LoginRecorder(login_body={"code": 0, "data": {"access_token": FRESH_TOKEN}})
    result, _ = run([bearer_case()], base_url="", env="测试环境", auth="login",
                    login_request=login_spec(token_extract={
                        "source": "json_body", "path": "$.data.access_token"}),
                    env_store=login_store, recorder=rec)
    assert result["summary"]["passed"] == 1
    assert result["auth"]["token_source"] == "json_body $.data.access_token"


def test_auth_token_path_variable_overrides_the_inferred_rule(tmp_path):
    """推断错了不必重跑体检：环境里写一个 auth_token_path 就压过它。"""
    cfg = GatewayConfig(str(tmp_path / "cfg"))
    cfg.upsert_environment({"name": "测试环境", "base_url": BASE, "variables": {
        "login_username": LOGIN_USER, "login_password": LOGIN_PWD,
        "auth_token_path": "$.payload.jwt"}})
    rec = LoginRecorder(login_body={"code": 0, "data": {"token": "WRONG-TOKEN-0001"},
                                    "payload": {"jwt": FRESH_TOKEN}})
    result, _ = run([bearer_case()], base_url="", env="测试环境", auth="login",
                    login_request=login_spec(), env_store=cfg, recorder=rec)
    assert result["summary"]["passed"] == 1          # 取对了才带得动后续请求
    assert "$.payload.jwt" in result["auth"]["token_source"]
    assert execute.AUTH_TOKEN_PATH_VAR in result["auth"]["token_source"]


def test_auth_token_path_can_point_at_a_cookie(tmp_path):
    """`cookie:名字` 走 Set-Cookie——体检不推断这种，用户可以显式写。"""
    cfg = GatewayConfig(str(tmp_path / "cfg"))
    cfg.upsert_environment({"name": "测试环境", "base_url": BASE, "variables": {
        "login_username": LOGIN_USER, "login_password": LOGIN_PWD,
        "auth_token_path": "cookie:SESSIONID"}})
    rec = LoginRecorder(
        login_body={"code": 0},
        login_headers={"Set-Cookie": f"SESSIONID={FRESH_TOKEN}; Path=/; HttpOnly"})
    result, _ = run([bearer_case()], base_url="", env="测试环境", auth="login",
                    login_request=login_spec(), env_store=cfg, recorder=rec)
    assert result["summary"]["passed"] == 1
    assert result["auth"]["token_source"].startswith("set_cookie SESSIONID")


# ── 登录换新：失败即整轮中止 ────────────────────────────────────────────────

def test_login_without_credentials_configured_aborts_the_whole_run(tmp_path):
    """环境里没配账密：整轮不执行，说清缺哪两个变量、去哪儿配。"""
    cfg = GatewayConfig(str(tmp_path / "cfg"))
    cfg.upsert_environment({"name": "测试环境", "base_url": BASE,
                            "variables": {"token": "stale-token-0123456789"}})
    rec = LoginRecorder()
    result, _ = run([bearer_case()], base_url="", env="测试环境", auth="login",
                    login_request=login_spec(), env_store=cfg, recorder=rec)
    assert result["ok"] is False
    assert result["error"] == execute.LOGIN_CREDENTIALS_MISSING
    assert "{{login_username}}" in result["message"]
    assert "{{login_password}}" in result["message"]
    assert rec.requests == []                        # 登录都没发，用例更没发
    assert "results" not in result and "report_dir" not in result


def test_login_http_failure_aborts_the_whole_run(login_store):
    """登录 401：不带着坏 token 硬跑一堆 401 用例，整轮停下并说原因。"""
    rec = LoginRecorder(login_status=401, login_body={"code": 401, "msg": "密码错误"})
    result, _ = run([bearer_case("TC-001"), bearer_case("TC-002")], base_url="",
                    env="测试环境", auth="login", login_request=login_spec(),
                    env_store=login_store, recorder=rec)
    assert result["ok"] is False and result["error"] == execute.LOGIN_FAILED
    assert "401" in result["message"] and LOGIN_PATH in result["message"]
    assert len(rec.requests) == 1                    # 只有那一次登录，用例一条没发
    assert execute.LOGIN_USERNAME_VAR in result["hint"]


def test_login_that_returns_no_token_aborts_instead_of_guessing(login_store):
    rec = LoginRecorder(login_body={"code": 0, "data": {"nickname": "alice"}})
    result, _ = run([bearer_case()], base_url="", env="测试环境", auth="login",
                    login_request=login_spec(), env_store=login_store, recorder=rec)
    assert result["ok"] is False
    assert result["error"] == execute.LOGIN_TOKEN_NOT_FOUND
    assert "$.data.token" in result["message"]
    assert len(rec.requests) == 1


def test_login_mode_without_a_login_request_says_where_to_get_one(login_store):
    result, rec = run([bearer_case()], base_url="", env="测试环境", auth="login",
                      env_store=login_store, recorder=LoginRecorder())
    assert result["ok"] is False
    assert result["error"] == execute.LOGIN_REQUEST_REQUIRED
    assert "parse_har" in result["hint"]
    assert rec.requests == []


def test_a_redacted_placeholder_left_in_the_login_body_aborts(login_store):
    """登录还要验证码/签名之类别的凭证（体检掩码成 <redacted>）——不猜，停。"""
    spec = login_spec(body={"mode": "raw", "language": "json", "raw": json.dumps({
        "username": "{{login_username}}", "password": "{{login_password}}",
        "sign": "<redacted>"})})
    result, rec = run([bearer_case()], base_url="", env="测试环境", auth="login",
                      login_request=spec, env_store=login_store,
                      recorder=LoginRecorder())
    assert result["ok"] is False
    assert result["error"] == execute.LOGIN_REQUEST_INVALID
    assert rec.requests == []


def test_a_transport_error_on_login_aborts_the_run(login_store):
    def boom(request):
        raise httpx.ConnectError("connection refused")

    result, _ = run([bearer_case()], base_url="", env="测试环境", auth="login",
                    login_request=login_spec(), env_store=login_store,
                    recorder=Recorder(boom))
    assert result["ok"] is False and result["error"] == execute.LOGIN_FAILED


def test_login_failure_writes_no_report(tmp_path, login_store):
    """一份全是 401 的报告没有信息量——登录换不来就干脆不落盘。"""
    rec = LoginRecorder(login_status=500, login_body={"msg": "boom"})
    result, _ = run([bearer_case()], tmp_path=tmp_path, base_url="", env="测试环境",
                    auth="login", login_request=login_spec(),
                    env_store=login_store, recorder=rec)
    assert result["ok"] is False
    root = tmp_path / "deliveries"
    assert list(root.iterdir()) == []


# ── 登录请求同样守四条红线 ──────────────────────────────────────────────────

def test_cross_host_login_is_refused_before_the_request_goes_out(login_store):
    """登录端点在别的 host 上 → 不把账密发过去，整轮不执行。"""
    rec = LoginRecorder()
    result, _ = run([bearer_case()], base_url="", env="测试环境", auth="login",
                    login_request=login_spec(
                        url="https://sso.evil.example.net" + LOGIN_PATH),
                    env_store=login_store, recorder=rec)
    assert result["ok"] is False
    assert result["error"] == execute.LOGIN_CROSS_HOST_BLOCKED
    assert "sso.evil.example.net" in result["message"]
    assert rec.requests == []                        # 一个包都没出去


def test_login_does_not_follow_redirects(login_store):
    """302 不跟随——免得把账密带去 Location 指的地方。"""
    hops = []

    def responder(request):
        hops.append(str(request.url))
        if request.url.path == LOGIN_PATH:
            return httpx.Response(302, headers={
                "Location": "https://sso.evil.example.net/collect"})
        return httpx.Response(200, json={"ok": 1})

    result, _ = run([bearer_case()], base_url="", env="测试环境", auth="login",
                    login_request=login_spec(), env_store=login_store,
                    recorder=Recorder(responder))
    assert result["ok"] is False and result["error"] == execute.LOGIN_FAILED
    assert hops == [BASE + LOGIN_PATH]               # 只打了这一下


# ── 凭据不出流（硬断言） ────────────────────────────────────────────────────

def test_credentials_and_fresh_token_never_appear_in_result_or_reports(
        tmp_path, login_store):
    """账号、口令、换来的新 token —— 返回值与两份报告里一个字节都不许有。"""
    rec = LoginRecorder()
    result, _ = run([bearer_case("TC-001"), bearer_case("TC-002")], tmp_path=tmp_path,
                    base_url="", env="测试环境", auth="login",
                    login_request=login_spec(), env_store=login_store, recorder=rec)
    assert result["ok"] is True and result["summary"]["passed"] == 2

    blobs = [json.dumps(result, ensure_ascii=False)]
    for name in ("execution_report.json", "execution_report.md"):
        with open(os.path.join(result["report_dir"], name), encoding="utf-8") as f:
            blobs.append(f.read())
    for blob in blobs:
        for secret in (LOGIN_USER, LOGIN_PWD, FRESH_TOKEN):
            assert secret not in blob
    # 但"这轮是怎么拿到凭据的"要写清楚（只有模式与端点，没有值）
    assert "本轮通过登录换新获取凭证" in blobs[1]
    assert "凭据来源：" in blobs[2] and LOGIN_PATH in blobs[2]


def test_credentials_do_not_leak_through_a_login_error_message(login_store):
    """被测系统把账号原样回显在错误里——出站脱敏门照样换掉。"""
    rec = LoginRecorder(login_status=400,
                        login_body={"msg": f"用户 {LOGIN_USER} 的口令 {LOGIN_PWD} 不对"})
    result, _ = run([bearer_case()], base_url="", env="测试环境", auth="login",
                    login_request=login_spec(), env_store=login_store, recorder=rec)
    assert result["ok"] is False
    blob = json.dumps(result, ensure_ascii=False)
    assert LOGIN_USER not in blob and LOGIN_PWD not in blob
    assert "{{login_password}}" in blob               # 换成了占位形态


def test_the_fresh_token_is_scrubbed_out_of_response_echoes(tmp_path, login_store):
    """被测接口把 token 回显在响应体里 —— 断言照常判，但出站换回占位。"""
    def responder(request):
        if request.url.path == LOGIN_PATH:
            return httpx.Response(200, json={"code": 0, "data": {"token": FRESH_TOKEN}})
        return httpx.Response(200, json={"echo": FRESH_TOKEN})

    cases = [case(request=req(assertions=[
        {"type": "json_path", "path": "$.echo", "expected": FRESH_TOKEN}]))]
    result, _ = run(cases, tmp_path=tmp_path, base_url="", env="测试环境",
                    auth="login", login_request=login_spec(),
                    env_store=login_store, recorder=Recorder(responder))
    # 断言按**原始**响应体判定（判过了），出站时才换成占位
    assert result["summary"]["passed"] == 1
    assert FRESH_TOKEN not in json.dumps(result, ensure_ascii=False)


# ── 真 socket 一轮：登录 → 拿 token → 带 token 请求 → 断言通过 ──────────────

class _AuthHandler(BaseHTTPRequestHandler):
    """一个最小的"要登录才给数据"的服务：口令对了发 token，请求带对 token 才给 200。"""

    issued = "server-issued-token-abcdef0123456789"

    def _reply(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):                              # noqa: N802 - stdlib 约定
        length = int(self.headers.get("Content-Length") or 0)
        try:
            sent = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            sent = {}
        if sent.get("username") == LOGIN_USER and sent.get("password") == LOGIN_PWD:
            self._reply(200, {"code": 0, "data": {"token": self.issued}})
        else:
            self._reply(401, {"code": 401, "msg": "bad credentials"})

    def do_GET(self):                               # noqa: N802 - stdlib 约定
        if self.headers.get("Authorization") == "Bearer " + self.issued:
            self._reply(200, {"code": 0, "data": {"total": 3}})
        else:
            self._reply(401, {"code": 401, "msg": "unauthorized"})

    def log_message(self, *args):                   # noqa: D102
        return


@pytest.fixture
def auth_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_real_login_refresh_round_trip(auth_server, tmp_path):
    """不注入 client：走 execute_cases 自己建的 httpx.Client 打真实本地端口。

    HAR 里那个 token 早过期了（这里根本没给），全靠登录换新——这条链路通了，
    「录一次、以后每轮自动换新」才算真的成立。
    """
    cfg = GatewayConfig(str(tmp_path / "cfg"))
    cfg.upsert_environment({"name": "本地", "base_url": auth_server,
                            "variables": {"login_username": LOGIN_USER,
                                          "login_password": LOGIN_PWD}})
    cases = [case("TC-001", request=req(
        url="{{baseUrl}}/api/v1/orders",
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"}],
        assertions=[{"type": "status", "expected": 200},
                    {"type": "json_path", "path": "$.data.total", "expected": 3}]))]
    result = execute.execute_cases(cases, env="本地", auth="login",
                                   login_request=login_spec(), timeout_s=5,
                                   env_store=cfg, title="登录换新真链路")
    assert result["ok"] is True
    assert result["summary"]["verdict"] == "1 过 0 败；全部通过"
    assert result["auth"]["refreshed"] is True
    assert result["auth"]["login_endpoint"] == "POST " + LOGIN_PATH
    blob = json.dumps(result, ensure_ascii=False)
    for secret in (LOGIN_USER, LOGIN_PWD, _AuthHandler.issued):
        assert secret not in blob


def test_real_login_refresh_with_wrong_credentials_aborts(auth_server, tmp_path):
    cfg = GatewayConfig(str(tmp_path / "cfg"))
    cfg.upsert_environment({"name": "本地", "base_url": auth_server,
                            "variables": {"login_username": LOGIN_USER,
                                          "login_password": "wrong-password-000000"}})
    result = execute.execute_cases([bearer_case()], env="本地", auth="login",
                                   login_request=login_spec(), timeout_s=5,
                                   env_store=cfg)
    assert result["ok"] is False and result["error"] == execute.LOGIN_FAILED
    assert "401" in result["message"]
