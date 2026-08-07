"""Offline v2 evaluation driver (hardening §6, regression matrix and held-out).

Scores every requested patch from clean materialized workspaces through the
shared core (DockerRunner on the host, exactly like the frozen controlled
pipeline), writes v2 records under ``results/raw/v2-<experiment-id>/`` with a
separate schema, and resolves truth labels from confirmed v1 annotations when
available (never from grader rewards).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from grader_audit.core.docker_runner import ContainerStartError, DockerRunner
from grader_audit.core.manifests import (
    LoadedPatch,
    LoadedTask,
    discover_patches,
    discover_tasks,
)
from grader_audit.core.models import PatchSplit, Split, load_patch_manifest_yaml
from grader_audit.core.orchestrator import git_info, prepare_task
from grader_audit.core.workspace import WorkspaceManager
from grader_audit.images import resolve_task_image
from grader_v2.grading.evaluator import (
    HardenedV2Context,
    HardenedV2Evaluator,
    V2EvaluatorResult,
)
from grader_v2.grading.records import (
    V2EvaluationRecord,
    V2Experiment,
    V2Git,
    V2Outcome,
    V2SubOutcome,
    V2Truth,
    V2WorkspaceHashes,
    load_v2_experiment,
    utc_now,
    validate_v2_experiment_id,
    write_v2_record,
)

GRADER_V2 = "hardened_v2"

#: Adaptive attempt patch directories at the repository root (contract §7.4).
ADAPTIVE_ATTEMPTS_REL = Path("adaptive_attempts")


@dataclass(frozen=True)
class ScoredPatch:
    task: LoadedTask
    patch: LoadedPatch | None
    split: str
    record: V2EvaluationRecord


@dataclass(frozen=True)
class V2ExperimentResult:
    experiment: V2Experiment
    rows: list[ScoredPatch]


class V2DriverError(RuntimeError):
    """v2 experiment failure; ``code`` selects the process exit code."""

    def __init__(self, message: str, code: int = 4) -> None:
        super().__init__(message)
        self.code = code


def _truth_from_annotation(
    annotations_root: Path, experiment_id: str, task: LoadedTask, patch_id: str
) -> V2Truth | None:
    ann_path = annotations_root / experiment_id / task.manifest.id / f"{patch_id}.yaml"
    if not ann_path.is_file():
        return None
    try:
        payload = yaml.safe_load(ann_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise V2DriverError(
            f"unreadable annotation {ann_path}: {exc}", code=2
        ) from None
    if not isinstance(payload, dict):
        raise V2DriverError(f"invalid annotation {ann_path}: not a mapping", code=2)
    payload_map = cast(dict[str, object], payload)
    label = payload_map.get("truth_label")
    if not isinstance(label, str) or label not in ("valid", "invalid"):
        raise V2DriverError(
            f"annotation {ann_path} has no usable truth_label", code=2
        )
    disposition = payload_map.get("disposition")
    if disposition != "confirmed":
        raise V2DriverError(
            f"annotation {ann_path} is not confirmed (disposition={disposition!r})",
            code=2,
        )
    reviewer = payload_map.get("reviewer")
    return V2Truth(
        label=label,
        source=f"confirmed-annotation:{experiment_id}",
        reviewer=str(reviewer) if reviewer else None,
    )


def _truth_for_patch(
    annotations_root: Path,
    annotation_experiment_ids: tuple[str, ...],
    task: LoadedTask,
    patch: LoadedPatch | None,
) -> V2Truth:
    if patch is None:
        return V2Truth(label="invalid", source="baseline-control", reviewer="none")
    for experiment_id in annotation_experiment_ids:
        confirmed = _truth_from_annotation(
            annotations_root, experiment_id, task, patch.manifest.id
        )
        if confirmed is not None:
            return confirmed
    return V2Truth(
        label=patch.manifest.label.value,
        source="patch-manifest",
        reviewer="none",
    )


def _load_adaptive_patches(project_root: Path, task: LoadedTask) -> list[LoadedPatch]:
    """Load adaptive attempts targeting *task* from ``adaptive_attempts/``."""
    root = project_root / ADAPTIVE_ATTEMPTS_REL
    if not root.is_dir():
        return []
    patches: list[LoadedPatch] = []
    for attempt_dir in sorted(root.iterdir()):
        if not attempt_dir.is_dir():
            continue
        meta_path = attempt_dir / "patch.yaml"
        diff_path = attempt_dir / "change.patch"
        if not meta_path.is_file() or not diff_path.is_file():
            continue
        try:
            meta = load_patch_manifest_yaml(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise V2DriverError(
                f"invalid adaptive patch manifest {meta_path}: {exc}", code=2
            ) from None
        if meta.task_id != task.manifest.id:
            continue
        diff_bytes = diff_path.read_bytes()
        from grader_audit.core.hashing import sha256_bytes

        patches.append(
            LoadedPatch(
                patch_dir=attempt_dir,
                manifest=meta,
                metadata_sha256=sha256_bytes(meta_path.read_bytes()),
                diff_sha256=sha256_bytes(diff_bytes),
                diff_bytes=diff_bytes,
            )
        )
    return patches


def _patches_for_split(
    project_root: Path,
    task: LoadedTask,
    split: str,
) -> list[LoadedPatch]:
    if split == Split.DEVELOPMENT.value:
        return discover_patches(task.task_dir, PatchSplit.DEVELOPMENT)
    if split == Split.FROZEN_EVAL.value:
        return discover_patches(task.task_dir, PatchSplit.FROZEN_EVAL)
    if split == "adaptive":
        return _load_adaptive_patches(project_root, task)
    raise V2DriverError(f"unsupported v2 split {split!r}", code=2)


def _run_one(
    *,
    project_root: Path,
    task: LoadedTask,
    patch: LoadedPatch | None,
    split: str,
    image: str,
    runner: DockerRunner,
    experiment_id: str,
    truth: V2Truth,
    fixed_seed: int | None,
) -> V2EvaluationRecord:
    runtime = prepare_task(task)
    manager = WorkspaceManager(task)
    workspace = manager.materialize()
    try:
        pristine = workspace.pristine_snapshot
        if patch is not None:
            apply_result = manager.apply_patch_to(workspace, patch)
            if not apply_result.ok:
                return _invalid_input_record(
                    task=task,
                    patch=patch,
                    split=split,
                    experiment_id=experiment_id,
                    truth=truth,
                    pristine_sha256=pristine.sha256,
                    error=f"patch_apply_failed: {apply_result.error}",
                    git=git_info(project_root),
                )
            pre_grade = workspace.snapshot()
        else:
            pre_grade = pristine
        context = HardenedV2Context(
            manifest=task.manifest,
            workspace_host=workspace.root,
            pristine_snapshot=pristine,
            pre_grade_snapshot=pre_grade,
            authoritative_tests_host=runtime.task.task_dir
            / task.manifest.grading.hardened_v1.tests_dir,
            expected_grader_assets_hash=runtime.authoritative_hash,
            image=image,
            memory_mb=task.manifest.runtime.memory_mb,
            pids_limit=task.manifest.runtime.pids_limit,
            seed=fixed_seed,
        )
        result: V2EvaluatorResult = HardenedV2Evaluator().evaluate(context, runner)
        post = workspace.snapshot()
        git = git_info(project_root)
        outcome = result.outcome
        return V2EvaluationRecord(
            experiment_id=experiment_id,
            timestamp_utc=utc_now(),
            task_id=task.manifest.id,
            split=split,
            patch_id=patch.manifest.id if patch is not None else "baseline",
            patch_diff_sha256=patch.diff_sha256 if patch is not None else "baseline",
            truth=truth,
            outcome=V2Outcome(
                status=outcome.status.value,
                reward=outcome.reward,
                reason_codes=list(outcome.reason_codes),
                warnings=list(outcome.warnings),
                error=(
                    {"code": outcome.error.code, "message": outcome.error.message}
                    if outcome.error is not None
                    else None
                ),
            ),
            v1_outcome=V2SubOutcome(
                status=result.v1_outcome.status.value,
                reward=result.v1_outcome.reward,
                reason_codes=list(result.v1_outcome.reason_codes),
            ),
            semantic=result.semantic,
            workspace=V2WorkspaceHashes(
                pristine_sha256=pristine.sha256,
                pre_grade_sha256=pre_grade.sha256,
                post_grade_sha256=post.sha256,
            ),
            git=V2Git(data_commit=git.data_commit, worktree_dirty=git.worktree_dirty),
            duration_seconds=outcome.duration_seconds,
        )
    except ContainerStartError as exc:
        raise V2DriverError(
            f"infrastructure error grading {task.manifest.id}: {exc}", code=4
        ) from None
    finally:
        manager.finalize_and_destroy(workspace)


def _invalid_input_record(
    *,
    task: LoadedTask,
    patch: LoadedPatch,
    split: str,
    experiment_id: str,
    truth: V2Truth,
    pristine_sha256: str,
    error: str,
    git: object,
) -> V2EvaluationRecord:
    return V2EvaluationRecord(
        experiment_id=experiment_id,
        timestamp_utc=utc_now(),
        task_id=task.manifest.id,
        split=split,
        patch_id=patch.manifest.id,
        patch_diff_sha256=patch.diff_sha256,
        truth=truth,
        outcome=V2Outcome(
            status="invalid_input",
            error={"code": "patch_apply_failed", "message": error},
        ),
        v1_outcome=V2SubOutcome(status="invalid_input"),
        semantic=None,
        workspace=V2WorkspaceHashes(
            pristine_sha256=pristine_sha256,
            pre_grade_sha256=pristine_sha256,
            post_grade_sha256=pristine_sha256,
        ),
        git=V2Git(
            data_commit=str(getattr(git, "data_commit", git)),
            worktree_dirty=bool(getattr(git, "worktree_dirty", False)),
        ),
    )


def run_v2_experiment(
    *,
    project_root: Path,
    tasks_dir: Path,
    raw_results_root: Path,
    annotations_root: Path,
    annotation_experiment_ids: tuple[str, ...],
    experiment_id: str,
    splits: tuple[str, ...],
    include_baseline: bool,
    fixed_seed: int | None = None,
) -> V2ExperimentResult:
    """Score the requested matrix under hardened_v2 and persist v2 records."""
    validate_v2_experiment_id(experiment_id)
    if not raw_results_root.is_absolute():
        raw_results_root = project_root / raw_results_root
    if not annotations_root.is_absolute():
        annotations_root = project_root / annotations_root
    experiment_dir = raw_results_root / experiment_id
    if experiment_dir.exists():
        raise V2DriverError(f"v2 experiment already exists: {experiment_dir}", code=2)

    runner = DockerRunner()
    tasks = discover_tasks(tasks_dir)
    if not tasks:
        raise V2DriverError(f"no tasks found under {tasks_dir}", code=2)
    rows: list[ScoredPatch] = []
    for task in tasks:
        try:
            image = resolve_task_image(task)
        except Exception as exc:
            raise V2DriverError(
                f"task image resolution failed for {task.manifest.id}: {exc}",
                code=4,
            ) from None
        patches: list[LoadedPatch | None] = []
        for split in splits:
            patches.extend(_patches_for_split(project_root, task, split))
        seen: set[str] = set()
        unique: list[LoadedPatch | None] = []
        for patch in patches:
            if patch is None:
                unique.append(patch)
                continue
            key = f"{patch.manifest.split.value}/{patch.manifest.id}"
            if key not in seen:
                if patch.manifest.split.value in splits:
                    unique.append(patch)
                seen.add(key)
        if include_baseline:
            unique.append(None)
        for patch in unique:
            split = (
                "baseline"
                if patch is None
                else _split_for_patch(patch.manifest.split.value)
            )
            truth = _truth_for_patch(
                annotations_root, annotation_experiment_ids, task, patch
            )
            record = _run_one(
                project_root=project_root,
                task=task,
                patch=patch,
                split=split,
                image=image,
                runner=runner,
                experiment_id=experiment_id,
                truth=truth,
                fixed_seed=fixed_seed,
            )
            write_v2_record(record, experiment_dir)
            rows.append(
                ScoredPatch(task=task, patch=patch, split=split, record=record)
            )
            _progress(f"  {task.manifest.id}/{record.patch_id}: "
                      f"{record.outcome.status} reward={record.outcome.reward} "
                      f"codes={record.outcome.reason_codes}")
    experiment = load_v2_experiment(experiment_dir)
    return V2ExperimentResult(experiment=experiment, rows=rows)


def _split_for_patch(patch_split: str) -> str:
    if patch_split == "adaptive":
        return "adaptive"
    if patch_split == "frozen_eval":
        return "frozen_eval"
    return "development"


def _progress(message: str) -> None:
    print(message, file=sys.stderr)
