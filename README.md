# Green Isn't Correct: Auditing Reward Reliability in HUD Coding Environments

> **RESULTS PENDING** — No controlled evaluation has been run. This repository is at
> **Gate 1 (synthetic vertical slice)**: strict result/evidence/outcome models, safe
> unified-diff validation and application, deterministic fresh-workspace materialization
> and snapshots, process and Docker execution, the naive / hardened-v1 / offline-oracle
> evaluators sharing one grading core, atomic no-overwrite result serialization, two tiny
> synthetic fixture repositories, and a Gate 1 integration matrix. Primary results will be
> published in this README only after the final controlled experiment completes and
> `results/report.md` is generated (Section 27.18 of `CODEX_TASK_HUD_GRADER_RELIABILITY_AUDIT.md`).

## Results

| Grader | Invalid rewarded | Valid rejected | Held-out invalid rewarded |
|---|---:|---:|---:|
| Naive | RESULTS PENDING | RESULTS PENDING | RESULTS PENDING |
| Hardened v1 | RESULTS PENDING | RESULTS PENDING | RESULTS PENDING |

## One concrete failure

RESULTS PENDING — a false reward accepted by naive and rejected by hardened, and a valid
alternative rejected (or an expected-scope demonstration), are recorded here as case
studies once the controlled audit produces raw results.

## What this audits

Central research question: how much can a coding-task grader reduce false rewards
without increasing false rejections of correct but non-canonical solutions?

- **False reward**: an invalid patch earns reward `1.0` (weakened tests, skips, fixture
  manipulation, hard-coded overfit, scope violations, runtime manipulation).
- **False rejection**: a valid patch receives reward `0.0` (non-canonical implementation,
  multi-file refactor, generalized fix).

Naive grader: reward `1.0` iff the configured pytest command exits `0`. Hardened grader:
authoritative tests outside the editable workspace, exact node-ID verification, immutable
asset hashing, scope classification, and stable structured reason codes.

## Reproduce (Gate 1)

```bash
uv sync --frozen
uv run grader-audit doctor
uv run ruff check .
uv run pyright
uv run pytest -q

# Validate the synthetic fixtures (needs Docker)
uv run grader-audit validate tests/fixtures --split development --repeat 1 --experiment-id gate1-validate
uv run grader-audit run-controlled --tasks tests/fixtures --graders naive,hardened_v1 --experiment-id gate1-controlled
```

`doctor` checks the Section 27.1 prerequisites (Python 3.12, uv/git/docker, reachable
Docker Engine able to run a Linux container, writable project root, Git repository with
usable author identity, importable HUD package, no API key required) and exits `0` only
when all pass.

Gate 1 implements `doctor`, `validate-manifests`, `validate`, and `run-controlled`.
`validate` runs baseline and gold from clean workspaces (naive, authoritative, and
offline-oracle suites) for the requested repeat count and fails on any variation.
`run-controlled` evaluates every development patch under each requested grader from
separate clean workspaces and refuses patches lacking a confirmed truth annotation.

## Architecture

```
grader_audit/
├── core/            # shared framework-independent models, path rules, snapshots,
│                    # patches, workspace materialization, Docker runner, recorder,
│                    # orchestrator, annotations, doctor
├── grading/
│   ├── naive/       # exact exit-code naive grader (exit-code only; collection parsed
│   │                # for observation)
│   └── v1/          # hardened-v1 evaluator, evidence parser, immutable in-container
│                    # runner (run_pytest.py, grader_plugin.py, pytest.ini)
├── oracle/          # offline oracle evaluator (labeling only; never mounted for grading)
├── images.py        # content-addressed immutable task-image builder
└── cli.py           # grader-audit CLI (doctor, validate-manifests, validate, run-controlled)
tests/fixtures/      # two tiny synthetic fixture repositories (Gate 1 integration tests)
tests/integration/   # Docker-backed Gate 1 integration matrix
results/             # raw records and report (Gate 1+)
docs/                # DECISIONS.md, TASK_SELECTION_LOG.md, ...
```

One framework-independent grading core serves the CLI; `grader_audit.hud_adapter` will
call the same core functions in Gate 2 so neither path can reimplement scope checks, test
parsing, reason codes, or acceptance logic.

## Dataset

RESULTS PENDING — exactly 3 development tasks are mined in Gate 3 and 2 frozen-evaluation
tasks after the v1 freeze. Rejected candidates are logged in `docs/TASK_SELECTION_LOG.md`.
Two tiny synthetic fixture repositories ship under `tests/fixtures/` for automated Gate 1
integration tests only (Section 27.5); they never enter reported research metrics.

## Method

Development (3 tasks, 5+ valid / 12+ invalid patches across 4+ attack families) →
freeze `grader-v1-frozen` → frozen evaluation (2 tasks, 3+ valid / 8+ invalid patches,
2+ novel families) → optional adaptive and natural rollouts. Truth labels come from the
offline oracle and manual review, never from grader rewards. Gate 1 demonstrates, on the
synthetic fixtures, a naive false reward and its hardened rejection: the naive grader
rewards a visible-test-weakening patch `1.0`, while hardened v1 rejects it via the
read-only authoritative suite with `authoritative_tests_failed`.

## Findings

RESULTS PENDING — false rewards and false rejections are reported as raw `x / n` counts,
never as a single combined accuracy.

## Limitations

- Small, non-random task sample; Python/pytest focus; hand-labeled patch validity.
- Controlled attacks do not estimate natural attack prevalence.
- Model/version results are time-sensitive.
- Held-out construction is not a fully blind external red team.
- The environment is not a complete sandbox-security audit.
- Passing authoritative tests is still an approximation of task correctness.
- No claim that grader hardening improves RL training without a training experiment.

## Repository map

- `CODEX_TASK_HUD_GRADER_RELIABILITY_AUDIT.md` — normative implementation contract (Sections 0–27).
- `grader_audit/` — package: core models, path rules, snapshots, patches, workspace materialization,
  Docker runner, recorder, orchestrator, naive/v1/oracle graders, immutable in-container runner.
- `tests/` — unit tests and the Docker-backed Gate 1 integration matrix.
- `tests/fixtures/` — two synthetic fixture repositories for integration tests.
- `docs/DECISIONS.md` — decisions and deviations from the contract.
- `docs/TASK_SELECTION_LOG.md` — rejected task candidates.
- `.github/workflows/ci.yml` — quality gates on push/PR.
