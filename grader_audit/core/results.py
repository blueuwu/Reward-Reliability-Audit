"""Persisted evaluation record schema (Section 27.16).

The record is strict (``extra="forbid"``), serialized with sorted object keys
and compact JSON separators, and written atomically by the recorder. Pydantic
enforces the cross-field invariants listed in Section 27.16.
"""

from __future__ import annotations

import json

from pydantic import Field, model_validator

from grader_audit.core.base import StrictModel
from grader_audit.core.outcomes import (
    Changes,
    EnvironmentInfo,
    ErrorInfo,
    GitInfo,
    GraderInfo,
    PatchInfo,
    ProcessInfo,
    ResultInfo,
    TaskInfo,
    TestEvidence,
    WorkspaceHashes,
)

_VALIDATION_CASES = ("baseline", "gold")
_PHASES = ("validation", "labeling", "controlled", "heldout", "adaptive", "natural_rollout")
_STATUSES = ("completed", "infrastructure_error", "invalid_input")


class EvaluationRecord(StrictModel):
    """One immutable JSON evaluation record (Section 27.16 top-level fields)."""

    schema_version: str
    run_id: str
    experiment_id: str
    timestamp_utc: str
    status: str
    phase: str
    validation_case: str | None = None
    repeat_index: int = 0
    git: GitInfo
    grader: GraderInfo
    task: TaskInfo
    patch: PatchInfo | None = None
    environment: EnvironmentInfo
    workspace: WorkspaceHashes
    result: ResultInfo
    process: ProcessInfo | None = None
    test_evidence: TestEvidence | None = None
    changes: Changes
    error: ErrorInfo | None = None

    @model_validator(mode="after")
    def check_invariants(self) -> EvaluationRecord:
        if self.schema_version != "1.0":
            raise ValueError("schema_version must be '1.0'")
        if self.status not in _STATUSES:
            raise ValueError(f"status must be one of {_STATUSES}")
        if self.phase not in _PHASES:
            raise ValueError(f"phase must be one of {_PHASES}")
        if self.validation_case is not None:
            if self.phase != "validation":
                raise ValueError("validation_case requires phase 'validation'")
            if self.validation_case not in _VALIDATION_CASES:
                raise ValueError(f"validation_case must be one of {_VALIDATION_CASES}")
        if self.phase == "validation" and self.validation_case is None:
            raise ValueError("validation records require a validation_case")
        if self.validation_case == "baseline":
            if self.patch is not None:
                raise ValueError("baseline validation records must have null patch")
        elif self.patch is None:
            raise ValueError("non-baseline records require a patch reference")
        if self.repeat_index < 0:
            raise ValueError("repeat_index must be non-negative")
        if self.status == "completed":
            if self.result.reward not in (0.0, 1.0):
                raise ValueError("completed records require reward 0.0 or 1.0")
            if self.result.accepted != (self.result.reward == 1.0):
                raise ValueError("accepted must equal reward == 1.0")
            if self.error is not None:
                raise ValueError("completed records must not carry an error")
        else:
            if self.result.reward is not None or self.result.accepted is not None:
                raise ValueError("non-completed records must have null reward and accepted")
            if self.error is None:
                raise ValueError("non-completed records require an error object")
        return self


def serialize_record(record: EvaluationRecord) -> bytes:
    """Serialize a record deterministically: UTF-8, sorted keys, compact JSON."""
    payload = record.model_dump(mode="json")
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return text.encode("utf-8")


class ValidationRun(StrictModel):
    """One grader's contribution to a baseline/gold validation repeat."""

    grader: GraderInfo
    status: str
    reward: float | None = None
    accepted: bool | None = None
    reason_codes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
    test_evidence: TestEvidence | None = None
    changes: Changes = Field(default_factory=Changes)
    workspace: WorkspaceHashes
    process: ProcessInfo | None = None
    duration_seconds: float = 0.0
    node_outcomes: dict[str, str] = Field(default_factory=dict)


class ValidationRecord(StrictModel):
    """One baseline/gold validation repeat aggregating naive, authoritative, oracle."""

    schema_version: str
    run_id: str
    experiment_id: str
    timestamp_utc: str
    git: GitInfo
    task: TaskInfo
    environment: EnvironmentInfo
    validation_case: str
    repeat_index: int
    runs: dict[str, ValidationRun]
    stable: bool

    @model_validator(mode="after")
    def check_invariants(self) -> ValidationRecord:
        if self.schema_version != "1.0":
            raise ValueError("schema_version must be '1.0'")
        if self.validation_case not in _VALIDATION_CASES:
            raise ValueError(f"validation_case must be one of {_VALIDATION_CASES}")
        if self.repeat_index < 1:
            raise ValueError("repeat_index is one-based for validation records")
        return self

    def serialize(self) -> bytes:
        payload = self.model_dump(mode="json")
        text = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return text.encode("utf-8")
