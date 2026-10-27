#!/usr/bin/env python3
"""Cross-fit a source-entropy × road-entropy interaction against frozen S1 residuals."""
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

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_N = 9379
N_FOLDS = 5
POI_REPORT = ROOT / "reports/poi-source-class-entropy.json"
ROAD_REPORT = ROOT / "reports/named-road-class-entropy.json"
RESIDUAL_REPORT = ROOT / "reports/s1-disagreement-orthogonalized.json"
POI_ENTROPY_SHA = "f5220df3aed4b66023f40df1e9fac0988e8d89b1f35ef1a6311b5fbc4d2b50ff"
ROAD_ENTROPY_SHA = "41610caca547f76604a8430f7cc0dfcdf628ea3f431632c5e5da11809da92f38"
RESIDUAL_SHA = "437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
POI_MAIN_RATIO = 0.8104966782110836
ROAD_MAIN_RATIO = 0.6607401765971582
FEATURES = ("log1p_poi_count", "Mx4_F", "S1_lagged_Mx4_F", "poi_source_entropy", "road_class_entropy")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mask_sha256(mask: np.ndarray) -> str:
    packed = np.packbits(np.asarray(mask, dtype=np.uint8), bitorder="little")
    return hashlib.sha256(packed.tobytes()).hexdigest()


def paired_delta_r2(y: np.ndarray, baseline_prediction: np.ndarray,
                    augmented_prediction: np.ndarray) -> dict[str, float]:
    y, base, augmented = (np.asarray(v, dtype=np.float64) for v in
                          (y, baseline_prediction, augmented_prediction))
    if y.ndim != 1 or base.shape != y.shape or augmented.shape != y.shape or y.size < 2:
        raise ValueError("paired held-out arrays must align and contain at least two rows")
    if not all(np.isfinite(v).all() for v in (y, base, augmented)):
        raise ValueError("paired held-out arrays must be finite")
    sst = float(np.sum((y - y.mean()) ** 2))
    if sst <= 0.:
        raise ValueError("held-out residual has zero variance")
    baseline_sse = float(np.sum((y - base) ** 2))
    augmented_sse = float(np.sum((y - augmented) ** 2))
    return {"baseline_sse": baseline_sse, "interaction_sse": augmented_sse,
            "heldout_total_sum_squares": sst,
            "baseline_r2": 1. - baseline_sse / sst,
            "interaction_r2": 1. - augmented_sse / sst,
            "delta_r2": (baseline_sse - augmented_sse) / sst}


def _zscore_train(values: np.ndarray) -> tuple[np.ndarray, float, float]:
    mean = float(np.mean(values))
    scale = float(np.std(values, ddof=0))
    if not np.isfinite(scale) or scale <= 0.:
        raise ValueError("training-fold feature has zero/nonfinite scale")
    return (values - mean) / scale, mean, scale


def crossfit_interaction(geoids: list[str], regions: np.ndarray, folds: np.ndarray,
                         outcome: np.ndarray, features: np.ndarray, mask: np.ndarray
                         ) -> dict[str, Any]:
    """Fit global region-intercept models using train-only scaling in each fixed fold."""
    region_values = np.asarray(regions, dtype=object)
    fold_values = np.asarray(folds, dtype=np.int8)
    y = np.asarray(outcome, dtype=np.float64)
    x = np.asarray(features, dtype=np.float64)
    use = np.asarray(mask, dtype=bool)
    n = len(geoids)
    if (len(set(geoids)) != n or region_values.shape != (n,) or fold_values.shape != (n,)
            or y.shape != (n,) or x.shape != (n, 5) or use.shape != (n,)):
        raise ValueError("cross-fit inputs must have exact aligned shapes and unique GEOIDs")
    if set(region_values.tolist()) != set(REGIONS) or np.any((fold_values < 0) | (fold_values >= N_FOLDS)):
        raise ValueError("cross-fit requires four regions and fixed fold IDs in 0..4")
    if np.any(use & (~np.isfinite(y) | ~np.isfinite(x).all(axis=1))):
        raise ValueError("complete-case interaction rows must have finite outcome and predictors")
    baseline_oof = np.full(n, np.nan)
    interaction_oof = np.full(n, np.nan)
    poi_z_oof = np.full(n, np.nan)
    road_z_oof = np.full(n, np.nan)
    product_oof = np.full(n, np.nan)
    fold_results: list[dict[str, Any]] = []
    fold_model_details: list[dict[str, Any]] = []
    region_codes = {region: i for i, region in enumerate(REGIONS)}

    for fold in range(N_FOLDS):
        train = use & (fold_values != fold)
        test = use & (fold_values == fold)
        if not test.any() or train.sum() <= 12:
            raise ValueError(f"fold {fold} has insufficient complete-case training or held-out support")
        # Training-only global scalers for count and frozen emissions.
        base_train_z = np.empty((int(train.sum()), 3), dtype=np.float64)
        base_test_z = np.empty((int(test.sum()), 3), dtype=np.float64)
        global_scalers: dict[str, dict[str, float]] = {}
        for j, name in enumerate(FEATURES[:3]):
            train_z, mean, scale = _zscore_train(x[train, j])
            base_train_z[:, j] = train_z
            base_test_z[:, j] = (x[test, j] - mean) / scale
            global_scalers[name] = {"training_mean": mean, "training_sd": scale}

        # Entropy main effects are standardized separately inside each region,
        # using only that region's training-fold support. Their product is the
        # single added interaction feature.
        train_z_ent = np.empty((int(train.sum()), 2), dtype=np.float64)
        test_z_ent = np.empty((int(test.sum()), 2), dtype=np.float64)
        entropy_scalers: dict[str, dict[str, dict[str, float]]] = {}
        train_rows = np.flatnonzero(train); test_rows = np.flatnonzero(test)
        for region in REGIONS:
            train_local = train & (region_values == region)
            test_local = test & (region_values == region)
            if not train_local.any() or not test_local.any():
                raise ValueError(f"fold {fold} lacks train or held-out complete cases in {region}")
            train_positions = np.flatnonzero(train_local)
            test_positions = np.flatnonzero(test_local)
            train_local_positions = np.searchsorted(train_rows, train_positions)
            test_local_positions = np.searchsorted(test_rows, test_positions)
            entropy_scalers[region] = {}
            for k, j in enumerate((3, 4)):
                z, mean, scale = _zscore_train(x[train_local, j])
                train_z_ent[train_local_positions, k] = z
                test_z_ent[test_local_positions, k] = (x[test_local, j] - mean) / scale
                entropy_scalers[region][FEATURES[j]] = {"training_mean": mean, "training_sd": scale}
        train_product = train_z_ent[:, 0] * train_z_ent[:, 1]
        test_product = test_z_ent[:, 0] * test_z_ent[:, 1]
        poi_z_oof[test_rows] = test_z_ent[:, 0]
        road_z_oof[test_rows] = test_z_ent[:, 1]
        product_oof[test_rows] = test_product

        def design(rows: np.ndarray, scaled_base: np.ndarray, scaled_entropy: np.ndarray,
                   product: np.ndarray, augmented: bool) -> np.ndarray:
            # Intercept plus three region indicators gives region-specific intercepts.
            codes = np.asarray([region_codes[r] for r in region_values[rows]], dtype=np.int8)
            region_dummies = np.column_stack([(codes == code).astype(np.float64) for code in range(1, len(REGIONS))])
            parts = [np.ones((len(rows), 1)), region_dummies, scaled_base, scaled_entropy]
            if augmented:
                parts.append(product[:, None])
            return np.column_stack(parts)

        xbase_train = design(train_rows, base_train_z, train_z_ent, train_product, False)
        xbase_test = design(test_rows, base_test_z, test_z_ent, test_product, False)
        xaug_train = design(train_rows, base_train_z, train_z_ent, train_product, True)
        xaug_test = design(test_rows, base_test_z, test_z_ent, test_product, True)
        beta_base, _, rank_base, singular_base = np.linalg.lstsq(xbase_train, y[train], rcond=None)
        beta_aug, _, rank_aug, singular_aug = np.linalg.lstsq(xaug_train, y[train], rcond=None)
        baseline_oof[test_rows] = xbase_test @ beta_base
        interaction_oof[test_rows] = xaug_test @ beta_aug
        metrics = paired_delta_r2(y[test], baseline_oof[test_rows], interaction_oof[test_rows])
        fold_results.append({"fold": fold, "train_n": int(train.sum()), "heldout_n": int(test.sum()), **metrics})
        fold_model_details.append({
            "fold": fold,
            "training_rows": int(train.sum()),
            "heldout_rows": int(test.sum()),
            "region_intercepts": True,
            "baseline_terms": ["intercept", "region_intercepts", *FEATURES[:3], *FEATURES[3:]],
            "augmented_term": "within_region_z_poi_source_entropy × within_region_z_road_class_entropy",
            "training_only_global_scalers": global_scalers,
            "training_only_within_region_entropy_scalers": entropy_scalers,
            "baseline_design_rank": int(rank_base), "baseline_design_columns": int(xbase_train.shape[1]),
            "interaction_design_rank": int(rank_aug), "interaction_design_columns": int(xaug_train.shape[1]),
            "baseline_min_singular_value": float(singular_base.min()),
            "interaction_min_singular_value": float(singular_aug.min()),
            "interaction_coefficient": float(beta_aug[-1]),
        })
    if any(np.any(use & ~np.isfinite(v)) or np.any(~use & np.isfinite(v)) for v in
           (baseline_oof, interaction_oof, poi_z_oof, road_z_oof, product_oof)):
        raise AssertionError("all predictions and standardized features must preserve the exact joint mask")
    return {"baseline_oof": baseline_oof, "interaction_oof": interaction_oof,
            "poi_z_oof": poi_z_oof, "road_z_oof": road_z_oof, "product_oof": product_oof,
            "fold_metrics": fold_results, "fold_model_details": fold_model_details}


def _load_parquet_by_pair(path: Path, columns: list[str], official: list[tuple[str, str]],
                          expected_sha: str) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    digest = sha256_file(path)
    if digest != expected_sha:
        raise ValueError(f"source artifact hash mismatch: {path}")
    table = pq.read_table(path, columns=["GEOID", "region", *columns])
    if table.num_rows != len(official):
        raise ValueError(f"source artifact row count mismatch: {path}")
    locations = {pair: i for i, pair in enumerate(official)}
    seen: set[tuple[str, str]] = set()
    arrays: dict[str, np.ndarray] = {}
    for column in columns:
        if pa.types.is_boolean(table.schema.field(column).type):
            arrays[column] = np.zeros(len(official), dtype=bool)
        else:
            arrays[column] = np.full(len(official), np.nan, dtype=np.float64)
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in locations or pair in seen:
            raise ValueError(f"unexpected/duplicate official ID in {path}: {pair}")
        seen.add(pair)
        i = locations[pair]
        for column in columns:
            value = row[column]
            if pa.types.is_boolean(table.schema.field(column).type):
                if not isinstance(value, bool):
                    raise ValueError(f"{path} {column} must be a nonnull boolean mask")
                arrays[column][i] = value
            elif value is not None:
                arrays[column][i] = float(value)
    if seen != set(official):
        raise ValueError(f"official ID alignment incomplete in {path}")
    return arrays, {"path": str(path), "sha256": digest, "rows": table.num_rows,
                    "columns_read": ["GEOID", "region", *columns], "exact_official_pair_alignment": True}


def load_inputs(workspace: Path) -> tuple[list[tuple[str, str]], np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N
            or len({g for g, _ in official}) != EXPECTED_N or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official IDs must be an exact 9,379-row/four-region partition")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    poi_report = json.loads(POI_REPORT.read_text(encoding="utf-8"))
    road_report = json.loads(ROAD_REPORT.read_text(encoding="utf-8"))
    residual_report = json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    if (abs(float(poi_report["measure_after"]["residual_variance_ratio"]) - POI_MAIN_RATIO) > 1e-12
            or abs(float(road_report["measure_after"]["residual_variance_ratio"]) - ROAD_MAIN_RATIO) > 1e-12
            or poi_report["scope"]["joint_crossfit_ids"] != 9377
            or road_report["scope"]["joint_crossfit_ids"] != 9228
            or residual_report["scope"]["disagreement_defined_ids"] != EXPECTED_N):
        raise ValueError("completed source/road entropy or frozen residual measure-before differs")

    poi_path = Path(poi_report["provenance"]["output_artifact"]["path"])
    road_path = Path(road_report["provenance"]["output_artifact"]["path"])
    residual_path = Path(residual_report["provenance"]["output_artifact"]["path"])
    poi, poi_meta = _load_parquet_by_pair(poi_path, ["provided_poi_count", "source_class_entropy", "entropy_defined",
        "Mx4_F", "Mx4_F_defined", "S1_lagged_Mx4_F", "S1_lagged_Mx4_F_defined"], official, POI_ENTROPY_SHA)
    road, road_meta = _load_parquet_by_pair(road_path, ["class_mix_entropy", "entropy_defined"], official, ROAD_ENTROPY_SHA)
    residual, residual_meta = _load_parquet_by_pair(residual_path, ["crossfit_residual", "crossfit_joint_defined", "Mx4_F",
        "Mx4_F_defined", "S1_lagged_Mx4_F", "S1_lagged_Mx4_F_defined", "fold"], official, RESIDUAL_SHA)

    poi_defined = poi["entropy_defined"]
    road_defined = road["entropy_defined"]
    residual_defined = residual["crossfit_joint_defined"]
    for values, mask, name in ((poi["source_class_entropy"], poi_defined, "POI source entropy"),
                               (road["class_mix_entropy"], road_defined, "road class entropy"),
                               (residual["crossfit_residual"], residual_defined, "frozen S1 residual")):
        if not np.array_equal(np.isfinite(values), mask):
            raise ValueError(f"{name} values and defined mask do not align")
    if not residual_defined.all() or not np.array_equal(np.isfinite(poi["Mx4_F"]), poi["Mx4_F_defined"]):
        raise ValueError("frozen residual must cover all IDs and POI predictor masks must be exact")
    if (not np.array_equal(poi["Mx4_F"], residual["Mx4_F"], equal_nan=True)
            or not np.array_equal(poi["S1_lagged_Mx4_F"], residual["S1_lagged_Mx4_F"], equal_nan=True)
            or not np.array_equal(poi["Mx4_F_defined"], residual["Mx4_F_defined"])
            or not np.array_equal(poi["S1_lagged_Mx4_F_defined"], residual["S1_lagged_Mx4_F_defined"])):
        raise ValueError("POI entropy and residual artifacts disagree on frozen Mx4/S1 features")
    count = poi["provided_poi_count"]
    if np.any(np.isfinite(count) & (count < 0.)):
        raise ValueError("provided POI count must be nonnegative")
    log_count = np.full(EXPECTED_N, np.nan)
    valid_count = np.isfinite(count)
    log_count[valid_count] = np.log1p(count[valid_count])
    features = np.column_stack((log_count, residual["Mx4_F"], residual["S1_lagged_Mx4_F"],
                                poi["source_class_entropy"], road["class_mix_entropy"]))
    joint = (residual_defined & poi_defined & road_defined & np.isfinite(features).all(axis=1)
             & residual["Mx4_F_defined"] & residual["S1_lagged_Mx4_F_defined"])
    if not joint.any():
        raise ValueError("no complete official rows for source×road entropy interaction")
    folds = residual["fold"].astype(np.int8)
    if np.any((folds < 0) | (folds >= N_FOLDS)):
        raise ValueError("frozen residual fold IDs must be in 0..4")
    return (official, np.asarray([r for _, r in official], dtype=object), residual["crossfit_residual"],
            features, joint, folds, {
                "official_geoid_region_sha256": official_sha,
                "poi_source_entropy_artifact": poi_meta, "road_class_entropy_artifact": road_meta,
                "frozen_s1_residual_artifact": residual_meta,
                "poi_entropy_report": {"path": str(POI_REPORT), "sha256": sha256_file(POI_REPORT),
                    "residual_variance_ratio": POI_MAIN_RATIO, "joint_crossfit_ids": 9377},
                "road_entropy_report": {"path": str(ROAD_REPORT), "sha256": sha256_file(ROAD_REPORT),
                    "residual_variance_ratio": ROAD_MAIN_RATIO, "joint_crossfit_ids": 9228},
                "frozen_s1_residual_report": {"path": str(RESIDUAL_REPORT), "sha256": sha256_file(RESIDUAL_REPORT)},
            })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    official, regions, residual, features, joint, folds, provenance = load_inputs(workspace)
    fit = crossfit_interaction([g for g, _ in official], regions, folds, residual, features, joint)
    pooled = paired_delta_r2(residual[joint], fit["baseline_oof"][joint], fit["interaction_oof"][joint])
    positive_folds = sum(row["delta_r2"] > 0. for row in fit["fold_metrics"])
    acceptance = {"pooled_delta_r2_at_least_0_01": bool(pooled["delta_r2"] >= .01),
                  "positive_delta_r2_in_at_least_4_of_5_folds": bool(positive_folds >= 4),
                  "positive_fold_count": int(positive_folds),
                  "interaction_screen_pass": bool(pooled["delta_r2"] >= .01 and positive_folds >= 4)}
    poi_entropy_defined = np.isfinite(features[:, 3])
    road_entropy_defined = np.isfinite(features[:, 4])
    residual_defined = np.isfinite(residual)
    table = pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "fixed_s1_fold": pa.array(folds, type=pa.int8()),
        "frozen_s1_residual": pa.array([float(v) if ok else None for v, ok in zip(residual, residual_defined)], type=pa.float64()),
        "poi_source_entropy": pa.array([float(v) if ok else None for v, ok in zip(features[:, 3], poi_entropy_defined)], type=pa.float64()),
        "road_class_entropy": pa.array([float(v) if ok else None for v, ok in zip(features[:, 4], road_entropy_defined)], type=pa.float64()),
        "log1p_poi_count": pa.array([float(v) if np.isfinite(v) else None for v in features[:, 0]], type=pa.float64()),
        "Mx4_F": pa.array([float(v) if np.isfinite(v) else None for v in features[:, 1]], type=pa.float64()),
        "Mx4_F_defined": pa.array(np.isfinite(features[:, 1]), type=pa.bool_()),
        "S1_lagged_Mx4_F": pa.array([float(v) if np.isfinite(v) else None for v in features[:, 2]], type=pa.float64()),
        "S1_lagged_Mx4_F_defined": pa.array(np.isfinite(features[:, 2]), type=pa.bool_()),
        "log1p_poi_count_defined": pa.array(np.isfinite(features[:, 0]), type=pa.bool_()),
        "poi_entropy_defined": pa.array(poi_entropy_defined, type=pa.bool_()),
        "road_entropy_defined": pa.array(road_entropy_defined, type=pa.bool_()),
        "interaction_joint_defined": pa.array(joint, type=pa.bool_()),
        "training_fold_z_poi_entropy": pa.array([float(v) if ok else None for v, ok in zip(fit["poi_z_oof"], joint)], type=pa.float64()),
        "training_fold_z_road_entropy": pa.array([float(v) if ok else None for v, ok in zip(fit["road_z_oof"], joint)], type=pa.float64()),
        "training_fold_entropy_product": pa.array([float(v) if ok else None for v, ok in zip(fit["product_oof"], joint)], type=pa.float64()),
        "baseline_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(fit["baseline_oof"], joint)], type=pa.float64()),
        "interaction_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(fit["interaction_oof"], joint)], type=pa.float64()),
    })
    for column, mask in (("frozen_s1_residual", residual_defined), ("poi_source_entropy", poi_entropy_defined),
                         ("road_class_entropy", road_entropy_defined), ("log1p_poi_count", np.isfinite(features[:, 0])),
                         ("Mx4_F", np.isfinite(features[:, 1])), ("S1_lagged_Mx4_F", np.isfinite(features[:, 2])),
                         ("training_fold_entropy_product", joint), ("baseline_oof_prediction", joint),
                         ("interaction_oof_prediction", joint)):
        values = np.asarray(table[column].to_numpy(zero_copy_only=False), dtype=np.float64)
        if not np.array_equal(np.isfinite(values), mask):
            raise AssertionError(f"output artifact null mask mismatch for {column}")
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("interaction prediction artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    report = {
        "schema_version": 1,
        "screen": "Interaction of provided POI source-class entropy and named-road class-share entropy on frozen cross-fitted orthogonalized S1 residual; unregistered",
        "measure_before": {"poi_source_class_entropy_residual_variance_ratio": POI_MAIN_RATIO,
                           "poi_source_class_entropy_joint_ids": 9377,
                           "road_class_entropy_residual_variance_ratio": ROAD_MAIN_RATIO,
                           "road_class_entropy_joint_ids": 9228,
                           "source_road_interaction_measured": False,
                           "poi_entropy_report_sha256": provenance["poi_entropy_report"]["sha256"],
                           "road_entropy_report_sha256": provenance["road_entropy_report"]["sha256"]},
        "method": {"outcome": "Reuse the frozen cross-fitted orthogonalized S1 disagreement residual; do not refit it.",
                   "folds": "Reuse exact five GEOID fold IDs from the frozen S1 residual artifact. For each fold, fit on the other four folds and predict the held-out fold.",
                   "baseline": "OLS with intercept and region-specific intercepts, log1p provided Overture POI count, frozen Mx4 F, frozen S1 lag, and both source-entropy and road-entropy main effects.",
                   "interaction": "Add the product of the two entropy main effects, each standardized within region using that fold's training rows only.",
                   "scaling": "Fit mean and population SD for log1p POI count, Mx4 F, and S1 lag on the training rows only. Within each region, fit separate entropy means and population SDs on that region's training rows only. Apply these frozen training-fold scalers to held-out rows.",
                   "support": "Use exact official GEOID-region pairs where frozen residual, both entropies, log1p POI count, Mx4 F and S1 lag are finite; both models use identical support. Do not impute missing rows.",
                   "metric": "Per-fold and pooled held-out incremental delta R2=(SSE_baseline-SSE_interaction)/SST for the same held-out residual rows; pooled SST centers all pooled OOF residuals on their pooled held-out mean.",
                   "unregistered": True, "interpretation_limit": "Label-free residual interaction screen; not target accuracy, causal evidence, or promotion."},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": provenance["official_geoid_region_sha256"],
                  "frozen_residual_defined_ids": int(residual_defined.sum()),
                  "poi_source_entropy_defined_ids": int(poi_entropy_defined.sum()),
                  "road_class_entropy_defined_ids": int(road_entropy_defined.sum()),
                  "log1p_poi_count_defined_ids": int(np.isfinite(features[:, 0]).sum()),
                  "Mx4_F_defined_ids": int(np.isfinite(features[:, 1]).sum()),
                  "S1_lagged_Mx4_F_defined_ids": int(np.isfinite(features[:, 2]).sum()),
                  "interaction_complete_case_ids": int(joint.sum()),
                  "interaction_null_ids": int((~joint).sum()),
                  "official_order_mask_sha256": {
                      "frozen_residual_defined": mask_sha256(residual_defined),
                      "poi_source_entropy_defined": mask_sha256(poi_entropy_defined),
                      "road_class_entropy_defined": mask_sha256(road_entropy_defined),
                      "log1p_poi_count_defined": mask_sha256(np.isfinite(features[:, 0])),
                      "Mx4_F_defined": mask_sha256(np.isfinite(features[:, 1])),
                      "S1_lagged_Mx4_F_defined": mask_sha256(np.isfinite(features[:, 2])),
                      "interaction_joint_defined": mask_sha256(joint)},
                  "fold_counts_official": {str(f): int(np.count_nonzero(folds == f)) for f in range(N_FOLDS)},
                  "fold_counts_interaction": {str(f): int(np.count_nonzero(joint & (folds == f))) for f in range(N_FOLDS)},
                  "exact_source_entropy_null_masks": True, "exact_frozen_residual_mask": True,
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_references_read": False, "external_data_used": [],
                  "leaderboard_feedback_used": False, "estimator_or_registry_changed": False, "uploads": 0},
        "measure_after": {"pooled_heldout_baseline_r2": pooled["baseline_r2"],
                          "pooled_heldout_interaction_r2": pooled["interaction_r2"],
                          "pooled_heldout_delta_r2": pooled["delta_r2"],
                          "pooled_baseline_sse": pooled["baseline_sse"],
                          "pooled_interaction_sse": pooled["interaction_sse"],
                          "pooled_heldout_total_sum_squares": pooled["heldout_total_sum_squares"],
                          "positive_fold_delta_r2_count": int(positive_folds),
                          "folds": fit["fold_metrics"]},
        "acceptance": acceptance,
        "provenance": {**provenance,
                       "output_artifact": {"path": str(artifact_path), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": table.num_rows, "columns": table.column_names,
                                           "deterministic_serialization": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The interaction passes the preregistered pooled incremental-R2 and fold-consistency gates; retain only as an unregistered descriptive diagnostic, not a target-accuracy, causal, or promotion claim." if acceptance["interaction_screen_pass"] else
                         "The interaction did not pass the preregistered pooled incremental-R2 and fold-consistency gates; retire this interaction as unsupported or redundant.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/source-road-entropy-interaction.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "source-road-entropy-interaction/oof-interaction-predictions.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"scope": report["scope"], "measure_after": report["measure_after"],
                      "acceptance": report["acceptance"], "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
