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
    reset_at = recipe.index("RUN rm -rf /app/web")
    copy_at = recipe.index("COPY --chown=deeptutor:deeptutor web/.next/standalone")
    assert reset_at < copy_at
    assert "mkdir -p /app/web" in recipe
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
    stage_at = script.index("Copy-DirectoryTree -Source $standaloneRoot")

    assert build_at < stage_at
    assert "[switch]$SkipWebBuild" in script
    assert "caller owns build freshness evidence" in script
    assert '$ErrorActionPreference = "Continue"' in script
    assert "$npmExitCode = $LASTEXITCODE" in script
    assert "& docker.exe build" in script
    assert "$dockerExitCode = $LASTEXITCODE" in script


def test_build_script_discovers_and_normalizes_the_clean_worktree_standalone_root() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert 'web\\.next\\required-server-files.json' in script
    assert "$requiredServerFiles.appDir" in script
    assert "manifest does not belong to this web root" in script
    assert "$requiredServerFiles.relativeAppDir" in script
    assert "Next 16.3 emits the app directly at standalone/" in script
    assert "$standaloneRoot = $standaloneBase" in script
    assert "[IO.Path]::IsPathRooted($relativeAppDir)" in script
    assert "$standaloneRoot.StartsWith" in script
    assert 'web\\.next\\standalone\\DeepTutor' in script
    assert 'Join-Path $normalizedStandaloneParent "web"' in script
    assert "Get-Command robocopy.exe" in script
    assert "$copyExitCode -gt 7" in script
