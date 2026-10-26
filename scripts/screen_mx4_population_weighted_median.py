#!/usr/bin/env python3
"""Screen leave-one-tract-out population-weighted median Mx4 county context."""
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
from bias60.inputs import official_scored_pairs  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
N_FOLDS = 5
MIN_COVERAGE = 0.9859
MIN_VARIANCE_RATIO = 0.10
MAX_ABS_R = 0.80
MX4_B = ("Mx4", "Mx", "B", "covariate")
MX4_F = ("Mx4", "Mx", "F", "covariate")
S1_LAG = ("S1", "S", "F", "covariate:lag_mx4_f_covariate")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def feature_hash(official: list[tuple[str, str]], values: np.ndarray,
                 defined: np.ndarray) -> str:
    records = [f"{geoid},{region},{int(ok)}," + (format(float(value), ".17g") if ok else "NULL")
               for (geoid, region), value, ok in zip(official, values, defined)]
    return hashlib.sha256("\n".join(records).encode("utf-8")).hexdigest()


def read_emission(path: Path, expected_sha: str, spec: tuple[str, str, str, str],
                  official: list[tuple[str, str]]) -> tuple[np.ndarray, np.ndarray, str]:
    idea, family, component, channel = spec
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = sha256_file(path)
    if digest != expected_sha:
        raise ValueError(f"frozen {idea} artifact hash differs from harness")
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "family", "component",
                                         "channel", "value", "defined"],
                          filters=[("idea", "=", idea), ("component", "=", component),
                                   ("channel", "=", channel)])
    if table.num_rows != len(official):
        raise ValueError(f"{idea}/{component}/{channel} has an unexpected row count")
    positions = {pair: i for i, pair in enumerate(official)}
    values = np.full(len(official), np.nan, dtype=np.float64)
    defined = np.zeros(len(official), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in positions or pair in seen:
            raise ValueError(f"{idea}: selected emission contains duplicate/out-of-scope pair {pair}")
        if (row["idea"], row["family"], row["component"], row["channel"]) != spec:
            raise ValueError("selected emission metadata does not match requested frozen feature")
        seen.add(pair)
        if bool(row["defined"]):
            value = float(row["value"])
            if not np.isfinite(value):
                raise ValueError(f"{idea}: defined source value is nonfinite")
            values[positions[pair]] = value
            defined[positions[pair]] = True
    if seen != set(official):
        raise ValueError(f"{idea}: selected emission fails exact GEOID/region partition")
    return values, defined, digest


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float | None:
    """Return the lower weighted median: first sorted value reaching half total weight."""
    x = np.asarray(values, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    if x.ndim != 1 or w.shape != x.shape:
        raise ValueError("weighted median values and weights must be aligned vectors")
    valid = np.isfinite(x) & np.isfinite(w) & (w > 0.0)
    x, w = x[valid], w[valid]
    if not x.size:
        return None
    if np.any((x < 0.0) | (x > 1.0)):
        raise ValueError("defined Mx4 source shares must be in [0,1]")
    order = np.argsort(x, kind="mergesort")
    x, w = x[order], w[order]
    threshold = 0.5 * float(w.sum())
    index = int(np.searchsorted(np.cumsum(w), threshold, side="left"))
    return float(x[min(index, x.size - 1)])


def leave_one_out_weighted_median(official: list[tuple[str, str]],
                                  county: np.ndarray, population: np.ndarray,
                                  source: np.ndarray, source_defined: np.ndarray
                                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Weighted median of eligible peer Mx4 B shares in each region/county, excluding self."""
    n = len(official)
    arrays = (np.asarray(county), np.asarray(population, dtype=np.float64),
              np.asarray(source, dtype=np.float64), np.asarray(source_defined, dtype=bool))
    if any(array.shape != (n,) for array in arrays):
        raise ValueError("county, population, source and defined arrays must align to official IDs")
    _, pop, share, share_defined = arrays
    valid = share_defined & np.isfinite(share) & np.isfinite(pop) & (pop > 0.0)
    if np.any(valid & ((share < 0.0) | (share > 1.0))):
        raise ValueError("eligible Mx4 B source shares must be in [0,1]")
    groups: dict[tuple[str, str], list[int]] = {}
    for i, ((_, region), county_id) in enumerate(zip(official, county)):
        if county_id is None or not str(county_id).strip():
            continue
        groups.setdefault((region, str(county_id)), []).append(i)
    result = np.full(n, np.nan, dtype=np.float64)
    defined = np.zeros(n, dtype=bool)
    peer_counts = np.zeros(n, dtype=np.int32)
    for indices in groups.values():
        eligible = [i for i in indices if valid[i]]
        if not eligible:
            continue
        x = share[eligible]
        w = pop[eligible]
        for i in indices:
            # Subtract only when the focal tract itself is an eligible population/share donor.
            if valid[i]:
                peer_indices = [j for j in eligible if j != i]
            else:
                peer_indices = eligible
            if not peer_indices:
                continue
            median = weighted_median(share[peer_indices], pop[peer_indices])
            if median is None:
                continue
            result[i] = median
            defined[i] = True
            peer_counts[i] = len(peer_indices)
    if np.any(defined & (~np.isfinite(result) | (result < 0.0) | (result > 1.0))):
        raise AssertionError("defined leave-one-out medians must be finite and in [0,1]")
    if np.any(~defined & np.isfinite(result)) or np.any(defined & (peer_counts <= 0)):
        raise AssertionError("median definedness must match existence of eligible peers")
    return result, defined, peer_counts


def deterministic_folds(geoids: list[str], region: str, n_folds: int = N_FOLDS) -> np.ndarray:
    if n_folds < 2 or region not in REGIONS or len(set(geoids)) != len(geoids):
        raise ValueError("folds need a known region, unique IDs and at least two folds")
    return np.asarray([
        int.from_bytes(hashlib.sha256(
            f"mx4p-conditional-innovation-v1|{region}|{geoid}".encode("utf-8")
        ).digest()[:8], "big") % n_folds for geoid in geoids
    ], dtype=np.int8)


def pearson(x: np.ndarray, y: np.ndarray) -> float | None:
    a, b = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    if a.ndim != 1 or b.shape != a.shape:
        raise ValueError("Pearson inputs must be aligned vectors")
    mask = np.isfinite(a) & np.isfinite(b)
    if int(mask.sum()) < 3:
        return None
    a, b = a[mask], b[mask]
    a, b = a - a.mean(), b - b.mean()
    denominator = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
    if denominator <= 0.0:
        return None
    r = float(np.dot(a, b) / denominator)
    if not np.isfinite(r):
        raise ValueError("defined Pearson correlation is nonfinite")
    return float(np.clip(r, -1.0, 1.0))


def crossfit_region(geoids: list[str], region: str, target: np.ndarray,
                    predictors: tuple[np.ndarray, np.ndarray], mask: np.ndarray
                    ) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], np.ndarray]:
    y = np.asarray(target, dtype=np.float64)
    x1, x2 = (np.asarray(x, dtype=np.float64) for x in predictors)
    joint = np.asarray(mask, dtype=bool)
    n = len(geoids)
    if any(x.shape != (n,) for x in (y, x1, x2, joint)):
        raise ValueError("cross-fit arrays must align with region GEOIDs")
    if np.any(joint & (~np.isfinite(y) | ~np.isfinite(x1) | ~np.isfinite(x2))):
        raise ValueError("jointly defined cross-fit inputs must be finite")
    folds = deterministic_folds(geoids, region)
    prediction = np.full(n, np.nan)
    residual = np.full(n, np.nan)
    reports = []
    for fold in range(N_FOLDS):
        test = joint & (folds == fold)
        train = joint & (folds != fold)
        if not test.any() or train.sum() < 3:
            raise ValueError(f"{region} fold {fold} lacks sufficient complete cases")
        train_x = np.column_stack((np.ones(train.sum()), x1[train], x2[train]))
        test_x = np.column_stack((np.ones(test.sum()), x1[test], x2[test]))
        coefficients, _, rank, singular = np.linalg.lstsq(train_x, y[train], rcond=None)
        prediction[test] = test_x @ coefficients
        residual[test] = y[test] - prediction[test]
        reports.append({"fold": fold, "train_joint_defined": int(train.sum()),
                        "heldout_joint_defined": int(test.sum()),
                        "coefficients_intercept_mx4_f_s1_lag": [float(v) for v in coefficients],
                        "design_rank": int(rank), "design_columns": 3,
                        "minimum_singular_value": float(singular.min()) if singular.size else None})
    if np.any(joint & (~np.isfinite(prediction) | ~np.isfinite(residual))) or np.any(~joint & np.isfinite(residual)):
        raise AssertionError("cross-fit residual mask is not exactly the joint-defined support")
    return prediction, residual, reports, folds


def distribution(values: np.ndarray) -> dict[str, Any]:
    x = np.asarray(values, dtype=np.float64)
    x = x[np.isfinite(x)]
    if not x.size:
        return {"n": 0, "min": None, "p50": None, "p95": None, "max": None,
                "mean": None, "variance": None}
    return {"n": int(x.size), "min": float(x.min()), "p50": float(np.quantile(x, .5)),
            "p95": float(np.quantile(x, .95)), "max": float(x.max()),
            "mean": float(x.mean()), "variance": float(x.var(ddof=0))}


def read_strata(workspace: Path, official: list[tuple[str, str]]) -> tuple[np.ndarray, np.ndarray, dict[str, str]]:
    positions = {pair: i for i, pair in enumerate(official)}
    county = np.full(len(official), "", dtype=object)
    population = np.full(len(official), np.nan, dtype=np.float64)
    seen: set[tuple[str, str]] = set()
    hashes: dict[str, str] = {}
    for region in REGIONS:
        path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        if not path.is_file():
            raise FileNotFoundError(path)
        hashes[region] = sha256_file(path)
        table = pq.read_table(path, columns=["GEOID", "COUNTYFP", "pop_total"])
        for row in table.to_pylist():
            pair = (str(row["GEOID"]), region)
            if pair not in positions:
                continue  # Source strata can contain extra tracts outside the official scored ID set.
            if pair in seen:
                raise ValueError(f"strata input contains duplicate official GEOID {pair}")
            seen.add(pair)
            index = positions[pair]
            county_id = row["COUNTYFP"]
            county[index] = "" if county_id is None else str(county_id).strip()
            try:
                population[index] = float(row["pop_total"]) if row["pop_total"] is not None else np.nan
            except (TypeError, ValueError):
                population[index] = np.nan
    if seen != set(official):
        raise ValueError("COUNTYFP/pop_total strata data does not exactly reconcile to official IDs")
    return county, population, hashes


def output_table(official: list[tuple[str, str]], candidate: np.ndarray, candidate_defined: np.ndarray,
                 peer_counts: np.ndarray, mx4_b: np.ndarray, mx4_b_defined: np.ndarray,
                 mx4_f: np.ndarray, mx4_f_defined: np.ndarray, s1_lag: np.ndarray,
                 s1_defined: np.ndarray, fold: np.ndarray, joint: np.ndarray,
                 prediction: np.ndarray, residual: np.ndarray) -> pa.Table:
    n = len(official)
    arrays = (candidate, candidate_defined, peer_counts, mx4_b, mx4_b_defined, mx4_f,
              mx4_f_defined, s1_lag, s1_defined, fold, joint, prediction, residual)
    if any(np.asarray(array).shape != (n,) for array in arrays):
        raise ValueError("output columns must align to exact official ID list")
    if np.any(joint & (~np.isfinite(prediction) | ~np.isfinite(residual))) or np.any(~joint & np.isfinite(residual)):
        raise ValueError("prediction/residual values must preserve the joint-defined mask")
    if not np.array_equal(joint, candidate_defined & mx4_f_defined & s1_defined):
        raise ValueError("joint support differs from source-defined masks")
    if np.any(candidate_defined & (~np.isfinite(candidate) | (candidate < 0.0) | (candidate > 1.0))):
        raise ValueError("defined median values must be finite and within [0,1]")
    ids, regions = zip(*official)
    def nullable(values: np.ndarray, mask: np.ndarray) -> pa.Array:
        return pa.array([float(v) if ok else None for v, ok in zip(values, mask)], type=pa.float64())
    return pa.table({
        "GEOID": pa.array(ids, type=pa.string()), "region": pa.array(regions, type=pa.string()),
        "peer_population_weighted_median_share": nullable(candidate, candidate_defined),
        "peer_defined": pa.array(candidate_defined, type=pa.bool_()),
        "eligible_peer_count": pa.array(peer_counts, type=pa.int32()),
        "mx4_b_source_share": nullable(mx4_b, mx4_b_defined),
        "mx4_b_defined": pa.array(mx4_b_defined, type=pa.bool_()),
        "mx4_f_county_source_share": nullable(mx4_f, mx4_f_defined),
        "mx4_f_defined": pa.array(mx4_f_defined, type=pa.bool_()),
        "s1_lagged_mx4_f": nullable(s1_lag, s1_defined),
        "s1_lag_defined": pa.array(s1_defined, type=pa.bool_()),
        "fold": pa.array(fold.astype(np.int8), type=pa.int8()),
        "jointly_defined": pa.array(joint, type=pa.bool_()),
        "crossfit_prediction": nullable(prediction, joint),
        "crossfit_residual": nullable(residual, joint),
    })


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    harness_path = ROOT / "reports/ideas-harness.json"
    registry_path = ROOT / "config/ideas.json"
    before_report_path = ROOT / "reports/mx4p-conditional-innovation.json"
    mean_report_path = ROOT / "reports/mx4-population-weighted-context.json"
    registry_sha_before = sha256_file(registry_path)
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if any(entry.get("id") == "Mx4p" for entry in registry["ideas"]):
        raise ValueError("Mx4p must remain unregistered during the sensitivity screen")
    for required in ("Mx4", "S1"):
        if required not in {x["id"] for x in registry["ideas"] if x.get("status") == "implemented"}:
            raise ValueError(f"registered input idea {required} is missing")
    official = official_scored_pairs(workspace)
    if len(official) != 9379 or len(set(official)) != 9379 or len({g for g, _ in official}) != 9379:
        raise ValueError("official IDs must be an exact 9,379 GEOID/region partition")
    if set(region for _, region in official) != set(REGIONS):
        raise ValueError("official ID list has unexpected or missing regions")
    values: dict[str, np.ndarray] = {}
    masks: dict[str, np.ndarray] = {}
    artifact_hashes: dict[str, str] = {}
    for spec in (MX4_B, MX4_F, S1_LAG):
        idea = spec[0]
        frozen = harness.get("per_idea", {}).get(idea)
        if not frozen or not frozen.get("geoid_partition_exact") or frozen.get("forbidden_source_hits"):
            raise ValueError(f"frozen harness does not certify exact, clean {idea} outputs")
        v, m, digest = read_emission(workspace / "ideas" / f"{idea}.parquet", frozen["sha256"], spec, official)
        key = "|".join((spec[0], spec[2], spec[3]))
        values[key], masks[key], artifact_hashes[idea] = v, m, digest
    county, population, strata_hashes = read_strata(workspace, official)
    b_key = "Mx4|B|covariate"
    f_key = "Mx4|F|covariate"
    lag_key = "S1|F|covariate:lag_mx4_f_covariate"
    candidate, candidate_defined, peer_counts = leave_one_out_weighted_median(
        official, county, population, values[b_key], masks[b_key])
    mx4_f, mx4_f_defined = values[f_key], masks[f_key]
    s1_lag, s1_defined = values[lag_key], masks[lag_key]
    joint = candidate_defined & mx4_f_defined & s1_defined
    if not joint.any():
        raise ValueError("no complete cases for candidate and both fixed predictors")
    prediction = np.full(len(official), np.nan)
    residual = np.full(len(official), np.nan)
    fold = np.full(len(official), -1, dtype=np.int8)
    regions = [r for _, r in official]
    region_stats: dict[str, Any] = {}
    pooled_resids: list[np.ndarray] = []
    pooled_target: list[np.ndarray] = []
    pooled_f: list[np.ndarray] = []
    pooled_lag: list[np.ndarray] = []
    for region in REGIONS:
        indices = np.flatnonzero(np.asarray(regions) == region)
        local_ids = [official[i][0] for i in indices]
        local_joint = joint[indices]
        local_pred, local_resid, fold_reports, folds = crossfit_region(
            local_ids, region, candidate[indices], (mx4_f[indices], s1_lag[indices]), local_joint)
        prediction[indices], residual[indices], fold[indices] = local_pred, local_resid, folds
        use = indices[local_joint]
        y = candidate[use]
        r = local_resid[local_joint]
        pooled_target.append(y); pooled_resids.append(r)
        pooled_f.append(mx4_f[use]); pooled_lag.append(s1_lag[use])
        region_stats[region] = {
            "official_ids": int(len(indices)), "median_defined_ids": int(candidate_defined[indices].sum()),
            "pairwise_joint_defined_ids": int(local_joint.sum()),
            "joint_coverage_fraction": float(local_joint.sum() / len(indices)),
            "median_distribution": distribution(candidate[indices][candidate_defined[indices]]),
            "eligible_peer_count_distribution": distribution(peer_counts[indices][candidate_defined[indices]]),
            "candidate_pearson_r_with_Mx4_F": pearson(y, mx4_f[use]),
            "candidate_pearson_r_with_S1_lagged_Mx4_F": pearson(y, s1_lag[use]),
            "crossfit_residual_pearson_r_with_Mx4_F": pearson(r, mx4_f[use]),
            "crossfit_residual_pearson_r_with_S1_lagged_Mx4_F": pearson(r, s1_lag[use]),
            "folds": fold_reports,
        }
    target_all = np.concatenate(pooled_target)
    residual_all = np.concatenate(pooled_resids)
    f_all = np.concatenate(pooled_f)
    lag_all = np.concatenate(pooled_lag)
    source_variance = float(target_all.var(ddof=0))
    residual_variance = float(residual_all.var(ddof=0))
    variance_ratio = residual_variance / source_variance if source_variance > 0.0 else None
    r_mx4 = pearson(target_all, f_all)
    r_s1 = pearson(target_all, lag_all)
    coverage_n = int(candidate_defined.sum())
    coverage = coverage_n / len(official)
    joint_n = int(joint.sum())
    joint_coverage = joint_n / len(official)
    table = output_table(official, candidate, candidate_defined, peer_counts,
                         values[b_key], masks[b_key], mx4_f, mx4_f_defined,
                         s1_lag, s1_defined, fold, joint, prediction, residual)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("median sensitivity artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    before = json.loads(before_report_path.read_text(encoding="utf-8"))
    mean_report = json.loads(mean_report_path.read_text(encoding="utf-8"))
    if (before["measure_after"]["heldout_residual_variance_ratio"] != 0.006577271031853296
            or before["scope"]["jointly_defined_ids"] != 9347
            or mean_report["per_tract_artifact"]["rows"] != 9379):
        raise ValueError("published Mx4p mean baseline does not match its recorded measurement")
    after_hashes = {idea: sha256_file(workspace / "ideas" / f"{idea}.parquet") for idea in artifact_hashes}
    registry_sha_after = sha256_file(registry_path)
    frozen_unchanged = after_hashes == artifact_hashes and registry_sha_after == registry_sha_before
    if not frozen_unchanged:
        raise AssertionError("frozen Mx4/S1 artifacts or registry changed")
    all_candidate_finite_bounded = bool(np.isfinite(candidate[candidate_defined]).all()
                                        and np.all((candidate[candidate_defined] >= 0.0)
                                                   & (candidate[candidate_defined] <= 1.0)))
    acceptance = {
        "exact_9379_geoid_region_partition": table.num_rows == 9379 and len(official) == 9379,
        "coverage_at_least_98_59_percent": coverage >= MIN_COVERAGE,
        "values_finite_and_in_0_1": all_candidate_finite_bounded,
        "pooled_crossfit_residual_variance_ratio": variance_ratio,
        "residual_variance_at_least_10_percent": variance_ratio is not None and variance_ratio >= MIN_VARIANCE_RATIO,
        "pearson_r_Mx4_F": r_mx4,
        "abs_pearson_below_0_8_Mx4_F": r_mx4 is not None and abs(r_mx4) < MAX_ABS_R,
        "pearson_r_S1_lagged_Mx4_F": r_s1,
        "abs_pearson_below_0_8_S1_lagged_Mx4_F": r_s1 is not None and abs(r_s1) < MAX_ABS_R,
        "frozen_Mx4_S1_and_registry_unchanged": frozen_unchanged,
    }
    acceptance["acceptance_pass"] = all(value for key, value in acceptance.items()
                                         if key not in ("pooled_crossfit_residual_variance_ratio",
                                                        "pearson_r_Mx4_F", "pearson_r_S1_lagged_Mx4_F"))
    report = {
        "schema_version": 1,
        "audit": "Mx4 population-weighted leave-one-tract-out county median; label-free unregistered sensitivity screen",
        "method": {
            "source": "frozen registered Mx4 B covariate: provided Overture building Esri-only source share",
            "aggregation": "Within each (region, COUNTYFP), for each focal tract compute the lower population-weighted median of other eligible tracts' Mx4 B source shares; eligible donors have defined share and finite positive provided pop_total. Focal tract omitted from peers.",
            "weighted_median_definition": "Sort values ascending with stable mergesort and return the first value whose cumulative positive population weight is at least half the peer weight.",
            "crossfit": "Five region-local folds assigned by SHA-256 of mx4p-conditional-innovation-v1|region|GEOID modulo five. Each fold held out once; fit region-specific OLS with intercept, frozen Mx4 F and S1 lagged-Mx4 F; no labels, tuning or imputation.",
            "variance_ratio": "Pooled population variance (ddof=0) of held-out residuals divided by the candidate variance on the same jointly defined support.",
            "nulls": "Undefined if no eligible peer population remains; serialized null with peer_defined=false and excluded from all metrics.",
            "unregistered_sensitivity_only": True,
        },
        "scope": {
            "official_ids": len(official), "exact_geoid_region_partition": True,
            "candidate_defined_ids": coverage_n, "candidate_undefined_ids": len(official) - coverage_n,
            "candidate_coverage_fraction": coverage, "jointly_defined_for_innovation": joint_n,
            "joint_coverage_fraction": joint_coverage,
            "labels_read": False, "sample_score_values_read": False,
            "pending_policy_Tm1_Tm4_artifacts_read": False,
            "removed_or_equivalent_reference_data_read": False, "external_inputs_read": False,
            "leaderboard_feedback_used": False, "registry_integration": False,
            "frozen_Mx4_or_S1_modified": False, "uploads": 0,
        },
        "measure_before": {
            "population_weighted_mean_coverage": mean_report["scope"]["peer_defined"],
            "population_weighted_mean_coverage_fraction": mean_report["scope"]["peer_defined_coverage_fraction"],
            "population_weighted_mean_conditional_residual_variance_ratio": before["measure_after"]["heldout_residual_variance_ratio"],
            "population_weighted_mean_max_abs_r_before_conditional_innovation": before["measure_before"]["previous_max_abs_Pearson_r"],
            "frozen_Mx4_S1_unchanged": True,
        },
        "measure_after": {
            "candidate_distribution": distribution(candidate[candidate_defined]),
            "eligible_peer_count_distribution": distribution(peer_counts[candidate_defined]),
            "jointly_defined_ids": joint_n, "joint_coverage_fraction": joint_coverage,
            "candidate_variance_on_joint_support": source_variance,
            "pooled_crossfit_residual_variance": residual_variance,
            "pooled_crossfit_residual_variance_ratio": variance_ratio,
            "crossfit_residual_distribution": distribution(residual_all),
            "candidate_pearson_r_with_Mx4_F": r_mx4,
            "candidate_pearson_r_with_S1_lagged_Mx4_F": r_s1,
            "regions": region_stats,
        },
        "acceptance_thresholds": {"candidate_coverage_minimum": MIN_COVERAGE,
                                   "bounds_inclusive": [0.0, 1.0],
                                   "residual_variance_ratio_minimum": MIN_VARIANCE_RATIO,
                                   "absolute_pearson_correlation_maximum_exclusive": MAX_ABS_R},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": hashlib.sha256("\n".join(
                f"{g},{r}" for g, r in official).encode("utf-8")).hexdigest(),
            "frozen_input_artifacts": {idea: {"path": str(workspace / "ideas" / f"{idea}.parquet"),
                                               "sha256": artifact_hashes[idea],
                                               "sha256_after": after_hashes[idea]}
                                       for idea in artifact_hashes},
            "strata_inputs": {region: {"path": str(workspace / "defined-flags-inputs" /
                                                     f"{region}-strata-tract-table.parquet"),
                                       "sha256": digest, "columns_read": ["GEOID", "COUNTYFP", "pop_total"]}
                              for region, digest in strata_hashes.items()},
            "registry_sha256_before": registry_sha_before, "registry_sha256_after": registry_sha_after,
            "harness_report_sha256": sha256_file(harness_path),
            "mean_baseline_report_sha256": sha256_file(mean_report_path),
            "conditional_innovation_baseline_report_sha256": sha256_file(before_report_path),
            "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                "sha256": sha256_file(artifact_path), "columns": table.column_names,
                                "deterministic_bytes": True},
            "selected_emission_sha256": {
                "Mx4_B": feature_hash(official, values[b_key], masks[b_key]),
                "Mx4_F": feature_hash(official, mx4_f, mx4_f_defined),
                "S1_lagged_Mx4_F": feature_hash(official, s1_lag, s1_defined),
            },
            "script_sha256": sha256_file(Path(__file__).resolve()),
        },
        "interpretation": "All specified distinctness and coverage gates pass; retain only as a separately named unregistered sensitivity, not as an accuracy claim or automatic integration." if acceptance["acceptance_pass"] else
        "At least one specified coverage, residual-variance, or correlation gate fails; do not integrate or promote the median sensitivity and leave frozen Mx4/S1 unchanged.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/mx4-population-weighted-median.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "mx4-population-weighted-median" / "mx4-population-weighted-median.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"],
                      "coverage": report["scope"]["candidate_coverage_fraction"],
                      "candidate": report["measure_after"]["candidate_distribution"],
                      "artifact": report["provenance"]["output_artifact"],
                      "report": str(report_path.relative_to(ROOT))}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
