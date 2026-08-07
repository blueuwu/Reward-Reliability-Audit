"""Hardened v2 evaluator (hardening §6).

v2 composes the frozen v1 mandatory checks (scope, asset integrity, exact
authoritative node IDs, process exit, evidence parsing — logical AND) with a
task-specific semantic suite generated from a post-rollout seed. A v1
rejection propagates unchanged (v2 is at least as strict). A v1 pass earns
``1.0`` only when the semantic suite also passes; a semantic failure earns
``0.0`` with v2 reason codes and full evidence.

Outcome semantics (hardening §6): controlled rewards are exactly ``0.0`` or
``1.0``; mandatory checks combine by logical AND; a worse substantive solution
never outscores a better one (the semantic suite is generated from the task
contract, never from the submitted code, and the seed is drawn only after the
agent finishes).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from grader_audit.core.docker_runner import ContainerStartError
from grader_audit.core.models import TaskManifest
from grader_audit.core.outcomes import (
    ErrorInfo,
    EvaluationOutcome,
    OutcomeStatus,
)
from grader_audit.core.process import Runner
from grader_audit.core.snapshots import WorkspaceSnapshot
from grader_audit.grading.v1.evaluator import (
    HardenedV1Context,
    HardenedV1Evaluator,
)
from grader_v2.grading.evidence import SemanticEvidence
from grader_v2.grading.reason_codes import (
    SEMANTIC_COLLECTION_MISMATCH,
    SEMANTIC_EVIDENCE_MISSING,
    SEMANTIC_INFRASTRUCTURE_ERROR,
    SEMANTIC_SUITE_TIMEOUT,
    SEMANTIC_TESTS_FAILED,
)
from grader_v2.grading.semantic import SemanticProfile, get_profile, run_semantic_suite

GRADER_HARDENED_V2 = "hardened_v2"


@dataclass
class HardenedV2Context:
    """v2 evaluation context: the full v1 context plus the seed control."""

    manifest: TaskManifest
    workspace_host: Path
    pristine_snapshot: WorkspaceSnapshot
    pre_grade_snapshot: WorkspaceSnapshot
    authoritative_tests_host: Path
    expected_grader_assets_hash: str
    image: str
    memory_mb: int
    pids_limit: int
    seed: int | None = None

    def v1_context(self) -> HardenedV1Context:
        return HardenedV1Context(
            manifest=self.manifest,
            workspace_host=self.workspace_host,
            pristine_snapshot=self.pristine_snapshot,
            pre_grade_snapshot=self.pre_grade_snapshot,
            authoritative_tests_host=self.authoritative_tests_host,
            expected_grader_assets_hash=self.expected_grader_assets_hash,
            image=self.image,
            memory_mb=self.memory_mb,
            pids_limit=self.pids_limit,
        )


@dataclass(frozen=True)
class V2EvaluatorResult:
    """The v2 outcome plus the semantic sub-result and the v1 sub-result."""

    outcome: EvaluationOutcome
    semantic: SemanticEvidence | None
    v1_outcome: EvaluationOutcome


class HardenedV2Evaluator:
    """Genuine semantic grader v2; never edits frozen-v1 behavior."""

    def evaluate(self, context: HardenedV2Context, runner: Runner) -> V2EvaluatorResult:
        v1_result = HardenedV1Evaluator().evaluate(context.v1_context(), runner)
        v1_outcome = v1_result.outcome
        if v1_outcome.status is not OutcomeStatus.COMPLETED or v1_outcome.reward != 1.0:
            # Mandatory checks combine by logical AND: any v1 rejection or
            # infrastructure outcome propagates unchanged.
            return V2EvaluatorResult(
                outcome=v1_outcome, semantic=None, v1_outcome=v1_outcome
            )

        task_id = context.manifest.id
        profile = get_profile(task_id)
        if profile is None:
            # No profile: v2 adds no new checks (documented extension point).
            return V2EvaluatorResult(
                outcome=v1_outcome, semantic=None, v1_outcome=v1_outcome
            )

        seed = context.seed if context.seed is not None else secrets.randbits(63)
        try:
            run = self._run_semantic(context, profile, seed, runner)
        except ContainerStartError as exc:
            return V2EvaluatorResult(
                outcome=EvaluationOutcome(
                    status=OutcomeStatus.INFRASTRUCTURE_ERROR,
                    reward=None,
                    reason_codes=[SEMANTIC_INFRASTRUCTURE_ERROR],
                    error=ErrorInfo(
                        code="semantic_infrastructure_error", message=str(exc)
                    ),
                ),
                semantic=None,
                v1_outcome=v1_outcome,
            )
        if run.process.timed_out:
            outcome = v1_outcome.model_copy(
                update={
                    "reason_codes": [*v1_outcome.reason_codes, SEMANTIC_SUITE_TIMEOUT],
                    "reward": 0.0,
                    "duration_seconds": v1_outcome.duration_seconds
                    + run.duration_seconds,
                }
            )
            return V2EvaluatorResult(
                outcome=outcome, semantic=run.evidence, v1_outcome=v1_outcome
            )
        if run.evidence.ok:
            outcome = v1_outcome.model_copy(
                update={
                    "duration_seconds": v1_outcome.duration_seconds
                    + run.duration_seconds,
                }
            )
            return V2EvaluatorResult(
                outcome=outcome, semantic=run.evidence, v1_outcome=v1_outcome
            )
        return V2EvaluatorResult(
            outcome=self._semantic_failure_outcome(
                v1_outcome, run.evidence, run.duration_seconds
            ),
            semantic=run.evidence,
            v1_outcome=v1_outcome,
        )

    @staticmethod
    def _run_semantic(
        context: HardenedV2Context,
        profile: SemanticProfile,
        seed: int,
        runner: Runner,
    ):
        manifest = context.manifest
        source_roots = list(manifest.workspace.source_roots)
        return run_semantic_suite(
            workspace_host=context.workspace_host,
            profile=profile,
            seed=seed,
            source_roots=source_roots,
            image=context.image,
            memory_mb=context.memory_mb,
            pids_limit=context.pids_limit,
            runner=runner,
        )

    @staticmethod
    def _semantic_failure_outcome(
        v1_outcome: EvaluationOutcome,
        evidence: SemanticEvidence,
        duration_seconds: float,
    ) -> EvaluationOutcome:
        reasons: list[str] = list(v1_outcome.reason_codes)
        if evidence.collection_mismatch:
            reasons.append(SEMANTIC_COLLECTION_MISMATCH)
        if evidence.failed or evidence.errors:
            reasons.append(SEMANTIC_TESTS_FAILED)
        if not evidence.report_sha256:
            reasons.append(SEMANTIC_EVIDENCE_MISSING)
        if not reasons:
            reasons.append(SEMANTIC_TESTS_FAILED)
        return EvaluationOutcome(
            status=OutcomeStatus.COMPLETED,
            reward=0.0,
            reason_codes=reasons,
            warnings=v1_outcome.warnings,
            test_evidence=v1_outcome.test_evidence,
            changes=v1_outcome.changes,
            process=v1_outcome.process,
            duration_seconds=v1_outcome.duration_seconds + duration_seconds,
        )
