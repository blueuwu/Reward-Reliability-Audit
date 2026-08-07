"""Shared fixtures for the grader_v2 suite.

Strict Docker mode (Gate D): when ``GRADER_AUDIT_REQUIRE_DOCKER=1``, Docker
unavailability or a non-Linux engine fails session setup with a precise
message and Docker-marked tests must run rather than skip. Without the
variable the Docker tests skip, matching the v1 suite's local-contributor
behavior.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from grader_audit.images import ensure_fixture_image

_REQUIRE_DOCKER = os.environ.get("GRADER_AUDIT_REQUIRE_DOCKER", "0") in ("1", "true", "yes")

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures"


def _docker_info() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}|{{.OSType}}"],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"docker unavailable: {exc}"
    if result.returncode != 0:
        return False, f"docker info failed: {result.stderr.strip()}"
    parts = [part.strip() for part in result.stdout.strip().split("|")]
    if len(parts) != 2 or not parts[0]:
        return False, "docker info returned no server version"
    if parts[1] and parts[1] != "linux":
        return False, (
            f"non-Linux Docker engine detected (OSType={parts[1]}); "
            "grader images are linux/amd64 only"
        )
    return True, parts[0]


_DOCKER_OK, _DOCKER_REASON = _docker_info()

if _REQUIRE_DOCKER and not _DOCKER_OK:
    pytest.exit(
        f"GRADER_AUDIT_REQUIRE_DOCKER=1 but Docker is not usable: {_DOCKER_REASON}",
        returncode=3,
    )

requires_docker = pytest.mark.skipif(
    not _DOCKER_OK, reason=f"Docker is not available: {_DOCKER_REASON}"
)


@pytest.fixture(scope="session")
def fixture_image() -> str:
    """The immutable Docker image used for every scored fixture run."""
    assert _DOCKER_OK, "fixture_image requires Docker"
    return ensure_fixture_image()
