"""提示词的回归测试。

提示词是这条链路里唯一"影响产出质量、又没有类型约束"的部分——
没有测试的话，一次手滑删掉一条约束不会有任何征兆，只会让生成质量悄悄变差。
所以这里断言的是**每条约束还在**，而不是措辞。
"""

from __future__ import annotations

import json

import pytest

from server.generate.context import Material
from server.generate.prompts import (
    DETAIL_SYSTEM,
    OUTLINE_SYSTEM,
    SCENARIO_SYSTEM,
    detail_prompt,
    outline_prompt,
    scenario_prompt,
)


@pytest.fixture()
def material():
    return Material(
        endpoints=[{"method": "POST", "path": "/api/order/create", "calls": 14,
                    "statuses": {"200": 12, "400": 2},
                    "sample_body": {"skuId": "SKU-1", "qty": 2},
                    "query_keys": ["from"]}],
        login={"method": "POST", "path": "/api/auth/login"},
        stats={"requests": 412, "noise_filtered": 297, "endpoints": 23},
        notes=[], scrub_hits={})


ALL_PROMPTS = ("scenario", "outline", "detail")


def _render(kind: str, m: Material) -> str:
    if kind == "scenario":
        return scenario_prompt(m)
    if kind == "outline":
        return outline_prompt(m, "登录后下单", max_cases=6)
    return detail_prompt(m, "登录后下单",
                         [{"id": "TC-001", "title": "正常下单", "intent": "正常"}])


# ── 三段共有的纪律 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ALL_PROMPTS)
def test_every_prompt_tells_the_model_placeholders_are_redactions(kind, material):
    """不写这句，模型会自作主张把 <手机号> 填成一个像样的真手机号，

    用例就带上了假数据——比留着占位符坏得多。
    """
    text = _render(kind, material)
    assert "<手机号>" in text and "占位符" in text
    assert "绝不要编造" in text


@pytest.mark.parametrize("kind", ALL_PROMPTS)
def test_every_prompt_carries_the_material(kind, material):
    text = _render(kind, material)
    assert "/api/order/create" in text
    assert "412" in text and "23" in text


def test_login_presence_is_stated_either_way(material):
    with_login = scenario_prompt(material)
    assert "/api/auth/login" in with_login

    without = Material(endpoints=material.endpoints, login=None,
                       stats=material.stats, notes=[], scrub_hits={})
    assert "没有识别出登录请求" in scenario_prompt(without)


# ── 第一段：一段大白话，不是表单 ─────────────────────────────────────────

def test_scenario_prompt_asks_for_one_plain_paragraph(material):
    """用户明确否掉过结构化字段版本——「用户也不知道」什么叫数据约束、前置条件。"""
    text = scenario_prompt(material)
    assert "一段话" in text
    assert "不要分条" in text
    for banned in ("数据约束", "前置条件"):
        assert banned not in text, f"这一段不该再出现结构化字段：{banned}"


def test_scenario_prompt_bans_technical_vocabulary(material):
    text = scenario_prompt(material)
    assert "不要出现接口路径" in text or "不要出现" in text


def test_scenario_prompt_gives_the_users_own_example(material):
    """范本是一句示范不是一张表格——用的就是用户给的那句。"""
    assert "输入密码登录，点击首页，切换设备，退出登录" in scenario_prompt(material)


def test_scenario_prompt_is_not_json_mode(material):
    """它的产出要直接显示给人读、给人改，不是结构化数据。"""
    assert "只输出 JSON" not in scenario_prompt(material)


# ── 第二段：AI 挑端点 + 只出清单 ─────────────────────────────────────────

def test_outline_prompt_makes_the_model_pick_endpoints(material):
    """挑端点是 AI 的活不是用户的活——一份 HAR 几百个接口，让人逐个勾是外抛苦力活。"""
    text = outline_prompt(material, "登录后下单", max_cases=6)
    assert "挑出" in text and "picked_endpoints" in text
    assert "skipped_reason" in text, "没挑的也要给理由，否则用户无从判断挑得对不对"


def test_outline_prompt_only_asks_for_titles_not_details(material):
    """借鉴 Apifox 的分步生成：改标题比改整条用例便宜十倍。"""
    text = outline_prompt(material, "登录后下单", max_cases=6)
    assert "只要标题和意图" in text
    assert "不要写请求和断言" in text


def test_outline_prompt_caps_the_case_count(material):
    assert "不超过 6 条" in outline_prompt(material, "x", max_cases=6)
    assert "不超过 20 条" in outline_prompt(material, "x", max_cases=20)


def test_outline_prompt_requires_marking_speculation(material):
    """HAR 没有 schema，模型容易凭常识编"应该有"的行为。要求它自己标出来。"""
    text = outline_prompt(material, "登录后下单", max_cases=6)
    assert "assumption" in text
    assert "推测" in text


# ── 第三段：字段只能来自素材，断言不能只有 status ─────────────────────────

def test_detail_prompt_forbids_inventing_field_names(material):
    """借鉴 MeterSphere：把"编出不存在的字段"从架构上掐掉，而不是事后校验去抓。"""
    text = detail_prompt(material, "s", [{"id": "TC-001"}])
    assert "只能来自" in text
    assert "不许出现" in text or "一律不许" in text


def test_detail_prompt_forbids_status_only_assertions(material):
    """只断言状态码的用例跑起来只能证明接口活着。这条与 case_validate 的 W05 同源。"""
    text = detail_prompt(material, "s", [{"id": "TC-001"}])
    assert "不要只断言 status" in text
    assert "至少再断言一项" in text


def test_detail_prompt_requires_token_placeholder_not_real_credentials(material):
    text = detail_prompt(material, "s", [{"id": "TC-001"}])
    assert "Bearer {{token}}" in text
    assert "绝不要写真实令牌" in text


def test_detail_prompt_pins_url_to_the_endpoint(material):
    """E13（URL 与来源端点对不上）是实际发生过的模型错误，提示词先堵一道。"""
    text = detail_prompt(material, "s", [{"id": "TC-001"}])
    assert "完全一致" in text


def test_detail_prompt_echoes_the_confirmed_case_list(material):
    cases = [{"id": "TC-007", "title": "优惠券过期应报错", "intent": "边界"}]
    text = detail_prompt(material, "s", cases)
    assert "TC-007" in text and "优惠券过期应报错" in text


# ── 输出格式：两段结构化的都要明说不要围栏 ───────────────────────────────

@pytest.mark.parametrize("kind", ("outline", "detail"))
def test_structured_prompts_demand_bare_json(kind, material):
    text = _render(kind, material)
    assert "只输出 JSON" in text
    assert "代码围栏" in text


def test_system_prompts_are_distinct_and_non_empty():
    prompts = {SCENARIO_SYSTEM, OUTLINE_SYSTEM, DETAIL_SYSTEM}
    assert len(prompts) == 3
    assert all(p.strip() for p in prompts)


def test_material_block_is_valid_json_inside_the_prompt(material):
    """端点清单是 JSON 嵌在提示词里；拼坏了模型读到的就是垃圾。"""
    text = outline_prompt(material, "s", max_cases=3)
    start = text.index("[", text.index("端点清单"))
    depth, end = 0, start
    for i, ch in enumerate(text[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert json.loads(text[start:end])[0]["path"] == "/api/order/create"
