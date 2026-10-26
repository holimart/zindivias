#!/usr/bin/env python3
"""Measure a region-median fallback for the residual undefined pooled-Mx5 rows."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import official_scored_pairs  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
MIN_REGION_PEERS = 30
MIN_FINAL_COVERAGE = 0.999
MAX_ADDITIONAL_IMPUTATION_FRACTION = 0.007


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def distribution(values: np.ndarray, defined: np.ndarray) -> dict[str, float | int | None]:
    x = np.asarray(values, dtype=np.float64)[np.asarray(defined, dtype=bool)]
    if not x.size:
        return {"n": 0, "min": None, "p50": None, "p95": None, "max": None, "mean": None}
    if not np.isfinite(x).all() or np.any((x < 0.0) | (x > 1.0)):
        raise ValueError("defined Mx5 values must be finite and in [0,1]")
    return {"n": int(x.size), "min": float(x.min()), "p50": float(np.quantile(x, .5)),
            "p95": float(np.quantile(x, .95)), "max": float(x.max()), "mean": float(x.mean())}


def apply_region_median_fallback(
    geoids: list[str], regions: list[str], observed_values: np.ndarray,
    observed_defined: np.ndarray, county_values: np.ndarray, county_defined: np.ndarray,
    minimum_peers: int = MIN_REGION_PEERS,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Fill county-undefined rows from >=minimum_peers other original observed region values.

    Only `observed_defined` values are donors: county-imputed values do not become donors.
    Observed and county-imputed values are preserved exactly, and ineligible rows stay null.
    """
    n = len(geoids)
    obs = np.asarray(observed_values, dtype=np.float64)
    obs_mask = np.asarray(observed_defined, dtype=bool)
    county = np.asarray(county_values, dtype=np.float64)
    county_mask = np.asarray(county_defined, dtype=bool)
    if len(regions) != n or any(a.shape != (n,) for a in (obs, obs_mask, county, county_mask)):
        raise ValueError("GEOID/region/value/defined inputs must align")
    if len(set(geoids)) != n:
        raise ValueError("duplicate official GEOID")
    if minimum_peers < 1:
        raise ValueError("minimum_peers must be positive")
    if any(region not in REGIONS for region in regions):
        raise ValueError("unknown or missing named study region")
    if np.any(obs_mask & (~np.isfinite(obs) | (obs < 0.0) | (obs > 1.0))):
        raise ValueError("observed values must be finite and in [0,1]")
    if np.any(~obs_mask & np.isfinite(obs)):
        raise ValueError("undefined observed values must remain null")
    if np.any(county_mask & (~np.isfinite(county) | (county < 0.0) | (county > 1.0))):
        raise ValueError("defined county values must be finite and in [0,1]")
    if np.any(~county_mask & np.isfinite(county)):
        raise ValueError("undefined county values must remain null")
    if np.any(obs_mask & (~county_mask | (obs != county))):
        raise ValueError("observed values must be present and unchanged in county-tier output")

    final = county.copy()
    final_mask = county_mask.copy()
    regional_imputed = np.zeros(n, dtype=bool)
    donor_counts = np.zeros(n, dtype=np.int64)
    regional_medians = np.full(n, np.nan, dtype=np.float64)
    region_array = np.asarray(regions)
    for region in REGIONS:
        indices = np.flatnonzero(region_array == region)
        observed_indices = indices[obs_mask[indices]]
        for index in indices[~county_mask[indices]]:
            donors = observed_indices[observed_indices != index]
            donor_counts[index] = len(donors)
            if len(donors) < minimum_peers:
                continue
            median = float(np.median(obs[donors]))
            if not np.isfinite(median) or not 0.0 <= median <= 1.0:
                raise AssertionError("regional observed-peer median is invalid")
            final[index] = median
            final_mask[index] = True
            regional_imputed[index] = True
            regional_medians[index] = median
    if np.any(regional_imputed & (donor_counts < minimum_peers)):
        raise AssertionError("region-imputed row has fewer than minimum observed donors")
    if np.any(obs_mask & ((final != obs) | ~final_mask | regional_imputed)):
        raise AssertionError("original observed values must remain unchanged")
    county_imputed = county_mask & ~obs_mask
    if np.any(county_imputed & ((final != county) | ~final_mask | regional_imputed)):
        raise AssertionError("county-imputed values must remain unchanged")
    if np.any(~final_mask & np.isfinite(final)):
        raise AssertionError("remaining undefined values must stay null, never zero-filled")
    if np.any(final_mask & (~np.isfinite(final) | (final < 0.0) | (final > 1.0))):
        raise AssertionError("all final defined values must be finite and in [0,1]")
    return final, final_mask, regional_imputed, donor_counts, regional_medians


def output_table(geoids: list[str], regions: list[str], observed_values: np.ndarray,
                 observed_defined: np.ndarray, county_values: np.ndarray, county_defined: np.ndarray,
                 final_values: np.ndarray, final_defined: np.ndarray, regional_imputed: np.ndarray,
                 donor_counts: np.ndarray, regional_medians: np.ndarray) -> pa.Table:
    n = len(geoids)
    arrays = [np.asarray(x) for x in (observed_values, observed_defined, county_values,
              county_defined, final_values, final_defined, regional_imputed, donor_counts,
              regional_medians)]
    if len(regions) != n or any(x.shape != (n,) for x in arrays):
        raise ValueError("output columns must align with official GEOIDs")
    obs, obs_mask, county, county_mask, final, final_mask, region_imp, peers, medians = arrays
    if np.any(obs_mask & ((obs != final) | ~final_mask | region_imp)):
        raise ValueError("output does not preserve observed values")
    county_imp = county_mask & ~obs_mask
    if np.any(county_imp & ((county != final) | ~final_mask | region_imp)):
        raise ValueError("output does not preserve county-imputed values")
    if np.any(region_imp & (~final_mask | county_mask | (peers < MIN_REGION_PEERS))):
        raise ValueError("region-imputation flags are inconsistent")
    if np.any(~final_mask & np.isfinite(final)):
        raise ValueError("undefined final values must be null")
    if np.any(final_mask & (~np.isfinite(final) | (final < 0.0) | (final > 1.0))):
        raise ValueError("defined final values must be finite and bounded")
    if np.any(region_imp & (medians != final)):
        raise ValueError("region fallback values must equal their recorded region median")
    return pa.table({
        "GEOID": pa.array(geoids, type=pa.string()),
        "region": pa.array(regions, type=pa.string()),
        "observed_share": pa.array([float(obs[i]) if obs_mask[i] else None for i in range(n)], type=pa.float64()),
        "observed_defined": pa.array(obs_mask, type=pa.bool_()),
        "county_peer_share": pa.array([float(county[i]) if county_mask[i] else None for i in range(n)], type=pa.float64()),
        "county_defined": pa.array(county_mask, type=pa.bool_()),
        "county_imputed": pa.array(county_imp, type=pa.bool_()),
        "region_median_if_imputed": pa.array([float(medians[i]) if region_imp[i] else None for i in range(n)], type=pa.float64()),
        "region_observed_donor_count": pa.array(peers, type=pa.int64()),
        "region_imputed": pa.array(region_imp, type=pa.bool_()),
        "final_share": pa.array([float(final[i]) if final_mask[i] else None for i in range(n)], type=pa.float64()),
        "final_defined": pa.array(final_mask, type=pa.bool_()),
    })


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def audit(workspace: Path, report_path: Path, out_path: Path) -> dict[str, Any]:
    source_report = json.loads(report_path.read_text(encoding="utf-8"))
    if (source_report.get("audit") != "Pooled Mx5 county-peer median fallback: observed/imputed coverage sensitivity, not integration"
            or not source_report.get("acceptance", {}).get("acceptance_pass")):
        raise ValueError("a completed passing Mx5 county-peer screen is required")
    if source_report["scope"].get("official_ids") != 9379:
        raise ValueError("source county-peer audit is not scoped to 9,379 official GEOIDs")
    source_artifact = workspace / "mx5-county-peer-imputation" / "county-peer-mx5.parquet"
    source_artifact_sha = sha256_file(source_artifact)
    if source_artifact_sha != source_report["output_artifact"]["sha256"]:
        raise ValueError("county-peer artifact hash differs from its frozen audit report")
    frozen_path = workspace / "ideas" / "Mx5.parquet"
    expected_frozen_sha = source_report["inputs"]["frozen_mx5_artifact"]["sha256"]
    frozen_sha_before = sha256_file(frozen_path)
    if frozen_sha_before != expected_frozen_sha:
        raise ValueError("frozen Mx5 artifact hash differs from county-peer source audit")

    official = official_scored_pairs(workspace)
    if len(official) != 9379 or len(set(official)) != 9379 or len({g for g, _ in official}) != 9379:
        raise ValueError("official GEOID/region set is not an exact 9,379-ID partition")
    official_hash = hashlib.sha256(json.dumps(official, ensure_ascii=False,
                                              separators=(",", ":")).encode("utf-8")).hexdigest()
    if official_hash != source_report["inputs"]["official_geoid_region_sha256"]:
        raise ValueError("official GEOID/region partition differs from county-peer source screen")
    geoids = [geoid for geoid, _ in official]
    regions = [region for _, region in official]
    if set(regions) != set(REGIONS):
        raise ValueError("unexpected named study region in official scope")
    position = {pair: index for index, pair in enumerate(official)}

    table_in = pq.read_table(source_artifact, columns=[
        "GEOID", "region", "observed_share", "observed_defined", "county_peer_mx5_share", "defined", "imputed"])
    if table_in.num_rows != len(official):
        raise ValueError("county-peer artifact row count differs from official GEOID partition")
    observed_values = np.full(len(official), np.nan, dtype=np.float64)
    observed_defined = np.zeros(len(official), dtype=bool)
    county_values = np.full(len(official), np.nan, dtype=np.float64)
    county_defined = np.zeros(len(official), dtype=bool)
    county_imputed_source = np.zeros(len(official), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table_in.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in position or pair in seen:
            raise ValueError(f"county-peer artifact contains unexpected or duplicate GEOID/region {pair}")
        seen.add(pair)
        index = position[pair]
        obs_flag = bool(row["observed_defined"])
        county_flag = bool(row["defined"])
        raw_obs, raw_county = row["observed_share"], row["county_peer_mx5_share"]
        if obs_flag:
            if raw_obs is None:
                raise ValueError(f"observed Mx5 share is null for {pair[0]}")
            observed_values[index] = float(raw_obs)
        elif raw_obs is not None:
            raise ValueError(f"undefined observed Mx5 value is not null for {pair[0]}")
        if county_flag:
            if raw_county is None:
                raise ValueError(f"defined county-tier Mx5 share is null for {pair[0]}")
            county_values[index] = float(raw_county)
        elif raw_county is not None:
            raise ValueError(f"undefined county-tier Mx5 value is not null for {pair[0]}")
        if bool(row["imputed"]) != (county_flag and not obs_flag):
            raise ValueError(f"county imputation flag is inconsistent for {pair[0]}")
        observed_defined[index], county_defined[index] = obs_flag, county_flag
        county_imputed_source[index] = bool(row["imputed"])
    if seen != set(official):
        raise ValueError("county-peer artifact does not reconcile exactly to all official GEOIDs")
    if int(observed_defined.sum()) != source_report["scope"]["observed_defined_ids"] \
            or int(county_imputed_source.sum()) != source_report["scope"]["imputed_ids"] \
            or int((~county_defined).sum()) != source_report["scope"]["remaining_undefined_ids"]:
        raise ValueError("county-tier source counts disagree with its audit report")

    final_values, final_defined, region_imputed, donor_counts, region_medians = apply_region_median_fallback(
        geoids, regions, observed_values, observed_defined, county_values, county_defined,
        minimum_peers=MIN_REGION_PEERS)
    output = output_table(geoids, regions, observed_values, observed_defined, county_values,
                          county_defined, final_values, final_defined, region_imputed,
                          donor_counts, region_medians)
    payload = parquet_bytes(output)
    if payload != parquet_bytes(output):
        raise ValueError("region-fallback output Parquet serialization is not deterministic")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)

    total = len(official)
    observed_n = int(observed_defined.sum())
    county_imputed_n = int((county_defined & ~observed_defined).sum())
    region_imputed_n = int(region_imputed.sum())
    null_n = int((~final_defined).sum())
    final_n = int(final_defined.sum())
    if observed_n + county_imputed_n + region_imputed_n + null_n != total:
        raise AssertionError("observed/county/region/null origin partition does not sum to official IDs")
    per_region: dict[str, Any] = {}
    for region in REGIONS:
        mask = np.asarray(regions) == region
        obs_mask = mask & observed_defined
        county_mask = mask & county_defined & ~observed_defined
        region_mask = mask & region_imputed
        null_mask = mask & ~final_defined
        donor_pool = observed_values[obs_mask]
        per_region[region] = {
            "official_ids": int(mask.sum()),
            "observed_ids": int(obs_mask.sum()),
            "county_imputed_ids": int(county_mask.sum()),
            "region_imputed_ids": int(region_mask.sum()),
            "remaining_null_ids": int(null_mask.sum()),
            "final_defined_ids": int((mask & final_defined).sum()),
            "final_coverage_fraction": float((mask & final_defined).sum() / mask.sum()),
            "additional_region_imputation_fraction_of_region": float(region_mask.sum() / mask.sum()),
            "observed_donor_count": int(donor_pool.size),
            "observed_donor_median": float(np.median(donor_pool)) if donor_pool.size else None,
            "region_imputed_donor_count_min": int(donor_counts[region_mask].min()) if region_mask.any() else None,
            "region_imputed_donor_count_max": int(donor_counts[region_mask].max()) if region_mask.any() else None,
            "final_value_distribution": distribution(final_values[mask], final_defined[mask]),
        }

    observed_preserved = bool(np.array_equal(final_values[observed_defined], observed_values[observed_defined]))
    county_preserved = bool(np.array_equal(final_values[county_defined], county_values[county_defined]))
    nulls_stay_null = bool(np.all(np.isnan(final_values[~final_defined])))
    bounded = bool(np.isfinite(final_values[final_defined]).all()
                    and np.all((final_values[final_defined] >= 0.0) & (final_values[final_defined] <= 1.0)))
    coverage = final_n / total
    additional_fraction = region_imputed_n / total
    source_artifact_sha_after = sha256_file(source_artifact)
    frozen_sha_after = sha256_file(frozen_path)
    if source_artifact_sha_after != source_artifact_sha or frozen_sha_after != frozen_sha_before:
        raise AssertionError("source county artifact or frozen Mx5 changed during read-only fallback screen")
    acceptance = {
        "exact_official_geoid_region_partition": len(seen) == total,
        "final_coverage_at_least_99_9_percent": coverage >= MIN_FINAL_COVERAGE,
        "additional_region_imputations_at_most_0_7_percent": additional_fraction <= MAX_ADDITIONAL_IMPUTATION_FRACTION,
        "every_region_fallback_has_at_least_30_other_observed_region_peers": bool(
            np.all(donor_counts[region_imputed] >= MIN_REGION_PEERS)),
        "observed_values_preserved_exactly": observed_preserved,
        "county_imputed_values_preserved_exactly": county_preserved,
        "remaining_undefined_values_remain_null": nulls_stay_null,
        "all_defined_values_finite_and_in_0_1": bounded,
        "county_artifact_and_frozen_Mx5_unchanged": True,
    }
    acceptance["acceptance_pass"] = all(acceptance.values())
    report = {
        "schema_version": 1,
        "audit": "Pooled Mx5 hierarchical region-median fallback after county imputation; label-free sensitivity, not integration",
        "method": {
            "fallback": "Only county-tier undefined GEOIDs are considered. Fill from the median of original observed pooled-Mx5 shares in the same named study region, excluding the target GEOID; require at least 30 other observed donors.",
            "donors": "Original observed values only (`observed_defined=true` in the county-peer artifact); county-imputed values are preserved but never donate to regional fallback.",
            "hierarchy": "Original observed > previously accepted county-peer imputation > region-median imputation > null. Existing higher-priority values are copied unchanged.",
            "null_semantics": "If fewer than 30 observed regional donors exist, retain null with defined=false; never substitute zero.",
            "feature": "Pooled Mx5 education-name school-taxonomy-mismatch share; bounded [0,1].",
            "minimum_other_observed_region_peers": MIN_REGION_PEERS,
        },
        "measure_before": {"official_ids": total, "county_defined_ids": int(county_defined.sum()),
                           "county_imputed_ids": county_imputed_n, "remaining_null_ids": int((~county_defined).sum()),
                           "county_coverage_fraction": float(county_defined.sum() / total),
                           "region_fallback_ids": 0},
        "measure_after": {"official_ids": total, "observed_ids": observed_n,
                          "county_imputed_ids": county_imputed_n, "region_imputed_ids": region_imputed_n,
                          "remaining_null_ids": null_n, "final_defined_ids": final_n,
                          "final_coverage_fraction": coverage,
                          "additional_region_imputation_fraction": additional_fraction,
                          "final_value_distribution": distribution(final_values, final_defined),
                          "regions": per_region},
        "scope": {"official_ids": total, "exact_official_geoid_region_partition": True,
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_reference_data_read": False, "external_data_read": False,
                  "leaderboard_feedback_used": False, "frozen_Mx5_modified": False,
                  "automatic_integration_or_registry_change": False, "uploads": 0},
        "acceptance_thresholds": {"final_coverage_minimum": MIN_FINAL_COVERAGE,
                                   "additional_region_imputation_fraction_maximum": MAX_ADDITIONAL_IMPUTATION_FRACTION,
                                   "minimum_other_observed_region_peers": MIN_REGION_PEERS},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_hash,
            "county_peer_source_report": {"path": str(report_path), "sha256": sha256_file(report_path)},
            "county_peer_artifact": {"path": str(source_artifact), "sha256": source_artifact_sha,
                                     "sha256_after": source_artifact_sha_after, "rows": table_in.num_rows},
            "frozen_Mx5_artifact": {"path": str(frozen_path), "sha256": frozen_sha_before,
                                    "sha256_after": frozen_sha_after},
            "script_sha256": sha256_file(Path(__file__).resolve()),
        },
        "output_artifact": {"path": str(out_path), "rows": output.num_rows,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "columns": output.column_names, "deterministic_bytes": True},
        "interpretation": "Region fallback meets the registered coverage and imputation bounds; preserve as a separately reviewed sensitivity only, without modifying or automatically integrating frozen Mx5."
        if acceptance["acceptance_pass"] else
        "Region fallback fails one or more acceptance criteria; retain as a diagnostic sensitivity only, without modifying or automatically integrating frozen Mx5.",
    }
    report_out = ROOT / "reports/mx5-region-fallback.json"
    report_out.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--source-report", default="reports/mx5-county-peer-imputation.json")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE is required")
    source_report_path = ROOT / args.source_report
    out_path = Path(args.out) if args.out else workspace / "mx5-region-fallback" / "region-fallback-mx5.parquet"
    report = audit(workspace, source_report_path, out_path)
    print(json.dumps({"report": "reports/mx5-region-fallback.json",
                      "measure_after": report["measure_after"],
                      "acceptance": report["acceptance"],
                      "output_artifact": report["output_artifact"]}, indent=2, sort_keys=True))
    return 0 if report["acceptance"]["acceptance_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
