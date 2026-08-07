# Codex handoff — 2026-08-07

## Direct-resume outcome

Codex resumed this handoff directly (without OpenCode/DeepSeek) and completed
the second audit draft. The corrected Gate 4 implementation and adversarial
tests are green and remain intentionally uncommitted for final review. No tag,
freeze lock, canonical evidence, held-out corpus, or Gate 5 work was created.

Final verification:

```text
uv sync --frozen: pass
ruff: pass
strict pyright: pass
targeted Gate 4/heldout/report/reproduce suite: 91 passed
full pytest: 331 passed, 22 skipped (Docker unavailable)
grader-audit --help: pass; run-heldout/report/reproduce listed
git diff --check: no whitespace errors (only existing LF/CRLF warnings)
```

The full suite must set `GIT_CEILING_DIRECTORIES` to the project root when its
pytest temp directory is placed under `.test-work`; otherwise temporary test
workspaces incorrectly inherit the parent repository's Git context. The three
tests affected by that harness placement passed in isolation and in the full
suite once the ceiling was set.

## Original objective (pre-resume snapshot)

Continue `CODEX_TASK_HUD_GRADER_RELIABILITY_AUDIT.md` with OpenCode DeepSeek as
the implementer and Codex as the reviewer. Finish the corrected Gate 4 before
creating a canonical freeze or restarting Gate 5.

## Repository state at stop

- Project root: `D:\Projects\rlhf 2`
- Branch: `master`
- HEAD: `a2a0660923f753314e22bcbc10bd7603e1e6c14e`
- Canonical `grader-v1-frozen` tag: absent
- Only freeze-related tag: `grader-v1-frozen-incomplete-20260806`
- `freeze/grader_v1.lock.json`: absent
- Active tasks: development-only; no heldout corpus is present
- No commits or tags were created during this work session
- The saved worktree contains 20 uncommitted paths (11 modified, 9 untracked),
  including the two handoff-document changes. Do not reset, restore, clean, or
  discard them.

Modified:

```text
docs/CODEX_HANDOFF_2026-08-06.md
docs/DECISIONS.md
grader_audit/cli.py
grader_audit/core/annotations.py
grader_audit/core/freeze.py
grader_audit/core/orchestrator.py
grader_audit/core/recorder.py
tests/integration/test_cli.py
tests/integration/test_gate3.py
tests/integration/test_gate4.py
tests/test_freeze.py
```

Untracked draft files:

```text
docs/CODEX_HANDOFF_2026-08-07.md
grader_audit/core/heldout.py
grader_audit/core/paths.py
grader_audit/core/reporting.py
grader_audit/core/reproduce.py
tests/integration/gate5_fixtures.py
tests/test_heldout.py
tests/test_reporting.py
tests/test_reproduce.py
```

## OpenCode state

- Session: `ses_028d82de5ffekp5rz5c5aFsTgk`
- Model: `opencode-go/deepseek-v4-flash`, variant `high`, agent `build`, auto
- The supervised worker PID 21012 and its children were deliberately stopped.
- No pytest/ruff/pyright subprocess remains.
- The user-started OpenCode TUI PID 18572 was left open and idle; it was not
  terminated because it belongs to the user's separate terminal.
- Resume the same session; do not fork it.

Suggested resume prompt:

```text
Continue the interrupted second Codex audit from docs/CODEX_HANDOFF_2026-08-07.md.
Preserve all uncommitted work. Fix the current reporting-test lint/type errors,
finish every unresolved blocker and adversarial test in the handoff, then run
ruff, pyright, targeted tests, and full pytest. Do not commit, tag, refreeze,
reintroduce heldout assets, or start Gate 5 until Codex reviews the result.
```

## Gate status

- Gates 0–3: complete.
- Original Gate 4 freeze: invalid/incomplete and archived by the annotated tag
  `grader-v1-frozen-incomplete-20260806`.
- Gate 4 correction: active, uncommitted, not approved.
- Historical Gate 5 attempt: noncanonical and removed from the active tree;
  preserved in Git history only.
- Gate 5 and Gate 6: not started canonically.

## Last green checkpoint

Before the second Codex audit edits, the first implementation pass achieved:

```text
ruff: pass
strict pyright: pass
targeted Gate 4/heldout/report/reproduce suite: 67 passed
full pytest: 330 passed in 176.14s
```

Codex rejected that green pass because important behaviors were optional,
miscounted, or not actually tested.

## Interrupted checkpoint (pre-resume)

DeepSeek was stopped while updating `tests/test_reporting.py` for the second
audit. The current tree is expected to fail static checks until that edit is
finished:

```text
ruff: FAIL
  tests/test_reporting.py:241 F821 Undefined name `cast`

pyright: FAIL (5 errors in tests/test_reporting.py)
  _bind_all_annotations is unused
  cast is undefined
  partially unknown mapping types around recorded_raw_record_hashes

pytest: not run after the latest second-audit edits
```

`git diff --check` emitted no whitespace errors; it only printed the existing
LF-to-CRLF warnings for several tracked files.

## Work implemented during the first audit pass

The uncommitted draft includes:

- Normative roots in `grader_audit/core/paths.py`, including `results/raw`.
- Exact patch-record paths
  `<grader>/<split>/<task>/<patch_id>.json`.
- Official CLI commands `run-heldout`, `report`, and `reproduce`.
- Plan reservation before evaluation for validation/controlled/heldout flows.
- Annotated-tag, tagged-lock-byte, parent/source-head, and protected-hash checks.
- Post-freeze heldout task introduction checks.
- Planned-matrix and artifact validation in reporting.
- Initial heldout/report/reproduce adversarial tests.
- First-pass test counts: 21 heldout tests, 14 reporting tests, and 5 reproduce
  tests; the second audit began expanding these.

## Second audit work partially implemented

DeepSeek began implementing:

- Atomic two-phase annotation binding via
  `grader_audit/core/annotations.py::bind_raw_record_hashes`.
- Record-hash orchestration via
  `grader_audit/core/heldout.py::bind_patch_raw_hashes`.
- Exact added-root enforcement for post-freeze tracked and untracked files.
- Exact two-grader enforcement in `run-controlled`.
- Relative raw/annotation root resolution against `project_root`.
- Development annotation preflight and both-split image-lock verification in
  `reproduce`.
- Mandatory report annotation/raw-record-hash validation.
- Validation-plan duplicate/phase/artifact checks.
- Unique corpus counts, per-grader heldout metrics, subtype denominators, and
  report case inventory.
- Hardened controlled/validation evidence eligibility in `freeze.py`.

These changes were interrupted before tests were repaired or reviewed. Treat
them as drafts, not completed work.

## Blockers recorded at stop (resolved by the direct resume)

1. Finish the interrupted `tests/test_reporting.py` edit and restore ruff and
   strict pyright first.
2. `run_heldout` currently calls `bind_patch_raw_hashes` inside the per-patch
   loop (`grader_audit/core/heldout.py`, currently around line 476). Move all
   annotation binding until after the entire heldout matrix is scored. Binding
   early dirties tracked annotations and would make later raw records report
   `git.worktree_dirty: true`.
3. Confirm `run-controlled` also binds only after every record is written. Its
   current placement is after the evaluation loops and is probably correct.
4. Untracked heldout task inputs must map to invalid input (exit 2), not a freeze
   violation (exit 5). The new generic added-file verifier may currently inspect
   an untracked `tasks/**` tree too early. Test the exact exit mapping.
5. Heldout annotations must themselves be tracked and committed. `git diff
   --quiet` ignores an untracked annotation, so add an explicit tracked-file
   check and an adversarial test.
6. Verify the atomic raw-hash binder preserves every human field byte-for-byte
   in meaning (`reviewer`, timestamp, truth, disposition, reason, notes), only
   adds/merges `recorded_raw_record_hashes`, and refuses conflicting existing
   hashes. It must not rewrite raw records.
7. Normal `report` and `reproduce` must actually pass/resolve annotations and
   require exact raw record hashes for both graders. Do not leave this optional.
8. Finish validation-plan adversarial tests: duplicate planned cells, missing,
   extra, wrong manifest, wrong path, bad artifact, and EvaluationRecord
   phase-smuggling outside the validation tree.
9. Finish freeze-evidence adversarial tests: duplicate plan and actual identity,
   wrong record location, pristine mismatch, unsafe/out-of-experiment artifact,
   duplicate/extra validation record, and validation manifest mismatch.
10. Review the rewritten metrics with exact rendered assertions:
    unique patch counts (not two grader rows), heldout instance/family detection
    separately per grader, and valid-subtype rejected numerator/denominator even
    when the numerator is zero.
11. The reproduction tests are still mostly preflight tests. Add a fully
    monkeypatched success-path orchestration test proving plan reservation,
    step order, both grader matrices, binder-before-report, unchanged tag, no
    model/network call, and no failure caused by the command's own output.
12. Review report case inventory paths and ensure every false reward, false
    rejection, infrastructure error, and invalid-input record is named without
    mixing manual interpretation into generated facts.
13. Re-run all checks independently; do not rely on DeepSeek's prose summary.

## Required verification order

After completing the second audit:

```powershell
uv sync --frozen
uv run ruff check .
uv run pyright
uv run pytest tests/test_heldout.py tests/test_reporting.py tests/test_reproduce.py tests/test_freeze.py tests/integration/test_gate4.py -q
uv run pytest -q
uv run grader-audit --help
```

Then Codex must inspect the complete diff and test quality. Do not commit yet.

## Sequence after code approval

1. Commit the corrected code/tests/docs only after independent review.
2. Create fresh canonical development validation and controlled evidence under
   `results/raw/` with committed pre-run annotations and clean provenance.
3. Mechanically bind raw record hashes after all scoring, review and commit the
   annotations/evidence, and return to a clean tree.
4. Run all Gate 4 quality/precondition checks.
5. Execute the exact normative freeze command and independently verify its
   annotated tag, lock bytes, parent, protected-tree hash, and result-set hash.
6. Only after the corrected canonical freeze may two genuinely fresh heldout
   tasks be introduced. Do not reuse the previously observed noncanonical
   heldout outcomes as canonical evidence.

## HUD review rule

Continue using the `hud-environment-builder` skill. Treat contamination,
reward-hacking paths, prompt–grader misalignment, task-family diversity, and
within-group signal as release blockers. Relevant doctrine:
`/v6/reference/advice` and `/v6/reference/graders`.
