"""Shared immutable test-suite execution for hardened v1 and the offline oracle.

Both evaluators run the exact same container contract, substituting the grader
root (``/opt/grader`` or ``/opt/oracle``). Oracle assets are never mounted for
naive or hardened grading.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from grader_audit.core.docker_runner import ContainerStartError
from grader_audit.core.process import CommandSpec, Mount, ProcessResult, Runner

RUNNER_SCRIPT = "/usr/local/bin/python"
RUNNER_ARGV0 = "-I"


@dataclass(frozen=True)
class SuiteRun:
    result: ProcessResult
    evidence_dir: Path
    report_path: Path


def run_test_suite(
    *,
    workspace_host: Path,
    grader_root: str,
    tests_host: Path,
    expected_nodeids: list[str],
    source_roots: list[str],
    image: str,
    memory_mb: int,
    pids_limit: int,
    timeout_seconds: float,
    runner: Runner,
    suite_dir: str = "tests",
) -> SuiteRun:
    """Run the immutable pytest runner in a fresh container.

    Authoritative/oracle assets are mounted read-only; the evidence directory is
    a fresh grader-controlled temporary location outside ``/workspace``.
    """
    evidence_dir = Path(tempfile.mkdtemp(prefix=f"ga-evidence-{uuid.uuid4().hex[:8]}-"))
    spec = CommandSpec(
        argv=[RUNNER_SCRIPT, RUNNER_ARGV0, "/opt/grader/run_pytest.py", grader_root],
        cwd="/workspace",
        timeout_seconds=timeout_seconds,
        env={
            "EVIDENCE_DIR": "/tmp/evidence",
            "WORKSPACE_ROOT": "/workspace",
            "SOURCE_ROOTS": json.dumps(source_roots),
            "EXPECTED_NODEIDS": json.dumps(expected_nodeids),
        },
    )
    mounts = [
        Mount(host_path=workspace_host, container_path="/workspace", read_only=False),
        Mount(host_path=tests_host, container_path=f"{grader_root}/{suite_dir}", read_only=True),
        Mount(host_path=evidence_dir, container_path="/tmp/evidence", read_only=False),
    ]
    try:
        result = runner.run(
            spec,
            mounts=mounts,
            image=image,
            memory_mb=memory_mb,
            pids_limit=pids_limit,
        )
    except ContainerStartError:
        raise
    return SuiteRun(
        result=result, evidence_dir=evidence_dir, report_path=evidence_dir / "report.json"
    )
