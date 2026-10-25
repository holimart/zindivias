"""Dst3: left-tail deficit for provided Overture building footprints only."""
from __future__ import annotations

import math

import numpy as np

from bias60.contracts import Emission, IdeaOutput, clip_one_minus_ratio
from bias60.ideas import implements
from bias60.ideas._common import quantiles


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def expected_left_tail(in_band: np.ndarray, log_mean: float, log_sd: float,
                       lower_m2: float = 40.0, upper_m2: float = 400.0) -> np.ndarray:
    """Extrapolate below-lower counts from the fitted lognormal and observed 40–400 m² counts."""
    if not (math.isfinite(log_mean) and math.isfinite(log_sd) and log_sd > 0):
        raise ValueError("lognormal parameters must be finite with positive scale")
    lower = math.log(lower_m2)
    upper = math.log(upper_m2)
    p_small = normal_cdf((lower - log_mean) / log_sd)
    p_band = normal_cdf((upper - log_mean) / log_sd) - p_small
    if not math.isfinite(p_band) or p_band <= 0 or p_small <= 0:
        raise ValueError("fitted lognormal has no usable mass in the registered bins")
    return np.asarray(in_band, dtype=np.float64) * (p_small / p_band)


@implements("Dst3")
def dst3(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Dst3"]
    counts, meta = inputs.building_area_bins(float(seed["small_m2"]), float(seed["upper_m2"]))
    n_band = counts["in_band"]
    observed_small = counts["below_small"]
    n_fit = int(meta["in_band_features"])
    if n_fit < int(seed["minimum_fit_footprints"]):
        raise ValueError(f"only {n_fit} provided in-band building footprints; frozen fit floor not met")
    log_mean = float(meta["log_area_sum"]) / n_fit
    variance = max(0.0, float(meta["log_area_squared_sum"]) / n_fit - log_mean * log_mean)
    log_sd = math.sqrt(variance)
    expectation = expected_left_tail(n_band, log_mean, log_sd,
                                     float(seed["small_m2"]), float(seed["upper_m2"]))
    gap, defined = clip_one_minus_ratio(observed_small, expectation)
    return IdeaOutput(
        idea_id="Dst3", region=inputs.region, geoids=list(inputs.geoids),
        emissions=[Emission(component="B", channel="gap", values=gap, defined=defined,
                            metadata={"formula": "clip(1 - observed_<40 / expected_<40, 0, 1)"})],
        diagnostics={"rows": int(gap.size), "defined": int(defined.sum()),
                     "undefined_reasons": {"no_in_band_tract_observations": int((~defined).sum())},
                     "small_footprint_count_distribution": quantiles(observed_small[defined]),
                     "in_band_count_distribution": quantiles(n_band[defined]),
                     "expected_small_count_distribution": quantiles(expectation[defined]),
                     "gap_distribution": quantiles(gap[defined]),
                     "lognormal_fit": {"log_area_mean": log_mean, "log_area_sd": log_sd,
                                       "fit_features": n_fit, "fit_domain_m2": [seed["small_m2"], seed["upper_m2"]]},
                     "source_layer": meta, "assignment": "provided Overture footprint centroid",
                     "microsoft_reference_used": False, "no_fitted_target_parameter": True})
