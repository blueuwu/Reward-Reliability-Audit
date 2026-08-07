"""Adversarial unit/integration tests for ``run-heldout`` (Section 27.14/27.15)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from grader_audit.core.annotations import AnnotationMismatchError, bind_raw_record_hashes
from grader_audit.core.heldout import (
    FrozenViolationError,
    HeldoutInputError,
    run_heldout,
    verify_frozen_lock,
    verify_heldout_selection,
)
from grader_audit.core.process import CommandSpec, Mount, ProcessResult
from tests.integration.gate5_fixtures import (
    add_heldout_task,
    make_frozen_repo,
)


def _tag_commit(root: Path) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "grader-v1-frozen^{commit}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _write(root: Path, rel: str, data: str = "x") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8", newline="\n")


def _commit(root: Path, message: str) -> None:
    import subprocess

    assert subprocess.run(
        ["git", "-C", str(root), "add", "-A"], capture_output=True, check=True
    ).returncode == 0
    assert subprocess.run(
        ["git", "-C", str(root), "commit", "-m", message],
        capture_output=True,
        text=True,
        check=True,
    ).returncode == 0


class _RaisingRunner:
    def run(
        self,
        spec: CommandSpec,
        *,
        mounts: Sequence[Mount],
        image: str,
        memory_mb: int,
        pids_limit: int,
    ) -> ProcessResult:
        raise RuntimeError("stub runner failure")


# ---------------------------------------------------------------------------
# verify_frozen_lock
# ---------------------------------------------------------------------------


def test_frozen_lock_passes_on_valid_freeze(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    lock = verify_frozen_lock(root, "grader-v1-frozen")
    assert lock["git_tag"] == "grader-v1-frozen"


def test_frozen_lock_accepts_new_heldout_task(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root)
    verify_frozen_lock(root, "grader-v1-frozen")  # must not raise


def test_frozen_lock_rejects_lightweight_tag(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    import subprocess

    subprocess.run(
        ["git", "-C", str(root), "tag", "-d", "grader-v1-frozen"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "tag", "grader-v1-frozen"],
        capture_output=True,
        check=True,
    )
    with pytest.raises(FrozenViolationError, match="not an annotated tag"):
        verify_frozen_lock(root, "grader-v1-frozen")


def test_frozen_lock_rejects_missing_tag(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    with pytest.raises(FrozenViolationError, match="missing"):
        verify_frozen_lock(root, "nonexistent-tag")


def test_frozen_lock_rejects_modified_protected_file(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    _write(root, "grader_audit/core.py", "changed")
    with pytest.raises(FrozenViolationError, match="protected hash mismatch"):
        verify_frozen_lock(root, "grader-v1-frozen")


def test_frozen_lock_rejects_missing_protected_file(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    (root / "grader_audit" / "core.py").unlink()
    with pytest.raises(FrozenViolationError, match="protected file missing"):
        verify_frozen_lock(root, "grader-v1-frozen")


def test_frozen_lock_rejects_modified_lock(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    lock_path = root / "freeze" / "grader_v1.lock.json"
    lock_path.write_text(lock_path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(FrozenViolationError, match="lock differs"):
        verify_frozen_lock(root, "grader-v1-frozen")


def test_frozen_lock_rejects_added_file_under_grader_audit(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    _write(root, "grader_audit/new_module.py", "new")
    _commit(root, "add module")
    with pytest.raises(FrozenViolationError, match="added protected-path file"):
        verify_frozen_lock(root, "grader-v1-frozen")


def test_frozen_lock_rejects_added_file_inside_locked_dev_task(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    _write(root, "tasks/legacy-dev/new.py", "new")
    _commit(root, "add dev task file")
    with pytest.raises(FrozenViolationError, match="added protected-path file"):
        verify_frozen_lock(root, "grader-v1-frozen")


def test_frozen_lock_rejects_new_development_task_tree(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    _write(root, "tasks/newdev/task.yaml", "split: development\n")
    _write(root, "tasks/newdev/other.txt", "x")
    _commit(root, "add new dev task")
    with pytest.raises(FrozenViolationError):
        verify_frozen_lock(root, "grader-v1-frozen")


def test_frozen_lock_rejects_new_task_tree_without_manifest(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    _write(root, "tasks/notatask/file.txt", "x")
    _commit(root, "add junk under tasks")
    with pytest.raises(FrozenViolationError, match=r"without task\.yaml"):
        verify_frozen_lock(root, "grader-v1-frozen")


def test_frozen_lock_rejects_added_root_file(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    _write(root, "tasks.py", "changed")  # modified root file -> hash mismatch caught first
    with pytest.raises(FrozenViolationError):
        verify_frozen_lock(root, "grader-v1-frozen")


# ---------------------------------------------------------------------------
# verify_heldout_selection
# ---------------------------------------------------------------------------


def test_heldout_selection_passes_on_clean_inputs(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root)
    tasks = verify_heldout_selection(
        root, root / "tasks", root / "results" / "annotations", "heldout-gate5-001",
        _tag_commit(root),
    )
    assert len(tasks) == 1


def test_heldout_selection_rejects_untracked_input(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root)
    _write(root, "tasks/heldout-task/extra.txt", "untracked")
    with pytest.raises(HeldoutInputError, match="untracked"):
        verify_heldout_selection(
            root, root / "tasks", root / "results" / "annotations", "heldout-gate5-001",
            _tag_commit(root),
        )


def test_heldout_selection_rejects_modified_input(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root)
    _write(root, "tasks/heldout-task/baseline/src/mod.py", "changed")
    with pytest.raises(HeldoutInputError, match="modified vs HEAD"):
        verify_heldout_selection(
            root, root / "tasks", root / "results" / "annotations", "heldout-gate5-001",
            _tag_commit(root),
        )


def test_heldout_selection_rejects_modified_annotation(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root)
    _write(
        root,
        "results/annotations/heldout-gate5-001/heldout-task/gold.yaml",
        "disposition: confirmed\n",
    )
    with pytest.raises(HeldoutInputError, match="modified vs HEAD"):
        verify_heldout_selection(
            root, root / "tasks", root / "results" / "annotations", "heldout-gate5-001",
            _tag_commit(root),
        )


def test_heldout_selection_rejects_missing_annotation(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root)
    # select under an experiment id that has no committed annotations
    with pytest.raises(HeldoutInputError):
        verify_heldout_selection(
            root,
            root / "tasks",
            root / "results" / "annotations",
            "no-annotations-exp",
            _tag_commit(root),
        )


def test_heldout_selection_rejects_untracked_annotation(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root)
    source = (
        root / "results" / "annotations" / "heldout-gate5-001"
        / "heldout-task" / "gold.yaml"
    )
    target = (
        root / "results" / "annotations" / "untracked-exp"
        / "heldout-task" / "gold.yaml"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    with pytest.raises(HeldoutInputError, match="is untracked"):
        verify_heldout_selection(
            root,
            root / "tasks",
            root / "results" / "annotations",
            "untracked-exp",
            _tag_commit(root),
        )


def test_raw_hash_binder_preserves_human_fields_and_raw_records(tmp_path: Path) -> None:
    annotations = tmp_path / "annotations"
    ann = annotations / "exp" / "task" / "patch.yaml"
    ann.parent.mkdir(parents=True)
    original = {
        "schema_version": "1.0",
        "reviewer": "human-reviewer",
        "timestamp_utc": "2026-08-07T01:02:03+00:00",
        "disposition": "confirmed",
        "truth_label": "invalid",
        "reason": "manual evidence",
        "notes": ["first", {"nested": True}],
        "recorded_patch_hashes": {"metadata_sha256": "a" * 64, "diff_sha256": "b" * 64},
        "recorded_raw_record_hashes": {"naive": "c" * 64},
    }
    ann.write_text(yaml.safe_dump(original, sort_keys=True), encoding="utf-8")
    raw_record = tmp_path / "raw.json"
    raw_record.write_bytes(b'{"immutable":true}\n')
    raw_before = raw_record.read_bytes()

    updated = bind_raw_record_hashes(
        annotations, "exp", "task", "patch", {"hardened_v1": "d" * 64}
    )

    for key, value in original.items():
        if key != "recorded_raw_record_hashes":
            assert updated[key] == value
    assert updated["recorded_raw_record_hashes"] == {
        "naive": "c" * 64,
        "hardened_v1": "d" * 64,
    }
    assert raw_record.read_bytes() == raw_before


def test_raw_hash_binder_refuses_conflict_without_rewrite(tmp_path: Path) -> None:
    annotations = tmp_path / "annotations"
    ann = annotations / "exp" / "task" / "patch.yaml"
    ann.parent.mkdir(parents=True)
    ann.write_text(
        yaml.safe_dump(
            {
                "disposition": "confirmed",
                "truth_label": "valid",
                "reviewer": "human",
                "recorded_raw_record_hashes": {"naive": "a" * 64},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    before = ann.read_bytes()
    with pytest.raises(AnnotationMismatchError, match="conflict"):
        bind_raw_record_hashes(
            annotations, "exp", "task", "patch", {"naive": "b" * 64}
        )
    assert ann.read_bytes() == before


# ---------------------------------------------------------------------------
# run_heldout preflight / plan reservation
# ---------------------------------------------------------------------------


def test_run_heldout_rejects_existing_experiment(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root)
    exp = root / "results" / "raw" / "heldout-gate5-001"
    exp.mkdir(parents=True)
    with pytest.raises(HeldoutInputError, match="already exists"):
        run_heldout(
            project_root=root,
            tasks_dir=root / "tasks",
            raw_results_root=root / "results" / "raw",
            experiment_id="heldout-gate5-001",
            graders=("naive", "hardened_v1"),
            require_tag="grader-v1-frozen",
            runner=_RaisingRunner(),
        )


def test_run_heldout_rejects_noncanonical_graders(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root)
    with pytest.raises(HeldoutInputError, match="graders must be exactly"):
        run_heldout(
            project_root=root,
            tasks_dir=root / "tasks",
            raw_results_root=root / "results" / "raw",
            experiment_id="heldout-gate5-001",
            graders=("naive",),
            require_tag="grader-v1-frozen",
            runner=_RaisingRunner(),
        )


def test_run_heldout_reserves_plan_before_evaluation(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root)
    with pytest.raises(RuntimeError, match="stub runner"):
        run_heldout(
            project_root=root,
            tasks_dir=root / "tasks",
            raw_results_root=root / "results" / "raw",
            experiment_id="heldout-gate5-001",
            graders=("naive", "hardened_v1"),
            require_tag="grader-v1-frozen",
            runner=_RaisingRunner(),
        )
    metadata = (
        root / "results" / "raw" / "heldout-gate5-001" / "metadata.json"
    )
    assert metadata.is_file()
    import json

    plan = json.loads(metadata.read_text(encoding="utf-8"))["plan"]["controlled"]
    assert len(plan) == 2  # gold patch x naive + hardened_v1
    assert plan[0]["phase"] == "heldout"


def test_run_heldout_exit_mapping_requires_frozen_violation(tmp_path: Path) -> None:
    """Protected mutation surfaces as FrozenViolationError (exit 5)."""
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root)
    _write(root, "grader_audit/core.py", "changed")
    with pytest.raises(FrozenViolationError, match="protected hash mismatch"):
        run_heldout(
            project_root=root,
            tasks_dir=root / "tasks",
            raw_results_root=root / "results" / "raw",
            experiment_id="heldout-gate5-001",
            graders=("naive", "hardened_v1"),
            require_tag="grader-v1-frozen",
            runner=_RaisingRunner(),
        )


def test_run_heldout_maps_untracked_task_input_to_invalid_input(tmp_path: Path) -> None:
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root)
    _write(root, "tasks/heldout-task/untracked.txt", "not committed")
    with pytest.raises(HeldoutInputError, match="untracked"):
        run_heldout(
            project_root=root,
            tasks_dir=root / "tasks",
            raw_results_root=root / "results" / "raw",
            experiment_id="heldout-gate5-001",
            graders=("naive", "hardened_v1"),
            require_tag="grader-v1-frozen",
            runner=_RaisingRunner(),
        )


def test_run_heldout_does_not_bind_until_entire_matrix_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_frozen_repo(tmp_path)
    add_heldout_task(root, "heldout-a")
    add_heldout_task(root, "heldout-b")
    evaluation_count = 0
    bound: list[tuple[object, ...]] = []

    class FakeWorkspace:
        pristine_snapshot = SimpleNamespace(sha256="a" * 64)

        def snapshot(self) -> SimpleNamespace:
            return SimpleNamespace(sha256="b" * 64)

    class FakeWorkspaceManager:
        def __init__(self, _task: object) -> None:
            pass

        def materialize(self) -> FakeWorkspace:
            return FakeWorkspace()

        def apply_patch_to(self, _workspace: object, _patch: object) -> SimpleNamespace:
            return SimpleNamespace(ok=True)

        def finalize_and_destroy(self, _workspace: object) -> None:
            pass

    def fake_evaluate(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal evaluation_count
        evaluation_count += 1
        if evaluation_count == 3:
            raise RuntimeError("second patch failed")
        return SimpleNamespace(outcome=object(), process_result=object())

    def fake_build(*_args: object, **_kwargs: object) -> None:
        return None

    def fake_bind(*args: object, **_kwargs: object) -> None:
        bound.append(args)

    monkeypatch.setattr("grader_audit.core.heldout.WorkspaceManager", FakeWorkspaceManager)
    monkeypatch.setattr("grader_audit.core.heldout.evaluate_grader", fake_evaluate)
    monkeypatch.setattr("grader_audit.core.heldout.build_patch_record", fake_build)
    monkeypatch.setattr("grader_audit.core.heldout.bind_patch_raw_hashes", fake_bind)

    with pytest.raises(RuntimeError, match="second patch failed"):
        run_heldout(
            project_root=root,
            tasks_dir=root / "tasks",
            raw_results_root=root / "results" / "raw",
            experiment_id="heldout-gate5-001",
            graders=("naive", "hardened_v1"),
            require_tag="grader-v1-frozen",
            runner=_RaisingRunner(),
        )
    assert evaluation_count == 3
    assert bound == []
