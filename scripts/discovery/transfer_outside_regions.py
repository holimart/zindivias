#!/usr/bin/env python3
"""Measure transfer of the border building-source pattern outside scored regions.

Reads only the manifested public Overture buildings extracts and Census TIGER/Line
2024 tract polygons. It does not read candidate predictions, scores, or removed
reference layers. Footprints are assigned by bbox centre to their containing tract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import shapefile
import shapely
from shapely.geometry import shape
from shapely.strtree import STRtree

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from estimate_gaps import classify_building_sources  # noqa: E402

RELEASE = "2026-09-23.1"
TARGETS = {
    "48141": ("El Paso TX", "border metro, outside scored regions"),
    "06025": ("Imperial CA", "border metro, outside scored regions"),
    "35013": ("Doña Ana NM", "border metro, outside scored regions"),
    "48135": ("Ector TX", "non-border, Hispanic-majority control"),
    "35001": ("Bernalillo NM", "non-border control"),
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_county_tracts(workspace: Path, county_fips: str):
    state = county_fips[:2]
    shp_path = workspace / "external-discovery-2026-10-27" / "_tract-geometry-extract" / state / f"tl_2024_{state}_tract.shp"
    if not shp_path.is_file():
        raise FileNotFoundError(shp_path)
    reader = shapefile.Reader(str(shp_path))
    fields = [field[0] for field in reader.fields[1:]]
    polygons, geoids = [], []
    county_part = county_fips[2:]
    for record in reader.iterShapeRecords():
        row = dict(zip(fields, record.record))
        if str(row["COUNTYFP"]).zfill(3) == county_part:
            polygons.append(shape(record.shape.__geo_interface__))
            geoids.append(str(row["GEOID"]))
    if not polygons:
        raise ValueError(f"no TIGER tracts found for county {county_fips}")
    return polygons, geoids, shp_path


def assign_centres(tree: STRtree, bbox_rows: list[dict[str, Any]]) -> np.ndarray:
    x = np.asarray([(r["xmin"] + r["xmax"]) / 2 for r in bbox_rows], dtype=np.float64)
    y = np.asarray([(r["ymin"] + r["ymax"]) / 2 for r in bbox_rows], dtype=np.float64)
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise ValueError("non-finite bbox centre")
    points = shapely.points(x, y)
    pairs = tree.query(points, predicate="within")
    assignment = np.full(len(points), -1, dtype=np.int64)
    if pairs.size:
        point_idx, tract_idx = pairs
        order = np.lexsort((tract_idx, point_idx))
        point_idx, tract_idx = point_idx[order], tract_idx[order]
        first = np.r_[True, point_idx[1:] != point_idx[:-1]]
        assignment[point_idx[first]] = tract_idx[first]
    return assignment


def is_google_only(names: set[str]) -> bool:
    return len(names) == 1 and any("google" in name for name in names)


def summarize_tracts(tract_counts: dict[str, Counter[str]]) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    tract_osm_shares = []
    under_five = 0
    for counts in tract_counts.values():
        n = counts["footprints"]
        if n <= 0:
            continue
        totals.update(counts)
        share = counts["osm"] / n
        tract_osm_shares.append(share)
        under_five += share < 0.05
    n = totals["footprints"]
    if not n:
        raise ValueError("no footprints were assigned to county tracts")
    return {
        "footprints_in_county": int(n),
        "tracts_with_footprints": len(tract_osm_shares),
        "osm_touched_share": totals["osm"] / n,
        "microsoft_only_share": totals["microsoft_only"] / n,
        "google_only_share": totals["google_only"] / n,
        "esri_touched_share": totals["esri"] / n,
        "other_or_mixed_share": totals["other"] / n,
        "tract_median_osm_share": statistics.median(tract_osm_shares),
        "tracts_osm_below_5pct": under_five,
        "tracts_osm_below_5pct_denominator": len(tract_osm_shares),
    }


def analyze_county(workspace: Path, data_dir: Path, manifest_file: dict[str, Any], county_fips: str):
    name, role = TARGETS[county_fips]
    parquet_path = data_dir / manifest_file["file"]
    actual_sha = sha256_file(parquet_path)
    if actual_sha != manifest_file["sha256"]:
        raise ValueError(f"SHA-256 mismatch for {parquet_path}: {actual_sha}")
    polygons, geoids, shp_path = load_county_tracts(workspace, county_fips)
    tree = STRtree(polygons)
    tract_counts: dict[str, Counter[str]] = {geoid: Counter() for geoid in geoids}
    members: Counter[str] = Counter()
    bbox_rows_read = county_assigned = 0
    parquet = pq.ParquetFile(parquet_path)
    for batch in parquet.iter_batches(columns=["bbox", "sources"], batch_size=100_000):
        bbox_rows = batch.column(batch.schema.get_field_index("bbox")).to_pylist()
        source_rows = batch.column(batch.schema.get_field_index("sources")).to_pylist()
        assignment = assign_centres(tree, bbox_rows)
        bbox_rows_read += len(bbox_rows)
        for idx in np.flatnonzero(assignment >= 0):
            geoid = geoids[int(assignment[idx])]
            classified = classify_building_sources(source_rows[int(idx)])
            names = classified["dataset_names"]
            counts = tract_counts[geoid]
            counts["footprints"] += 1
            counts["osm"] += bool(classified["osm"])
            counts["microsoft_only"] += classified["class"] == "microsoft_only"
            counts["google_only"] += is_google_only(names)
            counts["esri"] += bool(classified["esri"])
            counts["other"] += bool(classified["other"])
            for dataset in names:
                members[dataset] += 1
            county_assigned += 1
    if bbox_rows_read != manifest_file["rows"]:
        raise ValueError(f"row-count mismatch: read {bbox_rows_read}, manifest {manifest_file['rows']}")
    summary = summarize_tracts(tract_counts)
    summary.update({
        "county_fips": county_fips,
        "county": name,
        "role": role,
        "footprints_in_bbox": bbox_rows_read,
        "unassigned_outside_county": bbox_rows_read - county_assigned,
        "bbox_file": manifest_file["file"],
        "bbox_file_sha256": actual_sha,
        "dataset_membership_counts": dict(sorted((k, int(v)) for k, v in members.items())),
        "tract_geometry": str(shp_path.relative_to(workspace)),
    })
    return summary


def build_report(workspace: Path) -> dict[str, Any]:
    data_dir = workspace / "external-discovery-2026-10-29-fable"
    manifest_path = data_dir / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["source"]["release"] != RELEASE:
        raise ValueError(f"expected Overture release {RELEASE}")
    files = {entry["county_fips"]: entry for entry in manifest["files"]}
    tiger_provenance = {}
    results = []
    for fips in TARGETS:
        result = analyze_county(workspace, data_dir, files[fips], fips)
        state = fips[:2]
        provenance_path = workspace / "lab-prerequisites" / f"tl_2024_{state}_tract.zip.provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        if provenance.get("version") != "TIGER2024":
            raise ValueError(f"unexpected TIGER vintage for state {state}")
        tiger_provenance[state] = {
            "archive": provenance["name"],
            "url": provenance["url"],
            "retrieved_utc": provenance["retrieval_date_utc"],
            "sha256": provenance["sha256"],
            "licence": "U.S. Census TIGER/Line public domain (17 U.S.C. §105)",
        }
        results.append(result)
    border_report_path = REPO / "reports/discovery-border-metros.json"
    border_report = json.loads(border_report_path.read_text(encoding="utf-8"))
    reconciliation = border_report["results"]["source_count_reconciliation"]
    cache_info = border_report["inputs"]["feature_cache"]
    cache_path = Path(cache_info["path"])
    if not cache_path.is_file() or sha256_file(cache_path) != cache_info["sha256"]:
        raise ValueError(f"feature-cache hash mismatch: {cache_path}")
    cache = pq.read_table(cache_path, columns=[
        "GEOID", "overture_buildings", "overture_building_other_source_share"
    ])
    geoids = cache.column("GEOID").to_pylist()
    buildings = cache.column("overture_buildings").to_numpy(zero_copy_only=False)
    other_shares = cache.column("overture_building_other_source_share").to_numpy(zero_copy_only=False)
    bexar = np.asarray([str(geoid).startswith("48029") for geoid in geoids]) & (buildings > 0)
    bexar_other_share = float(np.average(other_shares[bexar], weights=buildings[bexar]))
    other_source_context = {
        "interpretation": "The classifier's other/mixed bucket is not a single provider; the existing source-count reconciliation associates border other with Google Open Buildings and non-border Texas other with USGS lidar.",
        "border_other_footprints": reconciliation["border_other_source_footprints"],
        "border_google_open_buildings_memberships": reconciliation["all_tx_google_open_buildings_source_memberships"],
        "nonborder_other_footprints": reconciliation["nonborder_other_source_footprints"],
        "nonborder_usgs_lidar_memberships": reconciliation["all_tx_usgs_lidar_source_memberships"],
        "bexar_cache": {
            "county_fips": "48029",
            "tracts_with_buildings": int(bexar.sum()),
            "building_count": int(buildings[bexar].sum()),
            "footprint_weighted_other_source_share": bexar_other_share,
            "feature_cache_path": str(cache_path.relative_to(workspace)),
            "feature_cache_sha256": cache_info["sha256"],
        },
        "supporting_report": {
            "path": str(border_report_path.relative_to(REPO)),
            "sha256": sha256_file(border_report_path),
        },
        "near_equal_count_differences": {
            "border_other_minus_google": reconciliation["border_minus_google_count"],
            "nonborder_other_minus_usgs": reconciliation["nonborder_minus_usgs_lidar_count"],
        },
    }
    return {
        "analysis": "Out-of-scored-region transfer of Google-only building source mix; descriptive discovery only",
        "analysis_script": {"path": "scripts/discovery/transfer_outside_regions.py",
                            "sha256": sha256_file(Path(__file__).resolve())},
        "data_and_restrictions": {
            "candidate_artifacts_or_predictions_read": False,
            "public_scores_used": False,
            "removed_reference_layers_read": False,
            "sample_score_values_used": False,
            "external_data_added_to_scored_predictions": False,
            "footprint_source_tags_are_provenance_not_correctness_or_community_activity": True,
        },
        "source": {
            "dataset": manifest["source"]["dataset"],
            "release": manifest["source"]["release"],
            "url": manifest["source"]["url"],
            "retrieved_utc": manifest["source"]["retrieved_utc"],
            "licence": manifest["source"]["licence"],
            "download_tool": manifest["source"]["tool"],
            "manifest": str(manifest_path.relative_to(workspace)),
            "manifest_sha256": sha256_file(manifest_path),
            "files": [{"path": entry["file"], "sha256": entry["sha256"], "rows": entry["rows"]}
                      for entry in manifest["files"] if entry["county_fips"] in TARGETS],
        },
        "tract_assignment": {
            "dataset": "U.S. Census TIGER/Line 2024 tracts",
            "assignment": "Overture bbox centre within the county tract polygon (Shapely STRtree)",
            "licence": "U.S. federal public domain (17 U.S.C. §105)",
            "state_archives": tiger_provenance,
        },
        "classification": {
            "function": "scripts/estimate_gaps.py::classify_building_sources",
            "osm_touched": "any source named osm or openstreetmap*",
            "microsoft_only": "source class microsoft_only from the lane classifier",
            "google_only": "one source name and it contains google",
            "esri_touched": "any source named esri*",
            "tract_low_osm_rule": "tract OSM-touched share <0.05; strict inequality",
            "shares": "footprint-weighted within county; tract median is unweighted across tracts",
        },
        "results": results,
        "other_source_context": other_source_context,
        "runtime": {"python": sys.version.split()[0], "numpy": np.__version__,
                    "pyarrow": __import__("pyarrow").__version__, "shapely": shapely.__version__},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ.get("COMPETITION_WORKSPACE", "")))
    parser.add_argument("--out", type=Path, default=REPO / "reports/discovery-border-transfer.json")
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error("--workspace must be an existing COMPETITION_WORKSPACE directory")
    report = build_report(args.workspace)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("county | buildings | OSM | MS-only | Google-only | Esri | tracts <5% OSM")
    for row in report["results"]:
        print(f"{row['county']:<12} | {row['footprints_in_county']:>7,} | "
              f"{row['osm_touched_share']:.3f} | {row['microsoft_only_share']:.3f} | "
              f"{row['google_only_share']:.3f} | {row['esri_touched_share']:.3f} | "
              f"{row['tracts_osm_below_5pct']}/{row['tracts_osm_below_5pct_denominator']}")
    print(f"wrote {args.out} (sha256={sha256_file(args.out)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
