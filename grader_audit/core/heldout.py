"""Official ``run-heldout`` orchestration (Sections 27.14, 27.15).

``grader-audit run-heldout --tasks TASKS --graders naive,hardened_v1
--experiment-id ID --require-tag grader-v1-frozen`` runs only the
frozen-evaluation matrix against the frozen naive grader and verified hardened
v1, using separate identical patched workspaces, and applies the same
confirmed-annotation requirement as ``run-controlled``.

It refuses to execute (exit 5) when the freeze tag/lock is missing or any
protected file is missing, changed, or added against the aggregate locked
selection, and refuses (exit 2) when a selected task is not ``frozen_eval``,
was not introduced strictly after the freeze commit, any selected held-out
input is untracked or modified, or any patch lacks a confirmed hash-matching
annotation. The full planned matrix (with immutable identity hashes) is
reserved before any evaluation so a partial failure stays diagnosable.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from grader_audit.core.annotations import (
    AnnotationMismatchError,
    MissingAnnotationError,
    bind_raw_record_hashes,
    require_confirmed_annotation,
)
from grader_audit.core.freeze import aggregate_rel_hashes
from grader_audit.core.hashing import sha256_file
from grader_audit.core.manifests import (
    LoadedTask,
    discover_patches,
    discover_tasks,
    load_task,
)
from grader_audit.core.models import PatchSplit, Split
from grader_audit.core.orchestrator import (
    build_patch_record,
    evaluate_grader,
    git_info,
    plan_cell,
    prepare_task,
    utc_now,
)
from grader_audit.core.paths import ANNOTATIONS_ROOT
from grader_audit.core.process import Runner
from grader_audit.core.recorder import ExperimentRecorder
from grader_audit.core.workspace import WorkspaceManager
from grader_audit.images import resolve_task_image

DEFAULT_FREEZE_LOCK_REL = Path("freeze") / "grader_v1.lock.json"
_ROOT_FILES = ("env.py", "tasks.py", "pyproject.toml", "uv.lock")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_HELDOUT_GRADERS = ("naive", "hardened_v1")


class HeldoutInputError(RuntimeError):
    """Uncommitted/missing held-out input or invalid selection (exit 2)."""


class FrozenViolationError(RuntimeError):
    """Freeze tag/lock/protected-hash violation (exit 5)."""


@dataclass(frozen=True)
class HeldoutSummary:
    experiment_id: str
    record_count: int
    task_ids: tuple[str, ...]
    patch_count: int


def _git(project_root: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(project_root), *argv],
        capture_output=True,
        text=True,
        check=False,
    )


def _require(condition: bool, code: type[RuntimeError], message: str) -> None:
    if not condition:
        raise code(message)


def _repo_rel(project_root: Path, path: Path) -> str:
    """Return a project-root-relative POSIX path for *path* (must be inside root)."""
    try:
        rel = path.resolve().relative_to(project_root.resolve())
    except ValueError:
        raise HeldoutInputError(f"path outside project root: {path}") from None
    return rel.as_posix()


def resolve_roots(
    project_root: Path, raw_results_root: Path, annotations_root: Path
) -> tuple[Path, Path]:
    """Resolve relative raw/annotation roots against the project root."""
    if not raw_results_root.is_absolute():
        raw_results_root = project_root / raw_results_root
    if not annotations_root.is_absolute():
        annotations_root = project_root / annotations_root
    return raw_results_root, annotations_root


def record_file_path(
    raw_results_root: Path,
    experiment_id: str,
    grader: str,
    split: str,
    task_id: str,
    patch_id: str,
) -> Path:
    return (
        raw_results_root / experiment_id / grader / split / task_id / f"{patch_id}.json"
    )


def bind_patch_raw_hashes(
    raw_results_root: Path,
    annotations_root: Path,
    experiment_id: str,
    graders: tuple[str, ...],
    split: str,
    task_id: str,
    patch_id: str,
) -> None:
    """Phase-2 mechanical binding: append raw record SHA-256s to the annotation."""
    raw_hash_by_grader = {
        grader: sha256_file(
            record_file_path(raw_results_root, experiment_id, grader, split, task_id, patch_id)
        )
        for grader in graders
    }
    bind_raw_record_hashes(
        annotations_root, experiment_id, task_id, patch_id, raw_hash_by_grader
    )


def _under_any(rel: str, roots: tuple[str, ...]) -> bool:
    return any(rel == root or rel.startswith(root.rstrip("/") + "/") for root in roots)


def verify_frozen_lock(
    project_root: Path,
    require_tag: str,
    lock_rel_path: Path = DEFAULT_FREEZE_LOCK_REL,
) -> dict[str, object]:
    """Verify the freeze tag/lock and the full protected surface (27.14)."""
    _require(bool(require_tag), FrozenViolationError, "require_tag must be provided")
    lock_path = project_root / lock_rel_path
    _require(lock_path.is_file(), FrozenViolationError, f"freeze lock missing: {lock_path}")
    lock_bytes = lock_path.read_bytes()
    lock = cast(dict[str, object], json.loads(lock_bytes.decode("utf-8")))

    tag = _git(project_root, "rev-parse", "-q", "--verify", f"refs/tags/{require_tag}")
    _require(tag.returncode == 0, FrozenViolationError, f"required tag {require_tag!r} missing")
    tag_type = _git(project_root, "cat-file", "-t", require_tag).stdout.strip()
    _require(
        tag_type == "tag",
        FrozenViolationError,
        f"tag {require_tag!r} is not an annotated tag",
    )
    tag_commit = _git(project_root, "rev-parse", "-q", f"{require_tag}^{{commit}}")
    _require(
        tag_commit.returncode == 0 and bool(_FULL_SHA.fullmatch(tag_commit.stdout.strip())),
        FrozenViolationError,
        "freeze tag does not resolve to a commit",
    )
    tc = tag_commit.stdout.strip()

    committed = _git(project_root, "show", f"{tc}:{lock_rel_path.as_posix()}")
    _require(
        committed.returncode == 0 and committed.stdout.encode("utf-8") == lock_bytes,
        FrozenViolationError,
        "working freeze lock differs from the lock committed at the freeze tag",
    )
    _require(lock.get("git_tag") == require_tag, FrozenViolationError, "lock git_tag mismatch")

    head = _git(project_root, "rev-parse", "HEAD").stdout.strip()
    _require(bool(_FULL_SHA.fullmatch(head)), FrozenViolationError, "no valid HEAD commit")
    parent = _git(project_root, "rev-parse", f"{tc}~1").stdout.strip()
    _require(
        parent == lock.get("source_head_sha256"),
        FrozenViolationError,
        "freeze commit parent does not match lock source_head",
    )

    protected = lock.get("protected_files")
    _require(isinstance(protected, dict), FrozenViolationError, "lock has no protected_files")
    protected = cast(dict[str, str], protected)
    for rel, expected in protected.items():
        path = project_root / Path(rel)
        _require(path.is_file(), FrozenViolationError, f"protected file missing: {rel}")
        _require(
            sha256_file(path) == expected,
            FrozenViolationError,
            f"protected hash mismatch: {rel}",
        )
    _require(
        aggregate_rel_hashes(protected) == lock.get("protected_tree_sha256"),
        FrozenViolationError,
        "protected aggregate does not match the lock",
    )

    tracked = [line for line in _git(project_root, "ls-files").stdout.splitlines() if line]
    allowed_additions = ("results", "docs", "grader_v2", "adaptive_attempts")
    seen_heldout_roots: set[str] = set()
    for rel in tracked:
        if rel == lock_rel_path.as_posix():
            # The lock is necessarily introduced by the freeze commit itself.
            continue
        if rel in protected:
            continue
        if rel in _ROOT_FILES:
            raise FrozenViolationError(f"added protected root file after freeze: {rel}")
        if rel.startswith(("grader_audit/", "tests/")):
            raise FrozenViolationError(f"added protected-path file after freeze: {rel}")
        if rel.startswith("tasks/"):
            parts = rel.split("/")
            if len(parts) < 2:
                raise FrozenViolationError(f"unexpected tasks/ path after freeze: {rel}")
            root = "tasks/" + parts[1]
            if not _file_absent_at(project_root, tc, f"{root}/task.yaml"):
                raise FrozenViolationError(f"added protected-path file after freeze: {rel}")
            if root in seen_heldout_roots:
                continue
            seen_heldout_roots.add(root)
            _verify_new_heldout_root(project_root, tc, root)
            continue
        if _under_any(rel, allowed_additions) or _is_root_markdown(rel):
            continue
        raise FrozenViolationError(f"unauthorized post-freeze addition: {rel}")

    untracked = _git(
        project_root, "status", "--porcelain", "--untracked-files=all"
    ).stdout
    for line in untracked.splitlines():
        rel = line[3:].strip()
        if not rel:
            continue
        if rel.startswith("tasks/"):
            raise FrozenViolationError(f"untracked held-out input after freeze: {rel}")
        if _under_any(rel, allowed_additions) or _is_root_markdown(rel):
            continue
        raise FrozenViolationError(f"untracked unauthorized addition after freeze: {rel}")
    return lock


def _is_root_markdown(rel: str) -> bool:
    return "/" not in rel and rel.endswith(".md")


def _verify_new_heldout_root(project_root: Path, tag_commit: str, root: str) -> None:
    """Validate a brand-new post-freeze ``tasks/<dir>`` tree (Section 27.14).

    The tree must be a valid ``frozen_eval`` task introduced strictly after the
    freeze commit; any other new task tree is a protected-surface violation.
    """
    rel = f"{root}/task.yaml"
    if not (project_root / Path(rel)).is_file():
        raise FrozenViolationError(f"new tasks/ tree without task.yaml: {root}")
    _require(
        _file_absent_at(project_root, tag_commit, rel),
        FrozenViolationError,
        f"new task tree existed at the freeze tag: {root}",
    )
    try:
        task = load_task(project_root / root)
    except Exception as exc:
        raise FrozenViolationError(f"invalid new task tree {root}: {exc}") from None
    _require(
        task.manifest.split is Split.FROZEN_EVAL,
        FrozenViolationError,
        f"new task tree {root} is not split frozen_eval",
    )
    intro = _intro_commit(project_root, rel)
    _require(
        intro is not None and intro != tag_commit
        and _git(project_root, "merge-base", "--is-ancestor", tag_commit, intro).returncode == 0,
        FrozenViolationError,
        f"new task tree {root} was not introduced strictly after the freeze commit",
    )


def _intro_commit(project_root: Path, rel: str) -> str | None:
    log = _git(
        project_root,
        "log",
        "--format=%H",
        "--diff-filter=A",
        "--",
        rel,
    ).stdout.strip().splitlines()
    if not log or not _FULL_SHA.fullmatch(log[0]):
        return None
    return log[0]


def _file_absent_at(project_root: Path, tag_commit: str, rel: str) -> bool:
    return _git(project_root, "cat-file", "-e", f"{tag_commit}:{rel}").returncode != 0


def verify_heldout_selection(
    project_root: Path,
    tasks_dir: Path,
    annotations_root: Path,
    experiment_id: str,
    tag_commit: str,
) -> list[LoadedTask]:
    """Verify every selected frozen_eval task and its inputs (path-safe, B4)."""
    tasks = [task for task in discover_tasks(tasks_dir) if task.manifest.split is Split.FROZEN_EVAL]
    _require(bool(tasks), HeldoutInputError, "no frozen_eval tasks selected")
    for task in tasks:
        task_rel = _repo_rel(project_root, task.task_dir)
        untracked = _git(project_root, "ls-files", "--others", "--exclude-standard", "--", task_rel)
        _require(
            not untracked.stdout.strip(),
            HeldoutInputError,
            f"{task.manifest.id} has untracked input files",
        )
        tracked = [
            line
            for line in _git(project_root, "ls-files", "--", task_rel).stdout.splitlines()
            if line
        ]
        _require(bool(tracked), HeldoutInputError, f"{task.manifest.id} has no tracked inputs")
        for rel in tracked:
            _require(
                _file_absent_at(project_root, tag_commit, rel),
                FrozenViolationError,
                f"{task.manifest.id} input existed at the freeze tag: {rel}",
            )
            _require(
                _git(project_root, "diff", "--quiet", "HEAD", "--", rel).returncode == 0,
                HeldoutInputError,
                f"{task.manifest.id} input modified vs HEAD: {rel}",
            )
        intro = _intro_commit(project_root, f"{task_rel}/task.yaml")
        _require(
            intro is not None
            and intro != tag_commit
            and _git(
                project_root, "merge-base", "--is-ancestor", tag_commit, intro
            ).returncode
            == 0,
            FrozenViolationError,
            f"{task.manifest.id} was not introduced strictly after the freeze commit",
        )
        for patch in discover_patches(task.task_dir, PatchSplit.FROZEN_EVAL):
            ann_path = (
                annotations_root
                / experiment_id
                / task.manifest.id
                / f"{patch.manifest.id}.yaml"
            )
            ann_rel = _repo_rel(project_root, ann_path)
            _require(
                _file_absent_at(project_root, tag_commit, ann_rel),
                FrozenViolationError,
                f"annotation for {task.manifest.id}/{patch.manifest.id} existed at freeze",
            )
            _require(
                _git(project_root, "diff", "--quiet", "HEAD", "--", ann_rel).returncode == 0,
                HeldoutInputError,
                f"annotation for {task.manifest.id}/{patch.manifest.id} modified vs HEAD",
            )
            tracked_annotation = _git(
                project_root, "ls-files", "--error-unmatch", "--", ann_rel
            )
            _require(
                tracked_annotation.returncode == 0
                and ann_rel in tracked_annotation.stdout.splitlines(),
                HeldoutInputError,
                f"annotation for {task.manifest.id}/{patch.manifest.id} is untracked",
            )
            try:
                require_confirmed_annotation(
                    annotations_root, experiment_id, patch
                )
            except (MissingAnnotationError, AnnotationMismatchError) as exc:
                raise HeldoutInputError(str(exc)) from None
    return tasks


def run_heldout(
    *,
    project_root: Path,
    tasks_dir: Path,
    raw_results_root: Path,
    annotations_root: Path = ANNOTATIONS_ROOT,
    experiment_id: str,
    graders: tuple[str, ...],
    require_tag: str,
    runner: Runner,
    refuse_existing: bool = True,
    write_plan: bool = True,
) -> HeldoutSummary:
    """Execute the frozen-evaluation matrix (Section 27.15 ``run-heldout``)."""
    if set(graders) != set(_HELDOUT_GRADERS) or len(set(graders)) != len(graders):
        raise HeldoutInputError(f"graders must be exactly {list(_HELDOUT_GRADERS)}")
    tasks_rel = _repo_rel(project_root, tasks_dir)
    untracked_task_inputs = _git(
        project_root, "ls-files", "--others", "--exclude-standard", "--", tasks_rel
    )
    if untracked_task_inputs.stdout.strip():
        raise HeldoutInputError("selected held-out task inputs include untracked files")
    verify_frozen_lock(project_root, require_tag)
    tag_commit = _git(project_root, "rev-parse", f"{require_tag}^{{commit}}").stdout.strip()

    raw_results_root, annotations_root = resolve_roots(
        project_root, raw_results_root, annotations_root
    )
    recorder = ExperimentRecorder(raw_results_root, experiment_id)
    if refuse_existing and recorder.experiment_dir.exists():
        raise HeldoutInputError(f"experiment already exists: {recorder.experiment_dir}")

    tasks = verify_heldout_selection(
        project_root, tasks_dir, annotations_root, experiment_id, tag_commit
    )

    plan: list[dict[str, object]] = []
    for task in tasks:
        for patch in discover_patches(task.task_dir, PatchSplit.FROZEN_EVAL):
            for grader in graders:
                plan.append(plan_cell(grader, task, patch, phase="heldout"))
    if write_plan:
        recorder.write_metadata(
            {
                "schema_version": "1.0",
                "experiment_id": experiment_id,
                "timestamp_utc": utc_now(),
                "git": git_info(project_root).model_dump(mode="json"),
                "plan": {"controlled": plan},
            }
        )

    patch_count = 0
    completed_patches: list[tuple[str, str, str]] = []
    for task in tasks:
        image = resolve_task_image(task)
        runtime = prepare_task(task)
        patches = discover_patches(task.task_dir, PatchSplit.FROZEN_EVAL)
        patch_count += len(patches)
        for patch in patches:
            pre_grade_by_grader: dict[str, tuple[str, str]] = {}
            for grader_name in graders:
                manager = WorkspaceManager(task)
                workspace = manager.materialize()
                try:
                    apply_result = manager.apply_patch_to(workspace, patch)
                    if not apply_result.ok:
                        raise HeldoutInputError(
                            f"{task.manifest.id}/{patch.manifest.id}: patch does not apply"
                        )
                    pristine = workspace.pristine_snapshot
                    pre_grade = workspace.snapshot()
                    evaluator_result = evaluate_grader(
                        grader_name,
                        runtime,
                        workspace,
                        pre_grade,
                        runner=runner,
                        image=image,
                    )
                    build_patch_record(
                        runtime,
                        patch,
                        grader_name,
                        workspace,
                        pristine,
                        pre_grade,
                        evaluator_result.outcome,
                        evaluator_result.process_result,
                        recorder,
                        project_root,
                        image,
                        phase="heldout",
                        grader_frozen_commit=tag_commit,
                    )
                    pre_grade_by_grader[grader_name] = (
                        pristine.sha256,
                        pre_grade.sha256,
                    )
                finally:
                    manager.finalize_and_destroy(workspace)
            if len({pristine_hash for pristine_hash, _ in pre_grade_by_grader.values()}) != 1:
                raise FrozenViolationError(
                    f"cross-grader pristine hash mismatch for "
                    f"{task.manifest.id}/{patch.manifest.id}"
                )
            if len({pre_hash for _, pre_hash in pre_grade_by_grader.values()}) != 1:
                raise FrozenViolationError(
                    f"cross-grader pre-grade hash mismatch for "
                    f"{task.manifest.id}/{patch.manifest.id}"
                )
            completed_patches.append(
                (task.manifest.split.value, task.manifest.id, patch.manifest.id)
            )

    # Bind only after the entire matrix has been written. Binding earlier dirties
    # tracked annotations and contaminates provenance for later raw records.
    for split, task_id, patch_id in completed_patches:
        bind_patch_raw_hashes(
                raw_results_root,
                annotations_root,
                experiment_id,
                graders,
                split,
                task_id,
                patch_id,
        )

    return HeldoutSummary(
        experiment_id=experiment_id,
        record_count=len(plan),
        task_ids=tuple(task.manifest.id for task in tasks),
        patch_count=patch_count,
    )
