# -*- coding: utf-8 -*-
"""Requirement-entity intake authority comes from a real one-time user receipt."""
from __future__ import annotations

import base64
import json
import os

import pytest

import server.main as main
from server.journey import artifacts, bridge_auth, tools
from tests.journey_decision_helper import (
    SECRET,
    bridge_token,
    entity_decision_kwargs,
    trusted_claims,
)


OWNER = "entity-test-owner"


def _args(entity: str = "custom_character") -> dict:
    return {
        "title": "新增自定义角色",
        "base_url": "http://127.0.0.1:8047",
        "workspace_id": "",
        "story_id": "",
        "requirement_text": "用户可以新增自定义角色。",
        "source_kind": "doc",
        "source_ref": "local",
        "environment_ref": "",
        "tier": "standard",
        "tier_confirmed_via": "chat_ask_user",
        "requirement_entity": entity,
        "requirement_entity_confirmed_via": "chat_ask_user",
        "prepare_requirement_entity": False,
        "intake_context": "",
    }


def _prepare(args: dict, *, owner: str = OWNER) -> tuple[dict, dict]:
    prepare_args = dict(
        args,
        requirement_entity="",
        requirement_entity_confirmed_via="",
        prepare_requirement_entity=True,
        intake_context="",
    )
    result = tools.ingest(
        owner=owner,
        _bridge_claims=trusted_claims("journey_ingest", owner=owner),
        **prepare_args,
    )
    assert result["ok"] is True and result["code"] == "NEEDS_GATE", result
    final_args = dict(
        args,
        prepare_requirement_entity=False,
        intake_context=result["intake_context"],
    )
    return result, final_args


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(bridge_auth.BRIDGE_SECRET_ENV, SECRET)
    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(artifacts, "BATCHES_ROOT", str(tmp_path / "batches"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", str(tmp_path / "runs"))
    from server.journey import ingest as ingest_mod

    monkeypatch.setattr(
        ingest_mod,
        "probe_target",
        lambda *_args, **_kwargs: {
            "reachable": True,
            "status": 200,
            "page_title": "",
            "body_head_sha256": "a" * 64,
        },
    )
    return tmp_path


def test_model_confirmation_label_cannot_create_entity_batch(isolated):
    args = _args()
    result = tools.ingest(owner=OWNER, **args)
    assert result["ok"] is False and result["code"] == "E_USER_DECISION_REQUIRED"
    assert not os.path.exists(isolated / OWNER)


def test_entity_receipt_binds_exact_answer_owner_and_arguments(isolated):
    prepared, args = _prepare(_args())
    encoded = prepared["intake_context"].split(".", 1)[0]
    snapshot = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert "arguments" not in snapshot
    assert args["requirement_text"] not in json.dumps(snapshot, ensure_ascii=False)
    assert snapshot["display"]["title"] == args["title"]
    assert prepared["requirement_digest"] in prepared["card"]["prompt"]
    wrong_answer = entity_decision_kwargs(
        args, "recommended_character", owner=OWNER,
        intake_context=prepared["intake_context"])
    result = tools.ingest(owner=OWNER, **args, **wrong_answer)
    assert result["ok"] is False and result["code"] == "E_USER_DECISION_INVALID"
    assert not os.path.exists(isolated / OWNER)

    other_owner = entity_decision_kwargs(
        args, args["requirement_entity"], owner="other",
        intake_context=prepared["intake_context"])
    result = tools.ingest(owner=OWNER, **args, **other_owner)
    assert result["ok"] is False and result["code"] == "E_USER_DECISION_REQUIRED"
    assert not os.path.exists(isolated / OWNER)

    changed_args = dict(args, title="另一个需求")
    # Production timing: the real card is still snapshot A, but the adapter
    # signs the model's post-answer call B.  The pre-answer intake token must
    # make that otherwise self-consistent B receipt fail closed.
    receipt = entity_decision_kwargs(
        changed_args, args["requirement_entity"], owner=OWNER,
        intake_context=prepared["intake_context"])
    result = tools.ingest(owner=OWNER, **changed_args, **receipt)
    assert result["ok"] is False and result["code"] == "E_USER_DECISION_INVALID"
    assert not os.path.exists(isolated / OWNER)


def test_verified_entity_receipt_is_persisted_and_consumed_once():
    prepared, args = _prepare(_args())
    receipt = entity_decision_kwargs(
        args, args["requirement_entity"], owner=OWNER,
        intake_context=prepared["intake_context"])
    first = tools.ingest(owner=OWNER, **args, **receipt)
    replay = tools.ingest(owner=OWNER, **args, **receipt)
    assert first["ok"] is True
    assert replay["ok"] is False and replay["code"] == "E_USER_DECISION_REPLAYED"
    profile = artifacts.load_artifact(
        first["batch_id"], "intake_profile", owner=OWNER)
    provenance = profile["requirement_entity_decision"]
    assert provenance["decision_hash"].startswith("sha256:")
    assert provenance["ask_user_tool_call_id"] == "ask-entity-test-1"
    assert len(artifacts.list_batches(owner=OWNER)) == 1


def test_ordinary_validation_failure_does_not_burn_entity_receipt(monkeypatch):
    prepared, args = _prepare(_args())
    receipt = entity_decision_kwargs(
        args, args["requirement_entity"], owner=OWNER,
        intake_context=prepared["intake_context"])
    from server.journey import ingest as ingest_mod

    monkeypatch.setattr(
        ingest_mod,
        "probe_target",
        lambda *_args, **_kwargs: {"reachable": False, "error": "temporarily down"},
    )
    failed = tools.ingest(owner=OWNER, **args, **receipt)
    assert failed["ok"] is False and failed["code"] == "E_INGEST_REJECTED"

    monkeypatch.setattr(
        ingest_mod,
        "probe_target",
        lambda *_args, **_kwargs: {
            "reachable": True, "status": 200, "page_title": "",
            "body_head_sha256": "a" * 64,
        },
    )
    retried = tools.ingest(owner=OWNER, **args, **receipt)
    assert retried["ok"] is True


def test_real_mcp_ingest_accepts_matching_bridge_and_entity_receipt():
    original = _args()
    prepare_args = dict(
        original,
        requirement_entity="",
        requirement_entity_confirmed_via="",
        prepare_requirement_entity=True,
        intake_context="",
    )
    prepared = main.journey_ingest(
        **prepare_args,
        bridge_context=bridge_token("journey_ingest", prepare_args, owner=OWNER),
    )
    assert prepared["ok"] is True and prepared["code"] == "NEEDS_GATE", prepared
    args = dict(
        original,
        prepare_requirement_entity=False,
        intake_context=prepared["intake_context"],
    )
    receipt = entity_decision_kwargs(
        args, args["requirement_entity"], owner=OWNER,
        intake_context=prepared["intake_context"])
    result = main.journey_ingest(
        **args,
        decision_context=receipt["decision_context"],
        bridge_context=bridge_token("journey_ingest", args, owner=OWNER),
    )
    assert result["ok"] is True


def test_real_production_signing_timing_rejects_ask_a_then_call_b():
    original = _args()
    prepare_args = dict(
        original,
        requirement_entity="",
        requirement_entity_confirmed_via="",
        prepare_requirement_entity=True,
        intake_context="",
    )
    prepared = main.journey_ingest(
        **prepare_args,
        bridge_context=bridge_token("journey_ingest", prepare_args, owner=OWNER),
    )
    changed = dict(
        original,
        title="被模型在用户回答后替换的另一份需求",
        prepare_requirement_entity=False,
        intake_context=prepared["intake_context"],
    )
    receipt = entity_decision_kwargs(
        changed, changed["requirement_entity"], owner=OWNER,
        intake_context=prepared["intake_context"])
    result = main.journey_ingest(
        **changed,
        decision_context=receipt["decision_context"],
        bridge_context=bridge_token("journey_ingest", changed, owner=OWNER),
    )
    assert result["ok"] is False and result["code"] == "E_USER_DECISION_INVALID"
