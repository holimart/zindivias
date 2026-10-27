#!/usr/bin/env python3
"""Screen rural/urban differences in the q90 absolute frozen S1 residual."""
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
MEDIAN_REPORT = ROOT / "reports/s1-orthogonalized-rural-strata.json"
MEDIAN_GAP_BEFORE = 4.933868494245855e-06
MIN_GROUP_N = 30
MIN_ABS_EQUAL_REGION_GAP = 0.001
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261028


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def q90_gap_by_region(official: list[tuple[str, str]], residual: np.ndarray,
                      residual_defined: np.ndarray, pct_urban: np.ndarray,
                      folds: np.ndarray) -> dict[str, Any]:
    n = len(official)
    arrays = (np.asarray(residual, dtype=np.float64), np.asarray(residual_defined, dtype=bool),
              np.asarray(pct_urban, dtype=np.float64), np.asarray(folds))
    if any(value.shape != (n,) for value in arrays):
        raise ValueError("residual, masks, strata and folds must align with official IDs")
    values, defined, urbanity, fold_ids = arrays
    if np.any(defined & ~np.isfinite(values)) or np.any(~defined & np.isfinite(values)):
        raise ValueError("residual nulls do not match the source defined mask")
    if np.any(np.isfinite(urbanity) & ((urbanity < 0.) | (urbanity > 1.))):
        raise ValueError("provided pct_urban values must be in [0,1]")
    if np.any((fold_ids < 0) | (fold_ids > 4)):
        raise ValueError("fixed source fold values must be in 0..4")
    if not defined.all():
        raise ValueError("the frozen source residual unexpectedly has undefined IDs")
    abs_residual = np.abs(values)
    result: dict[str, Any] = {"by_region": {}, "groups": {}}
    for region in REGIONS:
        region_mask = np.asarray([r == region for _, r in official], dtype=bool)
        local = region_mask & np.isfinite(urbanity)
        rural = local & (urbanity < 0.5)
        urban = local & (urbanity >= 0.5)
        nr, nu = int(rural.sum()), int(urban.sum())
        if not nr or not nu:
            raise ValueError(f"{region} must have rural and urban support to estimate q90 gap")
        q_rural = float(np.quantile(abs_residual[rural], .90))
        q_urban = float(np.quantile(abs_residual[urban], .90))
        result["by_region"][region] = {
            "official_ids": int(region_mask.sum()), "pct_urban_defined_ids": int(local.sum()),
            "rural_n": nr, "urban_n": nu, "rural_q90_abs_residual": q_rural,
            "urban_q90_abs_residual": q_urban, "signed_rural_minus_urban_q90_gap": q_rural - q_urban,
            "both_strata_n_at_least_30": nr >= MIN_GROUP_N and nu >= MIN_GROUP_N,
            "fixed_fold_counts": {str(f): int(np.count_nonzero(local & (fold_ids == f))) for f in range(5)},
        }
        result["groups"][region] = (abs_residual[rural], abs_residual[urban])
    gaps = [v["signed_rural_minus_urban_q90_gap"] for v in result["by_region"].values()]
    result["equal_region_signed_gap"] = float(np.mean(gaps))
    result["all_region_strata_support_at_least_30"] = all(
        v["rural_n"] >= MIN_GROUP_N and v["urban_n"] >= MIN_GROUP_N
        for v in result["by_region"].values())
    return result


def bootstrap_equal_region_gap(groups: dict[str, tuple[np.ndarray, np.ndarray]], *,
                               replicates: int = BOOTSTRAP_REPLICATES,
                               seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    if tuple(groups) != REGIONS:
        raise ValueError("bootstrap groups must include all four official regions in canonical order")
    if replicates < 100:
        raise ValueError("at least 100 bootstrap replicates are required")
    rng = np.random.default_rng(seed)
    draws = np.empty(replicates, dtype=np.float64)
    for b in range(replicates):
        regional_gaps = []
        for region in REGIONS:
            rural, urban = (np.asarray(v, dtype=np.float64) for v in groups[region])
            if rural.size < 1 or urban.size < 1 or not np.isfinite(rural).all() or not np.isfinite(urban).all():
                raise ValueError(f"{region} bootstrap strata must be nonempty and finite")
            rural_sample = rng.choice(rural, size=rural.size, replace=True)
            urban_sample = rng.choice(urban, size=urban.size, replace=True)
            regional_gaps.append(float(np.quantile(rural_sample, .90) - np.quantile(urban_sample, .90)))
        draws[b] = float(np.mean(regional_gaps))
    low, high = np.quantile(draws, [.025, .975])
    return {"replicates": int(replicates), "seed": int(seed),
            "resampling": "independent with-replacement tract resampling within each region×rural/urban stratum; equal-weight mean of four signed regional q90 gaps",
            "ci_95_percentile": [float(low), float(high)],
            "bootstrap_gap_distribution": {"mean": float(draws.mean()), "sd": float(draws.std(ddof=0)),
                                           "min": float(draws.min()), "max": float(draws.max())},
            "ci_excludes_zero": bool(low > 0. or high < 0.)}


def load_frozen_residual(path: Path, expected_sha: str,
                         official: list[tuple[str, str]]) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    digest = sha256_file(path)
    if digest != expected_sha:
        raise ValueError("frozen orthogonalized S1 residual artifact hash mismatch")
    table = pq.read_table(path, columns=["GEOID", "region", "crossfit_residual", "crossfit_joint_defined", "fold"])
    if table.num_rows != len(official):
        raise ValueError("frozen residual artifact must contain one row per official GEOID")
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
        index = locations[pair]
        defined[index] = bool(row["crossfit_joint_defined"])
        folds[index] = int(row["fold"])
        value = row["crossfit_residual"]
        if defined[index]:
            if value is None or not np.isfinite(float(value)):
                raise ValueError("defined frozen residual cannot be null or nonfinite")
            values[index] = float(value)
        elif value is not None:
            raise ValueError("undefined frozen residual must remain null")
    if seen != set(official):
        raise ValueError("frozen residual GEOID-region alignment is incomplete")
    fold_digest = hashlib.sha256("\n".join(
        f"{g},{r},{int(f)}" for (g, r), f in zip(official, folds)).encode()).hexdigest()
    return values, defined, folds, {"sha256": digest, "rows": table.num_rows,
                                    "columns_read": ["GEOID", "region", "crossfit_residual", "crossfit_joint_defined", "fold"],
                                    "exact_official_id_alignment": seen == set(official),
                                    "fixed_fold_sha256": fold_digest,
                                    "fixed_fold_counts": {str(f): int(np.count_nonzero(folds == f)) for f in range(5)}}


def load_pct_urban(workspace: Path, official: list[tuple[str, str]]) -> tuple[np.ndarray, dict[str, Any]]:
    values = np.full(len(official), np.nan, dtype=np.float64)
    locations = {pair: i for i, pair in enumerate(official)}
    manifest: dict[str, Any] = {}
    for region in REGIONS:
        path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"provided pct_urban table missing for {region}: {path}")
        table = pq.read_table(path, columns=["GEOID", "pct_urban"])
        seen: set[str] = set()
        for geoid, value in zip(table.column("GEOID").to_pylist(), table.column("pct_urban").to_pylist()):
            geoid = str(geoid)
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
        manifest[region] = {"path": str(path), "sha256": sha256_file(path),
                            "columns_read": ["GEOID", "pct_urban"], "official_ids": len(seen),
                            "pct_urban_defined_ids": int(np.isfinite(values[indices]).sum())}
    if np.any(np.isfinite(values) & ((values < 0.) | (values > 1.))):
        raise ValueError("finite provided pct_urban must be in [0,1]")
    return values, manifest


def make_output(official: list[tuple[str, str]], residual: np.ndarray, residual_defined: np.ndarray,
                folds: np.ndarray, pct_urban: np.ndarray) -> pa.Table:
    rural = np.isfinite(pct_urban) & (pct_urban < .5)
    urban = np.isfinite(pct_urban) & (pct_urban >= .5)
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "fixed_source_fold": pa.array(folds, type=pa.int8()),
        "crossfit_residual": pa.array([float(v) if ok else None for v, ok in zip(residual, residual_defined)], type=pa.float64()),
        "absolute_crossfit_residual": pa.array([abs(float(v)) if ok else None for v, ok in zip(residual, residual_defined)], type=pa.float64()),
        "residual_defined": pa.array(residual_defined, type=pa.bool_()),
        "pct_urban": pa.array([float(v) if np.isfinite(v) else None for v in pct_urban], type=pa.float64()),
        "rural_indicator": pa.array([bool(v) if ok else None for v, ok in zip(rural, np.isfinite(pct_urban))], type=pa.bool_()),
        "urban_indicator": pa.array([bool(v) if ok else None for v, ok in zip(urban, np.isfinite(pct_urban))], type=pa.bool_()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    source_report = json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    prior = json.loads(MEDIAN_REPORT.read_text(encoding="utf-8"))
    if (source_report["scope"]["official_ids"] != EXPECTED_N
            or source_report["scope"]["disagreement_defined_ids"] != EXPECTED_N
            or not np.isclose(prior["measure_after"]["absolute_median_absolute_residual_gap"], MEDIAN_GAP_BEFORE, rtol=0., atol=1e-15)
            or prior["scope"]["official_ids"] != EXPECTED_N):
        raise ValueError("frozen source residual or MEASURE-BEFORE median baseline differs")
    official_reader_path = ROOT / "scripts/audit_v2_component_masks.py"
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N
            or len({g for g, _ in official}) != EXPECTED_N or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official input must be an exact 9,379-ID/four-region partition")
    official_digest = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    residual_meta = source_report["provenance"]["output_artifact"]
    residual_path = Path(residual_meta["path"])
    residual, residual_defined, folds, residual_input = load_frozen_residual(residual_path, RESIDUAL_SHA, official)
    if not residual_defined.all() or residual_input["sha256"] != source_report["provenance"]["output_artifact"]["sha256"]:
        raise ValueError("frozen residual exact coverage/hash validation failed")
    pct_urban, strata_meta = load_pct_urban(workspace, official)
    screen = q90_gap_by_region(official, residual, residual_defined, pct_urban, folds)
    bootstrap = bootstrap_equal_region_gap(screen["groups"])
    equal_gap = screen["equal_region_signed_gap"]
    support_pass = screen["all_region_strata_support_at_least_30"]
    gap_pass = abs(equal_gap) >= MIN_ABS_EQUAL_REGION_GAP
    ci_pass = bool(bootstrap["ci_excludes_zero"])
    acceptance = {"exact_official_id_and_null_alignment": True,
                  "each_included_region_stratum_n_at_least_30": bool(support_pass),
                  "equal_region_signed_rural_minus_urban_q90_gap": float(equal_gap),
                  "absolute_equal_region_gap_at_least_0_001": bool(gap_pass),
                  "stratified_bootstrap_95_percent_ci": bootstrap["ci_95_percentile"],
                  "bootstrap_ci_excludes_zero": bool(ci_pass),
                  "upper_tail_heterogeneity_gate_pass": bool(support_pass and gap_pass and ci_pass)}
    table = make_output(official, residual, residual_defined, folds, pct_urban)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("diagnostic Parquet serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    report = {
        "schema_version": 1,
        "screen": "Absolute frozen cross-fitted orthogonalized S1 disagreement residual q90 rural/urban contrast; descriptive and unregistered",
        "measure_before": {"prior_absolute_rural_minus_urban_median_gap": MEDIAN_GAP_BEFORE,
                           "median_report_sha256": sha256_file(MEDIAN_REPORT),
                           "source_residual_artifact_sha256": RESIDUAL_SHA,
                           "q90_contrast_or_ci_measured": False},
        "method": {"residual": "Reuse the frozen cross-fitted orthogonalized S1 residual as emitted; no refit or recomputation.",
                   "outcome": "Within each official region, compute q90(|crossfit residual|) for rural pct_urban<0.5 and urban pct_urban>=0.5; regional gap is rural minus urban. Overall statistic is the unweighted mean of the four signed regional gaps.",
                   "bootstrap": "2,000 deterministic percentile-bootstrap replicates. Resample tracts independently with replacement within each region×rural/urban stratum, recompute four signed q90 gaps, and take their equal-region mean.",
                   "folds": "Copy and validate the fixed fold IDs from the frozen residual artifact; no fold reassignment or model fitting.",
                   "nulls": "Preserve source residual nulls; exclude missing/nonfinite pct_urban from both strata without imputation.",
                   "rural_definition": "Provided pct_urban < 0.5 is rural; pct_urban >= 0.5 is urban.",
                   "interpretation_limit": "Descriptive residual heterogeneity only; not a hidden-target error, causal, or accuracy claim.",
                   "unregistered": True},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": official_digest,
                  "residual_defined_ids": int(residual_defined.sum()), "pct_urban_defined_ids": int(np.isfinite(pct_urban).sum()),
                  "joint_defined_ids": int((residual_defined & np.isfinite(pct_urban)).sum()),
                  "residual_defined_without_pct_urban": int(np.count_nonzero(residual_defined & ~np.isfinite(pct_urban))),
                  "pct_urban_defined_without_residual": int(np.count_nonzero(~residual_defined & np.isfinite(pct_urban))),
                  "fold_counts": residual_input["fixed_fold_counts"], "regions": screen["by_region"],
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_reference_data_read": False, "external_layers_read": False,
                  "leaderboard_feedback_used": False, "registry_changes": False, "uploads": 0},
        "measure_after": {"equal_region_signed_rural_minus_urban_q90_gap": equal_gap,
                          "absolute_equal_region_gap": abs(equal_gap),
                          "stratified_bootstrap": bootstrap},
        "thresholds": {"minimum_each_region_stratum_support_inclusive": MIN_GROUP_N,
                       "minimum_absolute_equal_region_gap_inclusive": MIN_ABS_EQUAL_REGION_GAP,
                       "bootstrap_ci_must_exclude_zero": True,
                       "bootstrap_replicates": BOOTSTRAP_REPLICATES,
                       "bootstrap_seed": BOOTSTRAP_SEED},
        "acceptance": acceptance,
        "provenance": {"official_id_reader": str(official_reader_path),
                       "official_geoid_region_sha256": official_digest,
                       "source_report": str(RESIDUAL_REPORT), "source_report_sha256": sha256_file(RESIDUAL_REPORT),
                       "source_residual_artifact": str(residual_path), "source_residual_artifact_sha256": residual_input["sha256"],
                       "source_residual_artifact_columns_read": residual_input["columns_read"],
                       "fixed_fold_sha256": residual_input["fixed_fold_sha256"],
                       "strata_inputs": strata_meta,
                       "output_artifact": {"path": str(artifact_path), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": table.num_rows, "columns": table.column_names,
                                           "deterministic_serialization": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The q90 rural/urban contrast meets the preregistered support, effect-size, and bootstrap-CI gates; this is descriptive heterogeneity, not a target-error or causal finding." if acceptance["upper_tail_heterogeneity_gate_pass"] else
                         "Descriptive/inconclusive: at least one preregistered support, effect-size, or CI gate fails; no target-error or causal inference is warranted.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/s1-orthogonalized-rural-q90.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "s1-orthogonalized-rural-q90/rural-q90-residual-input.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "measure_after": report["measure_after"],
                      "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
