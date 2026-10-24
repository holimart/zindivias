#!/usr/bin/env python3
"""Audit Overture POI confidence by provided SVI quartile and urbanity.

This is a descriptive data-quality analysis, not a scored candidate, label
reconstruction, or causal claim. It reads only official sample GEOIDs, provided
strata columns, Overture bbox/confidence columns, and provided ACS tract
geometry for point-to-tract assignment. No Overture geometry, other POI
properties, removed reference layers, or external data are read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from audit_poi_categories import (  # noqa: E402
    BUCKET,
    _bbox_centers,
    assign_points,
    load_sample_tracts,
    read_sample_geoids,
)

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
STRATA_COLUMNS = ["GEOID", "svi_overall", "pct_urban", "svi_covered"]
POI_COLUMNS = ["bbox", "confidence"]
BOOTSTRAP_REPLICATES = 10_000
BASE_SEED = 20261024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def seed_for(label: str) -> int:
    return BASE_SEED + int.from_bytes(hashlib.sha256(label.encode("utf-8")).digest()[:4], "big")


def finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def assign_quartiles(svi_by_id: dict[str, float | None]) -> tuple[dict[str, int | None], list[float]]:
    valid = [(geoid, value) for geoid, value in svi_by_id.items() if value is not None]
    if not valid:
        raise ValueError("region has no finite provided svi_overall values")
    cuts = np.quantile(np.asarray([value for _, value in valid]), [0.25, 0.5, 0.75])
    # Quantile cut ties are kept together and assigned to the higher bin.
    result = {
        geoid: (int(np.searchsorted(cuts, value, side="right") + 1) if value is not None else None)
        for geoid, value in svi_by_id.items()
    }
    return result, [float(value) for value in cuts]


def tract_summary_records(
    region: str,
    geoids: list[str],
    poi_idx_chunks: list[np.ndarray],
    confidence_chunks: list[np.ndarray],
    missing_chunks: list[np.ndarray],
    invalid_chunks: list[np.ndarray],
    svi_rows: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    n_tracts = len(geoids)
    if not poi_idx_chunks:
        raise ValueError(f"no POI records read for {region}")
    tract_idx = np.concatenate(poi_idx_chunks)
    confidence = np.concatenate(confidence_chunks)
    missing = np.concatenate(missing_chunks)
    invalid = np.concatenate(invalid_chunks)
    assigned = tract_idx >= 0
    assigned_tracts = tract_idx[assigned]
    poi_counts = np.bincount(assigned_tracts, minlength=n_tracts)
    assigned_conf = confidence[assigned]
    assigned_missing = missing[assigned]
    assigned_invalid = invalid[assigned]
    valid_conf = np.isfinite(assigned_conf) & (assigned_conf >= 0) & (assigned_conf <= 1) & ~assigned_missing
    valid_tract_idx = assigned_tracts[valid_conf]
    valid_values = assigned_conf[valid_conf]
    valid_counts = np.bincount(valid_tract_idx, minlength=n_tracts)
    low_mask = valid_values < 0.5
    low_counts = np.bincount(valid_tract_idx[low_mask], minlength=n_tracts)
    missing_counts = np.bincount(assigned_tracts[assigned_missing], minlength=n_tracts)
    invalid_counts = np.bincount(assigned_tracts[assigned_invalid], minlength=n_tracts)

    order = np.argsort(valid_tract_idx, kind="stable")
    sorted_tracts = valid_tract_idx[order]
    sorted_values = valid_values[order]
    quantiles: dict[int, list[float]] = {}
    if len(sorted_values):
        starts = np.searchsorted(sorted_tracts, np.arange(n_tracts), side="left")
        ends = np.searchsorted(sorted_tracts, np.arange(n_tracts), side="right")
        for index in np.flatnonzero(ends > starts):
            quantiles[int(index)] = [
                float(value) for value in np.quantile(
                    sorted_values[starts[index]:ends[index]], [0.1, 0.25, 0.5, 0.75, 0.9]
                )
            ]

    svi_by_id = {geoid: finite_float(svi_rows[geoid]["svi_overall"]) for geoid in geoids}
    urban_by_id = {geoid: finite_float(svi_rows[geoid]["pct_urban"]) for geoid in geoids}
    quartiles, cuts = assign_quartiles(svi_by_id)
    rows: list[dict[str, Any]] = []
    for index, geoid in enumerate(geoids):
        values = quantiles.get(index)
        rows.append({
            "region": region,
            "GEOID": geoid,
            "svi_overall": svi_by_id[geoid],
            "svi_quartile": quartiles[geoid],
            "pct_urban": urban_by_id[geoid],
            "urbanity": ("unknown" if urban_by_id[geoid] is None else
                         "urban" if urban_by_id[geoid] >= 0.5 else "rural"),
            "svi_covered": svi_rows[geoid]["svi_covered"],
            "poi_count": int(poi_counts[index]),
            "confidence_valid_count": int(valid_counts[index]),
            "confidence_missing_count": int(missing_counts[index]),
            "confidence_invalid_count": int(invalid_counts[index]),
            "confidence_below_0_5_count": int(low_counts[index]),
            "confidence_below_0_5_share": (float(low_counts[index] / valid_counts[index])
                                              if valid_counts[index] else None),
            "confidence_q10": values[0] if values else None,
            "confidence_q25": values[1] if values else None,
            "confidence_q50": values[2] if values else None,
            "confidence_q75": values[3] if values else None,
            "confidence_q90": values[4] if values else None,
        })
    summary = {
        "official_sample_tracts": n_tracts,
        "tracts_with_finite_svi_overall": int(sum(value is not None for value in svi_by_id.values())),
        "tracts_missing_svi_overall": int(sum(value is None for value in svi_by_id.values())),
        "tracts_missing_pct_urban": int(sum(value is None for value in urban_by_id.values())),
        "svi_quartile_cutpoints_q25_q50_q75": cuts,
        "svi_quartile_cut_rule": "region-specific quantiles among official sample GEOIDs with finite svi_overall; searchsorted(side=right), so exact ties at cutpoints remain together in the higher quartile",
        "svi_covered_false_or_missing": int(sum(not bool(svi_rows[g]["svi_covered"]) for g in geoids)),
        "assigned_poi_rows": int(assigned.sum()),
        "unmatched_poi_rows": int((~assigned).sum()),
        "tracts_with_no_assigned_pois": int(np.count_nonzero(poi_counts == 0)),
        "poi_rows_with_valid_confidence": int(valid_counts.sum()),
        "poi_rows_confidence_missing": int(missing_counts.sum()),
        "poi_rows_confidence_invalid": int(invalid_counts.sum()),
        "poi_rows_confidence_below_0_5": int(low_counts.sum()),
        "overall_confidence_quantiles_q10_q25_q50_q75_q90": {
            name: float(value) for name, value in zip(
                ("q10", "q25", "q50", "q75", "q90"),
                np.quantile(valid_values, [0.1, 0.25, 0.5, 0.75, 0.9]),
            )
        } if len(valid_values) else {name: None for name in ("q10", "q25", "q50", "q75", "q90")},
    }
    return rows, summary


def bootstrap_share(low: np.ndarray, total: np.ndarray, seed: int,
                    replicates: int = BOOTSTRAP_REPLICATES) -> np.ndarray:
    """Cluster-bootstrap pooled place share by resampling tracts with replacement."""
    if len(total) == 0 or int(total.sum()) == 0:
        return np.full(replicates, np.nan)
    rng = np.random.default_rng(seed)
    output = np.empty(replicates, dtype=np.float64)
    chunk_size = max(1, min(256, replicates))
    for start in range(0, replicates, chunk_size):
        stop = min(replicates, start + chunk_size)
        indices = rng.integers(0, len(total), size=(stop - start, len(total)), dtype=np.int32)
        numerator = low[indices].sum(axis=1)
        denominator = total[indices].sum(axis=1)
        output[start:stop] = np.divide(
            numerator, denominator,
            out=np.full(stop - start, np.nan, dtype=np.float64),
            where=denominator > 0,
        )
    return output


def share_summary(rows: list[dict[str, Any]], region: str) -> dict[str, Any]:
    strata: dict[str, Any] = {}
    filtered = [row for row in rows if row["svi_quartile"] is not None and row["urbanity"] in {"urban", "rural"}]
    for urbanity in ("urban", "rural"):
        strata[urbanity] = {}
        for quartile in (1, 2, 3, 4):
            group = [row for row in filtered if row["urbanity"] == urbanity and row["svi_quartile"] == quartile]
            low = np.asarray([row["confidence_below_0_5_count"] for row in group], dtype=np.int64)
            total = np.asarray([row["confidence_valid_count"] for row in group], dtype=np.int64)
            share = float(low.sum() / total.sum()) if total.sum() else None
            boot = bootstrap_share(low, total, seed_for(f"{region}:{urbanity}:q{quartile}:share"))
            ci = (np.quantile(boot[np.isfinite(boot)], [0.025, 0.975]).tolist()
                  if np.isfinite(boot).any() else [None, None])
            tract_shares = [row["confidence_below_0_5_share"] for row in group
                            if row["confidence_below_0_5_share"] is not None]
            strata[urbanity][f"q{quartile}"] = {
                "tract_count": len(group),
                "tracts_with_pois": int(sum(row["poi_count"] > 0 for row in group)),
                "tracts_without_pois": int(sum(row["poi_count"] == 0 for row in group)),
                "poi_rows": int(sum(row["poi_count"] for row in group)),
                "valid_confidence_places": int(total.sum()),
                "low_confidence_places": int(low.sum()),
                "low_confidence_place_share": share,
                "cluster_bootstrap_95_ci": [float(x) if x is not None else None for x in ci],
                "mean_tract_low_confidence_share": (float(np.mean(tract_shares)) if tract_shares else None),
                "bootstrap_replicates": BOOTSTRAP_REPLICATES,
                "bootstrap_seed": seed_for(f"{region}:{urbanity}:q{quartile}:share"),
            }

    contrasts: dict[str, Any] = {}
    for urbanity in ("urban", "rural"):
        q1 = [row for row in filtered if row["urbanity"] == urbanity and row["svi_quartile"] == 1]
        q4 = [row for row in filtered if row["urbanity"] == urbanity and row["svi_quartile"] == 4]
        q1_low = np.asarray([row["confidence_below_0_5_count"] for row in q1], dtype=np.int64)
        q1_total = np.asarray([row["confidence_valid_count"] for row in q1], dtype=np.int64)
        q4_low = np.asarray([row["confidence_below_0_5_count"] for row in q4], dtype=np.int64)
        q4_total = np.asarray([row["confidence_valid_count"] for row in q4], dtype=np.int64)
        if len(q1) and len(q4) and q1_total.sum() and q4_total.sum():
            q1_share = float(q1_low.sum() / q1_total.sum())
            q4_share = float(q4_low.sum() / q4_total.sum())
            q1_boot = bootstrap_share(q1_low, q1_total, seed_for(f"{region}:{urbanity}:q1:contrast"))
            q4_boot = bootstrap_share(q4_low, q4_total, seed_for(f"{region}:{urbanity}:q4:contrast"))
            diff = q4_boot - q1_boot
            ci = np.quantile(diff[np.isfinite(diff)], [0.025, 0.975]).tolist()
            contrasts[urbanity] = {
                "q1_share": q1_share,
                "q4_share": q4_share,
                "q4_minus_q1_share_difference": q4_share - q1_share,
                "cluster_bootstrap_95_ci_difference": [float(x) for x in ci],
                "q1_tracts": len(q1),
                "q4_tracts": len(q4),
                "bootstrap_replicates": BOOTSTRAP_REPLICATES,
                "ci_excludes_zero": bool(ci[0] > 0 or ci[1] < 0),
            }
        else:
            contrasts[urbanity] = {"reason": "one or both quartile groups have no valid-confidence places"}
    return {
        "method": "pooled place-weighted share; 95% percentile cluster bootstrap resamples official tracts within each region × urbanity × SVI-quartile stratum",
        "urban_rural_rule": "rural if provided pct_urban < 0.5; urban if pct_urban >= 0.5; missing/nonfinite urbanity excluded",
        "low_confidence_rule": "provided Overture confidence < 0.5; denominator is finite confidence in [0,1]",
        "by_urbanity_and_svi_quartile": strata,
        "high_vs_low_svi_quartile_contrasts_within_urbanity": contrasts,
    }


def _column_compressed_bytes(metadata, names: set[str]) -> dict[str, int]:
    totals = {name: 0 for name in names}
    for rg in range(metadata.num_row_groups):
        group = metadata.row_group(rg)
        for col in range(metadata.num_columns):
            path = metadata.schema.column(col).path.split(".")[0]
            if path in totals:
                totals[path] += group.column(col).total_compressed_size
    return totals


def process_region(fs, workspace: Path, region: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
    sample_ids, geoid_digest = read_sample_geoids(sample_path)
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError(f"duplicate official sample GEOID in {region}")
    strata_path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
    if not strata_path.is_file():
        raise FileNotFoundError(strata_path)
    strata_table = pq.read_table(strata_path, columns=STRATA_COLUMNS)
    strata_records = strata_table.to_pylist()
    strata_all = {str(row["GEOID"]): row for row in strata_records}
    if len(strata_all) != len(strata_records):
        raise ValueError(f"duplicate strata GEOIDs for {region}")
    missing_ids = set(sample_ids) - set(strata_all)
    if missing_ids:
        raise ValueError(f"{region}: {len(missing_ids)} official sample GEOIDs missing from strata")
    strata_rows = {geoid: strata_all[geoid] for geoid in sample_ids}
    _, _geoms, tree, tract_input = load_sample_tracts(fs, region, sample_ids)

    poi_key = f"{BUCKET}/reference/{region}/{region}-overture-pois.parquet"
    poi_info = fs.get_file_info(poi_key)
    if poi_info.type != pafs.FileType.File:
        raise FileNotFoundError(f"provided POI object missing: s3://{poi_key}")
    parquet = pq.ParquetFile(fs.open_input_file(poi_key))
    missing_columns = set(POI_COLUMNS) - set(parquet.schema_arrow.names)
    if missing_columns:
        raise ValueError(f"{region}: missing Overture columns {sorted(missing_columns)}")
    column_bytes = _column_compressed_bytes(parquet.metadata, set(POI_COLUMNS))
    tract_idx_chunks: list[np.ndarray] = []
    confidence_chunks: list[np.ndarray] = []
    missing_chunks: list[np.ndarray] = []
    invalid_chunks: list[np.ndarray] = []
    poi_rows = unmatched_rows = missing_conf = invalid_conf = low_conf = 0
    for row_group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(row_group, columns=POI_COLUMNS)
        poi_rows += table.num_rows
        x, y = _bbox_centers(table)
        tract_idx = assign_points(tree, x, y)
        confidence_array = table.column("confidence").combine_chunks()
        null_mask = pc.is_null(confidence_array).to_numpy(zero_copy_only=False)
        confidence_values = confidence_array.fill_null(float("nan")).to_numpy(zero_copy_only=False)
        confidence_values = np.asarray(confidence_values, dtype=np.float64)
        valid = (~null_mask & np.isfinite(confidence_values) &
                 (confidence_values >= 0) & (confidence_values <= 1))
        invalid = ~null_mask & ~valid
        low = valid & (confidence_values < 0.5)
        unmatched_rows += int(np.count_nonzero(tract_idx < 0))
        assigned = tract_idx >= 0
        missing_conf += int(np.count_nonzero(assigned & null_mask))
        invalid_conf += int(np.count_nonzero(assigned & invalid))
        low_conf += int(np.count_nonzero(assigned & low))
        tract_idx_chunks.append(tract_idx)
        confidence_chunks.append(confidence_values)
        missing_chunks.append(null_mask)
        invalid_chunks.append(invalid)
    rows, stats = tract_summary_records(region, sample_ids, tract_idx_chunks,
                                        confidence_chunks, missing_chunks, invalid_chunks,
                                        strata_rows)
    if stats["unmatched_poi_rows"] != unmatched_rows or stats["poi_rows_confidence_missing"] != missing_conf:
        raise AssertionError("per-tract confidence aggregation does not reconcile with streamed totals")
    stats["poi_rows_confidence_invalid"] = invalid_conf
    stats["poi_rows_confidence_below_0_5"] = low_conf
    stats["poi_source"] = {
        "uri": f"s3://{poi_key}",
        "object_bytes": poi_info.size,
        "row_groups": parquet.metadata.num_row_groups,
        "input_rows": poi_rows,
        "assigned_columns_read": POI_COLUMNS,
        "column_compressed_bytes": column_bytes,
        "selected_column_compressed_bytes_total": int(sum(column_bytes.values())),
        "overture_geometry_read": False,
        "full_object_downloaded": False,
    }
    stats["sample_input"] = {
        "path": str(sample_path),
        "rows": len(sample_ids),
        "geoid_sequence_sha256": geoid_digest,
        "score_placeholder_values_used": False,
    }
    stats["strata_input"] = {
        "path": str(strata_path),
        "sha256": sha256_file(strata_path),
        "bytes": strata_path.stat().st_size,
        "read_columns": STRATA_COLUMNS,
        "source_rows": len(strata_records),
        "official_ids_not_in_source": 0,
    }
    stats["tract_geometry_input"] = tract_input
    return rows, stats


def make_arrow_table(rows: list[dict[str, Any]]) -> pa.Table:
    schema = pa.schema([
        ("region", pa.string()), ("GEOID", pa.string()),
        ("svi_overall", pa.float64()), ("svi_quartile", pa.int8()),
        ("pct_urban", pa.float64()), ("urbanity", pa.string()),
        ("svi_covered", pa.bool_()), ("poi_count", pa.int32()),
        ("confidence_valid_count", pa.int32()), ("confidence_missing_count", pa.int32()),
        ("confidence_invalid_count", pa.int32()), ("confidence_below_0_5_count", pa.int32()),
        ("confidence_below_0_5_share", pa.float64()),
        ("confidence_q10", pa.float64()), ("confidence_q25", pa.float64()),
        ("confidence_q50", pa.float64()), ("confidence_q75", pa.float64()),
        ("confidence_q90", pa.float64()),
    ])
    return pa.Table.from_pylist(rows, schema=schema)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"])
                        if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--cache", type=Path, default=None,
                        help="per-tract Parquet cache path (default: workspace/gap-estimator/features/...) ")
    parser.add_argument("--out", type=Path, default=Path("reports/confidence-by-stratum.json"))
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    workspace = args.workspace.resolve()
    cache_path = (args.cache.resolve() if args.cache else
                  workspace / "gap-estimator/features/poi-confidence-by-tract.parquet")
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    all_rows: list[dict[str, Any]] = []
    region_stats: dict[str, Any] = {}
    for region in REGIONS:
        rows, stats = process_region(fs, workspace, region)
        all_rows.extend(rows)
        stats["strata_analysis"] = share_summary(rows, region)
        region_stats[region] = stats
    if len(all_rows) != 9379 or len({row["GEOID"] for row in all_rows}) != 9379:
        raise ValueError(f"expected 9,379 unique official tract cache rows, got {len(all_rows)}")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = make_arrow_table(all_rows)
    pq.write_table(cache, cache_path, compression="zstd", version="2.6",
                   use_dictionary=["region", "GEOID", "urbanity"], row_group_size=8192)
    report = {
        "analysis": "provided Overture POI confidence<0.5 share by regional svi_overall quartile × urban/rural",
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                        "pyarrow": pa.__version__, "shapely": shapely.__version__},
        "interpretation": "descriptive data-quality screen only; not a hidden-target accuracy, causal, or stale-source finding",
        "constraints": {
            "external_data_used": [],
            "removed_reference_layers_read": False,
            "organizer_targets_or_sample_scores_used": False,
            "overture_geometry_read": False,
            "poi_columns_read": POI_COLUMNS,
            "strata_columns_read": STRATA_COLUMNS,
            "tract_geometry": "provided ACS tract geometry used solely to assign Overture bbox centers to official sample GEOIDs",
        },
        "definitions": {
            "svi_field": "provided strata.svi_overall",
            "svi_quartiles": "computed within each region over official sample tracts with finite svi_overall; quartile cut ties stay together in the higher bin; q4 is higher SVI",
            "urban_rural": "provided pct_urban < 0.5 is rural; >= 0.5 is urban; missing/nonfinite excluded from the cross-tab",
            "low_confidence": "provided Overture confidence < 0.5; denominator finite confidence in [0,1]",
            "primary_estimate": "pooled low-confidence place count / valid-confidence place count within each region × urbanity × SVI-quartile cell",
            "intervals": "95% percentile cluster-bootstrap intervals: resample official tracts with replacement within the same cell, then pool low/valid place counts; 10,000 replicates per cell, fixed SHA-derived seeds",
            "vulnerability_contrast": "q4 minus q1 pooled low-confidence share within each urban/rural group and region; independent tract resampling in each quartile",
        },
        "uncertainty_and_caveats": [
            "Bootstrap intervals reflect tract-resampling variability only; they do not adjust for spatial dependence or the finite, geographically clustered design.",
            "The table is descriptive; stratum differences do not show that SVI causes lower confidence or that confidence indicates data staleness.",
            "Overture confidence is treated only as the provided field; no source-dataset attribution was read or inferred.",
            "Tracts with no assigned POIs remain in the cache and are counted in tract denominators, but contribute zero places to pooled shares.",
            "Confidence missing/invalid counts are reported; rows outside the official sample are excluded from strata estimates.",
        ],
        "feature_cache": {
            "path": str(cache_path),
            "sha256": sha256_file(cache_path),
            "bytes": cache_path.stat().st_size,
            "rows": cache.num_rows,
            "columns": cache.column_names,
            "row_order": "region order in script, then authoritative sample GEOID order",
        },
        "regions": region_stats,
    }
    out = args.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"PASS confidence-by-stratum: {len(all_rows)} tract cache rows; "
          f"report {out}; cache {cache_path}")


if __name__ == "__main__":
    main()
