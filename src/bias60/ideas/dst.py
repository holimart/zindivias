"""Distributional estimators Dst1/2 from the provided Overture places layer."""
from __future__ import annotations

import numpy as np

from bias60.contracts import Emission, IdeaOutput
from bias60.ideas import implements
from bias60.ideas._common import quantiles


def _county_groups(values: object) -> list[str | None]:
    return [None if value is None else str(value) for value in np.asarray(values, dtype=object)]


@implements("Dst1")
def dst1(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Dst1"]
    counts, meta = inputs.poi_shape_capture(seed["source_a"], seed["source_b"])
    f1, f2 = counts["f1"], counts["f2"]
    n, s_obs = counts["poi_total"], counts["s_obs"]
    strata = inputs.strata([seed["county_column"]])
    counties = _county_groups(strata[seed["county_column"]])
    raw = np.zeros(n.shape, dtype=np.float64)
    valid = n > 0
    raw[valid] = f1[valid] / n[valid]
    county_mass: dict[str, float] = {}
    for county in sorted({county for county, ok in zip(counties, valid) if county is not None and ok}):
        mask = np.asarray([(value == county) for value in counties]) & valid
        county_mass[county] = float(f1[mask].sum() / n[mask].sum())
    region_mass = float(f1[valid].sum() / n[valid].sum()) if valid.any() else 0.0
    alpha = float(seed["county_shrinkage_pseudocount"])
    values = np.zeros(n.shape, dtype=np.float64)
    defined = valid.copy()
    for i in np.flatnonzero(valid):
        prior = county_mass.get(counties[i], region_mass)
        values[i] = float(np.clip((f1[i] + alpha * prior) / (n[i] + alpha), 0.0, 1.0))
    chao = np.zeros(n.shape, dtype=np.float64)
    chao_defined = (n > 0) & (f2 > 0)
    chao[chao_defined] = s_obs[chao_defined] + (f1[chao_defined] ** 2) / (2.0 * f2[chao_defined])
    return IdeaOutput(
        idea_id="Dst1", region=inputs.region, geoids=list(inputs.geoids),
        emissions=[Emission(component="Est", channel="gap", values=values, defined=defined,
                            metadata={"estimator": "county-shrunk Good-Turing missing mass f1/N"})],
        diagnostics={"rows": int(n.size), "defined": int(defined.sum()),
                     "undefined_reasons": {"no_primary_category_observations": int((~defined).sum())},
                     "good_turing_raw_distribution": quantiles(raw[valid]),
                     "county_shrunk_missing_mass_distribution": quantiles(values[defined]),
                     "chao1_estimated_category_count_distribution": quantiles(chao[chao_defined]),
                     "chao1_defined": int(chao_defined.sum()), "poi_layer": meta,
                     "county_shrinkage_pseudocount": alpha, "no_fitted_parameter": True})


@implements("Dst2")
def dst2(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Dst2"]
    counts, meta = inputs.poi_shape_capture(seed["source_a"], seed["source_b"])
    strata = inputs.strata([seed["county_column"]])
    counties = _county_groups(strata[seed["county_column"]])
    a, b, overlap, union, total = (counts[key] for key in
                                    ("source_a", "source_b", "source_overlap", "source_union", "poi_total"))
    county_totals: dict[str, np.ndarray] = {}
    for county in sorted({value for value in counties if value is not None}):
        mask = np.asarray([value == county for value in counties])
        county_totals[county] = np.array([a[mask].sum(), b[mask].sum(), overlap[mask].sum(),
                                          union[mask].sum(), total[mask].sum()], dtype=np.float64)
    values = np.zeros(a.shape, dtype=np.float64)
    defined = np.zeros(a.shape, dtype=np.bool_)
    pooled = np.zeros(a.shape, dtype=np.bool_)
    estimates = np.zeros(a.shape, dtype=np.float64)
    for i in range(a.size):
        if overlap[i] >= float(seed["minimum_tract_overlap"]):
            aa, bb, mm, nn, observed = a[i], b[i], overlap[i], union[i], total[i]
        elif counties[i] in county_totals:
            aa, bb, mm, nn, observed = county_totals[counties[i]]
            pooled[i] = True
        else:
            continue
        if mm < float(seed["minimum_pooled_overlap"]) or nn <= 0.0 or observed <= 0.0:
            continue
        nhat = ((aa + 1.0) * (bb + 1.0) / (mm + 1.0)) - 1.0
        if not np.isfinite(nhat) or nhat <= 0.0:
            continue
        estimates[i] = nhat
        values[i] = float(np.clip(1.0 - observed / nhat, 0.0, 1.0))
        defined[i] = True
    return IdeaOutput(
        idea_id="Dst2", region=inputs.region, geoids=list(inputs.geoids),
        emissions=[Emission(component="Est", channel="gap", values=values, defined=defined,
                            metadata={"estimator": "Chapman two-list capture-recapture"})],
        diagnostics={"rows": int(a.size), "defined": int(defined.sum()),
                     "undefined_reasons": {"overlap_below_tract_and_county_floor": int((~defined).sum())},
                     "chapman_population_distribution": quantiles(estimates[defined]),
                     "capture_gap_distribution": quantiles(values[defined]),
                     "capture_counts": {"source_a": int(a.sum()), "source_b": int(b.sum()),
                                        "overlap": int(overlap.sum()), "union": int(union.sum()),
                                        "all_pois": int(total.sum())},
                     "max_tract_overlap": int(overlap.max()) if overlap.size else 0,
                     "max_county_overlap": max((int(value[2]) for value in county_totals.values()), default=0),
                     "tract_rows_pooled_to_county": int(pooled.sum()),
                     "source_a": seed["source_a"], "source_b": seed["source_b"],
                     "excluded_sources": seed["excluded_sources"], "place_layer": meta,
                     "no_fitted_parameter": True})
