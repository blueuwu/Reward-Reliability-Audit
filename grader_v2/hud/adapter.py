"""HUD-facing adapter for the application-v2 deployment (D-054).

The protected ``grader_audit.hud_adapter.evaluator`` supports only
``naive``/``hardened_v1``. This module replicates its narrow adapter surface
(grade the workspace state through the shared core; map to HUD) and adds the
``hardened_v2`` dispatch. Everything else — task preparation, workspace
construction, snapshot capture, the protected evaluators, the HUD context and
result mapping — is the frozen code imported verbatim.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from hud.graders import EvaluationResult

from grader_audit.core.manifests import LoadedTask
from grader_audit.core.orchestrator import (
    GRADER_HARDENED_V1,
    GRADER_NAIVE,
    evaluate_grader,
    prepare_task,
)
from grader_audit.core.process import Runner
from grader_audit.core.snapshots import WorkspaceSnapshot, capture_snapshot
from grader_audit.core.workspace import Workspace
from grader_audit.hud_adapter.mapping import HudEvalContext, map_evaluation_result
from grader_v2.grading.evaluator import (
    GRADER_HARDENED_V2,
    HardenedV2Context,
    HardenedV2Evaluator,
)
from grader_v2.grading.evidence import SemanticEvidence
from grader_v2.hud.mapping import map_evaluation_result_v2

SUPPORTED_GRADER_VERSIONS = (GRADER_NAIVE, GRADER_HARDENED_V1, GRADER_HARDENED_V2)


@dataclass(frozen=True)
class HudGrade:
    """The core outcome plus its HUD-mapped result for one graded workspace."""

    outcome: object
    result: EvaluationResult
    semantic: SemanticEvidence | None = None


def _validate_grader_version(grader_version: str) -> str:
    if grader_version not in SUPPORTED_GRADER_VERSIONS:
        raise ValueError(
            f"unsupported grader_version {grader_version!r}; "
            f"choose from {sorted(SUPPORTED_GRADER_VERSIONS)}"
        )
    return grader_version


def grade_workspace(
    *,
    task: LoadedTask,
    grader_version: str,
    workspace_root: Path,
    pristine_snapshot: WorkspaceSnapshot,
    image: str,
    runner: Runner,
    patch_id: str | None = None,
    patch_diff_sha256: str | None = None,
    seed: int | None = None,
) -> HudGrade:
    """Grade the state of *workspace_root* with the shared core and map to HUD."""
    grader_version = _validate_grader_version(grader_version)
    runtime = prepare_task(task)
    workspace = Workspace(
        root=workspace_root,
        task_id=task.manifest.id,
        materialization_id="hud",
        pristine_snapshot=pristine_snapshot,
    )
    pre_grade = capture_snapshot(workspace_root)
    if grader_version == GRADER_HARDENED_V2:
        context = HardenedV2Context(
            manifest=task.manifest,
            workspace_host=workspace.root,
            pristine_snapshot=workspace.pristine_snapshot,
            pre_grade_snapshot=pre_grade,
            authoritative_tests_host=runtime.task.task_dir
            / task.manifest.grading.hardened_v1.tests_dir,
            expected_grader_assets_hash=runtime.authoritative_hash,
            image=image,
            memory_mb=task.manifest.runtime.memory_mb,
            pids_limit=task.manifest.runtime.pids_limit,
            seed=seed,
        )
        result = HardenedV2Evaluator().evaluate(context, runner)
        outcome = result.outcome
        semantic = result.semantic
    else:
        evaluator_result = evaluate_grader(
            grader_version, runtime, workspace, pre_grade, runner=runner, image=image
        )
        outcome = evaluator_result.outcome
        semantic = None
    post_grade = capture_snapshot(workspace_root)
    hud_context = HudEvalContext(
        task_id=task.manifest.id,
        grader_name=grader_version,
        grader_version=grader_version,
        manifest_sha256=task.manifest_sha256,
        patch_id=patch_id,
        patch_diff_sha256=patch_diff_sha256,
        pristine_sha256=pristine_snapshot.sha256,
        pre_grade_sha256=pre_grade.sha256,
        post_grade_sha256=post_grade.sha256,
    )
    if grader_version == GRADER_HARDENED_V2:
        result = map_evaluation_result_v2(outcome, hud_context, semantic)
    else:
        result = map_evaluation_result(outcome, hud_context)
    return HudGrade(outcome=outcome, result=result, semantic=semantic)


async def grade_workspace_async(
    *,
    task: LoadedTask,
    grader_version: str,
    workspace_root: Path,
    pristine_snapshot: WorkspaceSnapshot,
    image: str,
    runner: Runner,
    patch_id: str | None = None,
    patch_diff_sha256: str | None = None,
    seed: int | None = None,
) -> HudGrade:
    """Threaded wrapper so graded subprocesses never block the HUD event loop."""
    return await asyncio.to_thread(
        grade_workspace,
        task=task,
        grader_version=grader_version,
        workspace_root=workspace_root,
        pristine_snapshot=pristine_snapshot,
        image=image,
        runner=runner,
        patch_id=patch_id,
        patch_diff_sha256=patch_diff_sha256,
        seed=seed,
    )
