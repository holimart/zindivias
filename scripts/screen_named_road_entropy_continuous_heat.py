#!/usr/bin/env python3
"""Cross-fitted descriptive screen for a continuous provided heat gradient."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import official_scored_pairs  # noqa: E402
from audit_mx4p_conditional_innovation import parquet_bytes, read_emission  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_N = 9379
N_FOLDS = 5
BOOTSTRAP_REPLICATES = 2000
BASE_SEED = 20261029
ENTROPY_REPORT = ROOT / "reports/named-road-class-entropy.json"
CONTRAST_REPORT = ROOT / "reports/named-road-entropy-heat-contrast.json"
ENTROPY_COLUMNS = ["GEOID", "region", "class_mix_entropy", "entropy_defined",
                   "total_named_road_km", "pct_urban", "Mx4_F", "Mx4_F_defined",
                   "S1_lagged_Mx4_F", "S1_lagged_Mx4_F_defined", "fold"]
HEAT_COLUMNS = ["GEOID", "epht_heat_days_summer", "epht_covered"]
MX4_SHA = "8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af"
S1_SHA = "632829cf29281aa16487707cb555f52b4579cb0edfc0dd3faae360fa8f1dda03"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def seed_for(label: str) -> int:
    return BASE_SEED + int.from_bytes(hashlib.sha256(label.encode("utf-8")).digest()[:4], "big")


def _delta_r2(y: np.ndarray, base_prediction: np.ndarray, heat_prediction: np.ndarray) -> dict[str, float]:
    y = np.asarray(y, dtype=np.float64)
    base = np.asarray(base_prediction, dtype=np.float64)
    augmented = np.asarray(heat_prediction, dtype=np.float64)
    if y.ndim != 1 or base.shape != y.shape or augmented.shape != y.shape or y.size < 2:
        raise ValueError("paired R-squared arrays must be aligned with at least two rows")
    if not np.isfinite(y).all() or not np.isfinite(base).all() or not np.isfinite(augmented).all():
        raise ValueError("paired R-squared arrays must be finite")
    total_ss = float(np.sum((y - y.mean()) ** 2))
    if total_ss <= 0:
        raise ValueError("held-out entropy has zero variance")
    base_sse = float(np.sum((y - base) ** 2))
    augmented_sse = float(np.sum((y - augmented) ** 2))
    return {"baseline_r2": 1. - base_sse / total_ss,
            "heat_augmented_r2": 1. - augmented_sse / total_ss,
            "delta_r2": (base_sse - augmented_sse) / total_ss,
            "baseline_sse": base_sse, "heat_augmented_sse": augmented_sse, "total_ss": total_ss}


def crossfit_region(geoids: list[str], region: str, folds: np.ndarray, entropy: np.ndarray,
                    heat: np.ndarray, baseline_predictors: np.ndarray, mask: np.ndarray
                    ) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], np.ndarray, np.ndarray]:
    """Paired OOF baseline/augmented models with train-fold-only heat standardization."""
    n = len(geoids)
    fold_ids = np.asarray(folds, dtype=np.int8)
    y, h, x, use = (np.asarray(value) for value in (entropy, heat, baseline_predictors, mask))
    y, h, x = y.astype(np.float64), h.astype(np.float64), x.astype(np.float64)
    use = use.astype(bool)
    if region not in REGIONS or len(set(geoids)) != n:
        raise ValueError("crossfit requires unique IDs and a recognized region")
    if fold_ids.shape != (n,) or y.shape != (n,) or h.shape != (n,) or use.shape != (n,) or x.ndim != 2 or x.shape[0] != n:
        raise ValueError("crossfit inputs must align with region GEOIDs")
    if np.any((fold_ids < 0) | (fold_ids >= N_FOLDS)):
        raise ValueError("fold IDs must be in 0..4")
    if np.any(use & (~np.isfinite(y) | ~np.isfinite(h) | ~np.isfinite(x).all(axis=1))):
        raise ValueError("complete-case rows must have finite entropy, heat, and baseline predictors")
    baseline_prediction = np.full(n, np.nan, dtype=np.float64)
    augmented_prediction = np.full(n, np.nan, dtype=np.float64)
    slopes = np.full(N_FOLDS, np.nan, dtype=np.float64)
    diagnostics: list[dict[str, Any]] = []
    for fold in range(N_FOLDS):
        test = use & (fold_ids == fold)
        train = use & (fold_ids != fold)
        if not test.any() or int(train.sum()) <= x.shape[1] + 1:
            raise ValueError(f"{region} fold {fold}: insufficient finite training/held-out rows")
        heat_mean = float(h[train].mean())
        heat_sd = float(h[train].std(ddof=0))
        if not np.isfinite(heat_sd) or heat_sd <= 0:
            raise ValueError(f"{region} fold {fold}: training heat has zero/nonfinite standard deviation")
        train_z = (h[train] - heat_mean) / heat_sd
        test_z = (h[test] - heat_mean) / heat_sd
        base_train = np.column_stack((np.ones(int(train.sum())), x[train]))
        base_test = np.column_stack((np.ones(int(test.sum())), x[test]))
        augmented_train = np.column_stack((base_train, train_z))
        augmented_test = np.column_stack((base_test, test_z))
        beta_base, _, rank_base, singular_base = np.linalg.lstsq(base_train, y[train], rcond=None)
        beta_augmented, _, rank_augmented, singular_augmented = np.linalg.lstsq(augmented_train, y[train], rcond=None)
        baseline_prediction[test] = base_test @ beta_base
        augmented_prediction[test] = augmented_test @ beta_augmented
        slopes[fold] = float(beta_augmented[-1])
        diagnostics.append({"fold": fold, "train_n": int(train.sum()), "heldout_n": int(test.sum()),
                            "training_heat_mean": heat_mean, "training_heat_sd": heat_sd,
                            "baseline_design_rank": int(rank_base), "augmented_design_rank": int(rank_augmented),
                            "baseline_design_columns": int(base_train.shape[1]),
                            "augmented_design_columns": int(augmented_train.shape[1]),
                            "standardized_heat_slope": float(beta_augmented[-1]),
                            "baseline_min_singular_value": float(singular_base.min()) if singular_base.size else None,
                            "augmented_min_singular_value": float(singular_augmented.min()) if singular_augmented.size else None})
    if (np.any(use & (~np.isfinite(baseline_prediction) | ~np.isfinite(augmented_prediction)))
            or np.any(~use & (np.isfinite(baseline_prediction) | np.isfinite(augmented_prediction)))):
        raise AssertionError("paired predictions must match complete-case null support")
    return baseline_prediction, augmented_prediction, diagnostics, slopes, fold_ids


def bootstrap_region_delta(y: np.ndarray, base: np.ndarray, augmented: np.ndarray, seed: int,
                           replicates: int = BOOTSTRAP_REPLICATES) -> np.ndarray:
    y, base, augmented = (np.asarray(a, dtype=np.float64) for a in (y, base, augmented))
    if not (y.ndim == base.ndim == augmented.ndim == 1 and y.shape == base.shape == augmented.shape):
        raise ValueError("bootstrap inputs must be aligned vectors")
    if y.size < 2 or not all(np.isfinite(x).all() for x in (y, base, augmented)):
        raise ValueError("bootstrap requires at least two finite paired tracts")
    rng = np.random.default_rng(seed)
    output = np.empty(replicates, dtype=np.float64)
    for replicate in range(replicates):
        index = rng.integers(0, y.size, size=y.size)
        sample = _delta_r2(y[index], base[index], augmented[index])
        output[replicate] = sample["delta_r2"]
    return output


def bootstrap_pooled_delta(region_data: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]], seed: int,
                           replicates: int = BOOTSTRAP_REPLICATES) -> np.ndarray:
    """Stratified paired bootstrap, preserving each region's observed support size."""
    if set(region_data) != set(REGIONS):
        raise ValueError("pooled bootstrap requires all four regions")
    rng = np.random.default_rng(seed)
    output = np.empty(replicates, dtype=np.float64)
    arrays = {name: tuple(np.asarray(a, dtype=np.float64) for a in values)
              for name, values in region_data.items()}
    for name, (y, base, augmented) in arrays.items():
        if y.ndim != 1 or y.size < 2 or base.shape != y.shape or augmented.shape != y.shape:
            raise ValueError(f"invalid paired bootstrap input for {name}")
        if not all(np.isfinite(a).all() for a in (y, base, augmented)):
            raise ValueError(f"nonfinite paired bootstrap input for {name}")
    for replicate in range(replicates):
        ys, base_predictions, augmented_predictions = [], [], []
        for region in REGIONS:
            y, base, augmented = arrays[region]
            idx = rng.integers(0, len(y), size=len(y))
            ys.append(y[idx]); base_predictions.append(base[idx]); augmented_predictions.append(augmented[idx])
        output[replicate] = _delta_r2(np.concatenate(ys), np.concatenate(base_predictions),
                                      np.concatenate(augmented_predictions))["delta_r2"]
    return output


def _ci(values: np.ndarray) -> list[float]:
    low, high = np.quantile(np.asarray(values, dtype=np.float64), [.025, .975])
    return [float(low), float(high)]


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if np.isfinite(number) else None


def load_data(workspace: Path) -> tuple[list[tuple[str, str]], dict[str, np.ndarray], dict[str, Any]]:
    pairs = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(pairs) != EXPECTED_N or len(set(pairs)) != EXPECTED_N
            or len({g for g, _ in pairs}) != EXPECTED_N or set(r for _, r in pairs) != set(REGIONS)):
        raise ValueError("official ID source is not an exact 9,379-row/four-region partition")
    locations = {pair: index for index, pair in enumerate(pairs)}
    entropy_path = workspace / "named-road-class-entropy/class-entropy-crossfit.parquet"
    if not entropy_path.is_file():
        raise FileNotFoundError(f"completed normalized entropy artifact missing: {entropy_path}")
    entropy_hash = sha256_file(entropy_path)
    if not ENTROPY_REPORT.is_file() or not CONTRAST_REPORT.is_file():
        raise FileNotFoundError("completed named-road entropy and quartile-contrast reports are required")
    baseline_report = json.loads(ENTROPY_REPORT.read_text(encoding="utf-8"))
    expected_artifact = baseline_report["provenance"]["output_artifact"]
    if entropy_hash != expected_artifact["sha256"] or expected_artifact["rows"] != EXPECTED_N:
        raise ValueError("named-road entropy artifact does not match its completed report hash")
    prior_contrast_report = json.loads(CONTRAST_REPORT.read_text(encoding="utf-8"))
    previous_contrast = prior_contrast_report["measure_after"]
    expected_heat_hashes = {region: prior_contrast_report["provenance"]["heat_strata_by_region"][region]["sha256"]
                            for region in REGIONS}
    expected_ci = [-0.028599064603005524, 0.004768227456805289]
    if (baseline_report["scope"]["positive_named_road_length_ids"] != 9247
            or abs(float(previous_contrast["equal_region_mean_difference_upper_minus_remaining"])
                   - (-0.011791940866455353)) > 1e-12
            or not np.allclose(previous_contrast["equal_region_bootstrap_95_ci"], expected_ci, rtol=0., atol=1e-12)
            or previous_contrast["regions_with_at_least_30_entropy_defined_tracts_in_each_group"] != 4):
        raise ValueError("completed entropy/heat-contrast MEASURE-BEFORE values differ from the dispatch")
    table = pq.read_table(entropy_path, columns=ENTROPY_COLUMNS)
    if table.num_rows != EXPECTED_N:
        raise ValueError("entropy artifact row count differs from official ID count")
    values: dict[str, np.ndarray] = {
        "entropy": np.full(EXPECTED_N, np.nan), "entropy_defined": np.zeros(EXPECTED_N, dtype=bool),
        "total_road_km": np.full(EXPECTED_N, np.nan), "pct_urban": np.full(EXPECTED_N, np.nan),
        "mx4": np.full(EXPECTED_N, np.nan), "mx4_defined": np.zeros(EXPECTED_N, dtype=bool),
        "s1": np.full(EXPECTED_N, np.nan), "s1_defined": np.zeros(EXPECTED_N, dtype=bool),
        "fold": np.full(EXPECTED_N, -1, dtype=np.int8)}
    seen: set[tuple[str, str]] = set()
    columns = {name: table.column(name).to_pylist() for name in ENTROPY_COLUMNS}
    for row_i in range(table.num_rows):
        pair = (str(columns["GEOID"][row_i]), str(columns["region"][row_i]))
        if pair not in locations or pair in seen:
            raise ValueError(f"unexpected or duplicate entropy-artifact official ID: {pair}")
        seen.add(pair); i = locations[pair]
        for key, source in (("entropy", "class_mix_entropy"), ("total_road_km", "total_named_road_km"),
                            ("pct_urban", "pct_urban"), ("mx4", "Mx4_F"), ("s1", "S1_lagged_Mx4_F")):
            number = _finite_or_none(columns[source][row_i])
            if number is not None:
                values[key][i] = number
        for key, source in (("entropy_defined", "entropy_defined"), ("mx4_defined", "Mx4_F_defined"),
                            ("s1_defined", "S1_lagged_Mx4_F_defined")):
            raw = columns[source][row_i]
            if not isinstance(raw, bool):
                raise ValueError(f"invalid boolean source mask {source} on {pair}")
            values[key][i] = raw
        fold = columns["fold"][row_i]
        if fold is None or int(fold) not in range(N_FOLDS):
            raise ValueError(f"invalid frozen five-fold assignment on {pair}")
        values["fold"][i] = int(fold)
    if seen != set(pairs):
        raise ValueError("entropy artifact GEOID/region rows do not exactly match official IDs")
    if (not np.array_equal(np.isfinite(values["entropy"]), values["entropy_defined"])
            or not np.array_equal(np.isfinite(values["mx4"]), values["mx4_defined"])
            or not np.array_equal(np.isfinite(values["s1"]), values["s1_defined"])):
        raise ValueError("entropy or frozen-predictor values do not preserve their null masks")
    if np.any(values["entropy_defined"] & ((values["entropy"] < 0.) | (values["entropy"] > 1.))):
        raise ValueError("normalized named-road entropy must remain in [0,1]")
    if np.any(values["entropy_defined"] & (~np.isfinite(values["total_road_km"]) | (values["total_road_km"] <= 0.))):
        raise ValueError("defined entropy requires positive finite named-road length")
    if np.any(np.isfinite(values["pct_urban"]) & ((values["pct_urban"] < 0.) | (values["pct_urban"] > 1.))):
        raise ValueError("provided pct_urban must be within [0,1]")

    heat = np.full(EXPECTED_N, np.nan, dtype=np.float64)
    heat_mask = np.zeros(EXPECTED_N, dtype=bool)
    heat_meta: dict[str, Any] = {}
    for region in REGIONS:
        path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"provided heat strata missing: {path}")
        heat_table = pq.read_table(path, columns=HEAT_COLUMNS)
        local_ids = {g: locations[(g, region)] for g, r in pairs if r == region}
        local_seen: set[str] = set(); extras = 0
        for geoid_value, heat_value, covered_value in zip(
                heat_table.column("GEOID").to_pylist(), heat_table.column("epht_heat_days_summer").to_pylist(),
                heat_table.column("epht_covered").to_pylist()):
            geoid = str(geoid_value)
            if geoid not in local_ids:
                extras += 1; continue
            if geoid in local_seen:
                raise ValueError(f"duplicate heat-strata GEOID in {region}: {geoid}")
            local_seen.add(geoid)
            number = _finite_or_none(heat_value)
            covered = covered_value is True
            if covered and number is not None:
                i = local_ids[geoid]; heat[i] = number; heat_mask[i] = True
            elif covered and number is None:
                raise ValueError(f"provided heat is covered but null/nonfinite for {geoid}")
        if local_seen != set(local_ids):
            raise ValueError(f"heat strata IDs do not exactly cover official IDs in {region}")
        heat_sha = sha256_file(path)
        if heat_sha != expected_heat_hashes[region]:
            raise ValueError(f"provided heat strata hash differs from the completed quartile contrast for {region}")
        heat_meta[region] = {"path": str(path), "sha256": heat_sha, "columns_read": HEAT_COLUMNS,
                             "provided_rows": heat_table.num_rows, "official_ids_joined": len(local_seen),
                             "heat_covered_finite_official_ids": int(sum(heat_mask[local_ids[g]] for g in local_ids)),
                             "official_heat_missing": int(sum(not heat_mask[local_ids[g]] for g in local_ids)),
                             "nonofficial_table_rows_ignored": extras}
    # Resolve the frozen predictor source hashes and prove the entropy cache carries those exact emissions.
    mx4_path, s1_path = workspace / "ideas/Mx4.parquet", workspace / "ideas/S1.parquet"
    mx4, mx4_mask, mx4_hash = read_emission(mx4_path, MX4_SHA, "Mx4", "F", "covariate", pairs)
    s1, s1_mask, s1_hash = read_emission(s1_path, S1_SHA, "S1", "F", "covariate:lag_mx4_f_covariate", pairs)
    if (not np.array_equal(mx4_mask, values["mx4_defined"]) or not np.array_equal(s1_mask, values["s1_defined"])
            or not np.array_equal(mx4, values["mx4"], equal_nan=True)
            or not np.array_equal(s1, values["s1"], equal_nan=True)):
        raise ValueError("frozen predictor emissions differ from those recorded in entropy artifact")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in pairs).encode()).hexdigest()
    values["heat"] = heat; values["heat_defined"] = heat_mask
    return pairs, values, {
        "official_geoid_region_sha256": official_sha,
        "entropy_artifact": {"path": str(entropy_path), "sha256": entropy_hash, "columns_read": ENTROPY_COLUMNS,
                             "rows": table.num_rows, "entropy_defined_ids": int(values["entropy_defined"].sum())},
        "heat_strata_by_region": heat_meta,
        "frozen_predictors": {"Mx4": {"path": str(mx4_path), "sha256": mx4_hash},
                              "S1": {"path": str(s1_path), "sha256": s1_hash}},
        "entropy_baseline_report": {"path": str(ENTROPY_REPORT), "sha256": sha256_file(ENTROPY_REPORT)},
        "prior_quartile_report": {"path": str(CONTRAST_REPORT), "sha256": sha256_file(CONTRAST_REPORT)},
    }


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    pairs, values, provenance = load_data(workspace)
    n = len(pairs)
    entropy, entropy_mask, heat, heat_mask = (values[k] for k in
                                               ("entropy", "entropy_defined", "heat", "heat_defined"))
    log_road = np.full(n, np.nan, dtype=np.float64)
    log_road[entropy_mask] = np.log(values["total_road_km"][entropy_mask])
    base_predictors = np.column_stack((log_road, values["pct_urban"], values["mx4"], values["s1"]))
    base_mask = (entropy_mask & heat_mask & np.isfinite(base_predictors).all(axis=1)
                 & values["mx4_defined"] & values["s1_defined"])
    if not base_mask.any():
        raise ValueError("no complete cases for the paired heat gradient screen")
    prediction_base = np.full(n, np.nan); prediction_heat = np.full(n, np.nan)
    folds = values["fold"].copy()
    region_data: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    regional: dict[str, Any] = {}
    all_fold_diagnostics: dict[str, Any] = {}
    for region in REGIONS:
        ix = np.asarray([i for i, (_, r) in enumerate(pairs) if r == region], dtype=np.int64)
        local_ids = [pairs[i][0] for i in ix]
        pred0, pred1, fold_reports, slopes, returned_folds = crossfit_region(
            local_ids, region, folds[ix], entropy[ix], heat[ix], base_predictors[ix], base_mask[ix])
        if not np.array_equal(returned_folds, folds[ix]):
            raise AssertionError("frozen entropy GEOID folds were not reused exactly")
        prediction_base[ix], prediction_heat[ix] = pred0, pred1
        support = base_mask[ix]
        y, base_oof, heat_oof = entropy[ix][support], pred0[support], pred1[support]
        metrics = _delta_r2(y, base_oof, heat_oof)
        seed = seed_for(f"named-road-entropy-continuous-heat:{region}")
        bootstrap = bootstrap_region_delta(y, base_oof, heat_oof, seed)
        ci = _ci(bootstrap)
        region_data[region] = (y, base_oof, heat_oof)
        slopes_dict = {str(record["fold"]): float(record["standardized_heat_slope"]) for record in fold_reports}
        regional[region] = {
            "official_ids": int(ix.size), "entropy_defined_ids": int(entropy_mask[ix].sum()),
            "heat_covered_ids": int(heat_mask[ix].sum()), "joint_complete_case_ids": int(support.sum()),
            "fold_heldout_support": {str(f): int(np.count_nonzero(support & (folds[ix] == f))) for f in range(N_FOLDS)},
            "paired_heldout_baseline_r2": metrics["baseline_r2"],
            "paired_heldout_heat_augmented_r2": metrics["heat_augmented_r2"],
            "paired_heldout_delta_r2": metrics["delta_r2"],
            "paired_heldout_delta_r2_bootstrap_95_ci": ci,
            "fold_standardized_heat_slopes": slopes_dict,
            "mean_fold_standardized_heat_slope": float(slopes.mean()),
            "sd_fold_standardized_heat_slope": float(slopes.std(ddof=0)),
            "bootstrap_seed": seed, "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "fold_fit_diagnostics": fold_reports,
        }
        if sum(regional[region]["fold_heldout_support"].values()) != int(support.sum()):
            raise AssertionError(f"fold support does not cover complete cases for {region}")
    pooled_y = np.concatenate([region_data[r][0] for r in REGIONS])
    pooled_base = np.concatenate([region_data[r][1] for r in REGIONS])
    pooled_heat = np.concatenate([region_data[r][2] for r in REGIONS])
    pooled_metrics = _delta_r2(pooled_y, pooled_base, pooled_heat)
    pooled_seed = seed_for("named-road-entropy-continuous-heat:pooled-stratified")
    pooled_bootstrap = bootstrap_pooled_delta(region_data, pooled_seed)
    pooled_ci = _ci(pooled_bootstrap)
    positive_regions = sum(regional[r]["paired_heldout_delta_r2"] > 0. for r in REGIONS)
    gate = bool(pooled_metrics["delta_r2"] >= .01 and pooled_ci[0] > 0. and positive_regions >= 3)

    output = pa.table({
        "GEOID": pa.array([g for g, _ in pairs], type=pa.string()),
        "region": pa.array([r for _, r in pairs], type=pa.string()),
        "normalized_named_road_class_entropy": pa.array([float(x) if ok else None for x, ok in zip(entropy, entropy_mask)], type=pa.float64()),
        "entropy_defined": pa.array(entropy_mask, type=pa.bool_()),
        "epht_heat_days_summer": pa.array([float(x) if ok else None for x, ok in zip(heat, heat_mask)], type=pa.float64()),
        "heat_defined": pa.array(heat_mask, type=pa.bool_()),
        "log_total_named_road_km": pa.array([float(x) if np.isfinite(x) else None for x in log_road], type=pa.float64()),
        "pct_urban": pa.array([float(x) if np.isfinite(x) else None for x in values["pct_urban"]], type=pa.float64()),
        "Mx4_F": pa.array([float(x) if ok else None for x, ok in zip(values["mx4"], values["mx4_defined"])], type=pa.float64()),
        "S1_lagged_Mx4_F": pa.array([float(x) if ok else None for x, ok in zip(values["s1"], values["s1_defined"])], type=pa.float64()),
        "fold": pa.array(folds, type=pa.int8()),
        "joint_defined": pa.array(base_mask, type=pa.bool_()),
        "baseline_prediction": pa.array([float(x) if ok else None for x, ok in zip(prediction_base, base_mask)], type=pa.float64()),
        "heat_augmented_prediction": pa.array([float(x) if ok else None for x, ok in zip(prediction_heat, base_mask)], type=pa.float64()),
    })
    if not np.array_equal(np.isfinite(prediction_base), base_mask) or not np.array_equal(np.isfinite(prediction_heat), base_mask):
        raise AssertionError("paired prediction masks must exactly equal complete-case support")
    payload = parquet_bytes(output)
    if payload != parquet_bytes(output):
        raise ValueError("paired prediction artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    report = {
        "schema_version": 1,
        "screen": "Continuous provided summer-heat gradient and normalized named-road class-share entropy; paired five-fold held-out association screen; unregistered",
        "measure_before": {"entropy_defined_ids": 9247, "official_ids": 9379,
                           "quartile_equal_region_difference": -0.011791940866455353,
                           "quartile_bootstrap_95_ci": [-0.028599064603005524, 0.004768227456805289],
                           "quartile_regions_with_adequate_support": 4,
                           "continuous_heat_slope_previously_measured": False,
                           "quartile_report_sha256": provenance["prior_quartile_report"]["sha256"]},
        "definition": "Outcome is normalized entropy of provided Overture motorway/trunk/primary/secondary named-road length shares. The association screen compares held-out R2 for the same complete-case tracts, using a region-specific baseline OLS with intercept, log total named-road km, pct_urban, frozen Mx4 F and frozen S1 lag, versus the same model plus within-region standardized epht_heat_days_summer.",
        "method": {"folds": "Reuse the exact five GEOID fold assignments saved in the completed named-road entropy artifact; no fold reassignment.",
                   "heat_standardization": "Within each region and fold, fit heat mean and population SD on training tracts only; apply those training values to training and held-out heat. No held-out heat value contributes to its fold's scaling parameters.",
                   "paired_support": "Both baseline and augmented regressions use identical official tracts with finite entropy, heat, log named-road length, pct_urban, and frozen predictors.",
                   "crossfit": "For each region/fold, fit both least-squares models on the other four folds and obtain paired predictions for the held-out fold.",
                   "delta_r2": "For the pooled result, delta R2=(SSE_baseline-SSE_augmented)/pooled held-out entropy total sum of squares around the pooled held-out mean. Region-specific values use each region's held-out entropy sum of squares around that region's held-out mean.",
                   "bootstrap": "2,000 paired tract-bootstrap replicates. Resample paired held-out entropy and both model predictions with replacement within each region; pooled replicates preserve each region's observed support size. Percentile 95% intervals.",
                   "unregistered": True},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": provenance["official_geoid_region_sha256"],
                  "entropy_defined_ids": int(entropy_mask.sum()), "heat_covered_ids": int(heat_mask.sum()),
                  "paired_complete_case_ids": int(base_mask.sum()),
                  "fold_counts_official": {str(f): int(np.count_nonzero(folds == f)) for f in range(N_FOLDS)},
                  "fold_counts_complete_case": {str(f): int(np.count_nonzero(base_mask & (folds == f))) for f in range(N_FOLDS)},
                  "exact_entropy_null_mask_alignment": bool(np.array_equal(np.isfinite(entropy), entropy_mask)),
                  "exact_heat_null_mask_alignment": bool(np.array_equal(np.isfinite(heat), heat_mask)),
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_references_read": False, "external_data_used": [],
                  "leaderboard_feedback_used": False, "estimator_or_registry_changed": False, "uploads": 0},
        "measure_after": {"pooled_paired_heldout_baseline_r2": pooled_metrics["baseline_r2"],
                          "pooled_paired_heldout_heat_augmented_r2": pooled_metrics["heat_augmented_r2"],
                          "pooled_paired_heldout_delta_r2": pooled_metrics["delta_r2"],
                          "pooled_paired_heldout_delta_r2_bootstrap_95_ci": pooled_ci,
                          "pooled_bootstrap_seed": pooled_seed, "bootstrap_replicates": BOOTSTRAP_REPLICATES,
                          "regions_with_positive_heldout_delta_r2": positive_regions,
                          "regions": regional},
        "acceptance": {"pooled_delta_r2_at_least_0_01": bool(pooled_metrics["delta_r2"] >= .01),
                       "pooled_bootstrap_ci_lower_above_zero": bool(pooled_ci[0] > 0.),
                       "positive_heldout_delta_r2_in_at_least_3_of_4_regions": bool(positive_regions >= 3),
                       "continuous_heat_association_screen_pass": gate},
        "provenance": {**provenance,
                       "output_artifact": {"path": str(artifact_path), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": output.num_rows, "columns": output.column_names,
                                           "deterministic_serialization": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The continuous provided heat-gradient association passed the preregistered pooled effect, bootstrap-CI, and regional consistency gates; this remains descriptive and does not imply causality, target accuracy, or model promotion." if gate else
                         "The continuous heat-gradient screen did not pass every preregistered pooled effect, bootstrap-CI, and regional consistency gate; retain descriptive-only and make no target-accuracy or causal claim.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/named-road-entropy-continuous-heat.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "named-road-entropy-continuous-heat/paired-crossfit-predictions.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"scope": report["scope"], "measure_after": report["measure_after"],
                      "acceptance": report["acceptance"], "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
