"""Gate 4 integration tests: normative freeze success path and CLI behavior.

The freeze command needs no Docker. A synthetic development corpus (3 tasks,
9 valid and 12 invalid patches, 4 attack families), confirmed annotations, and
a complete controlled + validation result set are generated inside a temporary
Git repository; the quality gates are stubbed because the real gates are
exercised by the Gate 4 run itself, not by pytest.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from grader_audit.cli import app
from grader_audit.core.freeze import (
    FinalEvidenceSelection,
    FreezeError,
    FreezeResult,
    build_freeze_lock,
    run_freeze,
    worktree_clean,
)
from grader_audit.core.hashing import hash_tree, sha256_bytes
from grader_audit.core.manifests import (
    LoadedPatch,
    LoadedTask,
    discover_patches,
    discover_tasks,
    load_task,
)
from grader_audit.core.models import PatchSplit
from grader_audit.core.outcomes import (
    Changes,
    EnvironmentInfo,
    GitInfo,
    GraderInfo,
    PatchInfo,
    ProcessInfo,
    ResultInfo,
    TaskInfo,
    WorkspaceHashes,
)
from grader_audit.core.outcomes import (
    TestEvidence as _TestEvidence,
)
from grader_audit.core.results import (
    EvaluationRecord,
    ValidationRecord,
    ValidationRun,
    serialize_record,
)
from grader_audit.images import task_dockerfile_text

cli_runner = CliRunner()

_COMMIT = "1" * 40
_BASELINE_COMMIT = "2" * 40
_H = "a" * 64


def _git(root: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *argv],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    assert _git(root, "init", "-q", "-b", "master").returncode == 0
    _git(root, "config", "user.name", "Gate 4 Test")
    _git(root, "config", "user.email", "gate4@example.com")
    _git(root, "config", "commit.gpgsign", "false")


def _commit_all(root: Path, message: str) -> str:
    assert _git(root, "add", "-A").returncode == 0
    result = _git(root, "commit", "-m", message)
    assert result.returncode == 0, result.stderr
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8", newline="\n")


def _task_yaml(task_id: str, split: str, baseline_sha: str) -> str:
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
    expected_nodeids: ["test_auth.py::test_auth_0", "test_auth.py::test_auth_1"]
    timeout_seconds: 60
  oracle:
    tests_dir: oracle_tests
    expected_nodeids: ["test_oracle.py::test_oracle_0"]
validation:
  baseline_expected_failing_nodeids: ["test_auth.py::test_auth_0"]
  gold_patch_id: gold
"""


def _patch_yaml(
    task_id: str, patch_id: str, label: str, subtype: str, family: str | None
) -> str:
    lines = [
        'schema_version: "1.0"',
        f"id: {patch_id}",
        f"task_id: {task_id}",
        f"label: {label}",
        f"subtype: {subtype}",
        "split: development",
        f"description: synthetic {label} {subtype} patch",
        "why_labeled: synthetic corpus",
        "author: human_curated",
    ]
    if family is not None:
        lines.append(f"attack_family: {family}")
    return "\n".join(lines) + "\n"


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


def _write_patch(
    task_dir: Path,
    task_id: str,
    patch_id: str,
    label: str,
    subtype: str,
    family: str | None,
    suffix: str,
    *,
    multi: bool = False,
) -> None:
    root_name = "valid" if label == "valid" else "invalid_dev"
    patch_dir = task_dir / "patches" / root_name / patch_id
    _write(patch_dir / "patch.yaml", _patch_yaml(task_id, patch_id, label, subtype, family))
    diff = _new_file_diff(f"src/fix_{suffix}.py", suffix)
    if multi:
        diff += "\n" + _new_file_diff(f"src/helper_{suffix}.py", f"h_{suffix}")
    _write(patch_dir / "change.patch", diff)


def _make_task_corpus(root: Path) -> None:
    _write(root / ".gitignore", "__pycache__/\n.venv/\n.pytest_cache/\n")
    tasks_dir = root / "tasks"
    families = ("overfit", "runtime-manipulation", "scope-violation", "test-weakening")
    subtype_by_family = {
        "overfit": "overfit",
        "runtime-manipulation": "runtime_manipulation",
        "scope-violation": "scope_violation",
        "test-weakening": "reward_hack",
    }
    for i, task_id in enumerate(("synthetic-task-aaa", "synthetic-task-bbb", "synthetic-task-ccc")):
        task_dir = tasks_dir / task_id
        baseline = task_dir / "baseline"
        _write(baseline / "src" / "__init__.py", "VALUE = 1\n")
        _write(baseline / "LICENSE", "MIT\n")
        _write(task_dir / "visible_tests" / "conftest.py", "")
        _write(
            task_dir / "visible_tests" / "test_basic.py",
            "def test_basic():\n    assert True\n",
        )
        _write(
            task_dir / "authoritative_tests" / "test_auth.py",
            "def test_auth_0():\n    assert True\n",
        )
        _write(
            task_dir / "oracle_tests" / "test_oracle.py",
            "def test_oracle_0():\n    assert True\n",
        )
        _write(task_dir / "prompt.md", "Fix the bug in src.\n")
        _write(task_dir / "requirements.lock", "# stdlib-only\n")
        baseline_sha = hash_tree(baseline)
        _write(task_dir / "task.yaml", _task_yaml(task_id, "development", baseline_sha))

        _write_patch(task_dir, task_id, "gold", "valid", "gold", None, f"gold{i}")
        _write_patch(task_dir, task_id, f"alternative-{i}", "valid", "alternative", None, f"alt{i}")
        _write_patch(
            task_dir, task_id, f"unusual-valid-{i}", "valid", "unusual_valid", None, f"uv{i}",
            multi=True,
        )
        for j, family in enumerate(families):
            _write_patch(
                task_dir,
                task_id,
                f"{family}-{i}-{j}",
                "invalid",
                subtype_by_family[family],
                family,
                f"{family}{i}{j}",
            )

        task = load_task(task_dir)
        lock = {
            "schema_version": "1.0",
            "task_id": task_id,
            "build_platform": "linux/amd64",
            "build_digest": "sha256:" + "0" * 64,
            "task_manifest_sha256": task.manifest_sha256,
            "baseline_tree_sha256": baseline_sha,
            "requirements_lock_sha256": sha256_bytes(
                (task_dir / "requirements.lock").read_bytes()
            ),
            "dockerfile_sha256": sha256_bytes(task_dockerfile_text().encode("utf-8")),
        }
        _write(task_dir / "image.lock.json", json.dumps(lock, sort_keys=True))


def _environment() -> EnvironmentInfo:
    return EnvironmentInfo(
        python="3.12.13", pytest="9.1.1", hud="0.6.12", docker_image_digest="sha256:" + "0" * 64
    )


def _task_info(task: LoadedTask) -> TaskInfo:
    return TaskInfo(id=task.manifest.id, split="development", manifest_sha256=task.manifest_sha256)


def _patch_info(patch: LoadedPatch) -> PatchInfo:
    return PatchInfo(
        id=patch.manifest.id,
        label=patch.manifest.label.value,
        subtype=patch.manifest.subtype.value,
        attack_family=patch.manifest.attack_family,
        metadata_sha256=patch.metadata_sha256,
        diff_sha256=patch.diff_sha256,
    )


def _controlled_record(
    exp_id: str,
    grader: str,
    task: LoadedTask,
    patch: LoadedPatch,
    head: str,
    results_root: Path,
    *,
    data_commit: str | None = None,
    worktree_dirty: bool = False,
) -> EvaluationRecord:
    run_id = hashlib.sha256(
        f"{exp_id}{grader}{task.manifest.id}{patch.manifest.id}".encode()
    ).hexdigest()
    stdout = f"output of {grader} {patch.manifest.id}\n".encode()
    stderr = b""
    artifacts = results_root / exp_id / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    stdout_path = artifacts / f"{run_id}.stdout"
    stderr_path = artifacts / f"{run_id}.stderr"
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    project_root = results_root.parent
    reward = 1.0 if patch.manifest.label.value == "valid" else 0.0
    pre = hashlib.sha256(f"{task.manifest.id}{patch.manifest.id}".encode()).hexdigest()
    pristine = hashlib.sha256(f"pristine{task.manifest.id}".encode()).hexdigest()
    post = hashlib.sha256(f"post{task.manifest.id}{patch.manifest.id}{grader}".encode()).hexdigest()
    return EvaluationRecord(
        schema_version="1.0",
        run_id=run_id,
        experiment_id=exp_id,
        timestamp_utc="2026-08-06T00:00:00+00:00",
        status="completed",
        phase="controlled",
        validation_case=None,
        repeat_index=0,
        git=GitInfo(
            data_commit=data_commit if data_commit is not None else head,
            grader_frozen_commit=None,
            worktree_dirty=worktree_dirty,
        ),
        grader=GraderInfo(name=grader, version="v1"),
        task=_task_info(task),
        patch=_patch_info(patch),
        environment=_environment(),
        workspace=WorkspaceHashes(
            pristine_sha256=pristine, pre_grade_sha256=pre, post_grade_sha256=post
        ),
        result=ResultInfo(
            reward=reward,
            accepted=reward == 1.0,
            reason_codes=[],
            warnings=[],
            duration_seconds=0.5,
        ),
        process=ProcessInfo(
            argv=["python", "-m", "pytest", "tests", "-q"],
            cwd="/workspace",
            exit_code=0,
            timed_out=False,
            stdout_path=stdout_path.relative_to(project_root).as_posix(),
            stderr_path=stderr_path.relative_to(project_root).as_posix(),
            stdout_sha256=sha256_bytes(stdout),
            stderr_sha256=sha256_bytes(stderr),
            stdout_truncated=False,
            stderr_truncated=False,
            stdout_bytes=len(stdout),
            stderr_bytes=0,
            duration_seconds=0.5,
        ),
        test_evidence=None,
        changes=Changes(),
        error=None,
    )


def _validation_run(
    name: str,
    reward: float | None,
    accepted: bool | None,
    reasons: list[str],
    node: str,
) -> ValidationRun:
    return ValidationRun(
        grader=GraderInfo(name=name, version="v1"),
        status="completed",
        reward=reward,
        accepted=accepted,
        reason_codes=reasons,
        warnings=[],
        error=None,
        test_evidence=_TestEvidence(state="complete"),
        changes=Changes(),
        workspace=WorkspaceHashes(pristine_sha256=_H, pre_grade_sha256=_H, post_grade_sha256=_H),
        process=None,
        duration_seconds=0.0,
        node_outcomes={node: "passed" if accepted else "failed"} if name == "hardened_v1" else {},
    )


def _validation_record(
    exp_id: str,
    task: LoadedTask,
    case: str,
    idx: int,
    head: str,
    *,
    data_commit: str | None = None,
    worktree_dirty: bool = False,
) -> ValidationRecord:
    node = "test_auth.py::test_auth_0"
    if case == "baseline":
        runs = {
            "naive": _validation_run("naive", 0.0, False, [], node),
            "hardened_v1": _validation_run(
                "hardened_v1", 0.0, False, ["authoritative_tests_failed"], node
            ),
            "oracle": _validation_run("oracle", None, None, ["authoritative_tests_failed"], node),
        }
    else:
        runs = {
            "naive": _validation_run("naive", 1.0, True, [], node),
            "hardened_v1": _validation_run("hardened_v1", 1.0, True, [], node),
            "oracle": _validation_run("oracle", None, None, [], node),
        }
    return ValidationRecord(
        schema_version="1.0",
        run_id=hashlib.sha256(f"{exp_id}{task.manifest.id}{case}{idx}".encode()).hexdigest(),
        experiment_id=exp_id,
        timestamp_utc="2026-08-06T00:00:00+00:00",
        git=GitInfo(
            data_commit=data_commit if data_commit is not None else head,
            grader_frozen_commit=None,
            worktree_dirty=worktree_dirty,
        ),
        task=_task_info(task),
        environment=_environment(),
        validation_case=case,
        repeat_index=idx,
        runs=runs,
        stable=True,
    )


def _make_annotations(results_root: Path, exp_id: str, tasks: list[LoadedTask]) -> None:
    for task in tasks:
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
            annotation = {
                "schema_version": "1.0",
                "reviewer": "gate4-test",
                "timestamp_utc": "2026-08-06T00:00:00+00:00",
                "disposition": "confirmed",
                "truth_label": patch.manifest.label.value,
                "reason": "synthetic confirmed truth label",
                "recorded_patch_hashes": {
                    "metadata_sha256": patch.metadata_sha256,
                    "diff_sha256": patch.diff_sha256,
                },
            }
            _write(
                results_root
                / "annotations"
                / exp_id
                / task.manifest.id
                / f"{patch.manifest.id}.yaml",
                yaml.safe_dump(annotation, sort_keys=True),
            )


def _make_controlled_experiment(
    results_root: Path,
    exp_id: str,
    tasks: list[LoadedTask],
    head: str,
    *,
    data_commit: str | None = None,
    worktree_dirty: bool = False,
) -> None:
    planned = [
        {
            "grader": grader,
            "task_id": task.manifest.id,
            "patch_id": patch.manifest.id,
            "split": "development",
            "phase": "controlled",
            "task_manifest_sha256": task.manifest_sha256,
            "patch_metadata_sha256": patch.metadata_sha256,
            "patch_diff_sha256": patch.diff_sha256,
        }
        for task in tasks
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT)
        for grader in ("naive", "hardened_v1")
    ]
    metadata = {
        "schema_version": "1.0",
        "experiment_id": exp_id,
        "timestamp_utc": "2026-08-06T00:00:00+00:00",
        "git": {"data_commit": head, "grader_frozen_commit": None, "worktree_dirty": False},
        "plan": {"controlled": planned},
    }
    _write(results_root / exp_id / "metadata.json", json.dumps(metadata, sort_keys=True))

    for task in tasks:
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
            raw_hashes: dict[str, str] = {}
            for grader in ("naive", "hardened_v1"):
                record = _controlled_record(
                    exp_id,
                    grader,
                    task,
                    patch,
                    head,
                    results_root,
                    data_commit=data_commit,
                    worktree_dirty=worktree_dirty,
                )
                record_dir = (
                    results_root
                    / exp_id
                    / grader
                    / "development"
                    / task.manifest.id
                )
                serialized = serialize_record(record)
                _write(record_dir / f"{patch.manifest.id}.json", serialized.decode("utf-8"))
                raw_hashes[grader] = sha256_bytes(serialized)
            annotation_path = (
                results_root
                / "annotations"
                / exp_id
                / task.manifest.id
                / f"{patch.manifest.id}.yaml"
            )
            annotation = yaml.safe_load(annotation_path.read_text(encoding="utf-8"))
            assert isinstance(annotation, dict)
            annotation["recorded_raw_record_hashes"] = raw_hashes
            annotation_path.write_text(
                yaml.safe_dump(annotation, sort_keys=True), encoding="utf-8"
            )


def _make_validation_experiment(
    results_root: Path,
    exp_id: str,
    tasks: list[LoadedTask],
    head: str,
    *,
    data_commit: str | None = None,
    worktree_dirty: bool = False,
) -> None:
    validation_plan = [
        {
            "task_id": task.manifest.id,
            "split": task.manifest.split.value,
            "task_manifest_sha256": task.manifest_sha256,
            "validation_case": case,
            "repeat_index": idx,
        }
        for task in tasks
        for case in ("baseline", "gold")
        for idx in (1, 2, 3)
    ]
    _write(
        results_root / exp_id / "metadata.json",
        json.dumps(
            {
                "schema_version": "1.0",
                "experiment_id": exp_id,
                "timestamp_utc": "2026-08-06T00:00:00+00:00",
                "plan": {"controlled": [], "validation": validation_plan},
            },
            sort_keys=True,
        ),
    )
    for task in tasks:
        for case in ("baseline", "gold"):
            for idx in (1, 2, 3):
                record = _validation_record(
                    exp_id,
                    task,
                    case,
                    idx,
                    head,
                    data_commit=data_commit,
                    worktree_dirty=worktree_dirty,
                )
                path = (
                    results_root
                    / exp_id
                    / "validation"
                    / "development"
                    / task.manifest.id
                    / case
                    / f"{idx}.json"
                )
                _write(path, record.serialize().decode("utf-8"))


def _make_results(root: Path, head: str) -> None:
    results = root / "results"
    tasks = discover_tasks(root / "tasks")
    exp_id = "dev-controlled-test"
    _make_annotations(results, exp_id, tasks)
    _make_controlled_experiment(results, exp_id, tasks, head)
    _make_validation_experiment(results, "dev-validate-test", tasks, head)


def _make_historical_experiments(root: Path, head: str) -> None:
    """Write Gate-3-style historical experiments (all-zero SHA, dirty worktree)."""
    results = root / "results"
    tasks = discover_tasks(root / "tasks")
    _make_annotations(results, "dev-historical-controlled", tasks)
    _make_controlled_experiment(
        results,
        "dev-historical-controlled",
        tasks,
        head,
        data_commit="0" * 40,
        worktree_dirty=True,
    )
    _make_validation_experiment(
        results,
        "dev-historical-validate",
        tasks,
        head,
        data_commit="0" * 40,
        worktree_dirty=True,
    )


@pytest.fixture()
def frozen_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _init_repo(root)
    _make_task_corpus(root)
    head = _commit_all(root, "baseline")
    _make_results(root, head)
    _commit_all(root, "evidence")
    return root


def _pass_gates(_project_root: Path) -> dict[str, dict[str, object]]:
    return {
        name: {"passed": True, "detail": "exit 0"} for name in ("ruff", "pyright", "pytest")
    }


def _invoke_freeze(root: Path) -> FreezeResult:
    return run_freeze(
        project_root=root,
        grader="hardened_v1",
        git_tag="grader-v1-frozen",
        tasks_dir=root / "tasks",
        results_root=root / "results",
        annotations_root=root / "results" / "annotations",
        quality_gate_runner=_pass_gates,
    )


def test_freeze_success_path_commits_lock_and_tags(frozen_corpus: Path) -> None:
    root = frozen_corpus
    result = _invoke_freeze(root)

    lock_path = root / "freeze" / "grader_v1.lock.json"
    assert lock_path.is_file()
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["kind"] == "grader_freeze_v1"
    assert lock["grader"] == "hardened_v1"
    assert lock["git_tag"] == "grader-v1-frozen"
    assert lock["source_head_sha256"] == result.source_head_sha
    assert lock["protected_file_count"] > 0
    assert lock["development_result_file_count"] > 0
    assert lock["preconditions"]["corpus_minimums"] is True
    assert lock["preconditions"]["confirmed_annotations"] >= 21
    assert lock["preconditions"]["controlled_matrix_complete"] is True
    assert lock["preconditions"]["controlled_zero_infrastructure"] is True
    assert lock["preconditions"]["controlled_zero_invalid_input"] is True
    assert lock["preconditions"]["cross_grader_hashes_match"] is True
    assert lock["preconditions"]["artifact_hashes_match"] is True
    assert lock["preconditions"]["validation_stable"] is True
    assert lock["preconditions"]["evidence_data_commit_valid"] is True
    assert lock["preconditions"]["evidence_worktree_clean"] is True
    assert lock["preconditions"]["quality_gates"]["pytest"]["passed"] is True
    assert lock["experiments"]["controlled"] == ["dev-controlled-test"]
    assert lock["experiments"]["validation"] == ["dev-validate-test"]
    assert lock["preconditions"]["controlled_experiments"] == ["dev-controlled-test"]
    assert lock["preconditions"]["validation_experiments"] == ["dev-validate-test"]

    # Commit identity: exactly the lock file, message exactly as specified.
    commit_message = _git(root, "log", "-1", "--format=%s").stdout.strip()
    assert commit_message == "Freeze hardened grader v1"
    changed = _git(root, "show", "--name-only", "--format=", "HEAD").stdout.strip().splitlines()
    assert changed == ["freeze/grader_v1.lock.json"]
    assert _git(root, "rev-parse", "HEAD~1").stdout.strip() == result.source_head_sha

    # Annotated tag resolves to the freeze commit.
    assert _git(root, "cat-file", "-t", "grader-v1-frozen").stdout.strip() == "tag"
    assert result.tag_commit_sha == result.freeze_commit_sha
    assert result.tag_object_sha != result.tag_commit_sha
    tag_commit = _git(root, "rev-parse", "grader-v1-frozen^{commit}").stdout.strip()
    assert tag_commit == result.freeze_commit_sha
    tag_tree = _git(root, "rev-parse", "grader-v1-frozen^{tree}").stdout.strip()
    assert tag_tree == result.tag_tree_sha

    # Lock hashes still reproduce on the frozen tree.
    recomputed = build_freeze_lock(
        project_root=root,
        tasks=discover_tasks(root / "tasks"),
        selection=FinalEvidenceSelection(
            controlled=result.controlled_experiments,
            validation=result.validation_experiments,
        ),
        grader="hardened_v1",
        git_tag="grader-v1-frozen",
        source_head_sha=result.source_head_sha,
        gate_results=lock["preconditions"]["quality_gates"],
        stats={},
        raw_results_root=root / "results",
    )
    assert recomputed["protected_tree_sha256"] == lock["protected_tree_sha256"]
    assert recomputed["development_result_set_sha256"] == lock["development_result_set_sha256"]
    assert worktree_clean(root) is True

    # A second freeze must refuse on the existing tag.
    with pytest.raises(FreezeError, match="already exists"):
        _invoke_freeze(root)


def test_freeze_refuses_failed_quality_gate(frozen_corpus: Path) -> None:
    root = frozen_corpus

    def fail_pytest(_project_root: Path) -> dict[str, dict[str, object]]:
        return {
            "ruff": {"passed": True, "detail": "exit 0"},
            "pyright": {"passed": True, "detail": "exit 0"},
            "pytest": {"passed": False, "detail": "exit 5"},
        }

    with pytest.raises(FreezeError, match="quality gate failed: pytest"):
        run_freeze(
            project_root=root,
            grader="hardened_v1",
            git_tag="grader-v1-frozen",
            tasks_dir=root / "tasks",
            results_root=root / "results",
            annotations_root=root / "results" / "annotations",
            quality_gate_runner=fail_pytest,
        )
    assert not (root / "freeze" / "grader_v1.lock.json").exists()
    assert worktree_clean(root) is True


def test_freeze_refuses_dirty_after_evidence(frozen_corpus: Path) -> None:
    root = frozen_corpus
    (root / "tasks" / "synthetic-task-aaa" / "prompt.md").write_text("edited\n", encoding="utf-8")
    with pytest.raises(FreezeError, match="not clean"):
        _invoke_freeze(root)


# ---------------------------------------------------------------------------
# Content/provenance-based selection: historical (zero-SHA / dirty) evidence
# ---------------------------------------------------------------------------


def test_historical_experiments_excluded_from_freeze(frozen_corpus: Path) -> None:
    root = frozen_corpus
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    _make_historical_experiments(root, head)
    _commit_all(root, "add historical zero-SHA/dirty experiments")

    result = _invoke_freeze(root)
    lock = json.loads((root / "freeze" / "grader_v1.lock.json").read_text(encoding="utf-8"))

    # Selected inventory and precondition evidence contain only the clean
    # experiments; the historical Gate-3-style ones are absent.
    assert lock["experiments"]["controlled"] == ["dev-controlled-test"]
    assert lock["experiments"]["validation"] == ["dev-validate-test"]
    assert lock["preconditions"]["controlled_experiments"] == ["dev-controlled-test"]
    assert lock["preconditions"]["validation_experiments"] == ["dev-validate-test"]
    assert lock["preconditions"]["evidence_data_commit_valid"] is True
    assert lock["preconditions"]["evidence_worktree_clean"] is True
    assert lock["preconditions"]["coverage_complete"] is True

    # No historical path appears in the per-file result-set, and the recorded
    # hash equals the aggregate over exactly those files.
    assert not any("dev-historical" in path for path in lock["development_result_files"])
    files = dict(lock["development_result_files"])
    recomputed = _aggregate_over(files)
    assert recomputed == lock["development_result_set_sha256"]

    # Only the clean experiment directories contribute to the protected surface.
    assert not any("dev-historical" in path for path in lock["protected_files"])
    assert result.controlled_experiments == ("dev-controlled-test",)
    assert result.validation_experiments == ("dev-validate-test",)
    assert worktree_clean(root) is True


def test_freeze_rejects_duplicate_controlled_plan_cell(frozen_corpus: Path) -> None:
    metadata = frozen_corpus / "results" / "dev-controlled-test" / "metadata.json"
    data = json.loads(metadata.read_text(encoding="utf-8"))
    data["plan"]["controlled"].append(dict(data["plan"]["controlled"][0]))
    metadata.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    with pytest.raises(FreezeError, match="duplicate planned cell"):
        _invoke_freeze(frozen_corpus)


def test_freeze_rejects_controlled_record_at_wrong_location(frozen_corpus: Path) -> None:
    record = next(
        (frozen_corpus / "results" / "dev-controlled-test" / "naive").rglob("*.json")
    )
    record.rename(record.with_name("wrong.json"))
    with pytest.raises(FreezeError, match="record at wrong path"):
        _invoke_freeze(frozen_corpus)


def test_freeze_rejects_cross_grader_pristine_mismatch(frozen_corpus: Path) -> None:
    record = next(
        (frozen_corpus / "results" / "dev-controlled-test" / "hardened_v1").rglob(
            "*.json"
        )
    )
    data = json.loads(record.read_text(encoding="utf-8"))
    data["workspace"]["pristine_sha256"] = "0" * 64
    record.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    with pytest.raises(FreezeError, match="cross-grader pristine hash mismatch"):
        _invoke_freeze(frozen_corpus)


def test_freeze_rejects_artifact_outside_experiment(frozen_corpus: Path) -> None:
    outside = frozen_corpus / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    record = next(
        (frozen_corpus / "results" / "dev-controlled-test" / "naive").rglob("*.json")
    )
    data = json.loads(record.read_text(encoding="utf-8"))
    data["process"]["stdout_path"] = "outside.txt"
    data["process"]["stdout_sha256"] = sha256_bytes(outside.read_bytes())
    record.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    with pytest.raises(FreezeError, match="missing/unsafe artifact"):
        _invoke_freeze(frozen_corpus)


def test_freeze_rejects_duplicate_validation_plan_cell(frozen_corpus: Path) -> None:
    metadata = frozen_corpus / "results" / "dev-validate-test" / "metadata.json"
    data = json.loads(metadata.read_text(encoding="utf-8"))
    data["plan"]["validation"].append(dict(data["plan"]["validation"][0]))
    metadata.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    with pytest.raises(FreezeError, match="duplicate planned validation cell"):
        _invoke_freeze(frozen_corpus)


def test_freeze_rejects_extra_validation_record(frozen_corpus: Path) -> None:
    record = next(
        (frozen_corpus / "results" / "dev-validate-test" / "validation").rglob("1.json")
    )
    record.with_name("99.json").write_bytes(record.read_bytes())
    with pytest.raises(FreezeError, match="unexpected validation record path"):
        _invoke_freeze(frozen_corpus)


def test_freeze_rejects_validation_manifest_mismatch(frozen_corpus: Path) -> None:
    record = next(
        (frozen_corpus / "results" / "dev-validate-test" / "validation").rglob("1.json")
    )
    data = json.loads(record.read_text(encoding="utf-8"))
    data["task"]["manifest_sha256"] = "0" * 64
    record.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    with pytest.raises(FreezeError, match="validation manifest hash mismatch"):
        _invoke_freeze(frozen_corpus)


def test_zero_sha_controlled_cannot_satisfy_coverage(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _make_task_corpus(root)
    head = _commit_all(root, "baseline")
    tasks = discover_tasks(root / "tasks")
    results = root / "results"
    _make_annotations(results, "dev-historical-controlled", tasks)
    _make_controlled_experiment(
        results, "dev-historical-controlled", tasks, head, data_commit="0" * 40, worktree_dirty=True
    )
    _make_validation_experiment(results, "dev-validate-test", tasks, head)
    _commit_all(root, "evidence")

    with pytest.raises(FreezeError, match="no eligible controlled experiment"):
        _invoke_freeze(root)
    assert not (root / "freeze" / "grader_v1.lock.json").exists()
    assert worktree_clean(root) is True


def test_dirty_validation_cannot_satisfy_coverage(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _make_task_corpus(root)
    head = _commit_all(root, "baseline")
    tasks = discover_tasks(root / "tasks")
    results = root / "results"
    _make_annotations(results, "dev-controlled-test", tasks)
    _make_controlled_experiment(results, "dev-controlled-test", tasks, head)
    _make_validation_experiment(
        results, "dev-validate-test", tasks, head, data_commit="0" * 40, worktree_dirty=True
    )
    _commit_all(root, "evidence")

    with pytest.raises(FreezeError, match="no eligible validation experiment"):
        _invoke_freeze(root)
    assert not (root / "freeze" / "grader_v1.lock.json").exists()
    assert worktree_clean(root) is True


def test_mixed_historical_and_clean_only_selects_clean(frozen_corpus: Path) -> None:
    """A dirty but complete historical experiment is excluded even when clean
    evidence exists; the freeze succeeds using only the clean evidence."""
    root = frozen_corpus
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    tasks = discover_tasks(root / "tasks")
    results = root / "results"
    _make_annotations(results, "dev-dirty-controlled", tasks)
    _make_controlled_experiment(
        results, "dev-dirty-controlled", tasks, head, worktree_dirty=True
    )
    _commit_all(root, "add dirty-but-complete controlled experiment")

    result = _invoke_freeze(root)
    lock = json.loads((root / "freeze" / "grader_v1.lock.json").read_text(encoding="utf-8"))
    assert lock["experiments"]["controlled"] == ["dev-controlled-test"]
    assert "dev-dirty-controlled" not in lock["preconditions"]["controlled_experiments"]
    assert not any("dev-dirty-controlled" in path for path in lock["development_result_files"])
    assert result.controlled_experiments == ("dev-controlled-test",)


def _aggregate_over(files: dict[str, str]) -> str:
    import hashlib

    digest = hashlib.sha256()
    for rel in sorted(files):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(files[rel].encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# CLI behavior
# ---------------------------------------------------------------------------


def test_freeze_cli_refuses_existing_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "keep.txt").write_text("x", encoding="utf-8")
    _commit_all(root, "initial")
    _git(root, "tag", "-a", "grader-v1-frozen", "-m", "exists")
    monkeypatch.chdir(root)
    result = cli_runner.invoke(
        app,
        ["freeze", "--grader", "hardened_v1", "--git-tag", "grader-v1-frozen"],
    )
    assert result.exit_code == 5
    assert "already exists" in result.output


def test_freeze_cli_refuses_dirty_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "keep.txt").write_text("x", encoding="utf-8")
    _commit_all(root, "initial")
    (root / "keep.txt").write_text("changed", encoding="utf-8")
    monkeypatch.chdir(root)
    result = cli_runner.invoke(
        app,
        ["freeze", "--grader", "hardened_v1", "--git-tag", "grader-v1-frozen"],
    )
    assert result.exit_code == 5
    assert "not clean" in result.output
