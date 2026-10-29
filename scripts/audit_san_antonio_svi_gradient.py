#!/usr/bin/env python3
"""Describe OSM-attributed building share across provided SVI quartiles.

Reads only official sample GEOIDs, the provided Overture source-share feature
cache, and the provided strata columns ``svi_overall``, ``svi_covered``,
``pct_urban`` and ``pop_total``. It does not read hidden scores, external SVI,
removed reference layers, or geometry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

REGIONS = ("eastern-ok", "maricopa-az", "northern-ca", "south-central-tx")
SOURCE_COLUMN = "overture_building_osm_source_share"
REPLICATES = 5_000
BASE_SEED = 20261024


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def quartile_labels(rows: list[dict[str, Any]]) -> tuple[list[int | None], list[float]]:
    values = [number(row.get("svi_overall")) for row in rows]
    valid = np.asarray([x for x in values if x is not None], dtype=float)
    cuts = np.quantile(valid, [0.25, 0.50, 0.75])
    labels = [int(np.searchsorted(cuts, x, side="right") + 1) if x is not None else None
              for x in values]
    return labels, [float(x) for x in cuts]


def bootstrap_difference(q4: np.ndarray, q1: np.ndarray, seed: int) -> list[float] | None:
    if not len(q4) or not len(q1):
        return None
    rng = np.random.default_rng(seed)
    draws = np.empty(REPLICATES, dtype=np.float64)
    chunk = 128
    for start in range(0, REPLICATES, chunk):
        stop = min(REPLICATES, start + chunk)
        i4 = rng.integers(0, len(q4), size=(stop - start, len(q4)), dtype=np.int32)
        i1 = rng.integers(0, len(q1), size=(stop - start, len(q1)), dtype=np.int32)
        draws[start:stop] = q4[i4].mean(axis=1) - q1[i1].mean(axis=1)
    return [float(x) for x in np.quantile(draws, [0.025, 0.975])]


def ols_slope(rows: list[dict[str, Any]], cluster: bool) -> dict[str, Any]:
    valid = [(number(r.get("svi_overall")), number(r.get(SOURCE_COLUMN)), str(r["GEOID"][:5]))
             for r in rows]
    valid = [(x, y, c) for x, y, c in valid if x is not None and y is not None]
    x = np.asarray([a[0] for a in valid], dtype=float)
    y = np.asarray([a[1] for a in valid], dtype=float)
    X = np.column_stack([np.ones(len(x)), x])
    inv = np.linalg.pinv(X.T @ X)
    beta = inv @ X.T @ y
    resid = y - X @ beta
    if cluster:
        groups = np.asarray([a[2] for a in valid])
        unique = np.unique(groups)
        meat = np.zeros((2, 2), dtype=float)
        for group in unique:
            score = X[groups == group].T @ resid[groups == group]
            meat += np.outer(score, score)
        g = len(unique)
        correction = (g / (g - 1)) * ((len(y) - 1) / (len(y) - X.shape[1])) if g > 1 else 1.0
        cov = inv @ meat @ inv * correction
        method = "county-clustered CR1 OLS"
        clusters: int | None = int(g)
    else:
        # HC1 robust covariance; county-level clustering is not identifiable in Bexar.
        meat = np.zeros((2, 2), dtype=float)
        for row, error in zip(X, resid):
            meat += error * error * np.outer(row, row)
        cov = inv @ meat @ inv * (len(y) / max(1, len(y) - X.shape[1]))
        method = "HC1 heteroskedasticity-robust OLS; one-county Bexar scope"
        clusters = None
    return {"slope_per_1_svi_unit": float(beta[1]),
            "standard_error": float(np.sqrt(max(0.0, cov[1, 1]))),
            "n_tracts": int(len(y)), "county_clusters": clusters, "method": method}


def summarize_scope(rows: list[dict[str, Any]], scope: str,
                    cuts: list[float] | None = None) -> dict[str, Any]:
    if cuts is None:
        labels, cuts = quartile_labels(rows)
    else:
        labels = [int(np.searchsorted(cuts, number(r.get("svi_overall")), side="right") + 1)
                  if number(r.get("svi_overall")) is not None else None for r in rows]
    groups: dict[str, dict[str, Any]] = {}
    values: dict[int, np.ndarray] = {}
    for i, row in enumerate(rows):
        row["svi_q"] = labels[i]
    for q in range(1, 5):
        group = [r for r in rows if r["svi_q"] == q and number(r.get(SOURCE_COLUMN)) is not None]
        arr = np.asarray([number(r[SOURCE_COLUMN]) for r in group], dtype=float)
        values[q] = arr
        population = sum(number(r.get("pop_total")) or 0.0 for r in group)
        groups[f"q{q}"] = {"n_tracts": int(len(group)),
                            "population_sum": int(population),
                            "mean_osm_share": float(arr.mean()) if len(arr) else None}
    q4, q1 = values[4], values[1]
    seed = BASE_SEED
    delta = float(q4.mean() - q1.mean()) if len(q4) and len(q1) else None
    return {"scope": scope, "svi_quartile_cutpoints": cuts, "quartiles": groups,
            "q4_minus_q1": {"difference": delta,
                            "bootstrap_ci_95": bootstrap_difference(q4, q1, seed),
                            "bootstrap_replicates": REPLICATES, "bootstrap_seed": seed,
                            "n_q1": int(len(q1)), "n_q4": int(len(q4))}}


def load_scope(workspace: Path, region: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    sample = workspace / "samples" / f"{region}-sample-submission.csv"
    features = workspace / "gap-estimator" / "features" / f"{region}-overture-tract-summary.parquet"
    strata = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
    ids = pacsv.read_csv(sample, convert_options=pacsv.ConvertOptions(
        include_columns=["GEOID"], column_types={"GEOID": pa.string()}))["GEOID"].to_pylist()
    feature_rows = pq.read_table(features, columns=["GEOID", SOURCE_COLUMN]).to_pylist()
    strata_rows = pq.read_table(strata, columns=["GEOID", "svi_overall", "svi_covered", "pct_urban", "pop_total"]).to_pylist()
    feature = {str(r["GEOID"]): r for r in feature_rows}
    stratum = {str(r["GEOID"]): r for r in strata_rows}
    if len(ids) != len(set(ids)) or len(feature) != len(feature_rows) or len(stratum) != len(strata_rows):
        raise ValueError(f"duplicate GEOID in provided data for {region}")
    joined = []
    for geoid in ids:
        f, s = feature.get(geoid), stratum.get(geoid)
        if f is None or s is None:
            raise ValueError(f"official sample GEOID {geoid} unmatched in {region}")
        if s.get("svi_covered") and number(s.get("svi_overall")) is not None:
            joined.append({"GEOID": geoid, SOURCE_COLUMN: number(f.get(SOURCE_COLUMN)),
                           "svi_overall": number(s.get("svi_overall")),
                           "pct_urban": number(s.get("pct_urban")),
                           "pop_total": number(s.get("pop_total"))})
    return joined, {"sample": sha256(sample), "features": sha256(features), "strata": sha256(strata)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("reports/san-antonio-svi-gradient.json"))
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    data_by_region: dict[str, list[dict[str, Any]]] = {}
    hashes: dict[str, str] = {}
    for region in REGIONS:
        data_by_region[region], current = load_scope(workspace, region)
        hashes.update({f"{region}:{k}": v for k, v in current.items()})

    regional = {region: summarize_scope(rows, region)
                for region, rows in data_by_region.items()}
    tx = data_by_region["south-central-tx"]
    urban = [r.copy() for r in tx if number(r.get("pct_urban")) is not None and number(r["pct_urban"]) >= 0.5]
    tx_cuts = regional["south-central-tx"]["svi_quartile_cutpoints"]
    urban_summary = summarize_scope(urban, "South-Central Texas urban (`pct_urban >= 0.5`)",
                                    cuts=tx_cuts)
    tx_slope = ols_slope(tx, cluster=True)
    tx_urban_slope = ols_slope(urban, cluster=True)

    bexar = [r.copy() for r in tx if r["GEOID"].startswith("48029")]
    bexar_summary = summarize_scope(bexar, "Bexar County (San Antonio area)",
                                    cuts=tx_cuts)
    q4 = [r for r in bexar if r["svi_q"] == 4 and number(r.get(SOURCE_COLUMN)) is not None]
    q1 = [r for r in bexar if r["svi_q"] == 1 and number(r.get(SOURCE_COLUMN)) is not None]
    low = sorted(q4, key=lambda r: (number(r[SOURCE_COLUMN]), r["GEOID"]))[:12]
    under5 = [r for r in q4 if number(r[SOURCE_COLUMN]) < 0.05]
    bexar_summary["q4_below_5_percent_osm"] = {
        "tracts": len(under5),
        "population_sum": int(sum(number(r.get("pop_total")) or 0.0 for r in under5)),
        "q1_tracts_below_5_percent": sum(number(r[SOURCE_COLUMN]) < 0.05 for r in q1),
        "q1_tracts_with_source_denominator": len(q1),
    }
    bexar_summary["q4_lowest_12_geoid_share"] = [
        {"GEOID": r["GEOID"], "osm_share": number(r[SOURCE_COLUMN]),
         "svi_overall": number(r["svi_overall"])} for r in low]
    bexar_slope = ols_slope(bexar, cluster=False)
    report = {
        "analysis": "Descriptive OSM-attributed building share by provided SVI quartile",
        "definitions": {
            "outcome": "provided Overture building OSM source share; unweighted tract mean; source attribution is not an accuracy or completeness measure",
            "svi": "provided `svi_overall` where `svi_covered` is true; quartiles computed within scope by empirical 25/50/75% quantiles, ties assigned upward with searchsorted(side=right)",
            "q4_minus_q1": "mean Q4 OSM share minus mean Q1 OSM share",
            "bootstrap": "5,000 independent tract-with-replacement resamples within Q1 and Q4; percentile 95% interval; fixed seed 20261024",
            "urban": "provided pct_urban >= 0.5; retains South-Central Texas region-wide SVI cutpoints",
            "bexar": "county FIPS 48029 among official South-Central Texas sample IDs; uses the same South-Central Texas SVI quartile cutpoints for comparability",
        },
        "data_scope": {"official_sample_ids_only": True,
                       "provided_overture_source_share_only": True,
                       "provided_svi_only": True,
                       "provided_population_and_urbanity_only": True,
                       "external_data_used": [], "removed_reference_layers_read": False,
                       "sample_score_values_read": False,
                       "hidden_targets_or_leaderboard_values_read": False,
                       "tract_geometry_read": False, "input_sha256": hashes},
        "regional_svi_quartiles": regional,
        "south_central_texas_urban": urban_summary,
        "south_central_texas_slope": tx_slope,
        "south_central_texas_urban_slope": tx_urban_slope,
        "bexar_county": {**bexar_summary, "svi_slope": bexar_slope,
                          "scope_n_tracts_with_svi": len(bexar)},
        "limitations": [
            "Descriptive source-provenance association only; not accuracy, completeness, causal, or organizer-score evidence.",
            "The tract bootstrap does not account for spatial or county dependence; the South-Central Texas slope reports county-clustered CR1 uncertainty.",
            "Bexar County is used as a San Antonio-area referent; no city-boundary or directional-geography claim is made.",
            "Names of the lowest-share tracts are identifiers for partner-led review, not evidence of service failures.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    for region, result in regional.items():
        delta = result["q4_minus_q1"]
        lo, hi = delta["bootstrap_ci_95"]
        print(f"{region}: {delta['difference']:.4f} ({lo:.4f}, {hi:.4f}); n Q1/Q4={delta['n_q1']}/{delta['n_q4']}")
    result = bexar_summary["q4_minus_q1"]
    lo, hi = result["bootstrap_ci_95"]
    print(f"Bexar: {result['difference']:.4f} ({lo:.4f}, {hi:.4f}); slope={bexar_slope['slope_per_1_svi_unit']:.4f} SE={bexar_slope['standard_error']:.4f}")
    print(f"Report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
