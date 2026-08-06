"""Safe unified-diff validation and application (Section 27.8).

Patch bytes must be UTF-8 unified diffs with LF line endings. Paths are
validated *before* Git is invoked: absolute paths, ``..``, paths outside the
workspace, binary patches, submodule changes, symlink creation, file-mode
changes, and device/special-file changes are rejected. Application uses
``git apply --check --whitespace=nowarn`` followed by ``git apply
--whitespace=nowarn`` with the workspace as root.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from grader_audit.core.path_rules import classify_repository_relative

_HEADER_PATH = re.compile(r"^(?:---|\+\+\+)\s+(.+)$")
_DIFF_GIT = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
_RENAME_FROM = re.compile(r"^rename from (.+)$")
_RENAME_TO = re.compile(r"^rename to (.+)$")
_NEW_FILE_MODE = re.compile(r"^new file mode (\S+)$")
_MODE_CHANGE = re.compile(r"^(?:old|new) mode \S+$")
_DEV_NULL = "/dev/null"

_FORBIDDEN_PREFIXES = ("a/", "b/")
_FORBIDDEN_MODES = ("100755", "120000", "160000")


@dataclass(frozen=True)
class PatchValidationError:
    reason: str


@dataclass
class PatchApplyResult:
    applied_paths: list[str]
    ok: bool = True
    error: str | None = None


def _strip_prefix(path: str) -> str:
    for prefix in _FORBIDDEN_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def validate_patch_bytes(patch: bytes) -> str | None:
    """Return a reason string when *patch* is invalid, else ``None``.

    Paths are extracted from the diff headers and rejected before Git is
    invoked. This is deliberately conservative: any header form we cannot parse
    unambiguously is treated as invalid input.
    """
    if b"\x00" in patch:
        return "patch contains a NUL byte"
    text = patch.decode("utf-8", errors="strict")
    if "\r" in text:
        return "patch uses CRLF line endings; LF required"
    if "GIT binary patch" in text:
        return "binary patch is not allowed"
    if "Binary files" in text:
        return "binary patch is not allowed"
    if "Submodule " in text or "Subproject " in text:
        return "submodule changes are not allowed"

    paths: list[str] = []
    lines = text.splitlines()
    for line in lines:
        match = _DIFF_GIT.match(line)
        if match:
            paths.extend([match.group(1), match.group(2)])
            continue
        if _MODE_CHANGE.match(line):
            return "file-mode changes are not allowed"
        if _NEW_FILE_MODE.match(line):
            mode = _NEW_FILE_MODE.match(line)
            assert mode is not None
            if mode.group(1) in _FORBIDDEN_MODES:
                return f"new file mode {mode.group(1)} is not allowed"
            continue
        if _RENAME_FROM.match(line):
            rename = _RENAME_FROM.match(line)
            assert rename is not None
            paths.append(rename.group(1))
            continue
        if _RENAME_TO.match(line):
            rename = _RENAME_TO.match(line)
            assert rename is not None
            paths.append(rename.group(1))
            continue
        match = _HEADER_PATH.match(line)
        if match:
            candidate = match.group(1)
            if candidate == _DEV_NULL:
                continue
            if "\\" in candidate or '"' in candidate:
                return f"unsupported quoted path header {candidate!r}"
            paths.append(_strip_prefix(candidate))
            continue

    for path in paths:
        if path == _DEV_NULL:
            continue
        reason = classify_repository_relative(path)
        if reason is not None:
            return f"invalid path {path!r} in patch: {reason}"
    return None


def apply_patch(workspace: Path, patch: bytes) -> PatchApplyResult:
    """Validate *patch* and apply it to *workspace* with Git.

    *workspace* is used as the Git root; it need not be a repository. The
    returned result marks a corpus patch that does not apply cleanly as a
    failed application (``invalid_input`` upstream), not a grader rejection.
    """
    error = validate_patch_bytes(patch)
    if error is not None:
        return PatchApplyResult(applied_paths=[], ok=False, error=error)

    patch_path = workspace / ".grader-apply.patch"
    try:
        patch_path.write_bytes(patch)
        check = _git_apply(workspace, patch_path, check=True)
        if not check.ok:
            return PatchApplyResult(
                applied_paths=[], ok=False, error=check.error or "git apply --check failed"
            )
        apply = _git_apply(workspace, patch_path, check=False)
        if not apply.ok:
            return PatchApplyResult(
                applied_paths=[], ok=False, error=apply.error or "git apply failed"
            )
    finally:
        if patch_path.exists():
            patch_path.unlink()

    applied_paths = _extract_applied_paths(patch)
    return PatchApplyResult(applied_paths=applied_paths, ok=True)


@dataclass(frozen=True)
class _GitResult:
    ok: bool
    error: str | None


def _git_apply(workspace: Path, patch_path: Path, *, check: bool) -> _GitResult:
    argv = ["git", "-C", str(workspace), "apply", "--whitespace=nowarn"]
    if check:
        argv.append("--check")
    argv.append(str(patch_path))
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60.0)
    except FileNotFoundError:
        return _GitResult(False, "git executable not found")
    except subprocess.TimeoutExpired:
        return _GitResult(False, "git apply timed out")
    except OSError as exc:
        return _GitResult(False, f"git apply failed with OSError: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return _GitResult(False, detail or f"git apply exited {result.returncode}")
    return _GitResult(True, None)


def _extract_applied_paths(patch: bytes) -> list[str]:
    text = patch.decode("utf-8", errors="strict")
    paths: list[str] = []
    for line in text.splitlines():
        match = _HEADER_PATH.match(line)
        if match and match.group(1) not in (_DEV_NULL,):
            candidate = match.group(1)
            if '"' in candidate or "\\" in candidate:
                continue
            paths.append(_strip_prefix(candidate))
    seen: set[str] = set()
    ordered: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered
