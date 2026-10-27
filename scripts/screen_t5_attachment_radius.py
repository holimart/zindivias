#!/usr/bin/env python3
"""Nested county-CV screen of T5 building-to-road attachment radii.

Recomputes the frozen T5 building-centroid/road-distance feature from provided
Overture geometry at 75, 150, 300, and 600 metres. In each outer held-county
fold, radius selection uses only leave-one-county-out folds among the outer
training counties. Outcome is the pinned frozen S1 estimator-disagreement
residual, not a target label or accuracy measure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
from audit_mx4p_conditional_innovation import read_emission  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
REGION_COUNTS = {"maricopa-az": 1593, "northern-ca": 591, "eastern-ok": 1192, "south-central-tx": 6003}
EXPECTED_N = 9379
RADIUS_CANDIDATES = (75, 150, 300, 600)
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261106
MIN_POOLED_DELTA_R2 = 0.002
MAX_SUPPORTED_REGION_DECLINE = 0.005
CONFIG_PATH = ROOT / "config/bias60-harness-preregistered.json"
CONFIG_SHA256 = "12a15e11cd0459299e407e5f1f7056fa16a6a8ddad5d7bfd826b20dbe476d558"
T5_ARTIFACT_SHA256 = "feb7af5722fabacf90ed8915c2842ba5c5edddad8bad5d75028277f88af80140"
T5_ARTIFACT_PATH = Path(os.environ.get("COMPETITION_WORKSPACE", ".")) / "ideas/T5.parquet"
S1_SHA256 = "437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
S1_PATH = Path(os.environ.get("COMPETITION_WORKSPACE", ".")) / "s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
S1_REPORT_PATH = ROOT / "reports/s1-disagreement-orthogonalized.json"
OUT_PATH = ROOT / "reports/t5-attachment-radius-screen.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def vector_sha256(geoids: Sequence[str], regions: Sequence[str], values: np.ndarray,
                  defined: np.ndarray) -> str:
    if not (len(geoids) == len(regions) == len(values) == len(defined)):
        raise ValueError("radius feature hash vectors are misaligned")
    payload = "\n".join(
        f"{g},{r},{int(ok)},{format(float(v), '.17g') if ok else ''}"
        for g, r, v, ok in zip(geoids, regions, values, defined)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mask_sha256(mask: np.ndarray) -> str:
    return hashlib.sha256(np.packbits(np.asarray(mask, dtype=np.uint8), bitorder="little").tobytes()).hexdigest()


def county_fips(geoid: str) -> str:
    value = str(geoid)
    if len(value) != 11 or not value.isdigit():
        raise ValueError("GEOID must contain eleven digits")
    return value[:5]


def far_fraction(assigned: np.ndarray, near_counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    assigned = np.asarray(assigned, dtype=np.int64)
    near_counts = np.asarray(near_counts, dtype=np.int64)
    if assigned.ndim != 1 or near_counts.shape != assigned.shape:
        raise ValueError("assigned and near-count vectors must be aligned")
    if np.any(assigned < 0) or np.any(near_counts < 0) or np.any(near_counts > assigned):
        raise ValueError("building distance counts are inconsistent")
    defined = assigned > 0
    value = np.zeros(len(assigned), dtype=np.float64)
    value[defined] = 1.0 - near_counts[defined] / assigned[defined]
    if not np.isfinite(value).all() or np.any((value < 0.0) | (value > 1.0)):
        raise ValueError("attachment fractions must be finite and bounded")
    return value, defined


def fit_one_term(train_y: np.ndarray, train_x: np.ndarray, test_x: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    y = np.asarray(train_y, dtype=np.float64)
    x = np.asarray(train_x, dtype=np.float64)
    held = np.asarray(test_x, dtype=np.float64)
    if x.ndim != 1 or held.ndim != 1 or y.shape != x.shape or len(y) < 3 or len(held) == 0:
        raise ValueError("fold needs aligned vectors, at least three training rows, and held-out rows")
    if not (np.isfinite(y).all() and np.isfinite(x).all() and np.isfinite(held).all()):
        raise ValueError("fold values must be finite")
    mean = float(x.mean())
    sd = float(x.std(ddof=0))
    scale = sd if sd > 0.0 else 1.0
    z = (x - mean) / scale
    zh = (held - mean) / scale
    design = np.column_stack((np.ones(len(y)), z))
    coef, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
    prediction = np.column_stack((np.ones(len(held)), zh)) @ coef
    return prediction, {
        "training_n": len(y), "heldout_n": len(held), "training_feature_mean": mean,
        "training_feature_sd": sd, "training_applied_scale": scale,
        "intercept": float(coef[0]), "standardized_slope": float(coef[1]), "design_rank": int(rank),
    }


def delta_r2(baseline_sse: float, model_sse: float) -> float:
    if not np.isfinite([baseline_sse, model_sse]).all() or baseline_sse <= 0.0:
        raise ValueError("baseline SSE must be finite and positive")
    return (baseline_sse - model_sse) / baseline_sse


def select_radius_inner(y: np.ndarray, x_by_radius: Mapping[int, np.ndarray],
                        counties: np.ndarray, train_mask: np.ndarray,
                        radii: Sequence[int] = RADIUS_CANDIDATES) -> dict[str, Any]:
    """Select by pooled inner county-held-out SSE, restricted to train_mask."""
    y = np.asarray(y, dtype=np.float64)
    counties = np.asarray(counties, dtype=object)
    train_mask = np.asarray(train_mask, dtype=bool)
    if y.ndim != 1 or counties.shape != y.shape or train_mask.shape != y.shape:
        raise ValueError("inner-CV arrays are misaligned")
    train_counties = sorted(set(counties[train_mask].tolist()))
    if len(train_counties) < 3:
        raise ValueError("nested inner CV requires at least three outer-training counties")
    radius_scores: dict[str, Any] = {}
    for radius in radii:
        x = np.asarray(x_by_radius[int(radius)], dtype=np.float64)
        if x.shape != y.shape or not np.isfinite(x[train_mask]).all():
            raise ValueError(f"invalid radius-{radius} feature vector")
        oof = np.full(len(y), np.nan, dtype=np.float64)
        for held in train_counties:
            test = train_mask & (counties == held)
            inner_train = train_mask & (counties != held)
            pred, _ = fit_one_term(y[inner_train], x[inner_train], x[test])
            oof[test] = pred
            baseline = float(y[test] @ y[test])
            model = float(np.sum((y[test] - pred) ** 2))
        if not np.isfinite(oof[train_mask]).all() or np.isfinite(oof[~train_mask]).any():
            raise AssertionError("inner OOF predictions do not exactly cover outer-training rows")
        baseline_sse = float(np.sum(y[train_mask] ** 2))
        model_sse = float(np.sum((y[train_mask] - oof[train_mask]) ** 2))
        radius_scores[str(radius)] = {
            "inner_county_folds": len(train_counties), "inner_heldout_counties_sha256": hashlib.sha256(
                "\\n".join(train_counties).encode("utf-8")).hexdigest(),
            "inner_training_n": int(train_mask.sum()), "baseline_sse": baseline_sse,
            "model_sse": model_sse, "delta_r2": delta_r2(baseline_sse, model_sse),
        }
    # maximize inner pooled ΔR²; ties deterministically prefer the smaller radius.
    selected = min((int(r) for r in radius_scores), key=lambda r: (-radius_scores[str(r)]["delta_r2"], r))
    return {"selected_radius_m": selected, "selection_rule": "maximum pooled inner county-held-out delta-R2; exact ties choose smaller radius",
            "inner_heldout_counties": train_counties, "inner_scores_by_radius": radius_scores}


def nested_outer_cv(y: np.ndarray, x_by_radius: Mapping[int, np.ndarray], regions: np.ndarray,
                    counties: np.ndarray, support: np.ndarray,
                    radii: Sequence[int] = RADIUS_CANDIDATES) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, int]]:
    n = len(y)
    prediction = np.full(n, np.nan, dtype=np.float64)
    records: list[dict[str, Any]] = []
    fold_counts: dict[str, int] = {}
    for region in REGIONS:
        region_support = support & (regions == region)
        county_ids = sorted(set(counties[region_support].tolist()))
        fold_counts[region] = len(county_ids)
        if len(county_ids) < 4:
            raise ValueError(f"{region}: nested county CV needs at least four supported counties")
        for held in county_ids:
            outer_test = region_support & (counties == held)
            outer_train = region_support & (counties != held)
            selection = select_radius_inner(y, x_by_radius, counties, outer_train, radii)
            chosen = selection["selected_radius_m"]
            x = np.asarray(x_by_radius[chosen], dtype=np.float64)
            pred, fit = fit_one_term(y[outer_train], x[outer_train], x[outer_test])
            prediction[outer_test] = pred
            baseline = float(y[outer_test] @ y[outer_test])
            model = float(np.sum((y[outer_test] - pred) ** 2))
            records.append({"region": region, "heldout_county_fips": held,
                            "heldout_n": int(outer_test.sum()), "training_n": int(outer_train.sum()),
                            "outer_training_county_count": len(county_ids) - 1,
                            "selected_radius_m": chosen, "inner_selection": selection,
                            "baseline_sse": baseline, "model_sse": model,
                            "delta_r2": delta_r2(baseline, model), "outer_fit": fit})
    if np.any(support & ~np.isfinite(prediction)) or np.any(~support & np.isfinite(prediction)):
        raise AssertionError("outer OOF prediction support mismatch")
    return prediction, records, fold_counts


def score_summary(y: np.ndarray, pred: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    target = np.asarray(y, dtype=np.float64)[mask]
    estimate = np.asarray(pred, dtype=np.float64)[mask]
    baseline = float(target @ target)
    model = float(np.sum((target - estimate) ** 2))
    return {"n": int(len(target)), "baseline_sse": baseline, "model_sse": model,
            "delta_r2": delta_r2(baseline, model)}


def county_bootstrap(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = {region: [] for region in REGIONS}
    for row in records:
        grouped[row["region"]].append(row)
    if any(not rows for rows in grouped.values()):
        raise ValueError("county bootstrap requires held-out counties in all regions")
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    pooled = np.empty(BOOTSTRAP_REPLICATES, dtype=np.float64)
    regional = {region: np.empty(BOOTSTRAP_REPLICATES, dtype=np.float64) for region in REGIONS}
    for b in range(BOOTSTRAP_REPLICATES):
        total_base = total_model = 0.0
        for region in REGIONS:
            rows = grouped[region]
            draws = rng.integers(0, len(rows), size=len(rows))
            base = sum(float(rows[i]["baseline_sse"]) for i in draws)
            model = sum(float(rows[i]["model_sse"]) for i in draws)
            regional[region][b] = delta_r2(base, model)
            total_base += base
            total_model += model
        pooled[b] = delta_r2(total_base, total_model)
    def ci(values: np.ndarray) -> list[float]:
        return [float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))]
    return {"replicates": BOOTSTRAP_REPLICATES, "seed": BOOTSTRAP_SEED,
            "resampling": "paired outer-held-county baseline/model SSE blocks; counties sampled with replacement within each official region",
            "pooled_delta_r2_ci_95": ci(pooled),
            "regional_delta_r2_ci_95": {region: ci(values) for region, values in regional.items()},
            "degenerate_replicates": 0}


def load_s1_residual(workspace: Path, official: list[tuple[str, str]]) -> tuple[np.ndarray, dict[str, str]]:
    path = workspace / "s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    if sha256_file(path) != S1_SHA256:
        raise ValueError("pinned frozen S1 residual hash mismatch")
    report = json.loads(S1_REPORT_PATH.read_text(encoding="utf-8"))
    if report["provenance"]["output_artifact"]["sha256"] != S1_SHA256:
        raise ValueError("frozen S1 report/artifact hash mismatch")
    table = pq.read_table(path, columns=["GEOID", "region", "crossfit_residual", "crossfit_joint_defined"])
    if table.num_rows != len(official):
        raise ValueError("frozen S1 residual row count mismatch")
    positions = {pair: i for i, pair in enumerate(official)}
    values = np.full(len(official), np.nan, dtype=np.float64)
    seen = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in positions or pair in seen or row["crossfit_joint_defined"] is not True or row["crossfit_residual"] is None:
            raise ValueError(f"unexpected, duplicate, or undefined frozen S1 residual row {pair}")
        values[positions[pair]] = float(row["crossfit_residual"])
        seen.add(pair)
    if seen != set(official) or not np.isfinite(values).all():
        raise ValueError("frozen S1 residual does not cover exact official IDs")
    return values, {"path": str(path), "sha256": S1_SHA256, "report_sha256": sha256_file(S1_REPORT_PATH)}


def derive_region_features(config: dict[str, Any], workspace: Path, region: str,
                           geoids: list[str]) -> tuple[dict[int, np.ndarray], np.ndarray, dict[str, Any]]:
    """Recompute nearest provided-road distances once and emit four radius features."""
    inputs = ProvidedInputs(config, workspace, region, geoids)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    tracts = shapely.transform(inputs.tract_geometries(), transformer.transform, interleaved=False)
    tract_tree = shapely.STRtree(tracts)

    road_path = inputs.layer_path("roads-unfiltered")
    road_file = pq.ParquetFile(inputs.filesystem.open_input_file(road_path))
    if "geometry" not in road_file.schema_arrow.names:
        raise ValueError(f"{region}: provided road layer lacks geometry")
    inputs.accessed["parquet_columns"].append(f"{region}-overture-roads-unfiltered.parquet:geometry")
    road_parts = []
    roads_read = null_roads = 0
    for group in range(road_file.metadata.num_row_groups):
        wkbs = road_file.read_row_group(group, columns=["geometry"])["geometry"].to_pylist()
        roads_read += len(wkbs)
        valid = [wkb for wkb in wkbs if wkb is not None]
        null_roads += len(wkbs) - len(valid)
        if valid:
            road_parts.append(shapely.transform(shapely.from_wkb(valid), transformer.transform, interleaved=False))
    if not road_parts:
        raise ValueError(f"{region}: provided road layer contains no valid geometry")
    roads = np.concatenate(road_parts)
    road_tree = shapely.STRtree(roads)

    assigned = np.zeros(len(geoids), dtype=np.int64)
    near_counts = {radius: np.zeros(len(geoids), dtype=np.int64) for radius in RADIUS_CANDIDATES}
    building_path = inputs.layer_path("buildings")
    building_file = pq.ParquetFile(inputs.filesystem.open_input_file(building_path))
    if "geometry" not in building_file.schema_arrow.names:
        raise ValueError(f"{region}: provided building layer lacks geometry")
    inputs.accessed["parquet_columns"].append(f"{region}-overture-buildings.parquet:geometry")
    buildings_read = null_buildings = assigned_buildings = 0
    for group in range(building_file.metadata.num_row_groups):
        wkbs = building_file.read_row_group(group, columns=["geometry"])["geometry"].to_pylist()
        buildings_read += len(wkbs)
        valid_indices = [i for i, wkb in enumerate(wkbs) if wkb is not None]
        null_buildings += len(wkbs) - len(valid_indices)
        if not valid_indices:
            continue
        polygons = shapely.from_wkb([wkbs[i] for i in valid_indices])
        points = shapely.transform(shapely.centroid(polygons), transformer.transform, interleaved=False)
        owners = tract_tree.query(points, predicate="within")
        if not owners.size:
            continue
        point_ids, tract_ids = owners
        assigned += np.bincount(tract_ids, minlength=len(assigned))
        assigned_buildings += int(len(tract_ids))
        matches, distances = road_tree.query_nearest(
            points[point_ids], max_distance=float(max(RADIUS_CANDIDATES)),
            return_distance=True, all_matches=False)
        if not matches.size:
            continue
        matched_tracts = tract_ids[matches[0]]
        distances = np.asarray(distances, dtype=np.float64)
        for radius in RADIUS_CANDIDATES:
            close = distances <= float(radius)
            if np.any(close):
                near_counts[radius] += np.bincount(matched_tracts[close], minlength=len(assigned))
    values: dict[int, np.ndarray] = {}
    defined: np.ndarray | None = None
    for radius in RADIUS_CANDIDATES:
        vector, current_defined = far_fraction(assigned, near_counts[radius])
        if defined is None:
            defined = current_defined
        elif not np.array_equal(defined, current_defined):
            raise AssertionError("radius scenarios must share assigned-building support")
        values[radius] = vector
    assert defined is not None
    return values, defined, {
        "provided_layer_paths": {"buildings": building_path, "roads-unfiltered": road_path},
        "provided_layer_row_counts": {"buildings": buildings_read, "roads-unfiltered": roads_read},
        "null_geometry_rows": {"buildings": null_buildings, "roads-unfiltered": null_roads},
        "assigned_building_centroids": assigned_buildings,
        "assignment": "centroid strictly within provided ACS tract polygon; points on boundaries are not assigned",
        "distance_crs": "EPSG:5070", "max_nearest_distance_searched_m": max(RADIUS_CANDIDATES),
        "read_columns": sorted(inputs.accessed["parquet_columns"]),
        "tract_count": len(geoids),
    }


def run(workspace: Path, report_path: Path) -> dict[str, Any]:
    if sha256_file(CONFIG_PATH) != CONFIG_SHA256:
        raise ValueError("frozen T5 harness configuration hash mismatch")
    config = load_config(CONFIG_PATH)
    seed = config["seeds"]["T5"]
    if seed["road_layer"] != "roads-unfiltered" or seed["building_layer"] != "buildings" or seed["distance_m"] != 150.0:
        raise ValueError("registered T5 geometry and 150-m baseline recipe changed")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    geoids = [g for g, _ in official]
    region_vector = np.asarray([r for _, r in official], dtype=object)
    if len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N or len(set(geoids)) != EXPECTED_N:
        raise ValueError("official scored GEOID universe differs from 9,379 unique IDs")
    if set(region_vector.tolist()) != set(REGIONS):
        raise ValueError("official region partition differs from frozen four-region scope")
    for region in REGIONS:
        if int(np.sum(region_vector == region)) != REGION_COUNTS[region]:
            raise ValueError(f"official sample count mismatch for {region}")

    workspace_t5_path = workspace / "ideas/T5.parquet"
    frozen_t5, frozen_t5_defined, frozen_t5_sha = read_emission(
        workspace_t5_path, T5_ARTIFACT_SHA256, "T5", "C", "covariate:buildings_over_150m_from_road", official)
    s1, s1_meta = load_s1_residual(workspace, official)
    radius_values: dict[int, np.ndarray] = {r: np.full(EXPECTED_N, np.nan, dtype=np.float64) for r in RADIUS_CANDIDATES}
    radius_defined = np.zeros(EXPECTED_N, dtype=bool)
    feature_meta: dict[str, Any] = {}
    for region in REGIONS:
        positions = np.flatnonzero(region_vector == region)
        regional_geoids = [geoids[i] for i in positions]
        local_values, local_defined, meta = derive_region_features(config, workspace, region, regional_geoids)
        for radius in RADIUS_CANDIDATES:
            radius_values[radius][positions] = local_values[radius]
        radius_defined[positions] = local_defined
        feature_meta[region] = {**meta, "official_ids": len(positions), "feature_defined_ids": int(local_defined.sum())}
    if not np.array_equal(radius_defined, frozen_t5_defined):
        raise ValueError("recomputed radius feature support differs from frozen 150m T5 mask")
    if not np.allclose(radius_values[150][radius_defined], frozen_t5[radius_defined], rtol=0.0, atol=1e-12):
        max_diff = float(np.max(np.abs(radius_values[150][radius_defined] - frozen_t5[radius_defined])))
        raise ValueError(f"recomputed 150m feature does not reconcile to frozen T5; max diff={max_diff}")
    for radius in RADIUS_CANDIDATES:
        if np.any(radius_defined & (~np.isfinite(radius_values[radius]) | (radius_values[radius] < 0) | (radius_values[radius] > 1))):
            raise ValueError(f"radius {radius} feature has invalid defined values")
        radius_values[radius][~radius_defined] = np.nan
    support = radius_defined & np.isfinite(s1)
    if int(support.sum()) != 9377:
        raise ValueError("T5/S1 paired support differs from expected fixed coverage")
    counties = np.asarray([county_fips(g) for g in geoids], dtype=object)
    prediction, outer_records, fold_counts = nested_outer_cv(s1, radius_values, region_vector, counties, support)
    pooled = score_summary(s1, prediction, support)
    regional = {region: score_summary(s1, prediction, support & (region_vector == region)) for region in REGIONS}
    bootstrap = county_bootstrap(outer_records)
    selected_counts = {str(radius): sum(row["selected_radius_m"] == radius for row in outer_records) for radius in RADIUS_CANDIDATES}
    selected_rows = {str(radius): sum(row["heldout_n"] for row in outer_records if row["selected_radius_m"] == radius) for radius in RADIUS_CANDIDATES}
    max_region_decline = max(max(0.0, -regional[r]["delta_r2"]) for r in REGIONS)
    acceptance = {
        "pooled_delta_r2_at_least_0_002": pooled["delta_r2"] >= MIN_POOLED_DELTA_R2,
        "county_bootstrap_ci_lower_strictly_above_zero": bootstrap["pooled_delta_r2_ci_95"][0] > 0.0,
        "no_supported_region_decline_gt_0_005": max_region_decline <= MAX_SUPPORTED_REGION_DECLINE,
        "maximum_supported_region_decline": max_region_decline,
        "screen_pass": bool(pooled["delta_r2"] >= MIN_POOLED_DELTA_R2 and
                             bootstrap["pooled_delta_r2_ci_95"][0] > 0.0 and
                             max_region_decline <= MAX_SUPPORTED_REGION_DECLINE),
    }
    feature_hashes = {str(r): vector_sha256(geoids, [str(x) for x in region_vector], radius_values[r], radius_defined)
                      for r in RADIUS_CANDIDATES}
    feature_hashes_by_region = {}
    for region in REGIONS:
        idx = np.flatnonzero(region_vector == region)
        feature_hashes_by_region[region] = {str(r): vector_sha256(
            [geoids[i] for i in idx], [region] * len(idx), radius_values[r][idx], radius_defined[idx])
            for r in RADIUS_CANDIDATES}
    report = {
        "schema_version": 1,
        "screen": "Nested inner-county selection of T5 building-to-road attachment radius; outer county-held-out S1 disagreement residual association",
        "design": {
            "feature": "fraction of provided Overture building centroids assigned strictly within an ACS tract whose nearest provided Overture road is farther than radius",
            "radius_candidates_m": list(RADIUS_CANDIDATES),
            "baseline_radius_m": 150,
            "inner_selection": "For every outer held county within region, choose the radius with minimum pooled inner leave-one-county-out squared error among remaining outer-training counties; equivalently maximize pooled inner delta-R2 against zero correction. Exact ties choose smaller radius.",
            "outer_validation": "Within each official region, hold out each supported five-digit county FIPS once; fit intercept plus standardized linear T5 term on all other supported counties using training-only feature mean/population SD.",
            "metric": "Pooled and regional outer OOF delta-R2=(zero-correction S1 residual SSE - selected-radius OOF residual SSE)/zero-correction SSE.",
            "bootstrap": "2,000 paired outer held-county baseline/model SSE block resamples, counties sampled with replacement within each official region.",
            "outcome_interpretation": "Frozen S1 cross-fitted estimator-disagreement residual only; not labels, target score, accuracy, or promotion evidence.",
            "undefined_support": "Radius feature is undefined only when no provided building centroid is assigned to the tract; all candidate radii share this support.",
            "radius_selection_leakage_control": "Each outer held county is excluded from both inner radius selection and outer model fitting; outer outcome is used only once for evaluation.",
        },
        "scope": {
            "official_ids": EXPECTED_N, "official_geoids_unique": len(set(geoids)),
            "paired_residual_support": int(support.sum()), "undefined_attachment_feature_ids": int((~radius_defined).sum()),
            "region_official_counts": {r: int(np.sum(region_vector == r)) for r in REGIONS},
            "region_paired_support_counts": {r: int(np.sum(support & (region_vector == r))) for r in REGIONS},
            "county_fold_counts": fold_counts, "exact_official_alignment": True,
            "sample_score_placeholders_read": False,
        },
        "feature_coverage": {
            "values_and_mask_sha256_by_radius": feature_hashes,
            "values_and_mask_sha256_by_region_and_radius": feature_hashes_by_region,
            "defined_mask_sha256": mask_sha256(radius_defined),
            "150m_recomputation_matches_frozen_T5": True,
            "150m_max_abs_difference": float(np.max(np.abs(radius_values[150][radius_defined] - frozen_t5[frozen_t5_defined]))),
            "source_data_by_region": feature_meta,
        },
        "selected_radius": {
            "outer_county_fold_selection_counts": selected_counts,
            "outer_heldout_tract_counts": selected_rows,
            "outer_fold_selected_radius_by_county": [{"region": row["region"], "heldout_county_fips": row["heldout_county_fips"],
                                                       "selected_radius_m": row["selected_radius_m"]} for row in outer_records],
            "description": "Nested selected radius may differ by outer held county; no globally fixed radius was selected using outer outcomes.",
        },
        "measure_after": {
            "pooled_outer_oof": pooled,
            "regional_outer_oof": regional,
            "paired_county_block_bootstrap": bootstrap,
            "outer_county_folds": outer_records,
        },
        "thresholds": {
            "pooled_delta_r2_minimum_inclusive": MIN_POOLED_DELTA_R2,
            "pooled_county_bootstrap_ci_lower_strictly_above": 0.0,
            "minimum_supported_region_delta_r2_inclusive": -MAX_SUPPORTED_REGION_DECLINE,
        },
        "acceptance": acceptance,
        "provenance": {
            "frozen_S1_residual": s1_meta,
            "frozen_T5_150m_feature": {"path": str(workspace_t5_path), "sha256": frozen_t5_sha,
                                       "source_script": "src/bias60/ideas/t.py", "registered_recipe_distance_m": 150.0},
            "harness_config": {"path": "config/bias60-harness-preregistered.json", "sha256": sha256_file(CONFIG_PATH)},
            "radius_feature_definition_source": "provided Overture buildings and roads-unfiltered geometry; ACS tract polygon boundaries as supplied by the existing T5 feature pipeline",
            "bootstrap_seed": BOOTSTRAP_SEED, "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "analysis_script_sha256": sha256_file(Path(__file__).resolve()),
        },
        "safety": {
            "target_labels_read": False, "removed_or_equivalent_reference_layers_read": False,
            "external_data_read": False, "leaderboard_or_scores_accessed": False,
            "heldout_radius_tuning": False, "candidate_artifacts_modified": False, "uploads": 0,
            "candidate_integration_authorized": False,
        },
        "interpretation": "Nested radius selection was evaluated only on outer-held counties against a frozen S1 disagreement residual. The screen does not measure hidden-target accuracy. Retain no T5 correction unless every preregistered acceptance gate passes.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ.get("COMPETITION_WORKSPACE", ".")))
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report = run(args.workspace, args.out)
    summary = {"report": str(args.out), "official_ids": report["scope"]["official_ids"],
               "paired_support": report["scope"]["paired_residual_support"],
               "selected_radius_counts": report["selected_radius"]["outer_county_fold_selection_counts"],
               "pooled": report["measure_after"]["pooled_outer_oof"],
               "regional": report["measure_after"]["regional_outer_oof"],
               "pooled_ci": report["measure_after"]["paired_county_block_bootstrap"]["pooled_delta_r2_ci_95"],
               "acceptance": report["acceptance"]}
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
