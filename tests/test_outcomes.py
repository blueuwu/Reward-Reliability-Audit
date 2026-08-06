"""Outcome-model invariants and naive grader mapping (Sections 27.12, 27.19)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError

from grader_audit.core.docker_runner import ContainerStartError
from grader_audit.core.outcomes import (
    ErrorInfo,
    EvaluationOutcome,
    OutcomeStatus,
    ResultInfo,
)
from grader_audit.core.process import (
    CommandSpec,
    EvaluatorResult,
    Mount,
    ProcessResult,
)
from grader_audit.grading.naive.evaluator import NaiveContext, NaiveEvaluator


class ScriptedRunner:
    def __init__(self, result: ProcessResult) -> None:
        self._result = result

    def run(
        self,
        spec: CommandSpec,
        *,
        mounts: Sequence[Mount],
        image: str,
        memory_mb: int,
        pids_limit: int,
    ) -> ProcessResult:
        del spec, mounts, image, memory_mb, pids_limit
        return self._result


class FailingRunner:
    def run(
        self,
        spec: CommandSpec,
        *,
        mounts: Sequence[Mount],
        image: str,
        memory_mb: int,
        pids_limit: int,
    ) -> ProcessResult:
        del spec, mounts, image, memory_mb, pids_limit
        raise ContainerStartError("cannot start container")


def _context(workspace: Path) -> NaiveContext:
    return NaiveContext(
        argv=["python", "-m", "pytest", "tests", "-q"],
        cwd="/workspace",
        timeout_seconds=60.0,
        workspace_host=workspace,
        image="test-image",
        memory_mb=1024,
        pids_limit=256,
    )


def test_result_info_reward_is_exactly_binary() -> None:
    ResultInfo(reward=1.0, accepted=True)
    ResultInfo(reward=0.0, accepted=False)
    with pytest.raises(ValidationError):
        ResultInfo(reward=0.5, accepted=False)
    with pytest.raises(ValidationError):
        ResultInfo(reward=1.0, accepted=False)


def test_completed_outcome_requires_reward() -> None:
    with pytest.raises(ValidationError, match="require reward"):
        EvaluationOutcome(status=OutcomeStatus.COMPLETED, reward=None)


def test_non_completed_outcome_requires_error() -> None:
    with pytest.raises(ValidationError, match="require an error"):
        EvaluationOutcome(status=OutcomeStatus.INFRASTRUCTURE_ERROR)


def test_naive_exit_zero_maps_to_reward(tmp_path: Path) -> None:
    runner = ScriptedRunner(
        ProcessResult(
            exit_code=0, timed_out=False, stdout=b"3 passed", stderr=b"", duration_seconds=0.1
        )
    )
    result = NaiveEvaluator().evaluate(_context(tmp_path), runner)
    assert result.outcome.status is OutcomeStatus.COMPLETED
    assert result.outcome.reward == 1.0
    assert result.outcome.accepted is True
    assert result.outcome.reason_codes == ["naive_exit_zero"]


def test_naive_nonzero_exit_maps_to_reward(tmp_path: Path) -> None:
    runner = ScriptedRunner(
        ProcessResult(
            exit_code=1, timed_out=False, stdout=b"1 failed", stderr=b"", duration_seconds=0.1
        )
    )
    result = NaiveEvaluator().evaluate(_context(tmp_path), runner)
    assert result.outcome.reward == 0.0
    assert result.outcome.reason_codes == ["naive_nonzero_exit"]


def test_naive_timeout_is_completed_failure(tmp_path: Path) -> None:
    runner = ScriptedRunner(
        ProcessResult(exit_code=None, timed_out=True, stdout=b"", stderr=b"", duration_seconds=60.0)
    )
    result = NaiveEvaluator().evaluate(_context(tmp_path), runner)
    assert result.outcome.status is OutcomeStatus.COMPLETED
    assert result.outcome.reward == 0.0
    assert result.outcome.reason_codes == ["timeout"]


def test_naive_container_start_error_is_infrastructure(tmp_path: Path) -> None:
    result = NaiveEvaluator().evaluate(_context(tmp_path), FailingRunner())
    assert result.outcome.status is OutcomeStatus.INFRASTRUCTURE_ERROR
    assert result.outcome.reward is None
    assert result.outcome.reason_codes == ["environment_setup_failed"]
    assert isinstance(result.outcome.error, ErrorInfo)


@pytest.mark.parametrize("exit_code", [126, 127])
def test_naive_missing_executable_is_infrastructure(tmp_path: Path, exit_code: int) -> None:
    runner = ScriptedRunner(
        ProcessResult(
            exit_code=exit_code,
            timed_out=False,
            stdout=b"",
            stderr=b"executable not found",
            duration_seconds=0.1,
        )
    )
    result = NaiveEvaluator().evaluate(_context(tmp_path), runner)
    assert result.outcome.status is OutcomeStatus.INFRASTRUCTURE_ERROR
    assert result.outcome.reward is None
    assert result.outcome.reason_codes == ["environment_setup_failed"]


def test_naive_signal_terminated_is_completed_failure(tmp_path: Path) -> None:
    runner = ScriptedRunner(
        ProcessResult(
            exit_code=137,
            timed_out=False,
            stdout=b"",
            stderr=b"",
            duration_seconds=0.1,
        )
    )
    result = NaiveEvaluator().evaluate(_context(tmp_path), runner)
    assert result.outcome.status is OutcomeStatus.COMPLETED
    assert result.outcome.reward == 0.0
    assert result.outcome.reason_codes == ["naive_nonzero_exit"]


def test_evaluator_result_carries_process_result(tmp_path: Path) -> None:
    runner = ScriptedRunner(
        ProcessResult(exit_code=0, timed_out=False, stdout=b"ok", stderr=b"", duration_seconds=0.1)
    )
    result = NaiveEvaluator().evaluate(_context(tmp_path), runner)
    assert isinstance(result, EvaluatorResult)
    assert result.process_result is not None
    assert result.process_result.exit_code == 0
