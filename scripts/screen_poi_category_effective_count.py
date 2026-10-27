#!/usr/bin/env python3
"""Cross-fit effective POI primary-category count using only provided Overture fields."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import official_scored_pairs  # noqa: E402
from audit_mx4p_conditional_innovation import (  # noqa: E402
    deterministic_folds, distribution, parquet_bytes, pearson, read_emission,
)
from screen_poi_category_entropy import (  # noqa: E402
    BUCKET, REGIONS, read_region_places, sha256_file,
)

EXPECTED_N = 9379
MIN_PLACES = 10
MIN_COVERAGE = .9859
MIN_VARIANCE_RATIO = .10
MAX_ABS_RESIDUAL_CORRELATION = .80
N_FOLDS = 5
CATEGORY_REPORT = ROOT / "reports/poi-category-audit.json"
PRIOR_REPORT = ROOT / "reports/poi-category-entropy.json"
MX4_SHA = "8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af"
S1_SHA = "632829cf29281aa16487707cb555f52b4579cb0edfc0dd3faae360fa8f1dda03"


def effective_count_by_tract(category_counts: list[Counter[str]], unique_place_counts: np.ndarray,
                             *, min_places: int = MIN_PLACES) -> dict[str, np.ndarray]:
    """Compute exp(H) from primary-category proportions; retain nulls, never clip."""
    places = np.asarray(unique_place_counts, dtype=np.int64)
    if places.ndim != 1 or len(category_counts) != places.size:
        raise ValueError("category counters and unique-place counts must align")
    if np.any(places < 0) or min_places < 1:
        raise ValueError("place counts must be nonnegative and minimum must be positive")
    categorized = np.zeros(places.size, dtype=np.int64)
    effective = np.full(places.size, np.nan, dtype=np.float64)
    defined = np.zeros(places.size, dtype=bool)
    for i, counts in enumerate(category_counts):
        if any(not isinstance(value, (int, np.integer)) or value < 0 for value in counts.values()):
            raise ValueError("primary category counts must be nonnegative integers")
        categorized[i] = sum(int(value) for value in counts.values())
        if places[i] >= min_places and categorized[i] > 0:
            values = np.asarray([count for count in counts.values() if count > 0], dtype=np.float64)
            probabilities = values / values.sum()
            entropy = -float(np.sum(probabilities * np.log(probabilities)))
            effective[i] = float(np.exp(entropy))
            defined[i] = True
    if np.any(defined & (~np.isfinite(effective) | (effective < 1.))):
        raise ValueError("defined effective category count must be finite and at least one")
    if np.any(~defined & np.isfinite(effective)):
        raise AssertionError("undefined effective category counts must remain null")
    return {"effective_count": effective, "defined": defined,
            "unique_place_counts": places, "primary_categorized_place_counts": categorized}


def crossfit_region(geoids: list[str], region: str, target: np.ndarray, log_place_count: np.ndarray,
                    mx4: np.ndarray, s1: np.ndarray, defined: np.ndarray
                    ) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], np.ndarray]:
    """Five-fold region-local OLS: intercept + log1p count + frozen Mx4 F/S1 lag."""
    y, count, mx, lag, mask = (np.asarray(x, dtype=np.float64) for x in
                               (target, log_place_count, mx4, s1, defined))
    mask = mask.astype(bool)
    n = len(geoids)
    if any(x.shape != (n,) for x in (y, count, mx, lag, mask)):
        raise ValueError("cross-fit arrays must align with official GEOIDs")
    if np.any(mask & (~np.isfinite(y) | ~np.isfinite(count) | ~np.isfinite(mx) | ~np.isfinite(lag))):
        raise ValueError("jointly defined cross-fit rows must be finite")
    folds = deterministic_folds(geoids, region, N_FOLDS)
    prediction = np.full(n, np.nan, dtype=np.float64)
    residual = np.full(n, np.nan, dtype=np.float64)
    diagnostics: list[dict[str, Any]] = []
    for fold in range(N_FOLDS):
        test = mask & (folds == fold)
        train = mask & (folds != fold)
        if not test.any() or int(train.sum()) < 4:
            raise ValueError(f"{region} fold {fold}: insufficient finite train/test rows")
        design_train = np.column_stack((np.ones(int(train.sum())), count[train], mx[train], lag[train]))
        design_test = np.column_stack((np.ones(int(test.sum())), count[test], mx[test], lag[test]))
        coefficients, _, rank, singular = np.linalg.lstsq(design_train, y[train], rcond=None)
        prediction[test] = design_test @ coefficients
        residual[test] = y[test] - prediction[test]
        diagnostics.append({"fold": fold, "train_n": int(train.sum()), "heldout_n": int(test.sum()),
                            "coefficient_order": ["region_intercept", "log1p_unique_place_count", "Mx4_F",
                                                  "S1_lagged_Mx4_F"],
                            "coefficients": [float(x) for x in coefficients], "design_rank": int(rank),
                            "design_columns": int(design_train.shape[1]),
                            "minimum_singular_value": float(singular.min()) if singular.size else None})
    if np.any(mask & (~np.isfinite(prediction) | ~np.isfinite(residual))) or np.any(~mask & np.isfinite(residual)):
        raise AssertionError("crossfit output mask differs from complete-case support")
    return prediction, residual, diagnostics, folds


def output_table(pairs: list[tuple[str, str]], summary: dict[str, np.ndarray], log_count: np.ndarray,
                 mx4: np.ndarray, mx4_mask: np.ndarray, s1: np.ndarray, s1_mask: np.ndarray,
                 joint: np.ndarray, prediction: np.ndarray, residual: np.ndarray, folds: np.ndarray) -> pa.Table:
    n = len(pairs)
    arrays = (summary["effective_count"], summary["defined"], summary["unique_place_counts"],
              summary["primary_categorized_place_counts"], log_count, mx4, mx4_mask, s1, s1_mask,
              joint, prediction, residual, folds)
    if any(np.asarray(x).shape != (n,) for x in arrays):
        raise ValueError("output arrays must align with official ID rows")
    if not np.array_equal(joint, summary["defined"] & mx4_mask & s1_mask):
        raise ValueError("crossfit support must equal effective-count/Mx4/S1 mask intersection")
    if np.any(~joint & (np.isfinite(prediction) | np.isfinite(residual))):
        raise ValueError("crossfit predictions/residuals must remain null outside joint support")
    return pa.table({
        "GEOID": pa.array([g for g, _ in pairs], type=pa.string()),
        "region": pa.array([r for _, r in pairs], type=pa.string()),
        "unique_assigned_place_count": pa.array(summary["unique_place_counts"], type=pa.int64()),
        "primary_categorized_place_count": pa.array(summary["primary_categorized_place_counts"], type=pa.int64()),
        "effective_primary_category_count": pa.array(
            [float(x) if ok else None for x, ok in zip(summary["effective_count"], summary["defined"])], type=pa.float64()),
        "effective_count_defined": pa.array(summary["defined"], type=pa.bool_()),
        "log1p_unique_place_count": pa.array(log_count, type=pa.float64()),
        "Mx4_F": pa.array([float(x) if ok else None for x, ok in zip(mx4, mx4_mask)], type=pa.float64()),
        "Mx4_F_defined": pa.array(mx4_mask, type=pa.bool_()),
        "S1_lagged_Mx4_F": pa.array([float(x) if ok else None for x, ok in zip(s1, s1_mask)], type=pa.float64()),
        "S1_lagged_Mx4_F_defined": pa.array(s1_mask, type=pa.bool_()),
        "joint_defined": pa.array(joint, type=pa.bool_()),
        "fold": pa.array(folds, type=pa.int8()),
        "crossfit_prediction": pa.array([float(x) if ok else None for x, ok in zip(prediction, joint)], type=pa.float64()),
        "crossfit_residual": pa.array([float(x) if ok else None for x, ok in zip(residual, joint)], type=pa.float64()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    if not CATEGORY_REPORT.is_file() or not PRIOR_REPORT.is_file():
        raise FileNotFoundError("prior POI category audit and entropy report are required for the fixed baseline")
    category_report = json.loads(CATEGORY_REPORT.read_text(encoding="utf-8"))
    category_values = category_report.get("category_values_across_regions", [])
    if len(category_values) != 75 or len(set(category_values)) != 75:
        raise ValueError("frozen category audit must enumerate exactly 75 category values")
    prior_report = json.loads(PRIOR_REPORT.read_text(encoding="utf-8"))
    prior_acceptance = prior_report["acceptance"]
    if (prior_acceptance["eligible_tracts"] != 9327 or prior_acceptance["entropy_above_1_ids"] != 4400
            or prior_acceptance["exact_9379_official_id_alignment"] is not True):
        raise ValueError("prior POI entropy MEASURE-BEFORE values differ from the dispatched baseline")

    pairs = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(pairs) != EXPECTED_N or len(set(pairs)) != EXPECTED_N
            or len({g for g, _ in pairs}) != EXPECTED_N or set(r for _, r in pairs) != set(REGIONS)):
        raise ValueError("official IDs must be an exact 9,379-row/four-region partition")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in pairs).encode()).hexdigest()
    fs = pa.fs.S3FileSystem(anonymous=True, region="us-west-2") if hasattr(pa, "fs") else None
    if fs is None:
        import pyarrow.fs as pafs
        fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    counters: list[Counter[str] | None] = [None] * EXPECTED_N
    places = np.zeros(EXPECTED_N, dtype=np.int64)
    source_regions: dict[str, Any] = {}
    for region in REGIONS:
        indices = np.asarray([i for i, (_, r) in enumerate(pairs) if r == region], dtype=np.int64)
        geoids = [pairs[i][0] for i in indices]
        regional_counters, counts, metadata = read_region_places(fs, region, geoids)
        for index, counter in zip(indices, regional_counters):
            counters[int(index)] = counter
        places[indices] = counts
        source_regions[region] = metadata
    if any(counter is None for counter in counters):
        raise ValueError("official tract category counters are incomplete")
    typed_counters = [counter if counter is not None else Counter() for counter in counters]
    summary = effective_count_by_tract(typed_counters, places)
    effective = summary["effective_count"]
    effective_mask = summary["defined"]
    log_count = np.log1p(places.astype(np.float64))
    if not np.isfinite(log_count).all():
        raise ValueError("log1p unique place count must be finite for all official rows")

    mx4_path, s1_path = workspace / "ideas/Mx4.parquet", workspace / "ideas/S1.parquet"
    mx4, mx4_mask, mx4_hash = read_emission(mx4_path, MX4_SHA, "Mx4", "F", "covariate", pairs)
    s1, s1_mask, s1_hash = read_emission(s1_path, S1_SHA, "S1", "F", "covariate:lag_mx4_f_covariate", pairs)
    joint = effective_mask & mx4_mask & s1_mask
    if not joint.any():
        raise ValueError("no complete cases for cross-fit residualization")
    prediction = np.full(EXPECTED_N, np.nan, dtype=np.float64)
    residual = np.full(EXPECTED_N, np.nan, dtype=np.float64)
    folds = np.full(EXPECTED_N, -1, dtype=np.int8)
    regional: dict[str, Any] = {}
    for region in REGIONS:
        idx = np.asarray([i for i, (_, r) in enumerate(pairs) if r == region], dtype=np.int64)
        ids = [pairs[i][0] for i in idx]
        pred, resid, fold_reports, fold_ids = crossfit_region(
            ids, region, effective[idx], log_count[idx], mx4[idx], s1[idx], joint[idx])
        prediction[idx], residual[idx], folds[idx] = pred, resid, fold_ids
        mask = joint[idx]
        raw, rem = effective[idx][mask], resid[mask]
        regional[region] = {
            "official_ids": int(idx.size), "unique_places_ge_10": int((places[idx] >= MIN_PLACES).sum()),
            "effective_count_defined_ids": int(effective_mask[idx].sum()),
            "effective_count_coverage_fraction": float(effective_mask[idx].mean()),
            "joint_crossfit_ids": int(mask.sum()), "effective_count_distribution": distribution(raw),
            "residual_variance_ratio": float(rem.var(ddof=0) / raw.var(ddof=0)) if raw.var(ddof=0) > 0 else None,
            "residual_pearson_r_log1p_place_count": pearson(rem, log_count[idx][mask]),
            "residual_pearson_r_Mx4_F": pearson(rem, mx4[idx][mask]),
            "residual_pearson_r_S1_lagged_Mx4_F": pearson(rem, s1[idx][mask]),
            "folds": fold_reports,
        }
    raw = effective[joint]
    heldout = residual[joint]
    raw_var, residual_var = float(raw.var(ddof=0)), float(heldout.var(ddof=0))
    variance_ratio = residual_var / raw_var if raw_var > 0 else None
    correlations = {
        "log1p_unique_place_count": pearson(heldout, log_count[joint]),
        "Mx4_F": pearson(heldout, mx4[joint]),
        "S1_lagged_Mx4_F": pearson(heldout, s1[joint]),
    }
    coverage = float(effective_mask.mean())
    finite_ge_one = bool(np.isfinite(effective[effective_mask]).all() and np.all(effective[effective_mask] >= 1.))
    variance_pass = variance_ratio is not None and variance_ratio >= MIN_VARIANCE_RATIO
    coverage_pass = coverage >= MIN_COVERAGE
    frozen_corr_pass = all(correlations[k] is not None and abs(correlations[k]) < MAX_ABS_RESIDUAL_CORRELATION
                           for k in ("Mx4_F", "S1_lagged_Mx4_F"))
    if not np.array_equal(np.isfinite(effective), effective_mask):
        raise AssertionError("effective-count values and null mask do not align")

    table = output_table(pairs, summary, log_count, mx4, mx4_mask, s1, s1_mask,
                         joint, prediction, residual, folds)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("effective-count artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    acceptance = {
        "exact_9379_official_id_alignment": table.num_rows == EXPECTED_N and len(set(pairs)) == EXPECTED_N,
        "exact_effective_count_null_mask_alignment": bool(np.array_equal(np.isfinite(effective), effective_mask)),
        "official_coverage_fraction": coverage, "coverage_at_least_98_59_percent": bool(coverage_pass),
        "effective_count_finite_and_at_least_1": finite_ge_one,
        "crossfit_joint_ids": int(joint.sum()), "crossfit_residual_variance_ratio": variance_ratio,
        "residual_variance_at_least_10_percent": bool(variance_pass),
        "pooled_residual_pearson_correlations": correlations,
        "absolute_residual_correlation_below_0_80_with_each_frozen_predictor": bool(frozen_corr_pass),
        "frozen_predictor_hashes_match": bool(mx4_hash == MX4_SHA and s1_hash == S1_SHA),
        "distinct_screen_pass": bool(coverage_pass and finite_ge_one and variance_pass and frozen_corr_pass
                                      and mx4_hash == MX4_SHA and s1_hash == S1_SHA),
    }
    report = {
        "schema_version": 1,
        "screen": "Effective number exp(H) of primary Overture POI categories per official tract; five-fold cross-fit distinctness screen; unregistered",
        "definition": "For each tract, deduplicate nonnull provided Overture place IDs, assign by provided bbox center to official tract geometry, and count each unique ID once by its nonnull categories.primary. Among tracts with >=10 unique assigned IDs and at least one primary-category record, compute H=-sum(p*log(p)) over primary-category proportions, then effective_count=exp(H). Null-primary IDs count toward the >=10 unique-place support and log1p place-count predictor but not the category proportions. No clipping or fixed-vocabulary normalization is applied.",
        "method": {"source_fields": "Only provided Overture id, categories.primary, bbox and official provided GEOID/geometry tract assignment are used; no other Overture columns, reference layers, or external data.",
                   "cross_fit": "Five deterministic GEOID folds within each region. For each region, OLS with intercept, log1p unique assigned place count, frozen Mx4 F, and frozen S1 lagged Mx4 F is trained on four folds and predicts the fifth; no labels, tuning, or imputation.",
                   "variance_ratio": "Pooled population variance (ddof=0) of held-out residual divided by effective-count variance on the same complete-case support.",
                   "correlations": "Pooled Pearson correlations of held-out residual with log1p place count and both frozen predictors; acceptance gate applies to frozen Mx4 F and S1 lagged Mx4 F.",
                   "unregistered": True},
        "measure_before": {"normalized_entropy_above_1_ids": 4400, "normalized_entropy_eligible_ids": 9327,
                           "normalized_entropy_above_1_fraction_of_eligible": 4400 / 9327,
                           "effective_count_result_previously_measured": False,
                           "prior_report_sha256": sha256_file(PRIOR_REPORT)},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": official_sha,
                  "effective_count_defined_ids": int(effective_mask.sum()),
                  "effective_count_undefined_ids": int((~effective_mask).sum()),
                  "official_coverage_fraction": coverage, "unique_places_ge_10": int((places >= MIN_PLACES).sum()),
                  "joint_crossfit_ids": int(joint.sum()), "labels_read": False,
                  "sample_score_values_read": False, "removed_or_equivalent_reference_read": False,
                  "external_data_used": [], "leaderboard_feedback_used": False,
                  "estimator_or_registry_changed": False, "uploads": 0, "regions": regional},
        "measure_after": {"unique_place_count_distribution": distribution(places.astype(np.float64)),
                          "primary_categorized_place_count_distribution": distribution(summary["primary_categorized_place_counts"].astype(np.float64)),
                          "effective_count_distribution": distribution(effective[effective_mask]),
                          "effective_count_finite_and_at_least_1": finite_ge_one,
                          "effective_count_variance": raw_var, "crossfit_residual_variance": residual_var,
                          "crossfit_residual_variance_ratio": variance_ratio,
                          "pooled_residual_pearson_correlations": correlations},
        "thresholds": {"minimum_unique_places_inclusive": MIN_PLACES,
                       "minimum_official_coverage_inclusive": MIN_COVERAGE,
                       "minimum_residual_variance_ratio_inclusive": MIN_VARIANCE_RATIO,
                       "maximum_absolute_residual_correlation_with_frozen_predictors_exclusive": MAX_ABS_RESIDUAL_CORRELATION},
        "acceptance": acceptance,
        "provenance": {"official_geoid_region_sha256": official_sha,
                       "poi_category_audit_report_sha256": sha256_file(CATEGORY_REPORT),
                       "prior_entropy_report_sha256": sha256_file(PRIOR_REPORT),
                       "region_inputs": source_regions,
                       "Mx4_artifact": {"path": str(mx4_path), "sha256": mx4_hash},
                       "S1_artifact": {"path": str(s1_path), "sha256": s1_hash},
                       "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                           "sha256": hashlib.sha256(payload).hexdigest(),
                                           "columns": table.column_names, "deterministic_bytes": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The effective-count diagnostic passed all coverage, finite-support, residual-variance, and frozen-predictor correlation gates; retain only as an unregistered label-free diagnostic, not as target accuracy or promotion." if acceptance["distinct_screen_pass"] else
                         "The effective-count diagnostic failed at least one preregistered coverage or distinctness gate; retire it as a candidate signal and do not integrate it.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/poi-category-effective-count.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "poi-category-effective-count/effective-count-crossfit-residuals.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"acceptance": report["acceptance"],
                      "measure_after": {"official_coverage_fraction": report["scope"]["official_coverage_fraction"],
                                        "effective_count_distribution": report["measure_after"]["effective_count_distribution"],
                                        "crossfit_residual_variance_ratio": report["measure_after"]["crossfit_residual_variance_ratio"],
                                        "pooled_residual_pearson_correlations": report["measure_after"]["pooled_residual_pearson_correlations"]},
                      "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
