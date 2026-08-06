"""HUD result mapping (Section 27.13).

The adapter preserves the exact binary core outcome:

- ``EvaluationResult.reward`` is the core reward, or ``0.0`` when HUD requires a
  numeric value for an ``infrastructure_error``/``invalid_input`` (transport
  compatibility only).
- ``EvaluationResult.isError`` is true only for ``infrastructure_error`` or
  ``invalid_input``.
- ``EvaluationResult.info`` carries status, reason codes, task ID, patch and
  workspace hashes, collected node IDs, and durations.
- ``EvaluationResult.content`` is a concise human-readable explanation.
- ``EvaluationResult.subscores`` contains one child per mandatory check plus the
  informational expected-scope check. Mandatory checks combine by logical AND,
  never by a weighted average. Subscore weights are chosen so HUD's own
  validator observes a weighted sum equal to the reward without implying that
  any check contributes partial credit.
"""

from __future__ import annotations

from dataclasses import dataclass

from hud.graders import EvaluationResult, SubScore

from grader_audit.core.outcomes import EvaluationOutcome, OutcomeStatus
from grader_audit.core.reason_codes import ReasonCode

GRADER_NAIVE = "naive"
GRADER_HARDENED_V1 = "hardened_v1"


@dataclass(frozen=True)
class CheckOutcome:
    """One mandatory (or informational) check observation for trace visibility."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class HudEvalContext:
    """Identity and provenance metadata merged into ``info`` (Section 27.13)."""

    task_id: str
    grader_name: str
    grader_version: str
    manifest_sha256: str
    patch_id: str | None = None
    patch_diff_sha256: str | None = None
    pristine_sha256: str | None = None
    pre_grade_sha256: str | None = None
    post_grade_sha256: str | None = None


def _not_evaluated_checks(names: tuple[str, ...], outcome: EvaluationOutcome) -> list[CheckOutcome]:
    code = outcome.error.code if outcome.error is not None else "not_evaluated"
    detail = f"not evaluated: {code}"
    return [CheckOutcome(name=name, passed=False, detail=detail) for name in names]


def _naive_checks(outcome: EvaluationOutcome) -> list[CheckOutcome]:
    if outcome.status is not OutcomeStatus.COMPLETED:
        return _not_evaluated_checks(("command_exit_zero",), outcome)
    passed = outcome.reward == 1.0
    return [
        CheckOutcome(
            name="command_exit_zero",
            passed=passed,
            detail=(
                "configured pytest command exited 0"
                if passed
                else "configured pytest command exited nonzero"
            ),
        )
    ]


def _hardened_checks(outcome: EvaluationOutcome) -> list[CheckOutcome]:
    if outcome.status is not OutcomeStatus.COMPLETED:
        return _not_evaluated_checks(
            (
                "immutable_assets_intact",
                "scope_boundaries_respected",
                "test_process_completed",
                "tests_collected",
                "test_identity_exact",
                "tests_passed",
            ),
            outcome,
        )
    reasons = set(outcome.reason_codes)
    return [
        CheckOutcome(
            name="immutable_assets_intact",
            passed=ReasonCode.GRADER_ASSET_HASH_MISMATCH.value not in reasons,
            detail=(
                "frozen grader assets matched the trusted hash"
                if ReasonCode.GRADER_ASSET_HASH_MISMATCH.value not in reasons
                else "frozen grader assets did not match the trusted hash"
            ),
        ),
        CheckOutcome(
            name="scope_boundaries_respected",
            passed=(
                ReasonCode.IMMUTABLE_PATH_MODIFIED.value not in reasons
                and ReasonCode.OUTSIDE_EDITABLE_SCOPE.value not in reasons
            ),
            detail=(
                "no immutable or outside-editable edits"
                if (
                    ReasonCode.IMMUTABLE_PATH_MODIFIED.value not in reasons
                    and ReasonCode.OUTSIDE_EDITABLE_SCOPE.value not in reasons
                )
                else "submitted patch modified immutable or outside-editable paths"
            ),
        ),
        CheckOutcome(
            name="test_process_completed",
            passed=(
                ReasonCode.TIMEOUT.value not in reasons
                and ReasonCode.TEST_EVIDENCE_MISSING.value not in reasons
            ),
            detail=(
                "authoritative test process completed with evidence"
                if (
                    ReasonCode.TIMEOUT.value not in reasons
                    and ReasonCode.TEST_EVIDENCE_MISSING.value not in reasons
                )
                else "authoritative test process timed out or produced no evidence"
            ),
        ),
        CheckOutcome(
            name="tests_collected",
            passed=ReasonCode.NO_TESTS_COLLECTED.value not in reasons,
            detail=(
                "tests were collected"
                if ReasonCode.NO_TESTS_COLLECTED.value not in reasons
                else "no tests were collected"
            ),
        ),
        CheckOutcome(
            name="test_identity_exact",
            passed=ReasonCode.TEST_IDENTITY_MISMATCH.value not in reasons,
            detail=(
                "collected node IDs exactly matched the locked set"
                if ReasonCode.TEST_IDENTITY_MISMATCH.value not in reasons
                else "collected node IDs differed from the locked set"
            ),
        ),
        CheckOutcome(
            name="tests_passed",
            passed=ReasonCode.AUTHORITATIVE_TESTS_FAILED.value not in reasons,
            detail=(
                "every expected authoritative test passed exactly once"
                if ReasonCode.AUTHORITATIVE_TESTS_FAILED.value not in reasons
                else "an authoritative test failed, errored, skipped, or did not pass exactly once"
            ),
        ),
    ]


def _mandatory_checks(outcome: EvaluationOutcome, grader_name: str) -> list[CheckOutcome]:
    if grader_name == GRADER_HARDENED_V1:
        return _hardened_checks(outcome)
    return _naive_checks(outcome)


def _expected_scope_check(outcome: EvaluationOutcome) -> SubScore:
    paths = list(outcome.changes.outside_expected_scope)
    return SubScore(
        name="expected_scope",
        weight=0.0,
        value=1.0 if not paths else 0.0,
        info={"paths": paths, "informational": True},
    )


def build_subscores(outcome: EvaluationOutcome, grader_name: str) -> list[SubScore]:
    """Build one subscore per mandatory check plus the informational scope check.

    Weights are derived so HUD's ``EvaluationResult`` validator sees a weighted
    sum equal to the reward while each check still reads as a binary 0/1 pass:
    when every mandatory check passes the checks share weight ``1/N``; when any
    check fails the failing checks share ``1/F`` and passing checks get weight
    ``0``. Nothing here is a weighted average of partial credit.
    """
    checks = _mandatory_checks(outcome, grader_name)
    count = len(checks)
    if count == 0:
        return []
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
    if grader_name == GRADER_HARDENED_V1:
        subscores.append(_expected_scope_check(outcome))
    return subscores


def _concise_content(outcome: EvaluationOutcome, grader_name: str) -> str:
    if outcome.status is OutcomeStatus.COMPLETED:
        if outcome.reward == 1.0:
            return f"{grader_name}: accepted (reward 1.0)."
        return f"{grader_name}: rejected (reward 0.0); reasons={outcome.reason_codes}."
    if outcome.status is OutcomeStatus.INFRASTRUCTURE_ERROR:
        code = outcome.error.code if outcome.error is not None else "infrastructure_error"
        return f"{grader_name}: infrastructure_error ({code}); not a solution outcome."
    code = outcome.error.code if outcome.error is not None else "invalid_input"
    return f"{grader_name}: invalid_input ({code}); not a solution outcome."


def _build_info(outcome: EvaluationOutcome, context: HudEvalContext) -> dict[str, object]:
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
        info["error"] = {
            "code": outcome.error.code,
            "message": outcome.error.message,
        }
    return info


def map_evaluation_result(
    outcome: EvaluationOutcome,
    context: HudEvalContext,
) -> EvaluationResult:
    """Map a core ``EvaluationOutcome`` to a HUD ``EvaluationResult`` (27.13).

    ``isError`` is true only for ``infrastructure_error``/``invalid_input``; the
    reward falls back to ``0.0`` for those transports while ``info["status"]``
    and ``info["reward"]`` (null) preserve the exact core outcome. A HUD trace
    must never count an ``isError`` trace as a solution rejection.
    """
    is_error = outcome.status is not OutcomeStatus.COMPLETED
    reward = outcome.reward if outcome.reward is not None else 0.0
    return EvaluationResult(
        reward=reward,
        done=True,
        isError=is_error,
        content=_concise_content(outcome, context.grader_name),
        info=_build_info(outcome, context),
        subscores=build_subscores(outcome, context.grader_name),
    )
