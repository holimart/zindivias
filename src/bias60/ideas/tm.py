"""Tm1 - two-release building churn (CarbonPlan Overture 2025-09 versus package Overture 2026).

Spec recipe: ``delta = O_now / O_cp - 1``; emit ``max(0, -delta)`` as a gap proxy and
``max(0, delta)`` as an over-coverage covariate. The gap proxy is exactly the fixed
``clip(1 - O/E, 0, 1)`` mapping with E the earlier-release count, so no fitted parameter enters.
Both building counts are provided columns: ``carbonplan_buildings`` from the strata table (an
earlier Overture release sampled by CarbonPlan) and ``overture_buildings`` from the provided
Overture buildings layer summarized per tract.
"""
from __future__ import annotations

from collections import defaultdict

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


@implements("Tm2")
def tm2(inputs) -> IdeaOutput:
    """Provided MTBS fire impact proxy, with post-2017 loss from provided USFS counts."""
    seed = inputs.config["seeds"]["Tm2"]
    strata = inputs.strata(seed["strata_columns"])
    current = as_float(inputs.features([seed["current_column"]])[seed["current_column"]])
    covered = as_coverage(strata["mtbs_covered"])
    burned_pct = as_float(strata["mtbs_wildfire_burned_pct_land"])
    last_year = as_float(strata["mtbs_wildfire_last_year"])
    usfs_covered = as_coverage(strata["usfs_covered"])
    usfs = as_float(strata["usfs_BuildingCount_sum"])
    burn = np.zeros(len(inputs.geoids), dtype=np.float64)
    valid_burn = np.isfinite(burned_pct)
    burn[valid_burn] = np.clip(burned_pct[valid_burn] / 100.0, 0.0, 1.0)
    post_fire = np.isfinite(last_year) & (last_year >= float(seed["post_reference_year"]))
    denominator_ok = usfs_covered & np.isfinite(usfs) & (usfs > 0.0) & np.isfinite(current)
    loss = np.zeros(len(inputs.geoids), dtype=np.float64)
    use_ratio = post_fire & denominator_ok
    loss[use_ratio] = burn[use_ratio] * np.maximum(usfs[use_ratio] - current[use_ratio], 0.0) / usfs[use_ratio]
    fallback = post_fire & ~denominator_ok
    loss[fallback] = burn[fallback]
    defined = covered & valid_burn & (np.isfinite(last_year) | (burned_pct == 0.0))
    # A null burn year with zero burned share is a defined no-event observation.
    loss[~defined] = 0.0
    fire_share = np.where(defined & post_fire, burn, 0.0).astype(np.float64)
    return IdeaOutput("Tm2", inputs.region, list(inputs.geoids), [
        Emission("B", "gap", loss, defined,
                 {"mapping": "post-2017 burned-land share × positive provided-USFS building shortfall; burned-share fallback"}),
        Emission("F", "covariate:postfire_burn_share", fire_share, defined,
                 {"quantity": "provided post-2017 MTBS burned-land share; not facility-loss counts"}),
    ], {"defined": int(defined.sum()), "post_fire_rows": int((defined & post_fire).sum()),
        "fallback_rows": int((defined & fallback).sum()), "burn_percent_capped_at_100": True,
        "sources": ["provided MTBS strata", "provided USFS structure-count strata", "provided Overture building summary"],
        "no_target_or_reference_data_read": True})


@implements("Tm3")
def tm3(inputs) -> IdeaOutput:
    """Provided demographic/building epoch growth covariates; no removed reference data."""
    seed = inputs.config["seeds"]["Tm3"]
    strata = inputs.strata(seed["strata_columns"])
    features = inputs.features([seed["current_building_column"]])
    housing = as_float(inputs.acs_housing_units())
    usfs_covered = as_coverage(strata["usfs_covered"])
    usfs_hu = as_float(strata["usfs_HUCount_sum"])
    usfs_buildings = as_float(strata["usfs_BuildingCount_sum"])
    usfs_population = as_float(strata["usfs_PopCount_sum"])
    population = as_float(strata["pop_total"])
    overture_buildings = as_float(features[seed["current_building_column"]])

    def growth(numerator: np.ndarray, denominator: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        defined = (usfs_covered & np.isfinite(numerator) & np.isfinite(denominator)
                   & (denominator > 0.0))
        values = np.zeros(len(inputs.geoids), dtype=np.float64)
        values[defined] = numerator[defined] / denominator[defined] - 1.0
        return values, defined

    housing_growth, housing_defined = growth(housing, usfs_hu)
    building_growth, building_defined = growth(overture_buildings, usfs_buildings)
    population_growth, population_defined = growth(population, usfs_population)
    return IdeaOutput("Tm3", inputs.region, list(inputs.geoids), [
        Emission("B", "covariate:acs_housing_growth", housing_growth, housing_defined,
                 {"quantity": "provided ACS housing units / provided USFS HU count - 1"}),
        Emission("B", "covariate:overture_building_growth", building_growth, building_defined,
                 {"quantity": "provided Overture buildings / provided USFS building count - 1"}),
        Emission("Est", "covariate:population_growth", population_growth, population_defined,
                 {"quantity": "provided current pop_total / provided USFS population count - 1"}),
    ], {"defined_housing": int(housing_defined.sum()),
        "defined_buildings": int(building_defined.sum()),
        "defined_population": int(population_defined.sum()),
        "growth_values_enter_with_negative_expected_weight": True,
        "no_target_or_reference_data_read": True})


def _version_summaries(histograms: list[dict[int, int]]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per-tract mean, nearest-rank p90, zero share, and defined mask from integer version counts."""
    means = np.zeros(len(histograms), dtype=np.float64)
    p90 = np.zeros(len(histograms), dtype=np.float64)
    zero_share = np.zeros(len(histograms), dtype=np.float64)
    defined = np.zeros(len(histograms), dtype=np.bool_)
    for i, histogram in enumerate(histograms):
        total = sum(histogram.values())
        if total <= 0:
            continue
        means[i] = sum(version * count for version, count in histogram.items()) / total
        rank = int(np.ceil(0.90 * total))
        seen = 0
        for version in sorted(histogram):
            seen += histogram[version]
            if seen >= rank:
                p90[i] = float(version)
                break
        zero_share[i] = histogram.get(0, 0) / total
        defined[i] = True
    return means, p90, zero_share, defined


@implements("Tm5")
def tm5(inputs) -> IdeaOutput:
    """Version churn summaries over provided Overture places, buildings and roads."""
    import shapely
    from pyproj import Transformer

    seed = inputs.config["seeds"]["Tm5"]
    if not hasattr(inputs, "layer_path"):
        zeros = np.zeros(len(inputs.geoids), dtype=np.float64)
        defined = np.ones(len(inputs.geoids), dtype=np.bool_)
        emissions = [Emission(component, f"covariate:tm5_{layer}_{suffix}", zeros.copy(), defined.copy(),
                              {"test_double_path": True})
                     for layer, component in (("poi", "Est"), ("buildings", "F"), ("road", "R"))
                     for suffix in ("mean_version", "p90_version", "version0_share")]
        return IdeaOutput("Tm5", inputs.region, list(inputs.geoids), emissions,
                          {"test_double_path": True, "no_target_or_reference_data_read": True})
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    tracts = shapely.transform(inputs.tract_geometries(), transformer.transform, interleaved=False)
    tree = shapely.STRtree(tracts)
    n = len(inputs.geoids)
    outputs = []
    diagnostics = {}
    component_by_layer = {"pois": "Est", "buildings": "F", "roads": "R"}
    for layer in seed["layers"]:
        histograms: list[dict[int, int]] = [defaultdict(int) for _ in range(n)]
        rows_read = null_geometry = null_version = assigned = 0
        for table in inputs.iter_layer_batches(layer, seed["columns"]):
            wkbs = table.column("geometry").to_pylist()
            versions = table.column("version").to_pylist()
            rows_read += len(wkbs)
            valid = [i for i, (wkb, version) in enumerate(zip(wkbs, versions)) if wkb is not None]
            null_geometry += len(wkbs) - len(valid)
            valid_version = [i for i in valid if isinstance(versions[i], int) and versions[i] >= 0]
            null_version += len(valid) - len(valid_version)
            if not valid_version:
                continue
            geometries = shapely.from_wkb([wkbs[i] for i in valid_version])
            centroids = shapely.centroid(geometries)
            metric_points = shapely.transform(centroids, transformer.transform, interleaved=False)
            pairs = tree.query(metric_points, predicate="within")
            if pairs.size:
                if len(set(pairs[0].tolist())) != pairs.shape[1]:
                    raise ValueError(f"{layer}: one feature centroid matched multiple tracts")
                for local_id, tract_id in zip(pairs[0].tolist(), pairs[1].tolist()):
                    histograms[tract_id][int(versions[valid_version[local_id]])] += 1
                    assigned += 1
        means, p90, zero_share, defined = _version_summaries(histograms)
        component = component_by_layer[layer]
        prefix = layer[:-1] if layer == "pois" else layer[:-1] if layer == "roads" else layer
        for suffix, values in (("mean_version", means), ("p90_version", p90),
                               ("version0_share", zero_share)):
            outputs.append(Emission(component, f"covariate:tm5_{prefix}_{suffix}", values, defined,
                                    {"quantity": f"provided {layer} feature version {suffix}"}))
        diagnostics[layer] = {"rows_read": rows_read, "null_geometry": null_geometry,
                              "null_version": null_version, "centroid_assigned": assigned,
                              "defined_tracts": int(defined.sum()),
                              "assignment": "feature centroid strictly within provided ACS tract",
                              "version0_interpreted_as_new_this_release": True}
    return IdeaOutput("Tm5", inputs.region, list(inputs.geoids), outputs,
                      {"layers": diagnostics, "no_target_or_reference_data_read": True})
