#!/usr/bin/env python3
"""Discovery-only associations of public external layers with frozen gap proxies."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import shapefile
from shapely.geometry import shape as make_shape
from shapely.ops import transform, unary_union
from pyproj import Transformer

REGIONS = ["maricopa-az", "northern-ca", "eastern-ok", "south-central-tx"]
STATE_FIPS = {"maricopa-az": "04", "northern-ca": "06", "eastern-ok": "40", "south-central-tx": "48"}
GEOMETRY_STATES = sorted(set(STATE_FIPS.values()) | {"35"})  # Official Maricopa sample includes one NM GEOID.
CONTROLS = ["pct_urban", "svi_overall", "ruca_primary", "tribal_pct"]
CANDIDATES = {
    "A1_physical_equal": ["coverage_gap_score", "transport_gap", "building_gap", "poi_gap"],
    "A2_spectral": ["coverage_gap_score"],
    "A3_dawid_skene": ["coverage_gap_score"],
    "V2_model": ["coverage_gap_score"],
}
LAYERS = ["cdc_svi_2022", "usda_ruca_2020", "census_pdb_2026_broadband", "census_aiannh_2020_overlap"]
SEED = 20261027
BOOTSTRAPS = 1000


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def number(x):
    if x is None:
        return None
    s = str(x).strip()
    if not s or s.lower() in {"nan", "null", "none", "-999", "-999.0", "-9999", "-9999.0"}:
        return None
    try:
        z = float(s)
    except ValueError:
        return None
    if not math.isfinite(z) or z < -100:
        return None
    return z


def read_csv_map(path: Path, encoding: str, key: str, value: str, scale: float = 1.0):
    out = {}
    with path.open("r", encoding=encoding, newline="") as f:
        reader = csv.DictReader(f)
        if key not in (reader.fieldnames or []) or value not in (reader.fieldnames or []):
            raise ValueError(f"{path}: required columns {key}, {value} absent")
        for row in reader:
            geoid = str(row[key]).strip()
            if len(geoid) == 11 and geoid.isdigit():
                v = number(row[value])
                if v is not None:
                    out[geoid] = v * scale
    return out


def load_inputs(repo: Path, workspace: Path, manifest: dict):
    id_region = {}
    for region in REGIONS:
        p = workspace / "samples" / f"{region}-sample-submission.csv"
        with p.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                g = row["GEOID"].strip()
                if g in id_region:
                    raise ValueError(f"duplicate official GEOID {g}")
                id_region[g] = region
    if len(id_region) != 9379:
        raise ValueError(f"expected exactly 9379 official GEOIDs, got {len(id_region)}")

    control = {}
    for region in REGIONS:
        p = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        table = pq.read_table(p, columns=["GEOID", *CONTROLS])
        for row in table.to_pylist():
            g = str(row["GEOID"])
            if g in id_region:
                if g in control:
                    raise ValueError(f"duplicate strata GEOID {g}")
                control[g] = [number(row[c]) for c in CONTROLS]
    if set(control) != set(id_region):
        raise ValueError(f"strata join mismatch missing={len(set(id_region)-set(control))}")

    extroot = workspace / "external-discovery-2026-10-27"
    files = {}
    for ds in manifest["datasets"]:
        for item in ds["files"]:
            p = extroot / ds["id"] / item["name"]
            if not p.exists():
                raise FileNotFoundError(p)
            actual = sha256(p)
            if actual != item["sha256"]:
                raise ValueError(f"hash mismatch {p}: {actual} != {item['sha256']}")
            files[f"{ds['id']}/{item['name']}"] = {"path": str(p), "sha256": actual, "bytes": p.stat().st_size}

    svi = read_csv_map(extroot / "cdc-svi-2022" / "SVI_2022_US.csv", "utf-8-sig", "FIPS", "RPL_THEMES")
    ruca = read_csv_map(extroot / "usda-ers-ruca-2020" / "ruca-2020-census-tracts.csv", "latin1", "TractFIPS20", "PrimaryRUCA")
    pdb = read_csv_map(extroot / "census-pdb-2026" / "pdb2026tr.csv", "utf-8-sig", "GIDTR", "pct_HHD_No_Internet_ACS_20_24", scale=0.01)
    external = {
        "cdc_svi_2022": svi,
        "usda_ruca_2020": ruca,
        "census_pdb_2026_broadband": pdb,
    }
    aiannh_share, geo_provenance = build_aiannh_share(id_region, workspace, extroot)
    external["census_aiannh_2020_overlap"] = aiannh_share

    outcomes = {name: {} for name in CANDIDATES}
    candidate_files = {}
    for candidate, fields in CANDIDATES.items():
        p = repo / "reports" / "candidates" / f"{candidate}.csv"
        candidate_files[candidate] = {"path": str(p), "sha256": sha256(p)}
        with p.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                g = row["GEOID"].strip()
                if g in id_region:
                    for field in fields:
                        val = number(row[field])
                        if val is None:
                            raise ValueError(f"missing candidate score {candidate}.{field} at {g}")
                        outcomes[candidate].setdefault(field, {})[g] = val
        for field in fields:
            if set(outcomes[candidate][field]) != set(id_region):
                raise ValueError(f"candidate GEOID mismatch {candidate}.{field}")
    source_counts = {}
    for layer, values in external.items():
        matched = set(values) & set(id_region)
        source_counts[layer] = {
            "selected_exposure_nonmissing_official_geoids": len(matched),
            "official_geoids": len(id_region),
            "selected_exposure_missing_official_geoids": len(id_region) - len(matched),
            "selected_exposure_nonmissing_by_region": {
                r: sum(g in matched and reg == r for g, reg in id_region.items()) for r in REGIONS
            },
        }
    return id_region, control, external, outcomes, files, candidate_files, source_counts, geo_provenance


def build_aiannh_share(id_region: dict, workspace: Path, extroot: Path):
    ai_dir = extroot / "census-tiger-aiannh-2020"
    ai_base = ai_dir / "tl_2020_us_aiannh"
    ai_reader = shapefile.Reader(str(ai_base), encoding="utf-8")
    ai_fields = [x[0] for x in ai_reader.fields[1:]]
    ai_geoms = []
    for sr in ai_reader.iterShapeRecords():
        g = make_shape(sr.shape.__geo_interface__)
        minx, miny, maxx, maxy = g.bounds
        if maxx < -126 or minx > -93 or maxy < 24 or miny > 43:
            continue
        ai_geoms.append(g)
    project = Transformer.from_crs("EPSG:4269", "EPSG:5070", always_xy=True).transform
    ai_union = unary_union([transform(project, g) for g in ai_geoms])

    tmp = extroot / "_tract-geometry-extract"
    tmp.mkdir(exist_ok=True)
    tract_shapes = {}
    for state in GEOMETRY_STATES:
        zpath = workspace / "lab-prerequisites" / f"tl_2024_{state}_tract.zip"
        if not zpath.exists():
            raise FileNotFoundError(zpath)
        sub = tmp / state
        sub.mkdir(exist_ok=True)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(sub)
        shp = next(sub.glob("*.shp"))
        reader = shapefile.Reader(str(shp), encoding="utf-8")
        fields = [x[0] for x in reader.fields[1:]]
        geoid_col = fields.index("GEOID")
        for sr in reader.iterShapeRecords():
            rec = list(sr.record)
            g = str(rec[geoid_col])
            if g not in id_region:
                continue
            geom = transform(project, make_shape(sr.shape.__geo_interface__))
            if geom.is_empty or geom.area <= 0:
                continue
            share = geom.intersection(ai_union).area / geom.area
            tract_shapes[g] = min(1.0, max(0.0, float(share)))
    missing_geometry = sorted(set(id_region) - set(tract_shapes))
    provenance = {
        "tract_geometry_missing_official_geoids": missing_geometry,
        "tract_geometry_matched_official_geoids": len(tract_shapes),
        "operation": "area-weighted intersection in EPSG:5070 of 2024 Census tract polygons with the dissolved 2020 Census AIANNH polygon union; missing tract geometry remains missing, never coded zero",
        "aiannh_source": "external-discovery-2026-10-27/census-tiger-aiannh-2020/tl_2020_us_aiannh.zip",
        "aiannh_sha256": sha256(ai_dir / "tl_2020_us_aiannh.zip"),
        "tract_geometry_sources": {},
        "aiannh_features_intersecting_study_envelope": len(ai_geoms),
        "union_used_to_avoid_double_counting_overlapping_aiannh_polygons": True,
        "tracts_with_positive_aiannh_area_share": sum(v > 0 for v in tract_shapes.values()),
    }
    for state in GEOMETRY_STATES:
        p = workspace / "lab-prerequisites" / f"tl_2024_{state}_tract.zip"
        prov = p.with_suffix(p.suffix + ".provenance.json")
        provenance["tract_geometry_sources"][state] = json.loads(prov.read_text())
        if provenance["tract_geometry_sources"][state]["sha256"] != sha256(p):
            raise ValueError(f"tract source hash mismatch {p}")
    return tract_shapes, provenance


def region_design(regions):
    region_names = REGIONS[1:]
    rd = np.column_stack([[1.0 if r == name else 0.0 for r in regions] for name in region_names])
    return np.column_stack([np.ones(len(regions)), rd])


def design(controls, regions, exposure, train_idx, include_exposure):
    # A region intercept plus training-fold standardized covariates. Complete cases only.
    train_idx = np.asarray(train_idx, dtype=int)
    c = np.asarray(controls, dtype=float)
    e = np.asarray(exposure, dtype=float)
    means = np.mean(c[train_idx], axis=0)
    sds = np.std(c[train_idx], axis=0)
    sds[sds == 0] = 1.0
    cz = (c - means) / sds
    em = float(np.mean(e[train_idx]))
    es = float(np.std(e[train_idx])) or 1.0
    ez = (e - em) / es
    pieces = [region_design(regions), cz]
    if include_exposure:
        pieces.append(ez[:, None])
    return np.column_stack(pieces)


def fit_beta(X, y, groups):
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    res = y - X @ beta
    xtx_inv = np.linalg.pinv(X.T @ X)
    meat = np.zeros((X.shape[1], X.shape[1]))
    for g in np.unique(groups):
        m = groups == g
        score = X[m].T @ res[m]
        meat += np.outer(score, score)
    cov = xtx_inv @ meat @ xtx_inv
    j = X.shape[1] - 1
    se = math.sqrt(max(0.0, float(cov[j, j])))
    z = float(beta[j] / se) if se > 0 else 0.0
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return beta, se, p


def bh_adjust(pvals):
    p = np.asarray(pvals, dtype=float)
    order = np.argsort(p)
    q = np.ones_like(p)
    running = 1.0
    m = len(p)
    for rank0 in range(m - 1, -1, -1):
        idx = order[rank0]
        running = min(running, p[idx] * m / (rank0 + 1))
        q[idx] = running
    return q


def partial_residual_correlation(Xbase, exposure, y, groups):
    be = np.linalg.lstsq(Xbase, exposure, rcond=None)[0]
    by = np.linalg.lstsq(Xbase, y, rcond=None)[0]
    er = exposure - Xbase @ be
    yr = y - Xbase @ by
    raw_sd = float(np.std(exposure))
    residual_sd = float(np.std(er))
    ratio = residual_sd / raw_sd if raw_sd > 0 else 0.0
    if residual_sd <= max(1e-12, 1e-10 * raw_sd) or np.std(yr) <= 1e-12:
        return None, None, 1.0, ratio
    ez = er / residual_sd
    yz = yr / np.std(yr)
    Xr = np.column_stack([np.ones(len(ez)), ez])
    beta, se, p = fit_beta(Xr, yz, groups)
    return float(np.clip(beta[-1], -1.0, 1.0)), se, p, ratio


def county_boot_partial_ci(Xbase, exposure, y, regions, counties, reps=BOOTSTRAPS, seed=SEED):
    groups = np.unique(counties)
    by_region_groups = {r: [g for g in groups if g.startswith(r + "|")] for r in REGIONS}
    if any(len(x) == 0 for x in by_region_groups.values()):
        raise ValueError("every region must contribute at least one county")
    stats = {}
    for g in groups:
        m = counties == g
        x = Xbase[m]
        yy = y[m]
        ee = exposure[m]
        stats[g] = (x.T @ x, x.T @ yy, x.T @ ee, float(yy @ yy), float(ee @ ee), float(yy @ ee))
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(reps):
        xx = np.zeros((Xbase.shape[1], Xbase.shape[1]))
        xy = np.zeros(Xbase.shape[1])
        xe = np.zeros(Xbase.shape[1])
        yy = ee = ye = 0.0
        for region in REGIONS:
            gs = by_region_groups[region]
            draw = rng.choice(gs, size=len(gs), replace=True)
            counts = defaultdict(int)
            for g in draw:
                counts[g] += 1
            for g, count in counts.items():
                gx, gy, ge, gyy, gee, gye = stats[g]
                xx += count * gx
                xy += count * gy
                xe += count * ge
                yy += count * gyy
                ee += count * gee
                ye += count * gye
        inv = np.linalg.pinv(xx)
        syy = yy - float(xy @ inv @ xy)
        see = ee - float(xe @ inv @ xe)
        sye = ye - float(xy @ inv @ xe)
        if syy > 1e-12 and see > 1e-12:
            vals.append(float(np.clip(sye / math.sqrt(syy * see), -1.0, 1.0)))
    if len(vals) < reps * 0.8:
        return None, 1.0
    ci = [float(x) for x in np.quantile(vals, [0.025, 0.975])]
    positive = sum(v >= 0 for v in vals)
    negative = sum(v <= 0 for v in vals)
    p = min(1.0, 2.0 * (min(positive, negative) + 1) / (len(vals) + 1))
    return ci, float(p)


def grouped_oof_delta_r2(controls, regions, exposure, y, counties, seed=SEED, folds=5):
    # Deterministic, region-stratified assignment of whole counties to folds.
    rng = np.random.default_rng(seed)
    fold_for = {}
    for r in REGIONS:
        gs = sorted(set(counties[np.asarray(regions) == r]))
        gs = list(np.asarray(gs)[rng.permutation(len(gs))])
        for i, g in enumerate(gs):
            fold_for[g] = i % folds
    pred0 = np.full(len(y), np.nan)
    pred1 = np.full(len(y), np.nan)
    for fold in range(folds):
        test = np.asarray([fold_for[g] == fold for g in counties])
        train = ~test
        if test.sum() == 0 or train.sum() == 0:
            continue
        X0 = design(controls, regions, exposure, np.flatnonzero(train), False)
        X1 = design(controls, regions, exposure, np.flatnonzero(train), True)
        b0 = np.linalg.lstsq(X0[train], y[train], rcond=None)[0]
        b1 = np.linalg.lstsq(X1[train], y[train], rcond=None)[0]
        pred0[test] = X0[test] @ b0
        pred1[test] = X1[test] @ b1
    if np.isnan(pred0).any() or np.isnan(pred1).any():
        raise ValueError("incomplete grouped OOF predictions")
    tss = float(np.sum((y - np.mean(y)) ** 2))
    r2_0 = 1.0 - float(np.sum((y - pred0) ** 2)) / tss
    r2_1 = 1.0 - float(np.sum((y - pred1) ** 2)) / tss
    return {"folds": folds, "group_unit": "region|county", "baseline_oof_r2": r2_0, "layer_added_oof_r2": r2_1, "delta_oof_r2": r2_1 - r2_0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    repo = args.repo.resolve()
    workspace = args.workspace.resolve()
    out = args.out or (repo / "reports" / "discovery-external-layers.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = workspace / "external-discovery-2026-10-27" / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    id_region, ctrlmap, extmaps, outcomes, raw_files, candidate_files, source_counts, geo_provenance = load_inputs(repo, workspace, manifest)

    records = []
    for layer_idx, layer in enumerate(LAYERS):
        extmap = extmaps[layer]
        for cand, fields in CANDIDATES.items():
            for outcome_field in fields:
                outcome_map = outcomes[cand][outcome_field]
                geos = sorted(g for g in id_region if g in extmap and g in ctrlmap and g in outcome_map and all(v is not None for v in ctrlmap[g]))
                if not geos:
                    raise ValueError(f"no complete records for {layer} {cand}.{outcome_field}")
                regs = np.asarray([id_region[g] for g in geos])
                ctrls = np.asarray([ctrlmap[g] for g in geos], dtype=float)
                exposure = np.asarray([extmap[g] for g in geos], dtype=float)
                raw_y = np.asarray([outcome_map[g] for g in geos], dtype=float)
                # Standardize outcome with its analytic-sample SD; external slope is SD(score)/SD(exposure).
                ys = (raw_y - np.mean(raw_y)) / (np.std(raw_y) or 1.0)
                counties = np.asarray([f"{r}|{g[:5]}" for r, g in zip(regs, geos)])
                Xregion = region_design(regs)
                marginal_r, _, _, _ = partial_residual_correlation(Xregion, exposure, ys, counties)
                marginal_ci, marginal_p = county_boot_partial_ci(Xregion, exposure, ys, regs, counties, seed=SEED + layer_idx * 100 + len(records))
                Xbase = design(ctrls, regs, exposure, np.arange(len(geos)), False)
                conditional_r, se, _, unique_exposure_sd_ratio = partial_residual_correlation(Xbase, exposure, ys, counties)
                if conditional_r is None:
                    conditional_ci, conditional_p = None, 1.0
                else:
                    conditional_ci, conditional_p = county_boot_partial_ci(Xbase, exposure, ys, regs, counties, seed=SEED + 10000 + layer_idx * 100 + len(records))
                # Region-specific marginal correlations provide geographic direction replication.
                regional = {}
                for region in REGIONS:
                    ix = np.flatnonzero(regs == region)
                    if len(ix) < 5:
                        regional[region] = {"n": int(len(ix)), "region_adjusted_r": None}
                        continue
                    rr, _, _, _ = partial_residual_correlation(np.ones((len(ix), 1)), exposure[ix], ys[ix], counties[ix])
                    regional[region] = {"n": int(len(ix)), "region_adjusted_r": rr}
                oof = grouped_oof_delta_r2(ctrls, regs, exposure, ys, counties, seed=SEED + layer_idx, folds=5)
                records.append({
                    "layer": layer,
                    "outcome": f"{cand}.{outcome_field}",
                    "n": len(geos),
                    "region_adjusted_partial_correlation": marginal_r,
                    "region_adjusted_county_bootstrap_95_ci": marginal_ci,
                    "region_adjusted_county_bootstrap_p": marginal_p,
                    "provided_control_residual_partial_correlation": conditional_r,
                    "residual_to_raw_exposure_sd_ratio": unique_exposure_sd_ratio,
                    "conditional_nonestimable_reason": "external exposure is exactly represented by region and supplied controls" if conditional_r is None else None,
                    "conditional_county_cluster_robust_se": se,
                    "conditional_county_bootstrap_95_ci": conditional_ci,
                    "conditional_county_bootstrap_p_for_bh": conditional_p,
                    "region_adjusted_correlations": regional,
                    "regions_matching_pooled_marginal_sign": sum(1 for d in regional.values() if d["region_adjusted_r"] is not None and marginal_r is not None and d["region_adjusted_r"] * marginal_r > 0),
                    "grouped_county_holdout": oof,
                })
    qvals = bh_adjust([r["conditional_county_bootstrap_p_for_bh"] for r in records])
    for rec, q in zip(records, qvals):
        rec["bh_q_value_28_tests"] = float(q)
    reports = {
        "schema_version": 1,
        "analysis_id": "discovery-external-layers-2026-10-27",
        "status": "discovery-only; descriptive associations of external context with frozen provided-data proxies; not target-accuracy evidence",
        "preregistration": {"path": "config/discovery-external-layers-preregistered.json", "sha256": sha256(repo / "config" / "discovery-external-layers-preregistered.json")},
        "method": {
            "official_geoid_count": len(id_region),
            "region_counts": {r: sum(v == r for v in id_region.values()) for r in REGIONS},
            "official_geoids_with_state_prefix_outside_region_label": sorted(g for g, r in id_region.items() if g[:2] != STATE_FIPS[r]),
            "base_model": "region fixed effects plus provided pct_urban, svi_overall, ruca_primary and tribal_pct",
            "primary_effect": "region-adjusted partial correlation of each external exposure with frozen proxy scores, with county-cluster percentile CI; descriptive, not causal",
            "residual_screen": "conditional partial correlation after residualizing both variables on region fixed effects plus provided pct_urban, svi_overall, ruca_primary and tribal_pct; near-duplicates are reported not estimable",
            "conditional_p_values": "two-sided add-one sign-frequency from the county-cluster bootstrap; BH applied to these 28 conditional residual-screen p-values; a non-estimable exposure receives p=1",  
            "holdout": "5-fold region-stratified group assignment; entire state|county clusters held out; OOF delta R2 compares baseline controls with same model plus one external layer",
            "uncertainty": f"{BOOTSTRAPS} county cluster bootstrap draws, resampling counties with replacement within each region; percentile CI",
            "multiplicity": "Benjamini-Hochberg across all 28 layer-by-outcome conditional county-bootstrap tests; non-identifiable conditional exposure residuals are assigned p=1",  
            "limitations": ["all outcomes are label-free provided-data estimates, not measured organizer scores", "county clustering does not remove spatial dependence between neighboring counties", "four study regions do not support national generalization", "external covariates are not causal effects", "the overall-score candidate components may share inputs with provided controls", "dataset pages did not state a dataset-specific licence; the separate federal public-domain assessment is subject to third-party exceptions"],
        },
        "external_sources": manifest["datasets"],
        "failed_or_skipped_sources": manifest.get("failed_or_skipped", []),
        "source_access_notes": [
            "Four differently phrased/prefixed web searches were denied by per-lane capability policy during source discovery; analysis used the steward-staged data and official source URLs recorded in its manifest.",
            "FEMA NRI and FCC national broadband access outcomes are retained from the acquisition manifest; they were not substituted with unrelated layers.",
        ],
        "license_assessment": {
            "assessment": "The publisher agencies are U.S. federal agencies; these government-produced datasets are treated as U.S. government works in the public domain under 17 U.S.C. Section 105, subject to any third-party material exceptions. The fetched dataset pages did not state a dataset-specific license, so this is a documented statutory assessment rather than a quoted dataset license.",
            "legal_reference": "17 U.S.C. Section 105",
            "legal_reference_url": "https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title17-section105&num=0&edition=prelim",
            "per_dataset": [
                {"id": d["id"], "publisher": d["publisher"], "public_domain_basis": "U.S. federal agency-produced dataset; treated as a U.S. government work under 17 U.S.C. Section 105, subject to third-party exceptions", "publisher_page_dataset_specific_license": d.get("licence_terms")}
                for d in manifest["datasets"]
            ],
            "caveat": "Not legal advice; check for third-party exceptions before redistribution. Raw source data remain in the authorized workspace and are not committed.",
        },
        "acquisition_manifest": {
            "path": str(manifest_path),
            "sha256": sha256(manifest_path),
            "sha256sums_path": str(manifest_path.parent / "SHA256SUMS"),
            "sha256sums_sha256": sha256(manifest_path.parent / "SHA256SUMS"),
        },
        "source_files_verified": raw_files,
        "manifest_join_coverage": {d["id"]: d.get("join_coverage") for d in manifest["datasets"]},
        "tract_geometry_provenance": geo_provenance,
        "source_join_counts": source_counts,
        "candidate_files_read_only": candidate_files,
        "candidate_outputs_modified": False,
        "external_data_used_in_scored_candidates": False,
        "removed_reference_layers_used": False,
        "tests": records,
    }
    out.write_text(json.dumps(reports, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"report": str(out), "official_geoids": len(id_region), "tests": len(records), "layers": len(LAYERS), "rows_by_layer": source_counts}, indent=2))


if __name__ == "__main__":
    main()
