# -*- coding: utf-8 -*-
"""sot_gate 移植验收：原件 test_agent/loop/sot_gate.js selftest 向量逐条过（护栏 2）。

夹具逐字抄自原件（golden，禁改）；期望判决与原件 selftest 一致。
"""
import pytest

from server.journey.gates import sot_gate

FIX_DONE = """# X — SOT
## 被测构建
分支tip=abcd123 · 部署构建=abcd123(build_confirm 探部署 bundle 命中 marker v7) · 一致?是 · 冻结部署marker=v7
## doc×code 对齐
| 验收点 | 一致 / doc_missing / code_missing / 冲突 | 处置 |
| AC1 下拉新增选项 | 一致 | — |
| AC3 类型过滤 | doc_missing:实现端多一条兜底分支 | 已上浮产品 |
"""
FIX_EMPTY_NO_DECL = """# X — SOT
## 被测构建
分支tip=<commit> · 部署构建=<commit/待确认> · 一致?[是 / 否→影响]
## doc×code 对齐
| 验收点 | 一致 / doc_missing / code_missing / 冲突 | 处置 |
## gap
| 缺口 | 描述 | 目标层 |
"""
FIX_DEGRADED_NO_DECL = """# X — SOT
## 被测构建
未确认。本轮未走 deployment-fingerprint 探测,结论只到设计层。
## doc×code 对齐
未执行。未指定目标仓库,本轮范围止步于用例设计。
"""
FIX_DECL_OWNER_ONLY = FIX_DEGRADED_NO_DECL + """
## 零access降级声明
| 缺栏 | 谁来补 | 补不到的后果 |
| 被测构建 | 执行前由我跑 deployment-fingerprint | 待定 |
| doc×code 对齐 | 开发给仓库指针后由我补 | — |
"""
FIX_DECL_CONSEQ_ONLY = FIX_DEGRADED_NO_DECL + """
## 零access降级声明
| 缺栏 | 谁来补 | 补不到的后果 |
| 被测构建 | — | 补不到则本轮所有结论封顶"假设",执行期一律不作数 |
| doc×code 对齐 | 待定 | 补不到则"代码做了文档没说"的分支永远进不了用例,漏需求风险留着 |
"""
FIX_DECL_RESTATE = FIX_DEGRADED_NO_DECL + """
## 零access降级声明
| 缺栏 | 谁来补 | 补不到的后果 |
| 被测构建 | 执行前由我补 | 没做 |
| doc×code 对齐 | 开发给指针后我补 | 未执行 |
"""
FIX_DECL_GOOD = FIX_DEGRADED_NO_DECL + """
## 零access降级声明
| 缺栏 | 谁来补 | 补不到的后果 |
| 被测构建 | 进 N4 前由我跑 deployment-fingerprint;拿不到则找提测方要部署 commit | 补不到 → 本轮全部结论封顶"假设",任何 FAIL 只能记 PENDING,不作数 |
| doc×code 对齐 | 需求方/开发给出目标仓库指针后由我做反向对账 | 补不到 → "代码做了文档没说"的分支查不出来,漏需求的风险留在本轮之外没人接 |
"""
FIX_MISSING_SEC = """# X — SOT
## R 点(应然)
| R点 | 描述 |
## gap
| 缺口 |
"""
FIX_ONE_DONE_ONE_DEGRADED = """# X — SOT
## 被测构建
分支tip=abcd123 · 部署构建=abcd123(运行时探针命中 marker v7) · 一致?是
## doc×code 对齐
未执行。无目标仓库。
"""
FIX_DONE_NUMBERED = FIX_DONE.replace("## 被测构建", "## 1. 被测构建") \
                            .replace("## doc×code 对齐", "## 二、doc×code 对齐")
FIX_DECL_GOOD_NUMBERED = FIX_DECL_GOOD.replace("## 被测构建", "## （1）被测构建") \
                                      .replace("## doc×code 对齐", "## ② doc×code 对齐") \
                                      .replace("## 零access降级声明", "## 第三节 零access降级声明")
FIX_HEAD_SHAPE = """# X — SOT
## 关于被测构建的说明
分支tip=abcd123 · 部署构建=abcd123(探针命中 marker v7) · 一致?是
## doc×code 对齐
| 验收点 | 一致 | 处置 |
| AC1 | 一致 | — |
| AC2 下拉 | 一致 | — |
"""


@pytest.mark.parametrize("name,text,want", [
    ("两栏都做实 → 过", FIX_DONE, "PASS"),
    ("BB-259 同内容+标题带编号 → 照过", FIX_DONE_NUMBERED, "PASS"),
    ("BB-259 降级声明齐 + 三种编号形态 → 照过", FIX_DECL_GOOD_NUMBERED, "PASS"),
    ("两栏空模板 + 无降级声明 → 拦", FIX_EMPTY_NO_DECL, "REJECT"),
    ("两栏标未执行 + 无降级声明 → 拦", FIX_DEGRADED_NO_DECL, "REJECT"),
    ("有谁来补、后果占位 → 拦", FIX_DECL_OWNER_ONLY, "REJECT"),
    ("有后果、谁来补占位 → 拦", FIX_DECL_CONSEQ_ONLY, "REJECT"),
    ("后果只是复述没做 → 拦", FIX_DECL_RESTATE, "REJECT"),
    ("降级声明齐(谁来补+真后果) → 过", FIX_DECL_GOOD, "PASS"),
    ("整节缺失 → 拦", FIX_MISSING_SEC, "REJECT"),
    ("一栏做实一栏降级无声明 → 拦", FIX_ONE_DONE_ONE_DEGRADED, "REJECT"),
    ("BB-259 标题词不在开头 → 仍拦", FIX_HEAD_SHAPE, "REJECT"),
])
def test_sot_gate_original_vectors(name, text, want):
    r = sot_gate.check(text)
    assert r["verdict"] == want, f"{name}: {r['problems']}"


def test_constructive_done_column_not_flagged():
    # 原件构造性断言：做实的栏不被误拦，只拦 doc×code 那栏
    r = sot_gate.check(FIX_ONE_DONE_ONE_DEGRADED)
    assert r["columns"]["build_under_test"]["status"] == "DONE"
    assert r["problems"] and all("doc" in p for p in r["problems"])


def test_head_shape_error_points_to_format_not_content():
    r = sot_gate.check(FIX_HEAD_SHAPE)
    assert any("标题格式不符" in p for p in r["problems"])
    assert not any("缺「被测构建」节" in p for p in r["problems"])
