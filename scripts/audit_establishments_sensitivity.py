#!/usr/bin/env python3
"""Evaluate a no-CBP-data v2 establishments-half sensitivity on provided features.

This script writes aggregate sensitivity diagnostics, not a scored candidate.
It reads official sample GEOIDs, Overture all-POI counts, population, and the
existing v0 estimate output; it never reads CBP or other removed reference data.
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

from estimator_v2_establishments import (
    combine_poi_halves_v2,
    estimate_establishments_component_v2,
    facility_half_v2,
)

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
DEFAULT_ANALYSIS_CBP_K = 0.024
SENSITIVITY_KS = (0.015, 0.024, 0.04)


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


def finite_float(value: Any, field: str, geoid: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field} for official GEOID {geoid}") from exc
    if not math.isfinite(result):
        raise ValueError(f"nonfinite {field} for official GEOID {geoid}")
    return result


def composite_from_v0_parts(base: dict[str, str], poi_all: float,
                            population: float, cbp_k: float) -> dict[str, Any]:
    facility = facility_half_v2(
        (finite_float(base[f"{name}_gap"], f"{name}_gap", base["GEOID"]),
         int(base[f"{name}_defined"]))
        for name in ("fire", "ems", "schools")
    )
    establishments = estimate_establishments_component_v2(poi_all, population, cbp_k)
    poi = combine_poi_halves_v2(
        float(facility["facility_gap"]), int(facility["facility_defined"]),
        float(establishments["establishments_gap"]),
        int(establishments["establishments_defined"]),
    )
    components = []
    for name in ("transport", "building"):
        if int(base[f"{name}_defined"]):
            components.append(finite_float(base[f"{name}_gap"], f"{name}_gap", base["GEOID"]))
    if int(poi["poi_defined"]):
        components.append(float(poi["poi_gap"]))
    score = sum(components) / len(components) if components else None
    return {
        **establishments,
        **facility,
        **poi,
        "coverage_gap_score": score,
    }


def moment_summary(scores: list[float]) -> dict[str, Any]:
    if not scores:
        raise ValueError("cannot summarize empty composite score set")
    values = np.asarray(scores, dtype=np.float64)
    return {
        "defined_composite_tracts": int(len(values)),
        "composite_mean": float(np.mean(values)),
        "composite_E_p_squared": float(np.mean(np.square(values))),
    }


def summarize_group(
    records: list[dict[str, Any]], base_by_geoid: dict[str, dict[str, str]], cbp_k: float
) -> dict[str, Any]:
    establishment_gaps: list[float] = []
    positive_count = 0
    composite_scores_by_geoid: dict[str, float] = {}
    baseline_scores_by_geoid: dict[str, float] = {}
    baseline_cbp_gaps: list[float] = []
    baseline_cbp_zero_count = 0
    for row in records:
        base = base_by_geoid[row["GEOID"]]
        estimate = composite_from_v0_parts(base, row["overture_poi_all"], row["pop_total"], cbp_k)
        if int(estimate["establishments_defined"]):
            gap = float(estimate["establishments_gap"])
            establishment_gaps.append(gap)
            positive_count += int(gap > 0)
        if estimate["coverage_gap_score"] is not None:
            composite_scores_by_geoid[row["GEOID"]] = float(estimate["coverage_gap_score"])
        raw_baseline = base.get("coverage_gap_score", "")
        if raw_baseline not in ("", None):
            baseline_scores_by_geoid[row["GEOID"]] = finite_float(
                raw_baseline, "v0 coverage_gap_score", row["GEOID"])
        if int(base["cbp_defined"]):
            baseline_gap = finite_float(base["cbp_gap"], "v0 cbp_gap", row["GEOID"])
            baseline_cbp_gaps.append(baseline_gap)
            baseline_cbp_zero_count += int(baseline_gap == 0.0)
    if set(composite_scores_by_geoid) != set(baseline_scores_by_geoid):
        raise ValueError("v0 and sensitivity composite defined GEOIDs differ; comparison is not paired")
    n_all = len(records)
    n_est_defined = len(establishment_gaps)
    sensitivity_moments = moment_summary(list(composite_scores_by_geoid.values()))
    baseline_moments = moment_summary(list(baseline_scores_by_geoid.values()))
    return {
        "official_tracts": n_all,
        "establishments_defined_tracts": n_est_defined,
        "establishments_gap_positive_tracts": positive_count,
        "establishments_gap_positive_share_all_official": positive_count / n_all,
        "establishments_gap_positive_share_defined": (positive_count / n_est_defined
                                                      if n_est_defined else None),
        "establishments_gap_mean_defined_only": (float(np.mean(establishment_gaps))
                                                 if establishment_gaps else None),
        "establishments_gap_zero_share_defined": (
            sum(value == 0 for value in establishment_gaps) / n_est_defined
            if n_est_defined else None
        ),
        "zero_population_tracts": sum(row["pop_total"] == 0 for row in records),
        "zero_population_positive_poi_tracts": sum(
            row["pop_total"] == 0 and row["overture_poi_all"] > 0 for row in records
        ),
        "zero_population_zero_poi_undefined_tracts": sum(
            row["pop_total"] == 0 and row["overture_poi_all"] == 0 for row in records
        ),
        "sensitivity_composite": sensitivity_moments,
        "v0_composite": baseline_moments,
        "composite_mean_shift_vs_v0": (
            sensitivity_moments["composite_mean"] - baseline_moments["composite_mean"]
        ),
        "composite_E_p_squared_shift_vs_v0": (
            sensitivity_moments["composite_E_p_squared"] - baseline_moments["composite_E_p_squared"]
        ),
        "v0_establishments_component": {
            "defined_tracts": len(baseline_cbp_gaps),
            "gap_mean_defined_only": (float(np.mean(baseline_cbp_gaps))
                                       if baseline_cbp_gaps else None),
            "fraction_zero_among_defined": (
                baseline_cbp_zero_count / len(baseline_cbp_gaps) if baseline_cbp_gaps else None
            ),
        },
    }


def run(workspace: Path, v0_path: Path, out_path: Path, cbp_k: float) -> dict[str, Any]:
    if not math.isfinite(cbp_k) or cbp_k <= 0:
        raise ValueError("--cbp-k must be a finite positive number")
    region_records: dict[str, list[dict[str, Any]]] = {}
    input_manifest: dict[str, Any] = {}
    global_ids: list[str] = []

    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        ids = read_official_ids(sample_path)
        if not ids or len(ids) != len(set(ids)) or set(ids) & set(global_ids):
            raise ValueError(f"empty, duplicate, or cross-region duplicate official IDs: {region}")
        global_ids.extend(ids)
        feature_path = workspace / "gap-estimator" / "features" / f"{region}-overture-tract-summary.parquet"
        feature_table = pq.read_table(feature_path, columns=["GEOID", "overture_poi_all"])
        feature_rows = feature_table.to_pylist()
        if [str(row["GEOID"]) for row in feature_rows] != ids:
            raise ValueError(f"POI feature cache order/IDs do not exactly match {region} official sample")
        strata_path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        strata_table = pq.read_table(strata_path, columns=["GEOID", "pop_total"])
        strata_rows = strata_table.to_pylist()
        strata_by_geoid = {str(row["GEOID"]): row for row in strata_rows}
        if len(strata_by_geoid) != len(strata_rows) or not set(ids).issubset(strata_by_geoid):
            raise ValueError(f"provided population strata do not cover official sample for {region}")
        region_records[region] = []
        for feature in feature_rows:
            geoid = str(feature["GEOID"])
            poi_all = finite_float(feature["overture_poi_all"], "overture_poi_all", geoid)
            population = finite_float(strata_by_geoid[geoid]["pop_total"], "pop_total", geoid)
            if poi_all < 0 or population < 0:
                raise ValueError(f"negative provided count/population for {geoid}")
            region_records[region].append({
                "GEOID": geoid,
                "overture_poi_all": poi_all,
                "pop_total": population,
            })
        input_manifest[region] = {
            "official_sample": {"rows": len(ids), "sha256": sha256_file(sample_path),
                                "score_placeholder_values_read": False},
            "overture_poi_feature_cache": {
                "rows": feature_table.num_rows,
                "sha256": sha256_file(feature_path),
                "read_columns": ["GEOID", "overture_poi_all"],
            },
            "provided_population_strata": {
                "rows": strata_table.num_rows,
                "sha256": sha256_file(strata_path),
                "read_columns": ["GEOID", "pop_total"],
            },
        }
    if len(global_ids) != 9379:
        raise ValueError(f"expected 9,379 official sample GEOIDs, found {len(global_ids)}")

    with v0_path.open("r", newline="", encoding="utf-8-sig") as stream:
        v0_rows = list(csv.DictReader(stream))
    base_by_geoid = {row["GEOID"]: row for row in v0_rows}
    if len(base_by_geoid) != len(v0_rows) or set(base_by_geoid) != set(global_ids):
        raise ValueError("v0 estimate GEOIDs do not exactly match the official 9,379 GEOIDs")
    for geoid, row in base_by_geoid.items():
        row["GEOID"] = geoid

    all_records = [record for region in REGIONS for record in region_records[region]]
    k_values = sorted(set(SENSITIVITY_KS) | {float(cbp_k)})
    global_results: dict[str, Any] = {}
    regional_results: dict[str, Any] = {}
    for k in k_values:
        label = f"{k:.12g}"
        global_results[label] = summarize_group(all_records, base_by_geoid, k)
        regional_results[label] = {
            region: summarize_group(region_records[region], base_by_geoid, k)
            for region in REGIONS
        }
    baseline_metrics = moment_summary([
        finite_float(row["coverage_gap_score"], "v0 coverage_gap_score", geoid)
        for geoid, row in base_by_geoid.items()
        if row.get("coverage_gap_score", "") not in ("", None)
    ])
    report = {
        "analysis": "No-CBP-data v2 establishments-half sensitivity on provided features and the explicit task parameter",
        "model": {
            "formula": "clip(1 - overture_poi_all / (cbp_k * pop_total), 0, 1)",
            "component": "establishments half of the organizer's two-half POI component",
            "facility_half": "mean of defined v0 fire/EMS/schools gaps, held fixed for this isolated sensitivity",
            "poi_composition": "equal mean of the defined facility half and establishments half; one defined half stands alone",
            "zero_population_rule": "if pop_total == 0 and overture_poi_all > 0, establishments gap is 0 and defined; if both are zero, it is undefined with numeric placeholder 0",
            "cbp_k_analysis_default": {
                "value": DEFAULT_ANALYSIS_CBP_K,
                "derivation": "the selected task's planning note supplies the approximate national-rate anchor 8,000,000 / 330,000,000 = 0.02424, rounded to 0.024; this is a sensitivity-analysis parameter, not a measured challenge feature or a fitted target",
                "raw_cbp_data_read": False,
                "eligible_for_scored_candidate_without_new_approval": False,
            },
            "cbp_k_selected_by_flag": float(cbp_k),
            "analysis_only": True,
            "sensitivity_k_values": k_values,
            "scored_candidate_created": False,
        },
        "v0_baseline_global": {
            **baseline_metrics,
            "v0_establishments_component": global_results[f"{DEFAULT_ANALYSIS_CBP_K:.12g}"]["v0_establishments_component"],
            "source_path": str(v0_path.relative_to(ROOT)),
            "source_sha256": sha256_file(v0_path),
        },
        "global_sensitivity": global_results,
        "regional_sensitivity": regional_results,
        "inputs": input_manifest,
        "restrictions": {
            "sensitivity_uses_challenge_features_v0_model_outputs_and_explicit_task_k_values_only": True,
            "removed_reference_layers_or_equivalents_read": False,
            "cbp_tables_or_data_read": False,
            "external_datasets_or_numeric_files_read": False,
            "sample_score_placeholders_used_as_labels": False,
            "per_geoid_output_written": False,
            "scored_candidate_files_modified": False,
            "leaderboard_probe_or_calibration_performed": False,
            "note": "The three k values are model sensitivity assumptions specified in the task; the .024 analysis anchor is not an approved coefficient for any scored file. Future scored integration must satisfy the operator amendment using only permitted feature-derived/public-method rationale and must not consume CBP data.",
        },
        "limitations": [
            "There are no labels; the composite moments are model outputs and not estimates of target accuracy or leaderboard performance.",
            "Population scaling is a simple model prior and does not represent industry mix, daytime population, establishment size, or nonresidential activity.",
            "The zero-population edge rule is explicit but may not represent rural industrial/commercial establishments.",
            "Facility and transport/building components are held at v0; this is an isolated establishments-half sensitivity, not an assembled estimator v2 candidate.",
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
    parser.add_argument("--cbp-k", type=float, default=DEFAULT_ANALYSIS_CBP_K,
                        help="analysis-only establishments/population rate (default: 0.024); no scored candidate is emitted")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/establishments-sensitivity.json")
    args = parser.parse_args()
    report = run(args.workspace.resolve(), args.v0_estimates.resolve(), args.out.resolve(), args.cbp_k)
    print(f"Baseline composite mean={report['v0_baseline_global']['composite_mean']:.6f}; "
          f"E[p^2]={report['v0_baseline_global']['composite_E_p_squared']:.6f}")
    for k, stats in report["global_sensitivity"].items():
        print(f"k={k}: mean={stats['sensitivity_composite']['composite_mean']:.6f} "
              f"(shift {stats['composite_mean_shift_vs_v0']:+.6f}), "
              f"E[p^2]={stats['sensitivity_composite']['composite_E_p_squared']:.6f} "
              f"(shift {stats['composite_E_p_squared_shift_vs_v0']:+.6f}), "
              f"positive establishments gaps={stats['establishments_gap_positive_share_all_official']:.3%}")
    print(f"Report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
