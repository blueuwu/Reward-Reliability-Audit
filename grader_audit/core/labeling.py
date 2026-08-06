"""Independent truth-labeling facility (Sections 27.9 and 27.15).

``label-patches`` runs the offline oracle and the authoritative suite on a
patched workspace from a fresh baseline, stores machine evidence under
``results/labeling/<ID>/<split>/<task_id>/<patch_id>.json``, and writes a draft
annotation. Labels are never derived from either grader's reward; manual
approval is still required before any patch enters a controlled experiment.

The oracle is stronger than either grader and is used only for dataset
curation; its assets are never mounted during naive, hardened, or adaptive runs.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from grader_audit.core.grader_assets import hash_grader_assets
from grader_audit.core.hashing import sha256_file
from grader_audit.core.manifests import LoadedPatch, LoadedTask, discover_patches
from grader_audit.core.models import PatchSplit
from grader_audit.core.outcomes import OracleOutcome, OutcomeStatus, Phase
from grader_audit.core.process import Runner
from grader_audit.core.reason_codes import ReasonCode, serialize_reason_codes
from grader_audit.core.snapshots import classify_patch_changes, diff_snapshots
from grader_audit.core.workspace import Workspace, WorkspaceManager
from grader_audit.grading.v1.evidence import evaluate_evidence, load_report
from grader_audit.grading.v1.suite import run_test_suite
from grader_audit.oracle.evaluator import OracleContext, OracleEvaluator

_GRADER_ROOT = "/opt/grader"


@dataclass(frozen=True)
class LabelingEvidence:
    """The machine evidence written for one labeled patch."""

    schema_version: str
    labeling_id: str
    phase: str
    timestamp_utc: str
    task: Mapping[str, object]
    patch: Mapping[str, object]
    oracle: Mapping[str, object]
    authoritative: Mapping[str, object]
    draft_annotation: Mapping[str, object]

    def to_json_bytes(self) -> bytes:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "labeling_id": self.labeling_id,
            "phase": self.phase,
            "timestamp_utc": self.timestamp_utc,
            "task": dict(self.task),
            "patch": dict(self.patch),
            "oracle": dict(self.oracle),
            "authoritative": dict(self.authoritative),
            "draft_annotation": dict(self.draft_annotation),
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _task_record(task: LoadedTask) -> Mapping[str, object]:
    return {
        "id": task.manifest.id,
        "split": task.manifest.split.value,
        "manifest_sha256": task.manifest_sha256,
    }


def _patch_record(patch: LoadedPatch) -> Mapping[str, object]:
    return {
        "id": patch.manifest.id,
        "label": patch.manifest.label.value,
        "subtype": patch.manifest.subtype.value,
        "attack_family": patch.manifest.attack_family,
        "metadata_sha256": patch.metadata_sha256,
        "diff_sha256": patch.diff_sha256,
    }


def _oracle_record(outcome: OracleOutcome) -> Mapping[str, object]:
    evidence = outcome.test_evidence
    report_hash = evidence.report_sha256 if evidence is not None else None
    return {
        "status": outcome.status.value,
        "passed": outcome.passed,
        "reason_codes": outcome.reason_codes,
        "error": None if outcome.error is None else outcome.error.model_dump(mode="json"),
        "node_outcomes": outcome.node_outcomes,
        "report_sha256": report_hash,
    }


def draft_disposition(
    patch: LoadedPatch, oracle: Mapping[str, object], auth: Mapping[str, object]
) -> Mapping[str, object]:
    """Draft a manual-approval annotation from independent oracle evidence.

    A valid patch is confirmed only when the offline oracle passes; an invalid
    patch is confirmed only when the oracle fails (or a hard scope violation
    exists). Any inconsistency or infrastructure issue is marked for review and
    never enters primary metrics without manual approval.
    """
    oracle_status = cast(str, oracle["status"])
    oracle_passed = bool(oracle["passed"])
    auth_accepted = auth["accepted"]
    oracle_reasons = cast(list[str], oracle["reason_codes"])
    auth_reasons = cast(list[str], auth["reason_codes"])

    if oracle_status == OutcomeStatus.INFRASTRUCTURE_ERROR.value:
        disposition = "ambiguous"
        reason = "oracle infrastructure error; manual review required"
    elif patch.manifest.label.value == "valid":
        ok = oracle_passed and auth_accepted is True and not auth_reasons
        disposition = "confirmed" if ok else "relabel_required"
        reason = (
            "oracle and authoritative suites pass"
            if ok
            else f"valid label but evidence conflicts (oracle_passed={oracle_passed}, "
            f"authoritative_accepted={auth_accepted}, reasons={oracle_reasons + auth_reasons})"
        )
    else:
        ok = (not oracle_passed) or (auth_accepted is not True) or bool(auth_reasons)
        disposition = "confirmed" if ok else "relabel_required"
        reason = (
            f"oracle fails ({oracle_reasons}) or authoritative fails ({auth_reasons})"
            if ok
            else (
                "invalid label but oracle and authoritative suites both pass; "
                "manual review required"
            )
        )
    return {
        "disposition": disposition,
        "truth_label": patch.manifest.label.value,
        "reason": reason,
        "requires_manual_approval": True,
    }


def _run_authoritative_for_labeling(
    task: LoadedTask, workspace: Workspace, image: str, runner: Runner
) -> Mapping[str, object]:
    """Run the authoritative suite on *workspace* (labeling only, never a grader)."""
    manifest = task.manifest
    authoritative_host = task.task_dir / manifest.grading.hardened_v1.tests_dir
    suite = run_test_suite(
        workspace_host=workspace.root,
        grader_root=_GRADER_ROOT,
        tests_host=authoritative_host,
        expected_nodeids=manifest.grading.hardened_v1.expected_nodeids,
        source_roots=manifest.workspace.source_roots,
        image=image,
        memory_mb=manifest.runtime.memory_mb,
        pids_limit=manifest.runtime.pids_limit,
        timeout_seconds=manifest.grading.hardened_v1.timeout_seconds,
        runner=runner,
    )
    try:
        parsed = load_report(suite.report_path)
    except ValueError:
        shutil.rmtree(suite.evidence_dir, ignore_errors=True)
        return {
            "status": OutcomeStatus.COMPLETED.value,
            "accepted": False,
            "reason_codes": ["test_evidence_missing"],
            "error": None,
            "node_outcomes": {},
            "report_sha256": None,
        }
    reasons, _evidence = evaluate_evidence(manifest.grading.hardened_v1.expected_nodeids, parsed)
    report_hash = sha256_file(suite.report_path) if suite.report_path.is_file() else None
    if suite.result.exit_code != 0:
        reasons = [ReasonCode.AUTHORITATIVE_TESTS_FAILED, *reasons]
    accepted = not reasons and suite.result.exit_code == 0
    shutil.rmtree(suite.evidence_dir, ignore_errors=True)
    return {
        "status": OutcomeStatus.COMPLETED.value,
        "accepted": accepted,
        "reason_codes": serialize_reason_codes(reasons),
        "error": None,
        "node_outcomes": dict(parsed.node_outcomes),
        "report_sha256": report_hash,
    }


def _invalid_input_evidence(
    labeling_id: str, task: LoadedTask, patch: LoadedPatch, message: str
) -> LabelingEvidence:
    return LabelingEvidence(
        schema_version="1.0",
        labeling_id=labeling_id,
        phase=Phase.LABELING.value,
        timestamp_utc=_utc_now(),
        task=_task_record(task),
        patch=_patch_record(patch),
        oracle={
            "status": OutcomeStatus.INVALID_INPUT.value,
            "passed": False,
            "reason_codes": ["patch_apply_failed"],
            "error": {"code": "patch_apply_failed", "message": message},
            "node_outcomes": {},
            "report_sha256": None,
        },
        authoritative={
            "status": OutcomeStatus.INVALID_INPUT.value,
            "accepted": None,
            "reason_codes": ["patch_apply_failed"],
            "error": {"code": "patch_apply_failed", "message": message},
            "node_outcomes": {},
            "report_sha256": None,
        },
        draft_annotation={
            "disposition": "ambiguous",
            "truth_label": patch.manifest.label.value,
            "reason": "patch does not apply; dataset input invalid",
            "requires_manual_approval": True,
        },
    )


def label_patch(
    task: LoadedTask,
    patch: LoadedPatch,
    *,
    runner: Runner,
    image: str,
    labeling_id: str,
    oracle_hash: str,
) -> LabelingEvidence:
    """Label one patch from a fresh baseline with oracle + authoritative evidence."""
    manager = WorkspaceManager(task)
    workspace = manager.materialize()
    try:
        apply_result = manager.apply_patch_to(workspace, patch)
        if not apply_result.ok:
            return _invalid_input_evidence(
                labeling_id, task, patch, apply_result.error or "git apply failed"
            )

        pre_grade = workspace.snapshot()
        scope = classify_patch_changes(
            task.manifest, diff_snapshots(workspace.pristine_snapshot, pre_grade)
        )
        oracle_context = OracleContext(
            manifest=task.manifest,
            workspace_host=workspace.root,
            oracle_tests_host=task.task_dir / task.manifest.grading.oracle.tests_dir,
            expected_oracle_assets_hash=oracle_hash,
            image=image,
            memory_mb=task.manifest.runtime.memory_mb,
            pids_limit=task.manifest.runtime.pids_limit,
        )
        oracle_outcome = OracleEvaluator().evaluate(oracle_context, runner)
        auth = _run_authoritative_for_labeling(task, workspace, image, runner)
        draft = draft_disposition(patch, _oracle_record(oracle_outcome), auth)
        scope_violations = scope.immutable_violations + scope.outside_editable_scope
        if scope_violations:
            draft = {
                "disposition": (
                    "confirmed" if patch.manifest.label.value == "invalid" else "relabel_required"
                ),
                "truth_label": patch.manifest.label.value,
                "reason": f"hard scope violations: {scope_violations}",
                "requires_manual_approval": True,
            }
        return LabelingEvidence(
            schema_version="1.0",
            labeling_id=labeling_id,
            phase=Phase.LABELING.value,
            timestamp_utc=_utc_now(),
            task=_task_record(task),
            patch=_patch_record(patch),
            oracle=_oracle_record(oracle_outcome),
            authoritative=auth,
            draft_annotation=draft,
        )
    finally:
        manager.finalize_and_destroy(workspace)


def label_task(
    task: LoadedTask,
    *,
    runner: Runner,
    image: str,
    labeling_id: str,
) -> list[tuple[LoadedPatch, LabelingEvidence]]:
    """Label every patch of *task* for its declared split."""
    oracle_hash = hash_grader_assets(task.task_dir / task.manifest.grading.oracle.tests_dir)
    patch_split = (
        PatchSplit.DEVELOPMENT
        if task.manifest.split.value == "development"
        else PatchSplit.FROZEN_EVAL
    )
    results: list[tuple[LoadedPatch, LabelingEvidence]] = []
    for patch in discover_patches(task.task_dir, patch_split):
        evidence = label_patch(
            task,
            patch,
            runner=runner,
            image=image,
            labeling_id=labeling_id,
            oracle_hash=oracle_hash,
        )
        results.append((patch, evidence))
    return results
