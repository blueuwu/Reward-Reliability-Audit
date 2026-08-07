"""Semantic evidence models for hardened v2 (hardening §6).

Every v2 semantic run records the seed, generator version, mechanism list,
expected/collected node IDs, outcome counts, the generated-suite SHA-256, the
report SHA-256, and the failing cases with messages, so any failed case is
independently replayable (``grader-v2 replay``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import Field

from grader_audit.core.base import StrictModel
from grader_audit.core.hashing import sha256_file
from grader_v2.jsonutil import as_dict


class SemanticCaseResult(StrictModel):
    """One failed/errored case from the semantic suite."""

    nodeid: str
    outcome: str
    message: str


def _default_failed_cases() -> list[SemanticCaseResult]:
    return []


class SemanticEvidence(StrictModel):
    """Structured evidence of one seeded semantic-suite run."""

    schema_version: str = "1.0"
    profile_id: str
    generator_version: str
    seed: int
    mechanisms: list[str]
    case_count: int
    suite_sha256: str
    report_sha256: str | None = None
    expected_nodeids: list[str]
    collected_nodeids: list[str]
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failed_cases: list[SemanticCaseResult] = Field(default_factory=_default_failed_cases)

    @property
    def ok(self) -> bool:
        """All expected nodes collected, none failed/errored/skipped."""
        return (
            set(self.collected_nodeids) == set(self.expected_nodeids)
            and self.failed == 0
            and self.errors == 0
            and self.skipped == 0
        )

    @property
    def collection_mismatch(self) -> bool:
        return set(self.collected_nodeids) != set(self.expected_nodeids)


def parse_semantic_report(
    report_path: Path,
    *,
    profile_id: str,
    generator_version: str,
    seed: int,
    mechanisms: list[str],
    expected_nodeids: list[str],
    suite_sha256: str,
) -> SemanticEvidence:
    """Parse a pytest-json-report ``report.json`` into :class:`SemanticEvidence`."""
    expected = sorted(set(expected_nodeids))
    evidence = SemanticEvidence(
        profile_id=profile_id,
        generator_version=generator_version,
        seed=seed,
        mechanisms=list(mechanisms),
        case_count=len(expected),
        suite_sha256=suite_sha256,
        expected_nodeids=expected,
        collected_nodeids=[],
    )
    if not report_path.is_file():
        return evidence
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    report = cast(dict[str, object], payload) if isinstance(payload, dict) else {}
    evidence.report_sha256 = sha256_file(report_path)
    tests_value = report.get("tests")
    if not isinstance(tests_value, list):
        return evidence
    collected: set[str] = set()
    for entry in cast(list[object], tests_value):
        entry_map = as_dict(entry)
        if not entry_map:
            continue
        raw = entry_map.get("nodeid")
        nodeid = str(raw) if isinstance(raw, str) else ""
        if not nodeid:
            continue
        nodeid = _normalize_nodeid(nodeid)
        collected.add(nodeid)
        outcome = str(entry_map.get("outcome", ""))
        if outcome == "passed":
            evidence.passed += 1
        elif outcome == "failed":
            evidence.failed += 1
            evidence.failed_cases.append(
                SemanticCaseResult(
                    nodeid=nodeid,
                    outcome=outcome,
                    message=_failure_message(entry_map),
                )
            )
        elif outcome == "error":
            evidence.errors += 1
            evidence.failed_cases.append(
                SemanticCaseResult(
                    nodeid=nodeid,
                    outcome=outcome,
                    message=_failure_message(entry_map),
                )
            )
        elif outcome == "skipped":
            evidence.skipped += 1
    evidence.collected_nodeids = sorted(collected)
    return evidence


def _normalize_nodeid(nodeid: str) -> str:
    """Strip the ``tests/`` prefix the runner's node IDs carry (run_pytest.py)."""
    if "::" in nodeid:
        file_part, suffix = nodeid.split("::", 1)
    else:
        file_part, suffix = nodeid, ""
    file_part = file_part.replace("\\", "/")
    if file_part.startswith("tests/"):
        file_part = file_part[len("tests/") :]
    return f"{file_part}::{suffix}" if suffix else file_part


def _failure_message(entry: dict[str, object]) -> str:
    for key in ("longrepr", "call", "setup"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value[:4000]
        if isinstance(value, dict):
            value_map = cast(dict[str, object], value)
            crash = value_map.get("crash")
            if isinstance(crash, dict):
                crash_map = cast(dict[str, object], crash)
                message = crash_map.get("message")
                if isinstance(message, str) and message:
                    return message[:4000]
            text = value_map.get("traceback")
            if isinstance(text, str) and text:
                return text[:4000]
    return "no failure detail recorded"
