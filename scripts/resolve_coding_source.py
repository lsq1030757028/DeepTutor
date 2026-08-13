#!/usr/bin/env python3
"""Resolve a CODING trigger to one safe GitHub branch ref and exact commit."""

from __future__ import annotations

import argparse
import re
from typing import NamedTuple


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REF_RE = re.compile(r"^refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*$")
FALLBACK_METHODS = {"", "MANUAL", "API"}


class ResolvedSource(NamedTuple):
    ref: str
    commit: str
    mode: str


def _safe_branch_ref(ref: str) -> bool:
    components = ref[len("refs/heads/") :].split("/")
    return bool(
        REF_RE.fullmatch(ref)
        and ".." not in ref
        and "//" not in ref
        and "@{" not in ref
        and not ref.endswith(("/", ".", ".lock"))
        and all(not item.startswith(".") and not item.endswith(".lock") for item in components)
    )


def resolve_source(
    *,
    trigger_method: str = "",
    git_local_branch: str = "",
    git_commit: str = "",
    git_tag: str = "",
    github_ref: str = "",
    github_commit: str = "",
) -> ResolvedSource:
    """Fail closed unless the trigger binds one safe branch to one full SHA."""

    method = trigger_method.strip().upper()
    if method == "PUSH":
        if git_tag.strip():
            raise ValueError("tag push is not an accepted source trigger")
        branch = git_local_branch.strip()
        if branch.startswith("refs/"):
            raise ValueError("native push branch must be unqualified")
        ref = "refs/heads/" + branch
        commit = git_commit.strip()
        mode = "native-push"
    elif method in FALLBACK_METHODS:
        ref = github_ref.strip()
        commit = github_commit.strip()
        mode = "explicit-fallback"
    else:
        raise ValueError("unsupported CODING trigger method")

    if not _safe_branch_ref(ref):
        raise ValueError("source ref must be a safe refs/heads branch ref")
    if not COMMIT_RE.fullmatch(commit):
        raise ValueError("source commit must be a full lowercase 40-character SHA")
    return ResolvedSource(ref=ref, commit=commit, mode=mode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trigger-method", default="")
    parser.add_argument("--git-local-branch", default="")
    parser.add_argument("--git-commit", default="")
    parser.add_argument("--git-tag", default="")
    parser.add_argument("--github-ref", default="")
    parser.add_argument("--github-commit", default="")
    parser.add_argument("--field", choices=("ref", "commit", "mode"), required=True)
    args = parser.parse_args()
    try:
        result = resolve_source(
            trigger_method=args.trigger_method,
            git_local_branch=args.git_local_branch,
            git_commit=args.git_commit,
            git_tag=args.git_tag,
            github_ref=args.github_ref,
            github_commit=args.github_commit,
        )
    except ValueError as error:
        parser.error(str(error))
    print(getattr(result, args.field))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
