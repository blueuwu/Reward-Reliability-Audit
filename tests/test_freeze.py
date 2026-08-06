"""Unit tests for the Section 27.14 freeze machinery (no Docker required).

These tests exercise the git precondition helpers, protected/result-set file
selection and hashing, held-out detection, task-image lock verification, and
the fail-safe ``run_freeze`` preconditions in an isolated temporary repository.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from grader_audit.core.freeze import (
    FinalEvidenceSelection,
    FreezeError,
    aggregate_rel_hashes,
    find_held_out_content,
    git_author_configured,
    head_commit,
    protected_files,
    real_commit_sha,
    result_set_files,
    run_freeze,
    tag_exists,
    verify_task_image_locks,
    worktree_clean,
)
from grader_audit.core.hashing import hash_tree, sha256_bytes, sha256_file
from grader_audit.core.manifests import load_task
from grader_audit.images import task_dockerfile_text

_COMMIT = "1" * 40
_BASELINE_COMMIT = "2" * 40


def _git(root: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *argv],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    result = _git(root, "init", "-q", "-b", "master")
    assert result.returncode == 0, result.stderr
    _git(root, "config", "user.name", "Freeze Unit Test")
    _git(root, "config", "user.email", "freeze-unit@example.com")
    _git(root, "config", "commit.gpgsign", "false")


def _commit_all(root: Path, message: str) -> str:
    assert _git(root, "add", "-A").returncode == 0
    result = _git(root, "commit", "-m", message)
    assert result.returncode == 0, result.stderr
    head = _git(root, "rev-parse", "HEAD")
    return head.stdout.strip()


@pytest.fixture()
def no_git_global_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize host-level git identity so ``git config user.*`` is empty."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "C:/nonexistent-gate4-global-config")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "C:/nonexistent-gate4-system-config")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _write(root: Path, rel: str, data: str = "x") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8", newline="\n")


def _minimal_task_yaml(task_id: str, split: str, baseline_sha: str) -> str:
    return f"""schema_version: "1.0"
id: {task_id}
title: Synthetic task {task_id}
split: {split}
source:
  repo_url: https://example.invalid/{task_id}
  license_spdx: MIT
  license_file: baseline/LICENSE
  fix_commit: "{_COMMIT}"
  baseline_commit: "{_BASELINE_COMMIT}"
  vendored_tree_sha256: "{baseline_sha}"
runtime:
  python: "3.12"
  requirements_lock: requirements.lock
  build_timeout_seconds: 300
  command_timeout_seconds: 60
  memory_mb: 1024
  pids_limit: 256
workspace:
  source_dir: baseline
  container_root: /workspace
  source_roots: ["src"]
  visible_tests_dir: visible_tests
  visible_tests_target: tests
  expose_redacted_manifest: false
  editable_globs: ["src/**"]
  immutable_workspace_globs: ["task.yaml"]
  expected_change_globs: []
  generated_artifact_globs: ["**/__pycache__/**"]
grading:
  naive:
    argv: ["python", "-m", "pytest", "tests", "-q"]
    cwd: /workspace
    timeout_seconds: 60
  hardened_v1:
    tests_dir: authoritative_tests
    expected_nodeids: ["test_auth.py::test_auth_0"]
    timeout_seconds: 60
  oracle:
    tests_dir: oracle_tests
    expected_nodeids: ["test_oracle.py::test_oracle_0"]
validation:
  baseline_expected_failing_nodeids: ["test_auth.py::test_auth_0"]
  gold_patch_id: gold
"""


def _make_frozen_eval_task(root: Path, task_id: str = "heldout-task") -> None:
    task_dir = root / "tasks" / task_id
    baseline = task_dir / "baseline"
    _write(baseline, "src/__init__.py", "VALUE = 1\n")
    _write(baseline, "LICENSE", "MIT\n")
    for name in ("visible_tests", "authoritative_tests", "oracle_tests"):
        (task_dir / name).mkdir(parents=True)
    _write(task_dir / "authoritative_tests", "test_auth.py", "def test_auth_0():\n    pass\n")
    _write(task_dir / "oracle_tests", "test_oracle.py", "def test_oracle_0():\n    pass\n")
    _write(task_dir, "requirements.lock", "# stdlib-only\n")
    baseline_sha = hash_tree(baseline)
    _write(task_dir, "task.yaml", _minimal_task_yaml(task_id, "frozen_eval", baseline_sha))
    patch_dir = task_dir / "patches" / "valid" / "gold"
    _write(patch_dir, "patch.yaml", _minimal_patch_yaml(task_id, "gold"))
    _write(patch_dir, "change.patch", _new_file_diff("src/fix_gold.py", "gold"))


def _minimal_patch_yaml(task_id: str, patch_id: str) -> str:
    return f"""schema_version: "1.0"
id: {patch_id}
task_id: {task_id}
label: valid
subtype: gold
split: development
description: synthetic gold
why_labeled: synthetic
author: human_curated
"""


def _new_file_diff(rel_path: str, suffix: str) -> str:
    body = f"def fix_{suffix}():\n    pass\n"
    count = len(body.splitlines())
    lines = "".join(f"+{line}\n" for line in body.splitlines())
    return (
        f"diff --git a/{rel_path} b/{rel_path}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{rel_path}\n"
        f"@@ -0,0 +1,{count} @@\n"
        f"{lines}"
    )


# ---------------------------------------------------------------------------
# Git helper tests
# ---------------------------------------------------------------------------


def test_git_helpers_against_temp_repo(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _write(root, "keep.txt", "content")
    head = _commit_all(root, "initial")
    assert worktree_clean(root) is True
    assert git_author_configured(root) is True
    assert head_commit(root) == head
    assert tag_exists(root, "grader-v1-frozen") is False

    _git(root, "tag", "-a", "grader-v1-frozen", "-m", "msg")
    assert tag_exists(root, "grader-v1-frozen") is True

    _write(root, "dirty.txt", "change")
    assert worktree_clean(root) is False


def test_git_author_missing_is_detected(
    tmp_path: Path, no_git_global_config: None
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "master")
    assert git_author_configured(root) is False


# ---------------------------------------------------------------------------
# File selection and hashing tests
# ---------------------------------------------------------------------------


def test_protected_and_result_set_selection(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _write(root, "grader_audit/core/freeze.py", "core")
    _write(root, "tests/test_freeze.py", "test")
    _write(root, "tasks/sample-task/task.yaml", "task")
    _write(root, "results/annotations/dev-gate4-controlled/x/gold.yaml", "ann-current")
    _write(root, "results/annotations/dev-gate3-controlled/x/gold.yaml", "ann-historical")
    _write(root, "results/dev-gate4-controlled/record.json", "record-current")
    _write(root, "results/dev-gate4-validate-r3/record.json", "record-validate")
    _write(root, "results/dev-gate3-controlled/record.json", "record-historical")
    _write(root, "env.py", "env")
    _write(root, "tasks.py", "tasks")
    _write(root, "pyproject.toml", "pyproject")
    _write(root, "uv.lock", "lock")
    _write(root, "README.md", "docs")
    _commit_all(root, "initial")

    selection = FinalEvidenceSelection(
        controlled=("dev-gate4-controlled",), validation=("dev-gate4-validate-r3",)
    )
    protected = protected_files(root, selection)
    assert "grader_audit/core/freeze.py" in protected
    assert "tests/test_freeze.py" in protected
    assert "tasks/sample-task/task.yaml" in protected
    assert "env.py" in protected
    assert "tasks.py" in protected
    assert "pyproject.toml" in protected
    assert "uv.lock" in protected
    assert "README.md" not in protected
    assert "results/annotations/dev-gate4-controlled/x/gold.yaml" in protected
    assert "results/annotations/dev-gate3-controlled/x/gold.yaml" not in protected

    result_set = result_set_files(root, selection)
    assert "results/dev-gate4-controlled/record.json" in result_set
    assert "results/dev-gate4-validate-r3/record.json" in result_set
    assert "results/dev-gate3-controlled/record.json" not in result_set
    assert "results/annotations/dev-gate4-controlled/x/gold.yaml" not in result_set
    assert "README.md" not in result_set


def test_real_commit_sha_validator() -> None:
    assert real_commit_sha("a" * 40) is True
    assert real_commit_sha("0" * 40) is False
    assert real_commit_sha("abcdef") is False
    assert real_commit_sha("Z" * 40) is False
    assert real_commit_sha("") is False


def test_final_evidence_selection_annotations_roots() -> None:
    selection = FinalEvidenceSelection(controlled=("dev-gate4-controlled",), validation=())
    assert selection.annotations_roots == ("results/annotations/dev-gate4-controlled",)
    empty = FinalEvidenceSelection()
    assert empty.annotations_roots == ()


def test_aggregate_is_deterministic() -> None:
    first = aggregate_rel_hashes({"b": "2" * 64, "a": "1" * 64})
    second = aggregate_rel_hashes({"a": "1" * 64, "b": "2" * 64})
    assert first == second
    changed = aggregate_rel_hashes({"b": "2" * 64, "a": "3" * 64})
    assert first != changed


# ---------------------------------------------------------------------------
# Held-out and image-lock checks
# ---------------------------------------------------------------------------


def test_find_held_out_content_detects_grader_v2(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _write(root, "grader_v2/evaluator.py", "v2")
    errors = find_held_out_content(root, root / "tasks", root / "results")
    assert any("grader_v2" in error for error in errors)


def test_find_held_out_content_detects_frozen_eval_task(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _make_frozen_eval_task(root)
    errors = find_held_out_content(root, root / "tasks", root / "results")
    assert any("frozen-eval task" in error for error in errors)


def test_verify_task_image_locks_detects_stale_lock(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    task_id = "sample-task"
    task_dir = root / "tasks" / task_id
    baseline = task_dir / "baseline"
    _write(baseline, "src/__init__.py", "VALUE = 1\n")
    _write(baseline, "LICENSE", "MIT\n")
    (task_dir / "visible_tests").mkdir(parents=True)
    (task_dir / "authoritative_tests").mkdir(parents=True)
    (task_dir / "oracle_tests").mkdir(parents=True)
    _write(task_dir, "requirements.lock", "# stdlib-only\n")
    baseline_sha = hash_tree(baseline)
    _write(task_dir, "task.yaml", _minimal_task_yaml(task_id, "development", baseline_sha))
    task = load_task(task_dir)
    lock = {
        "schema_version": "1.0",
        "task_id": task_id,
        "build_platform": "linux/amd64",
        "build_digest": "sha256:" + "0" * 64,
        "task_manifest_sha256": task.manifest_sha256,
        "baseline_tree_sha256": baseline_sha,
        "requirements_lock_sha256": sha256_file(task_dir / "requirements.lock"),
        "dockerfile_sha256": sha256_bytes(task_dockerfile_text().encode("utf-8")),
    }
    (task_dir / "image.lock.json").write_text(json.dumps(lock, sort_keys=True), encoding="utf-8")
    assert verify_task_image_locks([task]) == []

    lock["task_manifest_sha256"] = "f" * 64
    (task_dir / "image.lock.json").write_text(json.dumps(lock, sort_keys=True), encoding="utf-8")
    errors = verify_task_image_locks([load_task(task_dir)])
    assert any("task_manifest_sha256 is stale" in error for error in errors)


# ---------------------------------------------------------------------------
# run_freeze fail-safe preconditions
# ---------------------------------------------------------------------------


def test_run_freeze_refuses_wrong_grader(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _write(root, "keep.txt", "x")
    _commit_all(root, "initial")
    with pytest.raises(FreezeError, match="only --grader hardened_v1"):
        run_freeze(
            project_root=root,
            grader="naive",
            git_tag="grader-v1-frozen",
            tasks_dir=root / "tasks",
            results_root=root / "results",
        )


def test_run_freeze_refuses_no_commits(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _write(root, "keep.txt", "x")
    with pytest.raises(FreezeError, match="repository has no commits"):
        run_freeze(
            project_root=root,
            grader="hardened_v1",
            git_tag="grader-v1-frozen",
            tasks_dir=root / "tasks",
            results_root=root / "results",
        )


def test_run_freeze_refuses_no_development_tasks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _write(root, "keep.txt", "x")
    (root / "tasks").mkdir()
    _commit_all(root, "initial")
    with pytest.raises(FreezeError, match="no development tasks"):
        run_freeze(
            project_root=root,
            grader="hardened_v1",
            git_tag="grader-v1-frozen",
            tasks_dir=root / "tasks",
            results_root=root / "results",
        )


def test_run_freeze_refuses_existing_tag(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _write(root, "keep.txt", "x")
    _commit_all(root, "initial")
    _git(root, "tag", "-a", "grader-v1-frozen", "-m", "exists")
    with pytest.raises(FreezeError, match="already exists"):
        run_freeze(
            project_root=root,
            grader="hardened_v1",
            git_tag="grader-v1-frozen",
            tasks_dir=root / "tasks",
            results_root=root / "results",
        )


def test_run_freeze_refuses_dirty_worktree(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _write(root, "keep.txt", "x")
    _commit_all(root, "initial")
    _write(root, "keep.txt", "changed")
    with pytest.raises(FreezeError, match="not clean"):
        run_freeze(
            project_root=root,
            grader="hardened_v1",
            git_tag="grader-v1-frozen",
            tasks_dir=root / "tasks",
            results_root=root / "results",
        )


def test_run_freeze_refuses_held_out_task(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _make_frozen_eval_task(root)
    _commit_all(root, "initial")
    with pytest.raises(FreezeError, match="frozen-eval task"):
        run_freeze(
            project_root=root,
            grader="hardened_v1",
            git_tag="grader-v1-frozen",
            tasks_dir=root / "tasks",
            results_root=root / "results",
        )


def test_run_freeze_refuses_missing_git_author(
    tmp_path: Path, no_git_global_config: None
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "master")
    _write(root, "keep.txt", "x")
    with pytest.raises(FreezeError, match=r"user\.name and user\.email"):
        run_freeze(
            project_root=root,
            grader="hardened_v1",
            git_tag="grader-v1-frozen",
            tasks_dir=root / "tasks",
            results_root=root / "results",
        )


def test_run_freeze_refuses_invalid_tag_name(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _write(root, "keep.txt", "x")
    _commit_all(root, "initial")
    with pytest.raises(FreezeError, match="invalid git tag name"):
        run_freeze(
            project_root=root,
            grader="hardened_v1",
            git_tag="bad tag!",
            tasks_dir=root / "tasks",
            results_root=root / "results",
        )


def test_sha256_helpers_present() -> None:
    assert sha256_bytes(b"freeze") == hashlib.sha256(b"freeze").hexdigest()
