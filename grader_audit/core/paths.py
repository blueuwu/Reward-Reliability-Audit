"""Normative on-disk result paths (Sections 27.15-27.18).

Raw evaluation records live under ``results/raw/<experiment_id>/``; truth
annotations under ``results/annotations/<experiment_id>/``; labeling evidence
under ``results/labeling/<labeling_id>/``; summaries under
``results/summaries/``; and the final report copy at ``results/report.md``.
"""

from __future__ import annotations

from pathlib import Path

RESULTS_ROOT = Path("results")
RAW_RESULTS_ROOT = RESULTS_ROOT / "raw"
ANNOTATIONS_ROOT = RESULTS_ROOT / "annotations"
LABELING_ROOT = RESULTS_ROOT / "labeling"
SUMMARIES_ROOT = RESULTS_ROOT / "summaries"
FINAL_REPORT = RESULTS_ROOT / "report.md"
