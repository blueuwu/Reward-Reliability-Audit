"""Gate 3 docker-backed integration tests for the real development corpus.

These tests exercise ``build-images``, ``label-patches``, and ``run-controlled``
against the vendored real tasks. They are skipped when Docker is unavailable.
The full 3-task matrix is driven by the CLI during the gate run; these tests use
a reduced scope (one task, one grader) so the pytest suite stays proportionate.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from typer.testing import CliRunner

from grader_audit.cli import app
from grader_audit.core.manifests import discover_tasks
from tests.conftest import requires_docker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = PROJECT_ROOT / "tasks"

cli_runner = CliRunner()


@requires_docker
def test_build_images_writes_locked_digests() -> None:
    result = cli_runner.invoke(
        app,
        ["build-images", str(TASKS_DIR), "--split", "development"],
    )
    assert result.exit_code == 0, result.output
    task = discover_tasks(TASKS_DIR)[0]
    lock = task.task_dir / "image.lock.json"
    assert lock.is_file()
    data = json.loads(lock.read_text(encoding="utf-8"))
    assert data["build_digest"].startswith("sha256:")
    assert data["build_platform"] == "linux/amd64"
    assert data["task_manifest_sha256"] == task.manifest_sha256
    assert data["baseline_tree_sha256"] == task.manifest.source.vendored_tree_sha256


@requires_docker
def test_label_patches_writes_confirmed_evidence_for_gold() -> None:
    with tempfile.TemporaryDirectory(prefix="ga-gate3-labeling-") as tmp:
        results_root = Path(tmp) / "results"
        labeling_id = "gate3-test-labeling"
        result = cli_runner.invoke(
            app,
            [
                "label-patches",
                str(TASKS_DIR),
                "--split",
                "development",
                "--labeling-id",
                labeling_id,
                "--labeling-root",
                str(results_root),
            ],
        )
        assert result.exit_code == 0, result.output
        task = discover_tasks(TASKS_DIR)[0]
        evidence = json.loads(
            (
                results_root
                / labeling_id
                / "development"
                / task.manifest.id
                / "gold.json"
            ).read_text(encoding="utf-8")
        )
        assert evidence["oracle"]["passed"] is True
        assert evidence["draft_annotation"]["disposition"] == "confirmed"
        assert evidence["patch"]["id"] == "gold"
        draft = (
            results_root
            / labeling_id
            / "annotations"
            / task.manifest.id
            / "gold.yaml"
        )
        assert draft.is_file()
