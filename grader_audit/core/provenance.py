"""Provenance helpers for result records (Section 27.16)."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_NO_COMMIT = "0" * 40


@dataclass(frozen=True)
class GitProvenance:
    data_commit: str
    worktree_dirty: bool


def git_provenance(project_root: Path) -> GitProvenance:
    data_commit = _git_rev_parse(project_root)
    dirty = _git_dirty(project_root)
    return GitProvenance(data_commit=data_commit, worktree_dirty=dirty)


def _git_rev_parse(project_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return _NO_COMMIT
    if result.returncode != 0:
        return _NO_COMMIT
    return result.stdout.strip() or _NO_COMMIT


def _git_dirty(project_root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=15.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return True
    return result.returncode != 0 or bool(result.stdout.strip())


def python_version() -> str:
    return sys.version.split()[0]


def pytest_version() -> str:
    try:
        return importlib.metadata.version("pytest")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def hud_version() -> str:
    try:
        return importlib.metadata.version("hud")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"
