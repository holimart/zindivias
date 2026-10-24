#!/usr/bin/env python3
"""Measure same-name, 50 m duplicate sensitivity for provided Overture facilities.

Reads only official sample GEOIDs, provided ACS tract geometry, and the provided
Overture POI bbox/category/name columns. It emits aggregate counts only; no
sample scores, labels, removed references, or external data are read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

import audit_poi_categories as poi_audit

BUCKET = poi_audit.BUCKET
REGIONS = poi_audit.REGIONS
FACILITY_FAMILIES = ("fire", "ems", "schools")
POI_COLUMNS = ["bbox", "categories", "names"]
RADIUS_M = 50.0


def normalize_primary_name(value: object) -> str | None:
    """Case/Unicode/whitespace-normalize names.primary; preserve punctuation."""
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", value).casefold()).strip()
    return normalized or None


def _record_key(record: tuple) -> tuple:
    # Deterministic representative order independent of hash/dict iteration.
    x, y, _tract, geoid, row_id, _families = record
    return (geoid, x, y, row_id)


def greedy_name_clusters(records: list[tuple], radius_m: float = RADIUS_M) -> list[list[int]]:
    """Greedily group records when within radius of a retained representative.

    Records are a list of (x_5070, y_5070, tract_index, GEOID, row_id, families).
    Stable-order representatives prevent transitive chaining from merging two
    endpoints more than ``radius_m`` apart.
    """
    if radius_m <= 0 or not np.isfinite(radius_m):
        raise ValueError("radius_m must be finite and positive")
    if len(records) < 2:
        return [[i] for i in range(len(records))]
    points = shapely.points(
        np.asarray([record[0] for record in records], dtype=np.float64),
        np.asarray([record[1] for record in records], dtype=np.float64),
    )
    pairs = shapely.STRtree(points).query(points, predicate="dwithin", distance=radius_m)
    neighbors: list[list[int]] = [[] for _ in records]
    for left, right in zip(pairs[0].tolist(), pairs[1].tolist()):
        if left != right:
            neighbors[left].append(right)
    order = sorted(range(len(records)), key=lambda idx: _record_key(records[idx]))
    retained: list[int] = []
    retained_set: set[int] = set()
    clusters_by_rep: dict[int, list[int]] = {}
    for idx in order:
        candidates = [rep for rep in neighbors[idx] if rep in retained_set]
        if not candidates:
            retained.append(idx)
            retained_set.add(idx)
            clusters_by_rep[idx] = [idx]
            continue
        x, y = records[idx][0], records[idx][1]
        representative = min(
            candidates,
            key=lambda rep: (
                (records[rep][0] - x) ** 2 + (records[rep][1] - y) ** 2,
                _record_key(records[rep]),
            ),
        )
        clusters_by_rep[representative].append(idx)
    return [clusters_by_rep[rep] for rep in retained]


def _read_sample_ids(path: Path) -> tuple[list[str], str]:
    # Reuse the previously audited GEOID-only reader; sample scores are ignored.
    return poi_audit.read_sample_geoids(path)


def audit_region(fs, workspace: Path, region: str) -> dict:
    sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
    geoids, geoid_hash = _read_sample_ids(sample_path)
    _ids, _geoms, tract_tree, tract_source = poi_audit.load_sample_tracts(fs, region, geoids)
    poi_key = f"{BUCKET}/reference/{region}/{region}-overture-pois.parquet"
    info = fs.get_file_info(poi_key)
    if info.type != pafs.FileType.File:
        raise FileNotFoundError(f"provided POI layer missing: s3://{poi_key}")
    parquet = pq.ParquetFile(fs.open_input_file(poi_key))
    missing = set(POI_COLUMNS) - set(parquet.schema_arrow.names)
    if missing:
        raise ValueError(f"POI source missing columns: {sorted(missing)}")
    selected_bytes = poi_audit._column_bytes(parquet.metadata, set(POI_COLUMNS))

    raw_counts = {family: np.zeros(len(geoids), dtype=np.int64) for family in FACILITY_FAMILIES}
    unnamed_counts = {family: np.zeros(len(geoids), dtype=np.int64) for family in FACILITY_FAMILIES}
    named_records: dict[str, list[tuple]] = defaultdict(list)
    input_rows = assigned_rows = unmatched_rows = facility_rows = 0
    row_id = 0
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)

    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=POI_COLUMNS)
        input_rows += table.num_rows
        x_lon, y_lat = poi_audit._bbox_centers(table)
        tract_index = poi_audit.assign_points(tract_tree, x_lon, y_lat)
        categories = table.column("categories").combine_chunks()
        primary = categories.field("primary").to_pylist()
        alternates = categories.field("alternate").to_pylist()
        names = table.column("names").combine_chunks().to_pylist()
        if not (len(primary) == len(alternates) == len(names) == len(tract_index)):
            raise ValueError(f"POI projected-column mismatch in {region} row group {rg}")
        selected_indices = [i for i, tract in enumerate(tract_index)
                            if tract >= 0 and poi_audit.category_tags(primary[i], alternates[i])
                            & (poi_audit.CORE_SETS["fire"] | poi_audit.CORE_SETS["ems"] | poi_audit.CORE_SETS["schools"])]
        if selected_indices:
            xs, ys = transformer.transform(x_lon[selected_indices], y_lat[selected_indices])
        else:
            xs, ys = np.asarray([], dtype=np.float64), np.asarray([], dtype=np.float64)
        xy_by_row = {idx: (float(x), float(y)) for idx, x, y in zip(selected_indices, xs, ys)}
        for i, tract in enumerate(tract_index):
            if tract < 0:
                unmatched_rows += 1
                continue
            assigned_rows += 1
            tags = poi_audit.category_tags(primary[i], alternates[i])
            families = tuple(family for family in FACILITY_FAMILIES if tags & poi_audit.CORE_SETS[family])
            if not families:
                continue
            facility_rows += 1
            name_struct = names[i]
            name = normalize_primary_name(name_struct.get("primary") if isinstance(name_struct, dict) else None)
            for family in families:
                raw_counts[family][tract] += 1
                if name is None:
                    unnamed_counts[family][tract] += 1
            if name is None:
                row_id += 1
                continue
            x, y = xy_by_row[i]
            record = (x, y, int(tract), geoids[int(tract)], row_id, families)
            named_records[name].append(record)
            row_id += 1

    dedup_counts = {family: values.copy() for family, values in unnamed_counts.items()}
    duplicate_clusters = duplicate_records = named_facility_rows = 0
    cluster_size_counts: Counter[str] = Counter()
    for records in named_records.values():
        named_facility_rows += len(records)
        clusters = greedy_name_clusters(records)
        for cluster in clusters:
            representative = records[cluster[0]]
            merged_families = set()
            for member in cluster:
                merged_families.update(records[member][5])
            tract = representative[2]
            for family in merged_families:
                dedup_counts[family][tract] += 1
            if len(cluster) > 1:
                duplicate_clusters += 1
                duplicate_records += len(cluster) - 1
                cluster_size_counts[str(len(cluster))] += 1

    type_results = {}
    raw_type_rows = dedup_type_rows = 0
    for family in FACILITY_FAMILIES:
        raw_rows = int(raw_counts[family].sum())
        dedup_rows = int(dedup_counts[family].sum())
        raw_type_rows += raw_rows
        dedup_type_rows += dedup_rows
        raw_zero = int(np.count_nonzero(raw_counts[family] == 0))
        dedup_zero = int(np.count_nonzero(dedup_counts[family] == 0))
        n = len(geoids)
        type_results[family] = {
            "raw_poi_rows": raw_rows,
            "deduplicated_poi_rows": dedup_rows,
            "rows_removed": raw_rows - dedup_rows,
            "share_of_raw_rows_removed": (raw_rows - dedup_rows) / raw_rows if raw_rows else None,
            "unnamed_rows_retained_unchanged": int(unnamed_counts[family].sum()),
            "raw_zero_tracts": raw_zero,
            "raw_zero_share": raw_zero / n,
            "deduplicated_zero_tracts": dedup_zero,
            "deduplicated_zero_share": dedup_zero / n,
            "zero_share_change_percentage_points": 100 * (dedup_zero - raw_zero) / n,
        }
    total_facility_memberships_removed = raw_type_rows - dedup_type_rows
    return {
        "official_sample": {"rows": len(geoids), "geoid_sequence_sha256": geoid_hash},
        "tract_geometry_source": tract_source,
        "poi_source": {
            "uri": f"s3://{poi_key}", "object_bytes": info.size,
            "input_rows": input_rows, "row_groups": parquet.metadata.num_row_groups,
            "assigned_rows": assigned_rows, "unmatched_rows": unmatched_rows,
            "columns_read": POI_COLUMNS,
            "selected_column_compressed_bytes": selected_bytes,
        },
        "facility_rows": {
            "raw_unique_rows": facility_rows,
            "named_primary_rows_considered": named_facility_rows,
            "unnamed_rows_retained_unchanged": facility_rows - named_facility_rows,
            "deduplicated_unique_rows": facility_rows - duplicate_records,
            "rows_removed": duplicate_records,
            "share_of_raw_facility_rows_removed": duplicate_records / facility_rows if facility_rows else None,
            "duplicate_clusters": duplicate_clusters,
            "cluster_size_frequency": dict(sorted(cluster_size_counts.items(), key=lambda item: int(item[0]))),
            "family_memberships_raw": raw_type_rows,
            "family_memberships_deduplicated": dedup_type_rows,
            "family_memberships_removed": total_facility_memberships_removed,
            "family_memberships_removed_share": total_facility_memberships_removed / raw_type_rows if raw_type_rows else None,
        },
        "by_family": type_results,
    }


def build_report(workspace: Path) -> dict:
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    regions = {region: audit_region(fs, workspace, region) for region in REGIONS}
    totals = {"raw_unique_facility_rows": 0, "deduplicated_unique_facility_rows": 0,
              "rows_removed": 0, "family_memberships_raw": 0,
              "family_memberships_deduplicated": 0, "family_memberships_removed": 0}
    total_types = {family: Counter() for family in FACILITY_FAMILIES}
    field_map = {
        "raw_unique_facility_rows": "raw_unique_rows",
        "deduplicated_unique_facility_rows": "deduplicated_unique_rows",
        "rows_removed": "rows_removed",
        "family_memberships_raw": "family_memberships_raw",
        "family_memberships_deduplicated": "family_memberships_deduplicated",
        "family_memberships_removed": "family_memberships_removed",
    }
    for result in regions.values():
        for key, source_key in field_map.items():
            totals[key] += result["facility_rows"][source_key]
        for family in FACILITY_FAMILIES:
            for key in ("raw_poi_rows", "deduplicated_poi_rows", "rows_removed",
                        "raw_zero_tracts", "deduplicated_zero_tracts"):
                total_types[family][key] += result["by_family"][family][key]
    total_tracts = sum(result["official_sample"]["rows"] for result in regions.values())
    total_types_report = {}
    for family, values in total_types.items():
        raw_rows = values["raw_poi_rows"]
        dedup_rows = values["deduplicated_poi_rows"]
        raw_zero = values["raw_zero_tracts"]
        dedup_zero = values["deduplicated_zero_tracts"]
        total_types_report[family] = {
            "raw_poi_rows": raw_rows,
            "deduplicated_poi_rows": dedup_rows,
            "rows_removed": values["rows_removed"],
            "share_of_raw_rows_removed": values["rows_removed"] / raw_rows if raw_rows else None,
            "raw_zero_tracts": raw_zero,
            "raw_zero_share": raw_zero / total_tracts,
            "deduplicated_zero_tracts": dedup_zero,
            "deduplicated_zero_share": dedup_zero / total_tracts,
            "zero_share_change_percentage_points": 100 * (dedup_zero - raw_zero) / total_tracts,
        }
    raw_memberships = totals["family_memberships_raw"]
    removed_memberships = totals["family_memberships_removed"]
    return {
        "schema_version": 1,
        "audit": "same-name Overture facility duplicate sensitivity at 50 m",
        "scope": "Official four-region samples only (9,379 GEOIDs); aggregates only, no per-tract output.",
        "policy": {
            "scored_default": "raw provided Overture facility rows; this report is sensitivity only and changes no candidate or estimator default",
            "dedup_key": "NFC-normalized, casefolded, whitespace-collapsed names.primary; punctuation retained; null/blank primary names are not deduplicated",
            "distance": "bbox centers transformed from EPSG:4326 to EPSG:5070; Euclidean distance <=50.0 m",
            "cluster_rule": "Within each normalized primary-name group, stable-order greedy clusters assign a row to the nearest previously retained representative within 50 m. This avoids transitive chains whose endpoints exceed 50 m. A merged cluster contributes the union of its selected fire/EMS/school families, at the representative's tract.",
            "family_sets": "Existing exact config/poi-category-definitions.json sets; a POI contributes at most once per family, and family memberships are audited separately.",
            "unnamed_policy": "Facility rows with missing/blank primary name are retained unchanged.",
            "rejected_use": "No hidden target, sample score placeholder, removed reference, or leaderboard information was used; no probe or scored candidate was created.",
        },
        "runtime": {"python": platform.python_version(), "numpy": np.__version__,
                    "pyarrow": pa.__version__, "shapely": shapely.__version__},
        "radius_m": RADIUS_M,
        "regions": regions,
        "all_regions": {
            "official_tracts": total_tracts,
            "facility_rows": {**totals,
                               "share_of_raw_facility_rows_removed": totals["rows_removed"] / totals["raw_unique_facility_rows"] if totals["raw_unique_facility_rows"] else None,
                               "family_memberships_removed_share": removed_memberships / raw_memberships if raw_memberships else None},
            "by_family": total_types_report,
        },
        "source_and_rules": {
            "bucket_root": f"s3://{BUCKET}/",
            "official_sample_scope_only": True,
            "sample_score_placeholders_read": False,
            "removed_reference_or_external_data_read": False,
            "candidate_or_submission_created": False,
            "category_config_sha256": hashlib.sha256(poi_audit.CATEGORY_CONFIG_PATH.read_bytes()).hexdigest(),
            "poi_category_helper_sha256": hashlib.sha256(Path(poi_audit.__file__).read_bytes()).hexdigest(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error(f"workspace does not exist: {args.workspace}")
    report = build_report(args.workspace.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Place duplicate sensitivity complete: 9,379 official IDs; report {args.out}")


if __name__ == "__main__":
    main()
