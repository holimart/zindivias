#!/usr/bin/env python3
"""Audit provided Overture POI categories, sources, confidence, and names.

Only the POI Parquet columns ``categories``, ``sources``, ``confidence``,
``names`` plus ``bbox`` for point-to-tract assignment are projected. The
provided ACS tract ``GEOID``/``geometry`` columns define the official sample
spatial index; no Overture geometry, removed reference layer, sample score, or
external facility list is read. Remote reads use anonymous ranged Parquet
column/row-group requests, not full-object downloads.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely

BUCKET = "us-west-2.opendata.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge"
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
POI_COLUMNS = ["bbox", "categories", "sources", "confidence", "names"]
MATCH_RE = re.compile(r"fire|ems|ambulance|emergen|school|educat|hospital|rescue|public_service", re.I)
NAME_PATTERNS = {
    "fire": re.compile(r"fire|rescue", re.I),
    "ems": re.compile(r"ems|ambulance|emergen", re.I),
    "schools": re.compile(r"school|educat", re.I),
    "hospitals": re.compile(r"hospital", re.I),
}
CATEGORY_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config/poi-category-definitions.json"
with CATEGORY_CONFIG_PATH.open("r", encoding="utf-8") as _stream:
    CORE_SETS = {name: set(values) for name, values in json.load(_stream)["selected_sets"].items()}


def read_sample_geoids(path: Path) -> tuple[list[str], str]:
    """Read only the official GEOID field; never parse sample score placeholders."""
    ids: list[str] = []
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if not header or "GEOID" not in header:
            raise ValueError(f"sample has no GEOID column: {path}")
        geoid_index = header.index("GEOID")
        for row in reader:
            value = row[geoid_index] if geoid_index < len(row) else ""
            if not value:
                raise ValueError(f"blank GEOID in {path}")
            ids.append(value)
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate GEOID in {path}")
    digest = hashlib.sha256("\\n".join(ids).encode("utf-8")).hexdigest()
    return ids, digest


def load_sample_tracts(fs, region: str, sample_ids: list[str]):
    """Read only provided ACS GEOID and polygon geometry for sample tract indexing."""
    key = f"{BUCKET}/reference/{region}/{region}-census-acs-housing.parquet"
    info = fs.get_file_info(key)
    if info.type != pafs.FileType.File:
        raise FileNotFoundError(f"provided tract geometry missing: s3://{key}")
    parquet = pq.ParquetFile(fs.open_input_file(key))
    sample_set = set(sample_ids)
    geoms_by_id: dict[str, Any] = {}
    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=["GEOID", "geometry"])
        ids = table.column("GEOID").to_pylist()
        wkb = table.column("geometry").to_pylist()
        for geoid, geom in zip(ids, wkb):
            if geoid in sample_set:
                if geoid in geoms_by_id:
                    raise ValueError(f"duplicate tract geometry {geoid} in {region}")
                if geom is None:
                    raise ValueError(f"null tract geometry {geoid} in {region}")
                geoms_by_id[geoid] = shapely.from_wkb(geom)
    missing = sample_set - set(geoms_by_id)
    if missing:
        raise ValueError(f"{len(missing)} sample GEOIDs have no provided geometry in {region}")
    # Match the order of the authoritative sample, as in the estimator cache.
    geoms = np.asarray([geoms_by_id[geoid] for geoid in sample_ids], dtype=object)
    tree = shapely.STRtree(geoms)
    metadata = parquet.metadata
    selected_bytes = sum(
        metadata.row_group(rg).column(col).total_compressed_size
        for rg in range(metadata.num_row_groups)
        for col in range(metadata.num_columns)
        if metadata.schema.column(col).path.split(".")[0] in {"GEOID", "geometry"}
    )
    return sample_ids, geoms, tree, {
        "source_uri": f"s3://{key}",
        "object_bytes": info.size,
        "selected_columns": ["GEOID", "geometry"],
        "selected_column_compressed_bytes": int(selected_bytes),
        "row_groups": metadata.num_row_groups,
        "rows": metadata.num_rows,
    }


def _bbox_centers(table) -> tuple[np.ndarray, np.ndarray]:
    bbox = table.column("bbox").combine_chunks()
    values: dict[str, np.ndarray] = {}
    for field in ("xmin", "ymin", "xmax", "ymax"):
        child = bbox.field(field)
        if child.null_count:
            raise ValueError(f"null bbox.{field} values: {child.null_count}")
        values[field] = child.to_numpy(zero_copy_only=False)
    x = (values["xmin"] + values["xmax"]) / 2
    y = (values["ymin"] + values["ymax"]) / 2
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("non-finite POI bbox center")
    return x, y


def assign_points(tree, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    points = shapely.points(x, y)
    result = np.full(len(points), -1, dtype=np.int64)
    pairs = tree.query(points, predicate="within")
    if pairs.size:
        point_idx, tract_idx = pairs
        order = np.lexsort((tract_idx, point_idx))
        point_idx, tract_idx = point_idx[order], tract_idx[order]
        first = np.r_[True, point_idx[1:] != point_idx[:-1]]
        result[point_idx[first]] = tract_idx[first]
    missing = np.flatnonzero(result < 0)
    if len(missing):
        pairs = tree.query(points[missing], predicate="intersects")
        if pairs.size:
            local_idx, tract_idx = pairs
            order = np.lexsort((tract_idx, local_idx))
            local_idx, tract_idx = local_idx[order], tract_idx[order]
            first = np.r_[True, local_idx[1:] != local_idx[:-1]]
            result[missing[local_idx[first]]] = tract_idx[first]
    return result


def category_tags(primary: str | None, alternates: list[str] | None) -> set[str]:
    tags = {str(x).strip().lower() for x in (alternates or []) if x and str(x).strip()}
    if primary and str(primary).strip():
        tags.add(str(primary).strip().lower())
    return tags


def name_values(value: dict | None) -> list[str]:
    if not value:
        return []
    output: list[str] = []
    primary = value.get("primary")
    if primary:
        output.append(str(primary))
    common = value.get("common")
    if isinstance(common, dict):
        output.extend(str(v) for v in common.values() if v)
    elif isinstance(common, list):
        output.extend(str(pair[1]) for pair in common
                      if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[1])
    rules = value.get("rules") or []
    for rule in rules:
        if isinstance(rule, dict) and rule.get("value"):
            output.append(str(rule["value"]))
    return output


def _sources_datasets(value: list[dict] | None) -> set[str]:
    if not value:
        return set()
    return {str(source["dataset"]).strip() for source in value
            if isinstance(source, dict) and source.get("dataset")}


def _matches(tags: set[str], selected: dict[str, set[str]]) -> dict[str, bool]:
    return {name: bool(tags & values) for name, values in selected.items()}


def _selected_variants(category_names: set[str]) -> dict[str, dict[str, set[str]]]:
    # Broad candidates are sensitivity checks only; the core sets remain the
    # explicit recommendation unless the audit evidence indicates otherwise.
    fire_broad = {name for name in category_names if re.search(r"fire|rescue", name, re.I)}
    ems_with_emergency_medicine = CORE_SETS["ems"] | {"emergency_medicine"}
    school_broad = {name for name in category_names if re.search(r"school|educat", name, re.I)}
    school_plus_daycare = CORE_SETS["schools"] | ({"day_care_preschool"} & category_names)
    return {
        "core_v0": {k: set(v) for k, v in CORE_SETS.items()},
        "fire_all_fire_or_rescue_categories": {
            **{k: set(v) for k, v in CORE_SETS.items()}, "fire": fire_broad,
        },
        "ems_plus_emergency_medicine": {
            **{k: set(v) for k, v in CORE_SETS.items()}, "ems": ems_with_emergency_medicine,
        },
        "schools_all_school_or_education_categories": {
            **{k: set(v) for k, v in CORE_SETS.items()}, "schools": school_broad,
        },
        "schools_plus_day_care_preschool": {
            **{k: set(v) for k, v in CORE_SETS.items()}, "schools": school_plus_daycare,
        },
    }


def _column_bytes(metadata, selected_columns: set[str]) -> dict[str, int]:
    totals = {name: 0 for name in selected_columns}
    for rg in range(metadata.num_row_groups):
        group = metadata.row_group(rg)
        for col in range(metadata.num_columns):
            column = group.column(col)
            path = metadata.schema.column(col).path.split(".")[0]
            if path in totals:
                totals[path] += column.total_compressed_size
    return totals


def audit_region(fs, region: str, workspace: Path) -> dict:
    sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
    sample_ids, sample_sha = read_sample_geoids(sample_path)
    geoids, _geoms, tree, tract_source = load_sample_tracts(fs, region, sample_ids)
    poi_key = f"{BUCKET}/reference/{region}/{region}-overture-pois.parquet"
    poi_info = fs.get_file_info(poi_key)
    if poi_info.type != pafs.FileType.File:
        raise FileNotFoundError(f"provided POI file missing: s3://{poi_key}")
    parquet = pq.ParquetFile(fs.open_input_file(poi_key))
    schema = parquet.schema_arrow
    missing_columns = set(POI_COLUMNS) - set(schema.names)
    if missing_columns:
        raise ValueError(f"POI table missing required columns: {sorted(missing_columns)}")
    selected_sizes = _column_bytes(parquet.metadata, set(POI_COLUMNS))
    primary_counts: Counter[str] = Counter()
    alternate_occurrences: Counter[str] = Counter()
    category_rows: Counter[str] = Counter()
    primary_pattern_hits: Counter[str] = Counter()
    alt_pattern_hits: Counter[str] = Counter()
    category_patterns = {"fire": re.compile(r"fire|rescue", re.I),
                         "ems": re.compile(r"ems|ambulance|emergen", re.I),
                         "schools": re.compile(r"school|educat", re.I),
                         "hospitals": re.compile(r"hospital", re.I)}
    name_outside_counts: Counter[str] = Counter()
    name_outside_core_counts: Counter[str] = Counter()
    source_mentions: Counter[str] = Counter()
    source_rows: dict[str, int] = defaultdict(int)
    confidence_values: list[float] = []
    confidence_missing = confidence_invalid = 0
    facility_confidence_values: list[float] = []
    facility_confidence_missing = 0
    low_facility_confidence = 0
    assigned_rows = unmatched_rows = input_rows = 0
    variant_category_names: set[str] = set()
    # Keep only integer counts by official sample tract; not names or raw records.
    variant_keys = ("core_v0", "fire_all_fire_or_rescue_categories",
                    "ems_plus_emergency_medicine", "schools_all_school_or_education_categories",
                    "schools_plus_day_care_preschool")
    core_tract_counts = np.zeros((len(geoids), 3), dtype=np.int64)
    broad_tract_counts = np.zeros((len(geoids), 3), dtype=np.int64)
    daycare_tract_counts = np.zeros(len(geoids), dtype=np.int64)
    facility_rows_unique = 0
    source_uri = f"s3://{poi_key}"
    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=POI_COLUMNS)
        input_rows += table.num_rows
        x, y = _bbox_centers(table)
        tract_idx = assign_points(tree, x, y)
        cat = table.column("categories").combine_chunks()
        primary = cat.field("primary").to_pylist()
        alternates = cat.field("alternate").to_pylist()
        sources = table.column("sources").combine_chunks().to_pylist()
        confidences = table.column("confidence").to_pylist()
        names = table.column("names").combine_chunks().to_pylist()
        if not (len(primary) == len(alternates) == len(sources) == len(confidences) == len(names) == len(tract_idx)):
            raise ValueError(f"POI column lengths disagree in {region} row group {rg}")
        for i, tract in enumerate(tract_idx):
            if tract < 0:
                unmatched_rows += 1
                continue
            assigned_rows += 1
            tags = category_tags(primary[i], alternates[i])
            selected_core = tags & (CORE_SETS["fire"] | CORE_SETS["ems"] | CORE_SETS["schools"])
            if selected_core:
                facility_rows_unique += 1
            families = (
                bool(tags & CORE_SETS["fire"]),
                bool(tags & CORE_SETS["ems"]),
                bool(tags & CORE_SETS["schools"]),
            )
            broad_families = (
                any(re.search(r"fire|rescue", tag, re.I) for tag in tags),
                bool(tags & (CORE_SETS["ems"] | {"emergency_medicine"})),
                any(re.search(r"school|educat", tag, re.I) for tag in tags),
            )
            for target_index, matched in enumerate(families):
                if matched:
                    core_tract_counts[tract, target_index] += 1
            for target_index, matched in enumerate(broad_families):
                if matched:
                    broad_tract_counts[tract, target_index] += 1
            if tags & (CORE_SETS["schools"] | {"day_care_preschool"}):
                daycare_tract_counts[tract] += 1
            for tag in tags:
                if MATCH_RE.search(tag):
                    category_rows[tag] += 1
                    variant_category_names.add(tag)
            if primary[i] and MATCH_RE.search(str(primary[i])):
                primary_counts[str(primary[i]).strip().lower()] += 1
                for family, pattern in category_patterns.items():
                    if pattern.search(str(primary[i])):
                        primary_pattern_hits[family] += 1
            for tag in set(str(x).strip().lower() for x in (alternates[i] or []) if x and str(x).strip()):
                if MATCH_RE.search(tag):
                    alternate_occurrences[tag] += 1
                    for family, pattern in category_patterns.items():
                        if pattern.search(tag):
                            alt_pattern_hits[family] += 1
            texts = name_values(names[i])
            core_name_sets = {**CORE_SETS, "hospitals": set()}
            for family, pattern in NAME_PATTERNS.items():
                if any(pattern.search(text) for text in texts):
                    if not any(pattern.search(tag) for tag in tags):
                        name_outside_counts[family] += 1
                    if not (tags & core_name_sets[family]):
                        name_outside_core_counts[family] += 1
            datasets = _sources_datasets(sources[i])
            for dataset in datasets:
                source_rows[dataset] += 1
            source_mentions.update(
                str(src["dataset"]).strip() for src in (sources[i] or [])
                if isinstance(src, dict) and src.get("dataset")
            )
            conf = confidences[i]
            if conf is None:
                confidence_missing += 1
            elif np.isfinite(conf) and 0 <= conf <= 1:
                confidence_values.append(float(conf))
                if tags & (CORE_SETS["fire"] | CORE_SETS["ems"] | CORE_SETS["schools"]):
                    facility_confidence_values.append(float(conf))
                    if conf < 0.5:
                        low_facility_confidence += 1
            else:
                confidence_invalid += 1
                if tags & (CORE_SETS["fire"] | CORE_SETS["ems"] | CORE_SETS["schools"]):
                    facility_confidence_missing += 1
            if conf is None and tags & (CORE_SETS["fire"] | CORE_SETS["ems"] | CORE_SETS["schools"]):
                facility_confidence_missing += 1
    if input_rows == 0:
        raise ValueError(f"no POI rows for {region}")
    # Each sensitivity variant changes one component while holding the other
    # two to the frozen core definitions.
    variants = _selected_variants(set(category_rows))
    tract_counts = {
        "core_v0": core_tract_counts,
        "fire_all_fire_or_rescue_categories": core_tract_counts.copy(),
        "ems_plus_emergency_medicine": core_tract_counts.copy(),
        "schools_all_school_or_education_categories": core_tract_counts.copy(),
        "schools_plus_day_care_preschool": core_tract_counts.copy(),
    }
    tract_counts["fire_all_fire_or_rescue_categories"][:, 0] = broad_tract_counts[:, 0]
    tract_counts["ems_plus_emergency_medicine"][:, 1] = broad_tract_counts[:, 1]
    tract_counts["schools_all_school_or_education_categories"][:, 2] = broad_tract_counts[:, 2]
    tract_counts["schools_plus_day_care_preschool"][:, 2] = daycare_tract_counts
    region_total_counts: dict[str, dict[str, Any]] = {}
    for vname, values in variants.items():
        arr = tract_counts[vname]
        group: dict[str, Any] = {"selected_categories": {k: sorted(v) for k, v in values.items()}}
        for j, kind in enumerate(("fire", "ems", "schools")):
            zeros = int(np.count_nonzero(arr[:, j] == 0))
            group[kind] = {"poi_rows": int(arr[:, j].sum()), "zero_tracts": zeros,
                           "tracts": len(geoids), "zero_share": zeros / len(geoids)}
        region_total_counts[vname] = group
    pquant = np.quantile(np.asarray(confidence_values), [0.1, 0.5, 0.9]).tolist() if confidence_values else [None] * 3
    selected_facility_rows = int(sum(region_total_counts["core_v0"][k]["poi_rows"] for k in ("fire", "ems", "schools")))
    return {
        "region": region,
        "official_sample": {"path": str(sample_path), "rows": len(sample_ids),
                             "geoid_sequence_sha256": sample_sha},
        "tract_index": tract_source,
        "poi_source": {
            "source_uri": source_uri,
            "object_bytes": poi_info.size,
            "input_rows": input_rows,
            "row_groups": parquet.metadata.num_row_groups,
            "assigned_to_official_sample": assigned_rows,
            "outside_or_unmatched_official_sample": unmatched_rows,
            "columns_read": POI_COLUMNS,
            "column_compressed_bytes": selected_sizes,
            "selected_column_compressed_bytes_total": int(sum(selected_sizes.values())),
            "overture_geometry_read": False,
            "sample_score_columns_read": False,
        },
        "category_frequency": [
            {"category": category,
             "primary_rows": int(primary_counts[category]),
             "alternate_occurrences": int(alternate_occurrences[category]),
             "rows_with_category": int(category_rows[category]),
             "primary_pattern_hits": [k for k, p in category_patterns.items() if p.search(category)]}
            for category in sorted(category_rows, key=lambda k: (-category_rows[k], k))
        ],
        "matching_primary_rows_by_family": {k: int(primary_pattern_hits[k]) for k in category_patterns},
        "matching_alternate_occurrences_by_family": {k: int(alt_pattern_hits[k]) for k in category_patterns},
        "name_regex_hits_outside_matching_categories": {k: int(name_outside_counts[k]) for k in NAME_PATTERNS},
        "name_regex_hits_outside_selected_core_sets": {k: int(name_outside_core_counts[k]) for k in NAME_PATTERNS},
        "source_dataset_mix": {
            "poi_rows_with_dataset": {k: int(v) for k, v in sorted(source_rows.items())},
            "source_mentions": {k: int(v) for k, v in sorted(source_mentions.items())},
        },
        "confidence": {
            "valid_nonnull_poi_rows": len(confidence_values),
            "missing_poi_confidence": confidence_missing,
            "invalid_confidence": confidence_invalid,
            "quantiles_q10_q50_q90": {k: float(v) for k, v in zip(("q10", "q50", "q90"), pquant)},
            "selected_core_facility_rows_unique": facility_rows_unique,
            "selected_core_facility_confidence_known": len(facility_confidence_values),
            "selected_core_facility_confidence_missing": facility_confidence_missing,
            "selected_core_facility_confidence_below_0_5": low_facility_confidence,
            "selected_core_facility_share_below_0_5": (low_facility_confidence / len(facility_confidence_values)
                                                        if facility_confidence_values else None),
            "selected_core_facility_type_rows_sum_nonunique": selected_facility_rows,
        },
        "tract_zero_sensitivity": region_total_counts,
    }


def build_report(workspace: Path) -> dict:
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    regions = {region: audit_region(fs, region, workspace) for region in REGIONS}
    category_union = sorted({item["category"] for region in regions.values()
                             for item in region["category_frequency"]})
    # Emit a rectangular per-region frequency table so absent categories are
    # explicit zeroes rather than missing rows.
    for result in regions.values():
        present = {item["category"]: item for item in result["category_frequency"]}
        result["category_frequency"] = [
            present.get(category, {
                "category": category, "primary_rows": 0, "alternate_occurrences": 0,
                "rows_with_category": 0,
                "primary_pattern_hits": [k for k, p in {
                    "fire": re.compile(r"fire|rescue", re.I),
                    "ems": re.compile(r"ems|ambulance|emergen", re.I),
                    "schools": re.compile(r"school|educat", re.I),
                    "hospitals": re.compile(r"hospital", re.I),
                }.items() if p.search(category)],
            }) for category in category_union
        ]
    recommendations = {
        "fire": sorted(CORE_SETS["fire"]),
        "ems": sorted(CORE_SETS["ems"]),
        "schools": sorted(CORE_SETS["schools"]),
        "hospitals": "excluded per organizer methodology; category and name hits are reported but not facilities",
        "decision": "Retain the existing exact fire and EMS station categories and the canonical school list. Exclude emergency_medicine from EMS because it is not an ambulance/EMS station category; exclude dance, driving, music, cosmetology, flight and other specialty schools from the school numerator. Use alternate-category/name evidence and the reported tract-zero sensitivity for review; do not tune these definitions on LB scores.",
    }
    return {
        "schema_version": 1,
        "audit": "provided Overture POI category/source/confidence/name audit",
        "runtime": {"python": platform.python_version(), "numpy": np.__version__,
                    "pyarrow": pa.__version__, "shapely": shapely.__version__},
        "script": {"path": str(Path(__file__).resolve()),
                   "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "constraint": "No labels, sample score placeholders, removed reference layers, external facility lists, or Overture geometry columns were read. The provided ACS tract geometry and Overture bbox centers were used only for spatial assignment to official sample IDs.",
        "regions": regions,
        "recommendation": recommendations,
        "category_config": {
            "path": str(CATEGORY_CONFIG_PATH),
            "sha256": hashlib.sha256(CATEGORY_CONFIG_PATH.read_bytes()).hexdigest(),
            "v2_contract": json.loads(CATEGORY_CONFIG_PATH.read_text(encoding="utf-8"))["v2_contract"],
        },
        "category_values_across_regions": category_union,
        "columns": {
            "overture_poi": POI_COLUMNS,
            "tract_index": ["GEOID", "geometry"],
            "score_placeholders_read": False,
            "removed_reference_or_external_data_read": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True,
                        help="authorized workspace containing samples/<region>-sample-submission.csv")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error(f"workspace does not exist: {args.workspace}")
    report = build_report(args.workspace.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(f"POI category audit: {sum(r['poi_source']['input_rows'] for r in report['regions'].values())} rows; report: {args.out}")


if __name__ == "__main__":
    main()
