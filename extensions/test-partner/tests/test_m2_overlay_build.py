"""M2 前端覆盖镜像的构建配方回归。

这些断言守住两次真实逃逸的根因：Next standalone 复制错层导致路由 404，
以及覆盖文件归 root 所有导致 Next 缓存写入 EACCES。实际镜像构建与运行态探针
属于集成测试，本文件只验证配方与临时目录保护模块本身。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "Dockerfile.m2overlay"
BUILD_SCRIPT = REPO_ROOT / "extensions" / "test-partner" / "scripts" / "build_m2_overlay.ps1"


def test_overlay_copies_the_live_next_standalone_root() -> None:
    recipe = DOCKERFILE.read_text(encoding="utf-8")

    assert "web/.next/standalone/DeepTutor/web/ ./web/" in recipe
    assert "web/.next/standalone/ ./web/" not in recipe
    assert 'test -f /app/web/server.js' in recipe
    assert 'test -d "/app/web/.next/server/app/(workspace)/test-journey"' in recipe
    assert "test ! -d /app/web/DeepTutor/web" in recipe


def test_overlay_includes_the_test_capability_runtime() -> None:
    recipe = DOCKERFILE.read_text(encoding="utf-8")

    assert "deeptutor/ ./deeptutor/" in recipe
    assert "BUILTIN_CAPABILITY_CLASSES" in recipe
    assert "deeptutor.agents.test.capability:TestCapability" in recipe
    assert "TestCapability.manifest.name == 'test'" in recipe


def test_every_overlay_copy_uses_the_runtime_user() -> None:
    copy_lines = [
        line.strip()
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("COPY ")
    ]

    assert len(copy_lines) == 6
    assert all("--chown=deeptutor:deeptutor" in line for line in copy_lines)
    recipe = DOCKERFILE.read_text(encoding="utf-8")
    assert "chown -R deeptutor:deeptutor /app/extensions/test-partner/server" in recipe
    assert "/app/extensions/test-partner/skills" in recipe


def test_temporary_context_guard_rejects_broad_or_lookalike_paths() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable on this platform")

    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BUILD_SCRIPT),
            "-SelfTest",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "build_m2_overlay self-test PASS" in completed.stdout


def test_build_script_rebuilds_web_before_staging_by_default() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")
    build_at = script.index("& npm.cmd run build")
    stage_at = script.index(
        'Copy-Item -LiteralPath (Join-Path $repoRoot "web\\.next\\standalone")'
    )

    assert build_at < stage_at
    assert "[switch]$SkipWebBuild" in script
    assert "caller owns build freshness evidence" in script
    assert '$ErrorActionPreference = "Continue"' in script
    assert "$npmExitCode = $LASTEXITCODE" in script
    assert "& docker.exe build" in script
    assert "$dockerExitCode = $LASTEXITCODE" in script
