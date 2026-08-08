"""Unit checks for the deployment workspace's namespace capability boundary."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grader_v2.hud import runtime
from grader_v2.hud.env import CapabilityDroppingWorkspace


def test_agent_command_drops_all_capability_sets_after_bubblewrap_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = CapabilityDroppingWorkspace(tmp_path, network=False)
    monkeypatch.setattr(workspace, "_bwrap", "/usr/bin/bwrap")

    def fake_which(_name: str) -> str:
        return "/usr/bin/setpriv"

    monkeypatch.setattr("grader_v2.hud.env.shutil.which", fake_which)

    argv = workspace.bwrap_argv(["sh", "-c", "true"])

    command_boundary = argv.index("--")
    assert "--unshare-net" in argv[:command_boundary]
    assert "--cap-drop" not in argv[:command_boundary]
    assert argv[command_boundary + 1 :] == [
        "/usr/bin/setpriv",
        "--bounding-set=-all",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "--no-new-privs",
        "--",
        "sh",
        "-c",
        "true",
    ]


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
    cap_adds = [argv[index + 1] for index, value in enumerate(argv) if value == "--cap-add"]
    assert cap_adds == ["SYS_ADMIN", "NET_ADMIN"]
    assert argv.count("--security-opt") == 3
    assert "apparmor=unconfined" in argv
    assert "seccomp=unconfined" in argv
    assert "systempaths=unconfined" in argv
