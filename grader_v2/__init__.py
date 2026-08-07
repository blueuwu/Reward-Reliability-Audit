"""Post-freeze ``grader_v2`` package (Sections 27.14/27.20 Gate 7).

The frozen ``grader_audit/`` v1 package is byte-immutable after the
``grader-v1-frozen`` tag (Section 27.14; see D-051). Any post-freeze
implementation lives in this separate tree and imports the frozen v1 core
rather than modifying it.

Current contents:

- ``reporting.py``: a path-tolerant report generator. The frozen v1 report
  validates recorded artifact paths with ``classify_repository_relative``,
  which rejects drive prefixes and absolute paths. On Windows orchestration
  hosts (explicitly supported by Section 27.1) the frozen recorder stores
  absolute artifact paths when the results root is resolved absolute (as
  ``reproduce``/``run-heldout`` do), so the frozen report refuses its own
  records. The v2 report accepts any recorded artifact path that resolves to a
  real file inside the experiment directory, without weakening any other
  validation (planned-matrix completeness, artifact hashes, cross-grader
  workspace hashes, confirmed hash-matching annotations).
- ``reproduce.py``: the same offline pipeline as the frozen ``reproduce``
  command, but finishing with the v2 report so the full reproduction completes
  on hosts where the frozen report step cannot resolve its own artifact paths.

Nothing here is required to produce or alter any raw result record; v2 only
reads raw records and writes report markdown.
"""

from __future__ import annotations
