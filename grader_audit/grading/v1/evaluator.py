"""Hardened v1 evaluator (Sections 8.2 and 27.12).

Evaluation follows the Section 27.12 mapping order exactly and preserves every
applicable reason code. Hard scope failures (immutable or outside-editable
edits in the submitted patch) short-circuit test execution with evidence marked
``not_run``. The generated-artifact allowlist never exempts a submitted patch.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from grader_audit.core.docker_runner import ContainerStartError
from grader_audit.core.grader_assets import hash_grader_assets
from grader_audit.core.hashing import sha256_file
from grader_audit.core.models import TaskManifest
from grader_audit.core.outcomes import (
    Changes,
    ErrorInfo,
    EvaluationOutcome,
    OutcomeStatus,
    TestEvidence,
    outcome_with_reason,
)
from grader_audit.core.process import EvaluatorResult, ProcessInfo, Runner, process_info
from grader_audit.core.reason_codes import ReasonCode
from grader_audit.core.snapshots import (
    WorkspaceSnapshot,
    capture_snapshot,
    classify_patch_changes,
    classify_post_grade_changes,
    diff_snapshots,
)
from grader_audit.grading.v1.evidence import evaluate_evidence, load_report
from grader_audit.grading.v1.suite import SuiteRun, run_test_suite

_GRADER_ROOT = "/opt/grader"
_RUNNER_ARGV = ["/usr/local/bin/python", "-I", "/opt/grader/run_pytest.py", "/opt/grader"]


@dataclass
class HardenedV1Context:
    manifest: TaskManifest
    workspace_host: Path
    pristine_snapshot: WorkspaceSnapshot
    pre_grade_snapshot: WorkspaceSnapshot
    authoritative_tests_host: Path
    expected_grader_assets_hash: str
    image: str
    memory_mb: int
    pids_limit: int
    keep_evidence_on_failure: bool = False


class HardenedV1Evaluator:
    def evaluate(self, context: HardenedV1Context, runner: Runner) -> EvaluatorResult:
        try:
            return self._evaluate(context, runner)
        except ContainerStartError as exc:
            return EvaluatorResult(
                outcome=outcome_with_reason(
                    OutcomeStatus.INFRASTRUCTURE_ERROR,
                    None,
                    [ReasonCode.ENVIRONMENT_SETUP_FAILED],
                    error=ErrorInfo(code="environment_setup_failed", message=str(exc)),
                ),
                process_result=None,
            )
        except Exception as exc:
            return EvaluatorResult(
                outcome=outcome_with_reason(
                    OutcomeStatus.INFRASTRUCTURE_ERROR,
                    None,
                    [ReasonCode.INTERNAL_GRADER_ERROR],
                    error=ErrorInfo(
                        code="internal_grader_error",
                        message=f"{type(exc).__name__}: {exc}",
                    ),
                ),
                process_result=None,
            )

    def _evaluate(self, context: HardenedV1Context, runner: Runner) -> EvaluatorResult:
        manifest = context.manifest
        patch_changes = classify_patch_changes(
            manifest,
            diff_snapshots(context.pristine_snapshot, context.pre_grade_snapshot),
        )
        warnings = [
            f"outside_expected_scope: {path}" for path in patch_changes.outside_expected_scope
        ]

        pre_asset_hash = hash_grader_assets(context.authoritative_tests_host)
        if pre_asset_hash != context.expected_grader_assets_hash:
            return EvaluatorResult(
                outcome=outcome_with_reason(
                    OutcomeStatus.INFRASTRUCTURE_ERROR,
                    None,
                    [ReasonCode.GRADER_ASSET_HASH_MISMATCH],
                    warnings=warnings,
                    error=ErrorInfo(
                        code="grader_asset_hash_mismatch",
                        message="frozen grader assets do not match the trusted hash before grading",
                    ),
                    changes=patch_changes,
                ),
                process_result=None,
            )

        reasons: list[ReasonCode] = []
        if patch_changes.immutable_violations:
            reasons.append(ReasonCode.IMMUTABLE_PATH_MODIFIED)
        if patch_changes.outside_editable_scope:
            reasons.append(ReasonCode.OUTSIDE_EDITABLE_SCOPE)

        if reasons:
            return EvaluatorResult(
                outcome=outcome_with_reason(
                    OutcomeStatus.COMPLETED,
                    0.0,
                    reasons,
                    warnings=warnings,
                    test_evidence=TestEvidence(state="not_run"),
                    changes=patch_changes,
                ),
                process_result=None,
            )

        suite = run_test_suite(
            workspace_host=context.workspace_host,
            grader_root=_GRADER_ROOT,
            tests_host=context.authoritative_tests_host,
            expected_nodeids=manifest.grading.hardened_v1.expected_nodeids,
            source_roots=manifest.workspace.source_roots,
            image=context.image,
            memory_mb=context.memory_mb,
            pids_limit=context.pids_limit,
            timeout_seconds=manifest.grading.hardened_v1.timeout_seconds,
            runner=runner,
        )
        process = process_info(suite.result, _RUNNER_ARGV, "/workspace")

        if suite.result.timed_out:
            reasons.append(ReasonCode.TIMEOUT)
            outcome = self._finish(
                context, patch_changes, reasons, warnings, process, suite, evidence_state="missing"
            )
            return EvaluatorResult(outcome=outcome, process_result=suite.result)

        try:
            parsed = load_report(suite.report_path)
        except ValueError:
            reasons.append(ReasonCode.TEST_EVIDENCE_MISSING)
            outcome = self._finish(
                context, patch_changes, reasons, warnings, process, suite, evidence_state="missing"
            )
            return EvaluatorResult(outcome=outcome, process_result=suite.result)

        # Section 27.11 requires process exit status 0 even when a report
        # parses: an inconsistent nonzero process exit is a solution failure,
        # never a pass.
        if suite.result.exit_code != 0:
            reasons.append(ReasonCode.AUTHORITATIVE_TESTS_FAILED)

        report_hash = sha256_file(suite.report_path) if suite.report_path.is_file() else None
        evidence_reasons, evidence = evaluate_evidence(
            manifest.grading.hardened_v1.expected_nodeids,
            parsed,
        )
        evidence.report_sha256 = report_hash
        reasons.extend(evidence_reasons)
        outcome = self._finish(
            context, patch_changes, reasons, warnings, process, suite, evidence=evidence
        )
        return EvaluatorResult(outcome=outcome, process_result=suite.result)

    def _finish(
        self,
        context: HardenedV1Context,
        patch_changes: Changes,
        reasons: list[ReasonCode],
        warnings: list[str],
        process: ProcessInfo,
        suite: SuiteRun,
        *,
        evidence: TestEvidence | None = None,
        evidence_state: str | None = None,
    ) -> EvaluationOutcome:
        if evidence is None:
            evidence = TestEvidence(state=evidence_state or "missing")

        post_changes = Changes()
        try:
            post_grade = capture_snapshot(context.workspace_host)
            post_changes = classify_post_grade_changes(
                context.manifest,
                diff_snapshots(context.pre_grade_snapshot, post_grade),
            )
        except ValueError:
            pass
        if post_changes.immutable_violations:
            reasons.append(ReasonCode.IMMUTABLE_PATH_MODIFIED)
        combined = _merge_changes(patch_changes, post_changes)

        post_asset_hash = hash_grader_assets(context.authoritative_tests_host)
        if post_asset_hash != context.expected_grader_assets_hash:
            if not context.keep_evidence_on_failure:
                shutil.rmtree(suite.evidence_dir, ignore_errors=True)
            return outcome_with_reason(
                OutcomeStatus.INFRASTRUCTURE_ERROR,
                None,
                [ReasonCode.GRADER_ASSET_HASH_MISMATCH],
                warnings=warnings,
                error=ErrorInfo(
                    code="grader_asset_hash_mismatch",
                    message="frozen grader assets do not match the trusted hash after grading",
                ),
                test_evidence=evidence,
                changes=combined,
                process=process,
                duration_seconds=suite.result.duration_seconds,
            )

        if not context.keep_evidence_on_failure:
            shutil.rmtree(suite.evidence_dir, ignore_errors=True)
        return outcome_with_reason(
            OutcomeStatus.COMPLETED,
            0.0 if reasons else 1.0,
            reasons,
            warnings=warnings,
            test_evidence=evidence,
            changes=combined,
            process=process,
            duration_seconds=suite.result.duration_seconds,
        )


def _merge_changes(a: Changes, b: Changes) -> Changes:
    def union(x: list[str], y: list[str]) -> list[str]:
        return sorted(set(x) | set(y), key=lambda p: p.encode("utf-8"))

    return Changes(
        modified_paths=union(a.modified_paths, b.modified_paths),
        immutable_violations=union(a.immutable_violations, b.immutable_violations),
        outside_editable_scope=union(a.outside_editable_scope, b.outside_editable_scope),
        outside_expected_scope=union(a.outside_expected_scope, b.outside_expected_scope),
        generated_artifacts=union(a.generated_artifacts, b.generated_artifacts),
    )
