# Green Isn't Correct: Auditing Reward Reliability in HUD Coding Environments

This repository is the **application-v2 release** (Track B, decision `D-053`): a
semantic hardening of the v1 grader reliability audit, shipped as an installable
package (`grader_audit` + `grader_v2`), a deployment container (`Dockerfile.hud`,
HUD v6 runtime), and a fully regenerable publication package.

The frozen v1 experiment (`grader-v1-frozen`, commit `c95a014`) remains
immutable: its 253 protected files are verified read-only by
`grader-v2 verify-v1-lock` (the only approved deltas are `pyproject.toml` and
`uv.lock`, which the application-v2 release extends by design).

## Headline numbers

| Experiment | Grader | Invalid rewarded | Valid rejected |
|---|---:|---:|---:|
| clean-clone-reproduction (non-blind reproduction of the frozen v1 experiment) | naive | 21 / 26 | 0 / 10 |
| clean-clone-reproduction | hardened v1 | 0 / 18 | 0 / 6 |
| probe-v1-blindspots (probe, labeled non-blind; the hidden-input sweep) | naive | 16 / 16 | 0 / 7 |
| probe-v1-blindspots (probe, labeled non-blind) | hardened v1 | 4 / 16 | 0 / 7 |
| v2-regression (frozen_eval split) | hardened v2 | 0 / 16 | 0 / 7 |
| v2-heldout (frozen_eval split, author-heldout set) | hardened v2 | 0 / 20 | 0 / 9 |

`results/report.md` is the authoritative numbers file for the frozen v1
experiment; `results/summaries/v2-regression.md` and
`results/summaries/v2-heldout.md` are generated from the v2 records
(schema `2.0`, grader version `hardened_v2`) and are byte-verified by
`grader-v2 publication --mode validate`.

## Why a v2

The probe (labeled non-blind) found 4 / 16 false rewards that the frozen v1
grader still produced: authoritative-overfit patches that pass every visible
test yet fail the task contract on hidden inputs. Hardened v2 composes the
frozen v1 mandatory checks (logical AND) with a **semantic suite** generated
from the task contract after the agent finishes:

- seeded, deterministic suites per task profile (`tinydb-docids-v1`,
  `tinydb-query-freeze-v1`), each replayable with `grader-v2 replay`;
- rewards are exactly `0.0` or `1.0`; infrastructure outcomes carry no reward;
- every v1 rejection propagates unchanged; a v1 pass earns `1.0` only when the
  semantic suite also passes;
- the v2 grading surface is frozen (`results/application-v2/freeze-v2.json`)
  and verified by `grader-v2 freeze-v2` / publication validation.

The v2-heldout set (4 invalid attacks across 4 mechanisms, 2 valid
alternatives, all authored after the v2 freeze and labeled `author-heldout`)
confirms the semantic suite rejects every attack it was designed to catch while
rewarding both valid alternatives.

## One concrete failure

`tinydb-missing-doc-ids/weaken-visible-tests` (development): the naive grader
rewards the visible-test-weakening patch `1.0` (exit 0), while hardened v1
rejects it via the read-only authoritative suite (`authoritative_tests_failed`).
On the held-out split, `tinydb-missing-doc-ids/import-shadow-tinydb-package`
behaves identically: rewarded by naive, rejected by hardened. The v2 semantic
suite goes further and rejects `tinydb-missing-doc-ids/list-only-skip-missing`
(an authoritative overfit that passes v1) with `semantic_tests_failed`; the
same attack against the deployed container scores `0.0` in the application
demo.

## What this audits

Central research question: how much can a coding-task grader reduce false
rewards without increasing false rejections of correct but non-canonical
solutions?

- **False reward**: an invalid patch earns reward `1.0` (weakened tests, skips,
  fixture manipulation, hard-coded overfit, scope violations, runtime
  manipulation).
- **False rejection**: a valid patch receives reward `0.0` (non-canonical
  implementation, multi-file refactor, generalized fix).

Naive grader: reward `1.0` iff the configured pytest command exits `0`. Hardened
grader: authoritative tests outside the editable workspace, exact node-ID
verification, immutable asset hashing, scope classification, stable structured
reason codes, and (v2) contract-derived semantic suites.

## Repository layout

| Path | Contents |
|---|---|
| `grader_audit/` | Frozen shared core: graders, orchestrator, runner, freeze machinery (immutable, v1 lock). |
| `grader_v2/` | Application-v2 release: semantic grader, v2 records/summaries, freeze + lock verification, HUD adapter, demo, publication. |
| `tasks/` | Five tasks (3 development, 2 frozen-eval) with baselines, authoritative/visible/oracle tests, and patch corpora. |
| `freeze/grader_v1.lock.json` | Immutable v1 freeze lock (verified read-only). |
| `results/` | Raw records, summaries, annotations, application-v2 baselines/freeze, publication manifest. |
| `docs/` | Decisions (`DECISIONS.md`), architecture, threat model, limitations, case studies. |

## Release gates (one command per gate, CI = the whole chain)

```powershell
$env:GRADER_AUDIT_REQUIRE_DOCKER='1'
uv sync --frozen
uv run ruff check .
uv run pyright
uv run pytest -q            # strict Docker mode: zero unexpected skips
uv run grader-audit doctor
uv run grader-v2 verify-v1-lock
uv run grader-v2 freeze-v2
uv run grader-v2 publication --mode validate
uv run grader-v2 demo       # HUD deployment container, end-to-end
```

The CI workflow (`.github/workflows/ci.yml`) runs the static gates on Linux,
the strict-Docker container integration plus the full release gate on Linux
with `GRADER_AUDIT_REQUIRE_DOCKER=1`, and the Windows orchestration paths
(no Linux containers).

## Documentation

- `docs/DECISIONS.md` — every decision (D-001 … D-058) with rationale.
- `docs/HUD_RUNTIME_ARCHITECTURE_V2.md` — the deployment runtime, runner
  isolation, and ten hardening questions answered.
- `docs/THREAT_MODEL.md`, `docs/LIMITATIONS.md`, `docs/CASE_STUDIES.md` —
  the audit narrative, validated (not overwritten) by publication.
