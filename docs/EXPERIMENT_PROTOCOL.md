# Experiment Protocol

How this audit is structured, in what order operations happen, and which
denominators count where. Normative references: contract
`CODEX_TASK_HUD_GRADER_RELIABILITY_AUDIT.md` sections 5, 7, 27.5, 27.8, 27.9,
27.14, 27.15, 27.17, 27.18, and decisions D-037, D-046, D-049, D-051, D-052.

## Splits

| Split | Tasks | Purpose | Timing |
|---|---|---|---|
| `development` | `inflection-titleize`, `schedule-repr-partial-job`, `tomli-type-error` | grader development corpus (6 valid / 18 invalid) | before the v1 freeze |
| `frozen_eval` | `tinydb-missing-doc-ids`, `tinydb-query-test-unhashable` | held-out evaluation (7 valid / 16 invalid after the probe) | introduced strictly after the `grader-v1-frozen` tag |
| `adaptive` | attempts under `adaptive_attempts/` | model-generated attacks | after the tag; never in controlled denominators |

## Order of operations

1. **Development**: baseline/gold validation (3 clean repeats per grader
   including the offline oracle), controlled evaluation of all development
   patches, two-phase truth annotations.
2. **Freeze** (`grader-audit freeze --grader hardened_v1 --git-tag
   grader-v1-frozen`): quality gates, protected-file hashing (253 files,
   `freeze/grader_v1.lock.json`), lock-only commit, annotated tag at
   `c95a014`. After the tag, v1 source is byte-immutable.
3. **Held-out evaluation** (`run-heldout`): only `frozen_eval` tasks whose
   inputs were introduced strictly after the tag, with confirmed annotations
   and a byte-identical freeze lock. Naive and hardened run from separate
   clean workspaces with identical pre-grade snapshot hashes.
4. **Post-hoc probe** (`probe-v1-blindspots`, work-order W1-W5): new patches
   authored with full knowledge of v1 behavior, labeled through
   `label-patches` (`probe-labeling`), scored in a separate experiment that
   never overwrites `clean-clone-reproduction`.
5. **Adaptive attempts** (`adaptive_attempts/`): preserved per contract §27.8
   with `patch.yaml`, `change.patch`, `prompt.md`, `transcript.json`,
   `verification.yaml`; oracle never exposed to the attacker before attempts
   are complete (§27.9).

## Denominator rules (contract §27.17, §27.8)

- Primary metrics use only approved, non-ambiguous patches with
  `status: completed`.
- Controlled invalid denominator: exactly the 18 development + 8 original
  held-out invalid patches (26 total for the blind experiment).
- Adaptive attempts are `split: adaptive` and never enter any controlled or
  probe invalid denominator.
- The probe experiment reports its own 16-patch invalid denominator, labelled
  explicitly as non-blind.
- `ambiguous` and infrastructure records are excluded from primary counts;
  infrastructure errors abort the run rather than becoming rejections.

## Truth labels (contract §27.9; D-049 two-phase annotation)

- **Phase 1 (before scoring)**: `label-patches` runs the offline oracle and the
  authoritative suite from a fresh baseline per patch, writes machine evidence
  (`results/labeling/<id>/...`) and a draft annotation; a human reviewer
  confirms `disposition`, `truth_label`, `reviewer`, `timestamp_utc`, and
  `recorded_patch_hashes` = {metadata_sha256, diff_sha256}. `run-controlled`
  and `run-heldout` refuse any patch without this.
- **Phase 2 (after scoring)**: a mechanical binder appends only
  `recorded_raw_record_hashes` (per grader, SHA-256 of the raw record) to the
  existing annotation; human fields are never rewritten (`grader_v2/bind_annotation_hashes.py`).
- A valid label requires unanimous evidence (oracle pass, authoritative pass,
  no hard scope violation, manual approval); an invalid label requires an
  oracle failure or a documented prohibited manipulation plus manual approval.
- Labels are never derived from grader rewards.

## Blind vs. probe (work-order W5)

- `clean-clone-reproduction` (72/72 completed records) is the genuinely blind
  held-out result and the primary published measurement.
- `probe-v1-blindspots` (46/46 records) is a **post-hoc targeted probe**: its
  patches were authored after seeing v1's behavior, and its counts are not a
  blind held-out estimate. Both experiments share the same frozen tag, the
  same labeling pipeline, and the same two-phase annotation protocol, so the
  contrast between them isolates the corpus-construction effect.

## Freeze protocol invariants

- The protected surface (253 files) must hash-match the lock before every
  `run-heldout`/`reproduce`; a single mismatch aborts the run (exit 5).
- Post-freeze tracked additions are allowed only under `results/`, `docs/`,
  `grader_v2/`, `adaptive_attempts/`, root-level `*.md`, and new `frozen_eval`
  task trees (D-051; `grader_audit/core/heldout.py`).
- Every `tasks/` input must be committed before scoring; untracked held-out
  input is a hard error.

## Sensitivity analyses (documented in prose, corpus never modified)

- Naive development false-reward rate: 15/18; excluding the three degenerate
  `scope-edit-prompt` patches (comment-only prompt edits) it is 15/15.
- The development corpus is frozen and cannot be relabeled or repaired
  (D-037); the caveat is stated rather than engineered away.
