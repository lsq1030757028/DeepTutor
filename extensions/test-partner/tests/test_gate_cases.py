# -*- coding: utf-8 -*-
"""cases_gate 移植验收：原件 test_agent/loop/cases_gate.js selftest 向量逐条过。

夹具逐字抄自原件（golden，禁改）；期望判决与原件 selftest 一致（16 例 + 构造性断言 5 条）。
"""
import json
import os

import pytest

from server.journey.gates import cases_gate as cg

MD_WITH_CASES = """# X — cases.md
## 用例
| 用例ID | 场景 | 步骤 | 预期结果 |
|---|---|---|---|
| TC-01 | 正常保存 | 按配方执行主流程 | 业务:页面提示保存成功,列表里该项显示为提交的值 · 取证:接口返回码为成功 |
| TC-02 | 边界 | 按配方执行边界输入 | 业务:被拦下并给出可读提示,列表里的值不变 |
"""
MD_NO_CASES = """# X — cases.md
## 优先级行动清单
P0 先做配置类,P1 再做展示类。本文件后续补充具体用例。
"""
MD_HEADING_CASES = """# X — cases.md
### TC-01 正常保存
步骤:按配方执行主流程。预期 业务:页面提示保存成功。
### TC-02 边界
步骤:按配方执行边界输入。预期 业务:被拦下并给出可读提示。
"""
MD_WHITEBOX_ONLY = """# X — cases.md
## 用例
| 用例ID | 场景 | 步骤 | 预期结果 |
|---|---|---|---|
| TC-01 | 存储精度 | 按配方执行 | 落库字段值为最小单位整数 |
| TC-02 | 内部配置 | 按配方执行 | 缓存单键被写为带小数的值 |
"""
MD_PROBE_MARKED = """# X — cases.md
## 用例
| 用例ID | 场景 | 步骤 | 预期结果 |
|---|---|---|---|
| TC-01 | 存储精度 | 按配方执行 | 探测:落库字段值(实现推导,不作判决依据,只记录现象回填 gap) |
| TC-02 | 主流程 | 按配方执行 | 业务:账单上的金额与下单时展示的一致 |
"""
MD_ONLY_EVIDENCE = """# X — cases.md
## 用例
| 用例ID | 场景 | 步骤 | 预期结果 |
|---|---|---|---|
| TC-01 | 主流程 | 按配方执行 | 取证:接口返回码为成功,落库值非空 |
"""
MD_MIXED_OK = """# X — cases.md
## 用例
| 用例ID | 场景 | 步骤 | 预期结果 |
|---|---|---|---|
| TC-01 | 主流程 | 按配方执行 | 业务:商家后台看到提交的值,账单实付金额与之一致 |
| TC-02 | 内部行为 | 按配方执行 | 探测:内部字段取整方式未确认,本条只记录现象,不作判决依据 |
"""
MD_PARTIAL_MISS = """# X — cases.md
## 用例
| 用例ID | 场景 | 步骤 | 预期结果 |
|---|---|---|---|
| TC-01 | 主流程 | 按配方执行 | 业务:页面提示成功 |
| TC-02 | 边界 | 按配方执行 | 业务:被拦下 |
| TC-03 | 内部 | 按配方执行 | 该字段按向下取整存储 |
"""
EXEC_JSON_NO_LAYER = json.dumps(
    {"meta": {}, "cases": [{"id": "TC-01", "expect": "落库值为最小单位整数"}]})
ANALYSIS_TEXT = "# X — test_analysis\n## Example Map\n| Rule | Example |\n|---|---|\n| R1 | 略 |\n"
EXEC_JSON = json.dumps({"meta": {}, "cases": [
    {"id": "TC-01", "expect": "业务:页面提示保存成功"},
    {"id": "TC-02", "expect": "业务:被拦下并给出可读提示"}]})
EXEC_JSON_EMPTY = json.dumps({"meta": {}, "cases": []})
IDX = json.dumps({"schema_version": "1.0", "generated_by": "kit/tools/cases_index.js",
                  "total": 1, "cases": [{"id": "TC-01"}]})


def mk(tmp_path, files):
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return str(tmp_path)


VECTORS = [
    ("有分析+有用例表格+机器清单 → 过",
     {"test_analysis.md": ANALYSIS_TEXT, "cases.md": MD_WITH_CASES, "cases_index.json": IDX},
     "PASS"),
    ("有分析+用例走小标题形态 → 过",
     {"test_analysis.md": ANALYSIS_TEXT, "cases.md": MD_HEADING_CASES, "cases_index.json": IDX},
     "PASS"),
    ("有分析+cases_exec.json 非空 → 过",
     {"test_analysis.md": ANALYSIS_TEXT, "cases_exec.json": EXEC_JSON}, "PASS"),
    ("有分析+完全没有用例集产物 → 拦",
     {"test_analysis.md": ANALYSIS_TEXT, "confirmed_facts.md": "# SOT"}, "REJECT"),
    ("有分析+有 cases.md 但零用例行 → 拦",
     {"test_analysis.md": ANALYSIS_TEXT, "cases.md": MD_NO_CASES}, "REJECT"),
    ("有分析+cases_exec.json 空数组 → 拦",
     {"test_analysis.md": ANALYSIS_TEXT, "cases_exec.json": EXEC_JSON_EMPTY}, "REJECT"),
    ("没走过设计 → 不适用放行", {"exec_result.json": "{}"}, "PASS"),
    ("预期全是实现推导值 → 拦",
     {"test_analysis.md": ANALYSIS_TEXT, "cases.md": MD_WHITEBOX_ONLY}, "REJECT"),
    ("实现推导的那条显式标探测 → 过",
     {"test_analysis.md": ANALYSIS_TEXT, "cases.md": MD_PROBE_MARKED, "cases_index.json": IDX},
     "PASS"),
    ("只有取证层 → 拦",
     {"test_analysis.md": ANALYSIS_TEXT, "cases.md": MD_ONLY_EVIDENCE}, "REJECT"),
    ("业务层与探测层混排 → 过",
     {"test_analysis.md": ANALYSIS_TEXT, "cases.md": MD_MIXED_OK, "cases_index.json": IDX},
     "PASS"),
    ("3 条里 1 条没标 → 拦",
     {"test_analysis.md": ANALYSIS_TEXT, "cases.md": MD_PARTIAL_MISS}, "REJECT"),
    ("cases_exec.json 的用例没标层 → 拦",
     {"test_analysis.md": ANALYSIS_TEXT, "cases_exec.json": EXEC_JSON_NO_LAYER}, "REJECT"),
    ("多面 run:一面齐一面缺 → 拦",
     {"admin/test_analysis.md": ANALYSIS_TEXT, "admin/cases.md": MD_WITH_CASES,
      "admin/cases_index.json": IDX, "backend/test_analysis.md": ANALYSIS_TEXT}, "REJECT"),
    ("只交 markdown 无机器清单 → 拦",
     {"test_analysis.md": ANALYSIS_TEXT, "cases.md": MD_WITH_CASES}, "REJECT"),
    ("cases_exec.json 本身是机器产物 → 不额外要清单",
     {"test_analysis.md": ANALYSIS_TEXT, "cases_exec.json": EXEC_JSON}, "PASS"),
]


@pytest.mark.parametrize("name,files,want", VECTORS)
def test_cases_gate_original_vectors(tmp_path, name, files, want):
    r = cg.check(mk(tmp_path, files))
    assert r["verdict"] == want, f"{name}: {r['problems']}"


def test_assert_ok_surface_not_flagged(tmp_path):
    d = mk(tmp_path, {"admin/test_analysis.md": ANALYSIS_TEXT,
                      "admin/cases.md": MD_WITH_CASES, "admin/cases_index.json": IDX,
                      "backend/test_analysis.md": ANALYSIS_TEXT})
    r = cg.check(d)
    assert len(r["problems"]) == 1 and "backend" in r["problems"][0]


def test_assert_header_row_not_counted():
    only_header = "# X\n| 用例ID | 场景 | 预期结果 |\n|---|---|---|\n"
    assert cg.count_md_cases(only_header)["n"] == 0


def test_assert_duplicate_id_deduped():
    dup = "# X\n### TC-01 a\n| 用例ID | 场景 |\n|---|---|\n| TC-01 | a |\n| TC-02 | b |\n"
    assert cg.count_md_cases(dup)["n"] == 2


def test_assert_layer_problem_named(tmp_path):
    d = mk(tmp_path, {"test_analysis.md": ANALYSIS_TEXT, "cases.md": MD_PARTIAL_MISS,
                      "cases_index.json": IDX})
    r = cg.check(d)
    assert len(r["problems"]) == 1
    assert "判决权分层" in r["problems"][0] and "TC-03" in r["problems"][0]
    assert "没有用例集产物" not in r["problems"][0]


def test_assert_layer_format_only_not_semantic(tmp_path):
    d = mk(tmp_path, {"test_analysis.md": ANALYSIS_TEXT, "cases_index.json": IDX,
                      "cases.md": "# X\n| 用例ID | 预期结果 |\n|---|---|\n| TC-01 | 业务:随便一句话 |\n"})
    assert cg.check(d)["verdict"] == "PASS"


def test_problem_kinds_aligned(tmp_path):
    d = mk(tmp_path, {"test_analysis.md": ANALYSIS_TEXT, "cases.md": MD_PARTIAL_MISS})
    r = cg.check(d)
    assert len(r["problem_kinds"]) == len(r["problems"])
    assert set(r["problem_kinds"]) == {"consumable", "layering"}
