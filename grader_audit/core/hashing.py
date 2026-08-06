"""SHA-256 helpers and deterministic tree hashing (Sections 21 and 27.10)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """SHA-256 over the raw bytes of *path*, read in binary mode."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def _tree_walk(root: Path) -> list[Path]:
    """Return every regular file under *root*, in sorted relative-path order."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if full.is_file():
                files.append(full)
    files.sort(key=lambda p: p.relative_to(root).as_posix())
    return files


def hash_tree(root: Path) -> str:
    """Deterministic SHA-256 over ``path:sha256`` lines sorted bytewise by POSIX path."""
    digest = hashlib.sha256()
    for path in _tree_walk(root):
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()
