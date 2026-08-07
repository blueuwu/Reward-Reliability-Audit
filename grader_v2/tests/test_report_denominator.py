"""Report denominator separation: v1/v2 results never mix (Gate E item 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grader_v2.grading.records import (
    V2EvaluationRecord,
    V2Experiment,
    V2Git,
    V2Outcome,
    V2SubOutcome,
    V2Truth,
    V2WorkspaceHashes,
    load_v2_experiment,
    render_v2_summary,
    write_v2_record,
)
from grader_v2.publication import v2_denominator_problems


def _record(
    patch_id: str,
    *,
    label: str,
    status: str = "completed",
    reward: float | None = 0.0,
    split: str = "frozen_eval",
    source: str = "confirmed-annotation:v2-heldout",
) -> V2EvaluationRecord:
    outcome = V2Outcome(
        status=status,
        reward=reward,
        reason_codes=["semantic_tests_failed"] if status == "completed" and reward == 0.0 else [],
    )
    v1 = V2SubOutcome(status="completed", reward=1.0, reason_codes=[])
    return V2EvaluationRecord(
        experiment_id="test-denominators",
        timestamp_utc="2026-08-07T00:00:00+00:00",
        task_id="tinydb-missing-doc-ids",
        split=split,
        patch_id=patch_id,
        patch_diff_sha256="d" * 64,
        truth=V2Truth(label=label, source=source, reviewer="review"),
        outcome=outcome,
        v1_outcome=v1,
        semantic=None,
        workspace=V2WorkspaceHashes(
            pristine_sha256="p",
            pre_grade_sha256="g",
            post_grade_sha256="h",
        ),
        git=V2Git(data_commit="c", worktree_dirty=False),
        duration_seconds=1.0,
    )


def _infra_record(patch_id: str, label: str = "invalid") -> V2EvaluationRecord:
    return _record(
        patch_id,
        label=label,
        status="infrastructure_error",
        reward=None,
    )


def test_v1_and_v2_denominators_are_distinct_schemas() -> None:
    v2 = _record("a", label="invalid")
    dumped = v2.model_dump(mode="json")
    assert dumped["schema_version"] == "2.0"
    assert dumped["kind"] == "grader_v2_evaluation"
    assert dumped["grader_version"] == "hardened_v2"
    assert "v1_outcome" in dumped
    assert "semantic" in dumped


def test_infrastructure_outcomes_excluded_from_denominators() -> None:
    records = [
        _record("invalid-a", label="invalid"),
        _record("invalid-b", label="invalid"),
        _infra_record("invalid-c"),
        _record("valid-a", label="valid", reward=1.0),
        _infra_record("valid-b", label="valid"),
    ]
    experiment = V2Experiment(experiment_id="test-denominators", records=records)
    summary = render_v2_summary(experiment)
    assert "## Status: INCOMPLETE" in summary
    for problem in ("invalid-c", "valid-b"):
        assert problem in summary
    valid = [r for r in records if r.truth.label == "valid" and not r.is_infrastructure]
    invalid = [r for r in records if r.truth.label == "invalid" and not r.is_infrastructure]
    assert f"False reward rate | 0 / {len(invalid)} (0.0%)" in summary
    assert f"False rejection rate | 0 / {len(valid)} (0.0%)" in summary
    assert "never counted as solution outcomes" in summary


def test_false_reward_counted_against_invalid_only() -> None:
    records = [
        _record("bad", label="invalid", reward=1.0),
        _record("ok", label="invalid"),
        _record("fine", label="valid", reward=1.0),
    ]
    experiment = V2Experiment(experiment_id="test-denominators", records=records)
    summary = render_v2_summary(experiment)
    assert "False reward rate | 1 / 2 (50.0%)" in summary
    assert "False rejection rate | 0 / 1 (0.0%)" in summary


def test_denominator_problems_flag_non_binary_rewards() -> None:
    experiment = V2Experiment(
        experiment_id="x", records=[_record("a", label="invalid", reward=0.5)]
    )
    problems = v2_denominator_problems(experiment)
    assert any("non-binary v2 reward" in problem for problem in problems)


def test_denominator_problems_flag_infra_with_reward() -> None:
    record = _record("a", label="invalid", status="infrastructure_error", reward=1.0)
    experiment = V2Experiment(experiment_id="x", records=[record])
    problems = v2_denominator_problems(experiment)
    assert any("infrastructure outcome carries a reward" in problem for problem in problems)


def test_denominator_problems_flag_unexpected_status() -> None:
    record = _record("a", label="invalid", status="running", reward=None)
    experiment = V2Experiment(experiment_id="x", records=[record])
    problems = v2_denominator_problems(experiment)
    assert any("unexpected v2 status" in problem for problem in problems)


def test_write_and_load_round_trip(tmp_path: Path) -> None:
    experiment_dir = tmp_path / "raw" / "v2-heldout"
    record = _record("gold", label="valid", reward=1.0)
    path = write_v2_record(record, experiment_dir)
    assert path.is_file()
    experiment = load_v2_experiment(experiment_dir)
    assert experiment.experiment_id == "test-denominators"
    assert len(experiment.records) == 1
    assert experiment.records[0].patch_id == "gold"


def test_load_rejects_v1_records_in_v2_experiment(tmp_path: Path) -> None:
    experiment_dir = tmp_path / "raw" / "v2-heldout"
    v1_record = {
        "schema_version": "1.0",
        "experiment_id": "clean-clone-reproduction",
        "grader": "hardened_v1",
        "task_id": "inflection-titleize",
        "patch_id": "gold",
        "split": "frozen_eval",
    }
    path = experiment_dir / "frozen_eval" / "inflection-titleize" / "gold.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(v1_record), encoding="utf-8")
    with pytest.raises(ValueError):
        load_v2_experiment(experiment_dir)


def test_load_rejects_mixed_experiment_ids(tmp_path: Path) -> None:
    experiment_dir = tmp_path / "raw" / "v2-heldout"
    record_a = _record("a", label="invalid")
    record_b = _record("b", label="invalid")
    record_b.experiment_id = "other-experiment"
    write_v2_record(record_a, experiment_dir)
    write_v2_record(record_b, experiment_dir)
    with pytest.raises(ValueError, match="mixed experiment ids"):
        load_v2_experiment(experiment_dir)
