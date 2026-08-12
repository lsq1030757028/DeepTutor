"""导出与下载路径的离线测试（设计稿第 7 屏的服务端）。

三条最重要：
1. 导出写进**批次目录内**，重导出覆盖同名文件（刷新，不堆副本）；
2. 产物指纹取自 `cases.json`（与采纳时一致，不是导出时新造的）；
3. 下载路径穿越进不来——文件名只能是批次目录下的一级文件名。
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from server import delivery
from server.gateway import workbench
from server.gateway.workbench import WorkbenchError

CASES = [
    {"编号": "TC-001", "标题": "分页查询订单列表成功", "前置条件": "已登录",
     "操作步骤": ["调用 GET /api/v1/orders"], "预期结果": "200", "优先级": "高",
     "所属模块": "订单",
     "request": {"method": "GET", "url": "{{baseUrl}}/api/v1/orders",
                 "assertions": [{"type": "status", "expected": 200}]}},
    {"编号": "TC-002", "标题": "人工核对对账单", "前置条件": "无",
     "操作步骤": ["打开后台"], "预期结果": "数字一致", "优先级": "中"},
]


@pytest.fixture()
def root(tmp_path, monkeypatch):
    base = tmp_path / "deliveries"
    base.mkdir()
    monkeypatch.setattr(delivery, "DELIVERIES_DIR", str(base))
    return str(base)


def make_batch(root, title="订单域用例", fmt="csv"):
    result = delivery.save_delivery(CASES, title=title, fmt=fmt,
                                    source_fingerprint="sha256:feed")
    assert result["ok"] is True, result
    time.sleep(0.01)
    return os.path.basename(result["delivery_dir"])


# ── 导出 ────────────────────────────────────────────────────────────────────

def test_export_writes_into_the_batch_dir(root):
    batch = make_batch(root)
    result = workbench.export_delivery(batch, ["markdown", "csv"], root)
    assert result["ok"] is True
    names = {f["name"] for f in result["files"]}
    assert names == {"cases.md", "cases.csv"}
    for f in result["files"]:
        assert os.path.dirname(f["path"]) == os.path.join(root, batch)
        assert f["bytes"] > 0


def test_export_keeps_the_adoption_fingerprint(root):
    batch = make_batch(root)
    workbench.export_delivery(batch, ["markdown"], root)
    text = open(os.path.join(root, batch, "cases.md"), encoding="utf-8").read()
    assert "sha256:feed" in text


def test_reexport_overwrites_instead_of_stacking_copies(root):
    batch = make_batch(root)
    workbench.export_delivery(batch, ["csv"], root)
    workbench.export_delivery(batch, ["csv"], root)
    files = [n for n in os.listdir(os.path.join(root, batch))
             if n.startswith("cases") and n.endswith(".csv")]
    assert files == ["cases.csv"]


def test_export_accepts_aliases_and_dedupes(root):
    batch = make_batch(root)
    # "excel" 是 xlsx 的别名；"both" 展开成 xlsx+postman——去重后三种
    result = workbench.export_delivery(batch, ["excel", "both"], root)
    assert result["format"] == "xlsx+postman"


def test_export_postman_warns_about_caseless_items(root):
    batch = make_batch(root)   # TC-002 没有 request 块
    result = workbench.export_delivery(batch, ["postman"], root)
    assert any("占位" in w for w in result["warnings"])


def test_unknown_format_is_a_named_refusal(root):
    batch = make_batch(root)
    with pytest.raises(WorkbenchError) as err:
        workbench.export_delivery(batch, ["pdf"], root)
    assert err.value.code == "FORMAT_UNSUPPORTED"


def test_empty_format_list_is_refused(root):
    batch = make_batch(root)
    with pytest.raises(WorkbenchError) as err:
        workbench.export_delivery(batch, [], root)
    assert err.value.code == "NO_FORMAT_SELECTED"


def test_export_of_a_legacy_batch_degrades_out_loud(root):
    name = "20260101-090000-旧批次"
    path = os.path.join(root, name)
    os.makedirs(path)
    with open(os.path.join(path, "receipt.json"), "w", encoding="utf-8") as f:
        json.dump({"title": "旧批次"}, f)
    with pytest.raises(WorkbenchError) as err:
        workbench.export_delivery(name, ["csv"], root)
    assert err.value.code == "NO_CASES_JSON"


# ── 下载路径 ────────────────────────────────────────────────────────────────

def test_file_path_resolves_a_real_product(root):
    batch = make_batch(root)
    path = workbench.delivery_file_path(batch, "cases.csv", root)
    assert os.path.isfile(path)


@pytest.mark.parametrize("bad", ["../receipt.json", "..", "a/b.csv",
                                 "a\\b.csv", "", "  "])
def test_file_path_traversal_is_refused(root, bad):
    batch = make_batch(root)
    with pytest.raises(WorkbenchError):
        workbench.delivery_file_path(batch, bad, root)


def test_missing_file_is_a_named_error(root):
    batch = make_batch(root)
    with pytest.raises(WorkbenchError) as err:
        workbench.delivery_file_path(batch, "nope.xlsx", root)
    assert err.value.code == "FILE_NOT_FOUND"


# ── 闭环三件套：required_vars / 变量反查 / 用例编辑（决策 0012）────────────

def test_required_vars_lists_referenced_variables_but_not_base_url(root):
    """baseUrl 来自环境的地址栏，不是变量表里的一项——混进去会让用户
    以为自己还差配一个变量。"""
    got = workbench.required_vars({
        "url": "{{baseUrl}}/api/u/{{uid}}",
        "headers": [{"key": "Authorization", "value": "Bearer {{token}}"}],
        "body": {"mode": "raw", "raw": '{"n":"{{name}}"}'},
    })
    assert got == ["uid", "token", "name"]
    assert "baseUrl" not in got


def test_required_vars_matches_the_execution_layer_gate(root):
    """与执行层判「变量缺失不发请求」用同一个实现——两处若各写各的，
    界面说齐了而执行说缺就成了必然。"""
    from server import execute

    request = {"url": "{{baseUrl}}/x/{{a}}", "headers": [], "assertions": []}
    assert workbench.required_vars(request) == [
        v for v in execute.missing_vars("{{baseUrl}}/x/{{a}}", {})
        if v != execute.BASE_URL_VAR]


def test_case_row_carries_required_vars_and_origin(root):
    batch = make_batch(root)
    row = workbench.read_delivery(batch, root)["cases"][0]
    assert "required_vars" in row
    assert row["origin"] == "ai", "没有 origin 字段的旧批次按 AI 生成处理"


def test_variable_usage_answers_who_uses_this_variable(root):
    # 本文件顶部的 CASES 刻意不带变量（它们服务导出用例），这里另造带变量的一批
    with_vars = [dict(CASES[0], **{"编号": "TC-100", "request": {
        "method": "GET", "url": "{{baseUrl}}/api/v1/orders",
        "headers": [{"key": "Authorization", "value": "Bearer {{token}}"}],
        "assertions": [{"type": "status", "expected": 200}]}})]
    saved = delivery.save_delivery(with_vars, fmt="csv", title="带变量的批次")
    batch = os.path.basename(saved["delivery_dir"])

    usage = workbench.variable_usage(root)["usage"]
    assert "token" in usage
    assert usage["token"]["case_count"] >= 1
    entry = next(d for d in usage["token"]["deliveries"] if d["id"] == batch)
    assert "TC-100" in entry["case_ids"]
    assert "baseUrl" not in usage, "baseUrl 不是变量表里的项，不该出现在反查表里"


def test_variable_usage_is_empty_not_an_error_when_no_batches(tmp_path):
    assert workbench.variable_usage(str(tmp_path / "nope"))["usage"] == {}


def test_editing_a_case_marks_it_human_and_persists(root):
    batch = make_batch(root)
    result = workbench.update_case(batch, "TC-001", {"title": "改过的标题"}, root)
    assert result["case"]["origin"] == "human"
    # 复读一次：改动真落盘了，不是只改了返回值
    row = workbench.read_delivery(batch, root)["cases"][0]
    assert row["title"] == "改过的标题" and row["origin"] == "human"


def test_concurrent_edits_to_different_cases_are_both_preserved(root):
    batch = make_batch(root)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda args: workbench.update_case(batch, args[0], args[1], root),
            [
                ("TC-001", {"title": "并发改标题"}),
                ("TC-002", {"expected": "并发改预期"}),
            ],
        ))

    assert all(result["ok"] for result in results)
    rows = {row["case_id"]: row for row in workbench.read_delivery(batch, root)["cases"]}
    assert rows["TC-001"]["title"] == "并发改标题"
    assert rows["TC-002"]["expected"] == "并发改预期"
    assert rows["TC-001"]["origin"] == rows["TC-002"]["origin"] == "human"
    batch_dir = os.path.join(root, batch)
    assert not [name for name in os.listdir(batch_dir) if name.endswith(".tmp")]


def test_identity_fields_cannot_be_edited(root):
    batch = make_batch(root)
    with pytest.raises(WorkbenchError) as err:
        workbench.update_case(batch, "TC-001", {"case_id": "TC-999"}, root)
    assert err.value.code == "FIELD_NOT_EDITABLE"


def test_an_edit_that_breaks_the_case_is_refused_and_nothing_is_written(root):
    """0010 硬约束二的延伸：没有理由允许合格的用例被改成不合格之后留在库里。"""
    batch = make_batch(root)
    before = workbench.read_delivery(batch, root)["cases"][0]["title"]
    with pytest.raises(WorkbenchError) as err:
        workbench.update_case(batch, "TC-001", {"request": {
            "method": "POST", "url": "{{baseUrl}}/a",
            "assertions": [{"type": "json_path", "path": "", "expected": 1}]}}, root)
    assert err.value.code == "CASE_INVALID"
    assert workbench.read_delivery(batch, root)["cases"][0]["title"] == before


def test_editing_an_unknown_case_is_a_named_error(root):
    batch = make_batch(root)
    with pytest.raises(WorkbenchError) as err:
        workbench.update_case(batch, "TC-404", {"title": "x"}, root)
    assert err.value.code == "CASE_NOT_FOUND"


def test_editing_a_legacy_batch_degrades_out_loud(root):
    name = "20260101-090000-旧批次"
    os.makedirs(os.path.join(root, name))
    with pytest.raises(WorkbenchError) as err:
        workbench.update_case(name, "TC-001", {"title": "x"}, root)
    assert err.value.code == "NO_CASES_JSON"
