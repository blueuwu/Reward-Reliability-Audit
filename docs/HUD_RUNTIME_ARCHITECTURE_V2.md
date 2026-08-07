# HUD Runtime Architecture — application-v2 (deployment container)

> Decision record for hardening Gate B. Supersedes the Section 27.13 local-smoke
> architecture for deployment purposes. Track B (`docs/DECISIONS.md` D-053):
> new HEAD is the application-v2 release; `grader-v1-frozen` remains the
> immutable historical evaluator. Official v6 references: [Environment](https://docs.hud.ai/v6/reference/environment),
> [Capabilities](https://docs.hud.ai/v6/reference/capabilities), [Tasks](https://docs.hud.ai/v6/reference/tasks),
> [Runtime](https://docs.hud.ai/v6/reference/runtime).

## 1. Answers to the ten required questions

| # | Question | Answer |
|---|---|---|
| 1 | Where does the agent shell/files capability run? | Inside the deployment container, as `env.workspace("/workspace", network=False, track_files=False)` — the v6 `ssh/2` capability served by an [bwrap](https://man.archlinux.org/man/bwrap.1) isolated SSH daemon confined to the workspace root. |
| 2 | What path is the editable workspace? | `/workspace` (deployment, `GRADER_AUDIT_IN_CONTAINER=1`); `.hud-workspace` (host local runs). The template stages a fresh declared baseline into this root per rollout (`WorkspaceManager.stage_fresh_root`). |
| 3 | Where do authoritative and semantic grader assets live? | The task trees (including `authoritative_tests/`) are baked into the image at `/app/tasks/<task-id>/`. The immutable grader payload is baked at `/opt/grader` (`run_pytest.py`, `pytest.ini`, `grader_plugin.py`). For a grading run, the trusted environment process binds the task's authoritative tests onto `/opt/grader/tests` (protected `run_test_suite` mounts; in-container `InContainerRunner` implements the mount as a trusted-process copy). Semantic suites are generated after the agent finishes into a grader-controlled directory and bound onto `/opt/grader/tests` the same way. Oracle assets are **not** baked into the deployment image at all — the oracle suite is a host-side labeling facility mounted only during offline labeling runs (contract §27.9, D-024). |
| 4 | Why can the agent not read or modify those assets? | The agent's only capability is the bwrap workspace namespace: it mounts `/workspace` (rw) plus system directories (ro) and tmpfs. `/app`, `/opt`, `/freeze`, the Docker socket, and the host are not mounted in that namespace, so the agent session literally cannot list or open grader assets. The control channel exposes no other capability. This is capability-level isolation (trust zones of §27.4), not a kernel sandbox audit; the threat model in `docs/THREAT_MODEL.md` says so explicitly. |
| 5 | Where are task dependencies installed? | Into the deployment image at build time from the task's pinned `requirements.lock` (`uv pip install --system --require-hashes`), pinned with hashes per D-030. One deployment image per task dependency digest (D-036: all five tasks share one byte-identical locked dependency set, so the release ships one image); the workspace source (`src/`) is staged per rollout, never baked as importable at grading time. |
| 6 | Which process executes untrusted task code? | The immutable in-container pytest runner: `python -I /opt/grader/run_pytest.py /opt/grader` (and the semantic suite through the same runner), an isolated argument-array subprocess with a sanitized allowlist environment (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONHASHSEED=0`, only manifest-declared `SOURCE_ROOTS` on `sys.path`), launched by the trusted environment process after the agent turn. No `shell=True` anywhere. |
| 7 | How are time, CPU, memory, process, and network limits enforced? | Time: per-command `timeout_seconds` from the task manifest enforced in both runners (`subprocess` timeout; `docker run` timeout). Memory/pids: declared per task and applied via `docker run --memory/--pids-limit` when the deployment container is started; inside the container the same manifest values are recorded in evidence. Network: `env.workspace(network=False)`; the container is started with `--network none` for scored executions where the orchestration permits. |
| 8 | Does grading require a Docker daemon from inside the HUD environment? | **No.** The deployment container contains no Docker CLI or socket; grading runs as an isolated subprocess against baked-in assets (`InContainerRunner`, `grader_v2/grading/runners.py`). Docker is a host-side tool only: the offline regression/labeling pipeline (`grader-audit`/`grader-v2` CLI) uses `DockerRunner` against task images. The two paths share the identical grading core, so outcomes agree (parity test). |
| 9 | How does one task-specific dependency image map to one HUD deployment? | `Dockerfile.hud` bakes the HUD runtime, the grader core, `grader_v2`, and every task tree; task dependencies are installed from the pinned locks (one digest). The served env is `grader_v2/hud/env.py` (`hud serve env_v2.py --host 0.0.0.0 --port 8000`, where `env_v2.py` is a one-line re-export generated in the image). `tasks.py`-style rows target env `hud-grader-audit-v2` with `args={"task_id": ..., "grader_version": ...}`. If a future task had a different dependency digest, the image would be rebuilt per digest and the deployment tag would carry that digest; the mapping rule is explicit. |
| 10 | How are initialization and shutdown resources released? | `@env.initialize`/`@env.shutdown` pair: the workspace capability's SSH daemon and any forwarded ports are stopped on shutdown; the subprocess runner kills the graded process group on timeout; the deployment test asserts no child process and no bound port remain after `env.stop()`, and the container exits cleanly on `docker stop` (SIGTERM → graceful `serve()` cancellation). |

## 2. Lifecycle

```text
deployment container starts (hud serve, 0.0.0.0:8000)
  -> @env.initialize hooks (workspace capability up)
  -> task RPC: stage_fresh_root(/workspace)          # baseline + visible tests + prompt
  -> yield prompt to agent
  -> agent edits /workspace through the ssh capability (bwrap-confined)
  -> trusted process grades final workspace state:
        prepare_task -> v1 mandatory checks (scope, asset hashes, authoritative suite)
        -> hardened_v2: semantic suite (post-rollout seed) -> structured evidence
  -> EvaluationResult (reward, reason codes, subscores) in the Run trace
  -> shutdown: workspace capability stopped; no daemon daemon left
```

Each rollout starts from a fresh baseline: `stage_fresh_root` wipes and re-stages the declared
baseline; the grader grades the workspace state (hashes captured pre/post grade), never the
textual final answer.

## 3. Process and trust model

| Zone | Process | Sees |
|---|---|---|
| Agent | SSH session in bwrap namespace | `/workspace` only |
| Trusted grader | HUD server process | `/app/tasks`, `/opt/grader`, generated semantic suites |
| Executed task code | `python -I /opt/grader/run_pytest.py ...` subprocess | `/workspace` (cwd), task deps, isolated env |
| Labeling (host only) | `grader-audit label-patches` | mounts oracle assets into task images |

## 4. Known deviations and relaxations

- In-container "bind mounts" are trusted-process copies onto baked image paths (D-056); the
  immutable payload files (`/opt/grader/run_pytest.py`, `pytest.ini`, `grader_plugin.py`) are
  never copied over — only `/opt/grader/tests` is replaced per run.
- The v2 HUD adapter replicates the protected adapter's mapping surface for `hardened_v2`
  (D-054); the protected mapping and grading core are imported verbatim.
- `verify_frozen_lock` (Section 27.14) is intentionally retired on Track-B HEAD (D-053); the
  release workflow verifies the v1 lock read-only with the two documented Track-B exceptions
  (D-055).

## 5. Test coverage that pins this document

- `grader_v2/tests/test_deployment_container.py` — builds the image from a clean context,
  starts it without a host dev environment, health-checks the control channel, runs
  deterministic stub rollouts (baseline `0.0`, gold `1.0`) through the served env, proves the
  agent capability cannot list `/app` or `/opt`, and asserts clean shutdown.
- `grader_v2/tests/test_parity.py` — offline DockerRunner and in-container InContainerRunner
  outcomes agree on the same workspace states.
- Infrastructure failure handling: a container-start/runner failure yields an
  `infrastructure_error` status with null reward, visible in the trace, never counted as a
  solution rejection (mapping inherited from the protected core, D-018).
