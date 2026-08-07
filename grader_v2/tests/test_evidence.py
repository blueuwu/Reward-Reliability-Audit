"""Semantic evidence parsing: malformed reports, mismatch detection, messages."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from grader_v2.grading.evidence import (
    SemanticCaseResult,
    SemanticEvidence,
    parse_semantic_report,
)


def _report(
    entries: Sequence[object],
    *,
    tests_missing: bool = False,
    root_not_object: bool = False,
) -> Path:
    path = Path(__import__("tempfile").mkdtemp(prefix="ga-sem-ev-")) / "report.json"
    if tests_missing:
        path.write_text(json.dumps({"summary": {}}), encoding="utf-8")
    elif root_not_object:
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    else:
        path.write_text(json.dumps({"tests": entries}), encoding="utf-8")
    return path


def _parse(report_path: Path) -> SemanticEvidence:
    return parse_semantic_report(
        report_path,
        profile_id="tinydb-docids-v1",
        generator_version="tinydb-docids-v1@1",
        seed=7,
        mechanisms=["randomized-hidden-inputs"],
        expected_nodeids=["test_semantic_docids.py::a", "test_semantic_docids.py::b"],
        suite_sha256="suite-hash",
    )


def test_missing_report_yields_uncacheable_evidence() -> None:
    evidence = _parse(Path("does-not-exist.json"))
    assert evidence.report_sha256 is None
    assert not evidence.ok
    assert evidence.collection_mismatch
    assert evidence.case_count == 2
    assert evidence.failed_cases == []


def test_root_not_object_is_ignored() -> None:
    evidence = _parse(_report([], root_not_object=True))
    assert evidence.report_sha256 is not None
    assert evidence.collected_nodeids == []
    assert not evidence.ok


def test_missing_tests_key_is_empty_run() -> None:
    evidence = _parse(_report([], tests_missing=True))
    assert evidence.passed == 0
    assert evidence.collection_mismatch


def test_malformed_entries_are_skipped() -> None:
    entries: list[object] = [
        {"nodeid": "test_semantic_docids.py::a", "outcome": "passed"},
        {"nodeid": 42, "outcome": "passed"},
        {"outcome": "passed"},
        {"nodeid": "test_semantic_docids.py::b", "outcome": "passed"},
        "not a dict",
    ]
    evidence = _parse(_report(entries))
    assert evidence.passed == 2
    assert set(evidence.collected_nodeids) == {
        "test_semantic_docids.py::a",
        "test_semantic_docids.py::b",
    }
    assert evidence.ok


def test_tests_prefix_normalization() -> None:
    entries = [
        {"nodeid": "tests/test_semantic_docids.py::a", "outcome": "passed"},
        {"nodeid": "tests\\test_semantic_docids.py::b", "outcome": "passed"},
    ]
    evidence = _parse(_report(entries))
    assert set(evidence.collected_nodeids) == {
        "test_semantic_docids.py::a",
        "test_semantic_docids.py::b",
    }
    assert evidence.ok


def test_failed_and_error_outcomes_recorded_with_messages() -> None:
    entries: list[dict[str, object]] = [
        {"nodeid": "test_semantic_docids.py::a", "outcome": "failed",
         "call": {"crash": {"message": "assert 0 == 1"}}},
        {"nodeid": "test_semantic_docids.py::b", "outcome": "error",
         "longrepr": "NameError: name 'tmp_path' is not defined"},
        {"nodeid": "test_semantic_docids.py::c", "outcome": "skipped"},
    ]
    evidence = _parse(_report(entries))
    assert evidence.failed == 1
    assert evidence.errors == 1
    assert evidence.skipped == 1
    assert not evidence.ok
    messages = {case.nodeid: case.message for case in evidence.failed_cases}
    assert "assert 0 == 1" in messages["test_semantic_docids.py::a"]
    assert "NameError" in messages["test_semantic_docids.py::b"]
    for case in evidence.failed_cases:
        assert isinstance(case, SemanticCaseResult)


def test_failure_message_falls_back_to_traceback() -> None:
    entries: list[dict[str, object]] = [
        {"nodeid": "test_semantic_docids.py::a", "outcome": "failed",
         "call": {"traceback": "tb-line-1\ntb-line-2"}},
    ]
    evidence = _parse(_report(entries))
    assert "tb-line-1" in evidence.failed_cases[0].message


def test_failure_message_without_detail() -> None:
    entries: list[dict[str, object]] = [
        {"nodeid": "test_semantic_docids.py::a", "outcome": "failed", "call": {}},
    ]
    evidence = _parse(_report(entries))
    assert evidence.failed_cases[0].message == "no failure detail recorded"
