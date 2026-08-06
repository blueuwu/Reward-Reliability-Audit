"""Task/patch discovery and the redacted informational manifest (Sections 27.7/27.10)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grader_audit.core.hashing import sha256_bytes
from grader_audit.core.models import (
    PatchManifest,
    PatchSplit,
    TaskManifest,
    load_patch_manifest_yaml,
    load_task_manifest_yaml,
)

TASK_MANIFEST_NAME = "task.yaml"
PATCH_MANIFEST_NAME = "patch.yaml"
PATCH_DIFF_NAME = "change.patch"


@dataclass(frozen=True)
class LoadedTask:
    task_dir: Path
    manifest: TaskManifest
    manifest_sha256: str
    raw_yaml: bytes


@dataclass(frozen=True)
class LoadedPatch:
    patch_dir: Path
    manifest: PatchManifest
    metadata_sha256: str
    diff_sha256: str
    diff_bytes: bytes


def load_task(task_dir: Path) -> LoadedTask:
    """Load a task manifest from *task_dir*, resolving the directory to absolute.

    Task paths are used as Docker bind-mount sources, which require absolute
    host paths regardless of the CLI's working directory.
    """
    resolved = task_dir.resolve()
    path = resolved / TASK_MANIFEST_NAME
    raw = path.read_bytes()
    manifest = load_task_manifest_yaml(raw.decode("utf-8"))
    return LoadedTask(
        task_dir=resolved, manifest=manifest, manifest_sha256=sha256_bytes(raw), raw_yaml=raw
    )


def discover_tasks(tasks_dir: Path) -> list[LoadedTask]:
    """Discover every task directory containing a ``task.yaml`` manifest."""
    if not tasks_dir.is_dir():
        raise FileNotFoundError(f"tasks directory does not exist: {tasks_dir}")
    tasks: list[LoadedTask] = []
    for child in sorted(tasks_dir.iterdir()):
        if child.is_dir() and (child / TASK_MANIFEST_NAME).is_file():
            tasks.append(load_task(child))
    return tasks


def load_patch(patch_dir: Path) -> LoadedPatch:
    meta_raw = (patch_dir / PATCH_MANIFEST_NAME).read_bytes()
    diff_raw = (patch_dir / PATCH_DIFF_NAME).read_bytes()
    manifest = load_patch_manifest_yaml(meta_raw.decode("utf-8"))
    return LoadedPatch(
        patch_dir=patch_dir,
        manifest=manifest,
        metadata_sha256=sha256_bytes(meta_raw),
        diff_sha256=sha256_bytes(diff_raw),
        diff_bytes=diff_raw,
    )


_PATCH_ROOT_NAMES = {
    PatchSplit.DEVELOPMENT: ("valid", "invalid_dev"),
    PatchSplit.FROZEN_EVAL: ("valid", "invalid_heldout"),
    PatchSplit.ADAPTIVE: ("valid",),
}


def discover_patches(task_dir: Path, split: PatchSplit) -> list[LoadedPatch]:
    """Discover patches for *split* under the canonical patch directories."""
    root_names = _PATCH_ROOT_NAMES[split]
    patches: list[LoadedPatch] = []
    for root_name in root_names:
        root = task_dir / "patches" / root_name
        if not root.is_dir():
            continue
        for patch_dir in sorted(root.iterdir()):
            if patch_dir.is_dir() and (patch_dir / PATCH_MANIFEST_NAME).is_file():
                patches.append(load_patch(patch_dir))
    patches.sort(key=lambda patch: patch.manifest.id)
    return patches


def redact_manifest(manifest: TaskManifest) -> dict[str, object]:
    """Build the fixed redacted informational task manifest (Section 27.10 step 4).

    The redacted copy omits authoritative and oracle paths, expected node IDs,
    validation expectations, and grader-only metadata. It never exposes host
    paths.
    """
    return {
        "schema_version": manifest.schema_version,
        "id": manifest.id,
        "title": manifest.title,
        "split": manifest.split.value,
        "source": {
            "repo_url": str(manifest.source.repo_url),
            "license_spdx": manifest.source.license_spdx,
            "license_file": manifest.source.license_file,
            "fix_commit": manifest.source.fix_commit,
            "baseline_commit": manifest.source.baseline_commit,
        },
        "runtime": {
            "python": manifest.runtime.python,
            "build_timeout_seconds": manifest.runtime.build_timeout_seconds,
            "command_timeout_seconds": manifest.runtime.command_timeout_seconds,
            "memory_mb": manifest.runtime.memory_mb,
            "pids_limit": manifest.runtime.pids_limit,
        },
        "workspace": {
            "container_root": manifest.workspace.container_root,
            "source_roots": manifest.workspace.source_roots,
            "visible_tests_target": manifest.workspace.visible_tests_target,
            "expose_redacted_manifest": manifest.workspace.expose_redacted_manifest,
            "editable_globs": manifest.workspace.editable_globs,
            "immutable_workspace_globs": manifest.workspace.immutable_workspace_globs,
            "expected_change_globs": manifest.workspace.expected_change_globs,
            "generated_artifact_globs": manifest.workspace.generated_artifact_globs,
        },
        "grading": {
            "naive": {
                "argv": manifest.grading.naive.argv,
                "cwd": manifest.grading.naive.cwd,
                "timeout_seconds": manifest.grading.naive.timeout_seconds,
            }
        },
    }
