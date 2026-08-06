"""Grader asset hashing (Sections 8.2.B and 27.12).

Authoritative tests and oracle tests are trusted, read-only grader assets. The
orchestrator hashes them before and after grading and treats any mismatch as an
infrastructure error (``grader_asset_hash_mismatch``).
"""

from __future__ import annotations

from pathlib import Path

from grader_audit.core.hashing import hash_tree


def hash_grader_assets(asset_dir: Path) -> str:
    """Deterministic SHA-256 over the trusted grader asset directory."""
    if not asset_dir.is_dir():
        raise FileNotFoundError(f"grader asset directory does not exist: {asset_dir}")
    return hash_tree(asset_dir)
