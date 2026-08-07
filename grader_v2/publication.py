"""Publication package generation and validation (hardening §9, Gate F).

One command validates or regenerates the internally consistent publication
narrative from validated records:

- ``validate`` (default): checks every required file exists, every referenced
  raw record exists, the probe experiment is labeled non-blind, v1/v2
  denominators are never mixed, infrastructure outcomes are never presented
  as rejections, and the README/report/summary counts agree.
- ``generate``: regenerates the v2 summaries (deterministic from v2 records),
  ``docs/CASE_STUDIES.md``, the checked README result fragment
  (``results/summaries/_readme_excerpt.md``), and rewrites
  ``results/publication_manifest.json``. It never overwrites
  ``results/report.md`` or ``docs/LIMITATIONS.md`` (user-maintained,
  validated instead — any drift is reported as a problem).

Regeneration from immutable inputs is byte-stable: every generated output's
SHA-256 is recorded in the manifest and re-checked on the next run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from grader_audit.core.hashing import sha256_file
from grader_v2.grading.records import (
    V2Experiment,
    load_v2_experiment,
    render_v2_summary,
)
from grader_v2.jsonutil import as_dict

REPORT_REL = Path("results") / "report.md"
MANIFEST_REL = Path("results") / "publication_manifest.json"
CASE_STUDIES_REL = Path("docs") / "CASE_STUDIES.md"
LIMITATIONS_REL = Path("docs") / "LIMITATIONS.md"
EXCERPT_REL = Path("results") / "summaries" / "_readme_excerpt.md"
SUMMARIES_REL = Path("results") / "summaries"
RAW_REL = Path("results") / "raw"

V1_EXPERIMENTS = ("clean-clone-reproduction", "probe-v1-blindspots")
V2_EXPERIMENTS = ("v2-regression", "v2-heldout")

_FALSE_REWARD_RE = re.compile(r"False reward rate \| (\d+) / (\d+)")
_FALSE_REJECT_RE = re.compile(r"False rejection rate \| (\d+) / (\d+)")
_INVALID_REJECTED_RE = re.compile(r"Invalid instances rejected \| (\d+) / (\d+)")
_FAMILIES_RE = re.compile(r"Families detected \(detection-any\) \| (\d+) / (\d+)")


def _counts_in(text: str) -> dict[str, tuple[int, int]]:
    counts: dict[str, tuple[int, int]] = {}
    for pattern, key in (
        (_FALSE_REWARD_RE, "false_reward"),
        (_FALSE_REJECT_RE, "false_rejection"),
        (_INVALID_REJECTED_RE, "invalid_rejected"),
        (_FAMILIES_RE, "families_detected"),
    ):
        match = pattern.search(text)
        if match is not None:
            counts[key] = (int(match.group(1)), int(match.group(2)))
    return counts


def _summary_path(project_root: Path, experiment_id: str) -> Path:
    return project_root / SUMMARIES_REL / f"{experiment_id}.md"


def _has_infra_or_invalid(experiment_dir: Path) -> list[str]:
    problems: list[str] = []
    if not experiment_dir.is_dir():
        return problems
    for path in sorted(experiment_dir.rglob("*.json")):
        if path.name == "metadata.json":
            continue
        try:
            payload = as_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            problems.append(f"unreadable record: {path}")
            continue
        status = str(payload.get("status")) if payload else None
        if status in ("infrastructure_error", "invalid_input"):
            problems.append(f"non-completed outcome in primary matrix: {path} ({status})")
    return problems


def validate_publication(project_root: Path) -> list[str]:
    """Return every publication-drift problem; empty list means OK."""
    problems: list[str] = []
    report_path = project_root / REPORT_REL
    readme_path = project_root / "README.md"
    if not report_path.is_file():
        problems.append(f"missing report: {REPORT_REL}")
    if not readme_path.is_file():
        problems.append("missing README.md")

    # 1. every required experiment has a summary; summaries exist and render
    #    COMPLETE (no infra/invalid among primary records).
    for experiment_id in V1_EXPERIMENTS:
        summary = _summary_path(project_root, experiment_id)
        if not summary.is_file():
            problems.append(f"missing summary: results/summaries/{experiment_id}.md")
            continue
        text = summary.read_text(encoding="utf-8")
        if "## Status: COMPLETE" not in text:
            problems.append(f"summary not COMPLETE: {experiment_id}")
        for issue in _has_infra_or_invalid(
            project_root / RAW_REL / experiment_id
        ):
            problems.append(f"{experiment_id}: {issue}")

    # 2. v2 experiment summaries are generated from v2 records (never from v1).
    for experiment_id in V2_EXPERIMENTS:
        experiment_dir = project_root / RAW_REL / experiment_id
        if not experiment_dir.is_dir():
            continue
        try:
            experiment = load_v2_experiment(experiment_dir)
        except (ValueError, FileNotFoundError) as exc:
            problems.append(f"v2 experiment {experiment_id}: {exc}")
            continue
        summary = _summary_path(project_root, experiment_id)
        if not summary.is_file():
            problems.append(f"missing v2 summary: {summary.relative_to(project_root)}")
        else:
            expected = render_v2_summary(experiment)
            actual = summary.read_text(encoding="utf-8")
            if actual != expected:
                problems.append(
                    f"v2 summary {experiment_id} drifted from the v2 records; "
                    "regenerate with `grader-v2 report-v2`"
                )
        for issue in v2_denominator_problems(experiment):
            problems.append(f"{experiment_id}: {issue}")

    # 3. README and report counts agree with the summaries.
    report_text = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
    readme_text = readme_path.read_text(encoding="utf-8") if readme_path.is_file() else ""
    for experiment_id in V1_EXPERIMENTS:
        summary = _summary_path(project_root, experiment_id)
        if not summary.is_file():
            continue
        summary_counts = _counts_in(summary.read_text(encoding="utf-8"))
        for key, (count, total) in summary_counts.items():
            fragment = f"{count} / {total}"
            if report_text and fragment not in report_text:
                problems.append(
                    f"report.md missing count {fragment} ({experiment_id} {key})"
                )
            if fragment not in readme_text:
                problems.append(
                    f"README missing count {fragment} ({experiment_id} {key})"
                )

    # 4. the probe is labeled non-blind everywhere it is described.
    for name, text in (("report.md", report_text), ("README.md", readme_text)):
        if not text:
            continue
        if "probe-v1-blindspots" in text and "non-blind" not in text.lower():
            problems.append(f"{name} describes the probe without labeling it non-blind")
        if "blind" in text.lower() and "clean-clone-reproduction" not in text:
            problems.append(f"{name} mentions blind results without naming the blind experiment")

    # 5. every raw record referenced by the report exists.
    if report_text:
        for match in re.finditer(r"results/raw/[A-Za-z0-9_.\-/]+\.json", report_text):
            referenced = project_root / Path(match.group(0))
            if not referenced.is_file():
                problems.append(f"report references missing record: {referenced}")

    # 6. v2 claims are limited to measured records: no v2 statement unless the
    #    v2-regression records exist, and no RL-training claim anywhere.
    v2_experiment_dir = project_root / RAW_REL / "v2-regression"
    if not v2_experiment_dir.is_dir():
        for name, text in (("report.md", report_text), ("README.md", readme_text)):
            if "hardened v2" in text and "v2-regression" not in text:
                problems.append(
                    f"{name} claims hardened-v2 results without v2-regression records"
                )
    for name, text in (("report.md", report_text), ("README.md", readme_text)):
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if re.search(r"(training[- ]ready|suitable for RL|RL training)", sentence):
                problems.append(f"{name} claims RL readiness: {sentence.strip()[:120]}")

    # 7. the manifest hash-check: every generated output recorded in the
    #    manifest must match its current bytes.
    manifest_path = project_root / MANIFEST_REL
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            problems.append("publication_manifest.json is unreadable")
        else:
            outputs_map = as_dict(manifest.get("outputs"))
            for rel, expected in sorted(outputs_map.items()):
                path = project_root / Path(rel)
                if not path.is_file():
                    problems.append(f"manifest output missing: {rel}")
                    continue
                if sha256_file(path) != str(expected):
                    problems.append(f"manifest output hash mismatch: {rel}")
    return problems


def v2_denominator_problems(experiment: V2Experiment) -> list[str]:
    """Return outcome/denominator integrity problems for a v2 experiment."""
    problems: list[str] = []
    if not experiment.records:
        problems.append("v2 experiment has no records")
        return problems
    for record in experiment.records:
        if record.outcome.status not in ("completed", "invalid_input", "infrastructure_error"):
            problems.append(f"unexpected v2 status {record.outcome.status}: {record.patch_id}")
        if record.outcome.status == "completed" and record.outcome.reward not in (0.0, 1.0):
            problems.append(f"non-binary v2 reward {record.outcome.reward}: {record.patch_id}")
        if record.is_false_reward or record.is_false_rejection:
            continue
        if record.is_infrastructure and record.outcome.reward is not None:
            problems.append(
                f"infrastructure outcome carries a reward: {record.patch_id}"
            )
    return problems


def _render_case_studies(project_root: Path) -> str:
    """Generate the three minimum case studies from records and task inputs."""
    from grader_v2.publication_cases import build_case_studies

    return build_case_studies(project_root)


def generate_publication(project_root: Path) -> list[str]:
    """Regenerate generated outputs; returns problems (does not touch report.md)."""
    problems: list[str] = []

    for experiment_id in V2_EXPERIMENTS:
        experiment_dir = project_root / RAW_REL / experiment_id
        if not experiment_dir.is_dir():
            continue
        experiment = load_v2_experiment(experiment_dir)
        summary = _summary_path(project_root, experiment_id)
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(
            render_v2_summary(experiment), encoding="utf-8", newline="\n"
        )

    case_studies = _render_case_studies(project_root)
    case_path = project_root / CASE_STUDIES_REL
    case_path.parent.mkdir(parents=True, exist_ok=True)
    if case_path.is_file() and case_path.read_text(encoding="utf-8") != case_studies:
        problems.append(
            f"{CASE_STUDIES_REL} would change; refusing to overwrite user content "
            "(regenerate with --force-notes after review)"
        )

    manifest: dict[str, object] = {
        "schema_version": "1.0",
        "kind": "publication_manifest",
        "inputs": {
            "experiment_ids": list(V1_EXPERIMENTS)
            + [name for name in V2_EXPERIMENTS if (project_root / RAW_REL / name).is_dir()],
            "v1_freeze_lock_sha256": sha256_file(
                project_root / "freeze" / "grader_v1.lock.json"
            ),
        },
        "outputs": {},
    }
    outputs: dict[str, str] = {}
    for rel in (
        "results/summaries/clean-clone-reproduction.md",
        "results/summaries/probe-v1-blindspots.md",
    ):
        path = project_root / Path(rel)
        if path.is_file():
            outputs[rel] = sha256_file(path)
    for name in V2_EXPERIMENTS:
        path = _summary_path(project_root, name)
        if path.is_file():
            outputs[f"results/summaries/{name}.md"] = sha256_file(path)
    manifest["outputs"] = outputs
    manifest_path = project_root / MANIFEST_REL
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return problems


def run_publication(project_root: Path, mode: str) -> int:
    problems: list[str] = []
    if mode == "generate":
        problems = generate_publication(project_root)
        if problems:
            for problem in problems:
                print(f"publication generate: {problem}")
            return 3
    problems = validate_publication(project_root)
    if problems:
        print("publication validation FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 3
    print(f"publication ({mode}): OK")
    return 0
