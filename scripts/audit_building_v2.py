#!/usr/bin/env python3
"""Audit a fixed v2 building prior against v0 and aggregate level diagnostics.

The score component uses only the provided Overture building count and the
fixed v2 constant. The provided USFS/CarbonPlan strata-derived footprint counts
are read solely for region-level diagnostics and never enter a prediction.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from estimator_v2_building import BUILDING_GAP_V2, estimate_building_component_v2

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
DIAGNOSTIC_COLUMNS = [
    "GEOID", "usfs_covered", "usfs_BuildingCount_sum",
    "carbonplan_covered", "carbonplan_buildings",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_official_ids(path: Path) -> list[str]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if "GEOID" not in (reader.fieldnames or []):
            raise ValueError(f"official sample has no GEOID column: {path}")
        return [row["GEOID"] for row in reader]


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"q10": None, "median": None, "q90": None}
    q10, median, q90 = np.quantile(np.asarray(values, dtype=np.float64), [0.1, 0.5, 0.9])
    return {"q10": float(q10), "median": float(median), "q90": float(q90)}


def valid_ratio(numerator: Any, denominator: Any, covered: Any) -> float | None:
    if not covered or numerator is None or denominator is None:
        return None
    try:
        top, bottom = float(numerator), float(denominator)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(top) or not math.isfinite(bottom) or bottom <= 0:
        return None
    return top / bottom


def summarize_diagnostic(ratios: list[float], label: str) -> dict[str, Any]:
    return {
        "n_positive_denominator_pairs": len(ratios),
        "ratio_definition": label,
        "ratio_quantiles": quantiles(ratios),
        "share_ratio_below_0_8": (sum(value < 0.8 for value in ratios) / len(ratios)
                                   if ratios else None),
        "share_within_1_percent_of_1": (sum(abs(value - 1.0) <= 0.01 for value in ratios) / len(ratios)
                                         if ratios else None),
        "diagnostic_only_not_used_for_scoring": True,
    }


def run(workspace: Path, v0_path: Path, out_path: Path) -> dict[str, Any]:
    region_ids: dict[str, list[str]] = {}
    region_by_geoid: dict[str, str] = {}
    features_by_region: dict[str, dict[str, dict[str, Any]]] = {}
    strata_by_region: dict[str, dict[str, dict[str, Any]]] = {}
    input_hashes: dict[str, dict[str, Any]] = {}

    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        ids = read_official_ids(sample_path)
        if not ids or len(ids) != len(set(ids)):
            raise ValueError(f"empty or duplicate official sample IDs in {region}")
        if any(geoid in region_by_geoid for geoid in ids):
            raise ValueError(f"duplicate official GEOID across regions: {region}")
        region_ids[region] = ids
        region_by_geoid.update({geoid: region for geoid in ids})
        feature_path = workspace / "gap-estimator" / "features" / f"{region}-overture-tract-summary.parquet"
        feature_table = pq.read_table(feature_path, columns=["GEOID", "overture_buildings"])
        feature_rows = feature_table.to_pylist()
        if [str(row["GEOID"]) for row in feature_rows] != ids:
            raise ValueError(f"feature cache order/IDs do not match official sample: {region}")
        features_by_region[region] = {str(row["GEOID"]): row for row in feature_rows}
        strata_path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        strata_table = pq.read_table(strata_path, columns=DIAGNOSTIC_COLUMNS)
        strata_rows = strata_table.to_pylist()
        strata_map = {str(row["GEOID"]): row for row in strata_rows}
        if len(strata_map) != len(strata_rows) or not set(ids).issubset(strata_map):
            raise ValueError(f"provided strata do not cover official sample: {region}")
        strata_by_region[region] = strata_map
        input_hashes[region] = {
            "official_sample": {"rows": len(ids), "sha256": sha256_file(sample_path),
                                 "score_placeholder_values_read": False},
            "feature_cache": {"rows": feature_table.num_rows,
                              "sha256": sha256_file(feature_path),
                              "read_columns": ["GEOID", "overture_buildings"]},
            "provided_strata": {"rows": strata_table.num_rows,
                                "sha256": sha256_file(strata_path),
                                "read_columns": DIAGNOSTIC_COLUMNS},
        }

    if len(region_by_geoid) != 9379:
        raise ValueError(f"expected 9,379 unique official GEOIDs, found {len(region_by_geoid)}")
    with v0_path.open("r", newline="", encoding="utf-8-sig") as stream:
        v0_rows = list(csv.DictReader(stream))
    v0_by_geoid = {row["GEOID"]: row for row in v0_rows}
    if len(v0_by_geoid) != len(v0_rows) or set(v0_by_geoid) != set(region_by_geoid):
        raise ValueError("v0 estimate GEOIDs do not exactly match official sample IDs")

    region_results: dict[str, Any] = {}
    global_v0_values: list[float] = []
    global_v0_defined: list[float] = []
    global_v2_numeric: list[float] = []
    global_v2_defined: list[float] = []
    global_v0_zero_defined = 0

    for region in REGIONS:
        ids = region_ids[region]
        v0_values = [float(v0_by_geoid[geoid]["building_gap"]) for geoid in ids]
        v0_defined_values = [float(v0_by_geoid[geoid]["building_gap"]) for geoid in ids
                            if v0_by_geoid[geoid]["building_defined"] == "1"]
        v0_zero_defined = sum(value == 0 for value in v0_defined_values)
        v2_predictions = [estimate_building_component_v2(
            features_by_region[region][geoid]["overture_buildings"]) for geoid in ids]
        v2_values = [float(row["building_gap"]) for row in v2_predictions]
        v2_defined_values = [float(row["building_gap"]) for row in v2_predictions
                             if row["building_defined"] == 1]

        usfs_ratios = []
        carbonplan_ratios = []
        for geoid in ids:
            overture_count = features_by_region[region][geoid]["overture_buildings"]
            strata = strata_by_region[region][geoid]
            ratio = valid_ratio(overture_count, strata["usfs_BuildingCount_sum"],
                                strata["usfs_covered"])
            if ratio is not None:
                usfs_ratios.append(ratio)
            ratio = valid_ratio(overture_count, strata["carbonplan_buildings"],
                                strata["carbonplan_covered"])
            if ratio is not None:
                carbonplan_ratios.append(ratio)

        region_results[region] = {
            "official_tracts": len(ids),
            "v0": {
                "building_gap_mean_all_tracts": float(np.mean(v0_values)),
                "building_defined_tracts": len(v0_defined_values),
                "building_gap_mean_defined_only": (float(np.mean(v0_defined_values))
                                                    if v0_defined_values else None),
                "fraction_zero_among_defined": (v0_zero_defined / len(v0_defined_values)
                                                if v0_defined_values else None),
            },
            "v2": {
                "building_gap_fixed_when_defined": BUILDING_GAP_V2,
                "building_gap_mean_all_tracts_numeric_with_undefined_placeholder": float(np.mean(v2_values)),
                "building_defined_tracts": len(v2_defined_values),
                "building_defined_share": len(v2_defined_values) / len(ids),
                "building_gap_mean_defined_only": (float(np.mean(v2_defined_values))
                                                    if v2_defined_values else None),
                "overture_buildings_zero_count": len(ids) - len(v2_defined_values),
            },
            "provided_footprint_level_diagnostics": {
                "overture_to_usfs_building_count": summarize_diagnostic(
                    usfs_ratios, "overture_buildings / usfs_BuildingCount_sum"),
                "overture_to_carbonplan_buildings": summarize_diagnostic(
                    carbonplan_ratios, "overture_buildings / carbonplan_buildings"),
                "per_tract_values_exported": False,
                "use_in_scored_predictions": False,
            },
        }
        global_v0_values.extend(v0_values)
        global_v0_defined.extend(v0_defined_values)
        global_v2_numeric.extend(v2_values)
        global_v2_defined.extend(v2_defined_values)
        global_v0_zero_defined += v0_zero_defined

    report = {
        "analysis": "Fixed near-zero building component v2 and aggregate level diagnostics",
        "building_v2_rule": {
            "fixed_gap": BUILDING_GAP_V2,
            "defined_when": "provided overture_buildings count > 0",
            "undefined_when": "provided overture_buildings count == 0; serialized numeric placeholder 0 is excluded by building_defined=0",
            "inputs_to_scored_component": ["overture_buildings", "fixed constant 0.01"],
            "usfs_or_carbonplan_input_to_scored_component": False,
            "leaderboard_calibration_used": False,
            "acs_housing_units_used_as_denominator_floor": False,
        },
        "v0_before": {
            "official_tracts": len(global_v0_values),
            "building_gap_mean_all_tracts": float(np.mean(global_v0_values)),
            "building_defined_tracts": len(global_v0_defined),
            "building_gap_mean_defined_only": (float(np.mean(global_v0_defined))
                                                if global_v0_defined else None),
            "fraction_zero_among_defined": (global_v0_zero_defined / len(global_v0_defined)
                                            if global_v0_defined else None),
            "input_path": str(v0_path.relative_to(ROOT)),
            "input_sha256": sha256_file(v0_path),
        },
        "v2_after": {
            "official_tracts": len(global_v2_numeric),
            "building_gap_mean_all_tracts_numeric_with_undefined_placeholder": float(np.mean(global_v2_numeric)),
            "building_defined_tracts": len(global_v2_defined),
            "building_defined_share": len(global_v2_defined) / len(global_v2_numeric),
            "building_gap_mean_defined_only": (float(np.mean(global_v2_defined))
                                                if global_v2_defined else None),
        },
        "inputs": input_hashes,
        "region_results": region_results,
        "restrictions": {
            "provided_usfs_and_carbonplan_strata_fields_used_only_for_aggregated_region_level_diagnostics": True,
            "per_tract_usfs_or_carbonplan_values_used_in_scored_component": False,
            "per_tract_diagnostic_rows_written": False,
            "labels_or_sample_score_placeholders_used": False,
            "removed_reference_layer_files_or_equivalents_read": False,
            "external_data_used": [],
            "scored_candidate_files_modified": False,
            "leaderboard_or_score_probe_performed": False,
        },
        "interpretation_limitations": [
            "The USFS and CarbonPlan strata columns are Microsoft-footprint derivatives and are used here only for aggregate level diagnostics; they are never model inputs or per-tract scored predictions.",
            "Overture source attribution and aggregate count ratios do not establish exact geometric identity, completeness, or matching vintage with the hidden reference.",
            "Using a positive Overture building count as the definedness proxy may miss a reference footprint that Overture entirely omits; this limitation is explicit and not calibrated from leaderboard feedback.",
            "The fixed 0.01 prior is a model-derived near-zero level, not a fitted hidden-target value or accuracy claim.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--v0-estimates", type=Path, default=ROOT / "reports/gap-estimates.csv")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/building-v2-audit.json")
    args = parser.parse_args()
    report = run(args.workspace.resolve(), args.v0_estimates.resolve(), args.out.resolve())
    print(f"v0 building mean={report['v0_before']['building_gap_mean_defined_only']:.6f}; "
          f"v2 fixed mean={report['v2_after']['building_gap_mean_defined_only']:.6f} "
          f"over {report['v2_after']['building_defined_tracts']} defined tracts")
    print(f"Audit: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
