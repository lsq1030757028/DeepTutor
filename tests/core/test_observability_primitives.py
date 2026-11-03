"""Write-side masking and terminal-state classification.

Both are used by every observation surface (stream events, the trace sidecar,
the diagnostic log), so a hole here is a hole in all three at once.
"""

from __future__ import annotations

import pytest

from deeptutor.core.observability import (
    TURN_STATUS_TO_STOP_KIND,
    classify_exception,
    redact_obj,
    redact_text,
    stop_completed,
    stop_interrupted,
)


class TestRedaction:
    @pytest.mark.parametrize(
        "raw, secret",
        [
            ("connect failed: token=abc123", "abc123"),
            ("headers: {'Authorization': 'Bearer eyJhbGciOi.J9payload.sig'}", "eyJhbGciOi"),
            ("api_key: sk-live-9f2ab7c41d3e88be rejected", "9f2ab7c41d3e88be"),
            ('config {"secret": "hunter2hunter2"}', "hunter2hunter2"),
            ("using ghp_AbCdEfGhIjKlMnOpQrSt1234", "AbCdEfGhIjKlMnOpQrSt1234"),
        ],
    )
    def test_a_recognised_credential_never_survives(self, raw: str, secret: str) -> None:
        masked = redact_text(raw)
        assert secret not in masked
        assert "****" in masked

    def test_a_short_secret_keeps_no_tail_at_all(self) -> None:
        # Four characters of a six-character secret is most of it.
        assert redact_text("token=abc123") == "token=****"

    def test_a_long_secret_keeps_a_four_char_tail_for_identification(self) -> None:
        masked = redact_text("token=abcdefghijklmnop9999")
        assert masked == "token=****9999"

    def test_ordinary_diagnostics_are_left_readable(self) -> None:
        text = "per-user MCP scope for u-42 did not connect in 3.0s; continuing without"
        assert redact_text(text) == text

    def test_masking_reaches_into_nested_structures(self) -> None:
        payload = {"servers": [{"name": "fastmcp", "error": "401 token=abc123"}]}
        masked = redact_obj(payload)
        assert "abc123" not in str(masked)
        assert masked["servers"][0]["name"] == "fastmcp"

    def test_truncation_happens_after_masking(self) -> None:
        masked = redact_text("token=abcdefghijklmnop9999 " + "x" * 100, max_chars=20)
        assert "abcdefghijklmnop9999" not in masked
        assert masked.endswith("…")


class TestStopReason:
    def test_the_three_turn_statuses_map_one_to_one(self) -> None:
        assert TURN_STATUS_TO_STOP_KIND == {
            "completed": "completed",
            "cancelled": "interrupted",
            "failed": "error",
        }

    def test_a_clean_completion_is_minimal(self) -> None:
        assert stop_completed() == {"kind": "completed", "code": "OK"}

    def test_a_cancellation_names_the_user(self) -> None:
        assert stop_interrupted() == {"kind": "interrupted", "code": "USER_CANCELLED"}

    @pytest.mark.parametrize(
        "message, expected",
        [
            ('no API key for provider route "deepseek-official"', "MISSING_CREDENTIAL"),
            ("Error code: 401 - Authentication Fails", "AUTH"),
            ("This model's maximum context length is 8192 tokens", "CONTEXT_OVERFLOW"),
            ("Request timed out after 60s", "PROVIDER_TIMEOUT"),
            ("Connection error: [Errno 111] refused", "PROVIDER_UNREACHABLE"),
            ("something nobody has seen before", "UNKNOWN"),
        ],
    )
    def test_provider_failures_are_classified(self, message: str, expected: str) -> None:
        stop = classify_exception(RuntimeError(message))
        assert stop["kind"] == "error"
        assert stop["code"] == expected

    def test_a_missing_key_is_not_read_as_an_auth_failure(self) -> None:
        # "no API key" matches the auth vocabulary too; the two are triaged
        # differently (configure vs rotate), so the order of the checks is
        # load-bearing.
        stop = classify_exception(RuntimeError("401: no api key configured"))
        assert stop["code"] == "MISSING_CREDENTIAL"

    def test_a_status_is_only_read_when_the_message_says_it_is_one(self) -> None:
        assert "status" not in classify_exception(RuntimeError("used 504 tokens of budget"))
        assert classify_exception(RuntimeError("Error code: 503 - upstream")).get("status") == 503

    def test_classification_never_raises(self) -> None:
        class _Hostile(Exception):
            def __str__(self) -> str:
                raise ValueError("nope")

        assert classify_exception(_Hostile())["kind"] == "error"
