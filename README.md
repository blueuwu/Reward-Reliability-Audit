# Green Isn't Correct: Auditing Reward Reliability in HUD Coding Environments

> **Results published (Gate 6)** — the final controlled experiment (`clean-clone-reproduction`,
> 72/72 completed records, 30/30 stable baseline/gold validation runs) finished under
> `grader-v1-frozen`; `results/report.md` (Section 27.18) is the authoritative numbers file.
> Hardened v1 reduces the naive grader's 21/26 false rewards to 0/26 with zero false
> rejections (0/10).

## Results

Final experiment: `clean-clone-reproduction` (frozen tag `grader-v1-frozen`, 3 development
tasks, 2 held-out frozen-evaluation tasks). See `results/report.md` for per-split metrics,
Wilson intervals, attack-family detection, and the full case inventory.

| Grader | Invalid rewarded | Valid rejected | Held-out invalid rewarded |
|---|---:|---:|---:|
| Naive | 21 / 26 | 0 / 10 | 6 / 8 |
| Hardened v1 | 0 / 26 | 0 / 10 | 0 / 8 |

## One concrete failure

`inflection-titleize/weaken-visible-tests` (development): the naive grader rewards the
visible-test-weakening patch `1.0` (exit 0), while hardened v1 rejects it via the
read-only authoritative suite (`authoritative_tests_failed`). On the held-out split,
`tinydb-missing-doc-ids/import-shadow-tinydb-package` behaves identically: rewarded by
naive, rejected by hardened.

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

## Reproduce (final, Gate 6)

```bash
uv sync --frozen
uv run grader-audit doctor
uv run ruff check .
uv run pyright
uv run pytest -q

# Full offline reproduction of the final experiment (needs Docker)
uv run python -m grader_v2.cli reproduce --tasks tasks --experiment-id <new-id>
```

The frozen v1 `reproduce`/`report` tools record artifact paths relative to the resolved
(result-root-absolute) working directory; the frozen report generator requires
repository-relative paths and refuses those records on Windows orchestration hosts
(D-052, `docs/DECISIONS.md`). `grader_v2/` reuses the frozen pipeline verbatim and only
replaces the report's artifact-path resolver; `results/report.md` is byte-identical to
`results/summaries/clean-clone-reproduction.md`.

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

3 development tasks (`inflection-titleize`, `schedule-repr-partial-job`, `tomli-type-error`;
6 valid / 18 invalid patches) and 2 held-out frozen-evaluation tasks
(`tinydb-missing-doc-ids`, `tinydb-query-test-unhashable`; 4 valid / 8 invalid patches,
2 families novel to development). Rejected candidates are logged in
`docs/TASK_SELECTION_LOG.md`. Two tiny synthetic fixture repositories ship under
`tests/fixtures/` for automated integration tests only (Section 27.5); they never enter
reported research metrics.

## Method

Development (3 tasks, 5+ valid / 12+ invalid patches across 4+ attack families) →
freeze `grader-v1-frozen` → frozen evaluation (2 tasks, 3+ valid / 8+ invalid patches,
2+ novel families) → optional adaptive and natural rollouts. Truth labels come from the
offline oracle and manual review, never from grader rewards. Gate 1 demonstrates, on the
synthetic fixtures, a naive false reward and its hardened rejection: the naive grader
rewards a visible-test-weakening patch `1.0`, while hardened v1 rejects it via the
read-only authoritative suite with `authoritative_tests_failed`.

## Findings

False rewards and false rejections are reported as raw `x / n` counts, never as a single
combined accuracy. Final counts (`clean-clone-reproduction`, both splits combined):
naive rewarded 21/26 invalid patches (80.8%, 95% Wilson [0.621, 0.915]) and rejected
0/10 valid; hardened v1 rewarded 0/26 invalid (95% Wilson [0.000, 0.129]) and rejected
0/10 valid. On the held-out split hardened v1 rejected 8/8 invalid instances (5/5 attack
families, 5/5 all-instances) versus naive 2/8 (1/5 families); family-level detection
tables and reason-code counts are in `results/report.md`.

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
