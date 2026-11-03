import base64
import hashlib
import hmac
import json

import pytest

from server.journey import bridge_auth


SECRET = "s" * 32


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign(payload: dict, secret: str = SECRET) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(sig)}"


def claims(
    *,
    tool="journey_ingest",
    args=None,
    now=1_800_000_000,
    owner="alice",
    capability="test",
    surface="capability",
) -> dict:
    return {
        "v": 1,
        "iss": "deeptutor",
        "aud": bridge_auth.BRIDGE_AUDIENCE,
        "owner_id": owner,
        "session_id": "session-1",
        "turn_id": "turn-1",
        "capability": capability,
        "surface": surface,
        "tool": tool,
        "args_sha256": bridge_auth.arguments_sha256(args or {}),
        "iat": now,
        "exp": now + 60,
        "jti": "jti-1",
    }


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv(bridge_auth.BRIDGE_SECRET_ENV, SECRET)


def test_valid_two_part_assertion_binds_identity_tool_and_arguments():
    args = {"title": "批次", "tier": "standard"}
    verified = bridge_auth.verify_context(
        sign(claims(args=args)),
        tool="journey_ingest",
        effective_args=args,
        now=1_800_000_001,
    )
    assert verified.owner == "alice"
    assert verified.session == "session-1"
    assert verified.turn == "turn-1"
    assert verified.capability == "test"


def test_reserved_identity_arguments_are_excluded_from_digest():
    base = {"batch_id": "b-20260812-aaaaaa"}
    spoofed = {
        **base,
        "owner": "mallory",
        "caller_surface": "unknown",
        "bridge_context": "fake",
        "gate_token": "old",
    }
    assert bridge_auth.arguments_sha256(base) == bridge_auth.arguments_sha256(spoofed)


@pytest.mark.parametrize("token", ["", "fake", "a.b.c", "."])
def test_missing_or_malformed_assertion_fails_closed(token):
    with pytest.raises(bridge_auth.BridgeAuthError):
        bridge_auth.verify_context(
            token, tool="journey_ingest", effective_args={}, now=1_800_000_001
        )


def test_missing_or_short_secret_fails_closed(monkeypatch):
    token = sign(claims())
    monkeypatch.setenv(bridge_auth.BRIDGE_SECRET_ENV, "short")
    with pytest.raises(bridge_auth.BridgeAuthError, match="at least 32"):
        bridge_auth.verify_context(
            token, tool="journey_ingest", effective_args={}, now=1_800_000_001
        )


@pytest.mark.parametrize(
    "change",
    [
        {"tool": "journey_execute"},
        {"aud": "somewhere-else"},
        {"owner_id": "_local"},
        {"capability": "chat"},
    ],
)
def test_wrong_identity_or_policy_claim_fails_closed(change):
    payload = claims()
    payload.update(change)
    with pytest.raises(bridge_auth.BridgeAuthError):
        bridge_auth.verify_context(
            sign(payload), tool="journey_ingest", effective_args={}, now=1_800_000_001
        )


def test_wrong_arguments_and_expired_assertion_fail_closed():
    token = sign(claims(args={"batch_id": "one"}))
    with pytest.raises(bridge_auth.BridgeAuthError, match="arguments"):
        bridge_auth.verify_context(
            token, tool="journey_ingest", effective_args={"batch_id": "two"}, now=1_800_000_001
        )
    expired = sign(claims(now=1_799_999_900))
    with pytest.raises(bridge_auth.BridgeAuthError, match="expired"):
        bridge_auth.verify_context(
            expired, tool="journey_ingest", effective_args={}, now=1_800_000_001
        )


def test_workbench_surface_is_read_only():
    read = claims(tool="journey_get_batch", args={"batch_id": "x"}, surface="workbench")
    assert (
        bridge_auth.verify_context(
            sign(read),
            tool="journey_get_batch",
            effective_args={"batch_id": "x"},
            now=1_800_000_001,
        ).surface
        == "workbench"
    )
    mutation = claims(tool="journey_execute", surface="workbench")
    with pytest.raises(bridge_auth.BridgeAuthError, match="read-only"):
        bridge_auth.verify_context(
            sign(mutation), tool="journey_execute", effective_args={}, now=1_800_000_001
        )


def test_trace_open_is_workbench_only():
    args = {"batch_id": "b", "run_id": "r", "trace_rel": "c/trace.zip"}
    workbench = claims(tool="journey_open_trace", args=args, surface="workbench")
    assert (
        bridge_auth.verify_context(
            sign(workbench),
            tool="journey_open_trace",
            effective_args=args,
            now=1_800_000_001,
        ).surface
        == "workbench"
    )
    capability = claims(tool="journey_open_trace", args=args)
    with pytest.raises(bridge_auth.BridgeAuthError, match="workbench"):
        bridge_auth.verify_context(
            sign(capability),
            tool="journey_open_trace",
            effective_args=args,
            now=1_800_000_001,
        )
