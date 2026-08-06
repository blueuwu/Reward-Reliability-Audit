"""Stable grader reason codes (Sections 8.2 and 27.12).

Reason codes are API. They are defined in one enum, serialized as their string
values, and tested against accidental changes. When several codes apply they are
sorted by the Section 27.12 evaluation order, not alphabetically.
"""

from __future__ import annotations

from enum import StrEnum


class ReasonCode(StrEnum):
    """Every stable reason code produced by the graders and orchestrator."""

    INTERNAL_GRADER_ERROR = "internal_grader_error"
    PATCH_APPLY_FAILED = "patch_apply_failed"
    ENVIRONMENT_SETUP_FAILED = "environment_setup_failed"
    GRADER_ASSET_HASH_MISMATCH = "grader_asset_hash_mismatch"
    IMMUTABLE_PATH_MODIFIED = "immutable_path_modified"
    OUTSIDE_EDITABLE_SCOPE = "outside_editable_scope"
    TIMEOUT = "timeout"
    TEST_EVIDENCE_MISSING = "test_evidence_missing"
    NO_TESTS_COLLECTED = "no_tests_collected"
    TEST_IDENTITY_MISMATCH = "test_identity_mismatch"
    AUTHORITATIVE_TESTS_FAILED = "authoritative_tests_failed"
    NAIVE_EXIT_ZERO = "naive_exit_zero"
    NAIVE_NONZERO_EXIT = "naive_nonzero_exit"
    TEST_COUNT_BELOW_MINIMUM = "test_count_below_minimum"
    UNSTABLE_RESULT = "unstable_result"


# Section 27.12 evaluation order. Naive-only codes are appended because they
# never co-occur with hardened codes; the ordering is stable and tested.
_REASON_ORDER: tuple[ReasonCode, ...] = (
    ReasonCode.PATCH_APPLY_FAILED,
    ReasonCode.ENVIRONMENT_SETUP_FAILED,
    ReasonCode.GRADER_ASSET_HASH_MISMATCH,
    ReasonCode.IMMUTABLE_PATH_MODIFIED,
    ReasonCode.OUTSIDE_EDITABLE_SCOPE,
    ReasonCode.TIMEOUT,
    ReasonCode.TEST_EVIDENCE_MISSING,
    ReasonCode.NO_TESTS_COLLECTED,
    ReasonCode.TEST_IDENTITY_MISMATCH,
    ReasonCode.AUTHORITATIVE_TESTS_FAILED,
    ReasonCode.INTERNAL_GRADER_ERROR,
    ReasonCode.NAIVE_EXIT_ZERO,
    ReasonCode.NAIVE_NONZERO_EXIT,
    ReasonCode.TEST_COUNT_BELOW_MINIMUM,
    ReasonCode.UNSTABLE_RESULT,
)

_ORDER_INDEX: dict[ReasonCode, int] = {code: index for index, code in enumerate(_REASON_ORDER)}


def sort_reason_codes(reasons: list[ReasonCode]) -> list[ReasonCode]:
    """Return *reasons* de-duplicated and sorted by Section 27.12 order."""
    return sorted(set(reasons), key=lambda code: _ORDER_INDEX[code])


def serialize_reason_codes(reasons: list[ReasonCode]) -> list[str]:
    """Serialize reason codes as sorted, stable string values."""
    return [code.value for code in sort_reason_codes(reasons)]
