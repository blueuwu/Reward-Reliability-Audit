"""Stable reason-code ordering and serialization (Sections 27.12, 27.19)."""

from __future__ import annotations

from grader_audit.core.reason_codes import (
    ReasonCode,
    serialize_reason_codes,
    sort_reason_codes,
)

# Serialized string values are API and must not change accidentally.
EXPECTED_SERIALIZED = {
    ReasonCode.INTERNAL_GRADER_ERROR: "internal_grader_error",
    ReasonCode.PATCH_APPLY_FAILED: "patch_apply_failed",
    ReasonCode.ENVIRONMENT_SETUP_FAILED: "environment_setup_failed",
    ReasonCode.GRADER_ASSET_HASH_MISMATCH: "grader_asset_hash_mismatch",
    ReasonCode.IMMUTABLE_PATH_MODIFIED: "immutable_path_modified",
    ReasonCode.OUTSIDE_EDITABLE_SCOPE: "outside_editable_scope",
    ReasonCode.TIMEOUT: "timeout",
    ReasonCode.TEST_EVIDENCE_MISSING: "test_evidence_missing",
    ReasonCode.NO_TESTS_COLLECTED: "no_tests_collected",
    ReasonCode.TEST_IDENTITY_MISMATCH: "test_identity_mismatch",
    ReasonCode.AUTHORITATIVE_TESTS_FAILED: "authoritative_tests_failed",
    ReasonCode.NAIVE_EXIT_ZERO: "naive_exit_zero",
    ReasonCode.NAIVE_NONZERO_EXIT: "naive_nonzero_exit",
    ReasonCode.TEST_COUNT_BELOW_MINIMUM: "test_count_below_minimum",
    ReasonCode.UNSTABLE_RESULT: "unstable_result",
}


def test_reason_code_strings_are_stable() -> None:
    for code, expected in EXPECTED_SERIALIZED.items():
        assert code.value == expected


def test_serialization_is_stable() -> None:
    codes = [
        ReasonCode.AUTHORITATIVE_TESTS_FAILED,
        ReasonCode.TEST_IDENTITY_MISMATCH,
        ReasonCode.AUTHORITATIVE_TESTS_FAILED,
    ]
    assert serialize_reason_codes(codes) == ["test_identity_mismatch", "authoritative_tests_failed"]


def test_sort_follows_evaluation_order_not_alphabetical() -> None:
    unsorted = [
        ReasonCode.AUTHORITATIVE_TESTS_FAILED,
        ReasonCode.IMMUTABLE_PATH_MODIFIED,
        ReasonCode.TEST_IDENTITY_MISMATCH,
    ]
    sorted_codes = sort_reason_codes(unsorted)
    assert sorted_codes == [
        ReasonCode.IMMUTABLE_PATH_MODIFIED,
        ReasonCode.TEST_IDENTITY_MISMATCH,
        ReasonCode.AUTHORITATIVE_TESTS_FAILED,
    ]
    # Alphabetically "authoritative_tests_failed" would sort first; the
    # evaluation order places it last.
    assert sorted_codes[0] is ReasonCode.IMMUTABLE_PATH_MODIFIED


def test_deduplication() -> None:
    assert sort_reason_codes([ReasonCode.TIMEOUT, ReasonCode.TIMEOUT]) == [ReasonCode.TIMEOUT]
