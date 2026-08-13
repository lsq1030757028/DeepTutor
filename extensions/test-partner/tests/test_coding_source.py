"""CODING source gate regression tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify_coding_source.py"
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
