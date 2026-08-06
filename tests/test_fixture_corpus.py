"""Corpus validation of the synthetic fixture repositories (Sections 27.5, 27.19)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grader_audit.core.manifests import LoadedTask, discover_patches, discover_tasks
from grader_audit.core.models import PatchSplit
from grader_audit.core.orchestrator import check_task_corpus

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def fixture_tasks() -> list[LoadedTask]:
    return discover_tasks(FIXTURES_DIR)


def test_fixture_corpus_is_valid(fixture_tasks: list[LoadedTask]) -> None:
    assert len(fixture_tasks) == 2
    for task in fixture_tasks:
        assert check_task_corpus(task) == []


def test_fixture_patch_counts(fixture_tasks: list[LoadedTask]) -> None:
    by_id = {task.manifest.id: task for task in fixture_tasks}
    stringutil = by_id["fixture-stringutil"]
    wordcount = by_id["fixture-wordcount"]

    stringutil_dev = discover_patches(stringutil.task_dir, PatchSplit.DEVELOPMENT)
    assert len(stringutil_dev) == 8
    valid = [p for p in stringutil_dev if p.manifest.label.value == "valid"]
    invalid = [p for p in stringutil_dev if p.manifest.label.value == "invalid"]
    assert len(valid) == 2
    assert len(invalid) == 6
    subtypes = {p.manifest.subtype.value for p in invalid}
    assert subtypes >= {"reward_hack", "runtime_manipulation", "scope_violation"}

    wordcount_dev = discover_patches(wordcount.task_dir, PatchSplit.DEVELOPMENT)
    assert len(wordcount_dev) == 3


def test_fixture_patches_reference_their_task(fixture_tasks: list[LoadedTask]) -> None:
    for task in fixture_tasks:
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
            assert patch.manifest.task_id == task.manifest.id
            assert patch.manifest.split.value == task.manifest.split.value


def test_fixture_expected_nodeids_nonempty_and_unique(fixture_tasks: list[LoadedTask]) -> None:
    for task in fixture_tasks:
        assert task.manifest.grading.hardened_v1.expected_nodeids
        assert task.manifest.grading.oracle.expected_nodeids
        assert len(task.manifest.grading.hardened_v1.expected_nodeids) == len(
            set(task.manifest.grading.hardened_v1.expected_nodeids)
        )
