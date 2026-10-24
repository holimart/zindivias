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

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
BUCKET = "us-west-2.opendata.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge"
ROAD_CLASSES = {"motorway", "trunk", "primary", "secondary"}
FIRE_CATEGORIES = {"fire_department", "fire_protection_service"}
EMS_CATEGORIES = {"ambulance_and_ems_services"}
SCHOOL_CATEGORIES = {
    "school", "elementary_school", "middle_school", "high_school",
    "public_school", "private_school", "charter_school", "religious_school",
    "preschool", "montessori_school",
}
OUTPUT_COLUMNS = [
    "GEOID", "coverage_gap_score", "transport_gap", "transport_defined",
    "building_gap", "building_defined", "poi_gap", "poi_defined",
    "fire_gap", "fire_defined", "ems_gap", "ems_defined",
    "schools_gap", "schools_defined", "cbp_gap", "cbp_defined",
]
SUBMISSION_COLUMNS = ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"]


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


def build_feature_cache(fs, region: str, cache_path: Path, sample_geoids: list[str]) -> dict:
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
    type_shares = poi_info["estimated_unclassified_type_shares"]
    arrays = [pa.array(geoids), pa.array(road_lengths), pa.array(building_counts),
              pa.array(poi_counts["all"]), pa.array(poi_counts["fire"]),
              pa.array(poi_counts["ems"]), pa.array(poi_counts["schools"]),
              pa.array(poi_counts["unclassified"]),
              pa.array([type_shares["fire"]] * len(geoids)),
              pa.array([type_shares["ems"]] * len(geoids)),
              pa.array([type_shares["schools"]] * len(geoids))]
    table = pa.Table.from_arrays(arrays, names=["GEOID", "overture_road_km",
        "overture_buildings", "overture_poi_all", "overture_poi_fire",
        "overture_poi_ems", "overture_poi_schools", "overture_poi_unclassified",
        "overture_unclassified_fire_share", "overture_unclassified_ems_share",
        "overture_unclassified_schools_share"])
    write_parquet(cache_path, table)
    return {"region": region, "tracts": len(geoids), "acs_source_uri": f"s3://{acs_key}",
            "acs_rows": acs_pf.metadata.num_rows, "acs_tracts_used": len(geoids),
            "acs_extra_geometries_excluded": acs_pf.metadata.num_rows - len(geoids),
            "acs_row_groups": acs_pf.metadata.num_row_groups,
            "buildings": building_info, "pois": poi_info, "roads": road_info,
            "feature_cache": str(cache_path), "feature_cache_sha256": sha256(cache_path),
            "aggregate_schema_version": 4,
            "elapsed_seconds": round(time.monotonic()-started, 2)}


def load_sample_and_strata(region: str, data_root: Path) -> tuple[list[dict], dict[str, dict]]:
    strata_path = data_root / f"{region}-strata-tract-table.parquet"
    sample_path = data_root / f"{region}-sample-submission.csv"
    if not strata_path.is_file() or not sample_path.is_file():
        raise FileNotFoundError(f"missing sample or strata for {region} under {data_root}")
    sample = list(csv.DictReader(sample_path.open("r", newline="", encoding="utf-8-sig")))
    ids = [row["GEOID"] for row in sample]
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
    def gap(observed: float, expected: float, defined: int) -> float:
        if not defined:
            return 0.0
        return min(1.0, max(0.0, 1.0 - observed / max(expected, 0.5)))

    transport_gap = gap(road, exp_road, flags["transport_defined"])
    building_gap = gap(buildings, exp_building, flags["building_defined"])
    # Apportion uncategorized POIs by the in-sample category share among
    # categorized Overture POIs; unknown types are never treated as zero.
    fire_observed = fire + unclassified_pois * fire_unknown_share
    ems_observed = ems + unclassified_pois * ems_unknown_share
    schools_observed = schools + unclassified_pois * schools_unknown_share
    fire_gap = gap(fire_observed, exp_fire, flags["fire_defined"])
    ems_gap = gap(ems_observed, exp_ems, flags["ems_defined"])
    schools_gap = gap(schools_observed, exp_schools, flags["schools_defined"])
    cbp_gap = gap(poi_all, exp_cbp, flags["cbp_defined"])
    facilities = [value for value, name in ((fire_gap, "fire_defined"),
        (ems_gap, "ems_defined"), (schools_gap, "schools_defined")) if flags[name]]
    poi_halves = []
    if facilities:
        poi_halves.append(sum(facilities) / len(facilities))
    if flags["cbp_defined"]:
        poi_halves.append(cbp_gap)
    flags["poi_defined"] = int(bool(poi_halves))
    poi_gap = sum(poi_halves) / len(poi_halves) if poi_halves else 0.0
    components = [(transport_gap, flags["transport_defined"]),
                  (building_gap, flags["building_defined"]),
                  (poi_gap, flags["poi_defined"])]
    included = [value for value, defined in components if defined]
    score = sum(included) / len(included) if included else 0.5
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
    parser.add_argument("--refresh-features", action="store_true", help="re-scan allowed Overture feature columns")
    args = parser.parse_args()
    workspace = args.workspace or (Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    if workspace is None:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    data_root = args.data_root or workspace / "defined-flags-inputs"
    feature_root = workspace / "gap-estimator" / "features"
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
        if (args.refresh_features or not feature_path.is_file() or
                cached_meta.get("aggregate_schema_version") != 4):
            info = build_feature_cache(fs, region, feature_path, [row["GEOID"] for row in sample])
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
    if len(all_records) != 9379 or len({row["GEOID"] for row in all_records}) != 9379:
        raise ValueError(f"expected 9,379 unique authoritative IDs, got {len(all_records)}")
    for row in all_records:
        for column in OUTPUT_COLUMNS[1:]:
            if column.endswith("_defined"):
                if row[column] not in (0, 1):
                    raise ValueError(f"invalid defined flag {column}: {row[column]}")
            elif not math.isfinite(float(row[column])) or not 0 <= float(row[column]) <= 1:
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
