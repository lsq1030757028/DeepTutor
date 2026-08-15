"""Static contract for the required self-hosted GitHub Actions gates."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROOT_WEB = ROOT / ".github" / "workflows" / "tests.yml"
TEST_PARTNER = ROOT / ".github" / "workflows" / "test-partner.yml"
CHECKOUT_SHA = "11d5960a326750d5838078e36cf38b85af677262"
UPLOAD_ARTIFACT_SHA = "ea165f8d65b6e75b540449e92b4886f43607fa02"
RUNNER = "runs-on: [self-hosted, linux, x64, deeptutor-ci]"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_both_required_workflows_use_safe_automatic_triggers_and_read_only_permissions():
    for path in (ROOT_WEB, TEST_PARTNER):
        text = _read(path)
        trigger = text.split("permissions:", maxsplit=1)[0]
        assert "pull_request:" in trigger
        assert "push:" in trigger
        assert "branches:\n      - main" in trigger
        assert "workflow_dispatch:" in trigger
        assert "pull_request_target:" not in trigger
        assert "permissions:\n  contents: read" in text
        assert RUNNER in text
        assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in text
        assert "secrets." not in text


def test_checkout_is_bound_to_the_event_full_sha_and_third_party_actions_are_pinned():
    for path in (ROOT_WEB, TEST_PARTNER):
        text = _read(path)
        assert "github.event.pull_request.head.sha" in text
        assert "github.sha" in text
        assert "^[0-9a-f]{40}$" in text
        assert 'test "$(git rev-parse HEAD)" = "$SOURCE_SHA"' in text
        assert f"actions/checkout@{CHECKOUT_SHA}" in text
        assert "actions/checkout@v" not in text
        assert f"actions/upload-artifact@{UPLOAD_ARTIFACT_SHA}" in text
        assert "actions/upload-artifact@v" not in text
        assert "persist-credentials: false" in text


def test_deeptutor_gate_keeps_the_historical_regression_surface():
    root_web = _read(ROOT_WEB)
    partner = _read(TEST_PARTNER)

    assert "python -m pytest -q tests deeptutor/learning/tests" in root_web
    for command in (
        "npm run test:node",
        "npm run i18n:parity",
        "npm run build",
        "npm run perf:check",
    ):
        assert command in root_web

    assert '-k "not test_ui_track_real_browser"' in partner
    assert "tests/test_journey_exec.py::test_ui_track_real_browser" in partner


def test_required_workflows_have_no_release_or_deployment_side_effects():
    text = (_read(ROOT_WEB) + "\n" + _read(TEST_PARTNER)).lower()
    forbidden = (
        "docker push",
        "docker login",
        "docker compose",
        "kubectl ",
        "helm ",
        "rsync ",
        "scp ",
    )
    for marker in forbidden:
        assert marker not in text
