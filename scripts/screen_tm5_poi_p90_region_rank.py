#!/usr/bin/env python3
"""Compare region-local empirical midpercentiles of Tm5 POI p90 version to global log scale."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import official_scored_pairs  # noqa: E402
from screen_tm5_building_p90_mx4_interaction import (  # noqa: E402
    REGIONS, TM5_SHA, sha256_file, feature_digest, read_emission, parquet_bytes,
)
from screen_tm5_p90_version_mx4_interaction import (  # noqa: E402
    EXPECTED_ARTIFACT_HASHES, distribution,
)

VERSION_MAX = 13.0
MIN_COVERAGE = 0.9859
MIN_P95_CHANGE = 0.05
POI_EMISSION = ("Tm5", "Est", "covariate:tm5_poi_p90_version")


def empirical_midpercentile(values: np.ndarray, defined: np.ndarray) -> np.ndarray:
    """Midrank defined values, normalized as (average 1-based rank - 1)/(n - 1)."""
    x = np.asarray(values, dtype=np.float64)
    mask = np.asarray(defined, dtype=bool)
    if x.ndim != 1 or mask.shape != x.shape:
        raise ValueError("values and defined mask must be aligned vectors")
    if np.any(mask & ~np.isfinite(x)):
        raise ValueError("defined values must be finite")
    if np.any(~mask & np.isfinite(x)):
        raise ValueError("undefined values must remain null")
    result = np.full(x.shape, np.nan, dtype=np.float64)
    indices = np.flatnonzero(mask)
    n = indices.size
    if n == 0:
        return result
    order = indices[np.argsort(x[indices], kind="mergesort")]
    sorted_x = x[order]
    begin = 0
    while begin < n:
        end = begin + 1
        while end < n and sorted_x[end] == sorted_x[begin]:
            end += 1
        avg_rank = ((begin + 1) + end) / 2.0
        result[order[begin:end]] = 0.5 if n == 1 else (avg_rank - 1.0) / (n - 1.0)
        begin = end
    if np.any(mask & (~np.isfinite(result) | (result < 0.0) | (result > 1.0))):
        raise AssertionError("empirical midpercentile outside [0,1]")
    return result


def region_rank(pairs: list[tuple[str, str]], values: np.ndarray,
                defined: np.ndarray) -> np.ndarray:
    if len(set(region for _, region in pairs)) != len(REGIONS) or set(region for _, region in pairs) != set(REGIONS):
        raise ValueError("official partition must cover the four frozen regions exactly")
    ranks = np.full(len(pairs), np.nan, dtype=np.float64)
    for region in REGIONS:
        local = np.asarray([r == region for _, r in pairs], dtype=bool)
        ranks[local] = empirical_midpercentile(values[local], defined[local])
    return ranks


def output_table(pairs: list[tuple[str, str]], raw: np.ndarray, defined: np.ndarray,
                 global_norm: np.ndarray, rank: np.ndarray, change: np.ndarray) -> pa.Table:
    n = len(pairs)
    if any(np.asarray(v).shape != (n,) for v in (raw, defined, global_norm, rank, change)):
        raise ValueError("output vectors must exactly align to official GEOID-region pairs")
    mask = np.asarray(defined, dtype=bool)
    if (np.any(mask & (~np.isfinite(rank) | (rank < 0.0) | (rank > 1.0)))
            or np.any(mask & (~np.isfinite(global_norm) | (global_norm < 0.0) | (global_norm > 1.0)))
            or np.any(mask & (~np.isfinite(change) | (change < 0.0) | (change > 1.0)))
            or np.any(~mask & (np.isfinite(rank) | np.isfinite(global_norm) | np.isfinite(change)))):
        raise ValueError("defined outputs must be finite/bounded and undefined outputs null")
    return pa.table({
        "GEOID": pa.array([g for g, _ in pairs], type=pa.string()),
        "region": pa.array([r for _, r in pairs], type=pa.string()),
        "tm5_poi_p90_version": pa.array([float(v) if ok else None for v, ok in zip(raw, mask)], type=pa.float64()),
        "poi_p90_version_defined": pa.array(mask, type=pa.bool_()),
        "global_log_normalized_version": pa.array([float(v) if ok else None for v, ok in zip(global_norm, mask)], type=pa.float64()),
        "within_region_midpercentile": pa.array([float(v) if ok else None for v, ok in zip(rank, mask)], type=pa.float64()),
        "absolute_change_from_global_log_normalized": pa.array([float(v) if ok else None for v, ok in zip(change, mask)], type=pa.float64()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    baseline_path = ROOT / "reports/tm5-p90-version-mx4-interaction.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    expected_tm5_sha = EXPECTED_ARTIFACT_HASHES["Tm5"]
    if (baseline["scope"]["official_ids"] != 9379
            or baseline["scope"]["tm5_p90_version_defined_ids"] != 9377
            or not np.isclose(baseline["measure_after"]["normalized_p90_input"]["p50"], 0.9086181061280519, rtol=0, atol=1e-12)
            or not np.isclose(baseline["measure_after"]["normalized_p90_input"]["p90"], 0.9415887339401863, rtol=0, atol=1e-12)
            or not np.isclose(baseline["measure_after"]["normalized_p90_input"]["p95"], 0.9415887339401863, rtol=0, atol=1e-12)
            or baseline["measure_before"]["p90_version_source_range_min"] != 1.0
            or baseline["measure_before"]["p90_version_source_range_max"] != 13.0
            or baseline["provenance"]["frozen_inputs"]["Tm5"]["sha256"] != expected_tm5_sha
            or expected_tm5_sha != TM5_SHA):
        raise ValueError("frozen POI p90 baseline differs from assigned measure-before")
    pairs = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(pairs) != 9379 or len(set(pairs)) != 9379 or len({g for g, _ in pairs}) != 9379
            or set(r for _, r in pairs) != set(REGIONS)):
        raise ValueError("official scored IDs must be the exact 9,379-ID four-region partition")
    partition_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in pairs).encode()).hexdigest()
    if partition_sha != baseline["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official GEOID-region partition differs from validated baseline")
    source_path = workspace / "ideas/Tm5.parquet"
    raw, defined = read_emission(source_path, expected_tm5_sha, *POI_EMISSION, pairs)
    if int(defined.sum()) != 9377 or not np.isclose(raw[defined].min(), 1., rtol=0, atol=1e-12) or not np.isclose(raw[defined].max(), 13., rtol=0, atol=1e-12):
        raise ValueError("provided POI p90 version coverage or [1,13] range differs from baseline")
    rank = region_rank(pairs, raw, defined)
    global_norm = np.full(len(pairs), np.nan, dtype=np.float64)
    global_norm[defined] = np.clip(np.log1p(raw[defined]) / np.log1p(VERSION_MAX), 0., 1.)
    change = np.full(len(pairs), np.nan, dtype=np.float64)
    change[defined] = np.abs(rank[defined] - global_norm[defined])
    if (not np.isfinite(rank[defined]).all() or not np.isfinite(global_norm[defined]).all()
            or not np.isfinite(change[defined]).all()
            or np.any((rank[defined] < 0.) | (rank[defined] > 1.))
            or np.any((global_norm[defined] < 0.) | (global_norm[defined] > 1.))
            or np.any((change[defined] < 0.) | (change[defined] > 1.))):
        raise ValueError("rank, baseline and absolute-change outputs must be finite and bounded [0,1]")
    overall = distribution(change[defined])
    regions: dict[str, Any] = {}
    for region in REGIONS:
        local = np.asarray([r == region for _, r in pairs], dtype=bool)
        mask = local & defined
        regions[region] = {
            "official_ids": int(local.sum()), "defined_ids": int(mask.sum()),
            "coverage_fraction": float(mask.sum() / local.sum()),
            "source_version": distribution(raw[mask]),
            "global_log_normalized_version": distribution(global_norm[mask]),
            "within_region_midpercentile": distribution(rank[mask]),
            "absolute_change_from_global_log_normalized": distribution(change[mask]),
        }
    table = output_table(pairs, raw, defined, global_norm, rank, change)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("rank artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    registry_path = ROOT / "config/ideas.json"
    registry_before = sha256_file(registry_path)
    source_after = sha256_file(source_path)
    registry_after = sha256_file(registry_path)
    frozen_unchanged = source_after == expected_tm5_sha and registry_after == registry_before
    if not frozen_unchanged:
        raise AssertionError("frozen Tm5 or registry changed")
    coverage = float(defined.mean())
    p95 = overall["p95"]
    acceptance = {
        "exact_9379_official_id_alignment": bool(table.num_rows == 9379 and len(set(pairs)) == 9379),
        "defined_ids": int(defined.sum()), "coverage_fraction": coverage,
        "coverage_at_least_98_59_percent": bool(coverage >= MIN_COVERAGE),
        "defined_ranks_and_changes_finite_bounded_0_1": True,
        "nulls_preserved": bool(np.array_equal(np.isfinite(rank), defined)
                                 and np.array_equal(np.isfinite(change), defined)),
        "p95_absolute_change_from_global_log_normalized": p95,
        "p95_change_at_least_0_05": bool(p95 >= MIN_P95_CHANGE),
        "frozen_Tm5_and_registry_unchanged": bool(frozen_unchanged),
        "screen_pass": bool(coverage >= MIN_COVERAGE and p95 >= MIN_P95_CHANGE and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "screen": "Tm5 POI p90 within-region empirical midpercentile rank; unregistered label-free scale sensitivity",
        "definition": "Rank only defined provided POI p90 versions separately within each frozen region. Ties receive average rank; percentile=(average 1-based rank−1)/(n−1), with singleton groups at 0.5. Compare against clip(log1p(version)/log1p(13),0,1) using absolute difference.",
        "measure_before": {
            "source_version_range": [1.0, 13.0],
            "global_log_normalized_p50": baseline["measure_after"]["normalized_p90_input"]["p50"],
            "global_log_normalized_p90": baseline["measure_after"]["normalized_p90_input"]["p90"],
            "global_log_normalized_p95": baseline["measure_after"]["normalized_p90_input"]["p95"],
            "global_transform_report_sha256": sha256_file(baseline_path),
        },
        "method": {"region": "Use only the frozen official GEOID-region partition.",
                   "ranking": "Each region's defined POI version values are ranked using tied average ranks. Undefined rows remain null and do not enter ranks.",
                   "integration": "Unregistered diagnostic only; no estimator, candidate, or registry updates."},
        "scope": {"official_ids": len(pairs), "exact_official_geoid_region_partition": True,
                  "defined_ids": int(defined.sum()), "undefined_ids": int((~defined).sum()),
                  "coverage_fraction": coverage, "labels_read": False,
                  "sample_score_values_read": False, "Mx4_or_other_inputs_read": False,
                  "removed_or_equivalent_reference_data_read": False, "external_data_read": False,
                  "leaderboard_feedback_used": False, "registry_integration": False,
                  "frozen_Tm5_modified": False, "uploads": 0},
        "measure_after": {"global_log_normalized_version": distribution(global_norm[defined]),
                          "within_region_midpercentile": distribution(rank[defined]),
                          "absolute_change_from_global_log_normalized": overall,
                          "p95_absolute_change_from_global_log_normalized": p95,
                          "regions": regions},
        "thresholds": {"minimum_coverage_inclusive": MIN_COVERAGE,
                       "output_bounds_inclusive": [0.0, 1.0],
                       "minimum_p95_absolute_change_inclusive": MIN_P95_CHANGE},
        "acceptance": acceptance,
        "provenance": {"official_geoid_region_sha256": partition_sha,
                       "Tm5_artifact": {"path": str(source_path), "sha256_before": expected_tm5_sha,
                                        "sha256_after": source_after,
                                        "poi_p90_version_emission_sha256": feature_digest(pairs, raw, defined)},
                       "baseline_report_sha256": sha256_file(baseline_path),
                       "registry_sha256_before": registry_before, "registry_sha256_after": registry_after,
                       "script_sha256": sha256_file(Path(__file__).resolve()),
                       "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                           "sha256": hashlib.sha256(payload).hexdigest(),
                                           "columns": table.column_names, "deterministic_bytes": True}},
        "interpretation": "Region-local midpercentile scale differs materially from the existing global log-normalized scale; retain only as an unregistered sensitivity pending a separate distinctness review." if acceptance["screen_pass"] else
        "One or more requested coverage, bounds, or effect gates failed; do not integrate the regional rank.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm5-poi-p90-region-rank.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "tm5-poi-p90-region-rank/poi-p90-region-rank.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "measure_after": {"p95_absolute_change_from_global_log_normalized": report["measure_after"]["p95_absolute_change_from_global_log_normalized"],
                                        "regional_p95": {r: report["measure_after"]["regions"][r]["absolute_change_from_global_log_normalized"]["p95"] for r in REGIONS}},
                      "output_artifact": report["provenance"]["output_artifact"], "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
