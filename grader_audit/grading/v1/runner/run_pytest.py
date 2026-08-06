#!/usr/bin/env python3
"""Immutable in-container pytest runner for hardened v1 and the offline oracle.

Launched as ``/usr/local/bin/python -I /opt/grader/run_pytest.py <root>`` where
``<root>`` is ``/opt/grader`` or ``/opt/oracle``.

The runner:
- sets ``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`` and clears pytest-affecting
  environment variables,
- imports pytest before touching ``sys.path``,
- loads only the pinned report plugin and the immutable grader plugin,
- inserts only manifest-declared workspace source roots into ``sys.path``,
- runs the authoritative test directory by absolute path with an immutable
  config (``-c <root>/pytest.ini --confcutdir <root>``),
- writes evidence under a fresh grader-controlled directory outside
  ``/workspace``,
- emits a machine-readable ``GRADER_SUMMARY <json>`` terminal summary.

Environment (explicit allowlist set by the orchestrator):
  EVIDENCE_DIR      - grader-controlled writable directory for report.json
  WORKSPACE_ROOT    - absolute path of the agent workspace
  SOURCE_ROOTS      - JSON array of workspace-relative source directories
  EXPECTED_NODEIDS  - JSON array of normalized expected node IDs
"""

import json
import os
import sys

GRADER_ROOTS = ("/opt/grader", "/opt/oracle")

_KEPT_ENV = ("PATH", "LANG", "LC_ALL", "HOME", "TMPDIR")


def _normalize_nodeid(nodeid: str, suite_dir: str = "tests") -> str:
    if "::" in nodeid:
        file_part, suffix = nodeid.split("::", 1)
    else:
        file_part, suffix = nodeid, ""
    file_part = file_part.replace("\\", "/")
    prefix = suite_dir.rstrip("/") + "/"
    if file_part.startswith(prefix):
        file_part = file_part[len(prefix) :]
    return f"{file_part}::{suffix}" if suffix else file_part


def _read_env_json(name: str) -> list[str]:
    raw = os.environ.get(name, "[]")
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a JSON array")
    return [item for item in value if isinstance(item, str)]


def _sanitize_environment() -> None:
    kept: dict[str, str] = {}
    for name in _KEPT_ENV:
        if name in os.environ:
            kept[name] = os.environ[name]
    kept["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    kept["PYTHONHASHSEED"] = "0"
    kept["PYTHONUTF8"] = "1"
    for name in list(os.environ.keys()):
        if name not in kept:
            del os.environ[name]
    os.environ.update(kept)


def _emit_summary(summary: dict[str, object]) -> None:
    print("GRADER_SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in GRADER_ROOTS:
        _emit_summary({"error": f"invalid grader root; expected one of {GRADER_ROOTS}"})
        return 2
    root = sys.argv[1]
    try:
        evidence_dir = os.environ["EVIDENCE_DIR"]
        workspace_root = os.environ["WORKSPACE_ROOT"]
        source_roots = _read_env_json("SOURCE_ROOTS")
        expected = _read_env_json("EXPECTED_NODEIDS")
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        _emit_summary({"error": f"runner environment invalid: {exc}"})
        return 2

    _sanitize_environment()

    import pytest  # imported before sys.path is modified

    # The runner and grader plugin live in the immutable runner directory
    # (/opt/grader), which is not on sys.path under -I isolated mode.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    for rel in reversed(source_roots):
        sys.path.insert(0, os.path.join(workspace_root, rel))

    report_path = os.path.join(evidence_dir, "report.json")
    argv = [
        "-c",
        os.path.join(root, "pytest.ini"),
        "--confcutdir",
        root,
        "-p",
        "no:cacheprovider",
        "-p",
        "pytest_jsonreport",
        "-p",
        "grader_plugin",
        "--json-report",
        "--json-report-file",
        report_path,
        "-q",
        os.path.join(root, "tests"),
    ]

    try:
        exitcode = pytest.main(argv)
    except Exception as exc:
        _emit_summary(
            {
                "exit_code": None,
                "report_exists": os.path.exists(report_path),
                "runner_error": str(exc),
            }
        )
        return 1

    summary: dict[str, object] = {
        "exit_code": exitcode,
        "report_exists": os.path.exists(report_path),
    }
    collected: list[str] = []
    counts: dict[str, int] = {}
    try:
        with open(report_path, encoding="utf-8") as handle:
            report = json.load(handle)
        tests = report.get("tests", []) if isinstance(report, dict) else []
        if isinstance(tests, list):
            for entry in tests:
                if not isinstance(entry, dict):
                    continue
                nodeid = entry.get("nodeid")
                outcome = entry.get("outcome")
                if isinstance(nodeid, str) and nodeid:
                    collected.append(_normalize_nodeid(nodeid))
                if isinstance(outcome, str):
                    counts[outcome] = counts.get(outcome, 0) + 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        summary["evidence_error"] = str(exc)
    summary["collected"] = sorted(set(collected))
    summary["expected"] = sorted(set(expected))
    summary["counts"] = counts
    _emit_summary(summary)
    return int(exitcode) if isinstance(exitcode, int) else 1


if __name__ == "__main__":
    sys.exit(main())
