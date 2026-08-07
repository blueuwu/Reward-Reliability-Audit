"""Docker container execution for scored tasks (Sections 27.1 and 27.11).

Every scored container uses ``--rm --network none``, drops all Linux
capabilities, sets ``no-new-privileges``, runs as a non-root user (the host UID
where available, so the host-created bind mounts stay readable and writable),
enforces the manifest memory and PID limits, receives only an explicit
environment allowlist, and enforces a container-level timeout. Never execute a
mutable tag: records reference the immutable digest.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
import uuid
from collections.abc import Sequence
from pathlib import Path

from grader_audit.core.process import CommandSpec, Mount, ProcessResult, truncate_capture

_DOCKER_KILL_TIMEOUT = 15.0
_GRACE_SECONDS = 10.0
_CONTAINER_UID = 1000
_CONTAINER_GID = 1000


def _container_user() -> str:
    """Return the ``--user`` value for a scored container.

    The bind-mount sources are created on the host by ``tempfile.mkdtemp`` (mode
    0700, owned by the invoking user), so the container process can only
    traverse ``/workspace`` and write the evidence directory when it shares that
    UID. A fixed UID silently breaks every scored run on any host whose user is
    not UID 1000 -- notably GitHub-hosted runners, where ``runner`` is UID 1001:
    pytest cannot reach the test directory and no ``report.json`` is produced.

    Falls back to the fixed non-root UID where the host UID is unavailable
    (Windows, whose Docker Desktop mounts do not carry host ownership) or where
    the invoking user is root, so a scored container never runs as root.
    """
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return f"{_CONTAINER_UID}:{_CONTAINER_GID}"
    uid, gid = getuid(), getgid()
    if uid == 0:
        return f"{_CONTAINER_UID}:{_CONTAINER_GID}"
    return f"{uid}:{gid}"

DEFAULT_ENV: dict[str, str] = {
    "PYTHONHASHSEED": "0",
    "PYTHONUTF8": "1",
}


class ContainerStartError(RuntimeError):
    """Raised when the trusted container cannot start (Section 27.12 mapping)."""


def _run_docker(argv: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            argv, capture_output=True, timeout=timeout_seconds, check=False
        )
    except FileNotFoundError:
        raise ContainerStartError("docker executable not found") from None
    except OSError as exc:
        raise ContainerStartError(f"docker invocation failed with OSError: {exc}") from exc


class DockerRunner:
    """Runs commands inside an immutable per-task container."""

    def __init__(self, *, network: str = "none") -> None:
        self._network = network

    def run(
        self,
        spec: CommandSpec,
        *,
        mounts: Sequence[Mount] = (),
        image: str,
        memory_mb: int,
        pids_limit: int,
    ) -> ProcessResult:
        container_name = f"ga-{uuid.uuid4().hex[:16]}"
        argv = [
            "docker",
            "run",
            "--rm",
            "--network",
            self._network,
            "--memory",
            f"{memory_mb}m",
            "--pids-limit",
            str(pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            _container_user(),
            "--stop-timeout",
            "5",
            "--name",
            container_name,
        ]
        env = dict(DEFAULT_ENV)
        env.update(spec.env)
        for name, value in sorted(env.items()):
            argv.extend(["-e", f"{name}={value}"])
        for mount in mounts:
            flag = ":ro" if mount.read_only else ""
            argv.extend(["-v", f"{mount.host_path}:{mount.container_path}{flag}"])
        argv.extend(["--workdir", spec.cwd, image, *spec.argv])

        started = time.monotonic()
        timeout = spec.timeout_seconds + _GRACE_SECONDS
        try:
            result = _run_docker(argv, timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            _kill_container(container_name)
            timed_out = True
            elapsed = time.monotonic() - started
            return ProcessResult(
                exit_code=None,
                timed_out=True,
                stdout=b"",
                stderr=b"container timed out",
                duration_seconds=elapsed,
            )

        elapsed = time.monotonic() - started
        if not timed_out and result.returncode in (125, 126, 127):
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise ContainerStartError(detail or f"docker run exited {result.returncode}")
        stdout, stdout_truncated, stdout_original = truncate_capture(result.stdout)
        stderr, stderr_truncated, stderr_original = truncate_capture(result.stderr)
        return ProcessResult(
            exit_code=result.returncode,
            timed_out=timed_out,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=elapsed,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            original_stdout_bytes=stdout_original,
            original_stderr_bytes=stderr_original,
        )


def _kill_container(container_name: str) -> None:
    with contextlib.suppress(subprocess.TimeoutExpired, OSError):
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=_DOCKER_KILL_TIMEOUT,
            check=False,
        )


def build_image(
    tag: str, dockerfile: Path, context: Path, *, timeout_seconds: float = 900.0
) -> str:
    """Build an immutable image from *dockerfile* with context *context*.

    Returns the image ID (``sha256:...``) recorded for provenance. Network is
    permitted during the build; scored executions use ``--network none``.
    """
    result = _run_docker(
        ["docker", "build", "-q", "-t", tag, "-f", str(dockerfile), str(context)],
        timeout_seconds,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ContainerStartError(f"docker build failed: {detail}")
    return image_id(tag)


def image_id(image: str) -> str:
    """Return the immutable image ID for *image*, refusing a missing image."""
    result = _run_docker(["docker", "image", "inspect", "--format", "{{.Id}}", image], 60.0)
    if result.returncode != 0:
        raise ContainerStartError(f"image not present: {image}")
    return result.stdout.decode("utf-8", errors="replace").strip()


def image_exists(image: str) -> bool:
    try:
        result = _run_docker(["docker", "image", "inspect", "--format", "{{.Id}}", image], 60.0)
    except ContainerStartError:
        return False
    return result.returncode == 0
