"""Exact exit-code naive grader (Sections 8.1 and 27.12).

Reward is ``1.0`` if and only if the configured pytest command exits ``0``.
Collection counts are parsed for observation but never change the reward.
Container resource and network limits are shared safety controls, not naive
hardening. Container-start and missing-executable failures are infrastructure
errors; a command timeout is a completed solution failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grader_audit.core.docker_runner import ContainerStartError
from grader_audit.core.outcomes import (
    ErrorInfo,
    EvaluationOutcome,
    OutcomeStatus,
    TestEvidence,
    outcome_with_reason,
)
from grader_audit.core.process import (
    CommandSpec,
    EvaluatorResult,
    Mount,
    ProcessResult,
    Runner,
    process_info,
)
from grader_audit.core.reason_codes import ReasonCode
from grader_audit.grading.v1.evidence import parse_collection_count


@dataclass(frozen=True)
class NaiveContext:
    argv: list[str]
    cwd: str
    timeout_seconds: float
    workspace_host: Path
    image: str
    memory_mb: int
    pids_limit: int


class NaiveEvaluator:
    def evaluate(self, context: NaiveContext, runner: Runner) -> EvaluatorResult:
        spec = CommandSpec(
            argv=context.argv, cwd=context.cwd, timeout_seconds=context.timeout_seconds
        )
        mounts = [
            Mount(host_path=context.workspace_host, container_path="/workspace", read_only=False)
        ]
        try:
            result = runner.run(
                spec,
                mounts=mounts,
                image=context.image,
                memory_mb=context.memory_mb,
                pids_limit=context.pids_limit,
            )
        except ContainerStartError as exc:
            outcome = outcome_with_reason(
                OutcomeStatus.INFRASTRUCTURE_ERROR,
                None,
                [ReasonCode.ENVIRONMENT_SETUP_FAILED],
                error=ErrorInfo(code="environment_setup_failed", message=str(exc)),
                duration_seconds=0.0,
            )
            return EvaluatorResult(outcome=outcome, process_result=None)

        return EvaluatorResult(outcome=_map_result(context, result), process_result=result)


def _map_result(context: NaiveContext, result: ProcessResult) -> EvaluationOutcome:
    process = process_info(result, context.argv, context.cwd)
    if result.exit_code in (125, 126, 127) and not result.timed_out:
        return outcome_with_reason(
            OutcomeStatus.INFRASTRUCTURE_ERROR,
            None,
            [ReasonCode.ENVIRONMENT_SETUP_FAILED],
            error=ErrorInfo(
                code="environment_setup_failed",
                message=f"container could not start or executable absent (exit {result.exit_code})",
            ),
            process=process,
            duration_seconds=result.duration_seconds,
        )
    if result.timed_out:
        evidence = TestEvidence(
            state="complete", parsed_collected_count=_observe_collection(result)
        )
        return outcome_with_reason(
            OutcomeStatus.COMPLETED,
            0.0,
            [ReasonCode.TIMEOUT],
            test_evidence=evidence,
            process=process,
            duration_seconds=result.duration_seconds,
        )

    observed = _observe_collection(result)
    evidence = TestEvidence(state="complete", parsed_collected_count=observed)
    if result.exit_code == 0:
        return outcome_with_reason(
            OutcomeStatus.COMPLETED,
            1.0,
            [ReasonCode.NAIVE_EXIT_ZERO],
            test_evidence=evidence,
            process=process,
            duration_seconds=result.duration_seconds,
        )
    return outcome_with_reason(
        OutcomeStatus.COMPLETED,
        0.0,
        [ReasonCode.NAIVE_NONZERO_EXIT],
        test_evidence=evidence,
        process=process,
        duration_seconds=result.duration_seconds,
    )


def _observe_collection(result: ProcessResult) -> int | None:
    output = result.stdout.decode("utf-8", errors="replace")
    return parse_collection_count(output)
