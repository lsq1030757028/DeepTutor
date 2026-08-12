import base64
import hashlib
import hmac
import json
import os
import time

import pytest

import server.main as main
from server.journey import artifacts, bridge_auth, tools


SECRET = "owner-isolation-secret-value-32b!"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _token(owner: str, tool: str, args: dict, *, surface="capability", capability="test") -> str:
    now = int(time.time())
    payload = {
        "v": 1,
        "iss": "deeptutor",
        "aud": bridge_auth.BRIDGE_AUDIENCE,
        "owner_id": owner,
        "session_id": f"session-{owner}",
        "turn_id": f"turn-{owner}",
        "capability": capability,
        "surface": surface,
        "tool": tool,
        "args_sha256": bridge_auth.arguments_sha256(args),
        "iat": now,
        "exp": now + 60,
        "jti": f"jti-{owner}-{tool}",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(sig)}"


def _ingest_args(title: str) -> dict:
    return {
        "title": title,
        "base_url": "http://127.0.0.1:8047",
        "workspace_id": "",
        "story_id": "",
        "requirement_text": "新增韩语语音角色，支持男女音色",
        "source_kind": "doc",
        "source_ref": "local",
        "environment_ref": "",
        "tier": "standard",
        "tier_confirmed_via": "test",
        "requirement_entity": "",
        "requirement_entity_confirmed_via": "",
    }


def _persisted(root: str) -> list[str]:
    return sorted(
        os.path.relpath(os.path.join(base, name), root)
        for base, _dirs, files in os.walk(root)
        for name in files
    )


@pytest.fixture()
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
    return str(tmp_path)


def _create(owner: str, title: str) -> str:
    args = _ingest_args(title)
    result = main.journey_ingest(
        **args,
        owner="spoofed-owner",
        caller_surface="unknown",
        bridge_context=_token(owner, "journey_ingest", args),
    )
    assert result["ok"] is True, result
    return result["batch_id"]


def test_two_owners_can_only_list_and_get_their_own_batches(isolated):
    alice_batch = _create("alice", "A 的批次")
    bob_batch = _create("bob", "B 的批次")

    alice = main.journey_list_batches(
        owner="bob",
        caller_surface="unknown",
        bridge_context=_token("alice", "journey_list_batches", {}, surface="workbench"),
    )
    bob = main.journey_list_batches(
        owner="alice",
        caller_surface="unknown",
        bridge_context=_token("bob", "journey_list_batches", {}, surface="workbench"),
    )
    assert [row["batch_id"] for row in alice["batches"]] == [alice_batch]
    assert [row["batch_id"] for row in bob["batches"]] == [bob_batch]

    before = _persisted(isolated)
    guessed = main.journey_get_batch(
        alice_batch,
        owner="alice",
        caller_surface="capability",
        bridge_context=_token(
            "bob", "journey_get_batch", {"batch_id": alice_batch}, surface="workbench"
        ),
    )
    assert guessed["ok"] is False and guessed["code"] == "E_NO_BATCH"
    assert _persisted(isolated) == before


def test_unsigned_or_wrong_surface_ingest_has_zero_persistence(isolated):
    args = _ingest_args("不能落盘")
    before = _persisted(isolated)
    unsigned = main.journey_ingest(**args, bridge_context="")
    assert unsigned["ok"] is False and unsigned["code"] == "E_TRUST_CONTEXT_REQUIRED"
    chat = main.journey_ingest(
        **args,
        bridge_context=_token("alice", "journey_ingest", args, capability="chat"),
    )
    assert chat["ok"] is False and chat["code"] == "E_TRUST_CONTEXT_REQUIRED"
    assert _persisted(isolated) == before


def test_legacy_local_partition_is_not_visible_to_bridged_owner(isolated):
    legacy = artifacts.create_batch("legacy", owner=artifacts.DEFAULT_OWNER)
    listed = main.journey_list_batches(
        bridge_context=_token("alice", "journey_list_batches", {}, surface="workbench"),
    )
    assert legacy["batch_id"] not in {row["batch_id"] for row in listed["batches"]}


def test_public_issuer_is_not_mounted_and_direct_tools_need_owner():
    assert not hasattr(main, "journey_issue_gate_token")
    assert "issue_gate_token" not in tools.MCP_TOOL_NAMES
    assert tools.list_batches()["code"] == "E_OWNER_REQUIRED"


def test_trace_open_rejects_cross_batch_cross_owner_and_path_escape(isolated, monkeypatch):
    alice_batch = _create("alice", "A trace")
    bob_batch = _create("bob", "B trace")
    alice_run = artifacts.new_run_id()
    bob_run = artifacts.new_run_id()
    with artifacts.trusted_owner("alice"):
        alice_meta = artifacts.load_batch(alice_batch)
        alice_meta["run_ids"] = [alice_run]
        artifacts.save_batch(alice_meta)
        alice_rd = artifacts.run_dir(alice_run, create=True)
        trace_dir = os.path.join(alice_rd, "case-ui")
        os.makedirs(trace_dir)
        with open(os.path.join(trace_dir, "trace.zip"), "wb") as fh:
            fh.write(b"trace")
    with artifacts.trusted_owner("bob"):
        bob_meta = artifacts.load_batch(bob_batch)
        bob_meta["run_ids"] = [bob_run]
        artifacts.save_batch(bob_meta)

    opened = []
    from server.gateway import journey_console

    monkeypatch.setattr(
        journey_console,
        "open_trace",
        lambda rid, rel: (
            opened.append((rid, rel))
            or {
                "ok": True,
                "started": True,
                "command": "safe command",
                "trace_path": "must-not-leak",
                "spawn_error": "",
            }
        ),
    )

    def invoke(owner: str, batch_id: str, run_id: str, rel: str):
        args = {"batch_id": batch_id, "run_id": run_id, "trace_rel": rel}
        return main.journey_open_trace(
            **args,
            bridge_context=_token(owner, "journey_open_trace", args, surface="workbench"),
        )

    ok = invoke("alice", alice_batch, alice_run, "case-ui/trace.zip")
    assert ok["ok"] is True and "trace_path" not in ok and "command" not in ok
    assert opened == [(alice_run, "case-ui/trace.zip")]
    assert invoke("alice", alice_batch, bob_run, "case-ui/trace.zip")["code"] == "E_NO_TRACE"
    assert invoke("bob", alice_batch, alice_run, "case-ui/trace.zip")["code"] == "E_NO_BATCH"
    assert invoke("alice", alice_batch, alice_run, "../trace.zip")["code"] == "E_NO_TRACE"

    with artifacts.trusted_owner("alice"):
        verdict_path = os.path.join(alice_rd, "verdicts.jsonl")
        with open(verdict_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": "suite/ui", "verdict": "PASS"}) + "\n")
    detail_args = {"batch_id": alice_batch}
    detail = main.journey_get_batch(
        alice_batch,
        bridge_context=_token("alice", "journey_get_batch", detail_args, surface="workbench"),
    )
    handle = detail["runs"][0]["verdicts"][0]["trace_rel"]
    assert handle == "case-ui/trace.zip"
    assert not os.path.isabs(handle)
