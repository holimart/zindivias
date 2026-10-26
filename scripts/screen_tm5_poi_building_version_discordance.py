#!/usr/bin/env python3
"""Measure within-region percentile discordance of provided Tm5 POI/building p90 versions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import official_scored_pairs  # noqa: E402
from screen_tm5_building_p90_mx4_interaction import (  # noqa: E402
    REGIONS, TM5_SHA, sha256_file, feature_digest, read_emission, parquet_bytes,
)
from screen_tm5_p90_version_mx4_interaction import (  # noqa: E402
    EXPECTED_ARTIFACT_HASHES, distribution as source_distribution,
)

MIN_COVERAGE = 0.9859
MIN_P95_DISCORDANCE = 0.05
POI_EMISSION = ("Tm5", "Est", "covariate:tm5_poi_p90_version")
BUILDING_EMISSION = ("Tm5", "F", "covariate:tm5_buildings_p90_version")


def empirical_midpercentile(values: np.ndarray, defined: np.ndarray) -> np.ndarray:
    """Return normalized within-sample midranks; ties share average rank, endpoints map 0/1."""
    x = np.asarray(values, dtype=np.float64)
    mask = np.asarray(defined, dtype=bool)
    if x.ndim != 1 or mask.shape != x.shape:
        raise ValueError("values and defined mask must be aligned vectors")
    if np.any(mask & ~np.isfinite(x)):
        raise ValueError("defined values must be finite")
    result = np.full(x.shape, np.nan, dtype=np.float64)
    idx = np.flatnonzero(mask)
    n = idx.size
    if n == 0:
        return result
    order = idx[np.argsort(x[idx], kind="mergesort")]
    sorted_values = x[order]
    start = 0
    while start < n:
        end = start + 1
        while end < n and sorted_values[end] == sorted_values[start]:
            end += 1
        # Average of 1-based ranks start+1 through end, normalized to [0,1].
        average_rank = ((start + 1) + end) / 2.0
        percentile = 0.5 if n == 1 else (average_rank - 1.0) / (n - 1.0)
        result[order[start:end]] = percentile
        start = end
    if np.any(mask & (~np.isfinite(result) | (result < 0.0) | (result > 1.0))):
        raise AssertionError("defined midpercentiles escaped [0,1]")
    return result


def version_discordance(official: list[tuple[str, str]],
                        poi: np.ndarray, poi_defined: np.ndarray,
                        building: np.ndarray, building_defined: np.ndarray) -> dict[str, Any]:
    n = len(official)
    poi = np.asarray(poi, dtype=np.float64)
    building = np.asarray(building, dtype=np.float64)
    poi_mask = np.asarray(poi_defined, dtype=bool)
    building_mask = np.asarray(building_defined, dtype=bool)
    if any(v.shape != (n,) for v in (poi, building, poi_mask, building_mask)):
        raise ValueError("official pairs and both version channels/masks must align")
    for values, mask, name in ((poi, poi_mask, "POI"), (building, building_mask, "building")):
        if np.any(mask & ~np.isfinite(values)):
            raise ValueError(f"defined {name} versions must be finite")
        if np.any(~mask & np.isfinite(values)):
            raise ValueError(f"undefined {name} versions must remain null")
        if np.any(mask & (values < 0.0)):
            raise ValueError(f"defined {name} versions must be nonnegative")
    poi_pct = np.full(n, np.nan, dtype=np.float64)
    building_pct = np.full(n, np.nan, dtype=np.float64)
    for region in REGIONS:
        local = np.asarray([r == region for _, r in official], dtype=bool)
        poi_pct[local] = empirical_midpercentile(poi[local], poi_mask[local])
        building_pct[local] = empirical_midpercentile(building[local], building_mask[local])
    joint = poi_mask & building_mask
    difference = np.full(n, np.nan, dtype=np.float64)
    difference[joint] = np.abs(poi_pct[joint] - building_pct[joint])
    if np.any(joint & (~np.isfinite(difference) | (difference < 0.0) | (difference > 1.0))):
        raise ValueError("defined percentile discordance must be finite and in [0,1]")
    return {"poi_percentile": poi_pct, "building_percentile": building_pct,
            "discordance": difference, "defined": joint,
            "poi_defined": poi_mask, "building_defined": building_mask}


def distribution(values: np.ndarray) -> dict[str, float | int | None]:
    x = np.asarray(values, dtype=np.float64)
    if x.ndim != 1 or (x.size and not np.isfinite(x).all()):
        raise ValueError("distribution requires finite one-dimensional values")
    if not x.size:
        return {"n": 0, "min": None, "p50": None, "p90": None,
                "p95": None, "max": None, "mean": None}
    return {"n": int(x.size), "min": float(x.min()), "p50": float(np.quantile(x, .5)),
            "p90": float(np.quantile(x, .9)), "p95": float(np.quantile(x, .95)),
            "max": float(x.max()), "mean": float(x.mean())}


def output_table(official: list[tuple[str, str]], poi: np.ndarray, building: np.ndarray,
                 result: dict[str, Any]) -> pa.Table:
    n = len(official)
    arrays = (poi, building, result["poi_defined"], result["building_defined"],
              result["poi_percentile"], result["building_percentile"],
              result["discordance"], result["defined"])
    if any(np.asarray(v).shape != (n,) for v in arrays):
        raise ValueError("output arrays must align to exact official ID partition")
    joint = np.asarray(result["defined"], dtype=bool)
    poi_mask = np.asarray(result["poi_defined"], dtype=bool)
    building_mask = np.asarray(result["building_defined"], dtype=bool)
    if not np.array_equal(joint, poi_mask & building_mask):
        raise ValueError("discordance mask must equal exact joint source-defined mask")
    disc = np.asarray(result["discordance"], dtype=np.float64)
    if np.any(joint & (~np.isfinite(disc) | (disc < 0.0) | (disc > 1.0))):
        raise ValueError("defined percentile differences must be finite and bounded [0,1]")
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "tm5_poi_p90_version": pa.array([float(v) if ok else None for v, ok in zip(poi, poi_mask)], type=pa.float64()),
        "poi_version_defined": pa.array(poi_mask, type=pa.bool_()),
        "poi_within_region_midpercentile": pa.array([float(v) if ok else None for v, ok in zip(result["poi_percentile"], poi_mask)], type=pa.float64()),
        "tm5_building_p90_version": pa.array([float(v) if ok else None for v, ok in zip(building, building_mask)], type=pa.float64()),
        "building_version_defined": pa.array(building_mask, type=pa.bool_()),
        "building_within_region_midpercentile": pa.array([float(v) if ok else None for v, ok in zip(result["building_percentile"], building_mask)], type=pa.float64()),
        "joint_defined": pa.array(joint, type=pa.bool_()),
        "absolute_cross_layer_percentile_difference": pa.array([float(v) if ok else None for v, ok in zip(disc, joint)], type=pa.float64()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    poi_report_path = ROOT / "reports/tm5-p90-version-mx4-interaction.json"
    building_report_path = ROOT / "reports/tm5-building-p90-mx4-interaction.json"
    poi_report = json.loads(poi_report_path.read_text(encoding="utf-8"))
    building_report = json.loads(building_report_path.read_text(encoding="utf-8"))
    if (poi_report["scope"]["official_ids"] != 9379
            or poi_report["scope"]["tm5_p90_version_defined_ids"] != 9377
            or not np.isclose(poi_report["measure_after"]["p95_absolute_change_from_normalized_p90"], 0.9415887339401863, rtol=0, atol=1e-12)
            or building_report["scope"]["official_ids"] != 9379
            or building_report["scope"]["tm5_building_p90_defined_ids"] != 9377
            or not np.isclose(building_report["measure_after"]["p95_absolute_change_from_normalized_building_p90_version"], 0.7737056144690831, rtol=0, atol=1e-12)):
        raise ValueError("prior POI/building version screens differ from assigned baselines")
    expected_tm5_sha = EXPECTED_ARTIFACT_HASHES["Tm5"]
    if (poi_report["provenance"]["frozen_inputs"]["Tm5"]["sha256"] != expected_tm5_sha
            or building_report["provenance"]["frozen_inputs"]["Tm5"]["sha256_before"] != expected_tm5_sha
            or expected_tm5_sha != TM5_SHA):
        raise ValueError("prior version screens do not match the same frozen Tm5 artifact")

    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != 9379 or len(set(official)) != 9379 or len({g for g, _ in official}) != 9379
            or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official scored IDs must form exact 9,379-ID four-region partition")
    official_hash = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode("utf-8")).hexdigest()
    if (official_hash != poi_report["provenance"]["official_geoid_region_sha256"]
            or official_hash != building_report["provenance"]["official_geoid_region_sha256"]):
        raise ValueError("official partition differs from one of the source screens")

    tm5_path = workspace / "ideas/Tm5.parquet"
    poi_values, poi_mask = read_emission(tm5_path, expected_tm5_sha, *POI_EMISSION, official)
    building_values, building_mask = read_emission(tm5_path, expected_tm5_sha, *BUILDING_EMISSION, official)
    if int(poi_mask.sum()) != 9377 or int(building_mask.sum()) != 9377:
        raise ValueError("source version coverage changed from the completed screens")
    if (not np.isclose(poi_values[poi_mask].min(), 1.0, rtol=0, atol=1e-12)
            or not np.isclose(poi_values[poi_mask].max(), 13.0, rtol=0, atol=1e-12)
            or not np.isclose(building_values[building_mask].min(), 1.0, rtol=0, atol=1e-12)
            or not np.isclose(building_values[building_mask].max(), 5.0, rtol=0, atol=1e-12)):
        raise ValueError("provided p90 versions no longer match their recorded [1,13] and [1,5] ranges")
    result = version_discordance(official, poi_values, poi_mask, building_values, building_mask)
    joint = np.asarray(result["defined"], dtype=bool)
    coverage = float(joint.mean())
    overall = distribution(result["discordance"][joint])
    regions: dict[str, Any] = {}
    for region in REGIONS:
        local = np.asarray([r == region for _, r in official], dtype=bool)
        local_joint = joint & local
        regions[region] = {
            "official_ids": int(local.sum()), "poi_defined_ids": int((poi_mask & local).sum()),
            "building_defined_ids": int((building_mask & local).sum()),
            "joint_defined_ids": int(local_joint.sum()),
            "joint_coverage_fraction": float(local_joint.sum() / local.sum()),
            "poi_source_version_distribution": source_distribution(poi_values[poi_mask & local]),
            "building_source_version_distribution": source_distribution(building_values[building_mask & local]),
            "poi_midpercentile_distribution": distribution(result["poi_percentile"][poi_mask & local]),
            "building_midpercentile_distribution": distribution(result["building_percentile"][building_mask & local]),
            "absolute_cross_layer_percentile_difference": distribution(result["discordance"][local_joint]),
        }
    p95 = overall["p95"]
    table = output_table(official, poi_values, building_values, result)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("discordance artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    registry_path = ROOT / "config/ideas.json"
    registry_before = sha256_file(registry_path)
    tm5_after = sha256_file(tm5_path)
    registry_after = sha256_file(registry_path)
    frozen_unchanged = tm5_after == expected_tm5_sha and registry_after == registry_before
    if not frozen_unchanged:
        raise AssertionError("frozen Tm5 artifact or registry changed during screen")
    exact_ids = table.num_rows == 9379 and len(set(official)) == 9379
    bounded = bool(np.isfinite(result["discordance"][joint]).all()
                   and np.all((result["discordance"][joint] >= 0.0) & (result["discordance"][joint] <= 1.0))
                   and np.all((result["poi_percentile"][poi_mask] >= 0.0) & (result["poi_percentile"][poi_mask] <= 1.0))
                   and np.all((result["building_percentile"][building_mask] >= 0.0) & (result["building_percentile"][building_mask] <= 1.0)))
    acceptance = {
        "exact_9379_official_id_alignment": bool(exact_ids),
        "joint_defined_ids": int(joint.sum()), "joint_coverage_fraction": coverage,
        "coverage_at_least_98_59_percent": bool(coverage >= MIN_COVERAGE),
        "joint_nulls_preserved_exactly": bool(np.array_equal(joint, poi_mask & building_mask)),
        "percentiles_and_discordance_finite_bounded_0_1": bounded,
        "p95_absolute_cross_layer_percentile_difference": p95,
        "p95_discordance_at_least_0_05": bool(p95 >= MIN_P95_DISCORDANCE),
        "frozen_Tm5_and_registry_unchanged": bool(frozen_unchanged),
        "distinct_cross_layer_screen_pass": bool(exact_ids and coverage >= MIN_COVERAGE and bounded
                                                   and p95 >= MIN_P95_DISCORDANCE and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "screen": "Within-region midpercentile discordance between Tm5 POI and building p90 versions; unregistered label-free sensitivity",
        "method": {
            "rank": "Within each official region and separately for each channel's own defined values, stable-sort values and assign average 1-based ranks to ties. Normalize midrank as (average_rank−1)/(n−1), mapping sample minimum to 0 and maximum to 1; singleton defined groups map to 0.5.",
            "candidate": "Absolute difference between POI and building normalized midranks, measured only on the joint-defined GEOIDs. Each channel is ranked over its own defined regional observations before taking the joint intersection.",
            "nulls": "Source values and ranks retain their individual defined masks; cross-layer difference is null unless both source channels are defined.",
            "unregistered": True,
        },
        "measure_before": {
            "POI_p90_version_range": [1.0, 13.0],
            "building_p90_version_range": [1.0, 5.0],
            "POI_p90_Mx4_interaction_coverage": poi_report["scope"]["coverage_fraction"],
            "POI_p90_Mx4_interaction_p95_change": poi_report["measure_after"]["p95_absolute_change_from_normalized_p90"],
            "building_p90_Mx4_interaction_coverage": building_report["scope"]["coverage_fraction"],
            "building_p90_Mx4_interaction_p95_change": building_report["measure_after"]["p95_absolute_change_from_normalized_building_p90_version"],
            "discordance_previously_measured": False,
            "poi_interaction_report_sha256": sha256_file(poi_report_path),
            "building_interaction_report_sha256": sha256_file(building_report_path),
        },
        "scope": {
            "official_ids": len(official), "exact_official_geoid_region_partition": exact_ids,
            "poi_defined_ids": int(poi_mask.sum()), "building_defined_ids": int(building_mask.sum()),
            "joint_defined_ids": int(joint.sum()), "joint_undefined_ids": int((~joint).sum()),
            "joint_coverage_fraction": coverage,
            "labels_read": False, "sample_score_values_read": False,
            "removed_or_equivalent_reference_data_read": False, "external_data_read": False,
            "leaderboard_feedback_used": False, "registry_integration": False,
            "frozen_Tm5_modified": False, "uploads": 0,
        },
        "measure_after": {
            "absolute_cross_layer_percentile_difference": overall,
            "p95_absolute_cross_layer_percentile_difference": p95,
            "regions": regions,
        },
        "thresholds": {"minimum_joint_coverage_inclusive": MIN_COVERAGE,
                       "percentile_and_difference_bounds_inclusive": [0.0, 1.0],
                       "minimum_p95_discordance_inclusive": MIN_P95_DISCORDANCE},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_hash,
            "Tm5_artifact": {"path": str(tm5_path), "sha256_before": expected_tm5_sha,
                             "sha256_after": tm5_after,
                             "poi_p90_version_emission_sha256": feature_digest(official, poi_values, poi_mask),
                             "building_p90_version_emission_sha256": feature_digest(official, building_values, building_mask)},
            "source_reports": {"poi_p90_mx4": {"path": str(poi_report_path), "sha256": sha256_file(poi_report_path)},
                               "building_p90_mx4": {"path": str(building_report_path), "sha256": sha256_file(building_report_path)}},
            "registry_sha256_before": registry_before, "registry_sha256_after": registry_after,
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "columns": table.column_names, "deterministic_bytes": True},
        },
        "interpretation": "Cross-layer percentile discordance passes coverage, bounds and p95 effect gates; retain only as an unregistered sensitivity, with no frozen-feature or registry changes." if acceptance["distinct_cross_layer_screen_pass"] else
        "One or more coverage, bounds or p95 effect gates failed; retain only the separate source diagnostics.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm5-poi-building-version-discordance.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "tm5-poi-building-version-discordance/version-discordance.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "measure_after": {"p95_absolute_cross_layer_percentile_difference": report["measure_after"]["p95_absolute_cross_layer_percentile_difference"],
                                        "regions": {r: report["measure_after"]["regions"][r]["absolute_cross_layer_percentile_difference"]["p95"] for r in REGIONS}},
                      "output_artifact": report["provenance"]["output_artifact"], "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
