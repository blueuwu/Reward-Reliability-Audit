"""v2 freeze machinery: surface snapshot, drift detection, read-only v1 lock.

All freeze tests run against a fabricated project root in tmp_path except the
read-only ``verify_v1_lock`` smoke test, which inspects the real repository
without writing anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grader_audit.core.hashing import sha256_bytes
from grader_v2.freeze import (
    V1LockVerificationError,
    freeze_v2,
    load_v2_freeze,
    verify_v1_lock,
    verify_v2_surface_unchanged,
)

SURFACE_FILES = (
    "grader_v2/grading/__init__.py",
    "grader_v2/grading/evidence.py",
    "grader_v2/cli.py",
    "grader_v2/hud/mapping.py",
)


def _fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    for rel in SURFACE_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content {rel}\n", encoding="utf-8")
    return root


def test_freeze_v2_snapshots_surface(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    output = freeze_v2(root)
    assert output.is_file()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["grader"] == "hardened_v2"
    assert payload["aggregate_sha256"] == sha256_bytes(
        json.dumps(payload["files"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    for rel in SURFACE_FILES:
        assert rel in payload["files"]
    profiles = {p["profile_id"] for p in payload["semantic_profiles"]}
    assert "tinydb-docids-v1" in profiles
    assert "tinydb-query-freeze-v1" in profiles


def test_verify_clean_after_freeze(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    freeze_v2(root)
    assert verify_v2_surface_unchanged(root) == []


def test_verify_detects_surface_drift(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    freeze_v2(root)
    (root / "grader_v2/grading/evidence.py").write_text("tampered\n", encoding="utf-8")
    problems = verify_v2_surface_unchanged(root)
    assert any("grader_v2/grading/evidence.py" in problem for problem in problems)


def test_verify_detects_missing_surface_file(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    freeze_v2(root)
    (root / "grader_v2/cli.py").unlink()
    problems = verify_v2_surface_unchanged(root)
    assert any("grader_v2/cli.py: missing" in problem for problem in problems)


def test_verify_refuses_missing_freeze(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    with pytest.raises(V1LockVerificationError, match="freeze-v2"):
        verify_v2_surface_unchanged(root)


def test_verify_ignores_non_surface_grader_v2_files(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    freeze_v2(root)
    extra = root / "grader_v2/demo.py"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("not part of the grading surface\n", encoding="utf-8")
    assert verify_v2_surface_unchanged(root) == []


def test_load_v2_freeze_round_trip(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    freeze_v2(root)
    payload = load_v2_freeze(root)
    assert payload["kind"] == "grader_v2_freeze"
    assert payload["aggregate_sha256"]


def _project_root() -> Path:
    probe = Path(__file__).resolve()
    for parent in probe.parents:
        if (parent / "freeze" / "grader_v1.lock.json").is_file():
            return parent
    raise AssertionError("test must run inside the repository")


def test_verify_v1_lock_read_only_smoke() -> None:
    """Read-only verification on the real repository (Track B, D-053/D-055).

    The two allowed lock deltas (pyproject.toml, uv.lock) must be reported as
    approved, never as failures; every other protected file must match.
    """
    root = _project_root()
    verification = verify_v1_lock(root)
    assert verification.ok
    assert verification.lock_hash_matches_tag
    approved = " ".join(verification.approved_mismatches)
    assert "pyproject.toml" in approved
    assert "uv.lock" in approved
