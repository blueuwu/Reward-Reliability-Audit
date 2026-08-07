"""Official ``reproduce`` orchestration (Section 27.15).

``grader-audit reproduce --tasks TASKS_DIR --experiment-id ID`` runs doctor,
manifest validation, task-image build/verification, baseline/gold validation
(both splits), development evaluation, frozen-lock verification, held-out
evaluation, and report generation under one recorder and one reserved plan. It
never creates or moves a freeze tag and never invokes a model, network, or API
key.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from grader_audit.core.annotations import (
    AnnotationMismatchError,
    MissingAnnotationError,
    require_confirmed_annotation,
)
from grader_audit.core.docker_runner import DockerRunner
from grader_audit.core.doctor import run_doctor
from grader_audit.core.freeze import verify_task_image_locks
from grader_audit.core.heldout import (
    FrozenViolationError,
    HeldoutInputError,
    bind_patch_raw_hashes,
    resolve_roots,
    run_heldout,
    verify_frozen_lock,
    verify_heldout_selection,
)
from grader_audit.core.manifests import discover_patches, discover_tasks
from grader_audit.core.models import PatchSplit, Split
from grader_audit.core.orchestrator import (
    check_development_corpus_minimums,
    check_task_corpus,
    git_info,
    plan_cell,
    run_controlled,
    run_validation,
    utc_now,
    validation_plan_cell,
)
from grader_audit.core.paths import ANNOTATIONS_ROOT
from grader_audit.core.recorder import ExperimentRecorder, validate_experiment_id
from grader_audit.core.reporting import run_report
from grader_audit.images import build_task_image

REPRODUCE_GRADERS = ("naive", "hardened_v1")


class ReproduceError(RuntimeError):
    """Reproduction failure; code attribute selects the Section 27.15 exit code."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ReproduceResult:
    experiment_id: str
    report_path: Path
    steps: tuple[str, ...]


def reproduce(
    *,
    project_root: Path,
    tasks_dir: Path,
    raw_results_root: Path,
    experiment_id: str,
    repeat: int = 3,
    annotations_root: Path = ANNOTATIONS_ROOT,
) -> ReproduceResult:
    """Run the full offline pipeline for *experiment_id*."""
    try:
        validate_experiment_id(experiment_id)
    except ValueError as exc:
        raise ReproduceError(str(exc), 2) from None
    steps: list[str] = []

    raw_results_root, annotations_root = resolve_roots(
        project_root, raw_results_root, annotations_root
    )
    recorder = ExperimentRecorder(raw_results_root, experiment_id)
    if recorder.experiment_dir.exists():
        raise ReproduceError(f"experiment already exists: {recorder.experiment_dir}", 2)

    doctor = run_doctor(project_root)
    if not doctor.all_ok:
        failed = [c.description for c in doctor.checks if not c.ok]
        raise ReproduceError(f"doctor failed: {failed}", 4)
    steps.append("doctor")

    tasks = discover_tasks(tasks_dir)
    errors: list[str] = []
    for task in tasks:
        errors += check_task_corpus(task)
    errors += check_development_corpus_minimums(tasks)
    if errors:
        raise ReproduceError("manifest/corpus validation failed: " + "; ".join(errors), 3)
    steps.append("validate-manifests")

    dev_tasks = [task for task in tasks if task.manifest.split is Split.DEVELOPMENT]
    frozen_tasks = [task for task in tasks if task.manifest.split is Split.FROZEN_EVAL]
    lock_errors = verify_task_image_locks(dev_tasks + frozen_tasks)
    if lock_errors:
        raise ReproduceError("image lock validation failed: " + "; ".join(lock_errors), 3)
    steps.append("image-lock-verification")

    # Frozen-lock verification, held-out input checks, and confirmed-dev-
    # annotation preflight must happen before any output is created so the
    # reproduction's own results never fail them.
    try:
        verify_frozen_lock(project_root, "grader-v1-frozen")
        steps.append("frozen-lock-verification")
        for task in dev_tasks:
            for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
                try:
                    require_confirmed_annotation(annotations_root, experiment_id, patch)
                except (MissingAnnotationError, AnnotationMismatchError) as exc:
                    raise ReproduceError(str(exc), 2) from None
        steps.append("dev-annotation-verification")
        if frozen_tasks:
            tag_commit = subprocess.run(
                ["git", "-C", str(project_root), "rev-parse", "grader-v1-frozen^{commit}"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            verify_heldout_selection(
                project_root, tasks_dir, annotations_root, experiment_id, tag_commit
            )
            steps.append("heldout-input-verification")
    except FrozenViolationError as exc:
        raise ReproduceError(str(exc), 5) from None
    except HeldoutInputError as exc:
        raise ReproduceError(str(exc), 2) from None

    runner = DockerRunner()
    images: dict[str, str] = {}
    for task in dev_tasks + frozen_tasks:
        try:
            images[task.manifest.id] = build_task_image(task)
        except Exception as exc:
            raise ReproduceError(
                f"image build/verify failed for {task.manifest.id}: {exc}", 4
            ) from None
    steps.append("build-images")

    plan: list[dict[str, object]] = []
    validation_plan: list[dict[str, object]] = []
    for task in dev_tasks + frozen_tasks:
        for case in ("baseline", "gold"):
            for idx in range(1, repeat + 1):
                validation_plan.append(validation_plan_cell(task, case, idx))
    for task in dev_tasks:
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
            for grader in REPRODUCE_GRADERS:
                plan.append(plan_cell(grader, task, patch, phase="controlled"))
    for task in frozen_tasks:
        for patch in discover_patches(task.task_dir, PatchSplit.FROZEN_EVAL):
            for grader in REPRODUCE_GRADERS:
                plan.append(plan_cell(grader, task, patch, phase="heldout"))
    recorder.write_metadata(
        {
            "schema_version": "1.0",
            "experiment_id": experiment_id,
            "timestamp_utc": utc_now(),
            "git": git_info(project_root).model_dump(mode="json"),
            "plan": {"controlled": plan, "validation": validation_plan},
        }
    )

    for task in dev_tasks:
        summary = run_validation(
            task,
            repeat=repeat,
            recorder=recorder,
            runner=runner,
            image=images[task.manifest.id],
            project_root=project_root,
            split=Split.DEVELOPMENT.value,
        )
        if not summary.stable:
            raise ReproduceError(f"baseline/gold unstable for {task.manifest.id}", 3)
    steps.append("validate-development")

    for task in frozen_tasks:
        summary = run_validation(
            task,
            repeat=repeat,
            recorder=recorder,
            runner=runner,
            image=images[task.manifest.id],
            project_root=project_root,
            split=Split.FROZEN_EVAL.value,
        )
        if not summary.stable:
            raise ReproduceError(f"baseline/gold unstable for {task.manifest.id}", 3)
    steps.append("validate-frozen-eval")

    for task in dev_tasks:
        run_controlled(
            task,
            recorder=recorder,
            runner=runner,
            image=images[task.manifest.id],
            project_root=project_root,
            graders=list(REPRODUCE_GRADERS),
        )
    steps.append("run-controlled")

    if frozen_tasks:
        run_heldout(
            project_root=project_root,
            tasks_dir=tasks_dir,
            raw_results_root=raw_results_root,
            annotations_root=annotations_root,
            experiment_id=experiment_id,
            graders=REPRODUCE_GRADERS,
            require_tag="grader-v1-frozen",
            runner=runner,
            refuse_existing=False,
            write_plan=False,
        )
        steps.append("run-heldout")

    # Phase-2 mechanical binding: append raw record SHA-256s to every confirmed
    # annotation for dev-controlled and held-out patches, before the report.
    for task in dev_tasks:
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
            bind_patch_raw_hashes(
                raw_results_root,
                annotations_root,
                experiment_id,
                REPRODUCE_GRADERS,
                task.manifest.split.value,
                task.manifest.id,
                patch.manifest.id,
            )
    for task in frozen_tasks:
        for patch in discover_patches(task.task_dir, PatchSplit.FROZEN_EVAL):
            bind_patch_raw_hashes(
                raw_results_root,
                annotations_root,
                experiment_id,
                REPRODUCE_GRADERS,
                task.manifest.split.value,
                task.manifest.id,
                patch.manifest.id,
            )
    steps.append("bind-raw-record-hashes")

    report_path = raw_results_root.parent / "summaries" / f"{experiment_id}.md"
    run_report(
        project_root=project_root,
        input_dir=recorder.experiment_dir,
        output_path=report_path,
        annotations_root=annotations_root,
    )
    steps.append("report")
    return ReproduceResult(experiment_id=experiment_id, report_path=report_path, steps=tuple(steps))
