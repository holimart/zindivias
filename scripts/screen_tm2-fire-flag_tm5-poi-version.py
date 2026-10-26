#!/usr/bin/env python3
"""Screen binary Tm2/Tm3 recovery flag × normalized Tm5 POI p90 version."""
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
from screen_tm2_recovery_flag_mx4_interaction import (  # noqa: E402
    REGIONS, TM2_SHA, TM3_SHA, MX4_SHA, sha256_file, read_flag_artifact, parquet_bytes,
)
from screen_tm5_p90_version_mx4_interaction import (  # noqa: E402
    P90_VERSION_MAX, MIN_COVERAGE, EXPECTED_ARTIFACT_HASHES, read_emission,
    feature_digest, distribution,
)

MIN_FLAGGED_CHANGE_P95 = 0.01


def recovery_version_interaction(flag: np.ndarray, flag_defined: np.ndarray,
                                 p90_version: np.ndarray, p90_defined: np.ndarray,
                                 version_max: float = P90_VERSION_MAX) -> dict[str, Any]:
    """Return log-normalized version × binary flag and absolute change from flag baseline."""
    flag = np.asarray(flag, dtype=np.float64)
    flag_mask = np.asarray(flag_defined, dtype=bool)
    raw = np.asarray(p90_version, dtype=np.float64)
    raw_mask = np.asarray(p90_defined, dtype=bool)
    if (flag.ndim != 1 or raw.shape != flag.shape or flag_mask.shape != flag.shape
            or raw_mask.shape != flag.shape):
        raise ValueError("flag/version values and masks must align as one-dimensional arrays")
    if not np.isfinite(version_max) or version_max <= 0.0:
        raise ValueError("version maximum must be positive and finite")
    if np.any(flag_mask & (~np.isfinite(flag) | ~np.isin(flag, (0.0, 1.0)))):
        raise ValueError("defined recovery flags must be finite and binary")
    if np.any(~flag_mask & np.isfinite(flag)):
        raise ValueError("undefined recovery flags must remain null")
    if np.any(raw_mask & (~np.isfinite(raw) | (raw < 0.0))):
        raise ValueError("defined Tm5 p90 version must be finite and nonnegative")
    normalized = np.full(raw.shape, np.nan, dtype=np.float64)
    normalized[raw_mask] = np.clip(np.log1p(raw[raw_mask]) / np.log1p(version_max), 0.0, 1.0)
    joint = flag_mask & raw_mask
    interaction = np.full(raw.shape, np.nan, dtype=np.float64)
    change = np.full(raw.shape, np.nan, dtype=np.float64)
    interaction[joint] = flag[joint] * normalized[joint]
    # Compare the product with the original binary recovery indicator, as in the prior flag×Mx4 screen.
    change[joint] = np.abs(interaction[joint] - flag[joint])
    if np.any(joint & (~np.isfinite(interaction) | (interaction < 0.0) | (interaction > 1.0))):
        raise ValueError("defined interaction must be finite and in [0,1]")
    return {"normalized_version": normalized, "interaction": interaction,
            "change_from_flag": change, "defined": joint,
            "version_clip_count": int(np.count_nonzero(raw_mask & (raw > version_max)))}


def output_table(official: list[tuple[str, str]], flag: np.ndarray, flag_defined: np.ndarray,
                 p90: np.ndarray, p90_defined: np.ndarray, result: dict[str, Any]) -> pa.Table:
    n = len(official)
    vectors = (flag, flag_defined, p90, p90_defined, result["normalized_version"],
               result["interaction"], result["change_from_flag"], result["defined"])
    if any(np.asarray(v).shape != (n,) for v in vectors):
        raise ValueError("output columns must align with exact official GEOID-region pairs")
    expected_joint = np.asarray(flag_defined, bool) & np.asarray(p90_defined, bool)
    if not np.array_equal(np.asarray(result["defined"], bool), expected_joint):
        raise ValueError("interaction definedness must equal the exact binary-flag/Tm5 joint mask")
    joint = np.asarray(result["defined"], bool)
    if np.any(joint & (~np.isfinite(result["interaction"]) | (result["interaction"] < 0.0) | (result["interaction"] > 1.0))):
        raise ValueError("defined interaction values must be finite and in [0,1]")
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "recovery_flag": pa.array([int(v) if ok else None for v, ok in zip(flag, flag_defined)], type=pa.int8()),
        "recovery_flag_defined": pa.array(flag_defined, type=pa.bool_()),
        "tm5_poi_p90_version": pa.array([float(v) if ok else None for v, ok in zip(p90, p90_defined)], type=pa.float64()),
        "tm5_poi_p90_version_defined": pa.array(p90_defined, type=pa.bool_()),
        "tm5_poi_p90_version_normalized": pa.array([float(v) if ok else None for v, ok in zip(result["normalized_version"], p90_defined)], type=pa.float64()),
        "recovery_flag_x_normalized_version": pa.array([float(v) if ok else None for v, ok in zip(result["interaction"], joint)], type=pa.float64()),
        "interaction_change_from_binary_flag": pa.array([float(v) if ok else None for v, ok in zip(result["change_from_flag"], joint)], type=pa.float64()),
        "interaction_defined": pa.array(joint, type=pa.bool_()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    flag_report_path = ROOT / "reports/tm2-fire-recovery-flag.json"
    version_report_path = ROOT / "reports/tm5-p90-version-mx4-interaction.json"
    flag_report = json.loads(flag_report_path.read_text(encoding="utf-8"))
    version_report = json.loads(version_report_path.read_text(encoding="utf-8"))
    if (flag_report["scope"]["official_ids"] != 9379 or flag_report["scope"]["flagged_ids"] != 30
            or flag_report["scope"]["joint_defined_ids"] != 9362
            or flag_report["scope"]["flagged_regions"] != 4
            or not flag_report["acceptance"]["acceptance_pass"]):
        raise ValueError("validated recovery-flag baseline differs from issued 30-flag/four-region values")
    if (version_report["scope"]["official_ids"] != 9379
            or version_report["scope"]["tm5_p90_version_defined_ids"] != 9377
            or version_report["scope"]["pairwise_defined_ids"] != 9377
            or not np.isclose(version_report["scope"]["coverage_fraction"], 0.9997867576500693, rtol=0, atol=1e-14)
            or not np.isclose(version_report["measure_after"]["p95_absolute_change_from_normalized_p90"], 0.9415887339401863, rtol=0, atol=1e-12)):
        raise ValueError("Tm5 POI p90-version baseline differs from issued coverage/effect")
    if version_report["provenance"]["frozen_inputs"]["Tm5"]["sha256"] != EXPECTED_ARTIFACT_HASHES["Tm5"]:
        raise ValueError("frozen Tm5 artifact hash differs from version interaction baseline")

    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != 9379 or len(set(official)) != 9379 or len({g for g, _ in official}) != 9379
            or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official partition must contain exactly 9,379 unique IDs in four regions")
    official_hash = hashlib.sha256(json.dumps(official, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    if official_hash != flag_report["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official ID partition differs from validated source flag")
    flag_path = workspace / "tm2-fire-recovery-flag/tm2-fire-recovery-flag.parquet"
    flag_sha = flag_report["output_artifact"]["sha256"]
    flags, flag_defined, _ = read_flag_artifact(flag_path, flag_sha, official)
    if flag_defined.sum() != 9362 or np.count_nonzero(flags[flag_defined] == 1.0) != 30:
        raise ValueError("binary source flag must remain 9,362-defined with exactly 30 positive flags")
    expected_flag_geoids = [g for region in REGIONS for g in flag_report["measure_after"]["regions"][region]["flagged_geoids"]]
    observed_flag_geoids = [g for (g, _), value, ok in zip(official, flags, flag_defined) if ok and value == 1.0]
    if sorted(expected_flag_geoids) != sorted(observed_flag_geoids) or len(set(observed_flag_geoids)) != 30:
        raise ValueError("flagged GEOID identities differ from validated source report")

    tm5_path = workspace / "ideas/Tm5.parquet"
    tm5_p90, tm5_defined, tm5_sha = read_emission(
        workspace, "Tm5", "Tm", "Est", "covariate:tm5_poi_p90_version", official,
        EXPECTED_ARTIFACT_HASHES["Tm5"])
    if tm5_sha != EXPECTED_ARTIFACT_HASHES["Tm5"] or int(tm5_defined.sum()) != 9377:
        raise ValueError("Tm5 p90-version source hash or defined coverage differs from frozen screen")
    observed_version = tm5_p90[tm5_defined]
    if (not np.isclose(observed_version.min(), 1.0, rtol=0, atol=1e-12)
            or not np.isclose(observed_version.max(), 13.0, rtol=0, atol=1e-12)):
        raise ValueError("frozen Tm5 POI p90-version no longer spans [1,13]")
    result = recovery_version_interaction(flags, flag_defined, tm5_p90, tm5_defined)
    joint = np.asarray(result["defined"], dtype=bool)
    flagged_joint = joint & (flags == 1.0)
    if not np.array_equal(joint, flag_defined & tm5_defined):
        raise AssertionError("joint interaction mask failed exact source-mask alignment")
    if int(flagged_joint.sum()) > 30 or int(flagged_joint.sum()) < 1:
        raise ValueError("flagged-only support must be a nonempty subset of the 30 validated flags")
    flagged_changes = result["change_from_flag"][flagged_joint]
    flagged_p95 = float(np.quantile(flagged_changes, 0.95))
    coverage = float(joint.mean())
    regions: dict[str, Any] = {}
    for region in REGIONS:
        local = np.asarray([r == region for _, r in official], dtype=bool)
        local_flag_defined = flag_defined & local
        local_tm5_defined = tm5_defined & local
        local_joint = joint & local
        local_flagged = flagged_joint & local
        changes = result["change_from_flag"][local_flagged]
        regions[region] = {
            "official_ids": int(local.sum()), "recovery_flag_defined_ids": int(local_flag_defined.sum()),
            "recovery_flag_positive_ids": int(np.count_nonzero(local_flag_defined & (flags == 1.0))),
            "tm5_p90_version_defined_ids": int(local_tm5_defined.sum()),
            "joint_defined_ids": int(local_joint.sum()), "joint_coverage_fraction": float(local_joint.sum() / local.sum()),
            "flagged_joint_ids": int(local_flagged.sum()),
            "flagged_only_absolute_change_from_flag": distribution(changes),
        }
    table = output_table(official, flags, flag_defined, tm5_p90, tm5_defined, result)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("interaction artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    registry_path = ROOT / "config/ideas.json"
    registry_before = sha256_file(registry_path)
    frozen_after = {"Tm5": sha256_file(tm5_path), "Tm2_flag": sha256_file(flag_path)}
    registry_after = sha256_file(registry_path)
    frozen_unchanged = (frozen_after == {"Tm5": EXPECTED_ARTIFACT_HASHES["Tm5"], "Tm2_flag": flag_sha}
                        and registry_after == registry_before)
    if not frozen_unchanged:
        raise AssertionError("frozen feature/flag artifacts or registry changed")
    bounded = bool(np.isfinite(result["interaction"][joint]).all()
                   and np.all((result["interaction"][joint] >= 0.0) & (result["interaction"][joint] <= 1.0)))
    exact_ids = table.num_rows == 9379 and len(set(official)) == 9379
    coverage_pass = coverage >= MIN_COVERAGE
    exact_mask = bool(np.array_equal(joint, flag_defined & tm5_defined)
                      and np.array_equal(table["interaction_defined"].to_numpy(), joint))
    p95_pass = flagged_p95 >= MIN_FLAGGED_CHANGE_P95
    acceptance = {
        "exact_9379_official_id_alignment": bool(exact_ids),
        "exact_binary_flag_and_joint_source_mask_alignment": exact_mask,
        "validated_30_flags_align_with_source_geoid_list": bool(sorted(expected_flag_geoids) == sorted(observed_flag_geoids)),
        "joint_coverage_fraction": coverage, "coverage_at_least_98_59_percent": bool(coverage_pass),
        "interaction_finite_and_bounded_0_1": bounded,
        "flagged_joint_ids": int(flagged_joint.sum()),
        "flagged_only_p95_absolute_change_from_flag": flagged_p95,
        "flagged_only_p95_change_at_least_0_01": bool(p95_pass),
        "frozen_Tm5_flag_artifact_and_registry_unchanged": bool(frozen_unchanged),
        "screen_pass": bool(exact_ids and exact_mask and coverage_pass and bounded and p95_pass and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "screen": "Validated Tm2×Tm3 binary recovery flag × normalized Tm5 POI p90-version; unregistered label-free interaction screen",
        "definition": "v=clip(log1p(Tm5_POI_p90_version)/log1p(13),0,1); interaction=recovery_flag*v on exact joint-defined support. Flagged-only change is |interaction−recovery_flag|, evaluated only where flag=1 and Tm5 is defined.",
        "method": {
            "source_flag": "Use the completed frozen 9,362-defined Tm2×Tm3 recovery-flag artifact; validate its exact binary mask and all 30 GEOIDs against the source report, without recomputing or changing the flag.",
            "Tm5_version": "Use only frozen Tm5 Est covariate:tm5_poi_p90_version, normalized by log1p(13) and clipped to [0,1].",
            "mask": "Interaction is defined exactly on recovery_flag_defined AND Tm5_p90_version_defined; all other values remain null. Flagged-only metric is restricted to flag=1 on that joint support.",
            "unregistered": True,
        },
        "measure_before": {
            "validated_recovery_flagged_ids": 30, "recovery_flagged_regions": 4,
            "tm5_p90_version_defined_ids": 9377,
            "tm5_p90_version_interaction_coverage_fraction": 0.9997867576500693,
            "tm5_p90_version_mx4_p95_absolute_change": 0.9415887339401863,
            "no_recovery_flag_x_poi_version_interaction_previously_measured": True,
            "recovery_flag_report_sha256": sha256_file(flag_report_path),
            "tm5_version_screen_report_sha256": sha256_file(version_report_path),
        },
        "scope": {
            "official_ids": len(official), "exact_official_id_alignment": exact_ids,
            "recovery_flag_defined_ids": int(flag_defined.sum()),
            "recovery_flag_positive_ids": int(np.count_nonzero(flag_defined & (flags == 1.0))),
            "recovery_flag_undefined_ids": int((~flag_defined).sum()),
            "tm5_p90_version_defined_ids": int(tm5_defined.sum()),
            "joint_defined_ids": int(joint.sum()), "joint_undefined_ids": int((~joint).sum()),
            "coverage_fraction": coverage, "flagged_joint_ids": int(flagged_joint.sum()),
            "labels_read": False, "sample_score_values_read": False,
            "Tm1_or_Tm4_inputs_or_artifacts_read": False,
            "removed_or_equivalent_reference_data_read": False, "external_data_read": False,
            "leaderboard_feedback_used": False, "registry_integration": False,
            "frozen_Tm5_or_recovery_flag_modified": False, "uploads": 0,
        },
        "measure_after": {
            "normalized_version_distribution_on_joint_support": distribution(result["normalized_version"][joint]),
            "interaction_distribution_on_joint_support": distribution(result["interaction"][joint]),
            "flagged_only_absolute_change_from_binary_flag": distribution(flagged_changes),
            "flagged_only_p95_absolute_change_from_flag": flagged_p95,
            "version_clip_count": int(result["version_clip_count"]),
            "regions": regions,
        },
        "thresholds": {"minimum_joint_coverage_inclusive": MIN_COVERAGE,
                       "interaction_bounds_inclusive": [0.0, 1.0],
                       "minimum_flagged_only_p95_absolute_change_inclusive": MIN_FLAGGED_CHANGE_P95},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_hash,
            "source_flag_artifact": {"path": str(flag_path), "sha256_before": flag_sha,
                                      "sha256_after": frozen_after["Tm2_flag"]},
            "Tm5_artifact": {"path": str(tm5_path), "sha256_before": tm5_sha,
                             "sha256_after": frozen_after["Tm5"],
                             "p90_version_emission_sha256": feature_digest(official, tm5_p90, tm5_defined)},
            "recovery_flag_report_sha256": sha256_file(flag_report_path),
            "tm5_version_report_sha256": sha256_file(version_report_path),
            "registry_sha256_before": registry_before, "registry_sha256_after": registry_after,
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "columns": table.column_names, "deterministic_bytes": True},
        },
        "interpretation": "The preregistered flagged-only interaction screen passes the requested coverage, mask, bounds and effect gates; retain as an unregistered sensitivity, with no promotion or estimator change." if acceptance["screen_pass"] else
        "One or more requested coverage, mask, bounds or flagged-only effect gates failed; retain the source diagnostics separately and do not integrate the interaction.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm2-fire-flag-tm5-poi-version.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "tm2-fire-flag-tm5-poi-version/flag-version-interaction.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "measure_after": {k: report["measure_after"][k] for k in (
                          "flagged_only_p95_absolute_change_from_flag", "flagged_only_absolute_change_from_binary_flag", "regions")},
                      "output_artifact": report["provenance"]["output_artifact"],
                      "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
