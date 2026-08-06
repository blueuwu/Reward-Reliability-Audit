"""Shared HUD evaluator entry point (Section 27.13).

The HUD task template calls this after the agent finishes. It stages nothing
itself: the template stages the declared baseline into a fresh workspace before
yielding the prompt, and this module grades the workspace state left by the
agent through the exact same core functions the controlled CLI uses
(:func:`grader_audit.core.orchestrator.evaluate_grader`). It never reimplements
scope checks, evidence parsing, reason codes, or acceptance logic.
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
from grader_audit.core.outcomes import EvaluationOutcome
from grader_audit.core.process import Runner
from grader_audit.core.snapshots import WorkspaceSnapshot, capture_snapshot
from grader_audit.core.workspace import Workspace
from grader_audit.hud_adapter.mapping import HudEvalContext, map_evaluation_result

SUPPORTED_GRADER_VERSIONS = (GRADER_NAIVE, GRADER_HARDENED_V1)


@dataclass(frozen=True)
class HudGrade:
    """The core outcome plus its HUD-mapped result for one graded workspace."""

    outcome: EvaluationOutcome
    result: EvaluationResult


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
) -> HudGrade:
    """Grade the state of *workspace_root* with the shared core and map to HUD.

    *pristine_snapshot* is the baseline snapshot captured at staging time; the
    pre-grade snapshot is captured from the workspace as the agent left it, so
    hardened scope rules classify the agent's own edits. The returned
    ``HudGrade`` carries both the raw core outcome and the mapped result.
    """
    grader_version = _validate_grader_version(grader_version)
    runtime = prepare_task(task)
    workspace = Workspace(
        root=workspace_root,
        task_id=task.manifest.id,
        materialization_id="hud",
        pristine_snapshot=pristine_snapshot,
    )
    pre_grade = capture_snapshot(workspace_root)
    evaluator_result = evaluate_grader(
        grader_version, runtime, workspace, pre_grade, runner=runner, image=image
    )
    outcome = evaluator_result.outcome
    post_grade = capture_snapshot(workspace_root)
    context = HudEvalContext(
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
    return HudGrade(outcome=outcome, result=map_evaluation_result(outcome, context))


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
) -> HudGrade:
    """Threaded wrapper so graded containers never block the HUD event loop."""
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
    )
