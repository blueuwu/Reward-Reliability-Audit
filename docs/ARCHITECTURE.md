# Architecture

## Single grading core

One framework-independent grading core serves both the CLI and the HUD
adapter (contract §27.3; D-028):

- `grader_audit/core/` — models, path rules, snapshots, patches, workspace
  materialization, Docker runner, recorder, orchestrator, annotations,
  doctor, heldout orchestration, reporting.
- `grader_audit/grading/naive/evaluator.py` — exact exit-code naive grader.
- `grader_audit/grading/v1/` — hardened v1 evaluator, evidence parser,
  immutable in-container runner (`runner/run_pytest.py`,
  `runner/grader_plugin.py`, `runner/pytest.ini`).
- `grader_audit/oracle/evaluator.py` — offline oracle (labeling only; never
  mountable from a grader or adaptive container).
- `grader_audit/hud_adapter/` — thin `EvaluationResult` mapping over the same
  core `evaluate_grader`/`prepare_task` functions; it never reimplements
  grading logic.

The naive and hardened records for one patch MUST have identical pre-grade
workspace snapshot hashes; the orchestrator enforces this
(`grader_audit/core/orchestrator.py::_verify_cross_grader_hashes`) and the
report refuses mismatches.

## CLI / HUD split

- `grader_audit/cli.py` — the `grader-audit` Typer CLI (`doctor`,
  `validate-manifests`, `validate`, `run-controlled`, `build-images`,
  `label-patches`, `freeze`, `run-heldout`, `report`, `reproduce`).
- `env.py` + `tasks.py` — HUD v6 templates and task rows; the HUD adapter
  grades the workspace state left by the agent by calling the same core
  functions the CLI uses (D-023, D-028).
- `grader_v2/` — post-freeze tooling only: `cli.py` (v2 reproduce/report
  drivers, D-052), `reporting.py` (path-tolerant report resolver),
  `bind_annotation_hashes.py` (phase-2 annotation binder). Nothing in
  `grader_v2/` modifies frozen v1.

## Container execution contract

Hardened v1 and the oracle run the exact same container contract
(`grader_audit/grading/v1/suite.py`):

- Command: `/usr/local/bin/python -I /opt/grader/run_pytest.py /opt/grader`
  (or `/opt/oracle`), cwd `/workspace`.
- Environment (explicit allowlist): `EVIDENCE_DIR`, `WORKSPACE_ROOT`,
  `SOURCE_ROOTS`, `EXPECTED_NODEIDS`; the runner sanitizes everything else
  (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONHASHSEED=0`, `PYTHONUTF8=1`).
- Mounts: workspace rw at `/workspace`; grader assets read-only at
  `/opt/grader` (or `/opt/oracle`); a fresh grader-controlled evidence dir.
- The runner inserts only manifest-declared `source_roots` into `sys.path`,
  runs the suite by absolute path with an immutable config
  (`-c <root>/pytest.ini --confcutdir <root>`), and writes
  `report.json` plus a machine-readable `GRADER_SUMMARY` line.
- The naive grader executes the manifest-configured command
  (`python -m pytest tests -q`, cwd `/workspace`) in the task image with
  resource limits.

Images are content-addressed per task (`tasks/*/image.lock.json`, D-035);
all three real tasks currently share one digest because their locked
dependency sets are byte-identical (D-036).

## Conformance answers (contract §27.21)

| Question | Answer |
|---|---|
| Single grading core used by CLI and HUD? | `grader_audit/core/orchestrator.py::evaluate_grader` / `prepare_task`; CLI at `grader_audit/cli.py`, HUD at `grader_audit/hud_adapter/evaluator.py` (D-028) |
| Exact command each naive grader executes? | per task `grading.naive.argv` in `tasks/*/task.yaml` (e.g. `["python", "-m", "pytest", "tests", "-q"]`), executed by `grader_audit/grading/naive/evaluator.py` |
| Where authoritative assets mount, permissions? | `tasks/*/authoritative_tests` → `/opt/grader/tests`, read-only bind mount, host-side SHA-256 before/after every run (`grader_audit/grading/v1/suite.py`, `evaluator.py`) |
| Where oracle assets stored; absence from agent runs tested? | `tasks/*/oracle_tests`; mounted only by `OracleContext` in `label-patches`; integration tests assert no oracle mount for naive/hardened (`tests/integration/`) |
| Pre-grade workspace hash per naive/hardened pair? | every record's `workspace.pre_grade_sha256`, e.g. `results/raw/clean-clone-reproduction/{naive,hardened_v1}/frozen_eval/tinydb-missing-doc-ids/gold.json` |
| Exact expected node IDs per task? | `grading.hardened_v1.expected_nodeids` and `grading.oracle.expected_nodeids` in each `tasks/*/task.yaml` |
| Image digest per record? | `environment.docker_image_digest` in every raw record; locked in `tasks/*/image.lock.json` (D-035) |
| Which records establish every patch truth label? | `results/labeling/heldout-gate5-labeling/` and `results/labeling/probe-labeling/` (oracle + authoritative node outcomes); confirmed in `results/annotations/<experiment>/` |
| Tag and protected-tree hash freezing v1? | `grader-v1-frozen` (commit `c95a014`), protected-tree SHA-256 `eb653ad81298f999d37914ceea2440995bd28f545db23b25f4642f247fabe046` in `freeze/grader_v1.lock.json` |
| Which commits introduced held-out assets after the tag? | `3d09654` (Gate 5 held-out task inputs), `b363a80` (probe patches), `1a8e4ed` (probe annotations), `70f6c70` (probe results) |
| Planned matrix ≥ 5 tasks / 8 valid / 20 invalid with required splits/families? | 5 tasks; development 6/18, frozen_eval 7/16 (post-probe); families per `results/report.md` tables |
| Zero infrastructure / invalid-input records in final matrix? | `results/raw/clean-clone-reproduction/` (72/72 completed) and `results/raw/probe-v1-blindspots/` (46/46 completed); `results/summaries/*.md` render `COMPLETE` |
| Final report regenerable offline? | `uv run grader-audit report` / `uv run python -m grader_v2.cli report` from raw records; no network or API keys (D-052 on Windows artifact paths) |
| Clean-clone `reproduce` completes? | `uv run python -m grader_v2.cli reproduce --tasks tasks --experiment-id <new-id>` (documented in README; D-052) |
