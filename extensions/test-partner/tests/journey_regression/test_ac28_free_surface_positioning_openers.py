# -*- coding: utf-8 -*-
"""AC28 · 定位段：助手为主干、旅程为场景之一（`free_surface_positioning_openers`）。

这是**框架层**的防复发锚。同一维度已经被纠正三次：spec r3 的
「测试模式以聊天 agent 为主干」被落成了「旅程的 agent 化」，于是用户丢一条 curl
只能得到"不在本版能力内"。锚在这里是为了让下一次改提示词的人一眼看见这条线。

诚实分级：本文件钉的是**纪律在不在提示词里**。
「模型真的不往接入流程上引」由 AC28 证据②（干净上下文 judge 判三例非旅程开场）
在 UAT 会话里证，本文件绿不代表那一条绿。
"""
from __future__ import annotations


def test_the_spine_is_an_assistant_not_a_pipeline(prompt_body):
    zh = "主干是随时接住任何测试问题" in prompt_body
    en = "The spine is catching any" in prompt_body and "testing question" in prompt_body
    assert zh or en, "「主干 = 随时接测试问题的测试助手」不在定位段里"


def test_the_journey_is_declared_as_one_scenario(prompt_body):
    zh = "是其中**一个场景**" in prompt_body and "我要正式测" in prompt_body
    en = ("is one scenario" in prompt_body
          and "want to properly test requirement" in prompt_body)
    assert zh or en, "「九格旅程 = 正式测需求时才进入的场景之一」不在定位段里"


def test_the_common_openers_are_named_one_by_one(prompt_body):
    """curl / 报错 / 日志 / 疑问——四样都要点名，只写"任何问题"等于没写。"""
    zh = all(word in prompt_body for word in ("curl", "报错", "日志", "疑问"))
    en = all(word in prompt_body for word in ("curl", "error", "log", "question"))
    assert zh or en, "常见开场清单不在定位段里"


def test_opening_with_a_curl_must_be_handled_on_the_spot(prompt_body):
    zh = "常见开场就地处理" in prompt_body and "当场" in prompt_body
    en = "Handle the common openers where they land" in prompt_body
    assert zh or en, "「当场处理」的纪律不在定位段里"


def test_steering_a_free_form_opener_into_intake_is_banned(prompt_body):
    """反面话术要指名道姓，否则下一版又会长出一句同义的引导。"""
    zh = ("请提供需求 ID" in prompt_body and "我们先接入需求" in prompt_body
          and "不得回" in prompt_body)
    en = ("please provide a requirement ID" in prompt_body
          and "let's onboard the requirement first" in prompt_body)
    assert zh or en, "「不得往接入流程上引」的具名反例不在定位段里"


def test_not_asking_to_test_a_requirement_means_no_intake_steering(prompt_body):
    zh = "就不要往接入流程上引" in prompt_body
    en = "do not steer them into intake" in prompt_body
    assert zh or en, "「没说要正式测需求就别引向接入」不在定位段里"


# ── 反向样例：旅程入口不能被这次重写打坏 ─────────────────────────────────
def test_the_journey_intake_path_is_still_intact(prompt_body):
    """AC28 证据③的机械半边：说「我要正式测 XX 需求」仍照常进接入。"""
    zh = "接入→澄清→用例→采纳→执行" in prompt_body
    en = "intake" in prompt_body and "adoption" in prompt_body and "execution" in prompt_body
    assert zh or en, "旅程主线在定位段重写后不见了"


def test_the_existing_journey_sections_are_still_there(prompt_body):
    """旅程各段原文不动（AC30）：几处关键段落的锚句逐条还在。"""
    zh_anchors = [
        "候选清单的默认口径",          # AC4 口径
        "疑点**一次性攒批问**",         # AC12 攒批
        "采纳是这条链上唯一不可逆的点",  # ADR-2 唯一硬闸
        "**默认一条写用例都不勾。**",    # AC6 披露
    ]
    en_anchors = [
        "Default shape of the candidate list",
        "adoption is the only",
        "Ask about open questions in one batch",
    ]
    assert (all(a in prompt_body for a in zh_anchors)
            or any(a in prompt_body for a in en_anchors)), "旅程既有段落被改动了"
