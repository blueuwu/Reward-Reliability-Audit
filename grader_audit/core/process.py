"""Command execution contracts (Sections 27.3 and 27.11).

Every scored command runs as an argument array (never ``shell=True``), captures
stdout and stderr as separate binary-safe artifacts, truncates each to 2 MiB,
enforces a timeout, and records the original byte counts.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from grader_audit.core.hashing import sha256_bytes
from grader_audit.core.outcomes import EvaluationOutcome, ProcessInfo

MAX_CAPTURE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class EvaluatorResult:
    """An evaluator outcome plus the raw process result for artifact persistence."""

    outcome: EvaluationOutcome
    process_result: ProcessResult | None = None


@dataclass(frozen=True)
class Mount:
    host_path: Path
    container_path: str
    read_only: bool = False


@dataclass(frozen=True)
class CommandSpec:
    argv: list[str]
    cwd: str = "/workspace"
    timeout_seconds: float = 60.0
    env: dict[str, str] = field(default_factory=lambda: dict[str, str]())


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    timed_out: bool
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    original_stdout_bytes: int = 0
    original_stderr_bytes: int = 0

    @property
    def stdout_sha256(self) -> str:
        return sha256_bytes(self.stdout)

    @property
    def stderr_sha256(self) -> str:
        return sha256_bytes(self.stderr)


class Runner(Protocol):
    """Protocol implemented by the container and host process runners."""

    def run(
        self,
        spec: CommandSpec,
        *,
        mounts: Sequence[Mount],
        image: str,
        memory_mb: int,
        pids_limit: int,
    ) -> ProcessResult: ...


class HostProcessRunner:
    """Host subprocess runner used only for unit tests on the orchestrator host.

    Scored task executions MUST use the container runner (Section 27.1); this
    class exists so command-mapping logic can be tested without a daemon.
    """

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd

    def run(
        self,
        spec: CommandSpec,
        *,
        mounts: Sequence[Mount] = (),
        image: str,
        memory_mb: int,
        pids_limit: int,
    ) -> ProcessResult:
        started = time.monotonic()
        try:
            result = subprocess.run(
                spec.argv,
                capture_output=True,
                timeout=spec.timeout_seconds,
                cwd=None if self._cwd is None else str(self._cwd),
                check=False,
            )
            elapsed = time.monotonic() - started
            stdout, stdout_truncated, stdout_original = truncate_capture(result.stdout)
            stderr, stderr_truncated, stderr_original = truncate_capture(result.stderr)
            return ProcessResult(
                exit_code=result.returncode,
                timed_out=False,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=elapsed,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                original_stdout_bytes=stdout_original,
                original_stderr_bytes=stderr_original,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started
            return ProcessResult(
                exit_code=None,
                timed_out=True,
                stdout=exc.stdout if isinstance(exc.stdout, bytes) else b"",
                stderr=exc.stderr if isinstance(exc.stderr, bytes) else b"",
                duration_seconds=elapsed,
            )
        except (FileNotFoundError, OSError):
            elapsed = time.monotonic() - started
            return ProcessResult(
                exit_code=None,
                timed_out=False,
                stdout=b"",
                stderr=b"executable not found",
                duration_seconds=elapsed,
            )


def truncate_capture(data: bytes) -> tuple[bytes, bool, int]:
    """Truncate captured output to ``MAX_CAPTURE_BYTES``."""
    if len(data) <= MAX_CAPTURE_BYTES:
        return data, False, len(data)
    return data[:MAX_CAPTURE_BYTES], True, len(data)


def process_info(result: ProcessResult, argv: list[str], cwd: str) -> ProcessInfo:
    """Build the serialized process record from a raw process result."""
    return ProcessInfo(
        argv=list(argv),
        cwd=cwd,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        stdout_sha256=result.stdout_sha256,
        stderr_sha256=result.stderr_sha256,
        stdout_truncated=result.stdout_truncated,
        stderr_truncated=result.stderr_truncated,
        stdout_bytes=result.original_stdout_bytes,
        stderr_bytes=result.original_stderr_bytes,
        duration_seconds=result.duration_seconds,
    )
