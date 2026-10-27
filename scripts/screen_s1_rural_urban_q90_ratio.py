#!/usr/bin/env python3
"""Screen rural/urban ratios of the frozen orthogonalized S1 residual q90."""
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
from audit_mx4p_conditional_innovation import parquet_bytes  # noqa: E402

EXPECTED_N = 9379
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
RESIDUAL_SHA = "437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
RESIDUAL_REPORT = ROOT / "reports/s1-disagreement-orthogonalized.json"
ABSOLUTE_Q90_REPORT = ROOT / "reports/s1-orthogonalized-rural-q90.json"
MEDIAN_REPORT = ROOT / "reports/s1-orthogonalized-rural-strata.json"
ABS_GAP_BEFORE = 0.0005424407320647002
ABS_CI_BEFORE = [6.186952912855549e-06, 0.0011112970401510032]
MEDIAN_GAP_BEFORE = 4.933868494245855e-06
MIN_GROUP_N = 30
MIN_ABS_LOG_RATIO = float(np.log(1.25))
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261028


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def q90_ratio_by_region(official: list[tuple[str, str]], residual: np.ndarray,
                        residual_defined: np.ndarray, pct_urban: np.ndarray,
                        folds: np.ndarray) -> dict[str, Any]:
    n = len(official)
    values = np.asarray(residual, dtype=np.float64)
    defined = np.asarray(residual_defined, dtype=bool)
    urbanity = np.asarray(pct_urban, dtype=np.float64)
    fold_ids = np.asarray(folds)
    if any(a.shape != (n,) for a in (values, defined, urbanity, fold_ids)):
        raise ValueError("residuals, masks, strata and folds must align with official IDs")
    if np.any(defined & ~np.isfinite(values)) or np.any(~defined & np.isfinite(values)):
        raise ValueError("residual nulls do not match the frozen defined mask")
    if not defined.all():
        raise ValueError("frozen residual source must define every official ID")
    if np.any(np.isfinite(urbanity) & ((urbanity < 0.) | (urbanity > 1.))):
        raise ValueError("provided pct_urban must be within [0,1]")
    if np.any((fold_ids < 0) | (fold_ids > 4)):
        raise ValueError("fixed source folds must be in 0..4")
    abs_residual = np.abs(values)
    by_region: dict[str, Any] = {}
    groups: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    log_ratios = []
    for region in REGIONS:
        region_mask = np.asarray([r == region for _, r in official], dtype=bool)
        usable = region_mask & np.isfinite(urbanity)
        rural_mask = usable & (urbanity < .5)
        urban_mask = usable & (urbanity >= .5)
        rural_values, urban_values = abs_residual[rural_mask], abs_residual[urban_mask]
        if not len(rural_values) or not len(urban_values):
            raise ValueError(f"{region} needs nonempty rural and urban strata")
        q_rural = float(np.quantile(rural_values, .90))
        q_urban = float(np.quantile(urban_values, .90))
        if not np.isfinite(q_rural) or not np.isfinite(q_urban) or q_rural <= 0. or q_urban <= 0.:
            raise ValueError(f"{region} q90 values must be positive and finite for a ratio")
        ratio = q_rural / q_urban
        log_ratio = float(np.log(ratio))
        log_ratios.append(log_ratio)
        by_region[region] = {
            "official_ids": int(region_mask.sum()), "pct_urban_defined_ids": int(usable.sum()),
            "rural_n": int(rural_mask.sum()), "urban_n": int(urban_mask.sum()),
            "rural_q90_abs_residual": q_rural, "urban_q90_abs_residual": q_urban,
            "rural_to_urban_q90_ratio": float(ratio), "log_rural_to_urban_q90_ratio": log_ratio,
            "both_strata_n_at_least_30": bool(rural_mask.sum() >= MIN_GROUP_N and urban_mask.sum() >= MIN_GROUP_N),
            "fixed_fold_counts": {str(f): int(np.count_nonzero(usable & (fold_ids == f))) for f in range(5)},
        }
        groups[region] = (rural_values, urban_values)
    supporting = sum(v["both_strata_n_at_least_30"] for v in by_region.values())
    return {"by_region": by_region, "groups": groups,
            "equal_region_mean_log_ratio": float(np.mean(log_ratios)),
            "regions_with_at_least_30_per_stratum": int(supporting),
            "support_gate_pass": bool(supporting >= 3)}


def bootstrap_equal_region_log_ratio(groups: dict[str, tuple[np.ndarray, np.ndarray]], *,
                                     replicates: int = BOOTSTRAP_REPLICATES,
                                     seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    if tuple(groups) != REGIONS:
        raise ValueError("bootstrap groups must contain four regions in canonical order")
    if replicates < 100:
        raise ValueError("at least 100 bootstrap replicates are required")
    rng = np.random.default_rng(seed)
    draws = np.empty(replicates, dtype=np.float64)
    for b in range(replicates):
        regional_log_ratios = []
        for region in REGIONS:
            rural, urban = (np.asarray(v, dtype=np.float64) for v in groups[region])
            if (rural.size < 1 or urban.size < 1 or not np.isfinite(rural).all()
                    or not np.isfinite(urban).all() or np.any(rural < 0.) or np.any(urban < 0.)):
                raise ValueError(f"{region} bootstrap strata must be nonempty, finite and nonnegative")
            rural_sample = rng.choice(rural, size=rural.size, replace=True)
            urban_sample = rng.choice(urban, size=urban.size, replace=True)
            q_rural = float(np.quantile(rural_sample, .90))
            q_urban = float(np.quantile(urban_sample, .90))
            if q_rural <= 0. or q_urban <= 0.:
                raise ValueError(f"{region} bootstrap q90 reached zero; log ratio is undefined")
            regional_log_ratios.append(float(np.log(q_rural / q_urban)))
        draws[b] = float(np.mean(regional_log_ratios))
    low, high = np.quantile(draws, [.025, .975])
    return {"replicates": int(replicates), "seed": int(seed),
            "resampling": "independent with-replacement tract resampling within each region×rural/urban stratum; recompute q90 ratios, log them, and average equally across four regions",
            "ci_95_percentile": [float(low), float(high)],
            "bootstrap_log_ratio_distribution": {"mean": float(draws.mean()), "sd": float(draws.std(ddof=0)),
                                                  "min": float(draws.min()), "max": float(draws.max())},
            "ci_excludes_zero": bool(low > 0. or high < 0.)}


def load_frozen_residual(path: Path, expected_sha: str,
                         official: list[tuple[str, str]]) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    digest = sha256_file(path)
    if digest != expected_sha:
        raise ValueError("frozen orthogonalized S1 residual artifact hash mismatch")
    table = pq.read_table(path, columns=["GEOID", "region", "crossfit_residual", "crossfit_joint_defined", "fold"])
    if table.num_rows != len(official):
        raise ValueError("frozen residual artifact must have one row per official GEOID")
    locations = {pair: i for i, pair in enumerate(official)}
    values = np.full(len(official), np.nan, dtype=np.float64)
    defined = np.zeros(len(official), dtype=bool)
    folds = np.full(len(official), -1, dtype=np.int8)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in locations or pair in seen:
            raise ValueError(f"unexpected or duplicate frozen residual ID {pair}")
        seen.add(pair)
        i = locations[pair]
        if not isinstance(row["crossfit_joint_defined"], bool):
            raise ValueError("frozen residual defined mask must be boolean")
        defined[i] = row["crossfit_joint_defined"]
        folds[i] = int(row["fold"])
        value = row["crossfit_residual"]
        if defined[i]:
            if value is None or not np.isfinite(float(value)):
                raise ValueError("defined frozen residual cannot be null/nonfinite")
            values[i] = float(value)
        elif value is not None:
            raise ValueError("undefined frozen residual must remain null")
    if seen != set(official):
        raise ValueError("frozen residual GEOID-region alignment is incomplete")
    fold_digest = hashlib.sha256("\n".join(f"{g},{r},{int(f)}" for (g, r), f in zip(official, folds)).encode()).hexdigest()
    return values, defined, folds, {"sha256": digest, "rows": table.num_rows,
                                    "columns_read": ["GEOID", "region", "crossfit_residual", "crossfit_joint_defined", "fold"],
                                    "exact_official_id_alignment": True,
                                    "fixed_fold_sha256": fold_digest,
                                    "fixed_fold_counts": {str(f): int(np.count_nonzero(folds == f)) for f in range(5)}}


def load_pct_urban(workspace: Path, official: list[tuple[str, str]]) -> tuple[np.ndarray, dict[str, Any]]:
    values = np.full(len(official), np.nan, dtype=np.float64)
    locations = {pair: i for i, pair in enumerate(official)}
    metadata: dict[str, Any] = {}
    for region in REGIONS:
        path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"provided pct_urban table missing for {region}: {path}")
        table = pq.read_table(path, columns=["GEOID", "pct_urban"])
        seen: set[str] = set()
        for geoid_value, value in zip(table.column("GEOID").to_pylist(), table.column("pct_urban").to_pylist()):
            geoid = str(geoid_value)
            pair = (geoid, region)
            if pair not in locations:
                continue
            if geoid in seen:
                raise ValueError(f"duplicate pct_urban GEOID {geoid}")
            seen.add(geoid)
            values[locations[pair]] = float(value) if value is not None else np.nan
        expected = {g for g, r in official if r == region}
        if seen != expected:
            raise ValueError(f"{region} provided pct_urban IDs do not exactly match official IDs")
        indices = [locations[(g, region)] for g in expected]
        metadata[region] = {"path": str(path), "sha256": sha256_file(path),
                            "columns_read": ["GEOID", "pct_urban"], "official_ids": len(seen),
                            "pct_urban_defined_ids": int(np.isfinite(values[indices]).sum())}
    if np.any(np.isfinite(values) & ((values < 0.) | (values > 1.))):
        raise ValueError("provided pct_urban must be in [0,1]")
    return values, metadata


def make_output(official: list[tuple[str, str]], residual: np.ndarray, defined: np.ndarray,
                folds: np.ndarray, pct_urban: np.ndarray) -> pa.Table:
    urban_defined = np.isfinite(pct_urban)
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "fixed_source_fold": pa.array(folds, type=pa.int8()),
        "crossfit_residual": pa.array([float(v) if ok else None for v, ok in zip(residual, defined)], type=pa.float64()),
        "absolute_crossfit_residual": pa.array([abs(float(v)) if ok else None for v, ok in zip(residual, defined)], type=pa.float64()),
        "residual_defined": pa.array(defined, type=pa.bool_()),
        "pct_urban": pa.array([float(v) if ok else None for v, ok in zip(pct_urban, urban_defined)], type=pa.float64()),
        "rural_indicator": pa.array([bool(v) if ok else None for v, ok in zip(pct_urban < .5, urban_defined)], type=pa.bool_()),
        "urban_indicator": pa.array([bool(v) if ok else None for v, ok in zip(pct_urban >= .5, urban_defined)], type=pa.bool_()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    source_report = json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    prior_q90_report = json.loads(ABSOLUTE_Q90_REPORT.read_text(encoding="utf-8"))
    median_report = json.loads(MEDIAN_REPORT.read_text(encoding="utf-8"))
    if (source_report["scope"]["official_ids"] != EXPECTED_N
            or source_report["scope"]["disagreement_defined_ids"] != EXPECTED_N
            or prior_q90_report["scope"]["official_ids"] != EXPECTED_N
            or not np.isclose(prior_q90_report["measure_after"]["equal_region_signed_rural_minus_urban_q90_gap"], ABS_GAP_BEFORE, rtol=0., atol=1e-15)
            or not np.allclose(prior_q90_report["measure_after"]["stratified_bootstrap"]["ci_95_percentile"], ABS_CI_BEFORE, rtol=0., atol=1e-15)
            or not np.isclose(median_report["measure_after"]["absolute_median_absolute_residual_gap"], MEDIAN_GAP_BEFORE, rtol=0., atol=1e-15)):
        raise ValueError("frozen source reports or dispatched MEASURE-BEFORE values differ")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N
            or len({g for g, _ in official}) != EXPECTED_N or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official input must be an exact 9,379-ID/four-region partition")
    official_digest = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    residual_path = Path(source_report["provenance"]["output_artifact"]["path"])
    residual, residual_defined, folds, residual_meta = load_frozen_residual(residual_path, RESIDUAL_SHA, official)
    if not residual_defined.all() or residual_meta["sha256"] != source_report["provenance"]["output_artifact"]["sha256"]:
        raise ValueError("frozen residual full support/hash validation failed")
    pct_urban, strata_meta = load_pct_urban(workspace, official)
    result = q90_ratio_by_region(official, residual, residual_defined, pct_urban, folds)
    bootstrap = bootstrap_equal_region_log_ratio(result["groups"])
    mean_log_ratio = result["equal_region_mean_log_ratio"]
    magnitude_pass = abs(mean_log_ratio) >= MIN_ABS_LOG_RATIO
    ci_pass = bootstrap["ci_excludes_zero"]
    gate = bool(result["support_gate_pass"] and magnitude_pass and ci_pass)
    output = make_output(official, residual, residual_defined, folds, pct_urban)
    payload = parquet_bytes(output)
    if payload != parquet_bytes(output):
        raise ValueError("diagnostic Parquet serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    report = {
        "schema_version": 1,
        "screen": "Rural/urban ratio of q90 absolute frozen cross-fitted orthogonalized S1 residual; descriptive and unregistered",
        "measure_before": {"absolute_equal_region_q90_gap": ABS_GAP_BEFORE,
                           "absolute_q90_gap_bootstrap_95_ci": ABS_CI_BEFORE,
                           "all_four_regions_had_at_least_30_per_stratum": True,
                           "relative_q90_statistic_measured": False,
                           "absolute_q90_report_sha256": sha256_file(ABSOLUTE_Q90_REPORT),
                           "frozen_residual_artifact_sha256": RESIDUAL_SHA},
        "method": {"residual": "Reuse the exact frozen cross-fitted orthogonalized S1 residual values; do not refit or recompute.",
                   "strata": "Provided pct_urban < 0.5 is rural; pct_urban >= 0.5 is urban. Missing pct_urban is excluded without imputation.",
                   "regional_statistic": "For each region, compute q90(|frozen residual|) in each stratum, the rural-to-urban q90 ratio, and its natural logarithm.",
                   "equal_region_statistic": "Unweighted mean of the four regional log(rural q90 / urban q90)) values.",
                   "bootstrap": "2,000 deterministic percentile-bootstrap replicates; independently resample tracts with replacement within each region×stratum, recompute q90 ratios and the equal-region mean log ratio.",
                   "folds": "Reuse fixed GEOID fold IDs from the frozen residual artifact; no model refit or fold reassignment.",
                   "unregistered": True,
                   "interpretation_limit": "Descriptive residual-scale heterogeneity only; not hidden-target error, accuracy, causal inference, or promotion evidence."},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": official_digest,
                  "residual_defined_ids": int(residual_defined.sum()), "pct_urban_defined_ids": int(np.isfinite(pct_urban).sum()),
                  "joint_defined_ids": int((residual_defined & np.isfinite(pct_urban)).sum()),
                  "residual_defined_without_pct_urban": int(np.count_nonzero(residual_defined & ~np.isfinite(pct_urban))),
                  "pct_urban_defined_without_residual": int(np.count_nonzero(~residual_defined & np.isfinite(pct_urban))),
                  "fixed_fold_counts": residual_meta["fixed_fold_counts"], "regions": result["by_region"],
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_references_read": False, "external_data_used": [],
                  "post_result_strata_used": False, "leaderboard_feedback_used": False,
                  "registry_changes": False, "uploads": 0},
        "measure_after": {"equal_region_mean_log_rural_to_urban_q90_ratio": mean_log_ratio,
                          "equal_region_geometric_mean_rural_to_urban_q90_ratio": float(np.exp(mean_log_ratio)),
                          "stratified_bootstrap": bootstrap,
                          "regions_with_at_least_30_per_stratum": result["regions_with_at_least_30_per_stratum"]},
        "thresholds": {"minimum_region_strata_support_inclusive": MIN_GROUP_N,
                       "minimum_regions_meeting_support": 3,
                       "minimum_absolute_equal_region_mean_log_ratio_inclusive": MIN_ABS_LOG_RATIO,
                       "equivalent_ratio_threshold": 1.25,
                       "bootstrap_ci_must_exclude_zero": True,
                       "bootstrap_replicates": BOOTSTRAP_REPLICATES,
                       "bootstrap_seed": BOOTSTRAP_SEED},
        "acceptance": {"at_least_3_regions_have_n_ge_30_in_each_stratum": result["support_gate_pass"],
                       "absolute_mean_log_ratio_at_least_log_1_25": bool(magnitude_pass),
                       "stratified_bootstrap_ci_excludes_zero": bool(ci_pass),
                       "relative_tail_heterogeneity_gate_pass": gate},
        "provenance": {"official_id_reader": str(ROOT / "scripts/audit_v2_component_masks.py"),
                       "official_geoid_region_sha256": official_digest,
                       "residual_source_report": str(RESIDUAL_REPORT), "residual_source_report_sha256": sha256_file(RESIDUAL_REPORT),
                       "residual_artifact": str(residual_path), "residual_artifact_sha256": residual_meta["sha256"],
                       "residual_artifact_columns_read": residual_meta["columns_read"],
                       "fixed_fold_sha256": residual_meta["fixed_fold_sha256"],
                       "pct_urban_strata_inputs": strata_meta,
                       "prior_absolute_q90_report": str(ABSOLUTE_Q90_REPORT),
                       "prior_absolute_q90_report_sha256": sha256_file(ABSOLUTE_Q90_REPORT),
                       "output_artifact": {"path": str(artifact_path), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": output.num_rows, "columns": output.column_names,
                                           "deterministic_serialization": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The relative q90 screen passed support, magnitude and CI gates; this remains a descriptive feature diagnostic, not a target-accuracy, causal, or promotion claim." if gate else
                         "The relative q90 screen did not pass every preregistered support, magnitude and CI gate; retain descriptive-only and make no target-accuracy or causal claim.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/s1-rural-urban-q90-ratio.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "s1-rural-urban-q90-ratio/rural-q90-ratio-input.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "measure_after": report["measure_after"],
                      "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
