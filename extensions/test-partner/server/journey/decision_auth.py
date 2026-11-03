# -*- coding: utf-8 -*-
"""Verify one-time user decisions for Journey business gates.

The bridge proves which authenticated Test turn made a tool call.  This module
separately proves that the same turn really paused on an interactive ``ask_user``
card and received the exact selection now being recorded.  Model-authored
``confirmed_via`` strings are audit labels only; they carry no authority.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from collections.abc import Collection
from typing import Any

from server.journey import digest as _digest
from server.journey import pw_runtime
from server.journey.bridge_auth import (
    BRIDGE_SECRET_ENV, MIN_SECRET_BYTES, BridgeClaims, arguments_sha256,
)

AUDIENCE = "test-partner-user-decision"
INTAKE_AUDIENCE = "test-partner-intake-snapshot"
MAX_TTL_S = 300
MAX_CLOCK_SKEW_S = 5
MAX_WRITE_CHOICES = 8
_INTAKE_MUTABLE_FIELDS = frozenset({
    "intake_context",
    "prepare_requirement_entity",
    "requirement_entity",
    "requirement_entity_confirmed_via",
})


class DecisionAuthError(ValueError):
    pass


def _secret() -> bytes:
    raw = os.environ.get(BRIDGE_SECRET_ENV, "").encode("utf-8")
    if len(raw) < MIN_SECRET_BYTES:
        raise DecisionAuthError("Journey decision signing key is not configured")
    return raw


def _decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise DecisionAuthError("user decision receipt is malformed")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (TypeError, ValueError) as exc:
        raise DecisionAuthError("user decision receipt is malformed") from exc


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def intake_base_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in arguments.items()
        if str(key) not in _INTAKE_MUTABLE_FIELDS
    }


def _requirement_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _display(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def expected_requirement_entity_question(intake: dict[str, Any]) -> dict[str, Any]:
    display = dict(intake.get("display") or {})
    args_digest = str(intake.get("args_sha256") or "")
    requirement_digest = str(intake.get("requirement_digest") or "")
    prompt = (
        "请确认此冻结接入对象实际要新增、修改或删除的业务实体 / Confirm the business "
        "entity this frozen intake creates, updates, or deletes.\n"
        f"标题 / Title: {_display(display.get('title'), 120)}\n"
        f"目标 / Target: {_display(display.get('target'), 180)}\n"
        f"需求摘要 / Requirement digest: {requirement_digest}\n"
        f"接入摘要 / Intake digest: {args_digest}"
    )
    return {
        "id": f"journey_requirement_entity:{args_digest.removeprefix('sha256:')[:24]}",
        "prompt": prompt,
        "header": "业务实体 / Entity",
        "multi_select": False,
        "allow_free_text": True,
        "options": [],
        "placeholder": "例如 custom_character / e.g. custom_character",
    }


def issue_intake_context(
    *,
    bridge: BridgeClaims,
    arguments: dict[str, Any],
    requirement_text: str,
    now: int | None = None,
) -> dict[str, Any]:
    if bridge.tool != "journey_ingest" or bridge.capability != "test" \
            or bridge.surface != "capability":
        raise DecisionAuthError("intake snapshot requires a trusted Test capability call")
    issued = int(time.time()) if now is None else int(now)
    frozen = intake_base_arguments(arguments)
    payload = {
        "v": 1,
        "iss": "test-partner",
        "aud": INTAKE_AUDIENCE,
        "owner_id": bridge.owner,
        "session_id": bridge.session,
        "turn_id": bridge.turn,
        "tool": "journey_ingest",
        "args_sha256": arguments_sha256(frozen),
        "requirement_digest": _requirement_digest(requirement_text),
        "display": {
            "title": _display(frozen.get("title"), 120),
            "target": _display(frozen.get("base_url"), 180),
        },
        "iat": issued,
        "exp": issued + MAX_TTL_S,
        "jti": str(bridge.jti or ""),
    }
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    token = f"{_encode(raw)}.{_encode(hmac.new(_secret(), raw, hashlib.sha256).digest())}"
    return {
        "intake_context": token,
        "intake_digest": payload["args_sha256"],
        "requirement_digest": payload["requirement_digest"],
        "card": expected_requirement_entity_question(payload),
    }


def verify_intake_context(
    token: Any,
    *,
    bridge: BridgeClaims,
    arguments: dict[str, Any],
    requirement_text: str,
    now: int | None = None,
) -> dict[str, Any]:
    if not isinstance(token, str) or not token.strip():
        raise DecisionAuthError("a frozen intake snapshot is required")
    parts = token.strip().split(".")
    if len(parts) != 2:
        raise DecisionAuthError("intake snapshot is malformed")
    raw = _decode(parts[0])
    supplied = _decode(parts[1])
    if not hmac.compare_digest(supplied, hmac.new(_secret(), raw, hashlib.sha256).digest()):
        raise DecisionAuthError("intake snapshot signature does not match")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionAuthError("intake snapshot payload is malformed") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise DecisionAuthError("unsupported intake snapshot version")
    if payload.get("iss") != "test-partner" or payload.get("aud") != INTAKE_AUDIENCE:
        raise DecisionAuthError("intake snapshot issuer or audience does not match")
    identity = (
        str(payload.get("owner_id") or ""),
        str(payload.get("session_id") or ""),
        str(payload.get("turn_id") or ""),
    )
    if identity != (bridge.owner, bridge.session, bridge.turn):
        raise DecisionAuthError("intake snapshot belongs to another owner/session/turn")
    if bridge.tool != "journey_ingest" or payload.get("tool") != "journey_ingest":
        raise DecisionAuthError("intake snapshot tool does not match")
    current = int(time.time()) if now is None else int(now)
    try:
        issued, expires = int(payload["iat"]), int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DecisionAuthError("intake snapshot timestamps are invalid") from exc
    if issued > current + MAX_CLOCK_SKEW_S or expires < current \
            or expires <= issued or expires - issued > MAX_TTL_S:
        raise DecisionAuthError("intake snapshot is expired or has an invalid lifetime")
    frozen = intake_base_arguments(arguments)
    if payload.get("args_sha256") != arguments_sha256(frozen):
        raise DecisionAuthError("tool arguments do not match the frozen intake snapshot")
    if payload.get("requirement_digest") != _requirement_digest(requirement_text):
        raise DecisionAuthError("requirement content changed after the intake snapshot")
    return payload


def _effective_write(case: dict[str, Any]) -> bool:
    recipe = ((case.get("automation") or {}).get("recipe") or {})
    return pw_runtime.effective_write_risk({
        "writes": bool((case.get("side_effects") or {}).get("writes")),
        "actions": list(recipe.get("actions") or []),
    })


def expected_question(batch_id: str, caseset: dict[str, Any]) -> dict[str, Any]:
    write_cases = [c for c in caseset.get("cases") or [] if _effective_write(c)]
    if len(write_cases) > MAX_WRITE_CHOICES:
        raise DecisionAuthError(
            f"write confirmation card supports at most {MAX_WRITE_CHOICES} cases")
    def description(case: dict[str, Any]) -> str:
        value = f"{case.get('source_case_digest', '')} | {case.get('title', '')}"
        # Match ask_user's model-facing normalizer.  Long business titles must
        # fail neither open nor closed merely because the card truncates them.
        return value if len(value) <= 200 else value[:200].rstrip() + "…"

    return {
        "id": f"journey_write_confirm:{batch_id}:{caseset.get('caseset_id', '')}",
        "multi_select": True,
        "allow_free_text": False,
        "options": [
            {
                "label": str(case.get("case_id") or ""),
                "description": description(case),
            }
            for case in write_cases
        ],
    }


def _verify_envelope(
    token: Any,
    *,
    bridge: BridgeClaims,
    tool: str,
    arguments: dict[str, Any],
    now: int | None = None,
) -> dict[str, Any]:
    if not isinstance(token, str) or not token.strip():
        raise DecisionAuthError("a verified interactive user decision is required")
    parts = token.strip().split(".")
    if len(parts) != 2:
        raise DecisionAuthError("user decision receipt is malformed")
    raw = _decode(parts[0])
    supplied = _decode(parts[1])
    if not hmac.compare_digest(supplied, hmac.new(_secret(), raw, hashlib.sha256).digest()):
        raise DecisionAuthError("user decision signature does not match")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionAuthError("user decision payload is malformed") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise DecisionAuthError("unsupported user decision version")
    if payload.get("iss") != "deeptutor" or payload.get("aud") != AUDIENCE:
        raise DecisionAuthError("user decision issuer or audience does not match")
    if payload.get("tool") != tool or bridge.tool != tool:
        raise DecisionAuthError("user decision tool does not match")
    if payload.get("args_sha256") != arguments_sha256(arguments):
        raise DecisionAuthError("user decision arguments do not match this tool call")
    identity = (
        str(payload.get("owner_id") or ""),
        str(payload.get("session_id") or ""),
        str(payload.get("turn_id") or ""),
    )
    if identity != (bridge.owner, bridge.session, bridge.turn):
        raise DecisionAuthError("user decision belongs to another owner/session/turn")
    if payload.get("capability") != "test" or payload.get("surface") != "capability":
        raise DecisionAuthError("user decision did not originate in Test capability")
    jti = str(payload.get("jti") or "").strip()
    tool_call_id = str(payload.get("ask_user_tool_call_id") or "").strip()
    if not jti or not tool_call_id:
        raise DecisionAuthError("user decision identity is incomplete")
    current = int(time.time()) if now is None else int(now)
    try:
        issued, expires = int(payload["iat"]), int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DecisionAuthError("user decision timestamps are invalid") from exc
    if issued > current + MAX_CLOCK_SKEW_S or expires < current:
        raise DecisionAuthError("user decision is not currently valid")
    if expires <= issued or expires - issued > MAX_TTL_S:
        raise DecisionAuthError("user decision lifetime is invalid")
    try:
        resolved_at = int(payload["resolved_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DecisionAuthError("user decision resolution time is invalid") from exc
    if resolved_at > issued + MAX_CLOCK_SKEW_S or issued - resolved_at > MAX_TTL_S:
        raise DecisionAuthError("user decision is stale or was signed before it resolved")
    payload["_verified_jti"] = jti
    payload["_verified_tool_call_id"] = tool_call_id
    return payload


def verify_decision_context(
    token: Any,
    *,
    bridge: BridgeClaims,
    batch_id: str,
    caseset: dict[str, Any],
    requested_case_ids: list[str],
    arguments: dict[str, Any],
    now: int | None = None,
) -> dict[str, Any]:
    payload = _verify_envelope(
        token, bridge=bridge, tool="journey_write_confirm",
        arguments=arguments, now=now)

    ask_user = payload.get("ask_user")
    questions = ask_user.get("questions") if isinstance(ask_user, dict) else None
    if not isinstance(questions, list) or len(questions) != 1 or not isinstance(questions[0], dict):
        raise DecisionAuthError("write decision must come from one structured question")
    actual = questions[0]
    expected = expected_question(batch_id, caseset)
    for field in ("id", "multi_select", "allow_free_text", "options"):
        if actual.get(field) != expected[field]:
            raise DecisionAuthError(f"write decision card {field} does not match current caseset")
    answers = payload.get("answers")
    if not isinstance(answers, list) or len(answers) != 1 or not isinstance(answers[0], dict):
        raise DecisionAuthError("write decision answer is missing")
    if str(answers[0].get("questionId") or "") != expected["id"]:
        raise DecisionAuthError("write decision answer belongs to another question")
    selected = [part.strip() for part in str(answers[0].get("text") or "").split(",")
                if part.strip()]
    allowed = {option["label"] for option in expected["options"]}
    if len(selected) != len(set(selected)) or any(case_id not in allowed for case_id in selected):
        raise DecisionAuthError("write decision contains an invalid or duplicate choice")
    if set(selected) != set(requested_case_ids):
        raise DecisionAuthError("tool case_ids do not match the user's selected choices")
    return {
        "jti": payload["_verified_jti"],
        "ask_user_tool_call_id": payload["_verified_tool_call_id"],
        "selected": selected,
        "question_id": expected["id"],
        "owner": bridge.owner,
        "session": bridge.session,
        "turn": bridge.turn,
    }


def verify_requirement_entity_context(
    token: Any,
    *,
    bridge: BridgeClaims,
    intake: dict[str, Any],
    requirement_entity: str,
    arguments: dict[str, Any],
    now: int | None = None,
) -> dict[str, Any]:
    entity = str(requirement_entity or "").strip()
    if not entity or len(entity) > 120:
        raise DecisionAuthError("requirement entity is missing or too long")
    payload = _verify_envelope(
        token, bridge=bridge, tool="journey_ingest",
        arguments=arguments, now=now)
    ask_user = payload.get("ask_user")
    questions = ask_user.get("questions") if isinstance(ask_user, dict) else None
    if not isinstance(questions, list) or len(questions) != 1 \
            or not isinstance(questions[0], dict):
        raise DecisionAuthError(
            "requirement entity must come from one structured question")
    actual = questions[0]
    expected = expected_requirement_entity_question(intake)
    for field in (
        "id", "prompt", "header", "multi_select", "allow_free_text", "options",
        "placeholder",
    ):
        if actual.get(field) != expected[field]:
            raise DecisionAuthError(
                f"requirement entity card {field} does not match the trusted contract")
    answers = payload.get("answers")
    if not isinstance(answers, list) or len(answers) != 1 \
            or not isinstance(answers[0], dict):
        raise DecisionAuthError("requirement entity answer is missing")
    if str(answers[0].get("questionId") or "") != expected["id"]:
        raise DecisionAuthError("requirement entity answer belongs to another question")
    selected = str(answers[0].get("text") or "").strip()
    if selected != entity:
        raise DecisionAuthError(
            "tool requirement_entity does not match the user's exact answer")
    return {
        "jti": payload["_verified_jti"],
        "ask_user_tool_call_id": payload["_verified_tool_call_id"],
        "selected": selected,
        "question_id": expected["id"],
        "owner": bridge.owner,
        "session": bridge.session,
        "turn": bridge.turn,
    }


# ── 采纳卡（ADR-2：采纳是唯一硬闸，授权强度不低于它替代的写确认）──────────────


def adopt_draft_digest(case: dict[str, Any]) -> str:
    """草稿用例的业务内容摘要（排除草稿身份字段）。

    与 ``adopt._draft_digest`` 同源：采纳时 ``case_id`` / ``source_case_digest``
    才分配，所以采纳卡只能绑草稿态的内容摘要。内容不变则摘要不变，
    内容一改摘要就变——这是「改用例后旧采纳授权自动失效」的锚。
    """
    return _digest.draft_case_digest(case)


def adopt_set_digest(cases: list[dict[str, Any]]) -> str:
    """整个采纳集（含只读用例）的内容摘要，按 draft_id 排序后取。

    只绑写用例不够：非写用例改了同样会让「用户看到的采纳集」与实际冻结的不是
    同一份。卡 id 绑这个摘要，任一条草稿内容变动都会让旧令牌对不上。
    """
    rows = sorted(
        (str(case.get("draft_id") or ""), adopt_draft_digest(case))
        for case in cases
    )
    return _digest.sha256_digest([{"draft_id": d, "digest": g} for d, g in rows])


def adopt_selection(case_draft: dict[str, Any],
                    selected_draft_ids: list[str]) -> list[dict[str, Any]]:
    """按 draft_id 取出被采纳的草稿，保持调用方给的顺序、去重。"""
    by_id = {str(case.get("draft_id") or ""): case
             for case in case_draft.get("cases") or []}
    picked: list[dict[str, Any]] = []
    for draft_id in dict.fromkeys(str(d or "") for d in selected_draft_ids or []):
        case = by_id.get(draft_id)
        if case is None:
            # 报"不在"必须同时给"有什么"——否则调用方只能穷举乱猜
            # (2026-08-28 U6 复验:模型先猜 d1 再猜 case_id,两发全脱靶)。
            available = sorted(x for x in by_id if x)
            raise DecisionAuthError(
                f"采纳集里的 draft_id 不在当前草稿中：{draft_id}。"
                f"当前草稿可选的 draft_id：{available or '（草稿里没有任何 draft_id）'}")
        picked.append(case)
    if not picked:
        raise DecisionAuthError("采纳集为空，没有可确认的内容")
    return picked


def expected_adopt_question(batch_id: str, case_draft: dict[str, Any],
                            selected_draft_ids: list[str]) -> dict[str, Any]:
    """采纳卡的可信契约：id 绑整个采纳集，选项绑逐条写用例的内容摘要。

    与 ``expected_question``（写确认卡）结构同形，故沿用同一套信封校验：
    模型没法自己编一张卡出来——它编的任何一个字段都会与这里重算的不一致。
    """
    selection = adopt_selection(case_draft, selected_draft_ids)
    write_cases = [case for case in selection if _effective_write(case)]
    if len(write_cases) > MAX_WRITE_CHOICES:
        raise DecisionAuthError(
            f"一张采纳卡最多能披露 {MAX_WRITE_CHOICES} 条会写数据的用例，这次超了。")

    def description(case: dict[str, Any]) -> str:
        """卡面上这一行**只放人读得懂的内容**（2026-08-29 Round 2 第 13 条）。

        原来这里是 `sha256:<64 hex> | 标题`，一条 64 位十六进制串占满整行，
        用户在授权卡上逐条读的时候，先读到的是他完全用不上的东西。

        **拿掉它不削弱绑定**：卡 id 是 `adopt_set_digest`（覆盖采纳集里每一条
        草稿的内容摘要）的前 24 位十六进制，任何一条用例内容变了 id 就对不上；
        `verify_adopt_context` 又会用**当前**草稿重算 `write_digests` 落账。
        逐条摘要出现在描述里，对这两道绑定是冗余的。

        摘要仍在信封内，只是搬到了模型读得到、用户读不到的地方
        （`tools.adopt` 的 `disclosure`）。
        """
        title = str(case.get("title") or case.get("draft_id") or "").strip()
        return title if len(title) <= 200 else title[:200].rstrip() + "…"

    set_digest = adopt_set_digest(selection)
    return {
        "id": f"journey_adopt:{batch_id}:{set_digest.removeprefix('sha256:')[:24]}",
        "multi_select": True,
        # 采纳卡保留「其他 — 自定义回复」一行（2026-09-05 R3 真会话实测）：宿主现在
        # 按本卡原样校回 ask_user 的问题，禁了自由文本之后，用户在采纳卡上就没有
        # 任何办法说「先不采纳」——全是只读用例时卡上连一个选项都没有，交空答复
        # 模型又会把卡再发一遍，死循环。放开自由文本不削弱闸：`verify_adopt_context`
        # 只认卡上选项 label 组成的答复，任何自由文本（比如「先不采纳」）都验不过，
        # 采纳一律被拒——这正是用户要的效果。
        "allow_free_text": True,
        "options": [
            {
                "label": str(case.get("draft_id") or ""),
                "description": description(case),
            }
            for case in write_cases
        ],
    }


def verify_adopt_context(
    token: Any,
    *,
    bridge: BridgeClaims,
    batch_id: str,
    case_draft: dict[str, Any],
    selected_draft_ids: list[str],
    authorized_write_draft_ids: list[str],
    arguments: dict[str, Any],
    consumed_jtis: Collection[str],
    now: int | None = None,
) -> dict[str, Any]:
    """校验采纳决定。返回值带 ``write_digests``，供派生写授权事件落账。

    ``consumed_jtis`` **是必填的**：调用方要把账本里已消费过的决定 jti 全给进来。

    做成必填而不是可选，是因为第一版把防重放留在了调用方——`tools.write_confirm`
    那段查账本的代码得由采纳工具自己照抄一遍。「照抄一遍」这种约定不是闸：
    忘了抄，校验照样返回成功，重放照样通过，而且没有任何东西会变红。
    独立复核实测确认过这一点（同一令牌原文连验两次都被接受）。
    参数必填以后，忘了传是 TypeError，在接线那一刻就炸。
    """
    payload = _verify_envelope(
        token, bridge=bridge, tool="journey_adopt",
        arguments=arguments, now=now)
    # 顺序有意：先验签再查重放。反过来的话，一份签名都不对的垃圾令牌会因为
    # jti 撞车而报「已消费过」，把「伪造」说成「重放」——成对错误态混说。
    if str(payload["_verified_jti"]) in {str(j or "") for j in consumed_jtis or ()}:
        raise DecisionAuthError("这份用户决定已经消费过，不能重复授权。")

    ask_user = payload.get("ask_user")
    questions = ask_user.get("questions") if isinstance(ask_user, dict) else None
    if not isinstance(questions, list) or len(questions) != 1             or not isinstance(questions[0], dict):
        raise DecisionAuthError("adoption decision must come from one structured question")
    actual = questions[0]
    expected = expected_adopt_question(batch_id, case_draft, selected_draft_ids)
    for field in ("id", "multi_select", "allow_free_text", "options"):
        if actual.get(field) != expected[field]:
            raise DecisionAuthError(
                f"adoption card {field} does not match the current draft set")
    answers = payload.get("answers")
    if not isinstance(answers, list) or len(answers) != 1 or not isinstance(answers[0], dict):
        raise DecisionAuthError("adoption decision answer is missing")
    if str(answers[0].get("questionId") or "") != expected["id"]:
        raise DecisionAuthError("adoption decision answer belongs to another question")
    selected = [part.strip() for part in str(answers[0].get("text") or "").split(",")
                if part.strip()]
    allowed = {option["label"] for option in expected["options"]}
    if len(selected) != len(set(selected)) or any(d not in allowed for d in selected):
        raise DecisionAuthError("adoption decision contains an invalid or duplicate choice")
    requested = list(dict.fromkeys(str(d or "") for d in authorized_write_draft_ids or []))
    if set(selected) != set(requested):
        raise DecisionAuthError(
            "tool authorized_write_draft_ids do not match the user's selected choices")
    by_id = {str(case.get("draft_id") or ""): case
             for case in adopt_selection(case_draft, selected_draft_ids)}
    return {
        "jti": payload["_verified_jti"],
        "ask_user_tool_call_id": payload["_verified_tool_call_id"],
        "selected": selected,
        "declined": sorted(allowed - set(selected)),
        "write_digests": {d: adopt_draft_digest(by_id[d]) for d in selected},
        "adopt_set_digest": adopt_set_digest(adopt_selection(
            case_draft, selected_draft_ids)),
        "question_id": expected["id"],
        "owner": bridge.owner,
        "session": bridge.session,
        "turn": bridge.turn,
    }


__all__ = [
    "DecisionAuthError", "adopt_draft_digest", "adopt_selection", "adopt_set_digest",
    "expected_adopt_question", "expected_question",
    "expected_requirement_entity_question",
    "intake_base_arguments", "issue_intake_context", "verify_adopt_context",
    "verify_decision_context", "verify_intake_context",
    "verify_requirement_entity_context",
]
