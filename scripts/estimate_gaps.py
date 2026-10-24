#!/usr/bin/env python3
"""Estimate Bias Bounty component gaps using only provided Overture/strata/ACS data.

No removed reference layers, labels, external datasets, or sample score values are read.
Overture features are spatially summarized from the public Source Cooperative
objects; small derived per-tract summaries are cached in the authorized workspace.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

from estimator_v2_establishments import estimate_establishments_component_v2

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
BUCKET = "us-west-2.opendata.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge"
ROAD_CLASSES = {"motorway", "trunk", "primary", "secondary"}
POI_CATEGORY_CONFIG = Path(__file__).resolve().parents[1] / "config/poi-category-definitions.json"
with POI_CATEGORY_CONFIG.open("r", encoding="utf-8") as _stream:
    _poi_category_definitions = json.load(_stream)["selected_sets"]
FIRE_CATEGORIES = set(_poi_category_definitions["fire"])
EMS_CATEGORIES = set(_poi_category_definitions["ems"])
SCHOOL_CATEGORIES = set(_poi_category_definitions["schools"])
# Fixed, region-pooled, model-derived logistic priors for facility reference
# presence. These are design assumptions, not fitted to reference or LB scores.
FACILITY_MISS_PRIORS = {
    "fire": {"intercept": -2.8, "log_density": 0.30, "urban_fraction": 0.70},
    "ems": {"intercept": -3.2, "log_density": 0.25, "urban_fraction": 0.60},
    "schools": {"intercept": -1.0, "log_density": 0.20, "urban_fraction": 0.35},
}
FACILITY_MISS_K_POP_ANALYSIS = 0.024
OUTPUT_COLUMNS = [
    "GEOID", "coverage_gap_score", "transport_gap", "transport_defined",
    "building_gap", "building_defined", "poi_gap", "poi_defined",
    "fire_gap", "fire_defined", "ems_gap", "ems_defined",
    "schools_gap", "schools_defined", "cbp_gap", "cbp_defined",
]
SUBMISSION_COLUMNS = ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"]
BUILDING_SOURCE_FEATURE_COLUMNS = [
    "overture_building_osm_source_share", "overture_building_microsoft_source_share",
    "overture_building_esri_source_share", "overture_building_other_source_share",
    "overture_building_no_dataset_source_share", "overture_building_ms_only_source_share",
    "overture_building_ms_osm_source_share", "overture_building_osm_only_source_share",
    "overture_building_esri_only_source_share", "overture_building_other_or_mixed_source_share",
]
POI_SOURCE_FEATURE_COLUMNS = [
    "overture_poi_meta_source_share", "overture_poi_microsoft_source_share",
    "overture_poi_other_upstream_source_share", "overture_poi_meta_not_microsoft_source_share",
    "overture_poi_microsoft_not_meta_source_share", "overture_poi_meta_and_microsoft_source_share",
    "overture_poi_other_without_meta_microsoft_source_share",
    "overture_poi_no_upstream_source_share", "overture_poi_sources_missing_share",
]
SOURCE_MIX_FEATURE_COLUMNS = BUILDING_SOURCE_FEATURE_COLUMNS + POI_SOURCE_FEATURE_COLUMNS


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_parquet(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd")


def open_source(fs, region: str, layer: str):
    key = f"{BUCKET}/reference/{region}/{region}-overture-{layer}.parquet"
    info = fs.get_file_info(key)
    if info.type != pafs.FileType.File:
        raise FileNotFoundError(f"provided Overture layer missing: s3://{key}")
    return key, info.size, pq.ParquetFile(fs.open_input_file(key))


def arrow_bbox_centers(table: pa.Table) -> tuple[np.ndarray, np.ndarray]:
    bbox = table.column("bbox").combine_chunks()
    values = {}
    for name in ("xmin", "ymin", "xmax", "ymax"):
        child = bbox.field(name)
        arr = child.to_numpy(zero_copy_only=False)
        if child.null_count:
            raise ValueError(f"null Overture bbox field: {name} ({child.null_count})")
        values[name] = arr
    x = (values["xmin"] + values["xmax"]) * 0.5
    y = (values["ymin"] + values["ymax"]) * 0.5
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("non-finite Overture bbox coordinate")
    return x, y


def assign_points(tree, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, int]:
    points = shapely.points(x, y)
    pairs = tree.query(points, predicate="within")
    assignment = np.full(len(points), -1, dtype=np.int64)
    if pairs.size:
        feature_idx, tract_idx = pairs
        # Boundary/touch duplicates are resolved deterministically by lowest tract index.
        order = np.lexsort((tract_idx, feature_idx))
        feature_idx, tract_idx = feature_idx[order], tract_idx[order]
        first = np.r_[True, feature_idx[1:] != feature_idx[:-1]]
        assignment[feature_idx[first]] = tract_idx[first]
    missing = np.flatnonzero(assignment < 0)
    if len(missing):
        boundary_pairs = tree.query(points[missing], predicate="intersects")
        if boundary_pairs.size:
            local_idx, tract_idx = boundary_pairs
            order = np.lexsort((tract_idx, local_idx))
            local_idx, tract_idx = local_idx[order], tract_idx[order]
            first = np.r_[True, local_idx[1:] != local_idx[:-1]]
            assignment[missing[local_idx[first]]] = tract_idx[first]
    return assignment, int(np.sum(assignment < 0))


def rowgroups(parquet_file, columns):
    for idx in range(parquet_file.metadata.num_row_groups):
        yield idx, parquet_file.read_row_group(idx, columns=columns)


def aggregate_buildings(fs, region: str, tree, n_tracts: int) -> tuple[np.ndarray, dict]:
    key, size, pf = open_source(fs, region, "buildings")
    counts = np.zeros(n_tracts, dtype=np.int64)
    total = unmatched = 0
    started = time.monotonic()
    for rg, table in rowgroups(pf, ["bbox"]):
        x, y = arrow_bbox_centers(table)
        assignment, outside = assign_points(tree, x, y)
        matched = assignment >= 0
        counts += np.bincount(assignment[matched], minlength=n_tracts)
        total += len(assignment)
        unmatched += outside
        if (rg + 1) % 50 == 0:
            print(f"{region} buildings rowgroups {rg + 1}/{pf.metadata.num_row_groups}; rows={total}", flush=True)
    return counts, {"source_uri": f"s3://{key}", "object_bytes": size,
                    "input_rows": total, "row_groups": pf.metadata.num_row_groups,
                    "unassigned_centers": unmatched, "elapsed_seconds": round(time.monotonic()-started, 2)}


def aggregate_pois(fs, region: str, tree, n_tracts: int) -> tuple[dict[str, np.ndarray], dict]:
    key, size, pf = open_source(fs, region, "pois")
    names = ("all", "fire", "ems", "schools", "unclassified")
    counts = {name: np.zeros(n_tracts, dtype=np.int64) for name in names}
    total = unmatched = unclassified_total = classified_total = 0
    category_counts = Counter()
    type_counts = Counter()
    started = time.monotonic()
    for rg, table in rowgroups(pf, ["bbox", "categories"]):
        x, y = arrow_bbox_centers(table)
        category_struct = table.column("categories").combine_chunks()
        primary = category_struct.field("primary").to_pylist()
        alternates = category_struct.field("alternate").to_pylist()
        if len(primary) != len(x):
            raise ValueError("POI category/bbox row mismatch")
        assignment, outside = assign_points(tree, x, y)
        matched = assignment >= 0
        counts["all"] += np.bincount(assignment[matched], minlength=n_tracts)
        unclassified_rows = 0
        for i, category in enumerate(primary):
            tags = set(alternates[i] or [])
            if category:
                tags.add(category)
            if assignment[i] < 0:
                continue
            for tag in tags:
                category_counts[tag] += 1
            targets = set()
            classified_total += int(bool(tags))
            if tags & FIRE_CATEGORIES:
                targets.add("fire")
            if tags & EMS_CATEGORIES:
                targets.add("ems")
            if tags & SCHOOL_CATEGORIES:
                targets.add("schools")
            if not tags:
                counts["unclassified"][assignment[i]] += 1
                unclassified_rows += 1
            for target in targets:
                counts[target][assignment[i]] += 1
                type_counts[target] += 1
        total += len(x)
        unmatched += outside
        unclassified_total += unclassified_rows
        if (rg + 1) % 25 == 0:
            print(f"{region} POIs rowgroups {rg + 1}/{pf.metadata.num_row_groups}; rows={total}", flush=True)
    if classified_total == 0:
        raise ValueError(f"no categorized in-sample POIs for {region}; cannot estimate category shares")
    type_shares = {name: type_counts[name] / classified_total for name in ("fire", "ems", "schools")}
    return counts, {"source_uri": f"s3://{key}", "object_bytes": size,
                    "input_rows": total, "row_groups": pf.metadata.num_row_groups,
                    "classified_in_sample_rows": int(classified_total),
                    "estimated_unclassified_type_shares": type_shares,
                    "unassigned_centers": unmatched,
                    "selected_category_counts": {k: category_counts[k] for k in sorted(
                        FIRE_CATEGORIES | EMS_CATEGORIES | SCHOOL_CATEGORIES)},
                    "unclassified_category_rows": int(unclassified_total),
                    "elapsed_seconds": round(time.monotonic()-started, 2)}


def _dataset_names(sources) -> set[str]:
    return {str(source["dataset"]).strip().casefold()
            for source in (sources or [])
            if isinstance(source, dict) and source.get("dataset") and
            str(source["dataset"]).strip()}


def classify_building_sources(sources) -> dict[str, object]:
    """Return source-presence flags and one disjoint provenance class for a building."""
    names = _dataset_names(sources)
    has_microsoft = any(name.startswith("microsoft") for name in names)
    has_osm = any(name == "osm" or name.startswith("openstreetmap") for name in names)
    has_esri = any(name.startswith("esri") for name in names)
    known = {name for name in names if name.startswith("microsoft") or
             name == "osm" or name.startswith("openstreetmap") or name.startswith("esri")}
    has_other = bool(names - known)
    if not names:
        source_class = "no_dataset"
    elif has_microsoft and has_osm and not has_esri and not has_other:
        source_class = "microsoft_osm"
    elif has_microsoft and not has_osm and not has_esri and not has_other:
        source_class = "microsoft_only"
    elif has_osm and not has_microsoft and not has_esri and not has_other:
        source_class = "osm_only"
    elif has_esri and not has_microsoft and not has_osm and not has_other:
        source_class = "esri_only"
    else:
        source_class = "other_or_mixed"
    return {"has_dataset": bool(names), "osm": has_osm, "microsoft": has_microsoft,
            "esri": has_esri, "other": has_other, "class": source_class,
            "dataset_names": names}


def classify_poi_sources(sources) -> dict[str, object]:
    """Classify upstream POI source membership; Overture wrappers are not providers."""
    names = _dataset_names(sources)
    has_meta = "meta" in names
    has_microsoft = any(name.startswith("microsoft") for name in names)
    other_names = names - {"meta", "overture", "overture-signals"}
    other_names = {name for name in other_names if not name.startswith("microsoft")}
    has_other = bool(other_names)
    if has_meta and has_microsoft:
        source_class = "meta_and_microsoft"
    elif has_meta:
        source_class = "meta_not_microsoft"
    elif has_microsoft:
        source_class = "microsoft_not_meta"
    elif has_other:
        source_class = "other_without_meta_microsoft"
    else:
        source_class = "no_upstream"
    return {"has_dataset": bool(names), "meta": has_meta,
            "microsoft": has_microsoft, "other_upstream": has_other,
            "class": source_class, "dataset_names": names}


def _source_list_dataset_column(table):
    source_array = table.column("sources").combine_chunks()
    if not (pa.types.is_list(source_array.type) or pa.types.is_large_list(source_array.type)):
        raise TypeError(f"expected list<struct> sources column, found {source_array.type}")
    if not pa.types.is_struct(source_array.type.value_type):
        raise TypeError(f"expected sources list elements to be structs: {source_array.type}")
    if "dataset" not in source_array.type.value_type.names:
        raise ValueError("sources structs have no dataset field")
    offsets = source_array.offsets.to_numpy(zero_copy_only=False).astype(np.int64, copy=False)
    datasets = source_array.values.field("dataset").to_pylist()
    null_rows = source_array.is_null().to_numpy(zero_copy_only=False)
    return offsets, datasets, null_rows


def _shares_from_counts(counts: dict[str, np.ndarray], total: np.ndarray,
                        definitions: dict[str, str]) -> dict[str, list[float | None]]:
    features: dict[str, list[float | None]] = {}
    for feature_name, count_name in definitions.items():
        features[feature_name] = [
            float(count / n) if n else None
            for count, n in zip(counts[count_name], total)
        ]
    return features


def _aggregate_source_layer(fs, region: str, layer: str, tree, n_tracts: int,
                            classifier) -> tuple[dict[str, np.ndarray], dict]:
    key, size, pf = open_source(fs, region, layer)
    if "sources" not in pf.schema_arrow.names or "bbox" not in pf.schema_arrow.names:
        raise ValueError(f"{layer} Parquet lacks bbox/sources columns for {region}")
    selected_bytes = {name: 0 for name in ("bbox", "sources")}
    for rg in range(pf.metadata.num_row_groups):
        metadata = pf.metadata.row_group(rg)
        for col in range(pf.metadata.num_columns):
            name = pf.metadata.schema.column(col).path.split(".")[0]
            if name in selected_bytes:
                selected_bytes[name] += metadata.column(col).total_compressed_size
    if layer == "buildings":
        count_names = ("assigned_rows", "sources_missing", "osm_any", "microsoft_any", "esri_any",
                       "other_any", "class_microsoft_only", "class_microsoft_osm", "class_osm_only",
                       "class_esri_only", "class_other_or_mixed", "class_no_dataset")
    else:
        count_names = ("assigned_rows", "sources_missing", "meta_any", "microsoft_any", "other_upstream_any",
                       "class_meta_not_microsoft", "class_microsoft_not_meta", "class_meta_and_microsoft",
                       "class_other_without_meta_microsoft", "class_no_upstream")
    counts = {name: np.zeros(n_tracts, dtype=np.int64) for name in count_names}
    total_rows = unmatched_rows = null_source_rows = 0
    dataset_membership_counts = Counter()
    for rg in range(pf.metadata.num_row_groups):
        table = pf.read_row_group(rg, columns=["bbox", "sources"])
        x, y = arrow_bbox_centers(table)
        assignment, outside = assign_points(tree, x, y)
        offsets, dataset_values, null_rows = _source_list_dataset_column(table)
        if len(assignment) != len(offsets) - 1:
            raise ValueError(f"{layer} bbox/sources row mismatch in {region} rowgroup {rg}")
        batch_flags = {name: np.zeros(len(assignment), dtype=bool) for name in count_names
                       if name != "assigned_rows"}
        for row_index, tract in enumerate(assignment):
            if tract < 0:
                continue
            start, stop = int(offsets[row_index]), int(offsets[row_index + 1])
            source_items = [{"dataset": value} for value in dataset_values[start:stop] if value]
            classification = classifier(source_items)
            if not classification["has_dataset"]:
                batch_flags["sources_missing"][row_index] = True
            for dataset in classification["dataset_names"]:
                dataset_membership_counts[dataset] += 1
            if layer == "buildings":
                for flag in ("osm", "microsoft", "esri", "other"):
                    batch_flags[f"{flag}_any"][row_index] = bool(classification[flag])
            else:
                for flag in ("meta", "microsoft", "other_upstream"):
                    batch_flags[f"{flag}_any"][row_index] = bool(classification[flag])
            batch_flags[f"class_{classification['class']}"][row_index] = True
        assigned = assignment >= 0
        assigned_tracts = assignment[assigned]
        counts["assigned_rows"] += np.bincount(assigned_tracts, minlength=n_tracts)
        counts["sources_missing"] += np.bincount(
            assignment[assigned & batch_flags["sources_missing"]], minlength=n_tracts)
        for name in count_names:
            if name in ("assigned_rows", "sources_missing"):
                continue
            mask = assigned & batch_flags[name]
            counts[name] += np.bincount(assignment[mask], minlength=n_tracts)
        total_rows += len(assignment)
        unmatched_rows += outside
        null_source_rows += int(np.count_nonzero(assigned & null_rows))
        if (rg + 1) % (50 if layer == "buildings" else 25) == 0:
            print(f"{region} {layer} source rowgroups {rg + 1}/{pf.metadata.num_row_groups}; "
                  f"rows={total_rows}", flush=True)

    source_info = {
        "source_uri": f"s3://{key}", "object_bytes": size, "input_rows": total_rows,
        "row_groups": pf.metadata.num_row_groups, "selected_columns": ["bbox", "sources"],
        "selected_column_compressed_bytes": selected_bytes,
        "selected_column_compressed_bytes_total": int(sum(selected_bytes.values())),
        "assigned_to_official_sample": int(counts["assigned_rows"].sum()),
        "unmatched_outside_official_sample": unmatched_rows,
        "assigned_rows_with_null_sources_list": null_source_rows,
        "dataset_membership_counts": dict(sorted(dataset_membership_counts.items())),
        "full_object_downloaded": False,
        "overture_geometry_read": False,
        "assigned_source_counts": {name: int(values.sum()) for name, values in counts.items()},
    }
    return counts, source_info


def aggregate_source_mix(fs, region: str, tree, n_tracts: int) -> tuple[dict, dict, dict]:
    """Aggregate source shares from only bbox+sources columns for buildings and places."""
    building_counts, building_info = _aggregate_source_layer(
        fs, region, "buildings", tree, n_tracts, classify_building_sources)
    poi_counts, poi_info = _aggregate_source_layer(
        fs, region, "pois", tree, n_tracts, classify_poi_sources)
    building_total = building_counts["assigned_rows"]
    poi_total = poi_counts["assigned_rows"]
    building_map = {
        "overture_building_osm_source_share": "osm_any",
        "overture_building_microsoft_source_share": "microsoft_any",
        "overture_building_esri_source_share": "esri_any",
        "overture_building_other_source_share": "other_any",
        "overture_building_no_dataset_source_share": "class_no_dataset",
        "overture_building_ms_only_source_share": "class_microsoft_only",
        "overture_building_ms_osm_source_share": "class_microsoft_osm",
        "overture_building_osm_only_source_share": "class_osm_only",
        "overture_building_esri_only_source_share": "class_esri_only",
        "overture_building_other_or_mixed_source_share": "class_other_or_mixed",
    }
    poi_map = {
        "overture_poi_meta_source_share": "meta_any",
        "overture_poi_microsoft_source_share": "microsoft_any",
        "overture_poi_other_upstream_source_share": "other_upstream_any",
        "overture_poi_meta_not_microsoft_source_share": "class_meta_not_microsoft",
        "overture_poi_microsoft_not_meta_source_share": "class_microsoft_not_meta",
        "overture_poi_meta_and_microsoft_source_share": "class_meta_and_microsoft",
        "overture_poi_other_without_meta_microsoft_source_share": "class_other_without_meta_microsoft",
        "overture_poi_no_upstream_source_share": "class_no_upstream",
        "overture_poi_sources_missing_share": "sources_missing",
    }
    features = _shares_from_counts(building_counts, building_total, building_map)
    features.update(_shares_from_counts(poi_counts, poi_total, poi_map))
    row_counts = {
        "buildings": building_counts["assigned_rows"],
        "pois": poi_counts["assigned_rows"],
    }
    return features, row_counts, {"buildings": building_info, "pois": poi_info}


def merge_source_mix_features(cache_path: Path, sample_geoids: list[str],
                              source_features: dict, source_row_counts: dict) -> dict:
    """Append source-mix columns to the per-tract feature cache, preserving sample order."""
    table = pq.read_table(cache_path)
    ids = [str(value) for value in table.column("GEOID").to_pylist()]
    if ids != list(sample_geoids) or len(ids) != len(set(ids)):
        raise ValueError("base feature cache GEOIDs do not match official ordered sample IDs")
    source_names = set(SOURCE_MIX_FEATURE_COLUMNS)
    present_source_names = source_names & set(table.column_names)
    if present_source_names and present_source_names != source_names:
        raise ValueError("feature cache has a partial set of source-mix columns")
    base_names = [name for name in table.column_names if name not in source_names]
    base_table = table.select(base_names)
    if set(base_names) & set(source_features):
        raise ValueError("base feature cache collides with a source-mix column")
    base_building = np.asarray(base_table.column("overture_buildings").to_numpy(), dtype=np.int64)
    base_poi = np.asarray(base_table.column("overture_poi_all").to_numpy(), dtype=np.int64)
    if not np.array_equal(base_building, source_row_counts["buildings"]):
        raise ValueError("source-scan building assignment counts differ from existing feature cache")
    if not np.array_equal(base_poi, source_row_counts["pois"]):
        raise ValueError("source-scan POI assignment counts differ from existing feature cache")
    arrays = list(base_table.columns)
    names = list(base_table.column_names)
    for name in SOURCE_MIX_FEATURE_COLUMNS:
        if name not in source_features:
            raise ValueError(f"source-mix column missing: {name}")
        values = source_features[name]
        if len(values) != len(ids):
            raise ValueError(f"source-mix row count mismatch for {name}")
        arrays.append(pa.array(values, type=pa.float64()))
        names.append(name)
    extended = pa.Table.from_arrays(arrays, names=names)
    write_parquet(cache_path, extended)
    return {"rows": extended.num_rows, "columns": extended.column_names,
            "sha256": sha256(cache_path), "bytes": cache_path.stat().st_size}


def aggregate_roads(fs, region: str, tree, tract_geoms, n_tracts: int) -> tuple[np.ndarray, dict]:
    key, size, pf = open_source(fs, region, "roads")
    total = selected = unmatched = 0
    lengths_m = np.zeros(n_tracts, dtype=np.float64)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    started = time.monotonic()
    for rg, table in rowgroups(pf, ["class", "geometry"]):
        classes = table.column("class").to_pylist()
        keep = np.fromiter((value in ROAD_CLASSES for value in classes), dtype=bool, count=len(classes))
        total += len(classes)
        if not keep.any():
            continue
        selected += int(keep.sum())
        wkb = table.column("geometry").to_pylist()
        if any(value is None for i, value in enumerate(wkb) if keep[i]):
            raise ValueError("null geometry among named Overture roads")
        lines = shapely.from_wkb([value for value, yes in zip(wkb, keep) if yes])
        if np.any(shapely.is_empty(lines)) or np.any(~shapely.is_valid(lines)):
            raise ValueError("invalid geometry among named Overture roads")
        pairs = tree.query(lines, predicate="intersects")
        if not pairs.size:
            unmatched += len(lines)
            continue
        road_idx, tract_idx = pairs
        clipped = shapely.intersection(lines[road_idx], tract_geoms[tract_idx])
        metric = shapely.transform(clipped, transformer.transform, interleaved=False)
        lengths = shapely.length(metric)
        valid_length = np.isfinite(lengths) & (lengths > 0)
        lengths_m += np.bincount(tract_idx[valid_length], weights=lengths[valid_length], minlength=n_tracts)
        intersected = np.zeros(len(lines), dtype=bool)
        intersected[road_idx] = True
        unmatched += int((~intersected).sum())
        if (rg + 1) % 5 == 0:
            print(f"{region} named roads rowgroups {rg + 1}/{pf.metadata.num_row_groups}; "
                  f"selected={selected}", flush=True)
    return lengths_m / 1000.0, {"source_uri": f"s3://{key}", "object_bytes": size,
                                "input_rows": total, "row_groups": pf.metadata.num_row_groups,
                                "named_class_rows": selected, "unassigned_features": unmatched,
                                "classes": sorted(ROAD_CLASSES),
                                "elapsed_seconds": round(time.monotonic()-started, 2)}


def build_feature_cache(fs, region: str, cache_path: Path, sample_geoids: list[str],
                        include_source_mix: bool = False) -> dict:
    acs_key = f"{BUCKET}/reference/{region}/{region}-census-acs-housing.parquet"
    acs_pf = pq.ParquetFile(fs.open_input_file(acs_key))
    acs = acs_pf.read(columns=["GEOID", "geometry"])
    all_geoids = [str(x) for x in acs.column("GEOID").to_pylist()]
    if len(all_geoids) != len(set(all_geoids)):
        raise ValueError(f"duplicate ACS GEOID for {region}")
    positions = {geoid: index for index, geoid in enumerate(all_geoids)}
    missing = set(sample_geoids) - set(positions)
    if missing:
        raise ValueError(f"authoritative sample IDs absent from ACS polygons for {region}: {len(missing)}")
    wkb = acs.column("geometry").to_pylist()
    if any(value is None for value in wkb):
        raise ValueError(f"null ACS tract geometry for {region}")
    # Construct the spatial index only from authoritative scored tract IDs;
    # additional ACS polygons are not used to produce scored estimates.
    tracts = shapely.from_wkb([wkb[positions[geoid]] for geoid in sample_geoids])
    geoids = list(sample_geoids)
    if np.any(shapely.is_empty(tracts)) or np.any(~shapely.is_valid(tracts)):
        raise ValueError(f"invalid ACS tract geometry for {region}")
    tree = shapely.STRtree(tracts)
    started = time.monotonic()
    building_counts, building_info = aggregate_buildings(fs, region, tree, len(geoids))
    poi_counts, poi_info = aggregate_pois(fs, region, tree, len(geoids))
    road_lengths, road_info = aggregate_roads(fs, region, tree, tracts, len(geoids))
    source_features = source_row_counts = source_info = None
    if include_source_mix:
        source_features, source_row_counts, source_info = aggregate_source_mix(
            fs, region, tree, len(geoids))
        if (not np.array_equal(source_row_counts["buildings"], building_counts) or
                not np.array_equal(source_row_counts["pois"], poi_counts["all"])):
            raise ValueError(f"source aggregation counts do not reconcile for {region}")
    type_shares = poi_info["estimated_unclassified_type_shares"]
    arrays = [pa.array(geoids), pa.array(road_lengths), pa.array(building_counts),
              pa.array(poi_counts["all"]), pa.array(poi_counts["fire"]),
              pa.array(poi_counts["ems"]), pa.array(poi_counts["schools"]),
              pa.array(poi_counts["unclassified"]),
              pa.array([type_shares["fire"]] * len(geoids)),
              pa.array([type_shares["ems"]] * len(geoids)),
              pa.array([type_shares["schools"]] * len(geoids))]
    feature_names = ["GEOID", "overture_road_km", "overture_buildings", "overture_poi_all",
        "overture_poi_fire", "overture_poi_ems", "overture_poi_schools",
        "overture_poi_unclassified", "overture_unclassified_fire_share",
        "overture_unclassified_ems_share", "overture_unclassified_schools_share"]
    if source_features is not None:
        arrays.extend(pa.array(source_features[name], type=pa.float64())
                      for name in SOURCE_MIX_FEATURE_COLUMNS)
        feature_names.extend(SOURCE_MIX_FEATURE_COLUMNS)
    table = pa.Table.from_arrays(arrays, names=feature_names)
    write_parquet(cache_path, table)
    return {"region": region, "tracts": len(geoids), "acs_source_uri": f"s3://{acs_key}",
            "acs_rows": acs_pf.metadata.num_rows, "acs_tracts_used": len(geoids),
            "acs_extra_geometries_excluded": acs_pf.metadata.num_rows - len(geoids),
            "acs_row_groups": acs_pf.metadata.num_row_groups,
            "buildings": building_info, "pois": poi_info, "roads": road_info,
            "source_mix": source_info,
            "feature_cache": str(cache_path), "feature_cache_sha256": sha256(cache_path),
            "aggregate_schema_version": 5 if include_source_mix else 4,
            "elapsed_seconds": round(time.monotonic()-started, 2)}


def load_sample_and_strata(region: str, data_root: Path) -> tuple[list[dict], dict[str, dict]]:
    strata_path = data_root / f"{region}-strata-tract-table.parquet"
    sample_path = data_root / f"{region}-sample-submission.csv"
    if not strata_path.is_file() or not sample_path.is_file():
        raise FileNotFoundError(f"missing sample or strata for {region} under {data_root}")
    # Parse only the ID column from the sample. Its score fields are contract
    # placeholders and are never loaded into prediction records.
    with sample_path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader, [])
        if "GEOID" not in header:
            raise ValueError(f"sample has no GEOID column: {sample_path}")
        geoid_index = header.index("GEOID")
        ids = [row[geoid_index] for row in reader]
    sample = [{"GEOID": geoid} for geoid in ids]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate sample GEOID: {region}")
    wanted = ["GEOID", "ALAND", "pop_total", "pop_urban", "pct_urban", "ur_class"]
    table = pq.read_table(strata_path, columns=wanted)
    strata = {str(row["GEOID"]): row for row in table.to_pylist()}
    if len(strata) != table.num_rows or not set(ids).issubset(strata):
        raise ValueError(f"strata GEOID mismatch: {region}")
    for geoid in ids:
        row = strata[geoid]
        if row["ALAND"] is None or row["pop_total"] is None or row["pop_urban"] is None:
            raise ValueError(f"null core strata covariate for {region} {geoid}")
    return sample, strata


def component_gap(observed: float, expected: float, defined: int) -> float:
    """Return a bounded gap; undefined components are excluded from means."""
    if not defined:
        return 0.0
    return min(1.0, max(0.0, 1.0 - observed / max(expected, 0.5)))


def combine_poi_gap(facility_gaps, cbp_gap: float, cbp_defined: int) -> tuple[float | None, int]:
    """Combine facility and establishment halves with equal half weight."""
    defined_facilities = [float(value) for value, defined in facility_gaps if defined]
    halves = []
    if defined_facilities:
        halves.append(sum(defined_facilities) / len(defined_facilities))
    if cbp_defined:
        halves.append(float(cbp_gap))
    if not halves:
        return None, 0
    return sum(halves) / len(halves), 1


def composite_mean(components) -> float | None:
    """Mean only defined components; return None when no composite exists."""
    values = [float(value) for value, defined in components if defined]
    return sum(values) / len(values) if values else None


def validate_exact_geoid_coverage(actual_ids, expected_ids) -> None:
    """Require exact ordered coverage with no missing, extra, or duplicate IDs."""
    actual, expected = list(actual_ids), list(expected_ids)
    if actual != expected or len(actual) != len(set(actual)):
        raise ValueError(
            f"GEOID coverage mismatch: actual={len(actual)} unique={len(set(actual))}; "
            f"expected={len(expected)}"
        )


def urban_fraction(row: dict) -> tuple[float, str]:
    value = row.get("pct_urban")
    if value is not None and math.isfinite(float(value)):
        return min(1.0, max(0.0, float(value))), "pct_urban"
    pop = float(row["pop_total"])
    urban_pop = float(row["pop_urban"])
    if pop > 0:
        return min(1.0, max(0.0, urban_pop / pop)), "pop_urban/pop_total"
    # Structural urbanity is unavailable on zero-population tracts. Use a
    # neutral prior, not a zero fill, and disclose every such case in the audit.
    return 0.5, "neutral_missing_urbanity_prior"


def facility_reference_presence_probability_v2(
    facility_type: str, covariates: dict, prior: dict | None = None
) -> tuple[float, float, str]:
    """Return fixed-prior P(reference type present), urbanity, and density index.

    The three coefficients are set by qualitative domain assumptions before
    looking at any target/leaderboard score. Region is deliberately pooled:
    there is no defensible region-specific reference prior without labels.
    Regional outputs are diagnostics only, never inputs to these coefficients.
    """
    if facility_type not in FACILITY_MISS_PRIORS and prior is None:
        raise ValueError(f"unknown facility type: {facility_type}")
    coefficients = prior or FACILITY_MISS_PRIORS[facility_type]
    urban, source = urban_fraction(covariates)
    population = float(covariates["pop_total"])
    land_area = float(covariates["ALAND"])
    if not math.isfinite(population) or population < 0:
        raise ValueError("pop_total must be finite and nonnegative")
    if not math.isfinite(land_area) or land_area < 0:
        raise ValueError("ALAND must be finite and nonnegative")
    area_km2 = max(land_area / 1_000_000.0, 0.01)
    density_index = math.log1p((population / area_km2) / 100.0)
    logit = (float(coefficients["intercept"])
             + float(coefficients["log_density"]) * density_index
             + float(coefficients["urban_fraction"]) * urban)
    probability = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit))))
    return probability, urban, source


def expected_facility_miss_gap_v2(
    probability_present: float, overture_count: float
) -> dict[str, int | float]:
    """Return E[gap] under a one-count-ahead reference-count prior.

    Conditional on the reference being present, use n_ref_prior=n_overture+1;
    this makes the mechanism exact at n_overture=0 (gap 1) and yields a
    shrinking positive-count gap otherwise. The expected gap is P(present)
    times that conditional gap. P(present)=0 means the reference is certainly
    absent, so the component is undefined, not a fabricated zero observation.
    """
    try:
        probability = float(probability_present)
        observed = float(overture_count)
    except (TypeError, ValueError) as exc:
        raise ValueError("probability and Overture count must be finite numbers") from exc
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError("reference-presence probability must be finite and in [0, 1]")
    if not math.isfinite(observed) or observed < 0:
        raise ValueError("Overture count must be finite and nonnegative")
    if probability == 0.0:
        return {"gap": 0.0, "defined": 0, "conditional_gap_if_present": 0.0}
    n_ref_prior = observed + 1.0
    conditional_gap = min(1.0, max(0.0, 1.0 - observed / n_ref_prior))
    return {"gap": probability * conditional_gap, "defined": 1,
            "conditional_gap_if_present": conditional_gap}


def estimate_poi_facility_miss_v2(
    features: dict, covariates: dict, k_pop: float = FACILITY_MISS_K_POP_ANALYSIS
) -> dict:
    """Model POI as an expectation from provided features and explicit task scalars."""
    type_columns = {
        "fire": "overture_poi_fire",
        "ems": "overture_poi_ems",
        "schools": "overture_poi_schools",
    }
    expected_gaps = []
    type_results = {}
    unclassified = float(features.get("overture_poi_unclassified", 0.0))
    for facility_type, column in type_columns.items():
        try:
            raw_observed = float(features[column])
            unknown_share = float(features.get(f"overture_unclassified_{facility_type}_share", 0.0))
            observed = raw_observed + unclassified * unknown_share
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"missing/invalid provided count for {column}") from exc
        if not math.isfinite(unknown_share) or not 0.0 <= unknown_share <= 1.0:
            raise ValueError(f"invalid unclassified {facility_type} share")
        probability, _, _ = facility_reference_presence_probability_v2(
            facility_type, covariates)
        estimate = expected_facility_miss_gap_v2(probability, observed)
        type_results[facility_type] = {
            "reference_presence_probability": probability,
            "raw_selected_category_count": raw_observed,
            "observed_overture_count_after_unclassified_share": observed,
            **estimate,
        }
        expected_gaps.append((float(estimate["gap"]), int(estimate["defined"])))
    facility_values = [gap for gap, defined in expected_gaps if defined]
    facility_gap = sum(facility_values) / len(facility_values) if facility_values else 0.0
    facility_defined = int(bool(facility_values))
    establishment = estimate_establishments_component_v2(
        float(features["overture_poi_all"]), float(covariates["pop_total"]), k_pop)
    poi_gap, poi_defined = combine_poi_gap(
        expected_gaps, float(establishment["establishments_gap"]),
        int(establishment["establishments_defined"]))
    urban, urban_source = urban_fraction(covariates)
    population = float(covariates["pop_total"])
    area_km2 = max(float(covariates["ALAND"]) / 1_000_000.0, 0.01)
    density_index = math.log1p((population / area_km2) / 100.0)
    return {
        "poi_gap": float(poi_gap) if poi_gap is not None else 0.0,
        "poi_defined": int(poi_defined),
        "facility_gap": facility_gap,
        "facility_defined": facility_defined,
        "establishments_gap": float(establishment["establishments_gap"]),
        "establishments_defined": int(establishment["establishments_defined"]),
        "facility_types": type_results,
        "urban_fraction": urban,
        "urbanity_source": urban_source,
        "log_density_index": density_index,
    }


def estimate_one(features: dict, covariates: dict, scale: float = 1.0) -> dict:
    pop = float(covariates["pop_total"])
    land_km2 = max(float(covariates["ALAND"]) / 1_000_000.0, 0.01)
    urban, urban_source = urban_fraction(covariates)
    road = float(features["overture_road_km"])
    buildings = float(features["overture_buildings"])
    poi_all = float(features["overture_poi_all"])
    fire = float(features["overture_poi_fire"])
    ems = float(features["overture_poi_ems"])
    schools = float(features["overture_poi_schools"])
    unclassified_pois = float(features["overture_poi_unclassified"])
    fire_unknown_share = float(features["overture_unclassified_fire_share"])
    ems_unknown_share = float(features["overture_unclassified_ems_share"])
    schools_unknown_share = float(features["overture_unclassified_schools_share"])

    # Explicit proxy denominators; these are not labels or reconstructions.
    exp_road = scale * (0.35 + 0.55 * math.sqrt(land_km2) + 0.0007 * pop + 2.0 * urban)
    exp_building = scale * max(0.5, 0.30 * pop + 4.0 * math.sqrt(land_km2) + 5.0 * urban)
    exp_fire = scale * (0.15 + pop / 30000.0 + 0.45 * urban)
    exp_ems = scale * (0.15 + pop / 40000.0 + 0.45 * urban)
    exp_schools = scale * (0.20 + pop / 2500.0 + 0.20 * urban)
    exp_cbp = scale * (0.20 + pop / 70.0 + 0.50 * urban)

    flags = {
        "transport_defined": int(exp_road >= 6.0),
        "building_defined": int(buildings > 0 or exp_building >= 1.0),
        "fire_defined": int(fire > 0 or exp_fire >= 0.50),
        "ems_defined": int(ems > 0 or exp_ems >= 0.50),
        "schools_defined": int(schools > 0 or exp_schools >= 0.50),
        "cbp_defined": int(poi_all > 0 or exp_cbp >= 1.0),
    }
    transport_gap = component_gap(road, exp_road, flags["transport_defined"])
    building_gap = component_gap(buildings, exp_building, flags["building_defined"])
    # Apportion uncategorized POIs by the in-sample category share among
    # categorized Overture POIs; unknown types are never treated as zero.
    fire_observed = fire + unclassified_pois * fire_unknown_share
    ems_observed = ems + unclassified_pois * ems_unknown_share
    schools_observed = schools + unclassified_pois * schools_unknown_share
    fire_gap = component_gap(fire_observed, exp_fire, flags["fire_defined"])
    ems_gap = component_gap(ems_observed, exp_ems, flags["ems_defined"])
    schools_gap = component_gap(schools_observed, exp_schools, flags["schools_defined"])
    cbp_gap = component_gap(poi_all, exp_cbp, flags["cbp_defined"])
    poi_gap_value, flags["poi_defined"] = combine_poi_gap(
        ((fire_gap, flags["fire_defined"]), (ems_gap, flags["ems_defined"]),
         (schools_gap, flags["schools_defined"])), cbp_gap, flags["cbp_defined"])
    poi_gap = poi_gap_value if poi_gap_value is not None else 0.0
    score = composite_mean(((transport_gap, flags["transport_defined"]),
                            (building_gap, flags["building_defined"]),
                            (poi_gap, flags["poi_defined"])))
    return {"transport_gap": transport_gap, "transport_defined": flags["transport_defined"],
            "building_gap": building_gap, "building_defined": flags["building_defined"],
            "poi_gap": poi_gap, "poi_defined": flags["poi_defined"],
            "fire_gap": fire_gap, "fire_defined": flags["fire_defined"],
            "ems_gap": ems_gap, "ems_defined": flags["ems_defined"],
            "schools_gap": schools_gap, "schools_defined": flags["schools_defined"],
            "cbp_gap": cbp_gap, "cbp_defined": flags["cbp_defined"],
            "coverage_gap_score": score, "urbanity_source": urban_source,
            "poi_unclassified_feature_present": int(unclassified_pois > 0),
            "poi_uncategorized_type_imputations": unclassified_pois *
                (fire_unknown_share + ems_unknown_share + schools_unknown_share)}


def _poi_v2_group_summary(records: list[dict], baseline_by_geoid: dict[str, dict]) -> dict:
    if not records:
        return {"tracts": 0}
    poi_values = [float(row["poi_gap"]) for row in records if row["poi_defined"]]
    v0_poi_values = [float(baseline_by_geoid[row["GEOID"]]["poi_gap"])
                     for row in records
                     if baseline_by_geoid[row["GEOID"]]["poi_defined"] == "1"]
    composite_values = [float(row["composite_gap"]) for row in records
                        if row["composite_gap"] is not None]
    v0_composite_values = [float(baseline_by_geoid[row["GEOID"]]["coverage_gap_score"])
                           for row in records
                           if baseline_by_geoid[row["GEOID"]].get("coverage_gap_score", "") not in ("", None)]
    result = {
        "tracts": len(records),
        "poi_defined_tracts": len(poi_values),
        "poi_v2_mean_defined": float(np.mean(poi_values)) if poi_values else None,
        "poi_v2_E_p_squared_defined": float(np.mean(np.square(poi_values))) if poi_values else None,
        "poi_v2_share_gt_0_25_defined": (sum(value > 0.25 for value in poi_values) / len(poi_values)
                                          if poi_values else None),
        "facility_half_mean_defined": float(np.mean([row["facility_gap"] for row in records
                                                       if row["facility_defined"]]))
        if any(row["facility_defined"] for row in records) else None,
        "establishments_half_mean_defined": float(np.mean([row["establishments_gap"] for row in records
                                                            if row["establishments_defined"]]))
        if any(row["establishments_defined"] for row in records) else None,
        "v0_poi_defined_tracts": len(v0_poi_values),
        "v0_poi_mean_defined": float(np.mean(v0_poi_values)) if v0_poi_values else None,
        "v0_poi_E_p_squared_defined": float(np.mean(np.square(v0_poi_values))) if v0_poi_values else None,
        "v0_composite_mean_defined": float(np.mean(v0_composite_values)) if v0_composite_values else None,
        "v0_composite_E_p_squared_defined": (float(np.mean(np.square(v0_composite_values)))
                                              if v0_composite_values else None),
        "v2_poi_with_v0_transport_building_composite_tracts": len(composite_values),
        "v2_poi_with_v0_transport_building_composite_mean": (
            float(np.mean(composite_values)) if composite_values else None),
        "v2_poi_with_v0_transport_building_composite_E_p_squared": (
            float(np.mean(np.square(composite_values))) if composite_values else None),
        "prior_probability_mean_by_type": {
            name: float(np.mean([row["facility_types"][name]["reference_presence_probability"]
                                 for row in records]))
            for name in ("fire", "ems", "schools")
        },
        "expected_facility_gap_mean_by_type": {
            name: float(np.mean([row["facility_types"][name]["gap"] for row in records]))
            for name in ("fire", "ems", "schools")
        },
        "observed_overture_zero_counts": {
            "fire0": sum(row["overture_poi_fire"] == 0 for row in records),
            "ems0": sum(row["overture_poi_ems"] == 0 for row in records),
            "sch0": sum(row["overture_poi_schools"] == 0 for row in records),
        },
        "urbanity_fallback_tracts": sum(row["urbanity_source"] != "pct_urban" for row in records),
    }
    return result


def run_poi_facility_miss_audit(
    workspace: Path,
    data_root: Path,
    feature_root: Path,
    v0_path: Path,
    out_path: Path,
    k_pop: float = FACILITY_MISS_K_POP_ANALYSIS,
) -> dict:
    """Run the score-free facility-miss POI model and write aggregate diagnostics."""
    if not math.isfinite(k_pop) or k_pop <= 0:
        raise ValueError("analysis k_pop must be finite and positive")
    with v0_path.open("r", newline="", encoding="utf-8-sig") as stream:
        v0_rows = list(csv.DictReader(stream))
    baseline_by_geoid = {row["GEOID"]: row for row in v0_rows}
    if len(baseline_by_geoid) != len(v0_rows):
        raise ValueError("duplicate GEOID in v0 gap estimates")

    regional_rows: dict[str, list[dict]] = {}
    input_manifest = {}
    official_ids = []
    for region in REGIONS:
        sample, strata = load_sample_and_strata(region, data_root)
        ids = [row["GEOID"] for row in sample]
        if set(official_ids) & set(ids):
            raise ValueError(f"official GEOID repeated across regions: {region}")
        official_ids.extend(ids)
        feature_path = feature_root / f"{region}-overture-tract-summary.parquet"
        if not feature_path.is_file():
            raise FileNotFoundError(f"provided Overture feature cache missing: {feature_path}")
        table = pq.read_table(feature_path, columns=[
            "GEOID", "overture_poi_all", "overture_poi_fire", "overture_poi_ems",
            "overture_poi_schools", "overture_poi_unclassified",
            "overture_unclassified_fire_share", "overture_unclassified_ems_share",
            "overture_unclassified_schools_share",
        ])
        feature_rows = table.to_pylist()
        if [str(row["GEOID"]) for row in feature_rows] != ids:
            raise ValueError(f"feature cache order/IDs do not match {region} official sample")
        if set(ids) - set(baseline_by_geoid):
            raise ValueError(f"v0 estimates missing official IDs for {region}")
        regional_rows[region] = []
        for feature in feature_rows:
            geoid = str(feature["GEOID"])
            estimate = estimate_poi_facility_miss_v2(feature, strata[geoid], k_pop)
            base = baseline_by_geoid[geoid]
            composite = composite_mean((
                (float(base["transport_gap"]), int(base["transport_defined"])),
                (float(base["building_gap"]), int(base["building_defined"])),
                (float(estimate["poi_gap"]), int(estimate["poi_defined"])),
            ))
            regional_rows[region].append({
                "GEOID": geoid,
                "region": region,
                "cell": "rural" if estimate["urban_fraction"] < 0.5 else "urban",
                "urbanity_source": estimate["urbanity_source"],
                "overture_poi_fire": float(feature["overture_poi_fire"]),
                "overture_poi_ems": float(feature["overture_poi_ems"]),
                "overture_poi_schools": float(feature["overture_poi_schools"]),
                "poi_gap": float(estimate["poi_gap"]),
                "poi_defined": int(estimate["poi_defined"]),
                "facility_gap": float(estimate["facility_gap"]),
                "facility_defined": int(estimate["facility_defined"]),
                "establishments_gap": float(estimate["establishments_gap"]),
                "establishments_defined": int(estimate["establishments_defined"]),
                "facility_types": estimate["facility_types"],
                "composite_gap": composite,
            })
        sample_path = data_root / f"{region}-sample-submission.csv"
        strata_path = data_root / f"{region}-strata-tract-table.parquet"
        input_manifest[region] = {
            "official_sample": {"rows": len(sample), "sha256": sha256(sample_path),
                                "score_placeholder_values_used": False},
            "provided_strata": {"rows": len(strata), "sha256": sha256(strata_path),
                                 "columns_used": ["GEOID", "ALAND", "pop_total", "pop_urban", "pct_urban"]},
            "provided_overture_poi_summary": {
                "rows": table.num_rows, "sha256": sha256(feature_path),
                "columns_used": ["GEOID", "overture_poi_all", "overture_poi_fire",
                                  "overture_poi_ems", "overture_poi_schools",
                                  "overture_poi_unclassified", "overture_unclassified_fire_share",
                                  "overture_unclassified_ems_share", "overture_unclassified_schools_share"],
            },
        }
    if len(official_ids) != 9379 or set(official_ids) != set(baseline_by_geoid):
        raise ValueError("v0 baseline and official feature IDs must exactly match 9,379 tracts")

    groups: dict[str, list[dict]] = {"all": []}
    for region in REGIONS:
        groups[region] = regional_rows[region]
        for cell in ("rural", "urban"):
            groups[f"{region}:{cell}"] = [row for row in regional_rows[region] if row["cell"] == cell]
        groups["all"].extend(regional_rows[region])
    summaries = {key: _poi_v2_group_summary(rows, baseline_by_geoid)
                 for key, rows in groups.items()}
    regional_mean_checks = {
        region: {
            "mean": summaries[region]["poi_v2_mean_defined"],
            "within_required_0_02_to_0_20": (
                summaries[region]["poi_v2_mean_defined"] is not None and
                0.02 <= summaries[region]["poi_v2_mean_defined"] <= 0.20
            ),
        }
        for region in REGIONS
    }
    if not all(item["within_required_0_02_to_0_20"] for item in regional_mean_checks.values()):
        raise ValueError(f"facility-miss POI region means outside required range: {regional_mean_checks}")
    report = {
        "analysis": "fixed-prior facility-miss POI v2 model-only audit",
        "model": {
            "prior_coefficients_fixed_before_any_target_or_leaderboard_score_viewed": True,
            "prior_type": "region-pooled logistic prior for reference presence; coefficients are subjective monotonic domain assumptions, not fitted to hidden scores, organizer region/cell moments, or leaderboard feedback",
            "logistic_formula": "P(ref_t present) = sigmoid(intercept_t + beta_density_t * log1p((pop_total / ALAND_km2) / 100) + beta_urban_t * urban_fraction)",
            "region_handling": "fully pooled; region is used only for audit tables because no allowed target evidence supports region-specific intercepts",
            "parameters_per_type": FACILITY_MISS_PRIORS,
            "feature_transforms": {
                "ALAND_km2": "max(ALAND / 1,000,000, 0.01)",
                "density_index": "log1p((pop_total / ALAND_km2) / 100)",
                "urban_fraction": "pct_urban, else pop_urban/pop_total when population > 0, else neutral 0.5",
            },
            "overture_type_count": "selected exact-category count plus the provided-summary unclassified count times the region's in-sample selected-category share, matching v0 handling of unclassified POIs",
            "conditional_gap_if_reference_present": "clip(1 - n_overture / (n_overture + 1), 0, 1); one-count-ahead model prior, not a measured reference denominator",
            "expected_type_gap": "P(ref_t present) * conditional_gap_if_reference_present",
            "undefined_rule": "P(ref_t present) == 0 means reference absence is certain: return numeric placeholder 0 with defined=0",
            "facility_half": "mean of defined expected fire, EMS, and school type gaps",
            "establishments_half": {
                "formula": "clip(1 - overture_poi_all / (k_pop * pop_total), 0, 1)",
                "analysis_k_pop": k_pop,
                "source": "task-specified approximate national-rate sensitivity anchor; no CBP data/table read",
                "scored_use_authorized": False,
                "zero_population": "positive POI count -> zero gap, defined; zero POIs -> undefined",
            },
            "poi_v2": "equal mean of defined facilities and establishments halves",
            "score_or_reference_data_used_for_fit": False,
            "region_or_cell_target_means_used_for_fit": False,
        },
        "comparison": {
            "v0_poi_mean_overall": summaries["all"]["v0_poi_mean_defined"],
            "v0_poi_mean_from_task_measure_before": 0.257,
            "v0_poi_mean_source": "local reports/gap-estimates.csv; the task MEASURE-BEFORE 0.257 is reported for comparison only, not used to fit priors",
            "v2_poi_regional_mean_acceptance": regional_mean_checks,
            "region_mean_constraint_rmse": None,
            "region_mean_constraint_rmse_not_computed_reason": "No region-mean target results are used or available to fit; the operator amendment forbids region/cell target-moment calibration. No labels are available.",
        },
        "summaries": summaries,
        "inputs": input_manifest,
        "poi_category_contract": {
            "path": "config/poi-category-definitions.json",
            "sha256": sha256(Path(__file__).resolve().parents[1] / "config/poi-category-definitions.json"),
            "selected_sets_source": "provided Overture feature-cache construction uses this shared exact-category contract",
        },
        "restrictions": {
            "removed_reference_layers_or_equivalents_read": False,
            "external_data_or_numeric_files_read": False,
            "sample_score_placeholders_used": False,
            "labels_or_leaderboard_scores_read": False,
            "per_geoid_output_written": False,
            "candidate_or_submission_file_created_or_modified": False,
            "leaderboard_probe_or_calibration_performed": False,
        },
        "limitations": [
            "The logistic coefficients and one-count-ahead conditional reference-count prior are explicit domain assumptions, not validated against labels.",
            "Region is pooled; regional differences in the report are feature-distribution diagnostics, not fitted region effects.",
            "The .024 establishments rate is an analysis-only planning anchor and is not authorized for any scored candidate without a separately compliant approval.",
            "Composite summaries hold v0 transport/building terms fixed and replace only the POI term; this is not an assembled v2 candidate.",
            "No predicted RMSE or target-mean fit is possible without labels, and the operator amendment prohibits fitting to region/cell target moments.",
        ],
        "rows": 9379,
        "v0_estimates": {"path": str(v0_path.relative_to(Path(__file__).resolve().parents[1])),
                          "sha256": sha256(v0_path)},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8")
    return report


def write_predictions(path: Path, records: list[dict], columns=OUTPUT_COLUMNS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows({key: record[key] for key in columns} for record in records)


def make_candidate_records(records: list[dict]) -> list[dict]:
    return [{key: row[key] for key in SUBMISSION_COLUMNS} for row in records]


def candidate_stats(records: list[dict]) -> dict:
    return {"rows": len(records),
            "per_region_means": {region: round(sum(r["coverage_gap_score"] for r in records
                if r["region"] == region) / sum(r["region"] == region for r in records), 9)
                for region in REGIONS},
            "per_region_defined_rates": {region: {
                flag: round(sum(r[flag] for r in records if r["region"] == region) /
                            sum(r["region"] == region for r in records), 9)
                for flag in ("transport_defined", "building_defined", "poi_defined",
                             "fire_defined", "ems_defined", "schools_defined", "cbp_defined")}
                for region in REGIONS}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, help="workspace directory with official samples and strata Parquets")
    parser.add_argument("--workspace", type=Path, help="workspace cache root; default COMPETITION_WORKSPACE")
    parser.add_argument("--out", type=Path, default=Path("reports/gap-estimates.csv"))
    parser.add_argument("--audit-out", type=Path, default=Path("reports/gap-estimator-audit.json"))
    parser.add_argument("--manifest", type=Path, default=Path("reports/candidates.json"))
    parser.add_argument("--poi-model", choices=("v0", "facility-miss"), default="v0",
                        help="select the legacy v0 POI proxy or the score-free facility-miss v2 audit")
    parser.add_argument("--poi-audit-out", type=Path,
                        default=Path("reports/poi-facility-miss-audit.json"),
                        help="aggregate report path used by --poi-model facility-miss")
    parser.add_argument("--refresh-features", action="store_true", help="re-scan allowed Overture feature columns")
    parser.add_argument("--include-source-mix", action="store_true",
                        help="include source-provenance shares; scans only sources+bbox for those source fields")
    args = parser.parse_args()
    workspace = args.workspace or (Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    if workspace is None:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    data_root = args.data_root or workspace / "defined-flags-inputs"
    feature_root = workspace / "gap-estimator" / "features"
    if args.poi_model == "facility-miss":
        if args.refresh_features or args.include_source_mix:
            parser.error("facility-miss audit uses existing permitted summary caches; feature refresh/source-mix flags are unsupported")
        report = run_poi_facility_miss_audit(
            workspace.resolve(), data_root.resolve(), feature_root.resolve(),
            (Path(__file__).resolve().parents[1] / "reports/gap-estimates.csv"),
            args.poi_audit_out.resolve(),
        )
        print(f"facility-miss POI v2 report: {args.poi_audit_out.resolve()}")
        print("POI means by region: " + json.dumps(report["comparison"]["v2_poi_regional_mean_acceptance"], sort_keys=True))
        print("No candidate or scored file was created or modified.")
        return 0
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    all_records = []
    region_info = {}
    region_data = {}
    input_hashes = {}
    max_composite_error = 0.0
    for region in REGIONS:
        sample_path = data_root / f"{region}-sample-submission.csv"
        strata_path = data_root / f"{region}-strata-tract-table.parquet"
        sample, strata = load_sample_and_strata(region, data_root)
        feature_path = feature_root / f"{region}-overture-tract-summary.parquet"
        feature_meta_path = feature_path.with_suffix(".json")
        cached_meta = (json.loads(feature_meta_path.read_text(encoding="utf-8"))
                       if feature_meta_path.is_file() else {})
        acceptable_cache_versions = {5} if args.include_source_mix else {4, 5}
        source_cache_missing = (cached_meta.get("aggregate_schema_version") == 5 and
                                not set(SOURCE_MIX_FEATURE_COLUMNS).issubset(
                                    set(pq.read_schema(feature_path).names)
                                    if feature_path.is_file() else set()))
        if (args.refresh_features or not feature_path.is_file() or source_cache_missing or
                cached_meta.get("aggregate_schema_version") not in acceptable_cache_versions):
            info = build_feature_cache(fs, region, feature_path, [row["GEOID"] for row in sample],
                                       include_source_mix=args.include_source_mix)
            feature_meta_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        else:
            info = cached_meta
            info["cache_reused"] = True
            info["feature_cache_sha256"] = sha256(feature_path)
        summary = {str(row["GEOID"]): row for row in pq.read_table(feature_path).to_pylist()}
        ids = [row["GEOID"] for row in sample]
        if (len(summary) != pq.ParquetFile(feature_path).metadata.num_rows or
                set(summary) != set(ids)):
            raise ValueError(f"feature summary GEOID join mismatch: {region}")
        missing_urban = 0
        region_records = []
        for sample_row in sample:
            geoid = sample_row["GEOID"]
            estimate = estimate_one(summary[geoid], strata[geoid])
            missing_urban += estimate["urbanity_source"] == "neutral_missing_urbanity_prior"
            region_records.append({"region": region, "GEOID": geoid, **estimate})
        all_records.extend(region_records)
        region_data[region] = {"features": summary, "strata": strata, "sample": sample}
        info.update({"sample_rows": len(sample), "strata_rows": len(strata),
                     "feature_summary_rows": len(summary),
                     "neutral_urbanity_fallbacks": int(missing_urban),
                     "feature_summary_unmatched_sample_ids": len(set(ids) - set(summary)),
                     "source_hashes": {sample_path.name: sha256(sample_path),
                                       strata_path.name: sha256(strata_path)}})
        region_info[region] = info
        input_hashes.update(info["source_hashes"])
        input_hashes[feature_path.name] = sha256(feature_path)
    expected_ids = [row["GEOID"] for region in REGIONS for row in region_data[region]["sample"]]
    validate_exact_geoid_coverage((row["GEOID"] for row in all_records), expected_ids)
    if len(all_records) != 9379:
        raise ValueError(f"expected 9,379 authoritative rows, got {len(all_records)}")
    for row in all_records:
        for column in OUTPUT_COLUMNS[1:]:
            if column.endswith("_defined"):
                if row[column] not in (0, 1):
                    raise ValueError(f"invalid defined flag {column}: {row[column]}")
            elif row[column] is None or not math.isfinite(float(row[column])) or not 0 <= float(row[column]) <= 1:
                raise ValueError(f"invalid score {column}: {row[column]}")
        defined_values = [row["transport_gap"] for _ in [0] if row["transport_defined"]]
        defined_values += [row["building_gap"] for _ in [0] if row["building_defined"]]
        defined_values += [row["poi_gap"] for _ in [0] if row["poi_defined"]]
        expected = sum(defined_values) / len(defined_values) if defined_values else 0.5
        error = abs(expected - row["coverage_gap_score"])
        max_composite_error = max(max_composite_error, error)
        if error > 1e-9:
            raise ValueError(f"composite consistency failure: {row['GEOID']}")
    write_predictions(args.out, all_records)

    # Candidate output schema is the official five-column sample; detailed
    # component flags/gaps remain in the estimator output and audit.
    c2 = make_candidate_records(all_records)
    c2_path = Path("reports/candidates/C2_component_estimator_v0.csv")
    write_predictions(c2_path, c2, SUBMISSION_COLUMNS)
    c3_records = []
    for region in REGIONS:
        source = region_data[region]
        for sample_row in source["sample"]:
            geoid = sample_row["GEOID"]
            c3_records.append({"region": region, "GEOID": geoid,
                **estimate_one(source["features"][geoid], source["strata"][geoid], scale=1.25)})
    c3_path = Path("reports/candidates/C3_denominator_x125.csv")
    write_predictions(c3_path, make_candidate_records(c3_records), SUBMISSION_COLUMNS)

    # C1 is a region-specific constant equal to the v0 mean in that region.
    regional_means = {region: sum(row["coverage_gap_score"] for row in all_records
        if row["region"] == region) / sum(row["region"] == region for row in all_records)
        for region in REGIONS}
    c1_records = []
    for row in all_records:
        value = regional_means[row["region"]]
        c1_records.append({"GEOID": row["GEOID"], "transport_gap": value,
                           "building_gap": value, "poi_gap": value,
                           "coverage_gap_score": value})
    c1_path = Path("reports/candidates/C1_region_constants.csv")
    write_predictions(c1_path, c1_records, SUBMISSION_COLUMNS)

    manifest_path = args.manifest
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = {"schema_version": 1, "competition": "zindi-bias-bounty",
                    "authoritative_regions": list(REGIONS)}
    candidates = []
    id_to_region = {row["GEOID"]: row["region"] for row in all_records}
    candidate_definitions = [
        ("C0", "all-zero baseline", Path("reports/candidates/C0_all_zero.csv"),
         "All score fields zero; target-RMS baseline.", None),
        ("C1", "per-region v0 mean constants", c1_path,
         "Within each region all four official score columns equal that region's C2 coverage-score prediction mean.", all_records),
        ("C2", "component estimator v0", c2_path,
         "Official five score columns projected from the full component estimator output.", all_records),
        ("C3", "denominator sensitivity x1.25", c3_path,
         "Same v0 covariates and observed counts; multiply all expected-reference proxies by 1.25.", c3_records),
    ]
    for candidate_id, name, candidate_path, description, flag_records in candidate_definitions:
        if not candidate_path.is_file():
            continue
        candidate_rows = list(csv.DictReader(candidate_path.open("r", newline="", encoding="utf-8")))
        candidates.append({"candidate_id": candidate_id, "name": name,
            "artifact": str(candidate_path), "sha256": sha256(candidate_path),
            "rows": len(candidate_rows), "columns": list(candidate_rows[0]) if candidate_rows else [],
            "pre_registered": True, "description": description,
            "column_checks": {"unique_columns": len(candidate_rows[0]) == len(set(candidate_rows[0])) if candidate_rows else False,
                "all_rows_have_columns": all(list(row) == list(candidate_rows[0]) for row in candidate_rows) if candidate_rows else False,
                "finite_bounded_scores": all(math.isfinite(float(row[column])) and 0 <= float(row[column]) <= 1
                    for row in candidate_rows for column in SUBMISSION_COLUMNS if column != "GEOID")},
            "per_region_means": {region: round(sum(float(r["coverage_gap_score"]) for r in candidate_rows
                if id_to_region.get(r["GEOID"]) == region) /
                sum(id_to_region.get(r["GEOID"]) == region for r in candidate_rows), 9) for region in REGIONS},
            "defined_rates": (candidate_stats(flag_records)["per_region_defined_rates"]
                              if flag_records is not None else None)})
    manifest.update({"authoritative_regions": list(REGIONS), "candidate_set": candidates,
        "feature_source": "provided Overture buildings/roads/POIs plus provided ACS housing geometries and strata covariates",
        "estimator_version": "v0"})
    manifest.pop("future_pre_registered_candidates", None)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    rule_any_undefined = {"maricopa-az": 0.55, "northern-ca": 0.37,
                          "eastern-ok": 0.21, "south-central-tx": 0.28}
    region_audit = {}
    for region in REGIONS:
        rr = [row for row in all_records if row["region"] == region]
        n_any_undefined = sum(not (r["transport_defined"] and r["building_defined"] and r["poi_defined"])
                              for r in rr)
        region_audit[region] = {"rows": len(rr),
            "defined_rates": {flag: round(sum(r[flag] for r in rr) / len(rr), 6)
                for flag in ("transport_defined", "building_defined", "poi_defined", "fire_defined",
                             "ems_defined", "schools_defined", "cbp_defined")},
            "rows_with_any_undefined_component": n_any_undefined,
            "fraction_with_any_undefined_component": round(n_any_undefined / len(rr), 6),
            "rows_with_unclassified_poi_features": sum(r["poi_unclassified_feature_present"] for r in rr),
            "poi_uncategorized_type_imputations": round(
                sum(r["poi_uncategorized_type_imputations"] for r in rr), 6),
            "official_rules_any_undefined_reference_fraction": rule_any_undefined[region],
            "estimate_minus_rules_context": round(n_any_undefined / len(rr) - rule_any_undefined[region], 6),
            "mean_scores": {name: round(sum(float(r[name]) for r in rr) / len(rr), 6)
                for name in ("transport_gap", "building_gap", "poi_gap", "coverage_gap_score")}}
    audit = {"method": "deterministic provided-feature proxy estimator; no labels or removed reference layers",
        "no_accuracy_claim": True,
        "source_constraints": {"provided_layers_used": ["Overture roads", "Overture buildings",
            "Overture POIs", "Census ACS tract geometries (housing-unit values not read)", "strata ALAND/population/urbanity"],
            "removed_or_external_layers_used": [],
            "excluded_strata_fields": ["USFS building counts", "CarbonPlan building counts"],
            "ACS_housing_use": "ACS files are used only for provided tract geometries; housing_units values are deliberately not read or used in any ratio/proxy, consistent with the official rules.",
            "feature_aggregation": "Road class-filtered lines intersected with authoritative sample ACS tract polygons and length measured in EPSG:5070; building/POI bbox centers assigned to containing scored tracts; unmatched features excluded and counted. POIs lacking usable categories are counted separately and apportioned by each region's known in-sample category share, rather than treated as category-specific zeroes."},
        "formulas": {"road_expected_km": "0.35 + 0.55*sqrt(ALAND_km2) + 0.0007*pop_total + 2*urban_fraction",
            "building_expected": "max(0.5, 0.30*pop_total + 4*sqrt(ALAND_km2) + 5*urban_fraction)",
            "fire_expected": "0.15 + pop_total/30000 + 0.45*urban_fraction",
            "ems_expected": "0.15 + pop_total/40000 + 0.45*urban_fraction",
            "schools_expected": "0.20 + pop_total/2500 + 0.20*urban_fraction",
            "cbp_expected": "0.20 + pop_total/70 + 0.50*urban_fraction",
            "gap": "1-min(1, observed/expected), clipped to [0,1]; undefined gaps written as 0 and excluded from component means",
            "defined_flags": "transport: expected named-highway length proxy >= 6 km (observed Overture length alone is not equivalent to a TIGER denominator); building: observed buildings > 0 OR expected buildings >= 1; each POI type: observed category count > 0 OR expected type count >= 0.5; CBP: all observed POIs > 0 OR expected establishments >= 1",
            "urbanity_missing": "Use observed pop_urban/pop_total when possible; for zero-pop missing pct_urban use neutral 0.5 prior and count it; no null-to-zero fill."},
        "rows": len(all_records), "columns": OUTPUT_COLUMNS,
        "official_rules_any_undefined_context": rule_any_undefined,
        "rules_context_note": "Official rules' aggregate frequencies are shown for comparison. No per-tract labels or leaderboard scores were used; this screen is not accuracy measurement, and the observed rate differences remain a limitation.",
        "composite_max_abs_error": max_composite_error, "per_region": region_audit,
        "feature_aggregation": region_info,
        "inputs": input_hashes,
        "candidate_manifest": str(manifest_path),
        "candidates": {item["candidate_id"]: {"artifact": item["artifact"], "sha256": item["sha256"],
            "rows": item["rows"], "columns": item["columns"], "per_region_means": item["per_region_means"],
            "defined_rates": item["defined_rates"]} for item in candidates}}
    args.audit_out.parent.mkdir(parents=True, exist_ok=True)
    args.audit_out.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(all_records)} estimator rows to {args.out}; audit {args.audit_out}; "
          f"C2 SHA-256 {sha256(c2_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
