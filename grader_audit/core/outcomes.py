"""Evaluator-facing outcome and evidence models (Sections 27.12 and 27.16).

These are the strict typed outputs produced by the shared grading core. The
persisted ``EvaluationRecord`` in :mod:`grader_audit.core.results` reuses them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from grader_audit.core.base import StrictModel
from grader_audit.core.reason_codes import ReasonCode, serialize_reason_codes

_EVIDENCE_STATES = ("complete", "not_run", "missing")


class OutcomeStatus(StrEnum):
    """Section 27.12 status values."""

    COMPLETED = "completed"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    INVALID_INPUT = "invalid_input"


class Phase(StrEnum):
    """Section 27.16 ``phase`` values."""

    VALIDATION = "validation"
    LABELING = "labeling"
    CONTROLLED = "controlled"
    HELDOUT = "heldout"
    ADAPTIVE = "adaptive"
    NATURAL_ROLLOUT = "natural_rollout"


class TestEvidence(StrictModel):
    """Parsed or observed test-execution evidence."""

    state: str
    collected_nodeids: list[str] = Field(default_factory=list)
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    xfailed: int = 0
    xpassed: int = 0
    report_sha256: str | None = None
    parsed_collected_count: int | None = None

    @model_validator(mode="after")
    def check_state(self) -> TestEvidence:
        if self.state not in _EVIDENCE_STATES:
            raise ValueError(f"evidence state must be one of {_EVIDENCE_STATES}")
        if any(
            count < 0
            for count in (
                self.passed,
                self.failed,
                self.errors,
                self.skipped,
                self.xfailed,
                self.xpassed,
            )
        ):
            raise ValueError("test outcome counts must be non-negative")
        return self


class Changes(StrictModel):
    """Workspace change classification (Sections 8.2.D and 27.10)."""

    modified_paths: list[str] = Field(default_factory=list)
    immutable_violations: list[str] = Field(default_factory=list)
    outside_editable_scope: list[str] = Field(default_factory=list)
    outside_expected_scope: list[str] = Field(default_factory=list)
    generated_artifacts: list[str] = Field(default_factory=list)

    @property
    def has_hard_violation(self) -> bool:
        return bool(self.immutable_violations or self.outside_editable_scope)


class ProcessInfo(StrictModel):
    """Subprocess observation recorded for every evaluated command."""

    argv: list[str] = Field(min_length=1)
    cwd: str
    exit_code: int | None = None
    timed_out: bool = False
    stdout_path: str | None = None
    stderr_path: str | None = None
    stdout_sha256: str | None = None
    stderr_sha256: str | None = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    duration_seconds: float = 0.0


class ResultInfo(StrictModel):
    """The binary solution outcome (Sections 4.2 and 27.12)."""

    reward: float | None = None
    accepted: bool | None = None
    reason_codes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0

    @model_validator(mode="after")
    def check_reward(self) -> ResultInfo:
        if self.reward is None:
            if self.accepted is not None:
                raise ValueError("accepted must be null when reward is null")
        else:
            if self.reward not in (0.0, 1.0):
                raise ValueError("controlled audit rewards are exactly 0.0 or 1.0")
            if self.accepted != (self.reward == 1.0):
                raise ValueError("accepted must equal reward == 1.0")
        return self


class WorkspaceHashes(StrictModel):
    """Deterministic snapshot hashes for one evaluation (Section 27.10)."""

    pristine_sha256: str
    pre_grade_sha256: str
    post_grade_sha256: str


class GraderInfo(StrictModel):
    name: str
    version: str


class TaskInfo(StrictModel):
    id: str
    split: str
    manifest_sha256: str


class PatchInfo(StrictModel):
    id: str
    label: str
    subtype: str
    attack_family: str | None = None
    metadata_sha256: str
    diff_sha256: str


class EnvironmentInfo(StrictModel):
    python: str
    pytest: str
    hud: str
    docker_image_digest: str


class GitInfo(StrictModel):
    data_commit: str
    grader_frozen_commit: str | None = None
    worktree_dirty: bool = False


class ErrorInfo(StrictModel):
    """Typed infrastructure/input error detail (Section 27.16)."""

    code: str
    message: str
    details: str | None = None


class EvaluationOutcome(StrictModel):
    """The shared core outcome produced by every evaluator."""

    status: OutcomeStatus
    reward: float | None = None
    reason_codes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
    test_evidence: TestEvidence | None = None
    changes: Changes = Field(default_factory=Changes)
    process: ProcessInfo | None = None
    duration_seconds: float = 0.0

    @model_validator(mode="after")
    def check_status(self) -> EvaluationOutcome:
        if self.status is OutcomeStatus.COMPLETED:
            if self.reward not in (0.0, 1.0):
                raise ValueError("completed outcomes require reward 0.0 or 1.0")
            if self.error is not None:
                raise ValueError("completed outcomes must not carry an error")
        else:
            if self.reward is not None:
                raise ValueError("non-completed outcomes must have null reward")
            if self.error is None:
                raise ValueError("non-completed outcomes require an error object")
        return self

    @property
    def accepted(self) -> bool | None:
        if self.status is not OutcomeStatus.COMPLETED:
            return None
        return self.reward == 1.0

    def to_result_info(self) -> ResultInfo:
        return ResultInfo(
            reward=self.reward,
            accepted=self.accepted,
            reason_codes=self.reason_codes,
            warnings=self.warnings,
            duration_seconds=self.duration_seconds,
        )


class OracleOutcome(StrictModel):
    """Offline oracle outcome used only for dataset labeling (Sections 8.3/27.9)."""

    status: OutcomeStatus
    passed: bool
    reason_codes: list[str] = Field(default_factory=list)
    test_evidence: TestEvidence | None = None
    error: ErrorInfo | None = None
    node_outcomes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_status(self) -> OracleOutcome:
        if self.status is OutcomeStatus.COMPLETED:
            if self.error is not None:
                raise ValueError("completed oracle outcomes must not carry an error")
        else:
            if self.error is None:
                raise ValueError("non-completed oracle outcomes require an error")
        return self


def outcome_with_reason(
    status: OutcomeStatus,
    reward: float | None,
    reasons: list[ReasonCode],
    *,
    warnings: list[str] | None = None,
    error: ErrorInfo | None = None,
    test_evidence: TestEvidence | None = None,
    changes: Changes | None = None,
    process: ProcessInfo | None = None,
    duration_seconds: float = 0.0,
) -> EvaluationOutcome:
    """Build an outcome, serializing reason codes in stable evaluation order."""
    return EvaluationOutcome(
        status=status,
        reward=reward,
        reason_codes=serialize_reason_codes(reasons),
        warnings=warnings or [],
        error=error,
        test_evidence=test_evidence,
        changes=changes if changes is not None else Changes(),
        process=process,
        duration_seconds=duration_seconds,
    )
