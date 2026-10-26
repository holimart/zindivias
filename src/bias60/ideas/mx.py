"""Family Mx: provider-mixture signals from provided Overture layers only.

No reference layers, labels, external geography, or leaderboard values are accessed.
"""
from __future__ import annotations

import numpy as np

from bias60.contracts import Emission, IdeaOutput
from bias60.ideas import implements
from bias60.ideas._common import as_float, quantiles
from bias60.ideas.m import (
    _bbox_assignments, _blank, _covariate, _facility_name,
    _iter_batches, _normalized_dataset, _spatial_index,
)


def _source_set(value) -> set[str]:
    # Empty dataset values and the aggregate Overture provenance records are not provider
    # memberships. Exclude them so singleton-source tests remain meaningful on conflated rows.
    administrative = {"", "overture", "overture-signals"}
    return {name for name in _normalized_dataset(value) if name not in administrative}


def _record_names(names) -> tuple[str, ...]:
    from bias60.ideas.sc_schema import normalize_names
    return normalize_names(names)


def _education_name(names, regex) -> bool:
    return any(regex.search(name) for name in _record_names(names))


@implements("Mx1")
def mx1(inputs) -> IdeaOutput:
    """TomTom share of clipped named-class and tertiary road length."""
    import shapely
    from pyproj import Transformer

    seed = inputs.config["seeds"]["Mx1"]
    n = len(inputs.geoids)
    polygons, _ = _spatial_index(inputs)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    metric_polygons = shapely.transform(polygons, transformer.transform, interleaved=False)
    tree = shapely.STRtree(metric_polygons)
    groups = {"named": frozenset(seed["named_classes"]), "tertiary": frozenset([seed["tertiary_class"]])}
    total = {key: _blank(n) for key in groups}
    tomtom = {key: _blank(n) for key in groups}
    scanned = selected = 0
    tomtom_source = str(seed["tomtom_source"]).casefold()

    for batch in _iter_batches(inputs, "roads", ["class", "sources", "geometry"]):
        classes = batch.column("class").to_pylist()
        sources = batch.column("sources").to_pylist()
        wkbs = batch.column("geometry").to_pylist()
        scanned += len(classes)
        indices, groups_for_rows, is_tomtom = [], [], []
        for i, klass in enumerate(classes):
            key = next((name for name, classes_set in groups.items()
                        if str(klass or "").casefold() in classes_set), None)
            if key is None or wkbs[i] is None:
                continue
            source_names = _source_set(sources[i])
            indices.append(i)
            groups_for_rows.append(key)
            is_tomtom.append(tomtom_source in source_names)
        selected += len(indices)
        for start in range(0, len(indices), 40_000):
            subset = indices[start:start + 40_000]
            lines = shapely.from_wkb([wkbs[i] for i in subset])
            lines = shapely.transform(lines, transformer.transform, interleaved=False)
            pairs = tree.query(lines, predicate="intersects")
            if pairs.size == 0:
                continue
            lengths = shapely.length(shapely.intersection(lines[pairs[0]], metric_polygons[pairs[1]]))
            for line_idx, tract_idx, length in zip(pairs[0].tolist(), pairs[1].tolist(), lengths.tolist()):
                if not np.isfinite(length) or length <= 0.0:
                    continue
                source_index = start + line_idx
                group = groups_for_rows[source_index]
                total[group][tract_idx] += length
                if is_tomtom[source_index]:
                    tomtom[group][tract_idx] += length

    emissions = []
    coverage = {}
    for key in ("named", "tertiary"):
        defined = total[key] > 0.0
        share = _blank(n)
        share[defined] = tomtom[key][defined] / total[key][defined]
        channel = f"tomtom_{key}_length_share"
        emissions.append(_covariate("R", channel, share, defined,
                                    f"share of clipped {key} road length with a TomTom source membership"))
        coverage[key] = int(defined.sum())
    return IdeaOutput("Mx1", inputs.region, list(inputs.geoids), emissions, {
        "rows_scanned": scanned, "classified_road_rows": selected,
        "named_length_m": float(total["named"].sum()),
        "tertiary_length_m": float(total["tertiary"].sum()),
        "tomtom_named_length_m": float(tomtom["named"].sum()),
        "tomtom_tertiary_length_m": float(tomtom["tertiary"].sum()),
        "coverage_by_class": coverage,
        "source_membership_rule": "count each clipped feature once in its class denominator; numerator when sources.dataset includes TomTom, case-insensitive",
        "geometry": "WKB linework transformed to EPSG:5070 and clipped to official tract polygons",
        "no_target_or_reference_data_read": True,
    })


@implements("Mx2")
def mx2(inputs) -> IdeaOutput:
    """Microsoft-only place shares overall and among facility-name-like places."""
    seed = inputs.config["seeds"]["Mx2"]
    n = len(inputs.geoids)
    _, tree = _spatial_index(inputs)
    totals = np.zeros(n, dtype=np.int64)
    microsoft = np.zeros(n, dtype=np.int64)
    facility_totals = np.zeros(n, dtype=np.int64)
    facility_microsoft = np.zeros(n, dtype=np.int64)
    scanned = outside = 0
    microsoft_source = str(seed["microsoft_source"]).casefold()
    for batch in _iter_batches(inputs, "pois", ["bbox", "sources", "names"]):
        bboxes = batch.column("bbox").to_pylist()
        sources = batch.column("sources").to_pylist()
        names = batch.column("names").to_pylist()
        assigned = _bbox_assignments(bboxes, tree, len(bboxes))
        scanned += len(bboxes)
        for i, tract in enumerate(assigned.tolist()):
            if tract < 0:
                outside += 1
                continue
            source_names = _source_set(sources[i])
            is_ms_only = source_names == {microsoft_source}
            totals[tract] += 1
            microsoft[tract] += int(is_ms_only)
            if _facility_name(names[i]):
                facility_totals[tract] += 1
                facility_microsoft[tract] += int(is_ms_only)
    all_defined, facility_defined = totals > 0, facility_totals > 0
    all_share, facility_share = _blank(n), _blank(n)
    all_share[all_defined] = microsoft[all_defined] / totals[all_defined]
    facility_share[facility_defined] = facility_microsoft[facility_defined] / facility_totals[facility_defined]
    return IdeaOutput("Mx2", inputs.region, list(inputs.geoids), [
        _covariate("Est", "microsoft_only_place_share", all_share, all_defined,
                   "share of assigned places whose distinct source dataset set is exactly Microsoft"),
        _covariate("F", "facility_microsoft_only_place_share", facility_share, facility_defined,
                   "share of facility-name-like assigned places whose distinct source set is exactly Microsoft"),
    ], {
        "rows_scanned": scanned, "assigned_places": int(totals.sum()),
        "microsoft_only_places": int(microsoft.sum()),
        "facility_name_places": int(facility_totals.sum()),
        "facility_microsoft_only_places": int(facility_microsoft.sum()),
        "outside_or_unassigned_rows": outside,
        "microsoft_only_rule": "after excluding administrative Overture and Overture Signals source records, the deduplicated provider set equals {'microsoft'}; mixed-provider features excluded",
        "facility_name_rule": "shared M-family facility name regex",
        "no_target_or_reference_data_read": True,
    })


@implements("Mx3")
def mx3(inputs) -> IdeaOutput:
    """Foursquare-only place count divided by all assigned Overture places."""
    seed = inputs.config["seeds"]["Mx3"]
    n = len(inputs.geoids)
    _, tree = _spatial_index(inputs)
    totals = np.zeros(n, dtype=np.int64)
    foursquare = np.zeros(n, dtype=np.int64)
    scanned = outside = 0
    source = str(seed["foursquare_source"]).casefold()
    for batch in _iter_batches(inputs, "pois", ["bbox", "sources"]):
        bboxes = batch.column("bbox").to_pylist()
        sources = batch.column("sources").to_pylist()
        assigned = _bbox_assignments(bboxes, tree, len(bboxes))
        scanned += len(bboxes)
        for i, tract in enumerate(assigned.tolist()):
            if tract < 0:
                outside += 1
                continue
            totals[tract] += 1
            foursquare[tract] += int(_source_set(sources[i]) == {source})
    defined = totals > 0
    share = _blank(n)
    share[defined] = foursquare[defined] / totals[defined]
    return IdeaOutput("Mx3", inputs.region, list(inputs.geoids), [
        _covariate("Est", "foursquare_only_place_share", share, defined,
                   "Foursquare-only assigned place count divided by all assigned Overture places"),
    ], {
        "rows_scanned": scanned, "assigned_places": int(totals.sum()),
        "foursquare_only_places": int(foursquare.sum()),
        "outside_or_unassigned_rows": outside,
        "foursquare_only_rule": "after excluding administrative Overture and Overture Signals source records, the deduplicated provider set equals {'foursquare'}",
        "no_target_or_reference_data_read": True,
    })


@implements("Mx5")
def mx5(inputs) -> IdeaOutput:
    """Source-stratified share of education-name places outside the frozen school category set."""
    import re

    seed = inputs.config["seeds"]["Mx5"]
    n = len(inputs.geoids)
    _, tree = _spatial_index(inputs)
    providers = list(seed["source_buckets"])
    denominator = {provider: np.zeros(n, dtype=np.int64) for provider in providers}
    mismatch = {provider: np.zeros(n, dtype=np.int64) for provider in providers}
    school_categories = {str(value).casefold() for value in seed["school_categories"]}
    education_pattern = re.compile(seed["education_name_regex"], re.IGNORECASE)
    scanned = outside = education_named = multisource = 0
    for batch in _iter_batches(inputs, "pois", ["bbox", "sources", "names", "categories"]):
        bboxes = batch.column("bbox").to_pylist()
        sources = batch.column("sources").to_pylist()
        names = batch.column("names").to_pylist()
        categories = batch.column("categories").to_pylist()
        assigned = _bbox_assignments(bboxes, tree, len(bboxes))
        scanned += len(bboxes)
        for i, tract in enumerate(assigned.tolist()):
            if tract < 0:
                outside += 1
                continue
            if not _education_name(names[i], education_pattern):
                continue
            education_named += 1
            primary = categories[i].get("primary") if isinstance(categories[i], dict) else None
            is_mismatch = str(primary or "").strip().casefold() not in school_categories
            source_names = _source_set(sources[i])
            buckets = sorted(source_names & set(providers[:-1]))
            if len(buckets) > 1:
                multisource += 1
            if not buckets:
                buckets = [providers[-1]]
            for provider in buckets:
                denominator[provider][tract] += 1
                mismatch[provider][tract] += int(is_mismatch)
    emissions = []
    coverage = {}
    for provider in providers:
        defined = denominator[provider] > 0
        share = _blank(n)
        share[defined] = mismatch[provider][defined] / denominator[provider][defined]
        emissions.append(_covariate("F", f"school_category_mismatch_share_{provider}", share, defined,
                                    f"share of education-name places from {provider} whose primary category is outside the frozen school set"))
        coverage[provider] = {"defined_tracts": int(defined.sum()),
                              "education_name_places": int(denominator[provider].sum()),
                              "category_mismatches": int(mismatch[provider].sum())}
    return IdeaOutput("Mx5", inputs.region, list(inputs.geoids), emissions, {
        "rows_scanned": scanned, "education_name_places": education_named,
        "outside_or_unassigned_rows": outside, "multisource_education_places": multisource,
        "source_buckets": providers, "coverage_by_source": coverage,
        "category_rule": "categories.primary only; missing or non-school value is outside the registered school category set",
        "school_categories": sorted(school_categories),
        "education_name_regex": seed["education_name_regex"],
        "multisource_rule": "a place contributes to each recognized source bucket it lists; otherwise to other",
        "no_target_or_reference_data_read": True,
    })


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
