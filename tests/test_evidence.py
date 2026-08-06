"""Evidence parsing and fail-closed reason mapping (Sections 27.11, 27.12, 27.19)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grader_audit.core.reason_codes import ReasonCode
from grader_audit.grading.v1.evidence import (
    PytestJsonReport,
    ReportTest,
    evaluate_evidence,
    load_report,
    normalize_nodeid,
    parse_report,
)

EXPECTED = [
    "test_normalize.py::test_normalize_tab",
    "test_normalize.py::test_normalize_newline",
    "test_normalize.py::test_normalize_spaces",
]


def _report(tests: list[tuple[str, str]], exitcode: int = 0) -> PytestJsonReport:
    return PytestJsonReport(
        exitcode=exitcode, tests=[ReportTest(nodeid=n, outcome=o) for n, o in tests]
    )


def test_normalize_nodeid_strips_suite_prefix() -> None:
    assert normalize_nodeid("tests/test_normalize.py::test_x[1]") == "test_normalize.py::test_x[1]"
    assert normalize_nodeid("test_normalize.py::test_x") == "test_normalize.py::test_x"


def test_parse_report_normalizes_and_sorts() -> None:
    report = _report(
        [
            ("tests/test_normalize.py::test_normalize_spaces", "passed"),
            ("tests/test_normalize.py::test_normalize_tab", "passed"),
            ("tests/test_normalize.py::test_normalize_newline", "passed"),
        ]
    )
    parsed = parse_report(report)
    assert parsed.collected_nodeids == sorted(EXPECTED)
    assert parsed.passed == 3
    assert parsed.duplicate_nodeids == []


def test_parse_report_duplicates_detected() -> None:
    report = _report([("tests/test_normalize.py::test_x", "passed")] * 2)
    parsed = parse_report(report)
    assert parsed.duplicate_nodeids == ["test_normalize.py::test_x"]


def test_evaluate_evidence_all_pass_no_reasons() -> None:
    report = _report(
        [
            ("tests/test_normalize.py::test_normalize_tab", "passed"),
            ("tests/test_normalize.py::test_normalize_newline", "passed"),
            ("tests/test_normalize.py::test_normalize_spaces", "passed"),
        ]
    )
    parsed = parse_report(report)
    reasons, evidence = evaluate_evidence(EXPECTED, parsed)
    assert reasons == []
    assert evidence.state == "complete"
    assert evidence.passed == 3


@pytest.mark.parametrize(
    ("outcomes", "expected_reason"),
    [
        ([("tests/test_normalize.py::test_tab", "failed")], ReasonCode.AUTHORITATIVE_TESTS_FAILED),
        ([("tests/test_normalize.py::test_tab", "error")], ReasonCode.AUTHORITATIVE_TESTS_FAILED),
        ([("tests/test_normalize.py::test_tab", "skipped")], ReasonCode.AUTHORITATIVE_TESTS_FAILED),
        ([("tests/test_normalize.py::test_tab", "xfailed")], ReasonCode.AUTHORITATIVE_TESTS_FAILED),
        ([("tests/test_normalize.py::test_tab", "xpassed")], ReasonCode.AUTHORITATIVE_TESTS_FAILED),
    ],
)
def test_evaluate_evidence_rejects_non_passed_outcomes(
    outcomes: list[tuple[str, str]], expected_reason: ReasonCode
) -> None:
    report = _report(outcomes)
    parsed = parse_report(report)
    reasons, _ = evaluate_evidence(EXPECTED, parsed)
    assert expected_reason in reasons


def test_evaluate_evidence_missing_test_rejected() -> None:
    report = _report([("tests/test_normalize.py::test_tab", "passed")])
    parsed = parse_report(report)
    reasons, _ = evaluate_evidence(EXPECTED, parsed)
    assert ReasonCode.AUTHORITATIVE_TESTS_FAILED in reasons


def test_evaluate_evidence_no_tests_collected() -> None:
    report = _report([])
    parsed = parse_report(report)
    reasons, _ = evaluate_evidence(EXPECTED, parsed)
    assert ReasonCode.NO_TESTS_COLLECTED in reasons
    assert ReasonCode.TEST_IDENTITY_MISMATCH in reasons


def test_evaluate_evidence_node_id_mismatch() -> None:
    report = _report(
        [
            ("tests/test_normalize.py::test_tab", "passed"),
            ("tests/test_normalize.py::test_newline", "passed"),
            ("tests/test_normalize.py::test_EXTRA", "passed"),
        ]
    )
    parsed = parse_report(report)
    reasons, _ = evaluate_evidence(EXPECTED, parsed)
    assert ReasonCode.TEST_IDENTITY_MISMATCH in reasons


def test_evaluate_evidence_nonzero_exit_rejected() -> None:
    report = _report(
        [
            ("tests/test_normalize.py::test_tab", "passed"),
            ("tests/test_normalize.py::test_newline", "passed"),
            ("tests/test_normalize.py::test_spaces", "passed"),
        ],
        exitcode=1,
    )
    parsed = parse_report(report)
    reasons, _ = evaluate_evidence(EXPECTED, parsed)
    assert ReasonCode.AUTHORITATIVE_TESTS_FAILED in reasons


def test_load_report_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="report file missing"):
        load_report(tmp_path / "missing.json")


def test_load_report_malformed_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text("not json {", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_report(path)


def test_load_report_wrong_schema_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text('{"exitcode": 0}', encoding="utf-8")
    with pytest.raises(ValueError, match="expected schema"):
        load_report(path)


def test_load_report_rejects_duplicate_nodeids(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    payload = (
        '{"exitcode": 0, "tests": ['
        '{"nodeid": "tests/test_normalize.py::test_x", "outcome": "passed"},'
        '{"nodeid": "tests/test_normalize.py::test_x", "outcome": "passed"}]}'
    )
    path.write_text(payload, encoding="utf-8")
    parsed = load_report(path)
    assert parsed.duplicate_nodeids == ["test_normalize.py::test_x"]
