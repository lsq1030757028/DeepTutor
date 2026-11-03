# -*- coding: utf-8 -*-
"""AC21 · 口径异常自检的纪律锚（文件级）。

## 这条判据在防什么

实测形态：同一个 TAPD 项目，按 `owner` 口径查回 3 条、按「测试人」自定义字段
口径查回 64 条。agent 若把 3 条当全集端出去，用户看到的是一份「这个项目没什么
可测的」的假象——错的不是查询本身，是没做口径自检。

## 本样例能证什么、不能证什么

**只能证纪律在提示词里**。AC21 的判据主体是行为层的：注入 owner 口径场景后，
agent 的输出里要真的出现交叉验证动作与差异说明——那需要一次带注入的 UAT 会话
+ 人读判卷，**本样例的绿不代表模型照做**。这里钉的是它的必要条件：
纪律一旦从提示词里掉了，行为层就没有任何来源。

五条断言各钉一条独立义务（触发条件 / 禁止动作 / 要求动作 / 回报形态 / 非可选），
刻意不共用同一句锚——共用一句 = 少一条覆盖。
"""


def test_the_self_check_trigger_is_named(prompt_body):
    """触发条件必须具名：结果数明显少于同类总量时要自检，不是「觉得不对劲时」。"""
    zh = "口径自检" in prompt_body
    en = "Self-check the query shape" in prompt_body
    assert zh or en, "口径自检的触发点不在提示词里（AC21 复发通道）"


def test_a_small_result_may_not_be_served_as_the_whole_set(prompt_body):
    """禁止动作：把少量结果当全集直接端出去——owner=3 条那次的原始症状。"""
    zh = "不要把少量结果当全集" in prompt_body
    en = "do not serve the small" in prompt_body
    assert zh or en, "「少量结果不得当全集」的禁令不在提示词里"


def test_cross_validation_against_the_other_candidate_field_is_required(prompt_body):
    """要求动作：换另一个候选字段交叉验证——只警告不给动作等于没纪律。"""
    zh = "交叉验证" in prompt_body
    en = "cross-check against the other candidate" in prompt_body
    assert zh or en, "交叉验证另一候选字段的动作不在提示词里"


def test_the_difference_is_reported_with_both_counts(prompt_body):
    """回报形态：差异要带两个口径的条数报给用户，不是自己挑一个悄悄用。"""
    zh = "按 B 口径 64 条" in prompt_body
    en = "64 by B" in prompt_body
    assert zh or en, "差异回报（两个口径各多少条）的示范不在提示词里"


def test_the_self_check_is_not_optional(prompt_body):
    """非可选：写成「建议」就会被模型当礼貌性步骤跳过，必须钉成判断义务。"""
    zh = "不是可选的礼貌" in prompt_body
    en = "not optional politeness" in prompt_body
    assert zh or en, "口径自检被写成可选项（软化即失效）"
