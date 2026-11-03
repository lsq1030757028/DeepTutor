#!/usr/bin/env python3
"""Verify CODING checked out the reviewed GitHub SHA of lsq1030757028/DeepTutor."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

CANONICAL = "lsq1030757028/deeptutor"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REF_RE = re.compile(r"^refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*$")


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def repo_from_remote(url: str) -> str:
    raw = url.strip()
    if raw.startswith("git@"):
        authority, separator, path = raw.partition(":")
        _, _, hostname = authority.partition("@")
        if not separator or hostname.lower() != "github.com":
            raise SystemExit("origin must be github.com")
    else:
        parsed = urlsplit(raw)
        if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != "github.com":
            raise SystemExit("origin must use https://github.com")
        path = parsed.path
    if path.startswith("/"):
        path = path[1:]
    if path.endswith("/"):
        path = path[:-1]
    if path.endswith(".git"):
        path = path[:-4]
    path = path.lower()
    if path != CANONICAL:
        raise SystemExit(f"origin is not {CANONICAL}")
    return path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default=".")
    p.add_argument("--expected-ref", required=True)
    p.add_argument("--expected-commit", required=True)
    args = p.parse_args()
    repo = Path(args.repo).resolve()
    if not REF_RE.match(args.expected_ref):
        raise SystemExit("GITHUB_REF must be refs/heads/...")
    commit = args.expected_commit.lower()
    if not COMMIT_RE.match(commit):
        raise SystemExit("GITHUB_COMMIT must be a 40-char lowercase SHA")
    repo_from_remote(git(repo, "remote", "get-url", "origin"))
    head = git(repo, "rev-parse", "HEAD").lower()
    if head != commit:
        raise SystemExit(f"HEAD {head} != expected {commit}")
    print(f"ok origin={CANONICAL} ref={args.expected_ref} commit={commit}")


if __name__ == "__main__":
    main()
