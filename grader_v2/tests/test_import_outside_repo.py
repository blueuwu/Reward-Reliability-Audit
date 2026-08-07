"""Package import and CLI behavior from outside the repository root (Gate E)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def outside_cwd(tmp_path: Path) -> Path:
    """A directory that is not the repository root and not under it."""
    return tmp_path / "outside"


def _run(cwd: Path, code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


def test_import_works_outside_repository(outside_cwd: Path) -> None:
    outside_cwd.mkdir(exist_ok=True)
    result = _run(
        outside_cwd,
        "import grader_v2.cli; import grader_v2.freeze; "
        "import grader_v2.grading.evaluator; print(grader_v2.cli.__name__)",
    )
    assert result.returncode == 0, result.stderr
    assert "grader_v2.cli" in result.stdout


def test_cli_parser_builds_outside_repository(outside_cwd: Path) -> None:
    outside_cwd.mkdir(exist_ok=True)
    result = _run(
        outside_cwd,
        "from grader_v2.cli import build_parser; "
        "parser = build_parser(); "
        "commands = []\n"
        "for action in parser._actions:\n"
        "    if action.dest == 'command':\n"
        "        commands = sorted(action.choices or ())\n"
        "print(','.join(commands))",
    )
    assert result.returncode == 0, result.stderr
    for command in ("eval-v2", "freeze-v2", "verify-v1-lock", "demo", "publication", "replay"):
        assert command in result.stdout


def test_no_cwd_dependency_for_grader_v2_only_imports(outside_cwd: Path) -> None:
    """Core grader_v2 modules must not touch Path.cwd() at import time."""
    outside_cwd.mkdir(exist_ok=True)
    result = _run(
        outside_cwd,
        "import grader_v2.grading.evidence, grader_v2.grading.generators, "
        "grader_v2.grading.records, grader_v2.jsonutil, grader_v2.publication_cases; "
        "print('import-ok')",
    )
    assert result.returncode == 0, result.stderr
    assert "import-ok" in result.stdout
