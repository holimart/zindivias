"""Shared numeric helpers for idea implementations."""
from __future__ import annotations

import numpy as np


def as_float(values: object) -> np.ndarray:
    """Coerce a provided column to float64, mapping nulls to NaN without filling them."""
    array = np.asarray(values, dtype=np.float64)
    return array


def as_coverage(values: object) -> np.ndarray:
    """Interpret a provided ``*_covered`` flag as a boolean without inventing coverage."""
    array = np.asarray(values)
    if array.dtype == np.bool_:
        return array
    numeric = np.asarray(values, dtype=np.float64)
    return np.isfinite(numeric) & (numeric != 0.0)


def quantiles(values: np.ndarray) -> dict[str, float]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return {"count": 0}
    return {
        "count": int(finite.size),
        "min": float(finite.min()),
        "p50": float(np.quantile(finite, 0.50)),
        "p90": float(np.quantile(finite, 0.90)),
        "max": float(finite.max()),
        "mean": float(finite.mean()),
    }
