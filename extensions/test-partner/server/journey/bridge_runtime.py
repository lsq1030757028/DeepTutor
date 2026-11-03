"""Fail-closed host injection and deployment preflight for the Journey bridge secret.

The Test Partner host process reads its copy from the gitignored gateway secret
store.  Deployment preflight compares that value with DeepTutor's gitignored
Compose environment file, but returns and prints key state only: never values,
lengths, masks, or digests.
"""

from __future__ import annotations

import argparse
import hmac
import os
from pathlib import Path
import sys
from typing import Sequence

from server.gateway.config import GatewayConfig, default_config
from server.journey.bridge_auth import BRIDGE_SECRET_ENV, MIN_SECRET_BYTES


class BridgeRuntimeConfigError(RuntimeError):
    """The host cannot start with one trustworthy shared bridge secret."""


def _validated_secret(value: str | None, *, source: str) -> str:
    secret = str(value or "")
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise BridgeRuntimeConfigError(f"{BRIDGE_SECRET_ENV} is missing or invalid in {source}")
    return secret


def _deeptutor_env_secret(path: str) -> str:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BridgeRuntimeConfigError(
            f"{BRIDGE_SECRET_ENV} is missing or invalid in DeepTutor environment"
        ) from exc

    matches: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        if key.strip() != BRIDGE_SECRET_ENV:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        matches.append(value)
    if len(matches) != 1:
        raise BridgeRuntimeConfigError(
            f"{BRIDGE_SECRET_ENV} is missing or ambiguous in DeepTutor environment"
        )
    return _validated_secret(matches[0], source="DeepTutor environment")


def _test_partner_secret(config: GatewayConfig) -> str:
    return _validated_secret(
        config.get_secret(BRIDGE_SECRET_ENV), source="Test Partner secret store"
    )


def prepare_test_partner_bridge_secret(*, config: GatewayConfig | None = None) -> dict[str, object]:
    """Inject the validated host secret before either listener starts.

    An inherited value may not silently override the gitignored secret store.
    When both are present they must match exactly; the store remains the source
    injected into this process.
    """
    actual_config = config or default_config()
    stored = _test_partner_secret(actual_config)
    inherited = os.environ.get(BRIDGE_SECRET_ENV)
    if inherited is not None:
        inherited = _validated_secret(inherited, source="Test Partner process environment")
        if not hmac.compare_digest(inherited, stored):
            raise BridgeRuntimeConfigError(
                f"{BRIDGE_SECRET_ENV} sources do not match for Test Partner"
            )
    os.environ[BRIDGE_SECRET_ENV] = stored
    return {"key": BRIDGE_SECRET_ENV, "test_partner_present": True}


def verify_deployment_bridge_secret(
    *, deeptutor_env_path: str, config: GatewayConfig | None = None
) -> dict[str, object]:
    """Verify both deployment inputs use the same valid value without exposing it."""
    actual_config = config or default_config()
    test_partner = _test_partner_secret(actual_config)
    deeptutor = _deeptutor_env_secret(deeptutor_env_path)
    if not hmac.compare_digest(deeptutor, test_partner):
        raise BridgeRuntimeConfigError(
            f"{BRIDGE_SECRET_ENV} does not match between DeepTutor and Test Partner"
        )
    return {
        "key": BRIDGE_SECRET_ENV,
        "deeptutor_present": True,
        "test_partner_present": True,
        "same_value": True,
    }


def cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify Journey bridge secret key state without printing values."
    )
    parser.add_argument("--deeptutor-env", required=True)
    args = parser.parse_args(argv)
    try:
        verify_deployment_bridge_secret(deeptutor_env_path=args.deeptutor_env)
    except BridgeRuntimeConfigError as exc:
        print(f"[ERROR] Journey bridge preflight failed: {exc}", file=sys.stderr)
        return 2
    print(
        "[test-partner] Journey bridge preflight passed: "
        "key present in both stores and values match"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
