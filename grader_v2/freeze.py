"""V2 freeze and read-only v1-lock verification (D-053, D-055).

- ``verify_v1_lock`` re-runs the frozen lock checks that remain meaningful on
  Track-B HEAD: the tag resolves, the working lock is byte-identical to the
  lock committed at the tag, and every protected-file hash matches the lock.
  The two Track-B-knowledge exceptions (protected ``pyproject.toml`` hash and
  tracked infrastructure additions) are reported explicitly instead of
  failing.
- ``freeze_v2`` snapshots the v2 grading surface (all files under
  ``grader_v2/grading/``, plus semantic profile/generator versions) into
  ``results/application-v2/freeze-v2.json``. The v2 held-out set is authored
  only after this snapshot exists (hardening §6 held-out rule).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from grader_audit.core.hashing import sha256_file
from grader_v2.jsonutil import as_dict, load_dict

FROZEN_LOCK_REL = Path("freeze") / "grader_v1.lock.json"
V2_FREEZE_OUTPUT = Path("results") / "application-v2" / "freeze-v2.json"
_FULL_SHA = re.compile(r"^[0-9a-f]{64}$")

#: Files under grader_v2/ that constitute the v2 grading surface.
_V2_PROTECTED_ROOTS = ("grading", "hud", "freeze.py", "cli.py")

#: Protected lock entries that are allowed to change on Track B (D-053, D-058):
#: the application-v2 release extends the package/quality surface, and the
#: lock files necessarily follow.
_TRACK_B_ALLOWED_LOCK_DELTAS = frozenset({"pyproject.toml", "uv.lock"})


class V1LockVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class V1LockVerification:
    tag_commit: str
    protected_matched: int
    protected_mismatches: list[str]
    approved_mismatches: list[str]
    track_b_exceptions: list[str]
    lock_hash_matches_tag: bool

    @property
    def ok(self) -> bool:
        return (
            self.lock_hash_matches_tag
            and self.protected_mismatches == []
        )


def verify_v1_lock(project_root: Path) -> V1LockVerification:
    """Verify the v1 freeze lock with the documented Track-B exceptions (D-055)."""
    import subprocess

    lock_path = project_root / FROZEN_LOCK_REL
    if not lock_path.is_file():
        raise V1LockVerificationError(f"freeze lock missing: {lock_path}")
    lock_bytes = lock_path.read_bytes()
    try:
        lock = json.loads(lock_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise V1LockVerificationError(f"freeze lock unreadable: {exc}") from None

    tag = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "rev-parse",
            "-q",
            "--verify",
            "refs/tags/grader-v1-frozen",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if tag.returncode != 0:
        raise V1LockVerificationError("required tag 'grader-v1-frozen' missing")
    tag_commit = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "-q", "grader-v1-frozen^{commit}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()

    committed = subprocess.run(
        ["git", "-C", str(project_root), "show", f"{tag_commit}:{FROZEN_LOCK_REL.as_posix()}"],
        capture_output=True,
        check=False,
    )
    lock_hash_matches = committed.returncode == 0 and committed.stdout == lock_bytes

    protected_map = as_dict(lock.get("protected_files"))
    mismatches: list[str] = []
    approved: list[str] = []
    matched = 0
    if not protected_map:
        raise V1LockVerificationError("freeze lock has no protected_files")
    for rel, expected in sorted(protected_map.items()):
        expected_text = str(expected)
        path = project_root / Path(rel)
        if not path.is_file():
            mismatches.append(f"{rel}: missing")
            continue
        actual = sha256_file(path)
        if actual == expected_text:
            matched += 1
        elif rel in _TRACK_B_ALLOWED_LOCK_DELTAS:
            approved.append(f"{rel}: hash mismatch (approved Track-B delta, D-053)")
        else:
            mismatches.append(f"{rel}: hash mismatch")

    exceptions = [
        "protected pyproject.toml hash changed (Track B, D-053): "
        "the application-v2 release extends strict Pyright/ruff coverage to grader_v2/",
        "tracked infrastructure files outside the D-037 surface (.github/, Dockerfile.hud, "
        ".gitignore, .env.example) are intentionally tracked on Track-B HEAD (D-053, D-051)",
    ]
    return V1LockVerification(
        tag_commit=tag_commit,
        protected_matched=matched,
        protected_mismatches=mismatches,
        approved_mismatches=approved,
        track_b_exceptions=exceptions,
        lock_hash_matches_tag=lock_hash_matches,
    )


def _collect_v2_surface_hashes(project_root: Path) -> dict[str, str]:
    root = project_root / "grader_v2"
    hashes: dict[str, str] = {}
    for rel in sorted(Path(root).rglob("*")):
        if not rel.is_file():
            continue
        if rel.name.endswith(".pyc"):
            continue
        parts = rel.relative_to(root).parts
        if not any(part in _V2_PROTECTED_ROOTS for part in parts):
            continue
        hashes[rel.relative_to(project_root).as_posix()] = sha256_file(rel)
    return hashes


def freeze_v2(project_root: Path) -> Path:
    """Snapshot the v2 grading surface into ``results/application-v2/freeze-v2.json``."""
    from grader_v2.grading.semantic import PROFILES

    surface = _collect_v2_surface_hashes(project_root)
    if not surface:
        raise V1LockVerificationError("no grader_v2 grading surface files found")
    from grader_audit.core.hashing import sha256_bytes

    aggregate = sha256_bytes(
        json.dumps(surface, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    payload = {
        "schema_version": "1.0",
        "kind": "grader_v2_freeze",
        "grader": "hardened_v2",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "aggregate_sha256": aggregate,
        "files": surface,
        "semantic_profiles": [
            {
                "profile_id": profile.profile_id,
                "generator_version": profile.generator_version,
                "task_ids": sorted(profile.task_ids),
                "mechanisms": list(profile.mechanisms),
            }
            for profile in PROFILES
        ],
    }
    output = project_root / V2_FREEZE_OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return output


def load_v2_freeze(project_root: Path) -> dict[str, object]:
    path = project_root / V2_FREEZE_OUTPUT
    if not path.is_file():
        raise V1LockVerificationError(
            f"v2 freeze missing: {path} — run `grader-v2 freeze-v2` before authoring "
            "the v2 held-out set"
        )
    payload = load_dict(path)
    return payload if payload else {}


def verify_v2_surface_unchanged(project_root: Path) -> list[str]:
    """Return hash mismatches between the frozen v2 surface and HEAD."""
    freeze = load_v2_freeze(project_root)
    recorded_map = as_dict(freeze.get("files"))
    if not recorded_map:
        raise V1LockVerificationError("v2 freeze has no 'files' entry")
    mismatches: list[str] = []
    for rel, expected in sorted(recorded_map.items()):
        path = project_root / Path(rel)
        if not path.is_file():
            mismatches.append(f"{rel}: missing")
            continue
        if sha256_file(path) != str(expected):
            mismatches.append(f"{rel}: hash mismatch")
    return mismatches
