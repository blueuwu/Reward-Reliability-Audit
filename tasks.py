"""Paired naive/hardened HUD task lists (Section 27.13).

Each naive/hardened pair stages the identical baseline, prompt, visible files,
and settings; only the ``grader_version`` argument differs. Both variants of a
task reference one env and one task image digest, so the same task image grades
naive and hardened rollouts.
"""

from __future__ import annotations

import os
from pathlib import Path

from hud.eval import Task, Taskset

_HERE = Path(__file__).resolve().parent
_TASKS_DIR = Path(os.environ.get("GRADER_AUDIT_TASKS_DIR", str(_HERE / "tasks")))

ENV_NAME = "hud-grader-audit"

NAIVE_VERSION = "naive"
HARDENED_VERSION = "hardened_v1"


def _task_ids() -> list[str]:
    from grader_audit.core.manifests import discover_tasks

    if not _TASKS_DIR.is_dir():
        return []
    return sorted(task.manifest.id for task in discover_tasks(_TASKS_DIR))


_TASK_IDS = _task_ids()

#: The env-registered template id; the RPC resolves tasks by this name.
TEMPLATE_ID = "grader_reliability_task"


def _paired_tasks() -> tuple[list[Task], list[Task]]:
    naive: list[Task] = []
    hardened: list[Task] = []
    for task_id in _TASK_IDS:
        naive.append(
            Task(
                env=ENV_NAME,
                id=TEMPLATE_ID,
                args={"task_id": task_id, "grader_version": NAIVE_VERSION},
            )
        )
        hardened.append(
            Task(
                env=ENV_NAME,
                id=TEMPLATE_ID,
                args={"task_id": task_id, "grader_version": HARDENED_VERSION},
            )
        )
    return naive, hardened


_NAIVE_TASKS, _HARDENED_TASKS = _paired_tasks()

tasks_naive = Taskset("hud-grader-audit-naive", _NAIVE_TASKS)
tasks_hardened = Taskset("hud-grader-audit-hardened", _HARDENED_TASKS)
