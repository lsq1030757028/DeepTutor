"""The Test capability publishes and enforces an explicit empty config contract."""

import pytest

from deeptutor.runtime.request_contracts import (
    get_capability_request_schema,
    validate_capability_config,
)


def test_test_capability_schema_forbids_unknown_keys() -> None:
    schema = get_capability_request_schema("test")
    assert schema["additionalProperties"] is False
    assert validate_capability_config("test", {}) == {}
    with pytest.raises(ValueError, match="Invalid test config"):
        validate_capability_config("test", {"owner": "spoof"})
