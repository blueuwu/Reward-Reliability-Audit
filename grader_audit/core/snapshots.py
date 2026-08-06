"""Deterministic workspace snapshots and snapshot diffing (Section 27.10).

A snapshot is independent of directory iteration order and mtime. Entries are
sorted bytewise by normalized POSIX path and serialized with sorted object keys
and compact JSON separators before hashing. File contents are hashed in binary
mode. Regular files use the canonical logical mode ``100644`` regardless of the
orchestration host's permission bits.
"""

from __future__ import annotations

import json
import os
import posixpath
import stat as stat_module
from pathlib import Path
from typing import Literal

from grader_audit.core.base import StrictModel
from grader_audit.core.hashing import sha256_file
from grader_audit.core.models import TaskManifest
from grader_audit.core.outcomes import Changes

_FILE_MODE = "100644"
_DIR_MODE = "040000"


class SnapshotEntry(StrictModel):
    path: str
    kind: Literal["file", "dir"]
    mode: str
    size: int
    sha256: str | None = None


class WorkspaceSnapshot(StrictModel):
    entries: list[SnapshotEntry]
    sha256: str

    def _serialized_bytes(self) -> bytes:
        payload = [entry.model_dump(mode="json") for entry in self.entries]
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return text.encode("utf-8")

    def recompute_sha256(self) -> str:
        """Recompute the deterministic snapshot hash from the entries."""
        return _hash_bytes(self._serialized_bytes())


def _hash_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return _hash_bytes(text.encode("utf-8"))


def _entry_from_path(root: Path, path: Path) -> SnapshotEntry | None:
    rel = posixpath.normpath(path.relative_to(root).as_posix())
    info = path.lstat()
    if stat_module.S_ISDIR(info.st_mode):
        return SnapshotEntry(path=rel, kind="dir", mode=_DIR_MODE, size=0, sha256=None)
    if stat_module.S_ISREG(info.st_mode):
        return SnapshotEntry(
            path=rel, kind="file", mode=_FILE_MODE, size=info.st_size, sha256=sha256_file(path)
        )
    raise ValueError(f"workspace entry {rel!r} is not a regular file or directory")


def capture_snapshot(root: Path) -> WorkspaceSnapshot:
    """Capture a deterministic snapshot of the workspace rooted at *root*."""
    entries: list[SnapshotEntry] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(dirnames):
            full = Path(dirpath) / name
            if full.is_symlink():
                raise ValueError(f"symlink in workspace: {full.relative_to(root).as_posix()}")
            entry = _entry_from_path(root, full)
            if entry is not None:
                entries.append(entry)
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if full.is_symlink():
                raise ValueError(f"symlink in workspace: {full.relative_to(root).as_posix()}")
            entry = _entry_from_path(root, full)
            if entry is not None:
                entries.append(entry)
    entries.sort(key=lambda entry: entry.path.encode("utf-8"))
    snapshot = WorkspaceSnapshot(entries=entries, sha256="")
    snapshot.sha256 = snapshot.recompute_sha256()
    return snapshot


class ChangeEntry(StrictModel):
    kind: Literal["created", "modified", "deleted", "renamed", "mode_changed"]
    path: str
    secondary_path: str | None = None


def diff_snapshots(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> list[ChangeEntry]:
    """Return the changed paths between two snapshots, with rename detection."""
    before_map = {entry.path: entry for entry in before.entries}
    after_map = {entry.path: entry for entry in after.entries}

    changes: list[ChangeEntry] = []
    for path in sorted(set(before_map) | set(after_map)):
        prev = before_map.get(path)
        curr = after_map.get(path)
        if prev is None:
            changes.append(ChangeEntry(kind="created", path=path))
        elif curr is None:
            changes.append(ChangeEntry(kind="deleted", path=path))
        elif prev.kind != curr.kind or prev.mode != curr.mode:
            changes.append(ChangeEntry(kind="mode_changed", path=path))
        elif prev.kind == "file" and curr.kind == "file" and prev.sha256 != curr.sha256:
            changes.append(ChangeEntry(kind="modified", path=path))

    created = {entry.path for entry in changes if entry.kind == "created"}
    deleted = {entry.path for entry in changes if entry.kind == "deleted"}
    if created and deleted:
        renamed_pairs: list[tuple[str, str]] = []
        for new_path in sorted(created):
            new_entry = after_map[new_path]
            if new_entry.kind != "file":
                continue
            for old_path in sorted(deleted):
                old_entry = before_map.get(old_path)
                if (
                    old_entry is not None
                    and old_entry.kind == "file"
                    and old_entry.sha256 == new_entry.sha256
                ):
                    renamed_pairs.append((new_path, old_path))
                    break
        for new_path, old_path in renamed_pairs:
            changes = [entry for entry in changes if entry.path not in (new_path, old_path)]
            changes.append(ChangeEntry(kind="renamed", path=new_path, secondary_path=old_path))

    return sorted(changes, key=lambda entry: entry.path.encode("utf-8"))


def _classify_change_paths(
    manifest: TaskManifest,
    changes: list[ChangeEntry],
    *,
    apply_generated_allowlist: bool,
) -> Changes:
    from grader_audit.core.scope import ChangeCategory, classify_path

    result = Changes()
    for entry in changes:
        paths = [entry.path]
        if entry.secondary_path is not None:
            paths.append(entry.secondary_path)
        for path in paths:
            category = classify_path(
                path, manifest, apply_generated_allowlist=apply_generated_allowlist
            )
            if category is ChangeCategory.IMMUTABLE_VIOLATION:
                result.immutable_violations.append(path)
            elif category is ChangeCategory.OUTSIDE_EDITABLE_SCOPE:
                result.outside_editable_scope.append(path)
            elif category is ChangeCategory.OUTSIDE_EXPECTED_SCOPE:
                result.outside_expected_scope.append(path)
            elif category is ChangeCategory.GENERATED_ARTIFACT:
                result.generated_artifacts.append(path)
            if (
                entry.path not in result.modified_paths
                and category is not ChangeCategory.GENERATED_ARTIFACT
            ):
                result.modified_paths.append(entry.path)
    for path_list in (
        result.immutable_violations,
        result.outside_editable_scope,
        result.outside_expected_scope,
        result.generated_artifacts,
    ):
        path_list.sort(key=lambda p: p.encode("utf-8"))
    result.modified_paths.sort(key=lambda p: p.encode("utf-8"))
    return result


def classify_patch_changes(manifest: TaskManifest, changes: list[ChangeEntry]) -> Changes:
    """Classify the pristine-to-pre-grade (submitted patch) diff.

    The generated-artifact allowlist is intentionally NOT applied here: a
    submitted patch cannot claim a file is a cache.
    """
    return _classify_change_paths(manifest, changes, apply_generated_allowlist=False)


def classify_post_grade_changes(manifest: TaskManifest, changes: list[ChangeEntry]) -> Changes:
    """Classify the pre-grade-to-post-grade (grader execution) diff.

    The generated allowlist applies here, but immutable-path changes always
    take precedence.
    """
    return _classify_change_paths(manifest, changes, apply_generated_allowlist=True)
