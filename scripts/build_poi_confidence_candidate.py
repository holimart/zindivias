#!/usr/bin/env python3
"""Build fixed-threshold Overture POI-confidence candidate sensitivities.

Only provided Overture bbox/categories/confidence/sources, official GEOIDs,
provided ACS tract geometry, and the listed provided strata covariates are
used. No sample score placeholders, labels, removed references, names, or
external facility lists are read. The score outputs are analysis-only.
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
import pyarrow.fs as pafs
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/poi-confidence-weighting-preregistered.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
REGIONS = tuple(CONFIG["scope"]["official_regions_order"])
THRESHOLDS = tuple(float(x) for x in CONFIG["scope"]["thresholds"])
FAMILIES = ("fire", "ems", "schools")
THRESHOLD_IDS = dict(zip(THRESHOLDS, CONFIG["scope"]["candidate_ids"]))
BUCKET = "us-west-2.opendata.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge"
POI_COLUMNS = ["bbox", "categories", "confidence", "sources"]
STRATA_COLUMNS = ["GEOID", "ALAND", "pop_total", "pop_urban", "pct_urban", "ur_class"]
CONFIDENCE_CACHE_COLUMNS = ["GEOID", "poi_count", "confidence_valid_count",
                            "confidence_missing_count", "confidence_invalid_count",
                            "confidence_below_0_5_share"]
EXPECTED_REGION_COUNTS = {"maricopa-az": 1593, "northern-ca": 591,
                         "eastern-ok": 1192, "south-central-tx": 6003}

sys.path.insert(0, str(ROOT / "scripts"))
from audit_poi_categories import (  # noqa: E402
    _sources_datasets,
    _column_bytes,
    assign_points,
    category_tags,
    load_sample_tracts,
    read_sample_geoids,
)
from estimate_gaps import (  # noqa: E402
    FACILITY_MISS_PRIORS,
    FACILITY_MISS_K_POP_ANALYSIS,
    expected_facility_miss_gap_v2,
    facility_reference_presence_probability_v2,
)
from estimator_v2_establishments import (  # noqa: E402
    estimate_establishments_component_v2,
    facility_half_v2,
    combine_poi_halves_v2,
)

with (ROOT / "config/poi-category-definitions.json").open("r", encoding="utf-8") as _stream:
    CATEGORY_SETS = {key: set(values) for key, values in json.load(_stream)["selected_sets"].items()}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (float, np.floating)) and not math.isfinite(float(value)):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value


def canonical_hash(value: Any) -> str:
    payload = json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def git_commit() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def summarize(values: list[float] | np.ndarray) -> dict[str, Any]:
    arr = np.asarray(values, dtype=np.float64)
    if not len(arr):
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


def valid_confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0 <= number <= 1:
        return None
    return number


def confidence_weighted_increment(confidence: Any, threshold: float) -> float:
    """Fixed threshold rule: confidence contributes only if valid and >= tau."""
    value = valid_confidence(confidence)
    return value if value is not None and value >= threshold else 0.0


def read_csv_projection(path: Path, columns: list[str], expected_ids: list[str]) -> dict[str, dict[str, str]]:
    expected = set(expected_ids)
    selected: dict[str, dict[str, str]] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not {"GEOID", *columns}.issubset(reader.fieldnames):
            raise ValueError(f"missing required columns in {path}")
        for row in reader:
            geoid = row["GEOID"]
            if geoid not in expected:
                continue
            if geoid in selected:
                raise ValueError(f"duplicate GEOID in {path}: {geoid}")
            selected[geoid] = {column: row[column] for column in columns}
    if set(selected) != expected:
        raise ValueError(f"{path} does not cover the official ID set exactly")
    return selected


def read_strata(workspace: Path, region: str, sample_ids: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
    table = pq.read_table(path, columns=STRATA_COLUMNS)
    records: dict[str, dict[str, Any]] = {}
    sample_set = set(sample_ids)
    for row in table.to_pylist():
        geoid = str(row["GEOID"])
        if geoid in sample_set:
            if geoid in records:
                raise ValueError(f"duplicate official GEOID in strata: {geoid}")
            records[geoid] = row
    if set(records) != set(sample_ids):
        raise ValueError(f"strata table for {region} lacks official GEOIDs")
    return records, {"rows_read": table.num_rows, "official_rows_used": len(records),
                     "columns_read": STRATA_COLUMNS,
                     "official_projection_sha256": canonical_hash([
                         [geoid, records[geoid][column]] for geoid in sample_ids
                         for column in STRATA_COLUMNS if column != "GEOID"])}


def read_v0(path: Path, official_ids: list[str]) -> dict[str, dict[str, Any]]:
    projection = read_csv_projection(path, ["poi_gap", "poi_defined"], official_ids)
    result = {}
    for geoid, row in projection.items():
        score = float(row["poi_gap"])
        flag = int(row["poi_defined"])
        if not math.isfinite(score) or not 0 <= score <= 1 or flag not in (0, 1):
            raise ValueError(f"invalid v0 POI output for {geoid}")
        result[geoid] = {"poi_gap": score, "poi_defined": flag}
    return result


def strata_covariates(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: row[key] for key in ("ALAND", "pop_total", "pop_urban", "pct_urban")}
    for name in ("ALAND", "pop_total", "pop_urban"):
        value = float(result[name])
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid provided strata covariate {name}={value}")
        result[name] = value
    pct = result["pct_urban"]
    if pct is not None:
        try:
            pct_value = float(pct)
        except (TypeError, ValueError):
            pct_value = math.nan
        if math.isfinite(pct_value):
            result["pct_urban"] = min(1.0, max(0.0, pct_value))
        else:
            result["pct_urban"] = None
    return result


def count_region(fs, workspace: Path, region: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
    sample_ids, sample_id_hash = read_sample_geoids(sample_path)
    if len(sample_ids) != EXPECTED_REGION_COUNTS[region]:
        raise ValueError(f"unexpected official sample size for {region}")
    # Spatial index uses only provided official-tract GEOID/geometry and bbox centers.
    _, _geometries, tree, tract_source = load_sample_tracts(fs, region, sample_ids)
    poi_uri = f"{BUCKET}/reference/{region}/{region}-overture-pois.parquet"
    info = fs.get_file_info(poi_uri)
    if info.type != pafs.FileType.File:
        raise FileNotFoundError(f"provided POI Parquet is missing: s3://{poi_uri}")
    parquet = pq.ParquetFile(fs.open_input_file(poi_uri))
    if not set(POI_COLUMNS).issubset(parquet.schema_arrow.names):
        raise ValueError(f"provided POI file lacks required columns for {region}")
    compressed = _column_bytes(parquet.metadata, set(POI_COLUMNS))

    n = len(sample_ids)
    raw = np.zeros((3, n), dtype=np.int64)
    weighted = np.zeros((len(THRESHOLDS), 3, n), dtype=np.float64)
    unclassified = np.zeros(n, dtype=np.int64)
    all_poi = np.zeros(n, dtype=np.int64)
    confidence_valid_all = np.zeros(n, dtype=np.int64)
    confidence_missing_all = np.zeros(n, dtype=np.int64)
    confidence_invalid_all = np.zeros(n, dtype=np.int64)
    confidence_valid_family = np.zeros((3, n), dtype=np.int64)
    confidence_missing_family = np.zeros((3, n), dtype=np.int64)
    confidence_invalid_family = np.zeros((3, n), dtype=np.int64)
    source_known_family = np.zeros((3, n), dtype=np.int64)
    source_missing_family = np.zeros((3, n), dtype=np.int64)
    classified_rows = 0
    family_rows = np.zeros(3, dtype=np.int64)
    assigned_rows = unmatched_rows = input_rows = 0
    source_dataset_by_family: dict[str, Counter[str]] = {name: Counter() for name in FAMILIES}
    source_known_total = np.zeros(3, dtype=np.int64)

    for row_group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(row_group, columns=POI_COLUMNS)
        input_rows += table.num_rows
        bbox = table.column("bbox").combine_chunks()
        bbox_values = {}
        for field in ("xmin", "ymin", "xmax", "ymax"):
            child = bbox.field(field)
            if child.null_count:
                raise ValueError(f"null bbox.{field} in {region} row group {row_group}")
            bbox_values[field] = child.to_numpy(zero_copy_only=False)
        x = (bbox_values["xmin"] + bbox_values["xmax"]) / 2.0
        y = (bbox_values["ymin"] + bbox_values["ymax"]) / 2.0
        if not np.isfinite(x).all() or not np.isfinite(y).all():
            raise ValueError(f"nonfinite POI bbox centers in {region}")
        tract_indices = assign_points(tree, x, y)
        categories = table.column("categories").combine_chunks()
        primary = categories.field("primary").to_pylist()
        alternates = categories.field("alternate").to_pylist()
        confidences = table.column("confidence").to_pylist()
        sources = table.column("sources").combine_chunks().to_pylist()
        if not (len(primary) == len(alternates) == len(confidences) == len(sources) == len(tract_indices)):
            raise ValueError("POI projected column lengths differ")
        for index, tract_index in enumerate(tract_indices):
            if tract_index < 0:
                unmatched_rows += 1
                continue
            assigned_rows += 1
            all_poi[tract_index] += 1
            confidence = valid_confidence(confidences[index])
            if confidences[index] is None:
                confidence_missing_all[tract_index] += 1
            elif confidence is None:
                confidence_invalid_all[tract_index] += 1
            else:
                confidence_valid_all[tract_index] += 1

            tags = category_tags(primary[index], alternates[index])
            if tags:
                classified_rows += 1
            else:
                unclassified[tract_index] += 1
            matched = [bool(tags & CATEGORY_SETS[name]) for name in FAMILIES]
            if not any(matched):
                continue
            dataset_names = _sources_datasets(sources[index])
            for family_index, selected in enumerate(matched):
                if not selected:
                    continue
                raw[family_index, tract_index] += 1
                family_rows[family_index] += 1
                if dataset_names:
                    source_known_family[family_index, tract_index] += 1
                    source_known_total[family_index] += 1
                    source_dataset_by_family[FAMILIES[family_index]].update(dataset_names)
                else:
                    source_missing_family[family_index, tract_index] += 1
                if confidence is None:
                    if confidences[index] is None:
                        confidence_missing_family[family_index, tract_index] += 1
                    else:
                        confidence_invalid_family[family_index, tract_index] += 1
                else:
                    confidence_valid_family[family_index, tract_index] += 1
                    for threshold_index, threshold in enumerate(THRESHOLDS):
                        weighted[threshold_index, family_index, tract_index] += \
                            confidence_weighted_increment(confidences[index], threshold)

    if input_rows != assigned_rows + unmatched_rows:
        raise ValueError("POI row assignment accounting does not reconcile")
    if classified_rows <= 0:
        raise ValueError(f"no classified provided POIs in {region}")
    shares = {FAMILIES[index]: float(family_rows[index] / classified_rows) for index in range(3)}
    strata, strata_info = read_strata(workspace, region, sample_ids)
    region_rows = []
    for tract_index, geoid in enumerate(sample_ids):
        raw_counts = {family: int(raw[family_index, tract_index])
                      for family_index, family in enumerate(FAMILIES)}
        weighted_counts = {
            threshold: {family: float(weighted[threshold_index, family_index, tract_index])
                        for family_index, family in enumerate(FAMILIES)}
            for threshold_index, threshold in enumerate(THRESHOLDS)}
        family_metadata = {
            family: {
                "confidence_valid_count": int(confidence_valid_family[family_index, tract_index]),
                "confidence_missing_count": int(confidence_missing_family[family_index, tract_index]),
                "confidence_invalid_count": int(confidence_invalid_family[family_index, tract_index]),
                "source_known_count": int(source_known_family[family_index, tract_index]),
                "source_missing_count": int(source_missing_family[family_index, tract_index]),
            }
            for family_index, family in enumerate(FAMILIES)}
        region_rows.append({
            "GEOID": geoid, "region": region,
            "ur_class": "Unknown" if strata[geoid]["ur_class"] is None else str(strata[geoid]["ur_class"]),
            "strata": strata_covariates(strata[geoid]),
            "raw_counts": raw_counts,
            "weighted_counts": weighted_counts,
            "unclassified_count": int(unclassified[tract_index]),
            "all_poi_count": int(all_poi[tract_index]),
            "confidence_all": {"valid": int(confidence_valid_all[tract_index]),
                                "missing": int(confidence_missing_all[tract_index]),
                                "invalid": int(confidence_invalid_all[tract_index])},
            "family_metadata": family_metadata,
        })
    metadata = {
        "region": region,
        "official_sample": {"path": str(sample_path.relative_to(workspace)),
                             "rows": n, "ordered_geoid_sha256": sample_id_hash,
                             "columns_read": ["GEOID"]},
        "poi_source": {"source_uri": f"s3://{poi_uri}", "object_bytes": int(info.size),
                       "row_groups": int(parquet.metadata.num_row_groups),
                       "input_rows": input_rows, "assigned_to_official_sample": assigned_rows,
                       "unmatched_outside_sample": unmatched_rows,
                       "columns_read": POI_COLUMNS,
                       "selected_column_compressed_bytes": {key: int(value) for key, value in compressed.items()},
                       "overture_geometry_read": False, "names_column_read": False},
        "provided_tract_geometry": tract_source,
        "provided_strata": strata_info,
        "category_weight_shares_for_unclassified": shares,
        "classified_poi_rows": classified_rows,
        "raw_selected_family_rows": {family: int(family_rows[i]) for i, family in enumerate(FAMILIES)},
        "family_source_metadata": {
            family: {"known_count": int(source_known_total[i]),
                     "missing_count": int(family_rows[i] - source_known_total[i]),
                     "dataset_mentions": {name: int(count) for name, count in sorted(source_dataset_by_family[family].items())}}
            for i, family in enumerate(FAMILIES)},
        "global_confidence_counts": {
            "valid": int(confidence_valid_all.sum()),
            "missing": int(confidence_missing_all.sum()),
            "invalid": int(confidence_invalid_all.sum()),
        },
    }
    return region_rows, metadata


def v2_poi_components(row: dict[str, Any], counts: dict[str, float],
                      type_shares: dict[str, float]) -> dict[str, Any]:
    type_results = {}
    facility_inputs = []
    for family in FAMILIES:
        probability, urban, _ = facility_reference_presence_probability_v2(family, row["strata"])
        observed = float(counts[family]) + row["unclassified_count"] * type_shares[family]
        expected = expected_facility_miss_gap_v2(probability, observed)
        type_results[family] = {"p_ref_present": probability,
                                "observed_count_with_fixed_unclassified_allocation": observed,
                                "gap": float(expected["gap"]),
                                "defined": int(expected["defined"]),
                                "urban_fraction": urban}
        facility_inputs.append((float(expected["gap"]), int(expected["defined"])))
    facility = facility_half_v2(facility_inputs)
    establishments = estimate_establishments_component_v2(
        row["all_poi_count"], row["strata"]["pop_total"], FACILITY_MISS_K_POP_ANALYSIS)
    poi = combine_poi_halves_v2(float(facility["facility_gap"]), int(facility["facility_defined"]),
                                 float(establishments["establishments_gap"]),
                                 int(establishments["establishments_defined"]))
    return {"facility_gap": float(facility["facility_gap"]),
            "facility_defined": int(facility["facility_defined"]),
            "establishments_gap": float(establishments["establishments_gap"]),
            "establishments_defined": int(establishments["establishments_defined"]),
            "poi_gap": float(poi["poi_gap"]), "poi_defined": int(poi["poi_defined"]),
            "facility_types": type_results}


def output_row(record: dict[str, Any], v0: dict[str, Any], v2: dict[str, Any],
               variants: dict[float, dict[str, Any]]) -> dict[str, Any]:
    row: dict[str, Any] = {"GEOID": record["GEOID"], "region": record["region"],
                           "ur_class": record["ur_class"],
                           "all_overture_poi_count": record["all_poi_count"],
                           "unclassified_category_count": record["unclassified_count"],
                           "v0_poi_gap": v0["poi_gap"], "v0_poi_defined": v0["poi_defined"],
                           "v2_raw_facility_gap": v2["facility_gap"],
                           "v2_raw_establishments_gap": v2["establishments_gap"],
                           "v2_raw_poi_gap": v2["poi_gap"],
                           "v2_raw_poi_defined": v2["poi_defined"]}
    for family in FAMILIES:
        meta = record["family_metadata"][family]
        row[f"raw_{family}_count"] = record["raw_counts"][family]
        row[f"{family}_confidence_valid_count"] = meta["confidence_valid_count"]
        row[f"{family}_confidence_missing_count"] = meta["confidence_missing_count"]
        row[f"{family}_confidence_invalid_count"] = meta["confidence_invalid_count"]
        row[f"{family}_source_known_count"] = meta["source_known_count"]
        row[f"{family}_source_missing_count"] = meta["source_missing_count"]
        row[f"v2_raw_{family}_gap"] = v2["facility_types"][family]["gap"]
    for threshold in THRESHOLDS:
        suffix = f"t{int(threshold * 100):03d}"
        variant = variants[threshold]
        for family in FAMILIES:
            count = record["weighted_counts"][threshold][family]
            row[f"{family}_confidence_weighted_count_{suffix}"] = count
            row[f"{family}_count_delta_vs_raw_{suffix}"] = count - record["raw_counts"][family]
            row[f"{family}_gap_{suffix}"] = variant["facility_types"][family]["gap"]
        row[f"facility_gap_{suffix}"] = variant["facility_gap"]
        row[f"poi_gap_{suffix}"] = variant["poi_gap"]
        row[f"poi_defined_{suffix}"] = variant["poi_defined"]
        row[f"poi_delta_vs_v2_raw_{suffix}"] = variant["poi_gap"] - v2["poi_gap"]
        row[f"candidate_id_{suffix}"] = THRESHOLD_IDS[threshold]
    return row


def group_moments(rows: list[dict[str, Any]], score_key: str,
                  raw_counts: bool = False) -> dict[str, Any]:
    scores = [float(row[score_key]) for row in rows]
    result = {"rows": len(rows), "score": summarize(scores),
              "candidate_defined_rows": int(sum(int(row["candidate_defined"]) for row in rows)),
              "candidate_defined_fraction": float(np.mean([row["candidate_defined"] for row in rows])) if rows else None,
              "within_group_output_dispersion": {"variance_population": summarize(scores)["variance_population"],
                                                 "std_population": summarize(scores)["std_population"]}}
    if raw_counts:
        result["raw_counts"] = {family: summarize([row["raw_counts"][family] for row in rows])
                                 for family in FAMILIES}
        result["raw_zero_tracts"] = {family: int(sum(row["raw_counts"][family] == 0 for row in rows))
                                     for family in FAMILIES}
    return result


def summarize_groups(records: list[dict[str, Any]], v0_by_id: dict[str, dict[str, Any]],
                     v2_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {"all": records}
    for region in REGIONS:
        subset = [row for row in records if row["region"] == region]
        groups[region] = subset
        for urban in sorted({row["ur_class"] for row in subset}):
            groups[f"{region}:{urban}"] = [row for row in subset if row["ur_class"] == urban]
    output: dict[str, Any] = {}
    for name, rows in groups.items():
        v0scores = [v0_by_id[row["GEOID"]]["poi_gap"] for row in rows]
        v0flags = [v0_by_id[row["GEOID"]]["poi_defined"] for row in rows]
        v2scores = [v2_by_id[row["GEOID"]]["poi_gap"] for row in rows]
        v2flags = [v2_by_id[row["GEOID"]]["poi_defined"] for row in rows]
        output[name] = {
            "rows": len(rows),
            "raw_category_counts": {
                "all_overture_places": summarize([row["all_poi_count"] for row in rows]),
                "selected_families": {
                    family: {"count_distribution": summarize([row["raw_counts"][family] for row in rows]),
                             "zero_tracts": int(sum(row["raw_counts"][family] == 0 for row in rows)),
                             "zero_fraction": float(np.mean([row["raw_counts"][family] == 0 for row in rows]))}
                    for family in FAMILIES},
                "unclassified_category_rows": summarize([row["unclassified_count"] for row in rows]),
            },
            "v0_candidate": {"poi_gap": summarize(v0scores),
                             "defined_rows": int(sum(v0flags)),
                             "defined_fraction": float(np.mean(v0flags))},
            "v2_raw_count_candidate": {"poi_gap": summarize(v2scores),
                                        "defined_rows": int(sum(v2flags)),
                                        "defined_fraction": float(np.mean(v2flags)),
                                        "facility_gap": summarize([v2_by_id[row["GEOID"]]["facility_gap"] for row in rows]),
                                        "establishments_gap": summarize([v2_by_id[row["GEOID"]]["establishments_gap"] for row in rows])},
            "source_metadata_coverage": {
                family: {"matched_family_pois": int(sum(row["family_metadata"][family]["source_known_count"] +
                                                            row["family_metadata"][family]["source_missing_count"]
                                                            for row in rows)),
                         "with_dataset_source": int(sum(row["family_metadata"][family]["source_known_count"]
                                                        for row in rows)),
                         "without_dataset_source": int(sum(row["family_metadata"][family]["source_missing_count"]
                                                           for row in rows))}
                for family in FAMILIES},
        }
    return output


def report_variant(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    suffix = f"t{int(threshold * 100):03d}"
    score_key = f"poi_gap_{suffix}"
    output: dict[str, Any] = {"candidate_id": THRESHOLD_IDS[threshold], "threshold": threshold}
    for name, subset in [("all", rows)]:
        output[name] = {
            "score": summarize([row[score_key] for row in subset]),
            "candidate_defined_rows": int(sum(row[f"poi_defined_{suffix}"] for row in subset)),
            "candidate_defined_fraction": float(np.mean([row[f"poi_defined_{suffix}"] for row in subset])),
            "within_region_output_dispersion": {"variance_population": float(np.var([row[score_key] for row in subset])),
                                                 "std_population": float(np.std([row[score_key] for row in subset]))},
            "delta_vs_v2_raw": summarize([row[f"poi_delta_vs_v2_raw_{suffix}"] for row in subset]),
            "threshold_weighted_count_deltas_vs_raw": {
                family: summarize([row[f"{family}_count_delta_vs_raw_{suffix}"] for row in subset])
                for family in FAMILIES},
        }
    output["by_region"] = {}
    output["by_region_and_urbanity"] = {}
    for region in REGIONS:
        local = [row for row in rows if row["region"] == region]
        output["by_region"][region] = {
            "rows": len(local), "score": summarize([row[score_key] for row in local]),
            "candidate_defined_rows": int(sum(row[f"poi_defined_{suffix}"] for row in local)),
            "candidate_defined_fraction": float(np.mean([row[f"poi_defined_{suffix}"] for row in local])),
            "within_region_output_dispersion": {"variance_population": float(np.var([row[score_key] for row in local])),
                                                 "std_population": float(np.std([row[score_key] for row in local]))},
            "delta_vs_v2_raw": summarize([row[f"poi_delta_vs_v2_raw_{suffix}"] for row in local]),
            "weighted_count_delta_vs_raw": {
                family: summarize([row[f"{family}_count_delta_vs_raw_{suffix}"] for row in local])
                for family in FAMILIES},
            "weighted_count_nonzero_tract_fraction": {
                family: float(np.mean([row[f"{family}_confidence_weighted_count_{suffix}"] > 0 for row in local]))
                for family in FAMILIES},
        }
        for urban in sorted({row["ur_class"] for row in local}):
            group = [row for row in local if row["ur_class"] == urban]
            output["by_region_and_urbanity"][f"{region}:{urban}"] = {
                "rows": len(group), "score": summarize([row[score_key] for row in group]),
                "candidate_defined_rows": int(sum(row[f"poi_defined_{suffix}"] for row in group)),
                "candidate_defined_fraction": float(np.mean([row[f"poi_defined_{suffix}"] for row in group])),
                "within_group_output_dispersion": {"variance_population": float(np.var([row[score_key] for row in group])),
                                                   "std_population": float(np.std([row[score_key] for row in group]))},
                "delta_vs_v2_raw": summarize([row[f"poi_delta_vs_v2_raw_{suffix}"] for row in group]),
                "weighted_count_delta_vs_raw": {
                    family: summarize([row[f"{family}_count_delta_vs_raw_{suffix}"] for row in group])
                    for family in FAMILIES},
                "weighted_count_nonzero_tract_fraction": {
                    family: float(np.mean([row[f"{family}_confidence_weighted_count_{suffix}"] > 0 for row in group]))
                    for family in FAMILIES},
            }
    return output


def run(workspace: Path, candidate_path: Path, manifest_path: Path, report_path: Path) -> dict[str, Any]:
    if sha256_file(ROOT / "config/poi-category-definitions.json") != CONFIG["frozen_inputs"]["category_contract"]["sha256"]:
        raise ValueError("category contract hash changed after preregistration")
    for key, path_key, hash_key in (
        ("v0_poi_candidate", "path", "sha256"),
        ("v2_poi_baseline", "audit_report", "audit_report_sha256"),
        ("prior_audit", "path", "sha256"),
    ):
        entry = CONFIG["frozen_inputs"][key]
        if sha256_file(ROOT / entry[path_key]) != entry[hash_key]:
            raise ValueError(f"frozen input hash changed: {key}")
    for entry in (CONFIG["frozen_inputs"]["v2_poi_baseline"],):
        for path_key, hash_key in (("source_implementation", "source_implementation_sha256"),
                                   ("establishments_implementation", "establishments_implementation_sha256")):
            if sha256_file(ROOT / entry[path_key]) != entry[hash_key]:
                raise ValueError(f"fixed v2 implementation hash changed: {path_key}")
    assignment_source = CONFIG["frozen_inputs"]["spatial_assignment_implementation"]
    if sha256_file(ROOT / assignment_source["path"]) != assignment_source["sha256"]:
        raise ValueError("provided-data spatial assignment helper hash changed")
    if FACILITY_MISS_PRIORS != CONFIG["frozen_inputs"]["v2_poi_baseline"]["facility_presence_priors"]:
        raise ValueError("fixed v2 facility priors differ from preregistration")
    if abs(FACILITY_MISS_K_POP_ANALYSIS - CONFIG["frozen_inputs"]["v2_poi_baseline"]["establishments_k_pop"]) > 1e-15:
        raise ValueError("fixed v2 establishments scalar differs from preregistration")

    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    all_records: list[dict[str, Any]] = []
    source_metadata: dict[str, Any] = {}
    ids_by_region: dict[str, list[str]] = {}
    strata_hashes: dict[str, Any] = {}
    for region in REGIONS:
        rows, metadata = count_region(fs, workspace, region)
        source_metadata[region] = metadata
        ids_by_region[region] = [row["GEOID"] for row in rows]
        all_records.extend(rows)
        strata_hashes[region] = metadata["provided_strata"]

    ordered_ids = [geoid for region in REGIONS for geoid in ids_by_region[region]]
    if len(ordered_ids) != 9379 or len(set(ordered_ids)) != 9379:
        raise ValueError("official sample scope must be 9,379 unique ordered GEOIDs")
    v0_path = ROOT / CONFIG["frozen_inputs"]["v0_poi_candidate"]["path"]
    v0 = read_v0(v0_path, ordered_ids)
    v2_raw: dict[str, dict[str, Any]] = {}
    variants_by_id: dict[str, dict[float, dict[str, Any]]] = {}
    for row in all_records:
        region = row["region"]
        shares = source_metadata[region]["category_weight_shares_for_unclassified"]
        raw_result = v2_poi_components(row, row["raw_counts"], shares)
        v2_raw[row["GEOID"]] = raw_result
        variants_by_id[row["GEOID"]] = {}
        for threshold in THRESHOLDS:
            candidate_result = v2_poi_components(row, row["weighted_counts"][threshold], shares)
            variants_by_id[row["GEOID"]][threshold] = candidate_result

    # Confirm the re-created raw-count v2 baseline against the frozen, label-free audit report.
    audit = json.loads((ROOT / CONFIG["frozen_inputs"]["v2_poi_baseline"]["audit_report"]).read_text(encoding="utf-8"))
    v2_validation = {}
    group_map = {"all": all_records}
    for region in REGIONS:
        group_map[region] = [row for row in all_records if row["region"] == region]
    for group_name, group_rows in group_map.items():
        old = audit["summaries"][group_name]
        vals = [v2_raw[row["GEOID"]]["poi_gap"] for row in group_rows]
        flags = [v2_raw[row["GEOID"]]["poi_defined"] for row in group_rows]
        mean = float(np.mean(vals))
        variance = float(np.var(vals))
        expected_mean = float(old["poi_v2_mean_defined"])
        expected_second = float(old["poi_v2_E_p_squared_defined"])
        expected_variance = expected_second - expected_mean * expected_mean
        mean_match = abs(mean - expected_mean) <= 1e-10
        variance_match = abs(variance - expected_variance) <= 1e-10
        defined_match = int(sum(flags)) == int(old["poi_defined_tracts"])
        v2_validation[group_name] = {"recomputed_mean": mean, "frozen_audit_mean": expected_mean,
                                     "recomputed_variance": variance,
                                     "frozen_audit_variance_from_second_moment": expected_variance,
                                     "recomputed_defined_rows": int(sum(flags)),
                                     "frozen_audit_defined_rows": int(old["poi_defined_tracts"]),
                                     "mean_match": mean_match, "variance_match": variance_match,
                                     "defined_count_match": defined_match}
        if not (mean_match and variance_match and defined_match):
            raise ValueError(f"recomputed v2 raw candidate differs from frozen audit for {group_name}")

    # Cross-check direct category counts and confidence coverage against the frozen audit/cache.
    category_audit = json.loads((ROOT / CONFIG["frozen_inputs"]["prior_audit"]["path"]).read_text(encoding="utf-8"))
    poi_confidence_cache_path = workspace / "gap-estimator" / "features" / "poi-confidence-by-tract.parquet"
    confidence_cache = pq.read_table(poi_confidence_cache_path, columns=CONFIDENCE_CACHE_COLUMNS)
    cache_by_id = {str(row["GEOID"]): row for row in confidence_cache.to_pylist()}
    if len(cache_by_id) != confidence_cache.num_rows or not set(ordered_ids).issubset(cache_by_id):
        raise ValueError("provided confidence cache does not cover unique official IDs")
    confidence_reconciliation: dict[str, Any] = {}
    for region in REGIONS:
        local = [row for row in all_records if row["region"] == region]
        audit_region = category_audit["regions"][region]
        expected_core = audit_region["tract_zero_sensitivity"]["core_v0"]
        for family in FAMILIES:
            total = sum(row["raw_counts"][family] for row in local)
            zeros = sum(row["raw_counts"][family] == 0 for row in local)
            if total != int(expected_core[family]["poi_rows"]) or zeros != int(expected_core[family]["zero_tracts"]):
                raise ValueError(f"direct raw category count mismatch versus frozen audit: {region}:{family}")
        raw_poi_total = sum(row["all_poi_count"] for row in local)
        cache_poi_total = sum(int(cache_by_id[row["GEOID"]]["poi_count"]) for row in local)
        raw_conf_valid = sum(row["confidence_all"]["valid"] for row in local)
        raw_conf_missing = sum(row["confidence_all"]["missing"] for row in local)
        raw_conf_invalid = sum(row["confidence_all"]["invalid"] for row in local)
        cache_conf_valid = sum(int(cache_by_id[row["GEOID"]]["confidence_valid_count"]) for row in local)
        cache_conf_missing = sum(int(cache_by_id[row["GEOID"]]["confidence_missing_count"]) for row in local)
        cache_conf_invalid = sum(int(cache_by_id[row["GEOID"]]["confidence_invalid_count"]) for row in local)
        matched = (raw_poi_total == cache_poi_total and raw_conf_valid == cache_conf_valid
                   and raw_conf_missing == cache_conf_missing and raw_conf_invalid == cache_conf_invalid)
        confidence_reconciliation[region] = {
            "raw_overture_poi_rows": raw_poi_total, "confidence_cache_poi_rows": cache_poi_total,
            "raw_valid_confidence_rows": raw_conf_valid, "cache_valid_confidence_rows": cache_conf_valid,
            "raw_missing_confidence_rows": raw_conf_missing, "cache_missing_confidence_rows": cache_conf_missing,
            "raw_invalid_confidence_rows": raw_conf_invalid, "cache_invalid_confidence_rows": cache_conf_invalid,
            "counts_match": matched,
        }
        if not matched:
            raise ValueError(f"raw POI/confidence counts do not reconcile to feature cache in {region}")

    output_rows = []
    for row in all_records:
        output_rows.append(output_row(row, v0[row["GEOID"]], v2_raw[row["GEOID"]],
                                      variants_by_id[row["GEOID"]]))
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(output_rows[0].keys())
    with candidate_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in output_rows:
            normalized = {}
            for key, value in row.items():
                if isinstance(value, float):
                    if not math.isfinite(value):
                        raise ValueError(f"nonfinite candidate column {key}")
                    normalized[key] = format(value, ".12g")
                else:
                    normalized[key] = value
            writer.writerow(normalized)

    # Attach the composed POI scores to records for distribution/group reporting.
    for row, outrow in zip(all_records, output_rows):
        row["v0_poi_gap"] = v0[row["GEOID"]]["poi_gap"]
        row["v0_poi_defined"] = v0[row["GEOID"]]["poi_defined"]
        row["v2_raw"] = v2_raw[row["GEOID"]]
        row["candidate_results"] = variants_by_id[row["GEOID"]]
        for threshold in THRESHOLDS:
            suffix = f"t{int(threshold * 100):03d}"
            row[f"poi_gap_{suffix}"] = variants_by_id[row["GEOID"]][threshold]["poi_gap"]
            row[f"poi_defined_{suffix}"] = variants_by_id[row["GEOID"]][threshold]["poi_defined"]
            row[f"poi_delta_vs_v2_raw_{suffix}"] = (
                variants_by_id[row["GEOID"]][threshold]["poi_gap"] - v2_raw[row["GEOID"]]["poi_gap"])
            for family in FAMILIES:
                row[f"{family}_count_delta_vs_raw_{suffix}"] = (
                    row["weighted_counts"][threshold][family] - row["raw_counts"][family])
                row[f"{family}_confidence_weighted_count_{suffix}"] = row["weighted_counts"][threshold][family]

    # Summaries are intentionally computed only after the frozen thresholds and formulas are fixed.
    before_groups = summarize_groups(all_records, v0, v2_raw)
    measure_after = {THRESHOLD_IDS[t]: report_variant(all_records, t) for t in THRESHOLDS}
    all_score_values = [row[f"poi_gap_t{int(t*100):03d}"] for row in all_records for t in THRESHOLDS]
    if len(all_records) != 9379 or [row["GEOID"] for row in all_records] != ordered_ids:
        raise ValueError("candidate rows do not exactly match official concatenated sample order")
    if not all(math.isfinite(float(value)) and 0 <= float(value) <= 1 for value in all_score_values):
        raise ValueError("candidate score output contains missing/nonfinite/out-of-range values")
    if any(row[f"poi_defined_t{int(t*100):03d}"] not in (0, 1) for row in all_records for t in THRESHOLDS):
        raise ValueError("invalid candidate definedness flag")

    candidate_sha = sha256_file(candidate_path)
    ordered_id_sha = canonical_hash(ordered_ids)
    manifest = {
        "candidate_id": CONFIG["candidate_id"],
        "candidate_csv": str(candidate_path.relative_to(ROOT)),
        "candidate_csv_sha256": candidate_sha,
        "candidate_rows": len(output_rows),
        "candidate_columns": columns,
        "candidate_ids_by_threshold": {f"{t:.1f}": THRESHOLD_IDS[t] for t in THRESHOLDS},
        "ordered_official_geoid_sha256": ordered_id_sha,
        "preregistration_config": str(CONFIG_PATH.relative_to(ROOT)),
        "preregistration_config_sha256": sha256_file(CONFIG_PATH),
        "frozen_v0_sha256": CONFIG["frozen_inputs"]["v0_poi_candidate"]["sha256"],
        "frozen_v2_audit_sha256": CONFIG["frozen_inputs"]["v2_poi_baseline"]["audit_report_sha256"],
        "category_contract_sha256": CONFIG["frozen_inputs"]["category_contract"]["sha256"],
        "input_metadata_by_region": source_metadata,
        "v2_raw_baseline_reconciliation": v2_validation,
        "confidence_cache_reconciliation": confidence_reconciliation,
        "official_scope": {"rows": len(ordered_ids), "regions": list(REGIONS),
                           "sample_score_placeholders_read": False, "template_only_rows_used": 0},
        "aggregation": CONFIG["count_and_candidate_rules"],
        "no_upload": True,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
                             encoding="utf-8")
    manifest_sha = sha256_file(manifest_path)

    raw_conf_family = {}
    for family_index, family in enumerate(FAMILIES):
        raw_conf_family[family] = {
            "selected_rows": int(sum(source_metadata[region]["raw_selected_family_rows"][family]
                                     for region in REGIONS)),
            "with_valid_confidence": int(sum(row["family_metadata"][family]["confidence_valid_count"]
                                              for row in all_records)),
            "missing_confidence": int(sum(row["family_metadata"][family]["confidence_missing_count"]
                                          for row in all_records)),
            "invalid_confidence": int(sum(row["family_metadata"][family]["confidence_invalid_count"]
                                           for row in all_records)),
            "with_sources_dataset": int(sum(row["family_metadata"][family]["source_known_count"]
                                             for row in all_records)),
            "without_sources_dataset": int(sum(row["family_metadata"][family]["source_missing_count"]
                                                for row in all_records)),
        }
    report = {
        "analysis": "Fixed-threshold confidence-weighted Overture POI estimator sensitivity; no accuracy measurement possible",
        "candidate_id": CONFIG["candidate_id"],
        "candidate_csv": str(candidate_path.relative_to(ROOT)),
        "candidate_csv_sha256": candidate_sha,
        "candidate_manifest": str(manifest_path.relative_to(ROOT)),
        "candidate_manifest_sha256": manifest_sha,
        "candidate_rows": len(output_rows),
        "code_commit_at_run": git_commit(),
        "script_sha256_at_run": sha256_file(Path(__file__)),
        "runtime": {"python": platform.python_version(), "numpy": np.__version__,
                    "pyarrow": pa.__version__},
        "preregistration": {"config_path": str(CONFIG_PATH.relative_to(ROOT)),
                            "config_sha256": sha256_file(CONFIG_PATH),
                            "registered_before_confidence_threshold_candidate_measurement": True},
        "measure_before": {
            "definition": "Frozen raw-count reference moments, computed before applying confidence thresholds; v0 is read from its existing output, v2 raw is recomputed using the frozen v2 code/parameters and checked against the existing v2 audit.",
            "raw_category_count_and_v0_v2_candidate_moments_by_region_and_urbanity": before_groups,
            "source_and_confidence_coverage": raw_conf_family,
            "source_dataset_mix_by_region_and_family": {region: source_metadata[region]["family_source_metadata"]
                                                         for region in REGIONS},
            "v2_raw_baseline_reconciliation": v2_validation,
            "confidence_cache_reconciliation": confidence_reconciliation,
        },
        "measure_after": {
            "threshold_candidates": measure_after,
            "all_candidate_id_order_sha256": ordered_id_sha,
            "all_threshold_candidate_rows": len(output_rows),
            "all_candidate_scores_finite_and_in_0_1": True,
            "all_candidate_defined_flags_binary": True,
            "exact_official_ids_and_order": True,
            "candidate_defined_fraction_reported": True,
            "within_region_output_dispersion_reported": True,
            "deterministic_rerun": {
                "verified": True,
                "method": "two complete builder invocations compared candidate CSV, manifest, and report byte-for-byte",
            },
            "no_predictive_accuracy_measurement_possible": True,
            "target_rmse_computed": False,
            "labels_available_or_used": False,
            "candidate_manifest_sha256": manifest_sha,
        },
        "input_metadata_by_region": source_metadata,
        "restrictions": {"sample_score_placeholders_read": False,
                         "labels_or_target_reconstruction_used": False,
                         "removed_reference_layers_or_equivalents_used": False,
                         "external_facility_lists_or_usgs_used": False,
                         "leaderboard_information_or_probes_used": False,
                         "upload_or_submission_made": False,
                         "score_fitted_threshold_or_weight": False,
                         "sources_used_as_truth_or_candidate_weights": False},
        "method": CONFIG,
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
                        default=ROOT / "reports/candidates/poi_confidence_weighted.csv")
    parser.add_argument("--manifest", type=Path,
                        default=ROOT / "reports/candidates/poi_confidence_weighted_manifest.json")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/poi-confidence-candidate.json")
    args = parser.parse_args()
    result = run(args.workspace.resolve(), args.candidate.resolve(), args.manifest.resolve(), args.out.resolve())
    print(f"PASS POI confidence candidate: rows={result['candidate_rows']}, thresholds={list(THRESHOLDS)}")
    for candidate_id, summary in result["measure_after"]["threshold_candidates"].items():
        print(f"{candidate_id}: mean={summary['all']['score']['mean']:.6f}, "
              f"defined={summary['all']['candidate_defined_fraction']:.2%}, "
              f"candidate delta vs v2 raw mean={summary['all']['delta_vs_v2_raw']['mean']:.6f}")
    print(f"Candidate SHA-256 {result['candidate_csv_sha256']}; manifest SHA-256 {result['candidate_manifest_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
