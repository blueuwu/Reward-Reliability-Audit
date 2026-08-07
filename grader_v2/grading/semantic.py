"""Semantic profiles and suite execution for hardened v2 (hardening §6).

A profile is the explicit extension point: tasks with a profile get seeded
semantic checks on top of the frozen v1 mandatory checks; tasks without one
score exactly as v1 (documented behavior, never implied stronger).

Suite execution reuses the frozen in-container runner (D-056): the generated
tests are mounted onto ``/opt/grader/tests`` and run through
``/opt/grader/run_pytest.py`` with the immutable grader config, so the
execution contract (isolated subprocess, sanitized environment, JSON report,
node-ID verification) is byte-identical to the authoritative path.
"""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from grader_audit.core.hashing import sha256_bytes
from grader_audit.core.process import CommandSpec, Mount, ProcessResult, Runner
from grader_v2.grading.evidence import SemanticEvidence, parse_semantic_report
from grader_v2.grading.generators import (
    GeneratedSuite,
    generate_docids_suite,
    generate_queries_suite,
)

_RUNNER_SCRIPT = "/usr/local/bin/python"
_RUNNER_ARGV0 = "-I"

#: The immutable runner root; the semantic suite replaces its ``tests``
#: directory per run (the payload files are never touched, D-056).
_GRADER_ROOT = "/opt/grader"

_GENERATORS: dict[str, Callable[[int], GeneratedSuite]] = {
    "tinydb-docids-v1": generate_docids_suite,
    "tinydb-query-freeze-v1": generate_queries_suite,
}


@dataclass(frozen=True)
class SemanticProfile:
    """One task-scoped semantic defense profile."""

    profile_id: str
    generator_version: str
    task_ids: frozenset[str]
    mechanisms: tuple[str, ...]
    timeout_seconds: float

    def generate(self, seed: int) -> GeneratedSuite:
        generator = _GENERATORS[self.profile_id]
        suite = generator(seed)
        return GeneratedSuite(
            filename=suite.filename,
            text=suite.text,
            expected_nodeids=list(suite.expected_nodeids),
        )


PROFILES: tuple[SemanticProfile, ...] = (
    SemanticProfile(
        profile_id="tinydb-docids-v1",
        generator_version="tinydb-docids-v1@1",
        task_ids=frozenset({"tinydb-missing-doc-ids"}),
        mechanisms=(
            "randomized-hidden-inputs",
            "input-shape-expansion",
            "state-transition",
            "metamorphic",
        ),
        timeout_seconds=60.0,
    ),
    SemanticProfile(
        profile_id="tinydb-query-freeze-v1",
        generator_version="tinydb-query-freeze-v1@1",
        task_ids=frozenset({"tinydb-query-test-unhashable"}),
        mechanisms=(
            "randomized-hidden-inputs",
            "input-shape-expansion",
            "metamorphic",
            "differential",
        ),
        timeout_seconds=60.0,
    ),
)

_PROFILE_BY_TASK: dict[str, SemanticProfile] = {
    task_id: profile for profile in PROFILES for task_id in profile.task_ids
}


def get_profile(task_id: str) -> SemanticProfile | None:
    """Return the semantic profile for *task_id*, or ``None`` (v1 behavior)."""
    return _PROFILE_BY_TASK.get(task_id)


@dataclass(frozen=True)
class SemanticRun:
    """One executed semantic suite plus its structured evidence."""

    suite_dir: Path
    evidence: SemanticEvidence
    process: ProcessResult
    duration_seconds: float


def write_generated_suite(base_dir: Path, suite: GeneratedSuite) -> tuple[Path, str]:
    """Write the generated test file into *base_dir*; return (dir, sha256)."""
    test_file = base_dir / suite.filename
    test_file.write_text(suite.text, encoding="utf-8", newline="\n")
    return base_dir, sha256_bytes(suite.text.encode("utf-8"))


def run_semantic_suite(
    *,
    workspace_host: Path,
    profile: SemanticProfile,
    seed: int,
    source_roots: list[str],
    image: str,
    memory_mb: int,
    pids_limit: int,
    runner: Runner,
    suite_base: Path | None = None,
) -> SemanticRun:
    """Generate and execute the seeded semantic suite; return evidence.

    *suite_base* (grader-controlled temp dir) is where the generated test file
    is written before it is bound onto ``/opt/grader/tests``. The seed is drawn
    by the caller AFTER the agent finishes (hardening §6); the caller records
    the returned evidence.
    """
    suite = profile.generate(seed)
    if suite_base is None:
        suite_base = Path(tempfile.mkdtemp(prefix=f"ga-semantic-{uuid.uuid4().hex[:8]}-"))
    suite_dir, suite_sha256 = write_generated_suite(suite_base, suite)
    evidence_dir = Path(tempfile.mkdtemp(prefix=f"ga-semantic-evidence-{uuid.uuid4().hex[:8]}-"))
    spec = CommandSpec(
        argv=[_RUNNER_SCRIPT, _RUNNER_ARGV0, "/opt/grader/run_pytest.py", _GRADER_ROOT],
        cwd="/workspace",
        timeout_seconds=profile.timeout_seconds,
        env={
            "EVIDENCE_DIR": "/tmp/evidence",
            "WORKSPACE_ROOT": "/workspace",
            "SOURCE_ROOTS": json.dumps(source_roots),
            "EXPECTED_NODEIDS": json.dumps(sorted(suite.expected_nodeids)),
        },
    )
    mounts = [
        Mount(host_path=workspace_host, container_path="/workspace", read_only=False),
        Mount(
            host_path=suite_dir,
            container_path=f"{_GRADER_ROOT}/tests",
            read_only=True,
        ),
        Mount(host_path=evidence_dir, container_path="/tmp/evidence", read_only=False),
    ]
    started = time.monotonic()
    result = runner.run(
        spec,
        mounts=mounts,
        image=image,
        memory_mb=memory_mb,
        pids_limit=pids_limit,
    )
    elapsed = time.monotonic() - started
    evidence = parse_semantic_report(
        evidence_dir / "report.json",
        profile_id=profile.profile_id,
        generator_version=profile.generator_version,
        seed=seed,
        mechanisms=list(profile.mechanisms),
        expected_nodeids=suite.expected_nodeids,
        suite_sha256=suite_sha256,
    )
    return SemanticRun(
        suite_dir=suite_dir,
        evidence=evidence,
        process=result,
        duration_seconds=elapsed,
    )
