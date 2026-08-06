"""Safe unified-diff validation and application (Sections 27.8, 27.19)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grader_audit.core.patches import apply_patch, validate_patch_bytes

VALID_PATCH = b"--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"


def _tree(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def test_valid_patch_applies(tmp_path: Path) -> None:
    _tree(tmp_path, {"a.py": "def f():\n    return 1\n"})
    result = apply_patch(tmp_path, VALID_PATCH)
    assert result.ok
    assert result.applied_paths == ["a.py"]
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "def f():\n    return 2\n"


def test_patch_that_does_not_apply_is_invalid_input(tmp_path: Path) -> None:
    _tree(tmp_path, {"a.py": "def f():\n    return 1\n"})
    bad = VALID_PATCH.replace(b"def f():", b"def g():")
    result = apply_patch(tmp_path, bad)
    assert not result.ok
    assert result.error is not None


@pytest.mark.parametrize(
    "patch_bytes",
    [
        b"--- a/../escape.py\n+++ b/../escape.py\n@@ -0,0 +1 @@\n+x\n",
        b"--- a/C:/abs.py\n+++ b/C:/abs.py\n@@ -0,0 +1 @@\n+x\n",
        b"--- a/a\\b.py\n+++ b/a\\b.py\n@@ -0,0 +1 @@\n+x\n",
        b"--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+x\n\x00",
        b"--- a/a.py\r\n+++ b/a.py\r\n@@ -0,0 +1 @@\r\n+x\r\n",
        b"GIT binary patch\nliteral 3\nKc$Nv;\n",
        b"diff --git a/sub b/sub\nnew file mode 160000\n",
        b"--- a/a.py\n+++ b/a.py\nold mode 100644\nnew mode 100755\n",
        b"--- /dev/null\n+++ b/link.py\n@@ -0,0 +1 @@\n+target\nnew file mode 120000\n",
    ],
)
def test_unsafe_patches_rejected(patch_bytes: bytes) -> None:
    assert validate_patch_bytes(patch_bytes) is not None


def test_new_regular_file_allowed(tmp_path: Path) -> None:
    _tree(tmp_path, {})
    patch = b"--- /dev/null\n+++ b/newfile.py\n@@ -0,0 +1 @@\n+x = 1\n"
    result = apply_patch(tmp_path, patch)
    assert result.ok
    assert result.applied_paths == ["newfile.py"]


def test_deletion_allowed(tmp_path: Path) -> None:
    _tree(tmp_path, {"a.py": "x = 1\n"})
    patch = b"--- a/a.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    result = apply_patch(tmp_path, patch)
    assert result.ok
    assert not (tmp_path / "a.py").exists()


def test_rename_allowed(tmp_path: Path) -> None:
    _tree(tmp_path, {"old.py": "x = 1\n"})
    patch = (
        b"diff --git a/old.py b/new.py\n"
        b"similarity index 100%\n"
        b"rename from old.py\n"
        b"rename to new.py\n"
    )
    result = apply_patch(tmp_path, patch)
    assert result.ok
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (tmp_path / "old.py").exists()


def test_multifile_patch_applies(tmp_path: Path) -> None:
    _tree(tmp_path, {"a.py": "x = 1\n"})
    patch = (
        b"--- a/a.py\n"
        b"+++ b/a.py\n"
        b"@@ -1 +1 @@\n"
        b"-x = 1\n"
        b"+x = 2\n"
        b"--- /dev/null\n"
        b"+++ b/b.py\n"
        b"@@ -0,0 +1 @@\n"
        b"+y = 3\n"
    )
    result = apply_patch(tmp_path, patch)
    assert result.ok
    assert sorted(result.applied_paths) == ["a.py", "b.py"]
