#!/usr/bin/env python3
"""Screen a preregistered, provided-feature building-morphology prior.

Reads only official sample GEOIDs, provided Overture building geometry and
selected attributes, provided ACS tract geometry for spatial assignment, and
provided ALAND/population context. It never reads reference footprint data,
labels, sample score values, or leaderboard feedback. Outputs are diagnostic
candidate features, not a submission.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

from estimate_gaps import BUCKET, REGIONS, arrow_bbox_centers, assign_points, open_source
from estimator_v2_building import BUILDING_GAP_V2

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/building-morphology-preregistered.json"
with CONFIG_PATH.open("r", encoding="utf-8") as stream:
    CONFIG = json.load(stream)
BUILDING_COLUMNS = ["bbox", "geometry", "subtype", "has_parts", "num_floors", "height"]
BETAS = tuple(float(value) for value in CONFIG["component_rule"]["sensitivity_betas"])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def current_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def official_ids(path: Path) -> list[str]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if "GEOID" not in (reader.fieldnames or []):
            raise ValueError(f"official sample has no GEOID column: {path}")
        ids = [row["GEOID"] for row in reader]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"empty or duplicate official sample IDs: {path}")
    return ids


def clip01(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("morphology inputs must be finite")
    return min(1.0, max(0.0, float(value)))


def morphology_support(
    *, building_count: int, land_area_m2: float, median_area_m2: float,
    verticality: float, subtype_share: float, has_parts_share: float,
    population: float,
) -> tuple[float, dict[str, float]]:
    """Compute the frozen six-term support index from config coefficients."""
    if building_count < 0 or not math.isfinite(land_area_m2) or land_area_m2 < 0:
        raise ValueError("invalid building count or ALAND")
    if not math.isfinite(population) or population < 0:
        raise ValueError("pop_total must be finite and nonnegative")
    area_km2 = max(land_area_m2 / 1_000_000.0,
                   float(CONFIG["spatial_assignment"]["land_area_floor_km2"]))
    scales = CONFIG["morphology_support"]["scales"]
    features = {
        "structure_density": clip01(math.log1p(building_count / area_km2) / math.log1p(float(scales["density_buildings_per_km2"]))),
        "median_area": clip01(math.log1p(max(0.0, median_area_m2)) / math.log1p(float(scales["median_area_m2"]))),
        "verticality": clip01(verticality),
        "subtype_support": clip01(subtype_share),
        "parts_support": clip01(has_parts_share),
        "population_context": clip01(math.log1p(population / area_km2) / math.log1p(float(scales["population_per_km2"]))),
    }
    weights = CONFIG["morphology_support"]["weights"]
    if set(weights) != set(features) or not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-12):
        raise ValueError("preregistered morphology weights must cover terms and sum to one")
    score = sum(float(weights[name]) * features[name] for name in features)
    return clip01(score), features


def morphology_gap(support: float, beta: float, defined: bool) -> float:
    """Return the bounded prior or its explicit undefined numeric placeholder."""
    if not defined:
        return 0.0
    base = float(CONFIG["component_rule"]["baseline_gap_value"])
    lower, upper = (float(value) for value in CONFIG["component_rule"]["clip_range"])
    return min(upper, max(lower, base + beta * (1.0 - clip01(support))))


def finite_summary(values: list[float] | np.ndarray) -> dict[str, float | int | None]:
    array = np.asarray(values, dtype=np.float64)
    array = array[np.isfinite(array)]
    if not len(array):
        return {"n": 0, "mean": None, "std": None, "min": None,
                "q10": None, "median": None, "q90": None, "max": None}
    q10, median, q90 = np.quantile(array, [0.1, 0.5, 0.9])
    return {
        "n": int(len(array)), "mean": float(array.mean()), "std": float(array.std()),
        "min": float(array.min()), "q10": float(q10), "median": float(median),
        "q90": float(q90), "max": float(array.max()),
    }


def arrow_float_values(array: pa.Array) -> np.ndarray:
    cast = pc.cast(array, pa.float64(), safe=False)
    filled = pc.fill_null(cast, pa.scalar(float("nan"), type=pa.float64()))
    return filled.to_numpy(zero_copy_only=False)


def subtype_present(array: pa.Array) -> np.ndarray:
    filled = pc.fill_null(array, pa.scalar("", type=pa.string()))
    trimmed = pc.utf8_trim_whitespace(filled)
    return pc.greater(pc.utf8_length(trimmed), 0).to_numpy(zero_copy_only=False)


def read_region_inputs(workspace: Path, region: str, fs) -> dict[str, Any]:
    sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
    ids = official_ids(sample_path)
    strata_path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
    strata_table = pq.read_table(strata_path, columns=["GEOID", "ALAND", "pop_total"])
    strata_rows = {str(row["GEOID"]): row for row in strata_table.to_pylist()}
    if len(strata_rows) != strata_table.num_rows or not set(ids).issubset(strata_rows):
        raise ValueError(f"strata do not uniquely cover official sample IDs in {region}")

    feature_path = workspace / "gap-estimator" / "features" / f"{region}-overture-tract-summary.parquet"
    feature_table = pq.read_table(feature_path, columns=["GEOID", "overture_buildings"])
    feature_rows = feature_table.to_pylist()
    if [str(row["GEOID"]) for row in feature_rows] != ids:
        raise ValueError(f"feature-cache IDs/order do not match official sample in {region}")
    feature_counts = np.asarray([int(row["overture_buildings"] or 0) for row in feature_rows], dtype=np.int64)

    geometry_key = f"{BUCKET}/reference/{region}/{region}-census-acs-housing.parquet"
    geometry_pf = pq.ParquetFile(fs.open_input_file(geometry_key))
    geometry_table = geometry_pf.read(columns=["GEOID", "geometry"])
    geometry_ids = [str(value) for value in geometry_table.column("GEOID").to_pylist()]
    geometry_map = dict(zip(geometry_ids, geometry_table.column("geometry").to_pylist()))
    if len(geometry_map) != len(geometry_ids) or not set(ids).issubset(geometry_map):
        raise ValueError(f"provided ACS tract geometries do not cover official sample in {region}")
    tract_geometries = [shapely.from_wkb(geometry_map[geoid]) for geoid in ids]
    tree = shapely.STRtree(tract_geometries)

    covariates = []
    for geoid in ids:
        row = strata_rows[geoid]
        land = float(row["ALAND"])
        population = float(row["pop_total"])
        if not math.isfinite(land) or land < 0 or not math.isfinite(population) or population < 0:
            raise ValueError(f"invalid allowed context for {region}:{geoid}")
        covariates.append({"ALAND": land, "pop_total": population})
    return {
        "ids": ids,
        "sample_path": sample_path,
        "strata_path": strata_path,
        "feature_path": feature_path,
        "feature_counts": feature_counts,
        "strata": covariates,
        "tract_geometries": tract_geometries,
        "tree": tree,
        "geometry_key": geometry_key,
        "strata_rows": strata_table.num_rows,
        "feature_rows": feature_table.num_rows,
    }


def scan_buildings(fs, region: str, inputs: dict[str, Any], transformer) -> dict[str, Any]:
    key, object_bytes, parquet_file = open_source(fs, region, "buildings")
    n_tracts = len(inputs["ids"])
    counts = np.zeros(n_tracts, dtype=np.int64)
    subtype_n = np.zeros(n_tracts, dtype=np.int64)
    parts_true_n = np.zeros(n_tracts, dtype=np.int64)
    parts_nonnull_n = np.zeros(n_tracts, dtype=np.int64)
    floors_present_n = np.zeros(n_tracts, dtype=np.int64)
    height_present_n = np.zeros(n_tracts, dtype=np.int64)
    vertical_sum = np.zeros(n_tracts, dtype=np.float64)
    invalid_geometry_n = np.zeros(n_tracts, dtype=np.int64)
    area_tract_chunks: list[np.ndarray] = []
    area_value_chunks: list[np.ndarray] = []
    total_rows = unmatched = assigned_rows = invalid_geometry_total = 0
    subtype_counts: Counter[str] = Counter()
    row_groups = parquet_file.metadata.num_row_groups

    for rg in range(row_groups):
        table = parquet_file.read_row_group(rg, columns=BUILDING_COLUMNS)
        x, y = arrow_bbox_centers(table)
        assignment, outside = assign_points(inputs["tree"], x, y)
        matched = assignment >= 0
        tract_ix = assignment[matched]
        matched_ix = np.flatnonzero(matched)
        n_rows = len(assignment)
        total_rows += n_rows
        unmatched += outside
        assigned_rows += int(matched.sum())
        if len(matched_ix):
            counts += np.bincount(tract_ix, minlength=n_tracts)

            subtype_array = table.column("subtype").combine_chunks()
            subtype_flag = subtype_present(subtype_array)[matched]
            subtype_n += np.bincount(tract_ix, weights=subtype_flag.astype(np.int64), minlength=n_tracts).astype(np.int64)
            for value in np.asarray(subtype_array.to_pylist(), dtype=object)[matched]:
                if value is not None and str(value).strip():
                    subtype_counts[str(value).strip()] += 1

            parts_array = table.column("has_parts").combine_chunks()
            parts_valid = parts_array.is_valid().to_numpy(zero_copy_only=False)[matched]
            parts_values = pc.fill_null(parts_array, pa.scalar(False, type=pa.bool_())).to_numpy(zero_copy_only=False)[matched]
            parts_nonnull_n += np.bincount(tract_ix, weights=parts_valid.astype(np.int64), minlength=n_tracts).astype(np.int64)
            parts_true_n += np.bincount(tract_ix, weights=parts_values.astype(np.int64), minlength=n_tracts).astype(np.int64)

            floors = arrow_float_values(table.column("num_floors").combine_chunks())
            heights = arrow_float_values(table.column("height").combine_chunks())
            floors = floors[matched]
            heights = heights[matched]
            floors_finite = np.isfinite(floors)
            heights_finite = np.isfinite(heights)
            floors_present_n += np.bincount(tract_ix, weights=floors_finite.astype(np.int64), minlength=n_tracts).astype(np.int64)
            height_present_n += np.bincount(tract_ix, weights=heights_finite.astype(np.int64), minlength=n_tracts).astype(np.int64)
            positive_floors = floors_finite & (floors > 0)
            positive_height = heights_finite & (heights > 0)
            scales = CONFIG["morphology_support"]["scales"]
            equivalent_floors = np.where(positive_floors, floors,
                                         np.where(positive_height, heights / float(scales["metres_per_floor"]), 0.0))
            vertical = np.clip(equivalent_floors / float(scales["vertical_floors"]), 0.0, 1.0)
            vertical_sum += np.bincount(tract_ix, weights=vertical, minlength=n_tracts)

            wkb_array = table.column("geometry").combine_chunks()
            geometry_values = wkb_array.to_numpy(zero_copy_only=False)
            geometries = shapely.from_wkb(geometry_values, on_invalid="ignore")
            projected = shapely.transform(geometries, transformer.transform, interleaved=False)
            areas = np.asarray(shapely.area(projected), dtype=np.float64)
            valid = np.asarray(shapely.is_valid(projected), dtype=bool) & np.isfinite(areas) & (areas > 0)
            matched_valid = matched & valid
            invalid_local = matched & ~valid
            if invalid_local.any():
                invalid_geometry_n += np.bincount(assignment[invalid_local], minlength=n_tracts)
                invalid_geometry_total += int(invalid_local.sum())
            if matched_valid.any():
                area_tract_chunks.append(assignment[matched_valid].astype(np.int32, copy=False))
                area_value_chunks.append(areas[matched_valid].astype(np.float64, copy=False))
        if (rg + 1) % 25 == 0 or rg + 1 == row_groups:
            print(f"{region}: building row groups {rg + 1}/{row_groups}; rows={total_rows}", flush=True)

    if total_rows != parquet_file.metadata.num_rows:
        raise ValueError(f"row count mismatch for provided buildings in {region}")
    if not np.array_equal(counts, inputs["feature_counts"]):
        mismatch = int(np.sum(counts != inputs["feature_counts"]))
        raise ValueError(f"raw bbox-center building count differs from provided feature cache in {region}: {mismatch} IDs")

    if area_tract_chunks:
        area_tract = np.concatenate(area_tract_chunks)
        area_values = np.concatenate(area_value_chunks)
        order = np.argsort(area_tract, kind="stable")
        sorted_tract = area_tract[order]
        sorted_area = area_values[order]
        boundaries = np.searchsorted(sorted_tract, np.arange(n_tracts + 1), side="left")
        median_area = np.zeros(n_tracts, dtype=np.float64)
        q10_area = np.zeros(n_tracts, dtype=np.float64)
        q90_area = np.zeros(n_tracts, dtype=np.float64)
        valid_area_count = np.zeros(n_tracts, dtype=np.int64)
        for i in range(n_tracts):
            values = sorted_area[boundaries[i]:boundaries[i + 1]]
            if len(values):
                q10_area[i], median_area[i], q90_area[i] = np.quantile(values, [0.1, 0.5, 0.9])
                valid_area_count[i] = len(values)
        regional_area_quantiles = finite_summary(area_values)
        tract_median_area_summary = finite_summary(median_area[valid_area_count > 0])
    else:
        median_area = q10_area = q90_area = np.zeros(n_tracts, dtype=np.float64)
        valid_area_count = np.zeros(n_tracts, dtype=np.int64)
        regional_area_quantiles = finite_summary([])
        tract_median_area_summary = finite_summary([])

    return {
        "counts": counts,
        "median_area_m2": median_area,
        "q10_area_m2": q10_area,
        "q90_area_m2": q90_area,
        "valid_area_count": valid_area_count,
        "subtype_n": subtype_n,
        "parts_true_n": parts_true_n,
        "parts_nonnull_n": parts_nonnull_n,
        "floors_present_n": floors_present_n,
        "height_present_n": height_present_n,
        "vertical_sum": vertical_sum,
        "invalid_geometry_n": invalid_geometry_n,
        "summary": {
            "source_uri": f"s3://{key}",
            "object_bytes": object_bytes,
            "source_rows": total_rows,
            "source_row_groups": row_groups,
            "assigned_to_official_tracts": assigned_rows,
            "unassigned_bbox_centers": unmatched,
            "invalid_or_missing_assigned_geometries": invalid_geometry_total,
            "valid_assigned_geometries": int(valid_area_count.sum()),
            "columns_read": BUILDING_COLUMNS,
            "projected_building_area_m2_all_assigned": regional_area_quantiles,
            "distribution_of_official_tract_median_area_m2": tract_median_area_summary,
            "subtype_counts_top_20": dict(subtype_counts.most_common(20)),
        },
    }


def component_summary(values: np.ndarray, baseline: np.ndarray, defined: np.ndarray) -> dict[str, Any]:
    changed = np.abs(values - baseline) > 1e-12
    lower, upper = (float(value) for value in CONFIG["component_rule"]["clip_range"])
    return {
        "all_official_tracts": finite_summary(values),
        "defined_tracts_only": finite_summary(values[defined]),
        "changed_vs_constant_baseline_tracts": int(changed.sum()),
        "changed_vs_constant_baseline_share_all": float(changed.mean()),
        "changed_vs_constant_baseline_share_defined": float(changed[defined].mean()) if defined.any() else None,
        "nonfinite_values": int((~np.isfinite(values)).sum()),
        "bounded_to_preregistered_range": bool(np.isfinite(values).all() and np.all((values >= lower) & (values <= upper))),
    }


def missingness_summary(total_by_tract: np.ndarray, present_by_tract: np.ndarray) -> dict[str, int | float]:
    total = int(np.sum(total_by_tract))
    present = int(np.sum(present_by_tract))
    if present < 0 or present > total:
        raise ValueError("attribute presence count is inconsistent with assigned building count")
    missing = total - present
    return {"missing_rows": missing, "missing_share": missing / total if total else 0.0}


def run(workspace: Path, candidate_csv: Path, out_json: Path) -> dict[str, Any]:
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    all_records: list[dict[str, Any]] = []
    region_reports: dict[str, Any] = {}
    total_ids = 0
    seen_ids: set[str] = set()

    baseline_gap = float(BUILDING_GAP_V2)
    prereg_base = float(CONFIG["component_rule"]["baseline_gap_value"])
    if not math.isclose(baseline_gap, prereg_base, abs_tol=1e-12):
        raise ValueError("preregistered baseline differs from existing constant building prior")

    for region in REGIONS:
        inputs = read_region_inputs(workspace, region, fs)
        ids = inputs["ids"]
        if seen_ids.intersection(ids):
            raise ValueError("duplicate official GEOIDs across regions")
        seen_ids.update(ids)
        total_ids += len(ids)
        raw = scan_buildings(fs, region, inputs, transformer)
        count = raw["counts"]
        defined = count > 0
        strata = inputs["strata"]
        support = np.zeros(len(ids), dtype=np.float64)
        support_features: dict[str, list[float]] = {name: [] for name in CONFIG["morphology_support"]["weights"]}
        rows: list[dict[str, Any]] = []
        for i, geoid in enumerate(ids):
            population = float(strata[i]["pop_total"])
            land = float(strata[i]["ALAND"])
            n = int(count[i])
            subtype_share = float(raw["subtype_n"][i] / n) if n else 0.0
            parts_share = float(raw["parts_true_n"][i] / n) if n else 0.0
            verticality = float(raw["vertical_sum"][i] / n) if n else 0.0
            score, terms = morphology_support(
                building_count=n, land_area_m2=land,
                median_area_m2=float(raw["median_area_m2"][i]),
                verticality=verticality, subtype_share=subtype_share,
                has_parts_share=parts_share, population=population,
            )
            support[i] = score
            for name, value in terms.items():
                support_features[name].append(value)
            baseline = baseline_gap if defined[i] else 0.0
            row = {
                "GEOID": geoid,
                "region": region,
                "building_defined": int(defined[i]),
                "overture_building_count": n,
                "building_area_q10_m2": float(raw["q10_area_m2"][i]),
                "building_area_median_m2": float(raw["median_area_m2"][i]),
                "building_area_q90_m2": float(raw["q90_area_m2"][i]),
                "valid_geometry_count": int(raw["valid_area_count"][i]),
                "morphology_support": score,
                "support_structure_density": terms["structure_density"],
                "support_median_area": terms["median_area"],
                "support_verticality": terms["verticality"],
                "support_subtype": terms["subtype_support"],
                "support_has_parts": terms["parts_support"],
                "support_population_context": terms["population_context"],
                "building_gap_constant_0_01": baseline,
            }
            for beta in BETAS:
                row[f"building_gap_morph_beta_{beta:.2f}"] = morphology_gap(score, beta, bool(defined[i]))
            rows.append(row)
            all_records.append(row)

        region_rows = rows
        base_values = np.asarray([row["building_gap_constant_0_01"] for row in region_rows], dtype=np.float64)
        region_defined = np.asarray([bool(row["building_defined"]) for row in region_rows], dtype=bool)
        sensitivity = {}
        for beta in BETAS:
            values = np.asarray([row[f"building_gap_morph_beta_{beta:.2f}"] for row in region_rows], dtype=np.float64)
            sensitivity[f"{beta:.2f}"] = component_summary(values, base_values, region_defined)
        n = len(ids)
        subtype_missing = missingness_summary(count, raw["subtype_n"])
        parts_missing = missingness_summary(count, raw["parts_nonnull_n"])
        floors_missing = missingness_summary(count, raw["floors_present_n"])
        height_missing = missingness_summary(count, raw["height_present_n"])
        total_assigned_buildings = int(count.sum())
        geometry_invalid_share = (float(raw["invalid_geometry_n"].sum() / total_assigned_buildings)
                                  if total_assigned_buildings else 0.0)
        region_reports[region] = {
            "official_tracts": n,
            "raw_building_scan": raw["summary"],
            "building_count_matches_existing_feature_cache": True,
            "attribute_missingness": {
                "assigned_building_rows": total_assigned_buildings,
                "subtype_null_or_empty_rows": subtype_missing["missing_rows"],
                "subtype_null_or_empty_share": subtype_missing["missing_share"],
                "has_parts_null_rows": parts_missing["missing_rows"],
                "has_parts_null_share": parts_missing["missing_share"],
                "has_parts_true_rows": int(raw["parts_true_n"].sum()),
                "num_floors_null_rows": floors_missing["missing_rows"],
                "num_floors_null_share": floors_missing["missing_share"],
                "height_null_rows": height_missing["missing_rows"],
                "height_null_share": height_missing["missing_share"],
                "geometry_missing_or_invalid_assigned_rows": int(raw["invalid_geometry_n"].sum()),
                "geometry_missing_or_invalid_share": geometry_invalid_share,
            },
            "morphology_support_terms_defined_tracts_only": {
                name: finite_summary(np.asarray(values)[region_defined])
                for name, values in support_features.items()
            },
            "morphology_support_defined_tracts_only": finite_summary(support[region_defined]),
            "baseline_component": finite_summary(base_values),
            "sensitivity_by_beta": sensitivity,
        }

    if total_ids != 9379 or len(seen_ids) != 9379:
        raise ValueError(f"expected 9,379 unique official IDs, found {total_ids}/{len(seen_ids)}")

    candidate_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(all_records[0])
    with candidate_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in all_records:
            writer.writerow({key: (format(value, ".12g") if isinstance(value, float) else value)
                             for key, value in row.items()})
    candidate_hash = sha256_file(candidate_csv)

    all_baseline = np.asarray([row["building_gap_constant_0_01"] for row in all_records], dtype=np.float64)
    all_defined = np.asarray([bool(row["building_defined"]) for row in all_records], dtype=bool)
    global_sensitivity = {}
    for beta in BETAS:
        values = np.asarray([row[f"building_gap_morph_beta_{beta:.2f}"] for row in all_records], dtype=np.float64)
        global_sensitivity[f"{beta:.2f}"] = component_summary(values, all_baseline, all_defined)

    input_hashes = {}
    for region in REGIONS:
        inp = read_region_inputs(workspace, region, fs)
        input_hashes[region] = {
            "official_sample": {"rows": len(inp["ids"]), "sha256": sha256_file(inp["sample_path"]),
                                "read_column": "GEOID only"},
            "strata": {"rows": inp["strata_rows"], "sha256": sha256_file(inp["strata_path"]),
                       "read_columns": ["GEOID", "ALAND", "pop_total"]},
            "overture_feature_cache": {"rows": inp["feature_rows"], "sha256": sha256_file(inp["feature_path"]),
                                        "read_columns": ["GEOID", "overture_buildings"]},
            "acs_geometry": {"source_uri": f"s3://{inp['geometry_key']}",
                             "rows": len(inp["tract_geometries"]),
                             "read_columns": ["GEOID", "geometry"]},
        }

    report = {
        "analysis": "Provided-feature-only building morphology prior sensitivity; diagnostic candidate, not a submission",
        "candidate_id": CONFIG["candidate_id"],
        "candidate_csv": str(candidate_csv.relative_to(ROOT)),
        "candidate_csv_sha256": candidate_hash,
        "candidate_rows": len(all_records),
        "candidate_columns": fieldnames,
        "pre_registration": {
            "config": str(CONFIG_PATH.relative_to(ROOT)),
            "config_sha256": sha256_file(CONFIG_PATH),
            "commit": "a0201588f133bb1dc79871ccb0f292c1c822e56f",
            "config_status": CONFIG["status"],
        },
        "code_commit_at_run": current_commit(),
        "script_sha256_at_run": sha256_file(Path(__file__)),
        "scope": {
            "official_regions": list(REGIONS),
            "official_tracts": total_ids,
            "eastern_WA_template_rows_included": False,
            "ordered_ID_coverage": "exact concatenation of the four official sample GEOID lists",
        },
        "method": CONFIG,
        "inputs": input_hashes,
        "per_region": region_reports,
        "global_sensitivity_by_beta": global_sensitivity,
        "restrictions": {
            "score_placeholders_or_target_labels_read": False,
            "MS_USFS_CarbonPlan_or_other_reference_footprint_data_read": False,
            "ACS_housing_units_read_or_used_as_denominator": False,
            "external_imagery_or_footprint_data_used": False,
            "leaderboard_or_score_feedback_used": False,
            "upload_or_submission_made": False,
            "candidate_is_scored_or_promoted": False,
            "all_feature_coefficients_preregistered_before_morphology_scan": True,
            "accuracy_claim": False,
        },
        "limitations": [
            "Morphology is an unvalidated built-form support proxy, not an observed reference-building count or completeness label.",
            "Bbox-center assignment matches the existing Overture building-count feature; geometry area is projected to EPSG:5070 but may include parent/part overlap and source-level duplication.",
            "The fixed coefficients are sensitivity assumptions, not estimated from hidden targets or leaderboard feedback; this screen cannot establish accuracy.",
            "A zero Overture building count leaves this building component undefined, matching the existing v2 definedness convention; no absent-reference inference is made.",
        ],
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
                        encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--candidate-csv", type=Path, default=ROOT / "reports/candidates/building_morphology.csv")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/building-morphology-candidate.json")
    args = parser.parse_args()
    report = run(args.workspace.resolve(), args.candidate_csv.resolve(), args.out.resolve())
    print(f"PASS morphology candidate: {report['candidate_rows']} official tracts; "
          f"primary beta changed {report['global_sensitivity_by_beta']['0.02']['changed_vs_constant_baseline_tracts']} tracts")
    print(f"Candidate SHA-256: {report['candidate_csv_sha256']}")
    print(f"Report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
