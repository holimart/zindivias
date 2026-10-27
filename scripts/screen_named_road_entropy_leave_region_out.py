#!/usr/bin/env python3
"""Test transport of provided named-road entropy covariates to unseen regions."""
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
from audit_mx4p_conditional_innovation import parquet_bytes, read_emission  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_N = 9379
ENTROPY_REPORT = ROOT / "reports/named-road-class-entropy.json"
ENTROPY_ARTIFACT_SHA = "41610caca547f76604a8430f7cc0dfcdf628ea3f431632c5e5da11809da92f38"
MX4_SHA = "8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af"
S1_SHA = "632829cf29281aa16487707cb555f52b4579cb0edfc0dd3faae360fa8f1dda03"
FEATURE_NAMES = ("log_total_named_road_km", "pct_urban", "Mx4_F", "S1_lagged_Mx4_F")
ARTIFACT_COLUMNS = ["GEOID", "region", "class_mix_entropy", "entropy_defined", "total_named_road_km",
                    "pct_urban", "Mx4_F", "Mx4_F_defined", "S1_lagged_Mx4_F",
                    "S1_lagged_Mx4_F_defined"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fit_training_fold(train_x: np.ndarray, train_y: np.ndarray, test_x: np.ndarray
                      ) -> tuple[np.ndarray, float, dict[str, Any]]:
    """Fit training-only median imputation, scaling, OLS and mean baseline."""
    x_train = np.asarray(train_x, dtype=np.float64)
    y_train = np.asarray(train_y, dtype=np.float64)
    x_test = np.asarray(test_x, dtype=np.float64)
    if (x_train.ndim != 2 or x_test.ndim != 2 or x_train.shape[1] != x_test.shape[1]
            or y_train.shape != (x_train.shape[0],) or x_train.shape[0] <= x_train.shape[1] + 1
            or x_test.shape[0] == 0):
        raise ValueError("invalid training or held-region arrays")
    if (not np.isfinite(y_train).all() or not np.isfinite(x_train[~np.isnan(x_train)]).all()
            or not np.isfinite(x_test[~np.isnan(x_test)]).all()):
        raise ValueError("predictors may be finite or NaN; training entropy must be finite")
    if np.isinf(x_train).any() or np.isinf(x_test).any():
        raise ValueError("infinite predictor values are invalid")
    missing_train = np.isnan(x_train)
    if np.any(missing_train.all(axis=0)):
        raise ValueError("a predictor is entirely missing in the training regions")
    medians = np.nanmedian(x_train, axis=0)
    train_imputed = np.where(missing_train, medians, x_train)
    test_missing = np.isnan(x_test)
    test_imputed = np.where(test_missing, medians, x_test)
    means = train_imputed.mean(axis=0)
    scales = train_imputed.std(axis=0, ddof=0)
    constant = scales <= np.finfo(np.float64).eps
    scales[constant] = 1.0
    train_z = (train_imputed - means) / scales
    test_z = (test_imputed - means) / scales
    design_train = np.column_stack((np.ones(len(train_y)), train_z))
    design_test = np.column_stack((np.ones(len(test_z)), test_z))
    coefficients, _, rank, singular = np.linalg.lstsq(design_train, y_train, rcond=None)
    diagnostics = {
        "training_rows": int(len(y_train)), "heldout_rows": int(len(test_z)),
        "predictor_names": list(FEATURE_NAMES),
        "training_missing_by_predictor": {name: int(missing_train[:, j].sum()) for j, name in enumerate(FEATURE_NAMES)},
        "heldout_missing_by_predictor": {name: int(test_missing[:, j].sum()) for j, name in enumerate(FEATURE_NAMES)},
        "training_only_medians": {name: float(medians[j]) for j, name in enumerate(FEATURE_NAMES)},
        "training_only_means_after_imputation": {name: float(means[j]) for j, name in enumerate(FEATURE_NAMES)},
        "training_only_scales_after_imputation": {name: float(scales[j]) for j, name in enumerate(FEATURE_NAMES)},
        "constant_training_predictors": [FEATURE_NAMES[j] for j in np.flatnonzero(constant)],
        "standardized_ols_coefficients_including_intercept": [float(v) for v in coefficients],
        "design_rank": int(rank), "design_columns": int(design_train.shape[1]),
        "minimum_singular_value": float(singular.min()) if singular.size else None,
        "training_mean_entropy_baseline": float(y_train.mean()),
    }
    return design_test @ coefficients, float(y_train.mean()), diagnostics


def r2_against_training_mean(y: np.ndarray, prediction: np.ndarray, training_mean: float) -> dict[str, float]:
    y = np.asarray(y, dtype=np.float64)
    prediction = np.asarray(prediction, dtype=np.float64)
    if y.ndim != 1 or prediction.shape != y.shape or y.size == 0:
        raise ValueError("R2 inputs must be aligned nonempty vectors")
    if not np.isfinite(y).all() or not np.isfinite(prediction).all() or not np.isfinite(training_mean):
        raise ValueError("R2 inputs must be finite")
    model_sse = float(np.sum((y - prediction) ** 2))
    mean_sse = float(np.sum((y - training_mean) ** 2))
    if mean_sse <= 0:
        raise ValueError("training-mean baseline has zero held-out error sum of squares")
    return {"model_sse": model_sse, "training_mean_baseline_sse": mean_sse,
            "r2_vs_training_mean": 1. - model_sse / mean_sse}


def _as_float(value: Any) -> float:
    return float(value) if value is not None and np.isfinite(float(value)) else float("nan")


def load_inputs(workspace: Path) -> tuple[list[tuple[str, str]], np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    pairs = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(pairs) != EXPECTED_N or len(set(pairs)) != EXPECTED_N
            or len({g for g, _ in pairs}) != EXPECTED_N or set(r for _, r in pairs) != set(REGIONS)):
        raise ValueError("official scored ID source must be an exact 9,379-row/four-region partition")
    index = {pair: i for i, pair in enumerate(pairs)}
    entropy_report = json.loads(ENTROPY_REPORT.read_text(encoding="utf-8"))
    artifact_path = Path(entropy_report["provenance"]["output_artifact"]["path"])
    artifact_sha = sha256_file(artifact_path)
    if artifact_sha != ENTROPY_ARTIFACT_SHA or artifact_sha != entropy_report["provenance"]["output_artifact"]["sha256"]:
        raise ValueError("completed named-road entropy artifact hash mismatch")
    import pyarrow.parquet as pq
    table = pq.read_table(artifact_path, columns=ARTIFACT_COLUMNS)
    if table.num_rows != EXPECTED_N:
        raise ValueError("entropy artifact row count differs from authoritative IDs")
    y = np.full(EXPECTED_N, np.nan, dtype=np.float64)
    defined = np.zeros(EXPECTED_N, dtype=bool)
    x_raw = np.full((EXPECTED_N, 4), np.nan, dtype=np.float64)
    mx4_defined = np.zeros(EXPECTED_N, dtype=bool)
    s1_defined = np.zeros(EXPECTED_N, dtype=bool)
    seen: set[tuple[str, str]] = set()
    columns = {name: table.column(name).to_pylist() for name in ARTIFACT_COLUMNS}
    for row in range(table.num_rows):
        pair = (str(columns["GEOID"][row]), str(columns["region"][row]))
        if pair not in index or pair in seen:
            raise ValueError(f"unexpected or duplicate entropy artifact ID: {pair}")
        seen.add(pair); i = index[pair]
        raw_mask = columns["entropy_defined"][row]
        if not isinstance(raw_mask, bool):
            raise ValueError(f"invalid entropy defined mask for {pair}")
        defined[i] = raw_mask
        y[i] = _as_float(columns["class_mix_entropy"][row])
        total = _as_float(columns["total_named_road_km"][row])
        urban = _as_float(columns["pct_urban"][row])
        mx4 = _as_float(columns["Mx4_F"][row]); s1 = _as_float(columns["S1_lagged_Mx4_F"][row])
        mx4_defined[i] = columns["Mx4_F_defined"][row] is True
        s1_defined[i] = columns["S1_lagged_Mx4_F_defined"][row] is True
        if raw_mask:
            if not np.isfinite(total) or total <= 0 or not np.isfinite(y[i]) or not 0 <= y[i] <= 1:
                raise ValueError(f"defined entropy must have finite bounded entropy and positive length for {pair}")
            x_raw[i, 0] = np.log(total)
        x_raw[i, 1:] = (urban, mx4, s1)
    if seen != set(pairs) or not np.array_equal(np.isfinite(y), defined):
        raise ValueError("entropy artifact does not exactly match official IDs and defined mask")
    prior_ratio = float(entropy_report["measure_after"]["residual_variance_ratio"])
    if (defined.sum() != 9247 or int(entropy_report["scope"]["joint_crossfit_ids"]) != 9228
            or abs(prior_ratio - 0.6607401765971582) > 1e-12):
        raise ValueError("completed entropy artifact counts or residual ratio differ from the dispatch measure-before")
    if not np.array_equal(np.isfinite(x_raw[:, 2]), mx4_defined) or not np.array_equal(np.isfinite(x_raw[:, 3]), s1_defined):
        raise ValueError("frozen predictor null masks differ from the completed entropy artifact")
    if np.any(np.isfinite(x_raw[:, 1]) & ((x_raw[:, 1] < 0) | (x_raw[:, 1] > 1))):
        raise ValueError("pct_urban must be in [0,1]")
    mx4_path, s1_path = workspace / "ideas/Mx4.parquet", workspace / "ideas/S1.parquet"
    expected_mx4 = entropy_report["provenance"]["Mx4"]["sha256"]
    expected_s1 = entropy_report["provenance"]["S1"]["sha256"]
    if expected_mx4 != MX4_SHA or expected_s1 != S1_SHA:
        raise ValueError("frozen Mx4/S1 hashes differ from the completed entropy report")
    mx4_values, mx4_mask, mx4_hash = read_emission(mx4_path, MX4_SHA, "Mx4", "F", "covariate", pairs)
    s1_values, s1_mask, s1_hash = read_emission(s1_path, S1_SHA, "S1", "F", "covariate:lag_mx4_f_covariate", pairs)
    if (not np.array_equal(mx4_values, x_raw[:, 2], equal_nan=True)
            or not np.array_equal(s1_values, x_raw[:, 3], equal_nan=True)
            or not np.array_equal(mx4_mask, mx4_defined) or not np.array_equal(s1_mask, s1_defined)):
        raise ValueError("frozen Mx4/S1 emissions differ from values embedded in entropy artifact")
    region = np.asarray([r for _, r in pairs], dtype=object)
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in pairs).encode()).hexdigest()
    return pairs, region, y, x_raw, {
        "official_geoid_region_sha256": official_sha,
        "entropy_artifact": {"path": str(artifact_path), "sha256": artifact_sha,
                            "rows": table.num_rows, "columns_read": ARTIFACT_COLUMNS,
                            "entropy_defined_ids": int(defined.sum()),
                            "complete_case_covariate_ids_from_source_screen": 9228},
        "frozen_predictors": {"Mx4": {"path": str(mx4_path), "sha256": mx4_hash},
                              "S1": {"path": str(s1_path), "sha256": s1_hash}},
        "entropy_report": {"path": str(ENTROPY_REPORT), "sha256": sha256_file(ENTROPY_REPORT),
                           "within_region_fivefold_residual_variance_ratio": prior_ratio},
        "missing_predictor_counts_entropy_defined": {
            name: int(np.count_nonzero(defined & np.isnan(x_raw[:, j]))) for j, name in enumerate(FEATURE_NAMES)},
        "official_ids": EXPECTED_N,
    }


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    pairs, regions, entropy, predictors, provenance = load_inputs(workspace)
    defined = np.isfinite(entropy)
    oof_prediction = np.full(len(pairs), np.nan, dtype=np.float64)
    oof_baseline = np.full(len(pairs), np.nan, dtype=np.float64)
    regional: dict[str, Any] = {}
    total_model_sse = total_baseline_sse = 0.
    heldout_count = 0
    for held_region in REGIONS:
        held = (regions == held_region) & defined
        train = (regions != held_region) & defined
        prediction, baseline, diagnostics = fit_training_fold(predictors[train], entropy[train], predictors[held])
        metrics = r2_against_training_mean(entropy[held], prediction, baseline)
        ids = np.flatnonzero((regions == held_region) & defined)
        oof_prediction[ids] = prediction
        oof_baseline[ids] = baseline
        total_model_sse += metrics["model_sse"]
        total_baseline_sse += metrics["training_mean_baseline_sse"]
        heldout_count += int(held.sum())
        regional[held_region] = {
            "official_ids": int(np.count_nonzero(regions == held_region)),
            "entropy_defined_heldout_ids": int(held.sum()), "training_entropy_defined_ids": int(train.sum()),
            "training_regions": [region for region in REGIONS if region != held_region],
            **metrics, "training_only_preprocessing_and_model": diagnostics,
        }
    if heldout_count != int(defined.sum()) or np.any(defined & (~np.isfinite(oof_prediction) | ~np.isfinite(oof_baseline))):
        raise AssertionError("leave-one-region-out predictions do not cover the entropy-defined support exactly")
    pooled_r2 = 1. - total_model_sse / total_baseline_sse
    positive_regions = sum(regional[r]["r2_vs_training_mean"] > 0. for r in REGIONS)
    acceptance = {"pooled_r2_at_least_0_10": bool(pooled_r2 >= .10),
                  "positive_held_region_r2_in_at_least_3_of_4": bool(positive_regions >= 3),
                  "transportability_screen_pass": bool(pooled_r2 >= .10 and positive_regions >= 3)}
    output = pa.table({
        "GEOID": pa.array([g for g, _ in pairs], type=pa.string()),
        "region": pa.array([r for _, r in pairs], type=pa.string()),
        "normalized_named_road_class_entropy": pa.array([float(v) if ok else None for v, ok in zip(entropy, defined)], type=pa.float64()),
        "entropy_defined": pa.array(defined, type=pa.bool_()),
        "log_total_named_road_km": pa.array([float(v) if np.isfinite(v) else None for v in predictors[:, 0]], type=pa.float64()),
        "pct_urban": pa.array([float(v) if np.isfinite(v) else None for v in predictors[:, 1]], type=pa.float64()),
        "Mx4_F": pa.array([float(v) if np.isfinite(v) else None for v in predictors[:, 2]], type=pa.float64()),
        "S1_lagged_Mx4_F": pa.array([float(v) if np.isfinite(v) else None for v in predictors[:, 3]], type=pa.float64()),
        "heldout_prediction": pa.array([float(v) if np.isfinite(v) else None for v in oof_prediction], type=pa.float64()),
        "training_mean_baseline": pa.array([float(v) if np.isfinite(v) else None for v in oof_baseline], type=pa.float64()),
    })
    if not np.array_equal(np.isfinite(oof_prediction), defined) or not np.array_equal(np.isfinite(oof_baseline), defined):
        raise AssertionError("prediction and baseline masks must exactly match entropy-defined support")
    payload = parquet_bytes(output)
    if payload != parquet_bytes(output):
        raise ValueError("LORO prediction artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    report = {
        "schema_version": 1,
        "screen": "Cross-region transportability of provided named-road class-share entropy; leave-one-region-out; unregistered",
        "measure_before": {"within_region_fivefold_residual_variance_ratio": 0.6607401765971582,
                           "jointly_eligible_ids": 9228, "held_region_out_measurement_available": False,
                           "source_report_sha256": provenance["entropy_report"]["sha256"]},
        "outcome": "Normalized Shannon entropy of provided Overture motorway/trunk/primary/secondary named-road length shares; undefined where named-road length is zero.",
        "method": {"split": "Four folds; hold out one entire study region and train on the other three. No rows from the held region enter preprocessing or model fitting.",
                   "predictors": list(FEATURE_NAMES),
                   "preprocessing": "For each fold, fit column medians, then means and population standard deviations after imputation, using only defined-entropy training rows from the other three regions. Apply those training-only transformations to the held region. Zero-variance training features use scale 1.",
                   "model": "One global ordinary least-squares linear model with intercept, fitted to standardized predictors and entropy on the three training regions; no region indicator or tuning.",
                   "baseline": "For each held region, predict the mean entropy from the other three training regions only.",
                   "metric": "Regional R2=1-SSE_model/SSE_training-mean-baseline within the held region. Pooled R2=1-sum of held-region model SSE/sum of each fold's held-region training-mean baseline SSE.",
                   "support": "All official tracts with defined entropy are evaluated. Missing predictors are imputed from training-region medians; entropy is never imputed.",
                   "registry_integration": False},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": provenance["official_geoid_region_sha256"],
                  "entropy_defined_ids": int(defined.sum()), "entropy_null_ids": int((~defined).sum()),
                  "heldout_prediction_ids": int(np.isfinite(oof_prediction).sum()),
                  "region_fold_count": len(REGIONS), "regions": list(REGIONS),
                  "exact_entropy_null_mask_alignment": bool(np.array_equal(np.isfinite(entropy), defined)),
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_references_read": False, "external_data_used": [],
                  "leaderboard_feedback_used": False, "estimator_or_registry_changed": False, "uploads": 0},
        "measure_after": {"pooled_leave_region_out_heldout_r2": float(pooled_r2),
                          "pooled_model_sse": float(total_model_sse),
                          "pooled_training_mean_baseline_sse": float(total_baseline_sse),
                          "pooled_entropy_defined_heldout_ids": int(heldout_count),
                          "positive_held_region_r2_count": int(positive_regions),
                          "regions": regional},
        "acceptance": acceptance,
        "provenance": {**provenance,
                       "output_artifact": {"path": str(artifact_path), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": output.num_rows, "columns": output.column_names,
                                           "deterministic_serialization": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The feature passed both preregistered leave-region-out gates; this is a feature transportability diagnostic only and is not target accuracy or evidence for estimator promotion." if acceptance["transportability_screen_pass"] else
                         "The feature did not pass both preregistered leave-region-out gates; retain it as a region-local diagnostic only, with no target-accuracy or promotion claim.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/named-road-entropy-leave-region-out.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "named-road-entropy-leave-region-out/leave-region-out-predictions.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"scope": report["scope"], "measure_after": report["measure_after"],
                      "acceptance": report["acceptance"], "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
