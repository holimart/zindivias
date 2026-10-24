#!/usr/bin/env python3
"""Build a preregistered Overture-source prior for the building component.

This is an analysis-only feature screen. It reads official sample GEOIDs,
selected provided Overture building count/source-share columns from the
workspace feature cache, and provided ALAND/pop_total context. The source
shares originate from Overture bbox/sources fields; this script does not read
raw geometry, reference footprint data, scores, or labels.
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
CONFIG_PATH = ROOT / "config/building-source-prior-preregistered.json"
with CONFIG_PATH.open("r", encoding="utf-8") as stream:
    CONFIG = json.load(stream)
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
SOURCE_FIELD_MAP = {
    "microsoft_only": "overture_building_ms_only_source_share",
    "microsoft_osm": "overture_building_ms_osm_source_share",
    "osm_only": "overture_building_osm_only_source_share",
    "esri_only": "overture_building_esri_only_source_share",
    "other_or_mixed": "overture_building_other_or_mixed_source_share",
    "no_dataset": "overture_building_no_dataset_source_share",
}
MEMBERSHIP_FIELD_MAP = {
    "microsoft_any": "overture_building_microsoft_source_share",
    "osm_any": "overture_building_osm_source_share",
    "esri_any": "overture_building_esri_source_share",
    "other_dataset_any": "overture_building_other_source_share",
}
BETAS = tuple(float(value) for value in CONFIG["component_rule"]["sensitivity_betas"])
PROFILES = CONFIG["source_mixture_profiles"]


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
        reader = csv.DictReader(stream)
        if "GEOID" not in (reader.fieldnames or []):
            raise ValueError(f"official sample has no GEOID column: {path}")
        ids = [str(row["GEOID"]) for row in reader]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"empty or duplicate official sample IDs: {path}")
    return ids


def clip01(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("source-prior inputs must be finite")
    return min(1.0, max(0.0, float(value)))


def population_context(population: float, land_m2: float) -> float:
    if not math.isfinite(population) or population < 0 or not math.isfinite(land_m2) or land_m2 < 0:
        raise ValueError("ALAND and pop_total must be finite and nonnegative")
    context = CONFIG["context"]
    land_km2 = max(land_m2 / 1_000_000.0, float(context["land_area_floor_km2"]))
    scale = float(context["population_density_scale_per_km2"])
    return clip01(math.log1p(population / land_km2) / math.log1p(scale))


def source_support(shares: dict[str, float], profile: dict[str, float]) -> float:
    if set(shares) != set(CONFIG["source_class_order"]) or set(profile) != set(shares):
        raise ValueError("source share/profile class sets must match preregistered class order")
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in shares.values()):
        raise ValueError("source class shares must be finite and within [0,1]")
    if not math.isclose(sum(shares.values()), 1.0, abs_tol=1e-6):
        raise ValueError("exclusive source class shares must partition assigned buildings")
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in profile.values()):
        raise ValueError("source-mixture coefficients must be within [0,1]")
    return clip01(sum(float(profile[name]) * shares[name] for name in shares))


def total_support(source_value: float, context_value: float) -> float:
    weights = CONFIG["context"]
    source_weight = float(weights["source_weight"])
    context_weight = float(weights["context_weight"])
    if not math.isclose(source_weight + context_weight, 1.0, abs_tol=1e-12):
        raise ValueError("source/context weights must sum to one")
    return clip01(source_weight * clip01(source_value) + context_weight * clip01(context_value))


def component_gap(support: float, beta: float, defined: bool) -> float:
    if not defined:
        return 0.0
    base = float(CONFIG["component_rule"]["baseline_gap_value"])
    lower, upper = (float(value) for value in CONFIG["component_rule"]["clip_range"])
    return min(upper, max(lower, base + beta * (1.0 - clip01(support))))


def summary(values: list[float] | np.ndarray) -> dict[str, float | int | None]:
    array = np.asarray(values, dtype=np.float64)
    array = array[np.isfinite(array)]
    if not len(array):
        return {"n": 0, "mean": None, "std": None, "min": None, "q10": None,
                "median": None, "q90": None, "max": None}
    q10, med, q90 = np.quantile(array, [0.1, 0.5, 0.9])
    return {"n": int(len(array)), "mean": float(array.mean()), "std": float(array.std()),
            "min": float(array.min()), "q10": float(q10), "median": float(med),
            "q90": float(q90), "max": float(array.max())}


def component_metrics(values: np.ndarray, baseline: np.ndarray, defined: np.ndarray) -> dict[str, Any]:
    lower, upper = (float(value) for value in CONFIG["component_rule"]["clip_range"])
    changed = np.abs(values - baseline) > 1e-12
    return {
        "all_official_tracts": summary(values),
        "defined_tracts_only": summary(values[defined]),
        "changed_vs_constant_baseline_tracts": int(changed.sum()),
        "changed_vs_constant_baseline_share_all": float(changed.mean()),
        "changed_vs_constant_baseline_share_defined": float(changed[defined].mean()) if defined.any() else None,
        "nonfinite_values": int((~np.isfinite(values)).sum()),
        "within_preregistered_absolute_component_bound": bool(
            np.isfinite(values).all() and np.all((values >= lower) & (values <= upper))),
    }


def finite_feature(row: dict[str, Any], column: str) -> float | None:
    value = row[column]
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"nonfinite source feature {column}")
    return number


def weighted_source_shares(counts: np.ndarray, rows: list[dict[str, float]]) -> dict[str, float | None]:
    total = int(counts.sum())
    if not total:
        return {name: None for name in rows[0]} if rows else {}
    return {name: float(sum(int(count) * row[name] for count, row in zip(counts, rows)) / total)
            for name in rows[0]}


def run(workspace: Path, candidate_path: Path, report_path: Path) -> dict[str, Any]:
    all_records: list[dict[str, Any]] = []
    per_region: dict[str, Any] = {}
    all_ids: list[str] = []
    input_hashes: dict[str, Any] = {}
    baseline_gap = float(CONFIG["component_rule"]["baseline_gap_value"])
    expected_columns = ["GEOID", "overture_buildings", *SOURCE_FIELD_MAP.values(), *MEMBERSHIP_FIELD_MAP.values()]

    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        sample_ids = official_ids(sample_path)
        if set(all_ids).intersection(sample_ids):
            raise ValueError("duplicate GEOID across official regional samples")
        all_ids.extend(sample_ids)

        feature_path = workspace / "gap-estimator" / "features" / f"{region}-overture-tract-summary.parquet"
        if not feature_path.is_file():
            raise FileNotFoundError(f"provided Overture feature cache not found: {feature_path}")
        feature_table = pq.read_table(feature_path, columns=expected_columns)
        feature_rows = feature_table.to_pylist()
        feature_ids = [str(row["GEOID"]) for row in feature_rows]
        if feature_ids != sample_ids:
            raise ValueError(f"feature cache GEOIDs/order do not exactly match official sample in {region}")

        strata_path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        strata_table = pq.read_table(strata_path, columns=["GEOID", "ALAND", "pop_total"])
        strata_rows = strata_table.to_pylist()
        strata_by_id = {str(row["GEOID"]): row for row in strata_rows}
        if len(strata_by_id) != len(strata_rows) or not set(sample_ids).issubset(strata_by_id):
            raise ValueError(f"allowed ALAND/pop_total context does not cover official sample for {region}")

        counts = np.asarray([int(row["overture_buildings"]) for row in feature_rows], dtype=np.int64)
        if np.any(counts < 0):
            raise ValueError("provided Overture building counts cannot be negative")
        defined = counts > 0
        profile_supports = {profile_name: np.zeros(len(sample_ids), dtype=np.float64)
                            for profile_name in PROFILES}
        context_scores = np.zeros(len(sample_ids), dtype=np.float64)
        class_share_rows: list[dict[str, float]] = []
        membership_rows: list[dict[str, float]] = []
        source_mix_defined = np.zeros(len(sample_ids), dtype=bool)
        records: list[dict[str, Any]] = []

        for index, (geoid, feature) in enumerate(zip(sample_ids, feature_rows)):
            context_row = strata_by_id[geoid]
            context = population_context(float(context_row["pop_total"]), float(context_row["ALAND"]))
            context_scores[index] = context
            class_values: dict[str, float] = {}
            membership_values: dict[str, float] = {}
            if defined[index]:
                for class_name, column in SOURCE_FIELD_MAP.items():
                    value = finite_feature(feature, column)
                    if value is None:
                        raise ValueError(f"source mix missing for building-defined tract {region}:{geoid}:{column}")
                    class_values[class_name] = value
                source_mix_defined[index] = True
                for membership, column in MEMBERSHIP_FIELD_MAP.items():
                    value = finite_feature(feature, column)
                    if value is None or not 0 <= value <= 1:
                        raise ValueError(f"invalid Overture source membership share {region}:{geoid}:{column}")
                    membership_values[membership] = value
                if any(not 0 <= value <= 1 for value in class_values.values()):
                    raise ValueError(f"exclusive source class share outside [0,1]: {region}:{geoid}")
                if not math.isclose(sum(class_values.values()), 1.0, abs_tol=1e-6):
                    raise ValueError(f"exclusive source classes fail to partition buildings: {region}:{geoid}")
                class_share_rows.append(class_values)
                membership_rows.append(membership_values)
                for profile_name, profile in PROFILES.items():
                    raw_source_support = source_support(class_values, profile)
                    profile_supports[profile_name][index] = total_support(raw_source_support, context)
            else:
                class_values = {name: 0.0 for name in SOURCE_FIELD_MAP}
                membership_values = {name: 0.0 for name in MEMBERSHIP_FIELD_MAP}
                class_share_rows.append(class_values)
                membership_rows.append(membership_values)
                for profile_name in PROFILES:
                    profile_supports[profile_name][index] = total_support(0.0, context)

            baseline = baseline_gap if defined[index] else 0.0
            record: dict[str, Any] = {
                "GEOID": geoid,
                "region": region,
                "building_defined": int(defined[index]),
                "overture_building_count": int(counts[index]),
                "source_mix_defined": int(source_mix_defined[index]),
                "population_context_support": context,
                "building_gap_constant_0_01": baseline,
            }
            for class_name in SOURCE_FIELD_MAP:
                record[f"source_class_{class_name}_share"] = class_values[class_name]
            for membership in MEMBERSHIP_FIELD_MAP:
                record[f"source_membership_{membership}_share"] = membership_values[membership]
            for profile_name in PROFILES:
                record[f"source_prior_support_{profile_name}"] = profile_supports[profile_name][index]
                for beta in BETAS:
                    record[f"building_gap_{profile_name}_beta_{beta:.2f}"] = component_gap(
                        profile_supports[profile_name][index], beta, bool(defined[index]))
            records.append(record)
            all_records.append(record)

        baseline_values = np.where(defined, baseline_gap, 0.0)
        region_sensitivity: dict[str, Any] = {}
        for profile_name in PROFILES:
            region_sensitivity[profile_name] = {}
            for beta in BETAS:
                values = np.asarray([row[f"building_gap_{profile_name}_beta_{beta:.2f}"] for row in records], dtype=np.float64)
                region_sensitivity[profile_name][f"{beta:.2f}"] = component_metrics(values, baseline_values, defined)

        assigned_building_count = int(counts.sum())
        weighted_classes = weighted_source_shares(counts, class_share_rows)
        weighted_memberships = weighted_source_shares(counts, membership_rows)
        if int(source_mix_defined.sum()) != int(defined.sum()):
            raise ValueError(f"source-mix coverage does not cover all defined buildings in {region}")
        per_region[region] = {
            "official_tracts": len(sample_ids),
            "building_defined_tracts": int(defined.sum()),
            "zero_overture_building_tracts": int((~defined).sum()),
            "assigned_overture_buildings": assigned_building_count,
            "source_mix_feature_coverage": {
                "source_mix_defined_tracts": int(source_mix_defined.sum()),
                "share_of_building_defined_tracts_with_all_six_exclusive_classes": (
                    float(source_mix_defined.sum() / defined.sum()) if defined.any() else None),
                "exclusive_classes_partition_share_per_defined_tract": True,
                "membership_shares_are_overlapping": True,
            },
            "building_weighted_exclusive_source_class_shares": weighted_classes,
            "building_weighted_overlapping_source_membership_shares": weighted_memberships,
            "tract_equal_weight_exclusive_source_class_share_summaries": {
                name: summary([row[name] for index, row in enumerate(class_share_rows)
                              if source_mix_defined[index]])
                for name in SOURCE_FIELD_MAP
            },
            "population_context_support_defined_tracts": summary(context_scores[defined]),
            "baseline_constant_0_01": summary(baseline_values),
            "sensitivity_by_fixed_source_profile_and_beta": region_sensitivity,
            "feature_cache": {
                "path": str(feature_path),
                "rows": feature_table.num_rows,
                "sha256": sha256_file(feature_path),
                "read_columns": expected_columns,
                "source_features_derived_from": ["provided Overture bbox", "provided Overture sources"],
            },
            "allowed_context_input": {
                "path": str(strata_path),
                "rows": strata_table.num_rows,
                "sha256": sha256_file(strata_path),
                "read_columns": ["GEOID", "ALAND", "pop_total"],
                "extra_strata_rows_excluded": len(strata_rows) - len(sample_ids),
            },
            "official_sample": {
                "path": str(sample_path),
                "rows": len(sample_ids),
                "sha256": sha256_file(sample_path),
                "read_column": "GEOID only; score placeholders ignored",
            },
        }
        input_hashes[region] = {
            "feature_cache_sha256": per_region[region]["feature_cache"]["sha256"],
            "strata_sha256": per_region[region]["allowed_context_input"]["sha256"],
            "sample_sha256": per_region[region]["official_sample"]["sha256"],
        }

    if len(all_ids) != 9379 or len(set(all_ids)) != 9379:
        raise ValueError(f"expected 9,379 unique official IDs, got {len(all_ids)}")
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_columns = list(all_records[0])
    with candidate_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=candidate_columns, lineterminator="\n")
        writer.writeheader()
        for record in all_records:
            writer.writerow({name: format(value, ".12g") if isinstance(value, float) else value
                             for name, value in record.items()})

    global_defined = np.asarray([bool(row["building_defined"]) for row in all_records], dtype=bool)
    global_baseline = np.asarray([row["building_gap_constant_0_01"] for row in all_records], dtype=np.float64)
    global_sensitivity: dict[str, Any] = {}
    for profile_name in PROFILES:
        global_sensitivity[profile_name] = {}
        for beta in BETAS:
            values = np.asarray([row[f"building_gap_{profile_name}_beta_{beta:.2f}"] for row in all_records], dtype=np.float64)
            global_sensitivity[profile_name][f"{beta:.2f}"] = component_metrics(values, global_baseline, global_defined)

    report = {
        "analysis": "Provided-feature-only Overture building-source prior sensitivity; diagnostic candidate, not a submission",
        "candidate_id": CONFIG["candidate_id"],
        "candidate_csv": str(candidate_path.relative_to(ROOT)),
        "candidate_csv_sha256": sha256_file(candidate_path),
        "candidate_rows": len(all_records),
        "candidate_columns": candidate_columns,
        "preregistration": {
            "config": str(CONFIG_PATH.relative_to(ROOT)),
            "config_sha256": sha256_file(CONFIG_PATH),
            "commit": "55bcacbe30f93581f1a7b3aba949fb31c91b064d",
            "status": CONFIG["status"],
        },
        "code_commit_at_run": git_commit(),
        "script_sha256_at_run": sha256_file(Path(__file__)),
        "environment": {"python": platform.python_version(), "numpy": np.__version__},
        "scope": {
            "official_regions": list(REGIONS),
            "official_tracts": len(all_records),
            "ordered_official_sample_ids": True,
            "template_only_rows_included": False,
        },
        "source_features": {
            "exclusive_share_columns": list(SOURCE_FIELD_MAP.values()),
            "overlapping_membership_columns": list(MEMBERSHIP_FIELD_MAP.values()),
            "exclusive_class_order": CONFIG["source_class_order"],
            "source_class_semantics": CONFIG["source_class_semantics"],
            "share_provenance": "Existing workspace feature cache; derived from provided Overture bbox centers assigned to provided ACS GEOID/geometry and the provided Overture sources[].dataset field. No Overture building geometry or any reference footprint was read by this screen.",
            "existing_source_audit": {
                "report": "reports/source-provenance.json",
                "sha256": sha256_file(ROOT / "reports/source-provenance.json"),
            },
        },
        "method": CONFIG,
        "input_hashes": input_hashes,
        "per_region": per_region,
        "global_sensitivity_by_profile_and_beta": global_sensitivity,
        "restrictions": {
            "sample_score_placeholders_or_target_labels_read": False,
            "reference_or_external_footprint_counts_or_geometry_read": False,
            "MS_USFS_CarbonPlan_per_tract_building_counts_read_or_used": False,
            "ACS_housing_units_read_or_used": False,
            "leaderboard_or_score_feedback_used": False,
            "external_data_used": [],
            "upload_or_submission_made": False,
            "candidate_accuracy_claim": False,
            "all_source_mixture_coefficients_preregistered_before_candidate_scan": True,
        },
        "limitations": [
            "Source labels indicate provenance metadata only; they do not establish geometry equality, footprint completeness, reference vintage, or matching with the hidden Microsoft reference.",
            "The six exclusive source classes partition building records, but the separately reported Microsoft/OSM/Esri/other memberships overlap and must not be summed.",
            "The source and context weights are fixed qualitative assumptions, not fitted rates or measured target gaps. This analysis has no labels or local accuracy metric.",
            "A positive Overture count is used only for the already-documented component definedness convention; the two zero-count tracts retain undefined status and numeric placeholder zero.",
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
                           encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, default=ROOT / "reports/candidates/building_source_prior.csv")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/building-source-prior.json")
    args = parser.parse_args()
    report = run(args.workspace.resolve(), args.candidate.resolve(), args.out.resolve())
    primary = report["global_sensitivity_by_profile_and_beta"][CONFIG["component_rule"]["primary_profile"]][f"{CONFIG['component_rule']['primary_beta']:.2f}"]
    print(f"PASS source-prior screen: {report['candidate_rows']} official tracts; "
          f"primary changed {primary['changed_vs_constant_baseline_tracts']} rows")
    print(f"Candidate SHA-256: {report['candidate_csv_sha256']}")
    print(f"Report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
