"""Generic HUD v6 coding environment for the grader-reliability audit (27.13).

One frozen ``env.py`` and one generic ``Dockerfile.hud`` serve every task.
Each task is graded by the shared framework-independent core: the template
stages the task's declared baseline into a fresh workspace before yielding the
prompt, and after the agent finishes it calls the same core evaluator the
controlled CLI uses. Paired naive/hardened task lists differ only by the
``grader_version`` argument and reference one task image digest per task.

Run locally without a model provider::

    set GRADER_AUDIT_TASKS_DIR=tests\\fixtures
    set GRADER_AUDIT_WORKSPACE_ROOT=<temp dir>
    uv run python -m grader_audit.hud_adapter.smoke

Deployment (platform): the generic ``Dockerfile.hud`` serves this file with
``hud serve env.py --host 0.0.0.0``; task baselines and locked dependencies are
baked into per-task immutable scoring images (``build-images``).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path

from hud import Environment
from hud.graders import EvaluationResult

from grader_audit.core.manifests import discover_tasks
from grader_audit.core.workspace import WorkspaceManager
from grader_audit.hud_adapter.evaluator import grade_workspace_async
from grader_audit.images import resolve_task_image

_HERE = Path(__file__).resolve().parent
_TASKS_DIR = Path(os.environ.get("GRADER_AUDIT_TASKS_DIR", str(_HERE / "tasks")))


def _default_workspace_root() -> Path:
    if os.environ.get("GRADER_AUDIT_IN_CONTAINER"):
        return Path("/workspace")
    return _HERE / ".hud-workspace"


_WORKSPACE_ROOT = Path(
    os.environ.get("GRADER_AUDIT_WORKSPACE_ROOT", str(_default_workspace_root()))
)

env = Environment(name="hud-grader-audit", version="0.1.0")
_workspace = env.workspace(_WORKSPACE_ROOT, network=False, track_files=False)


def _task_registry() -> dict[str, object]:
    if not _TASKS_DIR.is_dir():
        return {}
    return {task.manifest.id: task for task in discover_tasks(_TASKS_DIR)}


_TASK_BY_ID = _task_registry()


def _resolve_task(task_id: str):
    from grader_audit.core.manifests import LoadedTask

    task = _TASK_BY_ID.get(task_id)
    if task is None or not isinstance(task, LoadedTask):
        known = ", ".join(sorted(_TASK_BY_ID)) or "(no tasks registered)"
        raise ValueError(f"unknown task_id {task_id!r}; registered tasks: {known}")
    return task


def _new_runner():
    from grader_audit.core.docker_runner import DockerRunner

    return DockerRunner()


@env.template(
    id="grader_reliability_task",
    description="Bug-fix a fixture workspace; graded by the shared core.",
)
async def grader_reliability_task(
    task_id: str, grader_version: str
) -> AsyncGenerator[str, EvaluationResult]:
    """Stage the baseline, prompt the agent, and grade the workspace state.

    The second ``yield`` value is a ``hud.graders.EvaluationResult`` that the
    HUD server forwards verbatim (score/reward, info, content, isError,
    subscores). The workspace left by the agent is graded, never the textual
    answer.
    """
    task = _resolve_task(task_id)
    manager = WorkspaceManager(task)
    workspace = manager.stage_fresh_root(_workspace.root)
    prompt_path = task.task_dir / "prompt.md"
    prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""
    _answer = yield prompt
    image = resolve_task_image(task)
    grade = await grade_workspace_async(
        task=task,
        grader_version=grader_version,
        workspace_root=_workspace.root,
        pristine_snapshot=workspace.pristine_snapshot,
        image=image,
        runner=_new_runner(),
    )
    yield grade.result
