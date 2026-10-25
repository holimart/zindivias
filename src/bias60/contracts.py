"""Output contract for a 60-estimator idea: one row per (GEOID, component, channel).

The scorer consumes gap-scale values in [0, 1] obtained through the *fixed* mapping
``g = clip(1 - O / E, 0, 1)`` (spec section 3.1). A covariate is allowed instead, but it must be
labelled as one, must not be squeezed into [0, 1] by an undocumented transform, and may reach gap
scale only through the lab fit or a pre-registered monotone link. Nothing in this module fits a
parameter: the mapping has fixed endpoints and no tuning knob.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

CHANNELS = ("gap", "covariate")


def clip_one_minus_ratio(observed: np.ndarray, expectation: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (gap, defined) for the fixed mapping clip(1 - O/E, 0, 1).

    ``defined`` is False where E is missing, non-positive or non-finite, or where O is missing: the
    component is then undefined rather than zero, which is the distinction the scored target makes.
    No imputation, no mean fill, no clipped-to-zero undefined rows.
    """
    observed = np.asarray(observed, dtype=np.float64)
    expectation = np.asarray(expectation, dtype=np.float64)
    if observed.shape != expectation.shape:
        raise ValueError("observed and expectation arrays must share a shape")
    defined = (np.isfinite(observed) & np.isfinite(expectation) & (expectation > 0.0))
    gap = np.zeros(observed.shape, dtype=np.float64)
    ratio = np.full(observed.shape, np.nan, dtype=np.float64)
    ratio[defined] = observed[defined] / expectation[defined]
    gap[defined] = np.clip(1.0 - ratio[defined], 0.0, 1.0)
    return gap, defined


def positive_part(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    out = np.zeros(values.shape, dtype=np.float64)
    finite = np.isfinite(values)
    out[finite] = np.maximum(values[finite], 0.0)
    return out


@dataclass
class Emission:
    """One emitted column of one idea."""

    component: str
    channel: str
    values: np.ndarray
    defined: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self, *, n_rows: int, config: dict[str, Any]) -> None:
        if self.component not in config["vocabulary"]["components"]:
            raise ValueError(f"unknown component {self.component!r}")
        if self.channel not in CHANNELS:
            raise ValueError(f"unknown channel {self.channel!r}")
        if self.values.shape != (n_rows,) or self.defined.shape != (n_rows,):
            raise ValueError(f"{self.component}/{self.channel}: arrays must have {n_rows} rows")
        if self.values.dtype != np.float64 or self.defined.dtype != np.bool_:
            raise ValueError(f"{self.component}/{self.channel}: wrong dtype (need float64, bool)")
        known = self.defined
        if not np.isfinite(self.values[known]).all():
            raise ValueError(f"{self.component}/{self.channel}: non-finite value where defined")
        if not np.isfinite(self.values[~known]).all():
            raise ValueError(f"{self.component}/{self.channel}: non-finite value where undefined")
        if (self.values[~known] != 0.0).any():
            raise ValueError(f"{self.component}/{self.channel}: undefined rows must carry exactly 0.0")
        if self.channel == "gap":
            gap = self.values[known]
            low, high = config["vocabulary"]["gap_bounds"]
            if gap.size and (gap.min() < low - 1e-12 or gap.max() > high + 1e-12):
                raise ValueError(f"{self.component}/gap: value outside [{low}, {high}]")


@dataclass
class IdeaOutput:
    """Everything one idea produces for one region, before parquet serialisation."""

    idea_id: str
    region: str
    geoids: list[str]
    emissions: list[Emission]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def validate(self, *, config: dict[str, Any]) -> None:
        if not self.emissions:
            raise ValueError(f"{self.idea_id}: no emission")
        if len(set((e.component, e.channel) for e in self.emissions)) != len(self.emissions):
            raise ValueError(f"{self.idea_id}: duplicate (component, channel) emission")
        if not self.geoids:
            raise ValueError(f"{self.idea_id}: no rows")
        if len(set(self.geoids)) != len(self.geoids):
            raise ValueError(f"{self.idea_id}: duplicate GEOID")
        for emission in self.emissions:
            emission.validate(n_rows=len(self.geoids), config=config)
