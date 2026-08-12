# -*- coding: utf-8 -*-
"""Requirement-entity intake authority comes from a real one-time user receipt."""
from __future__ import annotations

import os

import pytest

import server.main as main
from server.journey import artifacts, bridge_auth, tools
from tests.journey_decision_helper import (
    SECRET,
    bridge_token,
    entity_decision_kwargs,
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
    }


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
    args = _args()
    wrong_answer = entity_decision_kwargs(args, "recommended_character", owner=OWNER)
    result = tools.ingest(owner=OWNER, **args, **wrong_answer)
    assert result["ok"] is False and result["code"] == "E_USER_DECISION_INVALID"
    assert not os.path.exists(isolated / OWNER)

    other_owner = entity_decision_kwargs(args, args["requirement_entity"], owner="other")
    result = tools.ingest(owner=OWNER, **args, **other_owner)
    assert result["ok"] is False and result["code"] == "E_USER_DECISION_REQUIRED"
    assert not os.path.exists(isolated / OWNER)

    changed_args = dict(args, title="另一个需求")
    receipt = entity_decision_kwargs(args, args["requirement_entity"], owner=OWNER)
    result = tools.ingest(owner=OWNER, **changed_args, **receipt)
    assert result["ok"] is False and result["code"] == "E_USER_DECISION_INVALID"
    assert not os.path.exists(isolated / OWNER)


def test_verified_entity_receipt_is_persisted_and_consumed_once():
    args = _args()
    receipt = entity_decision_kwargs(args, args["requirement_entity"], owner=OWNER)
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


def test_real_mcp_ingest_accepts_matching_bridge_and_entity_receipt():
    args = _args()
    receipt = entity_decision_kwargs(args, args["requirement_entity"], owner=OWNER)
    result = main.journey_ingest(
        **args,
        decision_context=receipt["decision_context"],
        bridge_context=bridge_token("journey_ingest", args, owner=OWNER),
    )
    assert result["ok"] is True
