"""Shared synthetic freeze + held-out fixtures for Gate 4/5 command tests.

Builds a hermetic git repository with a synthetic freeze (annotated tag, lock
whose protected files and aggregate match the tree), then lets tests add a
post-freeze ``frozen_eval`` task and confirmed annotations. No Docker is needed
for the verification paths exercised here.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import yaml

from grader_audit.core.hashing import hash_tree, sha256_file
from grader_audit.core.manifests import discover_patches, load_task
from grader_audit.core.models import PatchSplit

PROTECTED_FILES = [
    "grader_audit/__init__.py",
    "grader_audit/core.py",
    "tests/test_x.py",
    "tasks/legacy-dev/task.yaml",
    "env.py",
    "tasks.py",
    "pyproject.toml",
    "uv.lock",
    "results/annotations/dev-controlled/gold.yaml",
]

_FIX_COMMIT = "1" * 40
_BASE_COMMIT = "2" * 40


def _git(root: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *argv], capture_output=True, text=True, check=False
    )


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.name", "Gate5 Test")
    _git(root, "config", "user.email", "gate5@example.com")
    _git(root, "config", "commit.gpgsign", "false")


def _commit_all(root: Path, message: str) -> str:
    assert _git(root, "add", "-A").returncode == 0
    result = _git(root, "commit", "-m", message)
    assert result.returncode == 0, result.stderr
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8", newline="\n")


def _aggregate(rel_hashes: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(rel_hashes):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(rel_hashes[rel].encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def make_frozen_repo(tmp_path: Path) -> Path:
    """Create a repo with a valid annotated freeze (no held-out tasks yet)."""
    root = tmp_path / "repo"
    _init_repo(root)
    for rel in PROTECTED_FILES:
        if rel == "tasks/legacy-dev/task.yaml":
            _write(root / rel, _task_yaml("legacy-dev", "development", "a" * 64))
        else:
            _write(root / rel, f"# {rel}\n")
    base = _commit_all(root, "baseline")
    protected = {rel: sha256_file(root / rel) for rel in PROTECTED_FILES}
    lock: dict[str, object] = {
        "schema_version": "1.0",
        "kind": "grader_freeze_v1",
        "grader": "hardened_v1",
        "git_tag": "grader-v1-frozen",
        "source_head_sha256": base,
        "protected_tree_sha256": _aggregate(protected),
        "protected_files": protected,
        "experiments": {"controlled": ["dev-controlled"], "validation": []},
        "tasks": [],
    }
    _write(root / "freeze" / "grader_v1.lock.json", json.dumps(lock, sort_keys=True))
    freeze = _commit_all(root, "Freeze hardened grader v1")
    _git(root, "tag", "-a", "grader-v1-frozen", "-m", "Freeze hardened grader v1")
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == freeze
    return root


def _task_yaml(task_id: str, split: str, baseline_sha: str) -> str:
    return f"""schema_version: "1.0"
id: {task_id}
title: Synthetic {task_id}
split: {split}
source:
  repo_url: https://example.invalid/{task_id}
  license_spdx: MIT
  license_file: baseline/LICENSE
  fix_commit: "{_FIX_COMMIT}"
  baseline_commit: "{_BASE_COMMIT}"
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
  expose_redacted_manifest: true
  editable_globs: ["src/**", "tests/**"]
  immutable_workspace_globs: ["task.yaml", ".grader/**"]
  expected_change_globs: ["src/mod.py"]
  generated_artifact_globs: ["**/__pycache__/**", "**/*.pyc"]
grading:
  naive:
    argv: ["python", "-m", "pytest", "tests", "-q"]
    cwd: /workspace
    timeout_seconds: 60
  hardened_v1:
    tests_dir: authoritative_tests
    expected_nodeids: ["test_mod.py::test_works"]
    timeout_seconds: 60
  oracle:
    tests_dir: oracle_tests
    expected_nodeids: ["test_mod_oracle.py::test_oracle_works"]
validation:
  baseline_expected_failing_nodeids: ["test_mod.py::test_works"]
  gold_patch_id: gold
"""


def _patch_yaml(task_id: str) -> str:
    return f"""schema_version: "1.0"
id: gold
task_id: {task_id}
label: valid
subtype: gold
split: frozen_eval
attack_family: null
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


def add_heldout_task(root: Path, task_id: str = "heldout-task") -> str:
    """Add a valid post-freeze frozen_eval task + confirmed annotation.

    Returns the introduction commit SHA.
    """
    task_dir = root / "tasks" / task_id
    baseline = task_dir / "baseline"
    _write(baseline / "src" / "mod.py", "VALUE = 1\n")
    _write(baseline / "LICENSE", "MIT\n")
    _write(task_dir / "visible_tests" / "test_mod.py", "def test_works():\n    assert True\n")
    _write(
        task_dir / "authoritative_tests" / "test_mod.py",
        "def test_works():\n    assert True\n",
    )
    _write(
        task_dir / "oracle_tests" / "test_mod_oracle.py",
        "def test_oracle_works():\n    assert True\n",
    )
    _write(task_dir / "prompt.md", "Make it work.\n")
    _write(task_dir / "requirements.lock", "# stdlib-only\n")
    baseline_sha = hash_tree(baseline)
    _write(task_dir / "task.yaml", _task_yaml(task_id, "frozen_eval", baseline_sha))
    _write(
        task_dir / "patches" / "valid" / "gold" / "change.patch",
        _new_file_diff("src/fix_gold.py", "gold"),
    )
    _write(task_dir / "patches" / "valid" / "gold" / "patch.yaml", _patch_yaml(task_id))
    _write(
        task_dir / "image.lock.json",
        json.dumps(
            {
                "schema_version": "1.0",
                "task_id": task_id,
                "build_platform": "linux/amd64",
                "build_digest": "sha256:" + "0" * 64,
            },
            sort_keys=True,
        ),
    )
    commit = _commit_all(root, f"add heldout task {task_id}")

    task = load_task(task_dir)
    for patch in discover_patches(task.task_dir, PatchSplit.FROZEN_EVAL):
        annotation = {
            "schema_version": "1.0",
            "reviewer": "gate5-test",
            "timestamp_utc": "2026-08-06T00:00:00+00:00",
            "disposition": "confirmed",
            "truth_label": patch.manifest.label.value,
            "reason": "synthetic confirmed",
            "recorded_patch_hashes": {
                "metadata_sha256": patch.metadata_sha256,
                "diff_sha256": patch.diff_sha256,
            },
        }
        _write(
            root / "results" / "annotations" / "heldout-gate5-001" / task_id
            / f"{patch.manifest.id}.yaml",
            yaml.safe_dump(annotation, sort_keys=True),
        )
    _commit_all(root, f"confirm annotations for {task_id}")
    return commit
