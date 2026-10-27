#!/usr/bin/env python3
"""Test region-varying source×road entropy effects with fixed ridge shrinkage."""
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
from audit_mx4p_conditional_innovation import parquet_bytes  # noqa: E402
from screen_source_road_entropy_interaction import (  # noqa: E402
    EXPECTED_N, FEATURES, N_FOLDS, POI_MAIN_RATIO, POI_REPORT, REGIONS,
    ROAD_MAIN_RATIO, ROAD_REPORT, RESIDUAL_REPORT, load_inputs, mask_sha256,
    paired_delta_r2, sha256_file,
)

INTERACTION_REPORT = ROOT / "reports/source-road-entropy-interaction.json"
EXPECTED_POOLED_INTERACTION_DELTA_R2 = -4.8250971948343004e-05
EXPECTED_POSITIVE_FOLDS = 0
RIDGE_ALPHA = 100.0
MIN_POOLED_DELTA_R2 = 0.01
MIN_POSITIVE_REGIONS = 3


def deviation_design(product: np.ndarray, regions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Common product and three contrasts encode four sum-zero regional deviations."""
    x = np.asarray(product, dtype=np.float64)
    reg = np.asarray(regions, dtype=object)
    if x.ndim != 1 or reg.shape != x.shape or not np.isfinite(x).all():
        raise ValueError("interaction product and region IDs must be aligned and finite")
    if set(reg.tolist()) != set(REGIONS):
        raise ValueError("all four official regions are required")
    # Four deltas sum to zero; the last region is the negative sum of the first three.
    contrasts = np.column_stack([
        x * ((reg == region).astype(np.float64) - (reg == REGIONS[-1]).astype(np.float64))
        for region in REGIONS[:-1]
    ])
    return np.column_stack((x, contrasts)), contrasts


def deviation_penalty_matrix(n_columns: int, contrast_start: int, alpha: float = RIDGE_ALPHA) -> np.ndarray:
    """Penalty alpha * sum_r delta_r^2, with sum-to-zero four-region deviations."""
    if (contrast_start < 0 or n_columns - contrast_start != 3 or alpha < 0
            or not np.isfinite(alpha)):
        raise ValueError("ridge penalty needs exactly three deviation contrasts and a finite nonnegative alpha")
    penalty = np.zeros((n_columns, n_columns), dtype=np.float64)
    # delta_4 = -(delta_1 + delta_2 + delta_3), so sum(delta_r^2)=d'(I+11')d.
    penalty[contrast_start:, contrast_start:] = alpha * (np.eye(3) + np.ones((3, 3)))
    return penalty


def ridge_coefficients(design: np.ndarray, target: np.ndarray, *, contrast_start: int,
                       alpha: float = RIDGE_ALPHA) -> np.ndarray:
    x = np.asarray(design, dtype=np.float64)
    y = np.asarray(target, dtype=np.float64)
    if x.ndim != 2 or y.shape != (x.shape[0],) or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("ridge design and target must be aligned and finite")
    penalty = deviation_penalty_matrix(x.shape[1], contrast_start, alpha)
    return np.linalg.solve(x.T @ x + penalty, x.T @ y)


def region_slopes(common: float, independent_deviations: np.ndarray) -> dict[str, float]:
    deviations = np.asarray(independent_deviations, dtype=np.float64)
    if deviations.shape != (3,) or not np.isfinite(deviations).all() or not np.isfinite(common):
        raise ValueError("need one common coefficient and three finite deviation contrasts")
    full = np.r_[deviations, -float(deviations.sum())]
    return {region: float(common + full[i]) for i, region in enumerate(REGIONS)}


def crossfit_region_shrinkage(geoids: list[str], regions: np.ndarray, folds: np.ndarray,
                              outcome: np.ndarray, features: np.ndarray, mask: np.ndarray,
                              *, alpha: float = RIDGE_ALPHA) -> dict[str, Any]:
    """Nested OOF models, with train-only scaling and ridge penalty only on slope deviations."""
    reg = np.asarray(regions, dtype=object)
    fold_ids = np.asarray(folds, dtype=np.int8)
    y = np.asarray(outcome, dtype=np.float64)
    x = np.asarray(features, dtype=np.float64)
    use = np.asarray(mask, dtype=bool)
    n = len(geoids)
    if (len(set(geoids)) != n or reg.shape != (n,) or fold_ids.shape != (n,)
            or y.shape != (n,) or x.shape != (n, 5) or use.shape != (n,)):
        raise ValueError("crossfit inputs must be exact aligned vectors/matrix with unique GEOIDs")
    if (set(reg.tolist()) != set(REGIONS) or np.any((fold_ids < 0) | (fold_ids >= N_FOLDS))
            or np.any(use & (~np.isfinite(y) | ~np.isfinite(x).all(axis=1)))):
        raise ValueError("need four regions, fixed fold IDs and finite complete-case inputs")
    if alpha != RIDGE_ALPHA:
        raise ValueError("ridge alpha is fixed at 100; outer tuning is forbidden")
    base_oof = np.full(n, np.nan)
    augmented_oof = np.full(n, np.nan)
    product_oof = np.full(n, np.nan)
    fold_metrics: list[dict[str, Any]] = []
    fold_coefficients: list[dict[str, Any]] = []
    region_codes = {region: idx for idx, region in enumerate(REGIONS)}

    for fold in range(N_FOLDS):
        train, test = use & (fold_ids != fold), use & (fold_ids == fold)
        if not test.any() or train.sum() <= 12:
            raise ValueError(f"fold {fold} has insufficient training/held-out complete cases")
        train_rows, test_rows = np.flatnonzero(train), np.flatnonzero(test)
        # Match the pooled-interaction baseline's training-only transformations exactly.
        base_train = np.empty((int(train.sum()), 3)); base_test = np.empty((int(test.sum()), 3))
        global_scalers: dict[str, dict[str, float]] = {}
        for j, name in enumerate(FEATURES[:3]):
            mean, sd = float(x[train, j].mean()), float(x[train, j].std(ddof=0))
            if not np.isfinite(sd) or sd <= 0.:
                raise ValueError(f"feature {name} has zero training-fold scale")
            base_train[:, j] = (x[train, j] - mean) / sd
            base_test[:, j] = (x[test, j] - mean) / sd
            global_scalers[name] = {"training_mean": mean, "training_sd": sd}
        entropy_train = np.empty((int(train.sum()), 2)); entropy_test = np.empty((int(test.sum()), 2))
        entropy_scalers: dict[str, dict[str, dict[str, float]]] = {}
        for region in REGIONS:
            train_local, test_local = train & (reg == region), test & (reg == region)
            if not train_local.any() or not test_local.any():
                raise ValueError(f"fold {fold} lacks train/test complete cases in {region}")
            train_pos = np.searchsorted(train_rows, np.flatnonzero(train_local))
            test_pos = np.searchsorted(test_rows, np.flatnonzero(test_local))
            entropy_scalers[region] = {}
            for k, j in enumerate((3, 4)):
                mean, sd = float(x[train_local, j].mean()), float(x[train_local, j].std(ddof=0))
                if not np.isfinite(sd) or sd <= 0.:
                    raise ValueError(f"entropy feature {FEATURES[j]} has zero scale in {region}, fold {fold}")
                entropy_train[train_pos, k] = (x[train_local, j] - mean) / sd
                entropy_test[test_pos, k] = (x[test_local, j] - mean) / sd
                entropy_scalers[region][FEATURES[j]] = {"training_mean": mean, "training_sd": sd}
        train_product = entropy_train[:, 0] * entropy_train[:, 1]
        test_product = entropy_test[:, 0] * entropy_test[:, 1]
        product_oof[test_rows] = test_product

        def design(rows: np.ndarray, scaled_base: np.ndarray, scaled_entropy: np.ndarray,
                   product: np.ndarray) -> tuple[np.ndarray, int, np.ndarray]:
            codes = np.asarray([region_codes[r] for r in reg[rows]], dtype=np.int8)
            dummies = np.column_stack([(codes == code).astype(np.float64)
                                       for code in range(1, len(REGIONS))])
            main = np.column_stack((np.ones(len(rows)), dummies, scaled_base, scaled_entropy))
            interactions, contrasts = deviation_design(product, reg[rows])
            combined = np.column_stack((main, interactions))
            # The last 3 columns are the penalized sum-to-zero deviations; common product is unpenalized.
            return combined, main.shape[1] + 1, contrasts

        base_x_train, contrast_start_train, _ = design(train_rows, base_train, entropy_train, train_product)
        base_x_test, _, _ = design(test_rows, base_test, entropy_test, test_product)
        y_train = y[train]
        base_design_train = base_x_train[:, :contrast_start_train - 1]
        beta_base, _, rank_base, _ = np.linalg.lstsq(base_design_train, y_train, rcond=None)
        if rank_base != base_design_train.shape[1]:
            raise ValueError("additive baseline training design is rank deficient")
        base_pred = base_design_train @ beta_base
        base_test_design = base_x_test[:, :contrast_start_train - 1]
        base_oof[test_rows] = base_test_design @ beta_base

        beta_ridge = ridge_coefficients(base_x_train, y_train, contrast_start=contrast_start_train, alpha=alpha)
        augmented_oof[test_rows] = base_x_test @ beta_ridge
        # Unpenalized full model is reported only to quantify shrinkage; no tuning/selection uses it.
        beta_unpenalized, _, rank_full, _ = np.linalg.lstsq(base_x_train, y_train, rcond=None)
        if rank_full != base_x_train.shape[1]:
            raise ValueError("sum-zero interaction design is rank deficient")
        ridge_deviations = beta_ridge[contrast_start_train:]
        full_deviations = beta_unpenalized[contrast_start_train:]
        ridge_norm = float(np.sqrt(ridge_deviations @ (np.eye(3) + np.ones((3, 3))) @ ridge_deviations))
        unpenalized_norm = float(np.sqrt(full_deviations @ (np.eye(3) + np.ones((3, 3))) @ full_deviations))
        shrinkage_ratio = (ridge_norm / unpenalized_norm) if unpenalized_norm > 0. else None
        beta_common = float(beta_ridge[contrast_start_train - 1])
        slopes = region_slopes(beta_common, ridge_deviations)
        fold_metrics.append({"fold": fold, "training_n": int(train.sum()), "heldout_n": int(test.sum()),
                            **paired_delta_r2(y[test], base_oof[test], augmented_oof[test])})
        fold_coefficients.append({"fold": fold, "alpha": alpha,
            "penalized_objective": "training SSE + alpha * sum of squares of four sum-zero region deviations; common slope and baseline terms unpenalized",
            "common_product_coefficient": beta_common,
            "region_deviation_coefficients": {region: float((ridge_deviations[i] if i < 3 else -ridge_deviations.sum()))
                                               for i, region in enumerate(REGIONS)},
            "region_interaction_slopes": slopes,
            "ridge_deviation_l2_norm": ridge_norm,
            "unpenalized_deviation_l2_norm": unpenalized_norm,
            "ridge_to_unpenalized_deviation_norm_ratio": shrinkage_ratio,
            "training_only_global_scalers": global_scalers,
            "training_only_region_entropy_scalers": entropy_scalers})
    if any(np.any(use & ~np.isfinite(v)) or np.any(~use & np.isfinite(v))
           for v in (base_oof, augmented_oof, product_oof)):
        raise AssertionError("OOF predictions must preserve exact joint support")
    return {"baseline_oof": base_oof, "augmented_oof": augmented_oof,
            "product_oof": product_oof, "fold_metrics": fold_metrics,
            "fold_coefficients": fold_coefficients}


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    old_report = json.loads(INTERACTION_REPORT.read_text(encoding="utf-8"))
    if (not np.isclose(old_report["measure_after"]["pooled_heldout_delta_r2"],
                       EXPECTED_POOLED_INTERACTION_DELTA_R2, rtol=0., atol=1e-14)
            or old_report["measure_after"]["positive_fold_delta_r2_count"] != EXPECTED_POSITIVE_FOLDS):
        raise ValueError("pooled source-road interaction MEASURE-BEFORE changed")
    official, regions, residual, features, joint, folds, provenance = load_inputs(workspace)
    if len(official) != EXPECTED_N or int(joint.sum()) != 9247:
        raise ValueError("source/road interaction support differs from the frozen 9,247-row artifact")
    old_mask = old_report["scope"]["official_order_mask_sha256"]["interaction_joint_defined"]
    if mask_sha256(joint) != old_mask:
        raise ValueError("source-road complete-case mask differs from the prior pooled interaction")
    fit = crossfit_region_shrinkage([g for g, _ in official], regions, folds, residual, features, joint)
    pooled = paired_delta_r2(residual[joint], fit["baseline_oof"][joint], fit["augmented_oof"][joint])
    regional: dict[str, Any] = {}
    for region in REGIONS:
        use = joint & (regions == region)
        regional[region] = {"n": int(use.sum()), **paired_delta_r2(
            residual[use], fit["baseline_oof"][use], fit["augmented_oof"][use])}
    positive_regions = sum(row["delta_r2"] > 0. for row in regional.values())
    gate = bool(pooled["delta_r2"] >= MIN_POOLED_DELTA_R2 and positive_regions >= MIN_POSITIVE_REGIONS)
    residual_defined = np.isfinite(residual)
    poi_entropy_defined = np.isfinite(features[:, 3])
    road_entropy_defined = np.isfinite(features[:, 4])
    table = pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "fixed_s1_fold": pa.array(folds, type=pa.int8()),
        "frozen_s1_residual": pa.array([float(v) if ok else None for v, ok in zip(residual, residual_defined)], type=pa.float64()),
        "frozen_s1_residual_defined": pa.array(residual_defined, type=pa.bool_()),
        "poi_source_entropy": pa.array([float(v) if ok else None for v, ok in zip(features[:, 3], poi_entropy_defined)], type=pa.float64()),
        "poi_source_entropy_defined": pa.array(poi_entropy_defined, type=pa.bool_()),
        "road_class_entropy": pa.array([float(v) if ok else None for v, ok in zip(features[:, 4], road_entropy_defined)], type=pa.float64()),
        "road_class_entropy_defined": pa.array(road_entropy_defined, type=pa.bool_()),
        "log1p_poi_count": pa.array([float(v) if np.isfinite(v) else None for v in features[:, 0]], type=pa.float64()),
        "Mx4_F": pa.array([float(v) if np.isfinite(v) else None for v in features[:, 1]], type=pa.float64()),
        "S1_lagged_Mx4_F": pa.array([float(v) if np.isfinite(v) else None for v in features[:, 2]], type=pa.float64()),
        "interaction_complete_case_defined": pa.array(joint, type=pa.bool_()),
        "training_fold_region_standardized_entropy_product": pa.array([float(v) if ok else None for v, ok in zip(fit["product_oof"], joint)], type=pa.float64()),
        "baseline_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(fit["baseline_oof"], joint)], type=pa.float64()),
        "region_shrinkage_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(fit["augmented_oof"], joint)], type=pa.float64()),
    })
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("region-shrinkage artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    report = {
        "schema_version": 1,
        "screen": "Region-varying provided source-entropy × road-entropy effects with fixed ridge-shrunk deviations against frozen S1 residual; unregistered",
        "measure_before": {"pooled_interaction_delta_r2": EXPECTED_POOLED_INTERACTION_DELTA_R2,
                           "pooled_interaction_positive_folds": EXPECTED_POSITIVE_FOLDS,
                           "pooled_interaction_report_sha256": sha256_file(INTERACTION_REPORT),
                           "poi_source_entropy_residual_variance_ratio": POI_MAIN_RATIO,
                           "road_class_entropy_residual_variance_ratio": ROAD_MAIN_RATIO,
                           "complete_case_ids": int(joint.sum())},
        "method": {"outcome": "Reuse frozen cross-fitted orthogonalized S1 disagreement residual and its existing five GEOID folds; no residual refit.",
                   "baseline": "Same region-intercept, log1p POI count, frozen Mx4 F, S1 lag and both entropy main effects as the pooled interaction screen.",
                   "baseline_model": "OLS; all base features training-standardized globally per fold; entropy main effects standardized by region using that region's training-fold values only.",
                   "augmented_model": "Add a common standardized source-entropy × road-entropy product slope plus four region-specific interaction deviations constrained to sum to zero. Parameterize four deviations with three independent region contrasts; each region slope is common slope plus its deviation.",
                   "ridge_penalty": "Fixed alpha=100; penalize alpha times the sum of squares of the four sum-zero regional deviations only. Common product slope and baseline terms are unpenalized. Objective is training SSE + penalty; no outer tuning.",
                   "coefficient_shrinkage": "For disclosure only, report ridge and unpenalized four-deviation L2 norms and their ratio for each fold; unpenalized fit is not used for predictions or selection.",
                   "folds": "Reuse the exact five GEOID folds from the frozen S1 residual artifact. Every transform and model fit uses only the other four folds; predictions are paired on held-out complete cases.",
                   "support": "Exact same official ID mask as the previous pooled source-road interaction, requiring frozen residual, both entropy features and log count/Mx4/S1 predictors. No imputation.",
                   "metric": "Pooled and regional OOF incremental delta-R2=(baseline SSE - ridge model SSE)/shared held-out residual SST.",
                   "unregistered": True,
                   "interpretation_limit": "Label-free provided-feature association screen only; not target accuracy, causality, or estimator-promotion evidence."},
        "scope": {"official_ids": len(official), "official_geoid_region_sha256": provenance["official_geoid_region_sha256"],
                  "complete_case_ids": int(joint.sum()), "complete_case_mask_sha256": mask_sha256(joint),
                  "frozen_residual_mask_sha256": mask_sha256(residual_defined),
                  "fold_assignment_sha256": hashlib.sha256(np.asarray(folds, dtype=np.int8).tobytes()).hexdigest(),
                  "fold_support": {str(f): int(np.sum(joint & (folds == f))) for f in range(N_FOLDS)},
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_references_read": False, "external_data_used": [],
                  "heldout_preprocessing": False, "changed_folds": False,
                  "registry_changed": False, "leaderboard_feedback_used": False, "uploads": 0},
        "measure_after": {"pooled": pooled, "regional": regional,
                          "folds": fit["fold_metrics"], "positive_regions": positive_regions,
                          "coefficient_shrinkage_by_fold": fit["fold_coefficients"]},
        "thresholds": {"pooled_delta_r2_minimum_inclusive": MIN_POOLED_DELTA_R2,
                       "minimum_positive_region_estimates": MIN_POSITIVE_REGIONS,
                       "fixed_ridge_alpha": RIDGE_ALPHA},
        "acceptance": {"pooled_delta_r2_at_least_0_01": bool(pooled["delta_r2"] >= MIN_POOLED_DELTA_R2),
                       "positive_delta_in_at_least_3_of_4_regions": bool(positive_regions >= MIN_POSITIVE_REGIONS),
                       "region_shrinkage_screen_pass": gate},
        "provenance": {**provenance,
                       "pooled_interaction_report": {"path": str(INTERACTION_REPORT), "sha256": sha256_file(INTERACTION_REPORT)},
                       "source_entropy_report_sha256": sha256_file(POI_REPORT),
                       "road_entropy_report_sha256": sha256_file(ROAD_REPORT),
                       "frozen_residual_report_sha256": sha256_file(RESIDUAL_REPORT),
                       "output_artifact": {"path": str(artifact_path), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": table.num_rows, "columns": table.column_names,
                                           "deterministic_serialization": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The fixed ridge-shrunk region-varying source-road interaction passed all preregistered gates; descriptive association only, not target-accuracy or integration evidence." if gate else
                         "The fixed ridge-shrunk region-varying source-road interaction failed one or more preregistered gates; retire the interaction as unsupported and do not integrate it.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/source-road-entropy-region-shrinkage.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "source-road-entropy-region-shrinkage/oof-predictions.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"scope": report["scope"], "measure_after": report["measure_after"],
                      "acceptance": report["acceptance"], "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
