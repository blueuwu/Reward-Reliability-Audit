"""Schema-only tests for path safety rules (Section 27.7 field rules)."""

from __future__ import annotations

import pytest

from grader_audit.core.path_rules import (
    classify_container_path,
    classify_repository_relative,
    is_safe_container_path,
    is_safe_repository_relative,
    match_workspace_globs,
)


@pytest.mark.parametrize(
    "path",
    [
        "src/project/path.py",
        "tests/test_path.py",
        "a/b/c/d.py",
        "visible_tests",
        "task.yaml",
        ".grader/run.py",
        "requirements.lock",
        "baseline/LICENSE",
        "src/project/deep/nested/file.py",
    ],
)
def test_safe_repository_relative_paths(path: str) -> None:
    assert is_safe_repository_relative(path)
    assert classify_repository_relative(path) is None


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "..",
        "../escape.py",
        "src/../../escape.py",
        "a/../b.py",
        "/absolute.py",
        "C:/windows.py",
        "c:\\windows.py",
        "a\\b.py",
        "https://example.com/x.py",
        "http://example.com/x.py",
        "file:///etc/passwd",
        "src\x00nul.py",
    ],
)
def test_unsafe_repository_relative_paths(path: str) -> None:
    assert not is_safe_repository_relative(path)
    assert classify_repository_relative(path) is not None


@pytest.mark.parametrize(
    "path",
    [
        "/workspace",
        "/workspace/src/project/path.py",
        "/workspace/tests/test_a.py",
        "/opt/grader/pytest.ini",
        "/opt/grader/run_pytest.py",
    ],
)
def test_safe_container_paths(path: str) -> None:
    assert is_safe_container_path(path)
    assert classify_container_path(path) is None


@pytest.mark.parametrize(
    "path",
    [
        "",
        "workspace/src/x.py",
        "src/x.py",
        "/etc/passwd",
        "/workspace/../etc/passwd",
        "/opt/grader/../../etc/passwd",
        "/workspace_other/x.py",
        "/workspace\x00nul",
    ],
)
def test_unsafe_container_paths(path: str) -> None:
    assert not is_safe_container_path(path)
    assert classify_container_path(path) is not None


def test_glob_matching_is_case_sensitive() -> None:
    assert match_workspace_globs("src/project/path.py", ["src/**"])
    assert not match_workspace_globs("SRC/project/path.py", ["src/**"])
    assert not match_workspace_globs("src/project/path.py", ["SRC/**"])
    assert not match_workspace_globs("Tests/test_a.py", ["tests/**"])


def test_glob_gitwildmatch_semantics() -> None:
    patterns = [".pytest_cache/**", "**/__pycache__/**", "**/*.pyc"]
    assert match_workspace_globs(".pytest_cache/v/cache/nodeids", patterns)
    assert match_workspace_globs("src/__pycache__/path.cpython-312.pyc", patterns)
    assert match_workspace_globs("src/project/path.pyc", patterns)
    assert not match_workspace_globs("src/project/path.py", patterns)
    assert not match_workspace_globs("tests/conftest.py", patterns)


def test_glob_editable_and_expected_scope() -> None:
    editable = ["src/**", "tests/**"]
    expected = ["src/project/path.py"]
    assert match_workspace_globs("src/project/path.py", editable)
    assert match_workspace_globs("tests/test_path.py", editable)
    assert match_workspace_globs("src/project/path.py", expected)
    assert not match_workspace_globs("src/project/utils.py", expected)
