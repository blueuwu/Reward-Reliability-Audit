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
import re
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

_PROTECTED_DIR_ROOTS = ("grader_audit", "tests", "tasks")
_PROTECTED_FILE_ROOTS = ("env.py", "tasks.py", "pyproject.toml", "uv.lock")
_RESERVED_RESULT_DIRS = ("annotations", "labeling")
_QUALITY_GATES = (
    ("ruff", ("ruff", "check", ".")),
    ("pyright", ("pyright",)),
    ("pytest", ("pytest", "-q")),
)
_ZERO_COMMIT = "0" * 40
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def real_commit_sha(value: str) -> bool:
    """True when *value* is a real nonzero 40-char commit SHA (Section 27.14).

    Records produced before the first Git commit carry an all-zero placeholder;
    such records are historical and never count as final freeze evidence.
    """
    return bool(_FULL_SHA_RE.fullmatch(value)) and value != _ZERO_COMMIT


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
    controlled_experiments: tuple[str, ...]
    validation_experiments: tuple[str, ...]


@dataclass(frozen=True)
class FinalEvidenceSelection:
    """The final development evidence selected for freezing.

    Selection is content/provenance-based: only complete, stable, clean
    (``worktree_dirty: false``) experiments whose records carry real nonzero
    40-char commit SHAs and matching confirmed annotations are eligible.
    Historical experiments (e.g. records predating the first commit) are never
    selected and never appear in the result-set hash or lock inventory.
    """

    controlled: tuple[str, ...] = ()
    validation: tuple[str, ...] = ()

    @property
    def annotations_roots(self) -> tuple[str, ...]:
        return tuple(f"results/annotations/{exp}" for exp in self.controlled)


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


def protected_files(project_root: Path, selection: FinalEvidenceSelection) -> list[str]:
    """Tracked files that make up the Section 27.14 protected surface.

    The protected surface is the grader code, automated tests, development task
    inputs, the root manifests/locks, and the confirmed annotations of the
    selected final controlled experiment. Historical annotations are not part
    of the frozen surface.
    """
    roots = (
        _PROTECTED_DIR_ROOTS + _PROTECTED_FILE_ROOTS + selection.annotations_roots
    )
    return sorted(path for path in tracked_files(project_root) if _under_root(path, roots))


def result_set_files(
    project_root: Path, selection: FinalEvidenceSelection, raw_results_root: Path
) -> list[str]:
    """Tracked files that make up the final development result-set.

    Only the raw records and artifacts of the selected complete, stable, clean,
    committed validation and controlled experiments under ``results/raw/`` are
    included. Historical experiments (all-zero commit SHAs or dirty worktrees)
    never appear.
    """
    resolved = raw_results_root
    if resolved.is_absolute():
        resolved = resolved.relative_to(project_root)
    prefix = resolved.as_posix().rstrip("/")
    roots = [f"{prefix}/{exp}" for exp in selection.controlled + selection.validation]
    return sorted(
        path for path in tracked_files(project_root) if _under_root(path, tuple(roots))
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
    if (results_root.parent / "model_rollouts").exists():
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
    if not isinstance(data.get("plan"), dict):
        return False
    plan = cast(dict[str, object], data["plan"])
    controlled = plan.get("controlled")
    return isinstance(controlled, list) and len(cast(list[object], controlled)) > 0


def resolve_artifact_in_experiment(
    project_root: Path, exp_dir: Path, recorded: str
) -> Path | None:
    """Resolve an artifact path path-safely and require it inside the experiment dir."""
    from grader_audit.core.path_rules import classify_repository_relative

    if not recorded or "\x00" in recorded:
        return None
    normalized = recorded.replace("\\", "/")
    if classify_repository_relative(normalized) is not None:
        return None
    candidate = project_root / Path(normalized)
    if not candidate.is_file():
        return None
    try:
        candidate.resolve().relative_to(exp_dir.resolve())
    except ValueError:
        return None
    return candidate


def controlled_experiment_eligible(
    project_root: Path,
    results_root: Path,
    annotations_root: Path,
    exp_dir: Path,
    exp_id: str,
    task_by_id: dict[str, LoadedTask],
    patch_by_id: dict[tuple[str, str], LoadedPatch],
) -> tuple[bool, tuple[str, ...], set[tuple[str, str]]]:
    """Return ``(eligible, reasons, covered patches)`` for one controlled experiment.

    An experiment is eligible final evidence only when its planned matrix is
    complete with every record ``completed``, every record carries a real
    nonzero 40-char commit SHA and ``worktree_dirty: false``, cross-grader and
    artifact hashes match, and every planned patch has a confirmed
    hash-matching annotation. Any failure makes the whole experiment ineligible
    (it is historical and excluded rather than frozen).
    """
    reasons: list[str] = []
    metadata_path = exp_dir / "metadata.json"
    if not metadata_path.is_file():
        return False, (f"{exp_id}: controlled experiment has no metadata.json",), set()
    try:
        data = cast(dict[str, object], json.loads(metadata_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as exc:
        return False, (f"{exp_id}: unreadable metadata.json: {exc}",), set()
    plan = _plan_entries(data)
    if not plan:
        return False, (f"{exp_id}: plan.controlled is not a list",), set()

    expected: list[tuple[str, str, str]] = []
    seen_plan: set[tuple[str, str, str]] = set()
    for entry in plan:
        grader = entry.get("grader")
        task_id = entry.get("task_id")
        patch_id = entry.get("patch_id")
        if isinstance(grader, str) and isinstance(task_id, str) and isinstance(patch_id, str):
            key = (grader, task_id, patch_id)
            if key in seen_plan:
                reasons.append(f"{exp_id}: duplicate planned cell {key}")
                continue
            seen_plan.add(key)
            expected.append(key)
            if entry.get("phase") != "controlled":
                reasons.append(f"{exp_id}: planned cell {key} has non-controlled phase")
            if entry.get("split") != Split.DEVELOPMENT.value:
                reasons.append(f"{exp_id}: planned cell {key} has wrong split")
            task = task_by_id.get(task_id)
            patch = patch_by_id.get((task_id, patch_id))
            if task is None or entry.get("task_manifest_sha256") != task.manifest_sha256:
                reasons.append(f"{exp_id}: planned task manifest mismatch for {key}")
            if patch is None or entry.get("patch_metadata_sha256") != patch.metadata_sha256:
                reasons.append(f"{exp_id}: planned patch metadata mismatch for {key}")
            if patch is None or entry.get("patch_diff_sha256") != patch.diff_sha256:
                reasons.append(f"{exp_id}: planned patch diff mismatch for {key}")
    expected_set = set(expected)
    if not expected_set:
        return False, (f"{exp_id}: planned matrix is empty",), set()

    actual_keys: list[tuple[str, str, str]] = []
    records: dict[tuple[str, str, str], EvaluationRecord] = {}
    pristine: dict[tuple[str, str], dict[str, str]] = {}
    pre_grade: dict[tuple[str, str], dict[str, str]] = {}
    for grader in ("naive", "hardened_v1"):
        grader_dir = exp_dir / grader
        if not grader_dir.is_dir():
            reasons.append(f"{exp_id}: missing {grader} records dir")
            continue
        for record_path in sorted(grader_dir.rglob("*.json")):
            try:
                record = EvaluationRecord.model_validate(
                    json.loads(record_path.read_text(encoding="utf-8"))
                )
            except Exception as exc:
                reasons.append(f"{exp_id}: invalid record {record_path}: {exc}")
                continue
            if record.phase != "controlled":
                reasons.append(f"{exp_id}: record {record_path} phase is {record.phase!r}")
                continue
            if record.task.split != "development":
                reasons.append(f"{exp_id}: record {record_path} split is {record.task.split!r}")
                continue
            patch = record.patch
            if patch is None:
                reasons.append(f"{exp_id}: controlled record {record_path} lacks a patch")
                continue
            expected_path = (
                grader_dir / record.task.split / record.task.id / f"{patch.id}.json"
            )
            if record_path.resolve() != expected_path.resolve():
                reasons.append(f"{exp_id}: record at wrong path {record_path}")
            key = (record.grader.name, record.task.id, patch.id)
            if key in records:
                reasons.append(f"{exp_id}: duplicate actual record {key}")
                continue
            records[key] = record
            actual_keys.append(key)
            pristine.setdefault((record.task.id, patch.id), {})[
                record.grader.name
            ] = record.workspace.pristine_sha256
            pre_grade.setdefault((record.task.id, patch.id), {})[
                record.grader.name
            ] = record.workspace.pre_grade_sha256

            if record.status != OutcomeStatus.COMPLETED.value:
                reasons.append(
                    f"{exp_id}: {record.grader.name} {record.task.id}/{patch.id} "
                    f"status is {record.status!r}"
                )
            if not real_commit_sha(record.git.data_commit):
                reasons.append(
                    f"{exp_id}: {record.grader.name} {record.task.id}/{patch.id} "
                    f"has no real commit SHA ({record.git.data_commit!r})"
                )
            if record.git.worktree_dirty:
                reasons.append(
                    f"{exp_id}: {record.grader.name} {record.task.id}/{patch.id} "
                    "recorded a dirty worktree"
                )
            task = task_by_id.get(record.task.id)
            if task is None:
                reasons.append(f"{exp_id}: unknown task {record.task.id!r}")
            elif record.task.manifest_sha256 != task.manifest_sha256:
                reasons.append(f"{exp_id}: manifest hash mismatch for {record.task.id}")
            loaded = patch_by_id.get((record.task.id, patch.id))
            if loaded is None:
                reasons.append(f"{exp_id}: unknown patch {record.task.id}/{patch.id}")
            else:
                if patch.metadata_sha256 != loaded.metadata_sha256:
                    reasons.append(
                        f"{exp_id}: patch metadata hash mismatch {record.task.id}/{patch.id}"
                    )
                if patch.diff_sha256 != loaded.diff_sha256:
                    reasons.append(
                        f"{exp_id}: patch diff hash mismatch {record.task.id}/{patch.id}"
                    )
            process = record.process
            if process is not None:
                for attr in ("stdout_path", "stderr_path"):
                    recorded = getattr(process, attr)
                    sha = getattr(process, f"{attr[:6]}_sha256")
                    if bool(recorded) != bool(sha):
                        reasons.append(
                            f"{exp_id}: artifact pair invalid for "
                            f"{record.grader.name} {record.task.id}/{patch.id}"
                        )
                    elif recorded and sha:
                        artifact = resolve_artifact_in_experiment(
                            project_root, exp_dir, recorded
                        )
                        if artifact is None:
                            reasons.append(
                                f"{exp_id}: missing/unsafe artifact {recorded}"
                            )
                        elif sha256_file(artifact) != sha:
                            reasons.append(
                                f"{exp_id}: artifact hash mismatch {recorded}"
                            )

    actual_set = set(actual_keys)
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    if missing:
        reasons.append(f"{exp_id}: missing planned records: {missing}")
    if extra:
        reasons.append(f"{exp_id}: extra records: {extra}")

    for patch_key, graders in pristine.items():
        if len(set(graders.values())) != 1:
            reasons.append(
                f"{exp_id}: cross-grader pristine hash mismatch for "
                f"{patch_key[0]}/{patch_key[1]}"
            )
    for patch_key, graders in pre_grade.items():
        if len(set(graders.values())) != 1:
            reasons.append(
                f"{exp_id}: cross-grader pre-grade hash mismatch for "
                f"{patch_key[0]}/{patch_key[1]}"
            )

    for task_id, patch_id in sorted({(entry[1], entry[2]) for entry in expected}):
        patch = patch_by_id.get((task_id, patch_id))
        if patch is None:
            continue
        try:
            require_confirmed_annotation(annotations_root, exp_id, patch)
        except (MissingAnnotationError, AnnotationMismatchError) as exc:
            reasons.append(f"{exp_id}: annotation for {task_id}/{patch_id}: {exc}")

    covered = {(key[1], key[2]) for key in actual_keys}
    return (not reasons), tuple(reasons), covered


def validation_experiment_eligible(
    project_root: Path,
    exp_dir: Path,
    exp_id: str,
    dev_tasks: list[LoadedTask],
) -> tuple[bool, tuple[str, ...]]:
    """Return ``(eligible, reasons)`` for one validation experiment.

    Eligible only when every development task has three stable baseline and gold
    repeats whose records carry a real nonzero commit SHA and
    ``worktree_dirty: false``, match the task manifest identity, validate
    process artifacts, and match the experiment's reserved validation plan
    (when one exists) with no missing/extra/duplicate cells.
    """
    reasons: list[str] = []
    expected: set[tuple[str, str, str, int]] | None = None
    metadata_path = exp_dir / "metadata.json"
    if metadata_path.is_file():
        try:
            data = cast(dict[str, object], json.loads(metadata_path.read_text(encoding="utf-8")))
            plan_value = data.get("plan")
            validation_raw: list[object] = []
            if isinstance(plan_value, dict):
                validation = cast(dict[str, object], plan_value).get("validation")
                if isinstance(validation, list):
                    validation_raw = cast(list[object], validation)
            expected = set()
            seen: set[tuple[str, str, str, int]] = set()
            for entry in validation_raw:
                if not isinstance(entry, dict):
                    continue
                item = cast(dict[str, object], entry)
                repeat_value = item.get("repeat_index")
                key = (
                    str(item.get("split", "")),
                    str(item.get("task_id", "")),
                    str(item.get("validation_case", "")),
                    int(repeat_value) if isinstance(repeat_value, int) else 0,
                )
                if key in seen:
                    reasons.append(f"{exp_id}: duplicate planned validation cell {key}")
                    continue
                seen.add(key)
                expected.add(key)
        except (json.JSONDecodeError, OSError):
            reasons.append(f"{exp_id}: unreadable validation metadata.json")

    actual: set[tuple[str, str, str, int]] = set()
    expected_paths: set[Path] = set()
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
                expected_paths.add(path.resolve())
                if not path.is_file():
                    reasons.append(f"{exp_id}: missing {task.manifest.id} {case} repeat {idx}")
                    continue
                try:
                    record = ValidationRecord.model_validate(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                except Exception as exc:
                    reasons.append(f"{exp_id}: invalid validation record {path}: {exc}")
                    continue
                actual.add((task.manifest.split.value, task.manifest.id, case, idx))
                if (
                    record.task.id != task.manifest.id
                    or record.task.split != task.manifest.split.value
                    or record.validation_case != case
                    or record.repeat_index != idx
                ):
                    reasons.append(
                        f"{exp_id}: validation record identity/path mismatch at {path}"
                    )
                if not record.stable:
                    reasons.append(f"{exp_id}: {task.manifest.id} {case} repeat {idx} not stable")
                if not real_commit_sha(record.git.data_commit):
                    reasons.append(
                        f"{exp_id}: {task.manifest.id} {case} repeat {idx} "
                        f"has no real commit SHA ({record.git.data_commit!r})"
                    )
                if record.git.worktree_dirty:
                    reasons.append(
                        f"{exp_id}: {task.manifest.id} {case} repeat {idx} "
                        "recorded a dirty worktree"
                    )
                if record.task.manifest_sha256 != task.manifest_sha256:
                    reasons.append(
                        f"{exp_id}: {task.manifest.id} validation manifest hash mismatch"
                    )
                for run in record.runs.values():
                    process = run.process
                    if process is None:
                        continue
                    for attr in ("stdout_path", "stderr_path"):
                        recorded = getattr(process, attr)
                        sha = getattr(process, f"{attr[:6]}_sha256")
                        if bool(recorded) != bool(sha):
                            reasons.append(
                                f"{exp_id}: artifact pair invalid for "
                                f"{task.manifest.id} {case} repeat {idx}"
                            )
                        elif recorded and sha:
                            artifact = resolve_artifact_in_experiment(
                                project_root, exp_dir, recorded
                            )
                            if artifact is None:
                                reasons.append(
                                    f"{exp_id}: missing/unsafe artifact {recorded}"
                                )
                            elif sha256_file(artifact) != sha:
                                reasons.append(
                                    f"{exp_id}: artifact hash mismatch {recorded}"
                                )

    validation_root = exp_dir / "validation"
    if validation_root.is_dir():
        for path in validation_root.rglob("*.json"):
            if path.resolve() not in expected_paths:
                reasons.append(f"{exp_id}: unexpected validation record path {path}")

    if expected is not None:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing:
            reasons.append(f"{exp_id}: validation plan missing cells {missing}")
        if extra:
            reasons.append(f"{exp_id}: validation plan extra cells {extra}")
    return (not reasons), tuple(reasons)


def _empty_evidence_stats() -> dict[str, object]:
    return {
        "controlled_experiments": [],
        "validation_experiments": [],
        "confirmed_annotations": 0,
        "annotations_hash_matching": False,
        "controlled_matrix_complete": False,
        "controlled_zero_infrastructure": False,
        "controlled_zero_invalid_input": False,
        "cross_grader_hashes_match": False,
        "artifact_hashes_match": False,
        "validation_stable": False,
        "validation_repeat_count": 3,
        "evidence_data_commit_valid": False,
        "evidence_worktree_clean": False,
        "coverage_complete": False,
    }


def verify_development_results(
    project_root: Path,
    results_root: Path,
    tasks_dir: Path,
    annotations_root: Path,
) -> tuple[list[str], dict[str, object], FinalEvidenceSelection]:
    """Select final evidence and verify it (read-only, content/provenance-based).

    Returns ``(errors, stats, selection)``. Only complete, stable, clean,
    committed validation and controlled experiments with real nonzero commit
    SHAs and matching confirmed annotations/artifacts are selected; historical
    experiments (e.g. records predating the first commit) are excluded and
    never appear in the result-set hash or lock inventory.
    """
    errors: list[str] = []
    if not tasks_dir.is_dir():
        errors.append(f"tasks directory does not exist: {tasks_dir}")
        return errors, _empty_evidence_stats(), FinalEvidenceSelection()
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
        return errors, _empty_evidence_stats(), FinalEvidenceSelection()

    experiment_dirs = sorted(
        directory
        for directory in results_root.iterdir()
        if directory.is_dir() and directory.name not in _RESERVED_RESULT_DIRS
    )
    controlled_candidates: list[str] = []
    validation_candidates: list[str] = []
    selected_controlled: list[str] = []
    selected_validation: list[str] = []
    controlled_rejections: list[str] = []
    validation_rejections: list[str] = []
    covered: set[tuple[str, str]] = set()
    for exp_dir in experiment_dirs:
        is_controlled = (
            (exp_dir / "naive").is_dir()
            or (exp_dir / "hardened_v1").is_dir()
            or _has_controlled_plan(exp_dir)
        )
        if is_controlled:
            controlled_candidates.append(exp_dir.name)
            eligible, reasons, exp_covered = controlled_experiment_eligible(
                project_root,
                results_root,
                annotations_root,
                exp_dir,
                exp_dir.name,
                task_by_id,
                patch_by_id,
            )
            if eligible:
                selected_controlled.append(exp_dir.name)
                covered |= exp_covered
            else:
                controlled_rejections.extend(reasons)
        if (exp_dir / "validation").is_dir():
            validation_candidates.append(exp_dir.name)
            eligible, reasons = validation_experiment_eligible(
                project_root, exp_dir, exp_dir.name, dev_tasks
            )
            if eligible:
                selected_validation.append(exp_dir.name)
            else:
                validation_rejections.extend(reasons)

    if not controlled_candidates:
        errors.append("no controlled experiment found under results/")
    elif not selected_controlled:
        errors.append(
            "no eligible controlled experiment: every controlled experiment has "
            "incomplete records, non-completed outcomes, an all-zero commit SHA, "
            "a dirty worktree, or missing/mismatched annotations"
        )
        errors.extend(controlled_rejections)
    if not validation_candidates:
        errors.append("no validation experiment found under results/")
    elif not selected_validation:
        errors.append(
            "no eligible validation experiment: every validation experiment has "
            "unstable repeats, an all-zero commit SHA, or a dirty worktree"
        )
        errors.extend(validation_rejections)

    selection = FinalEvidenceSelection(
        controlled=tuple(selected_controlled), validation=tuple(selected_validation)
    )

    missing_patches = sorted(
        (task.manifest.id, patch.manifest.id)
        for task in dev_tasks
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT)
        if (task.manifest.id, patch.manifest.id) not in covered
    )
    if missing_patches:
        errors.append(
            "development patches missing completed, clean, committed controlled records: "
            f"{missing_patches}"
        )
    if dev_tasks and not selected_validation:
        errors.append(
            "development validation evidence missing: no eligible experiment provides "
            "3 stable baseline/gold repeats for every development task"
        )

    coverage_ok = bool(selected_controlled and selected_validation and not missing_patches)
    stats: dict[str, object] = {
        "controlled_experiments": selected_controlled,
        "validation_experiments": selected_validation,
        "confirmed_annotations": len(covered) if selected_controlled else 0,
        "annotations_hash_matching": bool(selected_controlled),
        "controlled_matrix_complete": bool(selected_controlled),
        "controlled_zero_infrastructure": bool(selected_controlled),
        "controlled_zero_invalid_input": bool(selected_controlled),
        "cross_grader_hashes_match": bool(selected_controlled),
        "artifact_hashes_match": bool(selected_controlled),
        "validation_stable": bool(selected_validation),
        "validation_repeat_count": 3,
        "evidence_data_commit_valid": bool(selected_controlled and selected_validation),
        "evidence_worktree_clean": bool(selected_controlled and selected_validation),
        "coverage_complete": coverage_ok,
    }
    return errors, stats, selection


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


def build_freeze_lock(
    *,
    project_root: Path,
    tasks: list[LoadedTask],
    selection: FinalEvidenceSelection,
    grader: str,
    git_tag: str,
    source_head_sha: str,
    gate_results: dict[str, dict[str, object]],
    stats: dict[str, object],
    raw_results_root: Path,
) -> dict[str, object]:
    protected = _hash_tracked(project_root, protected_files(project_root, selection))
    result_files = _hash_tracked(
        project_root, result_set_files(project_root, selection, raw_results_root)
    )
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
        "evidence_data_commit_valid",
        "evidence_worktree_clean",
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
        "experiments": {
            "controlled": list(selection.controlled),
            "validation": list(selection.validation),
        },
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
    annotations_root: Path,
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

    result_errors, stats, selection = verify_development_results(
        project_root, results_root, tasks_dir, annotations_root
    )
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
        selection=selection,
        grader=grader,
        git_tag=git_tag,
        source_head_sha=source_head,
        gate_results=gate_results,
        stats=stats,
        raw_results_root=results_root,
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
            _hash_tracked(project_root, protected_files(project_root, selection))
        )
        result_after = aggregate_rel_hashes(
            _hash_tracked(
                project_root,
                result_set_files(project_root, selection, results_root),
            )
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
        controlled_experiments=selection.controlled,
        validation_experiments=selection.validation,
    )
