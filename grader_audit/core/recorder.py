"""Atomic, no-overwrite result serialization (Section 27.16).

Results are written to a temporary sibling file and atomically renamed. An
existing record is never edited or overwritten; reruns use a new experiment ID.
Artifacts (stdout/stderr) are stored under ``artifacts/`` and referenced by path
and SHA-256 from the record.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from grader_audit.core.results import EvaluationRecord, ValidationRecord, serialize_record

_EXPERIMENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")

_GRADER_DIRS = {"naive", "hardened_v1", "oracle"}


class RecordExistsError(RuntimeError):
    """Raised when a result record already exists (no-overwrite policy)."""


def validate_experiment_id(experiment_id: str) -> None:
    if not _EXPERIMENT_ID_PATTERN.fullmatch(experiment_id):
        raise ValueError("experiment_id must match ^[a-z0-9][a-z0-9._-]{2,63}$")


class ExperimentRecorder:
    def __init__(self, results_root: Path, experiment_id: str) -> None:
        validate_experiment_id(experiment_id)
        self.results_root = results_root
        self.experiment_id = experiment_id
        self.experiment_dir = results_root / experiment_id
        self.artifacts_dir = self.experiment_dir / "artifacts"

    def record_path_for(self, record: EvaluationRecord) -> Path:
        if record.phase == "validation":
            assert record.validation_case is not None
            return (
                self.experiment_dir
                / "validation"
                / record.task.split
                / record.task.id
                / record.validation_case
                / (f"{record.repeat_index}.json")
            )
        if record.phase in ("controlled", "heldout"):
            assert record.patch is not None
            name = record.grader.name
            if name not in _GRADER_DIRS:
                raise ValueError(f"unsupported grader directory: {name}")
            return (
                self.experiment_dir
                / name
                / record.task.split
                / record.task.id
                / f"{record.patch.id}.json"
            )
        raise ValueError(f"unsupported record phase: {record.phase}")

    def write_record(self, record: EvaluationRecord) -> Path:
        """Atomically persist *record*, refusing to overwrite an existing one."""
        target = self.record_path_for(record)
        _atomic_write_no_overwrite(target, serialize_record(record))
        return target

    def write_artifact(self, run_id: str, suffix: str, data: bytes) -> Path:
        """Atomically store a binary artifact and return its path."""
        target = self.artifacts_dir / f"{run_id}.{suffix}"
        _atomic_write_no_overwrite(target, data)
        return target

    def write_validation_record(
        self,
        record: ValidationRecord,
        *,
        split: str,
        task_id: str,
        validation_case: str,
        repeat_index: int,
    ) -> Path:
        """Atomically persist a validation repeat record."""
        target = (
            self.experiment_dir
            / "validation"
            / split
            / task_id
            / validation_case
            / f"{repeat_index}.json"
        )
        _atomic_write_no_overwrite(target, record.serialize())
        return target

    def write_metadata(self, payload: dict[str, object]) -> Path:
        """Write the planned-matrix ``metadata.json`` atomically."""
        target = self.experiment_dir / "metadata.json"
        data = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        _atomic_write_no_overwrite(target, data)
        return target

    def exists(self, record: EvaluationRecord) -> bool:
        return self.record_path_for(record).exists()


def _atomic_write_no_overwrite(target: Path, data: bytes) -> None:
    if target.exists():
        raise RecordExistsError(f"refusing to overwrite existing record: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp-{uuid.uuid4().hex}")
    tmp.write_bytes(data)
    try:
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
