"""Small, testable implementation of the organizer's deficit/composite recipe.

These functions operate on model-side component estimates only. A false defined
flag means the component is omitted from the divisor; its numerical placeholder
is never an observation.
"""
from __future__ import annotations

import math
from collections.abc import Iterable


def clip01(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("gap must be finite")
    return min(1.0, max(0.0, value))


def deficit_gap(observed: float, expected: float, defined: bool) -> float:
    """A one-sided Overture shortfall; surplus is zero, missing reference is undefined."""
    if not defined:
        return 0.0
    observed, expected = float(observed), float(expected)
    if not math.isfinite(observed) or not math.isfinite(expected):
        raise ValueError("observed and expected must be finite")
    if observed < 0 or expected <= 0:
        raise ValueError("defined deficit requires observed >= 0 and expected > 0")
    return clip01(1.0 - observed / expected)


def defined_mean(values: Iterable[tuple[float, bool]]) -> tuple[float | None, int]:
    """Mean defined [0,1] estimates only; return None when none are defined."""
    used = []
    for value, is_defined in values:
        value = clip01(value)
        if is_defined:
            used.append(value)
    return (sum(used) / len(used), len(used)) if used else (None, 0)


def poi_gap(facility_type_gaps: Iterable[tuple[float, bool]],
            establishment_gap: float, establishment_defined: bool) -> tuple[float | None, int]:
    """Mean defined facility types, then equal-weight the two defined POI halves."""
    facility, n_types = defined_mean(facility_type_gaps)
    halves = []
    if n_types:
        halves.append(float(facility))
    establishment = clip01(establishment_gap)
    if establishment_defined:
        halves.append(establishment)
    return (sum(halves) / len(halves), 1) if halves else (None, 0)


def composite_gap(transport: tuple[float, bool], building: tuple[float, bool],
                  poi: tuple[float, bool]) -> tuple[float | None, int]:
    """Equal arithmetic mean of defined top-level components R, B and POI."""
    return defined_mean((transport, building, poi))
