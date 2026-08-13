"""CODING source gate regression tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify_coding_source.py"
JENKINSFILE = Path(__file__).resolve().parents[3] / "Jenkinsfile"
RESOLVER_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "resolve_coding_source.py"
SPEC = importlib.util.spec_from_file_location("verify_coding_source", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
VERIFY_CODING_SOURCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY_CODING_SOURCE)
RESOLVER_SPEC = importlib.util.spec_from_file_location("resolve_coding_source", RESOLVER_SCRIPT)
assert RESOLVER_SPEC is not None and RESOLVER_SPEC.loader is not None
RESOLVE_CODING_SOURCE = importlib.util.module_from_spec(RESOLVER_SPEC)
RESOLVER_SPEC.loader.exec_module(RESOLVE_CODING_SOURCE)


SHA = "1" * 40


def test_native_push_resolves_branch_and_exact_commit():
    result = RESOLVE_CODING_SOURCE.resolve_source(
        trigger_method="PUSH", git_local_branch="feat/native-ci", git_commit=SHA
    )
    assert result.ref == "refs/heads/feat/native-ci"
    assert result.commit == SHA
    assert result.mode == "native-push"


@pytest.mark.parametrize(
    ("trigger_method", "branch", "tag"),
    [
        ("MR", "feat/native-ci", ""),
        ("CRON", "feat/native-ci", ""),
        ("PUSH", "feat/native-ci", "v1.0.0"),
        ("PUSH", "refs/tags/v1", ""),
        ("PUSH", "feat/../main", ""),
        ("PUSH", "feat//main", ""),
        ("PUSH", "feat/.hidden", ""),
        ("PUSH", "feat/old.lock/main", ""),
    ],
)
def test_non_branch_or_unsafe_native_event_is_rejected(trigger_method, branch, tag):
    with pytest.raises(ValueError):
        RESOLVE_CODING_SOURCE.resolve_source(
            trigger_method=trigger_method,
            git_local_branch=branch,
            git_commit=SHA,
            git_tag=tag,
        )


@pytest.mark.parametrize("trigger_method", ["", "MANUAL", "API"])
def test_manual_and_api_keep_explicit_fixed_sha_fallback(trigger_method):
    result = RESOLVE_CODING_SOURCE.resolve_source(
        trigger_method=trigger_method,
        github_ref="refs/heads/main",
        github_commit=SHA,
    )
    assert (result.ref, result.commit, result.mode) == (
        "refs/heads/main",
        SHA,
        "explicit-fallback",
    )


def test_unknown_trigger_method_fails_closed():
    with pytest.raises(ValueError):
        RESOLVE_CODING_SOURCE.resolve_source(
            trigger_method="TAG_PUSH",
            github_ref="refs/heads/main",
            github_commit=SHA,
        )


@pytest.mark.parametrize("commit", ["1" * 39, "A" * 40])
def test_native_push_requires_full_lowercase_sha(commit):
    with pytest.raises(ValueError):
        RESOLVE_CODING_SOURCE.resolve_source(
            trigger_method="PUSH",
            git_local_branch="feat/native-ci",
            git_commit=commit,
        )


def test_jenkins_uses_resolved_native_source_without_enabling_deploy():
    jenkinsfile = (Path(__file__).resolve().parents[3] / "Jenkinsfile").read_text(
        encoding="utf-8"
    )
    for marker in (
        "CCI_TRIGGER_METHOD",
        "GIT_LOCAL_BRANCH",
        "GIT_COMMIT",
        "GIT_TAG",
        "env.SOURCE_REF",
        "env.SOURCE_COMMIT",
        "env.SOURCE_MODE",
        "branches: [[name: env.SOURCE_COMMIT]]",
        'refspec: "+${env.SOURCE_REF}:refs/remotes/origin/reviewed"',
        '--expected-ref "$SOURCE_REF"',
        '--expected-commit "$SOURCE_COMMIT"',
        "scripts/resolve_coding_source.py",
    ):
        assert marker in jenkinsfile
    assert "stage('Deploy" not in jenkinsfile
    assert "deploy_coding_artifact" not in jenkinsfile


@pytest.mark.parametrize(
    "remote",
    [
        "https://github.com/lsq1030757028/DeepTutor.git",
        "https://github.com/lsq1030757028/DeepTutor.git/",
        "git@github.com:lsq1030757028/DeepTutor.git",
    ],
)
def test_canonical_github_remote_is_accepted(remote):
    assert VERIFY_CODING_SOURCE.repo_from_remote(remote) == "lsq1030757028/deeptutor"


def test_remote_normalization_does_not_require_python_39_string_methods(monkeypatch):
    class Python38Path(str):
        def removeprefix(self, _prefix):
            raise AssertionError("Python 3.9-only str.removeprefix must not be used")

        def removesuffix(self, _suffix):
            raise AssertionError("Python 3.9-only str.removesuffix must not be used")

    parsed = SimpleNamespace(
        scheme="https",
        hostname="github.com",
        path=Python38Path("/lsq1030757028/DeepTutor.git/"),
    )
    monkeypatch.setattr(VERIFY_CODING_SOURCE, "urlsplit", lambda _url: parsed)

    assert (
        VERIFY_CODING_SOURCE.repo_from_remote("https://github.com/placeholder")
        == "lsq1030757028/deeptutor"
    )


@pytest.mark.parametrize(
    "remote",
    [
        "git@evil.example:lsq1030757028/DeepTutor.git",
        "git@github.com.evil.example:lsq1030757028/DeepTutor.git",
    ],
)
def test_non_github_host_is_rejected(remote):
    with pytest.raises(SystemExit, match="origin must be github.com"):
        VERIFY_CODING_SOURCE.repo_from_remote(remote)


@pytest.mark.parametrize(
    "remote",
    [
        "file://github.com/lsq1030757028/DeepTutor.git",
        "http://github.com/lsq1030757028/DeepTutor.git",
        "git://github.com/lsq1030757028/DeepTutor.git",
        "ssh://git@github.com/lsq1030757028/DeepTutor.git",
        "ssh://git@evil.example/lsq1030757028/DeepTutor.git",
    ],
)
def test_non_https_url_scheme_is_rejected(remote):
    with pytest.raises(SystemExit, match="origin must use https://github.com"):
        VERIFY_CODING_SOURCE.repo_from_remote(remote)


def test_coding_pipeline_keeps_the_complete_no_deploy_uat_gate():
    pipeline = JENKINSFILE.read_text(encoding="utf-8")
    required = [
        "git merge-base --is-ancestor",
        "--junitxml=/workspace/ci-artifacts/test-partner.xml",
        "pytest -q tests deeptutor/learning/tests",
        "mcr.microsoft.com/playwright/python@sha256:3de745b23fc4b33f",
        "npm run test:node",
        "npm run i18n:parity",
        "npm run build",
        "npm run perf:check",
    ]
    for command in required:
        assert command in pipeline
    assert "no-deploy" in pipeline
    assert "python:3.11-slim" not in pipeline
    assert "deeptutor start" not in pipeline
    assert "--port 3785" not in pipeline
