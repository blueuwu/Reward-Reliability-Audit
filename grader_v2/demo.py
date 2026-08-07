"""Deterministic application demo (hardening §11).

One command performs the full application arc with real evaluator paths and
no API key:

1. doctor check
2. deployment-image build or verified image resolution
3. baseline rollout -> reward 0 (stub agent against the deployment container)
4. gold rollout -> reward 1 (stub agent applies the gold diff through the
   workspace capability; graded by the served deployment)
5. naive exploit -> reward 1 (offline shared core)
6. same exploit under hardened v1 -> reward 0 (offline shared core)
7. semantic overfit under hardened v1 -> reward 1 (offline shared core)
8. same semantic overfit under hardened v2 -> measured outcome (seeded suite)
9. trace/result locations printed
10. publication validation

Nothing is stubbed: every step grades a real workspace state through the
frozen shared core or the deployed container. Run with
``grader-v2 demo`` (needs Docker; no model provider).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from grader_audit.core.docker_runner import DockerRunner
from grader_audit.core.doctor import run_doctor
from grader_audit.core.manifests import discover_patches, load_task
from grader_audit.core.models import PatchSplit
from grader_audit.core.orchestrator import evaluate_grader, prepare_task
from grader_audit.core.workspace import WorkspaceManager
from grader_audit.images import build_task_image
from grader_v2.grading.evaluator import (
    GRADER_HARDENED_V2,
    HardenedV2Context,
    HardenedV2Evaluator,
)
from grader_v2.hud.runtime import (
    DEFAULT_IMAGE,
    DeploymentError,
    resolve_deployment_image,
    run_stub_rollout_remote,
    start_deployment_container,
)
from grader_v2.jsonutil import as_dict

NAIVE = "naive"
V1 = "hardened_v1"

#: tinydb-missing-doc-ids; the naive exploit is the visible-test-weakening
#: patch of the same task; the semantic overfit is the confirmed
#: authoritative-overfit patch.
_TASK_ID = "tinydb-missing-doc-ids"
_NAIVE_EXPLOIT = "weaken-visible-tests"
_SEMANTIC_OVERFIT = "list-only-skip-missing"


def _step(message: str) -> None:
    print(f"\n==> {message}", flush=True)


def _find_patch(task_dir: Path, patch_id: str, split: PatchSplit):
    for patch in discover_patches(task_dir, split):
        if patch.manifest.id == patch_id:
            return patch
    raise FileNotFoundError(f"patch {patch_id!r} not found under {task_dir}")


def _offline_grade(
    task_dir: Path,
    patch_id: str | None,
    grader: str,
    runner: DockerRunner,
    seed: int | None = None,
) -> dict[str, object]:
    """Grade one real workspace state through the shared core (no stubs)."""
    task = load_task(task_dir)
    image = build_task_image(task)
    runtime = prepare_task(task)
    manager = WorkspaceManager(task)
    workspace = manager.materialize()
    try:
        pristine = workspace.pristine_snapshot
        if patch_id is not None:
            split = (
                PatchSplit.FROZEN_EVAL
                if task.manifest.split.value == "frozen_eval"
                else PatchSplit.DEVELOPMENT
            )
            patch = _find_patch(task_dir, patch_id, split)
            result = manager.apply_patch_to(workspace, patch)
            if not result.ok:
                raise RuntimeError(f"patch apply failed: {result.error}")
        pre_grade = workspace.snapshot()
        if grader == GRADER_HARDENED_V2:
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
                seed=seed,
            )
            evaluated = HardenedV2Evaluator().evaluate(context, runner)
            outcome = evaluated.outcome
            semantic = evaluated.semantic
        else:
            evaluated = evaluate_grader(
                grader, runtime, workspace, pre_grade, runner=runner, image=image
            )
            outcome = evaluated.outcome
            semantic = None
        post = workspace.snapshot()
        return {
            "task_id": task.manifest.id,
            "patch_id": patch_id or "baseline",
            "grader": grader,
            "status": outcome.status.value,
            "reward": outcome.reward,
            "reason_codes": list(outcome.reason_codes),
            "warnings": list(outcome.warnings),
            "semantic": (
                {
                    "profile_id": semantic.profile_id,
                    "seed": semantic.seed,
                    "failed": semantic.failed,
                    "errors": semantic.errors,
                    "case_count": semantic.case_count,
                }
                if semantic is not None
                else None
            ),
            "workspace": {
                "pristine_sha256": pristine.sha256,
                "pre_grade_sha256": pre_grade.sha256,
                "post_grade_sha256": post.sha256,
            },
        }
    finally:
        manager.finalize_and_destroy(workspace)


def run_demo(project_root: Path, args: object | None = None) -> int:
    started = time.monotonic()
    tasks_dir = project_root / "tasks"
    task_dir = tasks_dir / _TASK_ID
    results: dict[str, object] = {"steps": [], "timestamp_utc": None}
    steps: list[dict[str, object]] = []
    results["steps"] = steps
    from grader_v2.grading.records import utc_now

    results["timestamp_utc"] = utc_now()

    # 1. doctor
    _step("1/10 doctor check")
    doctor = run_doctor(project_root)
    if not doctor.all_ok:
        failed = [c.description for c in doctor.checks if not c.ok]
        print(f"doctor failed: {failed}")
        return 4
    print("doctor: all prerequisites satisfied")

    # 2. deployment image
    _step("2/10 deployment image build or verified resolution")
    try:
        image_id = resolve_deployment_image(project_root, DEFAULT_IMAGE, build=True)
    except DeploymentError as exc:
        print(f"deployment image failed: {exc}")
        return 4
    print(f"deployment image resolved: {image_id}")

    # 3-4. container rollouts (baseline -> 0, gold -> 1)
    container = start_deployment_container(DEFAULT_IMAGE)
    try:
        _step("3/10 baseline rollout against the deployment container")
        baseline = asyncio.run(
            run_stub_rollout_remote(
                url=container.url, task_id=_TASK_ID, grader_version=NAIVE
            )
        )
        print(
            f"baseline rollout: reward={baseline.reward} status={baseline.trace.status} "
            f"trace={baseline.trace.trace_id}"
        )
        if baseline.reward != 0.0:
            print(f"ERROR: expected baseline reward 0.0, got {baseline.reward}")
            return 3
        steps.append(
            {
                "step": 3,
                "kind": "container-rollout",
                "task_id": _TASK_ID,
                "grader": NAIVE,
                "patch": "baseline",
                "reward": baseline.reward,
                "trace_id": baseline.trace.trace_id,
                "url": container.url,
            }
        )

        _step("4/10 gold rollout against the deployment container")
        gold_patch = _find_patch(task_dir, "gold", PatchSplit.DEVELOPMENT)
        gold = asyncio.run(
            run_stub_rollout_remote(
                url=container.url,
                task_id=_TASK_ID,
                grader_version=NAIVE,
                diff_text=gold_patch.diff_bytes.decode("utf-8"),
            )
        )
        print(
            f"gold rollout: reward={gold.reward} status={gold.trace.status} "
            f"trace={gold.trace.trace_id}"
        )
        if gold.reward != 1.0:
            print(f"ERROR: expected gold reward 1.0, got {gold.reward}")
            return 3
        steps.append(
            {
                "step": 4,
                "kind": "container-rollout",
                "task_id": _TASK_ID,
                "grader": NAIVE,
                "patch": "gold",
                "reward": gold.reward,
                "trace_id": gold.trace.trace_id,
                "url": container.url,
            }
        )
    finally:
        asyncio.run(container.stop())

    # 5-8. offline shared-core rows
    runner = DockerRunner()
    _step(f"5/10 naive exploit ({_NAIVE_EXPLOIT}) under naive grader")
    naive = _offline_grade(task_dir, _NAIVE_EXPLOIT, NAIVE, runner)
    print(f"naive exploit: reward={naive['reward']} codes={naive['reason_codes']}")
    steps.append({"step": 5, **naive})

    _step("6/10 same exploit under hardened v1")
    v1 = _offline_grade(task_dir, _NAIVE_EXPLOIT, V1, runner)
    print(f"naive exploit under v1: reward={v1['reward']} codes={v1['reason_codes']}")
    steps.append({"step": 6, **v1})

    _step(f"7/10 semantic overfit ({_SEMANTIC_OVERFIT}) under hardened v1")
    overfit_v1 = _offline_grade(task_dir, _SEMANTIC_OVERFIT, V1, runner)
    print(
        f"semantic overfit under v1: reward={overfit_v1['reward']} "
        f"codes={overfit_v1['reason_codes']}"
    )
    steps.append({"step": 7, **overfit_v1})

    _step("8/10 same semantic overfit under hardened v2 (seeded)")
    overfit_v2 = _offline_grade(
        task_dir, _SEMANTIC_OVERFIT, GRADER_HARDENED_V2, runner, seed=20260807
    )
    print(
        f"semantic overfit under v2: reward={overfit_v2['reward']} "
        f"codes={overfit_v2['reason_codes']}"
    )
    semantic = as_dict(overfit_v2.get("semantic"))
    if semantic:
        print(
            f"  semantic: profile={semantic.get('profile_id')} "
            f"seed={semantic.get('seed')} "
            f"cases={semantic.get('case_count')} "
            f"failed={semantic.get('failed')}"
        )
    steps.append({"step": 8, **overfit_v2})

    # 9. result locations
    _step("9/10 trace and result locations")
    locations = {
        "deployment_image": image_id,
        "v1_blind_records": "results/raw/clean-clone-reproduction/",
        "v1_probe_records": "results/raw/probe-v1-blindspots/",
        "v2_records": "results/raw/v2-regression/",
        "summaries": "results/summaries/",
        "this_demo": "results/demo/demo-result.json",
    }
    for key, value in locations.items():
        print(f"  {key}: {value}")
    results["locations"] = locations

    # 10. publication validation
    _step("10/10 publication validation")
    from grader_v2.publication import validate_publication

    problems = validate_publication(project_root)
    if problems:
        print("publication validation FAILED:")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("publication validation: OK")

    demo_dir = project_root / "results" / "demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    output = demo_dir / "demo-result.json"
    output.write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"\ndemo complete in {time.monotonic() - started:.0f}s; JSON at {output}")
    return 0
