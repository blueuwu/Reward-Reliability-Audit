"""CLI end-to-end: run-controlled with confirmed annotations (Sections 27.15, 27.18)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml
from typer.testing import CliRunner

from grader_audit.cli import app
from grader_audit.core.manifests import discover_patches, discover_tasks, load_task
from grader_audit.core.models import PatchSplit
from tests.conftest import FIXTURES_DIR, requires_docker

cli_runner = CliRunner()


def _write_annotations(annotations_root: Path, experiment_id: str) -> None:
    for task in discover_tasks(FIXTURES_DIR):
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
            annotation = {
                "schema_version": "1.0",
                "reviewer": "gate1-fixture",
                "timestamp_utc": "2026-08-06T00:00:00+00:00",
                "disposition": "confirmed",
                "truth_label": patch.manifest.label.value,
                "reason": "fixture truth label established by the offline oracle",
                "recorded_patch_hashes": {
                    "metadata_sha256": patch.metadata_sha256,
                    "diff_sha256": patch.diff_sha256,
                },
            }
            path = (
                annotations_root
                / experiment_id
                / task.manifest.id
                / f"{patch.manifest.id}.yaml"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(annotation, sort_keys=True), encoding="utf-8")


@requires_docker
def test_run_controlled_cli_end_to_end() -> None:
    with tempfile.TemporaryDirectory(prefix="ga-cli-") as tmp:
        results_root = Path(tmp) / "results"
        annotations_root = Path(tmp) / "annotations"
        experiment_id = "gate1-cli-controlled"
        _write_annotations(annotations_root, experiment_id)
        result = cli_runner.invoke(
            app,
            [
                "run-controlled",
                "--tasks",
                str(FIXTURES_DIR),
                "--graders",
                "naive,hardened_v1",
                "--experiment-id",
                experiment_id,
                "--results-root",
                str(results_root),
                "--annotations-root",
                str(annotations_root),
            ],
        )
        assert result.exit_code == 0, result.output
        raw = results_root / experiment_id
        assert (raw / "metadata.json").is_file()
        assert (
            raw
            / "naive"
            / "development"
            / "fixture-stringutil"
            / "weaken-visible-tests.json"
        ).is_file()
        assert (
            raw
            / "hardened_v1"
            / "development"
            / "fixture-stringutil"
            / "weaken-visible-tests.json"
        ).is_file()


@requires_docker
def test_run_controlled_refuses_missing_annotation() -> None:
    with tempfile.TemporaryDirectory(prefix="ga-cli-") as tmp:
        results_root = Path(tmp) / "results"
        annotations_root = Path(tmp) / "annotations"
        experiment_id = "gate1-cli-missing-annotation"
        result = cli_runner.invoke(
            app,
            [
                "run-controlled",
                "--tasks",
                str(FIXTURES_DIR),
                "--graders",
                "naive,hardened_v1",
                "--experiment-id",
                experiment_id,
                "--results-root",
                str(results_root),
                "--annotations-root",
                str(annotations_root),
            ],
        )
        assert result.exit_code == 2
        assert "lacks a confirmed truth annotation" in result.output


@requires_docker
def test_run_controlled_refuses_hash_mismatch_annotation() -> None:
    with tempfile.TemporaryDirectory(prefix="ga-cli-") as tmp:
        results_root = Path(tmp) / "results"
        annotations_root = Path(tmp) / "annotations"
        experiment_id = "gate1-cli-bad-annotation"
        task = load_task(FIXTURES_DIR / "fixture-stringutil")
        patch = discover_patches(task.task_dir, PatchSplit.DEVELOPMENT)[0]
        annotation = {
            "schema_version": "1.0",
            "reviewer": "gate1-fixture",
            "timestamp_utc": "2026-08-06T00:00:00+00:00",
            "disposition": "confirmed",
            "truth_label": patch.manifest.label.value,
            "reason": "wrong hashes",
            "recorded_patch_hashes": {
                "metadata_sha256": "0" * 64,
                "diff_sha256": "0" * 64,
            },
        }
        path = (
            annotations_root
            / experiment_id
            / task.manifest.id
            / f"{patch.manifest.id}.yaml"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(annotation, sort_keys=True), encoding="utf-8")
        result = cli_runner.invoke(
            app,
            [
                "run-controlled",
                "--tasks",
                str(FIXTURES_DIR),
                "--graders",
                "naive,hardened_v1",
                "--experiment-id",
                experiment_id,
                "--results-root",
                str(results_root),
                "--annotations-root",
                str(annotations_root),
            ],
        )
        assert result.exit_code == 2
        assert "annotation metadata hash does not match" in result.output
