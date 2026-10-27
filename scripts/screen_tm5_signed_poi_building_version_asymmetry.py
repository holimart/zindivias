#!/usr/bin/env python3
"""Retain the sign of provided POI-minus-building within-region version-rank gaps."""
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
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import official_scored_pairs  # noqa: E402
from screen_tm5_building_p90_mx4_interaction import REGIONS, TM5_SHA, sha256_file, parquet_bytes  # noqa: E402

EXPECTED_N = 9379
MIN_COVERAGE = 0.9859
MIN_MEAN_GAP = 0.05
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261030
SOURCE_REPORT = ROOT / "reports/tm5-poi-building-version-discordance.json"


def signed_gap(poi_rank: np.ndarray, building_rank: np.ndarray,
               poi_defined: np.ndarray, building_defined: np.ndarray,
               joint_defined: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    poi = np.asarray(poi_rank, dtype=np.float64)
    building = np.asarray(building_rank, dtype=np.float64)
    pmask, bmask = np.asarray(poi_defined, dtype=bool), np.asarray(building_defined, dtype=bool)
    if poi.ndim != 1 or any(x.shape != poi.shape for x in (building, pmask, bmask)):
        raise ValueError("ranks and masks must be aligned one-dimensional arrays")
    if np.any(pmask & (~np.isfinite(poi) | (poi < 0.) | (poi > 1.))) or np.any(~pmask & np.isfinite(poi)):
        raise ValueError("POI rank must be bounded when defined and null otherwise")
    if np.any(bmask & (~np.isfinite(building) | (building < 0.) | (building > 1.))) or np.any(~bmask & np.isfinite(building)):
        raise ValueError("building rank must be bounded when defined and null otherwise")
    joint = pmask & bmask
    if joint_defined is not None and not np.array_equal(joint, np.asarray(joint_defined, dtype=bool)):
        raise ValueError("joint mask must exactly equal the two source-defined masks")
    gap = np.full(poi.shape, np.nan)
    gap[joint] = poi[joint] - building[joint]
    if np.any(joint & (~np.isfinite(gap) | (gap < -1.) | (gap > 1.))):
        raise ValueError("defined signed gaps must be finite and in [-1,1]")
    return gap, joint


def bootstrap_mean_ci(values: np.ndarray, *, seed: int, replicates: int = BOOTSTRAP_REPLICATES,
                      batch_size: int = 250) -> dict[str, Any]:
    x = np.asarray(values, dtype=np.float64)
    if x.ndim != 1 or not x.size or not np.isfinite(x).all() or replicates < 100 or batch_size < 1:
        raise ValueError("bootstrap requires finite nonempty values and at least 100 replicates")
    rng = np.random.default_rng(seed)
    means = np.empty(replicates, dtype=np.float64)
    start = 0
    while start < replicates:
        count = min(batch_size, replicates - start)
        indices = rng.integers(0, x.size, size=(count, x.size))
        means[start:start + count] = x[indices].mean(axis=1)
        start += count
    return {"mean": float(x.mean()), "ci95_percentile": [float(v) for v in np.quantile(means, [.025, .975])],
            "bootstrap_replicates": replicates, "seed": seed,
            "ci_method": "deterministic tract-resampled percentile bootstrap; descriptive, not spatial-cluster inference"}


def distribution(x: np.ndarray) -> dict[str, Any]:
    values = np.asarray(x, dtype=np.float64)
    if values.ndim != 1 or not values.size or not np.isfinite(values).all():
        raise ValueError("distribution requires finite nonempty vector")
    return {"n": int(values.size), "mean": float(values.mean()),
            "p50": float(np.quantile(values, .5)), "p95": float(np.quantile(values, .95)),
            "min": float(values.min()), "max": float(values.max())}


def load_rank_artifact(path: Path, expected_sha: str, official: list[tuple[str, str]]) -> dict[str, np.ndarray]:
    if not path.is_file() or sha256_file(path) != expected_sha:
        raise ValueError("rank source artifact missing or hash differs from the validated absolute-discordance report")
    table = pq.read_table(path, columns=["GEOID", "region", "poi_version_defined", "poi_within_region_midpercentile",
                                          "building_version_defined", "building_within_region_midpercentile",
                                          "joint_defined", "absolute_cross_layer_percentile_difference"])
    if table.num_rows != len(official):
        raise ValueError("rank source artifact must have exactly one row per official ID")
    positions = {pair: i for i, pair in enumerate(official)}
    poi = np.full(len(official), np.nan)
    building = np.full(len(official), np.nan)
    pmask = np.zeros(len(official), dtype=bool)
    bmask = np.zeros(len(official), dtype=bool)
    joint_mask = np.zeros(len(official), dtype=bool)
    abs_gap = np.full(len(official), np.nan)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in positions or pair in seen:
            raise ValueError(f"unexpected or duplicate rank-artifact ID: {pair}")
        seen.add(pair)
        i = positions[pair]
        pmask[i], bmask[i], joint_mask[i] = bool(row["poi_version_defined"]), bool(row["building_version_defined"]), bool(row["joint_defined"])
        if pmask[i]:
            if row["poi_within_region_midpercentile"] is None:
                raise ValueError("defined POI rank is null")
            poi[i] = float(row["poi_within_region_midpercentile"])
        elif row["poi_within_region_midpercentile"] is not None:
            raise ValueError("undefined POI rank must be null")
        if bmask[i]:
            if row["building_within_region_midpercentile"] is None:
                raise ValueError("defined building rank is null")
            building[i] = float(row["building_within_region_midpercentile"])
        elif row["building_within_region_midpercentile"] is not None:
            raise ValueError("undefined building rank must be null")
        if joint_mask[i]:
            if row["absolute_cross_layer_percentile_difference"] is None:
                raise ValueError("defined absolute-gap source must be nonnull")
            abs_gap[i] = float(row["absolute_cross_layer_percentile_difference"])
        elif row["absolute_cross_layer_percentile_difference"] is not None:
            raise ValueError("undefined absolute-gap source must be null")
    if seen != set(official):
        raise ValueError("rank artifact does not reconcile exactly to official IDs")
    if not np.array_equal(joint_mask, pmask & bmask):
        raise ValueError("rank source joint mask is not exact source-mask intersection")
    return {"poi_rank": poi, "building_rank": building, "poi_defined": pmask,
            "building_defined": bmask, "joint_defined": joint_mask, "absolute_gap": abs_gap}


def output_table(official: list[tuple[str, str]], source: dict[str, np.ndarray],
                 gap: np.ndarray, joint: np.ndarray) -> pa.Table:
    poi, building = source["poi_rank"], source["building_rank"]
    if not np.array_equal(joint, source["poi_defined"] & source["building_defined"]):
        raise ValueError("output signed-gap mask differs from exact joint source-defined mask")
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "poi_within_region_midpercentile": pa.array([float(v) if ok else None for v, ok in zip(poi, source["poi_defined"])], type=pa.float64()),
        "poi_version_defined": pa.array(source["poi_defined"], type=pa.bool_()),
        "building_within_region_midpercentile": pa.array([float(v) if ok else None for v, ok in zip(building, source["building_defined"])], type=pa.float64()),
        "building_version_defined": pa.array(source["building_defined"], type=pa.bool_()),
        "joint_defined": pa.array(joint, type=pa.bool_()),
        "signed_poi_minus_building_rank_gap": pa.array([float(v) if ok else None for v, ok in zip(gap, joint)], type=pa.float64()),
        "absolute_rank_gap": pa.array([float(v) if ok else None for v, ok in zip(source["absolute_gap"], joint)], type=pa.float64()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    source = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    if (source["scope"]["official_ids"] != EXPECTED_N or source["scope"]["joint_defined_ids"] != 9377
            or not np.isclose(source["measure_after"]["p95_absolute_cross_layer_percentile_difference"],
                              0.7056666666666667, rtol=0, atol=1e-12)
            or not np.isclose(source["measure_after"]["absolute_cross_layer_percentile_difference"]["p50"],
                              0.24291666666666667, rtol=0, atol=1e-12)):
        raise ValueError("absolute-discordance measure-before differs from issued baseline")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N
            or len({g for g, _ in official}) != EXPECTED_N or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official scored GEOID-region partition must have exactly 9,379 unique IDs")
    official_hash = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode("utf-8")).hexdigest()
    if official_hash != source["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official partition differs from the frozen rank source report")
    artifact_path_source = workspace / "tm5-poi-building-version-discordance" / "version-discordance.parquet"
    artifact_sha = source["provenance"]["output_artifact"]["sha256"]
    ranked = load_rank_artifact(artifact_path_source, artifact_sha, official)
    poi_path = workspace / "ideas/Tm5.parquet"
    tm5_before = sha256_file(poi_path)
    if tm5_before != TM5_SHA or source["provenance"]["Tm5_artifact"]["sha256_before"] != TM5_SHA:
        raise ValueError("frozen Tm5 artifact hash differs from source rank audit")
    gap, joint = signed_gap(ranked["poi_rank"], ranked["building_rank"], ranked["poi_defined"],
                            ranked["building_defined"], ranked["joint_defined"])
    if not np.array_equal(np.abs(gap[joint]), ranked["absolute_gap"][joint]):
        raise ValueError("signed result does not reproduce the frozen absolute-gap source values")
    coverage = float(joint.mean())
    signed_summary = distribution(gap[joint])
    regional: dict[str, Any] = {}
    for j, region in enumerate(REGIONS):
        local = np.asarray([r == region for _, r in official], dtype=bool)
        local_defined = joint & local
        regional_values = gap[local_defined]
        if not regional_values.size:
            raise ValueError(f"region {region} has no joint-defined ranks")
        regional[region] = {
            "official_ids": int(local.sum()), "joint_defined_ids": int(local_defined.sum()),
            "joint_coverage_fraction": float(local_defined.sum() / local.sum()),
            "mean_signed_gap_and_ci95": bootstrap_mean_ci(regional_values, seed=BOOTSTRAP_SEED + j),
            "signed_gap_distribution": distribution(regional_values),
            "p95_absolute_gap": float(np.quantile(np.abs(regional_values), .95)),
        }
    table = output_table(official, ranked, gap, joint)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("signed-gap Parquet serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    registry_path = ROOT / "config/ideas.json"
    registry_before = sha256_file(registry_path)
    tm5_after = sha256_file(poi_path)
    registry_after = sha256_file(registry_path)
    unchanged = tm5_after == TM5_SHA and registry_after == registry_before
    if not unchanged:
        raise AssertionError("frozen Tm5 or registry changed")
    qualifying = [r for r in REGIONS if abs(regional[r]["mean_signed_gap_and_ci95"]["mean"]) >= MIN_MEAN_GAP]
    acceptance = {
        "exact_9379_id_alignment": table.num_rows == EXPECTED_N and len(set(official)) == EXPECTED_N,
        "joint_defined_mask_exact_and_nulls_preserved": bool(np.array_equal(joint, ranked["poi_defined"] & ranked["building_defined"])
                                                                 and np.array_equal(np.isfinite(gap), joint)),
        "joint_coverage_fraction": coverage, "coverage_at_least_98_59_percent": bool(coverage >= MIN_COVERAGE),
        "signed_gap_finite_bounded_minus1_to_1": bool(np.isfinite(gap[joint]).all() and np.all((gap[joint] >= -1.) & (gap[joint] <= 1.))),
        "regions_with_absolute_mean_signed_gap_at_least_0_05": qualifying,
        "at_least_two_regions_meet_signed_mean_gate": len(qualifying) >= 2,
        "source_absolute_gap_p95_reproduced": bool(np.isclose(np.quantile(np.abs(gap[joint]), .95),
                                                                 source["measure_after"]["p95_absolute_cross_layer_percentile_difference"], rtol=0, atol=1e-12)),
        "frozen_Tm5_and_registry_unchanged": bool(unchanged),
        "signed_direction_screen_pass": bool(coverage >= MIN_COVERAGE and len(qualifying) >= 2 and unchanged),
    }
    report = {
        "schema_version": 1,
        "screen": "Signed within-region POI-minus-building p90-version midpercentile asymmetry; unregistered label-free diagnostic",
        "definition": "For each GEOID, signed_gap = independently ranked within-region POI p90 midpercentile − independently ranked within-region building p90 midpercentile. Preserve the validated source ranks and exact joint mask; positive means POI rank is higher.",
        "measure_before": {"absolute_gap_p95": source["measure_after"]["p95_absolute_cross_layer_percentile_difference"],
                           "absolute_gap_p50": source["measure_after"]["absolute_cross_layer_percentile_difference"]["p50"],
                           "joint_coverage_fraction": source["scope"]["joint_coverage_fraction"],
                           "direction_retained": False, "rank_source_report_sha256": sha256_file(SOURCE_REPORT),
                           "rank_source_artifact_sha256": artifact_sha},
        "method": {"rank_reuse": "Read separately ranked POI/building midpercentiles from the frozen prior diagnostic artifact; no re-ranking or imputation.",
                   "confidence_interval": "Within-region, deterministic tract-resampled percentile bootstrap (2,000 draws); descriptive interval, not spatial-cluster inference.",
                   "seed_base": BOOTSTRAP_SEED, "unregistered": True},
        "scope": {"official_ids": len(official), "joint_defined_ids": int(joint.sum()),
                  "joint_undefined_ids": int((~joint).sum()), "joint_coverage_fraction": coverage,
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_reference_data_read": False, "external_data_read": False,
                  "leaderboard_feedback_used": False, "registry_integration": False,
                  "frozen_Tm5_modified": False, "uploads": 0},
        "measure_after": {"signed_poi_minus_building_rank_gap": signed_summary,
                          "p95_absolute_gap": float(np.quantile(np.abs(gap[joint]), .95)),
                          "regional_signed_means_and_ci95": regional},
        "thresholds": {"minimum_joint_coverage_inclusive": MIN_COVERAGE,
                       "minimum_regions_with_absolute_signed_mean_inclusive": 2,
                       "minimum_absolute_regional_signed_mean_inclusive": MIN_MEAN_GAP,
                       "signed_gap_bounds_inclusive": [-1., 1.]},
        "acceptance": acceptance,
        "provenance": {"official_geoid_region_sha256": official_hash,
                       "frozen_Tm5_artifact": {"path": str(poi_path), "sha256_before": tm5_before, "sha256_after": tm5_after},
                       "source_rank_report": {"path": str(SOURCE_REPORT), "sha256": sha256_file(SOURCE_REPORT)},
                       "source_rank_artifact": {"path": str(artifact_path_source), "sha256": artifact_sha},
                       "registry_sha256_before": registry_before, "registry_sha256_after": registry_after,
                       "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                           "sha256": hashlib.sha256(payload).hexdigest(),
                                           "columns": table.column_names, "deterministic_bytes": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The signed direction is retained; at least two regions meet the preregistered mean-magnitude gate. Keep as a label-free diagnostic only; no feature promotion or causal/target-error interpretation." if acceptance["signed_direction_screen_pass"] else
                         "The regional signed-mean gate failed; retain only the established absolute-discordance diagnostic.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm5-signed-poi-building-version-asymmetry.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "tm5-signed-poi-building-version-asymmetry/signed-rank-gap.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"acceptance": report["acceptance"],
                      "regional_signed_means_and_ci95": report["measure_after"]["regional_signed_means_and_ci95"],
                      "output_artifact": report["provenance"]["output_artifact"], "report": str(ROOT / args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
