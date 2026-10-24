#!/usr/bin/env python3
"""Build an analysis-only cross-theme support adjustment candidate.

The builder reads only official GEOIDs, the frozen C2 predicted coverage score,
and selected provided Overture building/road/place feature-cache columns. It
never reads sample-score placeholders, labels, removed references, or external
layers. The adjustment is preregistered and is not an accuracy claim or upload.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/cross-theme-support-preregistered.json"
with CONFIG_PATH.open("r", encoding="utf-8") as stream:
    CONFIG = json.load(stream)
REGIONS = tuple(CONFIG["scope"]["regions"])
FEATURE_COLUMNS = [
    "GEOID",
    "overture_buildings",
    "overture_building_microsoft_source_share",
    "overture_named_road_km_5070",
    "overture_route_km",
    "overture_poi_all",
    "overture_poi_no_upstream_source_share",
]
SUPPORT_NAMES = tuple(CONFIG["cross_theme_adjustment"]["support_order"])
MISSING_FILL = float(CONFIG["support_features"]["building_source_support"]["missing_fill"])
SCORE_COLUMN = "coverage_gap_score"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def official_ids(path: Path) -> list[str]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if not header or "GEOID" not in header:
            raise ValueError(f"official sample lacks GEOID: {path}")
        geoid_index = header.index("GEOID")
        ids = [row[geoid_index] for row in reader]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"empty or duplicate official sample IDs: {path}")
    return ids


def clip01(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("support and score values must be finite")
    return min(1.0, max(0.0, float(value)))


def optional_nonnegative(value: Any, label: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return number


def optional_unit(value: Any, label: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError(f"{label} must be finite and in [0,1]")
    return number


def support_features(feature: dict[str, Any]) -> tuple[dict[str, float], dict[str, int]]:
    building_count = optional_nonnegative(feature["overture_buildings"], "overture_buildings")
    building_share = optional_unit(feature["overture_building_microsoft_source_share"],
                                   "overture_building_microsoft_source_share")
    if building_count is None:
        building_count = 0.0
    building_value = building_share if building_count > 0 and building_share is not None else None

    named_km = optional_nonnegative(feature["overture_named_road_km_5070"],
                                    "overture_named_road_km_5070")
    route_km = optional_nonnegative(feature["overture_route_km"], "overture_route_km")
    if named_km is None or route_km is None or named_km + route_km <= 0:
        road_value = None
    else:
        road_value = clip01(route_km / (named_km + route_km))

    poi_count = optional_nonnegative(feature["overture_poi_all"], "overture_poi_all")
    poi_missing_share = optional_unit(feature["overture_poi_no_upstream_source_share"],
                                      "overture_poi_no_upstream_source_share")
    if poi_count is None:
        poi_count = 0.0
    place_value = (1.0 - poi_missing_share
                   if poi_count > 0 and poi_missing_share is not None else None)

    raw = {
        "building_source_support": building_value,
        "road_route_support": road_value,
        "place_upstream_source_support": place_value,
    }
    missing = {name: int(value is None) for name, value in raw.items()}
    filled = {name: clip01(MISSING_FILL if value is None else value)
              for name, value in raw.items()}
    return filled, missing


def cross_theme_support(supports: dict[str, float]) -> tuple[float, float, float]:
    if set(supports) != set(SUPPORT_NAMES):
        raise ValueError("support feature set differs from preregistration")
    values = np.asarray([clip01(supports[name]) for name in SUPPORT_NAMES], dtype=np.float64)
    mean_support = float(values.mean())
    weakest_support = float(values.min())
    cross = clip01(0.5 * mean_support + 0.5 * weakest_support)
    return mean_support, weakest_support, cross


def adjusted_score(prior_score: float, cross_support: float) -> tuple[float, float]:
    prior = clip01(prior_score)
    support = clip01(cross_support)
    scale = float(CONFIG["cross_theme_adjustment"]["adjustment_scale"])
    adjustment = scale * (1.0 - support)
    score = clip01(prior + adjustment)
    if adjustment < -1e-12 or adjustment > float(CONFIG["cross_theme_adjustment"]["absolute_adjustment_bound"]) + 1e-12:
        raise ValueError("adjustment outside preregistered absolute bound")
    return adjustment, score


def summary(values: list[float] | np.ndarray) -> dict[str, float | int | None]:
    arr = np.asarray(values, dtype=np.float64)
    if not len(arr):
        return {"n": 0, "mean": None, "variance_population": None, "std_population": None,
                "min": None, "q10": None, "median": None, "q90": None, "max": None}
    if not np.isfinite(arr).all():
        raise ValueError("summary encountered nonfinite values")
    q10, median, q90 = np.quantile(arr, [0.1, 0.5, 0.9])
    return {"n": int(len(arr)), "mean": float(arr.mean()),
            "variance_population": float(arr.var()), "std_population": float(arr.std()),
            "min": float(arr.min()), "q10": float(q10), "median": float(median),
            "q90": float(q90), "max": float(arr.max())}


def average_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    sorted_values = values[order]
    start = 0
    while start < len(values):
        stop = start + 1
        while stop < len(values) and sorted_values[stop] == sorted_values[start]:
            stop += 1
        ranks[order[start:stop]] = (start + 1 + stop) / 2.0
        start = stop
    return ranks


def correlation(x: list[float], y: list[float]) -> dict[str, Any]:
    if len(x) != len(y):
        raise ValueError("paired support vectors must have equal length")
    if len(x) < 2:
        return {"n": len(x), "pearson": None, "spearman": None,
                "reason": "fewer than two complete observations"}
    xa, ya = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    if not np.isfinite(xa).all() or not np.isfinite(ya).all():
        raise ValueError("correlation received nonfinite values")
    if np.ptp(xa) == 0 or np.ptp(ya) == 0:
        return {"n": len(x), "pearson": None, "spearman": None,
                "reason": "constant input"}
    pearson = float(np.corrcoef(xa, ya)[0, 1])
    rx, ry = average_ranks(xa), average_ranks(ya)
    spearman = float(np.corrcoef(rx, ry)[0, 1])
    return {"n": len(x), "pearson": pearson, "spearman": spearman}


def pairwise_correlations(records: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for i, name_x in enumerate(SUPPORT_NAMES):
        for name_y in SUPPORT_NAMES[i + 1:]:
            pairs = [(row["raw_support"][name_x], row["raw_support"][name_y])
                     for row in records
                     if row["raw_support"][name_x] is not None and
                     row["raw_support"][name_y] is not None]
            output[f"{name_x}__{name_y}"] = correlation(
                [float(pair[0]) for pair in pairs], [float(pair[1]) for pair in pairs])
    return output


def moment_payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    result = {"frozen_prior_coverage_gap_score": summary([row["prior_score"] for row in records]),
              "support_feature_moments_filled_for_adjustment": {},
              "support_feature_moments_observed_only": {},
              "missingness": {}}
    for name in SUPPORT_NAMES:
        result["support_feature_moments_filled_for_adjustment"][name] = summary(
            [row["supports"][name] for row in records])
        observed = [row["raw_support"][name] for row in records if row["raw_support"][name] is not None]
        result["support_feature_moments_observed_only"][name] = summary(observed)
        result["missingness"][name] = {
            "missing_rows": int(sum(row["missing"][name] for row in records)),
            "available_rows": int(len(records) - sum(row["missing"][name] for row in records)),
            "missing_share": float(sum(row["missing"][name] for row in records) / len(records)) if records else None,
        }
    return result


def read_prior(path: Path) -> dict[str, float]:
    if sha256_file(path) != CONFIG["prior_candidate"]["sha256"]:
        raise ValueError("frozen prior candidate SHA-256 differs from preregistered C2 hash")
    scores: dict[str, float] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if not header or "GEOID" not in header or SCORE_COLUMN not in header:
            raise ValueError("prior C2 artifact lacks GEOID/coverage_gap_score")
        geo_index, score_index = header.index("GEOID"), header.index(SCORE_COLUMN)
        for row in reader:
            geoid = row[geo_index]
            score = float(row[score_index])
            if geoid in scores or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("prior C2 contains duplicate GEOID or invalid prediction")
            scores[geoid] = score
    if len(scores) != 9794:
        raise ValueError(f"frozen prior candidate row count changed: {len(scores)}")
    return scores


def canonical_projection_hash(ordered_ids: list[str], scores: dict[str, float]) -> str:
    digest = hashlib.sha256()
    digest.update(b"GEOID,coverage_gap_score\n")
    for geoid in ordered_ids:
        digest.update(f"{geoid},{scores[geoid]:.12g}\n".encode("utf-8"))
    return digest.hexdigest()


def pearson_spearman_all(records: list[dict[str, Any]]) -> dict[str, Any]:
    observed = pairwise_correlations(records)
    return {"pairwise_complete_raw_features": observed,
            "method": "Pearson correlation and Spearman correlation with average ranks for ties; pairwise complete rows only; descriptive, not target fit"}


def run(workspace: Path, candidate_path: Path, manifest_path: Path, report_path: Path) -> dict[str, Any]:
    prior_path = ROOT / CONFIG["prior_candidate"]["path"]
    prior_scores = read_prior(prior_path)
    records: list[dict[str, Any]] = []
    ids_by_region: dict[str, list[str]] = {}
    inputs_by_region: dict[str, Any] = {}
    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        sample_ids = official_ids(sample_path)
        ids_by_region[region] = sample_ids
        feature_path = workspace / "gap-estimator" / "features" / f"{region}-overture-tract-summary.parquet"
        table = pq.read_table(feature_path, columns=FEATURE_COLUMNS)
        feature_rows = table.to_pylist()
        feature_ids = [str(row["GEOID"]) for row in feature_rows]
        if feature_ids != sample_ids:
            raise ValueError(f"feature cache ID order differs from official sample for {region}")
        if not set(sample_ids).issubset(prior_scores):
            raise ValueError(f"frozen prior candidate missing official GEOIDs for {region}")
        for geoid, feature in zip(sample_ids, feature_rows):
            support, missing = support_features(feature)
            mean_support, weakest_support, cross_support = cross_theme_support(support)
            adjustment, adjusted = adjusted_score(prior_scores[geoid], cross_support)
            raw = {
                "building_source_support": optional_unit(feature["overture_building_microsoft_source_share"],
                                                          "overture_building_microsoft_source_share")
                if float(feature["overture_buildings"] or 0) > 0 else None,
                "road_route_support": None,
                "place_upstream_source_support": None,
            }
            named = optional_nonnegative(feature["overture_named_road_km_5070"], "overture_named_road_km_5070")
            route = optional_nonnegative(feature["overture_route_km"], "overture_route_km")
            if named is not None and route is not None and named + route > 0:
                raw["road_route_support"] = clip01(route / (named + route))
            poi_count = float(feature["overture_poi_all"] or 0)
            upstream_missing = optional_unit(feature["overture_poi_no_upstream_source_share"],
                                             "overture_poi_no_upstream_source_share")
            if poi_count > 0 and upstream_missing is not None:
                raw["place_upstream_source_support"] = 1.0 - upstream_missing
            record = {
                "GEOID": geoid,
                "region": region,
                "prior_score": prior_scores[geoid],
                "raw_support": raw,
                "supports": support,
                "missing": missing,
                "mean_support": mean_support,
                "weakest_support": weakest_support,
                "cross_theme_support": cross_support,
                "adjustment": adjustment,
                "adjusted_score": adjusted,
            }
            records.append(record)
        inputs_by_region[region] = {
            "feature_cache_path": str(feature_path.relative_to(workspace)),
            "feature_cache_sha256": sha256_file(feature_path),
            "rows": table.num_rows,
            "read_columns": FEATURE_COLUMNS,
            "official_sample_path": str(sample_path.relative_to(workspace)),
            "official_sample_sha256": sha256_file(sample_path),
            "official_rows": len(sample_ids),
            "feature_rows_match_official_order": True,
        }

    ordered_ids = [geoid for region in REGIONS for geoid in ids_by_region[region]]
    if len(ordered_ids) != 9379 or len(set(ordered_ids)) != 9379:
        raise ValueError(f"expected 9,379 unique official IDs, got {len(ordered_ids)}")
    records_by_id = {row["GEOID"]: row for row in records}
    if set(records_by_id) != set(ordered_ids):
        raise ValueError("candidate records do not exactly cover official GEOIDs")

    candidate_columns = [
        "GEOID", "region", "prior_candidate_id", "prior_coverage_gap_score",
        "building_source_support", "building_source_support_missing",
        "road_route_support", "road_route_support_missing",
        "place_upstream_source_support", "place_upstream_source_support_missing",
        "mean_theme_support", "weakest_theme_support", "cross_theme_support",
        "cross_theme_adjustment", "adjusted_coverage_gap_score",
    ]
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    with candidate_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=candidate_columns, lineterminator="\n")
        writer.writeheader()
        for geoid in ordered_ids:
            row = records_by_id[geoid]
            writer.writerow({
                "GEOID": geoid,
                "region": row["region"],
                "prior_candidate_id": CONFIG["prior_candidate"]["candidate_id"],
                "prior_coverage_gap_score": format(row["prior_score"], ".12g"),
                "building_source_support": format(row["supports"]["building_source_support"], ".12g"),
                "building_source_support_missing": row["missing"]["building_source_support"],
                "road_route_support": format(row["supports"]["road_route_support"], ".12g"),
                "road_route_support_missing": row["missing"]["road_route_support"],
                "place_upstream_source_support": format(row["supports"]["place_upstream_source_support"], ".12g"),
                "place_upstream_source_support_missing": row["missing"]["place_upstream_source_support"],
                "mean_theme_support": format(row["mean_support"], ".12g"),
                "weakest_theme_support": format(row["weakest_support"], ".12g"),
                "cross_theme_support": format(row["cross_theme_support"], ".12g"),
                "cross_theme_adjustment": format(row["adjustment"], ".12g"),
                "adjusted_coverage_gap_score": format(row["adjusted_score"], ".12g"),
            })

    def summarize_region(region_records: list[dict[str, Any]]) -> dict[str, Any]:
        before = np.asarray([row["prior_score"] for row in region_records], dtype=np.float64)
        after = np.asarray([row["adjusted_score"] for row in region_records], dtype=np.float64)
        delta = np.asarray([row["adjustment"] for row in region_records], dtype=np.float64)
        changed = np.abs(after - before) > float(CONFIG["cross_theme_adjustment"]["row_changed_tolerance"])
        result = {
            "rows": len(region_records),
            "feature_moments_and_coverage": moment_payload(region_records),
            "pairwise_support_correlations": pearson_spearman_all(region_records),
            "cross_theme_support": summary([row["cross_theme_support"] for row in region_records]),
            "theme_support_spread_max_minus_min": summary([
                max(row["supports"].values()) - min(row["supports"].values()) for row in region_records]),
            "prior_prediction": summary(before),
            "adjusted_prediction": summary(after),
            "adjustment": summary(delta),
            "prior_prediction_variance": float(before.var()),
            "adjusted_prediction_variance": float(after.var()),
            "adjustment_variance": float(delta.var()),
            "rows_changed": int(changed.sum()),
            "fraction_rows_changed": float(changed.mean()) if len(changed) else None,
            "max_absolute_adjustment": float(np.abs(delta).max()) if len(delta) else 0.0,
        }
        return result

    per_region: dict[str, Any] = {}
    for region in REGIONS:
        region_records = [row for row in records if row["region"] == region]
        per_region[region] = summarize_region(region_records)

    all_before = np.asarray([row["prior_score"] for row in records], dtype=np.float64)
    all_after = np.asarray([row["adjusted_score"] for row in records], dtype=np.float64)
    all_delta = np.asarray([row["adjustment"] for row in records], dtype=np.float64)
    global_changed = np.abs(all_after - all_before) > float(CONFIG["cross_theme_adjustment"]["row_changed_tolerance"])
    candidate_sha = sha256_file(candidate_path)
    prior_projection_sha = canonical_projection_hash(ordered_ids, prior_scores)
    manifest = {
        "candidate_id": CONFIG["candidate_id"],
        "candidate_csv": str(candidate_path.relative_to(ROOT)),
        "candidate_csv_sha256": candidate_sha,
        "candidate_rows": len(records),
        "candidate_columns": candidate_columns,
        "prior_candidate_id": CONFIG["prior_candidate"]["candidate_id"],
        "prior_candidate_sha256": sha256_file(prior_path),
        "prior_official_projection_sha256": prior_projection_sha,
        "config_path": str(CONFIG_PATH.relative_to(ROOT)),
        "config_sha256": sha256_file(CONFIG_PATH),
        "input_hashes_by_region": inputs_by_region,
        "scope": {"regions": list(REGIONS), "official_ids": len(ordered_ids),
                  "ordered_official_ids": True, "template_only_ids_excluded": 415},
        "score_bound": [0.0, 1.0],
        "maximum_absolute_adjustment": float(np.abs(all_delta).max()),
        "row_changed_tolerance": float(CONFIG["cross_theme_adjustment"]["row_changed_tolerance"]),
        "no_upload": True,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
                             encoding="utf-8")
    manifest_sha = sha256_file(manifest_path)

    report = {
        "analysis": "Deterministic Overture cross-theme support adjustment; analysis-only, not a submission",
        "candidate_id": CONFIG["candidate_id"],
        "candidate_csv": str(candidate_path.relative_to(ROOT)),
        "candidate_csv_sha256": candidate_sha,
        "candidate_manifest": str(manifest_path.relative_to(ROOT)),
        "candidate_manifest_sha256": manifest_sha,
        "candidate_rows": len(records),
        "code_commit_at_run": git_commit(),
        "script_sha256_at_run": sha256_file(Path(__file__)),
        "environment": {"python": platform.python_version(), "numpy": np.__version__},
        "preregistration": {
            "config": str(CONFIG_PATH.relative_to(ROOT)),
            "config_sha256": sha256_file(CONFIG_PATH),
            "config_commit": "f70d3877522eb75284f36d82834f11bb0bad1361",
            "registered_before_cross_theme_feature_measurement": True,
        },
        "measure_before": {
            "prior_candidate": CONFIG["prior_candidate"],
            "prior_official_projection_sha256": prior_projection_sha,
            "prior_score_moments_by_region": {
                region: per_region[region]["prior_prediction"] for region in REGIONS
            },
            "frozen_feature_schema": {
                "building": "overture_building_microsoft_source_share (Overture sources-derived membership); missing when no positive Overture building count",
                "road": "overture_route_km and overture_named_road_km_5070 (provided Overture routes/class support summaries)",
                "place": "overture_poi_no_upstream_source_share (Overture sources-derived attribution); missing when no positive Overture place count",
                "v2_context": CONFIG["mechanistic_v2_context"],
                "feature_cache_read_columns": FEATURE_COLUMNS,
            },
            "existing_mechanistic_reports": {
                "transport_route_v2": {"path": "reports/transport-route-ref-audit.json",
                                        "sha256": sha256_file(ROOT / "reports/transport-route-ref-audit.json")},
                "building_source_prior": {"path": "reports/building-source-prior.json",
                                           "sha256": sha256_file(ROOT / "reports/building-source-prior.json")},
                "building_morphology": {"path": "reports/building-morphology-candidate.json",
                                         "sha256": sha256_file(ROOT / "reports/building-morphology-candidate.json")},
                "place_source_provenance": {"path": "reports/source-provenance.json",
                                             "sha256": sha256_file(ROOT / "reports/source-provenance.json")},
                "facility_miss_prior": {"path": "reports/poi-facility-miss-audit.json",
                                         "sha256": sha256_file(ROOT / "reports/poi-facility-miss-audit.json")},
            },
        },
        "method": CONFIG,
        "input_hashes_by_region": inputs_by_region,
        "per_region": per_region,
        "global": {
            "rows": len(records),
            "feature_moments_and_coverage": moment_payload(records),
            "pairwise_support_correlations": pearson_spearman_all(records),
            "cross_theme_support": summary([row["cross_theme_support"] for row in records]),
            "theme_support_spread_max_minus_min": summary([
                max(row["supports"].values()) - min(row["supports"].values()) for row in records]),
            "prior_prediction": summary(all_before),
            "adjusted_prediction": summary(all_after),
            "adjustment": summary(all_delta),
            "prior_prediction_variance": float(all_before.var()),
            "adjusted_prediction_variance": float(all_after.var()),
            "adjustment_variance": float(all_delta.var()),
            "rows_changed": int(global_changed.sum()),
            "fraction_rows_changed": float(global_changed.mean()),
            "max_absolute_adjustment": float(np.abs(all_delta).max()),
            "max_absolute_adjustment_within_preregistered_0_05": bool(
                np.abs(all_delta).max() <= float(CONFIG["cross_theme_adjustment"]["absolute_adjustment_bound"]) + 1e-12),
            "all_support_and_score_rows_finite_bounded_0_1": bool(
                np.isfinite(all_before).all() and np.isfinite(all_after).all() and
                np.all((all_before >= 0) & (all_before <= 1)) and
                np.all((all_after >= 0) & (all_after <= 1)) and
                all(np.isfinite([row["cross_theme_support"], row["adjustment"]]).all() and
                    0 <= row["cross_theme_support"] <= 1 and 0 <= row["adjustment"] <= 0.05
                    for row in records)),
        },
        "restrictions": {
            "official_sample_score_placeholders_read_or_used": False,
            "labels_or_score_feedback_used": False,
            "removed_reference_layers_or_equivalents_read": False,
            "external_layers_or_numeric_data_used": False,
            "upload_or_submission_made": False,
            "accuracy_or_target_improvement_claim": False,
            "v2_component_bundle_assembled_or_promoted": False,
            "source_or_support_features_interpreted_as_truth": False,
        },
        "limitations": [
            "C2 is a frozen historical feature-derived prediction baseline, not a hidden target or fitted correction target; its full artifact hash is retained in the manifest.",
            "Microsoft source membership, Overture route-network tagging, and upstream place attribution are internal support covariates only; none proves reference completeness, geometry correctness, or real-world service availability.",
            "The route support ratio combines two potentially overlapping Overture length summaries solely to bound the indicator; it is not a length-completeness estimate.",
            "A neutral 0.5 fill keeps rows with unavailable support evidence finite; missingness flags are retained and missing support is not evidence of low coverage.",
            "The fixed adjustment is an unvalidated conservative sensitivity and is not a scored-v2 integration or evidence of improved accuracy.",
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
                           encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, default=ROOT / "reports/candidates/cross_theme_support.csv")
    parser.add_argument("--manifest", type=Path, default=ROOT / "reports/candidates/cross_theme_support_manifest.json")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/cross-theme-support.json")
    args = parser.parse_args()
    report = run(args.workspace.resolve(), args.candidate.resolve(), args.manifest.resolve(), args.out.resolve())
    global_result = report["global"]
    print(f"PASS cross-theme support: {report['candidate_rows']} official rows; "
          f"changed {global_result['rows_changed']} ({global_result['fraction_rows_changed']:.4%}); "
          f"max |adjustment|={global_result['max_absolute_adjustment']:.6f}")
    print(f"Candidate SHA-256 {report['candidate_csv_sha256']}; manifest SHA-256 {report['candidate_manifest_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
