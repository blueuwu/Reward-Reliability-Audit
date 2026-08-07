"""Mechanical phase-2 annotation binding driver (D-049, work-order W5 step 6).

``grader_audit.core.annotations.bind_raw_record_hashes`` has no CLI entry
point; this script drives it for one experiment by computing the raw-record
SHA-256 for every (grader, split, task, patch) path recorded under
``results/raw/<experiment_id>`` and appending ``recorded_raw_record_hashes``
to the confirmed phase-1 annotation. Only the raw-record-hash key is written;
all human fields are preserved (refusing conflicts).

Usage::

    uv run python -m grader_v2.bind_annotation_hashes --experiment-id ID
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from grader_audit.core.annotations import bind_raw_record_hashes
from grader_audit.core.hashing import sha256_file

DEFAULT_RAW_ROOT = Path("results") / "raw"
DEFAULT_ANNOTATIONS_ROOT = Path("results") / "annotations"


def bind_experiment(
    *,
    project_root: Path,
    raw_results_root: Path,
    annotations_root: Path,
    experiment_id: str,
) -> int:
    experiment_dir = raw_results_root / experiment_id
    if not experiment_dir.is_dir():
        print(f"error: experiment directory missing: {experiment_dir}", file=sys.stderr)
        return 2

    bound = 0
    for grader_dir in sorted(experiment_dir.iterdir()):
        if not grader_dir.is_dir():
            continue
        grader = grader_dir.name
        for split_dir in sorted(grader_dir.iterdir()):
            if not split_dir.is_dir():
                continue
            for task_dir in sorted(split_dir.iterdir()):
                if not task_dir.is_dir():
                    continue
                task_id = task_dir.name
                for record_path in sorted(task_dir.glob("*.json")):
                    patch_id = record_path.stem
                    try:
                        record = json.loads(record_path.read_bytes().decode("utf-8"))
                    except (OSError, json.JSONDecodeError) as exc:
                        print(
                            f"error: unreadable record {record_path.relative_to(project_root)}: {exc}",
                            file=sys.stderr,
                        )
                        return 3
                    raw_hash = sha256_file(record_path)
                    bind_raw_record_hashes(
                        annotations_root,
                        experiment_id,
                        task_id,
                        patch_id,
                        {grader: raw_hash},
                    )
                    print(
                        f"bound {grader} {task_id}/{patch_id} -> {raw_hash[:12]}"
                    )
                    bound += 1
    print(f"phase-2 binding complete: {bound} records bound")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--annotations-root", type=Path, default=DEFAULT_ANNOTATIONS_ROOT)
    args = parser.parse_args(argv)
    return bind_experiment(
        project_root=Path.cwd(),
        raw_results_root=args.results_root,
        annotations_root=args.annotations_root,
        experiment_id=args.experiment_id,
    )


if __name__ == "__main__":
    sys.exit(main())
