"""Workspace change classification (Sections 8.2.D and 27.10).

Classification order per path: immutable boundary, then editable boundary, then
expected scope (informational), then the generated-artifact allowlist. The
generated allowlist applies only to the pre-grade-to-post-grade diff; the
pristine-to-pre-grade (submitted patch) diff gets no generated exemption.
"""

from __future__ import annotations

from enum import StrEnum

from grader_audit.core.models import TaskManifest
from grader_audit.core.path_rules import match_workspace_globs


class ChangeCategory(StrEnum):
    IMMUTABLE_VIOLATION = "immutable_violation"
    OUTSIDE_EDITABLE_SCOPE = "outside_editable_scope"
    OUTSIDE_EXPECTED_SCOPE = "outside_expected_scope"
    EDITABLE_SOURCE_CHANGE = "editable_source_change"
    GENERATED_ARTIFACT = "generated_artifact"


# Literal names that must never be allowlisted as generated artifacts.
_FORBIDDEN_GENERATED = (
    "conftest.py",
    "pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
)


def classify_path(
    path: str, manifest: TaskManifest, *, apply_generated_allowlist: bool
) -> ChangeCategory:
    """Classify a single POSIX-normalized workspace-relative path.

    Order per Section 27.10: the immutable boundary always takes precedence; the
    generated allowlist applies only to the pre-grade-to-post-grade diff, before
    the editable boundary; ``expected_change_globs`` is informational only.
    """
    workspace = manifest.workspace
    if match_workspace_globs(path, workspace.immutable_workspace_globs):
        return ChangeCategory.IMMUTABLE_VIOLATION
    if apply_generated_allowlist and match_workspace_globs(
        path, workspace.generated_artifact_globs
    ):
        return ChangeCategory.GENERATED_ARTIFACT
    if match_workspace_globs(path, workspace.editable_globs):
        if match_workspace_globs(path, workspace.expected_change_globs):
            return ChangeCategory.EDITABLE_SOURCE_CHANGE
        return ChangeCategory.OUTSIDE_EXPECTED_SCOPE
    return ChangeCategory.OUTSIDE_EDITABLE_SCOPE


def validate_generated_globs(manifest: TaskManifest) -> None:
    """Reject generated-artifact globs that match forbidden literal names.

    Per Section 27.10, ``conftest.py``, ``pytest.ini``, ``pyproject.toml``,
    ``setup.cfg``, ``tox.ini``, source files, visible tests, authoritative
    tests, and task metadata must never be allowlisted as generated artifacts.
    """
    for literal in _FORBIDDEN_GENERATED:
        if match_workspace_globs(literal, manifest.workspace.generated_artifact_globs):
            raise ValueError(
                f"generated_artifact_globs must not allowlist {literal!r} "
                "(never allowlisted as a generated artifact)"
            )
