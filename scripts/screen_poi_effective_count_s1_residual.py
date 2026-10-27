#!/usr/bin/env python3
"""Cross-fit test of POI effective count against frozen S1 disagreement residuals."""
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
BOOTSTRAP_SEED = 20261029
MIN_POOLED_DELTA_R2 = 0.01
MIN_CI_LOWER = 0.0
MIN_POSITIVE_REGIONS = 3


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mask_sha256(mask: np.ndarray) -> str:
    value = np.asarray(mask, dtype=np.uint8)
    return hashlib.sha256(np.packbits(value, bitorder="little").tobytes()).hexdigest()


def within_region_train_standardize(values: np.ndarray, regions: np.ndarray,
                                    train: np.ndarray, test: np.ndarray
                                    ) -> tuple[np.ndarray, dict[str, dict[str, float]]]:
    """Standardize one feature within region, learning each region's moments on train only."""
    x = np.asarray(values, dtype=np.float64)
    region = np.asarray(regions, dtype=object)
    tr = np.asarray(train, dtype=bool)
    te = np.asarray(test, dtype=bool)
    if x.ndim != 1 or region.shape != x.shape or tr.shape != x.shape or te.shape != x.shape:
        raise ValueError("within-region scaling inputs must be aligned vectors")
    if np.any(tr & te) or not tr.any() or not te.any() or np.any((tr | te) & ~np.isfinite(x)):
        raise ValueError("train/test masks must be nonempty, disjoint and finite on selected rows")
    scaled = np.full(x.shape, np.nan, dtype=np.float64)
    stats: dict[str, dict[str, float]] = {}
    for name in sorted(set(region[tr | te].tolist())):
        train_region = tr & (region == name)
        test_region = te & (region == name)
        if not test_region.any():
            continue
        if not train_region.any():
            raise ValueError(f"region {name} has held-out rows but no training rows")
        mean = float(x[train_region].mean())
        sd = float(x[train_region].std(ddof=0))
        scale = sd if sd > 0. else 1.
        scaled[train_region] = (x[train_region] - mean) / scale
        scaled[test_region] = (x[test_region] - mean) / scale
        stats[name] = {"training_mean": mean, "training_sd": sd, "applied_scale": scale,
                       "training_n": int(train_region.sum()), "heldout_n": int(test_region.sum())}
    if np.any((tr | te) & ~np.isfinite(scaled)):
        raise AssertionError("within-region transform did not cover selected rows")
    return scaled, stats


def _design(features: np.ndarray, regions: np.ndarray, region_code: dict[str, int]) -> np.ndarray:
    codes = np.asarray([region_code[str(r)] for r in regions], dtype=np.int8)
    dummies = np.column_stack([(codes == code).astype(np.float64)
                               for code in range(1, len(region_code))])
    return np.column_stack((np.ones(len(codes)), dummies, features))


def fit_predict_fold(target: np.ndarray, baseline_features: np.ndarray, poi_feature: np.ndarray,
                     regions: np.ndarray, train: np.ndarray, test: np.ndarray
                     ) -> dict[str, Any]:
    """Fit nested OLS models using training rows only and return paired predictions."""
    y = np.asarray(target, dtype=np.float64)
    base_x = np.asarray(baseline_features, dtype=np.float64)
    poi_x = np.asarray(poi_feature, dtype=np.float64)
    region = np.asarray(regions, dtype=object)
    tr, te = np.asarray(train, dtype=bool), np.asarray(test, dtype=bool)
    n = y.size
    if (base_x.ndim != 2 or base_x.shape[0] != n or poi_x.shape != (n,) or region.shape != (n,)
            or tr.shape != (n,) or te.shape != (n,) or np.any(tr & te) or not tr.any() or not te.any()):
        raise ValueError("fold model inputs must be aligned with nonempty disjoint train/test masks")
    if np.any((tr | te) & (~np.isfinite(y) | ~np.isfinite(poi_x) | ~np.isfinite(base_x).all(axis=1))):
        raise ValueError("selected model rows must be finite")
    unique_regions = sorted(set(region.tolist()))
    if unique_regions != sorted(REGIONS):
        raise ValueError("fold model requires the four official regions")
    # Scale baseline continuous features globally on training rows only. This does not change
    # the unregularized OLS predictions and keeps the design numerically well-conditioned.
    base_train_mean = base_x[tr].mean(axis=0)
    base_train_sd = base_x[tr].std(axis=0, ddof=0)
    base_scale = np.where(base_train_sd > 0., base_train_sd, 1.)
    base_z = (base_x - base_train_mean) / base_scale
    poi_z, poi_stats = within_region_train_standardize(poi_x, region, tr, te)
    region_code = {name: idx for idx, name in enumerate(REGIONS)}
    design_base = _design(base_z, region, region_code)
    design_aug = np.column_stack((design_base, poi_z))
    beta_base, _, rank_base, _ = np.linalg.lstsq(design_base[tr], y[tr], rcond=None)
    beta_aug, _, rank_aug, _ = np.linalg.lstsq(design_aug[tr], y[tr], rcond=None)
    if rank_base != design_base.shape[1] or rank_aug != design_aug.shape[1]:
        raise ValueError("training-fold design is rank deficient")
    base_prediction = np.full(n, np.nan)
    augmented_prediction = np.full(n, np.nan)
    base_prediction[te] = design_base[te] @ beta_base
    augmented_prediction[te] = design_aug[te] @ beta_aug
    return {"baseline_prediction": base_prediction, "augmented_prediction": augmented_prediction,
            "poi_training_scaler": poi_stats}


def delta_r2(y: np.ndarray, baseline_prediction: np.ndarray,
             augmented_prediction: np.ndarray) -> dict[str, float]:
    target = np.asarray(y, dtype=np.float64)
    base = np.asarray(baseline_prediction, dtype=np.float64)
    aug = np.asarray(augmented_prediction, dtype=np.float64)
    if target.ndim != 1 or base.shape != target.shape or aug.shape != target.shape or not target.size:
        raise ValueError("paired delta-R2 inputs must be aligned nonempty vectors")
    if not np.isfinite(target).all() or not np.isfinite(base).all() or not np.isfinite(aug).all():
        raise ValueError("paired delta-R2 inputs must be finite")
    sst = float(np.sum((target - target.mean()) ** 2))
    if sst <= 0.:
        raise ValueError("held-out target must have positive total sum of squares")
    base_sse = float(np.sum((target - base) ** 2))
    aug_sse = float(np.sum((target - aug) ** 2))
    return {"baseline_r2": 1. - base_sse / sst, "augmented_r2": 1. - aug_sse / sst,
            "delta_r2": (base_sse - aug_sse) / sst, "baseline_sse": base_sse,
            "augmented_sse": aug_sse, "heldout_sst": sst}


def paired_tract_bootstrap(y: np.ndarray, baseline_prediction: np.ndarray,
                           augmented_prediction: np.ndarray, *, replicates: int = BOOTSTRAP_REPLICATES,
                           seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    target = np.asarray(y, dtype=np.float64)
    base = np.asarray(baseline_prediction, dtype=np.float64)
    aug = np.asarray(augmented_prediction, dtype=np.float64)
    if target.ndim != 1 or base.shape != target.shape or aug.shape != target.shape or target.size < 2:
        raise ValueError("bootstrap inputs must be aligned vectors with at least two tracts")
    if replicates < 100 or not (np.isfinite(target).all() and np.isfinite(base).all() and np.isfinite(aug).all()):
        raise ValueError("bootstrap inputs must be finite and use at least 100 replicates")
    rng = np.random.default_rng(seed)
    deltas = np.empty(replicates, dtype=np.float64)
    n = target.size
    for b in range(replicates):
        idx = rng.integers(0, n, size=n)
        yb, pb, qb = target[idx], base[idx], aug[idx]
        denominator = float(np.sum((yb - yb.mean()) ** 2))
        if denominator <= 0.:
            deltas[b] = np.nan
        else:
            deltas[b] = (float(np.sum((yb - pb) ** 2)) - float(np.sum((yb - qb) ** 2))) / denominator
    finite = deltas[np.isfinite(deltas)]
    if finite.size < int(.99 * replicates):
        raise ValueError("too many degenerate bootstrap resamples")
    return {"replicates": int(replicates), "seed": int(seed),
            "resampling": "paired with-replacement tract resampling over the exact pooled OOF support",
            "ci_95_percentile": [float(np.quantile(finite, .025)), float(np.quantile(finite, .975))],
            "degenerate_replicates": int(replicates - finite.size)}


def _read_rows(path: Path, expected_sha: str, columns: list[str], official: list[tuple[str, str]]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
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
        if name.endswith("_defined") or name in ("crossfit_joint_defined", "effective_count_defined"):
            out[name] = np.zeros(len(official), dtype=bool)
        else:
            out[name] = np.full(len(official), np.nan, dtype=np.float64)
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in locations or pair in seen:
            raise ValueError(f"unexpected or duplicate artifact pair: {pair}")
        seen.add(pair)
        i = locations[pair]
        for name in out:
            value = row[name]
            if name.endswith("_defined") or name in ("crossfit_joint_defined", "effective_count_defined"):
                if not isinstance(value, bool):
                    raise ValueError(f"artifact mask {name} must be boolean")
                out[name][i] = value
            elif value is not None:
                out[name][i] = float(value)
    if seen != set(official):
        raise ValueError(f"artifact is not an exact official ID-region partition: {path}")
    return out, {"path": str(path), "sha256": digest, "rows": table.num_rows,
                 "columns_read": columns, "exact_official_pair_alignment": True}


def _evaluate(y: np.ndarray, base: np.ndarray, aug: np.ndarray, mask: np.ndarray,
              regions: np.ndarray, folds: np.ndarray) -> dict[str, Any]:
    selected_y, selected_base, selected_aug = y[mask], base[mask], aug[mask]
    pooled = delta_r2(selected_y, selected_base, selected_aug)
    bootstrap = paired_tract_bootstrap(selected_y, selected_base, selected_aug)
    regional: dict[str, Any] = {}
    for region in REGIONS:
        use = mask & (regions == region)
        metrics = delta_r2(y[use], base[use], aug[use])
        regional[region] = {"n": int(use.sum()), **metrics}
    fold_reports: list[dict[str, Any]] = []
    for fold in range(N_FOLDS):
        use = mask & (folds == fold)
        metrics = delta_r2(y[use], base[use], aug[use])
        fold_reports.append({"fold": fold, "n": int(use.sum()), **metrics})
    return {"pooled": pooled, "bootstrap": bootstrap, "regional": regional, "folds": fold_reports}


def run(workspace: Path, report_path: Path, output_path: Path) -> dict[str, Any]:
    source_report = json.loads(EFFECTIVE_REPORT.read_text(encoding="utf-8"))
    if (source_report["scope"]["official_ids"] != EXPECTED_N
            or source_report["scope"]["effective_count_defined_ids"] != 9327
            or not np.isclose(source_report["measure_after"]["crossfit_residual_variance_ratio"],
                              WITHIN_REGION_RATIO, rtol=0., atol=1e-15)):
        raise ValueError("effective-count MEASURE-BEFORE report differs from the declared source")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N
            or len({g for g, _ in official}) != EXPECTED_N or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official IDs are not an exact 9,379-row/four-region partition")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    geoid = np.asarray([g for g, _ in official], dtype=object)
    regions = np.asarray([r for _, r in official], dtype=object)

    residual_path = workspace / "s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    residual, residual_meta = _read_rows(
        residual_path, RESIDUAL_SHA,
        ["GEOID", "region", "fold", "crossfit_residual", "crossfit_joint_defined"], official)
    effective_path = Path(source_report["provenance"]["output_artifact"]["path"])
    effective, effective_meta = _read_rows(
        effective_path, EFFECTIVE_SHA,
        ["GEOID", "region", "unique_assigned_place_count", "primary_categorized_place_count",
         "effective_primary_category_count", "effective_count_defined", "Mx4_F", "Mx4_F_defined",
         "S1_lagged_Mx4_F", "S1_lagged_Mx4_F_defined"], official)
    expected_folds = np.full(EXPECTED_N, -1, dtype=np.int8)
    for region in REGIONS:
        ix = np.flatnonzero(regions == region)
        ids = geoid[ix].tolist()
        expected_folds[ix] = deterministic_folds(ids, region, N_FOLDS)
    frozen_folds = residual["fold"].astype(np.int8)
    if not np.array_equal(frozen_folds, expected_folds):
        raise ValueError("frozen residual artifact folds differ from the required deterministic five GEOID folds")
    residual_mask = residual["crossfit_joint_defined"]
    if not np.array_equal(np.isfinite(residual["crossfit_residual"]), residual_mask):
        raise ValueError("frozen residual outcome values and mask disagree")
    if not np.array_equal(np.isfinite(effective["effective_primary_category_count"]), effective["effective_count_defined"]):
        raise ValueError("effective-count values and defined mask disagree")
    if not np.array_equal(np.isfinite(effective["Mx4_F"]), effective["Mx4_F_defined"]):
        raise ValueError("Mx4 values and defined mask disagree")
    if not np.array_equal(np.isfinite(effective["S1_lagged_Mx4_F"]), effective["S1_lagged_Mx4_F_defined"]):
        raise ValueError("S1 lag values and defined mask disagree")

    target = residual["crossfit_residual"]
    place_count = effective["unique_assigned_place_count"]
    poi_count = effective["effective_primary_category_count"]
    if np.any(effective["effective_count_defined"] & ((poi_count < 1.) | (place_count < 10.))):
        raise ValueError("effective-count values violate the frozen minimum-place eligibility")
    log_places = np.full(EXPECTED_N, np.nan)
    place_defined = np.isfinite(place_count) & (place_count >= 0.)
    log_places[place_defined] = np.log1p(place_count[place_defined])
    log_poi = np.full(EXPECTED_N, np.nan)
    poi_defined = effective["effective_count_defined"]
    log_poi[poi_defined] = np.log1p(poi_count[poi_defined])
    base_features = np.column_stack((log_places, effective["Mx4_F"], effective["S1_lagged_Mx4_F"]))
    support = (residual_mask & poi_defined & place_defined & effective["Mx4_F_defined"]
               & effective["S1_lagged_Mx4_F_defined"] & np.isfinite(base_features).all(axis=1)
               & np.isfinite(log_poi))
    if not support.any():
        raise ValueError("no paired complete cases for the five-fold residual screen")
    baseline_prediction = np.full(EXPECTED_N, np.nan)
    augmented_prediction = np.full(EXPECTED_N, np.nan)
    scaler_details: dict[str, Any] = {}
    for fold in range(N_FOLDS):
        test = support & (expected_folds == fold)
        train = support & (expected_folds != fold)
        if not test.any() or not train.any():
            raise ValueError(f"fold {fold} has empty train or held-out support")
        fit = fit_predict_fold(target, base_features, log_poi, regions, train, test)
        baseline_prediction[test] = fit["baseline_prediction"][test]
        augmented_prediction[test] = fit["augmented_prediction"][test]
        scaler_details[str(fold)] = fit["poi_training_scaler"]
    if np.any(support & (~np.isfinite(baseline_prediction) | ~np.isfinite(augmented_prediction))) or np.any(~support &
            (np.isfinite(baseline_prediction) | np.isfinite(augmented_prediction))):
        raise AssertionError("OOF predictions do not preserve paired support exactly")
    scores = _evaluate(target, baseline_prediction, augmented_prediction, support, regions, expected_folds)
    pooled_delta = scores["pooled"]["delta_r2"]
    ci_low, ci_high = scores["bootstrap"]["ci_95_percentile"]
    positive_regions = sum(row["delta_r2"] > 0. for row in scores["regional"].values())
    gate = bool(pooled_delta >= MIN_POOLED_DELTA_R2 and ci_low > MIN_CI_LOWER
                and positive_regions >= MIN_POSITIVE_REGIONS)

    output_table = pa.table({
        "GEOID": pa.array(geoid.tolist(), type=pa.string()),
        "region": pa.array(regions.tolist(), type=pa.string()),
        "fold": pa.array(expected_folds, type=pa.int8()),
        "frozen_s1_crossfit_residual": pa.array([float(v) if ok else None for v, ok in zip(target, residual_mask)], type=pa.float64()),
        "s1_residual_defined": pa.array(residual_mask, type=pa.bool_()),
        "effective_primary_category_count": pa.array([float(v) if ok else None for v, ok in zip(poi_count, poi_defined)], type=pa.float64()),
        "log1p_effective_primary_category_count": pa.array([float(v) if ok else None for v, ok in zip(log_poi, poi_defined)], type=pa.float64()),
        "unique_assigned_place_count": pa.array([int(v) if np.isfinite(v) else None for v in place_count], type=pa.int64()),
        "log1p_unique_place_count": pa.array([float(v) if ok else None for v, ok in zip(log_places, place_defined)], type=pa.float64()),
        "Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(effective["Mx4_F"], effective["Mx4_F_defined"])], type=pa.float64()),
        "S1_lagged_Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(effective["S1_lagged_Mx4_F"], effective["S1_lagged_Mx4_F_defined"])], type=pa.float64()),
        "paired_screen_defined": pa.array(support, type=pa.bool_()),
        "baseline_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(baseline_prediction, support)], type=pa.float64()),
        "augmented_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(augmented_prediction, support)], type=pa.float64()),
    })
    payload = parquet_bytes(output_table)
    if payload != parquet_bytes(output_table):
        raise ValueError("paired OOF artifact serialization is nondeterministic")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    artifact_sha = hashlib.sha256(payload).hexdigest()
    source_report_sha = sha256_file(EFFECTIVE_REPORT)
    report = {
        "schema_version": 1,
        "screen": "Five-fold held-out additive association of provided POI effective count with frozen cross-fitted S1 disagreement residual; unregistered",
        "measure_before": {"effective_count_crossfit_residual_variance_ratio": WITHIN_REGION_RATIO,
                           "effective_count_joint_ids": 9327, "effective_count_prior_residual_prediction_delta_r2_measured": False,
                           "effective_count_source_report_sha256": source_report_sha},
        "method": {"outcome": "Frozen cross-fitted orthogonalized S1 disagreement residual from the exact input artifact; no refitting or alteration of its construction.",
                   "baseline": "OLS with region intercept indicators, globally train-standardized log1p unique place count, Mx4 F, and S1 lagged Mx4 F.",
                   "augmentation": "Add log1p effective POI primary-category count, standardized separately within each region using only the training rows of each fold; zero training SD uses scale 1.",
                   "cross_validation": "The exact frozen five deterministic GEOID folds within each official region. For every fold, fit both nested models and all scaling moments on the other four folds, then score their paired held-out predictions.",
                   "metric": "Pooled and regional/fold held-out delta-R2 = augmented held-out R2 minus baseline held-out R2, equivalently (baseline SSE - augmented SSE)/the same held-out target SST. Positive favors the augmentation.",
                   "bootstrap": "Paired with-replacement tract resampling over the pooled OOF complete-case support; percentile 95% interval, fixed seed and replicate count in report.",
                   "support": "One paired mask requiring frozen residual, effective count, unique count, Mx4 F and S1 lag to be defined; no imputation or post-result exclusions.",
                   "model_coefficients_disclosed": False, "training_only_scaling_moments_recorded_by_fold": True,
                   "unregistered": True,
                   "interpretation_limit": "Provided-feature association only, not hidden-target accuracy, causal evidence, or estimator-promotion evidence."},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": official_sha,
                  "frozen_residual_defined_ids": int(residual_mask.sum()), "effective_count_defined_ids": int(poi_defined.sum()),
                  "paired_screen_ids": int(support.sum()), "paired_screen_undefined_ids": int((~support).sum()),
                  "residual_mask_sha256": mask_sha256(residual_mask), "effective_count_mask_sha256": mask_sha256(poi_defined),
                  "paired_mask_sha256": mask_sha256(support), "fold_assignment_sha256": hashlib.sha256(expected_folds.tobytes()).hexdigest(),
                  "labels_read": False, "sample_score_values_read": False, "removed_or_equivalent_references_read": False,
                  "external_data_used": [], "leaderboard_feedback_used": False, "post_result_exclusions": False,
                  "registry_changed": False, "uploads": 0},
        "measure_after": {"pooled": scores["pooled"], "paired_pooled_delta_r2": pooled_delta,
                          "paired_pooled_delta_r2_ci_95": scores["bootstrap"]["ci_95_percentile"],
                          "bootstrap": scores["bootstrap"], "regional": scores["regional"], "folds": scores["folds"],
                          "positive_regions": positive_regions},
        "thresholds": {"pooled_delta_r2_minimum_inclusive": MIN_POOLED_DELTA_R2,
                       "paired_tract_bootstrap_ci_lower_strictly_above": MIN_CI_LOWER,
                       "minimum_regions_with_positive_heldout_delta": MIN_POSITIVE_REGIONS},
        "acceptance": {"pooled_delta_r2_at_least_0_01": bool(pooled_delta >= MIN_POOLED_DELTA_R2),
                       "bootstrap_ci_lower_above_zero": bool(ci_low > MIN_CI_LOWER),
                       "positive_delta_in_at_least_3_of_4_regions": bool(positive_regions >= MIN_POSITIVE_REGIONS),
                       "effective_count_s1_residual_gate_pass": gate},
        "provenance": {"official_geoid_region_sha256": official_sha,
                       "frozen_residual_artifact": residual_meta,
                       "effective_count_artifact": effective_meta,
                       "effective_count_report": {"path": str(EFFECTIVE_REPORT), "sha256": source_report_sha},
                       "output_artifact": {"path": str(output_path), "sha256": artifact_sha,
                                           "rows": output_table.num_rows, "columns": output_table.column_names,
                                           "deterministic_serialization": True},
                       "poi_scaler_training_stats_by_fold": scaler_details,
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The additive effective-count association passed all preregistered gates; retain as a descriptive provided-feature association only, not as evidence of target accuracy or permission to integrate." if gate else
                         "The additive effective-count association failed at least one preregistered gate; retain the feature as descriptive only and do not integrate it.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/poi-effective-count-s1-residual.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    output_path = Path(args.artifact) if args.artifact else workspace / "poi-effective-count-s1-residual/paired-oof-predictions.parquet"
    report = run(workspace, ROOT / args.out, output_path)
    print(json.dumps({"scope": report["scope"], "measure_after": report["measure_after"],
                      "acceptance": report["acceptance"], "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
