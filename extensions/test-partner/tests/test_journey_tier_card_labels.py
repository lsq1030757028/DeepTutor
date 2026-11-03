"""档位卡的选项文案是给人看的：中文在前，枚举值只在括号里（判据 4，2026-09-05）。

UAT 实测深度卡落到用户面前是 "standard / checklist / deep" 三个裸英文标签——
模型照着服务端 card.options[].label 的开头写。value 仍是服务端枚举，不动。
"""

from __future__ import annotations

import re

from server.journey import ingest


def test_tier_card_labels_lead_with_chinese_and_keep_the_enum_value():
    card = ingest.propose_tier("需求正文 " * 100)["card"]
    values = [o["value"] for o in card["options"]]
    assert sorted(values) == sorted(ingest.TIERS)
    for option in card["options"]:
        label = option["label"]
        assert re.match(r"^[一-鿿]", label), f"label 不是中文开头: {label}"
        assert not re.match(r"^(standard|checklist|deep)", label, re.I), label
        assert f"（{option['value']}）" in label, f"label 里要带枚举值: {label}"
    assert card["recommended"] in ingest.TIERS
