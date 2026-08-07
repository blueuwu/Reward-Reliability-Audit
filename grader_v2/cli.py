"""``grader_v2`` reproduce driver (D-052).

The frozen v1 ``reproduce`` command runs the full offline pipeline but finishes
with the frozen report generator, which refuses its own artifact records on
Windows orchestration hosts (absolute recorded artifact paths; see
``grader_v2.reporting``). This driver runs the identical frozen pipeline
(doctor, manifest validation, image-lock verification, frozen-lock
verification, annotation preflight, held-out input verification, image build,
baseline/gold validation, controlled evaluation, held-out evaluation, raw-hash
binding) and then generates the report with the v2 path-tolerant generator.

The frozen pipeline is invoked verbatim (``grader_audit.core.reproduce``); a
``ReportError`` raised by its final report step is expected on hosts where the
frozen resolver cannot accept the recorded artifact paths, and the v2 report
step re-validates the complete matrix (fail-closed) before writing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from grader_audit.core.heldout import resolve_roots
from grader_audit.core.recorder import validate_experiment_id
from grader_audit.core.reporting import ReportError
from grader_audit.core.reproduce import (
    ReproduceError,
)
from grader_audit.core.reproduce import (
    reproduce as frozen_reproduce,
)
from grader_v2.reporting import run_report_v2

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_VALIDATION = 3
EXIT_INFRA = 4
EXIT_FREEZE = 5

_DEFAULT_ANNOTATIONS = Path("results") / "annotations"


def _resolve_root(project_root: Path, root: Path) -> Path:
    if not root.is_absolute():
        return project_root / root
    return root


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
    """Run the full offline pipeline and finish with the v2 report."""
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

    # Frozen pipeline: all evaluation steps, raw records, metadata, and raw-hash
    # binding. Its final report step may refuse on absolute artifact paths.
    frozen_report_failed = False
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
        # recorded artifact paths (D-052). All raw records were written and
        # bound before this step; the v2 report below re-validates them.
        frozen_report_failed = True
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
    if frozen_report_failed:
        # v2 report completed the validation the frozen report step could not.
        pass
    return report_path


def cmd_reproduce(args: argparse.Namespace) -> int:
    try:
        report_path = reproduce_v2(
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
    print(f"  report: {report_path}")
    return EXIT_OK


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
    if args.final:
        print(f"report (v2): copied byte-for-byte to {project_root / 'results' / 'report.md'}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m grader_v2.cli",
        description="Post-freeze grader_v2 tooling (D-052).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rep = sub.add_parser("reproduce", help="Run the full offline pipeline with the v2 report.")
    rep.add_argument("--tasks", type=Path, required=True)
    rep.add_argument("--experiment-id", required=True)
    rep.add_argument(
        "--results-root",
        type=Path,
        default=Path("results") / "raw",
        help="Raw results root (results/raw)",
    )
    rep.add_argument(
        "--annotations-root",
        type=Path,
        default=_DEFAULT_ANNOTATIONS,
        help="Annotations root (results/annotations)",
    )
    rep.add_argument("--repeat", type=int, default=3)
    rep.add_argument("--final", action="store_true", help="Also write results/report.md")
    rep.set_defaults(func=cmd_reproduce)

    rep2 = sub.add_parser("report", help="Generate a v2 report from raw results.")
    rep2.add_argument("--input", type=Path, required=True)
    rep2.add_argument("--output", type=Path, required=True)
    rep2.add_argument("--annotations-root", type=Path, default=_DEFAULT_ANNOTATIONS)
    rep2.add_argument("--final", action="store_true", help="Also write results/report.md")
    rep2.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
