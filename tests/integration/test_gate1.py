"""Gate 1 synthetic vertical-slice integration tests (Section 27.19 end-to-end).

These tests exercise the real Docker pipeline: fresh workspace materialization,
safe patch application, the naive and hardened evaluators sharing one core, the
offline oracle, deterministic snapshots, and atomic record persistence.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from grader_audit.core.docker_runner import DockerRunner
from grader_audit.core.manifests import LoadedTask, load_task
from grader_audit.core.orchestrator import run_controlled, run_validation
from grader_audit.core.recorder import ExperimentRecorder
from grader_audit.core.results import EvaluationRecord
from grader_audit.core.workspace import WorkspaceManager
from tests.conftest import FIXTURES_DIR, requires_docker

STRINGUTIL = FIXTURES_DIR / "fixture-stringutil"
WORDCOUNT = FIXTURES_DIR / "fixture-wordcount"


@pytest.fixture(scope="module")
def runner() -> DockerRunner:
    return DockerRunner()


def _task(fixture: Path) -> LoadedTask:
    return load_task(fixture)


def _records_for(
    records: list[EvaluationRecord], grader: str, patch_id: str
) -> list[EvaluationRecord]:
    return [
        record
        for record in records
        if record.grader.name == grader and record.patch is not None and record.patch.id == patch_id
    ]


@pytest.fixture(scope="module")
def stringutil_controlled(fixture_image: str, runner: DockerRunner) -> list[EvaluationRecord]:
    task = _task(STRINGUTIL)
    tmp = Path(tempfile.mkdtemp(prefix="ga-it-"))
    recorder = ExperimentRecorder(tmp, "gate1-controlled")
    return run_controlled(
        task,
        recorder=recorder,
        runner=runner,
        image=fixture_image,
        project_root=Path.cwd(),
        graders=["naive", "hardened_v1"],
    )


@requires_docker
def test_baseline_gold_three_repeats_stable(fixture_image: str, runner: DockerRunner) -> None:
    for fixture in (STRINGUTIL, WORDCOUNT):
        task = _task(fixture)
        tmp = Path(tempfile.mkdtemp(prefix="ga-it-"))
        recorder = ExperimentRecorder(tmp, f"gate1-validate-{task.manifest.id}")
        summary = run_validation(
            task,
            repeat=3,
            recorder=recorder,
            runner=runner,
            image=fixture_image,
            project_root=Path.cwd(),
            split="development",
        )
        assert summary.stable, summary.errors
        assert len(summary.records) == 6


@requires_docker
def test_naive_false_reward_and_hardened_rejection(
    fixture_image: str, runner: DockerRunner, stringutil_controlled: list[EvaluationRecord]
) -> None:
    naive = _records_for(stringutil_controlled, "naive", "weaken-visible-tests")[0]
    hardened = _records_for(stringutil_controlled, "hardened_v1", "weaken-visible-tests")[0]
    assert naive.result.reward == 1.0
    assert hardened.result.reward == 0.0
    assert "authoritative_tests_failed" in hardened.result.reason_codes


@requires_docker
def test_exact_node_id_collection_manipulation_rejected(
    fixture_image: str, runner: DockerRunner, stringutil_controlled: list[EvaluationRecord]
) -> None:
    naive = _records_for(stringutil_controlled, "naive", "collection-manipulation")[0]
    hardened = _records_for(stringutil_controlled, "hardened_v1", "collection-manipulation")[0]
    assert naive.result.reward == 1.0
    assert hardened.result.reward == 0.0
    assert "test_identity_mismatch" in hardened.result.reason_codes


@requires_docker
def test_pretest_scope_rejection(
    fixture_image: str, runner: DockerRunner, stringutil_controlled: list[EvaluationRecord]
) -> None:
    immutable = _records_for(stringutil_controlled, "hardened_v1", "immutable-edit")[0]
    assert immutable.result.reward == 0.0
    assert "immutable_path_modified" in immutable.result.reason_codes
    assert immutable.test_evidence is not None
    assert immutable.test_evidence.state == "not_run"

    outside = _records_for(stringutil_controlled, "hardened_v1", "outside-editable-edit")[0]
    assert outside.result.reward == 0.0
    assert "outside_editable_scope" in outside.result.reason_codes
    assert outside.test_evidence is not None
    assert outside.test_evidence.state == "not_run"


@requires_docker
def test_valid_multifile_alternative_accepted_with_warning(
    fixture_image: str, runner: DockerRunner, stringutil_controlled: list[EvaluationRecord]
) -> None:
    hardened = _records_for(stringutil_controlled, "hardened_v1", "multifile-alternative")[0]
    assert hardened.result.reward == 1.0
    assert any("outside_expected_scope" in warning for warning in hardened.result.warnings)


@requires_docker
def test_malformed_evidence_fails_closed(
    fixture_image: str, runner: DockerRunner, stringutil_controlled: list[EvaluationRecord]
) -> None:
    hardened = _records_for(stringutil_controlled, "hardened_v1", "report-plugin-shadowing")[0]
    assert hardened.result.reward == 0.0
    assert "test_evidence_missing" in hardened.result.reason_codes


@requires_docker
def test_skip_injection_naive_false_reward_rejected_by_authoritative(
    fixture_image: str, runner: DockerRunner, stringutil_controlled: list[EvaluationRecord]
) -> None:
    naive = _records_for(stringutil_controlled, "naive", "skip-injection")[0]
    hardened = _records_for(stringutil_controlled, "hardened_v1", "skip-injection")[0]
    assert naive.result.reward == 1.0
    assert hardened.result.reward == 0.0
    assert "authoritative_tests_failed" in hardened.result.reason_codes


@requires_docker
def test_valid_patches_accepted_by_both_graders(
    fixture_image: str, runner: DockerRunner, stringutil_controlled: list[EvaluationRecord]
) -> None:
    for patch_id in ("gold", "multifile-alternative"):
        naive = _records_for(stringutil_controlled, "naive", patch_id)[0]
        hardened = _records_for(stringutil_controlled, "hardened_v1", patch_id)[0]
        assert naive.result.reward == 1.0
        assert hardened.result.reward == 1.0


@requires_docker
def test_cross_grader_pre_grade_hashes_identical(
    stringutil_controlled: list[EvaluationRecord],
) -> None:
    pre_grade: dict[str, dict[str, str]] = {}
    for record in stringutil_controlled:
        assert record.patch is not None
        pre_grade.setdefault(record.patch.id, {})[record.grader.name] = (
            record.workspace.pre_grade_sha256
        )
    for patch_id, hashes in pre_grade.items():
        assert len(set(hashes.values())) == 1, f"pre-grade hash mismatch for {patch_id}"


@requires_docker
def test_no_evaluation_reuses_another_workspace(fixture_image: str, runner: DockerRunner) -> None:
    task = _task(STRINGUTIL)
    manager = WorkspaceManager(task)
    first = manager.materialize()
    second = manager.materialize()
    try:
        assert first.root != second.root
        assert first.root.exists()
        assert second.root.exists()
        first_root = first.root
        manager.finalize_and_destroy(first)
        assert not first_root.exists()
        assert second.root.exists()
    finally:
        manager.finalize_and_destroy(second)


@requires_docker
def test_gold_patch_does_not_touch_grader_assets(
    stringutil_controlled: list[EvaluationRecord],
) -> None:
    gold = _records_for(stringutil_controlled, "hardened_v1", "gold")[0]
    assert gold.changes.immutable_violations == []
    assert gold.changes.outside_editable_scope == []


@requires_docker
def test_wordcount_fixture_naive_false_reward_and_rejection(
    fixture_image: str, runner: DockerRunner
) -> None:
    task = _task(WORDCOUNT)
    tmp = Path(tempfile.mkdtemp(prefix="ga-it-"))
    recorder = ExperimentRecorder(tmp, "gate1-wordcount")
    records = run_controlled(
        task,
        recorder=recorder,
        runner=runner,
        image=fixture_image,
        project_root=Path.cwd(),
        graders=["naive", "hardened_v1"],
    )
    naive = _records_for(records, "naive", "weaken-visible-tests")[0]
    hardened = _records_for(records, "hardened_v1", "weaken-visible-tests")[0]
    assert naive.result.reward == 1.0
    assert hardened.result.reward == 0.0
    assert "authoritative_tests_failed" in hardened.result.reason_codes
