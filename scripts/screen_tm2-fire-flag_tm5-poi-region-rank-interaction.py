#!/usr/bin/env python3
"""Screen binary Tm2×Tm3 recovery flag multiplied by region-local Tm5 POI p90 rank."""
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
from screen_tm2_recovery_flag_mx4_interaction import (  # noqa: E402
    REGIONS, read_flag_artifact, sha256_file, parquet_bytes,
)
from screen_tm5_building_p90_mx4_interaction import (  # noqa: E402
    TM5_SHA, read_emission, feature_digest,
)
from screen_tm5_poi_p90_region_rank import empirical_midpercentile, region_rank  # noqa: E402

EXPECTED_N = 9379
MIN_COVERAGE = 0.9859
MIN_FLAGGED_P95_CHANGE = 0.05
FLAG_ARTIFACT_SHA = "5a51033b3c8c6cec395f5f51c6f86bd6d60e3c95098b5586ae13ca946e09d133"
FLAG_REPORT = ROOT / "reports/tm2-fire-recovery-flag.json"
GLOBAL_INTERACTION_REPORT = ROOT / "reports/tm2-fire-flag-tm5-poi-version.json"
RANK_REPORT = ROOT / "reports/tm5-poi-p90-region-rank.json"
POI_EMISSION = ("Tm5", "Est", "covariate:tm5_poi_p90_version")


def rank_interaction(flag: np.ndarray, flag_defined: np.ndarray,
                     version: np.ndarray, version_defined: np.ndarray,
                     rank: np.ndarray) -> dict[str, np.ndarray | int]:
    flag = np.asarray(flag, dtype=np.float64)
    flag_mask = np.asarray(flag_defined, dtype=bool)
    poi = np.asarray(version, dtype=np.float64)
    poi_mask = np.asarray(version_defined, dtype=bool)
    percentile = np.asarray(rank, dtype=np.float64)
    if flag.ndim != 1 or any(x.shape != flag.shape for x in (flag_mask, poi, poi_mask, percentile)):
        raise ValueError("flag, POI version, rank, and masks must align as vectors")
    if np.any(flag_mask & (~np.isfinite(flag) | ~np.isin(flag, (0., 1.)))):
        raise ValueError("defined recovery flags must be finite binary values")
    if np.any(~flag_mask & np.isfinite(flag)):
        raise ValueError("undefined recovery flags must remain null")
    if np.any(poi_mask & ~np.isfinite(poi)) or np.any(~poi_mask & np.isfinite(poi)):
        raise ValueError("POI version finite/null mask mismatch")
    if np.any(poi_mask & (~np.isfinite(percentile) | (percentile < 0.) | (percentile > 1.))):
        raise ValueError("defined region ranks must be finite and in [0,1]")
    if np.any(~poi_mask & np.isfinite(percentile)):
        raise ValueError("undefined POI ranks must remain null")
    joint = flag_mask & poi_mask
    interaction = np.full(flag.shape, np.nan)
    change = np.full(flag.shape, np.nan)
    interaction[joint] = flag[joint] * percentile[joint]
    change[joint] = np.abs(interaction[joint] - flag[joint])
    if np.any(joint & (~np.isfinite(interaction) | (interaction < 0.) | (interaction > 1.))):
        raise ValueError("defined interaction must be finite and bounded [0,1]")
    return {"interaction": interaction, "change": change, "defined": joint}


def output_table(pairs: list[tuple[str, str]], flag: np.ndarray, flag_defined: np.ndarray,
                 version: np.ndarray, version_defined: np.ndarray, rank: np.ndarray,
                 result: dict[str, Any]) -> pa.Table:
    joint = np.asarray(result["defined"], dtype=bool)
    n = len(pairs)
    arrays = (flag, flag_defined, version, version_defined, rank, result["interaction"], result["change"])
    if any(np.asarray(a).shape != (n,) for a in arrays):
        raise ValueError("output arrays must align to exact official GEOID-region IDs")
    if not np.array_equal(joint, np.asarray(flag_defined, bool) & np.asarray(version_defined, bool)):
        raise ValueError("joint interaction mask must equal exact source-mask intersection")
    return pa.table({
        "GEOID": pa.array([g for g, _ in pairs], type=pa.string()),
        "region": pa.array([r for _, r in pairs], type=pa.string()),
        "recovery_flag": pa.array([int(v) if ok else None for v, ok in zip(flag, flag_defined)], type=pa.int8()),
        "recovery_flag_defined": pa.array(flag_defined, type=pa.bool_()),
        "tm5_poi_p90_version": pa.array([float(v) if ok else None for v, ok in zip(version, version_defined)], type=pa.float64()),
        "poi_version_defined": pa.array(version_defined, type=pa.bool_()),
        "within_region_midpercentile": pa.array([float(v) if ok else None for v, ok in zip(rank, version_defined)], type=pa.float64()),
        "recovery_flag_x_region_rank": pa.array([float(v) if ok else None for v, ok in zip(result["interaction"], joint)], type=pa.float64()),
        "absolute_change_from_binary_flag": pa.array([float(v) if ok else None for v, ok in zip(result["change"], joint)], type=pa.float64()),
        "interaction_defined": pa.array(joint, type=pa.bool_()),
    })


def _distribution(x: np.ndarray) -> dict[str, Any]:
    values = np.asarray(x, dtype=np.float64)
    if not values.size:
        return {"n": 0, "min": None, "p50": None, "p95": None, "max": None}
    return {"n": int(values.size), "min": float(values.min()),
            "p50": float(np.quantile(values, .5)), "p95": float(np.quantile(values, .95)),
            "max": float(values.max())}


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    flag_report = json.loads(FLAG_REPORT.read_text(encoding="utf-8"))
    old_interaction = json.loads(GLOBAL_INTERACTION_REPORT.read_text(encoding="utf-8"))
    rank_report = json.loads(RANK_REPORT.read_text(encoding="utf-8"))
    if (flag_report["scope"]["official_ids"] != EXPECTED_N
            or flag_report["scope"]["flagged_ids"] != 30
            or flag_report["scope"]["flagged_regions"] != 4
            or not flag_report["acceptance"]["acceptance_pass"]):
        raise ValueError("validated source recovery flag must remain 30 IDs across four regions")
    if (not np.isclose(old_interaction["measure_after"]["flagged_only_p95_absolute_change_from_flag"],
                       0.09138189387194806, rtol=0, atol=1e-12)
            or not np.isclose(rank_report["measure_after"]["p95_absolute_change_from_global_log_normalized"],
                              0.5005576527275482, rtol=0, atol=1e-12)):
        raise ValueError("global-version/rank before measurements differ from the issued baseline")
    flag_path = workspace / "tm2-fire-recovery-flag" / "tm2-fire-recovery-flag.parquet"
    if sha256_file(flag_path) != FLAG_ARTIFACT_SHA or flag_report["output_artifact"]["sha256"] != FLAG_ARTIFACT_SHA:
        raise ValueError("frozen source flag artifact hash mismatch")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N
            or len({g for g, _ in official}) != EXPECTED_N or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official GEOID-region input must be the exact 9,379-ID partition")
    official_sha = hashlib.sha256(json.dumps(official, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    if official_sha != flag_report["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official partition differs from source recovery-flag report")
    flag, flag_defined, _ = read_flag_artifact(flag_path, FLAG_ARTIFACT_SHA, official)
    expected_geoids = [g for region in REGIONS for g in flag_report["measure_after"]["regions"][region]["flagged_geoids"]]
    actual_geoids = [g for (g, _), value, ok in zip(official, flag, flag_defined) if ok and value == 1]
    if len(actual_geoids) != 30 or len(set(actual_geoids)) != 30 or sorted(actual_geoids) != sorted(expected_geoids):
        raise ValueError("frozen 30-flag GEOID set differs from its validated report")
    tm5_path = workspace / "ideas/Tm5.parquet"
    version, version_defined = read_emission(tm5_path, TM5_SHA, *POI_EMISSION, official)
    if int(version_defined.sum()) != 9377:
        raise ValueError("frozen POI p90 version coverage differs from rank screen")
    ranks = region_rank(official, version, version_defined)
    result = rank_interaction(flag, flag_defined, version, version_defined, ranks)
    joint = np.asarray(result["defined"], dtype=bool)
    flagged = joint & (flag == 1.)
    if int(flagged.sum()) != 30:
        raise ValueError("all 30 frozen flags must have a defined POI rank")
    flagged_change = np.asarray(result["change"])[flagged]
    flagged_p95 = float(np.quantile(flagged_change, .95))
    regional: dict[str, Any] = {}
    for region in REGIONS:
        rmask = np.asarray([r == region for _, r in official], dtype=bool)
        local_flagged = flagged & rmask
        regional[region] = {
            "official_ids": int(rmask.sum()), "flag_defined_ids": int((flag_defined & rmask).sum()),
            "poi_version_defined_ids": int((version_defined & rmask).sum()),
            "joint_defined_ids": int((joint & rmask).sum()),
            "joint_coverage_fraction": float((joint & rmask).sum() / rmask.sum()),
            "flagged_ids": int(local_flagged.sum()),
            "flagged_geoid_list": [g for (g, rr), yes in zip(official, local_flagged) if rr == region and yes],
            "flagged_only_absolute_change_distribution": _distribution(np.asarray(result["change"])[local_flagged]),
            "rank_distribution": _distribution(ranks[rmask & version_defined]),
        }
    table = output_table(official, flag, flag_defined, version, version_defined, ranks, result)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("interaction artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    registry_path = ROOT / "config/ideas.json"
    registry_before = sha256_file(registry_path)
    flag_after, tm5_after, registry_after = sha256_file(flag_path), sha256_file(tm5_path), sha256_file(registry_path)
    frozen_unchanged = flag_after == FLAG_ARTIFACT_SHA and tm5_after == TM5_SHA and registry_after == registry_before
    if not frozen_unchanged:
        raise AssertionError("frozen flag/Tm5 artifacts or registry changed")
    coverage = float(joint.mean())
    bounded = bool(np.isfinite(np.asarray(result["interaction"])[joint]).all()
                   and np.all((np.asarray(result["interaction"])[joint] >= 0.)
                              & (np.asarray(result["interaction"])[joint] <= 1.)))
    coverage_pass = coverage >= MIN_COVERAGE
    p95_pass = flagged_p95 >= MIN_FLAGGED_P95_CHANGE
    exact_flags = len(actual_geoids) == 30 and sorted(actual_geoids) == sorted(expected_geoids)
    acceptance = {
        "exact_9379_id_alignment": table.num_rows == EXPECTED_N and len(set(official)) == EXPECTED_N,
        "exact_30_flag_alignment": exact_flags,
        "joint_defined_mask_equals_flag_and_version_masks": bool(np.array_equal(joint, flag_defined & version_defined)),
        "undefined_interactions_remain_null": bool(np.array_equal(np.isfinite(np.asarray(result["interaction"])), joint)),
        "joint_coverage_fraction": coverage, "coverage_at_least_98_59_percent": bool(coverage_pass),
        "interaction_finite_and_bounded_0_1": bounded,
        "flagged_ids_with_rank": int(flagged.sum()),
        "flagged_only_p95_absolute_change_from_binary_flag": flagged_p95,
        "flagged_only_p95_change_at_least_0_05": bool(p95_pass),
        "frozen_flag_Tm5_and_registry_unchanged": bool(frozen_unchanged),
        "screen_pass": bool(coverage_pass and exact_flags and bounded and p95_pass and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "screen": "Binary Tm2×Tm3 recovery flag × within-region POI p90 version midpercentile rank; unregistered label-free interaction sensitivity",
        "definition": "interaction = recovery_flag × within-region average-tie midpercentile of provided defined Tm5 POI p90 version. Flagged-only change = |interaction − recovery_flag|, measured only for the exact source flag=1 set.",
        "method": {"recovery_flag": "Reuse the frozen validated flag artifact and validate all 30 GEOIDs against the frozen source report; do not recompute or alter the flag.",
                   "poi_rank": "Rank only defined provided Tm5 Est covariate:tm5_poi_p90_version separately within each official region; ties receive average rank, normalized by (n−1), singleton at 0.5; undefined values remain null.",
                   "joint_mask": "Exact AND of source flag-defined and POI-version-defined masks; preserve all nulls.",
                   "unregistered": True},
        "measure_before": {"flagged_ids": 30, "flagged_regions": 4,
                           "flagged_only_p95_change_from_global_log_normalized": old_interaction["measure_after"]["flagged_only_p95_absolute_change_from_flag"],
                           "regional_rank_vs_global_log_transform_p95_absolute_change": rank_report["measure_after"]["p95_absolute_change_from_global_log_normalized"],
                           "flag_x_region_rank_previously_measured": False,
                           "source_flag_report_sha256": sha256_file(FLAG_REPORT),
                           "global_interaction_report_sha256": sha256_file(GLOBAL_INTERACTION_REPORT),
                           "region_rank_report_sha256": sha256_file(RANK_REPORT)},
        "scope": {"official_ids": len(official), "exact_official_id_alignment": True,
                  "flag_defined_ids": int(flag_defined.sum()), "poi_version_defined_ids": int(version_defined.sum()),
                  "joint_defined_ids": int(joint.sum()), "joint_undefined_ids": int((~joint).sum()),
                  "joint_coverage_fraction": coverage, "flagged_ids": int((flag_defined & (flag == 1.)).sum()),
                  "flagged_ids_with_defined_poi_rank": int(flagged.sum()), "regions": regional,
                  "labels_read": False, "sample_score_values_read": False, "Tm1_or_Tm4_read": False,
                  "removed_or_equivalent_reference_data_read": False, "external_data_read": False,
                  "leaderboard_feedback_used": False, "registry_integration": False, "uploads": 0},
        "measure_after": {"flagged_only_absolute_change_distribution": _distribution(flagged_change),
                          "flagged_only_p95_absolute_change_from_binary_flag": flagged_p95,
                          "joint_interaction_distribution": _distribution(np.asarray(result["interaction"])[joint]),
                          "regions": regional},
        "thresholds": {"minimum_joint_coverage_inclusive": MIN_COVERAGE,
                       "interaction_bounds_inclusive": [0., 1.],
                       "minimum_flagged_only_p95_absolute_change_inclusive": MIN_FLAGGED_P95_CHANGE},
        "acceptance": acceptance,
        "provenance": {"official_geoid_region_sha256": official_sha,
                       "source_flag_artifact": {"path": str(flag_path), "sha256_before": FLAG_ARTIFACT_SHA, "sha256_after": flag_after},
                       "source_flag_report_sha256": sha256_file(FLAG_REPORT),
                       "Tm5_artifact": {"path": str(tm5_path), "sha256_before": TM5_SHA, "sha256_after": tm5_after,
                                        "poi_p90_version_emission_sha256": feature_digest(official, version, version_defined)},
                       "registry_sha256_before": registry_before, "registry_sha256_after": registry_after,
                       "script_sha256": sha256_file(Path(__file__).resolve()),
                       "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                           "sha256": hashlib.sha256(payload).hexdigest(),
                                           "columns": table.column_names, "deterministic_bytes": True}},
        "interpretation": "The preregistered flagged-only region-rank interaction passes the requested coverage, mask, bounds and effect gates; retain only as an unregistered diagnostic, with no promotion or estimator change." if acceptance["screen_pass"] else
                         "One or more coverage, exact-flag-alignment, boundedness, or flagged-only effect gates failed; do not integrate the interaction.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm2-fire-flag-tm5-poi-region-rank-interaction.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "tm2-fire-flag-tm5-poi-region-rank-interaction" / "flag-region-rank-interaction.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "flagged_only_p95_absolute_change_from_binary_flag": report["measure_after"]["flagged_only_p95_absolute_change_from_binary_flag"],
                      "output_artifact": report["provenance"]["output_artifact"], "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
