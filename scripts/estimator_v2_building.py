"""Model-derived near-zero building component for estimator v2.

This module intentionally does not import strata fields or external reference
layers. A positive provided Overture building count marks the model component
as defined and receives the fixed residual prior; a zero count is left
undefined rather than assigned a deficit that cannot be justified from the
allowed inputs alone.
"""
from __future__ import annotations

import math

BUILDING_GAP_V2 = 0.01


def estimate_building_component_v2(overture_buildings: float) -> dict[str, int | float]:
    """Return the v2 component and defined flag using only Overture count.

    ``BUILDING_GAP_V2`` is fixed before any candidate evaluation. The output
    ``building_gap`` is a numeric placeholder when undefined and must be
    excluded from component means using ``building_defined``.
    """
    try:
        count = float(overture_buildings)
    except (TypeError, ValueError) as exc:
        raise ValueError("overture_buildings must be a finite nonnegative count") from exc
    if not math.isfinite(count) or count < 0:
        raise ValueError("overture_buildings must be a finite nonnegative count")
    defined = int(count > 0)
    return {
        "building_gap": BUILDING_GAP_V2 if defined else 0.0,
        "building_defined": defined,
    }
