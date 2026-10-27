#!/usr/bin/env python3
"""Screen one training-fold q90 hinge in POI effective count vs frozen S1 residual."""
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
from audit_mx4p_conditional_innovation import deterministic_folds, parquet_bytes  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_N = 9379
N_FOLDS = 5
RESIDUAL_SHA = "437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
EFFECTIVE_SHA = "74c35567243ad8ea3c00cf5117ef641e49148b63f89f7f07616ac9dcf33b8d6e"
EFFECTIVE_REPORT = ROOT / "reports/poi-category-effective-count.json"
LINEAR_REPORT = ROOT / "reports/poi-effective-count-s1-residual.json"
WITHIN_REGION_RATIO = 0.18096672540982753
EXPECTED_LINEAR_DELTA_R2 = -0.00014593
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261031
Q90 = 0.90
MIN_POOLED_DELTA_R2 = 0.005
MIN_CI_LOWER = 0.0
MIN_POSITIVE_REGIONS = 3


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mask_sha256(mask: np.ndarray) -> str:
    return hashlib.sha256(np.packbits(np.asarray(mask, dtype=np.uint8), bitorder="little").tobytes()).hexdigest()


def q90_hinge_train_only(log_effective_count: np.ndarray, train: np.ndarray,
                         evaluate: np.ndarray, *, quantile: float = Q90
                         ) -> tuple[np.ndarray, dict[str, float]]:
    """Create a single hinge using the training fold's pooled q90, never evaluation values."""
    x = np.asarray(log_effective_count, dtype=np.float64)
    tr, ev = np.asarray(train, dtype=bool), np.asarray(evaluate, dtype=bool)
    if (x.ndim != 1 or tr.shape != x.shape or ev.shape != x.shape or np.any(tr & ev)
            or not tr.any() or not ev.any() or np.any((tr | ev) & ~np.isfinite(x))):
        raise ValueError("q90 hinge needs aligned finite values and nonempty disjoint train/evaluation masks")
    if not 0. < quantile < 1.:
        raise ValueError("quantile must lie strictly between zero and one")
    threshold = float(np.quantile(x[tr], quantile, method="linear"))
    hinge = np.full(x.shape, np.nan)
    hinge[tr | ev] = np.maximum(x[tr | ev] - threshold, 0.)
    return hinge, {"training_q90_threshold": threshold, "training_n": int(tr.sum()),
                   "evaluation_n": int(ev.sum()), "quantile": float(quantile),
                   "quantile_method": "linear", "training_min": float(x[tr].min()),
                   "training_max": float(x[tr].max())}


def delta_r2(target: np.ndarray, baseline_prediction: np.ndarray,
             augmented_prediction: np.ndarray) -> dict[str, float]:
    y, base, aug = (np.asarray(v, dtype=np.float64) for v in
                    (target, baseline_prediction, augmented_prediction))
    if y.ndim != 1 or base.shape != y.shape or aug.shape != y.shape or y.size < 2:
        raise ValueError("paired held-out inputs must align and have at least two rows")
    if not (np.isfinite(y).all() and np.isfinite(base).all() and np.isfinite(aug).all()):
        raise ValueError("paired held-out values must be finite")
    sst = float(np.sum((y - y.mean()) ** 2))
    if sst <= 0.:
        raise ValueError("held-out target must have positive total sum of squares")
    sse_base = float(np.sum((y - base) ** 2))
    sse_aug = float(np.sum((y - aug) ** 2))
    return {"baseline_r2": 1. - sse_base / sst, "hinge_model_r2": 1. - sse_aug / sst,
            "delta_r2": (sse_base - sse_aug) / sst, "baseline_sse": sse_base,
            "hinge_model_sse": sse_aug, "heldout_sst": sst}


def fit_fold(target: np.ndarray, base_features: np.ndarray, log_effective_count: np.ndarray,
             regions: np.ndarray, train: np.ndarray, test: np.ndarray) -> dict[str, Any]:
    """Fit nested region-intercept OLS models with one training-fold q90 hinge."""
    y = np.asarray(target, dtype=np.float64)
    base = np.asarray(base_features, dtype=np.float64)
    log_count = np.asarray(log_effective_count, dtype=np.float64)
    reg = np.asarray(regions, dtype=object)
    tr, te = np.asarray(train, dtype=bool), np.asarray(test, dtype=bool)
    n = y.size
    if (base.ndim != 2 or base.shape[0] != n or log_count.shape != (n,) or reg.shape != (n,)
            or tr.shape != (n,) or te.shape != (n,) or np.any(tr & te)
            or not tr.any() or not te.any()):
        raise ValueError("model inputs must be aligned with nonempty disjoint train/test masks")
    if np.any((tr | te) & (~np.isfinite(y) | ~np.isfinite(log_count) | ~np.isfinite(base).all(axis=1))):
        raise ValueError("selected train/test values must be finite")
    if sorted(set(reg.tolist())) != sorted(REGIONS):
        raise ValueError("model requires all four official regions")
    threshold = float(np.quantile(log_count[tr], Q90, method="linear"))
    hinge = np.maximum(log_count - threshold, 0.)
    # Base predictors are train-standardized for numerical conditioning; no test values affect moments.
    mean = base[tr].mean(axis=0)
    sd = base[tr].std(axis=0, ddof=0)
    scale = np.where(sd > 0., sd, 1.)
    base_z = (base - mean) / scale
    codes = np.asarray([REGIONS.index(str(r)) for r in reg], dtype=np.int8)
    dummies = np.column_stack([(codes == code).astype(np.float64) for code in range(1, len(REGIONS))])
    main = np.column_stack((np.ones(n), dummies, base_z))
    design_base = main
    design_aug = np.column_stack((main, hinge))
    beta_base, _, rank_base, _ = np.linalg.lstsq(design_base[tr], y[tr], rcond=None)
    beta_aug, _, rank_aug, _ = np.linalg.lstsq(design_aug[tr], y[tr], rcond=None)
    if rank_base != design_base.shape[1] or rank_aug != design_aug.shape[1]:
        raise ValueError("training-fold design is rank deficient")
    pred_base, pred_aug = np.full(n, np.nan), np.full(n, np.nan)
    pred_base[te] = design_base[te] @ beta_base
    pred_aug[te] = design_aug[te] @ beta_aug
    return {"baseline_prediction": pred_base, "hinge_prediction": pred_aug,
            "hinge_feature": np.where(tr | te, hinge, np.nan),
            "training_threshold": threshold,
            "base_training_scaler": {"mean": mean.tolist(), "sd": sd.tolist(), "applied_scale": scale.tolist()}}


def county_block_bootstrap(target: np.ndarray, baseline_prediction: np.ndarray,
                           hinge_prediction: np.ndarray, county_ids: np.ndarray, *,
                           replicates: int = BOOTSTRAP_REPLICATES,
                           seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    """Paired percentile CI, resampling whole county clusters with replacement."""
    y, base, aug = (np.asarray(v, dtype=np.float64) for v in
                    (target, baseline_prediction, hinge_prediction))
    county = np.asarray(county_ids, dtype=object)
    if (y.ndim != 1 or base.shape != y.shape or aug.shape != y.shape or county.shape != y.shape
            or y.size < 2 or len(set(county.tolist())) < 2):
        raise ValueError("county bootstrap needs aligned vectors from at least two counties")
    if replicates < 100 or not (np.isfinite(y).all() and np.isfinite(base).all() and np.isfinite(aug).all()):
        raise ValueError("bootstrap inputs must be finite and use at least 100 replicates")
    groups = sorted(set(county.tolist()))
    positions = {key: np.flatnonzero(county == key) for key in groups}
    rng = np.random.default_rng(seed)
    deltas = np.empty(replicates, dtype=np.float64)
    for b in range(replicates):
        selected = rng.choice(len(groups), size=len(groups), replace=True)
        ix = np.concatenate([positions[groups[j]] for j in selected])
        yb, bb, ab = y[ix], base[ix], aug[ix]
        sst = float(np.sum((yb - yb.mean()) ** 2))
        deltas[b] = ((float(np.sum((yb - bb) ** 2)) - float(np.sum((yb - ab) ** 2))) / sst
                     if sst > 0. else np.nan)
    finite = deltas[np.isfinite(deltas)]
    if finite.size < int(.99 * replicates):
        raise ValueError("too many degenerate county-block bootstrap replicates")
    return {"replicates": int(replicates), "seed": int(seed), "cluster_count": len(groups),
            "resampling": "paired whole-county cluster bootstrap with replacement; repeated sampled counties repeat all their supported tracts",
            "ci_95_percentile": [float(np.quantile(finite, .025)), float(np.quantile(finite, .975))],
            "degenerate_replicates": int(replicates - finite.size)}


def read_rows(path: Path, expected_sha: str, columns: list[str],
              official: list[tuple[str, str]]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    digest = sha256_file(path)
    if digest != expected_sha:
        raise ValueError(f"artifact hash mismatch: {path}")
    table = pq.read_table(path, columns=columns)
    if table.num_rows != len(official):
        raise ValueError(f"artifact rows differ from official IDs: {path}")
    locations = {pair: i for i, pair in enumerate(official)}
    out: dict[str, np.ndarray] = {}
    for name in columns:
        if name in ("GEOID", "region"):
            continue
        if name.endswith("_defined") or name == "crossfit_joint_defined":
            out[name] = np.zeros(len(official), dtype=bool)
        else:
            out[name] = np.full(len(official), np.nan)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in locations or pair in seen:
            raise ValueError(f"unexpected or duplicate artifact pair: {pair}")
        seen.add(pair); i = locations[pair]
        for name in out:
            value = row[name]
            if name.endswith("_defined") or name == "crossfit_joint_defined":
                if not isinstance(value, bool):
                    raise ValueError(f"mask {name} must be boolean")
                out[name][i] = value
            elif value is not None:
                out[name][i] = float(value)
    if seen != set(official):
        raise ValueError(f"artifact is not an exact official ID-region partition: {path}")
    return out, {"path": str(path), "sha256": digest, "rows": table.num_rows,
                 "columns_read": columns, "exact_official_pair_alignment": True}


def run(workspace: Path, report_path: Path, output_path: Path) -> dict[str, Any]:
    prior = json.loads(LINEAR_REPORT.read_text(encoding="utf-8"))
    prior_delta = float(prior["measure_after"]["paired_pooled_delta_r2"])
    prior_ci = prior["measure_after"]["paired_pooled_delta_r2_ci_95"]
    prior_positive_regions = int(prior["measure_after"]["positive_regions"])
    if (not np.isclose(prior_delta, EXPECTED_LINEAR_DELTA_R2, rtol=0., atol=1e-8)
            or not (prior_ci[0] <= 0. <= prior_ci[1]) or prior_positive_regions != 2):
        raise ValueError("MEASURE-BEFORE effective-count linear screen differs from declared result")
    effective_report = json.loads(EFFECTIVE_REPORT.read_text(encoding="utf-8"))
    if (effective_report["scope"]["official_ids"] != EXPECTED_N
            or effective_report["scope"]["effective_count_defined_ids"] != 9327
            or not np.isclose(effective_report["measure_after"]["crossfit_residual_variance_ratio"],
                              WITHIN_REGION_RATIO, rtol=0., atol=1e-15)):
        raise ValueError("effective-count source report differs from frozen MEASURE-BEFORE")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N
            or len({g for g, _ in official}) != EXPECTED_N or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official IDs are not an exact 9,379-row/four-region partition")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    geoids = np.asarray([g for g, _ in official], dtype=object)
    regions = np.asarray([r for _, r in official], dtype=object)
    residual_path = workspace / "s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    residual, residual_meta = read_rows(residual_path, RESIDUAL_SHA,
        ["GEOID", "region", "fold", "crossfit_residual", "crossfit_joint_defined"], official)
    effective_path = Path(effective_report["provenance"]["output_artifact"]["path"])
    effective, effective_meta = read_rows(effective_path, EFFECTIVE_SHA,
        ["GEOID", "region", "unique_assigned_place_count", "effective_primary_category_count",
         "effective_count_defined", "Mx4_F", "Mx4_F_defined", "S1_lagged_Mx4_F", "S1_lagged_Mx4_F_defined"], official)
    folds = np.full(EXPECTED_N, -1, dtype=np.int8)
    for region in REGIONS:
        ix = np.flatnonzero(regions == region)
        folds[ix] = deterministic_folds(geoids[ix].tolist(), region, N_FOLDS)
    if not np.array_equal(folds, residual["fold"].astype(np.int8)):
        raise ValueError("frozen S1 folds differ from the exact deterministic five GEOID folds")
    residual_mask = residual["crossfit_joint_defined"]
    poi_mask = effective["effective_count_defined"]
    for value, mask, name in ((residual["crossfit_residual"], residual_mask, "frozen residual"),
                              (effective["effective_primary_category_count"], poi_mask, "effective count"),
                              (effective["Mx4_F"], effective["Mx4_F_defined"], "Mx4 F"),
                              (effective["S1_lagged_Mx4_F"], effective["S1_lagged_Mx4_F_defined"], "S1 lag")):
        if not np.array_equal(np.isfinite(value), mask):
            raise ValueError(f"source values/mask mismatch: {name}")
    place_count = effective["unique_assigned_place_count"]
    effective_count = effective["effective_primary_category_count"]
    if np.any(poi_mask & ((effective_count < 1.) | (place_count < 10.))):
        raise ValueError("effective-count support violates minimum unique-place definition")
    log_count = np.full(EXPECTED_N, np.nan)
    log_count[poi_mask] = np.log1p(effective_count[poi_mask])
    log_places = np.full(EXPECTED_N, np.nan)
    place_mask = np.isfinite(place_count) & (place_count >= 0.)
    log_places[place_mask] = np.log1p(place_count[place_mask])
    base_features = np.column_stack((log_places, effective["Mx4_F"], effective["S1_lagged_Mx4_F"]))
    support = (residual_mask & poi_mask & place_mask & effective["Mx4_F_defined"]
               & effective["S1_lagged_Mx4_F_defined"] & np.isfinite(base_features).all(axis=1)
               & np.isfinite(log_count))
    if int(support.sum()) != 9327:
        raise ValueError(f"paired support changed from expected effective-count support: {int(support.sum())}")
    outcome = residual["crossfit_residual"]
    base_oof, hinge_oof, hinge_values = np.full(EXPECTED_N, np.nan), np.full(EXPECTED_N, np.nan), np.full(EXPECTED_N, np.nan)
    fold_thresholds: dict[str, Any] = {}
    for fold in range(N_FOLDS):
        test, train = support & (folds == fold), support & (folds != fold)
        if not test.any() or not train.any():
            raise ValueError(f"fold {fold} has empty train or test support")
        fit = fit_fold(outcome, base_features, log_count, regions, train, test)
        base_oof[test] = fit["baseline_prediction"][test]
        hinge_oof[test] = fit["hinge_prediction"][test]
        hinge_values[test] = fit["hinge_feature"][test]
        threshold = fit["training_threshold"]
        fold_thresholds[str(fold)] = {"training_q90_threshold": threshold,
            "training_n": int(train.sum()), "heldout_n": int(test.sum()),
            "training_log_effective_count_min": float(log_count[train].min()),
            "training_log_effective_count_max": float(log_count[train].max()),
            "heldout_above_training_q90_n": int(np.sum(test & (log_count > threshold)))}
    if (np.any(support & (~np.isfinite(base_oof) | ~np.isfinite(hinge_oof) | ~np.isfinite(hinge_values)))
            or np.any(~support & (np.isfinite(base_oof) | np.isfinite(hinge_oof) | np.isfinite(hinge_values)))):
        raise AssertionError("OOF output does not preserve paired support")
    pooled = delta_r2(outcome[support], base_oof[support], hinge_oof[support])
    county_ids = np.asarray([g[:5] for g, _ in official], dtype=object)
    if any(len(g) != 11 or not g.isdigit() for g in geoids):
        raise ValueError("official GEOIDs must be 11-digit tract identifiers for county-block resampling")
    bootstrap = county_block_bootstrap(outcome[support], base_oof[support], hinge_oof[support], county_ids[support])
    regional: dict[str, Any] = {}
    for region in REGIONS:
        use = support & (regions == region)
        regional[region] = {"n": int(use.sum()), **delta_r2(outcome[use], base_oof[use], hinge_oof[use])}
    fold_metrics = []
    for fold in range(N_FOLDS):
        use = support & (folds == fold)
        fold_metrics.append({"fold": fold, "n": int(use.sum()),
                             **delta_r2(outcome[use], base_oof[use], hinge_oof[use])})
    positive_regions = sum(row["delta_r2"] > 0. for row in regional.values())
    ci_low, ci_high = bootstrap["ci_95_percentile"]
    gate = bool(pooled["delta_r2"] >= MIN_POOLED_DELTA_R2 and ci_low > MIN_CI_LOWER
                and positive_regions >= MIN_POSITIVE_REGIONS)
    output = pa.table({
        "GEOID": pa.array(geoids.tolist(), type=pa.string()),
        "region": pa.array(regions.tolist(), type=pa.string()),
        "county_fips": pa.array(county_ids.tolist(), type=pa.string()),
        "fold": pa.array(folds, type=pa.int8()),
        "frozen_s1_residual": pa.array([float(v) if ok else None for v, ok in zip(outcome, residual_mask)], type=pa.float64()),
        "s1_residual_defined": pa.array(residual_mask, type=pa.bool_()),
        "effective_count_defined": pa.array(poi_mask, type=pa.bool_()),
        "effective_primary_category_count": pa.array([float(v) if ok else None for v, ok in zip(effective_count, poi_mask)], type=pa.float64()),
        "log1p_effective_primary_category_count": pa.array([float(v) if ok else None for v, ok in zip(log_count, poi_mask)], type=pa.float64()),
        "unique_assigned_place_count": pa.array([int(v) if np.isfinite(v) else None for v in place_count], type=pa.int64()),
        "log1p_unique_place_count": pa.array([float(v) if ok else None for v, ok in zip(log_places, place_mask)], type=pa.float64()),
        "Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(effective["Mx4_F"], effective["Mx4_F_defined"])], type=pa.float64()),
        "S1_lagged_Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(effective["S1_lagged_Mx4_F"], effective["S1_lagged_Mx4_F_defined"])], type=pa.float64()),
        "q90_hinge_defined": pa.array(support, type=pa.bool_()),
        "training_fold_q90_hinge": pa.array([float(v) if ok else None for v, ok in zip(hinge_values, support)], type=pa.float64()),
        "baseline_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(base_oof, support)], type=pa.float64()),
        "hinge_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(hinge_oof, support)], type=pa.float64()),
    })
    payload = parquet_bytes(output)
    if payload != parquet_bytes(output):
        raise ValueError("upper-tail artifact serialization is nondeterministic")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    prior_sha, effective_report_sha = sha256_file(LINEAR_REPORT), sha256_file(EFFECTIVE_REPORT)
    report = {
        "schema_version": 1,
        "screen": "Single training-fold q90 hinge in log1p POI effective count against frozen S1 residual; unregistered",
        "measure_before": {"linear_effective_count_delta_r2": prior_delta,
                           "linear_effective_count_ci_95": prior_ci,
                           "linear_effective_count_positive_regions": prior_positive_regions,
                           "effective_count_prior_report_sha256": prior_sha,
                           "effective_count_residual_variance_ratio": WITHIN_REGION_RATIO,
                           "effective_count_defined_ids": 9327},
        "method": {"outcome": "Frozen cross-fitted orthogonalized S1 disagreement residual; residual artifact and GEOID folds verified, never refit.",
                   "baseline": "Region-intercept OLS with training-standardized log1p unique POI count, frozen Mx4 F and S1 lag, identical to the linear effective-count screen's baseline.",
                   "augmentation": "Add exactly one hinge max(0, log1p(effective count) - q90_training). The single pooled q90 threshold is recomputed from each fold's paired training rows using linear quantile interpolation; no grid or post-hoc threshold tuning.",
                   "preprocessing": "Baseline feature means/SDs and the q90 threshold are fit only on training-fold support. The hinge uses the raw log1p-count scale; no held-out values affect threshold or fit.",
                   "validation": "Exact frozen five deterministic GEOID folds within region; paired held-out predictions on the common complete-case support.",
                   "metric": "Held-out delta-R2=(baseline SSE-hinge SSE)/shared held-out residual SST; report pooled, per-region and per-fold values.",
                   "uncertainty": "Paired percentile 95% interval from whole-county cluster bootstrap with replacement, resampling all supported tracts within sampled counties.",
                   "county_definition": "First five digits of each official 11-digit tract GEOID; used only as bootstrap cluster IDs.",
                   "support": "Frozen residual, effective count, unique-place count, Mx4 F and S1 lag all defined; no imputation or exclusions after results.",
                   "unregistered": True, "interpretation_limit": "Provided-feature descriptive screen only; not hidden-target accuracy, causal evidence, or integration evidence."},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": official_sha,
                  "paired_support_ids": int(support.sum()), "paired_support_undefined_ids": int((~support).sum()),
                  "frozen_residual_defined_ids": int(residual_mask.sum()), "effective_count_defined_ids": int(poi_mask.sum()),
                  "paired_mask_sha256": mask_sha256(support), "residual_mask_sha256": mask_sha256(residual_mask),
                  "effective_count_mask_sha256": mask_sha256(poi_mask),
                  "fold_assignment_sha256": hashlib.sha256(folds.tobytes()).hexdigest(),
                  "fold_support": {str(f): int(np.sum(support & (folds == f))) for f in range(N_FOLDS)},
                  "county_cluster_count": len(set(county_ids[support].tolist())),
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_references_read": False, "external_data_used": [],
                  "changed_folds": False, "posthoc_threshold_tuning": False,
                  "leaderboard_feedback_used": False, "uploads": 0},
        "measure_after": {"pooled": pooled, "paired_county_block_bootstrap": bootstrap,
                          "paired_pooled_delta_r2_ci_95": bootstrap["ci_95_percentile"],
                          "regional": regional, "folds": fold_metrics,
                          "positive_regions": positive_regions,
                          "training_fold_q90_thresholds": fold_thresholds},
        "thresholds": {"pooled_delta_r2_minimum_inclusive": MIN_POOLED_DELTA_R2,
                       "county_block_ci_lower_strictly_above": MIN_CI_LOWER,
                       "minimum_positive_regions": MIN_POSITIVE_REGIONS,
                       "hinge_quantile_fixed": Q90},
        "acceptance": {"pooled_delta_r2_at_least_0_005": bool(pooled["delta_r2"] >= MIN_POOLED_DELTA_R2),
                       "county_block_ci_lower_above_zero": bool(ci_low > MIN_CI_LOWER),
                       "positive_delta_in_at_least_3_of_4_regions": bool(positive_regions >= MIN_POSITIVE_REGIONS),
                       "upper_tail_screen_gate_pass": gate},
        "provenance": {"official_geoid_region_sha256": official_sha,
                       "frozen_s1_residual_artifact": residual_meta,
                       "poi_effective_count_artifact": effective_meta,
                       "effective_count_source_report": {"path": str(EFFECTIVE_REPORT), "sha256": effective_report_sha},
                       "linear_screen_report": {"path": str(LINEAR_REPORT), "sha256": prior_sha},
                       "output_artifact": {"path": str(output_path), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": output.num_rows, "columns": output.column_names,
                                           "deterministic_serialization": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The q90 effective-count hinge passed all preregistered gates; retain as descriptive provided-feature association only, not as target-accuracy or integration evidence." if gate else
                         "The q90 effective-count hinge failed one or more preregistered gates; keep the feature descriptive-only and do not integrate the hinge."}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/poi-effective-count-upper-tail.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "poi-effective-count-upper-tail/paired-oof-predictions.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"scope": report["scope"], "measure_after": report["measure_after"],
                      "acceptance": report["acceptance"], "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
