from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from server.gateway.config import GatewayConfig

SECRET_ENV = "TEST_JOURNEY_BRIDGE_SECRET"
SECRET_A = "test-only-bridge-a-" + "a" * 32
SECRET_B = "test-only-bridge-b-" + "b" * 32


def _runtime():
    return importlib.import_module("server.journey.bridge_runtime")


def _config(tmp_path: Path, secret: str | None) -> GatewayConfig:
    config = GatewayConfig(str(tmp_path / "config"))
    os.makedirs(config.config_dir, exist_ok=True)
    text = "" if secret is None else f"{SECRET_ENV}={secret}\n"
    Path(config.secrets_path).write_text(text, encoding="utf-8")
    return config


@pytest.mark.parametrize("secret", [None, "short"])
def test_host_startup_rejects_missing_or_short_secret_without_leaking_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, secret: str | None
) -> None:
    runtime = _runtime()
    config = _config(tmp_path, secret)
    monkeypatch.delenv(SECRET_ENV, raising=False)

    with pytest.raises(runtime.BridgeRuntimeConfigError) as caught:
        runtime.prepare_test_partner_bridge_secret(config=config)

    assert SECRET_ENV not in os.environ
    assert not secret or secret not in str(caught.value)


def test_host_startup_injects_secret_but_reports_presence_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime()
    config = _config(tmp_path, SECRET_A)
    monkeypatch.delenv(SECRET_ENV, raising=False)

    report = runtime.prepare_test_partner_bridge_secret(config=config)

    assert os.environ[SECRET_ENV] == SECRET_A
    assert report == {"key": SECRET_ENV, "test_partner_present": True}
    assert SECRET_A not in repr(report)


def test_host_startup_rejects_inherited_mismatch_without_leaking_either_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime()
    config = _config(tmp_path, SECRET_A)
    monkeypatch.setenv(SECRET_ENV, SECRET_B)

    with pytest.raises(runtime.BridgeRuntimeConfigError) as caught:
        runtime.prepare_test_partner_bridge_secret(config=config)

    assert SECRET_A not in str(caught.value)
    assert SECRET_B not in str(caught.value)


def test_deployment_preflight_requires_same_named_same_value_secret(
    tmp_path: Path,
) -> None:
    runtime = _runtime()
    config = _config(tmp_path, SECRET_A)
    deeptutor_env = tmp_path / "deeptutor.env"
    deeptutor_env.write_text(f'{SECRET_ENV}="{SECRET_A}"\n', encoding="utf-8")

    report = runtime.verify_deployment_bridge_secret(
        deeptutor_env_path=str(deeptutor_env), config=config
    )

    assert report == {
        "key": SECRET_ENV,
        "deeptutor_present": True,
        "test_partner_present": True,
        "same_value": True,
    }
    assert SECRET_A not in repr(report)


@pytest.mark.parametrize("deeptutor_value", [None, "short", SECRET_B])
def test_deployment_preflight_fails_closed_without_value_disclosure(
    tmp_path: Path, deeptutor_value: str | None
) -> None:
    runtime = _runtime()
    config = _config(tmp_path, SECRET_A)
    deeptutor_env = tmp_path / "deeptutor.env"
    text = "" if deeptutor_value is None else f"{SECRET_ENV}={deeptutor_value}\n"
    deeptutor_env.write_text(text, encoding="utf-8")

    with pytest.raises(runtime.BridgeRuntimeConfigError) as caught:
        runtime.verify_deployment_bridge_secret(
            deeptutor_env_path=str(deeptutor_env), config=config
        )

    message = str(caught.value)
    assert SECRET_A not in message
    assert not deeptutor_value or deeptutor_value not in message


def test_server_main_checks_bridge_secret_before_opening_any_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_module = importlib.import_module("server.main")
    calls: list[str] = []

    def fail_preflight() -> None:
        calls.append("preflight")
        raise RuntimeError("bridge configuration unavailable")

    monkeypatch.setattr(
        main_module, "prepare_test_partner_bridge_secret", fail_preflight, raising=False
    )
    monkeypatch.setattr(main_module, "start_gateway", lambda: calls.append("gateway"))
    monkeypatch.setattr(main_module.mcp, "run", lambda **_: calls.append("mcp"))

    with pytest.raises(RuntimeError, match="bridge configuration unavailable"):
        main_module.main()

    assert calls == ["preflight"]


def test_windows_host_entrypoint_runs_cross_process_preflight_first() -> None:
    root = Path(__file__).resolve().parents[1]
    content = (root / "scripts" / "start_server.cmd").read_text(encoding="ascii")
    preflight = '-m server.journey.bridge_runtime --deeptutor-env "%DEEPTUTOR_ENV_FILE%"'

    assert preflight in content
    assert content.index(preflight) < content.index("-m server.main")
    assert f"echo %{SECRET_ENV}%" not in content


def test_host_secret_template_declares_blank_key_without_a_value() -> None:
    root = Path(__file__).resolve().parents[1]
    lines = (root / "config" / "secrets.env.example").read_text(encoding="utf-8").splitlines()

    matches = [line for line in lines if line.startswith(f"{SECRET_ENV}=")]

    assert matches == [f"{SECRET_ENV}="]
