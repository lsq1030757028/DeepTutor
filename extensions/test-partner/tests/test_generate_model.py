"""模型调用防线的回归测试。

三条防线各自对应一个实测出来的坑，测试就照着坑写：
超时/重试的默认值、截断与写错的区分、花钱参数的夹取。
"""

from __future__ import annotations

import pytest

from server.generate.model import (
    MAX_CALLS_PER_JOB,
    MAX_TOKENS_CEILING,
    SPEC_DETAIL,
    SPEC_OUTLINE,
    SPEC_SCENARIO,
    TIMEOUT_CEILING_S,
    CallSpec,
    OutputMalformed,
    OutputTruncated,
    budget_ok,
    clamp,
    parse_json_output,
)


# ── 防线一：超时与重试的默认值本身就是闸 ──────────────────────────────────

def test_default_spec_pins_retries_to_one():
    """上游默认带指数退避、最坏累计 500 秒量级。默认值必须是安全的那个，

    而不是靠调用点记得传——「忘记传」应当是不可能，不是"要注意"。
    """
    spec = CallSpec(max_tokens=100, timeout_s=10)
    assert spec.max_retries == 1
    assert spec.exponential_backoff is False


def test_retries_above_two_are_rejected():
    with pytest.raises(ValueError, match="账单放大器"):
        CallSpec(max_tokens=100, timeout_s=10, max_retries=3)


@pytest.mark.parametrize("kwargs", [
    {"max_tokens": 0, "timeout_s": 10},
    {"max_tokens": MAX_TOKENS_CEILING + 1, "timeout_s": 10},
    {"max_tokens": 100, "timeout_s": 0},
    {"max_tokens": 100, "timeout_s": TIMEOUT_CEILING_S + 1},
])
def test_out_of_range_spec_is_rejected_at_construction(kwargs):
    """越界在构造时就炸，不留到真发请求才发现。"""
    with pytest.raises(ValueError, match="越界"):
        CallSpec(**kwargs)


def test_the_three_shipped_specs_are_within_ceilings():
    """三个档位是"一共会花多少"的唯一可审计处，钉住它们别悄悄涨。"""
    for spec in (SPEC_SCENARIO, SPEC_OUTLINE, SPEC_DETAIL):
        assert 1 <= spec.max_tokens <= MAX_TOKENS_CEILING
        assert 1 <= spec.timeout_s <= TIMEOUT_CEILING_S
    assert SPEC_SCENARIO.json_mode is False, "起草场景要的是大白话不是 JSON"
    assert SPEC_OUTLINE.json_mode and SPEC_DETAIL.json_mode


# ── 防线二：截断 vs 写错，必须判成两种错 ──────────────────────────────────

def test_valid_json_parses():
    assert parse_json_output('{"cases": [1, 2]}') == {"cases": [1, 2]}


def test_markdown_fence_is_stripped():
    """json_mode 下仍有模型会加围栏。"""
    assert parse_json_output('```json\n{"a": 1}\n```') == {"a": 1}


@pytest.mark.parametrize("truncated", [
    '{"cases": [{"id": "TC-001", "title": "正常下单',      # 断在字符串里
    '{"cases": [{"id": "TC-001"}, {"id": "TC-002"',        # 断在对象里
    '[{"a": 1}, {"b": ',                                    # 断在值之前
])
def test_truncated_output_is_reported_as_truncation(truncated):
    """截断的处置是**减量重试**，与"写错了"完全不同。

    判错方向的代价很实在：把半截 JSON 当成写错送去修复，
    模型会把它补全成一个看起来合法、内容却是编的结果。
    """
    with pytest.raises(OutputTruncated):
        parse_json_output(truncated)


@pytest.mark.parametrize("malformed", [
    "{'cases': [1]}",              # 单引号
    '{"a": 1,}',                   # 尾逗号
    '{a: 1}',                      # 键没引号
    "这不是 JSON",
])
def test_malformed_but_complete_output_is_reported_as_malformed(malformed):
    """括号平衡但解析失败 = 模型确实写错了，处置是定向修复。"""
    with pytest.raises(OutputMalformed):
        parse_json_output(malformed)


def test_empty_output_is_malformed_not_truncated():
    """空字符串括号"平衡"，但它不是截断——别把它导去减量重试。"""
    with pytest.raises(OutputMalformed, match="空内容"):
        parse_json_output("   ")


def test_brackets_inside_strings_do_not_confuse_the_judge():
    """业务文案里带括号是常态，不能因此误判成截断。"""
    payload = '{"title": "下单时 {库存} 不足应拒绝 [重要]"}'
    assert parse_json_output(payload)["title"].startswith("下单时")


# ── 防线三：花钱的天花板 ──────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [0, -1, 999, "abc", None, 1.5e9, [], {}])
def test_hostile_values_fall_back_to_default(bad):
    """这些值都来自请求体。传 0 会除零，传 1 会把调用次数放大数倍。"""
    assert clamp(bad, low=2, high=10, default=5) == 5


@pytest.mark.parametrize("good,expect", [(2, 2), (5, 5), (10, 10), ("7", 7)])
def test_in_range_values_pass_through(good, expect):
    assert clamp(good, low=2, high=10, default=5) == expect


def test_job_budget_has_a_hard_ceiling():
    assert budget_ok(0) and budget_ok(MAX_CALLS_PER_JOB - 1)
    assert not budget_ok(MAX_CALLS_PER_JOB)
    assert not budget_ok(MAX_CALLS_PER_JOB + 50)
