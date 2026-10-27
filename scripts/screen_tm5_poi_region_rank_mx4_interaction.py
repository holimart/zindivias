#!/usr/bin/env python3
"""Cross-fit the within-region Tm5 POI p90 rank × frozen Mx4 F interaction."""
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
from audit_mx4p_conditional_innovation import (  # noqa: E402
    REGIONS, MIN_VARIANCE_RATIO, MAX_ABS_RESIDUAL_CORRELATION,
    crossfit_region, deterministic_folds, pearson, distribution, read_emission, parquet_bytes,
)
from screen_tm5_building_p90_mx4_interaction import TM5_SHA, read_emission as read_tm5_emission  # noqa: E402
from screen_tm5_poi_p90_region_rank import region_rank  # noqa: E402

EXPECTED_N = 9379
MIN_COVERAGE = 0.9859
MX4_SHA = "8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af"
S1_SHA = "632829cf29281aa16487707cb555f52b4579cb0edfc0dd3faae360fa8f1dda03"
POI_EMISSION = ("Tm5", "Est", "covariate:tm5_poi_p90_version")
MX4_EMISSION = ("Mx4", "F", "covariate")
S1_EMISSION = ("S1", "F", "covariate:lag_mx4_f_covariate")


def digest_feature(pairs: list[tuple[str, str]], values: np.ndarray,
                   defined: np.ndarray) -> str:
    lines = [f"{g},{r},{int(ok)}," + (format(float(v), ".17g") if ok else "NULL")
             for (g, r), v, ok in zip(pairs, values, defined)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def make_interaction(rank: np.ndarray, rank_defined: np.ndarray,
                     mx4: np.ndarray, mx4_defined: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    q = np.asarray(rank, dtype=np.float64)
    qmask = np.asarray(rank_defined, dtype=bool)
    x = np.asarray(mx4, dtype=np.float64)
    xmask = np.asarray(mx4_defined, dtype=bool)
    if q.ndim != 1 or any(v.shape != q.shape for v in (qmask, x, xmask)):
        raise ValueError("rank, Mx4 values and masks must be aligned vectors")
    if np.any(qmask & (~np.isfinite(q) | (q < 0.) | (q > 1.))) or np.any(~qmask & np.isfinite(q)):
        raise ValueError("defined POI ranks must be bounded; undefined ranks remain null")
    if np.any(xmask & (~np.isfinite(x) | (x < 0.) | (x > 1.))) or np.any(~xmask & np.isfinite(x)):
        raise ValueError("defined Mx4 F values must be bounded; undefined values remain null")
    joint = qmask & xmask
    product = np.full(q.shape, np.nan)
    product[joint] = q[joint] * x[joint]
    if np.any(joint & (~np.isfinite(product) | (product < 0.) | (product > 1.))):
        raise ValueError("defined rank×Mx4 interaction must be finite and bounded [0,1]")
    return product, joint


def output_table(pairs: list[tuple[str, str]], poi: np.ndarray, poi_mask: np.ndarray,
                 rank: np.ndarray, mx4: np.ndarray, mx4_mask: np.ndarray,
                 s1: np.ndarray, s1_mask: np.ndarray, joint: np.ndarray,
                 product: np.ndarray, prediction: np.ndarray, residual: np.ndarray,
                 folds: np.ndarray) -> pa.Table:
    n = len(pairs)
    values = (poi, poi_mask, rank, mx4, mx4_mask, s1, s1_mask, joint, product, prediction, residual, folds)
    if any(np.asarray(x).shape != (n,) for x in values):
        raise ValueError("output arrays must align to exact official GEOID-region IDs")
    expected_joint = np.asarray(poi_mask, bool) & np.asarray(mx4_mask, bool) & np.asarray(s1_mask, bool)
    if not np.array_equal(joint, expected_joint):
        raise ValueError("cross-fit support must preserve the exact POI/Mx4/S1 source-mask intersection")
    if np.any(joint & (~np.isfinite(product) | ~np.isfinite(prediction) | ~np.isfinite(residual))):
        raise ValueError("defined cross-fit rows must have finite product, prediction and residual")
    if np.any(~joint & (np.isfinite(product) | np.isfinite(prediction) | np.isfinite(residual))):
        raise ValueError("undefined outputs must remain null")
    return pa.table({
        "GEOID": pa.array([g for g, _ in pairs], type=pa.string()),
        "region": pa.array([r for _, r in pairs], type=pa.string()),
        "Tm5_POI_p90_version": pa.array([float(v) if ok else None for v, ok in zip(poi, poi_mask)], type=pa.float64()),
        "POI_version_defined": pa.array(poi_mask, type=pa.bool_()),
        "within_region_midpercentile": pa.array([float(v) if ok else None for v, ok in zip(rank, poi_mask)], type=pa.float64()),
        "Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(mx4, mx4_mask)], type=pa.float64()),
        "Mx4_F_defined": pa.array(mx4_mask, type=pa.bool_()),
        "S1_lagged_Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(s1, s1_mask)], type=pa.float64()),
        "S1_lagged_Mx4_F_defined": pa.array(s1_mask, type=pa.bool_()),
        "rank_x_Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(product, joint)], type=pa.float64()),
        "joint_defined": pa.array(joint, type=pa.bool_()),
        "fold": pa.array(folds, type=pa.int8()),
        "crossfit_prediction": pa.array([float(v) if ok else None for v, ok in zip(prediction, joint)], type=pa.float64()),
        "crossfit_residual": pa.array([float(v) if ok else None for v, ok in zip(residual, joint)], type=pa.float64()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    rank_report_path = ROOT / "reports/tm5-poi-p90-region-rank.json"
    global_report_path = ROOT / "reports/tm5-p90-version-mx4-interaction.json"
    s1_report_path = ROOT / "reports/s1-mx4-lagged-innovation.json"
    rank_report = json.loads(rank_report_path.read_text(encoding="utf-8"))
    global_report = json.loads(global_report_path.read_text(encoding="utf-8"))
    s1_report = json.loads(s1_report_path.read_text(encoding="utf-8"))
    if (rank_report["scope"]["official_ids"] != EXPECTED_N
            or rank_report["scope"]["defined_ids"] != 9377
            or not np.isclose(rank_report["measure_after"]["p95_absolute_change_from_global_log_normalized"],
                              0.5005576527275482, rtol=0, atol=1e-12)):
        raise ValueError("regional POI-rank baseline differs from the assigned measure-before")
    if (global_report["scope"]["official_ids"] != EXPECTED_N
            or global_report["scope"]["coverage_fraction"] < MIN_COVERAGE
            or not np.isclose(global_report["measure_after"]["p95_absolute_change_from_normalized_p90"],
                              0.9415887339401863, rtol=0, atol=1e-12)):
        raise ValueError("global POI-p90×Mx4 source screen differs from the assigned measure-before")
    pairs = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(pairs) != EXPECTED_N or len(set(pairs)) != EXPECTED_N
            or len({g for g, _ in pairs}) != EXPECTED_N or set(r for _, r in pairs) != set(REGIONS)):
        raise ValueError("official input must be an exact 9,379-ID four-region partition")
    partition_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in pairs).encode()).hexdigest()
    if partition_sha != rank_report["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official partition differs from validated regional-rank screen")
    poi_path, mx4_path, s1_path = (workspace / "ideas/Tm5.parquet", workspace / "ideas/Mx4.parquet",
                                   workspace / "ideas/S1.parquet")
    poi, poi_mask = read_tm5_emission(poi_path, TM5_SHA, *POI_EMISSION, pairs)
    mx4, mx4_mask, mx4_file_sha = read_emission(mx4_path, MX4_SHA, *MX4_EMISSION, pairs)
    s1, s1_mask, s1_file_sha = read_emission(s1_path, S1_SHA, *S1_EMISSION, pairs)
    if (mx4_file_sha != MX4_SHA or s1_file_sha != S1_SHA or int(poi_mask.sum()) != 9377
            or int(mx4_mask.sum()) != EXPECTED_N or int(s1_mask.sum()) != EXPECTED_N):
        raise ValueError("frozen POI/Mx4/S1 source hashes or coverage differ from audited baselines")
    rank = region_rank(pairs, poi, poi_mask)
    product, product_mask = make_interaction(rank, poi_mask, mx4, mx4_mask)
    joint = product_mask & s1_mask
    if int(joint.sum()) != int(poi_mask.sum()) or not np.array_equal(joint, poi_mask & mx4_mask & s1_mask):
        raise ValueError("joint product and both frozen predictors must preserve the exact source-mask intersection")
    prediction = np.full(EXPECTED_N, np.nan)
    residual = np.full(EXPECTED_N, np.nan)
    fold_ids = np.full(EXPECTED_N, -1, dtype=np.int8)
    regional: dict[str, Any] = {}
    for region in REGIONS:
        idx = np.asarray([i for i, (_, r) in enumerate(pairs) if r == region], dtype=np.int64)
        ids = [pairs[i][0] for i in idx]
        pred, resid, fold_reports, folds = crossfit_region(
            ids, region, product[idx], mx4[idx], s1[idx], joint[idx])
        prediction[idx], residual[idx], fold_ids[idx] = pred, resid, folds
        local = joint[idx]
        regional[region] = {
            "official_ids": int(idx.size), "joint_defined_ids": int(local.sum()),
            "coverage_fraction": float(local.mean()), "product_distribution": distribution(product[idx][local]),
            "crossfit_residual_distribution": distribution(resid[local]),
            "residual_variance_ratio": float(resid[local].var(ddof=0) / product[idx][local].var(ddof=0)),
            "residual_pearson_r_with_Mx4_F": pearson(resid[local], mx4[idx][local]),
            "residual_pearson_r_with_S1_lagged_Mx4_F": pearson(resid[local], s1[idx][local]),
            "folds": fold_reports,
        }
    raw_values = product[joint]
    residual_values = residual[joint]
    raw_variance = float(raw_values.var(ddof=0))
    residual_variance = float(residual_values.var(ddof=0))
    ratio = residual_variance / raw_variance if raw_variance > 0 else None
    correlations = {"Mx4_F": pearson(residual_values, mx4[joint]),
                    "S1_lagged_Mx4_F": pearson(residual_values, s1[joint])}
    corr_pass = all(v is not None and abs(v) < MAX_ABS_RESIDUAL_CORRELATION for v in correlations.values())
    ratio_pass = ratio is not None and ratio >= MIN_VARIANCE_RATIO
    coverage = float(joint.mean())
    coverage_pass = coverage >= MIN_COVERAGE
    registry_path = ROOT / "config/ideas.json"
    registry_sha_before = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    after_hashes = {"Tm5": hashlib.sha256(poi_path.read_bytes()).hexdigest(),
                    "Mx4": hashlib.sha256(mx4_path.read_bytes()).hexdigest(),
                    "S1": hashlib.sha256(s1_path.read_bytes()).hexdigest()}
    registry_sha_after = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    frozen_unchanged = after_hashes == {"Tm5": TM5_SHA, "Mx4": MX4_SHA, "S1": S1_SHA} and registry_sha_after == registry_sha_before
    if not frozen_unchanged:
        raise AssertionError("frozen feature artifacts or registry changed")
    table = output_table(pairs, poi, poi_mask, rank, mx4, mx4_mask, s1, s1_mask,
                         joint, product, prediction, residual, fold_ids)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("cross-fit artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    acceptance = {
        "exact_9379_id_alignment": bool(table.num_rows == EXPECTED_N and len(set(pairs)) == EXPECTED_N),
        "coverage_fraction": coverage, "coverage_at_least_98_59_percent": bool(coverage_pass),
        "finite_interaction_bounded_0_1": bool(np.isfinite(product[joint]).all() and np.all((product[joint] >= 0.) & (product[joint] <= 1.))),
        "all_joint_ids_have_out_of_fold_prediction_and_residual": int(np.isfinite(residual).sum()) == int(joint.sum()),
        "crossfit_residual_variance_ratio": ratio, "residual_variance_at_least_10_percent": bool(ratio_pass),
        "pooled_residual_pearson_correlations": correlations,
        "absolute_residual_correlation_below_0_80_with_each_predictor": bool(corr_pass),
        "frozen_Tm5_Mx4_S1_and_registry_unchanged": bool(frozen_unchanged),
        "distinct_screen_pass": bool(coverage_pass and ratio_pass and corr_pass and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "screen": "Within-region Tm5 POI p90 midpercentile × frozen Mx4 F; five-fold cross-fit residual after Mx4 F and S1 lagged Mx4 F; unregistered diagnostic",
        "method": {"feature": "rank product = average-tie within-region empirical midpercentile of defined provided Tm5 POI p90 version × frozen Mx4 F.",
                   "cross_fit": "Five deterministic region-local GEOID folds. For each fold, OLS with intercept, frozen Mx4 F and S1 lagged Mx4 F is fit on the other four folds and predicts the held-out fold. No tuning, labels, or imputation.",
                   "variance_ratio": "Pooled population variance (ddof=0) of held-out residuals divided by rank×Mx4 product variance on the same complete-case support.",
                   "correlation": "Pooled Pearson correlation of held-out residuals with each frozen predictor; regional values are descriptive.",
                   "nulls": "Source nulls remain null; interaction and cross-fit outputs are defined only on the exact POI-rank, Mx4, and S1 joint support.",
                   "unregistered": True},
        "measure_before": {"global_POI_version_x_Mx4_passed_p95_change": global_report["measure_after"]["p95_absolute_change_from_normalized_p90"],
                           "regional_rank_transform_p95_change_from_global": rank_report["measure_after"]["p95_absolute_change_from_global_log_normalized"],
                           "regional_rank_x_Mx4_feature_previously_measured": False,
                           "global_interaction_report_sha256": hashlib.sha256(global_report_path.read_bytes()).hexdigest(),
                           "region_rank_report_sha256": hashlib.sha256(rank_report_path.read_bytes()).hexdigest()},
        "scope": {"official_ids": len(pairs), "joint_defined_ids": int(joint.sum()),
                  "joint_undefined_ids": int((~joint).sum()), "coverage_fraction": coverage,
                  "poi_version_defined_ids": int(poi_mask.sum()), "Mx4_defined_ids": int(mx4_mask.sum()),
                  "S1_lagged_Mx4_defined_ids": int(s1_mask.sum()), "regions": regional,
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_reference_data_read": False, "external_data_read": False,
                  "leaderboard_feedback_used": False, "registry_integration": False, "uploads": 0},
        "measure_after": {"rank_x_Mx4_product_distribution": distribution(raw_values),
                          "product_variance": raw_variance, "crossfit_residual_distribution": distribution(residual_values),
                          "crossfit_residual_variance": residual_variance, "crossfit_residual_variance_ratio": ratio,
                          "pooled_residual_pearson_correlations": correlations, "regions": regional},
        "thresholds": {"minimum_coverage_inclusive": MIN_COVERAGE,
                       "minimum_residual_variance_ratio_inclusive": MIN_VARIANCE_RATIO,
                       "maximum_absolute_residual_predictor_correlation_exclusive": MAX_ABS_RESIDUAL_CORRELATION,
                       "interaction_bounds_inclusive": [0., 1.]},
        "acceptance": acceptance,
        "provenance": {"official_geoid_region_sha256": partition_sha,
                       "Tm5_artifact": {"path": str(poi_path), "sha256_before": TM5_SHA, "sha256_after": after_hashes["Tm5"],
                                        "poi_emission_sha256": digest_feature(pairs, poi, poi_mask)},
                       "Mx4_artifact": {"path": str(mx4_path), "sha256_before": MX4_SHA, "sha256_after": after_hashes["Mx4"]},
                       "S1_artifact": {"path": str(s1_path), "sha256_before": S1_SHA, "sha256_after": after_hashes["S1"]},
                       "registry_sha256_before": registry_sha_before, "registry_sha256_after": registry_sha_after,
                       "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                           "sha256": hashlib.sha256(payload).hexdigest(),
                                           "columns": table.column_names, "deterministic_bytes": True},
                       "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "interpretation": "The POI regional-rank×Mx4 interaction passes the preregistered distinctness screen; keep as an unregistered diagnostic only, not an accuracy claim or integration authorization." if acceptance["distinct_screen_pass"] else
                         "One or more preregistered coverage, boundedness, residual-variance or residual-correlation gates failed; do not integrate.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm5-poi-region-rank-mx4-interaction.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "tm5-poi-region-rank-mx4-interaction" / "rank-mx4-crossfit-residuals.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"acceptance": report["acceptance"], "measure_after": {
        "crossfit_residual_variance_ratio": report["measure_after"]["crossfit_residual_variance_ratio"],
        "pooled_residual_pearson_correlations": report["measure_after"]["pooled_residual_pearson_correlations"]},
        "output_artifact": report["provenance"]["output_artifact"], "report": str(ROOT / args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
