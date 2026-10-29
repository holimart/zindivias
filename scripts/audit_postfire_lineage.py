#!/usr/bin/env python3
"""Recheck provided-data source mix around the Paradise/Magalia discrepancy."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

REGIONS = ("eastern-ok", "maricopa-az", "northern-ca", "south-central-tx")
FOCAL = ("06007001800", "06007002100", "06007002000", "06007001900",
         "06007002300", "06007002200")
COUNTY_NAMES = {"061": "Placer", "067": "Sacramento"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def official_ids(path: Path) -> list[str]:
    # Read only the identifier column; do not access sample score placeholders.
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = csv.reader(f)
        header = next(rows)
        if not header or header[0] != "GEOID":
            raise ValueError(f"expected GEOID in first sample column: {path}")
        ids = [row[0] for row in rows]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"empty or duplicate official GEOIDs in {path}")
    return ids


def clustered_ols(df: pd.DataFrame) -> dict:
    d = df[(df.usfs_BuildingCount_sum >= 50) & (df.overture_buildings > 0)].copy()
    d["ratio"] = d.overture_buildings / d.usfs_BuildingCount_sum
    d["log_ratio"] = np.log(d.ratio)
    d["log_density"] = np.log1p(d.pop_total / (d.ALAND.clip(lower=1) / 1e6))
    d["county"] = d.GEOID.str[:5]
    d = d[["log_ratio", "overture_building_osm_source_share", "pct_urban",
           "log_density", "region", "county"]].dropna()
    # Intercept, OSM share, urbanity, log density, and region fixed effects.
    columns = [np.ones(len(d)), d.overture_building_osm_source_share.to_numpy(),
               d.pct_urban.to_numpy(), d.log_density.to_numpy()]
    for region in sorted(d.region.unique())[1:]:
        columns.append((d.region == region).to_numpy(dtype=float))
    x = np.column_stack(columns)
    y = d.log_ratio.to_numpy()
    xtx_inv = np.linalg.pinv(x.T @ x)
    beta = xtx_inv @ x.T @ y
    residual = y - x @ beta
    meat = np.zeros((x.shape[1], x.shape[1]))
    groups = d.county.to_numpy()
    for county in np.unique(groups):
        score = x[groups == county].T @ residual[groups == county]
        meat += np.outer(score, score)
    n, k, g = len(d), x.shape[1], len(np.unique(groups))
    correction = (g / (g - 1)) * ((n - 1) / (n - k))
    covariance = xtx_inv @ meat @ xtx_inv * correction
    return {"outcome": "log(Overture buildings / provided USFS WRC building count)",
            "predictor": "provided Overture OSM-only source share",
            "controls": ["pct_urban", "log1p(pop_total / ALAND_km2)", "region fixed effects"],
            "standard_error": "county-clustered sandwich with finite-sample correction",
            "n": n, "county_clusters": g, "coefficient": float(beta[1]),
            "standard_error_value": float(np.sqrt(covariance[1, 1]))}


def build(workspace: Path) -> dict:
    frames, inputs = [], {}
    for region in REGIONS:
        feature = workspace / "gap-estimator" / "features" / f"{region}-overture-tract-summary.parquet"
        strata = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        sample = workspace / "samples" / f"{region}-sample-submission.csv"
        ids = official_ids(sample)
        f = pd.read_parquet(feature)
        s = pd.read_parquet(strata, columns=["GEOID", "COUNTYFP", "ALAND", "pop_total",
                                             "pct_urban", "usfs_BuildingCount_sum",
                                             "mtbs_wildfire_burned_pct_land",
                                             "mtbs_wildfire_last_year"])
        if set(f.GEOID) != set(ids) or not set(ids).issubset(set(s.GEOID)):
            raise ValueError(f"feature/strata GEOIDs do not cover official sample for {region}")
        d = f.merge(s, on="GEOID", how="inner", validate="one_to_one")
        d["region"] = region
        frames.append(d)
        inputs[region] = {"official_sample": str(sample.relative_to(workspace)),
                          "official_geoid_sha256": hashlib.sha256(
                              ("\n".join(ids) + "\n").encode()).hexdigest(),
                          "feature_cache": str(feature.relative_to(workspace)),
                          "feature_cache_sha256": sha256(feature),
                          "strata": str(strata.relative_to(workspace)),
                          "strata_sha256": sha256(strata)}
    data = pd.concat(frames, ignore_index=True)
    focal = data[data.GEOID.isin(FOCAL)].copy()
    focal_rows = []
    for row in focal.sort_values("GEOID").itertuples():
        focal_rows.append({"GEOID": row.GEOID,
                           "overture_buildings": int(row.overture_buildings),
                           "microsoft_only_share": float(row.overture_building_ms_only_source_share),
                           "osm_only_share": float(row.overture_building_osm_only_source_share),
                           "overture_to_usfs_ratio": float(row.overture_buildings / row.usfs_BuildingCount_sum)})
    nc = data[(data.region == "northern-ca") &
              (data.mtbs_wildfire_burned_pct_land == 0) &
              (data.usfs_BuildingCount_sum >= 50) & (data.overture_buildings > 0)].copy()
    nc["overture_to_usfs_ratio"] = nc.overture_buildings / nc.usfs_BuildingCount_sum
    unburned_low = nc[nc.overture_to_usfs_ratio < 0.8].sort_values("GEOID")
    unburned_rows = [{"GEOID": r.GEOID, "county_fips": str(r.COUNTYFP).zfill(3),
                      "county_name": COUNTY_NAMES.get(str(r.COUNTYFP).zfill(3), "other"),
                      "pct_urban": float(r.pct_urban),
                      "overture_to_usfs_ratio": float(r.overture_to_usfs_ratio)}
                     for r in unburned_low.itertuples()]
    return {"analysis": "provided-data source-mix and USFS ratio recheck for Camp Fire writeup",
            "prohibited_sources_used": False,
            "scope": "official sample GEOIDs; provided Overture feature caches and provided strata only",
            "inputs": inputs,
            "focal_paradise_magalia": focal_rows,
            "focal_share_ranges": {
                "microsoft_only_min": min(r["microsoft_only_share"] for r in focal_rows),
                "microsoft_only_max": max(r["microsoft_only_share"] for r in focal_rows),
                "osm_only_min": min(r["osm_only_share"] for r in focal_rows),
                "osm_only_max": max(r["osm_only_share"] for r in focal_rows)},
            "pooled_ols": clustered_ols(data),
            "unburned_northern_ca_ratio_below_0_8": {
                "definition": "provided MTBS burned percentage equals 0; USFS WRC count >=50; Overture count >0; Overture/USFS ratio <0.8",
                "n": len(unburned_rows), "all_urban": bool(unburned_low.pct_urban.ge(0.5).all()),
                "ratio_min": float(unburned_low.overture_to_usfs_ratio.min()),
                "ratio_max": float(unburned_low.overture_to_usfs_ratio.max()),
                "tracts": unburned_rows}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    result = build(args.workspace)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(args.out),
                      "n_unburned_low": result["unburned_northern_ca_ratio_below_0_8"]["n"],
                      "counties": sorted({r["county_name"] for r in result["unburned_northern_ca_ratio_below_0_8"]["tracts"]}),
                      "ols": result["pooled_ols"]}, sort_keys=True))


if __name__ == "__main__":
    main()
