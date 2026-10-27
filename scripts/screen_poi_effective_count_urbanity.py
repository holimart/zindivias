#!/usr/bin/env python3
"""Five-fold interaction screen for POI effective count × provided tract urbanity."""
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
RESIDUAL_SHA = "437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
EFFECTIVE_SHA = "74c35567243ad8ea3c00cf5117ef641e49148b63f89f7f07616ac9dcf33b8d6e"
EFFECTIVE_REPORT = ROOT / "reports/poi-category-effective-count.json"
WITHIN_REGION_RATIO = 0.18096672540982753
N_FOLDS = 5
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261030
MIN_POOLED_DELTA_R2 = 0.01
MIN_CI_LOWER = 0.0
MIN_POSITIVE_FOLDS = 4


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mask_sha256(mask: np.ndarray) -> str:
    return hashlib.sha256(np.packbits(np.asarray(mask, dtype=np.uint8), bitorder="little").tobytes()).hexdigest()


def scale_poi_within_region_train_only(values: np.ndarray, regions: np.ndarray,
                                       train: np.ndarray, test: np.ndarray
                                       ) -> tuple[np.ndarray, dict[str, dict[str, float]]]:
    """Standardize log1p effective count by region, using training rows only."""
    x = np.asarray(values, dtype=np.float64)
    reg = np.asarray(regions, dtype=object)
    tr, te = np.asarray(train, dtype=bool), np.asarray(test, dtype=bool)
    if x.ndim != 1 or reg.shape != x.shape or tr.shape != x.shape or te.shape != x.shape:
        raise ValueError("scaling inputs must be aligned vectors")
    if np.any(tr & te) or not tr.any() or not te.any() or np.any((tr | te) & ~np.isfinite(x)):
        raise ValueError("train/test masks must be finite, nonempty and disjoint")
    scaled = np.full(x.shape, np.nan)
    stats: dict[str, dict[str, float]] = {}
    for region in sorted(set(reg[tr | te].tolist())):
        train_region = tr & (reg == region)
        test_region = te & (reg == region)
        if not test_region.any():
            continue
        if not train_region.any():
            raise ValueError(f"held-out region {region} has no training rows")
        mean = float(x[train_region].mean())
        sd = float(x[train_region].std(ddof=0))
        scale = sd if sd > 0. else 1.
        scaled[train_region] = (x[train_region] - mean) / scale
        scaled[test_region] = (x[test_region] - mean) / scale
        stats[region] = {"training_mean": mean, "training_sd": sd, "applied_scale": scale,
                         "training_n": int(train_region.sum()), "heldout_n": int(test_region.sum())}
    if np.any((tr | te) & ~np.isfinite(scaled)):
        raise AssertionError("POI scaler failed to cover selected rows")
    return scaled, stats


def make_design(base_features: np.ndarray, poi_main: np.ndarray, pct_urban: np.ndarray,
                poi_x_urbanity: np.ndarray, regions: np.ndarray, region_code: dict[str, int]
                ) -> tuple[np.ndarray, np.ndarray]:
    """Return main-effects design and its nested interaction augmentation."""
    n = len(regions)
    base = np.asarray(base_features, dtype=np.float64)
    poi = np.asarray(poi_main, dtype=np.float64)
    urban = np.asarray(pct_urban, dtype=np.float64)
    interaction = np.asarray(poi_x_urbanity, dtype=np.float64)
    reg = np.asarray(regions, dtype=object)
    if base.ndim != 2 or base.shape[0] != n or any(v.shape != (n,) for v in (poi, urban, interaction)):
        raise ValueError("design inputs must align")
    codes = np.asarray([region_code[str(r)] for r in reg], dtype=np.int8)
    dummies = np.column_stack([(codes == code).astype(np.float64) for code in range(1, len(region_code))])
    design_main = np.column_stack((np.ones(n), dummies, base, poi, urban))
    design_interaction = np.column_stack((design_main, interaction))
    return design_main, design_interaction


def fit_fold(target: np.ndarray, baseline_predictors: np.ndarray, log_poi: np.ndarray,
             pct_urban: np.ndarray, regions: np.ndarray, train: np.ndarray, test: np.ndarray
             ) -> dict[str, Any]:
    """Fit nested main-effects and interaction OLS models using training rows only."""
    y = np.asarray(target, dtype=np.float64)
    base_x = np.asarray(baseline_predictors, dtype=np.float64)
    poi = np.asarray(log_poi, dtype=np.float64)
    urban = np.asarray(pct_urban, dtype=np.float64)
    reg = np.asarray(regions, dtype=object)
    tr, te = np.asarray(train, dtype=bool), np.asarray(test, dtype=bool)
    n = len(y)
    if (base_x.ndim != 2 or base_x.shape[0] != n or poi.shape != (n,) or urban.shape != (n,)
            or reg.shape != (n,) or tr.shape != (n,) or te.shape != (n,)
            or np.any(tr & te) or not tr.any() or not te.any()):
        raise ValueError("fold inputs must align with nonempty disjoint train/test masks")
    if np.any((tr | te) & (~np.isfinite(y) | ~np.isfinite(poi) | ~np.isfinite(urban)
                              | ~np.isfinite(base_x).all(axis=1))):
        raise ValueError("selected fold rows must be finite")
    if np.any((tr | te) & ((urban < 0.) | (urban > 1.))):
        raise ValueError("pct_urban must lie in [0,1]")
    if sorted(set(reg.tolist())) != sorted(REGIONS):
        raise ValueError("interaction screen requires all four official regions")
    # Baseline continuous features are standardized globally using training rows only.
    base_mean = base_x[tr].mean(axis=0)
    base_sd = base_x[tr].std(axis=0, ddof=0)
    base_scale = np.where(base_sd > 0., base_sd, 1.)
    base_z = (base_x - base_mean) / base_scale
    poi_z, poi_stats = scale_poi_within_region_train_only(poi, reg, tr, te)
    # The preregistered product is training-fold-standardized log1p effective count × raw pct_urban.
    interaction = poi_z * urban
    region_code = {name: idx for idx, name in enumerate(REGIONS)}
    design_main, design_full = make_design(base_z, poi_z, urban, interaction, reg, region_code)
    beta_main, _, rank_main, _ = np.linalg.lstsq(design_main[tr], y[tr], rcond=None)
    beta_full, _, rank_full, _ = np.linalg.lstsq(design_full[tr], y[tr], rcond=None)
    if rank_main != design_main.shape[1] or rank_full != design_full.shape[1]:
        raise ValueError("training-fold design is rank deficient")
    main_prediction = np.full(n, np.nan)
    full_prediction = np.full(n, np.nan)
    main_prediction[te] = design_main[te] @ beta_main
    full_prediction[te] = design_full[te] @ beta_full
    return {"main_prediction": main_prediction, "interaction_prediction": full_prediction,
            "poi_training_scaler": poi_stats}


def delta_r2(target: np.ndarray, main_prediction: np.ndarray,
             interaction_prediction: np.ndarray) -> dict[str, float]:
    y = np.asarray(target, dtype=np.float64)
    main = np.asarray(main_prediction, dtype=np.float64)
    full = np.asarray(interaction_prediction, dtype=np.float64)
    if y.ndim != 1 or main.shape != y.shape or full.shape != y.shape or not y.size:
        raise ValueError("delta-R2 inputs must be aligned nonempty vectors")
    if not (np.isfinite(y).all() and np.isfinite(main).all() and np.isfinite(full).all()):
        raise ValueError("delta-R2 inputs must be finite")
    sst = float(np.sum((y - y.mean()) ** 2))
    if sst <= 0.:
        raise ValueError("held-out target must have positive total sum of squares")
    main_sse = float(np.sum((y - main) ** 2))
    full_sse = float(np.sum((y - full) ** 2))
    return {"main_effects_r2": 1. - main_sse / sst,
            "interaction_model_r2": 1. - full_sse / sst,
            "delta_r2": (main_sse - full_sse) / sst,
            "main_effects_sse": main_sse, "interaction_model_sse": full_sse, "heldout_sst": sst}


def paired_tract_bootstrap(target: np.ndarray, main_prediction: np.ndarray,
                           interaction_prediction: np.ndarray, *, replicates: int = BOOTSTRAP_REPLICATES,
                           seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    y = np.asarray(target, dtype=np.float64)
    main = np.asarray(main_prediction, dtype=np.float64)
    full = np.asarray(interaction_prediction, dtype=np.float64)
    if y.ndim != 1 or main.shape != y.shape or full.shape != y.shape or y.size < 2:
        raise ValueError("bootstrap vectors must align and contain at least two tracts")
    if replicates < 100 or not (np.isfinite(y).all() and np.isfinite(main).all() and np.isfinite(full).all()):
        raise ValueError("bootstrap inputs must be finite and use at least 100 replicates")
    rng = np.random.default_rng(seed)
    values = np.empty(replicates, dtype=np.float64)
    n = y.size
    for b in range(replicates):
        ix = rng.integers(0, n, size=n)
        yb, mb, fb = y[ix], main[ix], full[ix]
        sst = float(np.sum((yb - yb.mean()) ** 2))
        values[b] = ((float(np.sum((yb - mb) ** 2)) - float(np.sum((yb - fb) ** 2))) / sst
                     if sst > 0. else np.nan)
    finite = values[np.isfinite(values)]
    if finite.size < int(.99 * replicates):
        raise ValueError("too many degenerate bootstrap replicates")
    return {"replicates": int(replicates), "seed": int(seed),
            "resampling": "paired with-replacement tract resampling over the exact pooled OOF support",
            "ci_95_percentile": [float(np.quantile(finite, .025)), float(np.quantile(finite, .975))],
            "degenerate_replicates": int(replicates - finite.size)}


def _read_rows(path: Path, expected_sha: str, columns: list[str],
               official: list[tuple[str, str]]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    digest = sha256_file(path)
    if digest != expected_sha:
        raise ValueError(f"frozen artifact hash mismatch: {path}")
    table = pq.read_table(path, columns=columns)
    if table.num_rows != len(official):
        raise ValueError(f"artifact row count differs from official IDs: {path}")
    locations = {pair: i for i, pair in enumerate(official)}
    seen: set[tuple[str, str]] = set()
    out: dict[str, np.ndarray] = {}
    for name in columns:
        if name in ("GEOID", "region"):
            continue
        if name.endswith("_defined") or name in ("crossfit_joint_defined",):
            out[name] = np.zeros(len(official), dtype=bool)
        else:
            out[name] = np.full(len(official), np.nan, dtype=np.float64)
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in locations or pair in seen:
            raise ValueError(f"unexpected or duplicate artifact ID-region pair: {pair}")
        seen.add(pair)
        i = locations[pair]
        for name in out:
            value = row[name]
            if name.endswith("_defined") or name == "crossfit_joint_defined":
                if not isinstance(value, bool):
                    raise ValueError(f"artifact mask {name} must be boolean")
                out[name][i] = value
            elif value is not None:
                out[name][i] = float(value)
    if seen != set(official):
        raise ValueError(f"artifact is not an exact official ID-region partition: {path}")
    return out, {"path": str(path), "sha256": digest, "rows": table.num_rows,
                 "columns_read": columns, "exact_official_pair_alignment": True}


def read_pct_urban(workspace: Path, official: list[tuple[str, str]]) -> tuple[np.ndarray, dict[str, Any]]:
    values = np.full(len(official), np.nan)
    locations = {pair: i for i, pair in enumerate(official)}
    provenance: dict[str, Any] = {}
    for region in REGIONS:
        path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        table = pq.read_table(path, columns=["GEOID", "pct_urban"])
        expected = {g for g, r in official if r == region}
        found: set[str] = set()
        for geoid_value, value in zip(table["GEOID"].to_pylist(), table["pct_urban"].to_pylist()):
            geoid = str(geoid_value)
            pair = (geoid, region)
            if pair not in locations:
                continue
            if geoid in found:
                raise ValueError(f"duplicate pct_urban row in {region}: {geoid}")
            found.add(geoid)
            if value is not None:
                values[locations[pair]] = float(value)
        if found != expected:
            raise ValueError(f"pct_urban IDs differ from official IDs for {region}")
        ix = [locations[(g, region)] for g in expected]
        provenance[region] = {"path": str(path), "sha256": sha256_file(path),
                              "columns_read": ["GEOID", "pct_urban"],
                              "official_ids": len(found), "pct_urban_defined_ids": int(np.isfinite(values[ix]).sum())}
    if np.any(np.isfinite(values) & ((values < 0.) | (values > 1.))):
        raise ValueError("provided pct_urban values must lie in [0,1]")
    return values, provenance


def run(workspace: Path, report_path: Path, output_path: Path) -> dict[str, Any]:
    effective_report = json.loads(EFFECTIVE_REPORT.read_text(encoding="utf-8"))
    if (effective_report["scope"]["official_ids"] != EXPECTED_N
            or effective_report["scope"]["effective_count_defined_ids"] != 9327
            or not np.isclose(effective_report["measure_after"]["crossfit_residual_variance_ratio"],
                              WITHIN_REGION_RATIO, rtol=0., atol=1e-15)):
        raise ValueError("effective-count MEASURE-BEFORE values differ from the frozen report")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N
            or len({g for g, _ in official}) != EXPECTED_N or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official IDs are not an exact 9,379-row/four-region partition")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    geoids = np.asarray([g for g, _ in official], dtype=object)
    regions = np.asarray([r for _, r in official], dtype=object)

    residual_path = workspace / "s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    residual, residual_meta = _read_rows(
        residual_path, RESIDUAL_SHA,
        ["GEOID", "region", "fold", "crossfit_residual", "crossfit_joint_defined"], official)
    effective_path = Path(effective_report["provenance"]["output_artifact"]["path"])
    effective, effective_meta = _read_rows(
        effective_path, EFFECTIVE_SHA,
        ["GEOID", "region", "unique_assigned_place_count", "effective_primary_category_count",
         "effective_count_defined", "Mx4_F", "Mx4_F_defined", "S1_lagged_Mx4_F", "S1_lagged_Mx4_F_defined"], official)
    folds = np.full(EXPECTED_N, -1, dtype=np.int8)
    for region in REGIONS:
        ix = np.flatnonzero(regions == region)
        folds[ix] = deterministic_folds(geoids[ix].tolist(), region, N_FOLDS)
    if not np.array_equal(residual["fold"].astype(np.int8), folds):
        raise ValueError("frozen residual folds differ from required deterministic five GEOID folds")
    residual_mask = residual["crossfit_joint_defined"]
    poi_mask = effective["effective_count_defined"]
    if not np.array_equal(np.isfinite(residual["crossfit_residual"]), residual_mask):
        raise ValueError("frozen S1 residual values and support mask disagree")
    if not np.array_equal(np.isfinite(effective["effective_primary_category_count"]), poi_mask):
        raise ValueError("effective-count values and support mask disagree")
    for name in ("Mx4_F", "S1_lagged_Mx4_F"):
        if not np.array_equal(np.isfinite(effective[name]), effective[name + "_defined"]):
            raise ValueError(f"effective artifact mask mismatch for {name}")
    pct_urban, urban_provenance = read_pct_urban(workspace, official)
    urban_mask = np.isfinite(pct_urban)
    place_count = effective["unique_assigned_place_count"]
    place_mask = np.isfinite(place_count) & (place_count >= 0.)
    log_poi = np.full(EXPECTED_N, np.nan)
    log_poi[poi_mask] = np.log1p(effective["effective_primary_category_count"][poi_mask])
    log_places = np.full(EXPECTED_N, np.nan)
    log_places[place_mask] = np.log1p(place_count[place_mask])
    support = (residual_mask & poi_mask & urban_mask & place_mask & effective["Mx4_F_defined"]
               & effective["S1_lagged_Mx4_F_defined"] & np.isfinite(log_poi) & np.isfinite(log_places))
    if not support.any():
        raise ValueError("no paired complete cases for the effective-count × urbanity screen")
    target = residual["crossfit_residual"]
    # The interaction helper standardizes log1p effective count inside each training-fold region.
    baseline_features = np.column_stack((log_places, effective["Mx4_F"], effective["S1_lagged_Mx4_F"]))
    main_oof = np.full(EXPECTED_N, np.nan)
    interaction_oof = np.full(EXPECTED_N, np.nan)
    scaler_details: dict[str, Any] = {}
    for fold in range(N_FOLDS):
        test = support & (folds == fold)
        train = support & (folds != fold)
        if not test.any() or not train.any():
            raise ValueError(f"fold {fold} has empty paired train or test rows")
        fitted = fit_fold(target, baseline_features, log_poi, pct_urban, regions, train, test)
        main_oof[test] = fitted["main_prediction"][test]
        interaction_oof[test] = fitted["interaction_prediction"][test]
        scaler_details[str(fold)] = fitted["poi_training_scaler"]
    if (np.any(support & (~np.isfinite(main_oof) | ~np.isfinite(interaction_oof)))
            or np.any(~support & (np.isfinite(main_oof) | np.isfinite(interaction_oof)))):
        raise AssertionError("paired OOF predictions do not preserve the support mask")

    pooled = delta_r2(target[support], main_oof[support], interaction_oof[support])
    bootstrap = paired_tract_bootstrap(target[support], main_oof[support], interaction_oof[support])
    regional: dict[str, Any] = {}
    for region in REGIONS:
        use = support & (regions == region)
        regional[region] = {"n": int(use.sum()), **delta_r2(target[use], main_oof[use], interaction_oof[use])}
    fold_scores = []
    for fold in range(N_FOLDS):
        use = support & (folds == fold)
        fold_scores.append({"fold": fold, "n": int(use.sum()),
                            **delta_r2(target[use], main_oof[use], interaction_oof[use])})
    positive_folds = sum(x["delta_r2"] > 0. for x in fold_scores)
    ci_low, ci_high = bootstrap["ci_95_percentile"]
    pooled_delta = pooled["delta_r2"]
    gate = bool(pooled_delta >= MIN_POOLED_DELTA_R2 and ci_low > MIN_CI_LOWER
                and positive_folds >= MIN_POSITIVE_FOLDS)

    output = pa.table({
        "GEOID": pa.array(geoids.tolist(), type=pa.string()),
        "region": pa.array(regions.tolist(), type=pa.string()),
        "fold": pa.array(folds, type=pa.int8()),
        "frozen_s1_crossfit_residual": pa.array([float(v) if ok else None for v, ok in zip(target, residual_mask)], type=pa.float64()),
        "s1_residual_defined": pa.array(residual_mask, type=pa.bool_()),
        "effective_primary_category_count": pa.array([float(v) if ok else None for v, ok in zip(effective["effective_primary_category_count"], poi_mask)], type=pa.float64()),
        "log1p_effective_primary_category_count": pa.array([float(v) if ok else None for v, ok in zip(log_poi, poi_mask)], type=pa.float64()),
        "pct_urban": pa.array([float(v) if ok else None for v, ok in zip(pct_urban, urban_mask)], type=pa.float64()),
        "log1p_unique_place_count": pa.array([float(v) if ok else None for v, ok in zip(log_places, place_mask)], type=pa.float64()),
        "Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(effective["Mx4_F"], effective["Mx4_F_defined"])], type=pa.float64()),
        "S1_lagged_Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(effective["S1_lagged_Mx4_F"], effective["S1_lagged_Mx4_F_defined"])], type=pa.float64()),
        "paired_screen_defined": pa.array(support, type=pa.bool_()),
        "main_effects_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(main_oof, support)], type=pa.float64()),
        "interaction_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(interaction_oof, support)], type=pa.float64()),
    })
    payload = parquet_bytes(output)
    if payload != parquet_bytes(output):
        raise ValueError("interaction OOF artifact serialization is nondeterministic")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    effective_report_sha = sha256_file(EFFECTIVE_REPORT)
    report = {
        "schema_version": 1,
        "screen": "Five-fold held-out additive interaction screen: provided POI effective count × pct_urban against frozen S1 disagreement residual; unregistered",
        "measure_before": {"effective_count_crossfit_residual_variance_ratio": WITHIN_REGION_RATIO,
                           "effective_count_joint_ids": 9327, "urbanity_interaction_measured": False,
                           "effective_count_report_sha256": effective_report_sha},
        "method": {"outcome": "Frozen cross-fitted orthogonalized S1 disagreement residual; its source artifact and fold assignments are verified and not rebuilt.",
                   "main_effects_baseline": "Region intercepts, training-standardized log1p unique POI count, Mx4 F, S1 lag, training-fold within-region standardized log1p effective count, and provided pct_urban.",
                   "interaction": "Add the single product (training-fold within-region standardized log1p effective count) × raw pct_urban. Both main effects remain in the augmented model.",
                   "cross_validation": "Exact frozen five deterministic GEOID folds within each region. Nested OLS models and all fitted feature scaling use only the other four training folds; evaluation uses paired held-out rows.",
                   "metric": "Held-out delta-R2 = interaction-model R2 minus main-effects-model R2, equivalently (main-effects SSE - interaction SSE)/the shared held-out target SST; positive favors the interaction.",
                   "bootstrap": "Paired with-replacement tract bootstrap over pooled OOF complete-case support; percentile interval, fixed seed/replicates reported.",
                   "support": "Single paired mask requiring frozen residual, effective count, pct_urban, unique place count, Mx4 F and S1 lag all defined; no post-result strata or imputation.",
                   "model_coefficients_disclosed": False, "training_scaling_moments_recorded_by_fold": True,
                   "unregistered": True,
                   "interpretation_limit": "Label-free provided-feature interaction screen only; not hidden-target accuracy, causality, or estimator-integration evidence."},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": official_sha,
                  "frozen_residual_defined_ids": int(residual_mask.sum()), "effective_count_defined_ids": int(poi_mask.sum()),
                  "pct_urban_defined_ids": int(urban_mask.sum()), "paired_screen_ids": int(support.sum()),
                  "paired_screen_undefined_ids": int((~support).sum()),
                  "residual_mask_sha256": mask_sha256(residual_mask), "effective_count_mask_sha256": mask_sha256(poi_mask),
                  "pct_urban_mask_sha256": mask_sha256(urban_mask), "paired_mask_sha256": mask_sha256(support),
                  "fold_assignment_sha256": hashlib.sha256(folds.tobytes()).hexdigest(),
                  "labels_read": False, "sample_score_values_read": False, "removed_or_equivalent_references_read": False,
                  "external_data_used": [], "leaderboard_feedback_used": False, "post_result_strata": False,
                  "estimator_or_registry_changed": False, "uploads": 0},
        "measure_after": {"pooled": pooled, "paired_pooled_delta_r2": pooled_delta,
                          "paired_pooled_delta_r2_ci_95": bootstrap["ci_95_percentile"], "bootstrap": bootstrap,
                          "regional": regional, "folds": fold_scores,
                          "positive_folds": positive_folds},
        "thresholds": {"pooled_delta_r2_minimum_inclusive": MIN_POOLED_DELTA_R2,
                       "bootstrap_ci_lower_strictly_above": MIN_CI_LOWER,
                       "minimum_positive_folds": MIN_POSITIVE_FOLDS},
        "acceptance": {"pooled_delta_r2_at_least_0_01": bool(pooled_delta >= MIN_POOLED_DELTA_R2),
                       "bootstrap_ci_lower_above_zero": bool(ci_low > MIN_CI_LOWER),
                       "positive_delta_in_at_least_4_of_5_folds": bool(positive_folds >= MIN_POSITIVE_FOLDS),
                       "urbanity_interaction_gate_pass": gate},
        "provenance": {"official_geoid_region_sha256": official_sha,
                       "frozen_residual_artifact": residual_meta,
                       "effective_count_artifact": effective_meta,
                       "effective_count_report": {"path": str(EFFECTIVE_REPORT), "sha256": effective_report_sha},
                       "pct_urban_by_region": urban_provenance,
                       "output_artifact": {"path": str(output_path), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": output.num_rows, "columns": output.column_names,
                                           "deterministic_serialization": True},
                       "poi_training_scalers_by_fold": scaler_details,
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The POI effective-count × urbanity interaction passed all preregistered gates; this is only a descriptive provided-feature association and does not authorize integration." if gate else
                         "The POI effective-count × urbanity interaction failed one or more preregistered gates; retire the interaction as unsupported and do not integrate it.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/poi-effective-count-urbanity.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    output_path = Path(args.artifact) if args.artifact else workspace / "poi-effective-count-urbanity/paired-oof-predictions.parquet"
    report = run(workspace, ROOT / args.out, output_path)
    print(json.dumps({"scope": report["scope"], "measure_after": report["measure_after"],
                      "acceptance": report["acceptance"], "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
