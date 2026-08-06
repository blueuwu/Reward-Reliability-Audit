"""Real development corpus validation (Sections 27.5, 27.6, 27.15).

These tests never require Docker. They verify the vendored real-task corpus is
well-formed, meets the Section 27.5 development allocation, is not collected by
the repository pytest suite, and that the new Gate-3 image/labeling code paths
behave deterministically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grader_audit.core.labeling import draft_disposition
from grader_audit.core.manifests import LoadedPatch, LoadedTask, discover_patches, discover_tasks
from grader_audit.core.models import PatchSplit
from grader_audit.core.orchestrator import (
    check_development_corpus_minimums,
    check_task_corpus,
)
from grader_audit.images import (
    task_dockerfile_text,
    task_image_tag,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = PROJECT_ROOT / "tasks"


def _load_patch(task_dir: Path, patch_id: str) -> LoadedPatch:
    from grader_audit.core.manifests import load_patch

    return load_patch(
        task_dir
        / "patches"
        / ("valid" if patch_id in {"gold", "alternative-word-split"} else "invalid_dev")
        / patch_id
    )


@pytest.fixture(scope="module")
def real_tasks() -> list[LoadedTask]:
    if not TASKS_DIR.is_dir():
        pytest.skip("real task corpus not present")
    return discover_tasks(TASKS_DIR)


def test_real_corpus_has_exactly_three_development_tasks(real_tasks: list[LoadedTask]) -> None:
    dev = [task for task in real_tasks if task.manifest.split.value == "development"]
    assert len(dev) == 3
    assert {task.manifest.id for task in dev} == {
        "inflection-titleize",
        "tomli-type-error",
        "schedule-repr-partial-job",
    }


def test_real_corpus_manifests_and_patches_valid(real_tasks: list[LoadedTask]) -> None:
    for task in real_tasks:
        assert check_task_corpus(task) == []
        for split in (PatchSplit.DEVELOPMENT,):
            for patch in discover_patches(task.task_dir, split):
                assert patch.manifest.task_id == task.manifest.id
                assert patch.manifest.split.value == task.manifest.split.value


def test_real_corpus_meets_development_minimums(real_tasks: list[LoadedTask]) -> None:
    assert check_development_corpus_minimums(real_tasks) == []


def test_fixture_corpus_fails_development_minimums() -> None:
    fixtures = discover_tasks(Path(__file__).resolve().parent / "fixtures")
    errors = check_development_corpus_minimums(fixtures)
    assert any("exactly 3 tasks" in error for error in errors)


def test_real_corpus_patch_counts_and_families(real_tasks: list[LoadedTask]) -> None:
    valid: list[LoadedPatch] = []
    invalid: list[LoadedPatch] = []
    families: set[str] = set()
    for task in real_tasks:
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
            if patch.manifest.label.value == "valid":
                valid.append(patch)
            else:
                invalid.append(patch)
                assert patch.manifest.attack_family is not None
                families.add(patch.manifest.attack_family)
    assert len(valid) >= 5
    assert len(invalid) >= 12
    assert families >= {"test-weakening", "skip-injection", "hardcoded-overfit"}
    assert len(families) >= 4
    non_gold = [patch for patch in valid if patch.manifest.subtype.value != "gold"]
    assert len(non_gold) >= 3
    assert any(patch.manifest.subtype.value == "unusual_valid" for patch in non_gold)


def test_real_corpus_is_not_pytest_collected() -> None:
    """The real corpus must never enter the repository test suite."""
    collected = _pytest_collect_only_nodeids()
    task_paths = [node for node in collected if "tasks/" in node or "tasks\\" in node]
    assert task_paths == []


def _pytest_collect_only_nodeids() -> list[str]:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if "<" not in line]
    return [line.strip() for line in lines if line.strip()]


def test_real_corpus_baselines_have_no_vcs_or_caches() -> None:
    forbidden = {".git", ".pytest_cache", "__pycache__"}
    for task in discover_tasks(TASKS_DIR):
        baseline = task.task_dir / task.manifest.workspace.source_dir
        for path in baseline.rglob("*"):
            if path.is_dir() and path.name in forbidden:
                pytest.fail(f"forbidden directory in vendored baseline: {path}")
        assert not (baseline / ".git").exists()


def test_real_corpus_vendored_tree_hash_matches_manifest(
    real_tasks: list[LoadedTask],
) -> None:
    from grader_audit.core.hashing import hash_tree

    for task in real_tasks:
        actual = hash_tree(task.task_dir / task.manifest.workspace.source_dir)
        assert actual == task.manifest.source.vendored_tree_sha256


def test_task_dockerfile_pins_base_image_by_digest() -> None:
    text = task_dockerfile_text()
    assert "FROM python:3.12-slim@sha256:" in text
    assert "uv pip install --system --require-hashes -r /opt/task/requirements.lock" in text
    assert text.endswith("\n")


def test_task_image_tag_is_content_addressed(real_tasks: list[LoadedTask]) -> None:
    first = discover_tasks(TASKS_DIR)[0]
    tag = task_image_tag(first)
    assert tag.startswith("grader-audit-task-")
    assert len(tag.rsplit(":", 1)[1]) == 16


def test_draft_disposition_valid_confirmed() -> None:
    task = discover_tasks(TASKS_DIR)[0]
    patch = _load_patch(task.task_dir, "gold")
    oracle: dict[str, object] = {"status": "completed", "passed": True, "reason_codes": []}
    auth: dict[str, object] = {"accepted": True, "reason_codes": []}
    draft = draft_disposition(patch, oracle, auth)
    assert draft["disposition"] == "confirmed"


def test_draft_disposition_invalid_confirmed() -> None:
    task = discover_tasks(TASKS_DIR)[0]
    patch = _load_patch(task.task_dir, "weaken-visible-tests")
    oracle: dict[str, object] = {
        "status": "completed",
        "passed": False,
        "reason_codes": ["authoritative_tests_failed"],
    }
    auth: dict[str, object] = {"accepted": False, "reason_codes": ["authoritative_tests_failed"]}
    draft = draft_disposition(patch, oracle, auth)
    assert draft["disposition"] == "confirmed"


def test_draft_disposition_conflict_flags_relabel() -> None:
    task = discover_tasks(TASKS_DIR)[0]
    patch = _load_patch(task.task_dir, "gold")
    oracle: dict[str, object] = {
        "status": "completed",
        "passed": False,
        "reason_codes": ["authoritative_tests_failed"],
    }
    auth: dict[str, object] = {"accepted": False, "reason_codes": ["authoritative_tests_failed"]}
    draft = draft_disposition(patch, oracle, auth)
    assert draft["disposition"] == "relabel_required"


def test_draft_disposition_oracle_infra_is_ambiguous() -> None:
    task = discover_tasks(TASKS_DIR)[0]
    patch = _load_patch(task.task_dir, "gold")
    oracle: dict[str, object] = {
        "status": "infrastructure_error",
        "passed": False,
        "reason_codes": [],
    }
    auth: dict[str, object] = {"accepted": True, "reason_codes": []}
    draft = draft_disposition(patch, oracle, auth)
    assert draft["disposition"] == "ambiguous"
