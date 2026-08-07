"""Official ``report`` generation (Sections 27.15-27.18).

``grader-audit report --input results/raw/ID --output results/summaries/ID.md``
is read-only with respect to raw results. It validates every raw record and
artifact path/hash, the planned matrix (identity hashes, no duplicates, no
wrong-path/missing/extra cells), and cross-grader workspace hashes; stops with
an ``INCOMPLETE`` diagnostic (exit 3) on any infrastructure/invalid-input
outcome; and otherwise renders primary, split, combined, held-out, family,
reason, duration, and false-rejection-by-subtype metrics with Wilson intervals.
For the designated final experiment it also copies the generated Markdown
byte-for-byte to ``results/report.md``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from grader_audit.core.hashing import sha256_file
from grader_audit.core.outcomes import OutcomeStatus, ProcessInfo
from grader_audit.core.path_rules import classify_repository_relative
from grader_audit.core.results import EvaluationRecord, ValidationRecord

_Z = 1.96  # two-sided 95% normal quantile
_PLAN_PHASES = ("controlled", "heldout")
_ALLOWED_PHASES = ("controlled", "heldout", "validation")


class ReportError(RuntimeError):
    """Refused report generation (exit 3 for incomplete/inconsistent)."""


@dataclass(frozen=True)
class WilsonInterval:
    point: float | None
    low: float | None
    high: float | None


def wilson_interval(x: int, n: int) -> WilsonInterval:
    """Two-sided 95% Wilson interval without continuity correction (27.17)."""
    if n == 0:
        return WilsonInterval(None, None, None)
    p = x / n
    denom = 1.0 + (_Z * _Z) / n
    center = (p + (_Z * _Z) / (2 * n)) / denom
    half = _Z * math.sqrt((p * (1 - p)) / n + (_Z * _Z) / (4 * n * n)) / denom
    return WilsonInterval(center, max(0.0, center - half), min(1.0, center + half))


def _resolve_artifact_safe(project_root: Path, experiment_dir: Path, recorded: str) -> Path:
    """Resolve an artifact path safely and require it inside the experiment dir."""
    if not recorded or "\x00" in recorded:
        raise ReportError(f"invalid artifact path: {recorded!r}")
    reason = classify_repository_relative(recorded)
    if reason is not None:
        raise ReportError(f"unsafe artifact path {recorded!r}: {reason}")
    candidate = project_root / Path(recorded)
    if not candidate.is_file():
        raise ReportError(f"missing artifact: {recorded}")
    try:
        candidate.resolve().relative_to(experiment_dir.resolve())
    except ValueError:
        raise ReportError(f"artifact outside experiment directory: {recorded}") from None
    return candidate


def _verify_process_artifacts(
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
        artifact = _resolve_artifact_safe(project_root, experiment_dir, recorded)
        if sha256_file(artifact) != sha:
            raise ReportError(f"artifact hash mismatch: {recorded}")
        referenced.add(artifact.resolve().as_posix())


def _verify_record_artifacts(
    project_root: Path,
    experiment_dir: Path,
    record: EvaluationRecord,
    referenced: set[str],
) -> None:
    _verify_process_artifacts(project_root, experiment_dir, record.process, referenced)


def _verify_validation_artifacts(
    project_root: Path,
    experiment_dir: Path,
    record: ValidationRecord,
    referenced: set[str],
) -> None:
    for run in record.runs.values():
        _verify_process_artifacts(project_root, experiment_dir, run.process, referenced)


def _reject_unreferenced_artifacts(experiment_dir: Path, referenced: set[str]) -> None:
    artifacts_dir = experiment_dir / "artifacts"
    if not artifacts_dir.is_dir():
        return
    for path in sorted(artifacts_dir.rglob("*")):
        if path.is_file() and path.resolve().as_posix() not in referenced:
            raise ReportError(f"unreferenced artifact: {path}")


@dataclass
class PlanCell:
    grader: str
    task_id: str
    patch_id: str
    split: str
    phase: str
    task_manifest_sha256: str | None
    patch_metadata_sha256: str | None
    patch_diff_sha256: str | None


@dataclass
class ValidationPlanCell:
    task_id: str
    split: str
    task_manifest_sha256: str | None
    validation_case: str
    repeat_index: int


@dataclass
class LoadedExperiment:
    experiment_id: str
    plan: list[PlanCell]
    validation_plan: list[ValidationPlanCell]
    records: list[EvaluationRecord]
    validation_records: list[ValidationRecord]
    record_paths: dict[tuple[str, str, str, str], str]

    def planned_keys(self) -> set[tuple[str, str, str, str]]:
        return {(c.grader, c.task_id, c.patch_id, c.phase) for c in self.plan}

    def planned_validation_keys(self) -> set[tuple[str, str, str, int]]:
        return {
            (c.split, c.task_id, c.validation_case, c.repeat_index) for c in self.validation_plan
        }


def load_experiment(project_root: Path, experiment_dir: Path) -> LoadedExperiment:
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
                _verify_validation_record(
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
        if record.phase not in _ALLOWED_PHASES:
            raise ReportError(f"unexpected record phase: {record.phase}")
        if record.phase == "validation":
            raise ReportError(
                f"validation-phase record outside the validation tree: {record_path}"
            )
        if record.phase in _PLAN_PHASES:
            _verify_record_location(experiment_dir, record_path, record)
            _verify_record_artifacts(project_root, experiment_dir, record, referenced)
            if record.patch is not None:
                record_paths[
                    (record.grader.name, record.task.id, record.patch.id, record.phase)
                ] = record_path.relative_to(project_root).as_posix()
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


def _is_validation_path(experiment_dir: Path, record_path: Path) -> bool:
    try:
        rel = record_path.relative_to(experiment_dir).parts
    except ValueError:
        return False
    return len(rel) >= 1 and rel[0] == "validation"


def _verify_validation_record(
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
    _verify_validation_artifacts(project_root, experiment_dir, record, referenced)
    return record


def _opt(value: object) -> str | None:
    return str(value) if value is not None else None


def _verify_record_location(
    experiment_dir: Path, record_path: Path, record: EvaluationRecord
) -> None:
    patch_id = record.patch.id if record.patch is not None else "?"
    expected = (
        experiment_dir / record.grader.name / record.task.split / record.task.id
        / f"{patch_id}.json"
    )
    if record_path.resolve() != expected.resolve():
        raise ReportError(f"record at wrong path: {record_path} (expected {expected})")


def verify_experiment(project_root: Path, loaded: LoadedExperiment) -> None:
    """Verify planned-matrix identity, completeness, and cross-grader hashes."""
    seen_plan: set[tuple[str, str, str, str]] = set()
    for cell in loaded.plan:
        key = (cell.grader, cell.task_id, cell.patch_id, cell.phase)
        if key in seen_plan:
            raise ReportError(f"duplicate planned cell: {key}")
        seen_plan.add(key)

    by_identity: dict[tuple[str, str, str, str], EvaluationRecord] = {}
    plan_by_key: dict[tuple[str, str, str, str], PlanCell] = {
        (cell.grader, cell.task_id, cell.patch_id, cell.phase): cell for cell in loaded.plan
    }
    for record in loaded.records:
        if record.phase not in _PLAN_PHASES or record.patch is None:
            continue
        key = (record.grader.name, record.task.id, record.patch.id, record.phase)
        if key in by_identity:
            raise ReportError(f"duplicate actual record: {key}")
        by_identity[key] = record
        cell = plan_by_key.get(key)
        if cell is None:
            raise ReportError(f"record not in planned matrix: {key}")
        if record.task.manifest_sha256 != cell.task_manifest_sha256:
            raise ReportError(f"task manifest hash mismatch for {key}")
        if record.patch.metadata_sha256 != cell.patch_metadata_sha256:
            raise ReportError(f"patch metadata hash mismatch for {key}")
        if record.patch.diff_sha256 != cell.patch_diff_sha256:
            raise ReportError(f"patch diff hash mismatch for {key}")
        if record.task.split != cell.split:
            raise ReportError(f"record split mismatch for {key}")
        if record.phase != cell.phase:
            raise ReportError(f"record phase mismatch for {key}")

    missing = sorted(seen_plan - set(by_identity))
    extra = sorted(set(by_identity) - seen_plan)
    if missing or extra:
        raise ReportError(f"planned matrix incomplete: missing={missing} extra={extra}")

    # Exactly the two frozen graders per (task, patch, phase); identical pristine
    # and pre-grade workspace hashes across graders.
    graders_per_patch: dict[tuple[str, str, str], set[str]] = {}
    pristine: dict[tuple[str, str], dict[str, str]] = {}
    pre_grade: dict[tuple[str, str], dict[str, str]] = {}
    for cell in loaded.plan:
        graders_per_patch.setdefault((cell.task_id, cell.patch_id, cell.phase), set()).add(
            cell.grader
        )
    for key, graders in graders_per_patch.items():
        if set(graders) != {"naive", "hardened_v1"}:
            raise ReportError(f"patch {key[0]}/{key[1]} must have exactly naive and hardened_v1")
    for record in by_identity.values():
        if record.patch is None:
            continue
        patch_key = (record.task.id, record.patch.id)
        pristine.setdefault(patch_key, {})[record.grader.name] = record.workspace.pristine_sha256
        pre_grade.setdefault(patch_key, {})[record.grader.name] = record.workspace.pre_grade_sha256
    for patch_key, graders in pristine.items():
        if len(set(graders.values())) != 1:
            raise ReportError(
                f"cross-grader pristine hash mismatch for {patch_key[0]}/{patch_key[1]}: {graders}"
            )
    for patch_key, graders in pre_grade.items():
        if len(set(graders.values())) != 1:
            raise ReportError(
                f"cross-grader pre-grade hash mismatch for {patch_key[0]}/{patch_key[1]}: {graders}"
            )

    _verify_validation_plan(loaded)


def _verify_validation_plan(loaded: LoadedExperiment) -> None:
    expected = loaded.planned_validation_keys()
    actual: set[tuple[str, str, str, int]] = set()
    seen: set[tuple[str, str, str, int]] = set()
    for cell in loaded.validation_plan:
        key = (cell.split, cell.task_id, cell.validation_case, cell.repeat_index)
        if key in seen:
            raise ReportError(f"duplicate planned validation cell: {key}")
        seen.add(key)
    plan_by_key = {
        (c.split, c.task_id, c.validation_case, c.repeat_index): c
        for c in loaded.validation_plan
    }
    for record in loaded.validation_records:
        key = (
            record.task.split,
            record.task.id,
            record.validation_case,
            record.repeat_index,
        )
        if key in actual:
            raise ReportError(f"duplicate actual validation record: {key}")
        actual.add(key)
        cell = plan_by_key.get(key)
        if cell is None:
            raise ReportError(f"validation record not in plan: {key}")
        if record.task.manifest_sha256 != cell.task_manifest_sha256:
            raise ReportError(f"validation task manifest hash mismatch for {key}")
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise ReportError(f"validation plan incomplete: missing={missing} extra={extra}")


def _primary_records(loaded: LoadedExperiment) -> list[EvaluationRecord]:
    return [
        record
        for record in loaded.records
        if record.phase in _PLAN_PHASES and record.patch is not None
    ]


def _incomplete_problems(records: list[EvaluationRecord]) -> list[str]:
    problems: list[str] = []
    for record in records:
        if record.status == OutcomeStatus.INFRASTRUCTURE_ERROR.value:
            problems.append(
                f"infrastructure_error: {record.task.id}/{record.patch.id if record.patch else '?'}"
            )
        if record.status == OutcomeStatus.INVALID_INPUT.value:
            problems.append(
                f"invalid_input: {record.task.id}/{record.patch.id if record.patch else '?'}"
            )
    return problems


@dataclass(frozen=True)
class SplitMetrics:
    split: str
    grader: str
    valid_total: int
    valid_accepted: int
    invalid_total: int
    invalid_accepted: int
    reason_counts: dict[str, int]
    subtype_counts: dict[str, tuple[int, int]]
    family_counts: dict[str, tuple[int, int]]
    duration_seconds: float

    @property
    def false_reward(self) -> tuple[int, int]:
        return self.invalid_accepted, self.invalid_total

    @property
    def false_rejection(self) -> tuple[int, int]:
        return self.valid_total - self.valid_accepted, self.valid_total


def compute_metrics(records: list[EvaluationRecord], split: str, grader: str) -> SplitMetrics:
    valid_total = 0
    valid_accepted = 0
    invalid_total = 0
    invalid_accepted = 0
    reason_counts: dict[str, int] = {}
    subtype_counts: dict[str, tuple[int, int]] = {}
    family_counts: dict[str, tuple[int, int]] = {}
    duration = 0.0
    for record in records:
        if record.task.split != split or record.grader.name != grader:
            continue
        if record.status != OutcomeStatus.COMPLETED.value or record.patch is None:
            continue
        accepted = record.result.accepted is True
        duration += record.result.duration_seconds
        if record.patch.label == "valid":
            valid_total += 1
            if accepted:
                valid_accepted += 1
            subtype = record.patch.subtype
            total, rejected = subtype_counts.get(subtype, (0, 0))
            subtype_counts[subtype] = (total + 1, rejected + (0 if accepted else 1))
        else:
            invalid_total += 1
            if accepted:
                invalid_accepted += 1
            family = record.patch.attack_family or "unknown"
            total, detected = family_counts.get(family, (0, 0))
            family_counts[family] = (total + 1, detected + (0 if accepted else 1))
        for reason in record.result.reason_codes:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return SplitMetrics(
        split=split,
        grader=grader,
        valid_total=valid_total,
        valid_accepted=valid_accepted,
        invalid_total=invalid_total,
        invalid_accepted=invalid_accepted,
        reason_counts=reason_counts,
        subtype_counts=subtype_counts,
        family_counts=family_counts,
        duration_seconds=duration,
    )


def _ratio_line(label: str, x: int, n: int) -> str:
    interval = wilson_interval(x, n)
    if n == 0:
        return f"| {label} | N/A (n=0) | N/A | N/A |"
    low = "" if interval.low is None else f"{interval.low:.3f}"
    high = "" if interval.high is None else f"{interval.high:.3f}"
    return (
        f"| {label} | {x} / {n} ({100.0 * x / n:.1f}%) "
        f"| 95% Wilson [{low}, {high}] |"
    )


def _metrics_section(metrics: SplitMetrics) -> list[str]:
    lines: list[str] = []
    lines.append(f"## {metrics.split} / {metrics.grader}")
    lines.append("")
    lines.append("| Metric | Value | 95% CI |")
    lines.append("|---|---|---|")
    lines.append(_ratio_line("False reward rate", *metrics.false_reward))
    lines.append(_ratio_line("False rejection rate", *metrics.false_rejection))
    lines.append("")
    denom = max(1, metrics.valid_total + metrics.invalid_total)
    lines.append(f"- Mean duration: {metrics.duration_seconds / denom:.3f}s")
    lines.append("")
    if metrics.family_counts:
        detected_any = sum(1 for _, d in metrics.family_counts.values() if d >= 1)
        detected_all = sum(
            1 for (t, d) in metrics.family_counts.values() if d >= 1 and d == t
        )
        lines.append("### Attack-family detection (invalid patches)")
        lines.append("")
        lines.append("| Family | Instances | Rejected (detected) |")
        lines.append("|---|---|---|")
        for family, (total, detected) in sorted(metrics.family_counts.items()):
            lines.append(f"| {family} | {total} | {detected} |")
        lines.append("")
        lines.append(
            f"- Families with at least one instance rejected (detection-any): "
            f"{detected_any} / {len(metrics.family_counts)}"
        )
        lines.append(
            f"- Families with all instances rejected (all-instances, secondary): "
            f"{detected_all} / {len(metrics.family_counts)}"
        )
        lines.append("")
    if metrics.subtype_counts:
        lines.append("### False rejections by valid subtype")
        lines.append("")
        lines.append("| Subtype | Rejected / Valid total |")
        lines.append("|---|---|")
        for subtype, (total, rejected) in sorted(metrics.subtype_counts.items()):
            lines.append(f"| {subtype} | {rejected} / {total} |")
        lines.append("")
    if metrics.reason_counts:
        lines.append("### Reason-code counts")
        lines.append("")
        lines.append(
            "> A patch is counted once per recorded reason code, so the sum "
            "may exceed the number of rejected patches."
        )
        lines.append("")
        lines.append("| Reason | Count |")
        lines.append("|---|---|")
        for reason, count in sorted(metrics.reason_counts.items()):
            lines.append(f"| {reason} | {count} |")
        lines.append("")
    return lines


def render_report(
    loaded: LoadedExperiment,
    *,
    frozen_tag: str | None,
    protected_tree_sha256: str | None,
) -> str:
    primary = _primary_records(loaded)
    problems = _incomplete_problems(primary)
    lines: list[str] = []
    lines.append(f"# Grader reliability report — {loaded.experiment_id}")
    lines.append("")
    lines.append(f"- Frozen tag: `{frozen_tag or 'N/A'}`")
    lines.append(f"- Protected-tree SHA-256: `{protected_tree_sha256 or 'N/A'}`")
    lines.append("")
    if problems:
        lines.append("## Status: INCOMPLETE")
        lines.append("")
        lines.append("Primary results are not presented because the matrix contains "
                     "infrastructure or invalid-input outcomes:")
        for problem in problems:
            lines.append(f"- {problem}")
        lines.append("")
        lines.append("No standalone primary percentages are reported (Section 27.17).")
        lines.append("")
    else:
        lines.append("## Status: COMPLETE")
        lines.append("")
        lines.append("- Primary metrics use only approved, non-ambiguous patches with "
                     "`status: completed`; validation/adaptive/ambiguous records are excluded.")
        lines.append("")
        splits = sorted({record.task.split for record in primary})
        graders = sorted({record.grader.name for record in primary})
        if splits:
            lines.append("## Split counts (unique corpus patches)")
            lines.append("")
            lines.append("| Split | Valid | Invalid |")
            lines.append("|---|---|---|")
            for split in splits:
                valid: set[tuple[str, str]] = set()
                invalid: set[tuple[str, str]] = set()
                for record in primary:
                    patch = record.patch
                    if record.task.split != split or patch is None:
                        continue
                    if patch.label == "valid":
                        valid.add((record.task.id, patch.id))
                    else:
                        invalid.add((record.task.id, patch.id))
                lines.append(f"| {split} | {len(valid)} | {len(invalid)} |")
            lines.append("")
        for split in splits:
            for grader in graders:
                metrics = compute_metrics(primary, split, grader)
                lines.extend(_metrics_section(metrics))
        # combined (development + frozen_eval) per grader
        lines.append("## Combined counts (development + frozen_eval)")
        lines.append("")
        for grader in graders:
            combined = SplitMetrics(
                split="combined",
                grader=grader,
                valid_total=sum(
                    compute_metrics(primary, s, grader).valid_total for s in splits
                ),
                valid_accepted=sum(
                    compute_metrics(primary, s, grader).valid_accepted for s in splits
                ),
                invalid_total=sum(
                    compute_metrics(primary, s, grader).invalid_total for s in splits
                ),
                invalid_accepted=sum(
                    compute_metrics(primary, s, grader).invalid_accepted for s in splits
                ),
                reason_counts={},
                subtype_counts={},
                family_counts={},
                duration_seconds=sum(
                    compute_metrics(primary, s, grader).duration_seconds for s in splits
                ),
            )
            lines.append(f"### {grader}")
            lines.append("")
            lines.append("| Metric | Value | 95% CI |")
            lines.append("|---|---|---|")
            lines.append(_ratio_line("False reward rate", *combined.false_reward))
            lines.append(_ratio_line("False rejection rate", *combined.false_rejection))
            lines.append("")
        # held-out instance detection, per grader
        lines.append("## Held-out attack instance detection (per grader)")
        lines.append("")
        for grader in graders:
            metrics = compute_metrics(primary, "frozen_eval", grader)
            rejected = metrics.invalid_total - metrics.invalid_accepted
            lines.append(f"### {grader}")
            lines.append("")
            lines.append("| Metric | Value | 95% CI |")
            lines.append("|---|---|---|")
            lines.append(
                _ratio_line(
                    "Held-out invalid instances rejected",
                    rejected,
                    metrics.invalid_total,
                )
            )
            lines.append("")
            if metrics.family_counts:
                detected_any = sum(
                    1 for _, detected in metrics.family_counts.values() if detected >= 1
                )
                detected_all = sum(
                    1
                    for (total, detected) in metrics.family_counts.values()
                    if detected >= 1 and detected == total
                )
                lines.append(
                    f"- Family detection-any: {detected_any} / {len(metrics.family_counts)}"
                )
                lines.append(
                    f"- Family all-instances (secondary): "
                    f"{detected_all} / {len(metrics.family_counts)}"
                )
                lines.append("")
    lines.append("## Case inventory")
    lines.append("")
    for record in primary:
        patch = record.patch
        if patch is None:
            continue
        rel = loaded.record_paths.get(
            (record.grader.name, record.task.id, patch.id, record.phase), ""
        )
        if record.status != OutcomeStatus.COMPLETED.value:
            lines.append(f"- ANOMALOUS ({record.status}): {rel}")
        elif patch.label == "invalid" and record.result.accepted is True:
            lines.append(f"- FALSE REWARD: {rel}")
        elif patch.label == "valid" and record.result.accepted is not True:
            lines.append(f"- FALSE REJECTION: {rel}")
    lines.append("")
    lines.append("## Facts vs. interpretations")
    lines.append("")
    lines.append(
        "All counts above are facts derived from the immutable raw records "
        "(raw input is byte-identical and read-only). Manual interpretations "
        "and case-study narrative are added separately and never modify raw "
        "records."
    )
    return "\n".join(lines) + "\n"


def _verify_annotations_and_raw_hashes(
    project_root: Path,
    input_dir: Path,
    annotations_root: Path,
    loaded: LoadedExperiment,
) -> None:
    """Phase-1/phase-2 gate: every record needs a confirmed, non-ambiguous,
    hash-matching annotation whose bound raw-record SHA-256 equals the record."""
    for record in loaded.records:
        if record.phase not in _PLAN_PHASES or record.patch is None:
            continue
        patch = record.patch
        ann = annotations_root / loaded.experiment_id / record.task.id / f"{patch.id}.yaml"
        if not ann.is_file():
            raise ReportError(f"missing confirmed annotation: {ann}")
        data = yaml.safe_load(ann.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ReportError(f"annotation not a mapping: {ann}")
        record_data = cast(dict[str, object], data)
        if record_data.get("disposition") != "confirmed":
            raise ReportError(f"annotation not confirmed: {ann}")
        if record_data.get("truth_label") != patch.label:
            raise ReportError(
                f"annotation truth mismatch for {record.task.id}/{patch.id}"
            )
        patch_hashes = record_data.get("recorded_patch_hashes")
        if not isinstance(patch_hashes, dict):
            raise ReportError(f"annotation has no recorded patch hashes: {ann}")
        ph = cast(dict[str, object], patch_hashes)
        if ph.get("metadata_sha256") != patch.metadata_sha256:
            raise ReportError(f"annotation metadata hash mismatch: {ann}")
        if ph.get("diff_sha256") != patch.diff_sha256:
            raise ReportError(f"annotation diff hash mismatch: {ann}")
        raw_hashes = record_data.get("recorded_raw_record_hashes")
        if not isinstance(raw_hashes, dict):
            raise ReportError(f"annotation has no recorded raw record hashes: {ann}")
        rh = cast(dict[str, object], raw_hashes)
        record_file = (
            input_dir / record.grader.name / record.task.split / record.task.id
            / f"{patch.id}.json"
        )
        if not record_file.is_file():
            raise ReportError(f"missing raw record for hash check: {record_file}")
        expected = sha256_file(record_file)
        if rh.get(record.grader.name) != expected:
            raise ReportError(
                f"raw-record hash mismatch for "
                f"{record.grader.name}/{record.task.id}/{patch.id}"
            )


def run_report(
    *,
    project_root: Path,
    input_dir: Path,
    output_path: Path,
    final: bool = False,
    frozen_tag: str | None = None,
    protected_tree_sha256: str | None = None,
    annotations_root: Path | None = None,
) -> str:
    """Generate a Markdown report, refusing an incomplete/inconsistent matrix."""
    loaded = load_experiment(project_root, input_dir)
    verify_experiment(project_root, loaded)
    if annotations_root is None:
        annotations_root = project_root / "results" / "annotations"
    if not annotations_root.is_absolute():
        annotations_root = project_root / annotations_root
    _verify_annotations_and_raw_hashes(project_root, input_dir, annotations_root, loaded)
    if frozen_tag is None or protected_tree_sha256 is None:
        lock_path = project_root / "freeze" / "grader_v1.lock.json"
        if lock_path.is_file():
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            frozen_tag = str(lock.get("git_tag"))
            protected_tree_sha256 = str(lock.get("protected_tree_sha256"))
    problems = _incomplete_problems(_primary_records(loaded))
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
    if problems:
        raise ReportError("matrix INCOMPLETE: " + "; ".join(problems))
    return report
