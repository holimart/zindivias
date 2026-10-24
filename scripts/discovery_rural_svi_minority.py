#!/usr/bin/env python3
"""Screen a rural × SVI-minority-domain intersection using provided data only.

This analyzes predictions, not organiser labels. It is a reproducible discovery
hypothesis, not an accuracy or causal analysis.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
REGION = "south-central-tx"
BOOTSTRAP_REPLICATES = 50_000
BASE_SEED = 20261024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        return list(reader), list(reader.fieldnames or [])


def bootstrap_difference(high: np.ndarray, comparison: np.ndarray, seed: int) -> list[float]:
    if not len(high) or not len(comparison):
        raise ValueError("bootstrap requires nonempty groups")
    rng = np.random.default_rng(seed)
    high_means = rng.choice(high, (BOOTSTRAP_REPLICATES, len(high)), replace=True).mean(axis=1)
    comparison_means = rng.choice(
        comparison, (BOOTSTRAP_REPLICATES, len(comparison)), replace=True
    ).mean(axis=1)
    quantiles = np.quantile(high_means - comparison_means, [0.025, 0.975])
    return [float(quantiles[0]), float(quantiles[1])]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"])
                        if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--estimates", type=Path, default=ROOT / "reports/gap-estimates.csv")
    parser.add_argument("--sensitivity", type=Path,
                        default=ROOT / "reports/candidates/C3_denominator_x125.csv")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "reports/discovery-rural-svi-minority.json")
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    workspace = args.workspace.resolve()
    sample_path = workspace / "samples" / f"{REGION}-sample-submission.csv"
    strata_path = workspace / "defined-flags-inputs" / f"{REGION}-strata-tract-table.parquet"

    # Use only authoritative GEOIDs from sample files; ignore their placeholder
    # score values. Read only the needed fields from the provided strata table.
    sample_rows, sample_columns = read_csv(sample_path)
    if "GEOID" not in sample_columns:
        raise ValueError("official sample has no GEOID column")
    official_ids = [row["GEOID"] for row in sample_rows]
    strata_table = pq.read_table(strata_path, columns=[
        "GEOID", "pct_urban", "svi_minority", "epht_heat_days_summer",
    ])
    strata_rows = strata_table.to_pylist()
    strata_by_id = {str(row["GEOID"]): row for row in strata_rows}
    if len(strata_by_id) != len(strata_rows) or not set(official_ids).issubset(strata_by_id):
        raise ValueError("sample GEOIDs are duplicated or missing in provided strata")
    # The strata Parquet has seven Texas records outside the official scored IDs;
    # exclude those extra polygons/rows from this scored-tract screen.
    strata_by_id = {geoid: strata_by_id[geoid] for geoid in official_ids}

    estimate_rows, estimate_columns = read_csv(args.estimates)
    candidate_rows, candidate_columns = read_csv(args.sensitivity)
    official_set = set(official_ids)
    c2_rows = [row for row in estimate_rows if row["GEOID"] in official_set]
    c2_by_id = {row["GEOID"]: row for row in c2_rows}
    c3_by_id = {row["GEOID"]: row for row in candidate_rows}
    if len(c2_rows) != len(c2_by_id) or len(c2_by_id) != len(official_ids) or set(c2_by_id) != official_set:
        raise ValueError("C2 estimator rows do not exactly match official regional GEOIDs")
    if len(candidate_rows) != len(c3_by_id) or not set(official_ids).issubset(c3_by_id):
        raise ValueError("C3 sensitivity candidate has duplicate or missing official GEOIDs")

    rural = []
    rural_total = 0
    rural_missing_svi = 0
    missing_urbanity = 0
    for geoid in official_ids:
        row = strata_by_id[geoid]
        urban = row["pct_urban"]
        svi = row["svi_minority"]
        if urban is None or not np.isfinite(float(urban)):
            missing_urbanity += 1
            continue
        if float(urban) < 0.5:
            rural_total += 1
            if svi is None or not np.isfinite(float(svi)):
                rural_missing_svi += 1
                continue
        if svi is None or not np.isfinite(float(svi)):
            continue
        if float(urban) < 0.5:
            rural.append({
                "GEOID": geoid,
                "pct_urban": float(urban),
                "svi_minority": float(svi),
                "epht_heat_days_summer": row["epht_heat_days_summer"],
                "c2": c2_by_id[geoid],
                "c3": c3_by_id[geoid],
            })
    if not rural:
        raise ValueError("no rural tracts with nonmissing svi_minority")
    threshold = float(np.quantile([row["svi_minority"] for row in rural], 0.75))
    high = [row for row in rural if row["svi_minority"] >= threshold]
    comparison = [row for row in rural if row["svi_minority"] < threshold]
    if not high or not comparison:
        raise ValueError("quartile split has an empty group")

    outcomes = {}
    for version in ("c2", "c3"):
        high_scores = np.asarray([float(row[version]["coverage_gap_score"]) for row in high])
        comparison_scores = np.asarray([
            float(row[version]["coverage_gap_score"]) for row in comparison
        ])
        difference = float(high_scores.mean() - comparison_scores.mean())
        seed = BASE_SEED + sum(ord(ch) for ch in f"{version}coverage_gap_score")
        outcomes[version] = {
            "high_group_mean": float(high_scores.mean()),
            "comparison_group_mean": float(comparison_scores.mean()),
            "mean_difference_high_minus_comparison": difference,
            "bootstrap_95_percentile_ci": bootstrap_difference(
                high_scores, comparison_scores, seed
            ),
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "bootstrap_seed": seed,
        }

    components = {}
    for name in ("transport", "building", "poi"):
        flag = f"{name}_defined"
        gap = f"{name}_gap"
        high_values = np.asarray([
            float(row["c2"][gap]) for row in high if row["c2"][flag] == "1"
        ])
        comparison_values = np.asarray([
            float(row["c2"][gap]) for row in comparison if row["c2"][flag] == "1"
        ])
        seed = BASE_SEED + sum(ord(ch) for ch in name)
        components[name] = {
            "high_group_defined_n": int(len(high_values)),
            "high_group_defined_rate": float(len(high_values) / len(high)),
            "comparison_defined_n": int(len(comparison_values)),
            "comparison_defined_rate": float(len(comparison_values) / len(comparison)),
            "high_group_defined_mean": float(high_values.mean()),
            "comparison_defined_mean": float(comparison_values.mean()),
            "mean_difference_defined_only": float(high_values.mean() - comparison_values.mean()),
            "bootstrap_95_percentile_ci_defined_only": bootstrap_difference(
                high_values, comparison_values, seed
            ),
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "bootstrap_seed": seed,
        }

    named = []
    for row in sorted(high, key=lambda item: float(item["c2"]["coverage_gap_score"]),
                      reverse=True)[:10]:
        named.append({
            "GEOID": row["GEOID"],
            "svi_minority": row["svi_minority"],
            "pct_urban": row["pct_urban"],
            "epht_heat_days_summer": row["epht_heat_days_summer"],
            "C2_coverage_gap_score": float(row["c2"]["coverage_gap_score"]),
            "C2_transport_gap": float(row["c2"]["transport_gap"]),
            "C2_building_gap": float(row["c2"]["building_gap"]),
            "C2_poi_gap": float(row["c2"]["poi_gap"]),
        })

    audit = {
        "analysis": "rural x upper-quartile provided svi_minority field, South-Central Texas",
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                        "pyarrow": pa.__version__},
        "interpretation": "screen of C2/C3 proxy predictions, not organiser targets or measured ground truth",
        "fixed_scorecard_novelty": "joint rural x SVI minority-domain subgroup; the official description lists dimensions separately and does not specify this cross-tab",
        "group_definition": {
            "region": REGION,
            "rural_rule": "provided pct_urban < 0.5",
            "svi_field": "provided strata field svi_minority",
            "upper_quartile_cutoff_inclusive": threshold,
            "high_group_rule": "rural rows with svi_minority >= regional rural q75",
            "comparison_rule": "rural rows with svi_minority < regional rural q75",
            "rural_rows_total": rural_total,
            "rural_rows_with_nonmissing_svi": len(rural),
            "rural_rows_missing_svi": rural_missing_svi,
            "all_region_rows_missing_urbanity": missing_urbanity,
            "high_group_n": len(high),
            "comparison_group_n": len(comparison),
        },
        "composite_results": outcomes,
        "C2_component_results_defined_only": components,
        "named_high_group_tracts_sorted_by_C2_estimate": named,
        "uncertainty_note": "Percentile intervals resample tracts independently within each comparison group. They quantify empirical tract-resampling variability only; they do not account for spatial dependence, proxy-model uncertainty, or hidden-target error.",
        "causal_and_accuracy_limitations": [
            "No labels or removed reference layers were read; the audit does not estimate RMSE or validate true coverage gaps.",
            "The estimator does not use svi_minority as an input, but its predictions are still proxies and may share other covariate structure with strata.",
            "The pattern is descriptive and does not establish that vulnerability causes mapping gaps or that the estimate reflects organiser scores.",
        ],
        "inputs": {
            "official_sample": {"path": str(sample_path), "sha256": sha256(sample_path),
                                "rows": len(official_ids), "columns": sample_columns},
            "provided_strata": {"path": str(strata_path), "sha256": sha256(strata_path),
                                "read_columns": ["GEOID", "pct_urban", "svi_minority",
                                                 "epht_heat_days_summer"]},
            "C2_estimates": {"path": str(args.estimates), "sha256": sha256(args.estimates),
                             "read_columns": estimate_columns},
            "C3_sensitivity": {"path": str(args.sensitivity), "sha256": sha256(args.sensitivity),
                               "read_columns": candidate_columns},
            "external_public_data_used": [],
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"PASS {len(high)} upper-quartile rural tracts vs {len(comparison)} other rural tracts; "
          f"C2 difference {outcomes['c2']['mean_difference_high_minus_comparison']:.6f} "
          f"95% bootstrap CI {outcomes['c2']['bootstrap_95_percentile_ci']}")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
