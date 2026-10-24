"""Provided-feature-only establishments-half component for a future estimator v2.

The block models the organizer's all-places-versus-establishments half without
loading CBP data. ``cbp_k`` is an explicit model parameter; callers must pass it
rather than silently inheriting an external count or a leaderboard-tuned value.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any


def estimate_establishments_component_v2(
    overture_poi_all: float,
    pop_total: float,
    cbp_k: float,
) -> dict[str, int | float]:
    """Estimate the establishments gap as ``clip(1 - POIs/(k * population))``.

    ``pop_total > 0`` defines the model component. At zero population, a
    positive observed place count has zero estimated deficit (the clipped
    zero-population limit); when both population and Overture count are zero,
    the component is undefined and its numeric placeholder is excluded by the
    returned flag. The only inputs are challenge-provided Overture counts,
    population, and the explicit scalar parameter ``cbp_k``.
    """
    values = (overture_poi_all, pop_total, cbp_k)
    try:
        poi, population, k = (float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ValueError("POI count, population, and cbp_k must be finite numbers") from exc
    if not all(math.isfinite(value) for value in (poi, population, k)):
        raise ValueError("POI count, population, and cbp_k must be finite numbers")
    if poi < 0 or population < 0:
        raise ValueError("POI count and population must be nonnegative")
    if k <= 0:
        raise ValueError("cbp_k must be positive")

    defined = int(population > 0 or poi > 0)
    if not defined:
        gap = 0.0
    elif population == 0:
        gap = 0.0
    else:
        gap = min(1.0, max(0.0, 1.0 - poi / (k * population)))
    return {"establishments_gap": gap, "establishments_defined": defined}


def facility_half_v2(facility_gaps: Iterable[tuple[float, int]]) -> dict[str, int | float]:
    """Mean only defined fire/EMS/school gaps, mirroring the organizer facility half."""
    values: list[float] = []
    for gap, defined in facility_gaps:
        if defined:
            value = float(gap)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("defined facility gaps must be finite and bounded in [0, 1]")
            values.append(value)
    if not values:
        return {"facility_gap": 0.0, "facility_defined": 0}
    return {"facility_gap": sum(values) / len(values), "facility_defined": 1}


def combine_poi_halves_v2(
    facility_gap: float,
    facility_defined: int,
    establishments_gap: float,
    establishments_defined: int,
) -> dict[str, Any]:
    """Equal-weight whichever POI halves are defined, as in the published method."""
    halves: list[float] = []
    if facility_defined:
        halves.append(float(facility_gap))
    if establishments_defined:
        halves.append(float(establishments_gap))
    for value in halves:
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("defined POI-half gaps must be finite and bounded in [0, 1]")
    if not halves:
        return {"poi_gap": 0.0, "poi_defined": 0}
    return {"poi_gap": sum(halves) / len(halves), "poi_defined": 1}
