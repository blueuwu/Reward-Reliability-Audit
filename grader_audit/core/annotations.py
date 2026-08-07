"""Manual truth-label annotations (Sections 27.15, 27.18).

Two-phase lifecycle (see ``docs/DECISIONS.md`` D-049):

Phase 1 (before scoring): a manual reviewer confirms ``disposition``,
``truth_label``, ``reviewer``, ``timestamp_utc`` and the immutable patch
identity hashes (``recorded_patch_hashes``). ``run-controlled`` and
``run-heldout`` refuse any patch without this.

Phase 2 (after scoring): the mechanical binder appends ONLY
``recorded_raw_record_hashes`` (keyed by grader) to the existing annotation,
preserving every human field and refusing conflicts, so the final report can
verify exact raw-record SHA-256s.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import cast

import yaml

from grader_audit.core.manifests import LoadedPatch

DEFAULT_ANNOTATIONS_ROOT = Path("results") / "annotations"


class MissingAnnotationError(RuntimeError):
    pass


class AnnotationMismatchError(RuntimeError):
    pass


def annotation_path(
    annotations_root: Path, experiment_id: str, task_id: str, patch_id: str
) -> Path:
    return annotations_root / experiment_id / task_id / f"{patch_id}.yaml"


def load_annotation(path: Path) -> dict[str, object]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AnnotationMismatchError(f"annotation is not a mapping: {path}")
    return cast(dict[str, object], data)


def _atomic_write_annotation(path: Path, annotation: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    tmp.write_text(
        yaml.safe_dump(annotation, sort_keys=True).replace("\r\n", "\n"),
        encoding="utf-8",
        newline="\n",
    )
    try:
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def require_confirmed_annotation(
    annotations_root: Path,
    experiment_id: str,
    patch: LoadedPatch,
) -> dict[str, object]:
    """Return the confirmed annotation for *patch*, or raise a gate error."""
    path = annotation_path(
        annotations_root, experiment_id, patch.manifest.task_id, patch.manifest.id
    )
    if not path.is_file():
        raise MissingAnnotationError(
            f"patch {patch.manifest.id} lacks a confirmed truth annotation: {path}"
        )
    annotation = load_annotation(path)
    disposition = annotation.get("disposition")
    truth_label = annotation.get("truth_label")
    hashes = annotation.get("recorded_patch_hashes")
    if disposition != "confirmed":
        raise AnnotationMismatchError(f"annotation for {patch.manifest.id} is not confirmed")
    if truth_label != patch.manifest.label.value:
        raise AnnotationMismatchError(
            f"annotation truth label {truth_label!r} does not match "
            f"patch label {patch.manifest.label.value!r}"
        )
    if not isinstance(hashes, dict):
        raise AnnotationMismatchError(
            f"annotation for {patch.manifest.id} has no recorded patch hashes"
        )
    recorded = cast(dict[str, object], hashes)
    if recorded.get("metadata_sha256") != patch.metadata_sha256:
        raise AnnotationMismatchError(
            f"annotation metadata hash does not match {patch.manifest.id}"
        )
    if recorded.get("diff_sha256") != patch.diff_sha256:
        raise AnnotationMismatchError(f"annotation diff hash does not match {patch.manifest.id}")
    return annotation


def bind_raw_record_hashes(
    annotations_root: Path,
    experiment_id: str,
    task_id: str,
    patch_id: str,
    raw_hash_by_grader: dict[str, str],
) -> dict[str, object]:
    """Append raw-record SHA-256s to a confirmed annotation (phase 2, mechanical).

    Only the ``recorded_raw_record_hashes`` key is added or merged; all human
    fields (reviewer, timestamp, truth, disposition, reason, notes) are
    preserved. A conflicting existing hash for the same grader is refused.
    """
    path = annotation_path(annotations_root, experiment_id, task_id, patch_id)
    if not path.is_file():
        raise MissingAnnotationError(f"annotation missing for binding: {path}")
    annotation = load_annotation(path)
    if annotation.get("disposition") != "confirmed":
        raise AnnotationMismatchError(f"refusing to bind a non-confirmed annotation: {path}")
    existing = annotation.get("recorded_raw_record_hashes")
    merged: dict[str, object] = (
        dict(cast(dict[str, object], existing)) if isinstance(existing, dict) else {}
    )
    for grader, record_hash in raw_hash_by_grader.items():
        prior = merged.get(grader)
        if prior is not None and prior != record_hash:
            raise AnnotationMismatchError(
                f"raw-record-hash conflict for {task_id}/{patch_id}/{grader}"
            )
        merged[grader] = record_hash
    updated = dict(annotation)
    updated["recorded_raw_record_hashes"] = merged
    _atomic_write_annotation(path, updated)
    return updated
