"""导出与下载路径的离线测试（设计稿第 7 屏的服务端）。

三条最重要：
1. 导出写进**批次目录内**，重导出覆盖同名文件（刷新，不堆副本）；
2. 产物指纹取自 `cases.json`（与采纳时一致，不是导出时新造的）；
3. 下载路径穿越进不来——文件名只能是批次目录下的一级文件名。
"""

from __future__ import annotations

import json
import os
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
