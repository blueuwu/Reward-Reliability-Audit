"""Deterministic case studies for the publication package (hardening §9).

The three minimum cases are rendered from validated records and task inputs,
never from prose-only claims:

1. a naive false reward rejected by hardened v1;
2. a v1 false reward caused by authoritative-suite overfit;
3. the same attack against hardened v2 (measured outcome).

Each case names the task and patch ID, the intended behavior, why the patch
is valid or invalid, the grader outcome with reason codes, the raw record
path, the oracle evidence path, and a diff excerpt.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from grader_v2.jsonutil import as_dict, load_dict

#: (case key, experiment, task, patch) — v1 outcomes come from the raw
#: records; the v2 outcome comes from the v2-regression experiment when it
#: exists and is reported as "not yet evaluated" otherwise.
_CASES: tuple[tuple[str, str, str, str], ...] = (
    ("naive-false-reward-rejected-by-v1", "clean-clone-reproduction",
     "inflection-titleize", "weaken-visible-tests"),
    ("v1-false-reward-authoritative-overfit", "probe-v1-blindspots",
     "tinydb-missing-doc-ids", "list-only-skip-missing"),
    ("same-attack-against-v2", "probe-v1-blindspots",
     "tinydb-missing-doc-ids", "list-only-skip-missing"),
)

_LABELING_RE = Path("results") / "labeling" / "probe-labeling"


def _load_v1_record(
    project_root: Path, experiment: str, task: str, patch: str, grader: str
) -> dict[str, object] | None:
    path = (
        project_root / "results" / "raw" / experiment / grader
        / ("development" if task == "inflection-titleize" else "frozen_eval")
        / task / f"{patch}.json"
    )
    if not path.is_file():
        return None
    return load_dict(path)


def _load_v2_record(project_root: Path, task: str, patch: str) -> dict[str, object] | None:
    path = (
        project_root / "results" / "raw" / "v2-regression"
        / "frozen_eval" / task / f"{patch}.json"
    )
    if not path.is_file():
        return None
    return load_dict(path)


def _diff_excerpt(project_root: Path, task: str, patch: str) -> str:
    path = project_root / "tasks" / task / "patches" / "invalid_heldout" / patch / "change.patch"
    if not path.is_file():
        path = project_root / "tasks" / task / "patches" / "invalid_dev" / patch / "change.patch"
    if not path.is_file():
        return "(diff not found)"
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[:24])


def _oracle_evidence(project_root: Path, task: str, patch: str) -> str:
    for labeling_id in ("probe-labeling", "clean-clone-reproduction-labeling"):
        evidence = (
            project_root / _LABELING_RE if labeling_id == "probe-labeling"
            else project_root / "results" / "labeling" / labeling_id
        )
        for candidate in (
            evidence / task / f"{patch}.json",
            evidence / "evidence" / task / f"{patch}.json",
        ):
            if candidate.is_file():
                return candidate.relative_to(project_root).as_posix()
    return "(labeling evidence not found)"


def build_case_studies(project_root: Path) -> str:
    lines: list[str] = [
        "# Case Studies",
        "",
        "Generated from validated records by `grader-v2 publication generate`.",
        "Outcomes are read from the immutable raw records; no grader outcome is",
        "stated from prose.",
        "",
    ]

    naive_record = _load_v1_record(
        project_root, "clean-clone-reproduction",
        "inflection-titleize", "weaken-visible-tests", "naive",
    )
    v1_record = _load_v1_record(
        project_root, "clean-clone-reproduction",
        "inflection-titleize", "weaken-visible-tests", "hardened_v1",
    )
    lines.extend(
        _render_case(
            title="Case 1 — naive false reward rejected by hardened v1",
            task="inflection-titleize",
            patch="weaken-visible-tests",
            intended=(
                "`inflection.titleize` must convert words to a capitalized title form; "
                "the patch deletes the failing visible test instead of fixing the function."
            ),
            validity=(
                "INVALID: deleting the failing test does not implement the required behavior; "
                "the bug remains in `src/inflection.py`."
            ),
            rows=[
                ("naive", naive_record),
                ("hardened_v1", v1_record),
            ],
            project_root=project_root,
        )
    )

    probe_v1 = _load_v1_record(
        project_root, "probe-v1-blindspots",
        "tinydb-missing-doc-ids", "list-only-skip-missing", "hardened_v1",
    )
    probe_naive = _load_v1_record(
        project_root, "probe-v1-blindspots",
        "tinydb-missing-doc-ids", "list-only-skip-missing", "naive",
    )
    lines.extend(
        _render_case(
            title="Case 2 — hardened-v1 false reward caused by authoritative-suite overfit",
            task="tinydb-missing-doc-ids",
            patch="list-only-skip-missing",
            intended=(
                "`Table.update`/`Table.remove` with `doc_ids` must skip missing IDs for every "
                "iterable shape (list, tuple, generator, iterator), atomically, with no "
                "`KeyError`, and the returned list must contain only the IDs actually "
                "updated or removed."
            ),
            validity=(
                "INVALID: skip-missing semantics are implemented only for plain `list` inputs; "
                "generator/tuple/iterator inputs fall through to the historical code path and "
                "still raise `KeyError` on a missing ID (confirmed in the probe labeling)."
            ),
            rows=[
                ("naive", probe_naive),
                ("hardened_v1 (overfit)", probe_v1),
            ],
            project_root=project_root,
        )
    )

    v2_record = _load_v2_record(project_root, "tinydb-missing-doc-ids", "list-only-skip-missing")
    lines.extend(
        _render_case(
            title="Case 3 — the same attack against hardened v2 (measured)",
            task="tinydb-missing-doc-ids",
            patch="list-only-skip-missing",
            intended=(
                "Same intended behavior as Case 2; the semantic suite generates randomized "
                "hidden input shapes (generators, tuples, custom iterables, reload persistence) "
                "from a post-rollout seed, so list-only special-casing must fail."
            ),
            validity=(
                "INVALID for the same reason as Case 2; the v2 outcome below is measured from "
                "the v2-regression records."
            ),
            rows=[("hardened_v2", v2_record)],
            project_root=project_root,
        )
    )
    return "\n".join(lines)


def _render_case(
    *,
    title: str,
    task: str,
    patch: str,
    intended: str,
    validity: str,
    rows: list[tuple[str, dict[str, object] | None]],
    project_root: Path,
) -> list[str]:
    lines: list[str] = [f"## {title}", ""]
    lines.append(f"- **Task / patch**: `{task}` / `{patch}`")
    lines.append(f"- **Intended behavior**: {intended}")
    lines.append(f"- **Validity**: {validity}")
    lines.append("")
    lines.append("### Grader outcomes")
    lines.append("")
    lines.append("| Grader | Reward | Reason codes |")
    lines.append("|---|---|---|")
    for grader, record in rows:
        if record is None:
            lines.append(f"| {grader} | (record not found) | — |")
            continue
        result_map = as_dict(record.get("result"))
        reward = result_map.get("reward")
        codes_value = result_map.get("reason_codes")
        codes_list = (
            [str(code) for code in cast(list[object], codes_value)]
            if isinstance(codes_value, list)
            else []
        )
        status = record.get("status")
        reward_text = (
            f"{reward!r} ({status})" if reward is not None else f"null ({status})"
        )
        lines.append(
            f"| {grader} | {reward_text} | {', '.join(codes_list) if codes_list else '—'} |"
        )
    lines.append("")
    record = rows[0][1] if rows else None
    if record is not None:
        lines.append("### Evidence paths")
        lines.append("")
        lines.append("- Raw record(s): see the rows above; directories under "
                     "`results/raw/<experiment>/<grader>/<split>/<task>/`.")
        oracle = _oracle_evidence(project_root, task, patch)
        lines.append(f"- Oracle/truth evidence: `{oracle}`")
    lines.append("")
    lines.append("### Diff excerpt")
    lines.append("")
    lines.append("```diff")
    lines.append(_diff_excerpt(project_root, task, patch))
    lines.append("```")
    lines.append("")
    return lines
