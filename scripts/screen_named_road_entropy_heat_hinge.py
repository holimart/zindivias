#!/usr/bin/env python3
"""Cross-fitted upper-tail heat-hinge screen for named-road class entropy."""
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
from screen_named_road_entropy_continuous_heat import (  # noqa: E402
    BOOTSTRAP_REPLICATES,
    N_FOLDS,
    REGIONS,
    _ci,
    _delta_r2,
    bootstrap_pooled_delta,
    bootstrap_region_delta,
    load_data,
    seed_for,
)

CONTINUOUS_REPORT = ROOT / "reports/named-road-entropy-continuous-heat.json"
MEASURE_BEFORE_DELTA = 0.00019373184392459616
MEASURE_BEFORE_CI = [-0.0008463692946404365, 0.0012186074544889723]
MEASURE_BEFORE_POSITIVE_REGIONS = 1
Q75 = 0.75
MIN_DELTA_R2 = 0.01


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def heat_hinge(heat: np.ndarray, training_q75: float) -> np.ndarray:
    values = np.asarray(heat, dtype=np.float64)
    knot = float(training_q75)
    if values.ndim != 1 or not np.isfinite(values).all() or not np.isfinite(knot):
        raise ValueError("heat and training-fold q75 must be finite aligned values")
    return np.maximum(values - knot, 0.)


def crossfit_region(geoids: list[str], region: str, folds: np.ndarray, entropy: np.ndarray,
                    heat: np.ndarray, baseline_predictors: np.ndarray, mask: np.ndarray
                    ) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], np.ndarray, np.ndarray, np.ndarray]:
    """Fit paired region-local baseline/hinge OLS models with training-only q75 knots."""
    n = len(geoids)
    fold_ids = np.asarray(folds, dtype=np.int8)
    y = np.asarray(entropy, dtype=np.float64)
    h = np.asarray(heat, dtype=np.float64)
    x = np.asarray(baseline_predictors, dtype=np.float64)
    use = np.asarray(mask, dtype=bool)
    if region not in REGIONS or len(set(geoids)) != n:
        raise ValueError("crossfit requires unique GEOIDs and a recognized region")
    if (fold_ids.shape != (n,) or y.shape != (n,) or h.shape != (n,) or use.shape != (n,)
            or x.ndim != 2 or x.shape[0] != n):
        raise ValueError("crossfit inputs must align with region GEOIDs")
    if np.any((fold_ids < 0) | (fold_ids >= N_FOLDS)):
        raise ValueError("fold IDs must be in 0..4")
    if np.any(use & (~np.isfinite(y) | ~np.isfinite(h) | ~np.isfinite(x).all(axis=1))):
        raise ValueError("complete-case rows must have finite entropy, heat and baseline predictors")
    base_oof = np.full(n, np.nan, dtype=np.float64)
    hinge_oof = np.full(n, np.nan, dtype=np.float64)
    knot_oof = np.full(n, np.nan, dtype=np.float64)
    hinge_slopes = np.full(N_FOLDS, np.nan, dtype=np.float64)
    diagnostics: list[dict[str, Any]] = []
    for fold in range(N_FOLDS):
        test = use & (fold_ids == fold)
        train = use & (fold_ids != fold)
        if not test.any() or int(train.sum()) <= x.shape[1] + 1:
            raise ValueError(f"{region} fold {fold}: insufficient finite training or held-out rows")
        # The knot is estimated only from training-fold complete-case tracts.
        knot = float(np.quantile(h[train], Q75))
        train_hinge = heat_hinge(h[train], knot)
        test_hinge = heat_hinge(h[test], knot)
        base_train = np.column_stack((np.ones(int(train.sum())), x[train]))
        base_test = np.column_stack((np.ones(int(test.sum())), x[test]))
        aug_train = np.column_stack((base_train, train_hinge))
        aug_test = np.column_stack((base_test, test_hinge))
        beta_base, _, rank_base, singular_base = np.linalg.lstsq(base_train, y[train], rcond=None)
        beta_aug, _, rank_aug, singular_aug = np.linalg.lstsq(aug_train, y[train], rcond=None)
        base_oof[test] = base_test @ beta_base
        hinge_oof[test] = aug_test @ beta_aug
        knot_oof[test] = knot
        hinge_slopes[fold] = float(beta_aug[-1])
        diagnostics.append({"fold": fold, "train_n": int(train.sum()), "heldout_n": int(test.sum()),
                            "training_heat_q75_knot": knot,
                            "training_heat_min": float(np.min(h[train])),
                            "training_heat_max": float(np.max(h[train])),
                            "heldout_heat_above_training_q75_n": int(np.count_nonzero(h[test] > knot)),
                            "baseline_design_rank": int(rank_base), "hinge_design_rank": int(rank_aug),
                            "baseline_design_columns": int(base_train.shape[1]),
                            "hinge_design_columns": int(aug_train.shape[1]),
                            "hinge_slope": float(beta_aug[-1]),
                            "baseline_min_singular_value": float(singular_base.min()) if singular_base.size else None,
                            "hinge_min_singular_value": float(singular_aug.min()) if singular_aug.size else None})
    if (np.any(use & (~np.isfinite(base_oof) | ~np.isfinite(hinge_oof) | ~np.isfinite(knot_oof)))
            or np.any(~use & (np.isfinite(base_oof) | np.isfinite(hinge_oof) | np.isfinite(knot_oof)))):
        raise AssertionError("paired OOF predictions and knots must match the exact complete-case mask")
    return base_oof, hinge_oof, diagnostics, hinge_slopes, fold_ids, knot_oof


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    pairs, values, provenance = load_data(workspace)
    if not CONTINUOUS_REPORT.is_file():
        raise FileNotFoundError("the completed continuous-heat report is required")
    prior = json.loads(CONTINUOUS_REPORT.read_text(encoding="utf-8"))
    before = prior["measure_after"]
    if (not np.isclose(before["pooled_paired_heldout_delta_r2"], MEASURE_BEFORE_DELTA, rtol=0., atol=1e-15)
            or not np.allclose(before["pooled_paired_heldout_delta_r2_bootstrap_95_ci"], MEASURE_BEFORE_CI, rtol=0., atol=1e-15)
            or before["regions_with_positive_heldout_delta_r2"] != MEASURE_BEFORE_POSITIVE_REGIONS):
        raise ValueError("continuous heat MEASURE-BEFORE report differs from the dispatch")
    n = len(pairs)
    entropy, entropy_mask = values["entropy"], values["entropy_defined"]
    heat, heat_mask = values["heat"], values["heat_defined"]
    log_road = np.full(n, np.nan, dtype=np.float64)
    log_road[entropy_mask] = np.log(values["total_road_km"][entropy_mask])
    predictors = np.column_stack((log_road, values["pct_urban"], values["mx4"], values["s1"]))
    complete = (entropy_mask & heat_mask & np.isfinite(predictors).all(axis=1)
                & values["mx4_defined"] & values["s1_defined"])
    if not complete.any():
        raise ValueError("no complete cases for the upper-tail heat-hinge screen")
    folds = values["fold"].copy()
    base_oof = np.full(n, np.nan); hinge_oof = np.full(n, np.nan); knot_oof = np.full(n, np.nan)
    region_data: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    regional: dict[str, Any] = {}
    for region in REGIONS:
        idx = np.asarray([i for i, (_, r) in enumerate(pairs) if r == region], dtype=np.int64)
        local_ids = [pairs[i][0] for i in idx]
        pred_base, pred_hinge, fold_reports, slopes, used_folds, knots = crossfit_region(
            local_ids, region, folds[idx], entropy[idx], heat[idx], predictors[idx], complete[idx])
        if not np.array_equal(used_folds, folds[idx]):
            raise AssertionError("the exact named-road entropy GEOID folds were not reused")
        base_oof[idx], hinge_oof[idx], knot_oof[idx] = pred_base, pred_hinge, knots
        support = complete[idx]
        y, base, hinge = entropy[idx][support], pred_base[support], pred_hinge[support]
        metrics = _delta_r2(y, base, hinge)
        seed = seed_for(f"named-road-entropy-heat-hinge:{region}")
        ci = _ci(bootstrap_region_delta(y, base, hinge, seed, BOOTSTRAP_REPLICATES))
        region_data[region] = (y, base, hinge)
        regional[region] = {
            "official_ids": int(idx.size), "entropy_defined_ids": int(entropy_mask[idx].sum()),
            "heat_covered_ids": int(heat_mask[idx].sum()), "joint_complete_case_ids": int(support.sum()),
            "fold_heldout_support": {str(f): int(np.count_nonzero(support & (folds[idx] == f))) for f in range(N_FOLDS)},
            "heldout_baseline_r2": metrics["baseline_r2"], "heldout_hinge_r2": metrics["heat_augmented_r2"],
            "heldout_delta_r2": metrics["delta_r2"], "paired_bootstrap_95_ci": ci,
            "fold_hinge_slopes": {str(d["fold"]): d["hinge_slope"] for d in fold_reports},
            "mean_fold_hinge_slope": float(slopes.mean()), "sd_fold_hinge_slope": float(slopes.std(ddof=0)),
            "bootstrap_seed": seed, "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "fold_fit_diagnostics": fold_reports,
        }
        if sum(regional[region]["fold_heldout_support"].values()) != int(support.sum()):
            raise AssertionError(f"held-out fold support does not sum to complete cases in {region}")
    pooled_y = np.concatenate([region_data[r][0] for r in REGIONS])
    pooled_base = np.concatenate([region_data[r][1] for r in REGIONS])
    pooled_hinge = np.concatenate([region_data[r][2] for r in REGIONS])
    pooled_metrics = _delta_r2(pooled_y, pooled_base, pooled_hinge)
    pooled_seed = seed_for("named-road-entropy-heat-hinge:pooled-stratified")
    pooled_ci = _ci(bootstrap_pooled_delta(region_data, pooled_seed, BOOTSTRAP_REPLICATES))
    positive_regions = sum(regional[r]["heldout_delta_r2"] > 0. for r in REGIONS)
    gate = bool(pooled_metrics["delta_r2"] >= MIN_DELTA_R2 and pooled_ci[0] > 0. and positive_regions >= 3)

    output = pa.table({
        "GEOID": pa.array([g for g, _ in pairs], type=pa.string()),
        "region": pa.array([r for _, r in pairs], type=pa.string()),
        "normalized_named_road_class_entropy": pa.array([float(v) if ok else None for v, ok in zip(entropy, entropy_mask)], type=pa.float64()),
        "entropy_defined": pa.array(entropy_mask, type=pa.bool_()),
        "epht_heat_days_summer": pa.array([float(v) if ok else None for v, ok in zip(heat, heat_mask)], type=pa.float64()),
        "heat_defined": pa.array(heat_mask, type=pa.bool_()),
        "log_total_named_road_km": pa.array([float(v) if np.isfinite(v) else None for v in log_road], type=pa.float64()),
        "pct_urban": pa.array([float(v) if np.isfinite(v) else None for v in values["pct_urban"]], type=pa.float64()),
        "Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(values["mx4"], values["mx4_defined"])], type=pa.float64()),
        "S1_lagged_Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(values["s1"], values["s1_defined"])], type=pa.float64()),
        "fixed_entropy_fold": pa.array(folds, type=pa.int8()),
        "hinge_joint_defined": pa.array(complete, type=pa.bool_()),
        "training_fold_q75_heat_knot": pa.array([float(v) if ok else None for v, ok in zip(knot_oof, complete)], type=pa.float64()),
        "training_fold_q75_heat_hinge": pa.array([float(max(h - k, 0.)) if ok else None for h, k, ok in zip(heat, knot_oof, complete)], type=pa.float64()),
        "baseline_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(base_oof, complete)], type=pa.float64()),
        "heat_hinge_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(hinge_oof, complete)], type=pa.float64()),
    })
    for name in ("baseline_oof_prediction", "heat_hinge_oof_prediction", "training_fold_q75_heat_knot", "training_fold_q75_heat_hinge"):
        arr = np.asarray(output[name].to_numpy(zero_copy_only=False), dtype=np.float64)
        if not np.array_equal(np.isfinite(arr), complete):
            raise AssertionError(f"output mask for {name} differs from the exact complete-case mask")
    payload = parquet_bytes(output)
    if payload != parquet_bytes(output):
        raise ValueError("hinge diagnostic artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    report = {
        "schema_version": 1,
        "screen": "Training-fold q75 upper-tail hinge of provided summer heat for normalized named-road class-share entropy; unregistered association screen",
        "measure_before": {"continuous_heat_pooled_heldout_delta_r2": MEASURE_BEFORE_DELTA,
                           "continuous_heat_bootstrap_95_ci": MEASURE_BEFORE_CI,
                           "continuous_heat_positive_regions": MEASURE_BEFORE_POSITIVE_REGIONS,
                           "q75_heat_hinge_measured": False,
                           "continuous_heat_report_sha256": sha256_file(CONTINUOUS_REPORT)},
        "method": {"outcome": "Provided normalized named-road class-share entropy; no label or target outcome is read.",
                   "baseline": "Region-specific OLS with intercept, log total named-road km, provided pct_urban, frozen Mx4 F, and frozen S1 lag.",
                   "hinge": "Add max(provided summer heat - training-fold q75 heat, 0) with a region-specific fitted slope; there is no added common linear heat term.",
                   "knot": "For each region and held-out fold, calculate q75 from complete-case training tracts in the other four fixed GEOID folds only; apply that knot to training and held-out rows.",
                   "folds": "Reuse exact five GEOID fold assignments stored with the named-road entropy artifact; no reassignment.",
                   "support": "Paired baseline and hinge models use identical official tracts with finite entropy, provided heat, baseline predictors, and frozen covariates; no imputation.",
                   "bootstrap": "2,000 paired tract bootstrap replicates within each region; pooled bootstrap stratifies by region and preserves each region's observed complete-case size. Bootstrap held-out entropy and paired OOF predictions.",
                   "metric": "Held-out delta R2=(SSE_baseline-SSE_hinge)/held-out entropy total sum of squares, pooled around pooled mean and separately within region around that region's mean.",
                   "unregistered": True,
                   "interpretation_limit": "Provided-feature association only; not target accuracy, causality, or promotion evidence."},
        "scope": {"official_ids": len(pairs), "official_geoid_region_sha256": provenance["official_geoid_region_sha256"],
                  "entropy_defined_ids": int(entropy_mask.sum()), "heat_covered_ids": int(heat_mask.sum()),
                  "paired_complete_case_ids": int(complete.sum()), "complete_case_null_ids": int((~complete).sum()),
                  "official_fold_counts": {str(f): int(np.count_nonzero(folds == f)) for f in range(N_FOLDS)},
                  "complete_case_fold_counts": {str(f): int(np.count_nonzero(complete & (folds == f))) for f in range(N_FOLDS)},
                  "fold_support_sums_exact": True,
                  "entropy_null_mask_exact": bool(np.array_equal(np.isfinite(entropy), entropy_mask)),
                  "heat_null_mask_exact": bool(np.array_equal(np.isfinite(heat), heat_mask)),
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_references_read": False, "external_data_used": [],
                  "leaderboard_feedback_used": False, "estimator_or_registry_changed": False, "uploads": 0},
        "measure_after": {"pooled_heldout_baseline_r2": pooled_metrics["baseline_r2"],
                          "pooled_heldout_hinge_r2": pooled_metrics["heat_augmented_r2"],
                          "pooled_heldout_delta_r2": pooled_metrics["delta_r2"],
                          "pooled_bootstrap_95_ci": pooled_ci, "pooled_bootstrap_seed": pooled_seed,
                          "bootstrap_replicates": BOOTSTRAP_REPLICATES,
                          "regions_with_positive_heldout_delta_r2": positive_regions,
                          "regions": regional},
        "thresholds": {"pooled_delta_r2_minimum_inclusive": MIN_DELTA_R2,
                       "pooled_bootstrap_ci_lower_strictly_above_zero": True,
                       "minimum_regions_with_positive_heldout_delta_r2": 3,
                       "q75_training_quantile": Q75, "bootstrap_replicates": BOOTSTRAP_REPLICATES},
        "acceptance": {"pooled_delta_r2_at_least_0_01": bool(pooled_metrics["delta_r2"] >= MIN_DELTA_R2),
                       "pooled_bootstrap_ci_lower_above_zero": bool(pooled_ci[0] > 0.),
                       "positive_heldout_delta_r2_in_at_least_3_of_4_regions": bool(positive_regions >= 3),
                       "heat_hinge_screen_pass": gate},
        "provenance": {**provenance,
                       "continuous_heat_report": {"path": str(CONTINUOUS_REPORT), "sha256": sha256_file(CONTINUOUS_REPORT)},
                       "output_artifact": {"path": str(artifact_path), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": output.num_rows, "columns": output.column_names,
                                           "deterministic_serialization": True},
                       "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "interpretation": "The prespecified upper-tail hinge passed the held-out effect, paired-bootstrap and regional consistency gates; this is only provided-feature association evidence." if gate else
                         "The prespecified upper-tail hinge did not pass all held-out effect, paired-bootstrap and regional consistency gates; retain descriptive-only and make no target-accuracy or causal claim.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/named-road-entropy-heat-hinge.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "named-road-entropy-heat-hinge/paired-crossfit-predictions.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"scope": report["scope"], "measure_after": report["measure_after"],
                      "acceptance": report["acceptance"], "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
