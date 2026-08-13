# -*- coding: utf-8 -*-
"""downstream_gate 移植验收：原件 test_agent/loop/downstream_gate.js selftest 向量逐条过。

夹具逐字抄自原件（golden，禁改）；期望判决与原件 selftest 一致（21 例 + 构造性断言 8 条）。
"""
import pytest

from server.journey.gates import downstream_gate as dg

HEAD = "| 业务对象 | 生命周期环节 | 判别用例 | 不覆盖声明(谁覆盖·后果) |\n|---|---|---|---|\n"
FULL_OK = f"""# X — test_analysis
## 下游消费面盘点
{HEAD}| 对象A(本次新增) | 获得 | TC-01 | — |
| 同上 | 使用 | TC-02 | — |
| 同上 | 同类共存 | TC-03 | — |
| 同上 | 计量结算 | TC-04 | — |
| 同上 | 撤销退回 | — | 不覆盖:由承接该环节的需求测试负责 · 后果=本轮不担保对象撤销后的状态正确,风险留在本次范围外 |
| 同上 | 记账对账 | — | 不覆盖:由财务侧对账需求负责 · 后果=对不上账要到月结才暴露,本轮查不出 |
"""
FULL_OK_NUMBERED = FULL_OK.replace("## 下游消费面盘点", "## 三、下游消费面盘点")
ALT_HEADER = """# X — test_analysis
## 3. 下游消费面盘点
| 对象 | 环节 | 怎么被消费 | 判别用例或不覆盖声明 |
|---|---|---|---|
| 对象A | 获得 | 下游读它建档 | TC-01 |
| 同上 | 使用 | 下游按它放行 | TC-02 |
| 同上 | 同类共存 | 多种并存 | TC-03 |
| 同上 | 计量结算 | 计费用它 | TC-04 |
| 同上 | 撤销退回 | 回滚要用 | 不覆盖:由承接该环节的需求测试负责 · 后果=本轮不担保撤销后状态正确,风险留在范围外 |
| 同上 | 记账对账 | 月结对账 | 不覆盖:由财务侧对账需求负责 · 后果=对不上账要到月结才暴露,本轮查不出 |
"""
NO_HEADER = """# X — test_analysis
## 下游消费面盘点
| 对象A | 获得 | TC-01 | — |
| 同上 | 使用 | TC-02 | — |
"""
HEAD_SHAPE_BAD = """# X — test_analysis
## 关于下游的一些说明
| 业务对象 | 生命周期环节 | 判别用例 | 不覆盖声明 |
|---|---|---|---|
| 对象A | 获得 | TC-01 | — |
"""
NO_SECTION = """# X — test_analysis
## Example Map
| R点 | Charter |
"""
EMPTY_SECTION = """# X — test_analysis
## 下游消费面盘点
(略)
"""
EMPTY_WITH_DECL = """# X — test_analysis
## 下游消费面盘点
本需求不产出可被下游消费的业务对象:改动只在后台配置页的一个只读展示字段,不生成任何会流转到别的环节的数据。
"""
MISS_COEXIST = f"""# X — test_analysis
## 下游消费面盘点
{HEAD}| 对象A | 获得 | TC-01 | — |
| 同上 | 使用 | TC-02 | — |
| 同上 | 计量结算 | TC-04 | — |
| 同上 | 撤销退回 | TC-05 | — |
| 同上 | 记账对账 | TC-06 | — |
"""
BLANK_STAGE = f"""# X — test_analysis
## 下游消费面盘点
{HEAD}| 对象A | 获得 | TC-01 | — |
| 同上 | 使用 | — | — |
| 同上 | 同类共存 | TC-03 | — |
| 同上 | 计量结算 | TC-04 | — |
| 同上 | 撤销退回 | TC-05 | — |
| 同上 | 记账对账 | TC-06 | — |
"""
DECL_NO_OWNER = f"""# X — test_analysis
## 下游消费面盘点
{HEAD}| 对象A | 获得 | TC-01 | — |
| 同上 | 使用 | — | 不覆盖:本轮范围外,后果=用不出去也发现不了 |
| 同上 | 同类共存 | TC-03 | — |
| 同上 | 计量结算 | TC-04 | — |
| 同上 | 撤销退回 | TC-05 | — |
| 同上 | 记账对账 | TC-06 | — |
"""
DECL_NO_CONSEQ = f"""# X — test_analysis
## 下游消费面盘点
{HEAD}| 对象A | 获得 | TC-01 | — |
| 同上 | 使用 | — | 不覆盖:由下一轮需求的测试负责人覆盖 |
| 同上 | 同类共存 | TC-03 | — |
| 同上 | 计量结算 | TC-04 | — |
| 同上 | 撤销退回 | TC-05 | — |
| 同上 | 记账对账 | TC-06 | — |
"""
ALL_UNCOVERED = f"""# X — test_analysis
## 下游消费面盘点
{HEAD}| 对象A | 获得 | — | 不覆盖:由需求方后续安排 · 后果=风险留着 |
| 同上 | 使用 | — | 不覆盖:由需求方后续安排 · 后果=风险留着没人接 |
| 同上 | 同类共存 | — | 不覆盖:由需求方后续安排 · 后果=风险留着没人接 |
| 同上 | 计量结算 | — | 不覆盖:由需求方后续安排 · 后果=风险留着没人接 |
| 同上 | 撤销退回 | — | 不覆盖:由需求方后续安排 · 后果=风险留着没人接 |
| 同上 | 记账对账 | — | 不覆盖:由需求方后续安排 · 后果=风险留着没人接 |
"""
NUMBERED_CONT = f"""# X — test_analysis
## 下游消费面盘点
{HEAD}| ① 对象A | 获得 | TC-01 | — |
| ① 同上 | 使用 | TC-02 | — |
| ① 同上 | 同类共存 | TC-03 | — |
| ① 同上 | 计量结算 | TC-04 | — |
| ① 同上 | 撤销退回 | TC-05 | — |
| ① 同上 | 记账对账 | TC-06 | — |
"""
SINGLE_LETTER_REF = """# X — 分析
## 下游消费面盘点
| 业务对象 | 生命周期环节 | 判别用例 | 不覆盖声明(谁覆盖·后果) |
|---|---|---|---|
| 甲对象 | 获得/产生 | R1 | — |
| 同上 | 使用/消费 | R2 | — |
| 同上 | 同类共存 | R3 | — |
| 同上 | 计量结算 | R4 | — |
| 同上 | 撤销退回 | — | 不覆盖:由下一轮的回归用例承接 · 后果=撤销路径的错误本轮发现不了,风险留在范围外 |
| 同上 | 记账对账 | — | 不覆盖:由财务对账测试范围承接 · 后果=汇总口径错本轮查不出,可能造成账目不符 |
"""
SINGLE_LETTER_CASES = """# X — cases.md
| 用例ID | 关联R点 | 场景 | 预期结果 |
|---|---|---|---|
| TC-01 | R1 | 产生 | 与预期一致 |
| TC-02 | R2 | 消费 | 与预期一致 |
| TC-03 | R3 | 共存 | 与预期一致 |
| TC-04 | R4 | 结算 | 金额与预期一致 |
"""
SINGLE_LETTER_CASES_MISSING = """# X — cases.md
| 用例ID | 关联R点 | 场景 | 预期结果 |
|---|---|---|---|
| TC-01 | R1 | 产生 | 与预期一致 |
| TC-02 | R2 | 消费 | 与预期一致 |
"""
PAREN_STAGE = """# X — 分析
## 下游消费面盘点
| 业务对象 | 生命周期环节 | 判别用例 | 不覆盖声明(谁覆盖·后果) |
|---|---|---|---|
| 甲对象 | 获得/产生(后台配置产生) | TC-01 | — |
| 同上 | 使用/消费(用户实际购买并支付) | TC-02 | — |
| 同上 | 同类共存(新旧两种同时存在,各自生效) | TC-03 | — |
| 同上 | 计量结算(按新口径计算金额) | TC-04 | — |
| 同上 | 撤销退回(购买后退款,按精确值退还是截断退) | TC-05 | — |
| 同上 | 记账对账(财务统计口径) | TC-06 | — |
"""
TWO_TABLES = PAREN_STAGE + """
**新增下游 Rule(为盘点补齐)**

| R点(补) | Charter | 判别 Example | 执行配方 | role |
|---|---|---|---|---|
| R5 | Explore 某风险 with 某数据 to discover 某偏差 | 略 | 略 | ai |
| R6 | Explore 另一风险 with 另一数据 to discover 另一偏差 | 略 | 略 | ai |
"""


@pytest.mark.parametrize("name,text,ids,want", [
    ("六档齐+不覆盖带谁接和后果 → 准入", FULL_OK, None, "PASS"),
    ("整节缺失 → 打回", NO_SECTION, None, "REJECT"),
    ("节在但空 → 打回", EMPTY_SECTION, None, "REJECT"),
    ("节空但有不产出显式声明 → 准入", EMPTY_WITH_DECL, None, "PASS"),
    ("缺同类共存档 → 打回", MISS_COEXIST, None, "REJECT"),
    ("某环节既无用例也无声明 → 打回", BLANK_STAGE, None, "REJECT"),
    ("不覆盖没写谁覆盖 → 打回", DECL_NO_OWNER, None, "REJECT"),
    ("不覆盖没写后果 → 打回", DECL_NO_CONSEQ, None, "REJECT"),
    ("整条生命周期全声明不覆盖 → 打回", ALL_UNCOVERED, None, "REJECT"),
    ("声明的用例在用例集里不存在 → 打回(假覆盖)", FULL_OK, {"TC-99"}, "REJECT"),
    ("声明的用例都在用例集里 → 准入", FULL_OK,
     {"TC-01", "TC-02", "TC-03", "TC-04"}, "PASS"),
    ("单字母 id(R1式)从用例集正文收得到 → 准入", SINGLE_LETTER_REF,
     dg.collect_case_ids_from_text(SINGLE_LETTER_CASES), "PASS"),
    ("单字母 id 真的不在用例集里 → 仍打回", SINGLE_LETTER_REF,
     dg.collect_case_ids_from_text(SINGLE_LETTER_CASES_MISSING), "REJECT"),
    ("环节格带括号解释 → 准入(解释词不抢档位)", PAREN_STAGE, None, "PASS"),
    ("盘点表后紧跟补充表 → 准入(补充表行不算对象)", TWO_TABLES, None, "PASS"),
    ("对象格带编号+①同上续行 → 准入", NUMBERED_CONT, None, "PASS"),
    ("BB-259 标题带中文编号 → 照过", FULL_OK_NUMBERED, None, "PASS"),
    ("BB-259 列名同义词/列序不同/合并列 → 照过", ALT_HEADER, None, "PASS"),
    ("BB-259 负例：整张表没表头 → 打回", NO_HEADER, None, "REJECT"),
    ("BB-259 负例：标题词沾边但形状不对 → 打回", HEAD_SHAPE_BAD, None, "REJECT"),
])
def test_downstream_gate_original_vectors(name, text, ids, want):
    r = dg.check(text, ids)
    assert r["verdict"] == want, f"{name}: {r['problems']}"


def test_assert_coexist_named():
    r = dg.check(MISS_COEXIST, None)
    assert any("同类共存" in p for p in r["problems"])


def test_assert_no_header_reports_format_only():
    r = dg.check(NO_HEADER, None)
    assert len(r["problems"]) == 1
    assert "表头认不出来" in r["problems"][0] and "期望形状" in r["problems"][0]
    joined = " ".join(r["problems"])
    assert "这一环节是空的" not in joined and "盘点缺环节" not in joined and "零覆盖" not in joined


def test_assert_head_shape_not_missing_section():
    r = dg.check(HEAD_SHAPE_BAD, None)
    assert any("标题格式不符" in p and "期望标题" in p for p in r["problems"])
    assert not any("缺「下游消费面盘点」节" in p for p in r["problems"])


def test_assert_alt_header_still_strict():
    alt_bad = ALT_HEADER.replace(
        "| 同上 | 记账对账 | 月结对账 | 不覆盖:由财务侧对账需求负责 · 后果=对不上账要到月结才暴露,本轮查不出 |",
        "| 同上 | 记账对账 | 月结对账 | — |")
    r = dg.check(alt_bad, None)
    assert any("记账对账" in p and "这一环节是空的" in p for p in r["problems"])


def test_assert_all_stages_recognized():
    r = dg.check(FULL_OK, None)
    obj = next(iter(r["objects"].values()))
    assert len(obj["stages"]) == len(dg.STAGES)


def test_assert_numbered_ditto_same_object():
    r = dg.check(NUMBERED_CONT, None)
    assert len(r["objects"]) == 1


def test_assert_supplementary_table_not_object():
    r = dg.check(TWO_TABLES, None)
    assert len(r["objects"]) == 1


def test_assert_paren_stage_by_lead_label():
    r = dg.check(PAREN_STAGE, None)
    obj = next(iter(r["objects"].values()))
    assert len(obj["stages"]) == len(dg.STAGES) and not obj["extra"]
