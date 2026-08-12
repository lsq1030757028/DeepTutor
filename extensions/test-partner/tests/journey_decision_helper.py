"""Test-only issuer for verified interactive Journey decisions."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from server.journey import artifacts, decision_auth
from server.journey import bridge_auth
from server.journey.bridge_auth import BridgeClaims

SECRET = "journey-decision-test-secret-at-least-32-bytes"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decision_kwargs(batch_id: str, case_ids: list[str], *,
                    owner: str = "unit-test-owner",
                    session: str = "session-test", turn: str = "turn-test",
                    decided_by: str = "", confirmed_via: str = "ask_user_card",
                    now: int | None = None) -> dict:
    issued = int(time.time()) if now is None else int(now)
    caseset = artifacts.load_artifact(batch_id, "approved_caseset")
    question = decision_auth.expected_question(batch_id, caseset)
    payload = {
        "v": 1, "iss": "deeptutor", "aud": decision_auth.AUDIENCE,
        "owner_id": owner, "session_id": session, "turn_id": turn,
        "capability": "test", "surface": "capability",
        "tool": "journey_write_confirm",
        "args_sha256": bridge_auth.arguments_sha256({
            "batch_id": batch_id, "case_ids": case_ids,
            "decided_by": decided_by, "confirmed_via": confirmed_via,
        }),
        "ask_user_tool_call_id": "ask-test-1",
        "ask_user": {"intro": None, "questions": [question]},
        "answers": [{"questionId": question["id"], "text": ",".join(case_ids)}],
        "resolved_at": issued, "iat": issued, "exp": issued + 300,
        "jti": secrets.token_urlsafe(16),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    token = f"{_b64(raw)}.{_b64(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest())}"
    claims = BridgeClaims(owner=owner, session=session, turn=turn,
                          capability="test", surface="capability",
                          tool="journey_write_confirm", jti="bridge-test",
                          expires_at=issued + 60)
    return {"decision_context": token, "_bridge_claims": claims}


def entity_decision_kwargs(arguments: dict, requirement_entity: str, *,
                           owner: str = "unit-test-owner",
                           session: str = "session-test", turn: str = "turn-test",
                           prompt: str = "需求真正要新增、修改或删除的业务实体是什么？",
                           now: int | None = None) -> dict:
    issued = int(time.time()) if now is None else int(now)
    question = {
        **decision_auth.expected_requirement_entity_question(),
        "prompt": prompt,
    }
    payload = {
        "v": 1, "iss": "deeptutor", "aud": decision_auth.AUDIENCE,
        "owner_id": owner, "session_id": session, "turn_id": turn,
        "capability": "test", "surface": "capability",
        "tool": "journey_ingest",
        "args_sha256": bridge_auth.arguments_sha256(arguments),
        "ask_user_tool_call_id": "ask-entity-test-1",
        "ask_user": {"intro": None, "questions": [question]},
        "answers": [{"questionId": question["id"], "text": requirement_entity}],
        "resolved_at": issued, "iat": issued, "exp": issued + 300,
        "jti": secrets.token_urlsafe(16),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    token = f"{_b64(raw)}.{_b64(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest())}"
    claims = BridgeClaims(owner=owner, session=session, turn=turn,
                          capability="test", surface="capability",
                          tool="journey_ingest", jti="bridge-entity-test",
                          expires_at=issued + 60)
    return {"decision_context": token, "_bridge_claims": claims}


def bridge_token(tool: str, arguments: dict, *, owner: str = "unit-test-owner",
                 session: str = "session-test", turn: str = "turn-test") -> str:
    issued = int(time.time())
    payload = {
        "v": 1, "iss": "deeptutor", "aud": bridge_auth.BRIDGE_AUDIENCE,
        "owner_id": owner, "session_id": session, "turn_id": turn,
        "capability": "test", "surface": "capability", "tool": tool,
        "args_sha256": bridge_auth.arguments_sha256(arguments),
        "iat": issued, "exp": issued + 60, "jti": secrets.token_urlsafe(12),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return f"{_b64(raw)}.{_b64(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest())}"
