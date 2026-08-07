"""Adversarial tests for ``reproduce`` (Section 27.15)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from grader_audit.cli import app
from grader_audit.core.manifests import LoadedPatch, LoadedTask, discover_patches, load_task
from grader_audit.core.models import PatchSplit, Split
from grader_audit.core.reproduce import ReproduceError, reproduce
from tests.integration.gate5_fixtures import add_heldout_task, make_frozen_repo

cli_runner = CliRunner()


def _tag_ref(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "refs/tags/grader-v1-frozen"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_reproduce_refuses_existing_experiment(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    (root / "results" / "raw" / "rep-001").mkdir(parents=True)
    with pytest.raises(ReproduceError) as excinfo:
        reproduce(
            project_root=root,
            tasks_dir=root / "tasks",
            raw_results_root=root / "results" / "raw",
            experiment_id="rep-001",
        )
    assert excinfo.value.code == 2


def test_reproduce_never_moves_tag(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    before = _tag_ref(root)
    (root / "results" / "raw" / "rep-001").mkdir(parents=True)
    with pytest.raises(ReproduceError):
        reproduce(
            project_root=root,
            tasks_dir=root / "tasks",
            raw_results_root=root / "results" / "raw",
            experiment_id="rep-001",
        )
    after = _tag_ref(root)
    assert before == after
    tags = subprocess.run(
        ["git", "-C", str(root), "tag", "--list"], capture_output=True, text=True, check=True
    ).stdout.strip().splitlines()
    assert tags == ["grader-v1-frozen"]


def test_reproduce_invalid_experiment_id(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    with pytest.raises(ReproduceError) as excinfo:
        reproduce(
            project_root=root,
            tasks_dir=root / "tasks",
            raw_results_root=root / "results" / "raw",
            experiment_id="BAD ID!",
        )
    assert excinfo.value.code == 2


def test_reproduce_no_model_or_network_in_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight (before docker) must not import model/network machinery."""
    root = make_frozen_repo(tmp_path)

    def _fail_import(name: str) -> None:
        raise AssertionError(f"forbidden import during reproduce preflight: {name}")

    monkeypatch.setattr(
        "grader_audit.core.reproduce.build_task_image", _fail_import
    )
    (root / "results" / "raw" / "rep-001").mkdir(parents=True)
    with pytest.raises(ReproduceError, match="already exists"):
        reproduce(
            project_root=root,
            tasks_dir=root / "tasks",
            raw_results_root=root / "results" / "raw",
            experiment_id="rep-001",
        )


def test_reproduce_success_orchestrates_both_matrices_before_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root)
    tag_before = _tag_ref(root)
    frozen = load_task(root / "tasks" / "heldout-task")
    dev = LoadedTask(
        task_dir=frozen.task_dir,
        manifest=frozen.manifest.model_copy(
            update={"id": "development-task", "split": Split.DEVELOPMENT}
        ),
        manifest_sha256=frozen.manifest_sha256,
        raw_yaml=frozen.raw_yaml,
    )
    patch = discover_patches(frozen.task_dir, PatchSplit.FROZEN_EVAL)[0]
    events: list[str] = []

    def fake_doctor(_root: Path) -> SimpleNamespace:
        return SimpleNamespace(all_ok=True, checks=[])

    def fake_discover_tasks(_tasks: Path) -> list[LoadedTask]:
        return [dev, frozen]

    def fake_discover_patches(_task_dir: Path, _split: PatchSplit) -> list[LoadedPatch]:
        return [patch]

    def no_corpus_errors(_task: LoadedTask) -> list[str]:
        return []

    def no_minimum_errors(_tasks: list[LoadedTask]) -> list[str]:
        return []

    def no_lock_errors(_tasks: list[LoadedTask]) -> list[str]:
        return []

    def annotation_preflight(*_args: object) -> None:
        events.append("annotation-preflight")

    def verify_freeze(*_args: object) -> None:
        events.append("verify-freeze")

    def verify_heldout(*_args: object) -> None:
        events.append("verify-heldout")

    monkeypatch.setattr(
        "grader_audit.core.reproduce.run_doctor",
        fake_doctor,
    )
    monkeypatch.setattr(
        "grader_audit.core.reproduce.discover_tasks", fake_discover_tasks
    )
    monkeypatch.setattr(
        "grader_audit.core.reproduce.discover_patches", fake_discover_patches
    )
    monkeypatch.setattr("grader_audit.core.reproduce.check_task_corpus", no_corpus_errors)
    monkeypatch.setattr(
        "grader_audit.core.reproduce.check_development_corpus_minimums", no_minimum_errors
    )
    monkeypatch.setattr(
        "grader_audit.core.reproduce.verify_task_image_locks", no_lock_errors
    )
    monkeypatch.setattr(
        "grader_audit.core.reproduce.require_confirmed_annotation",
        annotation_preflight,
    )
    monkeypatch.setattr(
        "grader_audit.core.reproduce.verify_frozen_lock",
        verify_freeze,
    )
    monkeypatch.setattr(
        "grader_audit.core.reproduce.verify_heldout_selection",
        verify_heldout,
    )
    monkeypatch.setattr(
        "grader_audit.core.reproduce.DockerRunner",
        lambda: SimpleNamespace(),
    )

    def fake_build(task: LoadedTask) -> str:
        events.append(f"build:{task.manifest.split.value}")
        return "sha256:" + "1" * 64

    def fake_validation(task: LoadedTask, **_kwargs: object) -> SimpleNamespace:
        events.append(f"validate:{task.manifest.split.value}")
        return SimpleNamespace(stable=True)

    def fake_controlled(task: LoadedTask, **_kwargs: object) -> list[object]:
        events.append(f"controlled:{task.manifest.split.value}")
        return []

    def fake_heldout(**_kwargs: object) -> SimpleNamespace:
        events.append("heldout:frozen_eval")
        return SimpleNamespace()

    def fake_bind(
        _raw: Path,
        _annotations: Path,
        _experiment: str,
        _graders: tuple[str, ...],
        split: str,
        _task: str,
        _patch: str,
    ) -> None:
        events.append(f"bind:{split}")

    def fake_report(*, output_path: Path, **_kwargs: object) -> str:
        events.append("report")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("report\n", encoding="utf-8")
        return "report\n"

    monkeypatch.setattr("grader_audit.core.reproduce.build_task_image", fake_build)
    monkeypatch.setattr("grader_audit.core.reproduce.run_validation", fake_validation)
    monkeypatch.setattr("grader_audit.core.reproduce.run_controlled", fake_controlled)
    monkeypatch.setattr("grader_audit.core.reproduce.run_heldout", fake_heldout)
    monkeypatch.setattr("grader_audit.core.reproduce.bind_patch_raw_hashes", fake_bind)
    monkeypatch.setattr("grader_audit.core.reproduce.run_report", fake_report)

    result = reproduce(
        project_root=root,
        tasks_dir=root / "tasks",
        raw_results_root=Path("results/raw"),
        annotations_root=Path("results/annotations"),
        experiment_id="rep-001",
    )

    assert result.report_path.is_file()
    assert _tag_ref(root) == tag_before
    metadata = root / "results" / "raw" / "rep-001" / "metadata.json"
    plan = json.loads(metadata.read_text(encoding="utf-8"))["plan"]
    assert len(plan["controlled"]) == 4
    assert {cell["phase"] for cell in plan["controlled"]} == {"controlled", "heldout"}
    assert len(plan["validation"]) == 12
    assert events.index("verify-freeze") < events.index("build:development")
    assert events.index("validate:development") < events.index("controlled:development")
    assert events.index("validate:frozen_eval") < events.index("heldout:frozen_eval")
    assert events.index("bind:development") < events.index("report")
    assert events.index("bind:frozen_eval") < events.index("report")
    assert result.steps[-2:] == ("bind-raw-record-hashes", "report")


def test_cli_help_lists_run_heldout_report_reproduce() -> None:
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run-heldout" in result.output
    assert "report" in result.output
    assert "reproduce" in result.output
