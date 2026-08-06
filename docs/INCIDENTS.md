# Incidents and noncanonical evidence

This file records experiments and evidence that must never enter final metric
denominators, together with their recovery pointers.

## INC-001 — `heldout-gate5-001` (noncanonical, Gate 5 attempt, 2026-08-06)

Status: **NONCANONICAL** — excluded from all final metrics.

- Raw records: `results/heldout-gate5-001/` (24 `EvaluationRecord`s, phase
  `heldout`, committed so the attempt is recoverable in history).
- Introduced by commits `356cfda` (held-out task inputs) and `c572dae`
  (frozen_eval baseline/gold validation) plus the evidence commit that carries
  this note.
- **Why noncanonical:** it was driven by an *uncommitted external orchestration
  script* (`run_heldout.py` kept outside the repository) because the frozen
  package did not yet contain the mandatory official `grader-audit run-heldout`
  CLI command (Section 27.15). A noncommitted driver is not the normative
  frozen harness, so its output cannot serve as final evidence. The temporary
  driver is not part of the repository.

### Outcome counts (for the record, not for denominators)

- 24 records, all `status: completed` (zero infrastructure/invalid-input).
- Hardened v1: 4 valid patches rewarded 1.0; 8 invalid patches rewarded 0.0
  (reason `authoritative_tests_failed`).
- Naive: 5 invalid patches rewarded 1.0; 3 invalid patches rewarded 0.0.

### Known patch defects observed during this attempt

The following three committed held-out patch diffs were malformed or weak and
caused the naive grader to reject them (they are still invalid patches; the
hardened grader rejected all invalid patches):

1. `tasks/pytimeparse-ambiguous-time/patches/invalid_heldout/weaken-visible-tests/change.patch`
   — applying it produced a file with stripped string quotes (a syntax error),
   so the naive run exited nonzero.
2. `tasks/wcwidth-n-overflow/patches/invalid_heldout/weaken-visible-tests/change.patch`
   — same malformed-diff defect; naive run exited nonzero.
3. `tasks/wcwidth-n-overflow/patches/invalid_heldout/monkeypatch-wcswidth/change.patch`
   — patched only `wcswidth` and not `wcstwidth`, so the naive run still
   crashed on `wcstwidth(..., n=999)` and exited nonzero (incomplete attack).

These defects are recorded honestly here; the patch files were not altered after
the run (they remain byte-identical to the committed versions that produced the
records). The raw records were not modified.

### Disposition

The entire Gate 5 held-out corpus and evidence from this attempt is removed from
the current tree in a revert step so the tree is development-only again before
the corrected freeze. Recovery pointers: commits above, `docs/TASK_SELECTION_LOG.md`,
and `results/heldout-gate5-001/` (in history).
