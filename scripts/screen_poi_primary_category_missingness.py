#!/usr/bin/env python3
"""Test provided POI primary-category missing share against frozen S1 residuals."""
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
from audit_mx4p_conditional_innovation import deterministic_folds, distribution, parquet_bytes  # noqa: E402
from screen_poi_category_entropy import REGIONS, read_region_places  # noqa: E402
import pyarrow.fs as pafs  # noqa: E402

EXPECTED_N = 9379
RESIDUAL_SHA = "437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
ENTROPY_REPORT = ROOT / "reports/poi-category-entropy.json"
EXPECTED_ENTROPY_ELIGIBLE = 9327
N_FOLDS = 5
MIN_PLACES = 10
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261031
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
    return hashlib.sha256(np.packbits(np.asarray(mask, dtype=np.uint8), bitorder="little").tobytes()).hexdigest()


def primary_missing_share(total_unique: np.ndarray, categorized_unique: np.ndarray,
                          *, min_places: int = MIN_PLACES) -> dict[str, np.ndarray]:
    """Share of assigned unique POI IDs lacking nonblank categories.primary."""
    total = np.asarray(total_unique, dtype=np.int64)
    categorized = np.asarray(categorized_unique, dtype=np.int64)
    if total.ndim != 1 or categorized.shape != total.shape:
        raise ValueError("total and categorized unique-place counts must be aligned vectors")
    if np.any(total < 0) or np.any(categorized < 0) or np.any(categorized > total) or min_places < 1:
        raise ValueError("counts must satisfy 0 <= categorized <= total and minimum places must be positive")
    defined = total >= min_places
    share = np.full(total.shape, np.nan, dtype=np.float64)
    share[defined] = (total[defined] - categorized[defined]) / total[defined]
    if np.any(defined & (~np.isfinite(share) | (share < 0.) | (share > 1.))):
        raise ValueError("defined missing-primary share must be finite and within [0,1]")
    return {"missing_primary_count": total - categorized, "missing_primary_share": share,
            "defined": defined, "total_unique_count": total, "categorized_unique_count": categorized}


def standardize_train_only(values: np.ndarray, train: np.ndarray, test: np.ndarray
                           ) -> tuple[np.ndarray, dict[str, float]]:
    x = np.asarray(values, dtype=np.float64)
    tr, te = np.asarray(train, dtype=bool), np.asarray(test, dtype=bool)
    if x.ndim != 1 or tr.shape != x.shape or te.shape != x.shape or np.any(tr & te):
        raise ValueError("feature and disjoint train/test masks must align")
    if not tr.any() or not te.any() or np.any((tr | te) & ~np.isfinite(x)):
        raise ValueError("selected feature values must be finite and train/test masks nonempty")
    mean = float(x[tr].mean())
    sd = float(x[tr].std(ddof=0))
    scale = sd if sd > 0. else 1.
    z = np.full(x.shape, np.nan)
    z[tr | te] = (x[tr | te] - mean) / scale
    return z, {"training_mean": mean, "training_sd": sd, "applied_scale": scale,
               "training_n": int(tr.sum()), "heldout_n": int(te.sum())}


def _design(features: np.ndarray, regions: np.ndarray, region_code: dict[str, int]) -> np.ndarray:
    codes = np.asarray([region_code[str(r)] for r in regions], dtype=np.int8)
    dummies = np.column_stack([(codes == code).astype(np.float64)
                               for code in range(1, len(region_code))])
    return np.column_stack((np.ones(len(codes)), dummies, features))


def fit_fold(target: np.ndarray, baseline_features: np.ndarray, missing_share: np.ndarray,
             regions: np.ndarray, train: np.ndarray, test: np.ndarray) -> dict[str, Any]:
    """Fit nested OLS models; standardize all continuous features on train rows only."""
    y = np.asarray(target, dtype=np.float64)
    base = np.asarray(baseline_features, dtype=np.float64)
    share = np.asarray(missing_share, dtype=np.float64)
    reg = np.asarray(regions, dtype=object)
    tr, te = np.asarray(train, dtype=bool), np.asarray(test, dtype=bool)
    n = y.size
    if (base.ndim != 2 or base.shape[0] != n or share.shape != (n,) or reg.shape != (n,)
            or tr.shape != (n,) or te.shape != (n,) or np.any(tr & te) or not tr.any() or not te.any()):
        raise ValueError("fold inputs must align with nonempty disjoint train/test masks")
    if np.any((tr | te) & (~np.isfinite(y) | ~np.isfinite(share) | ~np.isfinite(base).all(axis=1))):
        raise ValueError("selected rows must have finite target and features")
    if sorted(set(reg.tolist())) != sorted(REGIONS):
        raise ValueError("model requires all four official regions")
    base_mean, base_sd = base[tr].mean(axis=0), base[tr].std(axis=0, ddof=0)
    base_scale = np.where(base_sd > 0., base_sd, 1.)
    base_z = (base - base_mean) / base_scale
    share_z, share_stats = standardize_train_only(share, tr, te)
    code = {name: i for i, name in enumerate(REGIONS)}
    design_base = _design(base_z, reg, code)
    design_full = np.column_stack((design_base, share_z))
    beta_base, _, rank_base, _ = np.linalg.lstsq(design_base[tr], y[tr], rcond=None)
    beta_full, _, rank_full, _ = np.linalg.lstsq(design_full[tr], y[tr], rcond=None)
    if rank_base != design_base.shape[1] or rank_full != design_full.shape[1]:
        raise ValueError("training-fold design is rank deficient")
    pred_base, pred_full = np.full(n, np.nan), np.full(n, np.nan)
    pred_base[te] = design_base[te] @ beta_base
    pred_full[te] = design_full[te] @ beta_full
    return {"baseline_prediction": pred_base, "augmented_prediction": pred_full,
            "missing_share_training_scaler": share_stats}


def delta_r2(target: np.ndarray, baseline_prediction: np.ndarray,
             augmented_prediction: np.ndarray) -> dict[str, float]:
    y = np.asarray(target, dtype=np.float64)
    base = np.asarray(baseline_prediction, dtype=np.float64)
    aug = np.asarray(augmented_prediction, dtype=np.float64)
    if y.ndim != 1 or base.shape != y.shape or aug.shape != y.shape or not y.size:
        raise ValueError("paired delta-R2 inputs must be aligned nonempty vectors")
    if not (np.isfinite(y).all() and np.isfinite(base).all() and np.isfinite(aug).all()):
        raise ValueError("paired delta-R2 inputs must be finite")
    sst = float(np.sum((y - y.mean()) ** 2))
    if sst <= 0.:
        raise ValueError("held-out target must have positive total sum of squares")
    base_sse, aug_sse = float(np.sum((y - base) ** 2)), float(np.sum((y - aug) ** 2))
    return {"baseline_r2": 1. - base_sse / sst, "augmented_r2": 1. - aug_sse / sst,
            "delta_r2": (base_sse - aug_sse) / sst, "baseline_sse": base_sse,
            "augmented_sse": aug_sse, "heldout_sst": sst}


def paired_tract_bootstrap(target: np.ndarray, baseline_prediction: np.ndarray,
                           augmented_prediction: np.ndarray, *, replicates: int = BOOTSTRAP_REPLICATES,
                           seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    y = np.asarray(target, dtype=np.float64)
    base = np.asarray(baseline_prediction, dtype=np.float64)
    aug = np.asarray(augmented_prediction, dtype=np.float64)
    if y.ndim != 1 or base.shape != y.shape or aug.shape != y.shape or y.size < 2:
        raise ValueError("bootstrap vectors must align and contain at least two tracts")
    if replicates < 100 or not (np.isfinite(y).all() and np.isfinite(base).all() and np.isfinite(aug).all()):
        raise ValueError("bootstrap data must be finite and use at least 100 replicates")
    rng = np.random.default_rng(seed)
    deltas = np.empty(replicates, dtype=np.float64)
    for b in range(replicates):
        ix = rng.integers(0, y.size, size=y.size)
        yb, bb, ab = y[ix], base[ix], aug[ix]
        sst = float(np.sum((yb - yb.mean()) ** 2))
        deltas[b] = ((float(np.sum((yb - bb) ** 2)) - float(np.sum((yb - ab) ** 2))) / sst
                     if sst > 0. else np.nan)
    finite = deltas[np.isfinite(deltas)]
    if finite.size < int(.99 * replicates):
        raise ValueError("too many degenerate tract-bootstrap replicates")
    return {"replicates": int(replicates), "seed": int(seed),
            "resampling": "paired with-replacement tract resampling over pooled OOF complete-case support",
            "ci_95_percentile": [float(np.quantile(finite, .025)), float(np.quantile(finite, .975))],
            "degenerate_replicates": int(replicates - finite.size)}


def read_residual(path: Path, official: list[tuple[str, str]]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    digest = sha256_file(path)
    if digest != RESIDUAL_SHA:
        raise ValueError("frozen residual artifact hash mismatch")
    columns = ["GEOID", "region", "fold", "crossfit_residual", "crossfit_joint_defined",
               "Mx4_F", "Mx4_F_defined", "S1_lagged_Mx4_F", "S1_lagged_Mx4_F_defined"]
    table = pq.read_table(path, columns=columns)
    if table.num_rows != len(official):
        raise ValueError("residual artifact row count differs from official IDs")
    loc = {pair: i for i, pair in enumerate(official)}
    n = len(official)
    out = {"fold": np.full(n, -1, dtype=np.int8), "crossfit_residual": np.full(n, np.nan),
           "crossfit_joint_defined": np.zeros(n, dtype=bool), "Mx4_F": np.full(n, np.nan),
           "Mx4_F_defined": np.zeros(n, dtype=bool), "S1_lagged_Mx4_F": np.full(n, np.nan),
           "S1_lagged_Mx4_F_defined": np.zeros(n, dtype=bool)}
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in loc or pair in seen:
            raise ValueError(f"unexpected or duplicate residual ID-region pair: {pair}")
        seen.add(pair); i = loc[pair]
        out["fold"][i] = int(row["fold"])
        for name in out:
            if name == "fold":
                continue
            value = row[name]
            if name.endswith("_defined") or name == "crossfit_joint_defined":
                if not isinstance(value, bool):
                    raise ValueError(f"residual mask {name} must be boolean")
                out[name][i] = value
            elif value is not None:
                out[name][i] = float(value)
    if seen != set(official):
        raise ValueError("residual artifact is not an exact official ID-region partition")
    for value, mask_name in (("crossfit_residual", "crossfit_joint_defined"),
                              ("Mx4_F", "Mx4_F_defined"),
                              ("S1_lagged_Mx4_F", "S1_lagged_Mx4_F_defined")):
        if not np.array_equal(np.isfinite(out[value]), out[mask_name]):
            raise ValueError(f"frozen residual artifact null mask mismatch: {value}")
    return out, {"path": str(path), "sha256": digest, "rows": table.num_rows,
                 "columns_read": columns, "exact_official_pair_alignment": True}


def run(workspace: Path, report_path: Path, output_path: Path) -> dict[str, Any]:
    entropy_report = json.loads(ENTROPY_REPORT.read_text(encoding="utf-8"))
    if entropy_report["acceptance"]["eligible_tracts"] != EXPECTED_ENTROPY_ELIGIBLE:
        raise ValueError("category entropy MEASURE-BEFORE eligible support changed")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N
            or len({g for g, _ in official}) != EXPECTED_N or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official IDs are not an exact 9,379-row/four-region partition")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    geoids = np.asarray([g for g, _ in official], dtype=object)
    regions = np.asarray([r for _, r in official], dtype=object)
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    all_places = np.zeros(EXPECTED_N, dtype=np.int64)
    categorized = np.zeros(EXPECTED_N, dtype=np.int64)
    source_regions: dict[str, Any] = {}
    for region in REGIONS:
        idx = np.flatnonzero(regions == region)
        counters, counts, metadata = read_region_places(fs, region, geoids[idx].tolist())
        if len(counters) != len(idx) or np.asarray(counts).shape != (len(idx),):
            raise ValueError(f"provided POI assignment output misaligns for {region}")
        all_places[idx] = counts
        categorized[idx] = np.asarray([sum(counter.values()) for counter in counters], dtype=np.int64)
        source_regions[region] = {key: value for key, value in metadata.items()
                                  if key != "observed_primary_category_support"}
        source_regions[region]["observed_primary_category_support_count"] = len(
            metadata["observed_primary_category_support"])
    feature = primary_missing_share(all_places, categorized)
    share, feature_mask = feature["missing_primary_share"], feature["defined"]
    residual_path = workspace / "s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    residual, residual_meta = read_residual(residual_path, official)
    folds = np.full(EXPECTED_N, -1, dtype=np.int8)
    for region in REGIONS:
        ix = np.flatnonzero(regions == region)
        folds[ix] = deterministic_folds(geoids[ix].tolist(), region, N_FOLDS)
    if not np.array_equal(folds, residual["fold"]):
        raise ValueError("frozen residual folds differ from exact five deterministic GEOID folds")
    residual_mask = residual["crossfit_joint_defined"]
    support = (residual_mask & feature_mask & residual["Mx4_F_defined"]
               & residual["S1_lagged_Mx4_F_defined"])
    if not support.any():
        raise ValueError("no paired support for primary-category missingness screen")
    log_places = np.log1p(all_places.astype(np.float64))
    baseline_features = np.column_stack((log_places, residual["Mx4_F"], residual["S1_lagged_Mx4_F"]))
    target = residual["crossfit_residual"]
    baseline_oof, augmented_oof = np.full(EXPECTED_N, np.nan), np.full(EXPECTED_N, np.nan)
    scaler_by_fold: dict[str, Any] = {}
    for fold in range(N_FOLDS):
        test = support & (folds == fold)
        train = support & (folds != fold)
        if not test.any() or not train.any():
            raise ValueError(f"fold {fold} has empty paired train or held-out support")
        fitted = fit_fold(target, baseline_features, share, regions, train, test)
        baseline_oof[test] = fitted["baseline_prediction"][test]
        augmented_oof[test] = fitted["augmented_prediction"][test]
        scaler_by_fold[str(fold)] = fitted["missing_share_training_scaler"]
    if (np.any(support & (~np.isfinite(baseline_oof) | ~np.isfinite(augmented_oof)))
            or np.any(~support & (np.isfinite(baseline_oof) | np.isfinite(augmented_oof)))):
        raise AssertionError("paired OOF prediction masks do not align with support")
    pooled = delta_r2(target[support], baseline_oof[support], augmented_oof[support])
    bootstrap = paired_tract_bootstrap(target[support], baseline_oof[support], augmented_oof[support])
    regional: dict[str, Any] = {}
    for region in REGIONS:
        use = support & (regions == region)
        regional[region] = {"official_ids": int(np.sum(regions == region)),
                            "missing_share_defined_ids": int(np.sum(feature_mask & (regions == region))),
                            "heldout_n": int(use.sum()),
                            "missing_share_distribution": distribution(share[feature_mask & (regions == region)]),
                            **delta_r2(target[use], baseline_oof[use], augmented_oof[use])}
    fold_scores = []
    for fold in range(N_FOLDS):
        use = support & (folds == fold)
        fold_scores.append({"fold": fold, "heldout_n": int(use.sum()),
                            "missing_share_defined_n": int(np.sum(feature_mask & (folds == fold))),
                            **delta_r2(target[use], baseline_oof[use], augmented_oof[use])})
    positive_regions = sum(row["delta_r2"] > 0. for row in regional.values())
    pooled_delta = pooled["delta_r2"]
    ci_low, ci_high = bootstrap["ci_95_percentile"]
    gate = bool(pooled_delta >= MIN_POOLED_DELTA_R2 and ci_low > MIN_CI_LOWER
                and positive_regions >= MIN_POSITIVE_REGIONS)

    output = pa.table({
        "GEOID": pa.array(geoids.tolist(), type=pa.string()),
        "region": pa.array(regions.tolist(), type=pa.string()),
        "fold": pa.array(folds, type=pa.int8()),
        "unique_assigned_place_count": pa.array(all_places, type=pa.int64()),
        "unique_places_with_primary_category": pa.array(categorized, type=pa.int64()),
        "unique_places_missing_primary_category": pa.array(feature["missing_primary_count"], type=pa.int64()),
        "primary_category_missing_share": pa.array([float(v) if ok else None for v, ok in zip(share, feature_mask)], type=pa.float64()),
        "primary_category_missing_share_defined": pa.array(feature_mask, type=pa.bool_()),
        "frozen_s1_crossfit_residual": pa.array([float(v) if ok else None for v, ok in zip(target, residual_mask)], type=pa.float64()),
        "s1_residual_defined": pa.array(residual_mask, type=pa.bool_()),
        "Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(residual["Mx4_F"], residual["Mx4_F_defined"])], type=pa.float64()),
        "S1_lagged_Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(residual["S1_lagged_Mx4_F"], residual["S1_lagged_Mx4_F_defined"])], type=pa.float64()),
        "paired_screen_defined": pa.array(support, type=pa.bool_()),
        "baseline_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(baseline_oof, support)], type=pa.float64()),
        "augmented_oof_prediction": pa.array([float(v) if ok else None for v, ok in zip(augmented_oof, support)], type=pa.float64()),
    })
    payload = parquet_bytes(output)
    if payload != parquet_bytes(output):
        raise ValueError("missingness OOF artifact serialization is nondeterministic")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    report = {
        "schema_version": 1,
        "screen": "Five-fold held-out association of provided POI primary-category missing share with frozen S1 disagreement residual; unregistered",
        "measure_before": {"per_tract_missing_share_effect_measured": False,
                           "category_entropy_eligible_ids": EXPECTED_ENTROPY_ELIGIBLE,
                           "category_entropy_report_sha256": sha256_file(ENTROPY_REPORT)},
        "method": {"feature": "For each official tract, count unique nonnull provided POI IDs assigned by provided bbox center to official tract geometry; among these, count IDs with nonblank categories.primary. Missing share=(total unique assigned IDs - IDs with primary)/total. Defined only when total unique assigned IDs >=10. Only id, categories.primary and bbox are read from POI rows.",
                   "outcome": "Frozen cross-fitted orthogonalized S1 disagreement residual; source artifact and five GEOID folds are verified and not rebuilt.",
                   "baseline": "Region-intercept OLS with log1p unique assigned POI count, Mx4 F and S1 lag; continuous predictors are standardized on training rows only.",
                   "augmentation": "Add one globally training-standardized primary-category missing-share term; baseline model terms are unchanged.",
                   "cross_validation": "Exact frozen five deterministic GEOID folds within each official region; all fitting and feature scaling use four training folds only.",
                   "metric": "Held-out delta-R2 = augmented OOF R2 minus baseline OOF R2, equivalently (baseline SSE - augmented SSE)/shared target SST; positive favors missing share.",
                   "bootstrap": "Paired with-replacement tract bootstrap over exact pooled OOF support; percentile 95% interval with fixed seed and replicate count.",
                   "support": "Intersection of frozen residual, minimum-10-unique-place missing-share feature, Mx4 F and S1-lag masks; no imputation or post-result thresholds.",
                   "model_coefficients_disclosed": False, "training_scaler_moments_recorded_by_fold": True,
                   "unregistered": True,
                   "interpretation_limit": "Provided-feature association only; not target accuracy, causal evidence, or estimator-promotion evidence."},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": official_sha,
                  "minimum_unique_places_inclusive": MIN_PLACES,
                  "missing_share_defined_ids": int(feature_mask.sum()), "missing_share_undefined_ids": int((~feature_mask).sum()),
                  "frozen_residual_defined_ids": int(residual_mask.sum()), "paired_screen_ids": int(support.sum()),
                  "paired_screen_undefined_ids": int((~support).sum()),
                  "missing_share_mask_sha256": mask_sha256(feature_mask),
                  "residual_mask_sha256": mask_sha256(residual_mask), "paired_mask_sha256": mask_sha256(support),
                  "fold_assignment_sha256": hashlib.sha256(folds.tobytes()).hexdigest(),
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_references_read": False, "categories_alternate_read": False,
                  "source_or_confidence_fields_read": False, "external_data_used": [],
                  "leaderboard_feedback_used": False, "post_result_thresholds": False,
                  "registry_changed": False, "uploads": 0},
        "measure_after": {"pooled": pooled, "paired_pooled_delta_r2": pooled_delta,
                          "paired_pooled_delta_r2_ci_95": bootstrap["ci_95_percentile"],
                          "bootstrap": bootstrap, "regional": regional, "folds": fold_scores,
                          "positive_regions": positive_regions},
        "thresholds": {"pooled_delta_r2_minimum_inclusive": MIN_POOLED_DELTA_R2,
                       "bootstrap_ci_lower_strictly_above": MIN_CI_LOWER,
                       "minimum_positive_regions": MIN_POSITIVE_REGIONS},
        "acceptance": {"pooled_delta_r2_at_least_0_01": bool(pooled_delta >= MIN_POOLED_DELTA_R2),
                       "bootstrap_ci_lower_above_zero": bool(ci_low > MIN_CI_LOWER),
                       "positive_delta_in_at_least_3_of_4_regions": bool(positive_regions >= MIN_POSITIVE_REGIONS),
                       "missingness_screen_gate_pass": gate},
        "provenance": {"official_geoid_region_sha256": official_sha,
                       "frozen_residual_artifact": residual_meta,
                       "provided_overture_assignment_by_region": source_regions,
                       "category_entropy_report": {"path": str(ENTROPY_REPORT), "sha256": sha256_file(ENTROPY_REPORT)},
                       "output_artifact": {"path": str(output_path), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": output.num_rows, "columns": output.column_names,
                                           "deterministic_serialization": True},
                       "missing_share_training_scalers_by_fold": scaler_by_fold,
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The primary-category missing-share association passed all preregistered gates; retain it only as a provided-feature diagnostic, with no causal, target-accuracy or integration claim." if gate else
                         "The primary-category missing-share association failed one or more preregistered gates; keep it descriptive only and do not integrate it.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/poi-primary-category-missingness.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    output = Path(args.artifact) if args.artifact else workspace / "poi-primary-category-missingness/paired-oof-predictions.parquet"
    report = run(workspace, ROOT / args.out, output)
    print(json.dumps({"scope": report["scope"], "measure_after": report["measure_after"],
                      "acceptance": report["acceptance"], "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
