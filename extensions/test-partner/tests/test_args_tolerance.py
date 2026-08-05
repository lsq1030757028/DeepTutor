"""入参规整的离线测试：能救的形状逐条救回并回显，救不回来的报清楚。

背景是实机冒烟里小模型传大体积工具参数时的形状不稳（JSON 字符串、层层包裹、
单条用例直接传对象）。每个分支都要验两件事：**救回来了** + **回显了规整动作**。
"""

from __future__ import annotations

import json

import pytest

from server import delivery
from server.args_tolerance import (ArgsToleranceError, coerce_cases, coerce_object,
                                   coerce_text, tolerant_delivery_args,
                                   tolerant_execute_args, tolerant_validate_args)
from server.case_validate import validate_cases

GOOD_CASE = {
    "编号": "TC-001",
    "标题": "已登录用户分页查询订单列表成功",
    "前置条件": "已拿到有效 token",
    "操作步骤": ["调用 GET /api/v1/orders，page=1"],
    "预期结果": "状态码 200；code=0",
    "优先级": "高",
}


# ── 分支①：cases 是 JSON 字符串 ─────────────────────────────────────────────

def test_cases_as_json_string_parsed():
    cases, extras, notes = coerce_cases(json.dumps([GOOD_CASE], ensure_ascii=False))
    assert cases == [GOOD_CASE]
    assert any("JSON" in n for n in notes)


def test_cases_as_json_string_of_single_object():
    cases, _extras, notes = coerce_cases(json.dumps(GOOD_CASE, ensure_ascii=False))
    assert cases == [GOOD_CASE]
    assert len(notes) == 2                          # 先解析，再包成数组


def test_validate_cases_accepts_json_string_and_echoes():
    result = validate_cases(json.dumps([GOOD_CASE], ensure_ascii=False))
    assert result["ok"] is True, result["errors"]
    assert result["normalized"], "规整动作必须回显给模型"


# ── 分支②：单键包裹 / 信封 ─────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["input", "arguments", "params", "args", "payload"])
def test_single_key_wrapper_peeled(key):
    cases, _extras, notes = coerce_cases({key: [GOOD_CASE]})
    assert cases == [GOOD_CASE]
    assert any(key in n for n in notes)


def test_cases_key_wrapper_peeled():
    cases, _extras, _notes = coerce_cases({"cases": [GOOD_CASE]})
    assert cases == [GOOD_CASE]


def test_nested_cases_in_cases_peeled():
    cases, _extras, _notes = coerce_cases({"cases": {"cases": [GOOD_CASE]}})
    assert cases == [GOOD_CASE]


def test_envelope_keeps_sibling_endpoints():
    """整套实参被包进 cases 时，同级的 endpoints 不能丢。"""
    endpoints = [{"method": "GET", "path": "/api/v1/orders"}]
    cases, eps, notes = tolerant_validate_args(
        {"cases": [GOOD_CASE], "endpoints": endpoints}, None)
    assert cases == [GOOD_CASE] and eps == endpoints
    assert any("endpoints" in n for n in notes)


def test_envelope_endpoints_reach_coverage():
    result = validate_cases({"cases": [GOOD_CASE],
                             "endpoints": [{"method": "GET", "path": "/api/v1/orders"},
                                           {"method": "POST", "path": "/api/v1/pay"}]})
    assert result["coverage"]["endpoint_total"] == 2
    assert result["coverage"]["uncovered_count"] == 1


def test_three_layers_deep_unwrapped():
    cases, _extras, notes = coerce_cases(
        {"input": {"arguments": {"cases": [GOOD_CASE]}}})
    assert cases == [GOOD_CASE]
    assert len([n for n in notes if "剥掉一层包裹" in n]) == 3


def test_four_layers_deep_refused():
    with pytest.raises(ArgsToleranceError) as exc:
        coerce_cases({"input": {"params": {"arguments": {"args": {"cases": [GOOD_CASE]}}}}})
    assert exc.value.code == "CASES_WRAPPED_TOO_DEEP"
    assert exc.value.normalized, "报错也要带上已经做过的规整动作"


def test_json_string_wrapping_envelope():
    cases, _extras, _notes = coerce_cases(
        json.dumps({"cases": [GOOD_CASE]}, ensure_ascii=False))
    assert cases == [GOOD_CASE]


# ── 分支③：单个用例对象 ────────────────────────────────────────────────────

def test_single_case_object_wrapped_into_list():
    cases, _extras, notes = coerce_cases(dict(GOOD_CASE))
    assert cases == [GOOD_CASE]
    assert any("单元素数组" in n for n in notes)


def test_validate_cases_accepts_single_case_object():
    result = validate_cases(dict(GOOD_CASE))
    assert result["ok"] is True, result["errors"]
    assert result["summary"]["case_count"] == 1
    assert result["normalized"]


# ── 分支④：救不回来的形状 ──────────────────────────────────────────────────

def test_broken_json_string_reports_clearly():
    with pytest.raises(ArgsToleranceError) as exc:
        coerce_cases('[{"编号": "TC-1", ')
    assert exc.value.code == "CASES_JSON_INVALID"
    assert "字符串" in exc.value.message and "JSON" in exc.value.message


def test_broken_json_surfaces_through_validate_cases():
    result = validate_cases('[{"编号": "TC-1", ')
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "CASES_JSON_INVALID"
    assert "不是合法 JSON" in result["errors"][0]["problem"]
    assert result["hint"]


@pytest.mark.parametrize("bad", [42, 3.5, True, None])
def test_scalar_input_refused(bad):
    with pytest.raises(ArgsToleranceError) as exc:
        coerce_cases(bad)
    assert exc.value.code == "CASES_TYPE_INVALID"


def test_double_encoded_json_still_parsed():
    once = json.dumps([GOOD_CASE], ensure_ascii=False)
    cases, _extras, _notes = coerce_cases(json.dumps(once))
    assert cases == [GOOD_CASE]


def test_triple_encoded_json_refused():
    blob = json.dumps(json.dumps(json.dumps([GOOD_CASE], ensure_ascii=False)))
    with pytest.raises(ArgsToleranceError) as exc:
        coerce_cases(blob)
    assert exc.value.code == "CASES_WRAPPED_TOO_DEEP"


# ── 正常形状不被打扰 ───────────────────────────────────────────────────────

def test_well_formed_input_gets_no_notes():
    cases, extras, notes = coerce_cases([GOOD_CASE])
    assert cases == [GOOD_CASE] and extras == {} and notes == []


def test_well_formed_validate_result_has_no_normalized_key():
    assert "normalized" not in validate_cases([GOOD_CASE])


# ── 标量入参：title / format ───────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("XLSX", "xlsx"), (" xlsx + postman ", "xlsx+postman"), ("Excel", "xlsx"),
    ("MarkDown", "markdown"), ("Postman", "postman"), ("APIFOX", "postman"),
    ("xlsx-postman", "xlsx+postman"), ("'csv'", "csv"),
])
def test_format_tolerance(raw, expected):
    assert delivery.normalize_format(raw) == expected


def test_unknown_format_still_refused():
    with pytest.raises(delivery.DeliveryError):
        delivery.normalize_format("pdf")


def test_coerce_text_unwraps_object_and_list():
    notes: list = []
    assert coerce_text({"title": "订单交付"}, notes, "title") == "订单交付"
    assert coerce_text(["订单交付"], notes, "title") == "订单交付"
    assert len(notes) == 2


def test_delivery_envelope_recovers_title_and_format():
    cases, fmt, title, fingerprint, notes = tolerant_delivery_args(
        {"cases": [GOOD_CASE], "format": "CSV", "title": "订单交付",
         "source_fingerprint": "sha256:abc"},
        fmt="xlsx+postman", title="测试用例")
    assert cases == [GOOD_CASE]
    assert delivery.normalize_format(fmt) == "csv"
    assert title == "订单交付" and fingerprint == "sha256:abc"
    assert notes


# ── 登录换新的三个新实参（0.8） ─────────────────────────────────────────────

LOGIN_SPEC = {"method": "POST", "url": "{{baseUrl}}/api/v1/auth/login"}


def test_coerce_object_parses_a_json_string_and_echoes_it():
    notes: list = []
    assert coerce_object(json.dumps(LOGIN_SPEC), notes, "login_request") == LOGIN_SPEC
    assert notes and "login_request" in notes[0]


def test_coerce_object_passes_bad_json_through_to_the_caller():
    """解不开就原样交给执行层——那里知道这个字段是干什么的，报错更能说清后果。"""
    notes: list = []
    assert coerce_object("{不是 JSON", notes, "login_request") == "{不是 JSON"
    assert notes == []


def test_execute_args_default_to_the_previous_behaviour():
    out = tolerant_execute_args([GOOD_CASE], base_url="https://api.example.com")
    assert out[8] == "env" and out[9] is None and out[10] == "token"


def test_execute_envelope_recovers_auth_and_login_request():
    """实参被整体包进 cases 时，auth / login_request 也得一并捡回来。"""
    out = tolerant_execute_args({
        "cases": [GOOD_CASE], "env": "测试环境", "auth": "LOGIN",
        "login_request": json.dumps(LOGIN_SPEC),
        "auth_token_variable": "jwt"})
    cases, _, _, _, _, _, _, env, auth, login_request, token_var, notes = out
    assert cases == [GOOD_CASE] and env == "测试环境"
    assert auth == "login"                       # 大小写规整
    assert login_request == LOGIN_SPEC
    assert token_var == "jwt"
    assert notes
