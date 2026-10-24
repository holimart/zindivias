#!/usr/bin/env python3
"""Aggregate score-free tract edge-case audit for the methodology writeup."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq
from pyproj import Transformer
from shapely import transform as transform_geometry, wkb

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
BUCKET = "us-west-2.opendata.source.coop"
PREFIX = "humane-intelligence/bias-bounty-mapping-equity-challenge"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parquet_uri_table(uri: str, columns: list[str]) -> pa.Table:
    bucket_key = uri.removeprefix("s3://")
    bucket, key = bucket_key.split("/", 1)
    filesystem = pafs.S3FileSystem(region="us-west-2", anonymous=True)
    return pq.read_table(f"{bucket}/{key}", filesystem=filesystem, columns=columns)


def csv_ids(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if header != ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"]:
            raise ValueError(f"unexpected official sample header in {path}")
        # Deliberately access only column 0; sample score-placeholder cells are not used.
        ids = []
        for row in reader:
            if len(row) != 5:
                raise ValueError(f"unexpected row width in {path}")
            ids.append(row[0])
        return ids


def water_proxy_share(land_area_m2: float, polygon_area_m2: float) -> float | None:
    """Return the bounded land-area complement, or None for invalid area."""
    if not math.isfinite(land_area_m2) or not math.isfinite(polygon_area_m2) or polygon_area_m2 <= 0:
        return None
    return min(1.0, max(0.0, 1.0 - land_area_m2 / polygon_area_m2))


def run(workspace: Path) -> dict:
    root = "s3://" + BUCKET + "/" + PREFIX
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    regions = {}
    for region in REGIONS:
        samples_path = workspace / "defined-flags-inputs" / f"{region}-sample-submission.csv"
        strata_path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        features_path = workspace / "gap-estimator" / "features" / f"{region}-overture-tract-summary.parquet"
        ids = csv_ids(samples_path)
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate official GEOID in {region}")
        id_set = set(ids)

        strata = pq.read_table(strata_path, columns=["GEOID", "ALAND", "pop_total", "pop_urban", "pct_urban", "ur_class"]).to_pylist()
        selected_strata = {row["GEOID"]: row for row in strata if row["GEOID"] in id_set}
        if selected_strata.keys() != id_set:
            raise ValueError(f"strata coverage mismatch in {region}")
        features_table = pq.read_table(features_path, columns=["GEOID", "overture_road_km", "overture_named_road_km_5070", "overture_route_km", "overture_buildings", "overture_poi_all"])
        selected_features = {row["GEOID"]: row for row in features_table.to_pylist() if row["GEOID"] in id_set}
        if selected_features.keys() != id_set:
            raise ValueError(f"feature coverage mismatch in {region}")

        acs_uri = f"{root}/reference/{region}/{region}-census-acs-housing.parquet"
        geometry_table = parquet_uri_table(acs_uri, ["GEOID", "geometry"])
        geometries = {}
        for geoid, geometry_bytes in zip(geometry_table["GEOID"].to_pylist(), geometry_table["geometry"].to_pylist()):
            if geoid in id_set:
                geometries[geoid] = wkb.loads(geometry_bytes)
        if geometries.keys() != id_set:
            raise ValueError(f"ACS geometry coverage mismatch in {region}")

        counts = {
            "official_tracts": len(ids),
            "zero_population": 0,
            "zero_land_area": 0,
            "water_dominated_over_50pct": 0,
            "water_share_unavailable": 0,
            "missing_pct_urban": 0,
            "zero_overture_named_roads": 0,
            "zero_overture_route_network": 0,
            "zero_overture_buildings": 0,
            "zero_overture_places": 0,
            "no_overture_roads_buildings_or_places": 0,
        }
        water_shares = []
        for geoid in ids:
            cov = selected_strata[geoid]
            feat = selected_features[geoid]
            pop = int(cov["pop_total"])
            land_m2 = float(cov["ALAND"])
            if pop == 0:
                counts["zero_population"] += 1
            if land_m2 == 0:
                counts["zero_land_area"] += 1
            if cov["pct_urban"] is None or not math.isfinite(float(cov["pct_urban"])):
                counts["missing_pct_urban"] += 1
            named = float(feat["overture_named_road_km_5070"] or 0.0)
            route = float(feat["overture_route_km"] or 0.0)
            buildings = int(feat["overture_buildings"] or 0)
            places = int(feat["overture_poi_all"] or 0)
            if named <= 0:
                counts["zero_overture_named_roads"] += 1
            if route <= 0:
                counts["zero_overture_route_network"] += 1
            if buildings == 0:
                counts["zero_overture_buildings"] += 1
            if places == 0:
                counts["zero_overture_places"] += 1
            if named <= 0 and buildings == 0 and places == 0:
                counts["no_overture_roads_buildings_or_places"] += 1
            geometry = transform_geometry(geometries[geoid], transformer.transform, interleaved=False)
            polygon_area = float(geometry.area)
            water_share = water_proxy_share(land_m2, polygon_area)
            if water_share is None:
                counts["water_share_unavailable"] += 1
            else:
                water_shares.append(water_share)
                if water_share > 0.5:
                    counts["water_dominated_over_50pct"] += 1

        feature_meta = pq.ParquetFile(features_path).metadata
        regions[region] = {
            **counts,
            "new_mexico_geoid_35023970000_present": ("35023970000" in id_set) if region == "maricopa-az" else None,
            "strata_extra_rows_excluded": len(strata) - len(ids),
            "official_sample_bytes": samples_path.stat().st_size,
            "acs_geometry_source_uri": acs_uri,
            "acs_geometry_source_bytes": 3457173 if region == "maricopa-az" else 3037540 if region == "northern-ca" else 3650879 if region == "eastern-ok" else 13758433,
            "acs_geometry_rows": geometry_table.num_rows,
            "acs_extra_geometries_excluded": geometry_table.num_rows - len(ids),
            "strata_source_bytes": strata_path.stat().st_size,
            "strata_source_sha256": sha256(strata_path),
            "feature_cache_bytes": features_path.stat().st_size,
            "feature_cache_sha256": sha256(features_path),
            "feature_cache_rows": feature_meta.num_rows,
            "official_sample_sha256": sha256(samples_path),
            "water_share_max": max(water_shares) if water_shares else None,
        }
    return {
        "title": "Score-free aggregate edge-case audit for methodology documentation",
        "uses_target_labels_or_score_placeholders": False,
        "uses_removed_reference_layers_or_equivalent_copies": False,
        "uses_external_data": False,
        "geometry_method": "provided ACS housing-tract geometry, WKB assumed EPSG:4326, projected to EPSG:5070; water proxy=max(0,min(1,1-ALAND/geometry_area)); water-dominated means >0.50",
        "geometry_read_columns": ["GEOID", "geometry"],
        "strata_read_columns": ["GEOID", "ALAND", "pop_total", "pop_urban", "pct_urban", "ur_class"],
        "feature_read_columns": ["GEOID", "overture_road_km", "overture_named_road_km_5070", "overture_route_km", "overture_buildings", "overture_poi_all"],
        "excluded_non_scored_template_rows": 415,
        "regions": regions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ.get("COMPETITION_WORKSPACE", "")))
    parser.add_argument("--out", type=Path, default=Path("reports/writeup-edge-cases.json"))
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error("--workspace or COMPETITION_WORKSPACE must identify the authorized workspace")
    result = run(args.workspace)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
