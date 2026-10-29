#!/usr/bin/env python3
"""Reproduce the South Texas border-metro Overture source-mix discovery.

Adapted from the steward's Fable ``s6.py`` work. This focused version reads only
GEOID, building counts/source shares, the documented stratum covariates and heat
context, plus CDC SVI 2022 EP_HISP for one descriptive sensitivity regression.
It does not read sample score placeholders, candidate files, public scores, or
removed reference layers. ``usfs_BuildingCount_sum`` is used only for the
provided-data contextual ratio diagnostic, never as a target or candidate input.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pyarrow
import pyarrow.parquet as pq

REGION = "south-central-tx"
BORDER_COUNTIES = {
    "48215": "Hidalgo",
    "48061": "Cameron",
    "48479": "Webb",
    "48427": "Starr",
    "48323": "Maverick",
    "48465": "Val Verde",
    "48505": "Zapata",
    "48247": "Jim Hogg",
    "48131": "Duval",
    "48127": "Dimmit",
}
NAMED_GEOIDS = ("48215021906", "48215023525", "48215024404", "48061012002")
FEATURE_COLUMNS = (
    "GEOID", "overture_buildings", "overture_building_osm_source_share",
    "overture_building_ms_only_source_share", "overture_building_other_source_share",
)
STRATA_COLUMNS = (
    "GEOID", "ALAND", "pop_total", "pct_urban", "svi_overall",
    "uhe_covered", "uhe_wbgtmax30_days_per_yr", "usfs_BuildingCount_sum",
)
SVI_URL = "https://svi.cdc.gov/Documents/Data/2022/csv/states/SVI_2022_US.csv"
SVI_MANIFEST = "external-discovery-2026-10-27/MANIFEST.json"
SVI_LICENSE_ASSESSMENT = (
    "Dataset-specific terms were not stated on the fetched ATSDR page; treated as a U.S. "
    "federal government work under 17 U.S.C. Section 105, subject to third-party exceptions. "
    "The statutory basis is an assessment, not a quoted dataset-specific licence."
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def bootstrap_difference_ci(group1: Iterable[float], group0: Iterable[float],
                            replicates: int = 5000, seed: int = 0) -> dict[str, Any]:
    """Independent tract bootstrap; matches the steward s6.py definition."""
    a = np.asarray(list(group1), dtype=float)
    b = np.asarray(list(group0), dtype=float)
    if not len(a) or not len(b):
        raise ValueError("both groups must contain observations")
    rng = np.random.default_rng(seed)
    draws = np.empty(replicates, dtype=float)
    for i in range(replicates):
        draws[i] = rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean()
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return {"difference": float(a.mean() - b.mean()),
            "ci_95": [float(lo), float(hi)], "replicates": replicates, "seed": seed}


def cluster_bootstrap_difference(rows: list[dict[str, Any]], replicates: int = 1000,
                                 seed: int = 0) -> dict[str, Any]:
    """Resample counties with replacement; estimate border-minus-rest tract mean."""
    counties = list(dict.fromkeys(r["county_fips"] for r in rows))
    grouped: dict[str, list[dict[str, Any]]] = {c: [] for c in counties}
    for row in rows:
        grouped[row["county_fips"]].append(row)
    rng = np.random.default_rng(seed)
    draws: list[float] = []
    for _ in range(replicates):
        sample = rng.choice(counties, len(counties))
        a: list[float] = []
        b: list[float] = []
        for county in sample:
            for row in grouped[str(county)]:
                (a if row["is_border"] else b).append(row["osm_share"])
        if a and b:
            draws.append(float(np.mean(a) - np.mean(b)))
    if len(draws) < 10:
        raise ValueError("too few valid county-bootstrap draws")
    lo, hi = np.percentile(draws, [2.5, 97.5])
    a = [r["osm_share"] for r in rows if r["is_border"]]
    b = [r["osm_share"] for r in rows if not r["is_border"]]
    return {"difference": float(np.mean(a) - np.mean(b)),
            "ci_95": [float(lo), float(hi)], "replicates": replicates,
            "valid_replicates": len(draws), "seed": seed,
            "cluster_count": len(counties)}


def cluster_ols(rows: list[dict[str, Any]], controls: tuple[str, ...]) -> dict[str, Any]:
    """OLS of tract OSM share on border indicator + controls, county-clustered SE."""
    complete = []
    for row in rows:
        if row["density"] is None:
            continue
        values = [row.get(c) for c in controls]
        if any(v is None for v in values):
            continue
        complete.append(row)
    if len(complete) <= len(controls) + 2:
        raise ValueError("insufficient complete cases for regression")
    x = np.column_stack([
        np.ones(len(complete)),
        np.asarray([float(r["is_border"]) for r in complete]),
        *[np.asarray([float(r[c]) for r in complete]) for c in controls],
    ])
    y = np.asarray([r["osm_share"] for r in complete], dtype=float)
    xtxi = np.linalg.pinv(x.T @ x)
    beta = xtxi @ x.T @ y
    resid = y - x @ beta
    meat = np.zeros((x.shape[1], x.shape[1]), dtype=float)
    clusters: dict[str, list[int]] = {}
    for i, row in enumerate(complete):
        clusters.setdefault(row["county_fips"], []).append(i)
    for indices in clusters.values():
        score = x[indices].T @ resid[indices]
        meat += np.outer(score, score)
    n, k, g = len(complete), x.shape[1], len(clusters)
    correction = (g / (g - 1)) * ((n - 1) / (n - k))
    variance = xtxi @ meat @ xtxi * correction
    se = float(np.sqrt(variance[1, 1]))
    coef = float(beta[1])
    return {"n": n, "counties": g, "border_coefficient": coef,
            "county_clustered_se": se,
            "normal_95_ci": [coef - 1.96 * se, coef + 1.96 * se],
            "controls": list(controls), "outcome": "tract OSM source share",
            "interpretation": "descriptive conditional association, not causal"}


def read_sample_geoids(path: Path) -> list[str]:
    # Deliberately access only GEOID; sample score placeholders are never parsed/used.
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if "GEOID" not in (reader.fieldnames or []):
            raise ValueError(f"sample lacks GEOID: {path}")
        ids = [str(row["GEOID"]) for row in reader]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate official sample GEOID")
    return ids


def read_svi_hisp(path: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    with path.open(encoding="latin-1", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"FIPS", "EP_HISP"}.issubset(reader.fieldnames or []):
            raise ValueError("CDC SVI file lacks FIPS / EP_HISP")
        for row in reader:
            geoid = (row.get("FIPS") or "").strip().zfill(11)
            value = _float(row.get("EP_HISP"))
            if geoid and value is not None:
                values[geoid] = value
    return values


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"tracts": 0, "population": 0, "osm_share_mean": None,
                "microsoft_only_share_mean": None, "other_source_footprints": 0,
                "other_source_weighted_share": None, "osm_below_5pct_tracts": 0,
                "population_in_osm_below_5pct": 0, "uhe_wbgt30_mean_days": None,
                "uhe_tracts": 0}
    building_total = sum(r["buildings"] for r in rows)
    other_count = sum(r["other_share"] * r["buildings"] for r in rows)
    osm_low = [r for r in rows if r["osm_share"] < 0.05]
    uhe = [r["uhe_days"] for r in rows if r["uhe_days"] is not None]
    return {
        "tracts": len(rows),
        "population": int(round(sum(r["population"] or 0 for r in rows))),
        "osm_share_mean_unweighted_tracts": float(np.mean([r["osm_share"] for r in rows])),
        "microsoft_only_share_mean_unweighted_tracts": float(np.mean([r["ms_only_share"] for r in rows])),
        "building_count": int(building_total),
        "other_source_footprints_rounded": int(round(other_count)),
        "other_source_weighted_share": float(other_count / building_total) if building_total else None,
        "osm_below_5pct_tracts": len(osm_low),
        "population_in_osm_below_5pct": int(round(sum(r["population"] or 0 for r in osm_low))),
        "uhe_wbgt30_mean_days": float(np.mean(uhe)) if uhe else None,
        "uhe_tracts": len(uhe),
    }


def _read_parquet(path: Path, columns: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    table = pq.read_table(path, columns=list(columns))
    rows = table.to_pylist()
    output = {str(row["GEOID"]): row for row in rows}
    if len(output) != len(rows):
        raise ValueError(f"duplicate GEOID in {path}")
    return output


def run(workspace: Path, repo: Path, out_path: Path) -> dict[str, Any]:
    sample_path = workspace / "samples" / f"{REGION}-sample-submission.csv"
    feature_path = workspace / "gap-estimator" / "features" / f"{REGION}-overture-tract-summary.parquet"
    strata_path = workspace / "defined-flags-inputs" / f"{REGION}-strata-tract-table.parquet"
    svi_path = workspace / "external-discovery-2026-10-27" / "cdc-svi-2022" / "SVI_2022_US.csv"
    source_report_path = repo / "reports" / "source-provenance.json"
    for path in (sample_path, feature_path, strata_path, svi_path, source_report_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    geoids = read_sample_geoids(sample_path)
    features = _read_parquet(feature_path, FEATURE_COLUMNS)
    strata = _read_parquet(strata_path, STRATA_COLUMNS)
    hisp = read_svi_hisp(svi_path)
    source_report = json.loads(source_report_path.read_text(encoding="utf-8"))
    global_datasets = source_report["regions"][REGION]["source_inputs"]["buildings"]["dataset_membership_counts"]
    records: list[dict[str, Any]] = []
    for geoid in geoids:
        if geoid not in features or geoid not in strata:
            raise ValueError(f"official GEOID missing from cache/strata: {geoid}")
        f, s = features[geoid], strata[geoid]
        osm = _float(f["overture_building_osm_source_share"])
        ms_only = _float(f["overture_building_ms_only_source_share"])
        other = _float(f["overture_building_other_source_share"])
        buildings = int(f["overture_buildings"] or 0)
        if buildings <= 0 or osm is None or ms_only is None or other is None:
            continue
        geoid_county = geoid[:5]
        area = _float(s["ALAND"])
        population = _float(s["pop_total"])
        density = (population / max(area, 1.0) * 1_000_000) if area is not None and population is not None else None
        uhe_covered = bool(s.get("uhe_covered"))
        uhe_days = _float(s.get("uhe_wbgtmax30_days_per_yr")) if uhe_covered else None
        ratio_denominator = _float(s.get("usfs_BuildingCount_sum"))
        records.append({
            "GEOID": geoid, "county_fips": geoid_county,
            "county_name": BORDER_COUNTIES.get(geoid_county),
            "is_border": geoid_county in BORDER_COUNTIES,
            "buildings": buildings, "osm_share": osm,
            "ms_only_share": ms_only, "other_share": other,
            "population": population, "pct_urban": _float(s.get("pct_urban")),
            "svi_overall": _float(s.get("svi_overall")),
            "ep_hisp": hisp.get(geoid), "density": density,
            "uhe_days": uhe_days,
            "usfs_building_count": ratio_denominator,
        })
    if len(records) != 6001:
        # This is the recorded official scored scope with nonzero Overture buildings.
        raise ValueError(f"expected 6001 scored tracts with buildings, got {len(records)}")
    border = [r for r in records if r["is_border"]]
    rest = [r for r in records if not r["is_border"]]
    # Match the steward's ``rural = pct_urban < 0.5`` convention: missing urbanity
    # compares false and therefore falls in the not-rural / urban-only complement.
    rest_urban = [r for r in rest if r["pct_urban"] is None or r["pct_urban"] >= 0.5]
    if (len(border), len(rest), len(rest_urban)) != (460, 5541, 4600):
        raise ValueError("group scope did not reproduce the frozen steward analysis")

    county_rows = []
    for fips, name in BORDER_COUNTIES.items():
        group = [r for r in border if r["county_fips"] == fips]
        if not group:
            raise ValueError(f"no scored tracts for border county {name}")
        vals = np.asarray([r["osm_share"] for r in group], dtype=float)
        rng = np.random.default_rng(1)
        draws = np.asarray([rng.choice(vals, len(vals)).mean() for _ in range(3000)])
        ratio = [r["buildings"] / r["usfs_building_count"] for r in group
                 if r["usfs_building_count"] is not None and r["usfs_building_count"] > 0]
        summary = summarize(group)
        county_rows.append({
            "county_fips": fips, "county": name, **summary,
            "osm_share_bootstrap_ci_95": [float(x) for x in np.percentile(draws, [2.5, 97.5])],
            "median_overture_to_provided_usfs_building_count_ratio_context_only": (
                float(np.median(ratio)) if ratio else None),
        })

    named = []
    for geoid in NAMED_GEOIDS:
        row = next((r for r in border if r["GEOID"] == geoid), None)
        is_zero_osm_example = geoid != "48061012002"
        if (row is None or row["buildings"] < 1500
                or (is_zero_osm_example and row["osm_share"] != 0.0)
                or (not is_zero_osm_example and row["osm_share"] >= 0.001)):
            raise ValueError(f"named tract failed its OSM-share / 1500-footprint check: {geoid}")
        named.append({"GEOID": geoid, "county": row["county_name"],
                      "overture_buildings": row["buildings"],
                      "osm_share": row["osm_share"],
                      "population": int(round(row["population"] or 0)),
                      "svi_overall": row["svi_overall"],
                      "microsoft_only_share": row["ms_only_share"],
                      "other_source_share": row["other_share"]})

    raw_ci = bootstrap_difference_ci([r["osm_share"] for r in border],
                                     [r["osm_share"] for r in rest])
    cluster_ci = cluster_bootstrap_difference(records)
    # All density values were calculated from provided population and land-area columns.
    for row in records:
        row["log_density"] = math.log1p(row["density"]) if row["density"] is not None and row["density"] >= 0 else None
    # Refit with the expected log-density covariate (the preceding placeholder call is not used).
    adjusted_urban_density = cluster_ols(records, ("pct_urban", "log_density"))
    adjusted_svi_hisp = cluster_ols(records, ("pct_urban", "log_density", "svi_overall", "ep_hisp"))

    result = {
        "analysis": "Rio Grande border-metro building source mix; descriptive discovery only",
        "source_script_adaptation": "Steward Fable deep-look s6.py, narrowed to source mix, group contrasts, county summaries, heat context, and the named examples",
        "scope": {"region": REGION, "official_sample_rows": len(geoids),
                  "scored_tracts_with_positive_overture_building_count": len(records),
                  "border_counties": [{"fips": f, "county": n} for f, n in BORDER_COUNTIES.items()]},
        "definitions": {
            "osm_share": "per-tract overture_building_osm_source_share; any OSM-touched source",
            "microsoft_only_share": "per-tract overture_building_ms_only_source_share",
            "other_source_share": "per-tract overture_building_other_source_share; source-level count reconciliation associates this border concentration with Google Open Buildings",
            "group_means": "unweighted means of tract-level source shares; source footprint count/share is weighted by overture_buildings",
            "population": "sum of provided pop_total for included tracts",
            "uhe": "unweighted mean of provided uhe_wbgtmax30_days_per_yr over covered tracts with nonmissing value",
            "density": "provided pop_total divided by provided ALAND in km^2; log1p for regression",
            "ratio_caveat": "provided Overture/USFS building-count ratio is contextual only; not a Microsoft component, hidden score, score proxy, or model feature",
        },
        "results": {
            "border_counties": summarize(border),
            "rest_of_texas_region": summarize(rest),
            "rest_urban_only": summarize(rest_urban),
            "raw_osm_share_difference_border_minus_rest": raw_ci,
            "county_cluster_osm_share_difference_border_minus_rest": cluster_ci,
            "adjusted_border_association_urban_density": adjusted_urban_density,
            "adjusted_border_association_plus_svi_hispanic": adjusted_svi_hisp,
            "border_county_table": county_rows,
            "named_low_osm_tracts_ge_1500_buildings": named,
            "source_count_reconciliation": {
                "border_other_source_footprints": summarize(border)["other_source_footprints_rounded"],
                "all_tx_google_open_buildings_source_memberships": int(global_datasets["google open buildings"]),
                "nonborder_other_source_footprints": summarize(rest)["other_source_footprints_rounded"],
                "all_tx_usgs_lidar_source_memberships": int(global_datasets["usgs lidar"]),
                "border_minus_google_count": summarize(border)["other_source_footprints_rounded"] - int(global_datasets["google open buildings"]),
                "nonborder_minus_usgs_lidar_count": summarize(rest)["other_source_footprints_rounded"] - int(global_datasets["usgs lidar"]),
                "interpretation": "near one-for-one source-count reconciliation supports (but does not by itself prove) a Google Open Buildings spill-over attribution; per-tract source share is the provided Overture source classification",
            },
        },
        "data_and_restrictions": {
            "removed_reference_layers_read": False,
            "sample_score_values_used": False,
            "public_scores_used": False,
            "candidate_artifacts_or_predictions_read": False,
            "svi_external_data_used_only_for_hispanic_control": True,
            "svi_source": {"name": "CDC/ATSDR SVI 2022", "url": SVI_URL,
                           "retrieved": "2026-10-27", "manifest": SVI_MANIFEST,
                           "manifest_sha256": None, "file_sha256": None,
                           "licence_terms": SVI_LICENSE_ASSESSMENT,
                           "statutory_basis_url": "https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title17-section105&num=0&edition=prelim"},
            "external_data_added_to_scored_predictions": False,
            "source_tags_are_provenance_not_correctness_or_community_activity": True,
        },
        "method": {"tract_bootstrap_replicates": 5000, "tract_bootstrap_seed": 0,
                   "county_bootstrap_replicates": 1000, "county_bootstrap_seed": 0,
                   "county_bootstrap_clusters": "GEOID first five characters",
                   "county_table_bootstrap_replicates": 3000,
                   "county_table_bootstrap_seed": 1,
                   "ols_covariance": "county-clustered sandwich SE with finite-sample correction",
                   "ols_density": "log1p(pop_total / (ALAND / 1,000,000))"},
        "analysis_script": {"path": "scripts/discovery/border_metros.py",
                            "sha256": sha256_file(Path(__file__).resolve())},
        "runtime": {"python": platform.python_version(), "numpy": np.__version__,
                    "pyarrow": pyarrow.__version__},
        "inputs": {},
    }
    workspace_inputs = {
        "official_sample": sample_path,
        "feature_cache": feature_path,
        "strata_table": strata_path,
        "cdc_svi_csv": svi_path,
        "source_provenance_report": source_report_path,
        "cdc_svi_manifest": workspace / SVI_MANIFEST,
    }
    for name, path in workspace_inputs.items():
        result["inputs"][name] = {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
    result["data_and_restrictions"]["svi_source"]["manifest_sha256"] = result["inputs"]["cdc_svi_manifest"]["sha256"]
    result["data_and_restrictions"]["svi_source"]["file_sha256"] = result["inputs"]["cdc_svi_csv"]["sha256"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.workspace, args.repo, args.out)
    print(json.dumps({"out": str(args.out), "border": result["results"]["border_counties"],
                      "rest": result["results"]["rest_of_texas_region"],
                      "county_cluster_ci": result["results"]["county_cluster_osm_share_difference_border_minus_rest"]["ci_95"]}, sort_keys=True))


if __name__ == "__main__":
    main()
