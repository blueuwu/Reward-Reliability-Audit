"""Deployment-container integration tests (Gate D).

These tests run the real Dockerfile.hud image and exercise the HUD control
channel end-to-end with deterministic stub agents: the baseline workspace must
score 0.0 and the gold patch must score 1.0 through the deployed grader. The
agent workspace is also proven isolated from the immutable grader assets.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from hud.eval.run import Run

from grader_audit.core.docker_runner import ContainerStartError
from grader_audit.core.manifests import discover_patches, load_task
from grader_audit.core.models import PatchSplit
from grader_audit.core.process import CommandSpec, Mount
from grader_v2.grading.runners import InContainerRunner
from grader_v2.hud.runtime import (
    resolve_deployment_image,
    run_stub_rollout_remote,
    start_deployment_container,
)
from grader_v2.tests.conftest import requires_docker

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TASK_ID = "tinydb-missing-doc-ids"
_NAIVE = "naive"


def _run_diagnostics(run: Run) -> str:
    """Render everything the rollout recorded so a failing gate names its cause."""
    parts = [f"status={run.trace.status}"]
    if run.trace.stop_reason:
        parts.append(f"stop_reason={run.trace.stop_reason}")
    if run.trace.content:
        parts.append(f"content={run.trace.content!r}")
    try:
        parts.append(f"evaluation={run.evaluation!r}")
    except Exception as exc:  # pragma: no cover - diagnostic only
        parts.append(f"evaluation_unavailable={exc!r}")
    return "; ".join(parts)


@pytest.fixture(scope="session")
def deployment_image() -> str:
    """The deployment image, built when missing (Dockerfile.hud)."""
    return resolve_deployment_image(_PROJECT_ROOT, build=True)


def _gold_diff() -> str:
    task = load_task(_PROJECT_ROOT / "tasks" / _TASK_ID)
    gold = next(
        patch
        for patch in discover_patches(task.task_dir, PatchSplit.FROZEN_EVAL)
        if patch.manifest.id == "gold"
    )
    return gold.diff_bytes.decode("utf-8")


@requires_docker
def test_deployment_image_runs_baseline_and_gold(deployment_image: str) -> None:
    """One live container: baseline 0.0, gold 1.0, both with traces."""
    container = start_deployment_container(deployment_image)
    try:
        asyncio.run(container.wait_ready())
        baseline = asyncio.run(
            run_stub_rollout_remote(
                url=container.url, task_id=_TASK_ID, grader_version=_NAIVE
            )
        )
        assert baseline.reward == 0.0, _run_diagnostics(baseline)
        assert baseline.trace.status == "completed", _run_diagnostics(baseline)
        assert baseline.trace.trace_id, _run_diagnostics(baseline)

        gold = asyncio.run(
            run_stub_rollout_remote(
                url=container.url,
                task_id=_TASK_ID,
                grader_version=_NAIVE,
                diff_text=_gold_diff(),
            )
        )
        assert gold.reward == 1.0, _run_diagnostics(gold)
        assert gold.trace.status == "completed", _run_diagnostics(gold)
    finally:
        asyncio.run(container.stop())


@requires_docker
def test_deployment_container_refuses_grader_root_replacement(
    tmp_path: Path, deployment_image: str
) -> None:
    """An in-container run must never replace non-replaceable grader roots."""
    runner = InContainerRunner()
    payload = tmp_path / "payload"
    payload.mkdir()
    with pytest.raises(ContainerStartError, match="refusing to replace"):
        runner.run(
            CommandSpec(argv=["true"], cwd=str(payload)),
            mounts=[
                Mount(host_path=payload, container_path="/opt/grader/other", read_only=True)
            ],
            image=deployment_image,
            memory_mb=512,
            pids_limit=128,
        )


@requires_docker
def test_agent_cannot_apply_patches_outside_workspace(
    deployment_image: str,
) -> None:
    """A diff targeting the grader assets fails inside the agent sandbox."""
    malicious = (
        "diff --git a/../opt/grader/tests/test_semantic_docids.py "
        "b/../opt/grader/tests/test_semantic_docids.py\n"
        "--- a/../opt/grader/tests/test_semantic_docids.py\n"
        "+++ b/../opt/grader/tests/test_semantic_docids.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-#! no-op\n"
        "+#! tampered\n"
    )
    container = start_deployment_container(deployment_image)
    try:
        asyncio.run(container.wait_ready())
        result = asyncio.run(
            run_stub_rollout_remote(
                url=container.url,
                task_id=_TASK_ID,
                grader_version=_NAIVE,
                diff_text=malicious,
            )
        )
        assert result.trace.content is not None
        assert "patch apply failed" in result.trace.content, _run_diagnostics(result)
        assert result.reward == 0.0, _run_diagnostics(result)
    finally:
        asyncio.run(container.stop())
