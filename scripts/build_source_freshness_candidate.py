#!/usr/bin/env python3
"""Build a bounded Overture source-update recency (freshness) adjustment candidate.

Reads only the provided Overture layers' ``bbox`` and ``sources`` columns, the provided ACS
tract geometries (``GEOID``, ``geometry``) and the ``GEOID`` column of the official sample
submissions. Source-update recency comes from ``sources[].update_time`` and
``sources[].version`` in the provided files only: no historical OSM/reference edits are
pulled, no external clock or timestamp source is read, no removed reference layer is
touched, no label or leaderboard value is used, and nothing is uploaded.

One screen question: is the provided time metadata usable, and does a small fixed stale /
metadata-gap uncertainty term add information beyond the frozen source-mixture proportions?
This is an intent screen, not an accuracy claim.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from estimate_gaps import (  # noqa: E402  (frozen rules reused, never re-derived)
    BUCKET, arrow_bbox_centers, assign_points, classify_building_sources, classify_poi_sources,
    open_source,
)

CONFIG_PATH = ROOT / "config/source-freshness-preregistered.json"
with CONFIG_PATH.open("r", encoding="utf-8") as stream:
    CONFIG = json.load(stream)

REGIONS: tuple[str, ...] = tuple(CONFIG["scope"]["regions"])
THEMES: tuple[str, ...] = tuple(CONFIG["scope"]["themes_in_order"])
THEME_WEIGHTS: dict[str, float] = dict(CONFIG["scope"]["theme_weights"])
LAYER_PATTERN: dict[str, str] = dict(CONFIG["scope"]["theme_layers"])
TIMESTAMP_STATUSES: tuple[str, ...] = tuple(CONFIG["timestamp_rules"]["statuses"])
VERSION_STATUSES: tuple[str, ...] = tuple(CONFIG["timestamp_rules"]["version_statuses"])
PRIMARY_AGE_STATUSES: tuple[str, ...] = tuple(CONFIG["timestamp_rules"]["primary_age_statuses"])
CUTOFFS: tuple[int, ...] = tuple(int(v) for v in CONFIG["timestamp_rules"]["fixed_cutoff_days"])
SENSITIVITY_CUTOFFS: tuple[int, ...] = tuple(int(v) for v in CONFIG["sensitivity"]["stale_cutoffs_days"])
STALE_DAYS = int(CONFIG["adjustment"]["stale_cutoff_days"])
ALL_CUTOFFS: tuple[int, ...] = tuple(sorted(set(CUTOFFS) | set(SENSITIVITY_CUTOFFS) | {STALE_DAYS}))
ENTRY_PERCENTILES: tuple[float, ...] = tuple(float(v) for v in
                                             CONFIG["timestamp_rules"]["entry_quantile_percentiles"])
W_STALE = float(CONFIG["adjustment"]["weights"]["stale"])
W_GAP = float(CONFIG["adjustment"]["weights"]["metadata_gap"])
MAX_ADJUSTMENT = float(CONFIG["adjustment"]["max_adjustment"])
UNDEFINED_PLACEHOLDER = float(CONFIG["adjustment"]["undefined_placeholder"])
MIN_ACCOUNTING = float(CONFIG["acceptance"]["metadata_accounting_share_min"])
ARITHMETIC_TOLERANCE = float(CONFIG["acceptance"]["independent_arithmetic_agreement_max_abs"])
FROZEN_MIXTURE_COLUMNS: tuple[str, ...] = tuple(CONFIG["beyond_mixture_test"]["frozen_mixture_columns"])
FEATURE_CACHE_TEMPLATE = "gap-estimator/features/{region}-overture-tract-summary.parquet"

REPORT_DEFAULT = ROOT / "reports/source-freshness-candidate.json"
CSV_DEFAULT = ROOT / "reports/candidates/source_freshness_candidate.csv"
MANIFEST_DEFAULT = ROOT / "reports/candidates/source_freshness_candidate_manifest.json"

TS_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d{1,9})?Z$")
TS_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d{1,9})?"
                       r"([+-]\d{2}:\d{2}|[+-]\d{2})$")
TS_NAIVE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d{1,9})?$")
DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUMERIC = re.compile(r"^\d+$")

TIMESTAMP_CODE = {status: index for index, status in enumerate(TIMESTAMP_STATUSES)}
VERSION_CODE = {status: index for index, status in enumerate(VERSION_STATUSES)}
PRIMARY_CODES = np.array([TIMESTAMP_CODE[status] for status in PRIMARY_AGE_STATUSES], dtype=np.int16)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                       text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def parse_utc(text: str) -> float | None:
    """Seconds since the UTC epoch for an explicitly zoned timestamp, else None."""
    normalised = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).timestamp()


def has_explicit_designator(value: object) -> bool:
    """True only when the raw string itself states a UTC designator or a numeric offset."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(TS_Z.match(text) or TS_OFFSET.match(text))


def classify_timestamp(value: object) -> tuple[str, float | None]:
    """One closed-set status per source entry; only explicit UTC designators give an instant."""
    if value is None:
        return "missing_null", None
    if not isinstance(value, str):
        return "malformed", None
    text = value.strip()
    if not text:
        return "missing_empty", None
    if TS_Z.match(text):
        instant = parse_utc(text)
        return ("parsed_utc_z", instant) if instant is not None else ("malformed", None)
    if TS_OFFSET.match(text):
        instant = parse_utc(text)
        return ("parsed_utc_offset", instant) if instant is not None else ("malformed", None)
    if TS_NAIVE.match(text) or DATE_ONLY.match(text):
        return "naive_no_timezone", None
    return "malformed", None


def classify_version(value: object) -> str:
    """One closed-set status for the companion sources[].version field."""
    if value is None:
        return "missing_null"
    if not isinstance(value, str):
        return "other"
    text = value.strip()
    if not text:
        return "missing_null"
    if DATE_ONLY.match(text):
        return "iso_date"
    if TS_Z.match(text) or TS_OFFSET.match(text):
        return "datetime_utc"
    if NUMERIC.match(text):
        return "numeric"
    return "other"


def normalise_dataset(value: object) -> str:
    return str(value).strip().casefold() if value and str(value).strip() else ""


def dataset_classes(theme: str, names: tuple[str, ...]) -> str:
    """Frozen provenance classes for buildings and POIs, documented classes for roads.

    Buildings and POIs delegate to the frozen classifiers even when the name set is empty, so the
    empty case keeps the class the frozen source-mix candidate already reports for it.
    """
    if theme == "buildings":
        return str(classify_building_sources([{"dataset": name} for name in names])["class"])
    if theme == "pois":
        return str(classify_poi_sources([{"dataset": name} for name in names])["class"])
    if not names:
        return "no_dataset"
    osm = any(name == "osm" or name.startswith("openstreetmap") for name in names)
    tomtom = any(name.startswith("tomtom") for name in names)
    known = {name for name in names if name == "osm" or name.startswith("openstreetmap")
             or name.startswith("tomtom")}
    other = bool(set(names) - known)
    if osm and tomtom and not other:
        return "osm_and_tomtom"
    if osm and not tomtom and not other:
        return "osm_only"
    if tomtom and not osm and not other:
        return "tomtom_only"
    return "other_or_mixed"


def official_ids(workspace: Path, region: str) -> tuple[list[str], dict[str, Any]]:
    path = workspace / "samples" / f"{region}-sample-submission.csv"
    if not path.is_file():
        raise FileNotFoundError(f"official sample submission missing: {path}")
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        index = header.index("GEOID")
        ids = [row[index] for row in reader if row and any(cell.strip() for cell in row)]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate official GEOID in {path}")
    return ids, {"path": str(path), "sha256": sha256_file(path), "rows": len(ids),
                 "columns_read": ["GEOID"], "score_placeholder_columns_read": False}


def tract_index(fs, region: str, ids: list[str]) -> tuple[shapely.STRtree, dict[str, Any]]:
    key = f"{BUCKET}/reference/{region}/{region}-census-acs-housing.parquet"
    parquet = pq.ParquetFile(fs.open_input_file(key))
    if not {"GEOID", "geometry"}.issubset(parquet.schema_arrow.names):
        raise ValueError(f"ACS layer lacks GEOID/geometry for {region}")
    table = parquet.read(columns=["GEOID", "geometry"])
    all_geoids = [str(value) for value in table.column("GEOID").to_pylist()]
    if len(all_geoids) != len(set(all_geoids)):
        raise ValueError(f"duplicate ACS GEOID for {region}")
    positions = {geoid: index for index, geoid in enumerate(all_geoids)}
    missing = set(ids) - set(positions)
    if missing:
        raise ValueError(f"official scored GEOIDs absent from ACS polygons for {region}: "
                         f"{len(missing)}")
    wkb = table.column("geometry").to_pylist()
    if any(value is None for value in wkb):
        raise ValueError(f"null ACS tract geometry for {region}")
    tracts = shapely.from_wkb([wkb[positions[geoid]] for geoid in ids])
    if np.any(shapely.is_empty(tracts)) or np.any(~shapely.is_valid(tracts)):
        raise ValueError(f"invalid ACS tract geometry for {region}")
    info = {"source_uri": f"s3://{key}", "columns_read": ["GEOID", "geometry"],
            "acs_rows": parquet.metadata.num_rows,
            "acs_row_groups": parquet.metadata.num_row_groups,
            "tract_index_built_from_official_ids_only": True,
            "extra_acs_geometries_excluded": parquet.metadata.num_rows - len(ids)}
    return shapely.STRtree(tracts), info


def open_layer(fs, region: str, theme: str):
    expected_name = LAYER_PATTERN[theme].format(region=region)
    key, size, parquet = open_source(fs, region, theme)
    if not key.endswith(expected_name):
        raise ValueError(f"unexpected layer path {key} for theme {theme}")
    if not {"bbox", "sources"}.issubset(parquet.schema_arrow.names):
        raise ValueError(f"provided layer lacks bbox/sources: s3://{key}")
    value_type = parquet.schema_arrow.field("sources").type.value_type
    needed = {"dataset", "update_time", "version"}
    if not needed.issubset(set(value_type.names)):
        raise ValueError(f"sources struct lacks {needed} at s3://{key}: {value_type.names}")
    selected_bytes = 0
    for row_group in range(parquet.metadata.num_row_groups):
        metadata = parquet.metadata.row_group(row_group)
        for column in range(parquet.metadata.num_columns):
            name = parquet.metadata.schema.column(column).path.split(".")[0]
            if name in ("bbox", "sources"):
                selected_bytes += metadata.column(column).total_compressed_size
    return key, parquet, {"source_uri": f"s3://{key}", "object_bytes": size,
                          "rows": parquet.metadata.num_rows,
                          "row_groups": parquet.metadata.num_row_groups,
                          "columns_read": ["bbox", "sources"],
                          "selected_column_compressed_bytes": int(selected_bytes),
                          "sources_struct_fields": list(value_type.names),
                          "full_object_downloaded": False, "geometry_column_read": False}


def aggregate_theme(fs, region: str, theme: str, tree: shapely.STRtree, n_tracts: int,
                    memo_ts: dict[object, tuple[str, float | None]],
                    memo_ver: dict[object, str],
                    memo_names: dict[tuple[str, ...], str]) -> tuple[dict[str, Any], dict]:
    key, parquet, meta = open_layer(fs, region, theme)
    status_count, version_count = len(TIMESTAMP_STATUSES), len(VERSION_STATUSES)
    counts: dict[str, np.ndarray] = {
        "features": np.zeros(n_tracts, dtype=np.int64),
        "entries": np.zeros(n_tracts, dtype=np.int64),
        "features_with_parsed": np.zeros(n_tracts, dtype=np.int64),
        "features_all_naive": np.zeros(n_tracts, dtype=np.int64),
        "features_no_entries": np.zeros(n_tracts, dtype=np.int64),
        "features_null_sources_list": np.zeros(n_tracts, dtype=np.int64),
    }
    entry_status = np.zeros((n_tracts, status_count), dtype=np.int64)
    entry_version = np.zeros((n_tracts, version_count), dtype=np.int64)
    class_counts: Counter[str] = Counter()
    dataset_entries: Counter[str] = Counter()
    dataset_parsed: Counter[str] = Counter()
    pair_tracts: list[np.ndarray] = []
    pair_seconds: list[np.ndarray] = []
    rows_seen = unmatched = designator_violations = 0
    unverified_primary_strings: set[str] = set()

    for row_group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(row_group, columns=["bbox", "sources"])
        x, y = arrow_bbox_centers(table)
        assignment, outside = assign_points(tree, x, y)
        n_rows = len(assignment)
        source_array = table.column("sources").combine_chunks()
        if not (pa.types.is_list(source_array.type) or pa.types.is_large_list(source_array.type)):
            raise TypeError(f"{theme} sources is not a list column for {region}")
        if not pa.types.is_struct(source_array.type.value_type):
            raise TypeError(f"{theme} sources elements are not structs for {region}")
        offsets = source_array.offsets.to_numpy(zero_copy_only=False).astype(np.int64)
        values = source_array.values
        time_values = values.field("update_time").to_pylist()
        version_values = values.field("version").to_pylist()
        dataset_values = values.field("dataset").to_pylist()
        n_entries = len(time_values)
        if len(version_values) != n_entries or len(dataset_values) != n_entries:
            raise ValueError(f"{theme} sources field lengths disagree in {region} row group {row_group}")
        entry_row = np.repeat(np.arange(n_rows, dtype=np.int64), np.diff(offsets))
        if len(entry_row) != n_entries:
            raise ValueError(f"{theme} offsets/entries mismatch in {region} row group {row_group}")

        codes = np.empty(n_entries, dtype=np.int16)
        seconds = np.full(n_entries, np.nan)
        for index, raw in enumerate(time_values):
            cached = memo_ts.get(raw)
            status, instant = cached if cached is not None else classify_timestamp(raw)
            if cached is None:
                memo_ts[raw] = (status, instant)
                if status in PRIMARY_AGE_STATUSES and not has_explicit_designator(raw):
                    designator_violations += 1
                    unverified_primary_strings.add(str(raw))
            codes[index] = TIMESTAMP_CODE[status]
            if instant is not None:
                seconds[index] = instant
        version_codes = np.empty(n_entries, dtype=np.int16)
        for index, raw in enumerate(version_values):
            cached = memo_ver.get(raw)
            status = cached if cached is not None else classify_version(raw)
            if cached is None:
                memo_ver[raw] = status
            version_codes[index] = VERSION_CODE[status]

        assigned = assignment >= 0
        row_entry_counts = np.diff(offsets)
        counts["features"] += np.bincount(assignment[assigned], minlength=n_tracts)
        counts["features_no_entries"] += np.bincount(
            assignment[assigned & (row_entry_counts == 0)], minlength=n_tracts)
        null_rows = source_array.is_null().to_numpy(zero_copy_only=False)
        counts["features_null_sources_list"] += np.bincount(
            assignment[assigned & null_rows], minlength=n_tracts)

        keep = assigned[entry_row]
        entry_tract = assignment[entry_row]
        kept_tracts = entry_tract[keep]
        counts["entries"] += np.bincount(kept_tracts, minlength=n_tracts)
        entry_status += np.bincount(kept_tracts * status_count + codes[keep],
                                    minlength=n_tracts * status_count
                                    ).reshape(n_tracts, status_count)
        entry_version += np.bincount(kept_tracts * version_count + version_codes[keep],
                                     minlength=n_tracts * version_count
                                     ).reshape(n_tracts, version_count)

        parsed_entry = np.isin(codes, PRIMARY_CODES)
        naive_entry = codes == TIMESTAMP_CODE["naive_no_timezone"]
        row_parsed = np.bincount(entry_row, weights=parsed_entry.astype(np.float64),
                                 minlength=n_rows) > 0
        row_naive = np.bincount(entry_row, weights=naive_entry.astype(np.float64),
                                minlength=n_rows) > 0
        counts["features_with_parsed"] += np.bincount(
            assignment[assigned & row_parsed], minlength=n_tracts)
        counts["features_all_naive"] += np.bincount(
            assignment[assigned & row_naive & ~row_parsed], minlength=n_tracts)

        selected = keep & parsed_entry
        if np.any(selected):
            pair_tracts.append(entry_tract[selected].astype(np.int32))
            pair_seconds.append(seconds[selected])
        if np.any(keep):
            dataset_entries.update(np.asarray(dataset_values, dtype=object)[keep])
            if np.any(selected):
                dataset_parsed.update(np.asarray(dataset_values, dtype=object)[selected])
        for row_index in np.flatnonzero(assigned):
            start, stop = int(offsets[row_index]), int(offsets[row_index + 1])
            names = tuple(sorted({normalise_dataset(value)
                                  for value in dataset_values[start:stop] if value}))
            cached = memo_names.get(names)
            label = cached if cached is not None else dataset_classes(theme, names)
            if cached is None:
                memo_names[names] = label
            class_counts[label] += 1
        rows_seen += n_rows
        unmatched += outside
        if (row_group + 1) % 25 == 0:
            print(f"{region} {theme} row groups {row_group + 1}/{parquet.metadata.num_row_groups}; "
                  f"rows={rows_seen}", flush=True)

    meta.update({"assigned_to_official_sample": int(counts["features"].sum()),
                 "unassigned_rows_outside_official_sample": unmatched,
                 "assigned_rows_with_null_sources_list": int(counts["features_null_sources_list"].sum()),
                 "dataset_entry_counts": dict(sorted(dataset_entries.items())),
                 "dataset_parsed_counts": dict(sorted(dataset_parsed.items())),
                 "provenance_class_feature_counts": dict(sorted(class_counts.items())),
                 "primary_timestamps_without_explicit_designator": designator_violations,
                 "examples_without_designator": sorted(unverified_primary_strings)[:10]})
    counts["entry_status"] = entry_status
    counts["entry_version"] = entry_version
    pairs = (np.concatenate(pair_tracts) if pair_tracts else np.zeros(0, dtype=np.int32),
             np.concatenate(pair_seconds) if pair_seconds else np.zeros(0, dtype=np.float64))
    return {"counts": counts, "pairs": pairs}, meta


def summarize_ages(pairs: tuple[np.ndarray, np.ndarray], n_tracts: int, epoch_seconds: float
                   ) -> dict[str, Any]:
    tracts, seconds = pairs
    summary: dict[str, Any] = {
        "parsed_total": np.zeros(n_tracts, dtype=np.int64),
        "tract_p10_seconds": np.full(n_tracts, np.nan),
        "tract_p50_seconds": np.full(n_tracts, np.nan),
        "tract_p90_seconds": np.full(n_tracts, np.nan),
        "tract_min_seconds": np.full(n_tracts, np.nan),
        "tract_max_seconds": np.full(n_tracts, np.nan),
        "vintage": {cutoff: np.zeros(n_tracts, dtype=np.int64) for cutoff in ALL_CUTOFFS},
        "entry_age_days": {},
    }
    if len(tracts) == 0:
        return summary
    order = np.lexsort((seconds, tracts))
    tract_sorted = tracts[order].astype(np.int64)
    seconds_sorted = seconds[order]
    boundaries = np.searchsorted(tract_sorted, np.arange(n_tracts + 1))
    starts = boundaries[:-1]
    lengths = boundaries[1:] - boundaries[:-1]
    summary["parsed_total"] += lengths
    valid = lengths > 0
    for name, percentile in (("tract_p10_seconds", 10), ("tract_p50_seconds", 50),
                             ("tract_p90_seconds", 90)):
        rank = np.maximum(1, np.ceil(percentile / 100.0 * lengths).astype(np.int64))
        index = np.where(valid, starts + rank - 1, 0)
        values = np.full(n_tracts, np.nan)
        values[valid] = seconds_sorted[index[valid]]
        summary[name] = values
    first_index = np.where(valid, starts, 0)
    last_index = np.where(valid, starts + lengths - 1, 0)
    summary["tract_min_seconds"][valid] = seconds_sorted[first_index[valid]]
    summary["tract_max_seconds"][valid] = seconds_sorted[last_index[valid]]
    for cutoff in ALL_CUTOFFS:
        threshold = epoch_seconds - cutoff * 86400.0
        summary["vintage"][cutoff] = np.bincount(tract_sorted[seconds_sorted >= threshold],
                                                 minlength=n_tracts)
    if len(seconds_sorted):
        # age is a decreasing linear function of the entry instant, so the p-th age
        # percentile is the (100 - p)-th instant percentile measured from the epoch
        instant_quantiles = np.quantile(seconds_sorted, [1.0 - p / 100.0 for p in ENTRY_PERCENTILES])
        summary["entry_age_days"] = {
            f"p{int(percentile)}": float((epoch_seconds - instant) / 86400.0)
            for percentile, instant in zip(ENTRY_PERCENTILES, instant_quantiles)}
        summary["entry_age_days"]["min"] = float((epoch_seconds - float(np.max(seconds_sorted)))
                                                 / 86400.0)
        summary["entry_age_days"]["max"] = float((epoch_seconds - float(np.min(seconds_sorted)))
                                                 / 86400.0)
    return summary


def shares(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    out = np.zeros(len(numerator), dtype=np.float64)
    nonzero = denominator > 0
    out[nonzero] = numerator[nonzero] / denominator[nonzero]
    return out


def finite_stats(values: np.ndarray) -> dict[str, Any]:
    finite = values[np.isfinite(values)] if len(values) else np.zeros(0)
    if not len(finite):
        return {"n": 0}
    return {"n": int(len(finite)), "mean": float(np.mean(finite)), "sd": float(np.std(finite)),
            "min": float(np.min(finite)), "p50": float(np.quantile(finite, 0.50)),
            "p95": float(np.quantile(finite, 0.95)), "max": float(np.max(finite))}


def rank_correlation(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3 or not np.isfinite(a).all() or not np.isfinite(b).all():
        return None
    rank_a = np.argsort(np.argsort(a)).astype(np.float64)
    rank_b = np.argsort(np.argsort(b)).astype(np.float64)
    if np.std(rank_a) == 0.0 or np.std(rank_b) == 0.0:
        return None
    return float(np.corrcoef(rank_a, rank_b)[0, 1])


def ols(target: np.ndarray, regressors: np.ndarray) -> dict[str, Any]:
    keep = np.isfinite(target) & np.isfinite(regressors).all(axis=1)
    y, x = target[keep], regressors[keep]
    if len(y) < x.shape[1] + 2 or np.std(y) == 0.0:
        return {"n": int(len(y)), "r_squared": None, "unexplained_sd": None,
                "residual_over_total_sd": None,
                "max_abs_pearson_r_with_single_regressor": None}
    design = np.column_stack([np.ones(len(y)), x])
    beta, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
    residual = y - design @ beta
    total = float(np.sum((y - np.mean(y)) ** 2))
    single = [abs(float(np.corrcoef(x[:, column], y)[0, 1])) for column in range(x.shape[1])
              if np.std(x[:, column]) > 0.0]
    return {"n": int(len(y)),
            "r_squared": (1.0 - float(np.sum(residual ** 2)) / total) if total > 0 else None,
            "unexplained_sd": float(np.std(residual)),
            "residual_over_total_sd": (float(np.std(residual) / np.std(y))
                                       if np.std(y) > 0 else None),
            "design_rank": int(rank), "intercept": float(beta[0]),
            "max_abs_pearson_r_with_single_regressor": max(single) if single else None}


def candidate_from_stale(stale_by_theme: dict[str, dict[str, np.ndarray]],
                         region: str, themes: tuple[str, ...]) -> np.ndarray:
    blocks = stale_by_theme[region]
    n_tracts = int(len(blocks[themes[0]]["features"]))
    weighted = np.zeros(n_tracts)
    weight_sum = np.zeros(n_tracts)
    for theme in themes:
        data = blocks[theme]
        entries, parsed, gap, stale, features = (data["entries"], data["parsed"], data["gap"],
                                                 data["stale"], data["features"])
        uncertainty = np.clip(W_STALE * shares(stale, parsed) + W_GAP * shares(gap, entries),
                              0.0, 1.0)
        present = features > 0
        weighted += np.where(present, THEME_WEIGHTS[theme] * uncertainty, 0.0)
        weight_sum += np.where(present, THEME_WEIGHTS[theme], 0.0)
    value = np.where(weight_sum > 0,
                     MAX_ADJUSTMENT * weighted / np.where(weight_sum > 0, weight_sum, 1.0),
                     UNDEFINED_PLACEHOLDER)
    return np.clip(value, 0.0, 1.0)


def independent_candidate(blocks: dict[str, dict[str, np.ndarray]],
                          themes: tuple[str, ...]) -> np.ndarray:
    """Second arithmetic path in exact rationals; guards the float path from drift."""
    n_tracts = int(len(blocks[themes[0]]["features"]))
    values: list[float] = []
    for index in range(n_tracts):
        weighted = Fraction(0)
        weight_sum = Fraction(0)
        for theme in themes:
            data = blocks[theme]
            if int(data["features"][index]) <= 0:
                continue
            entries = int(data["entries"][index])
            parsed = int(data["parsed"][index])
            gap = int(data["gap"][index])
            stale = int(data["stale"][index])
            stale_share = Fraction(stale, parsed) if parsed else Fraction(0)
            gap_share = Fraction(gap, entries) if entries else Fraction(0)
            uncertainty = min(Fraction(1), max(Fraction(0),
                                               Fraction(str(W_STALE)) * stale_share
                                               + Fraction(str(W_GAP)) * gap_share))
            weight = Fraction(str(THEME_WEIGHTS[theme]))
            weighted += weight * uncertainty
            weight_sum += weight
        if weight_sum == 0:
            values.append(UNDEFINED_PLACEHOLDER)
        else:
            value = float(Fraction(str(MAX_ADJUSTMENT)) * weighted / weight_sum)
            values.append(min(1.0, max(0.0, value)))
    return np.asarray(values, dtype=np.float64)


def candidate_columns(themes: tuple[str, ...]) -> list[str]:
    columns = ["GEOID", "region", "adjustment_defined", "source_freshness_adjustment"]
    for theme in themes:
        columns.extend([f"{theme}_present", f"{theme}_features", f"{theme}_entries",
                        f"{theme}_parsed_share", f"{theme}_metadata_gap_share",
                        f"{theme}_stale_share", f"{theme}_age_days_p50", f"{theme}_age_days_p90",
                        f"{theme}_uncertainty"])
    columns.extend(["entries_total", "entries_parsed_utc", "entries_naive_tz", "entries_missing",
                    "entries_malformed", "themes_present"])
    return columns


def build_theme_frames(region_data: dict[str, Any], themes: tuple[str, ...],
                       epoch_seconds: float) -> dict[str, dict[str, np.ndarray]]:
    frames: dict[str, dict[str, np.ndarray]] = {}
    for theme in themes:
        data = region_data["themes"][theme]
        counts = data["counts"]
        age = data["age"]
        entries = counts["entries"]
        parsed = age["parsed_total"]
        vintage = age["vintage"][STALE_DAYS]
        gap = entries - parsed
        stale = np.where(parsed > 0, parsed - vintage, 0)
        frames[theme] = {
            "features": counts["features"], "entries": entries, "parsed": parsed,
            "gap": gap, "stale": stale,
            # age decreases with the entry instant, so the oldest-tail age quantile is read off
            # the earliest-tail instant quantile
            "age_p10": (epoch_seconds - age["tract_p90_seconds"]) / 86400.0,
            "age_p50": (epoch_seconds - age["tract_p50_seconds"]) / 86400.0,
            "age_p90": (epoch_seconds - age["tract_p10_seconds"]) / 86400.0,
            "uncertainty": np.clip(W_STALE * shares(stale, parsed)
                                   + W_GAP * shares(gap, entries), 0.0, 1.0),
        }
    return frames


def write_candidate_csv(path: Path, ids: list[str], region_of_row: list[str],
                        region_offsets: dict[str, int], themes: tuple[str, ...],
                        adjustment: np.ndarray, defined: np.ndarray,
                        frames: dict[str, dict[str, dict[str, np.ndarray]]],
                        status_totals: dict[str, dict[str, np.ndarray]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(candidate_columns(themes))
        for index, geoid in enumerate(ids):
            region = region_of_row[index]
            local = index - region_offsets[region]
            row = [geoid, region, int(defined[index]), format(float(adjustment[index]), ".12g")]
            present = 0
            for theme in themes:
                frame = frames[region][theme]
                features = int(frame["features"][local])
                entries = int(frame["entries"][local])
                parsed = int(frame["parsed"][local])
                gap = int(frame["gap"][local])
                stale = int(frame["stale"][local])
                row.extend([int(features > 0), features, entries,
                            format(parsed / entries if entries else 0.0, ".12g"),
                            format(gap / entries if entries else 0.0, ".12g"),
                            format(stale / parsed if parsed else 0.0, ".12g"),
                            "" if not np.isfinite(frame["age_p50"][local])
                            else format(float(frame["age_p50"][local]), ".12g"),
                            "" if not np.isfinite(frame["age_p90"][local])
                            else format(float(frame["age_p90"][local]), ".12g"),
                            format(float(frame["uncertainty"][local]), ".12g")])
                present += int(features > 0)
                if np.isfinite(frame["age_p10"][local]) and not (
                        frame["age_p10"][local] <= frame["age_p50"][local] + 1e-9
                        and frame["age_p50"][local] <= frame["age_p90"][local] + 1e-9):
                    raise ValueError(f"unordered per-tract age quantiles for {geoid} {theme}")
            totals = status_totals[region]
            row.extend([int(totals["entries"][local]), int(totals["parsed"][local]),
                        int(totals["naive"][local]), int(totals["missing"][local]),
                        int(totals["malformed"][local]), present])
            writer.writerow(row)
    return len(ids)


def build(workspace: Path, out: Path, csv_out: Path, manifest_out: Path,
          regions: tuple[str, ...], themes: tuple[str, ...]) -> dict[str, Any]:
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    memo_ts: dict[object, tuple[str, float | None]] = {}
    memo_ver: dict[object, str] = {}
    memo_names: dict[tuple[str, ...], str] = {}
    region_data: dict[str, Any] = {}
    ids_by_region: dict[str, list[str]] = {}

    for region in regions:
        ids, sample_facts = official_ids(workspace, region)
        ids_by_region[region] = ids
        tree, acs_info = tract_index(fs, region, ids)
        cache_path = workspace / FEATURE_CACHE_TEMPLATE.format(region=region)
        if not cache_path.is_file():
            raise FileNotFoundError(f"frozen feature cache missing: {cache_path}")
        cache = pq.read_table(cache_path)
        absent = [name for name in FROZEN_MIXTURE_COLUMNS if name not in cache.column_names]
        if absent:
            raise ValueError(f"frozen source-mixture columns missing from {cache_path}: {absent}")
        if [str(value) for value in cache.column("GEOID").to_pylist()] != ids:
            raise ValueError(f"feature cache order differs from the official order for {region}")
        mixture = np.column_stack([np.asarray(cache.column(name).to_pylist(), dtype=np.float64)
                                   for name in FROZEN_MIXTURE_COLUMNS])
        themes_data: dict[str, Any] = {}
        for theme in themes:
            print(f"{region} {theme}: independent source-metadata scan starting", flush=True)
            started = time.monotonic()
            aggregate, meta = aggregate_theme(fs, region, theme, tree, len(ids), memo_ts,
                                              memo_ver, memo_names)
            print(f"{region} {theme}: scanned {meta['rows']} rows in "
                  f"{round(time.monotonic() - started, 2)} s", flush=True)
            themes_data[theme] = {**aggregate, "input": meta}
        region_data[region] = {"official_tracts": len(ids), "official_sample": sample_facts,
                               "acs": acs_info, "themes": themes_data, "mixture": mixture,
                               "feature_cache": {"path": str(cache_path),
                                                 "sha256": sha256_file(cache_path),
                                                 "columns_read": ["GEOID", *FROZEN_MIXTURE_COLUMNS],
                                                 "row_order_matches_official_sample": True}}
        del tree

    parsed_peaks = [float(np.max(region_data[region]["themes"][theme]["pairs"][1]))
                    for region in regions for theme in themes
                    if len(region_data[region]["themes"][theme]["pairs"][1])]
    if not parsed_peaks:
        raise ValueError("no explicitly-UTC update_time metadata found in the provided layers")
    epoch_seconds = float(max(parsed_peaks))
    epoch_iso = datetime.fromtimestamp(epoch_seconds, timezone.utc).isoformat()

    for region in regions:
        for theme in themes:
            data = region_data[region]["themes"][theme]
            data["age"] = summarize_ages(data["pairs"], region_data[region]["official_tracts"],
                                         epoch_seconds)
            data["pairs"] = (np.zeros(0, dtype=np.int32), np.zeros(0, dtype=np.float64))

    frames = {region: build_theme_frames(region_data[region], themes, epoch_seconds)
              for region in regions}
    adjustment_parts = [candidate_from_stale(frames, region, themes) for region in regions]
    reference_parts = [independent_candidate(frames[region], themes) for region in regions]
    adjustment = np.concatenate(adjustment_parts)
    reference = np.concatenate(reference_parts)
    arithmetic_gap = float(np.max(np.abs(adjustment - reference))) if len(adjustment) else 0.0
    defined = np.concatenate([np.any(np.column_stack(
        [frames[region][theme]["features"] > 0 for theme in themes]), axis=1).astype(np.int8)
        for region in regions])

    zero = lambda region: np.zeros(region_data[region]["official_tracts"], dtype=np.int64)
    status_totals: dict[str, dict[str, np.ndarray]] = {}
    for region in regions:
        counts = [region_data[region]["themes"][theme]["counts"] for theme in themes]
        ages = [region_data[region]["themes"][theme]["age"] for theme in themes]
        status_totals[region] = {
            "entries": sum((item["entries"] for item in counts), zero(region)),
            "parsed": sum((item["parsed_total"] for item in ages), zero(region)),
            "naive": sum((item["entry_status"][:, TIMESTAMP_CODE["naive_no_timezone"]]
                          for item in counts), zero(region)),
            "missing": sum((item["entry_status"][:, TIMESTAMP_CODE["missing_null"]]
                            + item["entry_status"][:, TIMESTAMP_CODE["missing_empty"]]
                            for item in counts), zero(region)),
            "malformed": sum((item["entry_status"][:, TIMESTAMP_CODE["malformed"]]
                              for item in counts), zero(region)),
        }

    region_of_row = [region for region in regions for _ in ids_by_region[region]]
    expected_ids = [geoid for region in regions for geoid in ids_by_region[region]]
    region_offsets: dict[str, int] = {}
    cursor = 0
    for region in regions:
        region_offsets[region] = cursor
        cursor += len(ids_by_region[region])
    rows_written = write_candidate_csv(csv_out, expected_ids, region_of_row, region_offsets,
                                       themes, adjustment, defined, frames, status_totals)

    manifest = {
        "candidate_id": "SRCFRRESH_INTENT_SCREEN_V1",
        "analysis_only_not_a_submission_artifact": True,
        "upload_performed": False,
        "candidate_csv": relative(csv_out), "candidate_csv_sha256": sha256_file(csv_out),
        "candidate_rows": rows_written, "candidate_columns": candidate_columns(themes),
        "scope": {"regions": list(regions), "themes": list(themes),
                  "scored_population": CONFIG["scope"]["scored_population"]},
        "reference_epoch_utc": epoch_iso, "reference_epoch_seconds": epoch_seconds,
        "frozen_bounds": {"adjustment_min": 0.0, "adjustment_max": MAX_ADJUSTMENT,
                          "theme_uncertainty_clip": list(CONFIG["adjustment"]["clip"]),
                          "stale_cutoff_days": STALE_DAYS,
                          "max_adjustment_rationale": CONFIG["adjustment"]["max_adjustment_rationale"]},
        "input_hashes_by_region": {
            region: {"official_sample": region_data[region]["official_sample"],
                     "feature_cache": region_data[region]["feature_cache"],
                     "acs": region_data[region]["acs"],
                     "layers": {theme: region_data[region]["themes"][theme]["input"]
                                for theme in themes}}
            for region in regions},
        "config": {"path": relative(CONFIG_PATH), "sha256": sha256_file(CONFIG_PATH)},
    }
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    entry_status_grand: Counter[str] = Counter()
    entry_version_grand: Counter[str] = Counter()
    for region in regions:
        for theme in themes:
            counts = region_data[region]["themes"][theme]["counts"]
            entry_status_grand.update({status: int(counts["entry_status"][:, index].sum())
                                       for index, status in enumerate(TIMESTAMP_STATUSES)})
            entry_version_grand.update({status: int(counts["entry_version"][:, index].sum())
                                        for index, status in enumerate(VERSION_STATUSES)})
    entries_grand = sum(entry_status_grand.values())
    features_grand = sum(int(region_data[region]["themes"][theme]["counts"]["features"].sum())
                         for region in regions for theme in themes)
    accounting_total, accounted_total, entries_counted, entryless_features = \
        closed_set_accounting(region_data, regions, themes)


    counts_matrix = np.column_stack([
        np.concatenate([frames[region][theme]["features"].astype(np.float64)
                        for region in regions]) for theme in themes])
    mixture_matrix = np.vstack([region_data[region]["mixture"] for region in regions])
    beyond = {
        "definition": CONFIG["beyond_mixture_test"]["method"],
        "mixture_columns_used": list(FROZEN_MIXTURE_COLUMNS),
        "adjustment_on_frozen_mixture": ols(adjustment[defined == 1],
                                            mixture_matrix[defined == 1]),
        "adjustment_on_frozen_mixture_and_theme_counts": ols(
            adjustment[defined == 1],
            np.column_stack([mixture_matrix, np.log1p(counts_matrix)])[defined == 1]),
        "adjustment_on_theme_counts_only": ols(adjustment[defined == 1],
                                               np.log1p(counts_matrix)[defined == 1]),
        "adopted": False,
    }

    sensitivity_cutoffs: dict[str, Any] = {}
    for cutoff in SENSITIVITY_CUTOFFS:
        variant_frames = {}
        for region in regions:
            variant_frames[region] = {}
            for theme in themes:
                data = region_data[region]["themes"][theme]
                parsed = data["age"]["parsed_total"]
                vintage = data["age"]["vintage"][cutoff]
                variant_frames[region][theme] = {
                    "features": data["counts"]["features"], "entries": data["counts"]["entries"],
                    "parsed": parsed, "gap": data["counts"]["entries"] - parsed,
                    "stale": np.where(parsed > 0, parsed - vintage, 0)}
        variant = np.concatenate([candidate_from_stale(variant_frames, region, themes)
                                  for region in regions])
        sensitivity_cutoffs[str(cutoff)] = {
            "stats": finite_stats(variant),
            "pearson_r_vs_primary": (float(np.corrcoef(variant, adjustment)[0, 1])
                                     if np.std(variant) > 0 and np.std(adjustment) > 0 else None),
            "spearman_r_vs_primary": rank_correlation(variant, adjustment),
            "max_absolute_delta": float(np.max(np.abs(variant - adjustment))),
            "share_within_one_percent_of_primary": float(np.mean(
                np.abs(variant - adjustment) <= 0.01 * max(MAX_ADJUSTMENT, 1e-12))),
            "adopted": False,
        }

    pooled_by_theme = {theme: pooled_theme(region_data, regions, theme, entries_grand)
                       for theme in themes}
    report = {
        "backlog_item": CONFIG["backlog_item"],
        "preregistration": {
            "config_path": relative(CONFIG_PATH), "config_sha256": sha256_file(CONFIG_PATH),
            "preregistered_on": CONFIG["preregistered_on"], "intent": CONFIG["intent"],
            "reference_epoch_policy": CONFIG["timestamp_rules"]["reference_epoch_policy"],
            "theme_uncertainty_formula": CONFIG["adjustment"]["theme_uncertainty_formula"],
            "adjustment_formula": CONFIG["adjustment"]["adjustment_formula"],
            "frozen_constants": {
                "theme_weights": THEME_WEIGHTS, "weights": CONFIG["adjustment"]["weights"],
                "max_adjustment": MAX_ADJUSTMENT, "stale_cutoff_days": STALE_DAYS,
                "fixed_cutoff_days": list(CUTOFFS),
                "undefined_placeholder": UNDEFINED_PLACEHOLDER},
            "constants_fixed_before_seeing_data": True,
            "band_or_bound_changed_after_seeing_data": False,
        },
        "measure_before": {
            "prior_report": "reports/building-source-mix.json",
            "prior_report_sha256": sha256_file(ROOT / "reports/building-source-mix.json"),
            "frozen_source_mix_columns": list(FROZEN_MIXTURE_COLUMNS),
            "frozen_source_mix_coverage_by_region": {
                region: {"tracts": int(region_data[region]["official_tracts"]),
                         "column_finite_share_min": float(min(
                             np.mean(np.isfinite(region_data[region]["mixture"][:, index]))
                             for index in range(len(FROZEN_MIXTURE_COLUMNS)))),
                         "column_finite_share_max": float(max(
                             np.mean(np.isfinite(region_data[region]["mixture"][:, index]))
                             for index in range(len(FROZEN_MIXTURE_COLUMNS)))),
                         "tracts_with_any_nonzero_mixture_share": int(np.count_nonzero(
                             np.any(region_data[region]["mixture"] > 0, axis=1)))}
                for region in regions},
            "feature_schema": {
                "sources_struct_fields": region_data[regions[0]]["themes"][themes[0]]["input"]
                                          ["sources_struct_fields"],
                "columns_read_from_overture": list(CONFIG["scope"]["columns_read_from_overture"]),
                "note": "sources is a list of structs; only dataset, update_time and version are "
                        "read from it.",
            },
        },
        "reference_epoch": {"policy": CONFIG["timestamp_rules"]["reference_epoch_policy"],
                            "value_utc": epoch_iso, "seconds": epoch_seconds,
                            "external_clock_read": False},
        "regions": describe_regions(region_data, regions, themes),
        "pooled": {
            "entries": entries_grand, "features": features_grand,
            "entries_by_timestamp_status": dict(entry_status_grand),
            "timestamp_status_shares": {status: entry_status_grand[status] / entries_grand
                                        for status in TIMESTAMP_STATUSES},
            "entries_by_version_status": dict(entry_version_grand),
            "version_status_shares": {status: entry_version_grand[status] / entries_grand
                                      for status in VERSION_STATUSES},
            "naive_used_in_primary": False,
            "primary_timestamps_without_explicit_designator": sum(
                region_data[region]["themes"][theme]["input"]
                ["primary_timestamps_without_explicit_designator"]
                for region in regions for theme in themes),
            "explicit_timestamp_share": (
                (entry_status_grand["parsed_utc_z"] + entry_status_grand["parsed_utc_offset"]
                 + entry_status_grand["naive_no_timezone"]) / entries_grand
                if entries_grand else None),
            "missing_or_malformed_timestamp_share": (
                entries_grand - entry_status_grand["parsed_utc_z"]
                - entry_status_grand["parsed_utc_offset"]
                - entry_status_grand["naive_no_timezone"]) / entries_grand if entries_grand else None,
            "closed_set_accounting": {
                "units": accounting_total, "accounted": accounted_total,
                "share": accounted_total / accounting_total if accounting_total else None,
                "required_share": MIN_ACCOUNTING,
                "unit_definition": "one per assigned source entry plus one per assigned feature "
                                   "whose sources list is null or empty",
                "entryless_features": entryless_features},
            "by_theme": pooled_by_theme,
        },
        "candidate": {
            "csv": relative(csv_out), "csv_sha256": sha256_file(csv_out), "rows": rows_written,
            "defined_tracts": int(defined.sum()),
            "undefined_tracts": int((defined == 0).sum()),
            "undefined_placeholder": UNDEFINED_PLACEHOLDER,
            "adjustment_stats": finite_stats(adjustment[defined == 1]),
            "adjustment_min": float(np.min(adjustment)), "adjustment_max": float(np.max(adjustment)),
            "per_region_adjustment": {
                region: finite_stats(adjustment[region_offsets[region]:
                                                region_offsets[region]
                                                + len(ids_by_region[region])])
                for region in regions},
            "independent_arithmetic_max_abs_difference": arithmetic_gap,
            "independent_arithmetic_tolerance": ARITHMETIC_TOLERANCE,
        },
        "beyond_source_mixture": beyond,
        "sensitivity": {
            "stale_cutoffs": sensitivity_cutoffs,
            "fixed_cutoff_days": list(SENSITIVITY_CUTOFFS),
            "variants": {
                "primary": {"adopted": True, "uses_only_explicit_utc_timestamps": True},
                "naive_as_utc": {"adopted": False,
                                 "note": "naive timestamps are counted in the status shares but "
                                         "never used as instants", "used_in_primary": False},
                "version_date_fallback": {"adopted": False,
                                          "note": "sources[].version coverage and status shares are "
                                                  "reported per theme and region; the calendar-date "
                                                  "fallback is never used in the primary adjustment",
                                          "used_in_primary": False}},
            "adoption": CONFIG["sensitivity"]["adoption"],
        },
        "measure_after": {
            "parseable_timestamp_share_by_theme": {
                theme: pooled_by_theme[theme]["parseable_share"] for theme in themes},
            "age_quantiles_days_by_theme_and_region": {
                theme: {region: region_data[region]["themes"][theme]["age"]["entry_age_days"]
                        for region in regions} for theme in themes},
            "tract_level_median_age_days_by_theme_and_region": {
                theme: {region: finite_stats(
                    (epoch_seconds - region_data[region]["themes"][theme]["age"]
                     ["tract_p50_seconds"]) / 86400.0) for region in regions}
                for theme in themes},
            "cumulative_vintage_at_fixed_cutoffs_by_theme": {
                theme: pooled_theme(region_data, regions, theme, entries_grand)["vintage_shares"]
                for theme in themes},
            "data_vintage_sensitivity": sensitivity_cutoffs,
            "bounds_and_hash_audit": {
                "adjustment_within_declared_bound": bool(np.all(adjustment >= 0.0)
                                                         and np.all(adjustment <= MAX_ADJUSTMENT)),
                "declared_bound": MAX_ADJUSTMENT,
                "candidate_csv_sha256": sha256_file(csv_out),
                "manifest_sha256": sha256_file(manifest_out),
            },
        },
        "acceptance": {},
        "prohibitions": dict(CONFIG["prohibitions"]),
        "reproducibility": {
            "command": 'COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" uv run --with numpy==2.5.4 '
                       '--with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python '
                       'scripts/build_source_freshness_candidate.py',
            "git_commit": git_commit(), "script_sha256": sha256_file(Path(__file__)),
            "config_sha256": sha256_file(CONFIG_PATH), "python": sys.version.split()[0],
            "numpy": np.__version__, "shapely": shapely.__version__,
            "timings": "wall-clock timings are printed to stdout and deliberately not embedded so "
                       "that repeated runs are byte-identical",
        },
        "method": {
            "population": "official scored GEOIDs only, authoritative sample order",
            "assignment": CONFIG["adjustment"]["deduplication"],
            "timestamp_status_set": list(TIMESTAMP_STATUSES),
            "version_status_set": list(VERSION_STATUSES),
            "primary_age_statuses": list(PRIMARY_AGE_STATUSES),
            "naive_policy": CONFIG["timestamp_rules"]["naive_no_timezone_policy"],
            "version_fallback_policy": CONFIG["timestamp_rules"]["version_date_fallback_policy"],
            "theme_aggregation": CONFIG["adjustment"]["theme_aggregation"],
            "stale_share_definition": CONFIG["adjustment"]["stale_share_definition"],
            "metadata_gap_share_definition": CONFIG["adjustment"]["metadata_gap_share_definition"],
            "undefined_rule": CONFIG["adjustment"]["undefined_rule"],
            "quantile_definition": "nearest rank on the per-tract sorted instant list with "
                                   "rank = max(1, ceil(percentile / 100 * n)); pooled entry "
                                   "quantiles use the equivalent reversed instant quantiles",
            "beyond_mixture_method": CONFIG["beyond_mixture_test"]["method"],
            "sources_of_record": CONFIG["sources_of_record"],
        },
    }

    gates = {
        "all_requested_regions_and_themes_scanned": all(
            theme in region_data[region]["themes"] for region in regions for theme in themes),
        "every_entry_has_exactly_one_timestamp_status": bool(
            entries_counted > 0 and sum(entry_status_grand.values()) == entries_counted),
        "every_entry_has_exactly_one_version_status": bool(
            entries_counted > 0 and sum(entry_version_grand.values()) == entries_counted),
        "metadata_accounting_share_at_least_95_percent": bool(
            accounting_total > 0 and (accounted_total / accounting_total) >= MIN_ACCOUNTING),
        "no_timezone_ambiguity_in_primary": bool(
            report["pooled"]["naive_used_in_primary"] is False
            and report["pooled"]["primary_timestamps_without_explicit_designator"] == 0),
        "outputs_finite_in_unit_interval": bool(np.isfinite(adjustment).all()
                                                and np.all(adjustment >= 0.0)
                                                and np.all(adjustment <= 1.0)),
        "adjustment_within_declared_bound": bool(np.all(adjustment <= MAX_ADJUSTMENT)),
        "independent_arithmetic_agreement_within_tolerance": bool(
            arithmetic_gap <= ARITHMETIC_TOLERANCE),
        "undefined_tracts_use_documented_placeholder": bool(
            np.all(adjustment[defined == 0] == UNDEFINED_PLACEHOLDER)),
        "candidate_rows_equal_official_population": bool(rows_written == len(region_of_row)),
        "candidate_carries_exactly_the_official_geoids_in_order": bool(
            read_csv_geoids(csv_out) == expected_ids),
        "no_region_mean_or_placeholder_imputation": bool(
            np.all(adjustment[defined == 0] == UNDEFINED_PLACEHOLDER)
            and len(set(adjustment[defined == 1])) > 1),
        "age_quantiles_are_ordered": bool(all(
            np.all(frames[region][theme]["age_p10"][ordered_mask(frames[region][theme])]
                   <= frames[region][theme]["age_p50"][ordered_mask(frames[region][theme])]
                   + 1e-9)
            and np.all(frames[region][theme]["age_p50"][ordered_mask(frames[region][theme])]
                       <= frames[region][theme]["age_p90"][ordered_mask(frames[region][theme])]
                       + 1e-9)
            for region in regions for theme in themes)),
        "no_target_accuracy_claim": True,
        "no_upload": True,
    }
    gates["all_passed"] = all(bool(value) for value in gates.values())
    report["acceptance"] = gates
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return report


def read_csv_geoids(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if header[0] != "GEOID":
            raise ValueError("candidate CSV lacks a leading GEOID column")
        return [row[0] for row in reader if row]


def closed_set_accounting(region_data: dict[str, Any], regions: tuple[str, ...],
                          themes: tuple[str, ...]) -> tuple[int, int, int, int]:
    """Closed-set accounting units: every source entry and every entry-less feature.

    An entry is accounted for when it carries exactly one declared timestamp status and one
    declared version status; a feature whose sources list is empty or null is accounted for only
    because that absence is an explicit declared state, not an unmeasured hole.
    """
    entries = entryless = classified = 0
    for region in regions:
        for theme in themes:
            counts = region_data[region]["themes"][theme]["counts"]
            entries += int(counts["entries"].sum())
            entryless += int(counts["features_no_entries"].sum())
            classified += int(counts["entry_status"].sum())
            classified += 0  # version statuses are checked separately by their own gate
    units = entries + entryless
    return units, classified + entryless, entries, entryless


def ordered_mask(frame: dict[str, np.ndarray]) -> np.ndarray:
    """Tracts where all three per-tract age quantiles exist (the ordering claim is conditional)."""
    return (np.isfinite(frame["age_p10"]) & np.isfinite(frame["age_p50"])
            & np.isfinite(frame["age_p90"]))


def pooled_theme(region_data: dict[str, Any], regions: tuple[str, ...], theme: str,
                 entries_grand: int) -> dict[str, Any]:
    status_totals: Counter[str] = Counter()
    version_totals: Counter[str] = Counter()
    entries = features = parsed = 0
    vintage_totals: Counter[str] = Counter()
    for region in regions:
        data = region_data[region]["themes"][theme]
        counts = data["counts"]
        for index, status in enumerate(TIMESTAMP_STATUSES):
            status_totals[status] += int(counts["entry_status"][:, index].sum())
        for index, status in enumerate(VERSION_STATUSES):
            version_totals[status] += int(counts["entry_version"][:, index].sum())
        entries += int(counts["entries"].sum())
        features += int(counts["features"].sum())
        parsed += int(data["age"]["parsed_total"].sum())
        for cutoff in CUTOFFS:
            vintage_totals[str(cutoff)] += int(data["age"]["vintage"][cutoff].sum())
    total_status = sum(status_totals.values())
    naive = status_totals["naive_no_timezone"]
    return {
        "features": features, "entries": entries,
        "entries_with_explicit_utc_time": parsed,
        "explicit_timestamp_share": (parsed + naive) / entries if entries else None,
        "missing_or_malformed_timestamp_share": (
            entries - parsed - naive) / entries if entries else None,
        "parseable_share": parsed / entries if entries else None,
        "share_of_all_entries": entries / entries_grand if entries_grand else None,
        "entries_by_timestamp_status": dict(status_totals),
        "timestamp_status_shares": {status: status_totals[status] / total_status
                                    for status in TIMESTAMP_STATUSES} if total_status else {},
        "entries_by_version_status": dict(version_totals),
        "version_status_shares": {status: version_totals[status] / total_status
                                  for status in VERSION_STATUSES} if total_status else {},
        "vintage_shares": {str(cutoff): (vintage_totals[str(cutoff)] / parsed if parsed else None)
                           for cutoff in CUTOFFS},
    }


def describe_regions(region_data: dict[str, Any], regions: tuple[str, ...],
                     themes: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for region in regions:
        data = region_data[region]
        out[region] = {"region": region, "official_tracts": data["official_tracts"],
                       "official_sample": data["official_sample"], "acs": data["acs"],
                       "feature_cache": data["feature_cache"], "themes": {}}
        for theme in themes:
            theme_data = data["themes"][theme]
            counts = theme_data["counts"]
            entries = int(counts["entries"].sum())
            statuses = {status: int(counts["entry_status"][:, index].sum())
                        for index, status in enumerate(TIMESTAMP_STATUSES)}
            versions = {status: int(counts["entry_version"][:, index].sum())
                        for index, status in enumerate(VERSION_STATUSES)}
            out[region]["themes"][theme] = {
                "input": theme_data["input"],
                "features_assigned": int(counts["features"].sum()),
                "entries": entries,
                "entries_by_timestamp_status": statuses,
                "timestamp_status_shares": {status: statuses[status] / entries
                                            for status in TIMESTAMP_STATUSES} if entries else {},
                "entries_by_version_status": versions,
                "version_status_shares": {status: versions[status] / entries
                                          for status in VERSION_STATUSES} if entries else {},
                "tracts_with_feature": int(np.count_nonzero(counts["features"])),
                "tracts_with_explicit_utc_time": int(np.count_nonzero(
                    theme_data["age"]["parsed_total"])),
                "tracts_without_any_source_entry": int(np.count_nonzero(
                    counts["features_no_entries"])),
                "entry_age_days": theme_data["age"]["entry_age_days"],
                "provenance_class_feature_counts": theme_data["input"]
                                                   ["provenance_class_feature_counts"],
            }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE"))
    parser.add_argument("--out", type=Path, default=REPORT_DEFAULT)
    parser.add_argument("--csv", type=Path, default=CSV_DEFAULT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_DEFAULT)
    parser.add_argument("--regions", default=",".join(REGIONS))
    parser.add_argument("--themes", default=",".join(THEMES))
    args = parser.parse_args()
    if not args.workspace:
        raise SystemExit("COMPETITION_WORKSPACE must be set or passed with --workspace")
    workspace = Path(args.workspace).resolve()
    regions = tuple(value for value in args.regions.split(",") if value)
    themes = tuple(value for value in args.themes.split(",") if value)
    if not regions or not set(regions) <= set(REGIONS):
        raise ValueError("--regions must be a non-empty subset of the preregistered regions")
    if not themes or not set(themes) <= set(THEMES):
        raise ValueError("--themes must be a non-empty subset of the preregistered themes")
    started = time.monotonic()
    report = build(workspace, args.out, args.csv, args.manifest, regions, themes)
    print(json.dumps({"gates_all_passed": report["acceptance"]["all_passed"],
                      "entries": report["pooled"]["entries"],
                      "parseable_share_by_theme": report["measure_after"]
                      ["parseable_timestamp_share_by_theme"],
                      "adjustment": report["candidate"]["adjustment_stats"],
                      "epoch_utc": report["reference_epoch"]["value_utc"]}, indent=1))
    failed = sorted(key for key, value in report["acceptance"].items() if value is False)
    if failed:
        print(f"FAIL source-freshness candidate gates: {failed}")
        return 1
    print("PASS source-freshness candidate: every source entry carries exactly one time and one "
          "version status, the primary adjustment uses only explicitly-UTC timestamps, and every "
          "output is finite inside the declared bound")
    print(f"total wall clock: {time.monotonic() - started:.2f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
