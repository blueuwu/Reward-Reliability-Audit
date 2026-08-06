"""Local HUD smoke test that needs no model provider or API key (Section 27.13).

This drives a real HUD v6 rollout through the installed SDK — env served via
``LocalRuntime``, workspace capability staged from the declared baseline, the
template's two-yield lifecycle, and the shared core grading the workspace state
— using a deterministic ``StubAgent`` that records an answer without calling any
model provider. It is a plumbing test, not a model evaluation: the workspace is
left at baseline, so the grader reports reward ``0.0`` and the trace records the
exact reason codes. Nothing here fakes a model trajectory.

Run directly::

    uv run python -m grader_audit.hud_adapter.smoke
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from hud.agents.base import Agent
from hud.eval import LocalRuntime
from hud.eval.run import Run, rollout

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _PROJECT_ROOT / "env.py"
_DEFAULT_TASKS_DIR = _PROJECT_ROOT / "tests" / "fixtures"


class StubAgent(Agent):
    """Deterministic no-op agent: records an answer, never calls a provider."""

    async def __call__(self, run: Run) -> None:
        run.trace.content = "stub agent (no provider call); the workspace state is graded"


def _prepare_environment(tasks_dir: Path, workspace_root: Path) -> None:
    os.environ["GRADER_AUDIT_TASKS_DIR"] = str(tasks_dir)
    os.environ["GRADER_AUDIT_WORKSPACE_ROOT"] = str(workspace_root)
    os.environ["GRADER_AUDIT_IN_CONTAINER"] = "0"


async def run_stub_rollout(
    *,
    task_id: str,
    grader_version: str,
    tasks_dir: Path = _DEFAULT_TASKS_DIR,
    workspace_root: Path | None = None,
    env_path: Path = _ENV_PATH,
) -> Run:
    """Run one real HUD rollout with the stub agent and return the graded ``Run``."""
    from hud.eval import Task

    if workspace_root is None:
        workspace_root = Path(tempfile.mkdtemp(prefix="ga-hud-smoke-"))
    _prepare_environment(tasks_dir, workspace_root)

    task = Task(
        env="hud-grader-audit",
        id="grader_reliability_task",
        args={"task_id": task_id, "grader_version": grader_version},
    )
    runtime = LocalRuntime(env_path, env="hud-grader-audit")
    return await rollout(task, StubAgent(), runtime=runtime)


async def _smoke_main() -> int:
    task_id = os.environ.get("GRADER_AUDIT_SMOKE_TASK", "fixture-stringutil")
    grader_version = os.environ.get("GRADER_AUDIT_SMOKE_GRADER", "naive")
    run = await run_stub_rollout(task_id=task_id, grader_version=grader_version)
    print(f"smoke rollout {run.slug}: reward={run.reward} status={run.trace.status}")
    print(f"evaluation info: {run.evaluation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_smoke_main()))
