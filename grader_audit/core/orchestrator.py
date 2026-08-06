"""Controlled audit orchestration (Sections 27.9-27.10, 27.14-27.16).

The orchestrator materializes a distinct fresh workspace for every
``(task, patch, grader, repeat)``, applies exactly one patch, grades with the
shared evaluators, captures deterministic snapshots, and persists atomic
no-overwrite records. Naive and hardened records for one patch MUST have
identical pre-grade workspace hashes; the orchestrator enforces this.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from grader_audit.core.grader_assets import hash_grader_assets
from grader_audit.core.hashing import hash_tree
from grader_audit.core.manifests import LoadedPatch, LoadedTask, discover_patches
from grader_audit.core.models import PatchSplit
from grader_audit.core.outcomes import (
    Changes,
    EnvironmentInfo,
    ErrorInfo,
    EvaluationOutcome,
    GitInfo,
    GraderInfo,
    OutcomeStatus,
    PatchInfo,
    Phase,
    TaskInfo,
    TestEvidence,
    WorkspaceHashes,
)
from grader_audit.core.process import EvaluatorResult, ProcessResult, Runner, process_info
from grader_audit.core.provenance import git_provenance, hud_version, pytest_version, python_version
from grader_audit.core.reason_codes import serialize_reason_codes
from grader_audit.core.recorder import ExperimentRecorder
from grader_audit.core.results import (
    EvaluationRecord,
    ValidationRecord,
    ValidationRun,
)
from grader_audit.core.scope import validate_generated_globs
from grader_audit.core.snapshots import WorkspaceSnapshot
from grader_audit.core.workspace import Workspace, WorkspaceManager
from grader_audit.grading.naive.evaluator import NaiveContext, NaiveEvaluator
from grader_audit.grading.v1.evaluator import HardenedV1Context, HardenedV1Evaluator
from grader_audit.grading.v1.evidence import evaluate_evidence, load_report
from grader_audit.grading.v1.suite import run_test_suite
from grader_audit.oracle.evaluator import OracleContext, OracleEvaluator

GRADER_NAIVE = "naive"
GRADER_HARDENED_V1 = "hardened_v1"
GRADER_ORACLE = "oracle"

_GRADER_VERSION = "v1"


@dataclass(frozen=True)
class TaskRuntime:
    task: LoadedTask
    authoritative_hash: str
    oracle_hash: str


class CrossGraderWorkspaceMismatchError(RuntimeError):
    """Raised when naive/hardened pre-grade hashes differ for one patch."""


class CorpusCheckError(RuntimeError):
    """Raised when a task fails corpus validation (exit 3)."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def build_environment_info(image_digest: str) -> EnvironmentInfo:
    return EnvironmentInfo(
        python=python_version(),
        pytest=pytest_version(),
        hud=hud_version(),
        docker_image_digest=image_digest,
    )


def git_info(project_root: Path) -> GitInfo:
    provenance = git_provenance(project_root)
    return GitInfo(
        data_commit=provenance.data_commit,
        grader_frozen_commit=None,
        worktree_dirty=provenance.worktree_dirty,
    )


def task_info(task: LoadedTask) -> TaskInfo:
    return TaskInfo(
        id=task.manifest.id, split=task.manifest.split.value, manifest_sha256=task.manifest_sha256
    )


def patch_info(patch: LoadedPatch) -> PatchInfo:
    return PatchInfo(
        id=patch.manifest.id,
        label=patch.manifest.label.value,
        subtype=patch.manifest.subtype.value,
        attack_family=patch.manifest.attack_family,
        metadata_sha256=patch.metadata_sha256,
        diff_sha256=patch.diff_sha256,
    )


def workspace_hashes(
    pristine: WorkspaceSnapshot, pre: WorkspaceSnapshot, post: WorkspaceSnapshot
) -> WorkspaceHashes:
    return WorkspaceHashes(
        pristine_sha256=pristine.sha256,
        pre_grade_sha256=pre.sha256,
        post_grade_sha256=post.sha256,
    )


def prepare_task(task: LoadedTask) -> TaskRuntime:
    """Hash trusted grader assets for one task and validate generated globs."""
    validate_generated_globs(task.manifest)
    authoritative = task.task_dir / task.manifest.grading.hardened_v1.tests_dir
    oracle = task.task_dir / task.manifest.grading.oracle.tests_dir
    return TaskRuntime(
        task=task,
        authoritative_hash=hash_grader_assets(authoritative),
        oracle_hash=hash_grader_assets(oracle),
    )


def evaluate_grader(
    grader_name: str,
    runtime: TaskRuntime,
    workspace: Workspace,
    pre_grade_snapshot: WorkspaceSnapshot,
    *,
    runner: Runner,
    image: str,
) -> EvaluatorResult:
    """Run one grader on a patched workspace using the shared core."""
    manifest = runtime.task.manifest
    if grader_name == GRADER_NAIVE:
        context = NaiveContext(
            argv=manifest.grading.naive.argv,
            cwd=manifest.grading.naive.cwd,
            timeout_seconds=float(manifest.grading.naive.timeout_seconds),
            workspace_host=workspace.root,
            image=image,
            memory_mb=manifest.runtime.memory_mb,
            pids_limit=manifest.runtime.pids_limit,
        )
        return NaiveEvaluator().evaluate(context, runner)
    if grader_name == GRADER_HARDENED_V1:
        context = HardenedV1Context(
            manifest=manifest,
            workspace_host=workspace.root,
            pristine_snapshot=workspace.pristine_snapshot,
            pre_grade_snapshot=pre_grade_snapshot,
            authoritative_tests_host=runtime.task.task_dir / manifest.grading.hardened_v1.tests_dir,
            expected_grader_assets_hash=runtime.authoritative_hash,
            image=image,
            memory_mb=manifest.runtime.memory_mb,
            pids_limit=manifest.runtime.pids_limit,
        )
        return HardenedV1Evaluator().evaluate(context, runner)
    raise ValueError(f"unsupported grader: {grader_name}")


def run_oracle_for_labeling(
    runtime: TaskRuntime, workspace: Workspace, *, runner: Runner, image: str
):
    """Run the offline oracle on a patched workspace (labeling facility)."""
    manifest = runtime.task.manifest
    context = OracleContext(
        manifest=manifest,
        workspace_host=workspace.root,
        oracle_tests_host=runtime.task.task_dir / manifest.grading.oracle.tests_dir,
        expected_oracle_assets_hash=runtime.oracle_hash,
        image=image,
        memory_mb=manifest.runtime.memory_mb,
        pids_limit=manifest.runtime.pids_limit,
    )
    return OracleEvaluator().evaluate(context, runner)


# ---------------------------------------------------------------------------
# Corpus validation (schema + cross-reference + patch application)
# ---------------------------------------------------------------------------


def check_task_corpus(task: LoadedTask) -> list[str]:
    """Return corpus-validation error strings (Section 27.6/27.7 checks)."""
    errors: list[str] = []
    manifest = task.manifest
    baseline = task.task_dir / manifest.workspace.source_dir
    if not baseline.is_dir():
        errors.append(f"{task.task_dir.name}: baseline dir missing: {baseline}")
    elif hash_tree(baseline) != manifest.source.vendored_tree_sha256:
        errors.append(
            f"{task.task_dir.name}: vendored_tree_sha256 does not match the baseline tree "
            "(rerun build to refresh the lock)"
        )
    for name in (
        "visible_tests",
        manifest.grading.hardened_v1.tests_dir,
        manifest.grading.oracle.tests_dir,
    ):
        if not (task.task_dir / name).is_dir():
            errors.append(f"{task.task_dir.name}: required directory missing: {name}")

    patches = discover_patches(task.task_dir, PatchSplit.DEVELOPMENT)
    patch_by_id = {patch.manifest.id: patch for patch in patches}
    gold_id = manifest.validation.gold_patch_id
    gold = patch_by_id.get(gold_id)
    if gold is None:
        errors.append(f"{task.task_dir.name}: gold patch {gold_id!r} not found under patches/valid")
    elif gold.manifest.subtype.value != "gold":
        errors.append(f"{task.task_dir.name}: gold patch {gold_id!r} must have subtype 'gold'")

    for patch in patches:
        if patch.manifest.task_id != manifest.id:
            errors.append(
                f"{task.task_dir.name}/{patch.manifest.id}: patch.task_id does not "
                "match the task id"
            )
        if patch.manifest.split.value != manifest.split.value:
            errors.append(
                f"{task.task_dir.name}/{patch.manifest.id}: patch split does not "
                "match the task split"
            )
        apply_error = check_patch_applies(task, patch)
        if apply_error is not None:
            errors.append(
                f"{task.task_dir.name}/{patch.manifest.id}: patch does not apply: {apply_error}"
            )

    if len(manifest.grading.hardened_v1.expected_nodeids) != len(
        set(manifest.grading.hardened_v1.expected_nodeids)
    ):
        errors.append(f"{task.task_dir.name}: duplicate hardened expected node IDs")
    if len(manifest.grading.oracle.expected_nodeids) != len(
        set(manifest.grading.oracle.expected_nodeids)
    ):
        errors.append(f"{task.task_dir.name}: duplicate oracle expected node IDs")
    try:
        validate_generated_globs(manifest)
    except ValueError as exc:
        errors.append(f"{task.task_dir.name}: {exc}")
    return errors


def check_patch_applies(task: LoadedTask, patch: LoadedPatch) -> str | None:
    """Check a patch applies cleanly to a fresh materialized baseline."""
    from grader_audit.core.patches import apply_patch

    manager = WorkspaceManager(task)
    workspace = manager.materialize()
    try:
        result = apply_patch(workspace.root, patch.diff_bytes)
        if not result.ok:
            return result.error
        return None
    finally:
        manager.finalize_and_destroy(workspace)


# ---------------------------------------------------------------------------
# Corpus-wide minimums (Sections 27.5 and 27.15)
# ---------------------------------------------------------------------------


def check_development_corpus_minimums(tasks: list[LoadedTask]) -> list[str]:
    """Enforce the Section 27.5 development allocation on *tasks*.

    The development split must contain exactly three tasks, at least five
    approved-shape valid patches (exactly one gold per task, at least three
    non-canonical alternatives, at least one multi-file unusual_valid), at least
    twelve invalid patches, and at least four attack families.
    """
    from grader_audit.core.models import PatchLabel, Split

    errors: list[str] = []
    dev = [task for task in tasks if task.manifest.split is Split.DEVELOPMENT]
    if len(dev) != 3:
        errors.append(f"development split must contain exactly 3 tasks, found {len(dev)}")

    valid_patches: list[LoadedPatch] = []
    invalid_patches: list[LoadedPatch] = []
    families: set[str] = set()
    for task in dev:
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
            if patch.manifest.label is PatchLabel.VALID:
                valid_patches.append(patch)
            else:
                invalid_patches.append(patch)
                if patch.manifest.attack_family:
                    families.add(patch.manifest.attack_family)

    if len(valid_patches) < 5:
        errors.append(
            f"development corpus has {len(valid_patches)} valid patches; at least 5 required"
        )
    if len(invalid_patches) < 12:
        errors.append(
            f"development corpus has {len(invalid_patches)} invalid patches; at least 12 required"
        )
    if len(families) < 4:
        errors.append(
            f"development corpus has {len(families)} attack families; at least 4 required"
        )

    for task in dev:
        golds = [
            patch
            for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT)
            if patch.manifest.subtype.value == "gold"
        ]
        if len(golds) != 1:
            errors.append(
                f"{task.manifest.id}: must have exactly one gold patch, found {len(golds)}"
            )

    non_gold = [patch for patch in valid_patches if patch.manifest.subtype.value != "gold"]
    if len(non_gold) < 3:
        errors.append(
            f"development corpus has {len(non_gold)} non-canonical valid patches; "
            "at least 3 required"
        )
    unusual = [
        patch for patch in non_gold if patch.manifest.subtype.value == "unusual_valid"
    ]
    if not unusual:
        errors.append(
            "development corpus must include at least one unusual_valid (multi-file) alternative"
        )
    return errors


# ---------------------------------------------------------------------------
# Baseline/gold validation
# ---------------------------------------------------------------------------


@dataclass
class ValidationSummary:
    records: list[ValidationRecord]
    stable: bool
    errors: list[str]


def run_validation(
    task: LoadedTask,
    *,
    repeat: int,
    recorder: ExperimentRecorder,
    runner: Runner,
    image: str,
    project_root: Path,
    split: str,
) -> ValidationSummary:
    runtime = prepare_task(task)
    records: list[ValidationRecord] = []
    errors: list[str] = []
    stable = True
    for case in ("baseline", "gold"):
        for repeat_index in range(1, repeat + 1):
            record = _run_validation_repeat(
                runtime, case, repeat_index, recorder, runner, image, project_root
            )
            records.append(record)
            recorder.write_validation_record(
                record,
                split=split,
                task_id=task.manifest.id,
                validation_case=case,
                repeat_index=repeat_index,
            )
            if not record.stable:
                stable = False
    if not stable:
        errors.append(f"{task.manifest.id}: baseline/gold outcomes varied across repeats")
    return ValidationSummary(records=records, stable=stable, errors=errors)


def _run_validation_repeat(
    runtime: TaskRuntime,
    case: str,
    repeat_index: int,
    recorder: ExperimentRecorder,
    runner: Runner,
    image: str,
    project_root: Path,
) -> ValidationRecord:
    run_id = uuid.uuid4().hex
    runs: dict[str, ValidationRun] = {}
    for grader_name in (GRADER_NAIVE, GRADER_HARDENED_V1, GRADER_ORACLE):
        run = _run_validation_grader(runtime, case, grader_name, runner, image)
        runs[grader_name] = run
    stable = _validation_case_stable(case, runs, runtime)
    return ValidationRecord(
        schema_version="1.0",
        run_id=run_id,
        experiment_id=recorder.experiment_id,
        timestamp_utc=utc_now(),
        git=git_info(project_root),
        task=task_info(runtime.task),
        environment=build_environment_info(image),
        validation_case=case,
        repeat_index=repeat_index,
        runs=runs,
        stable=stable,
    )


def _run_validation_grader(
    runtime: TaskRuntime,
    case: str,
    grader_name: str,
    runner: Runner,
    image: str,
) -> ValidationRun:
    manager = WorkspaceManager(runtime.task)
    workspace = manager.materialize()
    try:
        pre_grade: WorkspaceSnapshot | None = None
        if case == "gold":
            gold_patch = _find_gold_patch(runtime.task)
            result = manager.apply_patch_to(workspace, gold_patch)
            if not result.ok:
                return _invalid_validation_run(
                    grader_name, workspace, f"gold patch apply failed: {result.error}"
                )
            pre_grade = workspace.snapshot()

        if grader_name == GRADER_NAIVE:
            return _run_naive_validation(runtime, workspace, pre_grade, image, runner)
        if grader_name == GRADER_HARDENED_V1:
            return _run_authoritative_validation(runtime, workspace, pre_grade, image, runner)
        return _run_oracle_validation(runtime, workspace, pre_grade, image, runner)
    finally:
        manager.finalize_and_destroy(workspace)


def _find_gold_patch(task: LoadedTask) -> LoadedPatch:
    patches = discover_patches(task.task_dir, PatchSplit.DEVELOPMENT)
    for patch in patches:
        if patch.manifest.id == task.manifest.validation.gold_patch_id:
            return patch
    raise CorpusCheckError(
        f"gold patch {task.manifest.validation.gold_patch_id!r} not found for {task.manifest.id}"
    )


def _invalid_validation_run(grader_name: str, workspace: Workspace, message: str) -> ValidationRun:
    return ValidationRun(
        grader=GraderInfo(name=grader_name, version=_GRADER_VERSION),
        status=OutcomeStatus.INVALID_INPUT.value,
        reward=None,
        accepted=None,
        reason_codes=[],
        error=ErrorInfo(code="patch_apply_failed", message=message),
        changes=Changes(),
        workspace=workspace_hashes(
            workspace.pristine_snapshot, workspace.pristine_snapshot, workspace.pristine_snapshot
        ),
        duration_seconds=0.0,
    )


def _run_naive_validation(
    runtime: TaskRuntime,
    workspace: Workspace,
    pre_grade: WorkspaceSnapshot | None,
    image: str,
    runner: Runner,
) -> ValidationRun:
    manifest = runtime.task.manifest
    context = NaiveContext(
        argv=manifest.grading.naive.argv,
        cwd=manifest.grading.naive.cwd,
        timeout_seconds=float(manifest.grading.naive.timeout_seconds),
        workspace_host=workspace.root,
        image=image,
        memory_mb=manifest.runtime.memory_mb,
        pids_limit=manifest.runtime.pids_limit,
    )
    result = NaiveEvaluator().evaluate(context, runner)
    if pre_grade is None:
        pre_grade = workspace.pristine_snapshot
    post = workspace.snapshot()
    return _validation_run_from_outcome(GRADER_NAIVE, result.outcome, workspace, pre_grade, post)


def _run_authoritative_validation(
    runtime: TaskRuntime,
    workspace: Workspace,
    pre_grade: WorkspaceSnapshot | None,
    image: str,
    runner: Runner,
) -> ValidationRun:
    manifest = runtime.task.manifest
    pre_asset_hash = hash_grader_assets(
        runtime.task.task_dir / manifest.grading.hardened_v1.tests_dir
    )
    suite = run_test_suite(
        workspace_host=workspace.root,
        grader_root="/opt/grader",
        tests_host=runtime.task.task_dir / manifest.grading.hardened_v1.tests_dir,
        expected_nodeids=manifest.grading.hardened_v1.expected_nodeids,
        source_roots=manifest.workspace.source_roots,
        image=image,
        memory_mb=manifest.runtime.memory_mb,
        pids_limit=manifest.runtime.pids_limit,
        timeout_seconds=manifest.grading.hardened_v1.timeout_seconds,
        runner=runner,
    )
    post_asset_hash = hash_grader_assets(
        runtime.task.task_dir / manifest.grading.hardened_v1.tests_dir
    )
    if suite.result.timed_out:
        outcome = EvaluationOutcome(
            status=OutcomeStatus.COMPLETED,
            reward=0.0,
            reason_codes=["timeout"],
            test_evidence=TestEvidence(state="missing"),
        )
        node_outcomes: dict[str, str] = {}
    elif pre_asset_hash != post_asset_hash or pre_asset_hash != runtime.authoritative_hash:
        outcome = EvaluationOutcome(
            status=OutcomeStatus.INFRASTRUCTURE_ERROR,
            reward=None,
            reason_codes=["grader_asset_hash_mismatch"],
            error=ErrorInfo(
                code="grader_asset_hash_mismatch", message="grader asset hash mismatch"
            ),
        )
        node_outcomes = {}
    else:
        try:
            parsed = load_report(suite.report_path)
            node_outcomes = dict(parsed.node_outcomes)
        except ValueError:
            outcome = EvaluationOutcome(
                status=OutcomeStatus.COMPLETED,
                reward=0.0,
                reason_codes=["test_evidence_missing"],
                test_evidence=TestEvidence(state="missing"),
            )
            node_outcomes = {}
        else:
            reasons, evidence = evaluate_evidence(
                manifest.grading.hardened_v1.expected_nodeids, parsed
            )
            outcome = EvaluationOutcome(
                status=OutcomeStatus.COMPLETED,
                reward=1.0 if not reasons else 0.0,
                reason_codes=serialize_reason_codes(reasons),
                test_evidence=evidence,
            )
    if pre_grade is None:
        pre_grade = workspace.pristine_snapshot
    post = workspace.snapshot()
    if outcome.process is None:
        outcome.process = process_info(
            suite.result,
            ["/usr/local/bin/python", "-I", "/opt/grader/run_pytest.py", "/opt/grader"],
            "/workspace",
        )
    _cleanup_evidence(suite.evidence_dir)
    run = _validation_run_from_outcome(GRADER_HARDENED_V1, outcome, workspace, pre_grade, post)
    run.node_outcomes = node_outcomes
    return run


def _cleanup_evidence(evidence_dir: Path) -> None:
    import shutil

    shutil.rmtree(evidence_dir, ignore_errors=True)


def _run_oracle_validation(
    runtime: TaskRuntime,
    workspace: Workspace,
    pre_grade: WorkspaceSnapshot | None,
    image: str,
    runner: Runner,
) -> ValidationRun:
    result = run_oracle_for_labeling(runtime, workspace, runner=runner, image=image)
    if pre_grade is None:
        pre_grade = workspace.pristine_snapshot
    post = workspace.snapshot()
    run = ValidationRun(
        grader=GraderInfo(name=GRADER_ORACLE, version=_GRADER_VERSION),
        status=result.status.value,
        reward=None,
        accepted=None,
        reason_codes=result.reason_codes,
        warnings=[],
        error=result.error,
        test_evidence=result.test_evidence,
        changes=Changes(),
        workspace=workspace_hashes(workspace.pristine_snapshot, pre_grade, post),
        process=None,
        duration_seconds=0.0,
        node_outcomes=result.node_outcomes,
    )
    return run


def _validation_run_from_outcome(
    grader_name: str,
    outcome: EvaluationOutcome,
    workspace: Workspace,
    pre_grade: WorkspaceSnapshot,
    post: WorkspaceSnapshot,
) -> ValidationRun:
    return ValidationRun(
        grader=GraderInfo(name=grader_name, version=_GRADER_VERSION),
        status=outcome.status.value,
        reward=outcome.reward,
        accepted=outcome.accepted,
        reason_codes=outcome.reason_codes,
        warnings=outcome.warnings,
        error=outcome.error,
        test_evidence=outcome.test_evidence,
        changes=outcome.changes,
        workspace=workspace_hashes(workspace.pristine_snapshot, pre_grade, post),
        process=outcome.process,
        duration_seconds=outcome.duration_seconds,
    )


def _validation_case_stable(
    case: str, runs: dict[str, ValidationRun], runtime: TaskRuntime
) -> bool:
    naive = runs[GRADER_NAIVE]
    hardened = runs[GRADER_HARDENED_V1]
    if any(run.status != OutcomeStatus.COMPLETED.value for run in runs.values()):
        return False
    if case == "baseline":
        failing = runtime.task.manifest.validation.baseline_expected_failing_nodeids
        expected_failed = any(
            hardened.node_outcomes.get(nodeid) not in (None, "passed") for nodeid in failing
        )
        return (
            naive.reward == 0.0
            and hardened.reward == 0.0
            and expected_failed
            and not _oracle_passed(runs)
        )
    return naive.reward == 1.0 and hardened.reward == 1.0 and _oracle_passed(runs)


def _oracle_passed(runs: dict[str, ValidationRun]) -> bool:
    oracle = runs[GRADER_ORACLE]
    return oracle.status == OutcomeStatus.COMPLETED.value and not oracle.reason_codes


# ---------------------------------------------------------------------------
# Controlled patch evaluation
# ---------------------------------------------------------------------------


def run_controlled(
    task: LoadedTask,
    *,
    recorder: ExperimentRecorder,
    runner: Runner,
    image: str,
    project_root: Path,
    graders: list[str],
) -> list[EvaluationRecord]:
    runtime = prepare_task(task)
    patches = discover_patches(task.task_dir, PatchSplit.DEVELOPMENT)
    records: list[EvaluationRecord] = []
    pre_grade_hashes: dict[str, dict[str, str]] = {}
    for patch in patches:
        pre_grade_hashes[patch.manifest.id] = {}
        for grader_name in graders:
            record = _run_controlled_patch_grader(
                runtime, patch, grader_name, recorder, runner, image, project_root
            )
            records.append(record)
            pre_grade_hashes[patch.manifest.id][grader_name] = record.workspace.pre_grade_sha256
    _verify_cross_grader_hashes(pre_grade_hashes)
    return records


def _verify_cross_grader_hashes(pre_grade_hashes: dict[str, dict[str, str]]) -> None:
    for patch_id, hashes in pre_grade_hashes.items():
        if len(set(hashes.values())) != 1:
            raise CrossGraderWorkspaceMismatchError(
                f"pre-grade workspace hashes differ across graders for patch {patch_id}: {hashes}"
            )


def _run_controlled_patch_grader(
    runtime: TaskRuntime,
    patch: LoadedPatch,
    grader_name: str,
    recorder: ExperimentRecorder,
    runner: Runner,
    image: str,
    project_root: Path,
) -> EvaluationRecord:
    manager = WorkspaceManager(runtime.task)
    workspace = manager.materialize()
    try:
        apply_result = manager.apply_patch_to(workspace, patch)
        if not apply_result.ok:
            outcome = EvaluationOutcome(
                status=OutcomeStatus.INVALID_INPUT,
                reward=None,
                reason_codes=["patch_apply_failed"],
                error=ErrorInfo(
                    code="patch_apply_failed", message=apply_result.error or "git apply failed"
                ),
            )
            return _build_controlled_record(
                runtime,
                patch,
                grader_name,
                workspace,
                workspace.pristine_snapshot,
                workspace.pristine_snapshot,
                outcome,
                None,
                recorder,
                project_root,
                image,
            )
        pre_grade = workspace.snapshot()
        evaluator_result = evaluate_grader(
            grader_name, runtime, workspace, pre_grade, runner=runner, image=image
        )
        return _build_controlled_record(
            runtime,
            patch,
            grader_name,
            workspace,
            workspace.pristine_snapshot,
            pre_grade,
            evaluator_result.outcome,
            evaluator_result.process_result,
            recorder,
            project_root,
            image,
        )
    finally:
        manager.finalize_and_destroy(workspace)


def _build_controlled_record(
    runtime: TaskRuntime,
    patch: LoadedPatch,
    grader_name: str,
    workspace: Workspace,
    pristine: WorkspaceSnapshot,
    pre_grade: WorkspaceSnapshot,
    outcome: EvaluationOutcome,
    process_result: ProcessResult | None,
    recorder: ExperimentRecorder,
    project_root: Path,
    image: str,
) -> EvaluationRecord:
    run_id = uuid.uuid4().hex
    process = outcome.process
    if process is not None and process_result is not None:
        stdout_path = recorder.write_artifact(run_id, "stdout", process_result.stdout)
        stderr_path = recorder.write_artifact(run_id, "stderr", process_result.stderr)
        process.stdout_path = str(stdout_path)
        process.stderr_path = str(stderr_path)

    record = EvaluationRecord(
        schema_version="1.0",
        run_id=run_id,
        experiment_id=recorder.experiment_id,
        timestamp_utc=utc_now(),
        status=outcome.status.value,
        phase=Phase.CONTROLLED.value,
        validation_case=None,
        repeat_index=0,
        git=git_info(project_root),
        grader=GraderInfo(name=grader_name, version=_GRADER_VERSION),
        task=task_info(runtime.task),
        patch=patch_info(patch),
        environment=build_environment_info(image),
        workspace=workspace_hashes(pristine, pre_grade, workspace.snapshot()),
        result=outcome.to_result_info(),
        process=process,
        test_evidence=outcome.test_evidence,
        changes=outcome.changes,
        error=outcome.error,
    )
    recorder.write_record(record)
    return record


def plan_metadata(
    *,
    experiment_id: str,
    project_root: Path,
    tasks: list[LoadedTask],
    graders: list[str],
) -> dict[str, object]:
    """Build the planned-matrix metadata document (Section 27.16)."""
    controlled: list[dict[str, str]] = []
    for task in tasks:
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
            for grader in graders:
                controlled.append(
                    {
                        "grader": grader,
                        "task_id": task.manifest.id,
                        "patch_id": patch.manifest.id,
                        "split": task.manifest.split.value,
                    }
                )
    return {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "timestamp_utc": utc_now(),
        "git": git_info(project_root).model_dump(mode="json"),
        "plan": {"controlled": controlled},
    }
