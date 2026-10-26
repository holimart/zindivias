#!/usr/bin/env python3
"""Screen the validated binary Tm2/Tm3 recovery flag × frozen Mx4 F interaction."""
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
MIN_FLAGGED_CHANGE_P95 = 0.01
TM2_SHA = "470c23b2902cbb652dfc884c22ba2a743a85538aa819ddd179b1ff2a3aea41bc"
TM3_SHA = "502ff5d6c66628c97db82142a9ea935cc7b2817ff4cb8a9852b5e94d9b0b2940"
MX4_SHA = "8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def feature_digest(official: list[tuple[str, str]], values: np.ndarray,
                   defined: np.ndarray) -> str:
    rows = [f"{g},{r},{int(ok)}," + (format(float(v), ".17g") if ok else "NULL")
            for (g, r), v, ok in zip(official, values, defined)]
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def make_flag(gap: np.ndarray, gap_defined: np.ndarray, growth: np.ndarray,
              growth_defined: np.ndarray, growth_threshold: float = 0.10
              ) -> tuple[np.ndarray, np.ndarray]:
    gap = np.asarray(gap, dtype=np.float64)
    gap_mask = np.asarray(gap_defined, dtype=bool)
    growth = np.asarray(growth, dtype=np.float64)
    growth_mask = np.asarray(growth_defined, dtype=bool)
    if (gap.ndim != 1 or growth.shape != gap.shape or gap_mask.shape != gap.shape
            or growth_mask.shape != gap.shape):
        raise ValueError("Tm2/Tm3 values and defined masks must be aligned vectors")
    if not np.isfinite(growth_threshold):
        raise ValueError("growth threshold must be finite")
    if np.any(gap_mask & (~np.isfinite(gap) | (gap < 0.0) | (gap > 1.0))):
        raise ValueError("defined Tm2 gap must be finite and in [0,1]")
    if np.any(growth_mask & ~np.isfinite(growth)):
        raise ValueError("defined Tm3 growth must be finite")
    defined = gap_mask & growth_mask
    flag = np.zeros(gap.shape, dtype=np.int8)
    flag[defined] = ((gap[defined] > 0.0) & (growth[defined] >= growth_threshold)).astype(np.int8)
    return flag, defined


def interaction_values(flag: np.ndarray, flag_defined: np.ndarray,
                       mx4_f: np.ndarray, mx4_defined: np.ndarray
                       ) -> dict[str, np.ndarray | int]:
    flag = np.asarray(flag)
    flag_mask = np.asarray(flag_defined, dtype=bool)
    mx4 = np.asarray(mx4_f, dtype=np.float64)
    mx4_mask = np.asarray(mx4_defined, dtype=bool)
    if flag.ndim != 1 or any(v.shape != flag.shape for v in (flag_mask, mx4, mx4_mask)):
        raise ValueError("flag/Mx4 values and masks must align as vectors")
    if np.any(flag_mask & ~np.isin(flag, (0, 1))):
        raise ValueError("defined recovery flags must be binary")
    if np.any(~flag_mask & np.isfinite(flag.astype(np.float64))):
        raise ValueError("undefined recovery flag slots must remain null/NaN")
    if np.any(mx4_mask & ~np.isfinite(mx4)):
        raise ValueError("defined Mx4 F values must be finite")
    joint = flag_mask & mx4_mask
    clipped = np.full(mx4.shape, np.nan, dtype=np.float64)
    clipped[mx4_mask] = np.clip(mx4[mx4_mask], 0.0, 1.0)
    interaction = np.full(mx4.shape, np.nan, dtype=np.float64)
    interaction[joint] = flag[joint].astype(np.float64) * clipped[joint]
    change = np.full(mx4.shape, np.nan, dtype=np.float64)
    change[joint] = np.abs(interaction[joint] - flag[joint].astype(np.float64))
    if np.any(joint & (~np.isfinite(interaction) | (interaction < 0.0) | (interaction > 1.0))):
        raise ValueError("defined recovery-flag interaction must be finite and in [0,1]")
    return {"clipped_mx4": clipped, "interaction": interaction, "change": change,
            "defined": joint, "mx4_clip_count": int(np.count_nonzero(mx4_mask & ((mx4 < 0.0) | (mx4 > 1.0))))}


def read_emission(path: Path, expected_sha: str, idea: str, component: str, channel: str,
                  official: list[tuple[str, str]]) -> tuple[np.ndarray, np.ndarray]:
    if not path.is_file() or sha256_file(path) != expected_sha:
        raise ValueError(f"{idea} frozen artifact is missing or differs from audited hash")
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "component", "channel", "value", "defined"],
                          filters=[("idea", "=", idea), ("component", "=", component), ("channel", "=", channel)])
    if table.num_rows != len(official):
        raise ValueError(f"{idea}/{component}/{channel}: expected exactly one row per official GEOID")
    positions = {pair: i for i, pair in enumerate(official)}
    values = np.full(len(official), np.nan, dtype=np.float64)
    defined = np.zeros(len(official), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in positions or pair in seen:
            raise ValueError(f"unexpected/duplicate {idea} row {pair}")
        if (row["idea"], row["component"], row["channel"]) != (idea, component, channel):
            raise ValueError("frozen emission metadata mismatch")
        seen.add(pair)
        if bool(row["defined"]):
            if row["value"] is None or not np.isfinite(float(row["value"])):
                raise ValueError(f"defined {idea} emission is null/nonfinite at {pair[0]}")
            values[positions[pair]] = float(row["value"])
            defined[positions[pair]] = True
    if seen != set(official):
        raise ValueError(f"{idea} selected emission does not reconcile to official IDs")
    return values, defined


def read_flag_artifact(path: Path, expected_sha: str, official: list[tuple[str, str]]) -> tuple[np.ndarray, np.ndarray, str]:
    if not path.is_file() or sha256_file(path) != expected_sha:
        raise ValueError("validated binary recovery artifact hash mismatch")
    table = pq.read_table(path, columns=["GEOID", "region", "recovery_flag", "recovery_flag_defined"])
    if table.num_rows != len(official):
        raise ValueError("recovery flag artifact must have one row per official ID")
    positions = {pair: i for i, pair in enumerate(official)}
    flags = np.full(len(official), np.nan, dtype=np.float64)
    defined = np.zeros(len(official), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in positions or pair in seen:
            raise ValueError(f"unexpected or duplicate recovery flag row {pair}")
        seen.add(pair)
        index = positions[pair]
        defined[index] = bool(row["recovery_flag_defined"])
        raw = row["recovery_flag"]
        if defined[index]:
            if raw is None or int(raw) not in (0, 1):
                raise ValueError(f"defined recovery flag must be binary for {pair[0]}")
            flags[index] = int(raw)
        elif raw is not None:
            raise ValueError(f"undefined recovery flags must be null for {pair[0]}")
    if seen != set(official):
        raise ValueError("recovery artifact ID partition differs from official GEOIDs")
    return flags, defined, expected_sha


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def output_table(official: list[tuple[str, str]], gap: np.ndarray, gap_defined: np.ndarray,
                 growth: np.ndarray, growth_defined: np.ndarray, flag: np.ndarray,
                 flag_defined: np.ndarray, mx4: np.ndarray, mx4_defined: np.ndarray,
                 result: dict[str, Any]) -> pa.Table:
    n = len(official)
    vectors = (gap, gap_defined, growth, growth_defined, flag, flag_defined, mx4, mx4_defined,
               result["clipped_mx4"], result["interaction"], result["change"], result["defined"])
    if any(np.asarray(v).shape != (n,) for v in vectors):
        raise ValueError("output arrays must align to exact official IDs")
    joint = np.asarray(result["defined"], dtype=bool)
    if not np.array_equal(joint, np.asarray(flag_defined, bool) & np.asarray(mx4_defined, bool)):
        raise ValueError("interaction definedness must preserve joint Tm2/Tm3/Mx4 mask")
    ids, regions = zip(*official)
    return pa.table({
        "GEOID": pa.array(ids, type=pa.string()), "region": pa.array(regions, type=pa.string()),
        "tm2_building_gap": pa.array([float(v) if ok else None for v, ok in zip(gap, gap_defined)], type=pa.float64()),
        "tm2_defined": pa.array(gap_defined, type=pa.bool_()),
        "tm3_acs_housing_growth": pa.array([float(v) if ok else None for v, ok in zip(growth, growth_defined)], type=pa.float64()),
        "tm3_defined": pa.array(growth_defined, type=pa.bool_()),
        "recovery_flag": pa.array([int(v) if ok else None for v, ok in zip(flag, flag_defined)], type=pa.int8()),
        "recovery_flag_defined": pa.array(flag_defined, type=pa.bool_()),
        "mx4_f_source_share": pa.array([float(v) if ok else None for v, ok in zip(mx4, mx4_defined)], type=pa.float64()),
        "mx4_defined": pa.array(mx4_defined, type=pa.bool_()),
        "mx4_f_clipped": pa.array([float(v) if ok else None for v, ok in zip(result["clipped_mx4"], mx4_defined)], type=pa.float64()),
        "interaction": pa.array([float(v) if ok else None for v, ok in zip(result["interaction"], joint)], type=pa.float64()),
        "absolute_change_from_binary_flag": pa.array([float(v) if ok else None for v, ok in zip(result["change"], joint)], type=pa.float64()),
        "interaction_defined": pa.array(joint, type=pa.bool_()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    baseline_path = ROOT / "reports/tm2-fire-recovery-flag.json"
    baseline_sha = sha256_file(baseline_path)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if (baseline["scope"]["official_ids"] != 9379 or baseline["scope"]["flagged_ids"] != 30
            or baseline["scope"]["joint_defined_ids"] != 9362 or baseline["scope"]["flagged_regions"] != 4
            or not baseline["acceptance"]["acceptance_pass"]):
        raise ValueError("source recovery-flag screen no longer matches its validated 30-ID/four-region baseline")
    expected_frozen = {"Tm2": baseline["provenance"]["frozen_inputs"]["Tm2"]["sha256"],
                       "Tm3": baseline["provenance"]["frozen_inputs"]["Tm3"]["sha256"]}
    if expected_frozen != {"Tm2": TM2_SHA, "Tm3": TM3_SHA}:
        raise ValueError("source Tm2/Tm3 hashes differ from frozen baseline")
    flag_path = workspace / "tm2-fire-recovery-flag" / "tm2-fire-recovery-flag.parquet"
    expected_flag_sha = baseline["output_artifact"]["sha256"]
    if sha256_file(flag_path) != expected_flag_sha:
        raise ValueError("source binary recovery artifact differs from baseline report")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != 9379 or len(set(official)) != 9379 or len({g for g, _ in official}) != 9379
            or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official GEOIDs must be an exact 9,379-ID four-region partition")
    official_sha = hashlib.sha256(json.dumps(official, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    if official_sha != baseline["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official GEOID-region partition differs from recovery flag source report")

    tm2_path, tm3_path = workspace / "ideas/Tm2.parquet", workspace / "ideas/Tm3.parquet"
    mx4_path = workspace / "ideas/Mx4.parquet"
    registry_path, candidates_path = ROOT / "config/ideas.json", ROOT / "reports/candidates.json"
    registry_before, candidates_before = sha256_file(registry_path), sha256_file(candidates_path)
    tm2_gap, tm2_defined = read_emission(tm2_path, TM2_SHA, "Tm2", "B", "gap", official)
    tm3_growth, tm3_defined = read_emission(tm3_path, TM3_SHA, "Tm3", "B", "covariate:acs_housing_growth", official)
    mx4, mx4_defined = read_emission(mx4_path, MX4_SHA, "Mx4", "F", "covariate", official)
    if int(tm2_defined.sum()) != 9377 or int(tm3_defined.sum()) != 9362 or int(mx4_defined.sum()) != 9379:
        raise ValueError("frozen Tm2/Tm3/Mx4 defined counts differ from audited baseline")
    derived_flag, derived_defined = make_flag(tm2_gap, tm2_defined, tm3_growth, tm3_defined)
    recorded_flag, recorded_defined, _ = read_flag_artifact(flag_path, expected_flag_sha, official)
    if not np.array_equal(derived_defined, recorded_defined) or not np.array_equal(derived_flag[derived_defined], recorded_flag[recorded_defined]):
        raise ValueError("recomputed Tm2/Tm3 flags do not exactly reproduce the completed flag artifact")
    if int(recorded_defined.sum()) != 9362 or int((recorded_flag[recorded_defined] == 1).sum()) != 30:
        raise ValueError("recovery flag artifact changed from the exact 9,362/30 baseline")
    baseline_flag_geoids = [g for region in REGIONS for g in baseline["measure_after"]["regions"][region]["flagged_geoids"]]
    observed_flag_geoids = [g for (g, _), val, ok in zip(official, recorded_flag, recorded_defined) if ok and val == 1]
    if sorted(baseline_flag_geoids) != sorted(observed_flag_geoids) or len(set(observed_flag_geoids)) != 30:
        raise ValueError("all 30 flagged GEOIDs must align exactly with the completed source report")

    result = interaction_values(recorded_flag, recorded_defined, mx4, mx4_defined)
    joint = np.asarray(result["defined"], dtype=bool)
    flagged = joint & (recorded_flag == 1)
    changes_flagged = np.asarray(result["change"])[flagged]
    if int(flagged.sum()) != 30 or changes_flagged.size != 30:
        raise ValueError("flagged interaction support must contain the exact 30 source flags")
    p95_change = float(np.quantile(changes_flagged, 0.95))
    regional: dict[str, Any] = {}
    for region in REGIONS:
        mask = np.asarray([r == region for _, r in official], dtype=bool)
        local_defined = joint & mask
        local_flagged = flagged & mask
        local_changes = np.asarray(result["change"])[local_flagged]
        regional[region] = {
            "official_ids": int(mask.sum()), "tm2_defined_ids": int((tm2_defined & mask).sum()),
            "tm3_defined_ids": int((tm3_defined & mask).sum()), "mx4_defined_ids": int((mx4_defined & mask).sum()),
            "joint_defined_ids": int(local_defined.sum()), "flagged_ids": int(local_flagged.sum()),
            "undefined_ids": int(np.count_nonzero(mask & ~joint)),
            "flagged_geoid_list": [g for (g, r), yes in zip(official, local_flagged) if r == region and yes],
            "flagged_support_absolute_change_distribution": {
                "n": int(local_changes.size),
                "p50": float(np.quantile(local_changes, .5)) if local_changes.size else None,
                "p95": float(np.quantile(local_changes, .95)) if local_changes.size else None,
                "max": float(local_changes.max()) if local_changes.size else None,
            },
        }
    table = output_table(official, tm2_gap, tm2_defined, tm3_growth, tm3_defined,
                         recorded_flag, recorded_defined, mx4, mx4_defined, result)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("interaction artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    tm2_after, tm3_after, mx4_after = sha256_file(tm2_path), sha256_file(tm3_path), sha256_file(mx4_path)
    flag_after = sha256_file(flag_path)
    registry_after, candidates_after = sha256_file(registry_path), sha256_file(candidates_path)
    frozen_unchanged = ({"Tm2": tm2_after, "Tm3": tm3_after, "Mx4": mx4_after}
                        == {"Tm2": TM2_SHA, "Tm3": TM3_SHA, "Mx4": MX4_SHA}
                        and flag_after == expected_flag_sha and registry_after == registry_before
                        and candidates_after == candidates_before)
    if not frozen_unchanged:
        raise AssertionError("frozen features, source diagnostic, candidates or registry changed")
    exact_ids = table.num_rows == 9379 and len(set(official)) == 9379
    bounded = bool(np.isfinite(np.asarray(result["interaction"])[joint]).all()
                   and np.all((np.asarray(result["interaction"])[joint] >= 0.0)
                              & (np.asarray(result["interaction"])[joint] <= 1.0)))
    acceptance = {
        "exact_9379_id_alignment": exact_ids,
        "all_30_source_flags_align_exactly": sorted(baseline_flag_geoids) == sorted(observed_flag_geoids),
        "joint_Tm2_Tm3_Mx4_defined_mask_preserved": bool(np.array_equal(joint, derived_defined & mx4_defined)),
        "undefined_interactions_remain_null": bool(np.all(np.isnan(np.asarray(result["interaction"])[~joint]))),
        "interaction_finite_and_bounded_0_1": bounded,
        "flagged_support_p95_absolute_change": p95_change,
        "flagged_support_p95_change_at_least_0_01": p95_change >= MIN_FLAGGED_CHANGE_P95,
        "frozen_Tm2_Tm3_Mx4_flag_artifact_registry_unchanged": frozen_unchanged,
        "distinct_interaction_screen_pass": bool(exact_ids and p95_change >= MIN_FLAGGED_CHANGE_P95 and bounded and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "screen": "Tm2/Tm3 binary material-recovery flag × Mx4 F; unregistered label-free interaction sensitivity",
        "definition": "On the exact joint-defined Tm2/Tm3/Mx4 F support, interaction = recovery_flag * clip(Mx4_F,0,1). Recovery flag is 1 iff Tm2 gap >0 and Tm3 ACS housing growth >=0.10; otherwise 0. Undefined unless both Tm2 and Tm3 define the flag and Mx4 F is defined.",
        "method": {
            "source_flag": "Recompute and validate the completed sparse Tm2/Tm3 flag artifact; require exact 30 flagged GEOIDs and exact defined mask.",
            "change_metric": "For the 30 flagged IDs, absolute change = |interaction - binary recovery flag|; report p95 and regional distributions. Zero-flag jointly-defined rows have zero interaction and no flagged-support change observation.",
            "Mx4_handling": "Clip frozen Mx4 F to [0,1] before multiplication; record clipping count; do not impute nulls.",
            "not_promoted": True,
        },
        "measure_before": {
            "validated_flagged_ids": 30, "flagged_regions": 4,
            "joint_Tm2_Tm3_defined_ids": 9362,
            "flag_interaction_change_previously_measured": False,
            "source_flag_report_sha256": baseline_sha,
            "source_flag_report": "reports/tm2-fire-recovery-flag.json",
        },
        "scope": {
            "official_ids": len(official), "exact_official_geoid_region_partition": exact_ids,
            "Tm2_defined_ids": int(tm2_defined.sum()), "Tm3_defined_ids": int(tm3_defined.sum()),
            "Mx4_F_defined_ids": int(mx4_defined.sum()), "joint_defined_ids": int(joint.sum()),
            "joint_undefined_ids": int(len(official) - joint.sum()),
            "flagged_ids_on_joint_support": int(flagged.sum()), "coverage_fraction": float(joint.mean()),
            "labels_read": False, "sample_score_values_read": False,
            "Tm1_or_Tm4_inputs_read": False, "removed_or_equivalent_reference_data_read": False,
            "external_data_read": False, "leaderboard_feedback_used": False,
            "frozen_Tm2_Tm3_Mx4_modified": False, "registry_integration": False, "uploads": 0,
        },
        "measure_after": {
            "flagged_support_absolute_change_distribution": {
                "n": int(changes_flagged.size), "min": float(changes_flagged.min()),
                "p50": float(np.quantile(changes_flagged, .5)), "p95": p95_change,
                "max": float(changes_flagged.max()), "mean": float(changes_flagged.mean()),
            },
            "joint_interaction_distribution": {
                "n": int(joint.sum()), "min": float(np.asarray(result["interaction"])[joint].min()),
                "p50": float(np.quantile(np.asarray(result["interaction"])[joint], .5)),
                "p95": float(np.quantile(np.asarray(result["interaction"])[joint], .95)),
                "max": float(np.asarray(result["interaction"])[joint].max()),
            },
            "Mx4_clipped_id_count": int(result["mx4_clip_count"]),
            "regions": regional,
        },
        "thresholds": {"minimum_flagged_support_p95_absolute_change": MIN_FLAGGED_CHANGE_P95,
                       "interaction_bounds_inclusive": [0.0, 1.0]},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_sha,
            "source_flag_artifact": {"path": str(flag_path), "sha256_before": expected_flag_sha, "sha256_after": flag_after},
            "frozen_inputs": {
                "Tm2": {"path": str(tm2_path), "sha256_before": TM2_SHA, "sha256_after": tm2_after,
                        "gap_emission_sha256": feature_digest(official, tm2_gap, tm2_defined)},
                "Tm3": {"path": str(tm3_path), "sha256_before": TM3_SHA, "sha256_after": tm3_after,
                        "acs_housing_growth_emission_sha256": feature_digest(official, tm3_growth, tm3_defined)},
                "Mx4": {"path": str(mx4_path), "sha256_before": MX4_SHA, "sha256_after": mx4_after,
                        "F_emission_sha256": feature_digest(official, mx4, mx4_defined)},
            },
            "registry_sha256_before": registry_before, "registry_sha256_after": registry_after,
            "candidate_manifest_sha256_before": candidates_before, "candidate_manifest_sha256_after": candidates_after,
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "columns": table.column_names, "deterministic_bytes": True},
        },
        "interpretation": "The flagged-support p95 change clears 0.01; retain only as an unregistered label-free sensitivity and leave frozen Tm2/Tm3/Mx4, registry, and candidates unchanged." if acceptance["distinct_interaction_screen_pass"] else
        "The flagged-support p95 change is below 0.01 or another invariant failed; retain the binary recovery flag alone and leave frozen inputs unchanged.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm2-recovery-flag-mx4-interaction.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE is required and must exist")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "tm2-recovery-flag-mx4-interaction" / "recovery-flag-mx4-interaction.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "measure_after": report["measure_after"],
                      "output_artifact": report["provenance"]["output_artifact"],
                      "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
