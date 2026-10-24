#!/usr/bin/env python3
"""Build a preregistered density-context sensitivity from provided challenge features.

Only official sample GEOIDs, provided Overture building counts, ACS/strata
population and land area, urbanity class for descriptive grouping, and provided
ACS tract polygons (GEOID/geometry only) are used. Neighborhoods are calculated
within each official region and exclude the focal tract. No sample scores,
removed reference values, labels, or external layers are read.
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
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/density-context-preregistered.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
REGIONS = tuple(CONFIG["scope"]["regions"])
TEMPLATE_ONLY_REGION = CONFIG["scope"]["template_only_region"]
TEMPLATE_PATH = ROOT / CONFIG["inputs"]["template_ids"]["path"]
C2_PATH = ROOT / CONFIG["prior_candidate"]["path"]
C2_SHA256 = CONFIG["prior_candidate"]["sha256"]
SCORE_COLUMN = CONFIG["prior_candidate"]["score_column"]
SCORE_FIELDS = ["prior_coverage_gap_score", "proposed_density_adjustment",
                "candidate_delta", "density_context_coverage_gap_score"]
STRATA_COLUMNS = ["GEOID", "ALAND", "pop_total", "ur_class"]
OVERTURE_COLUMNS = ["GEOID", "overture_buildings"]
ACS_GEOMETRY_COLUMNS = ["GEOID", "geometry"]
BUCKET = "us-west-2.opendata.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def digest_ids(ids: list[str]) -> str:
    digest = hashlib.sha256()
    for geoid in ids:
        digest.update(geoid.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def git_file_commit(path: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%H", "--", str(path.relative_to(ROOT))],
            text=True).strip()
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


def official_ids(path: Path) -> list[str]:
    """Read only GEOID values from the authoritative sample; never access score fields."""
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if not header or "GEOID" not in header:
            raise ValueError(f"official sample has no GEOID column: {path}")
        index = header.index("GEOID")
        ids = [row[index] for row in reader]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"empty or duplicate official sample IDs: {path}")
    return ids


def read_template(path: Path) -> tuple[list[str], dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"GEOID", "region"}.issubset(reader.fieldnames):
            raise ValueError("score-free template projection must contain GEOID and region")
        rows = list(reader)
    ids = [row["GEOID"] for row in rows]
    if len(ids) != 9794 or len(set(ids)) != len(ids):
        raise ValueError(f"expected 9,794 unique template GEOIDs, got {len(ids)}")
    return ids, {row["GEOID"]: row["region"] for row in rows}


def read_prior(path: Path, official_set: set[str]) -> dict[str, float]:
    if sha256_file(path) != C2_SHA256:
        raise ValueError("frozen C2 artifact SHA-256 differs from preregistered value")
    scores: dict[str, float] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if not header or "GEOID" not in header or SCORE_COLUMN not in header:
            raise ValueError("frozen C2 artifact lacks GEOID/coverage_gap_score")
        geoid_i, score_i = header.index("GEOID"), header.index(SCORE_COLUMN)
        total_rows = 0
        for row in reader:
            total_rows += 1
            geoid = row[geoid_i]
            if geoid not in official_set:
                continue
            score = float(row[score_i])
            if geoid in scores or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("frozen C2 has duplicate official IDs or invalid prediction")
            scores[geoid] = score
    if total_rows != 9794 or set(scores) != official_set:
        raise ValueError("frozen C2 does not have the expected full-template rows and official IDs")
    return scores


def row_fingerprint(columns: list[str], rows: list[dict[str, Any]]) -> str:
    return digest_json({"columns": columns, "rows": [[row.get(column) for column in columns]
                                                      for row in rows]})


def finite_nonnegative(value: Any, label: str) -> float:
    if value is None:
        raise ValueError(f"missing required {label}")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return number


def unit(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("density support must be finite")
    return min(1.0, max(0.0, float(value)))


def density_support(density: float | None, scale: float) -> float | None:
    if density is None:
        return None
    if not math.isfinite(density) or density < 0:
        raise ValueError("density must be finite and nonnegative")
    return unit(math.log1p(density) / math.log1p(scale))


def aggregate_neighbors(target: int, raw_neighbors: list[int], values: list[float | None]
                        ) -> float | None:
    """Mean only neighboring values; self is excluded even if returned by the tree."""
    selected = [float(values[index]) for index in raw_neighbors
                if index != target and values[index] is not None]
    return float(np.mean(selected)) if selected else None


def summarize(values: list[float] | np.ndarray) -> dict[str, Any]:
    array = np.asarray(values, dtype=np.float64)
    if not len(array):
        return {"n": 0, "mean": None, "variance_population": None,
                "std_population": None, "min": None, "q10": None,
                "median": None, "q90": None, "max": None}
    if not np.isfinite(array).all():
        raise ValueError("summary contains nonfinite values")
    q10, median, q90 = np.quantile(array, [0.1, 0.5, 0.9])
    return {"n": int(len(array)), "mean": float(array.mean()),
            "variance_population": float(array.var()), "std_population": float(array.std()),
            "min": float(array.min()), "q10": float(q10), "median": float(median),
            "q90": float(q90), "max": float(array.max())}


def load_strata(workspace: Path, region: str, ids: list[str]
                ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
    table = pq.read_table(path, columns=STRATA_COLUMNS)
    rows = table.to_pylist()
    row_map = {str(row["GEOID"]): row for row in rows}
    if len(row_map) != len(rows) or not set(ids).issubset(row_map):
        raise ValueError(f"strata table is missing or duplicates official IDs for {region}")
    selected = {geoid: row_map[geoid] for geoid in ids}
    for geoid, row in selected.items():
        finite_nonnegative(row["ALAND"], f"ALAND:{geoid}")
        finite_nonnegative(row["pop_total"], f"pop_total:{geoid}")
    fingerprint_rows = [selected[geoid] for geoid in ids]
    return selected, {
        "path": str(path.relative_to(workspace)),
        "selected_columns": STRATA_COLUMNS,
        "table_rows": len(rows),
        "official_rows_used": len(ids),
        "other_rows_not_used": len(rows) - len(ids),
        "official_projection_sha256": row_fingerprint(STRATA_COLUMNS, fingerprint_rows),
    }


def load_overture(workspace: Path, region: str, ids: list[str]
                   ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    path = workspace / "gap-estimator" / "features" / f"{region}-overture-tract-summary.parquet"
    table = pq.read_table(path, columns=OVERTURE_COLUMNS)
    rows = table.to_pylist()
    row_ids = [str(row["GEOID"]) for row in rows]
    if row_ids != ids:
        raise ValueError(f"Overture feature cache ID order/coverage mismatch for {region}")
    for row in rows:
        row["GEOID"] = str(row["GEOID"])
        finite_nonnegative(row["overture_buildings"], f"overture_buildings:{row['GEOID']}")
    return {row["GEOID"]: row for row in rows}, {
        "path": str(path.relative_to(workspace)),
        "selected_columns": OVERTURE_COLUMNS,
        "rows": len(rows),
        "official_rows_matched_exact_order": True,
        "official_projection_sha256": row_fingerprint(OVERTURE_COLUMNS, rows),
    }


def load_acs_geometry(fs: pafs.S3FileSystem, region: str, ids: list[str]
                      ) -> tuple[np.ndarray, dict[str, Any]]:
    key = f"{BUCKET}/reference/{region}/{region}-census-acs-housing.parquet"
    info = fs.get_file_info(key)
    pf = pq.ParquetFile(fs.open_input_file(key))
    table = pf.read(columns=ACS_GEOMETRY_COLUMNS)
    rows = table.to_pylist()
    ids_all = [str(row["GEOID"]) for row in rows]
    if len(ids_all) != len(set(ids_all)):
        raise ValueError(f"duplicate GEOIDs in provided ACS geometry for {region}")
    by_id = {str(row["GEOID"]): row["geometry"] for row in rows}
    if not set(ids).issubset(by_id):
        raise ValueError(f"provided ACS geometry lacks official IDs in {region}")
    selected_wkb = [by_id[geoid] for geoid in ids]
    if any(wkb is None for wkb in selected_wkb):
        raise ValueError(f"null provided ACS geometry for official GEOID in {region}")
    geometries = shapely.from_wkb(selected_wkb)
    if np.any(shapely.is_empty(geometries)) or np.any(~shapely.is_valid(geometries)):
        raise ValueError(f"empty/invalid provided ACS polygon in {region}")
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    projected = shapely.transform(geometries, transformer.transform, interleaved=False)
    points = shapely.centroid(projected)
    if np.any(shapely.is_empty(points)) or np.any(~shapely.is_valid(points)):
        raise ValueError(f"invalid projected tract centroid in {region}")
    xs, ys = shapely.get_x(points), shapely.get_y(points)
    if not np.isfinite(xs).all() or not np.isfinite(ys).all():
        raise ValueError(f"nonfinite projected centroid in {region}")
    centroid_rows = [[geoid, float(x), float(y)] for geoid, x, y in zip(ids, xs, ys)]
    geometry_hash = hashlib.sha256()
    for geoid, wkb in zip(ids, selected_wkb):
        geometry_hash.update(geoid.encode("utf-8"))
        geometry_hash.update(b"\0")
        geometry_hash.update(wkb)
        geometry_hash.update(b"\n")
    return points, {
        "source_uri": f"s3://{key}",
        "object_bytes": info.size,
        "source_rows": pf.metadata.num_rows,
        "columns_read": ACS_GEOMETRY_COLUMNS,
        "official_geometries_used": len(ids),
        "nonofficial_geometry_rows_used": 0,
        "official_wkb_projection_sha256": geometry_hash.hexdigest(),
        "projected_centroids_epsg5070_sha256": digest_json(centroid_rows),
    }


def raw_neighborhoods(points: np.ndarray, radii: list[int]) -> dict[int, list[list[int]]]:
    tree = shapely.STRtree(points)
    neighborhoods: dict[int, list[list[int]]] = {}
    for radius in radii:
        per_target = []
        for target, point in enumerate(points):
            indexes = tree.query(point, predicate="dwithin", distance=float(radius))
            neighbors = sorted({int(index) for index in indexes})
            if target not in neighbors:
                raise AssertionError("STRtree omitted focal point from its own radius query")
            per_target.append(neighbors)
        neighborhoods[radius] = per_target
    return neighborhoods


def compute_region(workspace: Path, fs: pafs.S3FileSystem, region: str,
                   ids: list[str], prior_scores: dict[str, float]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    strata, strata_input = load_strata(workspace, region, ids)
    overture, overture_input = load_overture(workspace, region, ids)
    points, geometry_input = load_acs_geometry(fs, region, ids)
    records: list[dict[str, Any]] = []
    population_supports: list[float | None] = []
    building_supports: list[float | None] = []
    populations: list[float] = []
    areas: list[float] = []
    buildings: list[float] = []
    urbanities: list[str] = []
    for geoid in ids:
        land = finite_nonnegative(strata[geoid]["ALAND"], f"ALAND:{geoid}")
        population = finite_nonnegative(strata[geoid]["pop_total"], f"pop_total:{geoid}")
        building_count = finite_nonnegative(overture[geoid]["overture_buildings"],
                                            f"overture_buildings:{geoid}")
        area_km2 = land / 1_000_000.0
        if area_km2 > 0:
            pop_density = population / area_km2
            building_density = building_count / area_km2
        else:
            pop_density = building_density = None
        p_support = density_support(pop_density, float(CONFIG["density_features"]["population_scale"]))
        b_support = density_support(building_density, float(CONFIG["density_features"]["building_scale"]))
        population_supports.append(p_support)
        building_supports.append(b_support)
        populations.append(population)
        areas.append(land)
        buildings.append(building_count)
        ur_class = strata[geoid]["ur_class"]
        urbanities.append(str(ur_class) if ur_class not in (None, "") else "unclassified")

    radii = [int(value) for value in CONFIG["neighborhood"]["radii_meters"]]
    neighborhoods = raw_neighborhoods(points, radii)
    region_records = []
    invariance: dict[str, Any] = {f"{radius}m": {"rows_checked": 0,
                                                  "max_abs_change_after_self_perturbation": 0.0}
                                  for radius in radii}
    support_by_radius: dict[int, dict[str, list[float | None]]] = {}
    neighbor_counts: dict[int, list[int]] = {}
    for radius in radii:
        raw = neighborhoods[radius]
        neighbor_counts[radius] = [sum(index != target for index in indexes)
                                   for target, indexes in enumerate(raw)]
        support_by_radius[radius] = {
            "population": [aggregate_neighbors(i, indexes, population_supports)
                           for i, indexes in enumerate(raw)],
            "buildings": [aggregate_neighbors(i, indexes, building_supports)
                          for i, indexes in enumerate(raw)],
        }
        for metric, values in (("population", population_supports), ("buildings", building_supports)):
            max_change = 0.0
            checked = 0
            for target, indexes in enumerate(raw):
                original = aggregate_neighbors(target, indexes, values)
                perturbed = list(values)
                perturbed[target] = 987654.321
                changed = aggregate_neighbors(target, indexes, perturbed)
                if original is None:
                    if changed is not None:
                        raise AssertionError("self-perturbation changed a missing neighborhood summary")
                else:
                    if changed is None:
                        raise AssertionError("self-perturbation removed a neighborhood summary")
                    max_change = max(max_change, abs(original - changed))
                checked += 1
            invariance[f"{radius}m"]["rows_checked"] += checked
            invariance[f"{radius}m"]["max_abs_change_after_self_perturbation"] = max(
                invariance[f"{radius}m"]["max_abs_change_after_self_perturbation"], max_change)

    for position, geoid in enumerate(ids):
        context_features: dict[str, float] = {}
        feature_missing: dict[str, int] = {}
        for radius in radii:
            suffix = f"{radius // 1000}km"
            for metric, output_name in (("population", f"population_support_{suffix}"),
                                        ("buildings", f"building_support_{suffix}")):
                value = support_by_radius[radius][metric][position]
                feature_missing[output_name] = int(value is None)
                context_features[output_name] = 0.5 if value is None else unit(value)
        observed = [support_by_radius[radius][metric][position]
                    for radius in radii for metric in ("population", "buildings")
                    if support_by_radius[radius][metric][position] is not None]
        context_defined = bool(observed)
        context_support = float(np.mean(observed)) if context_defined else 1.0
        proposal = (float(CONFIG["candidate_adjustment"]["scale"]) *
                    (1.0 - context_support)) if context_defined else 0.0
        prior = prior_scores[geoid]
        adjusted = min(1.0, max(0.0, prior + proposal))
        region_records.append({
            "GEOID": geoid,
            "region": region,
            "urbanity": urbanities[position],
            "prior_score": prior,
            "population_density": (populations[position] / (areas[position] / 1_000_000.0)
                                   if areas[position] > 0 else None),
            "building_density": (buildings[position] / (areas[position] / 1_000_000.0)
                                 if areas[position] > 0 else None),
            "supports": context_features,
            "feature_missing": feature_missing,
            "neighbor_count": {f"{radius // 1000}km": neighbor_counts[radius][position]
                               for radius in radii},
            "context_support": unit(context_support),
            "context_support_missing": int(not context_defined),
            "proposed_adjustment": proposal,
            "candidate_delta": adjusted - prior,
            "adjusted_score": adjusted,
        })
    for radius in radii:
        invariance[f"{radius}m"]["self_exclusion_verified"] = (
            invariance[f"{radius}m"]["max_abs_change_after_self_perturbation"] <= 1e-15)
        invariance[f"{radius}m"]["metrics_checked_per_row"] = 2

    density_caps = CONFIG["density_features"]
    source_coverage = {
        "official_rows": len(ids),
        "overture_building_count_available": len(buildings),
        "overture_building_count_missing": 0,
        "population_available": len(populations),
        "population_missing": 0,
        "aland_available": len(areas),
        "aland_missing": 0,
        "positive_aland_rows": int(sum(area > 0 for area in areas)),
        "zero_aland_rows": int(sum(area == 0 for area in areas)),
        "urbanity_class_missing": int(sum(value == "unclassified" for value in urbanities)),
        "valid_population_density_rows": int(sum(value is not None for value in population_supports)),
        "valid_overture_building_density_rows": int(sum(value is not None for value in building_supports)),
    }
    inputs = {
        "strata": strata_input,
        "overture_feature_cache": overture_input,
        "provided_acs_geometry": geometry_input,
        "region_neighbor_domain": "exact official GEOID list for this region only",
    }
    diagnostics = {
        "rows": len(ids),
        "source_feature_coverage": source_coverage,
        "input_hashes": inputs,
        "tract_density_support_moments_before_neighborhood": {
            "population_density_persons_per_km2": summarize([
                float(row["population_density"]) for row in region_records
                if row["population_density"] is not None]),
            "overture_building_records_per_km2": summarize([
                float(row["building_density"]) for row in region_records
                if row["building_density"] is not None]),
            "population_support": summarize([float(value) for value in population_supports if value is not None]),
            "building_support": summarize([float(value) for value in building_supports if value is not None]),
            "fixed_scales": {"population_density_cap_persons_per_km2": density_caps["population_scale"],
                             "building_density_cap_records_per_km2": density_caps["building_scale"]},
        },
        "neighborhood_feature_moments": {},
        "neighbor_count_by_radius": {},
        "leave_one_tract_out_invariance": invariance,
    }
    for radius in radii:
        suffix = f"{radius // 1000}km"
        diagnostics["neighbor_count_by_radius"][suffix] = summarize(neighbor_counts[radius])
        for metric in ("population", "buildings"):
            name = f"{metric}_support_{suffix}"
            values = [support_by_radius[radius][metric][i] for i in range(len(ids))]
            observed = [float(value) for value in values if value is not None]
            diagnostics["neighborhood_feature_moments"][name] = {
                "observed": summarize(observed),
                "missing_rows": len(ids) - len(observed),
                "missing_share": (len(ids) - len(observed)) / len(ids),
                "filled_for_candidate": summarize([0.5 if value is None else float(value) for value in values]),
                "neighbor_available_count": summarize([
                    sum(index != i and (population_supports if metric == "population" else building_supports)[index] is not None
                        for index in neighborhoods[radius][i]) for i in range(len(ids))]),
            }
    return region_records, diagnostics


def summarize_candidate(records: list[dict[str, Any]]) -> dict[str, Any]:
    prior = [row["prior_score"] for row in records]
    adjusted = [row["adjusted_score"] for row in records]
    delta = [row["candidate_delta"] for row in records]
    proposal = [row["proposed_adjustment"] for row in records]
    changed = [abs(a - b) > float(CONFIG["candidate_adjustment"]["row_changed_tolerance"])
               for a, b in zip(prior, adjusted)]
    output = {
        "rows": len(records),
        "prior_prediction": summarize(prior),
        "adjusted_prediction": summarize(adjusted),
        "candidate_delta": summarize(delta),
        "proposed_adjustment": summarize(proposal),
        "prior_prediction_variance": float(np.var(prior)) if prior else None,
        "adjusted_prediction_variance": float(np.var(adjusted)) if adjusted else None,
        "rows_changed": int(sum(changed)),
        "fraction_rows_changed": float(np.mean(changed)) if changed else None,
        "context_support": summarize([row["context_support"] for row in records]),
        "rows_with_any_context": int(sum(not row["context_support_missing"] for row in records)),
        "rows_missing_all_context": int(sum(row["context_support_missing"] for row in records)),
        "max_absolute_candidate_delta": max((abs(value) for value in delta), default=0.0),
        "by_urbanity": {},
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        groups.setdefault(row["urbanity"], []).append(row)
    for urbanity, group in sorted(groups.items()):
        gprior = [row["prior_score"] for row in group]
        gadj = [row["adjusted_score"] for row in group]
        gdelta = [row["candidate_delta"] for row in group]
        output["by_urbanity"][urbanity] = {
            "rows": len(group),
            "prior_prediction": summarize(gprior),
            "adjusted_prediction": summarize(gadj),
            "candidate_delta": summarize(gdelta),
            "prior_prediction_variance": float(np.var(gprior)),
            "adjusted_prediction_variance": float(np.var(gadj)),
            "rows_changed": int(sum(abs(a-b) > float(CONFIG["candidate_adjustment"]["row_changed_tolerance"])
                                     for a, b in zip(gprior, gadj))),
            "context_feature_missing_rows": {
                name: int(sum(row["feature_missing"][name] for row in group))
                for name in group[0]["feature_missing"]
            },
        }
    return output


def run(workspace: Path, candidate_path: Path, manifest_path: Path, report_path: Path) -> dict[str, Any]:
    template_ids, template_region = read_template(TEMPLATE_PATH)
    official_by_region = {region: official_ids(
        workspace / "samples" / f"{region}-sample-submission.csv") for region in REGIONS}
    official_region: dict[str, str] = {}
    for region, ids in official_by_region.items():
        for geoid in ids:
            if geoid in official_region:
                raise ValueError(f"official GEOID appears in multiple regions: {geoid}")
            official_region[geoid] = region
    official_ids_ordered = [geoid for region in REGIONS for geoid in official_by_region[region]]
    if len(official_ids_ordered) != int(CONFIG["scope"]["official_id_count"]):
        raise ValueError("official sample count differs from preregistered count")
    template_only_ids = [geoid for geoid in template_ids if geoid not in official_region]
    if len(template_only_ids) != 415 or not set(official_ids_ordered).issubset(template_ids):
        raise ValueError("score-free template does not equal official IDs plus 415 template-only IDs")
    if any(template_region[geoid] != region for geoid, region in official_region.items()):
        raise ValueError("template region metadata conflicts with official region sample")
    template_only = [geoid for geoid in template_ids if geoid not in official_region]
    if any(template_region[geoid] != TEMPLATE_ONLY_REGION for geoid in template_only):
        raise ValueError("template-only rows are not all in the preregistered eastern-WA region")
    prior_scores = read_prior(C2_PATH, set(official_region))
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")

    official_records: list[dict[str, Any]] = []
    inputs_by_region = {}
    diagnostics_by_region = {}
    for region in REGIONS:
        region_records, diagnostics = compute_region(
            workspace, fs, region, official_by_region[region], prior_scores)
        official_records.extend(region_records)
        inputs_by_region[region] = diagnostics["input_hashes"]
        diagnostics_by_region[region] = diagnostics

    record_by_id = {row["GEOID"]: row for row in official_records}
    if len(record_by_id) != len(official_records) or set(record_by_id) != set(official_region):
        raise ValueError("computed density candidate does not exactly cover official GEOIDs")
    template_records: list[dict[str, Any]] = []
    for geoid in template_ids:
        if geoid in record_by_id:
            template_records.append(record_by_id[geoid])
        else:
            template_records.append({
                "GEOID": geoid,
                "region": TEMPLATE_ONLY_REGION,
                "urbanity": "template-only-no-features",
                "prior_score": 0.0,
                "supports": {
                    "population_support_5km": 0.5, "building_support_5km": 0.5,
                    "population_support_20km": 0.5, "building_support_20km": 0.5,
                },
                "feature_missing": {
                    "population_support_5km": 1, "building_support_5km": 1,
                    "population_support_20km": 1, "building_support_20km": 1,
                },
                "neighbor_count": {"5km": 0, "20km": 0},
                "context_support": 1.0,
                "context_support_missing": 1,
                "proposed_adjustment": 0.0,
                "candidate_delta": 0.0,
                "adjusted_score": 0.0,
            })

    candidate_columns = [
        "GEOID", "region", "urbanity", "prior_candidate_id", "prior_coverage_gap_score",
        "neighbor_count_5km", "population_support_5km", "population_support_5km_missing",
        "building_support_5km", "building_support_5km_missing",
        "neighbor_count_20km", "population_support_20km", "population_support_20km_missing",
        "building_support_20km", "building_support_20km_missing",
        "density_context_support", "density_context_support_missing",
        "proposed_density_adjustment", "candidate_delta", "density_context_coverage_gap_score",
    ]
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    with candidate_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=candidate_columns, lineterminator="\n")
        writer.writeheader()
        for row in template_records:
            supports = row["supports"]
            missing = row["feature_missing"]
            writer.writerow({
                "GEOID": row["GEOID"],
                "region": row["region"],
                "urbanity": row["urbanity"],
                "prior_candidate_id": CONFIG["prior_candidate"]["candidate_id"],
                "prior_coverage_gap_score": format(row["prior_score"], ".12g"),
                "neighbor_count_5km": row["neighbor_count"]["5km"],
                "population_support_5km": format(supports["population_support_5km"], ".12g"),
                "population_support_5km_missing": missing["population_support_5km"],
                "building_support_5km": format(supports["building_support_5km"], ".12g"),
                "building_support_5km_missing": missing["building_support_5km"],
                "neighbor_count_20km": row["neighbor_count"]["20km"],
                "population_support_20km": format(supports["population_support_20km"], ".12g"),
                "population_support_20km_missing": missing["population_support_20km"],
                "building_support_20km": format(supports["building_support_20km"], ".12g"),
                "building_support_20km_missing": missing["building_support_20km"],
                "density_context_support": format(row["context_support"], ".12g"),
                "density_context_support_missing": row["context_support_missing"],
                "proposed_density_adjustment": format(row["proposed_adjustment"], ".12g"),
                "candidate_delta": format(row["candidate_delta"], ".12g"),
                "density_context_coverage_gap_score": format(row["adjusted_score"], ".12g"),
            })

    # Validate exact platform-template ordering and all score/support bounds.
    with candidate_path.open("r", newline="", encoding="utf-8") as stream:
        candidate_rows = list(csv.DictReader(stream))
    if [row["GEOID"] for row in candidate_rows] != template_ids:
        raise ValueError("candidate GEOIDs do not exactly match platform template order")
    for row in candidate_rows:
        for column in ("prior_coverage_gap_score", "density_context_coverage_gap_score",
                       "density_context_support", "population_support_5km", "building_support_5km",
                       "population_support_20km", "building_support_20km"):
            value = float(row[column])
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"invalid [0,1] candidate value {column} for {row['GEOID']}")
        for column in ("proposed_density_adjustment", "candidate_delta"):
            value = float(row[column])
            if not math.isfinite(value) or not 0 <= value <= 0.05 + 1e-12:
                raise ValueError(f"invalid adjustment {column} for {row['GEOID']}")

    official_candidate_summary = summarize_candidate(official_records)
    per_region: dict[str, Any] = {}
    for region in REGIONS:
        rows = [row for row in official_records if row["region"] == region]
        summary = summarize_candidate(rows)
        summary["feature_coverage"] = diagnostics_by_region[region]["source_feature_coverage"]
        summary["tract_density_support_moments_before_neighborhood"] = diagnostics_by_region[region][
            "tract_density_support_moments_before_neighborhood"]
        summary["neighborhood_feature_moments"] = diagnostics_by_region[region]["neighborhood_feature_moments"]
        summary["neighbor_count_by_radius"] = diagnostics_by_region[region]["neighbor_count_by_radius"]
        summary["leave_one_tract_out_invariance"] = diagnostics_by_region[region][
            "leave_one_tract_out_invariance"]
        per_region[region] = summary

    template_only_records = [row for row in template_records if row["region"] == TEMPLATE_ONLY_REGION]
    prior_projection_hash = digest_json([[row["GEOID"], format(row["prior_score"], ".12g")]
                                        for row in official_records])
    template_hash = sha256_file(TEMPLATE_PATH)
    candidate_sha = sha256_file(candidate_path)
    manifest = {
        "candidate_id": CONFIG["candidate_id"],
        "candidate_csv": str(candidate_path.relative_to(ROOT)),
        "candidate_csv_sha256": candidate_sha,
        "candidate_rows": len(candidate_rows),
        "candidate_columns": candidate_columns,
        "template_id_order_sha256": digest_ids(template_ids),
        "template_projection_sha256": template_hash,
        "official_id_count": len(official_region),
        "official_id_order_sha256": digest_json({region: digest_ids(official_by_region[region])
                                                  for region in REGIONS}),
        "prior_candidate_id": CONFIG["prior_candidate"]["candidate_id"],
        "prior_candidate_sha256": sha256_file(C2_PATH),
        "prior_official_prediction_projection_sha256": prior_projection_hash,
        "config_path": str(CONFIG_PATH.relative_to(ROOT)),
        "config_sha256": sha256_file(CONFIG_PATH),
        "inputs_by_region": inputs_by_region,
        "scope": {"official_rows": len(official_records), "template_only_rows": len(template_only_records),
                  "template_only_region": TEMPLATE_ONLY_REGION,
                  "template_order_exact": True, "official_regions_only_for_neighbors": True},
        "all_scores_and_supports_finite_bounded_0_1": True,
        "maximum_absolute_candidate_delta": max(abs(row["candidate_delta"]) for row in template_records),
        "no_upload": True,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
                             encoding="utf-8")
    manifest_sha = sha256_file(manifest_path)

    baseline_by_region = {region: per_region[region]["prior_prediction"] for region in REGIONS}
    baseline_by_urbanity: dict[str, Any] = {}
    for region in REGIONS:
        region_rows = [row for row in official_records if row["region"] == region]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in region_rows:
            groups.setdefault(row["urbanity"], []).append(row)
        baseline_by_urbanity[region] = {
            urbanity: {"rows": len(group), "prior_prediction": summarize(
                [row["prior_score"] for row in group])}
            for urbanity, group in sorted(groups.items())
        }
    report = {
        "analysis": "Provided-feature, region-local density context adjustment sensitivity; no labels or accuracy test",
        "candidate_id": CONFIG["candidate_id"],
        "candidate_csv": str(candidate_path.relative_to(ROOT)),
        "candidate_csv_sha256": candidate_sha,
        "candidate_manifest": str(manifest_path.relative_to(ROOT)),
        "candidate_manifest_sha256": manifest_sha,
        "candidate_rows": len(candidate_rows),
        "code_commit_at_run": git_commit(),
        "script_sha256_at_run": sha256_file(Path(__file__)),
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                         "pyarrow": __import__("pyarrow").__version__,
                         "shapely": shapely.__version__},
        "preregistration": {"config_path": str(CONFIG_PATH.relative_to(ROOT)),
                            "config_sha256": sha256_file(CONFIG_PATH),
                            "config_commit": git_file_commit(CONFIG_PATH),
                            "registered_before_density_feature_measurement": True},
        "method": CONFIG,
        "measure_before": {
            "prior_candidate": CONFIG["prior_candidate"],
            "prior_official_prediction_projection_sha256": prior_projection_hash,
            "current_candidate_moments_global_official": official_candidate_summary["prior_prediction"],
            "current_candidate_moments_by_region": baseline_by_region,
            "current_candidate_moments_by_region_and_urbanity": baseline_by_urbanity,
            "existing_provided_feature_coverage_by_region": {
                region: per_region[region]["feature_coverage"] for region in REGIONS
            },
            "selected_columns_only": {
                "overture": OVERTURE_COLUMNS,
                "acs_strata": STRATA_COLUMNS,
                "provided_acs_geometry": ACS_GEOMETRY_COLUMNS,
                "official_sample": ["GEOID"],
                "template_projection": ["GEOID", "region"],
                "prior_candidate": ["GEOID", SCORE_COLUMN],
            },
        },
        "measure_after": {
            "official_scope": official_candidate_summary,
            "per_region": per_region,
            "template_only": {
                "region": TEMPLATE_ONLY_REGION,
                "rows": len(template_only_records),
                "prior_and_candidate_score": 0.0,
                "candidate_delta": summarize([row["candidate_delta"] for row in template_only_records]),
                "all_density_context_missing": True,
                "scored_target_claim": False,
            },
            "all_template_rows": summarize_candidate(template_records),
            "leave_one_tract_out_invariance_all_regions": all(
                all(details["self_exclusion_verified"] and
                    details["max_abs_change_after_self_perturbation"] <= 1e-15
                    for details in diagnostics_by_region[region]["leave_one_tract_out_invariance"].values())
                for region in REGIONS),
            "exact_candidate_ids_and_bounds": {
                "exact_template_count": len(candidate_rows) == 9794,
                "exact_template_order_once": [row["GEOID"] for row in candidate_rows] == template_ids,
                "official_ids_exactly_covered": set(record_by_id) == set(official_region),
                "all_scores_and_supports_finite_bounded_0_1": True,
                "maximum_absolute_candidate_delta": max(abs(row["candidate_delta"]) for row in template_records),
                "maximum_adjustment_at_most_0_05": max(abs(row["candidate_delta"]) for row in template_records) <= 0.05 + 1e-12,
            },
        },
        "input_hashes_by_region": inputs_by_region,
        "template_projection_sha256": template_hash,
        "restrictions": {
            "sample_score_placeholders_read_or_used": False,
            "labels_or_target_proxies_used": False,
            "removed_reference_layers_or_equivalents_read": False,
            "external_reference_layers_used": False,
            "leaderboard_information_or_score_fitting_used": False,
            "upload_or_submission_made": False,
            "accuracy_claim": False,
        },
        "limitations": CONFIG["limitations"],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
                           encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--candidate", type=Path,
                        default=ROOT / "reports/candidates/density_context_candidate.csv")
    parser.add_argument("--manifest", type=Path,
                        default=ROOT / "reports/candidates/density_context_candidate_manifest.json")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/density-context-candidate.json")
    args = parser.parse_args()
    result = run(args.workspace.resolve(), args.candidate.resolve(), args.manifest.resolve(), args.out.resolve())
    measured = result["measure_after"]["official_scope"]
    print(f"PASS density context: {result['candidate_rows']} template rows; "
          f"{measured['rows_changed']}/{measured['rows']} official rows changed; "
          f"max |delta|={measured['max_absolute_candidate_delta']:.6f}; "
          f"self-leakage={result['measure_after']['leave_one_tract_out_invariance_all_regions']}")
    print(f"Candidate SHA-256 {result['candidate_csv_sha256']}; "
          f"manifest SHA-256 {result['candidate_manifest_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
