#!/usr/bin/env python3
"""Audit Overture source provenance by tract and provided stratum.

Only the provided Overture ``bbox`` and ``sources`` columns are read for both
buildings and POIs. Bbox centers are assigned to the authoritative sample using
provided ACS tract geometry. Source-mix shares are appended to the existing
per-tract feature cache but are not inputs to the scored estimator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import estimate_gaps as estimator  # noqa: E402
from audit_poi_categories import (  # noqa: E402
    BUCKET,
    load_sample_tracts,
    read_sample_geoids,
)

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
STRATA_COLUMNS = [
    "GEOID", "svi_overall", "svi_covered", "tribal_any", "pct_urban",
    "epht_heat_days_summer", "epht_covered", "usdm_summer_dsci", "usdm_covered",
]
METRICS = {
    "building_osm_share": "overture_building_osm_source_share",
    "poi_meta_share": "overture_poi_meta_source_share",
    "poi_microsoft_share": "overture_poi_microsoft_source_share",
    "poi_other_upstream_share": "overture_poi_other_upstream_source_share",
}
CORRELATION_METRICS = (
    "building_osm_share", "poi_non_meta_share", "poi_meta_share",
    "poi_microsoft_share", "poi_other_upstream_share",
)
CORRELATES = ("svi_overall", "tribal_any", "pct_urban",
              "epht_heat_days_summer", "usdm_summer_dsci")
BOOTSTRAP_REPLICATES = 2000
BASE_SEED = 20261024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def seed_for(label: str) -> int:
    return BASE_SEED + int.from_bytes(hashlib.sha256(label.encode("utf-8")).digest()[:4], "big")


def bootstrap_mean(values: list[float], seed: int,
                   replicates: int = BOOTSTRAP_REPLICATES) -> list[float] | None:
    if not values:
        return None
    data = np.asarray(values, dtype=np.float64)
    if not np.isfinite(data).all():
        raise ValueError("bootstrap received nonfinite value")
    if len(data) == 1:
        return [float(data[0]), float(data[0])]
    rng = np.random.default_rng(seed)
    estimates = np.empty(replicates, dtype=np.float64)
    chunk_size = 128
    for start in range(0, replicates, chunk_size):
        stop = min(replicates, start + chunk_size)
        indices = rng.integers(0, len(data), size=(stop - start, len(data)), dtype=np.int32)
        estimates[start:stop] = data[indices].mean(axis=1)
    low, high = np.quantile(estimates, [0.025, 0.975])
    return [float(low), float(high)]


def bootstrap_difference_ci(values1: list[float], values0: list[float], seed: int,
                            replicates: int = BOOTSTRAP_REPLICATES) -> dict[str, Any]:
    if not values1 or not values0:
        return {"difference": None, "ci_95": None, "reason": "one comparison group has no valid source share"}
    first = np.asarray(values1, dtype=np.float64)
    second = np.asarray(values0, dtype=np.float64)
    estimate = float(first.mean() - second.mean())
    if len(first) == len(second) == 1:
        return {"group1_mean": float(first[0]), "group0_mean": float(second[0]),
                "difference": estimate, "ci_95": [estimate, estimate],
                "ci_excludes_zero": bool(estimate != 0), "bootstrap_replicates": replicates}
    rng = np.random.default_rng(seed)
    draws = np.empty(replicates, dtype=np.float64)
    chunk_size = 128
    for start in range(0, replicates, chunk_size):
        stop = min(replicates, start + chunk_size)
        idx1 = rng.integers(0, len(first), size=(stop - start, len(first)), dtype=np.int32)
        idx0 = rng.integers(0, len(second), size=(stop - start, len(second)), dtype=np.int32)
        draws[start:stop] = first[idx1].mean(axis=1) - second[idx0].mean(axis=1)
    lower, upper = np.quantile(draws, [0.025, 0.975])
    return {"group1_mean": float(first.mean()), "group0_mean": float(second.mean()),
            "difference": estimate, "ci_95": [float(lower), float(upper)],
            "ci_excludes_zero": bool(lower > 0 or upper < 0),
            "bootstrap_replicates": replicates, "bootstrap_seed": seed}


def average_ranks(values: np.ndarray) -> np.ndarray:
    """One-based average ranks, matching Spearman's tie convention."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    sorted_values = values[order]
    start = 0
    while start < len(values):
        stop = start + 1
        while stop < len(values) and sorted_values[stop] == sorted_values[start]:
            stop += 1
        average_rank = (start + 1 + stop) / 2.0
        ranks[order[start:stop]] = average_rank
        start = stop
    return ranks


def spearman(x_values: list[float | None], y_values: list[float | None]) -> dict[str, Any]:
    pairs = [(float(x), float(y)) for x, y in zip(x_values, y_values)
             if x is not None and y is not None and np.isfinite(float(x)) and np.isfinite(float(y))]
    if len(pairs) < 3:
        return {"n": len(pairs), "rho": None, "reason": "fewer than three complete pairs"}
    x = np.asarray([pair[0] for pair in pairs], dtype=np.float64)
    y = np.asarray([pair[1] for pair in pairs], dtype=np.float64)
    rx, ry = average_ranks(x), average_ranks(y)
    if np.ptp(rx) == 0 or np.ptp(ry) == 0:
        return {"n": len(pairs), "rho": None, "reason": "constant input"}
    rho = float(np.corrcoef(rx, ry)[0, 1])
    return {"n": len(pairs), "rho": rho}


def quantile_bins(values: list[float | None], label: str) -> tuple[list[int | None], list[float]]:
    finite = np.asarray([value for value in values if value is not None], dtype=np.float64)
    if not len(finite):
        return [None] * len(values), []
    cuts = np.quantile(finite, [0.25, 0.5, 0.75])
    bins = [
        int(np.searchsorted(cuts, value, side="right") + 1) if value is not None else None
        for value in values
    ]
    return bins, [float(cut) for cut in cuts]


def upper_quartile_labels(values: list[float | None]) -> tuple[list[str | None], float | None]:
    finite = np.asarray([value for value in values if value is not None], dtype=np.float64)
    if not len(finite):
        return [None] * len(values), None
    cutoff = float(np.quantile(finite, 0.75))
    labels = [
        ("upper_quartile" if value >= cutoff else "lower_three_quartiles")
        if value is not None else None for value in values
    ]
    return labels, cutoff


def source_metrics_for_row(feature: dict) -> dict[str, float | None]:
    output = {name: finite_float(feature.get(column)) for name, column in METRICS.items()}
    meta = output["poi_meta_share"]
    output["poi_non_meta_share"] = 1.0 - meta if meta is not None else None
    return output


def summarize_stratum(rows: list[dict], label: str, region: str) -> dict[str, Any]:
    summaries = {}
    for metric in (*METRICS.keys(), "poi_non_meta_share"):
        values = [row["metrics"][metric] for row in rows if row["metrics"][metric] is not None]
        values = [float(value) for value in values]
        seed = seed_for(f"{region}:{label}:{metric}")
        summaries[metric] = {
            "tracts_in_stratum": len(rows),
            "tracts_with_valid_denominator": len(values),
            "mean_unweighted_across_tracts": float(np.mean(values)) if values else None,
            "tract_cluster_bootstrap_95_ci": bootstrap_mean(values, seed),
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "bootstrap_seed": seed,
        }
    return {"n_tracts": len(rows), "source_share_means": summaries}


def classify_tracts(region: str, records: list[dict]) -> tuple[dict[str, Any], dict[str, Any]]:
    svi_values = [row["svi_overall"] for row in records]
    svi_bins, svi_cuts = quantile_bins(svi_values, "svi_overall")
    heat_labels, heat_cut = upper_quartile_labels([row["epht_heat_days_summer"] for row in records])
    drought_labels, drought_cut = upper_quartile_labels([row["usdm_summer_dsci"] for row in records])
    for index, row in enumerate(records):
        row["svi_quartile"] = svi_bins[index]
        row["heat_group"] = heat_labels[index]
        row["drought_group"] = drought_labels[index]
        urban = row["pct_urban"]
        row["urbanity"] = ("rural" if urban < 0.5 else "urban") if urban is not None else None
    definitions = {
        "svi_quartiles": "region-specific quantiles among official sample tracts with covered finite svi_overall; side=right keeps cutpoint ties together in the higher quartile",
        "svi_q25_q50_q75_cutpoints": svi_cuts,
        "heat_upper_quartile_cutoff": heat_cut,
        "heat_group_rule": "epht_heat_days_summer >= region q75; finite covered values only; ties included above",
        "drought_upper_quartile_cutoff": drought_cut,
        "drought_group_rule": "usdm_summer_dsci >= region q75; finite covered values only; ties included above",
        "urbanity_rule": "provided pct_urban < 0.5 rural; >= 0.5 urban; missing excluded",
        "tribal_rule": "provided tribal_any boolean; missing excluded",
    }
    groups: dict[str, dict[str, list[dict]]] = {
        "svi_overall_quartile": {f"q{i}": [row for row in records if row["svi_quartile"] == i]
                                 for i in range(1, 5)},
        "tribal_any": {
            "tribal_any": [row for row in records if row["tribal_any"] is True],
            "non_tribal": [row for row in records if row["tribal_any"] is False],
        },
        "urbanity": {name: [row for row in records if row["urbanity"] == name]
                     for name in ("rural", "urban")},
        "summer_heat_upper_quartile": {name: [row for row in records if row["heat_group"] == name]
                                        for name in ("upper_quartile", "lower_three_quartiles")},
        "summer_drought_upper_quartile": {name: [row for row in records if row["drought_group"] == name]
                                           for name in ("upper_quartile", "lower_three_quartiles")},
    }
    summarized = {
        group_name: {label: summarize_stratum(rows, f"{group_name}:{label}", region)
                     for label, rows in group_rows.items()}
        for group_name, group_rows in groups.items()
    }
    contrast_pairs = {
        "svi_q4_minus_q1": (groups["svi_overall_quartile"]["q4"], groups["svi_overall_quartile"]["q1"]),
        "tribal_any_minus_non_tribal": (groups["tribal_any"]["tribal_any"], groups["tribal_any"]["non_tribal"]),
        "rural_minus_urban": (groups["urbanity"]["rural"], groups["urbanity"]["urban"]),
        "heat_upper_minus_lower_three": (groups["summer_heat_upper_quartile"]["upper_quartile"],
                                          groups["summer_heat_upper_quartile"]["lower_three_quartiles"]),
        "drought_upper_minus_lower_three": (groups["summer_drought_upper_quartile"]["upper_quartile"],
                                             groups["summer_drought_upper_quartile"]["lower_three_quartiles"]),
    }
    contrasts: dict[str, Any] = {}
    for contrast_name, (group1, group0) in contrast_pairs.items():
        contrasts[contrast_name] = {}
        for metric in ("building_osm_share", "poi_non_meta_share"):
            values1 = [float(row["metrics"][metric]) for row in group1
                       if row["metrics"][metric] is not None]
            values0 = [float(row["metrics"][metric]) for row in group0
                       if row["metrics"][metric] is not None]
            seed = seed_for(f"{region}:{contrast_name}:{metric}:contrast")
            contrasts[contrast_name][metric] = {
                **bootstrap_difference_ci(values1, values0, seed),
                "group1_tracts_with_source_denominator": len(values1),
                "group0_tracts_with_source_denominator": len(values0),
                "contrast_direction": "group1 minus group0",
            }
    correlations: dict[str, Any] = {}
    for source_metric in CORRELATION_METRICS:
        correlations[source_metric] = {}
        source_values = [row["metrics"][source_metric] for row in records]
        for covariate in CORRELATES:
            y_values = [float(row[covariate]) if row[covariate] is not None else None for row in records]
            correlations[source_metric][covariate] = spearman(source_values, y_values)
    return {"definitions": definitions, "stratum_means": summarized,
            "stratum_contrasts": contrasts, "spearman_correlations": correlations}, groups


def run(workspace: Path, report_path: Path, figure_path: Path,
        refresh_source: bool = False) -> dict[str, Any]:
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    feature_root = workspace / "gap-estimator" / "features"
    all_region_results: dict[str, Any] = {}
    all_records: dict[str, list[dict]] = {}
    metadata_updates: list[tuple[Path, dict]] = []

    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        sample_ids, geoid_hash = read_sample_geoids(sample_path)
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError(f"duplicate official sample GEOID: {region}")
        feature_path = feature_root / f"{region}-overture-tract-summary.parquet"
        feature_meta_path = feature_path.with_suffix(".json")
        if not feature_path.is_file():
            raise FileNotFoundError(f"existing core feature cache required: {feature_path}")
        old_meta = json.loads(feature_meta_path.read_text(encoding="utf-8")) if feature_meta_path.is_file() else {}
        base_cache_hash = old_meta.get("source_mix_base_feature_cache_sha256")
        if not base_cache_hash:
            base_cache_hash = old_meta.get("feature_cache_sha256") or sha256_file(feature_path)
        feature_schema = set(pq.read_schema(feature_path).names)
        has_source_cache = set(estimator.SOURCE_MIX_FEATURE_COLUMNS).issubset(feature_schema)
        current_feature_sha = sha256_file(feature_path)
        reuse_source_cache = (not refresh_source and has_source_cache and
                              isinstance(old_meta.get("source_mix"), dict) and
                              old_meta.get("feature_cache_sha256") == current_feature_sha)
        _ids, _geometries, tree, geometry_info = load_sample_tracts(fs, region, sample_ids)
        if reuse_source_cache:
            source_info = old_meta["source_mix"]
            merged_info = {"rows": pq.ParquetFile(feature_path).metadata.num_rows,
                           "columns": pq.read_schema(feature_path).names,
                           "sha256": current_feature_sha,
                           "bytes": feature_path.stat().st_size}
        else:
            source_features, source_counts, source_info = estimator.aggregate_source_mix(
                fs, region, tree, len(sample_ids))
            merged_info = estimator.merge_source_mix_features(
                feature_path, sample_ids, source_features, source_counts)
        feature_table = pq.read_table(feature_path, columns=[
            "GEOID", "overture_buildings", "overture_poi_all", *estimator.SOURCE_MIX_FEATURE_COLUMNS,
        ])
        feature_rows = feature_table.to_pylist()
        if [str(row["GEOID"]) for row in feature_rows] != sample_ids:
            raise ValueError(f"extended feature cache order mismatch for {region}")

        strata_path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        if not strata_path.is_file():
            raise FileNotFoundError(strata_path)
        strata_table = pq.read_table(strata_path, columns=STRATA_COLUMNS)
        strata_values = strata_table.to_pylist()
        strata_map = {str(row["GEOID"]): row for row in strata_values}
        if len(strata_map) != len(strata_values) or not set(sample_ids).issubset(strata_map):
            raise ValueError(f"strata join mismatch for {region}")

        records = []
        for geoid, feature in zip(sample_ids, feature_rows):
            strata = strata_map[geoid]
            svi_covered = bool(strata["svi_covered"]) if strata["svi_covered"] is not None else False
            heat_covered = bool(strata["epht_covered"]) if strata["epht_covered"] is not None else False
            drought_covered = bool(strata["usdm_covered"]) if strata["usdm_covered"] is not None else False
            svi = finite_float(strata["svi_overall"]) if svi_covered else None
            heat = finite_float(strata["epht_heat_days_summer"]) if heat_covered else None
            drought = finite_float(strata["usdm_summer_dsci"]) if drought_covered else None
            tribal_value = strata["tribal_any"]
            tribal = (bool(tribal_value) if isinstance(tribal_value, (bool, np.bool_)) else
                      int(tribal_value) == 1 if tribal_value in (0, 1) else None)
            metrics = source_metrics_for_row(feature)
            records.append({
                "GEOID": geoid, "region": region,
                "svi_overall": svi, "tribal_any": tribal,
                "pct_urban": finite_float(strata["pct_urban"]),
                "epht_heat_days_summer": heat, "usdm_summer_dsci": drought,
                "overture_buildings": int(feature["overture_buildings"]),
                "overture_poi_all": int(feature["overture_poi_all"]),
                "metrics": metrics,
            })
        region_result, groups = classify_tracts(region, records)
        high_svi = [row for row in records if row["svi_quartile"] == 4 and
                    row["metrics"]["building_osm_share"] is not None]
        high_svi.sort(key=lambda row: (row["metrics"]["building_osm_share"], row["GEOID"]))
        lowest = [{
            "GEOID": row["GEOID"],
            "region": region,
            "svi_overall": row["svi_overall"],
            "svi_quartile": row["svi_quartile"],
            "tribal_any": row["tribal_any"],
            "pct_urban": row["pct_urban"],
            "urbanity": row["urbanity"],
            "overture_buildings": row["overture_buildings"],
            "building_osm_source_share": row["metrics"]["building_osm_share"],
        } for row in high_svi[:10]]

        # Preserve the original baseline cache SHA across idempotent source-mix refreshes.
        old_meta.update({
            "aggregate_schema_version": 5,
            "source_mix_base_feature_cache_sha256": base_cache_hash,
            "feature_cache_sha256": merged_info["sha256"],
            "source_mix": source_info,
            "source_mix_geometry": geometry_info,
            "source_mix_columns": estimator.SOURCE_MIX_FEATURE_COLUMNS,
        })
        metadata_updates.append((feature_meta_path, old_meta))
        all_records[region] = records
        all_region_results[region] = {
            "official_sample": {"path": str(sample_path), "rows": len(sample_ids),
                                 "ordered_geoid_sequence_sha256": geoid_hash,
                                 "score_placeholder_values_used": False},
            "strata_input": {"path": str(strata_path), "sha256": sha256_file(strata_path),
                             "bytes": strata_path.stat().st_size,
                             "read_columns": STRATA_COLUMNS,
                             "source_rows": len(strata_values),
                             "extra_strata_rows_excluded": len(strata_values) - len(sample_ids)},
            "provided_acs_geometry": geometry_info,
            "source_inputs": source_info,
            "feature_cache": {
                "path": str(feature_path), "rows": merged_info["rows"],
                "bytes": merged_info["bytes"], "sha256": merged_info["sha256"],
                "base_cache_sha256_before_source_extension": base_cache_hash,
                "read_columns_for_analysis": ["GEOID", "overture_buildings", "overture_poi_all",
                                               *estimator.SOURCE_MIX_FEATURE_COLUMNS],
                "source_mix_columns": estimator.SOURCE_MIX_FEATURE_COLUMNS,
                "source_row_counts_reconciled_to_core_features": True,
            },
            **region_result,
            "named_lowest_osm_high_svi": lowest,
            "high_svi_tracts_without_buildings": sum(
                row["svi_quartile"] == 4 and row["overture_buildings"] == 0 for row in records),
        }

    for path, metadata in metadata_updates:
        path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8")

    figure_path.parent.mkdir(parents=True, exist_ok=True)
    draw_source_figure(all_region_results, figure_path)
    report = {
        "analysis": "Overture source provenance by official tract and provided stratum",
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                        "pyarrow": pa.__version__},
        "interpretation": "descriptive source-attribution analysis; source mentions do not prove feature correctness or local community mapping activity",
        "data_and_restrictions": {
            "external_data_used": [],
            "removed_reference_layers_read": False,
            "sample_score_values_used": False,
            "overture_geometry_read": False,
            "permitted_overture_columns_read_for_source_analysis": ["bbox", "sources"],
            "tract_geometry": "provided ACS GEOID/geometry used only for bbox-center assignment to official sample tracts",
            "strata_columns_read": STRATA_COLUMNS,
            "official_scope": "four authoritative regional sample GEOID lists; any source table rows outside these IDs are excluded",
            "use_in_scored_predictions": "source-mix shares are stored in the feature cache for discovery only and are not read by estimate_one or candidate output",
        },
        "source_definitions": {
            "building_osm_share": "fraction of assigned building records with any dataset name equal to OSM or starting with OpenStreetMap",
            "building_microsoft": "any dataset name starting with Microsoft; observed label includes Microsoft ML Buildings",
            "building_esri": "any dataset name starting with Esri; observed label includes Esri Community Maps",
            "building_exclusive_classes": ["microsoft_only", "microsoft_osm", "osm_only", "esri_only", "other_or_mixed", "no_dataset"],
            "poi_meta": "fraction of assigned POIs with any source dataset named meta",
            "poi_microsoft": "fraction of assigned POIs with any source dataset starting with Microsoft",
            "poi_other_upstream": "fraction of assigned POIs with any source dataset other than meta, Microsoft, Overture, or Overture-signals",
            "poi_non_meta": "1 minus the per-place meta source-membership share; includes Microsoft, other upstream sources, and places without a named upstream provider",
            "poi_membership_shares_overlap": True,
            "place_exclusive_classes": ["meta_not_microsoft", "microsoft_not_meta", "meta_and_microsoft", "other_without_meta_microsoft", "no_upstream"],
            "note": "Sources are multi-valued; membership shares may overlap. The exclusive classes are also reported to show a disjoint partition.",
        },
        "stratum_definitions": {
            "svi_overall": "within-region quartiles q1–q4 over finite covered values; cutpoint ties assigned to higher bin",
            "tribal_any": "provided boolean overlap indicator; true and false groups",
            "urbanity": "rural if pct_urban < 0.5, urban if >= 0.5",
            "heat": "provided epht_heat_days_summer; region-specific top quartile vs lower three",
            "drought": "provided usdm_summer_dsci; region-specific top quartile vs lower three",
        },
        "statistics": {
            "stratum_means": "unweighted mean of tract-level source shares; 95% percentile CI from 2,000 bootstrap resamples of tracts within each stratum",
            "stratum_contrasts": "independent tract bootstrap of group-mean differences for q4−q1, tribal−nontribal, rural−urban, and high-vs-lower heat/drought groups; 2,000 replicates",
            "spearman": "per-region Spearman rank correlation with pairwise complete observations; average ranks for ties; no p-values or causal interpretation",
            "named_tracts": "ten lowest non-null building OSM-source shares among finite-SVI q4 tracts per region, sorted by share then GEOID",
        },
        "uncertainty_and_limitations": [
            "Source membership is a provenance field, not a measurement of source quality, completeness, age, or which source uniquely contributed each geometry.",
            "Buildings/places may have multiple provider attributions; per-provider shares overlap and should not be summed as a composition.",
            "Per-tract means give every tract equal weight; they are not population-weighted and do not measure an individual's map access.",
            "The bootstrap reflects tract resampling only and ignores spatial dependence; correlations are descriptive, region-specific, and not adjusted for covariates.",
            "No labels, hidden targets, external datasets, or removed reference layers were used; the analysis does not estimate organizer scores or actual map completeness.",
        ],
        "figure": {"path": str(figure_path)},
        "regions": all_region_results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report["figure"]["sha256"] = sha256_file(figure_path)
    # Add the figure checksum to the durable report and keep a deterministic final JSON.
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                           encoding="utf-8")
    return report


def draw_source_figure(region_results: dict[str, dict], path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(len(REGIONS), 2, figsize=(13, 15), sharex=True)
    qx = np.arange(1, 5)
    for row_index, region in enumerate(REGIONS):
        result = region_results[region]
        svi_groups = result["stratum_means"]["svi_overall_quartile"]
        ax = axes[row_index, 0]
        metric = "building_osm_share"
        means, lows, highs = [], [], []
        for quartile in range(1, 5):
            stat = svi_groups[f"q{quartile}"]["source_share_means"][metric]
            mean = stat["mean_unweighted_across_tracts"]
            ci = stat["tract_cluster_bootstrap_95_ci"]
            means.append(np.nan if mean is None else mean)
            lows.append(np.nan if ci is None else mean - ci[0])
            highs.append(np.nan if ci is None else ci[1] - mean)
        ax.errorbar(qx, means, yerr=np.asarray([lows, highs]), marker="o", capsize=3,
                    color="#315a86", label="OpenStreetMap-touched buildings")
        ax.set_ylim(-0.04, 1.04)
        ax.set_ylabel("Share of buildings")
        ax.set_title(f"{region}: building sources")
        ax.grid(axis="y", alpha=0.25)
        if row_index == 0:
            ax.legend(loc="best", fontsize=8)

        ax = axes[row_index, 1]
        for metric, label, color in (
            ("poi_meta_share", "Meta", "#6a3d9a"),
            ("poi_microsoft_share", "Microsoft", "#e31a1c"),
            ("poi_other_upstream_share", "Other upstream", "#33a02c"),
        ):
            means, lows, highs = [], [], []
            for quartile in range(1, 5):
                stat = svi_groups[f"q{quartile}"]["source_share_means"][metric]
                mean = stat["mean_unweighted_across_tracts"]
                ci = stat["tract_cluster_bootstrap_95_ci"]
                means.append(np.nan if mean is None else mean)
                lows.append(np.nan if ci is None or mean is None else mean - ci[0])
                highs.append(np.nan if ci is None or mean is None else ci[1] - mean)
            ax.errorbar(qx, means, yerr=np.asarray([lows, highs]), marker="o", capsize=2,
                        color=color, label=label)
        ax.set_ylim(-0.04, 1.04)
        ax.set_ylabel("Per-tract mean source membership")
        ax.set_title(f"{region}: place sources (overlapping memberships)")
        ax.grid(axis="y", alpha=0.25)
        if row_index == 0:
            ax.legend(loc="best", fontsize=8)
    for ax in axes[-1, :]:
        ax.set_xticks(qx, ["Q1", "Q2", "Q3", "Q4"])
        ax.set_xlabel("Region-specific provided SVI quartile (Q4 highest)")
    fig.suptitle("Overture provider-source shares across provided SVI strata\n"
                 "Points are equal-weight tract means; bars are 95% tract-bootstrap CIs", fontsize=13)
    fig.text(0.5, 0.008,
             "Place-provider membership shares can overlap. Associations are descriptive, not source-quality or hidden-score measurements.",
             ha="center", fontsize=8)
    fig.tight_layout(rect=[0, 0.025, 1, 0.94])
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, metadata={"Software": "Matplotlib", "Title": "Overture source provenance by SVI"},
                facecolor="white")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"])
                        if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/source-provenance.json")
    parser.add_argument("--figure", type=Path, default=ROOT / "reports/source-provenance.png")
    parser.add_argument("--refresh-source", action="store_true",
                        help="re-scan remote Overture sources+bbox even when the source feature cache exists")
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    result = run(args.workspace.resolve(), args.out.resolve(), args.figure.resolve(),
                 refresh_source=args.refresh_source)
    print(f"PASS source provenance: four regions, report {args.out.resolve()}, figure {args.figure.resolve()}")
    print(f"Figure SHA-256 {result['figure']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
