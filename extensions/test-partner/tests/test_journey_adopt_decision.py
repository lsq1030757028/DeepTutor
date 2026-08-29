# -*- coding: utf-8 -*-
"""采纳卡决定令牌（ADR-2 / AC7）——批次 0 的 A7 风险穿刺。

## 这条测试在证什么

spec 假设 A7：`decision_auth` 的签发/校验链**能否**扩展到承载「一批写用例的
内容摘要集」，而不必重写整套机制。它是 ADR-2 的落地关口：如果承载不了，
唯一硬闸方案就得回 ADR-2 重议——**不许靠放宽校验绕过**（会触发 0025 重开条件）。

## 与写确认卡的差别（为什么不能直接复用 expected_question）

`source_case_digest` 与 `case_id` 都在**采纳时刻**才分配（`adopt.py` 的稳定
身份分配）。采纳卡在采纳**之前**弹，那时手里只有 `case_draft`，所以卡只能绑
草稿态内容摘要 `digest.draft_case_digest`。内容一致则摘要一致——这就是
「用例改了旧采纳授权自动失效」（AC18）的锚。

## 三种注入（AC7 的证据口径）

1. 篡改摘要：伪造一张摘要不同的卡 → 与重算契约对不上，拒。
2. 重放 jti：同一份决定消费两次 → 账本里查得到，拒。
3. 改用例后复用：草稿内容变了再拿旧令牌 → 卡 id 与选项同时对不上，拒。
"""
import base64
import copy
import hashlib
import hmac
import json
import secrets
import time

import pytest

from server.journey import artifacts, bridge_auth, decision_auth, digest

from tests.test_journey_exec import (  # noqa: F401 - fixtures 经 import 生效
    GOOD_ANALYSIS, GOOD_FACTS, api_case, store, target)
from tests.journey_decision_helper import SECRET, trusted_claims

TRUSTED_OWNER = "unit-test-owner"


@pytest.fixture(autouse=True)
def _decision_secret(monkeypatch):
    monkeypatch.setenv("TEST_JOURNEY_BRIDGE_SECRET", SECRET)


def _draft_batch(store, target, cases, *, owner=TRUSTED_OWNER):
    """只跑到 case_draft 为止——采纳卡是在采纳之前弹的。"""
    from server.journey import analyze, clarify, draft_cases, ingest
    with artifacts.trusted_owner(owner):
        r = ingest.ingest("采纳卡自测", target, source_kind="requirement_doc",
                          source_ref="local", requirement_text="接口需求正文",
                          tier="standard", tier_confirmed_via="test", owner=owner)
        assert r["ok"], r
        bid = r["batch_id"]
        assert clarify.clarify(bid, rules=[{"rule_id": "R1", "statement": "接口规则",
                                            "source_quote": "q"}],
                               confirmed_facts_md=GOOD_FACTS)["ok"]
        assert analyze.analyze(bid, example_map=[
            {"rule_id": "R1", "charter": "接口", "examples": [{"id": "e1"}]}],
            analysis_md=GOOD_ANALYSIS)["ok"]
        assert draft_cases.draft(bid, cases=cases)["ok"]
        return bid


def _load_draft(bid):
    return artifacts.load_artifact(bid, "case_draft")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _adopt_arguments(bid, selected, authorized):
    return {"batch_id": bid, "selected_draft_ids": list(selected),
            "authorized_write_draft_ids": list(authorized),
            "caseset_slug": "adopttest"}


def adopt_decision(bid, case_draft, selected, authorized, *, question=None,
                   owner=TRUSTED_OWNER, session="session-test", turn="turn-test",
                   jti=None, now=None, answer_ids=None):
    """测试专用签发方：模拟 DT 侧真实完成的 ask_user 采纳卡。"""
    issued = int(time.time()) if now is None else int(now)
    card = question or decision_auth.expected_adopt_question(
        bid, case_draft, selected)
    payload = {
        "v": 1, "iss": "deeptutor", "aud": decision_auth.AUDIENCE,
        "owner_id": owner, "session_id": session, "turn_id": turn,
        "capability": "test", "surface": "capability",
        "tool": "journey_adopt",
        "args_sha256": bridge_auth.arguments_sha256(
            _adopt_arguments(bid, selected, authorized)),
        "ask_user_tool_call_id": "ask-adopt-test-1",
        "ask_user": {"intro": None, "questions": [card]},
        "answers": [{"questionId": card["id"],
                     "text": ",".join(authorized if answer_ids is None else answer_ids)}],
        "resolved_at": issued, "iat": issued, "exp": issued + 300,
        "jti": jti or secrets.token_urlsafe(16),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    token = f"{_b64(raw)}.{_b64(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest())}"
    claims = trusted_claims("journey_adopt", owner=owner, session=session,
                            turn=turn, now=issued)
    return token, claims


def _verify(bid, case_draft, selected, authorized, token, claims, *, consumed=()):
    return decision_auth.verify_adopt_context(
        token, bridge=claims, batch_id=bid, case_draft=case_draft,
        selected_draft_ids=selected, authorized_write_draft_ids=authorized,
        arguments=_adopt_arguments(bid, selected, authorized),
        consumed_jtis=consumed)


# ── 承载：令牌真的绑住了写用例摘要集 ────────────────────────────────────────


def test_adopt_card_binds_the_write_case_digest_set(store, target):
    """A7 的正面答案：卡上逐条披露写用例的内容摘要，令牌照单验签。"""
    cases = [api_case("dw1", writes=True), api_case("dw2", writes=True),
             api_case("dr1")]
    bid = _draft_batch(store, target, cases)
    draft = _load_draft(bid)
    selected = ["dw1", "dw2", "dr1"]

    card = decision_auth.expected_adopt_question(bid, draft, selected)
    # 只读用例不上授权行——给不写数据的用例授权没有意义（沿用 write_confirm 取舍 2）
    assert [o["label"] for o in card["options"]] == ["dw1", "dw2"]
    by_id = {c["draft_id"]: c for c in draft["cases"]}
    # 2026-08-29 判据翻面：**卡面描述不再挂逐条摘要**。原来每行开头是
    # `sha256:<64 hex>`，用户在授权卡上读到的第一样东西是他完全用不上的串
    # （Round 2 复验第 13 条）。绑定没削弱——卡 id 由 adopt_set_digest 派生，
    # 覆盖采纳集里每一条草稿的内容摘要，改一个字 id 就对不上（下一条测试守）；
    # 逐条摘要搬进了 tools 的 disclosure，模型仍拿得到。
    for option in card["options"]:
        assert "sha256:" not in option["description"]
        assert option["description"] == by_id[option["label"]].get("title", "")
    assert card["id"].endswith(
        decision_auth.adopt_set_digest(
            decision_auth.adopt_selection(draft, selected)
        ).removeprefix("sha256:")[:24])

    token, claims = adopt_decision(bid, draft, selected, ["dw1"])
    decision = _verify(bid, draft, selected, ["dw1"], token, claims)
    assert decision["selected"] == ["dw1"]
    assert decision["declined"] == ["dw2"]
    # 派生写授权事件要落的就是这份摘要（AC8）
    assert decision["write_digests"] == {
        "dw1": digest.draft_case_digest(by_id["dw1"])}
    assert decision["jti"] and decision["ask_user_tool_call_id"]


def test_empty_authorization_is_a_legal_adopt_answer(store, target):
    """「采纳但一条写的都不授权」是合法答案，不是错误——与写确认卡同语义。"""
    bid = _draft_batch(store, target, [api_case("dw1", writes=True),
                                       api_case("dr1")])
    draft = _load_draft(bid)
    selected = ["dw1", "dr1"]
    token, claims = adopt_decision(bid, draft, selected, [])
    decision = _verify(bid, draft, selected, [], token, claims)
    assert decision["selected"] == [] and decision["declined"] == ["dw1"]
    assert decision["write_digests"] == {}


def test_read_only_adoption_needs_no_write_choices(store, target):
    bid = _draft_batch(store, target, [api_case("dr1"), api_case("dr2")])
    draft = _load_draft(bid)
    selected = ["dr1", "dr2"]
    card = decision_auth.expected_adopt_question(bid, draft, selected)
    assert card["options"] == []
    token, claims = adopt_decision(bid, draft, selected, [])
    assert _verify(bid, draft, selected, [], token, claims)["selected"] == []


# ── 注入 1：篡改摘要 ────────────────────────────────────────────────────────


def test_injection_tampered_digest_on_the_card_is_rejected(store, target):
    """模型自己编一张「摘要好看一点」的卡 → 与服务端重算的契约对不上。"""
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    draft = _load_draft(bid)
    selected = ["dw1"]
    forged = copy.deepcopy(decision_auth.expected_adopt_question(bid, draft, selected))
    forged["options"][0]["description"] = "sha256:" + "0" * 64 + " | API dw1"
    token, claims = adopt_decision(bid, draft, selected, ["dw1"], question=forged)
    with pytest.raises(decision_auth.DecisionAuthError,
                       match="adoption card options does not match"):
        _verify(bid, draft, selected, ["dw1"], token, claims)


def test_injection_tampered_card_id_is_rejected(store, target):
    """卡 id 绑整个采纳集摘要——换掉 id 等于换掉它代表的那批内容。"""
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    draft = _load_draft(bid)
    selected = ["dw1"]
    forged = copy.deepcopy(decision_auth.expected_adopt_question(bid, draft, selected))
    forged["id"] = "journey_adopt:{0}:{1}".format(bid, "f" * 24)
    token, claims = adopt_decision(bid, draft, selected, ["dw1"], question=forged)
    with pytest.raises(decision_auth.DecisionAuthError,
                       match="adoption card id does not match"):
        _verify(bid, draft, selected, ["dw1"], token, claims)


def test_tool_arguments_must_match_the_signed_decision(store, target):
    """工具参数与用户所答不一致（越权多授一条）→ 拒。"""
    cases = [api_case("dw1", writes=True), api_case("dw2", writes=True)]
    bid = _draft_batch(store, target, cases)
    draft = _load_draft(bid)
    selected = ["dw1", "dw2"]
    token, claims = adopt_decision(bid, draft, selected, ["dw1"])
    with pytest.raises(decision_auth.DecisionAuthError):
        _verify(bid, draft, selected, ["dw1", "dw2"], token, claims)


# ── 注入 2：重放 ────────────────────────────────────────────────────────────


def test_injection_replayed_jti_is_rejected_not_merely_detectable(store, target):
    """同一份决定消费两次：第二次必须**判红**。

    ## 这条测试的第一版是错的，值得留个记号

    第一版只断言「jti 能在账本里查得到」，然后把校验的**成功返回值**拿去断言——
    它证明的是「可检出」，不是「被拒绝」。独立复核用探针实测：那时同一令牌原文
    连验两次都被接受。判据写着「三种注入均被拒」，实际只落了两种。

    「可检出」与「被拒绝」之间隔着一段调用方必须自己写的代码。把那段代码留给
    调用方，就等于把闸留给约定——忘了写，校验照样返回成功，没有任何东西会变红。
    所以 `consumed_jtis` 现在是必填参数，忘了传是 TypeError。
    """
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    draft = _load_draft(bid)
    selected = ["dw1"]
    fixed_jti = secrets.token_urlsafe(16)

    token, claims = adopt_decision(bid, draft, selected, ["dw1"], jti=fixed_jti)
    first = _verify(bid, draft, selected, ["dw1"], token, claims)
    with artifacts.trusted_owner(TRUSTED_OWNER):
        artifacts.append_event(bid, {"type": "adopt_confirm",
                                     "decision_jti": first["jti"]})
    prior = {str(e.get("decision_jti") or "")
             for e in artifacts.read_events(bid)
             if e.get("type") == "adopt_confirm"}
    assert first["jti"] in prior

    # 重新签一份同 jti 的令牌（模型换个签发时刻重放同一个决定）
    token2, claims2 = adopt_decision(bid, draft, selected, ["dw1"], jti=fixed_jti)
    with pytest.raises(decision_auth.DecisionAuthError, match="已经消费过"):
        _verify(bid, draft, selected, ["dw1"], token2, claims2, consumed=prior)

    # 原样重放同一份令牌，同样判红
    with pytest.raises(decision_auth.DecisionAuthError, match="已经消费过"):
        _verify(bid, draft, selected, ["dw1"], token, claims, consumed=prior)


def test_replay_check_cannot_be_forgotten_by_the_caller(store, target):
    """`consumed_jtis` 必填：忘了传就炸在接线那一刻，而不是安静放行。"""
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    draft = _load_draft(bid)
    token, claims = adopt_decision(bid, draft, ["dw1"], ["dw1"])
    with pytest.raises(TypeError):
        decision_auth.verify_adopt_context(
            token, bridge=claims, batch_id=bid, case_draft=draft,
            selected_draft_ids=["dw1"], authorized_write_draft_ids=["dw1"],
            arguments=_adopt_arguments(bid, ["dw1"], ["dw1"]))


def test_a_forged_token_reads_as_forged_not_as_replayed(store, target):
    """成对错误态分开说：签名不对的令牌不许因为 jti 撞车被报成「重放」。"""
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    draft = _load_draft(bid)
    token, claims = adopt_decision(bid, draft, ["dw1"], ["dw1"], jti="collide")
    forged = token.rsplit(".", 1)[0] + "." + _b64(b"not-a-real-signature")
    with pytest.raises(decision_auth.DecisionAuthError, match="signature does not match"):
        _verify(bid, draft, ["dw1"], ["dw1"], forged, claims, consumed={"collide"})


def test_answer_that_disagrees_with_the_tool_arguments_is_rejected(store, target):
    """守卫 `set(selected) != set(requested)` 的直接覆盖。

    复核指出这道守卫在第一版里零覆盖：`authorized_write_draft_ids` 本身在
    `arguments` 里，所以「越权多授」总是先被 `args_sha256` 挡下，走不到这一行。
    这里让**签名里的答案**与**工具参数**各说各话（参数说只授 dw1，卡上答的是
    dw1+dw2），才真正打到它——将来这行被误删会有测试变红。
    """
    cases = [api_case("dw1", writes=True), api_case("dw2", writes=True)]
    bid = _draft_batch(store, target, cases)
    draft = _load_draft(bid)
    selected = ["dw1", "dw2"]
    token, claims = adopt_decision(bid, draft, selected, ["dw1"],
                                   answer_ids=["dw1", "dw2"])
    with pytest.raises(decision_auth.DecisionAuthError,
                       match="do not match the user's selected choices"):
        _verify(bid, draft, selected, ["dw1"], token, claims)


def test_decision_from_another_session_is_rejected(store, target):
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    draft = _load_draft(bid)
    selected = ["dw1"]
    token, _ = adopt_decision(bid, draft, selected, ["dw1"], session="other-session")
    claims = trusted_claims("journey_adopt", owner=TRUSTED_OWNER)
    with pytest.raises(decision_auth.DecisionAuthError,
                       match="belongs to another owner/session/turn"):
        _verify(bid, draft, selected, ["dw1"], token, claims)


def test_expired_decision_is_rejected(store, target):
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    draft = _load_draft(bid)
    selected = ["dw1"]
    stale = int(time.time()) - decision_auth.MAX_TTL_S - 60
    token, claims = adopt_decision(bid, draft, selected, ["dw1"], now=stale)
    with pytest.raises(decision_auth.DecisionAuthError,
                       match="not currently valid"):
        decision_auth.verify_adopt_context(
            token, bridge=claims, batch_id=bid, case_draft=draft,
            selected_draft_ids=selected, authorized_write_draft_ids=["dw1"],
            arguments=_adopt_arguments(bid, selected, ["dw1"]),
            consumed_jtis=())


# ── 注入 3：改用例后复用旧授权 ──────────────────────────────────────────────


def test_injection_editing_a_case_after_adoption_invalidates_the_token(store, target):
    """AC18 的机制锚：草稿内容一改，旧采纳令牌立刻对不上。"""
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    draft = _load_draft(bid)
    selected = ["dw1"]
    token, claims = adopt_decision(bid, draft, selected, ["dw1"])
    assert _verify(bid, draft, selected, ["dw1"], token, claims)["selected"] == ["dw1"]

    edited = copy.deepcopy(draft)
    edited["cases"][0]["expected"] = "改成了另一件事"
    with pytest.raises(decision_auth.DecisionAuthError,
                       match="adoption card id does not match"):
        _verify(bid, edited, selected, ["dw1"], token, claims)


def test_injection_editing_a_read_only_case_also_invalidates_the_token(store, target):
    """只绑写用例不够：非写用例改了，用户看到的采纳集也已经不是这一份。"""
    bid = _draft_batch(store, target, [api_case("dw1", writes=True),
                                       api_case("dr1")])
    draft = _load_draft(bid)
    selected = ["dw1", "dr1"]
    token, claims = adopt_decision(bid, draft, selected, ["dw1"])
    assert _verify(bid, draft, selected, ["dw1"], token, claims)["selected"] == ["dw1"]

    edited = copy.deepcopy(draft)
    edited["cases"][1]["expected"] = "只读用例也被改了"
    with pytest.raises(decision_auth.DecisionAuthError,
                       match="adoption card id does not match"):
        _verify(bid, edited, selected, ["dw1"], token, claims)


def test_shrinking_the_adopt_set_invalidates_the_token(store, target):
    """少采纳一条同样换了一份采纳集——不能拿上一份的授权顶。

    这里先挡下的是信封里的 `args_sha256`（`selected_draft_ids` 是工具参数），
    卡 id 这层是第二道。两道都要在：参数层管「这次调用」，卡 id 层管「用户
    当时看到的是哪一份采纳集」——只留前者的话，换一条传参路径就绕过去了。
    """
    bid = _draft_batch(store, target, [api_case("dw1", writes=True),
                                       api_case("dr1")])
    draft = _load_draft(bid)
    token, claims = adopt_decision(bid, draft, ["dw1", "dr1"], ["dw1"])
    with pytest.raises(decision_auth.DecisionAuthError,
                       match="arguments do not match this tool call"):
        _verify(bid, draft, ["dw1"], ["dw1"], token, claims)
    # 第二道：卡 id 本身也已经换了
    assert decision_auth.expected_adopt_question(bid, draft, ["dw1"])["id"] != \
        decision_auth.expected_adopt_question(bid, draft, ["dw1", "dr1"])["id"]


# ── 边界 ────────────────────────────────────────────────────────────────────


def test_unknown_draft_id_in_the_adopt_set_is_rejected(store, target):
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    draft = _load_draft(bid)
    with pytest.raises(decision_auth.DecisionAuthError, match="不在当前草稿中"):
        decision_auth.expected_adopt_question(bid, draft, ["dw1", "ghost"])


def test_empty_adopt_set_is_rejected(store, target):
    bid = _draft_batch(store, target, [api_case("dw1", writes=True)])
    draft = _load_draft(bid)
    with pytest.raises(decision_auth.DecisionAuthError, match="采纳集为空"):
        decision_auth.expected_adopt_question(bid, draft, [])


def test_draft_digest_primitive_is_shared_with_adopt(store, target):
    """采纳卡披露的摘要与 adopt 用来分配稳定身份的摘要必须是同一个原语。

    两边各算一套的话，卡上披露的内容与实际冻结的内容会悄悄分家——
    这正是 BB-502 那一类「挂载面与运行时面不一致」的形态。
    """
    from server.journey import adopt as adopt_mod
    case = api_case("dw1", writes=True)
    assert adopt_mod._draft_digest(case) == digest.draft_case_digest(case)
    assert decision_auth.adopt_draft_digest(case) == digest.draft_case_digest(case)
