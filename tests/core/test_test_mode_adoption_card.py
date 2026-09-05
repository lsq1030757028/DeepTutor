"""[fork] 采纳卡的披露与选项以服务端签发为准，不取决于模型抄得认不认真（BB-715 补修）。

2026-09-05 UAT（会话 unified_1788603204448_e9762dd3，批次 b-20260905-3ced7b6d）：
journey_prepare_adoption 返回的 `disclosure_md` 八条用例三行齐（d3~d8 的"写什么"
值为空），模型贴进 ask_user intro 时把值为空的那六行整行删掉；卡上 d3~d8 就只剩
两行。提示词已经写着"一字不删"，但提示词只是愿望。下面钉住宿主的机械保证：

1. 本轮签发过的卡，宿主记得（从工具结果里读 card / disclosure_md）；
2. 模型调 ask_user 时命中卡 id 的问题按卡校回，intro 以 disclosure_md 原文打头；
3. 没签过卡 / 没命中的问题原样放行。
"""

from __future__ import annotations

import json

from deeptutor.agents.test.pipeline import TestJourneyPipeline, enforce_adoption_card
from deeptutor.core.context import UnifiedContext

CARD_ID = "journey_adopt:b-20260905-3ced7b6d:37bfdfcbb21e0709751152a0"
CARD = {
    "id": CARD_ID,
    "multi_select": True,
    # 服务端契约：采纳卡保留自由文本行，用户能在卡上说「先不采纳」
    "allow_free_text": True,
    "options": [{"label": f"d{i}", "description": f"用例 {i}"} for i in range(1, 9)],
}
DISCLOSURE_MD = "\n".join(
    line
    for i in range(1, 9)
    for line in (
        f"- d{i} 用例 {i}：**会写数据**",
        f"  - 写什么：{'测试环境-测试商品下单' if i <= 2 else ''}",
        "  - 怎么清理：测试环境下单后作废/退款该测试订单",
        "  - 凭什么授权：仅限测试环境测试门店与测试商品",
    )
)
PREPARE_RESULT = json.dumps(
    {"ok": True, "code": "NEEDS_GATE", "needs_gate": "adoption", "card": CARD,
     "disclosure": [], "disclosure_md": DISCLOSURE_MD},
    ensure_ascii=False,
    indent=2,
)


def _model_ask_user_args() -> dict:
    """模型实际发出的形状：删掉了空值行、把卡改成了允许自由文本、还加了一问。"""
    intro_lines = [
        line for line in DISCLOSURE_MD.splitlines() if not line.endswith("写什么：")
    ]
    return {
        "intro": "第一批用例草稿（d1-d8）逐条披露如下：\n\n" + "\n".join(intro_lines),
        "questions": [
            {
                "id": CARD_ID,
                "header": "采纳(d1-d8)",
                "prompt": "请勾选要采纳的用例：",
                "multi_select": False,
                "allow_free_text": False,
                "options": [{"label": f"d{i}", "description": f"用例 {i}"} for i in range(1, 8)],
            },
            {
                "id": "write_authorization_batch1",
                "header": "写授权(d1-d8)",
                "prompt": "请勾选要授权实际执行的用例：",
                "multi_select": True,
                "options": [{"label": "全部不授权（推荐）"}]
                + [{"label": f"d{i}"} for i in range(1, 9)],
            },
        ],
    }


def test_intro_is_led_by_the_server_disclosure_verbatim() -> None:
    fixed = enforce_adoption_card(_model_ask_user_args(), {CARD_ID: {"card": CARD, "disclosure_md": DISCLOSURE_MD}})
    assert fixed["intro"].startswith(DISCLOSURE_MD)
    # 打头的披露段八条用例每条三行都在，包括值为空的"写什么"
    lead = fixed["intro"][: len(DISCLOSURE_MD)]
    assert lead.count("写什么：") == 8
    assert lead.count("怎么清理：") == 8
    assert lead.count("凭什么授权：") == 8
    # 模型自己的话保留在披露之后
    assert "第一批用例草稿（d1-d8）逐条披露如下" in fixed["intro"]


def test_the_card_question_is_restored_to_what_the_server_issued() -> None:
    fixed = enforce_adoption_card(_model_ask_user_args(), {CARD_ID: {"card": CARD, "disclosure_md": DISCLOSURE_MD}})
    card_q = fixed["questions"][0]
    assert [o["label"] for o in card_q["options"]] == [f"d{i}" for i in range(1, 9)]
    assert card_q["multi_select"] is True
    assert card_q["allow_free_text"] is True  # 以卡为准：自由文本行回来了
    # 模型自己加的写授权问题不动（9 个选项交给 ask_user 的上限判）
    other = fixed["questions"][1]
    assert other["id"] == "write_authorization_batch1"
    assert len(other["options"]) == 9


def test_an_intro_that_already_carries_the_disclosure_is_left_alone() -> None:
    args = _model_ask_user_args()
    args["intro"] = DISCLOSURE_MD + "\n\n请选择。"
    fixed = enforce_adoption_card(args, {CARD_ID: {"card": CARD, "disclosure_md": DISCLOSURE_MD}})
    assert fixed["intro"] == args["intro"]


def test_questions_that_match_no_card_pass_through_untouched() -> None:
    args = {"questions": [{"id": "tier_selection", "prompt": "档位？", "options": ["a", "b"]}]}
    assert enforce_adoption_card(args, {CARD_ID: {"card": CARD, "disclosure_md": DISCLOSURE_MD}}) is args
    assert enforce_adoption_card(_model_ask_user_args(), {}) is not None


def test_pipeline_remembers_the_issued_card_and_enforces_it_on_ask_user() -> None:
    pipeline = TestJourneyPipeline(language="zh")
    context = UnifiedContext(session_id="s-adopt", user_message="先看采纳卡", active_capability="test")

    # 未签卡：ask_user 参数原样通过
    before = pipeline._augment_tool_kwargs("ask_user", _model_ask_user_args(), context)
    assert before["intro"].count("写什么：") == 2

    # 工具结果里出现签发的卡（journey_prepare_adoption 的 role=tool 消息）
    pipeline._remember_adoption_cards(
        [{"role": "tool", "tool_call_id": "call_1", "content": "Result:\n" + PREPARE_RESULT}]
    )
    after = pipeline._augment_tool_kwargs("ask_user", _model_ask_user_args(), context)
    assert after["intro"].startswith(DISCLOSURE_MD)
    assert after["intro"][: len(DISCLOSURE_MD)].count("写什么：") == 8
    assert after["questions"][0]["allow_free_text"] is True
    assert len(after["questions"][0]["options"]) == 8

    # 别的工具不受影响
    other = pipeline._augment_tool_kwargs("rag", {"query": "q"}, context)
    assert other["query"] == "q"
