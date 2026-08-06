"""Shared pytest fixtures for the Gate 1 suite.

The immutable Docker task image is built once per session and reused. The whole
integration suite is skipped when Docker is unavailable or cannot run Linux
containers, so the suite stays green on hosts without a daemon.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grader_audit.images import ensure_fixture_image

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        return False


DOCKER_AVAILABLE = _docker_available()

requires_docker = pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker is not available")


@pytest.fixture(scope="session")
def fixture_image() -> str:
    """The immutable Docker image used for every scored fixture run."""
    assert DOCKER_AVAILABLE, "fixture_image requires Docker"
    return ensure_fixture_image()
