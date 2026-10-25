"""Mx4 - local-GIS capacity through Esri Community Maps presence, as a reference-side prior.

Spec recipe: "county Esri share and the tract's own share", component F and B. The idea is that
counties which donate footprints to Esri Community Maps run strong GIS programmes, and their
published *reference* facility inventories are richer, so the facility gap can be larger even where
Overture looks good. Nothing here estimates the numerator; it is a covariate about the reference.

The county share is the building-count-weighted mean of the provided per-tract Esri-only share over
the official tracts of that county. Weighting by the provided building count is declared here rather
than fitted, and an empty or zero-weight county stays undefined instead of falling back to a mean.
"""
from __future__ import annotations

import numpy as np

from bias60.contracts import Emission, IdeaOutput
from bias60.ideas import implements
from bias60.ideas._common import as_float, quantiles


def _county_groups(county_codes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic county group index per row, in order of first appearance."""
    codes = np.asarray([str(value) for value in county_codes], dtype=object)
    order: dict[str, int] = {}
    groups = np.empty(codes.size, dtype=np.int64)
    for index, code in enumerate(codes):
        if code not in order:
            order[code] = len(order)
        groups[index] = order[code]
    return groups, np.array([code for code, _ in sorted(order.items(), key=lambda item: item[1])],
                            dtype=object)


@implements("Mx4")
def mx4(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Mx4"]
    tract_column = seed["tract_share_column"]
    weight_column = seed["weight_column"]
    strata = inputs.strata([seed["county_column"]])
    features = inputs.features([tract_column, weight_column])

    tract_share = as_float(features[tract_column])
    weight = as_float(features[weight_column])
    groups, labels = _county_groups(strata[seed["county_column"]])

    usable = np.isfinite(tract_share) & np.isfinite(weight) & (weight > 0.0)
    weight_sum = np.bincount(groups[usable], weights=weight[usable], minlength=len(labels))
    share_sum = np.bincount(groups[usable], weights=weight[usable] * tract_share[usable],
                            minlength=len(labels))
    with np.errstate(invalid="ignore", divide="ignore"):
        county_share = np.where(weight_sum > 0.0, share_sum / np.where(weight_sum > 0.0, weight_sum, 1.0),
                                np.nan)
    county_row_share = county_share[groups]

    county_defined = np.isfinite(county_row_share)
    tract_defined = np.isfinite(tract_share)
    county_values = np.zeros(tract_share.shape, dtype=np.float64)
    tract_values = np.zeros(tract_share.shape, dtype=np.float64)
    county_values[county_defined] = county_row_share[county_defined]
    tract_values[tract_defined] = tract_share[tract_defined]
    if county_defined.any() and (county_values[county_defined] < 0.0).any():
        raise ValueError("Mx4 county Esri share is negative")

    diagnostics = {
        "rows": int(tract_share.size),
        "counties": int(len(labels)),
        "counties_without_building_weight": int(np.sum(weight_sum <= 0.0)),
        "county_defined": int(county_defined.sum()),
        "tract_defined": int(tract_defined.sum()),
        "county_share_distribution": quantiles(county_row_share),
        "tract_share_distribution": quantiles(tract_share),
        "aggregation": "building-count-weighted mean of the provided per-tract Esri-only share "
                       "within COUNTYFP over official tracts",
        "no_fitted_parameter": True,
    }
    return IdeaOutput(
        idea_id="Mx4",
        region=inputs.region,
        geoids=list(inputs.geoids),
        emissions=[
            Emission(component="F", channel="covariate", values=county_values,
                     defined=county_defined,
                     metadata={"quantity": "county Esri Community Maps share (reference-side)"}),
            Emission(component="B", channel="covariate", values=tract_values,
                     defined=tract_defined,
                     metadata={"quantity": "tract Esri-only building share"}),
        ],
        diagnostics=diagnostics,
    )
