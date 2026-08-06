"""Hardened v1 exact outcome-row tests (Sections 27.12, 27.19).

Every row of the Section 27.12 hardened mapping is exercised without Docker: a
fake ``Runner`` substitutes the container, writing the JSON report into the
grader-controlled evidence directory exactly as the immutable in-container
runner would. Scope rows short-circuit before the runner is called.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from grader_audit.core.docker_runner import ContainerStartError
from grader_audit.core.grader_assets import hash_grader_assets
from grader_audit.core.manifests import LoadedPatch, LoadedTask, discover_patches, load_task
from grader_audit.core.models import PatchSplit
from grader_audit.core.outcomes import EvaluationOutcome, OutcomeStatus
from grader_audit.core.process import CommandSpec, Mount, ProcessResult
from grader_audit.core.workspace import Workspace, WorkspaceManager
from grader_audit.grading.v1.evaluator import HardenedV1Context, HardenedV1Evaluator
from tests.conftest import FIXTURES_DIR

EXPECTED_NODEIDS = [
    "test_normalize.py::test_normalize_tab",
    "test_normalize.py::test_normalize_newline",
    "test_normalize.py::test_normalize_spaces",
    "test_normalize.py::test_normalize_empty",
    "test_normalize.py::test_normalize_single",
    "test_normalize.py::test_normalize_nbsp",
    "test_optional.py::test_underline_basic",
    "test_optional.py::test_underline_empty",
]


class ReportRunner:
    """Writes a report into the evidence mount, then returns a scripted result.

    The in-container runner writes ``report.json`` into the ``/tmp/evidence``
    mount; this fake mirrors that by writing into the corresponding host path
    found in the mount list.
    """

    def __init__(
        self,
        result: ProcessResult,
        report_payload: dict[str, object] | None = None,
    ) -> None:
        self._result = result
        self._report_payload = report_payload

    def run(
        self,
        spec: CommandSpec,
        *,
        mounts: Sequence[Mount],
        image: str,
        memory_mb: int,
        pids_limit: int,
    ) -> ProcessResult:
        del spec, image, memory_mb, pids_limit
        if self._report_payload is not None:
            evidence_host = next(
                mount.host_path
                for mount in mounts
                if mount.container_path == "/tmp/evidence"
            )
            (evidence_host / "report.json").write_text(
                json.dumps(self._report_payload), encoding="utf-8"
            )
        return self._result


class RaisingRunner:
    def __init__(self, error: BaseException) -> None:
        self._error = error

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
        raise self._error


def _task() -> LoadedTask:
    return load_task(FIXTURES_DIR / "fixture-stringutil")


def _patch(task: LoadedTask, patch_id: str) -> LoadedPatch:
    for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
        if patch.manifest.id == patch_id:
            return patch
    raise AssertionError(f"patch {patch_id} not found")


@pytest.fixture(scope="module")
def task() -> LoadedTask:
    return _task()


@pytest.fixture(scope="module")
def expected_assets_hash(task: LoadedTask) -> str:
    return hash_grader_assets(task.task_dir / task.manifest.grading.hardened_v1.tests_dir)


def _patched_workspace(task: LoadedTask, patch_id: str) -> tuple[Workspace, WorkspaceManager]:
    manager = WorkspaceManager(task)
    workspace = manager.materialize()
    result = manager.apply_patch_to(workspace, _patch(task, patch_id))
    assert result.ok, result.error
    return workspace, manager


def _context(
    task: LoadedTask,
    workspace: Workspace,
    expected_hash: str,
) -> HardenedV1Context:
    return HardenedV1Context(
        manifest=task.manifest,
        workspace_host=workspace.root,
        pristine_snapshot=workspace.pristine_snapshot,
        pre_grade_snapshot=workspace.snapshot(),
        authoritative_tests_host=task.task_dir / task.manifest.grading.hardened_v1.tests_dir,
        expected_grader_assets_hash=expected_hash,
        image="test-image",
        memory_mb=task.manifest.runtime.memory_mb,
        pids_limit=task.manifest.runtime.pids_limit,
    )


def _report(tests: list[tuple[str, str]], exitcode: int = 0) -> dict[str, object]:
    return {
        "exitcode": exitcode,
        "tests": [{"nodeid": nodeid, "outcome": outcome} for nodeid, outcome in tests],
    }


def _passing_report() -> dict[str, object]:
    return _report([(f"tests/{nodeid}", "passed") for nodeid in EXPECTED_NODEIDS])


def _result(exit_code: int | None, *, timed_out: bool = False) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        timed_out=timed_out,
        stdout=b"",
        stderr=b"",
        duration_seconds=0.1,
    )


def _completed(result: EvaluationOutcome) -> EvaluationOutcome:
    assert result.status is OutcomeStatus.COMPLETED
    return result


def test_pre_asset_hash_mismatch_is_infrastructure(
    task: LoadedTask, expected_assets_hash: str
) -> None:
    workspace, manager = _patched_workspace(task, "gold")
    try:
        runner = RaisingRunner(AssertionError("runner must not be called on asset mismatch"))
        context = _context(task, workspace, "0" * 64)
        result = HardenedV1Evaluator().evaluate(context, runner)
        assert result.outcome.status is OutcomeStatus.INFRASTRUCTURE_ERROR
        assert result.outcome.reward is None
        assert result.outcome.reason_codes == ["grader_asset_hash_mismatch"]
    finally:
        manager.finalize_and_destroy(workspace)


def test_immutable_path_modified_short_circuits(
    task: LoadedTask, expected_assets_hash: str
) -> None:
    workspace, manager = _patched_workspace(task, "immutable-edit")
    try:
        runner = RaisingRunner(AssertionError("scope failure must short-circuit tests"))
        context = _context(task, workspace, expected_assets_hash)
        result = HardenedV1Evaluator().evaluate(context, runner)
        outcome = _completed(result.outcome)
        assert outcome.reward == 0.0
        assert "immutable_path_modified" in outcome.reason_codes
        assert outcome.test_evidence is not None
        assert outcome.test_evidence.state == "not_run"
        assert ".grader/tamper.txt" in outcome.changes.immutable_violations
    finally:
        manager.finalize_and_destroy(workspace)


def test_outside_editable_scope_rejected(
    task: LoadedTask, expected_assets_hash: str
) -> None:
    workspace, manager = _patched_workspace(task, "outside-editable-edit")
    try:
        runner = RaisingRunner(AssertionError("scope failure must short-circuit tests"))
        context = _context(task, workspace, expected_assets_hash)
        result = HardenedV1Evaluator().evaluate(context, runner)
        outcome = _completed(result.outcome)
        assert outcome.reward == 0.0
        assert outcome.reason_codes == ["outside_editable_scope"]
        assert outcome.test_evidence is not None
        assert outcome.test_evidence.state == "not_run"
    finally:
        manager.finalize_and_destroy(workspace)


def test_all_mandatory_rows_pass_earn_reward_one(
    task: LoadedTask, expected_assets_hash: str
) -> None:
    workspace, manager = _patched_workspace(task, "gold")
    try:
        runner = ReportRunner(
            _result(0),
            report_payload=_passing_report(),
        )
        context = _context(task, workspace, expected_assets_hash)
        result = HardenedV1Evaluator().evaluate(context, runner)
        outcome = _completed(result.outcome)
        assert outcome.reward == 1.0
        assert outcome.reason_codes == []
        assert outcome.test_evidence is not None
        assert outcome.test_evidence.passed == 8
        assert outcome.test_evidence.collected_nodeids == sorted(EXPECTED_NODEIDS)
    finally:
        manager.finalize_and_destroy(workspace)


def test_timeout_maps_to_completed_timeout(task: LoadedTask, expected_assets_hash: str) -> None:
    workspace, manager = _patched_workspace(task, "gold")
    try:
        runner = ReportRunner(
            _result(None, timed_out=True)
        )
        context = _context(task, workspace, expected_assets_hash)
        result = HardenedV1Evaluator().evaluate(context, runner)
        outcome = _completed(result.outcome)
        assert outcome.reward == 0.0
        assert outcome.reason_codes == ["timeout"]
    finally:
        manager.finalize_and_destroy(workspace)


def test_missing_report_maps_to_test_evidence_missing(
    task: LoadedTask, expected_assets_hash: str
) -> None:
    workspace, manager = _patched_workspace(task, "gold")
    try:
        runner = ReportRunner(
            _result(0)
        )
        context = _context(task, workspace, expected_assets_hash)
        result = HardenedV1Evaluator().evaluate(context, runner)
        outcome = _completed(result.outcome)
        assert outcome.reward == 0.0
        assert outcome.reason_codes == ["test_evidence_missing"]
        assert outcome.test_evidence is not None
        assert outcome.test_evidence.state == "missing"
    finally:
        manager.finalize_and_destroy(workspace)


def test_malformed_report_maps_to_test_evidence_missing(
    task: LoadedTask, expected_assets_hash: str
) -> None:
    workspace, manager = _patched_workspace(task, "gold")
    try:
        runner = ReportRunner(
            _result(1),
            report_payload={"not": "a report"},
        )
        context = _context(task, workspace, expected_assets_hash)
        result = HardenedV1Evaluator().evaluate(context, runner)
        outcome = _completed(result.outcome)
        assert outcome.reward == 0.0
        assert "test_evidence_missing" in outcome.reason_codes
    finally:
        manager.finalize_and_destroy(workspace)


def test_no_tests_collected_maps_to_no_tests_collected(
    task: LoadedTask, expected_assets_hash: str
) -> None:
    workspace, manager = _patched_workspace(task, "gold")
    try:
        runner = ReportRunner(
            _result(0),
            report_payload=_report([]),
        )
        context = _context(task, workspace, expected_assets_hash)
        result = HardenedV1Evaluator().evaluate(context, runner)
        outcome = _completed(result.outcome)
        assert outcome.reward == 0.0
        assert "no_tests_collected" in outcome.reason_codes
        assert "test_identity_mismatch" in outcome.reason_codes
    finally:
        manager.finalize_and_destroy(workspace)


def test_node_id_mismatch_maps_to_test_identity_mismatch(
    task: LoadedTask, expected_assets_hash: str
) -> None:
    workspace, manager = _patched_workspace(task, "gold")
    try:
        extra = [*EXPECTED_NODEIDS[:-1], "test_optional.py::test_EXTRA"]
        runner = ReportRunner(
            _result(0),
            report_payload=_report([(f"tests/{nodeid}", "passed") for nodeid in extra]),
        )
        context = _context(task, workspace, expected_assets_hash)
        result = HardenedV1Evaluator().evaluate(context, runner)
        outcome = _completed(result.outcome)
        assert outcome.reward == 0.0
        assert "test_identity_mismatch" in outcome.reason_codes
        assert "authoritative_tests_failed" in outcome.reason_codes
    finally:
        manager.finalize_and_destroy(workspace)


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        ("failed", "authoritative_tests_failed"),
        ("error", "authoritative_tests_failed"),
        ("skipped", "authoritative_tests_failed"),
        ("xfailed", "authoritative_tests_failed"),
        ("xpassed", "authoritative_tests_failed"),
    ],
)
def test_non_passed_authoritative_outcome_rejected(
    task: LoadedTask, expected_assets_hash: str, outcome: str, reason: str
) -> None:
    workspace, manager = _patched_workspace(task, "gold")
    try:
        tests = [(f"tests/{nodeid}", "passed") for nodeid in EXPECTED_NODEIDS]
        tests[0] = (tests[0][0], outcome)
        runner = ReportRunner(
            _result(1),
            report_payload=_report(tests, exitcode=1),
        )
        context = _context(task, workspace, expected_assets_hash)
        result = HardenedV1Evaluator().evaluate(context, runner)
        graded = _completed(result.outcome)
        assert graded.reward == 0.0
        assert reason in graded.reason_codes
    finally:
        manager.finalize_and_destroy(workspace)


def test_nonzero_process_exit_with_valid_report_is_still_a_failure(
    task: LoadedTask, expected_assets_hash: str
) -> None:
    workspace, manager = _patched_workspace(task, "gold")
    try:
        runner = ReportRunner(
            _result(1),
            report_payload=_passing_report(),
        )
        context = _context(task, workspace, expected_assets_hash)
        result = HardenedV1Evaluator().evaluate(context, runner)
        outcome = _completed(result.outcome)
        assert outcome.reward == 0.0
        assert "authoritative_tests_failed" in outcome.reason_codes
    finally:
        manager.finalize_and_destroy(workspace)


def test_container_start_error_is_infrastructure(
    task: LoadedTask, expected_assets_hash: str
) -> None:
    workspace, manager = _patched_workspace(task, "gold")
    try:
        runner = RaisingRunner(ContainerStartError("cannot start container"))
        context = _context(task, workspace, expected_assets_hash)
        result = HardenedV1Evaluator().evaluate(context, runner)
        assert result.outcome.status is OutcomeStatus.INFRASTRUCTURE_ERROR
        assert result.outcome.reason_codes == ["environment_setup_failed"]
        assert result.outcome.reward is None
    finally:
        manager.finalize_and_destroy(workspace)


def test_internal_grader_error_is_infrastructure(
    task: LoadedTask, expected_assets_hash: str
) -> None:
    workspace, manager = _patched_workspace(task, "gold")
    try:
        runner = RaisingRunner(ValueError("unexpected grader fault"))
        context = _context(task, workspace, expected_assets_hash)
        result = HardenedV1Evaluator().evaluate(context, runner)
        assert result.outcome.status is OutcomeStatus.INFRASTRUCTURE_ERROR
        assert result.outcome.reason_codes == ["internal_grader_error"]
        assert result.outcome.error is not None
        assert "unexpected grader fault" in result.outcome.error.message
    finally:
        manager.finalize_and_destroy(workspace)

