"""Path safety rules for the normative task manifest (Section 27.7).

Repository-relative paths use ``/``, are relative, and contain no ``..``,
drive prefix, URI, NUL, or leading ``/``. Container paths are absolute POSIX
paths under ``/workspace`` or ``/opt/grader``. Glob matching uses GitWildMatch
semantics through ``pathspec`` with case-sensitive matching on every host.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable

from pathspec.gitignore import GitIgnoreSpec

_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_NUL = "\x00"

CONTAINER_ROOTS: tuple[str, ...] = ("/workspace", "/opt/grader")


def classify_repository_relative(path: str) -> str | None:
    """Return a reason string when *path* violates Section 27.7, else ``None``."""
    if not path:
        return "empty path"
    if _NUL in path:
        return "contains a NUL byte"
    if _DRIVE_PREFIX.match(path):
        return "contains a drive prefix"
    if _URI_SCHEME.match(path):
        return "is a URI rather than a repository-relative path"
    if path.startswith("/"):
        return "is absolute"
    if "\\" in path:
        return "uses backslash separators"
    if any(part == ".." for part in path.split("/")):
        return "contains a parent-directory segment"
    if posixpath.normpath(path) in ("", "."):
        return "does not name a file"
    return None


def is_safe_repository_relative(path: str) -> bool:
    """True when *path* is a safe repository-relative POSIX path."""
    return classify_repository_relative(path) is None


def classify_container_path(path: str) -> str | None:
    """Return a reason string when *path* violates the container-path rule, else ``None``."""
    if not path:
        return "empty path"
    if _NUL in path:
        return "contains a NUL byte"
    if not posixpath.isabs(path):
        return "is not absolute"
    if any(part == ".." for part in path.split("/")):
        return "contains a parent-directory segment"
    normalized = posixpath.normpath(path)
    if not any(normalized == root or normalized.startswith(root + "/") for root in CONTAINER_ROOTS):
        return "is not under /workspace or /opt/grader"
    return None


def is_safe_container_path(path: str) -> bool:
    """True when *path* is an absolute POSIX path under a declared container root."""
    return classify_container_path(path) is None


def match_workspace_globs(path: str, patterns: Iterable[str]) -> bool:
    """Match a POSIX-normalized path against GitWildMatch patterns, case-sensitively.

    ``GitIgnoreSpec`` is the pathspec 1.1.x implementation of GitWildMatch
    semantics (the ``gitwildmatch`` factory is its deprecated alias). Matching
    compiles plain regexes without an ignore-case flag on every host, so it is
    case-sensitive on Windows and Linux alike.
    """
    normalized = posixpath.normpath(path)
    spec = GitIgnoreSpec.from_lines(list(patterns))
    return spec.match_file(normalized)
