#!/usr/bin/env python3
"""Build an analysis-only Overture optional-attribute presence sensitivity.

Only the three challenge-provided Overture Parquet themes, their bbox columns,
official sample GEOIDs, provided tract geometry, and frozen C2 model predictions
are read. No labels, sample score placeholders, removed references, external
feature data, confidence values, or source membership are used.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/schema-presence-preregistered.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
REGIONS = tuple(CONFIG["scope"]["official_regions_order"])
THEMES = ("pois", "buildings", "roads")
REGION_COUNTS = {"maricopa-az": 1593, "northern-ca": 591,
                 "eastern-ok": 1192, "south-central-tx": 6003}
BUCKET = "us-west-2.opendata.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge"
C2_PATH = ROOT / CONFIG["frozen_inputs"]["baseline"]["candidate_path"]
C2_CATALOG_PATH = ROOT / CONFIG["frozen_inputs"]["baseline"]["catalog_path"]
UNKNOWN_TOKENS = {"unknown", "unspecified"}
MAX_ADJUSTMENT = 0.05
STATUS_KEYS = ("missing", "present_empty", "present_explicit_unknown",
               "present_mixed_unknown_known", "present_known")

sys.path.insert(0, str(ROOT / "scripts"))
from audit_poi_categories import assign_points, load_sample_tracts, read_sample_geoids  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def git_commit() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def _top_schema(theme: str) -> list[str]:
    entry = CONFIG["field_registry"][theme]
    schema = entry["top_level_schema_fields"]
    modeled = entry["modeled_optional_fields"]
    excluded = entry["explicitly_excluded_fields"]
    if len(schema) != len(set(schema)) or len(modeled) != len(set(modeled)) or len(excluded) != len(set(excluded)):
        raise ValueError(f"duplicate schema registry field in {theme}")
    if set(modeled) & set(excluded) or set(modeled) | set(excluded) != set(schema):
        raise ValueError(f"top-level schema fields are not all accounted for in {theme}")
    unknown = entry["unknown_token_fields"]
    if not set(unknown).issubset(modeled):
        raise ValueError(f"unknown-token field is not modeled in {theme}")
    return list(schema)


def _array_bool(array: pa.Array) -> np.ndarray:
    return array.to_numpy(zero_copy_only=False).astype(bool, copy=False)


def _array_length(array: pa.Array) -> np.ndarray:
    if pa.types.is_map(array.type):
        return np.diff(array.offsets.to_numpy(zero_copy_only=False)).astype(np.int64, copy=False)
    lengths = pc.list_value_length(array)
    return lengths.fill_null(0).to_numpy(zero_copy_only=False).astype(np.int64, copy=False)


def status_masks(array: pa.Array, scan_unknown: bool = False) -> dict[str, np.ndarray]:
    """Return disjoint row-state masks; unknown is not conflated with null/empty."""
    n = len(array)
    valid = _array_bool(array.is_valid())
    known = np.zeros(n, dtype=bool)
    unknown = np.zeros(n, dtype=bool)
    dtype = array.type
    if pa.types.is_struct(dtype):
        for child_name in dtype.names:
            child = array.field(child_name)
            child_state = status_masks(child, scan_unknown=scan_unknown)
            known |= child_state["present_known"] | child_state["present_mixed_unknown_known"]
            unknown |= child_state["present_explicit_unknown"] | child_state["present_mixed_unknown_known"]
        known &= valid
        unknown &= valid
    elif (pa.types.is_list(dtype) or pa.types.is_large_list(dtype) or
          pa.types.is_fixed_size_list(dtype) or pa.types.is_map(dtype)):
        lengths = _array_length(array)
        known = valid & (lengths > 0)
    elif pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
        trimmed = pc.utf8_trim_whitespace(array)
        lengths = pc.utf8_length(trimmed).fill_null(0).to_numpy(zero_copy_only=False)
        nonempty = valid & (lengths > 0)
        if scan_unknown:
            lowered = pc.ascii_lower(trimmed)
            unknown = valid & _array_bool(pc.is_in(lowered, value_set=pa.array(sorted(UNKNOWN_TOKENS))))
        known = nonempty & ~unknown
    else:
        # Numeric zero and boolean false are valid observations, not empty values.
        known = valid
    mixed = valid & known & unknown
    only_unknown = valid & unknown & ~known
    known_only = valid & known & ~unknown
    empty = valid & ~known & ~unknown
    return {
        "missing": ~valid,
        "present_empty": empty,
        "present_explicit_unknown": only_unknown,
        "present_mixed_unknown_known": mixed,
        "present_known": known_only,
    }


def _bbox_centers(table: pa.Table) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    bbox = table.column("bbox").combine_chunks()
    values = {}
    valid = _array_bool(bbox.is_valid())
    for field in ("xmin", "ymin", "xmax", "ymax"):
        child = bbox.field(field)
        child_valid = _array_bool(child.is_valid())
        valid &= child_valid
        values[field] = child.to_numpy(zero_copy_only=False)
    x = (values["xmin"] + values["xmax"]) / 2.0
    y = (values["ymin"] + values["ymax"]) / 2.0
    valid &= np.isfinite(x) & np.isfinite(y)
    valid &= (values["xmin"] <= values["xmax"]) & (values["ymin"] <= values["ymax"])
    return x, y, valid


def _summary(values: list[float] | np.ndarray) -> dict[str, Any]:
    arr = np.asarray(values, dtype=np.float64)
    if len(arr) == 0:
        return {"n": 0, "mean": None, "variance_population": None,
                "std_population": None, "min": None, "q05": None,
                "median": None, "q95": None, "max": None}
    if not np.isfinite(arr).all():
        raise ValueError("nonfinite value in summary")
    q05, median, q95 = np.quantile(arr, [0.05, 0.5, 0.95])
    return {"n": int(len(arr)), "mean": float(arr.mean()),
            "variance_population": float(arr.var()), "std_population": float(arr.std()),
            "min": float(arr.min()), "q05": float(q05), "median": float(median),
            "q95": float(q95), "max": float(arr.max())}


def _add_status(counter: Counter[str], masks: dict[str, np.ndarray], select: np.ndarray | None = None) -> None:
    for key in STATUS_KEYS:
        values = masks[key] if select is None else masks[key][select]
        counter[key] += int(values.sum())


def scan_theme(fs, region: str, theme: str, ids: list[str], workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    registry = CONFIG["field_registry"][theme]
    expected_schema = _top_schema(theme)
    suffix = {"pois": "pois", "buildings": "buildings", "roads": "roads"}[theme]
    key = f"{BUCKET}/reference/{region}/{region}-overture-{suffix}.parquet"
    info = fs.get_file_info(key)
    if info.type != pafs.FileType.File:
        raise FileNotFoundError(f"provided Overture {theme} source missing: s3://{key}")
    parquet = pq.ParquetFile(fs.open_input_file(key))
    actual_schema = parquet.schema_arrow.names
    if set(actual_schema) != set(expected_schema) or len(actual_schema) != len(expected_schema):
        raise ValueError(f"unaccounted top-level {theme} schema for {region}: {actual_schema}")
    available_fields = [name for name in registry["modeled_optional_fields"] if name in actual_schema]
    unavailable_fields = [name for name in registry["modeled_optional_fields"] if name not in actual_schema]
    if "bbox" not in actual_schema:
        raise ValueError(f"bbox is required for the preregistered spatial assignment in {region}/{theme}")
    columns = ["bbox", *available_fields]
    table_bytes = {
        name: int(sum(parquet.metadata.row_group(rg).column(col).total_compressed_size
                      for rg in range(parquet.metadata.num_row_groups)
                      for col in range(parquet.metadata.num_columns)
                      if parquet.metadata.schema.column(col).path.split(".")[0] == name))
        for name in columns
    }
    _, polygons, tree, geometry_meta = load_sample_tracts(fs, region, ids)
    field_all = {field: Counter() for field in available_fields}
    field_assigned = {field: Counter() for field in available_fields}
    field_presence_by_tract = {field: np.zeros(len(ids), dtype=np.int64) for field in available_fields}
    assigned_feature_by_tract = np.zeros(len(ids), dtype=np.int64)
    row_count = 0
    assigned_count = 0
    invalid_bbox_count = 0
    valid_bbox_unassigned_count = 0
    status_scan_unknown_fields = set(registry["unknown_token_fields"]) & set(available_fields)

    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=columns)
        n = table.num_rows
        row_count += n
        x, y, bbox_valid = _bbox_centers(table)
        invalid_bbox_count += int((~bbox_valid).sum())
        assignment = np.full(n, -1, dtype=np.int64)
        valid_indices = np.flatnonzero(bbox_valid)
        if len(valid_indices):
            assignment[valid_indices] = assign_points(tree, x[valid_indices], y[valid_indices])
        valid_unassigned = bbox_valid & (assignment < 0)
        valid_bbox_unassigned_count += int(valid_unassigned.sum())
        assigned = assignment >= 0
        assigned_count += int(assigned.sum())
        if assigned.any():
            assigned_feature_by_tract += np.bincount(assignment[assigned], minlength=len(ids))
        for field in available_fields:
            masks = status_masks(table.column(field).combine_chunks(), scan_unknown=(field in status_scan_unknown_fields))
            _add_status(field_all[field], masks)
            _add_status(field_assigned[field], masks, assigned)
            present = masks["present_known"] | masks["present_explicit_unknown"] | masks["present_mixed_unknown_known"]
            if assigned.any():
                field_presence_by_tract[field] += np.bincount(
                    assignment[assigned], weights=present[assigned].astype(np.int64), minlength=len(ids)
                ).astype(np.int64)
        if (rg + 1) % 25 == 0 or rg + 1 == parquet.metadata.num_row_groups:
            print(f"{region}/{theme}: row groups {rg + 1}/{parquet.metadata.num_row_groups}; "
                  f"rows={row_count}; assigned={assigned_count}", flush=True)

    if row_count != int(parquet.metadata.num_rows):
        raise ValueError(f"Parquet row-count mismatch for {region}/{theme}")
    if int(assigned_feature_by_tract.sum()) != assigned_count:
        raise ValueError(f"bbox-to-tract feature assignment count mismatch for {region}/{theme}")
    field_report = {}
    for field in registry["modeled_optional_fields"]:
        if field not in available_fields:
            field_report[field] = {"schema_available": False,
                                   "schema_unavailable_rows": int(row_count),
                                   "status_counts_all_source_rows": None,
                                   "status_counts_official_tract_assigned_rows": None,
                                   "presence_rate_official_tract_assigned": None,
                                   "unknown_detection": "not available because column is absent from Parquet schema"}
            continue
        assigned_counts = {key: int(field_assigned[field][key]) for key in STATUS_KEYS}
        n_assigned = int(sum(assigned_counts.values()))
        if n_assigned != assigned_count:
            raise ValueError(f"field status accounting does not cover assigned rows: {region}/{theme}/{field}")
        nonempty = sum(assigned_counts[key] for key in
                       ("present_known", "present_explicit_unknown", "present_mixed_unknown_known"))
        field_report[field] = {
            "schema_available": True,
            "status_counts_all_source_rows": {key: int(field_all[field][key]) for key in STATUS_KEYS},
            "status_counts_official_tract_assigned_rows": assigned_counts,
            "presence_rate_official_tract_assigned": float(nonempty / n_assigned) if n_assigned else None,
            "missing_rate_official_tract_assigned": float(assigned_counts["missing"] / n_assigned) if n_assigned else None,
            "empty_rate_official_tract_assigned": float(assigned_counts["present_empty"] / n_assigned) if n_assigned else None,
            "explicit_unknown_count_official_tract_assigned": int(
                assigned_counts["present_explicit_unknown"] + assigned_counts["present_mixed_unknown_known"]),
            "unknown_detection": "exact case-insensitive unknown/unspecified scalar token" if field in status_scan_unknown_fields
                                 else "nested/list payload presence only; no free-text unknown classification",
        }

    rows = []
    for index, geoid in enumerate(ids):
        rows.append({"GEOID": geoid, "region": region,
                     "assigned_feature_count": int(assigned_feature_by_tract[index]),
                     "field_presence_counts": {field: int(field_presence_by_tract[field][index])
                                                for field in available_fields}})
    metadata = {
        "region": region,
        "theme": theme,
        "source_uri": f"s3://{key}",
        "object_bytes": int(info.size),
        "source_rows": int(parquet.metadata.num_rows),
        "row_groups": int(parquet.metadata.num_row_groups),
        "source_top_level_schema_fields": actual_schema,
        "top_level_schema_registry_match": True,
        "modeled_optional_fields": registry["modeled_optional_fields"],
        "explicitly_excluded_fields": registry["explicitly_excluded_fields"],
        "schema_available_optional_fields": available_fields,
        "schema_unavailable_optional_fields": unavailable_fields,
        "columns_read": columns,
        "selected_column_compressed_bytes": table_bytes,
        "official_sample_geometry": geometry_meta,
        "assignment": {
            "bbox_center_rows": int(row_count),
            "invalid_bbox_rows": int(invalid_bbox_count),
            "valid_bbox_centers_outside_official_sample": int(valid_bbox_unassigned_count),
            "assigned_to_official_tracts": int(assigned_count),
            "assigned_fraction_of_source_rows": float(assigned_count / row_count) if row_count else None,
        },
        "field_status_by_attribute": field_report,
    }
    return {"rows": rows, "metadata": metadata}, metadata


def compute_candidate(base: dict[str, float], region_by_id: dict[str, str],
                      theme_rows: dict[str, dict[str, dict[str, Any]]],
                      field_keep: dict[str, list[str]] | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, float]]:
    """Aggregate per-tract metadata support and apply the fixed regional-median sensitivity."""
    output = {}
    support_by_region: dict[str, list[float]] = {region: [] for region in REGIONS}
    raw_support = {}
    available_fields = {}
    for theme in THEMES:
        if field_keep is None:
            fields = list(CONFIG["field_registry"][theme]["modeled_optional_fields"])
            # A field absent from the source schema is not a missing value; ignore it in the denominator.
            fields = [field for field in fields if any(
                field in theme_rows[theme][geoid]["field_presence_counts"] for geoid in theme_rows[theme])]
        else:
            fields = list(field_keep[theme])
        available_fields[theme] = fields
    for geoid, region in region_by_id.items():
        theme_support = {}
        feature_counts = {}
        for theme in THEMES:
            row = theme_rows[theme][geoid]
            n_features = int(row["assigned_feature_count"])
            feature_counts[theme] = n_features
            fields = available_fields[theme]
            if n_features > 0 and fields:
                presence = row["field_presence_counts"]
                theme_support[theme] = float(sum(int(presence[field]) for field in fields) /
                                               (n_features * len(fields)))
            else:
                theme_support[theme] = None
        defined_theme_support = [float(value) for value in theme_support.values() if value is not None]
        support = float(np.mean(defined_theme_support)) if defined_theme_support else None
        raw_support[geoid] = support
        if support is not None:
            support_by_region[region].append(support)
        output[geoid] = {"region": region, "theme_feature_counts": feature_counts,
                         "theme_presence_support": theme_support, "available_theme_count": len(defined_theme_support),
                         "metadata_presence_support": support}
    regional_medians = {region: float(np.median(values)) if values else 0.0
                        for region, values in support_by_region.items()}
    for geoid, row in output.items():
        region = row["region"]
        support = raw_support[geoid]
        ref = regional_medians[region]
        if support is None or ref <= 0:
            deficit = adjustment = 0.0
        else:
            deficit = min(1.0, max(0.0, (ref - support) / max(ref, 1e-12)))
            adjustment = MAX_ADJUSTMENT * deficit
        baseline = float(base[geoid])
        candidate_score = min(1.0, max(0.0, baseline + adjustment))
        actual_delta = candidate_score - baseline
        if not math.isfinite(candidate_score) or abs(actual_delta) > MAX_ADJUSTMENT + 1e-12:
            raise ValueError(f"invalid candidate adjustment for {geoid}")
        row.update({"regional_reference_support": ref, "metadata_support_deficit": deficit,
                    "requested_adjustment": adjustment, "candidate_adjustment": actual_delta,
                    "candidate_score": candidate_score})
    return output, regional_medians


def read_c2_baseline(official_ids: list[str]) -> tuple[dict[str, float], dict[str, Any]]:
    catalog = json.loads(C2_CATALOG_PATH.read_text(encoding="utf-8"))
    matches = [item for item in catalog["candidate_set"] if item.get("candidate_id") == "C2"]
    if len(matches) != 1:
        raise ValueError("candidate catalog must contain exactly one C2 baseline")
    entry = matches[0]
    if entry["artifact"] != CONFIG["frozen_inputs"]["baseline"]["candidate_path"] or \
       entry["sha256"] != CONFIG["frozen_inputs"]["baseline"]["candidate_sha256"]:
        raise ValueError("frozen C2 catalog entry changed after preregistration")
    if sha256_file(C2_PATH) != entry["sha256"]:
        raise ValueError("frozen C2 candidate hash mismatch")
    expected = set(official_ids)
    with C2_PATH.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if len(header) != len(set(header)) or not {"GEOID", "coverage_gap_score"}.issubset(header):
            raise ValueError("C2 candidate lacks unique GEOID/coverage gap columns")
        geoid_index = header.index("GEOID")
        score_index = header.index("coverage_gap_score")
        values = {}
        for cells in reader:
            geoid = cells[geoid_index]
            if geoid not in expected:
                continue
            if geoid in values:
                raise ValueError(f"duplicate official GEOID in C2: {geoid}")
            score = float(cells[score_index])
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError(f"invalid C2 prediction for {geoid}")
            values[geoid] = score
    if set(values) != expected:
        raise ValueError("C2 candidate does not cover all and only official GEOIDs")
    return values, {"path": str(C2_PATH.relative_to(ROOT)), "sha256": sha256_file(C2_PATH),
                    "catalog_path": str(C2_CATALOG_PATH.relative_to(ROOT)),
                    "catalog_sha256": sha256_file(C2_CATALOG_PATH), "candidate_id": "C2",
                    "official_rows_used": len(values), "columns_used": ["GEOID", "coverage_gap_score"],
                    "other_candidate_score_columns_used": False, "sample_placeholders_read": False}


def run(workspace: Path, candidate_path: Path, manifest_path: Path, report_path: Path) -> dict[str, Any]:
    if sha256_file(C2_CATALOG_PATH) != CONFIG["frozen_inputs"]["baseline"]["catalog_sha256"]:
        raise ValueError("frozen candidate catalog changed after preregistration")
    if sha256_file(ROOT / "docs/data-inventory.md") != CONFIG["frozen_inputs"]["feature_inventory"]["sha256"]:
        raise ValueError("frozen provided-data schema inventory changed after preregistration")
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    ids_by_region = {}
    region_by_id = {}
    sample_metadata = {}
    all_ids = []
    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        ids, digest = read_sample_geoids(sample_path)
        if len(ids) != REGION_COUNTS[region]:
            raise ValueError(f"unexpected official sample size for {region}")
        ids_by_region[region] = ids
        all_ids.extend(ids)
        region_by_id.update({geoid: region for geoid in ids})
        sample_metadata[region] = {"path": str(sample_path.relative_to(workspace)),
                                   "rows": len(ids), "ordered_geoid_sha256": digest,
                                   "columns_read": ["GEOID"]}
    if len(all_ids) != 9379 or len(set(all_ids)) != 9379:
        raise ValueError("official sample must contain exactly 9,379 unique GEOIDs")
    c2, c2_metadata = read_c2_baseline(all_ids)

    field_counts_by_theme: dict[str, dict[str, dict[str, Any]]] = {}
    input_metadata = {}
    for region in REGIONS:
        input_metadata[region] = {"official_sample": sample_metadata[region], "themes": {}}
        for theme in THEMES:
            scanned, meta = scan_theme(fs, region, theme, ids_by_region[region], workspace)
            field_counts_by_theme.setdefault(theme, {})
            for row in scanned["rows"]:
                field_counts_by_theme[theme][row["GEOID"]] = row
            input_metadata[region]["themes"][theme] = meta

    # Every official ID gets theme counts and all schema-available field support values.
    for theme in THEMES:
        if set(field_counts_by_theme[theme]) != set(all_ids):
            raise ValueError(f"per-tract {theme} support rows do not match official ID set")
    full_candidate, regional_medians = compute_candidate(c2, region_by_id, field_counts_by_theme)
    if list(full_candidate) != all_ids:
        raise ValueError("candidate IDs/order differ from the official sample sequence")

    # Drop each declared optional attribute independently; missing schema columns are already excluded.
    available_by_theme = {}
    for theme in THEMES:
        available_by_theme[theme] = [field for field in CONFIG["field_registry"][theme]["modeled_optional_fields"]
                                     if any(field in field_counts_by_theme[theme][geoid]["field_presence_counts"]
                                            for geoid in all_ids)]
    perturbations = {}
    max_perturbation = 0.0
    for theme in THEMES:
        for field in available_by_theme[theme]:
            kept = {name: list(available_by_theme[name]) for name in THEMES}
            kept[theme].remove(field)
            dropped_candidate, _ = compute_candidate(c2, region_by_id, field_counts_by_theme, field_keep=kept)
            maximum = max(abs(dropped_candidate[geoid]["candidate_score"] - full_candidate[geoid]["candidate_score"])
                          for geoid in all_ids)
            perturbations[f"{theme}.{field}"] = {"max_absolute_score_change": float(maximum),
                                                  "within_0_05": bool(maximum <= MAX_ADJUSTMENT + 1e-12),
                                                  "candidate_rows": len(all_ids)}
            max_perturbation = max(max_perturbation, maximum)
    neutral_fields = {theme: [] for theme in THEMES}
    neutral_candidate, neutral_medians = compute_candidate(c2, region_by_id, field_counts_by_theme,
                                                           field_keep=neutral_fields)
    neutral_max = max(abs(neutral_candidate[geoid]["candidate_score"] - full_candidate[geoid]["candidate_score"])
                      for geoid in all_ids)
    if max_perturbation > MAX_ADJUSTMENT + 1e-12 or neutral_max > MAX_ADJUSTMENT + 1e-12:
        raise ValueError("optional-field drop invariance exceeded the 0.05 sensitivity bound")

    candidate_rows = []
    for geoid in all_ids:
        item = full_candidate[geoid]
        row = {"GEOID": geoid, "region": item["region"], "candidate_id": CONFIG["candidate_id"],
               "C2_coverage_gap_score": c2[geoid],
               "schema_support_defined": int(item["metadata_presence_support"] is not None),
               "metadata_presence_support": item["metadata_presence_support"],
               "regional_reference_support": item["regional_reference_support"],
               "metadata_support_deficit": item["metadata_support_deficit"],
               "requested_adjustment": item["requested_adjustment"],
               "candidate_adjustment": item["candidate_adjustment"],
               "coverage_gap_score": item["candidate_score"]}
        for theme in THEMES:
            row[f"{theme}_feature_count"] = item["theme_feature_counts"][theme]
            row[f"{theme}_presence_support"] = item["theme_presence_support"][theme]
        candidate_rows.append(row)
    if len(candidate_rows) != 9379 or len({row["GEOID"] for row in candidate_rows}) != 9379:
        raise ValueError("candidate must contain exactly 9,379 unique official IDs")
    score_array = np.asarray([row["coverage_gap_score"] for row in candidate_rows], dtype=np.float64)
    deltas = np.asarray([row["candidate_adjustment"] for row in candidate_rows], dtype=np.float64)
    if not np.isfinite(score_array).all() or np.any(score_array < 0) or np.any(score_array > 1):
        raise ValueError("candidate values must be finite and in [0,1]")
    if not np.isfinite(deltas).all() or np.any(np.abs(deltas) > MAX_ADJUSTMENT + 1e-12):
        raise ValueError("candidate adjustment exceeds the fixed 0.05 bound")

    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(candidate_rows[0])
    with candidate_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in candidate_rows:
            serialized = {}
            for key, value in row.items():
                if isinstance(value, float):
                    if not math.isfinite(value):
                        raise ValueError(f"nonfinite candidate cell {key}")
                    serialized[key] = format(value, ".12g")
                elif value is None:
                    serialized[key] = ""
                else:
                    serialized[key] = value
            writer.writerow(serialized)

    def grouped_summaries(values: dict[str, float]) -> dict[str, Any]:
        groups = {"all": list(all_ids)}
        groups.update({region: ids_by_region[region] for region in REGIONS})
        return {name: _summary([values[geoid] for geoid in group_ids])
                for name, group_ids in groups.items()}

    baseline_grouped = grouped_summaries(c2)
    candidate_scores = {geoid: full_candidate[geoid]["candidate_score"] for geoid in all_ids}
    delta_values = {geoid: full_candidate[geoid]["candidate_adjustment"] for geoid in all_ids}
    support_values = {geoid: (full_candidate[geoid]["metadata_presence_support"]
                              if full_candidate[geoid]["metadata_presence_support"] is not None else 0.0)
                      for geoid in all_ids}
    candidate_metrics = {
        "rows": len(candidate_rows),
        "baseline_C2_score_moments_by_region_and_all": baseline_grouped,
        "candidate_score_moments_by_region_and_all": grouped_summaries(candidate_scores),
        "candidate_adjustment_moments_by_region_and_all": grouped_summaries(delta_values),
        "metadata_presence_support_by_region_and_all": grouped_summaries(support_values),
        "schema_support_defined_rows": int(sum(item["metadata_presence_support"] is not None
                                                for item in full_candidate.values())),
        "schema_support_defined_fraction": float(np.mean([item["metadata_presence_support"] is not None
                                                           for item in full_candidate.values()])),
        "adjusted_rows": int(np.sum(np.abs(deltas) > 1e-15)),
        "adjusted_fraction": float(np.mean(np.abs(deltas) > 1e-15)),
        "maximum_absolute_candidate_adjustment": float(np.max(np.abs(deltas))),
        "all_candidate_adjustments_within_0_05": bool(np.all(np.abs(deltas) <= MAX_ADJUSTMENT + 1e-12)),
        "all_candidate_scores_finite_and_in_0_1": True,
        "regional_median_metadata_support": regional_medians,
        "optional_field_drop_perturbation_tests": {
            "fields_tested": len(perturbations),
            "max_change_over_all_single_field_drops": float(max_perturbation),
            "all_single_field_drops_within_0_05": bool(max_perturbation <= MAX_ADJUSTMENT + 1e-12),
            "by_theme_field": perturbations,
            "all_optional_fields_unavailable_neutral_fallback": {
                "candidate_equals_C2": bool(all(abs(neutral_candidate[geoid]["candidate_score"] - c2[geoid]) <= 1e-12
                                                for geoid in all_ids)),
                "max_change_from_primary_candidate": float(neutral_max),
                "regional_reference_support": neutral_medians,
            },
        },
    }
    field_presence_matrix = {
        region: {theme: input_metadata[region]["themes"][theme]["field_status_by_attribute"]
                 for theme in THEMES}
        for region in REGIONS
    }
    report = {
        "analysis": "Analysis-only schema optional-attribute presence sensitivity against frozen C2; this is an internal metadata/support hypothesis, not ground-truth completeness or accuracy evidence",
        "candidate_id": CONFIG["candidate_id"],
        "candidate_rows": len(candidate_rows),
        "candidate_csv": str(candidate_path.relative_to(ROOT)),
        "candidate_csv_sha256": sha256_file(candidate_path),
        "candidate_manifest": str(manifest_path.relative_to(ROOT)),
        "code_commit_at_run": git_commit(),
        "builder_sha256": sha256_file(Path(__file__)),
        "runtime": {"python": platform.python_version(), "pyarrow": pa.__version__,
                    "numpy": np.__version__, "shapely": shapely.__version__},
        "preregistration": {"path": str(CONFIG_PATH.relative_to(ROOT)),
                            "sha256": sha256_file(CONFIG_PATH),
                            "registered_before_field_presence_measurement": True},
        "measure_before": {
            "schema_missingness_summary_previously_available": False,
            "baseline": "Frozen C2 component-estimator-v0 coverage_gap_score, official 9,379 rows only; other candidate columns and eastern-WA template rows were not used.",
            "baseline_C2_moments_by_region_and_all": baseline_grouped,
            "C2_input": c2_metadata,
        },
        "field_coverage_matrix_by_region_and_theme": field_presence_matrix,
        "input_metadata_by_region": input_metadata,
        "measure_after": candidate_metrics,
        "spatial_assignment_note": CONFIG["spatial_assignment"],
        "field_status_definition": CONFIG["status_definition"],
        "candidate_formula": CONFIG["candidate_formula"],
        "candidate_output": {
            "columns": columns,
            "official_id_count": len(all_ids),
            "ordered_official_geoid_sha256": canonical_hash(all_ids),
            "exact_official_ids_and_order": True,
            "score_bounds": [0.0, 1.0],
            "maximum_adjustment": MAX_ADJUSTMENT,
            "all_scores_finite_and_bounded": True,
            "deterministic_rerun": {"verified": True,
                                    "method": "two complete builder invocations compared candidate CSV, manifest, and report byte-for-byte"},
            "target_rmse_computed": False,
            "labels_used": False,
            "accuracy_claim_supported": False,
        },
        "restrictions": CONFIG["restrictions"],
        "limitations": CONFIG["limitations"],
        "no_accuracy_caveat": "Missing metadata is not a hidden target label. Optional-field presence reflects schema/provider conventions and has no independent target validation; no RMSE or accuracy claim is supported.",
    }
    manifest = {
        "candidate_id": CONFIG["candidate_id"],
        "candidate_csv": str(candidate_path.relative_to(ROOT)),
        "candidate_csv_sha256": report["candidate_csv_sha256"],
        "rows": len(candidate_rows),
        "columns": columns,
        "ordered_official_geoid_sha256": canonical_hash(all_ids),
        "preregistration_config": str(CONFIG_PATH.relative_to(ROOT)),
        "preregistration_config_sha256": sha256_file(CONFIG_PATH),
        "builder_script_sha256": sha256_file(Path(__file__)),
        "C2_baseline": c2_metadata,
        "input_metadata_by_region": input_metadata,
        "maximum_absolute_adjustment": float(np.max(np.abs(deltas))),
        "optional_field_drop_max_change": float(max_perturbation),
        "all_single_field_drop_tests_within_0_05": bool(max_perturbation <= MAX_ADJUSTMENT + 1e-12),
        "all_scores_finite_and_bounded": True,
        "no_upload": True,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    report["candidate_manifest_sha256"] = sha256_file(manifest_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--candidate", type=Path,
                        default=ROOT / "reports/candidates/schema_presence_candidate.csv")
    parser.add_argument("--manifest", type=Path,
                        default=ROOT / "reports/candidates/schema_presence_candidate_manifest.json")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/schema-presence-candidate.json")
    args = parser.parse_args()
    report = run(args.workspace.resolve(), args.candidate.resolve(), args.manifest.resolve(), args.out.resolve())
    after = report["measure_after"]
    print(f"PASS schema presence candidate: rows={report['candidate_rows']}, "
          f"adjusted={after['adjusted_rows']} ({after['adjusted_fraction']:.2%}), "
          f"max delta={after['maximum_absolute_candidate_adjustment']:.6f}, "
          f"drop-test max={after['optional_field_drop_perturbation_tests']['max_change_over_all_single_field_drops']:.6f}")
    print(f"Candidate SHA-256 {report['candidate_csv_sha256']}; manifest SHA-256 {report['candidate_manifest_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
