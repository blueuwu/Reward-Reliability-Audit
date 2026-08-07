"""Unit checks for the deployment workspace's namespace capability boundary."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grader_v2.hud import runtime
from grader_v2.hud.env import CapabilityDroppingWorkspace


def test_agent_command_drops_all_bubblewrap_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = CapabilityDroppingWorkspace(tmp_path, network=False)
    monkeypatch.setattr(workspace, "_bwrap", "/usr/bin/bwrap")

    argv = workspace.bwrap_argv(["sh", "-c", "true"])

    command_boundary = argv.index("--")
    assert argv[command_boundary - 2 : command_boundary] == ["--cap-drop", "ALL"]
    assert "--unshare-net" in argv[:command_boundary]


def test_deployment_grants_namespace_setup_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="container-id\n", stderr="")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)

    container = runtime.start_deployment_container("image-id", host_port=19000)

    assert container.container_id == "container-id"
    argv = calls[0]
    assert argv[argv.index("--cap-add") : argv.index("--cap-add") + 2] == [
        "--cap-add",
        "SYS_ADMIN",
    ]
    assert argv.count("--security-opt") == 2
    assert "apparmor=unconfined" in argv
    assert "seccomp=unconfined" in argv
