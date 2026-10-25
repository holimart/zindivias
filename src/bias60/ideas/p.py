"""P2 normative fire-station spacing and P4 rural arterial-density prior.

Both are label-free fixed recipes. P2 translates preregistered area standards to expected station
counts; P4 uses the highest road-density rural tercile as a within-region completeness anchor only in
its specified eastern-OK and south-central-TX applicability domain.
"""
from __future__ import annotations

import numpy as np

from bias60.contracts import Emission, IdeaOutput, clip_one_minus_ratio
from bias60.ideas import implements
from bias60.ideas._common import as_float, quantiles


@implements("P2")
def p2(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["P2"]
    observed = as_float(inputs.features([seed["observed_fire_column"]])
                        [seed["observed_fire_column"]])
    strata = inputs.strata([seed["area_column"], seed["population_column"], seed["ruca_column"]])
    area = as_float(strata[seed["area_column"]])
    population = as_float(strata[seed["population_column"]])
    ruca = as_float(strata[seed["ruca_column"]])
    area_km2 = area / 1_000_000.0
    standards = np.full(area.shape, np.nan, dtype=np.float64)
    for density_class, codes in seed["ruca_classes"].items():
        standards[np.isin(ruca, np.asarray(codes, dtype=np.float64))] = float(
            seed["standard_area_km2"][density_class])
    expectation = np.full(area.shape, np.nan, dtype=np.float64)
    valid = (np.isfinite(area_km2) & (area_km2 > 0) & np.isfinite(population)
             & (population >= 0) & np.isfinite(standards) & (standards > 0))
    expectation[valid] = area_km2[valid] / standards[valid]
    gap, defined = clip_one_minus_ratio(observed, expectation)
    population_density = np.full(area.shape, np.nan, dtype=np.float64)
    population_density[valid] = population[valid] / area_km2[valid]
    return IdeaOutput(
        idea_id="P2", region=inputs.region, geoids=list(inputs.geoids),
        emissions=[Emission(component="F", channel="gap", values=gap, defined=defined,
                            metadata={"mapping": "clip(1 - O_fire / (ALAND_km2 / A_std), 0, 1)"})],
        diagnostics={
            "rows": int(gap.size), "defined": int(defined.sum()),
            "undefined_reasons": {"missing_or_invalid_ruca_area_population_or_fire": int((~defined).sum())},
            "standard_area_km2_by_ruca_class": seed["standard_area_km2"],
            "expected_fire_distribution": quantiles(expectation[valid]),
            "population_density_distribution": quantiles(population_density[valid]),
            "gap_distribution": quantiles(gap[defined]),
            "no_fitted_parameter": True,
        })


@implements("P4")
def p4(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["P4"]
    road = as_float(inputs.features([seed["road_column"]])[seed["road_column"]])
    strata = inputs.strata([seed["area_column"], seed["ruca_column"]])
    area = as_float(strata[seed["area_column"]])
    ruca = as_float(strata[seed["ruca_column"]])
    area_km2 = area / 1_000_000.0
    density = np.full(area.shape, np.nan, dtype=np.float64)
    valid_density = np.isfinite(road) & (road >= 0) & np.isfinite(area_km2) & (area_km2 > 0)
    density[valid_density] = road[valid_density] / area_km2[valid_density]
    rural = np.isin(ruca, np.asarray(seed["rural_ruca"], dtype=np.float64))
    applicable = inputs.region in seed["applicable_regions"]
    defined = np.zeros(len(inputs.geoids), dtype=np.bool_)
    gap = np.zeros(len(inputs.geoids), dtype=np.float64)
    diagnostics: dict[str, object] = {"rows": int(gap.size), "applicable_region": applicable,
                                      "rural_tracts": int((rural & valid_density).sum()),
                                      "anchor_tracts": 0, "rho_km_per_km2": None,
                                      "gap_distribution": {"count": 0}}
    if applicable:
        rural_valid = rural & valid_density
        if rural_valid.any():
            threshold = float(np.quantile(density[rural_valid], float(seed["anchor_quantile"])))
            anchors = rural_valid & (density >= threshold)
            rho = float(np.median(density[anchors]))
            diagnostics["anchor_tracts"] = int(anchors.sum())
            diagnostics["anchor_threshold_km_per_km2"] = threshold
            if rho > 0 and np.isfinite(rho):
                defined = rural_valid
                gap[defined] = np.clip(1.0 - density[defined] / rho, 0.0, 1.0)
                diagnostics["rho_km_per_km2"] = rho
                diagnostics["gap_distribution"] = quantiles(gap[defined])
    diagnostics["defined"] = int(defined.sum())
    diagnostics["undefined_reasons"] = {
        "outside_eastern_ok_or_south_central_tx": int(not applicable) * len(inputs.geoids),
        "not_rural_or_missing_inputs_or_no_positive_anchor": int(len(inputs.geoids) - defined.sum()
                                                                  - (int(not applicable) * len(inputs.geoids))),
    }
    return IdeaOutput(
        idea_id="P4", region=inputs.region, geoids=list(inputs.geoids),
        emissions=[Emission(component="R", channel="gap", values=gap, defined=defined,
                            metadata={"anchor": "top road-density rural tercile, region median"})],
        diagnostics=diagnostics)
