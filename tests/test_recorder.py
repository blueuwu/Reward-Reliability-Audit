"""Atomic no-overwrite result serialization (Sections 27.16, 27.19)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from grader_audit.core.outcomes import (
    Changes,
    EnvironmentInfo,
    GitInfo,
    GraderInfo,
    PatchInfo,
    ResultInfo,
    TaskInfo,
    WorkspaceHashes,
)
from grader_audit.core.recorder import (
    ExperimentRecorder,
    RecordExistsError,
    validate_experiment_id,
)
from grader_audit.core.results import EvaluationRecord, ValidationRecord, ValidationRun


def _record(experiment_id: str) -> EvaluationRecord:
    return EvaluationRecord(
        schema_version="1.0",
        run_id="run-1",
        experiment_id=experiment_id,
        timestamp_utc="2026-08-06T00:00:00+00:00",
        status="completed",
        phase="controlled",
        repeat_index=0,
        git=GitInfo(data_commit="0" * 40, worktree_dirty=False),
        grader=GraderInfo(name="naive", version="v1"),
        task=TaskInfo(id="fixture-stringutil", split="development", manifest_sha256="0" * 64),
        patch=PatchInfo(
            id="gold",
            label="valid",
            subtype="gold",
            attack_family=None,
            metadata_sha256="0" * 64,
            diff_sha256="0" * 64,
        ),
        environment=EnvironmentInfo(
            python="3.12.0", pytest="9.1.1", hud="0.6.12", docker_image_digest="sha256:test"
        ),
        workspace=WorkspaceHashes(
            pristine_sha256="0" * 64, pre_grade_sha256="0" * 64, post_grade_sha256="0" * 64
        ),
        result=ResultInfo(reward=1.0, accepted=True, reason_codes=["naive_exit_zero"]),
        process=None,
        test_evidence=None,
        changes=Changes(),
    )


def test_experiment_id_validation() -> None:
    validate_experiment_id("controlled-001")
    for bad in ("A", "ab", "-bad", "bad id", "x" * 65):
        with pytest.raises(ValueError):
            validate_experiment_id(bad)


def _revalidate(record: EvaluationRecord) -> EvaluationRecord:
    return EvaluationRecord.model_validate(record.model_dump(mode="python"))


def test_record_invariants_completed() -> None:
    with pytest.raises(ValidationError, match="exactly"):
        ResultInfo(reward=0.5, accepted=False)
    with pytest.raises(ValidationError, match="require an error"):
        _revalidate(
            _record("controlled-001").model_copy(
                update={"status": "infrastructure_error", "result": ResultInfo()}
            )
        )
    with pytest.raises(ValidationError, match="accepted must equal reward"):
        _revalidate(
            _record("controlled-001").model_copy(
                update={"result": ResultInfo(reward=0.0, accepted=True)}
            )
        )


def test_record_baseline_requires_null_patch() -> None:
    record = _revalidate(
        _record("controlled-001").model_copy(
            update={"phase": "validation", "validation_case": "baseline", "patch": None}
        )
    )
    assert record.validation_case == "baseline"
    with pytest.raises(ValidationError, match="must have null patch"):
        _revalidate(
            _record("controlled-001").model_copy(
                update={"phase": "validation", "validation_case": "baseline"}
            )
        )


def test_recorder_writes_atomically_and_refuses_overwrite(tmp_path: Path) -> None:
    recorder = ExperimentRecorder(tmp_path, "controlled-001")
    path = recorder.write_record(_record("controlled-001"))
    assert path.is_file()
    with pytest.raises(RecordExistsError):
        recorder.write_record(_record("controlled-001"))


def test_recorder_validation_record_path(tmp_path: Path) -> None:
    recorder = ExperimentRecorder(tmp_path, "controlled-001")
    run = ValidationRun(
        grader=GraderInfo(name="naive", version="v1"),
        status="completed",
        reward=0.0,
        accepted=False,
        changes=Changes(),
        workspace=WorkspaceHashes(
            pristine_sha256="0" * 64, pre_grade_sha256="0" * 64, post_grade_sha256="0" * 64
        ),
    )
    record = ValidationRecord(
        schema_version="1.0",
        run_id="r1",
        experiment_id="controlled-001",
        timestamp_utc="2026-08-06T00:00:00+00:00",
        git=GitInfo(data_commit="0" * 40, worktree_dirty=False),
        task=TaskInfo(id="fixture-stringutil", split="development", manifest_sha256="0" * 64),
        environment=EnvironmentInfo(
            python="3.12.0", pytest="9.1.1", hud="0.6.12", docker_image_digest="sha256:test"
        ),
        validation_case="baseline",
        repeat_index=1,
        runs={"naive": run},
        stable=True,
    )
    path = recorder.write_validation_record(
        record,
        split="development",
        task_id="fixture-stringutil",
        validation_case="baseline",
        repeat_index=1,
    )
    assert path.name == "1.json"
    assert "validation/development/fixture-stringutil/baseline" in path.as_posix()


def test_serialized_record_is_compact_sorted(tmp_path: Path) -> None:
    recorder = ExperimentRecorder(tmp_path, "controlled-001")
    path = recorder.write_record(_record("controlled-001"))
    raw = path.read_bytes()
    assert raw.count(b"\n") == 0
    text = raw.decode("utf-8")
    keys = ["changes", "environment", "error", "experiment_id", "git", "grader", "patch"]
    positions = [text.index(f'"{key}"') for key in keys]
    assert positions == sorted(positions)
