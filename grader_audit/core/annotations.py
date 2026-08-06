"""Manual truth-label annotations (Sections 27.18 and 27.15).

``run-controlled`` refuses any patch lacking a ``confirmed`` annotation whose
recorded patch hashes match the current patch metadata and diff. Annotations are
separate from raw results and never mutate them.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml

from grader_audit.core.manifests import LoadedPatch


class MissingAnnotationError(RuntimeError):
    pass


class AnnotationMismatchError(RuntimeError):
    pass


def annotation_path(results_root: Path, experiment_id: str, task_id: str, patch_id: str) -> Path:
    return results_root / "annotations" / experiment_id / task_id / f"{patch_id}.yaml"


def load_annotation(path: Path) -> dict[str, object]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AnnotationMismatchError(f"annotation is not a mapping: {path}")
    return cast(dict[str, object], data)


def require_confirmed_annotation(
    results_root: Path,
    experiment_id: str,
    patch: LoadedPatch,
) -> dict[str, object]:
    """Return the confirmed annotation for *patch*, or raise a gate error."""
    path = annotation_path(results_root, experiment_id, patch.manifest.task_id, patch.manifest.id)
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
