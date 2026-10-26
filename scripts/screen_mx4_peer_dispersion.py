#!/usr/bin/env python3
"""Screen leave-one-tract-out population-weighted county Mx4 peer IQR."""
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
from audit_mx4p_conditional_innovation import (  # noqa: E402
    crossfit_region, pearson, distribution as crossfit_distribution,
)

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
N_FOLDS = 5
MIN_COVERAGE = 0.9859
MIN_RESIDUAL_VARIANCE_RATIO = 0.10
MAX_ABS_PEARSON = 0.80
MX4_SHA = "8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af"
S1_SHA = "632829cf29281aa16487707cb555f52b4579cb0edfc0dd3faae360fa8f1dda03"
STRATA_SHA = {
    "maricopa-az": "f19ec2f5afd400827b2420a81916bdec95b08772badd85fdb250149c334a5fcd",
    "northern-ca": "7fc19dce5ca76ee94eb2006d9bd51bed71d5bfbcdf3ef756605e0998df940243",
    "eastern-ok": "e82862b9d9db436410ad47df466e133df94df6328f6a138d5bd871b491ba0d54",
    "south-central-tx": "55589565024414f244ccbcadcc45a90860a3840ec9d8a4bdfa8a126c2128a354",
}
FEATURES = {
    "mx4_b": ("Mx4", "Mx", "B", "covariate"),
    "mx4_f": ("Mx4", "Mx", "F", "covariate"),
    "s1_lag": ("S1", "S", "F", "covariate:lag_mx4_f_covariate"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def feature_digest(official: list[tuple[str, str]], values: np.ndarray,
                   defined: np.ndarray) -> str:
    lines = [f"{g},{r},{int(ok)}," + (format(float(v), ".17g") if ok else "NULL")
             for (g, r), v, ok in zip(official, values, defined)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """Lower weighted empirical quantile: first sorted value reaching q of total weight."""
    x = np.asarray(values, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    if x.ndim != 1 or w.shape != x.shape or x.size == 0:
        raise ValueError("weighted quantile requires nonempty aligned vectors")
    if not 0.0 <= q <= 1.0 or not np.isfinite(x).all() or not np.isfinite(w).all() or np.any(w <= 0.0):
        raise ValueError("weighted quantile requires finite values and positive finite weights")
    order = np.argsort(x, kind="mergesort")
    sorted_values, sorted_weights = x[order], w[order]
    threshold = q * float(sorted_weights.sum(dtype=np.float64))
    index = int(np.searchsorted(np.cumsum(sorted_weights, dtype=np.float64), threshold, side="left"))
    return float(sorted_values[min(index, len(sorted_values) - 1)])


def peer_weighted_iqr(official: list[tuple[str, str]], countyfp: list[str],
                      mx4_share: np.ndarray, share_defined: np.ndarray,
                      population: np.ndarray) -> dict[str, Any]:
    """Weighted Q75-Q25 from eligible same-region/county peers, excluding focal tract."""
    n = len(official)
    share = np.asarray(mx4_share, dtype=np.float64)
    share_mask = np.asarray(share_defined, dtype=bool)
    pop = np.asarray(population, dtype=np.float64)
    if len(countyfp) != n or any(a.shape != (n,) for a in (share, share_mask, pop)):
        raise ValueError("official IDs, county, Mx4 share, and population must align")
    if np.any(share_mask & (~np.isfinite(share) | (share < 0.0) | (share > 1.0))):
        raise ValueError("defined Mx4 tract shares must be finite and in [0,1]")
    if np.any(np.isfinite(pop) & (pop < 0.0)):
        raise ValueError("provided pop_total cannot be negative")
    usable = share_mask & np.isfinite(pop) & (pop > 0.0)
    groups: dict[tuple[str, str], list[int]] = {}
    for index, ((_, region), county) in enumerate(zip(official, countyfp)):
        groups.setdefault((region, str(county)), []).append(index)
    q25 = np.full(n, np.nan, dtype=np.float64)
    q75 = np.full(n, np.nan, dtype=np.float64)
    dispersion = np.full(n, np.nan, dtype=np.float64)
    peer_count = np.zeros(n, dtype=np.int32)
    peer_population = np.zeros(n, dtype=np.float64)
    for positions in groups.values():
        for focal in positions:
            donors = [i for i in positions if i != focal and usable[i]]
            if not donors:
                continue
            donor_idx = np.asarray(donors, dtype=np.int64)
            donor_shares = share[donor_idx]
            donor_weights = pop[donor_idx]
            low = weighted_quantile(donor_shares, donor_weights, 0.25)
            high = weighted_quantile(donor_shares, donor_weights, 0.75)
            if high < low:
                raise AssertionError("weighted Q75 fell below Q25")
            q25[focal], q75[focal] = low, high
            dispersion[focal] = high - low
            peer_count[focal] = len(donors)
            peer_population[focal] = float(donor_weights.sum(dtype=np.float64))
    defined = np.isfinite(dispersion)
    if np.any(defined & ((dispersion < 0.0) | (dispersion > 1.0))):
        raise ValueError("peer weighted IQR escaped [0,1]")
    return {"q25": q25, "q75": q75, "dispersion": dispersion,
            "defined": defined, "peer_count": peer_count,
            "peer_population": peer_population, "eligible_donor": usable,
            "county_group_count": len(groups)}


def read_feature(path: Path, expected_sha: str, idea: str, family: str, component: str,
                 channel: str, official: list[tuple[str, str]]) -> tuple[np.ndarray, np.ndarray, str]:
    if not path.is_file() or sha256_file(path) != expected_sha:
        raise ValueError(f"{idea} frozen artifact missing or hash mismatch")
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "family", "component", "channel", "value", "defined"],
                          filters=[("idea", "=", idea), ("component", "=", component), ("channel", "=", channel)])
    if table.num_rows != len(official):
        raise ValueError(f"{idea}|{component}|{channel}: expected one emission per official GEOID")
    positions = {pair: i for i, pair in enumerate(official)}
    values = np.full(len(official), np.nan, dtype=np.float64)
    defined = np.zeros(len(official), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in positions or pair in seen:
            raise ValueError(f"{idea}: duplicate or out-of-partition row {pair}")
        if (row["idea"], row["family"], row["component"], row["channel"]) != (idea, family, component, channel):
            raise ValueError("selected emission metadata differs from requested feature")
        seen.add(pair)
        if bool(row["defined"]):
            if row["value"] is None or not np.isfinite(float(row["value"])):
                raise ValueError(f"{idea}: defined value is null or nonfinite at {pair[0]}")
            values[positions[pair]] = float(row["value"])
            defined[positions[pair]] = True
    if seen != set(official):
        raise ValueError(f"{idea} selected emission does not reconcile exactly to official IDs")
    return values, defined, expected_sha


def read_strata(workspace: Path, region: str, region_pairs: list[tuple[str, str]]) -> tuple[dict[str, tuple[str, float]], dict[str, Any]]:
    path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
    digest = sha256_file(path)
    if digest != STRATA_SHA[region]:
        raise ValueError(f"{region} provided strata SHA differs from established source")
    table = pq.read_table(path, columns=["GEOID", "COUNTYFP", "pop_total"])
    wanted = {g for g, _ in region_pairs}
    rows: dict[str, tuple[str, float]] = {}
    for row in table.to_pylist():
        geoid = str(row["GEOID"])
        if geoid not in wanted:
            continue
        if geoid in rows or row["COUNTYFP"] is None:
            raise ValueError(f"{region}: duplicate GEOID or missing county for {geoid}")
        pop = float(row["pop_total"]) if row["pop_total"] is not None else np.nan
        rows[geoid] = (str(row["COUNTYFP"]), pop)
    if set(rows) != wanted:
        raise ValueError(f"{region}: strata rows do not exactly cover official GEOIDs")
    return rows, {"path": str(path), "sha256": digest,
                  "columns_read": ["GEOID", "COUNTYFP", "pop_total"], "official_ids": len(rows)}


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def output_table(official: list[tuple[str, str]], counties: list[str], population: np.ndarray,
                 share: np.ndarray, share_defined: np.ndarray, candidate: dict[str, Any],
                 predictors: dict[str, np.ndarray], predictor_masks: dict[str, np.ndarray],
                 joint: np.ndarray, prediction: np.ndarray, residual: np.ndarray,
                 fold_ids: np.ndarray) -> pa.Table:
    n = len(official)
    vectors = [population, share, share_defined, candidate["q25"], candidate["q75"],
               candidate["dispersion"], candidate["defined"], candidate["peer_count"],
               candidate["peer_population"], joint, prediction, residual, fold_ids,
               *predictors.values(), *predictor_masks.values()]
    if len(counties) != n or any(np.asarray(x).shape != (n,) for x in vectors):
        raise ValueError("output values and masks must align to exact official IDs")
    cdef = np.asarray(candidate["defined"], bool)
    if np.any(cdef & (~np.isfinite(candidate["dispersion"]) | (candidate["dispersion"] < 0) | (candidate["dispersion"] > 1))):
        raise ValueError("defined dispersion must be finite and in [0,1]")
    if np.any(joint & (~np.isfinite(prediction) | ~np.isfinite(residual))):
        raise ValueError("cross-fit prediction/residual must be finite on joint support")
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "COUNTYFP": pa.array(counties, type=pa.string()),
        "pop_total": pa.array([float(x) if np.isfinite(x) else None for x in population], type=pa.float64()),
        "mx4_b_source_share": pa.array([float(v) if ok else None for v, ok in zip(share, share_defined)], type=pa.float64()),
        "mx4_b_defined": pa.array(share_defined, type=pa.bool_()),
        "peer_population_weighted_q25": pa.array([float(v) if ok else None for v, ok in zip(candidate["q25"], cdef)], type=pa.float64()),
        "peer_population_weighted_q75": pa.array([float(v) if ok else None for v, ok in zip(candidate["q75"], cdef)], type=pa.float64()),
        "peer_population_weighted_iqr": pa.array([float(v) if ok else None for v, ok in zip(candidate["dispersion"], cdef)], type=pa.float64()),
        "peer_defined": pa.array(cdef, type=pa.bool_()),
        "eligible_peer_count": pa.array(candidate["peer_count"], type=pa.int32()),
        "eligible_peer_population": pa.array(candidate["peer_population"], type=pa.float64()),
        "mx4_f": pa.array([float(v) if ok else None for v, ok in zip(predictors["mx4_f"], predictor_masks["mx4_f"])], type=pa.float64()),
        "mx4_f_defined": pa.array(predictor_masks["mx4_f"], type=pa.bool_()),
        "s1_lagged_mx4_f": pa.array([float(v) if ok else None for v, ok in zip(predictors["s1_lag"], predictor_masks["s1_lag"])], type=pa.float64()),
        "s1_lagged_mx4_f_defined": pa.array(predictor_masks["s1_lag"], type=pa.bool_()),
        "crossfit_joint_defined": pa.array(joint, type=pa.bool_()),
        "fold": pa.array(fold_ids, type=pa.int8()),
        "crossfit_prediction": pa.array([float(v) if ok else None for v, ok in zip(prediction, joint)], type=pa.float64()),
        "crossfit_residual": pa.array([float(v) if ok else None for v, ok in zip(residual, joint)], type=pa.float64()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    median_path = ROOT / "reports/mx4-population-weighted-median.json"
    mean_path = ROOT / "reports/mx4p-conditional-innovation.json"
    median_report = json.loads(median_path.read_text(encoding="utf-8"))
    mean_report = json.loads(mean_path.read_text(encoding="utf-8"))
    before_ratio = median_report["measure_after"]["pooled_crossfit_residual_variance_ratio"]
    before_r = [median_report["measure_after"]["candidate_pearson_r_with_Mx4_F"],
                median_report["measure_after"]["candidate_pearson_r_with_S1_lagged_Mx4_F"]]
    before_mean_ratio = mean_report["measure_after"]["heldout_residual_variance_ratio"]
    if (not np.isclose(before_ratio, 0.05846601972131671, rtol=0, atol=1e-14)
            or not np.allclose(before_r, [0.934552336673731, 0.9249166691142697], rtol=0, atol=1e-12)
            or not np.isclose(before_mean_ratio, 0.006577271031853296, rtol=0, atol=1e-14)):
        raise ValueError("weighted-median/mean before measures differ from the issued baselines")
    official = official_scored_pairs(workspace)
    if (len(official) != 9379 or len(set(official)) != 9379
            or len({g for g, _ in official}) != 9379 or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official GEOID-region pairs must be an exact 9,379-ID partition")
    official_hash = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode("utf-8")).hexdigest()
    if official_hash != median_report["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official ID partition differs from prior median audit")

    registry_path = ROOT / "config/ideas.json"
    registry_hash_before = sha256_file(registry_path)
    mx4_path, s1_path = workspace / "ideas/Mx4.parquet", workspace / "ideas/S1.parquet"
    mx4_b, mx4_b_defined, mx4_sha = read_feature(mx4_path, MX4_SHA, *FEATURES["mx4_b"], official)
    mx4_f, mx4_f_defined, _ = read_feature(mx4_path, MX4_SHA, *FEATURES["mx4_f"], official)
    s1_lag, s1_lag_defined, s1_sha = read_feature(s1_path, S1_SHA, *FEATURES["s1_lag"], official)
    if mx4_sha != MX4_SHA or s1_sha != S1_SHA:
        raise AssertionError("frozen Mx4/S1 hashes differ from expected inputs")
    if int(mx4_b_defined.sum()) != 9377 or int(mx4_f_defined.sum()) != 9379:
        raise ValueError("Mx4 input coverage differs from frozen audited values")

    counties = [""] * len(official)
    population = np.full(len(official), np.nan, dtype=np.float64)
    strata_manifest: dict[str, Any] = {}
    positions = {g: i for i, (g, _) in enumerate(official)}
    for region in REGIONS:
        region_pairs = [(g, r) for g, r in official if r == region]
        strata_rows, metadata = read_strata(workspace, region, region_pairs)
        strata_manifest[region] = metadata
        for geoid, (county, pop) in strata_rows.items():
            i = positions[geoid]
            counties[i], population[i] = county, pop
    candidate = peer_weighted_iqr(official, counties, mx4_b, mx4_b_defined, population)
    defined = candidate["defined"]
    if not defined.any():
        raise ValueError("no peer county dispersion values are defined")
    if np.any(defined & ((candidate["dispersion"] < 0.0) | (candidate["dispersion"] > 1.0))):
        raise ValueError("peer IQR escaped [0,1]")

    predictor_masks = {"mx4_f": mx4_f_defined, "s1_lag": s1_lag_defined}
    predictors = {"mx4_f": mx4_f, "s1_lag": s1_lag}
    joint = defined & mx4_f_defined & s1_lag_defined
    predictions = np.full(len(official), np.nan, dtype=np.float64)
    residuals = np.full(len(official), np.nan, dtype=np.float64)
    folds = np.full(len(official), -1, dtype=np.int8)
    region_reports: dict[str, Any] = {}
    for region in REGIONS:
        idx = np.asarray([i for i, (_, r) in enumerate(official) if r == region], dtype=np.int64)
        ids = [official[i][0] for i in idx]
        local_joint = joint[idx]
        pred, resid, fold_reports, fold_ids = crossfit_region(
            ids, region, candidate["dispersion"][idx], mx4_f[idx], s1_lag[idx], local_joint, n_folds=N_FOLDS)
        predictions[idx], residuals[idx], folds[idx] = pred, resid, fold_ids
        local_candidate = candidate["dispersion"][idx][local_joint]
        local_resid = resid[local_joint]
        original_var = float(local_candidate.var(ddof=0)) if local_candidate.size else 0.0
        resid_var = float(local_resid.var(ddof=0)) if local_resid.size else 0.0
        region_reports[region] = {
            "official_ids": len(idx),
            "candidate_defined_ids": int(defined[idx].sum()),
            "crossfit_joint_defined_ids": int(local_joint.sum()),
            "candidate_coverage_fraction": float(defined[idx].mean()),
            "dispersion_distribution": crossfit_distribution(candidate["dispersion"][idx][defined[idx]]),
            "peer_count_min": int(candidate["peer_count"][idx][defined[idx]].min()) if defined[idx].any() else None,
            "peer_count_median": float(np.median(candidate["peer_count"][idx][defined[idx]])) if defined[idx].any() else None,
            "peer_count_max": int(candidate["peer_count"][idx][defined[idx]].max()) if defined[idx].any() else None,
            "crossfit_residual_variance_ratio": resid_var / original_var if original_var > 0 else None,
            "pearson_r_dispersion_with_mx4_f": pearson(local_candidate, mx4_f[idx][local_joint]) if local_joint.any() else None,
            "pearson_r_dispersion_with_s1_lagged_mx4_f": pearson(local_candidate, s1_lag[idx][local_joint]) if local_joint.any() else None,
            "folds": fold_reports,
        }
    joint_values = candidate["dispersion"][joint]
    joint_residuals = residuals[joint]
    candidate_variance = float(joint_values.var(ddof=0)) if joint_values.size else 0.0
    residual_variance = float(joint_residuals.var(ddof=0)) if joint_residuals.size else 0.0
    ratio = residual_variance / candidate_variance if candidate_variance > 0 else None
    correlations = {
        "mx4_f": pearson(joint_values, mx4_f[joint]),
        "s1_lagged_mx4_f": pearson(joint_values, s1_lag[joint]),
    }
    coverage = float(defined.mean())
    registry_hash_after = sha256_file(registry_path)
    frozen_after = {"Mx4": sha256_file(mx4_path), "S1": sha256_file(s1_path)}
    frozen_unchanged = registry_hash_after == registry_hash_before and frozen_after == {"Mx4": MX4_SHA, "S1": S1_SHA}
    if not frozen_unchanged:
        raise AssertionError("frozen Mx4/S1 or registry changed during diagnostic")

    table = output_table(official, counties, population, mx4_b, mx4_b_defined, candidate,
                         predictors, predictor_masks, joint, predictions, residuals, folds)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("dispersion Parquet serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    exact_ids = table.num_rows == 9379 and len(set(official)) == 9379
    bounded = bool(np.isfinite(candidate["dispersion"][defined]).all()
                   and np.all((candidate["dispersion"][defined] >= 0.0)
                              & (candidate["dispersion"][defined] <= 1.0)))
    coverage_pass = coverage >= MIN_COVERAGE
    ratio_pass = ratio is not None and ratio >= MIN_RESIDUAL_VARIANCE_RATIO
    correlation_pass = all(value is not None and abs(value) < MAX_ABS_PEARSON for value in correlations.values())
    acceptance = {
        "exact_9379_official_geoid_region_partition": exact_ids,
        "defined_dispersion_values_finite_and_in_0_1": bounded,
        "candidate_coverage_fraction": coverage,
        "candidate_coverage_at_least_98_59_percent": coverage_pass,
        "crossfit_joint_defined_ids": int(joint.sum()),
        "crossfit_residual_variance_ratio": ratio,
        "residual_variance_at_least_10_percent": bool(ratio_pass),
        "pearson_r_candidate_dispersion_with_mx4_f": correlations["mx4_f"],
        "pearson_r_candidate_dispersion_with_s1_lagged_mx4_f": correlations["s1_lagged_mx4_f"],
        "absolute_correlations_below_0_80": bool(correlation_pass),
        "frozen_Mx4_S1_registry_unchanged": bool(frozen_unchanged),
        "distinct_screen_pass": bool(exact_ids and bounded and coverage_pass and ratio_pass
                                      and correlation_pass and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "audit": "Leave-one-tract-out population-weighted county Mx4 peer interquartile range; unregistered label-free sensitivity",
        "method": {
            "candidate": "Within each (region, provided COUNTYFP), compute weighted Q75 minus weighted Q25 of other eligible tracts' frozen Mx4 B covariate (overture_building_esri_source_share), excluding the focal tract. Eligible peers require defined Mx4 B share and finite positive provided pop_total.",
            "weighted_quantile": "Sort values ascending with stable mergesort; return first value whose cumulative positive population weight is at least q times total peer weight. If no eligible peers remain, candidate is undefined; a single eligible peer yields IQR zero.",
            "crossfit": "Five region-local folds assigned via SHA-256 of mx4p-conditional-innovation-v1|region|GEOID modulo five. For each fold, fit intercept+frozen Mx4 F+frozen S1 lagged-Mx4 F OLS on other four folds, then predict held-out dispersion. No tuning, labels, regularization, or imputation.",
            "variance_ratio": "Pooled population variance (ddof=0) of held-out residuals divided by candidate dispersion variance on the same complete-case candidate/Mx4 F/S1-lag support.",
            "pearson_gate": "Pearson r of candidate dispersion with each frozen predictor, calculated on the same complete-case support.",
        },
        "measure_before": {
            "weighted_median_residual_variance_ratio": before_ratio,
            "weighted_median_pearson_r_with_Mx4_F": before_r[0],
            "weighted_median_pearson_r_with_S1_lagged_Mx4_F": before_r[1],
            "weighted_mean_residual_variance_ratio": before_mean_ratio,
            "median_baseline_report_sha256": sha256_file(median_path),
            "mean_baseline_report_sha256": sha256_file(mean_path),
        },
        "scope": {
            "official_ids": len(official), "exact_id_partition": exact_ids,
            "candidate_defined_ids": int(defined.sum()), "candidate_undefined_ids": int((~defined).sum()),
            "candidate_coverage_fraction": coverage,
            "crossfit_joint_defined_ids": int(joint.sum()), "crossfit_joint_undefined_ids": int(len(official) - joint.sum()),
            "county_groups": int(candidate["county_group_count"]),
            "labels_read": False, "sample_score_values_read": False,
            "removed_or_equivalent_reference_data_read": False, "external_inputs_read": False,
            "leaderboard_feedback_used": False, "frozen_Mx4_S1_modified": False,
            "registry_integration": False, "uploads": 0,
        },
        "measure_after": {
            "dispersion_distribution": crossfit_distribution(candidate["dispersion"][defined]),
            "eligible_peer_count_distribution": crossfit_distribution(candidate["peer_count"][defined]),
            "candidate_variance_on_crossfit_support": candidate_variance,
            "crossfit_residual_variance": residual_variance,
            "crossfit_residual_variance_ratio": ratio,
            "crossfit_residual_distribution": crossfit_distribution(joint_residuals),
            "candidate_pearson_r_with_Mx4_F": correlations["mx4_f"],
            "candidate_pearson_r_with_S1_lagged_Mx4_F": correlations["s1_lagged_mx4_f"],
            "regions": region_reports,
        },
        "acceptance_thresholds": {"minimum_candidate_coverage": MIN_COVERAGE,
                                  "dispersion_bounds_inclusive": [0.0, 1.0],
                                  "minimum_crossfit_residual_variance_ratio": MIN_RESIDUAL_VARIANCE_RATIO,
                                  "maximum_absolute_candidate_predictor_pearson_r_exclusive": MAX_ABS_PEARSON},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_hash,
            "frozen_inputs": {
                "Mx4": {"path": str(mx4_path), "sha256": mx4_sha, "sha256_after": frozen_after["Mx4"],
                        "B_share_emission_sha256": feature_digest(official, mx4_b, mx4_b_defined),
                        "F_predictor_emission_sha256": feature_digest(official, mx4_f, mx4_f_defined)},
                "S1": {"path": str(s1_path), "sha256": s1_sha, "sha256_after": frozen_after["S1"],
                       "lagged_F_emission_sha256": feature_digest(official, s1_lag, s1_lag_defined)},
            },
            "strata_inputs": strata_manifest,
            "registry_sha256_before": registry_hash_before,
            "registry_sha256_after": registry_hash_after,
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "columns": table.column_names, "deterministic_bytes": True},
        },
        "interpretation": "The candidate meets distinctness gates; keep only as an unregistered sensitivity and do not alter frozen features or registry." if acceptance["distinct_screen_pass"] else
        "One or more coverage, residual-variance, or correlation gates failed; retire the dispersion candidate and leave frozen Mx4/S1 unchanged.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/mx4-peer-dispersion.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "mx4-peer-dispersion" / "mx4-peer-dispersion.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "output_artifact": report["provenance"]["output_artifact"],
                      "report": str(report_path.relative_to(ROOT))}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
