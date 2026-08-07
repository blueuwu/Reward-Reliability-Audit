"""``grader_v2`` application-v2 CLI (D-052, D-053).

Commands:
- ``reproduce`` — the frozen offline pipeline with the v2 path-tolerant report.
- ``report`` — v2 report generation from raw results.
- ``eval-v2`` — score a patch matrix under hardened_v2 (regression or held-out).
- ``report-v2`` — render a v2 experiment summary.
- ``replay`` — regenerate a semantic suite from its recorded seed and compare.
- ``freeze-v2`` — snapshot the v2 grading surface (before authoring held-out).
- ``verify-v1-lock`` — read-only v1 freeze verification with Track-B exceptions.
- ``demo`` — the deterministic application demo (hardening §11).
- ``publication`` — generate/validate the publication package (hardening §9).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

from grader_audit.core.heldout import resolve_roots
from grader_audit.core.recorder import validate_experiment_id
from grader_audit.core.reporting import ReportError
from grader_audit.core.reproduce import ReproduceError
from grader_audit.core.reproduce import reproduce as frozen_reproduce
from grader_v2.freeze import (
    V1LockVerificationError,
    verify_v1_lock,
)
from grader_v2.freeze import (
    freeze_v2 as freeze_v2_snapshot,
)
from grader_v2.grading.driver import V2DriverError, run_v2_experiment
from grader_v2.grading.records import (
    load_v2_experiment,
    render_v2_summary,
)
from grader_v2.jsonutil import as_dict
from grader_v2.reporting import run_report_v2

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_VALIDATION = 3
EXIT_INFRA = 4
EXIT_FREEZE = 5

_DEFAULT_ANNOTATIONS = Path("results") / "annotations"
_DEFAULT_RAW = Path("results") / "raw"


def _resolve_root(project_root: Path, root: Path) -> Path:
    if not root.is_absolute():
        return project_root / root
    return root


def cmd_reproduce(args: argparse.Namespace) -> int:
    try:
        validate_experiment_id(args.experiment_id)
    except ValueError as exc:
        print(f"reproduce (v2) failed: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        reproduce_v2(
            project_root=Path.cwd(),
            tasks_dir=args.tasks,
            raw_results_root=args.results_root,
            annotations_root=args.annotations_root,
            experiment_id=args.experiment_id,
            repeat=args.repeat,
            final=args.final,
        )
    except ReproduceError as exc:
        print(f"reproduce (v2) failed: {exc}", file=sys.stderr)
        return exc.code
    except ReportError as exc:
        print(f"report (v2) refused: {exc}", file=sys.stderr)
        return EXIT_VALIDATION
    print(f"reproduce (v2): experiment {args.experiment_id} complete")
    return EXIT_OK


def reproduce_v2(
    *,
    project_root: Path,
    tasks_dir: Path,
    raw_results_root: Path,
    annotations_root: Path,
    experiment_id: str,
    repeat: int = 3,
    final: bool = False,
) -> Path:
    """Run the full offline pipeline and finish with the v2 report (D-052)."""
    try:
        validate_experiment_id(experiment_id)
    except ValueError as exc:
        raise ReproduceError(str(exc), EXIT_USAGE) from None

    resolved_raw, resolved_ann = resolve_roots(
        project_root, raw_results_root, annotations_root
    )
    if (resolved_raw / experiment_id).exists():
        raise ReproduceError(
            f"experiment already exists: {resolved_raw / experiment_id}", EXIT_USAGE
        )

    try:
        frozen_reproduce(
            project_root=project_root,
            tasks_dir=tasks_dir,
            raw_results_root=raw_results_root,
            annotations_root=annotations_root,
            experiment_id=experiment_id,
            repeat=repeat,
        )
    except ReportError:
        # Expected on hosts where the frozen report resolver cannot accept the
        # recorded artifact paths (D-052); the v2 report below re-validates.
        pass
    except ReproduceError:
        raise

    report_path = resolved_raw.parent / "summaries" / f"{experiment_id}.md"
    run_report_v2(
        project_root=project_root,
        input_dir=resolved_raw / experiment_id,
        output_path=report_path,
        final=final,
        annotations_root=resolved_ann,
    )
    return report_path


def cmd_report(args: argparse.Namespace) -> int:
    project_root = Path.cwd()
    try:
        run_report_v2(
            project_root=project_root,
            input_dir=args.input,
            output_path=args.output,
            final=args.final,
            annotations_root=_resolve_root(project_root, args.annotations_root),
        )
    except ReportError as exc:
        print(f"report (v2) refused: {exc}", file=sys.stderr)
        return EXIT_VALIDATION
    print(f"report (v2): {args.output}")
    return EXIT_OK


def cmd_eval_v2(args: argparse.Namespace) -> int:
    project_root = Path.cwd()
    annotation_ids = tuple(
        part.strip() for part in args.annotations_experiment.split(",") if part.strip()
    )
    if not annotation_ids:
        annotation_ids = ("probe-v1-blindspots", "clean-clone-reproduction")
    splits = tuple(part.strip() for part in args.splits.split(",") if part.strip())
    if not splits:
        print("eval-v2: --splits must name at least one split", file=sys.stderr)
        return EXIT_USAGE
    fixed_seed = args.seed
    try:
        result = run_v2_experiment(
            project_root=project_root,
            tasks_dir=args.tasks,
            raw_results_root=args.results_root,
            annotations_root=args.annotations_root,
            annotation_experiment_ids=annotation_ids,
            experiment_id=args.experiment_id,
            splits=splits,
            include_baseline=args.baseline,
            fixed_seed=fixed_seed,
        )
    except V2DriverError as exc:
        print(f"eval-v2 failed: {exc}", file=sys.stderr)
        return exc.code
    summary = render_v2_summary(result.experiment)
    summary_path = args.results_root.parent / "summaries" / f"{args.experiment_id}.md"
    if not summary_path.is_absolute():
        summary_path = project_root / summary_path
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary, encoding="utf-8", newline="\n")
    print(f"eval-v2: experiment {args.experiment_id} complete")
    print(f"  records: {len(result.experiment.records)}")
    print(f"  summary: {summary_path}")
    return EXIT_OK


def cmd_report_v2(args: argparse.Namespace) -> int:
    project_root = Path.cwd()
    input_dir = _resolve_root(project_root, args.input)
    try:
        experiment = load_v2_experiment(input_dir)
    except (ValueError, FileNotFoundError) as exc:
        print(f"report-v2 refused: {exc}", file=sys.stderr)
        return EXIT_VALIDATION
    summary = render_v2_summary(experiment)
    output = args.output
    if not output.is_absolute():
        output = project_root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(summary, encoding="utf-8", newline="\n")
    print(f"report-v2: {output}")
    return EXIT_OK


def cmd_replay(args: argparse.Namespace) -> int:
    """Regenerate a semantic suite from its recorded seed and compare."""
    project_root = Path.cwd()
    from grader_v2.grading.semantic import get_profile

    profile = get_profile(args.task)
    if profile is None:
        print(f"replay: no semantic profile for task {args.task!r}", file=sys.stderr)
        return EXIT_USAGE
    seed = args.seed
    suite = profile.generate(seed)
    expected = set(suite.expected_nodeids)
    recorded: set[str] = set()
    if args.record is not None:
        record_path = _resolve_root(project_root, args.record)
        try:
            payload = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"replay: unreadable record {record_path}: {exc}", file=sys.stderr)
            return EXIT_VALIDATION
        sem = as_dict(payload).get("semantic")
        sem_dict = as_dict(sem)
        if not sem_dict:
            print("replay: record has no semantic evidence", file=sys.stderr)
            return EXIT_VALIDATION
        raw_nodeids = sem_dict.get("expected_nodeids")
        recorded = (
            {str(node) for node in cast(list[object], raw_nodeids)}
            if isinstance(raw_nodeids, list)
            else set()
        )
        if sem_dict.get("generator_version") != profile.generator_version:
            print(
                f"replay: generator version mismatch: "
                f"record={sem_dict.get('generator_version')} "
                f"current={profile.generator_version}",
                file=sys.stderr,
            )
            return EXIT_VALIDATION
        if sem_dict.get("seed") != seed:
            print(
                f"replay: seed mismatch: record={sem_dict.get('seed')} requested={seed}",
                file=sys.stderr,
            )
            return EXIT_VALIDATION
    output = args.output
    if not output.is_absolute():
        output = project_root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(suite.text, encoding="utf-8", newline="\n")
    print(f"replay: wrote {output}")
    if recorded and recorded != expected:
        print(
            "replay: node-ID mismatch with the recorded evidence: "
            f"recorded={sorted(recorded)} generated={sorted(expected)}",
            file=sys.stderr,
        )
        return EXIT_VALIDATION
    return EXIT_OK


def cmd_freeze_v2(args: argparse.Namespace) -> int:
    project_root = Path.cwd()
    try:
        output = freeze_v2_snapshot(project_root)
    except V1LockVerificationError as exc:
        print(f"freeze-v2 refused: {exc}", file=sys.stderr)
        return EXIT_FREEZE
    print(f"freeze-v2: hardened_v2 grading surface frozen at {output}")
    return EXIT_OK


def cmd_verify_v1_lock(args: argparse.Namespace) -> int:
    project_root = Path.cwd()
    try:
        verification = verify_v1_lock(project_root)
    except V1LockVerificationError as exc:
        print(f"verify-v1-lock refused: {exc}", file=sys.stderr)
        return EXIT_FREEZE
    print(f"verify-v1-lock: tag {verification.tag_commit[:12]}…")
    print(f"  protected files matching: {verification.protected_matched}")
    if verification.protected_mismatches:
        print("  protected mismatches:", file=sys.stderr)
        for mismatch in verification.protected_mismatches:
            print(f"    - {mismatch}", file=sys.stderr)
        return EXIT_FREEZE
    print(f"  lock byte-identical to tag commit: {verification.lock_hash_matches_tag}")
    if verification.approved_mismatches:
        print("  approved Track-B deltas (D-053):")
        for mismatch in verification.approved_mismatches:
            print(f"    - {mismatch}")
    print("  Track-B exceptions (documented, not failures):")
    for exception in verification.track_b_exceptions:
        print(f"    - {exception}")
    print("verify-v1-lock: OK (with Track-B exceptions)")
    return EXIT_OK


def cmd_demo(args: argparse.Namespace) -> int:
    from grader_v2.demo import run_demo

    return run_demo(project_root=Path.cwd(), args=args)


def cmd_publication(args: argparse.Namespace) -> int:
    from grader_v2.publication import run_publication

    return run_publication(project_root=Path.cwd(), mode=args.mode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grader-v2",
        description="Application-v2 release tooling (Track B, D-053).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rep = sub.add_parser("reproduce", help="Run the full offline pipeline with the v2 report.")
    rep.add_argument("--tasks", type=Path, required=True)
    rep.add_argument("--experiment-id", required=True)
    rep.add_argument("--results-root", type=Path, default=_DEFAULT_RAW)
    rep.add_argument("--annotations-root", type=Path, default=_DEFAULT_ANNOTATIONS)
    rep.add_argument("--repeat", type=int, default=3)
    rep.add_argument("--final", action="store_true")
    rep.set_defaults(func=cmd_reproduce)

    rep2 = sub.add_parser("report", help="Generate a v2 report from raw results.")
    rep2.add_argument("--input", type=Path, required=True)
    rep2.add_argument("--output", type=Path, required=True)
    rep2.add_argument("--annotations-root", type=Path, default=_DEFAULT_ANNOTATIONS)
    rep2.add_argument("--final", action="store_true")
    rep2.set_defaults(func=cmd_report)

    ev = sub.add_parser("eval-v2", help="Score a matrix under hardened_v2.")
    ev.add_argument("--tasks", type=Path, required=True)
    ev.add_argument("--experiment-id", required=True)
    ev.add_argument("--results-root", type=Path, default=_DEFAULT_RAW)
    ev.add_argument("--annotations-root", type=Path, default=_DEFAULT_ANNOTATIONS)
    ev.add_argument(
        "--annotations-experiment",
        default="probe-v1-blindspots,clean-clone-reproduction",
        help="Comma-separated confirmed-annotation experiment ids tried in order.",
    )
    ev.add_argument(
        "--splits",
        default="development,frozen_eval,adaptive",
        help="Comma-separated splits: development, frozen_eval, adaptive.",
    )
    ev.add_argument("--baseline", action="store_true", help="Include baseline rows.")
    ev.add_argument("--seed", type=int, default=None, help="Fixed seed (deterministic run).")
    ev.set_defaults(func=cmd_eval_v2)

    rv = sub.add_parser("report-v2", help="Render a v2 experiment summary.")
    rv.add_argument("--input", type=Path, required=True)
    rv.add_argument("--output", type=Path, required=True)
    rv.set_defaults(func=cmd_report_v2)

    rp = sub.add_parser("replay", help="Regenerate a semantic suite from a seed.")
    rp.add_argument("--task", required=True)
    rp.add_argument("--seed", type=int, required=True)
    rp.add_argument("--output", type=Path, required=True)
    rp.add_argument(
        "--record", type=Path, default=None,
        help="Optional v2 record to verify against.",
    )
    rp.set_defaults(func=cmd_replay)

    fz = sub.add_parser("freeze-v2", help="Snapshot the v2 grading surface.")
    fz.set_defaults(func=cmd_freeze_v2)

    vl = sub.add_parser("verify-v1-lock", help="Read-only v1 freeze verification (D-055).")
    vl.set_defaults(func=cmd_verify_v1_lock)

    dm = sub.add_parser("demo", help="Run the deterministic application demo.")
    dm.set_defaults(func=cmd_demo)

    pub = sub.add_parser(
        "publication", help="Generate or validate the publication package (Gate F)."
    )
    pub.add_argument(
        "--mode",
        choices=("validate", "generate"),
        default="validate",
        help="validate: check drift and hashes; generate: rewrite outputs.",
    )
    pub.set_defaults(func=cmd_publication)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
