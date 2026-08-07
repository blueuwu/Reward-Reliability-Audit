"""Typed JSON helpers for pyright strict mode.

``json.loads`` and ``isinstance`` narrowing yield partially-unknown
``dict[Unknown, Unknown]`` types that strict mode rejects; these helpers
recover fully-known ``dict[str, object]`` types at the boundary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast


def as_dict(value: object | None) -> dict[str, object]:
    """Return *value* as a fully-typed mapping, or {} when not a mapping."""
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def load_dict(path: Path) -> dict[str, object]:
    """Read a JSON object file into a fully-typed mapping."""
    return as_dict(json.loads(path.read_text(encoding="utf-8")))
