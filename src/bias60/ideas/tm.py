"""Tm1 - two-release building churn (CarbonPlan Overture 2025-09 versus package Overture 2026).

Spec recipe: ``delta = O_now / O_cp - 1``; emit ``max(0, -delta)`` as a gap proxy and
``max(0, delta)`` as an over-coverage covariate. The gap proxy is exactly the fixed
``clip(1 - O/E, 0, 1)`` mapping with E the earlier-release count, so no fitted parameter enters.
Both building counts are provided columns: ``carbonplan_buildings`` from the strata table (an
earlier Overture release sampled by CarbonPlan) and ``overture_buildings`` from the provided
Overture buildings layer summarized per tract.
"""
from __future__ import annotations

import numpy as np

from bias60.contracts import Emission, IdeaOutput, clip_one_minus_ratio
from bias60.ideas import implements
from bias60.ideas._common import as_coverage, as_float, quantiles


@implements("Tm1")
def tm1(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Tm1"]
    coverage_column = seed["coverage_column"]
    count_column = seed["earlier_release_column"]
    strata = inputs.strata([count_column, coverage_column])
    features = inputs.features([seed["current_column"]])

    earlier = as_float(strata[count_column])
    current = as_float(features[seed["current_column"]])
    covered = as_coverage(strata[coverage_column])

    # An uncovered CarbonPlan cell is undefined, not zero: treating it as zero would fabricate a
    # total building gap. Expectation must also be strictly positive for the ratio to exist.
    expectation = np.where(covered, earlier, np.nan)
    gap, defined = clip_one_minus_ratio(current, expectation)

    over_coverage = np.zeros(gap.shape, dtype=np.float64)
    ratio = np.full(gap.shape, np.nan, dtype=np.float64)
    ratio[defined] = current[defined] / expectation[defined]
    over_coverage[defined] = np.maximum(ratio[defined] - 1.0, 0.0)
    over_coverage[~defined] = 0.0
    if (over_coverage[defined] > seed["covariate_maximum"] + 1e-12).any():
        raise ValueError("Tm1 over-coverage covariate exceeds its preregistered maximum")

    diagnostics = {
        "rows": int(gap.size),
        "defined_gap": int(defined.sum()),
        "undefined_reasons": {
            "carbonplan_uncovered": int((~covered).sum()),
            "earlier_release_not_positive": int((covered & (np.isfinite(earlier))
                                                 & ~(earlier > 0.0)).sum()),
            "current_count_missing": int((~np.isfinite(current)).sum()),
        },
        "gap_distribution": quantiles(gap[defined]),
        "over_coverage_distribution": quantiles(over_coverage[defined]),
        "drop_share_of_defined": float(np.mean(gap[defined] > 0.0)) if defined.any() else 0.0,
        "fixed_mapping": "clip(1 - O/E, 0, 1) with E = provided earlier-release building count",
        "no_fitted_parameter": True,
    }
    return IdeaOutput(
        idea_id="Tm1",
        region=inputs.region,
        geoids=list(inputs.geoids),
        emissions=[
            Emission(component="B", channel="gap", values=gap, defined=defined,
                     metadata={"mapping": "clip(1 - O_now/O_carbonplan, 0, 1)"}),
            Emission(component="B", channel="covariate", values=over_coverage, defined=defined,
                     metadata={"quantity": "max(0, O_now/O_carbonplan - 1)"}),
        ],
        diagnostics=diagnostics,
    )
