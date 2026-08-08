"""Deployment-container lifecycle helpers (hardening §5, Gate B).

These helpers start the built deployment image as an independent container
(no host development environment, no Docker socket inside), wait for its
control channel, drive deterministic no-provider stub rollouts against it
through ``Runtime("tcp://...")``, and tear the container down cleanly. They
are shared by the container integration tests and the final demo.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from hud.agents.base import Agent
from hud.capabilities import SSHClient
from hud.clients import connect
from hud.eval import Task
from hud.eval.run import Run, rollout

from hud import Runtime

DEFAULT_IMAGE = "grader-audit-hud-app"
_DEFAULT_HOST_PORT = 18000


class DeploymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeploymentContainer:
    """A started deployment container; ``url`` is its control channel."""

    container_id: str
    url: str
    host_port: int
    image: str

    async def wait_ready(self, timeout: float = 180.0) -> None:
        """Wait until the control channel answers ``hello``."""
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                async with connect(Runtime(self.url)) as client:
                    if client.manifest is not None:
                        return
            except Exception as exc:
                last_error = exc
            await asyncio.sleep(1.0)
        raise DeploymentError(
            f"deployment container {self.container_id[:12]} not ready at {self.url}: {last_error}"
        )

    async def stop(self, timeout: float = 60.0) -> None:
        """Stop and remove the container; raises when it is already gone."""
        result = subprocess.run(
            ["docker", "rm", "-f", self.container_id],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode not in (0, 1):  # 1: already gone
            raise DeploymentError(f"docker rm failed: {result.stderr.strip()}")


def build_deployment_image(
    project_root: Path,
    image: str = DEFAULT_IMAGE,
    *,
    timeout_seconds: float = 1200.0,
) -> str:
    """Build the deployment image from a clean context; returns its image ID."""
    result = subprocess.run(
        [
            "docker",
            "build",
            "-q",
            "-t",
            image,
            "-f",
            str(project_root / "Dockerfile.hud"),
            str(project_root),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        raise DeploymentError(f"docker build failed: {result.stderr.strip()[:4000]}")
    inspect = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        timeout=60.0,
        check=False,
    )
    if inspect.returncode != 0:
        raise DeploymentError(f"image not present after build: {image}")
    return inspect.stdout.strip()


def image_exists(image: str = DEFAULT_IMAGE) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        timeout=60.0,
        check=False,
    )
    return result.returncode == 0


def resolve_deployment_image(
    project_root: Path,
    image: str = DEFAULT_IMAGE,
    *,
    build: bool = False,
) -> str:
    """Resolve the deployment image ID, optionally building it first."""
    if build or not image_exists(image):
        return build_deployment_image(project_root, image)
    inspect = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        timeout=60.0,
        check=False,
    )
    if inspect.returncode != 0:
        raise DeploymentError(f"image not present: {image}")
    return inspect.stdout.strip()


def start_deployment_container(
    image: str = DEFAULT_IMAGE,
    *,
    host_port: int | None = None,
    memory_mb: int = 2048,
    pids_limit: int = 512,
) -> DeploymentContainer:
    """Start the deployment container with a published control channel.

    The security options and ``SYS_ADMIN``/``NET_ADMIN`` capabilities are required, not
    incidental. The HUD workspace isolates every agent SSH session with bwrap,
    which creates nested
    user, PID, IPC, UTS, cgroup, and network namespaces. On GitHub's Ubuntu
    runners, both the ``docker-default`` AppArmor profile and Docker's default
    seccomp profile block parts of that namespace setup. If user-namespace
    creation fails, ``--unshare-user-try`` silently degrades and a later
    non-optional unshare exits with EPERM, so every agent command exits 1.

    ``SYS_ADMIN`` and ``NET_ADMIN`` are available only to bubblewrap while it
    constructs that boundary (including loopback in the new network namespace).
    The deployment workspace runs the agent command through ``setpriv`` after
    setup, with empty effective, permitted, inheritable, ambient, and bounding
    capability sets plus ``no_new_privs``. The container receives no host mounts
    or Docker socket.
    """
    port = host_port or _DEFAULT_HOST_PORT
    name = f"ga-hud-app-{uuid.uuid4().hex[:8]}"
    result = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{port}:8000",
            "--memory",
            f"{memory_mb}m",
            "--pids-limit",
            str(pids_limit),
            "--cap-add",
            "SYS_ADMIN",
            "--cap-add",
            "NET_ADMIN",
            "--security-opt",
            "apparmor=unconfined",
            "--security-opt",
            "seccomp=unconfined",
            image,
        ],
        capture_output=True,
        text=True,
        timeout=120.0,
        check=False,
    )
    if result.returncode != 0:
        raise DeploymentError(f"docker run failed: {result.stderr.strip()[:4000]}")
    return DeploymentContainer(
        container_id=result.stdout.strip(),
        url=f"tcp://127.0.0.1:{port}",
        host_port=port,
        image=image,
    )


class PatchApplyingStubAgent(Agent):
    """Deterministic no-provider agent that edits the workspace via the ssh capability.

    Applies *diff_text* through the workspace capability (write + ``git apply``),
    proving the agent path can modify the graded workspace state; it never calls
    a model provider.
    """

    def __init__(
        self,
        diff_text: str = "",
        trace_content: str = "stub agent (no provider)",
    ) -> None:
        self.diff_text = diff_text
        self.trace_content = trace_content

    async def __call__(self, run: Run) -> None:
        run.trace.content = self.trace_content
        if not self.diff_text:
            return
        try:
            shell = cast(SSHClient, await run.client.open("shell"))
        except Exception as exc:
            run.trace.content = f"stub patch apply failed opening shell: {exc}"
            return
        try:
            await shell.write_text("/stub-apply.patch", self.diff_text)
        except Exception as exc:
            run.trace.content = (
                f"stub patch write failed: {exc}"
                f" stderr={getattr(exc, 'stderr', None)!r}"
                f" stdout={getattr(exc, 'stdout', None)!r}"
            )
            return
        try:
            result = await shell.conn.run(
                "pwd; id -u; wc -c stub-apply.patch; "
                "head -n 2 stub-apply.patch; "
                "git apply --whitespace=nowarn stub-apply.patch"
            )
            if result.exit_status != 0:
                run.trace.content = (
                    f"stub patch apply failed: {(result.stderr or '')[:2000]}"
                )
                return
            await shell.conn.run("rm -f stub-apply.patch")
        except Exception as exc:
            run.trace.content = (
                f"stub patch apply failed: {exc}"
                f" stderr={getattr(exc, 'stderr', None)!r}"
                f" stdout={getattr(exc, 'stdout', None)!r}"
            )
        finally:
            with contextlib.suppress(Exception):
                await shell.close()


async def run_stub_rollout_remote(
    *,
    url: str,
    task_id: str,
    grader_version: str,
    diff_text: str = "",
    env_name: str = "hud-grader-audit-v2",
) -> Run:
    """Run one deterministic stub rollout against a served deployment container."""
    task = Task(
        env=env_name,
        id="grader_reliability_task",
        args={"task_id": task_id, "grader_version": grader_version},
    )
    return await rollout(task, PatchApplyingStubAgent(diff_text), runtime=Runtime(url))
