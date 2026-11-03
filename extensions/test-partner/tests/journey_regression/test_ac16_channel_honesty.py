# -*- coding: utf-8 -*-
"""AC16 · 能力缺失显式告知（bug-bank BB-667 / BB-668 的回归锚）。

- BB-667：测试模式降级到无工具时界面零提示，用户以为是模型在犯傻。
- BB-668：TAPD MCP 掉线后不重连，断连期被「暂时没有查询到需求」这类业务话术
  掩盖——用户去改查询条件，而不是去修连接。

这里钉死纪律半边（提示词锚，文件级）：通道不通必须首轮明说 + 给替代路径，
禁止业务化说辞盖住连接问题。**界面侧降级提示（BB-667 的产品修复）与重连机制
（BB-668）不在本样例覆盖内**——那两半修复落地时需各自的注入测试，
本样例的绿不代表它们修了。
"""


def test_channel_honesty_discipline_is_in_the_prompt(prompt_body):
    zh = "通道不通就说通道不通" in prompt_body
    en = "say the channel is down" in prompt_body
    assert zh or en, "首轮明说连接问题的纪律不在提示词里（BB-667/BB-668 复发通道）"


def test_business_wording_coverup_is_explicitly_banned(prompt_body):
    zh = "不许用业务化的说辞盖住连接问题" in prompt_body
    en = "Never paper over a connection problem" in prompt_body
    assert zh or en, "禁止业务话术掩盖断连的纪律不在提示词里（BB-668 的话术形态）"


def test_the_misleading_phrase_is_named_as_the_counterexample(prompt_body):
    """反例要指名道姓：BB-668 里那句话术必须作为反面教材钉在提示词里。"""
    zh = "暂时没有查询到符合条件的需求" in prompt_body
    en = "No\nmatching requirements found" in prompt_body.replace("\r\n", "\n") \
        or "matching requirements found at the moment" in prompt_body
    assert zh or en, "误导性话术的具名反例不在提示词里"


def test_an_alternative_path_is_offered(prompt_body):
    zh = "替代路径" in prompt_body
    en = "the alternative path" in prompt_body
    assert zh or en, "断连时的替代路径指引不在提示词里"
