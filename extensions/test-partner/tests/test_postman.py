"""Postman Collection 翻译层的离线测试：URL 解析、断言翻译、占位与统计。"""

from __future__ import annotations

import json

import pytest

from server import postman

CASE = {
    "编号": "TC-001",
    "标题": "分页查询订单列表成功",
    "前置条件": "持有效 token",
    "操作步骤": ["调用 GET /api/v1/orders"],
    "预期结果": "状态码 200",
    "优先级": "高",
    "所属模块": "订单",
    "request": {
        "method": "GET",
        "url": "{{baseUrl}}/api/v1/orders?page=1",
        "headers": [{"key": "Accept", "value": "application/json"}],
        "assertions": [{"type": "status", "expected": 200}],
    },
}


def case(**overrides):
    c = json.loads(json.dumps(CASE, ensure_ascii=False))
    c.update(overrides)
    return c


def items_of(collection):
    return [item for folder in collection["item"] for item in folder["item"]]


# ── URL 解析 ────────────────────────────────────────────────────────────────

def test_url_object_variable_prefix():
    obj = postman.url_object("{{baseUrl}}/api/v1/orders?page=1&status=paid")
    assert obj["raw"] == "{{baseUrl}}/api/v1/orders?page=1&status=paid"
    assert obj["host"] == ["{{baseUrl}}"]
    assert obj["path"] == ["api", "v1", "orders"]
    assert obj["query"] == [{"key": "page", "value": "1"},
                            {"key": "status", "value": "paid"}]
    assert "protocol" not in obj


def test_url_object_absolute():
    obj = postman.url_object("https://api.shop.example.com:8443/api/v1/orders/:id")
    assert obj["protocol"] == "https"
    assert obj["host"] == ["api", "shop", "example", "com"]
    assert obj["port"] == "8443"
    assert obj["path"] == ["api", "v1", "orders", ":id"]      # :id 即 Postman 路径变量


def test_url_object_bare_path_gets_base_url_prefix():
    """裸路径补 {{baseUrl}}，否则导入后跑不起来。"""
    obj = postman.url_object("/api/v1/orders")
    assert obj["raw"] == "{{baseUrl}}/api/v1/orders"
    assert obj["host"] == ["{{baseUrl}}"]


def test_url_object_empty_falls_back():
    obj = postman.url_object("")
    assert obj["raw"] == "{{baseUrl}}" and obj["path"] == []


# ── jsonpath → JS 取值 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("path,expected", [
    ("$.data.total", '["data"]["total"]'),
    ("data.total", '["data"]["total"]'),
    ("$.data.items[0].id", '["data"]["items"][0]["id"]'),
    ("$.code", '["code"]'),
    ("$", ""),
])
def test_json_path_accessor(path, expected):
    assert postman.json_path_accessor(path) == expected


# ── 断言翻译 ────────────────────────────────────────────────────────────────

def test_status_assertion():
    script = postman.assertion_script({"type": "status", "expected": 200})
    assert "pm.response.to.have.status(200);" in "\n".join(script)


def test_body_contains_assertion_escapes_quotes():
    script = "\n".join(postman.assertion_script(
        {"type": "body_contains", "expected": '"code":0'}))
    assert 'pm.expect(pm.response.text()).to.include("\\"code\\":0");' in script


def test_json_path_assertion_uses_eql_with_typed_literal():
    script = "\n".join(postman.assertion_script(
        {"type": "json_path", "path": "$.data.total", "expected": 2}))
    assert "var jsonData = pm.response.json();" in script
    assert 'pm.expect(jsonData["data"]["total"]).to.eql(2);' in script
    text = "\n".join(postman.assertion_script(
        {"type": "json_path", "path": "$.msg", "expected": "成功"}))
    assert 'to.eql("成功");' in text


@pytest.mark.parametrize("bad", [
    {"type": "regex", "expected": ".*"},
    {"type": "status", "expected": "两百"},
    {"type": "json_path", "expected": 1},
    "状态码 200",
])
def test_untranslatable_assertion_is_skipped_not_crashed(bad):
    assert postman.assertion_script(bad) == []


# ── collection 组装 ─────────────────────────────────────────────────────────

def test_folders_follow_module_and_first_seen_order():
    cases = [case(编号="TC-1", 所属模块="购物车"),
             case(编号="TC-2", 所属模块="订单"),
             case(编号="TC-3", 所属模块="购物车")]
    collection, stats = postman.build_collection(cases, title="混合模块")
    assert [f["name"] for f in collection["item"]] == ["购物车", "订单"]
    assert [len(f["item"]) for f in collection["item"]] == [2, 1]
    assert stats["folder_count"] == 2 and stats["item_count"] == 3


def test_case_without_module_lands_in_default_folder():
    c = case()
    c.pop("所属模块")
    collection, _ = postman.build_collection([c], title="无模块")
    assert collection["item"][0]["name"] == "未分类"


def test_placeholder_item_counted_and_marked():
    c = case(编号="TC-9")
    c.pop("request")
    collection, stats = postman.build_collection([c], title="人执行")
    item = items_of(collection)[0]
    assert stats["placeholder_count"] == 1
    assert postman.PLACEHOLDER_NOTE in item["request"]["description"]
    assert item["request"]["url"]["raw"] == "{{baseUrl}}"
    assert "event" not in item
    assert postman.PLACEHOLDER_NOTE in collection["info"]["description"]


def test_item_without_assertions_has_no_test_script():
    c = case(request={"method": "GET", "url": "{{baseUrl}}/api/v1/orders"})
    collection, stats = postman.build_collection([c], title="无断言")
    assert "event" not in items_of(collection)[0]
    assert stats["items_without_test"] == 1 and stats["assertion_count"] == 0


def test_skipped_assertions_counted():
    c = case(request=dict(CASE["request"],
                          assertions=[{"type": "status", "expected": 200},
                                      {"type": "regex", "expected": ".*"}]))
    _, stats = postman.build_collection([c], title="半数可翻")
    assert stats["assertion_count"] == 1 and stats["skipped_assertions"] == 1


def test_english_keys_and_header_name_alias_accepted():
    c = {"case_id": "TC-100", "title": "删除购物车条目", "preconditions": "有 1 件商品",
         "steps": ["调用 DELETE /api/v1/cart/items/42"], "expected": "状态码 204",
         "priority": "中", "module": "购物车",
         "request": {"method": "DELETE", "url": "{{baseUrl}}/api/v1/cart/items/42",
                     "headers": [{"name": "X-Token", "value": "{{token}}"}],
                     "assertions": [{"type": "status", "expected": 204}]}}
    collection, _ = postman.build_collection([c], title="英文键")
    item = items_of(collection)[0]
    assert item["name"] == "TC-100 删除购物车条目"
    assert item["request"]["header"][0]["key"] == "X-Token"


@pytest.mark.parametrize("value", ["Bearer {{token}}", "Basic {{cred}}",
                                   "{{token}}", "<redacted>", "***"])
def test_placeholder_header_value_exported_verbatim(value):
    """`Bearer {{token}}` 就是 Postman 里的正确写法——导出时原样保留，不改不掩。"""
    c = case(request=dict(CASE["request"],
                          headers=[{"key": "Authorization", "value": value}]))
    collection, _ = postman.build_collection([c], title="占位保留")
    header = items_of(collection)[0]["request"]["header"][0]
    assert header == {"key": "Authorization", "value": value, "type": "text"}


def test_placeholder_query_and_body_exported_verbatim():
    c = case(request={"method": "POST",
                      "url": "{{baseUrl}}/api/v1/auth/login?sign={{sign}}",
                      "body": {"mode": "raw", "language": "json",
                               "raw": '{"password":"{{password}}"}'},
                      "assertions": [{"type": "status", "expected": 200}]})
    collection, _ = postman.build_collection([c], title="占位保留")
    request = items_of(collection)[0]["request"]
    assert request["url"]["query"] == [{"key": "sign", "value": "{{sign}}"}]
    assert request["body"]["raw"] == '{"password":"{{password}}"}'


def test_collection_id_is_stable_for_same_input():
    a, _ = postman.build_collection([CASE], title="同一份输入")
    b, _ = postman.build_collection([CASE], title="同一份输入")
    c, _ = postman.build_collection([case(编号="TC-002")], title="同一份输入")
    assert a["info"]["_postman_id"] == b["info"]["_postman_id"]
    assert a["info"]["_postman_id"] != c["info"]["_postman_id"]


def test_non_dict_cases_are_dropped_not_crashed():
    collection, stats = postman.build_collection(["我是一条字符串用例", CASE], title="混入脏数据")
    assert stats["item_count"] == 1
    assert len(items_of(collection)) == 1
