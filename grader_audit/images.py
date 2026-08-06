"""Immutable per-task image building (Sections 27.6 and 27.11).

One immutable Linux image per task contains Python 3.12, the pinned pytest and
JSON-report plugin (installed from the hashed task ``requirements.lock`` via
``uv pip install --system --require-hashes``), and the grader runner at
``/opt/grader`` plus an oracle config at ``/opt/oracle``. The base image is
pinned by OCI digest. ``build-images`` writes ``image.lock.json`` atomically
with the input hashes and the resolved build digest; images are referenced by
immutable digest in every record and the same digest is used for all runs of a
task.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import tempfile
from pathlib import Path
from typing import cast

from grader_audit.core.docker_runner import build_image, image_exists, image_id
from grader_audit.core.hashing import sha256_bytes, sha256_file
from grader_audit.core.manifests import LoadedTask

_RUNNER_DIR = Path(__file__).resolve().parent / "grading" / "v1" / "runner"
_RUNNER_FILES = ("run_pytest.py", "grader_plugin.py", "pytest.ini")
_IMAGE_PREFIX = "grader-audit-fixture"
_TASK_IMAGE_PREFIX = "grader-audit-task"

#: python:3.12-slim, linux/amd64, resolved via ``docker manifest inspect``.
_BASE_PYTHON_DIGEST = "sha256:d657ab0ade19f404a6ccc883ab399540de667aff751748ce23c07330c5a89e64"
#: uv version installed in the task images (must match the host tooling).
_UV_VERSION = "0.11.28"
#: Build platform recorded in image.lock.json (Section 27.6).
_BUILD_PLATFORM = "linux/amd64"

_TASK_LOCK_SCHEMA_VERSION = "1.0"


def _pinned(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        raise RuntimeError(
            f"package {name} is not installed on the host; cannot pin the image"
        ) from None


def _dockerfile_text() -> str:
    pytest_pin = f"pytest=={_pinned('pytest')}"
    report_pin = f"pytest-json-report=={_pinned('pytest-json-report')}"
    return (
        "FROM python:3.12-slim\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
        f"RUN pip install --no-cache-dir {pytest_pin} {report_pin}\n"
        "COPY run_pytest.py /opt/grader/run_pytest.py\n"
        "COPY grader_plugin.py /opt/grader/grader_plugin.py\n"
        "COPY pytest.ini /opt/grader/pytest.ini\n"
        "COPY pytest.ini /opt/oracle/pytest.ini\n"
    )


def fixture_image_tag() -> str:
    """Content-addressed tag for the shared synthetic task image."""
    digest = hashlib.sha256()
    digest.update(_dockerfile_text().encode("utf-8"))
    for name in _RUNNER_FILES:
        digest.update((_RUNNER_DIR / name).read_bytes())
    return f"{_IMAGE_PREFIX}:{digest.hexdigest()[:16]}"


def ensure_fixture_image(*, timeout_seconds: float = 900.0) -> str:
    """Build (or reuse) the fixture image and return its immutable image ID."""
    tag = fixture_image_tag()
    if image_exists(tag):
        return image_id(tag)
    context = Path(tempfile.mkdtemp(prefix="ga-image-context-"))
    try:
        for name in _RUNNER_FILES:
            shutil.copyfile(_RUNNER_DIR / name, context / name)
        dockerfile = context / "Dockerfile"
        dockerfile.write_text(_dockerfile_text(), encoding="utf-8")
        return build_image(tag, dockerfile, context, timeout_seconds=timeout_seconds)
    finally:
        shutil.rmtree(context, ignore_errors=True)


# ---------------------------------------------------------------------------
# Real per-task images (Section 27.6)
# ---------------------------------------------------------------------------


def task_dockerfile_text() -> str:
    """Dockerfile for a real per-task image.

    The base image is pinned by OCI digest; the hashed ``requirements.lock`` is
    installed with ``uv pip install --system --require-hashes``. Pytest and the
    JSON-report plugin are part of every task lock (see ``tasks/*/requirements.lock``).
    """
    return (
        "# syntax=docker/dockerfile:1\n"
        f"FROM python:3.12-slim@{_BASE_PYTHON_DIGEST}\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
        "COPY requirements.lock /opt/task/requirements.lock\n"
        f"RUN pip install --no-cache-dir uv=={_UV_VERSION}\n"
        "RUN uv pip install --system --require-hashes -r /opt/task/requirements.lock\n"
        "COPY run_pytest.py /opt/grader/run_pytest.py\n"
        "COPY grader_plugin.py /opt/grader/grader_plugin.py\n"
        "COPY pytest.ini /opt/grader/pytest.ini\n"
        "COPY pytest.ini /opt/oracle/pytest.ini\n"
    )


def _tree_hash_of(task: LoadedTask) -> str:
    from grader_audit.core.hashing import hash_tree

    return hash_tree(task.task_dir / task.manifest.workspace.source_dir)


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def task_image_tag(task: LoadedTask) -> str:
    """Content-addressed tag for the real task image."""
    lock = task.task_dir / task.manifest.runtime.requirements_lock
    digest = hashlib.sha256()
    digest.update(task.manifest_sha256.encode("ascii"))
    digest.update(_tree_hash_of(task).encode("ascii"))
    digest.update(sha256_file(lock).encode("ascii"))
    digest.update(sha256_text(task_dockerfile_text()).encode("ascii"))
    return f"{_TASK_IMAGE_PREFIX}-{task.manifest.id}:{digest.hexdigest()[:16]}"


def read_task_image_lock(task: LoadedTask) -> dict[str, object] | None:
    lock_path = task.task_dir / "image.lock.json"
    if not lock_path.is_file():
        return None
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{task.manifest.id}: image.lock.json is not a mapping")
    return cast(dict[str, object], data)


def build_task_image(task: LoadedTask, *, timeout_seconds: float = 1200.0) -> str:
    """Build (or reuse) the per-task immutable image and write image.lock.json.

    Returns the immutable image ID (``sha256:...``) recorded in the lock. If a
    lock already exists whose input hashes match the current task state, the
    recorded digest is reused; otherwise the image is rebuilt from the identical
    locked inputs. ``image.lock.json`` is written atomically and never
    overwritten with a conflicting task.
    """
    lock_path = task.task_dir / "image.lock.json"
    baseline_tree = _tree_hash_of(task)
    inputs = {
        "task_manifest_sha256": task.manifest_sha256,
        "baseline_tree_sha256": baseline_tree,
        "requirements_lock_sha256": sha256_file(
            task.task_dir / task.manifest.runtime.requirements_lock
        ),
        "dockerfile_sha256": sha256_text(task_dockerfile_text()),
    }
    existing = read_task_image_lock(task)
    if existing is not None:
        _verify_lock_consistency(task, existing, inputs)
        digest = existing.get("build_digest")
        if isinstance(digest, str) and digest.startswith("sha256:"):
            tag = task_image_tag(task)
            if image_exists(tag) and image_id(tag) == digest:
                return digest
            # Image may have been pruned; rebuild from identical inputs.
        if lock_path.exists():
            lock_path.unlink()
    if lock_path.exists():
        lock_path.unlink()

    tag = task_image_tag(task)
    context = Path(tempfile.mkdtemp(prefix=f"ga-task-image-{task.manifest.id}-"))
    try:
        for name in _RUNNER_FILES:
            shutil.copyfile(_RUNNER_DIR / name, context / name)
        shutil.copyfile(
            task.task_dir / task.manifest.runtime.requirements_lock,
            context / "requirements.lock",
        )
        dockerfile = context / "Dockerfile"
        dockerfile.write_text(task_dockerfile_text(), encoding="utf-8")
        digest = build_image(tag, dockerfile, context, timeout_seconds=timeout_seconds)
    finally:
        shutil.rmtree(context, ignore_errors=True)

    payload: dict[str, object] = {
        "schema_version": _TASK_LOCK_SCHEMA_VERSION,
        "task_id": task.manifest.id,
        "build_platform": _BUILD_PLATFORM,
        "build_digest": digest,
    }
    payload.update(inputs)
    _atomic_write_json(lock_path, payload)
    return digest


def _verify_lock_consistency(
    task: LoadedTask, lock: dict[str, object], inputs: dict[str, str]
) -> None:
    for key, value in inputs.items():
        recorded = lock.get(key)
        if recorded != value:
            raise ValueError(
                f"{task.manifest.id}: image.lock.json input {key} is stale; "
                f"rerun build-images (recorded={recorded!r}, current={value!r})"
            )


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    import os
    import uuid

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    tmp.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    try:
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def resolve_task_image(task: LoadedTask, *, timeout_seconds: float = 900.0) -> str:
    """Return the immutable scoring image digest for *task* (Sections 27.6/27.13).

    Resolution is by task, never by grader: naive and hardened variants of a
    task MUST use the same task-image digest. A real task (one whose
    requirements lock declares pinned distributions) MUST be built by
    ``build-images`` before any scored run; without an ``image.lock.json`` this
    is an error, never a silent fallback. The synthetic fixture tasks (stdlib-only
    with an empty lock) share the fixture image.
    """
    lock = task.task_dir / "image.lock.json"
    if lock.is_file():
        data = json.loads(lock.read_text(encoding="utf-8"))
        digest = data.get("build_digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise ValueError(
                f"{task.manifest.id}: image.lock.json has no valid 'build_digest' entry"
            )
        return digest

    lock_path = task.task_dir / task.manifest.runtime.requirements_lock
    has_distributions = False
    if lock_path.is_file():
        for line in lock_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                has_distributions = True
                break
    if has_distributions:
        raise FileNotFoundError(
            f"{task.manifest.id}: task image not built; run 'grader-audit build-images' first"
        )
    return ensure_fixture_image(timeout_seconds=timeout_seconds)
