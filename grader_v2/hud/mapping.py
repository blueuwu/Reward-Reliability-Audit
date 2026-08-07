"""HUD result mapping for hardened_v2 (D-054).

The protected ``grader_audit.hud_adapter.mapping`` supports only
``naive``/``hardened_v1`` mandatory-check lists. This module replicates that
mapping for ``hardened_v2`` (same ``EvaluationResult`` shape, same isError /
reward / subscores semantics) and adds the semantic-suite check to the
mandatory list. The protected mapping and grading core are imported verbatim;
only the check list and the ``info`` block are extended.
"""

from __future__ import annotations

from hud.graders import EvaluationResult, SubScore

from grader_audit.core.outcomes import EvaluationOutcome, OutcomeStatus
from grader_audit.hud_adapter.mapping import (
    CheckOutcome,
    HudEvalContext,
    _concise_content,  # pyright: ignore[reportPrivateUsage]
)
from grader_v2.grading.evidence import SemanticEvidence

_V1_CHECK_NAMES = (
    "immutable_assets_intact",
    "scope_boundaries_respected",
    "test_process_completed",
    "tests_collected",
    "test_identity_exact",
    "tests_passed",
)

_SEMANTIC_CHECK_NAMES = ("semantic_tests_passed",)


def _v2_checks(outcome: EvaluationOutcome) -> list[CheckOutcome]:
    """v1 mandatory checks plus the semantic check (logical AND)."""
    if outcome.status is not OutcomeStatus.COMPLETED:
        return [
            CheckOutcome(
                name=name,
                passed=False,
                detail=(
                    f"not evaluated: "
                    f"{outcome.error.code if outcome.error is not None else 'not_evaluated'}"
                ),
            )
            for name in _V1_CHECK_NAMES + _SEMANTIC_CHECK_NAMES
        ]
    reasons = set(outcome.reason_codes)
    from grader_v2.grading.reason_codes import (
        SEMANTIC_COLLECTION_MISMATCH,
        SEMANTIC_EVIDENCE_MISSING,
        SEMANTIC_SUITE_TIMEOUT,
        SEMANTIC_TESTS_FAILED,
    )

    semantic_failed = reasons.intersection(
        {
            SEMANTIC_TESTS_FAILED,
            SEMANTIC_COLLECTION_MISMATCH,
            SEMANTIC_EVIDENCE_MISSING,
            SEMANTIC_SUITE_TIMEOUT,
        }
    )
    return [
        CheckOutcome(
            name="immutable_assets_intact",
            passed="grader_asset_hash_mismatch" not in reasons,
            detail="frozen grader assets matched the trusted hash",
        ),
        CheckOutcome(
            name="scope_boundaries_respected",
            passed=not reasons.intersection(
                {"immutable_path_modified", "outside_editable_scope"}
            ),
            detail="no immutable or outside-editable edits",
        ),
        CheckOutcome(
            name="test_process_completed",
            passed=not reasons.intersection({"timeout", "test_evidence_missing"}),
            detail="authoritative test process completed with evidence",
        ),
        CheckOutcome(
            name="tests_collected",
            passed="no_tests_collected" not in reasons,
            detail="tests were collected",
        ),
        CheckOutcome(
            name="test_identity_exact",
            passed="test_identity_mismatch" not in reasons,
            detail="collected node IDs exactly matched the locked set",
        ),
        CheckOutcome(
            name="tests_passed",
            passed="authoritative_tests_failed" not in reasons,
            detail="every expected authoritative test passed exactly once",
        ),
        CheckOutcome(
            name="semantic_tests_passed",
            passed=not semantic_failed,
            detail=(
                "seeded semantic suite passed"
                if not semantic_failed
                else "seeded semantic suite failed: "
                + ", ".join(sorted(semantic_failed))
            ),
        ),
    ]


def _build_subscores(outcome: EvaluationOutcome, grader_name: str) -> list[SubScore]:
    """One subscore per mandatory check; weights make the sum equal the reward."""
    checks = _v2_checks(outcome)
    count = len(checks)
    failed = [check for check in checks if not check.passed]
    if outcome.status is OutcomeStatus.COMPLETED and outcome.reward == 1.0:
        weight_for = 1.0 / count
        weights = {check.name: weight_for for check in checks}
    elif failed:
        failing_weight = 1.0 / len(failed)
        weights = {check.name: 0.0 if check.passed else failing_weight for check in checks}
    else:
        weight_for = 1.0 / count
        weights = {check.name: weight_for for check in checks}
    subscores = [
        SubScore(
            name=check.name,
            weight=weights[check.name],
            value=1.0 if check.passed else 0.0,
            info={"detail": check.detail},
        )
        for check in checks
    ]
    subscores.append(
        SubScore(
            name="expected_scope",
            weight=0.0,
            value=1.0 if not outcome.changes.outside_expected_scope else 0.0,
            info={
                "paths": list(outcome.changes.outside_expected_scope),
                "informational": True,
            },
        )
    )
    return subscores


def _build_info(
    outcome: EvaluationOutcome,
    context: HudEvalContext,
    semantic: SemanticEvidence | None,
) -> dict[str, object]:
    collected: list[str] = []
    if outcome.test_evidence is not None:
        collected = list(outcome.test_evidence.collected_nodeids)
    info: dict[str, object] = {
        "status": outcome.status.value,
        "accepted": outcome.accepted,
        "reward": outcome.reward,
        "reason_codes": list(outcome.reason_codes),
        "warnings": list(outcome.warnings),
        "grader": context.grader_name,
        "grader_version": context.grader_version,
        "task_id": context.task_id,
        "manifest_sha256": context.manifest_sha256,
        "collected_nodeids": collected,
        "duration_seconds": outcome.duration_seconds,
    }
    if context.patch_id is not None:
        info["patch_id"] = context.patch_id
    if context.patch_diff_sha256 is not None:
        info["patch_diff_sha256"] = context.patch_diff_sha256
    info["workspace_hashes"] = {
        "pristine_sha256": context.pristine_sha256,
        "pre_grade_sha256": context.pre_grade_sha256,
        "post_grade_sha256": context.post_grade_sha256,
    }
    if outcome.error is not None:
        info["error"] = {"code": outcome.error.code, "message": outcome.error.message}
    if semantic is not None:
        info["semantic"] = {
            "profile_id": semantic.profile_id,
            "generator_version": semantic.generator_version,
            "seed": semantic.seed,
            "case_count": semantic.case_count,
            "passed": semantic.passed,
            "failed": semantic.failed,
            "errors": semantic.errors,
            "suite_sha256": semantic.suite_sha256,
        }
    return info


def map_evaluation_result_v2(
    outcome: EvaluationOutcome,
    context: HudEvalContext,
    semantic: SemanticEvidence | None,
) -> EvaluationResult:
    """Map a v2 core outcome to a HUD ``EvaluationResult`` (27.13 semantics)."""
    is_error = outcome.status is not OutcomeStatus.COMPLETED
    reward = outcome.reward if outcome.reward is not None else 0.0
    return EvaluationResult(
        reward=reward,
        done=True,
        isError=is_error,
        content=_concise_content(outcome, context.grader_name),
        info=_build_info(outcome, context, semantic),
        subscores=_build_subscores(outcome, context.grader_name),
    )
