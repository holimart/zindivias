"""Sca1: conditional Poisson expectation of the clipped ratio, using P2 fire inputs."""
from __future__ import annotations

import math

import numpy as np

from bias60.contracts import Emission, IdeaOutput
from bias60.ideas import implements
from bias60.ideas._common import as_float, quantiles


def _p2_inputs(inputs) -> tuple[np.ndarray, np.ndarray]:
    """Return O_fire and its preregistered P2 Poisson rate, without imputing missing data."""
    seed = inputs.config["seeds"]["P2"]
    observed = as_float(inputs.features([seed["observed_fire_column"]])
                        [seed["observed_fire_column"]])
    strata = inputs.strata([seed["area_column"], seed["population_column"], seed["ruca_column"]])
    area_km2 = as_float(strata[seed["area_column"]]) / 1_000_000.0
    population = as_float(strata[seed["population_column"]])
    ruca = as_float(strata[seed["ruca_column"]])
    standards = np.full(area_km2.shape, np.nan, dtype=np.float64)
    for density_class, codes in seed["ruca_classes"].items():
        standards[np.isin(ruca, np.asarray(codes, dtype=np.float64))] = float(
            seed["standard_area_km2"][density_class])
    rate = np.full(area_km2.shape, np.nan, dtype=np.float64)
    valid = (np.isfinite(area_km2) & (area_km2 > 0.0) & np.isfinite(population)
             & (population >= 0.0) & np.isfinite(standards) & (standards > 0.0))
    rate[valid] = area_km2[valid] / standards[valid]
    return observed, rate


def conditional_poisson_clipped_ratio(rate: float, observed: float, tail_sd: float = 12.0,
                                      tail_extra: int = 40) -> float:
    """Compute E[max(0, 1-O/R) | R>=1], R~Poisson(rate), by a truncated PMF sum.

    The upper limit is rate + 12 standard deviations + 40 counts; the omitted Poisson tail is
    negligible at float64 precision for the frozen study rates. The preregistered operational rate
    range stays below 745, so exp(-rate) remains representable. No fitting occurs.
    """
    if not math.isfinite(rate) or rate <= 0.0 or not math.isfinite(observed) or observed < 0.0:
        raise ValueError("rate must be positive and observed count finite/non-negative")
    if observed == 0.0:
        return 1.0
    p = math.exp(-rate)
    if p == 0.0:
        raise ValueError("rate exceeds the preregistered numerically stable Poisson range")
    denominator = -math.expm1(-rate)
    last = int(math.ceil(rate + tail_sd * math.sqrt(rate) + tail_extra))
    weighted = 0.0
    for count in range(1, last + 1):
        p *= rate / count
        weighted += p * max(0.0, 1.0 - observed / count)
    return min(1.0, max(0.0, weighted / denominator))


@implements("Sca1")
def sca1(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Sca1"]
    observed, rate = _p2_inputs(inputs)
    defined = (np.isfinite(observed) & (observed >= 0.0) & np.isfinite(rate) & (rate > 0.0))
    values = np.zeros(observed.shape, dtype=np.float64)
    # Compute only on defined rows; preserve undefined as contractually required zero.
    for index in np.flatnonzero(defined):
        values[index] = conditional_poisson_clipped_ratio(
            float(rate[index]), float(observed[index]), float(seed["tail_standard_deviations"]),
            int(seed["tail_extra_counts"]))
    return IdeaOutput(
        idea_id="Sca1", region=inputs.region, geoids=list(inputs.geoids),
        emissions=[Emission(component="F", channel="gap", values=values, defined=defined,
                            metadata={"formula": "E[max(0, 1-O/R) | R>=1], R~Poisson(P2_rate)"})],
        diagnostics={
            "rows": int(values.size), "defined": int(defined.sum()),
            "undefined_reasons": {"invalid_fire_or_p2_rate_inputs": int((~defined).sum())},
            "poisson_rate_distribution": quantiles(rate[defined]),
            "observed_fire_distribution": quantiles(observed[defined]),
            "gap_distribution": quantiles(values[defined]),
            "tail_truncation": {"standard_deviations": float(seed["tail_standard_deviations"]),
                                "extra_counts": int(seed["tail_extra_counts"])},
            "conditioned_on_positive_denominator": True,
            "no_fitted_parameter": True,
        })
