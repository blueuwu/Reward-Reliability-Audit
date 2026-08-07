"""Runner implementations for the application-v2 deployment (D-053, D-056).

The frozen grading core executes every scored command through the ``Runner``
protocol. Two runner implementations share that core and must produce the same
outcomes:

- ``DockerRunner`` (protected, ``grader_audit.core.docker_runner``) — used by
  the host-side offline pipeline (regression matrices, labeling); every scored
  command runs in a fresh task container with ``--rm --network none``.
- ``InContainerRunner`` (this module) — used inside the deployment container,
  where no Docker daemon exists (hardening Gate B, question 8). "Bind mounts"
  are implemented as trusted-process directory copies onto the baked-in image
  paths: the serving process is the trusted zone, the immutable payload files
  under ``/opt/grader`` are never overwritten, and only the run-specific
  ``tests`` directory is replaced per run. Read-write mounts (the evidence
  directory) are seeded from their host side before the run and copied back
  after it, mirroring the container bind-mount contract.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from grader_audit.core.docker_runner import ContainerStartError
from grader_audit.core.process import CommandSpec, Mount, ProcessResult, truncate_capture

#: Container paths whose contents may be replaced per run by the trusted process.
#: Only the run-specific tests directory is ever replaced; the immutable payload
#: files (run_pytest.py, grader_plugin.py, pytest.ini) are never touched.
_REPLACEABLE_ROOTS = frozenset({"/opt/grader/tests", "/opt/oracle/tests"})

#: Environment allowlist for the in-container subprocess, mirroring the Docker
#: runner's explicit-environment contract.
_DEFAULT_ENV: dict[str, str] = {
    "PYTHONHASHSEED": "0",
    "PYTHONUTF8": "1",
}


def _copy_tree(source: Path, target: Path) -> None:
    """Replace *target* with a recursive copy of *source*."""
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)


class InContainerRunner:
    """Runs the immutable grader payload as an isolated argument-array subprocess.

    ``memory_mb`` and ``pids_limit`` are recorded for evidence only: the
    deployment container's own runtime applies those limits; the subprocess
    inherits them.
    """

    def __init__(self) -> None:
        self._applied_evidence: list[tuple[Path, Path]] = []

    def run(
        self,
        spec: CommandSpec,
        *,
        mounts: Sequence[Mount] = (),
        image: str,
        memory_mb: int,
        pids_limit: int,
    ) -> ProcessResult:
        self._applied_evidence = []
        try:
            self._apply_mounts(mounts)
            return self._run(spec, image, memory_mb, pids_limit)
        except OSError as exc:
            raise ContainerStartError(f"in-container run failed: {exc}") from exc
        finally:
            self._copy_back_evidence()

    def _apply_mounts(self, mounts: Sequence[Mount]) -> None:
        for mount in mounts:
            host = mount.host_path.resolve()
            container = Path(mount.container_path).resolve()
            if host == container:
                # Already in place inside the container (workspace root).
                if not host.is_dir():
                    host.mkdir(parents=True, exist_ok=True)
                continue
            if mount.read_only:
                rel = mount.container_path.rstrip("/")
                if rel not in _REPLACEABLE_ROOTS:
                    raise ContainerStartError(
                        f"refusing to replace non-replaceable read-only root: "
                        f"{mount.container_path}"
                    )
                if not host.is_dir():
                    raise ContainerStartError(f"mount source missing: {mount.host_path}")
                _copy_tree(host, container)
                continue
            # Read-write mount: seed the container side from the host side and
            # copy changes back after the run (evidence directory contract).
            if not host.is_dir():
                host.mkdir(parents=True, exist_ok=True)
            if container.exists():
                shutil.rmtree(container)
            _copy_tree(host, container)
            self._applied_evidence.append((container, host))

    def _copy_back_evidence(self) -> None:
        for container, host in self._applied_evidence:
            if container.is_dir() and host.is_dir():
                shutil.rmtree(host)
                _copy_tree(container, host)

    def _run(
        self, spec: CommandSpec, image: str, memory_mb: int, pids_limit: int
    ) -> ProcessResult:
        env = dict(_DEFAULT_ENV)
        # Minimal allowlist, mirroring the Docker runner: the container's PATH
        # (the naive grader runs plain `python -m pytest`) plus the pinned
        # hash/encoding settings and the orchestrator-supplied variables.
        if os.environ.get("PATH"):
            env["PATH"] = os.environ["PATH"]
        env.update(spec.env)
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                list(spec.argv),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=spec.cwd,
                env=env,
                start_new_session=True,
            )
            child_pid = proc.pid
            try:
                stdout, stderr = proc.communicate(timeout=spec.timeout_seconds)
            except subprocess.TimeoutExpired:
                self._kill_group(child_pid)
                stdout, stderr = proc.communicate()
                elapsed = time.monotonic() - started
                return ProcessResult(
                    exit_code=None,
                    timed_out=True,
                    stdout=stdout,
                    stderr=stderr,
                    duration_seconds=elapsed,
                )
            elapsed = time.monotonic() - started
            out, out_truncated, out_original = truncate_capture(stdout)
            err, err_truncated, err_original = truncate_capture(stderr)
            return ProcessResult(
                exit_code=proc.returncode,
                timed_out=False,
                stdout=out,
                stderr=err,
                duration_seconds=elapsed,
                stdout_truncated=out_truncated,
                stderr_truncated=err_truncated,
                original_stdout_bytes=out_original,
                original_stderr_bytes=err_original,
            )
        except (FileNotFoundError, OSError):
            elapsed = time.monotonic() - started
            return ProcessResult(
                exit_code=None,
                timed_out=False,
                stdout=b"",
                stderr=b"in-container executable not found",
                duration_seconds=elapsed,
            )

    @staticmethod
    def _kill_group(child_pid: int) -> None:
        """Best-effort kill of the graded process group after a timeout."""
        import contextlib
        import sys

        killpg = getattr(os, "killpg", None)
        if killpg is None or sys.platform == "win32":
            return
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            killpg(child_pid, signal.SIGKILL)
