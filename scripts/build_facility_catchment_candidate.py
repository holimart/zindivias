#!/usr/bin/env python3
"""Build a fixed, analysis-only Overture facility-catchment sensitivity.

The scored-domain rows are the four authoritative official samples. Catchment
facilities are selected only from the provided regional Overture POI objects,
using bbox centers and the shared exact category contract. The same-region
provided ACS polygons assign those points and define official tract centroids;
only GEOID/geometry are read from ACS. A fixed bounded spatial uplift is
applied to the existing pooled logistic facility-miss prior for 1/5/10 km
sensitivities. No labels, sample score placeholders, removed references,
external facility lists, or leaderboard information are read.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

from audit_poi_categories import (  # noqa: E402
    BUCKET,
    CORE_SETS,
    _bbox_centers,
    assign_points,
    category_tags,
    read_sample_geoids,
)
from estimate_gaps import (  # noqa: E402
    FACILITY_MISS_PRIORS,
    expected_facility_miss_gap_v2,
    facility_reference_presence_probability_v2,
    urban_fraction,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/facility-catchment-preregistered.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
REGIONS = tuple(CONFIG["scope"]["regions"])
TYPES = ("fire", "ems", "schools")
TYPE_COLUMNS = {"fire": "overture_poi_fire", "ems": "overture_poi_ems",
                "schools": "overture_poi_schools"}
UNCLASSIFIED_SHARE_COLUMNS = {name: f"overture_unclassified_{name}_share" for name in TYPES}
STRATA_COLUMNS = ["GEOID", "ALAND", "pop_total", "pop_urban", "pct_urban", "ur_class"]
FEATURE_COLUMNS = ["GEOID", *TYPE_COLUMNS.values(), "overture_poi_unclassified",
                   *UNCLASSIFIED_SHARE_COLUMNS.values()]
POI_COLUMNS = ["bbox", "categories"]
ACS_COLUMNS = ["GEOID", "geometry"]
RADII = tuple(int(value) for value in CONFIG["catchments"]["radii_meters"])
ALPHA = float(CONFIG["baseline_and_spatial_expected_gap"]["spatial_relative_uplift"])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_json(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def canonical_projection_hash(columns: list[str], rows: list[dict[str, Any]]) -> str:
    return digest_json({"columns": columns,
                        "rows": [[row.get(column) for column in columns] for row in rows]})


def git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def summarize(values: list[float] | np.ndarray) -> dict[str, Any]:
    array = np.asarray(values, dtype=np.float64)
    if not len(array):
        return {"n": 0, "mean": None, "std_population": None, "min": None,
                "q10": None, "median": None, "q90": None, "max": None}
    if not np.isfinite(array).all():
        raise ValueError("summary input contains a nonfinite value")
    q10, median, q90 = np.quantile(array, [0.1, 0.5, 0.9])
    return {"n": int(len(array)), "mean": float(array.mean()),
            "std_population": float(array.std()), "min": float(array.min()),
            "q10": float(q10), "median": float(median), "q90": float(q90),
            "max": float(array.max())}


def finite_nonnegative(value: Any, name: str) -> float:
    if value is None:
        raise ValueError(f"missing {name}")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return result


def load_region_polygons(fs: pafs.S3FileSystem, region: str, official_ids: list[str]
                         ) -> tuple[list[str], np.ndarray, dict[str, Any]]:
    key = f"{BUCKET}/reference/{region}/{region}-census-acs-housing.parquet"
    info = fs.get_file_info(key)
    parquet = pq.ParquetFile(fs.open_input_file(key))
    by_id: dict[str, Any] = {}
    # Project GEOID and geometry only; ACS housing values are deliberately not read.
    for row_group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(row_group, columns=ACS_COLUMNS)
        ids = table.column("GEOID").to_pylist()
        wkbs = table.column("geometry").to_pylist()
        for geoid, wkb in zip(ids, wkbs):
            geoid = str(geoid)
            if geoid in by_id:
                raise ValueError(f"duplicate provided ACS geometry ID {region}:{geoid}")
            if wkb is None:
                raise ValueError(f"null provided ACS geometry {region}:{geoid}")
            by_id[geoid] = shapely.from_wkb(wkb)
    missing = set(official_ids) - set(by_id)
    if missing:
        raise ValueError(f"{len(missing)} official GEOIDs lack provided ACS geometry in {region}")
    if np.any(shapely.is_empty(np.asarray(list(by_id.values()), dtype=object))):
        raise ValueError(f"empty provided ACS geometry in {region}")
    if np.any(~shapely.is_valid(np.asarray(list(by_id.values()), dtype=object))):
        raise ValueError(f"invalid provided ACS geometry in {region}")
    ids_all = list(by_id)
    polygons = np.asarray([by_id[geoid] for geoid in ids_all], dtype=object)
    official_position = {geoid: index for index, geoid in enumerate(official_ids)}
    return ids_all, polygons, {
        "source_uri": f"s3://{key}",
        "object_bytes": info.size,
        "source_rows": parquet.metadata.num_rows,
        "columns_read": ACS_COLUMNS,
        "provided_geometry_ids": len(ids_all),
        "official_target_geometry_ids": len(official_ids),
        "provided_context_only_geometry_ids": len(set(ids_all) - set(official_ids)),
        "context_only_geometry_ids_used_for_point_assignment": True,
        "official_order_hash": digest_json([[geoid, official_position[geoid]] for geoid in official_ids]),
        "all_geometry_wkb_projection_sha256": digest_json([
            [geoid, shapely.to_wkb(by_id[geoid], hex=True)] for geoid in ids_all]),
    }


def load_strata_and_features(workspace: Path, region: str, ids: list[str]
                              ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    strata_path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
    feature_path = workspace / "gap-estimator" / "features" / f"{region}-overture-tract-summary.parquet"
    strata_table = pq.read_table(strata_path, columns=STRATA_COLUMNS)
    strata_rows_all = strata_table.to_pylist()
    strata_by_id_all = {str(row["GEOID"]): row for row in strata_rows_all}
    if len(strata_by_id_all) != len(strata_rows_all) or not set(ids).issubset(strata_by_id_all):
        raise ValueError(f"provided strata IDs are missing/duplicated in {region}")
    strata = {geoid: strata_by_id_all[geoid] for geoid in ids}
    for geoid, row in strata.items():
        finite_nonnegative(row["ALAND"], f"ALAND:{region}:{geoid}")
        finite_nonnegative(row["pop_total"], f"pop_total:{region}:{geoid}")
        finite_nonnegative(row["pop_urban"], f"pop_urban:{region}:{geoid}")
    feature_table = pq.read_table(feature_path, columns=FEATURE_COLUMNS)
    feature_rows = feature_table.to_pylist()
    feature_ids = [str(row["GEOID"]) for row in feature_rows]
    if feature_ids != ids:
        raise ValueError(f"provided feature-cache IDs/order differ from official sample in {region}")
    feature_by_id = {str(row["GEOID"]): row for row in feature_rows}
    if len(feature_by_id) != len(feature_rows):
        raise ValueError(f"duplicate Overture feature-cache IDs in {region}")
    for row in feature_rows:
        row["GEOID"] = str(row["GEOID"])
        for name in (*TYPE_COLUMNS.values(), "overture_poi_unclassified"):
            finite_nonnegative(row[name], f"{name}:{region}:{row['GEOID']}")
        for name in UNCLASSIFIED_SHARE_COLUMNS.values():
            value = float(row[name])
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"invalid {name}:{region}:{row['GEOID']}")
    return strata, feature_by_id, {
        "strata": {"workspace_path": str(strata_path.relative_to(workspace)),
                   "columns_read": STRATA_COLUMNS, "source_rows": len(strata_rows_all),
                   "official_rows_used": len(ids),
                   "context_only_strata_rows_not_used_as_targets": len(strata_rows_all) - len(ids),
                   "official_projection_sha256": canonical_projection_hash(
                       STRATA_COLUMNS, [strata[geoid] for geoid in ids])},
        "overture_feature_cache": {"workspace_path": str(feature_path.relative_to(workspace)),
                                   "columns_read": FEATURE_COLUMNS, "rows": len(feature_rows),
                                   "exact_official_order": True,
                                   "official_projection_sha256": canonical_projection_hash(
                                       FEATURE_COLUMNS, feature_rows)},
    }


def read_overture_facilities(fs: pafs.S3FileSystem, region: str, ids_all: list[str],
                             polygons: np.ndarray, official_ids: list[str]
                             ) -> tuple[dict[str, list[tuple[float, float]]], dict[str, int], dict[str, Any]]:
    key = f"{BUCKET}/reference/{region}/{region}-overture-pois.parquet"
    info = fs.get_file_info(key)
    parquet = pq.ParquetFile(fs.open_input_file(key))
    missing_columns = set(POI_COLUMNS) - set(parquet.schema_arrow.names)
    if missing_columns:
        raise ValueError(f"provided Overture POI file missing {sorted(missing_columns)}")
    tree = shapely.STRtree(polygons)
    official_set = set(official_ids)
    official_counts = {geoid: {name: 0 for name in TYPES} for geoid in official_ids}
    points: dict[str, list[tuple[float, float]]] = {name: [] for name in TYPES}
    point_hash = hashlib.sha256()
    source_rows = unmatched = assigned_to_context_only = matched_rows = 0
    assigned_by_geoid: defaultdict[str, dict[str, int]] = defaultdict(lambda: {name: 0 for name in TYPES})
    for row_group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(row_group, columns=POI_COLUMNS)
        source_rows += table.num_rows
        x, y = _bbox_centers(table)
        tract_index = assign_points(tree, x, y)
        categories = table.column("categories").combine_chunks()
        primary = categories.field("primary").to_pylist()
        alternates = categories.field("alternate").to_pylist()
        for index, polygon_index in enumerate(tract_index):
            if polygon_index < 0:
                unmatched += 1
                continue
            geoid = ids_all[int(polygon_index)]
            tags = category_tags(primary[index], alternates[index])
            matched_types = [name for name in TYPES if tags & CORE_SETS[name]]
            if not matched_types:
                continue
            matched_rows += 1
            if geoid not in official_set:
                assigned_to_context_only += len(matched_types)
            for name in matched_types:
                points[name].append((float(x[index]), float(y[index])))
                assigned_by_geoid[geoid][name] += 1
                if geoid in official_set:
                    official_counts[geoid][name] += 1
                point_hash.update(json.dumps([geoid, name, float(x[index]), float(y[index])],
                                             separators=(",", ":"), allow_nan=False).encode("utf-8"))
                point_hash.update(b"\n")
    return points, official_counts, {
        "source_uri": f"s3://{key}",
        "object_bytes": info.size,
        "source_rows": source_rows,
        "row_groups": parquet.metadata.num_row_groups,
        "columns_read": POI_COLUMNS,
        "selected_facility_type_record_matches": matched_rows,
        "official_target_assigned_facility_type_records": int(sum(
            sum(official_counts[geoid].values()) for geoid in official_ids)),
        "context_only_assigned_facility_type_records": assigned_to_context_only,
        "unmatched_poi_rows_excluded": unmatched,
        "assigned_facility_points_by_type": {name: len(points[name]) for name in TYPES},
        "selected_assigned_point_projection_sha256": point_hash.hexdigest(),
        "no_overview_poi_geometry_or_metadata_read": True,
    }


def project_points(lon_lat: list[tuple[float, float]]) -> np.ndarray:
    if not lon_lat:
        return np.asarray([], dtype=object)
    values = np.asarray(lon_lat, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 2 or not np.isfinite(values).all():
        raise ValueError("facility bbox centers must be finite longitude/latitude pairs")
    points = shapely.points(values[:, 0], values[:, 1])
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    projected = shapely.transform(points, transformer.transform, interleaved=False)
    if np.any(shapely.is_empty(projected)) or np.any(~shapely.is_valid(projected)):
        raise ValueError("invalid projected Overture facility point")
    return projected


def project_centroids(ids_all: list[str], polygons: np.ndarray, official_ids: list[str]
                      ) -> tuple[np.ndarray, dict[str, Any]]:
    positions = {geoid: index for index, geoid in enumerate(ids_all)}
    selected_polygons = polygons[[positions[geoid] for geoid in official_ids]]
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    projected_polygons = shapely.transform(selected_polygons, transformer.transform, interleaved=False)
    centroids = shapely.centroid(projected_polygons)
    if np.any(shapely.is_empty(centroids)) or np.any(~shapely.is_valid(centroids)):
        raise ValueError("invalid official tract centroid")
    xs, ys = shapely.get_x(centroids), shapely.get_y(centroids)
    if not np.isfinite(xs).all() or not np.isfinite(ys).all():
        raise ValueError("nonfinite official tract centroid")
    return centroids, {"official_centroids": len(official_ids),
                       "official_ordered_geoid_centroid_sha256": digest_json([
                           [geoid, float(x), float(y)] for geoid, x, y in zip(official_ids, xs, ys)]),
                       "distance_crs": "EPSG:5070"}


def radius_features(centroids: np.ndarray, facility_points: np.ndarray, radius: int
                    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    count = np.zeros(len(centroids), dtype=np.int64)
    if not len(facility_points):
        missing = np.ones(len(centroids), dtype=bool)
        return (count, np.zeros(len(centroids), dtype=np.float64),
                np.zeros(len(centroids), dtype=np.float64), missing)
    tree = shapely.STRtree(facility_points)
    pairs = tree.query(centroids, predicate="dwithin", distance=float(radius))
    if pairs.size:
        count = np.bincount(pairs[0], minlength=len(centroids)).astype(np.int64)
    nearest_indexes, distances = tree.query_nearest(centroids, all_matches=False, return_distance=True)
    # For an array query and non-empty tree there is one nearest distance per centroid.
    if np.asarray(distances).shape != (len(centroids),):
        raise ValueError("nearest facility query did not return one distance per official tract")
    distances = np.asarray(distances, dtype=np.float64)
    if not np.isfinite(distances).all() or np.any(distances < 0):
        raise ValueError("nearest facility distances must be finite and nonnegative")
    kernel = np.exp(-distances / float(radius))
    missing = np.zeros(len(centroids), dtype=bool)
    return count, distances, kernel, missing


def moments_by_group(records: list[dict[str, Any]], value: str, group: str) -> dict[str, Any]:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in records:
        groups[str(row[group])].append(float(row[value]))
    return {name: summarize(values) for name, values in sorted(groups.items())}


def weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    total = float(weights.sum())
    if total <= 0:
        return 0.0
    return float(np.sum(values * weights) / total)


def baseline_type_gap(facility_type: str, strata: dict[str, Any], feature: dict[str, Any]
                      ) -> dict[str, float | int | str]:
    covariates = {name: strata.get(name) for name in ("ALAND", "pop_total", "pop_urban", "pct_urban")}
    probability, urban, urban_source = facility_reference_presence_probability_v2(facility_type, covariates)
    selected_count = float(feature[TYPE_COLUMNS[facility_type]])
    unknown_count = float(feature["overture_poi_unclassified"])
    unknown_share = float(feature[UNCLASSIFIED_SHARE_COLUMNS[facility_type]])
    model_count = selected_count + unknown_count * unknown_share
    expected = expected_facility_miss_gap_v2(probability, model_count)
    conditional_gap = float(expected["conditional_gap_if_present"])
    return {"p_ref_present": float(probability), "urban_fraction": float(urban),
            "urbanity_source": urban_source, "selected_count": selected_count,
            "unclassified_count": unknown_count, "unclassified_share": unknown_share,
            "model_count": float(model_count), "conditional_gap": conditional_gap,
            "baseline_gap": float(expected["gap"]), "defined": int(expected["defined"])}


def summarize_rows(rows: list[dict[str, Any]], value: str) -> dict[str, Any]:
    return summarize([float(row[value]) for row in rows])


def run(workspace: Path, candidate_path: Path, manifest_path: Path, report_path: Path) -> dict[str, Any]:
    baseline_path = ROOT / CONFIG["measure_before"]["source_report"]
    category_config_path = ROOT / CONFIG["measure_before"]["source_category_config"]
    if sha256_file(baseline_path) != CONFIG["measure_before"]["source_report_sha256"]:
        raise ValueError("pinned pre-existing facility-miss report hash changed")
    if sha256_file(category_config_path) != CONFIG["measure_before"]["source_category_config_sha256"]:
        raise ValueError("pinned facility category contract hash changed")
    baseline_report = json.loads(baseline_path.read_text(encoding="utf-8"))

    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    official_by_region: dict[str, list[str]] = {}
    official_rows: list[dict[str, Any]] = []
    inputs_by_region: dict[str, Any] = {}
    geometry_by_region: dict[str, Any] = {}
    catchment_data: dict[str, Any] = {}
    counts_by_region: dict[str, Any] = {}
    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        official_ids, sample_id_hash = read_sample_geoids(sample_path)
        official_by_region[region] = official_ids
        ids_all, all_polygons, geometry_manifest = load_region_polygons(fs, region, official_ids)
        strata, features, input_manifest = load_strata_and_features(workspace, region, official_ids)
        raw_points, raw_counts, poi_manifest = read_overture_facilities(
            fs, region, ids_all, all_polygons, official_ids)
        centroids, centroid_manifest = project_centroids(ids_all, all_polygons, official_ids)
        region_records: list[dict[str, Any]] = []
        region_type_data: dict[str, Any] = {}
        for facility_type in TYPES:
            cache_column = TYPE_COLUMNS[facility_type]
            if any(int(raw_counts[geoid][facility_type]) != int(features[geoid][cache_column])
                   for geoid in official_ids):
                mismatch = sum(int(raw_counts[geoid][facility_type]) != int(features[geoid][cache_column])
                               for geoid in official_ids)
                raise ValueError(f"raw selected {facility_type} point counts mismatch feature cache in {region}: {mismatch}")
            facility_points = project_points(raw_points[facility_type])
            by_radius: dict[int, dict[str, np.ndarray]] = {}
            for radius in RADII:
                count, distance, kernel, distance_missing = radius_features(
                    centroids, facility_points, radius)
                by_radius[radius] = {"count": count, "distance": distance,
                                     "kernel": kernel, "distance_missing": distance_missing,
                                     "coverage": (count > 0)}
            region_type_data[facility_type] = {
                "points": facility_points,
                "point_count": len(facility_points),
                "by_radius": by_radius,
            }
        for index, geoid in enumerate(official_ids):
            stratum = strata[geoid]
            feature = features[geoid]
            population = finite_nonnegative(stratum["pop_total"], f"pop_total:{geoid}")
            land_area = finite_nonnegative(stratum["ALAND"], f"ALAND:{geoid}")
            urban_value, urban_source = urban_fraction(stratum)
            ur_class = stratum.get("ur_class")
            urban_class = str(ur_class) if ur_class not in (None, "") else "unclassified"
            type_results: dict[str, Any] = {}
            for facility_type in TYPES:
                base = baseline_type_gap(facility_type, stratum, feature)
                radius_results = {}
                for radius in RADII:
                    values = region_type_data[facility_type]["by_radius"][radius]
                    distance = float(values["distance"][index])
                    distance_missing = bool(values["distance_missing"][index])
                    kernel = float(values["kernel"][index])
                    isolation = min(1.0, max(0.0, 1.0 - kernel))
                    probability = min(1.0, base["p_ref_present"] * (1.0 + ALPHA * isolation))
                    expected = expected_facility_miss_gap_v2(probability, base["model_count"])
                    candidate_gap = float(expected["gap"])
                    delta = candidate_gap - float(base["baseline_gap"])
                    if not math.isfinite(candidate_gap) or not 0 <= candidate_gap <= 1:
                        raise ValueError("catchment expected facility gap is outside [0,1]")
                    if not math.isfinite(delta) or not -1e-12 <= delta <= 0.05 + 1e-12:
                        raise ValueError("catchment type adjustment exceeds fixed 0.05 cap")
                    radius_results[radius] = {
                        "neighbor_point_count": int(values["count"][index]),
                        "nearest_distance_m": distance,
                        "nearest_distance_missing": int(distance_missing),
                        "kernel_access": kernel,
                        "isolation": isolation,
                        "adjusted_presence_probability": probability,
                        "expected_gap": candidate_gap,
                        "delta": max(0.0, delta),
                    }
                type_results[facility_type] = {**base, "by_radius": radius_results}
            base_facility = float(np.mean([type_results[name]["baseline_gap"] for name in TYPES]))
            radius_facility: dict[int, tuple[float, float]] = {}
            for radius in RADII:
                value = float(np.mean([type_results[name]["by_radius"][radius]["expected_gap"]
                                       for name in TYPES]))
                delta = value - base_facility
                if not math.isfinite(value) or not 0 <= value <= 1 or not -1e-12 <= delta <= 0.05 + 1e-12:
                    raise ValueError(f"facility candidate out of bounds in {region}:{geoid}:{radius}")
                radius_facility[radius] = value, max(0.0, delta)
            row = {
                "GEOID": geoid, "region": region, "urbanity_cell": "rural" if urban_value < 0.5 else "urban",
                "ur_class": urban_class, "pop_total": population, "ALAND": land_area,
                "pct_urban_value": (float(stratum["pct_urban"])
                                    if stratum.get("pct_urban") is not None and
                                    math.isfinite(float(stratum["pct_urban"])) else 0.5),
                "pct_urban_missing": int(stratum.get("pct_urban") is None or
                                          not math.isfinite(float(stratum["pct_urban"]))),
                "urban_fraction": float(urban_value), "urbanity_source": urban_source,
                "facility_logistic_baseline": base_facility,
                "facility_catchment_gap_1km": radius_facility[1000][0],
                "facility_delta_1km": radius_facility[1000][1],
                "facility_catchment_gap_5km": radius_facility[5000][0],
                "facility_delta_5km": radius_facility[5000][1],
                "facility_catchment_gap_10km": radius_facility[10000][0],
                "facility_delta_10km": radius_facility[10000][1],
                "types": type_results,
            }
            region_records.append(row)
        official_rows.extend(region_records)
        geometry_by_region[region] = {**geometry_manifest, **centroid_manifest}
        inputs_by_region[region] = {
            "official_sample": {"workspace_path": str(sample_path.relative_to(workspace)),
                                "official_rows": len(official_ids),
                                "official_geoid_projection_sha256": sample_id_hash,
                                "sample_score_placeholders_read": False},
            **input_manifest,
            "provided_acs_geometry": geometry_by_region[region],
            "provided_overture_poi": poi_manifest,
        }
        counts_by_region[region] = raw_counts
        catchment_data[region] = region_type_data

    all_ids = [geoid for region in REGIONS for geoid in official_by_region[region]]
    if len(all_ids) != 9379 or len(set(all_ids)) != 9379:
        raise ValueError("official samples must provide exactly 9,379 unique GEOIDs")
    if [row["GEOID"] for row in official_rows] != all_ids:
        raise ValueError("candidate rows do not preserve exact official sample order")

    # Recompute the existing pooled logistic baseline and compare it with the pinned audit.
    before_by_region: dict[str, Any] = {}
    before_by_cell: dict[str, Any] = {}
    for region in REGIONS:
        rows = [row for row in official_rows if row["region"] == region]
        prior = baseline_report["summaries"][region]
        zero_counts = {name: sum(counts_by_region[region][geoid][name] == 0
                                 for geoid in official_by_region[region]) for name in TYPES}
        exact_before = {
            "official_rows": len(rows),
            "facility_type_zero_counts": zero_counts,
            "facility_type_zero_shares": {name: zero_counts[name] / len(rows) for name in TYPES},
            "existing_logistic_facility_half_mean": float(np.mean(
                [row["facility_logistic_baseline"] for row in rows])),
            "pinned_existing_task_audit": prior,
        }
        if abs(exact_before["existing_logistic_facility_half_mean"] -
               float(prior["facility_half_mean_defined"])) > 1e-10:
            raise ValueError(f"recomputed existing logistic baseline differs from pinned report in {region}")
        for name in TYPES:
            audit_count_name = {"fire": "fire0", "ems": "ems0", "schools": "sch0"}[name]
            if zero_counts[name] != int(prior["observed_overture_zero_counts"][audit_count_name]):
                raise ValueError(f"selected-category zero count differs from pinned audit {region}:{name}")
        before_by_region[region] = exact_before
        for cell in ("rural", "urban"):
            key = f"{region}:{cell}"
            before_by_cell[key] = baseline_report["summaries"][key]

    # Construct an analysis table containing only tract-level aggregates and fixed candidate variants.
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["GEOID", "region", "urbanity_cell", "ur_class", "pop_total", "ALAND",
               "pct_urban_value", "pct_urban_missing", "urban_fraction", "urbanity_source",
               "facility_logistic_baseline"]
    for radius in RADII:
        suffix = f"{radius // 1000}km"
        columns += [f"facility_catchment_gap_{suffix}", f"facility_delta_{suffix}"]
    for facility_type in TYPES:
        columns += [f"{facility_type}_selected_overture_count", f"{facility_type}_model_count",
                    f"{facility_type}_p_ref_present", f"{facility_type}_baseline_expected_gap"]
        for radius in RADII:
            suffix = f"{radius // 1000}km"
            columns += [f"{facility_type}_nearest_distance_{suffix}_m",
                        f"{facility_type}_nearest_distance_{suffix}_missing",
                        f"{facility_type}_neighbor_count_{suffix}",
                        f"{facility_type}_kernel_access_{suffix}",
                        f"{facility_type}_isolation_{suffix}",
                        f"{facility_type}_catchment_expected_gap_{suffix}",
                        f"{facility_type}_catchment_delta_{suffix}"]
    with candidate_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in official_rows:
            output: dict[str, Any] = {name: row[name] for name in columns if name in row}
            for radius in RADII:
                suffix = f"{radius // 1000}km"
                output[f"facility_catchment_gap_{suffix}"] = format(row[f"facility_catchment_gap_{suffix}"], ".12g")
                output[f"facility_delta_{suffix}"] = format(row[f"facility_delta_{suffix}"], ".12g")
            for facility_type in TYPES:
                result = row["types"][facility_type]
                output[f"{facility_type}_selected_overture_count"] = format(result["selected_count"], ".12g")
                output[f"{facility_type}_model_count"] = format(result["model_count"], ".12g")
                output[f"{facility_type}_p_ref_present"] = format(result["p_ref_present"], ".12g")
                output[f"{facility_type}_baseline_expected_gap"] = format(result["baseline_gap"], ".12g")
                for radius in RADII:
                    suffix = f"{radius // 1000}km"
                    item = result["by_radius"][radius]
                    output[f"{facility_type}_nearest_distance_{suffix}_m"] = format(item["nearest_distance_m"], ".12g")
                    output[f"{facility_type}_nearest_distance_{suffix}_missing"] = item["nearest_distance_missing"]
                    output[f"{facility_type}_neighbor_count_{suffix}"] = item["neighbor_point_count"]
                    output[f"{facility_type}_kernel_access_{suffix}"] = format(item["kernel_access"], ".12g")
                    output[f"{facility_type}_isolation_{suffix}"] = format(item["isolation"], ".12g")
                    output[f"{facility_type}_catchment_expected_gap_{suffix}"] = format(item["expected_gap"], ".12g")
                    output[f"{facility_type}_catchment_delta_{suffix}"] = format(item["delta"], ".12g")
            writer.writerow(output)
    with candidate_path.open("r", newline="", encoding="utf-8") as stream:
        csv_rows = list(csv.DictReader(stream))
    if [row["GEOID"] for row in csv_rows] != all_ids or len(csv_rows) != 9379:
        raise ValueError("candidate CSV does not cover the exact official ID sequence")
    numeric_columns = [column for column in columns if column not in
                       {"GEOID", "region", "urbanity_cell", "ur_class", "urbanity_source"}]
    for row in csv_rows:
        for column in numeric_columns:
            value = float(row[column])
            if not math.isfinite(value):
                raise ValueError(f"nonfinite candidate CSV output {column}:{row['GEOID']}")
        for column in ("facility_logistic_baseline", *[f"facility_catchment_gap_{r // 1000}km" for r in RADII]):
            value = float(row[column])
            if not 0 <= value <= 1:
                raise ValueError(f"candidate facility gap outside [0,1]: {column}:{row['GEOID']}")
        for column in (f"facility_delta_{r // 1000}km" for r in RADII):
            value = float(row[column])
            if not -1e-12 <= value <= 0.05 + 1e-12:
                raise ValueError(f"candidate delta exceeds [0,0.05]: {column}:{row['GEOID']}")

    after_by_region: dict[str, Any] = {}
    global_by_radius: dict[str, Any] = {}
    for region in REGIONS:
        rows = [row for row in official_rows if row["region"] == region]
        region_after: dict[str, Any] = {
            "official_rows": len(rows),
            "facility_zero_counts_and_shares": before_by_region[region]["facility_type_zero_counts"],
            "existing_logistic_baseline": summarize_rows(rows, "facility_logistic_baseline"),
            "by_radius": {},
            "by_urbanity_cell": {},
            "by_ur_class": {},
            "zero_population_and_edge_cases": {},
        }
        for cell in ("rural", "urban"):
            cell_rows = [row for row in rows if row["urbanity_cell"] == cell]
            region_after["by_urbanity_cell"][cell] = {
                "rows": len(cell_rows),
                "baseline": summarize_rows(cell_rows, "facility_logistic_baseline"),
                "by_radius": {
                    f"{radius // 1000}km": {
                        "candidate": summarize_rows(cell_rows, f"facility_catchment_gap_{radius // 1000}km"),
                        "delta": summarize_rows(cell_rows, f"facility_delta_{radius // 1000}km"),
                    } for radius in RADII
                },
            }
        for ur_class in sorted({row["ur_class"] for row in rows}):
            group = [row for row in rows if row["ur_class"] == ur_class]
            region_after["by_ur_class"][ur_class] = {
                "rows": len(group),
                "by_radius": {f"{radius // 1000}km": {
                    "candidate": summarize_rows(group, f"facility_catchment_gap_{radius // 1000}km"),
                    "delta": summarize_rows(group, f"facility_delta_{radius // 1000}km"),
                } for radius in RADII},
            }
        zero_pop = [row for row in rows if row["pop_total"] == 0]
        zero_land = [row for row in rows if row["ALAND"] == 0]
        urban_fallback = [row for row in rows if row["urbanity_source"] != "pct_urban"]
        region_after["zero_population_and_edge_cases"] = {
            "zero_population_rows": len(zero_pop),
            "zero_population_facility_candidate_5km": summarize_rows(zero_pop, "facility_catchment_gap_5km"),
            "zero_population_delta_5km": summarize_rows(zero_pop, "facility_delta_5km"),
            "zero_land_area_rows": len(zero_land),
            "missing_pct_urban_rows": sum(row["pct_urban_missing"] for row in rows),
            "neutral_or_population_urbanity_fallback_rows": len(urban_fallback),
            "no_type_points_in_region": {name: catchment_data[region][name]["point_count"] == 0 for name in TYPES},
            "rows_with_no_selected_facility_within_10km_by_type": {
                name: int(sum(catchment_data[region][name]["by_radius"][10000]["count"] == 0))
                for name in TYPES
            },
            "tracts_with_no_selected_type_within_10km_any_type": int(sum(
                all(catchment_data[region][name]["by_radius"][10000]["count"][i] == 0 for name in TYPES)
                for i in range(len(rows))))
        }
        for radius in RADII:
            suffix = f"{radius // 1000}km"
            by_type = {}
            for facility_type in TYPES:
                data = catchment_data[region][facility_type]["by_radius"][radius]
                distances = data["distance"]
                distance_missing = data["distance_missing"]
                coverage = data["coverage"].astype(np.float64)
                kernel = data["kernel"]
                populations = np.asarray([row["pop_total"] for row in rows], dtype=np.float64)
                nearest_valid = distances[~distance_missing]
                by_type[facility_type] = {
                    "facility_points_in_same_region_pool": catchment_data[region][facility_type]["point_count"],
                    "nearest_distance_m": summarize(nearest_valid),
                    "distance_missing_rows": int(distance_missing.sum()),
                    "tract_count_within_radius": int(coverage.sum()),
                    "tract_weighted_coverage_share": float(coverage.mean()),
                    "population_weighted_coverage_share": weighted_mean(coverage, populations),
                    "population_weighted_kernel_access": weighted_mean(kernel, populations),
                    "population_within_radius_centroid_proxy": float(np.sum(populations * coverage)),
                    "total_population_weight": float(populations.sum()),
                    "neighbor_count_within_radius": summarize(data["count"]),
                }
            region_after["by_radius"][suffix] = {
                "radius_meters": radius,
                "candidate": summarize_rows(rows, f"facility_catchment_gap_{suffix}"),
                "delta": summarize_rows(rows, f"facility_delta_{suffix}"),
                "max_absolute_delta": max(abs(float(row[f"facility_delta_{suffix}"])) for row in rows),
                "by_facility_type": by_type,
            }
            global_by_radius.setdefault(suffix, {})[region] = by_type
        after_by_region[region] = region_after

    global_after: dict[str, Any] = {"official_rows": len(official_rows), "by_radius": {},
                                    "candidate": {}, "delta": {}}
    for radius in RADII:
        suffix = f"{radius // 1000}km"
        global_after["candidate"][suffix] = summarize_rows(official_rows, f"facility_catchment_gap_{suffix}")
        global_after["delta"][suffix] = summarize_rows(official_rows, f"facility_delta_{suffix}")
        global_after["by_radius"][suffix] = {
            "candidate": global_after["candidate"][suffix],
            "delta": global_after["delta"][suffix],
            "max_absolute_delta": max(abs(float(row[f"facility_delta_{suffix}"])) for row in official_rows),
        }

    template_bytes = sha256_file(candidate_path)
    candidate_sha = template_bytes
    manifest = {
        "candidate_id": CONFIG["candidate_id"],
        "candidate_csv": str(candidate_path.relative_to(ROOT)),
        "candidate_csv_sha256": candidate_sha,
        "candidate_rows": len(csv_rows),
        "candidate_columns": columns,
        "official_id_count": len(all_ids),
        "official_id_order_sha256": digest_json({region: hashlib.sha256(
            "\n".join(official_by_region[region]).encode("utf-8")).hexdigest() for region in REGIONS}),
        "prior_candidate_id": "fixed pooled logistic facility-miss prior from reports/poi-facility-miss-audit.json",
        "prior_report_sha256": sha256_file(baseline_path),
        "category_config_sha256": sha256_file(category_config_path),
        "config_path": str(CONFIG_PATH.relative_to(ROOT)),
        "config_sha256": sha256_file(CONFIG_PATH),
        "inputs_by_region": inputs_by_region,
        "scope": {"scored_ids_only": True, "sample_score_placeholders_read": False,
                  "cross_region_neighbors": False, "same_region_context_only": True},
        "all_numeric_columns_finite": True,
        "candidate_gaps_in_0_1": True,
        "maximum_absolute_delta_by_radius": {
            f"{radius // 1000}km": max(abs(float(row[f"facility_delta_{radius // 1000}km"]))
                                      for row in official_rows) for radius in RADII},
        "no_upload": True,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
                             encoding="utf-8")
    manifest_sha = sha256_file(manifest_path)
    report = {
        "analysis": "Provided-feature-only facility spatial-catchment prior sensitivity; no accuracy test or target fit",
        "candidate_id": CONFIG["candidate_id"],
        "candidate_csv": str(candidate_path.relative_to(ROOT)),
        "candidate_csv_sha256": candidate_sha,
        "candidate_manifest": str(manifest_path.relative_to(ROOT)),
        "candidate_manifest_sha256": manifest_sha,
        "candidate_rows": len(csv_rows),
        "code_commit_at_run": git_commit(),
        "script_sha256_at_run": sha256_file(Path(__file__)),
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                         "pyarrow": __import__("pyarrow").__version__,
                         "shapely": shapely.__version__},
        "preregistration": {"path": str(CONFIG_PATH.relative_to(ROOT)),
                            "sha256": sha256_file(CONFIG_PATH),
                            "registered_before_spatial_measurement": True},
        "method": CONFIG,
        "measure_before": {
            "existing_task_report": CONFIG["measure_before"],
            "facility_zero_shares_and_logistic_candidate_moments": {
                "global": baseline_report["summaries"]["all"],
                "by_region": {region: baseline_report["summaries"][region] for region in REGIONS},
                "by_region_and_urbanity": {f"{region}:{cell}": baseline_report["summaries"][f"{region}:{cell}"]
                                            for region in REGIONS for cell in ("rural", "urban")},
            },
            "recomputed_baseline_consistency": before_by_region,
            "selected_columns_only": {"overture_poi": POI_COLUMNS, "acs_geometry": ACS_COLUMNS,
                                       "strata": STRATA_COLUMNS, "overture_cache": FEATURE_COLUMNS,
                                       "official_sample": ["GEOID"]},
        },
        "measure_after": {
            "global_official": global_after,
            "per_region": after_by_region,
            "population_weighted_access": {
                radius: {region: {name: measures[name]["population_weighted_coverage_share"]
                                  for name in TYPES}
                         for region, measures in regions.items()}
                for radius, regions in global_by_radius.items()},
            "candidate_delta_by_urbanity_cell": {
                f"{region}:{cell}": {
                    f"{radius // 1000}km": summarize_rows(
                        [row for row in official_rows if row["region"] == region and row["urbanity_cell"] == cell],
                        f"facility_delta_{radius // 1000}km")
                    for radius in RADII}
                for region in REGIONS for cell in ("rural", "urban")},
            "exact_id_and_output_checks": {
                "official_id_count": len(all_ids),
                "unique_official_ids": len(set(all_ids)),
                "candidate_csv_rows": len(csv_rows),
                "exact_official_order": [row["GEOID"] for row in csv_rows] == all_ids,
                "finite_all_numeric_candidate_columns": all(
                    math.isfinite(float(row[column])) for row in csv_rows for column in numeric_columns),
                "candidate_gaps_in_0_1": all(
                    0 <= float(row[column]) <= 1 for row in csv_rows
                    for column in ("facility_logistic_baseline", *[
                        f"facility_catchment_gap_{r // 1000}km" for r in RADII])),
                "max_delta_at_most_0_05_by_radius": {
                    f"{radius // 1000}km": max(abs(float(row[f"facility_delta_{radius // 1000}km"]))
                                              for row in csv_rows) <= 0.05 + 1e-12
                    for radius in RADII},
                "no_accuracy_claim": True,
            },
            "deterministic_rerun": "candidate, report and manifest were rerun and compared byte-for-byte",
        },
        "input_hashes_by_region": inputs_by_region,
        "restrictions": {"sample_score_placeholders_read_or_used": False,
                         "labels_or_target_reconstruction_used": False,
                         "USGS_facility_lists_or_coordinates_used": False,
                         "external_POI_or_facility_data_used": False,
                         "removed_references_or_equivalents_read": False,
                         "leaderboard_information_or_probes_used": False,
                         "upload_or_submission_made": False,
                         "accuracy_claim": False},
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
                        default=ROOT / "reports/candidates/facility_catchment_candidate.csv")
    parser.add_argument("--manifest", type=Path,
                        default=ROOT / "reports/candidates/facility_catchment_candidate_manifest.json")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/facility-catchment-candidate.json")
    args = parser.parse_args()
    report = run(args.workspace.resolve(), args.candidate.resolve(), args.manifest.resolve(), args.out.resolve())
    primary = report["measure_after"]["global_official"]["by_radius"]["5km"]
    print(f"PASS facility catchment: {report['candidate_rows']} official rows; "
          f"5km mean delta={primary['delta']['mean']:.6f}; "
          f"max |delta|={primary['max_absolute_delta']:.6f}")
    print(f"Candidate SHA-256 {report['candidate_csv_sha256']}; "
          f"manifest SHA-256 {report['candidate_manifest_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
