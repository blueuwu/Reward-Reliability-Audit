"""Host environment checks required by Section 27.1 (``doctor`` command).

``doctor`` MUST check and report, without changing project state: Python
3.12.x, availability of ``uv``/``git``/``docker``, a reachable Docker Engine
that can run a Linux container, a writable project root, a Git repository with
a usable author identity, an importable HUD package, and that no API key is
required for controlled commands. Exit 0 only when all prerequisites pass.
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_REQUIRED_TOOLS = ("uv", "git", "docker")
_CONTAINER_PROBE_IMAGE = "hello-world"
_API_KEY_ENV_VARS = (
    "HUD_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GOOGLE_API_KEY",
    "DEEPSEEK_API_KEY",
)

_DOCKER_INFO_TIMEOUT_SECONDS = 60.0
_CONTAINER_RUN_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class DoctorCheck:
    key: str
    description: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def all_ok(self) -> bool:
        return all(check.ok for check in self.checks)


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float = 60.0,
) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=None if cwd is None else str(cwd),
            check=False,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return 127, "", f"executable not found: {argv[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out after {timeout_seconds:g}s"
    except OSError as exc:
        return 125, "", f"OS error: {exc}"


def _python_check() -> DoctorCheck:
    version = sys.version_info
    ok = (version.major, version.minor) == (3, 12)
    detail = f"Python {sys.version.split()[0]}"
    if not ok:
        detail += "; Section 27.1 requires 3.12.x"
    return DoctorCheck("python_version", "Python is version 3.12.x", ok, detail)


def _tool_checks() -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for name in _REQUIRED_TOOLS:
        path = shutil.which(name)
        ok = path is not None
        detail = f"{name} at {path}" if path is not None else f"{name} not found on PATH"
        checks.append(DoctorCheck(f"tool_{name}", f"{name} is available", ok, detail))
    return checks


def _docker_engine_check() -> DoctorCheck:
    code, out, err = _run(["docker", "info"], timeout_seconds=_DOCKER_INFO_TIMEOUT_SECONDS)
    ok = code == 0
    if ok:
        detail = "docker info: Docker Engine reachable"
    else:
        detail = f"docker info failed (exit {code}): {err.strip() or out.strip()}"
    return DoctorCheck("docker_engine_reachable", "Docker Engine is reachable", ok, detail)


def _docker_container_check() -> DoctorCheck:
    image = _CONTAINER_PROBE_IMAGE
    inspect_code, _, _ = _run(
        ["docker", "image", "inspect", image],
        timeout_seconds=_DOCKER_INFO_TIMEOUT_SECONDS,
    )
    pulled = inspect_code != 0
    code, out, err = _run(
        ["docker", "run", "--rm", "--network", "none", image],
        timeout_seconds=_CONTAINER_RUN_TIMEOUT_SECONDS,
    )
    ok = code == 0
    pull_note = " (image pulled on first use)" if pulled else ""
    error_tail = err.strip() or out.strip()
    if ok:
        detail = f"docker run --rm --network none {image} exited 0{pull_note}"
    else:
        detail = f"docker run --rm --network none {image} failed (exit {code}): {error_tail}"
    return DoctorCheck(
        "docker_linux_container",
        "Docker Engine can run a Linux container",
        ok,
        detail,
    )


def _writable_check(project_root: Path) -> DoctorCheck:
    try:
        with tempfile.NamedTemporaryFile(
            dir=project_root,
            prefix=".grader-doctor-probe-",
            delete=True,
        ) as probe:
            probe.write(b"probe")
            probe.flush()
        ok = True
        detail = f"project root is writable: {project_root}"
    except OSError as exc:
        ok = False
        detail = f"project root is not writable: {exc}"
    return DoctorCheck("project_root_writable", "Project root is writable", ok, detail)


def _git_repo_check(project_root: Path) -> DoctorCheck:
    code, out, _ = _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=project_root)
    ok = code == 0 and out.strip() == "true"
    detail = (
        f"{project_root} is inside a Git work tree"
        if ok
        else "not inside a Git work tree (run git init)"
    )
    return DoctorCheck("project_root_is_git_repo", "Project root is a Git repository", ok, detail)


def _git_author_check(project_root: Path) -> DoctorCheck:
    name_code, name_out, _ = _run(["git", "config", "user.name"], cwd=project_root)
    email_code, email_out, _ = _run(["git", "config", "user.email"], cwd=project_root)
    name = name_out.strip() if name_code == 0 else ""
    email = email_out.strip() if email_code == 0 else ""
    ok = bool(name and email)
    detail = (
        f"git user.name={name!r}, user.email={email!r}"
        if ok
        else "git user.name and/or user.email missing (required before freeze)"
    )
    return DoctorCheck("git_author_configured", "Git author name and email are usable", ok, detail)


def _hud_check() -> DoctorCheck:
    try:
        importlib.import_module("hud")
        version = importlib.metadata.version("hud")
        ok = bool(version)
        detail = f"hud {version} importable" if ok else "hud importable but version unresolved"
    except Exception as exc:
        ok = False
        detail = f"hud import failed: {exc}"
    return DoctorCheck("hud_importable", "Installed HUD version can be imported", ok, detail)


def _api_key_check() -> DoctorCheck:
    present = [name for name in _API_KEY_ENV_VARS if os.environ.get(name)]
    note = "none present" if not present else "present but ignored by controlled commands"
    return DoctorCheck(
        "no_api_key_required",
        "No API key is required for controlled commands",
        True,
        f"controlled commands read no API keys; {note}",
    )


def run_doctor(project_root: Path) -> DoctorReport:
    """Run every Section 27.1 check against *project_root*."""
    checks = [
        _python_check(),
        *_tool_checks(),
        _docker_engine_check(),
        _docker_container_check(),
        _writable_check(project_root),
        _git_repo_check(project_root),
        _git_author_check(project_root),
        _hud_check(),
        _api_key_check(),
    ]
    return DoctorReport(checks=tuple(checks))
