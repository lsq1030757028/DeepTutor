"""[fork] BB-717 — a ``<tool_call>`` block in the content channel is a tool call or nothing.

2026-09-05 UAT (session ``unified_1788603204448_e9762dd3``): twice in one
walk-through the model wrote ``ask_user`` as a text-protocol block whose JSON
was slightly broken, and the whole block landed in the chat body as the final
answer — no card, trajectory ``tool calls 0``. Both raw blocks below are
verbatim from that session. The host must turn them into the call the model
meant; a block it truly cannot parse must still never be shown as text.
"""

from __future__ import annotations

import json

from deeptutor.agents.chat.text_tool_calls import (
    extract_text_tool_calls,
    has_text_tool_call_markup,
    lenient_json_object,
    repair_json_text,
)

# S2 reply: unescaped double quotes inside the ``prompt`` string.
S2_RAW = (
    '<tool_call> {"name": "ask_user", "input": {"questions": [{"id": "requirement_source", '
    '"header": "需求来源", "prompt": "这次查"已提测且处理人是我"的需求，是从 TAPD 取，'
    '还是你想直接把需求正文或附件贴给我？", "options": [{"label": "从 TAPD 取（推荐）", '
    '"description": "延续刚才的项目，按状态=已提测、处理人=我筛选"}, {"label": "我贴正文/附件", '
    '"description": "不查 TAPD，你直接提供需求内容"}]}]}} </tool_call>'
)

# S3 final reply after "先不采纳": one stray ``]}`` pair before the end.
S3_RAW = (
    '<tool_call> {"name": "ask_user", "input": {"intro": "已按你的选择处理：d1-d8 这一批本轮不采纳，'
    '也没有授权任何写操作——这 8 条用例现在还停留在草稿状态，没有进入用例库，被测系统没有任何数据变化。", '
    '"questions": [{"id": "next_step_after_defer", "header": "下一步", "prompt": "13 条用例草稿全部涉及'
    '下单写操作，第一批（d1-d8）你选择暂不采纳。接下来想怎么走？", "options": [{"label": "先看第二批草稿'
    '再说（推荐）", "description": "继续把 d9-d13 的采纳卡也过一遍"}, {"label": "全部暂停，先不采纳任何用例", '
    '"description": "本轮到用例草稿为止"}, {"label": "调整用例范围后再谈采纳", "description": '
    '"减少写操作数量，我可以重新设计"}]}]}]}} </tool_call>'
)


def test_both_uat_blocks_are_broken_json_as_written() -> None:
    for raw in (S2_RAW, S3_RAW):
        block = raw[len("<tool_call> ") : -len(" </tool_call>")]
        try:
            json.loads(block)
        except json.JSONDecodeError:
            continue
        raise AssertionError("fixture is valid JSON; it no longer reproduces the defect")


def test_unescaped_inner_quotes_are_repaired_into_the_intended_call() -> None:
    calls, cleaned = extract_text_tool_calls(S2_RAW)
    assert len(calls) == 1
    assert calls[0]["name"] == "ask_user"
    args = json.loads(calls[0]["arguments"])
    prompt = args["questions"][0]["prompt"]
    assert '这次查"已提测且处理人是我"的需求' in prompt
    assert [o["label"] for o in args["questions"][0]["options"]] == [
        "从 TAPD 取（推荐）",
        "我贴正文/附件",
    ]
    assert cleaned == ""


def test_stray_closing_brackets_are_repaired_into_the_intended_call() -> None:
    calls, cleaned = extract_text_tool_calls(S3_RAW)
    assert len(calls) == 1
    args = json.loads(calls[0]["arguments"])
    assert args["intro"].startswith("已按你的选择处理")
    assert len(args["questions"][0]["options"]) == 3
    assert cleaned == ""


def test_prose_before_the_block_survives_and_the_block_does_not() -> None:
    calls, cleaned = extract_text_tool_calls("先说一句。\n" + S2_RAW)
    assert calls and calls[0]["name"] == "ask_user"
    assert cleaned == "先说一句。"
    assert "<tool_call>" not in cleaned


def test_an_unrepairable_block_is_stripped_not_answered() -> None:
    raw = '前言\n<tool_call>{"name": "ask_user", "input": {"questions": [{"id": }]}}</tool_call>'
    calls, cleaned = extract_text_tool_calls(raw)
    assert calls == []
    assert "<tool_call>" not in cleaned and "ask_user" not in cleaned
    assert cleaned == "前言"


def test_a_truncated_block_is_stripped_too() -> None:
    raw = '<tool_call>{"name": "ask_user", "input": {"questions": ['
    calls, cleaned = extract_text_tool_calls(raw)
    assert calls == []
    assert cleaned == ""


def test_plain_prose_is_untouched() -> None:
    text = "这一批用例没有写操作，可以直接采纳。"
    assert not has_text_tool_call_markup(text)
    calls, cleaned = extract_text_tool_calls(text)
    assert calls == []
    assert cleaned is text


def test_repair_leaves_valid_json_alone() -> None:
    raw = '{"name": "x", "input": {"a": "he said \\"hi\\"", "n": [1, 2]}}'
    assert json.loads(repair_json_text(raw)) == json.loads(raw)
    assert lenient_json_object(raw) == json.loads(raw)
