"""Deterministic serialisation helpers shared by the bias60 reports.

Reports must be reproducible byte-for-byte from a given commit, with one documented exception: the
fields that record *which commit produced the file* necessarily differ once the repository moves on.
Those are isolated here so every report normalises them the same way.
"""
from __future__ import annotations

import json
from typing import Any

RUNTIME_FIELDS = ("code_commit_at_run",)
RUNTIME_ENV_FIELDS = ("git_commit",)
WALL_CLOCK_KEYS = ("timestamp", "generated_at", "utcnow", "elapsed", "duration", "wall_clock",
                   "datetime", "time_")


def canonical_bytes(document: Any) -> bytes:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"


def strip_runtime_fields(document: dict[str, Any]) -> dict[str, Any]:
    copy = json.loads(json.dumps(document))
    for key in RUNTIME_FIELDS:
        copy.pop(key, None)
    environment = copy.get("environment")
    if isinstance(environment, dict):
        for key in RUNTIME_ENV_FIELDS:
            environment.pop(key, None)
    return copy


def normalized_bytes(document: dict[str, Any]) -> bytes:
    """Serialise a report without the run-time commit fields (see module docstring)."""
    return canonical_bytes(strip_runtime_fields(document))


def wall_clock_hits(node: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(node, dict):
        for key, child in node.items():
            if any(word in str(key).lower() for word in WALL_CLOCK_KEYS):
                hits.append(f"{path}/{key}")
            hits.extend(wall_clock_hits(child, f"{path}/{key}"))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            hits.extend(wall_clock_hits(child, f"{path}[{index}]"))
    return hits
