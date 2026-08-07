"""V2 experiment records, separate from the v1 record surface (hardening §6).

v2 records use a distinct schema (``kind: grader_v2_evaluation``,
``schema_version: 2.0``) and live under ``results/raw/v2-<experiment-id>/`` so
the frozen v1 report tooling can never confuse them with v1 records and v1
denominators never mix with v2 outcomes. The summary renderer mirrors the v1
summary shape (per-split counts, reason-code counts, case inventory) so the
publication reads coherently while the v1/v2 separation stays visible.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from grader_audit.core.base import StrictModel
from grader_v2.grading.evidence import SemanticEvidence

_V2_SCHEMA_VERSION = "2.0"
_V2_KIND = "grader_v2_evaluation"


class V2Truth(StrictModel):
    """Truth label attached to a v2-scored patch."""

    label: str  # "valid" | "invalid"
    source: str  # e.g. "probe-annotations", "patch-manifest", "author-heldout"
    reviewer: str | None = None


class V2Outcome(StrictModel):
    """The v2 binary outcome (mirrors the core outcome shape)."""

    status: str
    reward: float | None = None
    reason_codes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: dict[str, str] | None = None


class V2SubOutcome(StrictModel):
    """The v1 sub-result kept for transparency."""

    status: str
    reward: float | None = None
    reason_codes: list[str] = Field(default_factory=list)


class V2WorkspaceHashes(StrictModel):
    pristine_sha256: str
    pre_grade_sha256: str
    post_grade_sha256: str


class V2Git(StrictModel):
    data_commit: str
    worktree_dirty: bool = False


class V2EvaluationRecord(StrictModel):
    """One hardened-v2 scored workspace."""

    schema_version: str = _V2_SCHEMA_VERSION
    kind: str = _V2_KIND
    experiment_id: str
    timestamp_utc: str
    grader_version: str = "hardened_v2"
    task_id: str
    split: str
    patch_id: str
    patch_diff_sha256: str
    truth: V2Truth
    outcome: V2Outcome
    v1_outcome: V2SubOutcome
    semantic: SemanticEvidence | None = None
    workspace: V2WorkspaceHashes
    git: V2Git
    duration_seconds: float = 0.0

    @property
    def is_false_reward(self) -> bool:
        return (
            self.truth.label == "invalid"
            and self.outcome.status == "completed"
            and self.outcome.reward == 1.0
        )

    @property
    def is_false_rejection(self) -> bool:
        return (
            self.truth.label == "valid"
            and self.outcome.status == "completed"
            and self.outcome.reward == 0.0
        )

    @property
    def is_infrastructure(self) -> bool:
        return self.outcome.status == "infrastructure_error"

    @property
    def is_invalid_input(self) -> bool:
        return self.outcome.status == "invalid_input"


@dataclass(frozen=True)
class V2Experiment:
    """A loaded v2 experiment: records plus experiment-level metadata."""

    experiment_id: str
    records: list[V2EvaluationRecord]

    def by_split(self, split: str) -> list[V2EvaluationRecord]:
        return [record for record in self.records if record.split == split]

    def by_family(self, split: str) -> dict[str, list[V2EvaluationRecord]]:
        raise NotImplementedError


def validate_v2_experiment_id(experiment_id: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", experiment_id):
        raise ValueError(
            f"invalid v2 experiment id {experiment_id!r}; "
            "use lowercase letters, digits, and dashes"
        )


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def load_v2_experiment(experiment_dir: Path) -> V2Experiment:
    """Load every v2 record under *experiment_dir* (fail-closed on invalid)."""
    if not experiment_dir.is_dir():
        raise FileNotFoundError(f"v2 experiment directory missing: {experiment_dir}")
    records: list[V2EvaluationRecord] = []
    for path in sorted(experiment_dir.rglob("*.json")):
        if path.name == "metadata.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid v2 record {path}: {exc}") from None
        record = V2EvaluationRecord.model_validate(payload)
        records.append(record)
    if not records:
        raise ValueError(f"no v2 records under {experiment_dir}")
    ids = {record.experiment_id for record in records}
    if len(ids) != 1:
        raise ValueError(f"mixed experiment ids under {experiment_dir}: {sorted(ids)}")
    return V2Experiment(experiment_id=ids.pop(), records=records)


def write_v2_record(record: V2EvaluationRecord, experiment_dir: Path) -> Path:
    """Write one v2 record under ``<experiment_dir>/<split>/<task>/<patch>.json``."""
    path = (
        experiment_dir
        / record.split
        / record.task_id
        / f"{record.patch_id}.json"
    )
    if path.exists():
        raise FileExistsError(f"v2 record already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        record.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return path


# ---------------------------------------------------------------------------
# Summary rendering (v1-summary-shaped, v2-only denominators)
# ---------------------------------------------------------------------------


def _wilson(p: float, n: int) -> tuple[float, float]:
    import math

    if n == 0:
        return (0.0, 0.0)
    z = 1.959963984540054
    phat = p / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _pct(count: int, total: int) -> str:
    if total == 0:
        return "0 / 0 (—)"
    return f"{count} / {total} ({100.0 * count / total:.1f}%)"


def render_v2_summary(experiment: V2Experiment) -> str:
    """Render the v2 experiment summary (COMPLETE when no infra/invalid input)."""
    lines: list[str] = [
        f"# Grader reliability report — {experiment.experiment_id} (hardened v2)",
        "",
        "- Grader version: `hardened_v2` (semantic grader; records schema `2.0`)",
        f"- Record count: {len(experiment.records)}",
        "",
    ]
    problems: list[str] = []
    for record in experiment.records:
        if record.is_infrastructure:
            problems.append(
                f"infrastructure_error: {record.split}/{record.task_id}/{record.patch_id}"
            )
        if record.is_invalid_input:
            problems.append(
                f"invalid_input: {record.split}/{record.task_id}/{record.patch_id}"
            )
    if problems:
        lines.append("## Status: INCOMPLETE")
        lines.append("")
        for problem in sorted(problems):
            lines.append(f"- {problem}")
        lines.append("")
    else:
        lines.append("## Status: COMPLETE")
        lines.append("")
        lines.append(
            "- Primary metrics use only records with `status: completed`; "
            "infrastructure and invalid-input outcomes are never counted as "
            "solution outcomes."
        )
        lines.append("")

    splits = sorted({record.split for record in experiment.records})
    for split in splits:
        records = experiment.by_split(split)
        solution = [
            r for r in records
            if not r.is_infrastructure and not r.is_invalid_input
        ]
        valid = [r for r in solution if r.truth.label == "valid"]
        invalid = [r for r in solution if r.truth.label == "invalid"]
        lines.append(f"## {split} / hardened_v2")
        lines.append("")
        lines.append("| Metric | Value | 95% CI |")
        lines.append("|---|---|---|")
        fr = sum(1 for r in invalid if r.is_false_reward)
        fr_lo, fr_hi = _wilson(fr, len(invalid))
        frj = sum(1 for r in valid if r.is_false_rejection)
        frj_lo, frj_hi = _wilson(frj, len(valid))
        lines.append(
            f"| False reward rate | {_pct(fr, len(invalid))} | "
            f"95% Wilson [{fr_lo:.3f}, {fr_hi:.3f}] |"
        )
        lines.append(
            f"| False rejection rate | {_pct(frj, len(valid))} | "
            f"95% Wilson [{frj_lo:.3f}, {frj_hi:.3f}] |"
        )
        lines.append("")
        lines.append(
            "- Denominators include only `completed` records with a truth "
            "label; infrastructure and invalid-input outcomes are never "
            "counted as solution outcomes."
        )
        lines.append("")

        families: dict[str, list[V2EvaluationRecord]] = {}
        for record in invalid:
            families.setdefault(record.patch_id, []).append(record)
        lines.append("### Invalid patches (rejected / total)")
        lines.append("")
        lines.append("| Patch | Rewarded (false) | Reason codes |")
        lines.append("|---|---|---|")
        for patch_id in sorted(families):
            patch_records = families[patch_id]
            head = patch_records[0]
            rewarded = head.outcome.reward == 1.0
            lines.append(
                f"| `{patch_id}` | {'YES' if rewarded else 'no'} | "
                f"{', '.join(head.outcome.reason_codes) or '—'} |"
            )
        lines.append("")

        reason_counts: dict[str, int] = {}
        for record in records:
            for code in record.outcome.reason_codes:
                reason_counts[code] = reason_counts.get(code, 0) + 1
        if reason_counts:
            lines.append("### Reason-code counts")
            lines.append("")
            lines.append("> A patch is counted once per recorded reason code.")
            lines.append("")
            lines.append("| Reason | Count |")
            lines.append("|---|---|")
            for code in sorted(reason_counts):
                lines.append(f"| {code} | {reason_counts[code]} |")
            lines.append("")

    lines.append("## Semantic evidence summary")
    lines.append("")
    lines.append("| Patch | Profile | Seed | Cases | Failed | Suite SHA-256 |")
    lines.append("|---|---|---|---|---|---|")
    for record in sorted(experiment.records, key=lambda r: r.patch_id):
        if record.semantic is None:
            lines.append(
                f"| `{record.patch_id}` | — | — | — | — | — |"
            )
            continue
        sem = record.semantic
        lines.append(
            f"| `{record.patch_id}` | `{sem.profile_id}` | {sem.seed} | "
            f"{sem.case_count} | {sem.failed + sem.errors} | "
            f"`{sem.suite_sha256[:12]}…` |"
        )
    lines.append("")

    lines.append("## Facts vs. interpretations")
    lines.append("")
    lines.append(
        "All counts above are facts derived from the v2 records. Manual "
        "interpretations and case-study narrative are added separately and "
        "never modify raw records."
    )
    lines.append("")
    return "\n".join(lines)
