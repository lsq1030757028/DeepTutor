"""validate_cases 的离线测试：每条规则的过/拦都要有一例。"""

from __future__ import annotations

import copy

import pytest

from server.case_validate import validate_cases

GOOD_CASE = {
    "编号": "TC-001",
    "标题": "已登录用户分页查询订单列表成功",
    "前置条件": "已通过 POST /api/v1/auth/login 拿到有效 token",
    "操作步骤": [
        "携带有效 token 调用 GET /api/v1/orders，page=1",
        "检查响应状态码与列表结构",
    ],
    "预期结果": "状态码 200；code=0；data.total 为整数；列表元素含订单号与状态字段",
    "优先级": "高",
    "所属模块": "订单",
    "用例类型": "功能测试",
    "测试数据": "page=1",
    "关联端点": ["GET /api/v1/orders"],
}

ENDPOINTS = [
    {"method": "GET", "path": "/api/v1/orders", "count": 2},
    {"method": "POST", "path": "/api/v1/auth/login", "count": 1},
    {"method": "DELETE", "path": "/api/v1/cart/items/:id", "count": 1},
]

#: 带 host 的端点清单（parse_har 报告的真实形态，用于 request.url 的 host 一致性）
ENDPOINTS_WITH_HOST = [dict(ep, host="api.shop.example.com") for ep in ENDPOINTS]

GOOD_REQUEST = {
    "method": "GET",
    "url": "{{baseUrl}}/api/v1/orders?page=1",
    "headers": [{"key": "Authorization", "value": "{{token}}"},
                {"key": "Accept", "value": "application/json"}],
    "body": {"mode": "none"},
    "assertions": [
        {"type": "status", "expected": 200},
        {"type": "json_path", "path": "$.data.total", "expected": 2},
        {"type": "body_contains", "expected": "\"code\":0"},
    ],
}


def request(**overrides):
    r = copy.deepcopy(GOOD_REQUEST)
    for k, v in overrides.items():
        if v is None:
            r.pop(k, None)
        else:
            r[k] = v
    return r


def case(**overrides):
    c = copy.deepcopy(GOOD_CASE)
    for k, v in overrides.items():
        if v is None:
            c.pop(k, None)
        else:
            c[k] = v
    return c


def codes(result, bucket="errors"):
    return {item["code"] for item in result[bucket]}


# ── 过 ──────────────────────────────────────────────────────────────────────

def test_good_case_passes():
    result = validate_cases([GOOD_CASE])
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["summary"]["case_count"] == 1


def test_english_keys_accepted():
    result = validate_cases([{
        "case_id": "TC-100",
        "title": "删除购物车条目",
        "preconditions": "购物车里已有 1 件商品",
        "steps": ["调用 DELETE /api/v1/cart/items/42"],
        "expected": "状态码 204，购物车条目数减 1",
        "priority": "中",
    }])
    assert result["ok"] is True, result["errors"]


def test_steps_as_multiline_string_accepted():
    result = validate_cases([case(操作步骤="1. 登录\n2. 调用 GET /api/v1/orders")])
    assert result["ok"] is True, result["errors"]


def test_priority_all_three_values_pass():
    cases = [case(编号=f"TC-{i}", 优先级=p) for i, p in enumerate(("高", "中", "低"))]
    assert validate_cases(cases)["ok"] is True


# ── 拦：六字段 ──────────────────────────────────────────────────────────────

def test_missing_id_flagged():
    assert "E02_ID_EMPTY" in codes(validate_cases([case(编号=None)]))


def test_empty_id_flagged():
    assert "E02_ID_EMPTY" in codes(validate_cases([case(编号="  ")]))


def test_duplicate_id_flagged():
    result = validate_cases([GOOD_CASE, case(标题="另一条")])
    assert "E03_ID_DUPLICATE" in codes(result)
    assert result["errors"][0]["case_id"] == "TC-001"


def test_empty_title_flagged():
    assert "E04_TITLE_EMPTY" in codes(validate_cases([case(标题="")]))


def test_missing_precondition_is_error():
    assert "E05_PRECONDITION_MISSING" in codes(validate_cases([case(前置条件=None)]))


def test_empty_precondition_is_warning_not_error():
    result = validate_cases([case(前置条件="")])
    assert result["ok"] is True
    assert "W01_PRECONDITION_EMPTY" in codes(result, "warnings")


def test_empty_steps_flagged():
    assert "E06_STEPS_EMPTY" in codes(validate_cases([case(操作步骤=[])]))


def test_empty_expected_flagged():
    assert "E07_EXPECTED_EMPTY" in codes(validate_cases([case(预期结果="")]))


@pytest.mark.parametrize("bad", ["P0", "high", "紧急", ""])
def test_bad_priority_flagged(bad):
    result = validate_cases([case(优先级=bad)])
    assert "E08_PRIORITY_INVALID" in codes(result)
    assert result["errors"][0]["field"] == "优先级"


def test_error_points_at_case_and_field():
    result = validate_cases([case(预期结果="")])
    err = next(e for e in result["errors"] if e["code"] == "E07_EXPECTED_EMPTY")
    assert err["case_id"] == "TC-001" and err["field"] == "预期结果" and err["problem"]


def test_non_dict_case_flagged():
    assert "E01_SHAPE" in codes(validate_cases(["我是一条字符串用例"]))


def test_non_list_input_flagged():
    """数字/字符串一类救不回来的形状仍然拦（单个用例对象已由入参规整救回，另有测试）。"""
    result = validate_cases(42)
    assert result["ok"] is False and result["errors"][0]["code"] == "CASES_TYPE_INVALID"


def test_empty_list_flagged():
    result = validate_cases([])
    assert result["ok"] is False and result["errors"][0]["code"] == "E00_INPUT"


# ── 拦：场景级粒度 ──────────────────────────────────────────────────────────

def test_too_many_steps_is_warning():
    result = validate_cases([case(操作步骤=[f"第 {i} 步操作" for i in range(1, 20)])])
    assert result["ok"] is True
    assert "W02_STEPS_TOO_MANY" in codes(result, "warnings")


def test_fifteen_steps_still_clean():
    result = validate_cases([case(操作步骤=[f"第 {i} 步操作" for i in range(1, 16)])])
    assert result["warnings"] == []


# ── 拦：执行声明措辞 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "已执行该接口并确认返回 200",
    "实际验证通过",
    "测试已通过",
    "PASSED",
])
def test_past_execution_claim_flagged(text):
    result = validate_cases([case(预期结果=text)])
    assert "E09_EXECUTION_CLAIM" in codes(result)


@pytest.mark.parametrize("text", [
    "接下来我会调用 GET /api/v1/orders 验证",
    "我将调用退款接口",
    "将调用 POST /api/v1/orders/:id/refund",
])
def test_future_execution_claim_flagged(text):
    result = validate_cases([case(操作步骤=[text])])
    assert "E09_EXECUTION_CLAIM" in codes(result)


@pytest.mark.parametrize("text", [
    "未执行任何写操作，只读校验",
    "尚未验证的分支不在本用例范围",
    "点击提交后系统扣减库存并返回 200",
    "校验通过后跳转订单详情页",
])
def test_normal_wording_not_flagged(text):
    result = validate_cases([case(预期结果=text)])
    assert "E09_EXECUTION_CLAIM" not in codes(result), result["errors"]


def test_execution_claim_reports_the_matched_snippet():
    result = validate_cases([case(标题="已执行的订单查询")])
    err = next(e for e in result["errors"] if e["code"] == "E09_EXECUTION_CLAIM")
    assert "已执行" in err["problem"] and err["field"] == "标题"


# ── 覆盖率 ──────────────────────────────────────────────────────────────────

def test_coverage_reports_uncovered_as_warning():
    result = validate_cases([GOOD_CASE], ENDPOINTS)
    assert result["ok"] is True                      # 覆盖缺口不算失败
    assert result["coverage"]["endpoint_total"] == 3
    assert result["coverage"]["covered"] == ["GET /api/v1/orders"]
    assert "POST /api/v1/auth/login" in result["coverage"]["uncovered"]
    assert "W04_ENDPOINT_UNCOVERED" in codes(result, "warnings")


def test_coverage_picks_endpoints_from_step_text():
    """步骤正文里写的 METHOD /path 也算覆盖，不必单独填关联端点字段。"""
    c = case(关联端点=None, 前置条件="无",
             操作步骤=["调用 POST /api/v1/auth/login 登录", "调用 GET /api/v1/orders"])
    result = validate_cases([c], ENDPOINTS)
    assert set(result["coverage"]["covered"]) == {
        "GET /api/v1/orders", "POST /api/v1/auth/login"}


def test_path_params_normalized_when_matching_coverage():
    c = case(编号="TC-9", 关联端点=["DELETE /api/v1/cart/items/42"])
    result = validate_cases([c], ENDPOINTS)
    assert "DELETE /api/v1/cart/items/:id" in result["coverage"]["covered"]


def test_unknown_endpoint_is_warning():
    c = case(关联端点=["GET /api/v1/does-not-exist"])
    result = validate_cases([c], ENDPOINTS)
    assert "W03_ENDPOINT_UNKNOWN" in codes(result, "warnings")


def test_full_coverage_no_warning():
    cases = [
        case(编号="TC-1", 关联端点=["GET /api/v1/orders"]),
        case(编号="TC-2", 关联端点=["POST /api/v1/auth/login"]),
        case(编号="TC-3", 关联端点=["DELETE /api/v1/cart/items/7"]),
    ]
    result = validate_cases(cases, ENDPOINTS)
    assert result["coverage"]["uncovered"] == []
    assert "W04_ENDPOINT_UNCOVERED" not in codes(result, "warnings")


def test_no_endpoints_means_no_coverage_section():
    result = validate_cases([GOOD_CASE])
    assert result["coverage"]["uncovered"] == []


# ── 可执行请求块（M2.5：E10-E15 / W05） ─────────────────────────────────────

def test_case_without_request_block_still_passes():
    """request 是可选的——人执行用例不带请求块照样合法。"""
    result = validate_cases([GOOD_CASE], ENDPOINTS_WITH_HOST)
    assert result["ok"] is True
    assert result["summary"]["cases_with_request"] == 0


def test_good_request_block_passes():
    result = validate_cases([case(request=GOOD_REQUEST)], ENDPOINTS_WITH_HOST)
    assert result["ok"] is True, result["errors"]
    assert result["warnings"] == [] or "W05_REQUEST_NO_ASSERTION" not in codes(
        result, "warnings")
    assert result["summary"]["cases_with_request"] == 1


def test_absolute_url_with_known_host_passes():
    c = case(request=request(url="https://api.shop.example.com/api/v1/orders?page=1"))
    assert validate_cases([c], ENDPOINTS_WITH_HOST)["ok"] is True


def test_request_key_alias_accepted():
    c = case(请求=GOOD_REQUEST)
    result = validate_cases([c], ENDPOINTS_WITH_HOST)
    assert result["ok"] is True, result["errors"]
    assert result["summary"]["cases_with_request"] == 1


# method

@pytest.mark.parametrize("bad", ["FETCH", "", "get post"])
def test_bad_method_flagged(bad):
    result = validate_cases([case(request=request(method=bad))], ENDPOINTS_WITH_HOST)
    assert "E11_REQUEST_METHOD_INVALID" in codes(result)


def test_lowercase_method_accepted():
    c = case(request=request(method="get"))
    assert validate_cases([c], ENDPOINTS_WITH_HOST)["ok"] is True


# url

def test_empty_url_flagged():
    result = validate_cases([case(request=request(url=""))], ENDPOINTS_WITH_HOST)
    assert "E12_REQUEST_URL_EMPTY" in codes(result)


def test_url_path_mismatching_declared_endpoint_is_error():
    c = case(request=request(url="{{baseUrl}}/api/v1/cart/items"))
    result = validate_cases([c], ENDPOINTS_WITH_HOST)
    err = next(e for e in result["errors"] if e["code"] == "E13_REQUEST_URL_MISMATCH")
    assert err["case_id"] == "TC-001" and err["field"] == "请求块.url"
    assert "/api/v1/cart/items" in err["problem"]


def test_url_method_mismatching_declared_endpoint_is_error():
    """path 对但 method 不对，一样算对不上。"""
    c = case(request=request(method="POST"))
    assert "E13_REQUEST_URL_MISMATCH" in codes(validate_cases([c], ENDPOINTS_WITH_HOST))


def test_url_not_in_report_is_error_when_no_endpoint_declared():
    c = case(关联端点=None, request=request(url="{{baseUrl}}/api/v1/nope"))
    assert "E13_REQUEST_URL_MISMATCH" in codes(validate_cases([c], ENDPOINTS_WITH_HOST))


def test_foreign_host_is_error():
    c = case(request=request(url="https://evil.example.net/api/v1/orders"))
    result = validate_cases([c], ENDPOINTS_WITH_HOST)
    assert "E13_REQUEST_URL_MISMATCH" in codes(result)
    assert any("evil.example.net" in e["problem"] for e in result["errors"])


def test_path_params_normalized_in_request_url():
    c = case(编号="TC-9", 关联端点=["DELETE /api/v1/cart/items/:id"],
             request=request(method="DELETE", url="{{baseUrl}}/api/v1/cart/items/42"))
    result = validate_cases([c], ENDPOINTS_WITH_HOST)
    assert result["ok"] is True, result["errors"]
    assert "DELETE /api/v1/cart/items/:id" in result["coverage"]["covered"]


def test_request_url_counts_toward_coverage():
    c = case(关联端点=None, 操作步骤=["按接口文档发起分页查询"],
             request=request())
    result = validate_cases([c], ENDPOINTS_WITH_HOST)
    assert result["coverage"]["covered"] == ["GET /api/v1/orders"]


# assertions

def test_request_without_assertions_is_warning_not_error():
    result = validate_cases([case(request=request(assertions=None))], ENDPOINTS_WITH_HOST)
    assert result["ok"] is True
    assert "W05_REQUEST_NO_ASSERTION" in codes(result, "warnings")


def test_request_with_empty_assertion_list_is_warning():
    result = validate_cases([case(request=request(assertions=[]))], ENDPOINTS_WITH_HOST)
    assert result["ok"] is True
    assert "W05_REQUEST_NO_ASSERTION" in codes(result, "warnings")


@pytest.mark.parametrize("bad", [
    {"type": "http_code", "expected": 200},          # 类型不认识
    {"type": "status", "expected": "两百"},           # 状态码不是整数
    {"type": "status", "expected": 999},             # 状态码越界
    {"type": "json_path", "expected": 2},            # 缺 path
    {"type": "json_path", "path": "$.data.total"},   # 缺 expected
    {"type": "body_contains", "expected": ""},       # 空 needle
    "状态码 200",                                     # 不是对象
])
def test_bad_assertion_flagged(bad):
    c = case(request=request(assertions=[bad]))
    result = validate_cases([c], ENDPOINTS_WITH_HOST)
    assert "E15_ASSERTION_INVALID" in codes(result)
    err = next(e for e in result["errors"] if e["code"] == "E15_ASSERTION_INVALID")
    assert err["field"].startswith("请求块.assertions[0]")


# 形状

@pytest.mark.parametrize("bad", ["GET /api/v1/orders", [], 42])
def test_request_not_an_object_flagged(bad):
    assert "E14_REQUEST_SHAPE" in codes(validate_cases([case(request=bad)],
                                                       ENDPOINTS_WITH_HOST))


def test_headers_not_a_list_flagged():
    c = case(request=request(headers={"Authorization": "{{token}}"}))
    assert "E14_REQUEST_SHAPE" in codes(validate_cases([c], ENDPOINTS_WITH_HOST))


def test_bad_body_mode_flagged():
    c = case(request=request(body={"mode": "form-data", "raw": "x=1"}))
    assert "E14_REQUEST_SHAPE" in codes(validate_cases([c], ENDPOINTS_WITH_HOST))


# E10 脱敏复检

def test_plaintext_token_in_header_flagged():
    c = case(request=request(headers=[
        {"key": "Authorization", "value": "Bearer eyJhbGciOiJIUzI1NiJ9.REALTOKEN.sig"}]))
    result = validate_cases([c], ENDPOINTS_WITH_HOST)
    assert "E10_REQUEST_SENSITIVE" in codes(result)
    assert any("Authorization" in e["problem"] for e in result["errors"])


def test_redacted_and_variable_placeholders_pass():
    for value in ("<redacted>", "{{token}}", "{{ authToken }}"):
        c = case(request=request(headers=[{"key": "X-Token", "value": value}]))
        result = validate_cases([c], ENDPOINTS_WITH_HOST)
        assert "E10_REQUEST_SENSITIVE" not in codes(result), value


#: E10 放行的占位形态（热修 0.3.1：`Bearer {{token}}` 是 Postman 的正确写法，
#: 以前被当明文凭据拦，逼着模型把对的写法改坏）
PLACEHOLDER_VALUES = [
    "{{token}}", "{{ authToken }}", "${API_TOKEN}", "<redacted>", "<your-token>",
    "***", "********",
    "Bearer {{token}}", "bearer {{token}}", "Bearer  {{token}}",
    "Basic {{cred}}", "Token {{apiToken}}", "JWT {{jwt}}",
    "Bearer <redacted>", "Basic ***", "Bearer ${API_TOKEN}",
]


@pytest.mark.parametrize("value", PLACEHOLDER_VALUES)
def test_placeholder_values_not_flagged_as_credentials(value):
    c = case(request=request(headers=[{"key": "Authorization", "value": value}]))
    result = validate_cases([c], ENDPOINTS_WITH_HOST)
    assert "E10_REQUEST_SENSITIVE" not in codes(result), value
    assert result["ok"] is True, result["errors"]


#: 真凭证照拦——放行占位不等于放行「scheme + 真值」
REAL_CREDENTIAL_VALUES = [
    "Bearer eyJhbGciOiJIUzI1NiJ9.REALPAYLOAD.signature",
    "eyJhbGciOiJIUzI1NiJ9.REALPAYLOAD.signature",
    "Basic YWxpY2U6UEBzc3cwcmQtcmVhbA==",
    # Split fake token fixtures so repository secret scanners do not mistake
    # committed test data for live credentials. Runtime values stay identical.
    "Bearer sk-" + "live-9f8e7d6c5b4a3210fedcba98",
    "ghp_" + "16C7e42F292c6912E7710c838347Ae178B4a",
    "Bearer {{token}} eyJhbGciOiJIUzI1NiJ9.REAL.sig",
]


@pytest.mark.parametrize("value", REAL_CREDENTIAL_VALUES)
def test_real_credentials_still_flagged(value):
    c = case(request=request(headers=[{"key": "Authorization", "value": value}]))
    result = validate_cases([c], ENDPOINTS_WITH_HOST)
    assert "E10_REQUEST_SENSITIVE" in codes(result), value


def test_placeholder_in_body_and_query_not_flagged():
    c = case(编号="TC-2", 关联端点=["POST /api/v1/auth/login"],
             request=request(method="POST",
                             url="{{baseUrl}}/api/v1/auth/login?sign={{sign}}",
                             body={"mode": "raw", "language": "json",
                                   "raw": '{"username":"alice","password":"{{password}}",'
                                          '"apikey":"***"}'}))
    result = validate_cases([c], ENDPOINTS_WITH_HOST)
    assert result["ok"] is True, result["errors"]


def test_sentinel_recheck_beats_placeholder_shape():
    """哨兵集里的真值即使被包成 scheme + 值的样子也照拦（放行只认占位原子）。"""
    leak = "eyJhbGciOiJIUzI1NiJ9.SUPERSECRET.sig"
    c = case(request=request(headers=[{"key": "Authorization",
                                       "value": f"Bearer {leak}"}]))
    result = validate_cases([c], ENDPOINTS_WITH_HOST, sensitive_values={leak})
    assert "E10_REQUEST_SENSITIVE" in codes(result)


def test_plaintext_password_in_body_flagged():
    c = case(编号="TC-2", 关联端点=["POST /api/v1/auth/login"],
             request=request(method="POST", url="{{baseUrl}}/api/v1/auth/login",
                             body={"mode": "raw", "language": "json",
                                   "raw": '{"username":"alice","password":"P@ssw0rd-secret"}'}))
    result = validate_cases([c], ENDPOINTS_WITH_HOST)
    assert "E10_REQUEST_SENSITIVE" in codes(result)
    assert any("password" in e["problem"] for e in result["errors"])


def test_masked_body_passes():
    c = case(编号="TC-2", 关联端点=["POST /api/v1/auth/login"],
             request=request(method="POST", url="{{baseUrl}}/api/v1/auth/login",
                             body={"mode": "raw", "language": "json",
                                   "raw": '{"username":"alice","password":"<redacted>"}'}))
    result = validate_cases([c], ENDPOINTS_WITH_HOST)
    assert result["ok"] is True, result["errors"]


def test_credential_in_query_flagged():
    c = case(request=request(url="{{baseUrl}}/api/v1/orders?page=1&sign=ab12cd34ef56ab78"))
    assert "E10_REQUEST_SENSITIVE" in codes(validate_cases([c], ENDPOINTS_WITH_HOST))


def test_numeric_field_under_credentialish_key_not_flagged():
    """`token_expires_in: 7200` 是正常业务字段，不该被判成明文凭证。"""
    c = case(编号="TC-3", 关联端点=["POST /api/v1/auth/login"],
             request=request(method="POST", url="{{baseUrl}}/api/v1/auth/login",
                             body={"mode": "raw", "language": "json",
                                   "raw": '{"token_expires_in":7200,"remember":true}'}))
    result = validate_cases([c], ENDPOINTS_WITH_HOST)
    assert "E10_REQUEST_SENSITIVE" not in codes(result), result["errors"]


def test_sentinel_values_recheck_catches_leak_outside_credential_keys():
    """哨兵集复检：凭证值躲在普通字段里（结构化扫描认不出）也要拦。"""
    leak = "eyJhbGciOiJIUzI1NiJ9.SUPERSECRETTOKENVALUE.sig"
    c = case(request=request(headers=[{"key": "X-Trace", "value": leak}]))
    clean = validate_cases([c], ENDPOINTS_WITH_HOST)
    assert "E10_REQUEST_SENSITIVE" not in codes(clean)      # 没给哨兵集时认不出
    guarded = validate_cases([c], ENDPOINTS_WITH_HOST, sensitive_values={leak})
    assert "E10_REQUEST_SENSITIVE" in codes(guarded)


def test_sentinel_recheck_scans_body_and_url():
    leak = "SUPERSECRETTOKENVALUE0123456789"
    body_case = case(request=request(body={"mode": "raw", "language": "json",
                                           "raw": '{"note":"%s"}' % leak}))
    url_case = case(编号="TC-2",
                    request=request(url=f"{{{{baseUrl}}}}/api/v1/orders?trace={leak}"))
    for c in (body_case, url_case):
        result = validate_cases([c], ENDPOINTS_WITH_HOST, sensitive_values=[leak])
        assert "E10_REQUEST_SENSITIVE" in codes(result), c["编号"]
