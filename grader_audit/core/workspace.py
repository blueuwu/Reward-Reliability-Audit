"""Fresh workspace materialization (Section 27.10).

Every ``(task, patch, grader, repeat)`` uses a distinct temporary workspace.
The manager copies the vendored baseline without following symlinks, stages the
visible tests and prompt, writes the redacted informational manifest, rejects
unsafe tree members, and captures deterministic snapshots. Workspaces are never
reset or reused.
"""

from __future__ import annotations

import os
import shutil
import stat as stat_module
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from grader_audit.core.manifests import LoadedPatch, LoadedTask, redact_manifest
from grader_audit.core.models import TaskManifest
from grader_audit.core.patches import PatchApplyResult, apply_patch
from grader_audit.core.snapshots import WorkspaceSnapshot, capture_snapshot


@dataclass
class Workspace:
    root: Path
    task_id: str
    materialization_id: str
    pristine_snapshot: WorkspaceSnapshot

    def snapshot(self) -> WorkspaceSnapshot:
        return capture_snapshot(self.root)

    def destroy(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)


@dataclass
class WorkspaceManager:
    task: LoadedTask
    base_temp_dir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()))
    _materialized: set[Path] = field(default_factory=lambda: set[Path]())

    @property
    def manifest(self) -> TaskManifest:
        return self.task.manifest

    def materialize(self) -> Workspace:
        """Create a fresh, deterministic workspace and capture the pristine snapshot."""
        materialization_id = uuid.uuid4().hex
        root = Path(
            tempfile.mkdtemp(
                prefix=f"ga-{self.manifest.id}-{materialization_id[:8]}-", dir=self.base_temp_dir
            )
        )
        self._materialized.add(root)
        try:
            self._copy_baseline(root)
            self._stage_visible_tests(root)
            self._stage_prompt(root)
            self._stage_redacted_manifest(root)
            _validate_workspace_tree(root)
            pristine = capture_snapshot(root)
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            self._materialized.discard(root)
            raise
        return Workspace(
            root=root,
            task_id=self.manifest.id,
            materialization_id=materialization_id,
            pristine_snapshot=pristine,
        )

    def _copy_baseline(self, root: Path) -> None:
        baseline = self.task.task_dir / self.manifest.workspace.source_dir
        _copy_tree_without_symlinks(baseline, root)

    def _stage_visible_tests(self, root: Path) -> None:
        visible = self.task.task_dir / self.manifest.workspace.visible_tests_dir
        target = root / self.manifest.workspace.visible_tests_target
        if visible.is_dir():
            _copy_tree_without_symlinks(visible, target)

    def _stage_prompt(self, root: Path) -> None:
        prompt = self.task.task_dir / "prompt.md"
        if prompt.is_file():
            shutil.copyfile(prompt, root / "prompt.md")

    def _stage_redacted_manifest(self, root: Path) -> None:
        if self.manifest.workspace.expose_redacted_manifest:
            redacted = redact_manifest(self.manifest)
            text = yaml.safe_dump(redacted, sort_keys=True, allow_unicode=True)
            (root / "task.yaml").write_text(text, encoding="utf-8")

    def stage_fresh_root(self, root: Path) -> Workspace:
        """Stage a fresh baseline into an existing directory *root*.

        Used by the HUD adapter (Section 27.13): the HUD workspace sandbox is a
        fixed directory per rollout, so this reuses the exact same staging logic
        as :meth:`materialize` (baseline, visible tests, prompt, redacted
        manifest) after clearing prior contents. The pristine snapshot is
        captured from the staged tree. Callers own the directory lifecycle;
        nothing is registered in ``_materialized``.
        """
        root.mkdir(parents=True, exist_ok=True)
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        self._copy_baseline(root)
        self._stage_visible_tests(root)
        self._stage_prompt(root)
        self._stage_redacted_manifest(root)
        _validate_workspace_tree(root)
        pristine = capture_snapshot(root)
        return Workspace(
            root=root,
            task_id=self.manifest.id,
            materialization_id=uuid.uuid4().hex,
            pristine_snapshot=pristine,
        )

    def apply_patch_to(self, workspace: Workspace, patch: LoadedPatch) -> PatchApplyResult:
        """Apply exactly one patch to a materialized workspace."""
        return apply_patch(workspace.root, patch.diff_bytes)

    def finalize_and_destroy(self, workspace: Workspace) -> None:
        """Delete a temporary workspace; never reset or reuse it."""
        workspace.destroy()
        self._materialized.discard(workspace.root)


def _copy_tree_without_symlinks(source: Path, target: Path) -> None:
    """Copy *source* to *target* without following symlinks.

    Symlinks anywhere in the source are treated as a fatal error because the
    materialized workspace must never contain links that can escape its root.
    """
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames.sort()
        for name in sorted(dirnames):
            full = Path(dirpath) / name
            if full.is_symlink():
                raise ValueError(
                    f"symlink in vendored baseline: {full.relative_to(source).as_posix()}"
                )
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if full.is_symlink():
                raise ValueError(
                    f"symlink in vendored baseline: {full.relative_to(source).as_posix()}"
                )
            rel = full.relative_to(source)
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(full, dest)


def _validate_workspace_tree(root: Path) -> None:
    """Reject symlinks, escaping hardlinks, sockets, devices, FIFOs, and traversal."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in sorted(dirnames):
            full = Path(dirpath) / name
            info = full.lstat()
            if not stat_module.S_ISDIR(info.st_mode):
                raise ValueError(
                    f"unsupported directory entry: {full.relative_to(root).as_posix()}"
                )
        for name in sorted(filenames):
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            info = full.lstat()
            if stat_module.S_ISLNK(info.st_mode):
                raise ValueError(f"symlink in materialized workspace: {rel}")
            if not (stat_module.S_ISREG(info.st_mode) or stat_module.S_ISDIR(info.st_mode)):
                raise ValueError(f"special file in materialized workspace: {rel}")
    # Path traversal guard: every file must resolve inside the root.
    for entry in sorted(root.rglob("*")):
        try:
            entry.relative_to(root)
        except ValueError:
            raise ValueError(f"path traversal in materialized workspace: {entry}") from None
