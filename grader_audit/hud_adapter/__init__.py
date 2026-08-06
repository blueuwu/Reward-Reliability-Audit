"""HUD adapter (Section 27.13).

Maps the framework-independent grading core to HUD v6 results and provides the
shared evaluator the HUD task template calls after the agent finishes. The
adapter never reimplements core grading logic.
"""

from __future__ import annotations

from grader_audit.hud_adapter.evaluator import (
    SUPPORTED_GRADER_VERSIONS,
    HudGrade,
    grade_workspace,
    grade_workspace_async,
)
from grader_audit.hud_adapter.mapping import (
    GRADER_HARDENED_V1,
    GRADER_NAIVE,
    HudEvalContext,
    build_subscores,
    map_evaluation_result,
)

__all__ = [
    "GRADER_HARDENED_V1",
    "GRADER_NAIVE",
    "SUPPORTED_GRADER_VERSIONS",
    "HudEvalContext",
    "HudGrade",
    "build_subscores",
    "grade_workspace",
    "grade_workspace_async",
    "map_evaluation_result",
]
