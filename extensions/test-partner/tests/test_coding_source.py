"""CODING source gate regression tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify_coding_source.py"
JENKINSFILE = Path(__file__).resolve().parents[3] / "Jenkinsfile"
SPEC = importlib.util.spec_from_file_location("verify_coding_source", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
VERIFY_CODING_SOURCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY_CODING_SOURCE)


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
        "npm run test:node",
        "npm run i18n:parity",
        "npm run build",
        "npm run perf:check",
    ]
    for command in required:
        assert command in pipeline
    assert "no-deploy" in pipeline
    assert "deeptutor start" not in pipeline
    assert "--port 3785" not in pipeline
