"""v1/v2 result separation and v2 outcome semantics (unit level, no Docker).

The HardenedV2Evaluator composes the frozen v1 mandatory checks with the
semantic suite. These tests fake the v1 evaluator and the semantic runner so
the composition rules are pinned without containers:

- a v1 rejection must propagate unchanged (semantic evidence absent);
- a v1 pass earns 1.0 only when the semantic suite passes;
- semantic failures produce binary 0.0 with v2 reason codes;
- timeouts and infrastructure errors follow the documented outcome contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from grader_audit.core.docker_runner import ContainerStartError
from grader_audit.core.models import TaskManifest, WorkspaceInfo
from grader_audit.core.outcomes import (
    ErrorInfo,
    EvaluationOutcome,
    OutcomeStatus,
)
from grader_audit.core.process import CommandSpec, Mount, ProcessResult
from grader_v2.grading.evaluator import (
    GRADER_HARDENED_V2,
    HardenedV2Context,
    HardenedV2Evaluator,
)
from grader_v2.grading.evidence import SemanticEvidence
from grader_v2.grading.reason_codes import (
    SEMANTIC_COLLECTION_MISMATCH,
    SEMANTIC_INFRASTRUCTURE_ERROR,
    SEMANTIC_SUITE_TIMEOUT,
    SEMANTIC_TESTS_FAILED,
)
from grader_v2.grading.semantic import SemanticRun


def _snapshot() -> Any:
    from grader_audit.core.snapshots import WorkspaceSnapshot

    return WorkspaceSnapshot(entries=[], sha256="snapshot")


def _context(
    seed: int | None = 20260807,
    task_id: str = "tinydb-missing-doc-ids",
) -> HardenedV2Context:
    return HardenedV2Context(
        manifest=TaskManifest.model_construct(
            id=task_id,
            workspace=WorkspaceInfo.model_construct(source_roots=["src"]),
        ),
        workspace_host=Path("/workspace"),
        pristine_snapshot=_snapshot(),
        pre_grade_snapshot=_snapshot(),
        authoritative_tests_host=Path("/opt/grader/tests"),
        expected_grader_assets_hash="asset-hash",
        image="grader-test-image",
        memory_mb=1024,
        pids_limit=256,
        seed=seed,
    )


class _UnusedRunner:
    """Structurally valid runner that fails if a unit test unexpectedly uses it."""

    def run(
        self,
        spec: CommandSpec,
        *,
        mounts: Sequence[Mount],
        image: str,
        memory_mb: int,
        pids_limit: int,
    ) -> ProcessResult:
        raise AssertionError("the fake v1 evaluator must not invoke the runner")


def _v1_outcome(
    reward: float | None,
    status: OutcomeStatus = OutcomeStatus.COMPLETED,
) -> EvaluationOutcome:
    if status is OutcomeStatus.COMPLETED:
        return EvaluationOutcome(
            status=status, reward=reward, reason_codes=[], duration_seconds=1.0
        )
    return EvaluationOutcome(
        status=status,
        reward=None,
        error=ErrorInfo(code="infra", message="boom"),
    )


class _FakeV1:
    def __init__(self, outcome: EvaluationOutcome) -> None:
        self._outcome = outcome

    def evaluate(self, context: Any, runner: Any) -> Any:
        return type("Result", (), {"outcome": self._outcome})()


def _evidence(ok: bool, *, failed: int = 0, errors: int = 0,
              collected: list[str] | None = None,
              expected: list[str] | None = None) -> SemanticEvidence:
    return SemanticEvidence(
        profile_id="tinydb-docids-v1",
        generator_version="tinydb-docids-v1@1",
        seed=20260807,
        mechanisms=["randomized-hidden-inputs"],
        case_count=len(expected or []),
        suite_sha256="suite",
        report_sha256="report",
        expected_nodeids=sorted(expected or []),
        collected_nodeids=sorted(collected if collected is not None else (expected or [])),
        failed=failed,
        errors=errors,
    )


def _fake_semantic(evidence: SemanticEvidence, *, timed_out: bool = False,
                   duration: float = 2.0, exit_code: int = 0,
                   raise_infra: bool = False) -> Any:
    def run(**kwargs: Any) -> SemanticRun:
        if raise_infra:
            raise ContainerStartError("container exploded")
        return SemanticRun(
            suite_dir=Path("/tmp/suite"),
            evidence=evidence,
            process=ProcessResult(
                exit_code=None if timed_out else exit_code,
                timed_out=timed_out,
                stdout=b"",
                stderr=b"",
                duration_seconds=duration,
            ),
            duration_seconds=duration,
        )

    return run


def _evaluate(monkeypatch: Any, v1_outcome: EvaluationOutcome,
              semantic: Any, task_id: str = "tinydb-missing-doc-ids",
              seed: int | None = 20260807) -> Any:
    import grader_v2.grading.evaluator as evaluator_mod

    monkeypatch.setattr(
        evaluator_mod, "HardenedV1Evaluator", lambda: _FakeV1(v1_outcome)
    )
    monkeypatch.setattr(evaluator_mod, "run_semantic_suite", semantic)
    context = _context(seed, task_id)
    return HardenedV2Evaluator().evaluate(context, runner=_UnusedRunner())


def test_v1_rejection_propagates_unchanged(monkeypatch: Any) -> None:
    outcome = _v1_outcome(0.0)
    result = _evaluate(
        monkeypatch, outcome, _fake_semantic(_evidence(True))
    )
    assert result.outcome is outcome
    assert result.semantic is None
    assert result.v1_outcome is outcome


def test_v1_infrastructure_propagates(monkeypatch: Any) -> None:
    outcome = _v1_outcome(None, OutcomeStatus.INFRASTRUCTURE_ERROR)
    result = _evaluate(
        monkeypatch, outcome, _fake_semantic(_evidence(True))
    )
    assert result.outcome is outcome
    assert result.semantic is None


def test_no_profile_returns_v1_outcome(monkeypatch: Any) -> None:
    outcome = _v1_outcome(1.0)
    result = _evaluate(
        monkeypatch, outcome, _fake_semantic(_evidence(True)),
        task_id="tomli-type-error",
    )
    assert result.outcome is outcome
    assert result.semantic is None


def test_semantic_pass_keeps_reward_and_adds_duration(monkeypatch: Any) -> None:
    outcome = _v1_outcome(1.0)
    result = _evaluate(
        monkeypatch, outcome, _fake_semantic(_evidence(True), duration=2.5)
    )
    assert result.outcome.reward == 1.0
    assert result.outcome.duration_seconds == 1.0 + 2.5
    assert result.semantic is not None
    assert result.semantic.ok


def test_semantic_failure_yields_zero_with_reason(monkeypatch: Any) -> None:
    evidence = _evidence(
        False,
        failed=2,
        collected=["a", "b", "c"],
        expected=["a", "b"],
    )
    result = _evaluate(monkeypatch, _v1_outcome(1.0), _fake_semantic(evidence))
    assert result.outcome.reward == 0.0
    assert SEMANTIC_COLLECTION_MISMATCH in result.outcome.reason_codes
    assert SEMANTIC_TESTS_FAILED in result.outcome.reason_codes
    assert result.outcome.duration_seconds == 1.0 + 2.0
    assert result.semantic is not None


def test_semantic_failure_without_detail_still_zero(monkeypatch: Any) -> None:
    evidence = _evidence(False, failed=0, errors=0, collected=["a"], expected=["a", "b"])
    result = _evaluate(monkeypatch, _v1_outcome(1.0), _fake_semantic(evidence))
    assert result.outcome.reward == 0.0
    assert SEMANTIC_COLLECTION_MISMATCH in result.outcome.reason_codes


def test_semantic_timeout_yields_zero_with_timeout_reason(monkeypatch: Any) -> None:
    evidence = _evidence(True)
    result = _evaluate(
        monkeypatch, _v1_outcome(1.0),
        _fake_semantic(evidence, timed_out=True, duration=60.0),
    )
    assert result.outcome.reward == 0.0
    assert SEMANTIC_SUITE_TIMEOUT in result.outcome.reason_codes
    assert result.outcome.duration_seconds == 1.0 + 60.0


def test_semantic_container_failure_is_infrastructure(monkeypatch: Any) -> None:
    result = _evaluate(
        monkeypatch, _v1_outcome(1.0),
        _fake_semantic(_evidence(True), raise_infra=True),
    )
    assert result.outcome.status is OutcomeStatus.INFRASTRUCTURE_ERROR
    assert result.outcome.reward is None
    assert SEMANTIC_INFRASTRUCTURE_ERROR in result.outcome.reason_codes
    assert result.semantic is None


def test_grader_constant() -> None:
    assert GRADER_HARDENED_V2 == "hardened_v2"
