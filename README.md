# Green Isn't Correct

## Auditing reward reliability in HUD coding environments

A coding agent can make the test command exit successfully without fixing the
task. It can weaken visible tests, skip collection, shadow an installed package,
hard-code observed cases, or alter files the task never allowed it to touch. If
the grader rewards only exit code `0`, each of those failures becomes a training
signal.

This project measures that problem and implements a hardened grader for HUD
coding environments. It includes five reproducible Python tasks, valid and
adversarial patch corpora, independent labeling evidence, immutable experiment
records, a HUD v6 deployment container, and an installable command-line package.

The central result is straightforward: a pytest-only grader rewarded **21 of 26
invalid patches** in the original experiment. The hardened v1 grader rejected all
26, but a later targeted probe found four contract-violating patches that could
still satisfy its fixed authoritative tests. Hardened v2 adds deterministic
semantic checks and rejects those observed bypasses while continuing to accept
the valid alternatives in this corpus.

> **Scope:** these are controlled experiments over five Python/pytest tasks, not
> an estimate of failure rates in production agent traffic. The targeted probe is
> explicitly non-blind. See [Limitations](#limitations) before generalizing the
> results.

## Results at a glance

| Evaluation | Grader | Invalid patches rewarded | Valid patches rejected |
|---|---|---:|---:|
| `clean-clone-reproduction`, development + blind frozen evaluation | pytest exit code | **21 / 26** | 0 / 10 |
| `clean-clone-reproduction`, development + blind frozen evaluation | hardened v1 | **0 / 26** | 0 / 10 |
| `probe-v1-blindspots`, targeted and non-blind | hardened v1 | **4 / 16** | 0 / 7 |
| Same probe corpus scored by v2 | hardened v2 | **0 / 16** | 0 / 7 |
| Expanded post-freeze evaluation | hardened v2 | **0 / 20** | 0 / 9 |

The expanded evaluation adds four invalid attacks and two valid solutions that
were authored after the v2 grading surface was frozen. Its totals include the
earlier frozen-evaluation corpus; it is not a second set of 29 entirely new
patches. With 20 invalid examples, a measured `0 / 20` false-reward count still
has a 95% Wilson upper bound of 16.1%—useful evidence, not proof that the true
error rate is zero.

Within the development split of `clean-clone-reproduction`, hardened v1's false
reward count was **0 / 18** and its false-rejection count was **0 / 6**. The
combined row above adds the genuinely blind frozen-evaluation split.

Authoritative result files:

- [`results/report.md`](results/report.md) — original experiment and non-blind
  v1 probe, including confidence intervals and patch-level records
- [`results/summaries/v2-regression.md`](results/summaries/v2-regression.md) —
  v2 regression over the pre-freeze corpus
- [`results/summaries/v2-heldout.md`](results/summaries/v2-heldout.md) — expanded
  post-freeze v2 evaluation
- [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) — interpretation boundaries and
  known confounds

## What was tested

The corpus contains three development tasks and two frozen-evaluation tasks,
drawn from real defects in Inflection, Schedule, TOMLI, and TinyDB. Each task
contains a baseline repository, a natural-language prompt, visible tests,
trusted authoritative tests, independent oracle tests, and labeled patches.

Invalid patches exercise these failure modes:

- visible-test deletion or weakening;
- skip injection and test-discovery manipulation;
- hard-coded and shape-specific overfitting;
- runtime monkeypatching and import shadowing;
- edits outside the declared task scope;
- fixes that satisfy fixed assertions while violating the broader task contract.

Valid patches include canonical fixes, alternative implementations, generalized
fixes, multi-file refactors, and relocated logic. Those variants matter because
a grader that rejects every unusual solution can suppress false rewards simply
by rejecting good work too.

Truth labels are fixed before scoring and combine oracle-test evidence with
manual review. Raw scoring records distinguish completed solution outcomes from
infrastructure failures; an infrastructure error receives no reward and is never
counted as a rejected solution.

## The failure that motivated v2

Consider `tinydb-missing-doc-ids/list-only-skip-missing`. The patch handles the
visible list-shaped input and passes every frozen v1 authoritative assertion, so
hardened v1 awards `1.0`. It still violates the task contract: equivalent
generator inputs raise `KeyError`.

That is a more important failure than simple test deletion. File protections and
hidden tests can stop direct tampering, but they cannot detect behavior the
trusted suite never exercises. The post-hoc probe produced four such
`authoritative-overfit` patches:

| Task | Overfit behavior missed by v1 |
|---|---|
| `tinydb-missing-doc-ids` | accepts only list-shaped IDs |
| `tinydb-missing-doc-ids` | updates memory but loses changes after storage reload |
| `tinydb-query-test-unhashable` | freezes only shallow arguments |
| `tinydb-query-test-unhashable` | supports only the visible container shapes |

All four pass hardened v1 and fail the oracle. Hardened v2 rejects them with the
structured reason `semantic_tests_failed`.

## How the hardened grader works

The agent edits only a fresh `/workspace`. The trusted grader and its test assets
remain outside that workspace.

```text
task baseline + visible tests + prompt
                  |
                  v
      fresh agent workspace (HUD SSH capability)
                  |
                  v
        final workspace state—not final prose
                  |
        +---------+-------------------+
        |                             |
        v                             v
  v1 mandatory checks        deterministic semantic suite
  - allowed-path policy      - generated after the rollout
  - immutable hashes         - seeded and replayable
  - exact test node IDs      - derived from the task contract
  - authoritative tests     - hidden from the agent
        |                             |
        +-------------+---------------+
                      v
          reward 1.0 only if both pass
```

Key properties:

- **Substance over exit status.** A successful visible test command is not
  sufficient evidence of correctness.
- **Fail closed.** Invalid inputs and infrastructure failures cannot become
  positive solution rewards.
- **Binary, monotonic composition.** Every v1 rejection remains a v2 rejection;
  a v1 pass earns `1.0` only if the semantic suite also passes.
- **Replayable semantic evidence.** Each suite records its profile, seed, case
  count, failures, and SHA-256 digest and can be regenerated with
  `grader-v2 replay`.
- **Fresh state per rollout.** The workspace is restaged before each task, and
  the grader evaluates the resulting filesystem rather than trusting the
  agent's final response.
- **Separated trust zones.** The agent sees `/workspace`; authoritative tests,
  semantic-suite generation, and grading code stay in the trusted process.

The full process and trust model is documented in
[`docs/HUD_RUNTIME_ARCHITECTURE_V2.md`](docs/HUD_RUNTIME_ARCHITECTURE_V2.md).
The attack surface and explicit exclusions are in
[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

## Run it

### Requirements

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Git
- Docker for container integration tests and the end-to-end HUD demo

Install the locked environment:

```bash
uv sync --frozen
```

Run the fast checks that do not require Docker:

```bash
uv run ruff check .
uv run pyright
uv run pytest -q tests grader_v2/tests --ignore=grader_v2/tests/test_deployment_container.py
```

Validate the frozen evidence and generated publication files:

```bash
uv run grader-v2 verify-v1-lock
uv run grader-v2 publication --mode validate
```

With Docker running, execute the full test suite and deployment demo:

```bash
uv run grader-audit doctor
uv run pytest -q
uv run grader-v2 demo
```

For strict CI-equivalent Docker enforcement, set
`GRADER_AUDIT_REQUIRE_DOCKER=1` before `doctor` and `pytest`. The Linux CI job
uses that setting so missing container coverage fails instead of being skipped.

Useful entry points:

```bash
uv run grader-v2 --help
uv run grader-v2 reproduce --help
uv run grader-v2 eval-v2 --help
uv run grader-v2 replay --help
```

## Reproducibility and evidence

The v1 experiment is preserved by the annotated tag `grader-v1-frozen` and a
content lock covering 253 protected files. `grader-v2 verify-v1-lock` verifies
that surface without rewriting it; the two permitted release changes are
recorded in the decision log.

V2 records use schema `2.0` and identify the grader version, task, patch, truth
label, environment, reason codes, and semantic-suite evidence. Generated
summaries are checked byte-for-byte against those records by
`grader-v2 publication --mode validate`. The v2 grading surface has its own
snapshot at [`results/application-v2/freeze-v2.json`](results/application-v2/freeze-v2.json).

This separation is intentional:

- raw records are measurement evidence;
- generated summaries are reproducible views of those records;
- manual interpretation lives in documentation and does not alter either one.

## Repository map

| Path | Purpose |
|---|---|
| [`grader_audit/`](grader_audit/) | Frozen v1 grading core, orchestration, runners, and record machinery |
| [`grader_v2/`](grader_v2/) | Semantic grader, HUD adapter, replay, release validation, and demo |
| [`tasks/`](tasks/) | Five task baselines, tests, metadata, and valid/adversarial patch corpora |
| [`freeze/grader_v1.lock.json`](freeze/grader_v1.lock.json) | Content lock for the protected v1 surface |
| [`results/`](results/) | Raw records, labels, generated summaries, freeze data, and publication manifest |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Core v1 architecture and evidence flow |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Decision log with rationale and provenance |
| [`docs/EXPERIMENT_PROTOCOL.md`](docs/EXPERIMENT_PROTOCOL.md) | Experimental procedure and metric definitions |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | Linux, Docker, and Windows verification gates |

## Limitations

The most important boundaries are:

- The sample is five Python/pytest tasks; two evaluation tasks come from
  adjacent TinyDB commits. The counts describe this corpus only.
- The adversarial patches are controlled attacks, not a measurement of how
  often coding agents attempt these behaviors naturally.
- The v1 blind-spot probe was designed after inspecting the frozen grader. It
  demonstrates reachable failures but cannot provide a blind prevalence estimate.
- Labels depend on oracle tests and manual review. The oracle is an independent
  evidence channel, not perfect ground truth.
- This is a grader-reliability study, not a general sandbox-security audit.
- No policy-optimization experiment is included, so the project does not claim
  improved downstream learning outcomes.
- Passing deterministic semantic tests remains an approximation of the task
  contract. V2 closes the observed gaps; it does not establish complete semantic
  correctness.

See [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) for the full discussion,
including confidence intervals, corpus confounds, and the original development
set's three no-op scope patches.
