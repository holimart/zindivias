#!/usr/bin/env python3
"""Screen a sparse Tm2-gap/Tm3-ACS-growth binary flag without modifying frozen features."""
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
GROWTH_THRESHOLD = 0.10
MIN_FLAGGED_IDS = 20
MIN_FLAGGED_REGIONS = 2


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def emission_digest(official: list[tuple[str, str]], values: np.ndarray,
                    defined: np.ndarray) -> str:
    rows = [f"{g},{r},{int(ok)}," + (format(float(v), ".17g") if ok else "NULL")
            for (g, r), v, ok in zip(official, values, defined)]
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def make_recovery_flag(gap: np.ndarray, gap_defined: np.ndarray,
                       growth: np.ndarray, growth_defined: np.ndarray,
                       threshold: float = GROWTH_THRESHOLD) -> tuple[np.ndarray, np.ndarray]:
    """Flag jointly-defined IDs iff Tm2 gap > 0 and Tm3 housing growth >= threshold."""
    gap = np.asarray(gap, dtype=np.float64)
    gap_mask = np.asarray(gap_defined, dtype=bool)
    growth = np.asarray(growth, dtype=np.float64)
    growth_mask = np.asarray(growth_defined, dtype=bool)
    if (gap.ndim != 1 or growth.ndim != 1 or gap.shape != growth.shape
            or gap_mask.shape != gap.shape or growth_mask.shape != gap.shape):
        raise ValueError("Tm2/Tm3 values and defined masks must be aligned one-dimensional arrays")
    if not np.isfinite(threshold):
        raise ValueError("housing growth threshold must be finite")
    if np.any(gap_mask & (~np.isfinite(gap) | (gap < 0.0) | (gap > 1.0))):
        raise ValueError("defined Tm2 building gaps must be finite and in [0,1]")
    if np.any(growth_mask & ~np.isfinite(growth)):
        raise ValueError("defined Tm3 ACS housing growth must be finite")
    joint = gap_mask & growth_mask
    flag = np.zeros(gap.shape, dtype=np.int8)
    flag[joint] = ((gap[joint] > 0.0) & (growth[joint] >= threshold)).astype(np.int8)
    if np.any((flag[joint] != 0) & (flag[joint] != 1)) or np.any(flag[~joint] != 0):
        raise AssertionError("recovery flags must be binary and undefined slots stay internal zero")
    return flag, joint


def sparse_flag_gate(region_counts: dict[str, int], *, min_flags: int = MIN_FLAGGED_IDS,
                     min_regions: int = MIN_FLAGGED_REGIONS) -> dict[str, Any]:
    if any(int(count) < 0 for count in region_counts.values()):
        raise ValueError("regional flag counts cannot be negative")
    total = int(sum(region_counts.values()))
    active = int(sum(int(count) > 0 for count in region_counts.values()))
    return {"flagged_ids": total, "flagged_regions": active,
            "pass": total >= min_flags and active >= min_regions}


def read_emission(path: Path, *, expected_sha256: str, idea: str,
                  component: str, channel: str,
                  official: list[tuple[str, str]]) -> tuple[np.ndarray, np.ndarray, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = sha256_file(path)
    if digest != expected_sha256:
        raise ValueError(f"{idea} frozen artifact SHA differs from the prior measured report")
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "component", "channel", "value", "defined"],
                          filters=[("idea", "=", idea), ("component", "=", component), ("channel", "=", channel)])
    rows = table.to_pylist()
    if len(rows) != len(official):
        raise ValueError(f"{idea}/{component}/{channel}: expected one row per official GEOID")
    pairs = [(str(row["GEOID"]), str(row["region"])) for row in rows]
    if pairs != official:
        raise ValueError(f"{idea}/{component}/{channel}: emission does not exactly match official GEOID-region order")
    values = np.full(len(rows), np.nan, dtype=np.float64)
    defined = np.zeros(len(rows), dtype=bool)
    for i, row in enumerate(rows):
        if (row["idea"], row["component"], row["channel"]) != (idea, component, channel):
            raise ValueError("selected emission metadata differs from requested feature")
        if bool(row["defined"]):
            if row["value"] is None:
                raise ValueError(f"defined {idea} value is null at {pairs[i][0]}")
            values[i] = float(row["value"])
            defined[i] = True
            if not np.isfinite(values[i]):
                raise ValueError(f"defined {idea} value is nonfinite at {pairs[i][0]}")
        # Undefined source placeholders are ignored; only the explicit defined mask is authoritative.
    return values, defined, digest


def output_table(official: list[tuple[str, str]], gap: np.ndarray, gap_defined: np.ndarray,
                 growth: np.ndarray, growth_defined: np.ndarray, fire_share: np.ndarray,
                 fire_defined: np.ndarray, flag: np.ndarray, flag_defined: np.ndarray) -> pa.Table:
    n = len(official)
    arrays = [np.asarray(x) for x in (gap, gap_defined, growth, growth_defined, fire_share,
                                      fire_defined, flag, flag_defined)]
    if any(x.shape != (n,) for x in arrays):
        raise ValueError("output values and masks must align to the official GEOID list")
    if not np.array_equal(flag_defined, gap_defined & growth_defined):
        raise ValueError("flag must be defined exactly on jointly-defined Tm2/Tm3 IDs")
    gap, gap_mask, growth, growth_mask, fire_share, fire_mask, flag, flag_mask = arrays
    if np.any(flag_mask & ~np.isin(flag, (0, 1))):
        raise ValueError("defined recovery flag must be binary")
    if np.any(flag_mask & ((flag != ((gap > 0.0) & (growth >= GROWTH_THRESHOLD)).astype(np.int8)) | ~gap_mask | ~growth_mask)):
        raise ValueError("defined flag does not follow the declared threshold rule")
    if np.any(fire_mask & (~np.isfinite(fire_share) | (fire_share < 0.0) | (fire_share > 1.0))):
        raise ValueError("defined Tm2 fire share must be finite and in [0,1]")
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "tm2_building_gap": pa.array([float(v) if d else None for v, d in zip(gap, gap_mask)], type=pa.float64()),
        "tm2_defined": pa.array(gap_mask, type=pa.bool_()),
        "tm3_acs_housing_growth": pa.array([float(v) if d else None for v, d in zip(growth, growth_mask)], type=pa.float64()),
        "tm3_defined": pa.array(growth_mask, type=pa.bool_()),
        "tm2_postfire_burn_share": pa.array([float(v) if d else None for v, d in zip(fire_share, fire_mask)], type=pa.float64()),
        "fire_status_defined": pa.array(fire_mask, type=pa.bool_()),
        "fire_affected": pa.array(fire_mask & (fire_share > 0.0), type=pa.bool_()),
        "recovery_flag": pa.array([int(v) if d else None for v, d in zip(flag, flag_mask)], type=pa.int8()),
        "recovery_flag_defined": pa.array(flag_mask, type=pa.bool_()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    baseline_path = ROOT / "reports/tm2-fire-recovery-log-scale.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_fire_p95 = baseline["acceptance"]["fire_conditional_p95_absolute_change"]
    baseline_fire_count = baseline["scope"]["fire_affected_tm2_defined_ids"]
    if (not np.isclose(baseline_fire_p95, 0.007985380024531023, rtol=0, atol=1e-15)
            or baseline_fire_count != 381 or baseline["scope"]["official_ids"] != 9379):
        raise ValueError("log-scale before-measure no longer matches the issued 0.00798538 / 381 baseline")

    registry_path = ROOT / "config/ideas.json"
    candidate_path = ROOT / "reports/candidates.json"
    registry_sha_before = sha256_file(registry_path)
    candidates_sha_before = sha256_file(candidate_path)
    official = official_scored_pairs(workspace)
    if (len(official) != 9379 or len(set(official)) != 9379
            or len({g for g, _ in official}) != 9379 or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official scored IDs must be an exact 9,379-ID GEOID-region partition")
    official_hash = hashlib.sha256(json.dumps(official, ensure_ascii=False,
                                               separators=(",", ":")).encode("utf-8")).hexdigest()
    expected_official_hash = baseline["provenance"]["official_geoid_region_sha256"]
    if official_hash != expected_official_hash:
        raise ValueError("official GEOID-region partition differs from prior fire-recovery audit")

    paths = {idea: workspace / "ideas" / f"{idea}.parquet" for idea in ("Tm2", "Tm3")}
    expected_hashes = {idea: baseline["provenance"]["frozen_inputs"][idea]["sha256"] for idea in paths}
    gap, gap_defined, tm2_sha = read_emission(paths["Tm2"], expected_sha256=expected_hashes["Tm2"],
                                              idea="Tm2", component="B", channel="gap", official=official)
    fire_share, fire_defined, tm2_fire_sha = read_emission(
        paths["Tm2"], expected_sha256=expected_hashes["Tm2"], idea="Tm2", component="F",
        channel="covariate:postfire_burn_share", official=official)
    growth, growth_defined, tm3_sha = read_emission(
        paths["Tm3"], expected_sha256=expected_hashes["Tm3"], idea="Tm3", component="B",
        channel="covariate:acs_housing_growth", official=official)
    if tm2_sha != tm2_fire_sha or tm2_sha != expected_hashes["Tm2"] or tm3_sha != expected_hashes["Tm3"]:
        raise AssertionError("frozen Tm2/Tm3 artifact hash changed between selected emission reads")
    if not np.array_equal(gap_defined, fire_defined):
        raise ValueError("Tm2 gap and postfire-status masks do not align")
    if int(gap_defined.sum()) != 9377 or int(growth_defined.sum()) != 9362:
        raise ValueError("Tm2/Tm3 source definedness differs from the registered baseline")
    if int((fire_defined & (fire_share > 0.0)).sum()) != baseline_fire_count:
        raise ValueError("current Tm2 postfire-positive count differs from baseline")

    flag, flag_defined = make_recovery_flag(gap, gap_defined, growth, growth_defined)
    expected_defined = gap_defined & growth_defined
    if not np.array_equal(flag_defined, expected_defined):
        raise AssertionError("flag definedness must equal exact Tm2/Tm3 intersection")
    if int(flag_defined.sum()) != 9362:
        raise ValueError("joint Tm2/Tm3 flag coverage differs from expected 9,362 IDs")
    flagged = flag_defined & (flag == 1)
    if np.any(flagged & (~gap_defined | ~growth_defined | (gap <= 0.0) | (growth < GROWTH_THRESHOLD))):
        raise AssertionError("flagged row violates Tm2/Tm3 rule")
    if np.any(flag_defined & ~np.isin(flag, (0, 1))):
        raise AssertionError("defined output flag is not binary")
    region_reports: dict[str, Any] = {}
    for region in REGIONS:
        local = np.asarray([r == region for _, r in official], dtype=bool)
        local_defined = flag_defined & local
        local_flagged = flagged & local
        region_reports[region] = {
            "official_ids": int(local.sum()),
            "tm2_gap_defined_ids": int((gap_defined & local).sum()),
            "tm3_growth_defined_ids": int((growth_defined & local).sum()),
            "joint_defined_ids": int(local_defined.sum()),
            "flagged_ids": int(local_flagged.sum()),
            "unflagged_joint_ids": int(np.count_nonzero(local_defined & (flag == 0))),
            "undefined_ids": int(np.count_nonzero(local & ~flag_defined)),
            "fire_affected_ids": int(np.count_nonzero(local & fire_defined & (fire_share > 0.0))),
            "flagged_fire_affected_ids": int(np.count_nonzero(local_flagged & fire_defined & (fire_share > 0.0))),
            "flagged_geoids": [g for (g, r), yes in zip(official, local_flagged) if r == region and yes],
        }

    table = output_table(official, gap, gap_defined, growth, growth_defined,
                         fire_share, fire_defined, flag, flag_defined)
    payload = pq_bytes(table)
    if payload != pq_bytes(table):
        raise ValueError("binary recovery artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    registry_sha_after = sha256_file(registry_path)
    candidates_sha_after = sha256_file(candidate_path)
    frozen_after = {idea: sha256_file(path) for idea, path in paths.items()}
    frozen_unchanged = frozen_after == expected_hashes
    if not frozen_unchanged or registry_sha_before != registry_sha_after or candidates_sha_before != candidates_sha_after:
        raise AssertionError("screen modified frozen Tm2/Tm3, registry, or candidate artifacts")

    flagged_count = int(flagged.sum())
    flagged_fire_affected_count = int(np.count_nonzero(flagged & fire_defined & (fire_share > 0.0)))
    gate = sparse_flag_gate({region: details["flagged_ids"] for region, details in region_reports.items()})
    flagged_regions = gate["flagged_regions"]
    acceptance = {
        "exact_9379_official_geoid_region_alignment": table.num_rows == 9379 and len(set(official)) == 9379,
        "flag_defined_exactly_on_joint_Tm2_Tm3_mask": bool(np.array_equal(flag_defined, gap_defined & growth_defined)),
        "defined_flags_are_binary_and_match_threshold_rule": bool(np.all(np.isin(flag[flag_defined], (0, 1)))
                                                                  and not np.any(flagged & ((gap <= 0) | (growth < GROWTH_THRESHOLD)))),
        "all_undefined_flags_remain_null": int((~flag_defined).sum()) == int(table["recovery_flag"].null_count),
        "at_least_20_flags_across_at_least_two_regions": gate["pass"] and gate["flagged_ids"] == flagged_count,
        "frozen_Tm2_Tm3_registry_and_candidates_unchanged": frozen_unchanged and registry_sha_before == registry_sha_after and candidates_sha_before == candidates_sha_after,
    }
    acceptance["acceptance_pass"] = all(acceptance.values())
    report = {
        "schema_version": 1,
        "screen": "Tm2 positive building-gap and Tm3 ACS housing-growth >=10% binary recovery flag; label-free diagnostic only",
        "definition": "On jointly-defined IDs: recovery_flag=1 iff Tm2 building gap > 0 and Tm3 ACS housing growth >= 0.10; otherwise 0. Undefined unless both source emissions are defined. Tm2 postfire-burn-share is recorded only for context and is not an additional flag condition.",
        "thresholds": {"tm2_building_gap_exclusive_minimum": 0.0,
                       "tm3_acs_housing_growth_inclusive_minimum": GROWTH_THRESHOLD,
                       "minimum_flagged_ids": MIN_FLAGGED_IDS,
                       "minimum_regions_with_flags": MIN_FLAGGED_REGIONS},
        "measure_before": {
            "log_scaled_fire_conditional_p95_absolute_change": baseline_fire_p95,
            "log_screen_threshold": baseline["acceptance"]["minimum_fire_conditional_p95_absolute_change"],
            "log_adjustment_effect_gate_passed": baseline["acceptance"]["fire_conditional_p95_at_least_0_01"],
            "tm2_postfire_burn_share_positive_ids": baseline_fire_count,
            "log_scale_baseline_report_sha256": sha256_file(baseline_path),
        },
        "scope": {
            "official_ids": len(official), "exact_official_id_alignment": True,
            "tm2_gap_defined_ids": int(gap_defined.sum()),
            "tm3_growth_defined_ids": int(growth_defined.sum()),
            "joint_defined_ids": int(flag_defined.sum()),
            "flagged_ids": flagged_count,
            "flagged_fire_affected_ids": flagged_fire_affected_count,
            "flagged_ids_outside_fire_affected": int(flagged_count - flagged_fire_affected_count),
            "unflagged_joint_ids": int(np.count_nonzero(flag_defined & (flag == 0))),
            "undefined_ids": int((~flag_defined).sum()),
            "coverage_fraction": float(flag_defined.mean()),
            "fire_status_defined_ids": int(fire_defined.sum()),
            "tm2_postfire_burn_share_positive_ids": int((fire_defined & (fire_share > 0.0)).sum()),
            "flagged_regions": int(flagged_regions),
            "labels_read": False, "sample_score_values_read": False,
            "Tm1_or_Tm4_inputs_read": False, "removed_or_equivalent_reference_data_read": False,
            "leaderboard_values_used": False, "candidate_or_registry_modified": False,
            "frozen_Tm2_or_Tm3_modified": False, "uploads": 0,
        },
        "measure_after": {"flagged_ids": flagged_count,
                          "flagged_fire_affected_ids": flagged_fire_affected_count,
                          "flagged_ids_outside_fire_affected": int(flagged_count - flagged_fire_affected_count),
                          "flagged_regions": int(flagged_regions),
                          "regions": region_reports},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_hash,
            "source_fire_log_scale_report_sha256": sha256_file(baseline_path),
            "frozen_inputs": {
                "Tm2": {"path": str(paths["Tm2"]), "sha256": tm2_sha, "sha256_after": frozen_after["Tm2"],
                        "gap_emission_sha256": emission_digest(official, gap, gap_defined),
                        "fire_share_emission_sha256": emission_digest(official, fire_share, fire_defined)},
                "Tm3": {"path": str(paths["Tm3"]), "sha256": tm3_sha, "sha256_after": frozen_after["Tm3"],
                        "acs_housing_growth_emission_sha256": emission_digest(official, growth, growth_defined)},
            },
            "registry_sha256_before": registry_sha_before, "registry_sha256_after": registry_sha_after,
            "candidate_manifest_sha256_before": candidates_sha_before,
            "candidate_manifest_sha256_after": candidates_sha_after,
            "script_sha256": sha256_file(Path(__file__).resolve()),
        },
        "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "columns": table.column_names, "deterministic_bytes": True},
        "interpretation": "The flag meets its sparse diagnostic screen and may be retained only as an unregistered label-free sensitivity; frozen Tm2/Tm3, candidates, and registry remain unchanged." if acceptance["acceptance_pass"] else
        "The flag does not meet the sparse diagnostic acceptance rule; retain frozen Tm2/Tm3 unchanged and do not promote or integrate the flag.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def pq_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm2-fire-recovery-flag.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "tm2-fire-recovery-flag" / "tm2-fire-recovery-flag.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "measure_after": {"flagged_ids": report["measure_after"]["flagged_ids"],
                                        "flagged_fire_affected_ids": report["measure_after"]["flagged_fire_affected_ids"],
                                        "flagged_regions": report["measure_after"]["flagged_regions"],
                                        "regions": {r: {"flagged_ids": d["flagged_ids"],
                                                        "flagged_geoids": d["flagged_geoids"]}
                                                    for r, d in report["measure_after"]["regions"].items()}},
                      "output_artifact": report["output_artifact"],
                      "report": str(report_path.relative_to(ROOT))}, indent=2, sort_keys=True))
    return 0 if report["acceptance"]["acceptance_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
