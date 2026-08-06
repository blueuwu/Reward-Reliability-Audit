"""Parsing and validation of the immutable pytest JSON report (Section 27.11).

Reported node IDs are normalized by removing the authoritative-suite prefix,
converting path separators to ``/``, and retaining the complete ``::`` suffix
including parameter IDs. Duplicate normalized IDs are rejected. Acceptance
compares node IDs as sets and stores them sorted for deterministic records.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ConfigDict, model_validator

from grader_audit.core.base import StrictModel
from grader_audit.core.outcomes import TestEvidence
from grader_audit.core.reason_codes import ReasonCode

_COLLECTION_COUNT_PATTERN = re.compile(r"(\d+)\s+collected")
_PASSED_PATTERN = re.compile(r"(\d+)\s+passed")

_OUTCOMES = ("passed", "failed", "error", "skipped", "xfailed", "xpassed")


class ReportTest(StrictModel):
    model_config = ConfigDict(extra="allow")

    nodeid: str
    outcome: str = "error"

    @model_validator(mode="after")
    def check_outcome(self) -> ReportTest:
        if self.outcome not in _OUTCOMES:
            self.outcome = "error"
        return self


class PytestJsonReport(StrictModel):
    model_config = ConfigDict(extra="allow")

    exitcode: int
    tests: list[ReportTest]


def normalize_nodeid(nodeid: str, suite_dir: str = "tests") -> str:
    """Normalize a reported node ID for exact set comparison."""
    if "::" in nodeid:
        file_part, suffix = nodeid.split("::", 1)
    else:
        file_part, suffix = nodeid, ""
    file_part = file_part.replace("\\", "/")
    file_part = posixpath.normpath(file_part)
    prefix = suite_dir.rstrip("/") + "/"
    if file_part.startswith(prefix):
        file_part = file_part[len(prefix) :]
    normalized = file_part
    if suffix:
        normalized = f"{file_part}::{suffix}"
    return normalized


@dataclass(frozen=True)
class ParsedReport:
    exitcode: int
    collected_nodeids: list[str]
    passed: int
    failed: int
    errors: int
    skipped: int
    xfailed: int
    xpassed: int
    duplicate_nodeids: list[str]
    node_outcomes: dict[str, str]


def parse_report(report: PytestJsonReport, *, suite_dir: str = "tests") -> ParsedReport:
    """Parse a validated report model, normalizing and validating node IDs."""
    counts: dict[str, int] = dict.fromkeys(_OUTCOMES, 0)
    normalized: list[str] = []
    node_outcomes: dict[str, str] = {}
    for entry in report.tests:
        nodeid = normalize_nodeid(entry.nodeid, suite_dir=suite_dir)
        normalized.append(nodeid)
        counts[entry.outcome] = counts.get(entry.outcome, 0) + 1
        node_outcomes[nodeid] = entry.outcome

    seen: set[str] = set()
    duplicates: list[str] = []
    for nodeid in normalized:
        if nodeid in seen and nodeid not in duplicates:
            duplicates.append(nodeid)
        seen.add(nodeid)

    return ParsedReport(
        exitcode=report.exitcode,
        collected_nodeids=sorted(set(normalized)),
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["error"],
        skipped=counts["skipped"],
        xfailed=counts["xfailed"],
        xpassed=counts["xpassed"],
        duplicate_nodeids=duplicates,
        node_outcomes=node_outcomes,
    )


def load_report(path: Path, *, suite_dir: str = "tests") -> ParsedReport:
    """Load and parse the JSON report from a grader-controlled path.

    Raises ``ValueError`` when the report is missing or malformed so the
    evaluator can fail closed with ``test_evidence_missing``.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ValueError("report file missing") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"report is not valid JSON: {exc}") from exc
    try:
        report = PytestJsonReport.model_validate(data)
    except Exception as exc:
        raise ValueError(f"report does not match the expected schema: {exc}") from exc
    return parse_report(report, suite_dir=suite_dir)


def evaluate_evidence(
    expected_nodeids: list[str],
    parsed: ParsedReport,
    *,
    suite_dir: str = "tests",
) -> tuple[list[ReasonCode], TestEvidence]:
    """Return the applicable hardened reasons and evidence (Section 27.12).

    Node-ID and outcome checks are exact: the collected set must equal the
    expected set, every expected test must be reported ``passed`` exactly once,
    and there must be zero failures/errors/skips/xfails/xpasses.
    """
    del suite_dir
    expected = set(expected_nodeids)
    reasons: list[ReasonCode] = []

    if parsed.duplicate_nodeids:
        reasons.append(ReasonCode.TEST_IDENTITY_MISMATCH)
    if not parsed.collected_nodeids:
        reasons.append(ReasonCode.NO_TESTS_COLLECTED)
    if set(parsed.collected_nodeids) != expected:
        reasons.append(ReasonCode.TEST_IDENTITY_MISMATCH)
    if parsed.exitcode != 0:
        reasons.append(ReasonCode.AUTHORITATIVE_TESTS_FAILED)
    if any(
        count > 0
        for count in (parsed.failed, parsed.errors, parsed.skipped, parsed.xfailed, parsed.xpassed)
    ):
        reasons.append(ReasonCode.AUTHORITATIVE_TESTS_FAILED)

    passed_counts: dict[str, int] = {}
    for nodeid, outcome in parsed.node_outcomes.items():
        if outcome == "passed":
            passed_counts[nodeid] = passed_counts.get(nodeid, 0) + 1
    missing_or_not_passed = [nodeid for nodeid in expected if passed_counts.get(nodeid, 0) != 1]
    if missing_or_not_passed:
        reasons.append(ReasonCode.AUTHORITATIVE_TESTS_FAILED)

    evidence = TestEvidence(
        state="complete",
        collected_nodeids=parsed.collected_nodeids,
        passed=parsed.passed,
        failed=parsed.failed,
        errors=parsed.errors,
        skipped=parsed.skipped,
        xfailed=parsed.xfailed,
        xpassed=parsed.xpassed,
    )
    return reasons, evidence


def parse_collection_count(output: str) -> int | None:
    """Parse the pytest collection count from stdout for observation only."""
    match = _COLLECTION_COUNT_PATTERN.search(output)
    if match:
        return int(match.group(1))
    match = _PASSED_PATTERN.search(output)
    if match:
        return int(match.group(1))
    return None
