"""Sc5 - filtered versus unfiltered roads, the only direct trace of the organiser's numerator.

The package ships both ``roads`` and ``roads-unfiltered``. Whatever rule produced the filtered layer
is probably the organiser's pre-filter, so named-class length that exists in the unfiltered layer but
not in the filtered one is excluded from the numerator and becomes a gap. Spec recipe: "named-class
length in unfiltered minus filtered, as a share", component R.

Both layers are measured identically: named-class segments clipped to the official tract polygons in
the EPSG:5070 equal-area CRS. The filtered measurement is cross-checked against the lane's existing
per-tract cache column, so a convention mismatch between the two layers cannot masquerade as signal.
"""
from __future__ import annotations

import numpy as np

from bias60.contracts import Emission, IdeaOutput, clip_one_minus_ratio
from bias60.ideas import implements
from bias60.ideas._common import as_float, quantiles


@implements("Sc5")
def sc5(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Sc5"]
    filtered_km, filtered_meta = inputs.named_class_km(seed["filtered_layer"])
    unfiltered_km, unfiltered_meta = inputs.named_class_km(seed["unfiltered_layer"])

    gap, defined = clip_one_minus_ratio(filtered_km, unfiltered_km)
    share = np.zeros(gap.shape, dtype=np.float64)
    share[defined] = 1.0 - filtered_km[defined] / unfiltered_km[defined]
    share[~defined] = 0.0

    cached = as_float(inputs.features([seed["cross_check_column"]])[seed["cross_check_column"]])
    cached_total = float(np.nansum(np.where(np.isfinite(cached), cached, np.nan)))
    measured_total = float(filtered_km.sum())
    tolerance = seed["cross_check_relative_tolerance"]
    if cached_total > 0.0:
        residual = abs(measured_total - cached_total) / cached_total
    else:
        residual = 0.0 if measured_total == 0.0 else 1.0
    if residual > tolerance:
        raise ValueError(
            f"Sc5 filtered named-class length does not reconcile with the provided feature cache "
            f"(measured {measured_total:.6f} km, cached {cached_total:.6f} km, relative "
            f"{residual:.3e} > {tolerance:.1e})")

    anomaly_rows = int(np.sum((unfiltered_km <= 0.0) & (filtered_km > 0.0)))
    zero_but_present = int(np.sum((filtered_km <= 0.0) & (unfiltered_km > 0.0)))
    diagnostics = {
        "rows": int(gap.size),
        "defined_gap": int(defined.sum()),
        "layers": {seed["filtered_layer"]: filtered_meta, seed["unfiltered_layer"]: unfiltered_meta},
        "named_length_km": {seed["filtered_layer"]: measured_total,
                            seed["unfiltered_layer"]: float(unfiltered_km.sum())},
        "cross_check": {"column": seed["cross_check_column"],
                        "cached_km": cached_total, "measured_km": measured_total,
                        "relative_residual": residual, "tolerance": tolerance},
        "tracts_excluded_entirely": zero_but_present,
        "tracts_with_filtered_length_absent_from_unfiltered": anomaly_rows,
        "gap_distribution": quantiles(gap[defined]),
        "excluded_share_distribution": quantiles(share[defined]),
        "fixed_mapping": "clip(1 - O_filtered/E_unfiltered, 0, 1) with both lengths clipped in EPSG:5070",
        "no_fitted_parameter": True,
    }
    return IdeaOutput(
        idea_id="Sc5",
        region=inputs.region,
        geoids=list(inputs.geoids),
        emissions=[
            Emission(component="R", channel="gap", values=gap, defined=defined,
                     metadata={"mapping": "clip(1 - O_filtered/E_unfiltered, 0, 1)"}),
            Emission(component="R", channel="covariate", values=share, defined=defined,
                     metadata={"quantity": "unclipped (L_unfiltered - L_filtered) / L_unfiltered"}),
        ],
        diagnostics=diagnostics,
    )
