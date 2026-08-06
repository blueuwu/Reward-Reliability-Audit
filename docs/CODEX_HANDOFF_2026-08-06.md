# Codex handoff — 2026-08-06

## Objective

Continue implementing `CODEX_TASK_HUD_GRADER_RELIABILITY_AUDIT.md` with OpenCode using
`opencode-go/deepseek-v4-flash` (`high`, `build`, `--auto`). The user requested full
automation through Gate 4, but asked to stop for the day and preserve the exact state.

## Current state

- Gates 0–3 are complete.
- Gate 4 is started but incomplete.
- There are no Git commits and no tags yet. The branch is `master` with an unborn HEAD.
- The worktree intentionally remains uncommitted. Do not discard or reset any files.
- No held-out/frozen-eval tasks, `grader_v2/`, adaptive attempts, reports, or model rollouts
  have been created.

## Gate 3 independently verified

- Exactly three real MIT-licensed development tasks:
  - `inflection-titleize`
  - `tomli-type-error`
  - `schedule-repr-partial-job`
- Corpus: 6 valid and 18 invalid patches across 5 attack families.
- Task-specific image locks exist and Docker images were built.
- All 3 baseline/gold pairs were stable at repeat 3 (18 records).
- Final controlled matrix: 48/48 `completed`, zero infrastructure/invalid-input outcomes.
- Naive rewarded 15/18 invalid patches; hardened v1 rewarded 0/18 invalid patches.
- Both graders accepted all 6 valid patches.
- Confirmed annotations matched patch hashes before the final controlled run.
- Rejected upstream candidates are documented in `docs/TASK_SELECTION_LOG.md`.
- Final independent checks before Gate 4:
  - `uv sync --frozen`: pass
  - `grader-audit doctor`: pass
  - `ruff check .`: pass
  - strict `pyright`: pass
  - `grader-audit validate-manifests tasks --require-minimums`: pass
  - full pytest: 261 passed in about 125 seconds

## Gate 4 OpenCode session

- Session ID: `ses_028d82de5ffekp5rz5c5aFsTgk`
- Title: `HUD audit Gate 4`
- The active process was deliberately stopped at the user's request.
- Resume the same session, do not fork unless necessary.

Suggested PowerShell resume command:

```powershell
& 'C:\nvm4w\nodejs\node_modules\opencode-ai\bin\opencode.exe' run `
  --session ses_028d82de5ffekp5rz5c5aFsTgk `
  --model opencode-go/deepseek-v4-flash `
  --variant high --agent build --auto `
  'Continue Gate 4 from docs/CODEX_HANDOFF_2026-08-06.md. Finish implementation, tests, evidence regeneration, baseline/evidence commits, normative freeze commit, annotated tag, and independent verification. Do not start Gate 5.'
```

## Gate 4 implementation progress

DeepSeek completed the Gate 4 contract/codebase audit and added:

- `grader_audit/core/freeze.py` (currently about 876 lines), covering freeze preconditions,
  protected-file hashing, development result-set integrity, lock construction, Git commit,
  and annotated-tag handling.
- Freeze CLI wiring in `grader_audit/cli.py`.

Latest checks on this incomplete implementation:

- Ruff: pass.
- Pyright: **one remaining error** at `grader_audit/core/freeze.py:73`:
  `Type of "item" is unknown (reportUnknownVariableType)` in `_plan_entries`.
- Freeze unit/integration tests have not yet been written.
- No Git mutation has occurred.

## Critical evidence issue to preserve

Existing Gate 3 result records were generated before the repository had a first commit, so
they contain `git.data_commit` equal to forty zeroes and `git.worktree_dirty: true`.
Do not freeze those records as final evidence.

Required sequence:

1. Finish freeze implementation and tests while the tree is uncommitted.
2. Run quality gates.
3. Create a comprehensive baseline commit tracking all required Gate 0–3 source, tests,
   task inputs, annotations, raw records/logs/evidence, documentation, and locks. Exclude
   caches, temporary files, secrets, and mining clones.
4. On the clean committed tree, regenerate repeat-3 validation and the full development
   controlled matrix so records contain the real full HEAD SHA and
   `git.worktree_dirty: false`.
5. Confirm annotations remain `confirmed` and their recorded diff/metadata hashes match
   before scoring. Preserve zero infrastructure/invalid-input outcomes.
6. Commit the regenerated validation/controlled evidence, returning to a clean tree.
7. Execute exactly:
   `grader-audit freeze --grader hardened_v1 --git-tag grader-v1-frozen`
8. The freeze command must write and commit only `freeze/grader_v1.lock.json`, with commit
   message exactly `Freeze hardened grader v1`, create annotated tag
   `grader-v1-frozen`, verify the tag resolves to that commit, and emit the full tag commit
   SHA plus protected-tree hash.
9. Independently verify tag, commit contents, lock hashes, result-set hash,
   protected-tree hash, parent evidence commit, and clean worktree.

## Normative Gate 4 constraints

Read Sections 5, 14, 20, 21, 27.14, 27.15, and Gate 4 in Section 28 before continuing.
The freeze command must refuse before mutation for an existing tag, dirty worktree,
missing author configuration, failed quality/preconditions, incomplete or untracked
development evidence, held-out content, inconsistent annotations/results, or hash
mismatch. The lock protects all paths specified in Section 27.14 and records per-file
hashes, aggregate protected-tree hash, development result-set hash, source HEAD, package
versions, and UTC timestamp.

Do not begin Gate 5, create or inspect held-out attacks, add `grader_v2`, generate final
reports, or run model rollouts.

