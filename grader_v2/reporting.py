"""Path-tolerant report generator for ``grader_v2`` (see package docstring).

The frozen v1 report tool (``grader_audit.core.reporting``) rejects recorded
artifact paths that are not repository-relative, which fails on Windows
orchestration hosts whenever the recorder was rooted at an absolute path (the
``reproduce`` and ``run-heldout`` paths resolve roots absolute). This module
reimplements the report pipeline with one deliberate relaxation: an artifact
path is accepted when it resolves to a real file inside the experiment
directory, whether the recorded path is repository-relative or absolute.
Everything else (record schema validation, planned-matrix identity and
completeness, cross-grader workspace hashes, artifact SHA-256 verification,
confirmed hash-matching annotations, INCOMPLETE semantics) is identical to v1.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from grader_audit.core.hashing import sha256_file
from grader_audit.core.outcomes import ProcessInfo
from grader_audit.core.reporting import (
    LoadedExperiment,
    PlanCell,
    ReportError,
    ValidationPlanCell,
    _incomplete_problems,  # pyright: ignore[reportPrivateUsage]
    _primary_records,  # pyright: ignore[reportPrivateUsage]
    _verify_annotations_and_raw_hashes,  # pyright: ignore[reportPrivateUsage]
    render_report,
    verify_experiment,
)
from grader_audit.core.results import EvaluationRecord, ValidationRecord


def resolve_artifact_safe_v2(project_root: Path, experiment_dir: Path, recorded: str) -> Path:
    """Resolve a recorded artifact path, tolerating absolute paths in the experiment dir.

    v1 behavior: only repository-relative paths are accepted. v2 additionally
    accepts absolute (drive-prefixed or root-prefixed) paths whose resolved
    target lies inside *experiment_dir*. NUL bytes and ``..`` traversal are
    rejected as in v1; the containment check is mandatory for both forms.
    """
    if not recorded or "\x00" in recorded:
        raise ReportError(f"invalid artifact path: {recorded!r}")
    normalized = recorded.replace("\\", "/")
    candidate = Path(normalized)
    if not candidate.is_absolute():
        from grader_audit.core.path_rules import classify_repository_relative

        reason = classify_repository_relative(normalized)
        if reason is not None:
            raise ReportError(f"unsafe artifact path {recorded!r}: {reason}")
        candidate = project_root / candidate
    if not candidate.is_file():
        raise ReportError(f"missing artifact: {recorded}")
    try:
        candidate.resolve().relative_to(experiment_dir.resolve())
    except ValueError:
        raise ReportError(f"artifact outside experiment directory: {recorded}") from None
    return candidate


def _verify_process_artifacts_v2(
    project_root: Path,
    experiment_dir: Path,
    process: ProcessInfo | None,
    referenced: set[str],
) -> None:
    if process is None:
        return
    for attr in ("stdout_path", "stderr_path"):
        recorded = getattr(process, attr)
        sha = getattr(process, f"{attr[:6]}_sha256")
        if bool(recorded) != bool(sha):
            raise ReportError(
                f"artifact {attr} must have both a path and a hash or neither: "
                f"{recorded!r} vs {sha!r}"
            )
        if not recorded or not sha:
            continue
        artifact = resolve_artifact_safe_v2(project_root, experiment_dir, recorded)
        if sha256_file(artifact) != sha:
            raise ReportError(f"artifact hash mismatch: {recorded}")
        referenced.add(artifact.resolve().as_posix())


def _verify_record_artifacts_v2(
    project_root: Path,
    experiment_dir: Path,
    record: EvaluationRecord,
    referenced: set[str],
) -> None:
    _verify_process_artifacts_v2(project_root, experiment_dir, record.process, referenced)


def _verify_validation_artifacts_v2(
    project_root: Path,
    experiment_dir: Path,
    record: ValidationRecord,
    referenced: set[str],
) -> None:
    for run in record.runs.values():
        _verify_process_artifacts_v2(project_root, experiment_dir, run.process, referenced)


def _reject_unreferenced_artifacts(experiment_dir: Path, referenced: set[str]) -> None:
    artifacts_dir = experiment_dir / "artifacts"
    if not artifacts_dir.is_dir():
        return
    for path in sorted(artifacts_dir.rglob("*")):
        if path.is_file() and path.resolve().as_posix() not in referenced:
            raise ReportError(f"unreferenced artifact: {path}")


def _is_validation_path(experiment_dir: Path, record_path: Path) -> bool:
    try:
        rel = record_path.relative_to(experiment_dir).parts
    except ValueError:
        return False
    return len(rel) >= 1 and rel[0] == "validation"


def _verify_validation_record_v2(
    project_root: Path,
    experiment_dir: Path,
    record_path: Path,
    experiment_id: str,
    referenced: set[str],
) -> ValidationRecord:
    try:
        record = ValidationRecord.model_validate(
            json.loads(record_path.read_text(encoding="utf-8"))
        )
    except Exception as exc:
        raise ReportError(f"invalid validation record {record_path}: {exc}") from None
    if record.experiment_id != experiment_id:
        raise ReportError(
            f"validation record experiment id mismatch: "
            f"{record.experiment_id} != {experiment_id}"
        )
    expected = (
        experiment_dir
        / "validation"
        / record.task.split
        / record.task.id
        / record.validation_case
        / f"{record.repeat_index}.json"
    )
    if record_path.resolve() != expected.resolve():
        raise ReportError(
            f"validation record at wrong path: {record_path} (expected {expected})"
        )
    if not record.stable:
        raise ReportError(f"validation record not stable: {record_path}")
    _verify_validation_artifacts_v2(project_root, experiment_dir, record, referenced)
    return record


def _verify_record_location_v2(
    experiment_dir: Path, record_path: Path, record: EvaluationRecord
) -> None:
    patch_id = record.patch.id if record.patch is not None else "?"
    expected = (
        experiment_dir / record.grader.name / record.task.split / record.task.id
        / f"{patch_id}.json"
    )
    if record_path.resolve() != expected.resolve():
        raise ReportError(f"record at wrong path: {record_path} (expected {expected})")


def _opt(value: object) -> str | None:
    return str(value) if value is not None else None


def load_experiment_v2(project_root: Path, experiment_dir: Path) -> LoadedExperiment:
    """Load an experiment exactly like v1, but with v2 artifact resolution.

    The planned-matrix metadata and record paths are parsed identically to
    ``grader_audit.core.reporting.load_experiment``; only the artifact-path
    resolver and the record-relative-path computation are relaxed to tolerate
    absolute recorded paths on Windows hosts.
    """
    metadata_path = experiment_dir / "metadata.json"
    if not metadata_path.is_file():
        raise ReportError(f"experiment has no metadata.json: {experiment_dir}")
    metadata = cast(dict[str, object], json.loads(metadata_path.read_text(encoding="utf-8")))
    experiment_id = str(metadata.get("experiment_id", experiment_dir.name))
    plan_value = metadata.get("plan")
    plan_raw: list[object] = []
    validation_raw: list[object] = []
    if isinstance(plan_value, dict):
        plan_dict = cast(dict[str, object], plan_value)
        controlled = plan_dict.get("controlled")
        if isinstance(controlled, list):
            plan_raw = cast(list[object], controlled)
        validation = plan_dict.get("validation")
        if isinstance(validation, list):
            validation_raw = cast(list[object], validation)
    plan: list[PlanCell] = []
    for entry in plan_raw:
        if not isinstance(entry, dict):
            continue
        item = cast(dict[str, object], entry)
        plan.append(
            PlanCell(
                grader=str(item.get("grader", "")),
                task_id=str(item.get("task_id", "")),
                patch_id=str(item.get("patch_id", "")),
                split=str(item.get("split", "")),
                phase=str(item.get("phase", "controlled")),
                task_manifest_sha256=_opt(item.get("task_manifest_sha256")),
                patch_metadata_sha256=_opt(item.get("patch_metadata_sha256")),
                patch_diff_sha256=_opt(item.get("patch_diff_sha256")),
            )
        )
    validation_plan: list[ValidationPlanCell] = []
    for entry in validation_raw:
        if not isinstance(entry, dict):
            continue
        item = cast(dict[str, object], entry)
        repeat_value = item.get("repeat_index")
        repeat_index = int(repeat_value) if isinstance(repeat_value, int) else 0
        validation_plan.append(
            ValidationPlanCell(
                task_id=str(item.get("task_id", "")),
                split=str(item.get("split", "")),
                task_manifest_sha256=_opt(item.get("task_manifest_sha256")),
                validation_case=str(item.get("validation_case", "")),
                repeat_index=repeat_index,
            )
        )
    if not plan and not validation_plan:
        raise ReportError(f"experiment has no valid plan: {experiment_dir}")

    records: list[EvaluationRecord] = []
    validation_records: list[ValidationRecord] = []
    referenced: set[str] = set()
    record_paths: dict[tuple[str, str, str, str], str] = {}
    for record_path in sorted(experiment_dir.rglob("*.json")):
        if record_path.name == "metadata.json":
            continue
        if _is_validation_path(experiment_dir, record_path):
            validation_records.append(
                _verify_validation_record_v2(
                    project_root, experiment_dir, record_path, experiment_id, referenced
                )
            )
            continue
        try:
            record = EvaluationRecord.model_validate(
                json.loads(record_path.read_text(encoding="utf-8"))
            )
        except Exception as exc:
            raise ReportError(f"invalid record {record_path}: {exc}") from None
        if record.experiment_id != experiment_id:
            raise ReportError(
                f"record experiment id mismatch: {record.experiment_id} != {experiment_id}"
            )
        if record.phase not in ("controlled", "heldout", "validation"):
            raise ReportError(f"unexpected record phase: {record.phase}")
        if record.phase == "validation":
            raise ReportError(
                f"validation-phase record outside the validation tree: {record_path}"
            )
        if record.phase in ("controlled", "heldout"):
            _verify_record_location_v2(experiment_dir, record_path, record)
            _verify_record_artifacts_v2(project_root, experiment_dir, record, referenced)
            if record.patch is not None:
                record_paths[
                    (record.grader.name, record.task.id, record.patch.id, record.phase)
                ] = record_path.resolve().relative_to(project_root.resolve()).as_posix()
        records.append(record)
    _reject_unreferenced_artifacts(experiment_dir, referenced)
    return LoadedExperiment(
        experiment_id=experiment_id,
        plan=plan,
        validation_plan=validation_plan,
        records=records,
        validation_records=validation_records,
        record_paths=record_paths,
    )


def run_report_v2(
    *,
    project_root: Path,
    input_dir: Path,
    output_path: Path,
    final: bool = False,
    annotations_root: Path | None = None,
) -> str:
    """Generate a Markdown report with v2 artifact-path tolerance (D-052).

    All v1 report semantics are preserved except the artifact-path resolver:
    planned-matrix identity/completeness, artifact SHA-256s, cross-grader
    workspace hashes, confirmed hash-matching annotations, and the
    ``INCOMPLETE`` status for infrastructure/invalid-input outcomes.
    """
    loaded = load_experiment_v2(project_root, input_dir)
    verify_experiment(project_root, loaded)
    if annotations_root is None:
        annotations_root = project_root / "results" / "annotations"
    if not annotations_root.is_absolute():
        annotations_root = project_root / annotations_root
    _verify_annotations_and_raw_hashes(project_root, input_dir, annotations_root, loaded)

    frozen_tag: str | None = None
    protected_tree_sha256: str | None = None
    lock_path = project_root / "freeze" / "grader_v1.lock.json"
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        frozen_tag = str(lock.get("git_tag"))
        protected_tree_sha256 = str(lock.get("protected_tree_sha256"))

    report = render_report(
        loaded, frozen_tag=frozen_tag, protected_tree_sha256=protected_tree_sha256
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8", newline="\n")
    if final:
        from grader_audit.core.paths import FINAL_REPORT

        target = project_root / FINAL_REPORT
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report, encoding="utf-8", newline="\n")

    problems = _incomplete_problems(_primary_records(loaded))
    if problems:
        raise ReportError("matrix INCOMPLETE: " + "; ".join(problems))
    return report
