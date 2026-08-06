"""Normative grader-v1 freeze protocol (Section 27.14).

``grader-audit freeze --grader hardened_v1 --git-tag grader-v1-frozen`` freezes
hardened grader v1 before any held-out or adaptive evaluation. It refuses to
mutate the repository unless every Section 27.14 precondition holds, hashes the
exact protected surface plus the development result set, records the lock in
``freeze/grader_v1.lock.json``, commits ONLY that lock file with message
``Freeze hardened grader v1``, creates the annotated tag ``grader-v1-frozen``,
and verifies the tag resolves to the new commit. Any precondition failure
raises :class:`FreezeError` before the lock file is written (exit 5).
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from grader_audit.core.annotations import (
    AnnotationMismatchError,
    MissingAnnotationError,
    require_confirmed_annotation,
)
from grader_audit.core.hashing import hash_tree, sha256_bytes, sha256_file
from grader_audit.core.manifests import (
    LoadedPatch,
    LoadedTask,
    discover_patches,
    discover_tasks,
)
from grader_audit.core.models import PatchSplit, Split
from grader_audit.core.orchestrator import (
    check_development_corpus_minimums,
    check_task_corpus,
)
from grader_audit.core.outcomes import OutcomeStatus
from grader_audit.core.provenance import hud_version, pytest_version, python_version
from grader_audit.core.results import EvaluationRecord, ValidationRecord
from grader_audit.images import read_task_image_lock, task_dockerfile_text

FREEZE_SCHEMA_VERSION = "1.0"
FREEZE_COMMIT_MESSAGE = "Freeze hardened grader v1"
DEFAULT_FREEZE_LOCK_REL = Path("freeze") / "grader_v1.lock.json"

_PROTECTED_DIR_ROOTS = ("grader_audit", "tests", "tasks", "results/annotations")
_PROTECTED_FILE_ROOTS = ("env.py", "tasks.py", "pyproject.toml", "uv.lock")
_RESULT_SET_ROOT = "results"
_RESERVED_RESULT_DIRS = ("annotations", "labeling")
_QUALITY_GATES = (
    ("ruff", ("ruff", "check", ".")),
    ("pyright", ("pyright",)),
    ("pytest", ("pytest", "-q")),
)


def _plan_entries(data: dict[str, object]) -> list[dict[str, object]]:
    plan_value = data.get("plan")
    if not isinstance(plan_value, dict):
        return []
    controlled_value = cast(dict[str, object], plan_value).get("controlled")
    if not isinstance(controlled_value, list):
        return []
    controlled = cast(list[object], controlled_value)
    return [
        cast(dict[str, object], item) for item in controlled if isinstance(item, dict)
    ]


class FreezeError(RuntimeError):
    """Raised when a Section 27.14 precondition fails (exit 5)."""


@dataclass(frozen=True)
class FreezeResult:
    lock_path: Path
    freeze_commit_sha: str
    tag_object_sha: str
    tag_commit_sha: str
    tag_tree_sha: str
    source_head_sha: str
    protected_tree_sha256: str
    development_result_set_sha256: str
    timestamp_utc: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _git(
    project_root: Path, argv: list[str], *, timeout_seconds: float = 60.0
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(project_root), *argv],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FreezeError(f"git {' '.join(argv)} timed out: {exc}") from None
    except OSError as exc:
        raise FreezeError(f"git {' '.join(argv)} could not start: {exc}") from None


def git_ok(project_root: Path, argv: list[str]) -> str:
    result = _git(project_root, argv)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise FreezeError(
            f"git {' '.join(argv)} failed (exit {result.returncode}): {detail}"
        )
    return result.stdout.strip()


def tag_exists(project_root: Path, tag: str) -> bool:
    result = _git(project_root, ["show-ref", "--verify", "--quiet", f"refs/tags/{tag}"])
    return result.returncode == 0


def git_author_configured(project_root: Path) -> bool:
    name = _git(project_root, ["config", "user.name"]).stdout.strip()
    email = _git(project_root, ["config", "user.email"]).stdout.strip()
    return bool(name and email)


def worktree_clean(project_root: Path) -> bool:
    result = _git(project_root, ["status", "--porcelain", "--untracked-files=all"])
    return result.returncode == 0 and not result.stdout.strip()


def head_commit(project_root: Path) -> str | None:
    result = _git(project_root, ["rev-parse", "HEAD"])
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def tracked_files(project_root: Path) -> list[str]:
    result = _git(project_root, ["ls-files"], timeout_seconds=120.0)
    if result.returncode != 0:
        raise FreezeError(f"git ls-files failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line]


def _under_root(rel_posix: str, roots: tuple[str, ...]) -> bool:
    for root in roots:
        if rel_posix == root:
            return True
        if rel_posix.startswith(root.rstrip("/") + "/"):
            return True
    return False


def protected_files(project_root: Path) -> list[str]:
    """Tracked files that make up the Section 27.14 protected surface."""
    roots = _PROTECTED_DIR_ROOTS + _PROTECTED_FILE_ROOTS
    return sorted(path for path in tracked_files(project_root) if _under_root(path, roots))


def result_set_files(project_root: Path) -> list[str]:
    """Tracked files that make up the development result set."""
    return sorted(
        path
        for path in tracked_files(project_root)
        if _under_root(path, (_RESULT_SET_ROOT,))
    )


def aggregate_rel_hashes(rel_hashes: dict[str, str]) -> str:
    """Deterministic SHA-256 over ``path:sha256`` pairs sorted bytewise by path."""
    digest = hashlib.sha256()
    for rel in sorted(rel_hashes):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(rel_hashes[rel].encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _hash_tracked(project_root: Path, rel_paths: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in rel_paths:
        hashes[rel] = sha256_file(project_root / Path(rel))
    return hashes


# ---------------------------------------------------------------------------
# Precondition checks
# ---------------------------------------------------------------------------


def find_held_out_content(
    project_root: Path, tasks_dir: Path, results_root: Path
) -> list[str]:
    """Return every held-out artifact that must not exist before freezing."""
    errors: list[str] = []
    for name in ("grader_v2", "adaptive_attempts"):
        if (project_root / name).exists():
            errors.append(f"held-out tree present before freeze: {name}")
    if (results_root / "model_rollouts").exists():
        errors.append("results/model_rollouts present before freeze")
    if not tasks_dir.is_dir():
        return errors
    for task in discover_tasks(tasks_dir):
        if task.manifest.split is Split.FROZEN_EVAL:
            errors.append(f"frozen-eval task present before freeze: {task.manifest.id}")
        for patch in discover_patches(task.task_dir, PatchSplit.FROZEN_EVAL):
            if patch.manifest.split is PatchSplit.FROZEN_EVAL:
                errors.append(
                    f"frozen-eval patch present before freeze: "
                    f"{task.manifest.id}/{patch.manifest.id}"
                )
    return errors


def _baseline_tree_hash(task: LoadedTask) -> str:
    return hash_tree(task.task_dir / task.manifest.workspace.source_dir)


def verify_task_image_locks(tasks: list[LoadedTask]) -> list[str]:
    """Read-only check that each image.lock.json input hash matches the corpus."""
    errors: list[str] = []
    for task in tasks:
        lock = read_task_image_lock(task)
        if lock is None:
            errors.append(f"{task.manifest.id}: image.lock.json missing (run build-images)")
            continue
        expected = {
            "task_manifest_sha256": task.manifest_sha256,
            "baseline_tree_sha256": _baseline_tree_hash(task),
            "requirements_lock_sha256": sha256_file(
                task.task_dir / task.manifest.runtime.requirements_lock
            ),
            "dockerfile_sha256": sha256_bytes(task_dockerfile_text().encode("utf-8")),
        }
        for key, value in expected.items():
            if lock.get(key) != value:
                errors.append(f"{task.manifest.id}: image.lock.json {key} is stale")
    return errors


# ---------------------------------------------------------------------------
# Quality gates (Section 27.14 "unit, integration, lint, and type checks pass")
# ---------------------------------------------------------------------------


def run_all_quality_gates(project_root: Path) -> dict[str, dict[str, object]]:
    """Run ruff, pyright, and pytest against *project_root* (no mutation)."""
    uv = shutil.which("uv")
    results: dict[str, dict[str, object]] = {}
    for name, argv in _QUALITY_GATES:
        if uv is None:
            results[name] = {"passed": False, "detail": "uv not found on PATH"}
            continue
        try:
            proc = subprocess.run(
                [uv, "run", *argv],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=1800.0,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            results[name] = {"passed": False, "detail": f"could not run: {exc}"}
            continue
        results[name] = {"passed": proc.returncode == 0, "detail": f"exit {proc.returncode}"}
    return results


QualityGateRunner = Callable[[Path], dict[str, dict[str, object]]]


# ---------------------------------------------------------------------------
# Development result-set integrity verification
# ---------------------------------------------------------------------------


def _has_controlled_plan(exp_dir: Path) -> bool:
    metadata = exp_dir / "metadata.json"
    if not metadata.is_file():
        return False
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(data.get("plan"), dict)


def _resolve_artifact(project_root: Path, recorded: str) -> Path | None:
    candidate = project_root / Path(recorded)
    if not candidate.is_file():
        candidate = project_root / recorded
    if not candidate.is_file():
        return None
    return candidate


def _verify_controlled_experiment(
    project_root: Path,
    results_root: Path,
    exp_dir: Path,
    exp_id: str,
    task_by_id: dict[str, LoadedTask],
    patch_by_id: dict[tuple[str, str], LoadedPatch],
    stats: dict[str, object],
) -> tuple[list[str], set[tuple[str, str]]]:
    errors: list[str] = []
    metadata_path = exp_dir / "metadata.json"
    if not metadata_path.is_file():
        errors.append(f"{exp_id}: controlled experiment has no metadata.json")
        return errors, set()
    data = cast(dict[str, object], json.loads(metadata_path.read_text(encoding="utf-8")))
    plan = _plan_entries(data)
    if not plan:
        errors.append(f"{exp_id}: plan.controlled is not a list")

    expected: set[tuple[str, str, str]] = set()
    for entry in plan:
        grader = entry.get("grader")
        task_id = entry.get("task_id")
        patch_id = entry.get("patch_id")
        if isinstance(grader, str) and isinstance(task_id, str) and isinstance(patch_id, str):
            expected.add((grader, task_id, patch_id))
    if not expected:
        errors.append(f"{exp_id}: planned matrix is empty")

    actual: set[tuple[str, str, str]] = set()
    records: dict[tuple[str, str, str], EvaluationRecord] = {}
    pre_grade: dict[tuple[str, str], dict[str, str]] = {}
    zero_infra = True
    zero_invalid = True
    artifact_ok = True
    for grader in ("naive", "hardened_v1"):
        grader_dir = exp_dir / grader
        if not grader_dir.is_dir():
            errors.append(f"{exp_id}: missing {grader} records dir")
            continue
        for record_path in sorted(grader_dir.rglob("record.json")):
            try:
                record = EvaluationRecord.model_validate(
                    json.loads(record_path.read_text(encoding="utf-8"))
                )
            except Exception as exc:
                errors.append(f"{exp_id}: invalid record {record_path}: {exc}")
                continue
            if record.phase != "controlled":
                errors.append(f"{exp_id}: record {record_path} phase is {record.phase!r}")
                continue
            if record.task.split != "development":
                errors.append(f"{exp_id}: record {record_path} split is {record.task.split!r}")
                continue
            patch = record.patch
            if patch is None:
                errors.append(f"{exp_id}: controlled record {record_path} lacks a patch")
                continue
            key = (record.grader.name, record.task.id, patch.id)
            actual.add(key)
            records[key] = record
            pre_grade.setdefault((record.task.id, patch.id), {})[
                record.grader.name
            ] = record.workspace.pre_grade_sha256

            if record.status == OutcomeStatus.INFRASTRUCTURE_ERROR.value:
                zero_infra = False
            if record.status == OutcomeStatus.INVALID_INPUT.value:
                zero_invalid = False
            if record.status != OutcomeStatus.COMPLETED.value:
                errors.append(
                    f"{exp_id}: {record.grader.name} {record.task.id}/{patch.id} "
                    f"status is {record.status!r}"
                )
            task = task_by_id.get(record.task.id)
            if task is None:
                errors.append(f"{exp_id}: unknown task {record.task.id!r}")
            elif record.task.manifest_sha256 != task.manifest_sha256:
                errors.append(f"{exp_id}: manifest hash mismatch for {record.task.id}")
            loaded = patch_by_id.get((record.task.id, patch.id))
            if loaded is None:
                errors.append(f"{exp_id}: unknown patch {record.task.id}/{patch.id}")
            else:
                if patch.metadata_sha256 != loaded.metadata_sha256:
                    errors.append(
                        f"{exp_id}: patch metadata hash mismatch {record.task.id}/{patch.id}"
                    )
                if patch.diff_sha256 != loaded.diff_sha256:
                    errors.append(
                        f"{exp_id}: patch diff hash mismatch {record.task.id}/{patch.id}"
                    )
            process = record.process
            if process is not None and process.stdout_sha256 and process.stdout_path:
                artifact = _resolve_artifact(project_root, process.stdout_path)
                if artifact is None:
                    errors.append(f"{exp_id}: missing artifact {process.stdout_path}")
                    artifact_ok = False
                elif sha256_file(artifact) != process.stdout_sha256:
                    errors.append(f"{exp_id}: artifact hash mismatch {process.stdout_path}")
                    artifact_ok = False
            if process is not None and process.stderr_sha256 and process.stderr_path:
                artifact = _resolve_artifact(project_root, process.stderr_path)
                if artifact is None:
                    errors.append(f"{exp_id}: missing artifact {process.stderr_path}")
                    artifact_ok = False
                elif sha256_file(artifact) != process.stderr_sha256:
                    errors.append(f"{exp_id}: artifact hash mismatch {process.stderr_path}")
                    artifact_ok = False

    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.append(f"{exp_id}: missing planned records: {missing}")
    if extra:
        errors.append(f"{exp_id}: extra records: {extra}")

    cross_ok = True
    for patch_key, graders in pre_grade.items():
        if len(set(graders.values())) != 1:
            errors.append(
                f"{exp_id}: cross-grader pre-grade hash mismatch for "
                f"{patch_key[0]}/{patch_key[1]}: {graders}"
            )
            cross_ok = False

    confirmed = 0
    hash_ok = True
    for task_id, patch_id in sorted({(entry[1], entry[2]) for entry in expected}):
        patch = patch_by_id.get((task_id, patch_id))
        if patch is None:
            continue
        try:
            require_confirmed_annotation(results_root, exp_id, patch)
            confirmed += 1
        except (MissingAnnotationError, AnnotationMismatchError) as exc:
            errors.append(f"{exp_id}: annotation for {task_id}/{patch_id}: {exc}")
            hash_ok = False

    stats["confirmed_annotations"] = (
        int(cast(int, stats.get("confirmed_annotations", 0))) + confirmed
    )
    stats["annotations_hash_matching"] = bool(
        cast(bool, stats.get("annotations_hash_matching", True)) and hash_ok
    )
    stats["controlled_matrix_complete"] = bool(
        cast(bool, stats.get("controlled_matrix_complete", True)) and not (missing or extra)
    )
    stats["controlled_zero_infrastructure"] = bool(
        cast(bool, stats.get("controlled_zero_infrastructure", True)) and zero_infra
    )
    stats["controlled_zero_invalid_input"] = bool(
        cast(bool, stats.get("controlled_zero_invalid_input", True)) and zero_invalid
    )
    stats["cross_grader_hashes_match"] = bool(
        cast(bool, stats.get("cross_grader_hashes_match", True)) and cross_ok
    )
    stats["artifact_hashes_match"] = bool(
        cast(bool, stats.get("artifact_hashes_match", True)) and artifact_ok
    )
    return errors, {(key[1], key[2]) for key in actual}


def _verify_validation_experiment(
    exp_dir: Path,
    exp_id: str,
    dev_tasks: list[LoadedTask],
    stats: dict[str, object],
) -> list[str]:
    errors: list[str] = []
    task_stable: dict[str, bool] = {task.manifest.id: True for task in dev_tasks}
    for task in dev_tasks:
        for case in ("baseline", "gold"):
            for idx in (1, 2, 3):
                path = (
                    exp_dir
                    / "validation"
                    / task.manifest.split.value
                    / task.manifest.id
                    / case
                    / f"{idx}.json"
                )
                if not path.is_file():
                    errors.append(f"{exp_id}: missing {task.manifest.id} {case} repeat {idx}")
                    task_stable[task.manifest.id] = False
                    continue
                try:
                    record = ValidationRecord.model_validate(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                except Exception as exc:
                    errors.append(f"{exp_id}: invalid validation record {path}: {exc}")
                    task_stable[task.manifest.id] = False
                    continue
                if not record.stable:
                    errors.append(
                        f"{exp_id}: {task.manifest.id} {case} repeat {idx} not stable"
                    )
                    task_stable[task.manifest.id] = False
    stats["validation_stable"] = bool(
        cast(bool, stats.get("validation_stable", True)) and all(task_stable.values())
    )
    return errors


def verify_development_results(
    project_root: Path, results_root: Path, tasks_dir: Path
) -> tuple[list[str], dict[str, object]]:
    """Verify every development result against its planned matrix (read-only)."""
    errors: list[str] = []
    stats: dict[str, object] = {
        "controlled_experiments": [],
        "validation_experiments": [],
        "confirmed_annotations": 0,
        "annotations_hash_matching": True,
        "controlled_matrix_complete": True,
        "controlled_zero_infrastructure": True,
        "controlled_zero_invalid_input": True,
        "cross_grader_hashes_match": True,
        "artifact_hashes_match": True,
        "validation_stable": True,
        "validation_repeat_count": 3,
        "coverage_complete": True,
    }
    if not tasks_dir.is_dir():
        errors.append(f"tasks directory does not exist: {tasks_dir}")
        return errors, stats
    dev_tasks = [
        task for task in discover_tasks(tasks_dir) if task.manifest.split is Split.DEVELOPMENT
    ]
    task_by_id = {task.manifest.id: task for task in dev_tasks}
    patch_by_id: dict[tuple[str, str], LoadedPatch] = {}
    for task in dev_tasks:
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
            patch_by_id[(task.manifest.id, patch.manifest.id)] = patch

    if not results_root.is_dir():
        errors.append(f"results root missing: {results_root}")
        return errors, stats

    experiment_dirs = sorted(
        directory
        for directory in results_root.iterdir()
        if directory.is_dir() and directory.name not in _RESERVED_RESULT_DIRS
    )
    controlled_exps: list[str] = []
    validation_exps: list[str] = []
    covered: set[tuple[str, str]] = set()
    for exp_dir in experiment_dirs:
        is_controlled = (
            (exp_dir / "naive").is_dir()
            or (exp_dir / "hardened_v1").is_dir()
            or _has_controlled_plan(exp_dir)
        )
        if is_controlled:
            controlled_exps.append(exp_dir.name)
            exp_errors, exp_covered = _verify_controlled_experiment(
                project_root,
                results_root,
                exp_dir,
                exp_dir.name,
                task_by_id,
                patch_by_id,
                stats,
            )
            errors += exp_errors
            covered |= exp_covered
        if (exp_dir / "validation").is_dir():
            validation_exps.append(exp_dir.name)
            errors += _verify_validation_experiment(exp_dir, exp_dir.name, dev_tasks, stats)

    if not controlled_exps:
        errors.append("no controlled experiment found under results/")
    if not validation_exps:
        errors.append("no validation experiment found under results/")

    missing_patches = sorted(
        (task.manifest.id, patch.manifest.id)
        for task in dev_tasks
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT)
        if (task.manifest.id, patch.manifest.id) not in covered
    )
    if missing_patches:
        stats["coverage_complete"] = False
        errors.append(
            "development patches missing completed controlled records: "
            f"{missing_patches}"
        )

    stats["controlled_experiments"] = controlled_exps
    stats["validation_experiments"] = validation_exps
    return errors, stats


# ---------------------------------------------------------------------------
# Lock payload and execution
# ---------------------------------------------------------------------------


def _package_versions() -> dict[str, str]:
    def version_of(name: str) -> str:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    return {
        "python": python_version(),
        "pytest": pytest_version(),
        "hud": hud_version(),
        "pydantic": version_of("pydantic"),
        "typer": version_of("typer"),
        "ruff": version_of("ruff"),
        "pyright": version_of("pyright"),
    }


def _experiment_inventory(results_root: Path) -> dict[str, object]:
    controlled: list[str] = []
    validation: list[str] = []
    labeling: list[str] = []
    if results_root.is_dir():
        for directory in sorted(results_root.iterdir()):
            if not directory.is_dir() or directory.name in _RESERVED_RESULT_DIRS:
                continue
            is_controlled = (
                (directory / "naive").is_dir()
                or (directory / "hardened_v1").is_dir()
                or _has_controlled_plan(directory)
            )
            if is_controlled:
                controlled.append(directory.name)
            if (directory / "validation").is_dir():
                validation.append(directory.name)
    labeling_dir = results_root / "labeling"
    if labeling_dir.is_dir():
        labeling = sorted(
            child.name for child in labeling_dir.iterdir() if child.is_dir()
        )
    return {
        "controlled": controlled,
        "validation": validation,
        "labeling": labeling,
    }


def build_freeze_lock(
    *,
    project_root: Path,
    tasks: list[LoadedTask],
    grader: str,
    git_tag: str,
    source_head_sha: str,
    gate_results: dict[str, dict[str, object]],
    stats: dict[str, object],
) -> dict[str, object]:
    protected = _hash_tracked(project_root, protected_files(project_root))
    result_files = _hash_tracked(project_root, result_set_files(project_root))
    dev_tasks = [task for task in tasks if task.manifest.split is Split.DEVELOPMENT]
    task_records: list[dict[str, object]] = []
    for task in dev_tasks:
        lock = read_task_image_lock(task)
        digest = lock.get("build_digest") if lock is not None else None
        task_records.append(
            {
                "id": task.manifest.id,
                "manifest_sha256": task.manifest_sha256,
                "image_digest": digest if isinstance(digest, str) else None,
                "dockerfile_sha256": sha256_bytes(task_dockerfile_text().encode("utf-8")),
            }
        )
    preconditions: dict[str, object] = {
        "git_author_configured": True,
        "worktree_clean_before": True,
        "tag_absent": True,
        "held_out_content_absent": True,
        "task_corpus_valid": True,
        "corpus_minimums": True,
        "quality_gates": gate_results,
        "all_development_files_tracked": True,
    }
    for key in (
        "confirmed_annotations",
        "annotations_hash_matching",
        "controlled_experiments",
        "validation_experiments",
        "controlled_matrix_complete",
        "controlled_zero_infrastructure",
        "controlled_zero_invalid_input",
        "cross_grader_hashes_match",
        "artifact_hashes_match",
        "validation_stable",
        "validation_repeat_count",
        "coverage_complete",
    ):
        preconditions[key] = stats.get(key)
    return {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "kind": "grader_freeze_v1",
        "grader": grader,
        "git_tag": git_tag,
        "timestamp_utc": utc_now(),
        "source_head_sha256": source_head_sha,
        "package_versions": _package_versions(),
        "protected_tree_sha256": aggregate_rel_hashes(protected),
        "protected_file_count": len(protected),
        "protected_files": protected,
        "tasks": task_records,
        "development_result_set_sha256": aggregate_rel_hashes(result_files),
        "development_result_file_count": len(result_files),
        "development_result_files": result_files,
        "experiments": _experiment_inventory(project_root / "results"),
        "preconditions": preconditions,
    }


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    tmp.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    try:
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _run_freeze_quality_gates(
    project_root: Path, runner: QualityGateRunner
) -> tuple[dict[str, dict[str, object]], list[str]]:
    gate_results = runner(project_root)
    failures = [
        f"{name}: {detail}"
        for name, result in gate_results.items()
        if not bool(result.get("passed"))
        for detail in [str(result.get("detail", "failed"))]
    ]
    return gate_results, failures


def run_freeze(
    *,
    project_root: Path,
    grader: str,
    git_tag: str,
    tasks_dir: Path,
    results_root: Path,
    lock_rel_path: Path = DEFAULT_FREEZE_LOCK_REL,
    quality_gate_runner: QualityGateRunner | None = None,
) -> FreezeResult:
    """Execute the Section 27.14 freeze, refusing any mutation on failure."""
    if grader != "hardened_v1":
        raise FreezeError(f"freeze supports only --grader hardened_v1, got {grader!r}")
    if _git(project_root, ["check-ref-format", f"refs/tags/{git_tag}"]).returncode != 0:
        raise FreezeError(f"invalid git tag name {git_tag!r}")

    problems: list[str] = []
    if tag_exists(project_root, git_tag):
        problems.append(f"git tag {git_tag!r} already exists")
    lock_path = project_root / lock_rel_path
    if lock_path.exists():
        problems.append(f"freeze lock already exists: {lock_path}")
    if not git_author_configured(project_root):
        problems.append("git user.name and user.email must be configured before freeze")
    source_head = head_commit(project_root)
    if source_head is None:
        problems.append("repository has no commits; create a baseline commit before freeze")
    if not worktree_clean(project_root):
        problems.append("working tree is not clean")

    if not tasks_dir.is_dir():
        problems.append(f"tasks directory does not exist: {tasks_dir}")
        tasks = []
    else:
        tasks = discover_tasks(tasks_dir)
    dev_tasks = [task for task in tasks if task.manifest.split is Split.DEVELOPMENT]
    if not dev_tasks:
        problems.append(f"no development tasks under {tasks_dir}")
    problems += find_held_out_content(project_root, tasks_dir, results_root)
    for task in tasks:
        problems += check_task_corpus(task)
    problems += check_development_corpus_minimums(tasks)
    problems += verify_task_image_locks(dev_tasks)

    result_errors, stats = verify_development_results(project_root, results_root, tasks_dir)
    problems += result_errors

    gate_results: dict[str, dict[str, object]] = {}
    if not problems:
        gate_runner = quality_gate_runner or run_all_quality_gates
        gate_results, gate_failures = _run_freeze_quality_gates(project_root, gate_runner)
        problems += [f"quality gate failed: {failure}" for failure in gate_failures]
        if not worktree_clean(project_root):
            problems.append("working tree became dirty after quality gates")

    if problems:
        raise FreezeError("freeze preconditions not met:\n" + "\n".join(f"- {p}" for p in problems))

    assert source_head is not None
    payload = build_freeze_lock(
        project_root=project_root,
        tasks=tasks,
        grader=grader,
        git_tag=git_tag,
        source_head_sha=source_head,
        gate_results=gate_results,
        stats=stats,
    )
    _atomic_write_json(lock_path, payload)

    committed = False
    try:
        staged = _git(project_root, ["add", "--", lock_rel_path.as_posix()])
        if staged.returncode != 0:
            raise FreezeError(f"git add failed: {staged.stderr.strip()}")
        only = _git(project_root, ["diff", "--cached", "--name-only"])
        if only.stdout.strip() != lock_rel_path.as_posix():
            raise FreezeError(
                f"refusing to commit: staged paths are not exactly the lock file "
                f"({only.stdout.strip()!r})"
            )
        commit = _git(project_root, ["commit", "-m", FREEZE_COMMIT_MESSAGE])
        if commit.returncode != 0:
            raise FreezeError(
                f"freeze commit failed: {commit.stderr.strip() or commit.stdout.strip()}"
            )
        committed = True
        freeze_commit_sha = git_ok(project_root, ["rev-parse", "HEAD"])

        tag_message = FREEZE_COMMIT_MESSAGE
        tag = _git(project_root, ["tag", "-a", git_tag, "-m", tag_message])
        if tag.returncode != 0:
            raise FreezeError(f"git tag failed: {tag.stderr.strip()}")

        tag_object_sha = git_ok(project_root, ["rev-parse", git_tag])
        tag_commit_sha = git_ok(project_root, ["rev-parse", f"{git_tag}^{{commit}}"])
        tag_tree_sha = git_ok(project_root, ["rev-parse", f"{git_tag}^{{tree}}"])
        if tag_commit_sha != freeze_commit_sha:
            raise FreezeError(
                f"tag {git_tag!r} resolves to {tag_commit_sha}, expected {freeze_commit_sha}"
            )

        protected_after = aggregate_rel_hashes(
            _hash_tracked(project_root, protected_files(project_root))
        )
        result_after = aggregate_rel_hashes(
            _hash_tracked(project_root, result_set_files(project_root))
        )
        recorded_protected = cast(str, payload["protected_tree_sha256"])
        recorded_result = cast(str, payload["development_result_set_sha256"])
        if protected_after != recorded_protected:
            raise FreezeError(
                f"protected-tree hash changed after commit: "
                f"{recorded_protected} -> {protected_after}"
            )
        if result_after != recorded_result:
            raise FreezeError(
                f"result-set hash changed after commit: {recorded_result} -> {result_after}"
            )
        if not worktree_clean(project_root):
            raise FreezeError("working tree is not clean after freeze")
    except BaseException:
        if not committed and lock_path.exists():
            lock_path.unlink(missing_ok=True)
        raise

    return FreezeResult(
        lock_path=lock_path,
        freeze_commit_sha=freeze_commit_sha,
        tag_object_sha=tag_object_sha,
        tag_commit_sha=tag_commit_sha,
        tag_tree_sha=tag_tree_sha,
        source_head_sha=source_head,
        protected_tree_sha256=cast(str, payload["protected_tree_sha256"]),
        development_result_set_sha256=cast(str, payload["development_result_set_sha256"]),
        timestamp_utc=cast(str, payload["timestamp_utc"]),
    )
