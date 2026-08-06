"""Offline oracle evaluator (Sections 8.3 and 27.9).

The oracle is a dataset-curation and manual-audit facility only. It MUST NOT be
callable through HUD, from the task container, or by the agent, and its assets
are never mounted during naive, hardened, or adaptive-attack runs. It uses the
same environment clearing, isolated interpreter, plugin allowlist, exact
node-ID validation, resource limits, and read-only mount rules as hardened,
substituting ``/opt/oracle`` for ``/opt/grader``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grader_audit.core.docker_runner import ContainerStartError
from grader_audit.core.grader_assets import hash_grader_assets
from grader_audit.core.hashing import sha256_file
from grader_audit.core.models import TaskManifest
from grader_audit.core.outcomes import (
    ErrorInfo,
    OracleOutcome,
    OutcomeStatus,
    TestEvidence,
)
from grader_audit.core.process import Runner
from grader_audit.core.reason_codes import ReasonCode, serialize_reason_codes
from grader_audit.grading.v1.evidence import evaluate_evidence, load_report
from grader_audit.grading.v1.suite import run_test_suite

_ORACLE_ROOT = "/opt/oracle"
_RUNNER_ARGV = ["/usr/local/bin/python", "-I", "/opt/grader/run_pytest.py", "/opt/oracle"]


@dataclass
class OracleContext:
    manifest: TaskManifest
    workspace_host: Path
    oracle_tests_host: Path
    expected_oracle_assets_hash: str
    image: str
    memory_mb: int
    pids_limit: int


class OracleEvaluator:
    def evaluate(self, context: OracleContext, runner: Runner) -> OracleOutcome:
        manifest = context.manifest
        pre_asset_hash = hash_grader_assets(context.oracle_tests_host)
        if pre_asset_hash != context.expected_oracle_assets_hash:
            return OracleOutcome(
                status=OutcomeStatus.INFRASTRUCTURE_ERROR,
                passed=False,
                reason_codes=[ReasonCode.GRADER_ASSET_HASH_MISMATCH.value],
                error=ErrorInfo(
                    code="grader_asset_hash_mismatch",
                    message="oracle assets do not match the trusted hash",
                ),
            )

        try:
            suite = run_test_suite(
                workspace_host=context.workspace_host,
                grader_root=_ORACLE_ROOT,
                tests_host=context.oracle_tests_host,
                expected_nodeids=manifest.grading.oracle.expected_nodeids,
                source_roots=manifest.workspace.source_roots,
                image=context.image,
                memory_mb=context.memory_mb,
                pids_limit=context.pids_limit,
                timeout_seconds=manifest.runtime.command_timeout_seconds,
                runner=runner,
            )
        except ContainerStartError as exc:
            return OracleOutcome(
                status=OutcomeStatus.INFRASTRUCTURE_ERROR,
                passed=False,
                reason_codes=[ReasonCode.ENVIRONMENT_SETUP_FAILED.value],
                error=ErrorInfo(code="environment_setup_failed", message=str(exc)),
            )

        if suite.result.timed_out:
            return OracleOutcome(
                status=OutcomeStatus.COMPLETED,
                passed=False,
                reason_codes=[ReasonCode.TIMEOUT.value],
                test_evidence=TestEvidence(state="missing"),
            )

        try:
            parsed = load_report(suite.report_path)
        except ValueError:
            return OracleOutcome(
                status=OutcomeStatus.COMPLETED,
                passed=False,
                reason_codes=[ReasonCode.TEST_EVIDENCE_MISSING.value],
                test_evidence=TestEvidence(state="missing"),
            )

        report_hash = sha256_file(suite.report_path) if suite.report_path.is_file() else None
        reasons, evidence = evaluate_evidence(
            manifest.grading.oracle.expected_nodeids,
            parsed,
        )
        evidence.report_sha256 = report_hash
        passed = not reasons and parsed.exitcode == 0
        status = OutcomeStatus.COMPLETED
        return OracleOutcome(
            status=status,
            passed=passed,
            reason_codes=serialize_reason_codes(reasons),
            test_evidence=evidence,
            node_outcomes=dict(parsed.node_outcomes),
        )
