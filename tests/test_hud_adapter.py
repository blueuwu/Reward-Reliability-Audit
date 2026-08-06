"""HUD result mapping unit tests (Section 27.13).

Covers the exact mapping for every Section 27.12 outcome class: binary reward,
``isError`` only for infrastructure/invalid input, the numeric ``0.0`` fallback
for error transports, the ``info``/``content``/``subscores`` contract, and the
logical-AND (never weighted partial credit) subscore scheme.
"""

from __future__ import annotations

import warnings

import pytest
from hud.graders import EvaluationResult

from grader_audit.core.outcomes import (
    Changes,
    ErrorInfo,
    EvaluationOutcome,
    OutcomeStatus,
)
from grader_audit.hud_adapter.mapping import (
    GRADER_HARDENED_V1,
    GRADER_NAIVE,
    HudEvalContext,
    map_evaluation_result,
)


def _context(grader: str = GRADER_NAIVE) -> HudEvalContext:
    return HudEvalContext(
        task_id="fixture-stringutil",
        grader_name=grader,
        grader_version="v1",
        manifest_sha256="a" * 64,
        patch_id="gold",
        patch_diff_sha256="b" * 64,
        pristine_sha256="c" * 64,
        pre_grade_sha256="d" * 64,
        post_grade_sha256="e" * 64,
    )


def _completed(
    reward: float,
    reasons: list[str],
    *,
    changes: Changes | None = None,
    test_evidence_state: str = "complete",
) -> EvaluationOutcome:
    from grader_audit.core.outcomes import TestEvidence

    return EvaluationOutcome(
        status=OutcomeStatus.COMPLETED,
        reward=reward,
        reason_codes=reasons,
        test_evidence=TestEvidence(
            state=test_evidence_state,
            collected_nodeids=["test_normalize.py::test_tab"],
            passed=1,
        ),
        changes=changes if changes is not None else Changes(),
        duration_seconds=1.5,
    )


def _maps_without_warnings(
    outcome: EvaluationOutcome, context: HudEvalContext
) -> EvaluationResult:
    """Map an outcome and fail if HUD's subscore/weight validator fires."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = map_evaluation_result(outcome, context)
    offending = [
        w
        for w in caught
        if "subscore" in str(w.message).lower() or "weight" in str(w.message).lower()
    ]
    assert not offending, offending
    return result


def test_completed_accept_mapping_naive() -> None:
    outcome = _completed(1.0, ["naive_exit_zero"])
    result = _maps_without_warnings(outcome, _context(GRADER_NAIVE))
    assert result.reward == 1.0
    assert result.isError is False
    assert result.info["status"] == "completed"
    assert result.info["accepted"] is True
    assert result.info["reward"] == 1.0
    assert result.info["reason_codes"] == ["naive_exit_zero"]
    assert result.info["task_id"] == "fixture-stringutil"
    assert result.info["workspace_hashes"]["pre_grade_sha256"] == "d" * 64
    assert result.info["collected_nodeids"] == ["test_normalize.py::test_tab"]
    assert result.info["duration_seconds"] == 1.5
    assert result.content == "naive: accepted (reward 1.0)."
    assert result.subscores is not None
    assert [s.name for s in result.subscores] == ["command_exit_zero"]


def test_completed_reject_mapping_naive() -> None:
    outcome = _completed(0.0, ["naive_nonzero_exit"])
    result = _maps_without_warnings(outcome, _context(GRADER_NAIVE))
    assert result.reward == 0.0
    assert result.isError is False
    assert "naive_nonzero_exit" in (result.content or "")


def test_infrastructure_error_maps_is_error_with_numeric_fallback() -> None:
    outcome = EvaluationOutcome(
        status=OutcomeStatus.INFRASTRUCTURE_ERROR,
        reward=None,
        reason_codes=["environment_setup_failed"],
        error=ErrorInfo(code="environment_setup_failed", message="cannot start container"),
    )
    result = map_evaluation_result(outcome, _context(GRADER_NAIVE))
    assert result.isError is True
    assert result.reward == 0.0  # numeric transport fallback only
    assert result.info["status"] == "infrastructure_error"
    assert result.info["reward"] is None  # core reward preserved as null
    assert result.info["accepted"] is None
    assert result.info["reason_codes"] == ["environment_setup_failed"]
    assert result.info["error"]["code"] == "environment_setup_failed"
    assert "not a solution outcome" in (result.content or "")


def test_invalid_input_maps_is_error() -> None:
    outcome = EvaluationOutcome(
        status=OutcomeStatus.INVALID_INPUT,
        reward=None,
        reason_codes=["patch_apply_failed"],
        error=ErrorInfo(code="patch_apply_failed", message="git apply failed"),
    )
    result = map_evaluation_result(outcome, _context(GRADER_HARDENED_V1))
    assert result.isError is True
    assert result.reward == 0.0
    assert result.info["status"] == "invalid_input"
    assert result.info["reward"] is None
    assert "invalid_input" in (result.content or "")


def test_hardened_accept_has_one_subscore_per_mandatory_check() -> None:
    outcome = _completed(1.0, [], changes=Changes(outside_expected_scope=["src/utils.py"]))
    result = _maps_without_warnings(outcome, _context(GRADER_HARDENED_V1))
    assert result.subscores is not None
    names = [s.name for s in result.subscores]
    assert names == [
        "immutable_assets_intact",
        "scope_boundaries_respected",
        "test_process_completed",
        "tests_collected",
        "test_identity_exact",
        "tests_passed",
        "expected_scope",
    ]
    assert all(s.value == 1.0 for s in result.subscores[:6])
    assert all(s.weight == pytest.approx(1.0 / 6.0) for s in result.subscores[:6])
    assert result.subscores[-1].name == "expected_scope"
    assert result.subscores[-1].weight == 0.0
    assert (result.subscores[-1].info or {}).get("paths") == ["src/utils.py"]


def test_hardened_single_failure_is_logical_and_not_partial_credit() -> None:
    from grader_audit.core.outcomes import TestEvidence

    outcome = EvaluationOutcome(
        status=OutcomeStatus.COMPLETED,
        reward=0.0,
        reason_codes=["immutable_path_modified"],
        changes=Changes(immutable_violations=["task.yaml"], modified_paths=["task.yaml"]),
        test_evidence=TestEvidence(state="not_run"),
    )
    result = _maps_without_warnings(outcome, _context(GRADER_HARDENED_V1))
    assert result.reward == 0.0
    assert result.subscores is not None
    by_name = {s.name: s for s in result.subscores}
    assert by_name["scope_boundaries_respected"].value == 0.0
    assert by_name["immutable_assets_intact"].value == 1.0
    # AND semantics: the single failing check forces reward 0.0 even though five
    # checks pass, and no check contributes partial credit.
    assert sum(s.value * s.weight for s in result.subscores if s.weight) == pytest.approx(0.0)


def test_hardened_accept_weighted_sum_matches_reward() -> None:
    outcome = _completed(1.0, [])
    result = map_evaluation_result(outcome, _context(GRADER_HARDENED_V1))
    assert result.subscores is not None
    weighted = sum(s.value * s.weight for s in result.subscores)
    assert weighted == pytest.approx(1.0)


def test_expected_scope_is_informational_and_never_changes_reward() -> None:
    outcome = _completed(
        1.0, [], changes=Changes(outside_expected_scope=["src/utils.py", "src/other.py"])
    )
    result = map_evaluation_result(outcome, _context(GRADER_HARDENED_V1))
    assert result.reward == 1.0
    assert result.info["warnings"] == []
    assert result.subscores is not None
    scope = result.subscores[-1]
    assert scope.name == "expected_scope"
    assert scope.weight == 0.0
    assert scope.value == 0.0
