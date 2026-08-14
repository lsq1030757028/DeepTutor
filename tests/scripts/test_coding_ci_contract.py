from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CREDENTIAL_ENV = "DEEPTUTOR_GITHUB_SSH_CREDENTIALS_ID"


def test_checkout_uses_validated_repository_credentials_from_job_env() -> None:
    content = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")

    assert "CREDENTIALS_ID =" not in content
    validation = f"requireRepositoryCredentialsId(env.{CREDENTIAL_ENV})"
    assert validation in content
    assert "if (!credentialsId ||" in content
    assert "REPLACE|PLACEHOLDER|CHANGE_ME|CHANGEME|TODO" in content
    assert "^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$" in content
    assert "credentialsId: credentialsId" in content
    assert "credentialsId: env." not in content
    assert f"params.{CREDENTIAL_ENV}" not in content
    assert content.count("checkout([") == 1
    assert content.index(validation) < content.index("checkout([")


def test_repository_credentials_id_is_never_printed() -> None:
    content = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    output_lines = [
        line
        for line in content.splitlines()
        if any(marker in line for marker in ("echo ", "print", "println"))
    ]
    assert all(CREDENTIAL_ENV not in line for line in output_lines)
    assert all("credentialsId" not in line for line in output_lines)


def test_github_host_key_verification_is_not_weakened_in_source() -> None:
    content = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")

    assert "StrictHostKeyChecking=no" not in content
    assert "ssh-keyscan" not in content
