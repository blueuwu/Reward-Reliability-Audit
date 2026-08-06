"""HUD parity and local smoke tests (Sections 27.13, 27.19 item 8).

Three layers:

1. Import/API compatibility against the installed HUD 0.6.12 package (no Docker).
2. Parity: the same fixture workspace evaluated through the local core and
   through the HUD adapter yields identical status, reward, acceptance, and
   reason codes.
3. A real local HUD v6 rollout (env served via ``LocalRuntime``, workspace
   capability staged from the declared baseline, two-yield task lifecycle) driven
   by a deterministic stub agent that never calls a model provider. The workspace
   is left at baseline, so the grader must report reward ``0.0`` with the exact
   reason codes — a plumbing test, not a faked model trajectory.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

from grader_audit.core.docker_runner import DockerRunner
from grader_audit.core.manifests import LoadedTask, discover_patches, load_task
from grader_audit.core.models import PatchSplit
from grader_audit.core.orchestrator import evaluate_grader, prepare_task
from grader_audit.core.workspace import Workspace, WorkspaceManager
from grader_audit.hud_adapter.evaluator import grade_workspace
from grader_audit.hud_adapter.smoke import run_stub_rollout
from tests.conftest import FIXTURES_DIR, requires_docker


def test_hud_api_surface_matches_installed_package() -> None:
    """Guard against HUD API drift: the symbols we use exist on 0.6.12."""
    import hud
    from hud import Environment
    from hud.eval import LocalRuntime, Task, Taskset
    from hud.eval.run import Run, rollout
    from hud.graders import EvaluationResult, SubScore

    assert hud.__version__.startswith("0.6.")
    assert callable(Environment)
    assert callable(Environment.template)
    assert callable(Environment.workspace)
    assert callable(LocalRuntime)
    assert callable(Task)
    assert callable(Taskset)
    assert callable(Run)
    assert callable(rollout)
    assert "reward" in EvaluationResult.model_fields
    assert "isError" in EvaluationResult.model_fields
    assert "info" in EvaluationResult.model_fields
    assert "subscores" in EvaluationResult.model_fields
    assert "value" in SubScore.model_fields
    assert "weight" in SubScore.model_fields


def _find_patch(task: LoadedTask, patch_id: str):
    for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
        if patch.manifest.id == patch_id:
            return patch
    raise AssertionError(f"patch {patch_id} not found")


def _patched(task: LoadedTask, patch_id: str) -> tuple[Workspace, WorkspaceManager]:
    manager = WorkspaceManager(task)
    workspace = manager.materialize()
    result = manager.apply_patch_to(workspace, _find_patch(task, patch_id))
    assert result.ok, result.error
    return workspace, manager


@pytest.fixture(scope="module")
def runner() -> DockerRunner:
    return DockerRunner()


@pytest.mark.parametrize("grader", ["naive", "hardened_v1"])
@pytest.mark.parametrize("patch_id", ["gold", "weaken-visible-tests"])
@requires_docker
def test_parity_local_core_and_hud_adapter_are_identical(
    fixture_image: str, runner: DockerRunner, grader: str, patch_id: str
) -> None:
    """The HUD adapter must call the same core and preserve every outcome field."""
    task = load_task(FIXTURES_DIR / "fixture-stringutil")

    workspace_a, manager_a = _patched(task, patch_id)
    runtime = prepare_task(task)
    pre_a = workspace_a.snapshot()
    try:
        outcome_a = evaluate_grader(
            grader, runtime, workspace_a, pre_a, runner=runner, image=fixture_image
        ).outcome
    finally:
        manager_a.finalize_and_destroy(workspace_a)

    workspace_b, manager_b = _patched(task, patch_id)
    pre_b = workspace_b.snapshot()
    try:
        hud_grade = grade_workspace(
            task=task,
            grader_version=grader,
            workspace_root=workspace_b.root,
            pristine_snapshot=workspace_b.pristine_snapshot,
            image=fixture_image,
            runner=runner,
            patch_id=patch_id,
        )
    finally:
        manager_b.finalize_and_destroy(workspace_b)

    # Identical patched workspace state across the two paths.
    assert pre_a.sha256 == pre_b.sha256

    outcome_b = hud_grade.outcome
    assert outcome_a.status is outcome_b.status
    assert outcome_a.reward == outcome_b.reward
    assert outcome_a.accepted == outcome_b.accepted
    assert outcome_a.reason_codes == outcome_b.reason_codes

    # The HUD result preserves status, reward, acceptance, and reason codes.
    assert hud_grade.result.info["status"] == outcome_b.status.value
    assert hud_grade.result.info["reward"] == outcome_b.reward
    assert hud_grade.result.info["accepted"] == outcome_b.accepted
    assert hud_grade.result.info["reason_codes"] == outcome_b.reason_codes
    assert hud_grade.result.info["task_id"] == task.manifest.id
    assert hud_grade.result.info["manifest_sha256"] == task.manifest_sha256
    assert hud_grade.result.info["collected_nodeids"] == (
        outcome_b.test_evidence.collected_nodeids if outcome_b.test_evidence else []
    )
    assert hud_grade.result.isError == (outcome_b.status.value != "completed")


def test_paired_task_lists_differ_only_by_grader_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 27.13: naive/hardened variants share everything but grader_version."""
    monkeypatch.setenv("GRADER_AUDIT_TASKS_DIR", str(FIXTURES_DIR))
    tasks_module = importlib.import_module("tasks")
    tasks_module = importlib.reload(tasks_module)

    naive = list(tasks_module.tasks_naive)
    hardened = list(tasks_module.tasks_hardened)
    assert len(naive) == len(hardened) == 2
    for n, h in zip(naive, hardened, strict=True):
        assert n.env == h.env == "hud-grader-audit"
        assert n.id == h.id == "grader_reliability_task"
        assert n.args["task_id"] == h.args["task_id"]
        assert n.args["grader_version"] == "naive"
        assert h.args["grader_version"] == "hardened_v1"
        assert n.default_slug() != h.default_slug()


@requires_docker
def test_local_hud_rollout_smoke_with_stub_agent(
    fixture_image: str, tmp_path: Path
) -> None:
    """A real HUD rollout over the workspace capability, without a provider."""
    del fixture_image  # requested to ensure the fixture image is built first
    workspace_root = tmp_path / "hud-ws"
    run = asyncio.run(
        run_stub_rollout(
            task_id="fixture-stringutil",
            grader_version="naive",
            tasks_dir=FIXTURES_DIR,
            workspace_root=workspace_root,
        )
    )
    assert run.trace.status == "completed"
    # The stub agent leaves the workspace at baseline, so the naive grader must
    # report a completed rejection with the exact reason code.
    assert run.reward == 0.0
    info = run.evaluation.get("info", {})
    assert info.get("status") == "completed"
    assert info.get("accepted") is False
    assert "naive_nonzero_exit" in info.get("reason_codes", [])
    assert info.get("task_id") == "fixture-stringutil"
