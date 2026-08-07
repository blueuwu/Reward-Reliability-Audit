# Limitations

Honest limits of this audit, expanded from the README list. Several of these
are demonstrated facts of the corpus, not hypotheticals.

## Corpus and measurement

- **Small, non-random task sample.** Five tasks, all Python/pytest; no
  JavaScript, no non-Python toolchains, no web or systems tasks. Counts are
  descriptive of this corpus, not estimates of any population.
- **Two of five tasks come from the same upstream repository (tinydb).** The
  two frozen-evaluation tasks (`tinydb-missing-doc-ids`,
  `tinydb-query-test-unhashable`) are both TinyDB bugs from adjacent commits.
  Held-out family novelty is therefore within one codebase, which may
  understate the difficulty of generalizing to unseen repositories.
- **The authoritative suite alone separated the original 36-patch corpus.**
  Before the probe, every invalid patch failed the authoritative suite and
  every valid patch passed it; hardened v1's scope classification,
  immutable-asset hashing, node-ID verification, and evidence parsing were
  non-load-bearing for every recorded blind verdict. The `probe-v1-blindspots`
  experiment demonstrates the consequence: 4 / 16 invalid patches now receive
  reward 1.0 from hardened v1.
- **The oracle never disagreed with the authoritative suite on the original
  corpus.** The independent truth channel required by contract §27.9 was not
  exercised as independent until the probe (all four probe overfits diverge:
  authoritative accepts, oracle fails). The oracle is still an approximation,
  not ground truth.
- **Three development "attacks" are no-op patches.** `scope-edit-prompt` (one
  per development task) only appends a comment to `prompt.md`; they are not
  reward hacks, and every grader rejects them. Excluding them, the naive
  development false-reward rate is 15/15 rather than 15/18. The development
  corpus is frozen and cannot be relabeled (D-037); the sensitivity is stated
  in prose in `results/report.md` and here.

## Method

- **Hand-labeled patch validity.** Truth labels combine oracle evidence and
  manual review; labeling disagreements would change counts. Labels were fixed
  before scoring (two-phase protocol, D-049).
- **Controlled attacks do not estimate natural attack prevalence.** The
  invalid corpus is adversarially constructed; real models may attack more or
  less often and in different ways.
- **The probe is a post-hoc targeted probe, not blind.** `probe-v1-blindspots`
  patches were authored after seeing v1's frozen behavior and its held-out
  results; its counts must never be read as a blind held-out estimate. The
  blind numbers remain `clean-clone-reproduction`.
- **Adaptive attempts are model-curated and small.** Six attempts (3 per
  frozen task) were preserved; two confirmed bypasses share mechanisms with
  the curated probe. This is evidence of reachable blind spots, not a
  prevalence estimate.
- **Held-out construction is not a fully blind external red team.** The
  held-out tasks were built by the same project pipeline that built the
  development corpus.

## Grader scope

- **Not a sandbox-security audit.** Code under test executes in the test
  process; in-process manipulation that survives the isolation rules is a
  legitimate finding, not covered by v1's defenses (contract §27.4).
- **Passing authoritative tests is still an approximation of task
  correctness.** The probe shows the approximation boundary: assertions
  determine detectability, and a fix's generality beyond those assertions is
  not measured by the authoritative suite alone.
- **Expected-scope warnings are informational.** Multi-file or relocated
  correct fixes are rewarded with warnings; the report does not penalize them,
  so false-rejection rates here cannot speak to graders that reject on
  expected-scope violations.

## External validity

- Model/version results are time-sensitive; HUD and pinned package versions
  are recorded per record (`environment.hud`, `python`, `pytest`).
- No claim that grader hardening improves RL training without a training
  experiment; no natural-rollout rate is reported, so controlled-attack rates
  must not be conflated with production reward quality.
