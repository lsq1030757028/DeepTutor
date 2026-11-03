"""生成产出 → 消费形状的规整（BB-487 / BB-488）。

本文件的主张只有一句：**"生成出东西了"不等于"东西能用"。**

原来的验证链在这里断了一环——端到端脚本断言的是生成任务的返回值
（那里断言确实在，只是在 `request` 外面），从没验证过采纳落盘之后
这些用例还能不能执行。用户 UAT 时 8 条用例全部 `executable=false`，
就是这一环缺失的代价。所以本文件最后一节直接走通
`生成产出 → save_delivery → read_delivery`，断言 `executable=true`。
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from server import delivery
from server.gateway import workbench
from server.generate.case_shape import (
    normalize_assertion,
    normalize_generated_case,
    normalize_generated_cases,
)


# ── 断言归位：BB-487 的本体 ────────────────────────────────────────────────

def test_top_level_assertions_move_into_the_request():
    """旧提示词的形状：断言是 request 的兄弟。消费侧只认 request 里面的。"""
    raw = {"id": "TC-001", "title": "下单成功",
           "request": {"method": "POST", "url": "/api/order"},
           "assertions": [{"kind": "status", "expect": 200}]}
    case, notes = normalize_generated_case(raw)
    assert case["request"]["assertions"] == [{"type": "status", "expected": 200}]
    assert "assertions" not in case, "顶层那份要搬走，不能两处都留"
    assert any("归位" in n for n in notes), "救回动作必须留痕"


def test_assertion_key_synonyms_are_translated():
    assert normalize_assertion({"kind": "status", "expect": 200}) == \
        {"type": "status", "expected": 200}
    assert normalize_assertion({"type": "json_path", "json_path": "$.a", "value": 1}) == \
        {"type": "json_path", "expected": 1, "path": "$.a"}


def test_op_exists_becomes_a_path_assertion_without_an_expected_value():
    """旧提示词的例子里有 `op: exists`，本仓没有这个写法，翻成 path + 空期望。"""
    out = normalize_assertion({"kind": "json_path", "path": "$.data.id", "op": "exists"})
    assert out == {"type": "json_path", "expected": None, "path": "$.data.id"}


def test_an_unrecognisable_assertion_is_dropped_not_kept_empty():
    """留一条 type 为空的断言，会让用例假装可执行——那是把格式问题伪装成测试失败。"""
    raw = {"id": "TC-001", "request": {"method": "GET", "url": "/a"},
           "assertions": [{"没有类型": 1}, {"kind": "status", "expect": 200}]}
    case, notes = normalize_generated_case(raw)
    assert case["request"]["assertions"] == [{"type": "status", "expected": 200}]
    assert any("丢弃" in n for n in notes)


def test_assertions_already_in_the_right_place_are_left_alone():
    """新提示词直接产出规范形状时，这一层不该多事。"""
    raw = {"case_id": "TC-001",
           "request": {"method": "GET", "url": "/a",
                       "assertions": [{"type": "status", "expected": 200}]}}
    case, notes = normalize_generated_case(raw)
    assert case["request"]["assertions"] == [{"type": "status", "expected": 200}]
    assert notes == [], "没救什么就不该有说明"


def test_assertions_in_both_places_prefer_the_consumer_side():
    raw = {"case_id": "TC-001",
           "request": {"method": "GET", "url": "/a",
                       "assertions": [{"type": "status", "expected": 200}]},
           "assertions": [{"kind": "status", "expect": 500}]}
    case, notes = normalize_generated_case(raw)
    assert case["request"]["assertions"] == [{"type": "status", "expected": 200}]
    assert any("同时出现" in n for n in notes)


# ── 结构性字段推导：BB-488 的一半 ─────────────────────────────────────────

def test_structural_fields_are_derived_from_the_outline():
    raw = {"id": "TC-001", "title": "下单",
           "request": {"method": "POST", "url": "/api/order/create"}}
    outline = {"id": "TC-001", "intent": "异常",
               "endpoint": {"method": "POST", "path": "/api/order/create"}}
    case, _ = normalize_generated_case(raw, outline)
    assert case["endpoints"] == ["POST /api/order/create"]
    assert case["module"] == "order", "模块取路径第二段——第一段几乎总是 api"
    assert case["case_type"] == "异常测试"
    assert case["priority"] == "中"


def test_prose_fields_are_never_fabricated():
    """**本层只救形状，不造内容。**

    编一句"预期返回成功"会让导出的用例表看着满了、实际是假的，比空着更坏。
    空值由 `case_validate` 的必填校验去报，那是它的职责。
    """
    raw = {"id": "TC-001", "title": "下单",
           "request": {"method": "POST", "url": "/api/order"}}
    case, _ = normalize_generated_case(raw)
    for field in ("preconditions", "steps", "expected", "test_data"):
        assert not case.get(field), f"{field} 被代笔了"


def test_model_written_prose_is_preserved():
    raw = {"id": "TC-001", "title": "下单",
           "preconditions": "已登录", "steps": ["调用 POST /api/order"],
           "expected": "返回 200 且带订单号", "test_data": "skuId=SKU-1",
           "request": {"method": "POST", "url": "/api/order"}}
    case, _ = normalize_generated_case(raw)
    assert case["preconditions"] == "已登录"
    assert case["steps"] == ["调用 POST /api/order"]
    assert case["expected"] == "返回 200 且带订单号"
    assert case["test_data"] == "skuId=SKU-1"


def test_batch_normalisation_matches_outlines_by_id():
    cases = [{"id": "TC-002", "title": "b", "request": {"method": "GET", "url": "/api/x/y"}},
             {"id": "TC-001", "title": "a", "request": {"method": "GET", "url": "/api/x/y"}}]
    outlines = [{"id": "TC-001", "intent": "正常"}, {"id": "TC-002", "intent": "鉴权"}]
    out, _ = normalize_generated_cases(cases, outlines)
    by_id = {c["case_id"]: c for c in out}
    assert by_id["TC-001"]["priority"] == "高"
    assert by_id["TC-002"]["case_type"] == "安全测试", "按 id 对齐，不是按顺序"


def test_garbage_entries_are_dropped_with_a_note():
    out, notes = normalize_generated_cases(["垃圾", 42, {"id": "TC-001"}])
    assert [c["case_id"] for c in out] == ["TC-001"]
    assert any("不是对象" in n for n in notes)


# ── 端到端：原来缺的正是这一环 ────────────────────────────────────────────

def test_a_generated_case_survives_adoption_as_an_executable_one():
    """**生成产出 → 落盘 → 工作台读回，`executable` 必须是 true。**

    这条测试就是 BB-487 的资产：原来的验证止步于"生成返回值里有断言"，
    而断言在没人读的位置上，落盘后每条都是 `executable=false`——
    勾选框全禁用、执行按钮永远灰着。断言"能执行"才是这条链的终点。
    """
    model_output = [{
        "id": "TC-001", "title": "管理员正确账密应登录成功",
        "intent": "正常",
        "preconditions": "后台可访问", "steps": ["调用 POST /api/auth/admin_login"],
        "expected": "返回 200 且响应体带 token", "test_data": "管理员账号",
        "request": {"method": "POST", "url": "/api/auth/admin_login",
                    "headers": [{"name": "Content-Type", "value": "application/json"}],
                    "body": {"mode": "raw", "language": "json",
                             "raw": '{"username": "{{login_username}}"}'}},
        # 旧形状：断言在外面、键名是 kind/expect
        "assertions": [{"kind": "status", "expect": 200},
                       {"kind": "json_path", "path": "$.data.token", "op": "exists"}],
    }]
    outlines = [{"id": "TC-001", "intent": "正常",
                 "endpoint": {"method": "POST", "path": "/api/auth/admin_login"}}]

    shaped, _ = normalize_generated_cases(model_output, outlines)

    with tempfile.TemporaryDirectory() as root:
        saved = delivery.save_delivery(shaped, fmt="csv", title="端到端",
                                       out_root=root)
        assert saved["ok"] is True
        batch = os.path.basename(saved["delivery_dir"])
        detail = workbench.read_delivery(batch, root)

        assert detail["executable_count"] == 1, "落盘后不可执行 = BB-487 复发"
        row = detail["cases"][0]
        assert row["executable"] is True
        assert row["assertion_count"] == 2

        # BB-488：导出的用例表里那几列不能是空的
        csv_text = open(os.path.join(saved["delivery_dir"], "cases.csv"),
                        encoding="utf-8-sig").read()
        for must in ("后台可访问", "返回 200 且响应体带 token", "管理员账号"):
            assert must in csv_text, f"导出表里缺「{must}」，用例表对人不可用"


def test_the_old_broken_shape_is_what_this_guards_against():
    """反向钉住：不过规整层的旧形状，落盘后确实是不可执行的。

    这条是 BB-487 的复现配方。它转红意味着消费侧改了读取位置，
    那时该改的是本测试的前提，而不是删掉规整层。
    """
    broken = [{"case_id": "TC-001", "title": "x",
               "request": {"method": "GET", "url": "/api/a"},
               "assertions": [{"kind": "status", "expect": 200}]}]
    with tempfile.TemporaryDirectory() as root:
        saved = delivery.save_delivery(broken, fmt="csv", title="旧形状",
                                       out_root=root)
        detail = workbench.read_delivery(os.path.basename(saved["delivery_dir"]), root)
        assert detail["executable_count"] == 0
