#!/usr/bin/env python3
"""Join public CDC PLACES 2025 context to scored Texas tracts.

This is a discovery-only population-context analysis. It does not use or
reconstruct organizer target scores and is never an input to scored candidates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

BORDER_COUNTIES = {
    "48215", "48061", "48479", "48427", "48323", "48465", "48505",
    "48247", "48131", "48127",
}
MEASURES = {
    "ACCESS2": "uninsured adults aged 18–64",
    "DISABILITY": "any disability",
    "DIABETES": "diagnosed diabetes",
    "GHLTH": "fair/poor self-rated health",
    "MOBILITY": "mobility disability",
    "DEPRESSION": "depression",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def weighted_mean(frame: pd.DataFrame, measure: str) -> float:
    return float(np.average(frame[measure].astype(float), weights=frame.pop18.astype(float)))


def county_cluster_ci(frame: pd.DataFrame, group: str, measure: str,
                      rng: np.random.Generator, replicates: int = 1000) -> tuple[float, float, float]:
    """Population-weighted difference, resampling county clusters with replacement."""
    # Sufficient statistics let each bootstrap draw preserve all tracts in each
    # sampled county, including counties containing both groups.
    d = frame[["county", group, "pop18", measure]].copy()
    d["weighted_value"] = d[measure].astype(float) * d.pop18.astype(float)
    agg = d.groupby(["county", group], observed=True).agg(
        pop=("pop18", "sum"), weighted=("weighted_value", "sum")
    ).reset_index()
    counties = frame.county.drop_duplicates().to_numpy()
    index = {(row["county"], bool(row[group])): (float(row["pop"]), float(row["weighted"]))
             for row in agg.to_dict(orient="records")}
    point = weighted_mean(frame[frame[group]], measure) - weighted_mean(frame[~frame[group]], measure)
    draws = np.empty(replicates, dtype=float)
    for b in range(replicates):
        sampled = rng.choice(counties, size=len(counties), replace=True)
        sums = {True: [0.0, 0.0], False: [0.0, 0.0]}
        for county in sampled:
            for label in (True, False):
                values = index.get((county, label))
                if values:
                    sums[label][0] += values[0]
                    sums[label][1] += values[1]
        draws[b] = sums[True][1] / sums[True][0] - sums[False][1] / sums[False][0]
    lo, hi = np.quantile(draws, [0.025, 0.975])
    return point, float(lo), float(hi)


def analyze(features: pd.DataFrame, strata: pd.DataFrame, places: pd.DataFrame,
            replicates: int = 1000, seed: int = 0) -> dict:
    features = features[["GEOID", "overture_buildings", "overture_building_osm_source_share"]]
    strata = strata[["GEOID", "pct_urban", "pop_total", "svi_overall"]]
    master = features.merge(strata, on="GEOID", how="inner", validate="one_to_one")
    tx_buildings = master[(master.overture_buildings > 0) & master.GEOID.str.startswith("48")].copy()
    w = places.pivot_table(index="locationid", columns="measureid", values="data_value", aggfunc="first")
    pop18 = places.groupby("locationid").totalpop18plus.first()
    w["pop18"] = pop18
    tx = tx_buildings.merge(w, left_on="GEOID", right_index=True, how="left", validate="one_to_one")
    tx = tx[tx.ACCESS2.notna() & (tx.pop18 > 0)].copy()
    tx["county"] = tx.GEOID.str[:5]
    tx["low_osm"] = tx.overture_building_osm_source_share < 0.05
    tx["border_county"] = tx.county.isin(BORDER_COUNTIES)
    # Exclude measures with missing values from that contrast only.
    rng = np.random.default_rng(seed)
    contrasts = {}
    for group, label, subset in (
        ("low_osm", "OSM share <5% vs >=5%, all Texas scored tracts", tx),
        ("low_osm", "OSM share <5% vs >=5%, urban tracts only", tx[tx.pct_urban >= 0.5]),
        ("border_county", "ten border counties vs rest of South-Central Texas", tx),
    ):
        result = {"tracts_group1": int(subset[group].sum()),
                  "tracts_group0": int((~subset[group]).sum()),
                  "adults_group1": int(subset.loc[subset[group], "pop18"].sum()),
                  "adults_group0": int(subset.loc[~subset[group], "pop18"].sum()),
                  "counties": int(subset.county.nunique()), "measures": {}}
        for code, name in MEASURES.items():
            d = subset.dropna(subset=[code, "pop18"]).copy()
            a, b = d[d[group]], d[~d[group]]
            if a.empty or b.empty:
                continue
            point, lo, hi = county_cluster_ci(d, group, code, rng, replicates)
            result["measures"][code] = {
                "label": name,
                "group1_weighted_prevalence_pct": weighted_mean(a, code),
                "group0_weighted_prevalence_pct": weighted_mean(b, code),
                "difference_pp": point, "county_cluster_ci95_pp": [lo, hi],
            }
        contrasts[label] = result
    # Within-county, population-weighted demeaned low-OSM contrast.
    d = tx.copy()
    counts = d.groupby("county").low_osm.agg(["any", lambda x: (~x).any()])
    both_counties = counts[counts["any"] & counts["<lambda_0>"]].index
    dd = d[d.county.isin(both_counties)].copy()
    within = {}
    for code, name in MEASURES.items():
        ddm = dd.dropna(subset=[code, "pop18"]).copy()
        county_mean = ddm.groupby("county").apply(
            lambda x: np.average(x[code].astype(float), weights=x.pop18.astype(float)),
            include_groups=False,
        )
        ddm["demeaned"] = ddm[code].astype(float) - ddm.county.map(county_mean)
        a, b = ddm[ddm.low_osm], ddm[~ddm.low_osm]
        within[code] = {
            "label": name,
            "difference_pp": float(np.average(a.demeaned, weights=a.pop18) - np.average(b.demeaned, weights=b.pop18)),
        }
    return {
        "analysis": "CDC PLACES 2025 health prevalence as border-population context",
        "scope": "Best Bias Discovery only; not evidence about low-OSM tracts and not used in scored predictions",
        "inputs": {"tx_scored_tracts_with_buildings": int(len(tx_buildings)),
                   "pl_places_joined_tracts": int(tx.ACCESS2.notna().sum()),
                   "analysis_tracts": int(len(tx)), "county_clusters": int(tx.county.nunique()),
                   "border_county_fips": sorted(BORDER_COUNTIES)},
        "method": {"join_key": "2020 tract GEOID (CDC locationid)",
                   "weight": "CDC totalpop18plus; population-weighted crude prevalence",
                   "bootstrap": f"{replicates} county-cluster resamples with replacement; seed={seed}; percentile 95% interval",
                   "border_comparison": "the ten named Texas-Mexico border counties vs the rest of the South-Central Texas scored region",
                   "low_osm_threshold": "provided Overture building OSM-source share < 0.05",
                   "limitations": ["descriptive population context, not causal", "within-county low-OSM comparison is not evidence that low-OSM tracts are sicker", "health outcomes and dispatch/evacuation effects were not measured"]},
        "contrasts": contrasts,
        "within_county_low_osm_vs_other_pp": within,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--replicates", type=int, default=1000)
    args = parser.parse_args()
    root = args.workspace
    feat_path = root / "gap-estimator/features/south-central-tx-overture-tract-summary.parquet"
    strata_path = root / "defined-flags-inputs/south-central-tx-strata-tract-table.parquet"
    source_dir = root / "external-discovery-2026-10-29-fable/cdc-places-2025-tx"
    places_path = source_dir / "places2025_tract_tx_crude_selected.csv"
    manifest_path = source_dir / "MANIFEST.json"
    report = analyze(pd.read_parquet(feat_path), pd.read_parquet(strata_path),
                     pd.read_csv(places_path, dtype={"locationid": str, "countyfips": str}),
                     args.replicates)
    report["input_sha256"] = {"overture_feature_cache": sha256(feat_path),
                              "provided_strata": sha256(strata_path),
                              "cdc_places_csv": sha256(places_path),
                              "cdc_manifest": sha256(manifest_path)}
    report["cdc_manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    border = report["contrasts"]["ten border counties vs rest of South-Central Texas"]["measures"]
    low = report["contrasts"]["OSM share <5% vs >=5%, all Texas scored tracts"]["measures"]["ACCESS2"]
    within = report["within_county_low_osm_vs_other_pp"]["ACCESS2"]["difference_pp"]
    print(f"tracts with buildings={report['inputs']['tx_scored_tracts_with_buildings']}; "
          f"PLACES join={report['inputs']['pl_places_joined_tracts']}; "
          f"analysis={report['inputs']['analysis_tracts']}; counties={report['inputs']['county_clusters']}")
    for code in MEASURES:
        m = border[code]
        print(f"border {m['label']}: {m['group1_weighted_prevalence_pct']:.1f}% vs "
              f"{m['group0_weighted_prevalence_pct']:.1f}%; difference {m['difference_pp']:+.1f} "
              f"[{m['county_cluster_ci95_pp'][0]:+.1f}, {m['county_cluster_ci95_pp'][1]:+.1f}] pp")
    print(f"low OSM (<5%) ACCESS2 contrast: {low['difference_pp']:+.1f} pp "
          f"[{low['county_cluster_ci95_pp'][0]:+.1f}, {low['county_cluster_ci95_pp'][1]:+.1f}]; "
          f"within-county difference={within:+.1f} pp")
    print(f"report: {args.out}")


if __name__ == "__main__":
    main()
