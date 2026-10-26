#!/usr/bin/env python3
"""Cross-fitted label-free innovation screen for unregistered Mx4p."""
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
MIN_VARIANCE_RATIO = 0.10
MAX_ABS_RESIDUAL_CORRELATION = 0.80
TARGET = ("Mx4p", "F", "covariate:population_weighted_peer_county_share")
PREDICTORS = (
    ("Mx4", "F", "covariate"),
    ("S1", "F", "covariate:lag_mx4_f_covariate"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def deterministic_folds(geoids: list[str], region: str, n_folds: int = N_FOLDS) -> np.ndarray:
    """Assign stable, region-local folds independently of feature values or row order."""
    if n_folds < 2 or region not in REGIONS:
        raise ValueError("fold assignment requires a known region and at least two folds")
    if len(set(geoids)) != len(geoids):
        raise ValueError("fold assignment received duplicate GEOIDs")
    codes = []
    for geoid in geoids:
        payload = f"mx4p-conditional-innovation-v1|{region}|{geoid}".encode("utf-8")
        codes.append(int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % n_folds)
    return np.asarray(codes, dtype=np.int8)


def pearson(left: np.ndarray, right: np.ndarray) -> float | None:
    x = np.asarray(left, dtype=np.float64)
    y = np.asarray(right, dtype=np.float64)
    if x.shape != y.shape or x.ndim != 1:
        raise ValueError("Pearson inputs must be aligned one-dimensional arrays")
    valid = np.isfinite(x) & np.isfinite(y)
    if int(valid.sum()) < 3:
        return None
    x, y = x[valid], y[valid]
    x, y = x - x.mean(), y - y.mean()
    denominator = float(np.sqrt(np.dot(x, x) * np.dot(y, y)))
    if denominator <= 0.0:
        return None
    value = float(np.dot(x, y) / denominator)
    if not np.isfinite(value):
        raise ValueError("defined Pearson correlation must be finite")
    return float(np.clip(value, -1.0, 1.0))


def distribution(values: np.ndarray) -> dict[str, float | int | None]:
    x = np.asarray(values, dtype=np.float64)
    x = x[np.isfinite(x)]
    if not x.size:
        return {"n": 0, "min": None, "p05": None, "p50": None, "p95": None,
                "max": None, "mean": None, "sd": None, "variance": None}
    return {"n": int(x.size), "min": float(x.min()),
            "p05": float(np.quantile(x, .05)), "p50": float(np.quantile(x, .5)),
            "p95": float(np.quantile(x, .95)), "max": float(x.max()),
            "mean": float(x.mean()), "sd": float(x.std(ddof=0)),
            "variance": float(x.var(ddof=0))}


def crossfit_region(geoids: list[str], region: str, target: np.ndarray,
                    predictor_1: np.ndarray, predictor_2: np.ndarray,
                    joint_defined: np.ndarray, n_folds: int = N_FOLDS
                    ) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], np.ndarray]:
    """Fit region-specific intercept+two-predictor OLS on four folds, predict the fifth."""
    y = np.asarray(target, dtype=np.float64)
    x1 = np.asarray(predictor_1, dtype=np.float64)
    x2 = np.asarray(predictor_2, dtype=np.float64)
    mask = np.asarray(joint_defined, dtype=bool)
    n = len(geoids)
    if any(vector.shape != (n,) for vector in (y, x1, x2, mask)):
        raise ValueError("cross-fit inputs must align with the region GEOID list")
    if np.any(mask & (~np.isfinite(y) | ~np.isfinite(x1) | ~np.isfinite(x2))):
        raise ValueError("jointly defined cross-fit rows must be finite")
    folds = deterministic_folds(geoids, region, n_folds)
    prediction = np.full(n, np.nan, dtype=np.float64)
    residual = np.full(n, np.nan, dtype=np.float64)
    fold_reports = []
    for fold in range(n_folds):
        test = mask & (folds == fold)
        train = mask & (folds != fold)
        n_test, n_train = int(test.sum()), int(train.sum())
        if n_test == 0 or n_train < 3:
            raise ValueError(f"{region} fold {fold}: insufficient jointly defined train/test rows")
        design_train = np.column_stack((np.ones(n_train), x1[train], x2[train]))
        design_test = np.column_stack((np.ones(n_test), x1[test], x2[test]))
        coefficients, _, rank, singular_values = np.linalg.lstsq(design_train, y[train], rcond=None)
        prediction[test] = design_test @ coefficients
        residual[test] = y[test] - prediction[test]
        fold_reports.append({
            "fold": fold,
            "train_joint_defined": n_train,
            "heldout_joint_defined": n_test,
            "coefficient_order": ["intercept", PREDICTORS[0][0] + "|" + PREDICTORS[0][1] + "|" + PREDICTORS[0][2],
                                  PREDICTORS[1][0] + "|" + PREDICTORS[1][1] + "|" + PREDICTORS[1][2]],
            "coefficients": [float(value) for value in coefficients],
            "design_rank": int(rank),
            "design_columns": int(design_train.shape[1]),
            "minimum_singular_value": float(np.min(singular_values)) if singular_values.size else None,
            "maximum_singular_value": float(np.max(singular_values)) if singular_values.size else None,
            "heldout_residual_mean": float(residual[test].mean()),
            "heldout_residual_variance": float(residual[test].var(ddof=0)),
        })
    if np.any(mask & (~np.isfinite(prediction) | ~np.isfinite(residual))) or np.any(~mask & np.isfinite(residual)):
        raise AssertionError("cross-fitting must produce exactly one finite held-out residual per complete row")
    return prediction, residual, fold_reports, folds


def read_emission(path: Path, expected_sha: str | None, idea: str, component: str, channel: str,
                  official: list[tuple[str, str]]) -> tuple[np.ndarray, np.ndarray, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = sha256_file(path)
    if expected_sha is not None and digest != expected_sha:
        raise ValueError(f"{idea} artifact hash differs from its frozen harness digest")
    # The filters ensure that only the declared emission is materialized for analysis.
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "component", "channel", "value", "defined"],
                          filters=[("idea", "=", idea), ("component", "=", component),
                                   ("channel", "=", channel)])
    if table.num_rows != len(official):
        raise ValueError(f"{idea}|{component}|{channel}: expected one row for each official GEOID")
    positions = {pair: index for index, pair in enumerate(official)}
    values = np.full(len(official), np.nan, dtype=np.float64)
    defined = np.zeros(len(official), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in positions or pair in seen:
            raise ValueError(f"{idea}|{component}|{channel}: unexpected or duplicate official ID {pair}")
        if row["idea"] != idea or row["component"] != component or row["channel"] != channel:
            raise ValueError("filtered feature metadata does not match requested emission")
        seen.add(pair)
        if bool(row["defined"]):
            value = float(row["value"])
            if not np.isfinite(value):
                raise ValueError(f"{idea}|{component}|{channel}: defined value is nonfinite")
            values[positions[pair]] = value
            defined[positions[pair]] = True
    if seen != set(official):
        raise ValueError(f"{idea}|{component}|{channel}: GEOID/region partition differs from official scope")
    return values, defined, digest


def output_table(geoids: list[str], regions: list[str], fold: np.ndarray,
                 feature_values: dict[str, np.ndarray], feature_defined: dict[str, np.ndarray],
                 jointly_defined: np.ndarray, prediction: np.ndarray, residual: np.ndarray) -> pa.Table:
    n = len(geoids)
    if len(regions) != n or any(np.asarray(x).shape != (n,) for x in
                                [fold, jointly_defined, prediction, residual, *feature_values.values(),
                                 *feature_defined.values()]):
        raise ValueError("output artifact arrays do not align with official GEOIDs")
    if np.any(jointly_defined & (~np.isfinite(prediction) | ~np.isfinite(residual))) \
            or np.any(~jointly_defined & (np.isfinite(prediction) | np.isfinite(residual))):
        raise ValueError("prediction/residual null masks must equal the joint-defined mask")
    columns: dict[str, Any] = {
        "GEOID": pa.array(geoids, type=pa.string()),
        "region": pa.array(regions, type=pa.string()),
        "fold": pa.array(fold.astype(np.int8), type=pa.int8()),
        "jointly_defined": pa.array(jointly_defined, type=pa.bool_()),
        "crossfit_prediction": pa.array([float(x) if ok else None for x, ok in zip(prediction, jointly_defined)],
                                         type=pa.float64()),
        "crossfit_residual": pa.array([float(x) if ok else None for x, ok in zip(residual, jointly_defined)],
                                       type=pa.float64()),
    }
    for feature_key, values in feature_values.items():
        name = feature_key.lower().replace("|", "_").replace(":", "_")
        mask = feature_defined[feature_key]
        columns[name] = pa.array([float(x) if ok else None for x, ok in zip(values, mask)], type=pa.float64())
        columns[name + "_defined"] = pa.array(mask, type=pa.bool_())
    return pa.table(columns)


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def run(workspace: Path, report_path: Path, residual_path: Path) -> dict[str, Any]:
    registry_path = ROOT / "config/ideas.json"
    harness_path = ROOT / "reports/ideas-harness.json"
    registry_sha256_before = sha256_file(registry_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if any(item.get("id") == "Mx4p" for item in registry["ideas"]):
        raise ValueError("Mx4p must remain unregistered throughout this diagnostic")
    registered = {item["id"] for item in registry["ideas"] if item.get("status") == "implemented"}
    if not {"Mx4", "S1"}.issubset(registered):
        raise ValueError("Mx4 and S1 must be registered input ideas")
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    official = official_scored_pairs(workspace)
    if len(official) != 9379 or len(set(official)) != 9379 or len({geoid for geoid, _ in official}) != 9379:
        raise ValueError("official GEOID/region pairs must be an exact 9,379-ID partition")
    geoids = [geoid for geoid, _ in official]
    regions = [region for _, region in official]
    if set(regions) != set(REGIONS):
        raise ValueError("unexpected region in official scored partition")
    feature_values: dict[str, np.ndarray] = {}
    feature_defined: dict[str, np.ndarray] = {}
    artifact_hashes: dict[str, str] = {}
    feature_specs = {"Mx4p|F|covariate:population_weighted_peer_county_share": TARGET,
                     "Mx4|F|covariate": PREDICTORS[0],
                     "S1|F|covariate:lag_mx4_f_covariate": PREDICTORS[1]}
    for key, (idea, component, channel) in feature_specs.items():
        path = workspace / "ideas" / f"{idea}.parquet"
        expected_hash = harness.get("per_idea", {}).get(idea, {}).get("sha256") if idea != "Mx4p" else None
        values, defined, digest = read_emission(path, expected_hash, idea, component, channel, official)
        feature_values[key], feature_defined[key], artifact_hashes[idea] = values, defined, digest
    mx4p_values = feature_values["Mx4p|F|covariate:population_weighted_peer_county_share"]
    mx4p_mask = feature_defined["Mx4p|F|covariate:population_weighted_peer_county_share"]
    if np.any(mx4p_mask & ((mx4p_values < 0.0) | (mx4p_values > 1.0))):
        raise ValueError("defined Mx4p values must be in [0,1]")
    for idea_id in ("Mx4", "S1"):
        if not harness.get("per_idea", {}).get(idea_id, {}).get("geoid_partition_exact"):
            raise ValueError(f"harness does not certify {idea_id}'s exact official ID partition")
    predictor_1 = feature_values["Mx4|F|covariate"]
    predictor_2 = feature_values["S1|F|covariate:lag_mx4_f_covariate"]
    pred_mask_1 = feature_defined["Mx4|F|covariate"]
    pred_mask_2 = feature_defined["S1|F|covariate:lag_mx4_f_covariate"]
    joint = mx4p_mask & pred_mask_1 & pred_mask_2
    if joint.sum() <= 0:
        raise ValueError("no jointly defined IDs for conditional innovation screen")

    predictions = np.full(len(geoids), np.nan, dtype=np.float64)
    residuals = np.full(len(geoids), np.nan, dtype=np.float64)
    fold_ids = np.full(len(geoids), -1, dtype=np.int8)
    region_summaries: dict[str, Any] = {}
    for region in REGIONS:
        region_indices = np.flatnonzero(np.asarray(regions) == region)
        local_ids = [geoids[index] for index in region_indices]
        local_joint = joint[region_indices]
        local_fold = deterministic_folds(local_ids, region)
        local_prediction, local_residual, folds, _ = crossfit_region(
            local_ids, region, mx4p_values[region_indices], predictor_1[region_indices],
            predictor_2[region_indices], local_joint)
        predictions[region_indices], residuals[region_indices] = local_prediction, local_residual
        fold_ids[region_indices] = local_fold
        n_joint = int(local_joint.sum())
        original = mx4p_values[region_indices][local_joint]
        heldout_residual = local_residual[local_joint]
        original_variance = float(original.var(ddof=0))
        residual_variance = float(heldout_residual.var(ddof=0))
        region_summaries[region] = {
            "official_ids": len(region_indices),
            "Mx4p_defined_ids": int(mx4p_mask[region_indices].sum()),
            "Mx4_F_defined_ids": int(pred_mask_1[region_indices].sum()),
            "S1_lagged_Mx4_F_defined_ids": int(pred_mask_2[region_indices].sum()),
            "jointly_defined_ids": n_joint,
            "joint_undefined_ids": int(len(region_indices) - n_joint),
            "folds": folds,
            "original_Mx4p_distribution_on_joint_support": distribution(original),
            "crossfit_residual_distribution": distribution(heldout_residual),
            "heldout_residual_variance_ratio": (residual_variance / original_variance
                                                  if original_variance > 0.0 else None),
            "residual_pearson_r_with_Mx4_F": pearson(heldout_residual, predictor_1[region_indices][local_joint]),
            "residual_pearson_r_with_S1_lagged_Mx4_F": pearson(heldout_residual, predictor_2[region_indices][local_joint]),
        }

    valid_residuals = residuals[joint]
    original_all = mx4p_values[joint]
    original_variance = float(original_all.var(ddof=0))
    residual_variance = float(valid_residuals.var(ddof=0))
    residual_variance_ratio = residual_variance / original_variance if original_variance > 0.0 else None
    overall_correlations = {
        "Mx4_F": pearson(valid_residuals, predictor_1[joint]),
        "S1_lagged_Mx4_F": pearson(valid_residuals, predictor_2[joint]),
    }
    joint_by_region = {region: int(region_summaries[region]["jointly_defined_ids"]) for region in REGIONS}
    feature_coverage = {key: int(mask.sum()) for key, mask in feature_defined.items()}
    if int(mx4p_mask.sum()) != 9347:
        raise ValueError(f"Mx4p coverage differs from frozen baseline: {int(mx4p_mask.sum())}/9379")
    distinct = residual_variance_ratio is not None and residual_variance_ratio >= MIN_VARIANCE_RATIO
    distinct = distinct and all(value is not None and abs(value) < MAX_ABS_RESIDUAL_CORRELATION
                                for value in overall_correlations.values())
    registry_sha256_after = sha256_file(registry_path)
    frozen_hashes_after = {idea: sha256_file(workspace / "ideas" / f"{idea}.parquet")
                           for idea in ("Mx4", "S1")}
    registry_unchanged = registry_sha256_after == registry_sha256_before
    frozen_artifacts_unchanged = all(frozen_hashes_after[idea] == artifact_hashes[idea]
                                     for idea in ("Mx4", "S1"))
    if not registry_unchanged or not frozen_artifacts_unchanged:
        raise AssertionError("registry or frozen Mx4/S1 changed during read-only innovation audit")

    table = output_table(geoids, regions, fold_ids, feature_values, feature_defined,
                         joint, predictions, residuals)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("cross-fit residual Parquet serialization is not deterministic")
    residual_path.parent.mkdir(parents=True, exist_ok=True)
    residual_path.write_bytes(payload)
    report = {
        "schema_version": 1,
        "audit": "Five-fold within-region cross-fitted linear Mx4p innovation screen; label-free diagnostic, not promotion",
        "method": {
            "response": TARGET[0] + "|" + TARGET[1] + "|" + TARGET[2],
            "predictors": ["|".join(PREDICTORS[0]), "|".join(PREDICTORS[1])],
            "residualizer": "Ordinary least squares with intercept and both fixed predictors; np.linalg.lstsq (minimum-norm solution if rank deficient); fit separately by region and fold, with no feature selection, tuning, regularization, or labels.",
            "cross_fitting": "Five region-local folds; each fold is assigned by SHA-256 of the versioned region+GEOID string modulo five, independently of feature values. Train on the other four folds, predict the held-out fold. Only jointly defined finite Mx4p/Mx4/S1-lag rows enter either fit or metric; no imputation.",
            "variance_ratio": "Population variance (ddof=0) of pooled held-out cross-fit residuals divided by population variance of Mx4p on the same complete-case support.",
            "correlation_gate": "Distinct-innovation criterion uses pooled held-out residual Pearson r with each predictor; report regional correlations descriptively.",
            "fold_count": N_FOLDS,
        },
        "scope": {
            "official_ids": len(official), "exact_9379_GEOID_region_partition": True,
            "official_ids_by_region": {region: regions.count(region) for region in REGIONS},
            "feature_defined_ids": feature_coverage,
            "jointly_defined_ids": int(joint.sum()),
            "jointly_undefined_ids": len(official) - int(joint.sum()),
            "jointly_defined_by_region": joint_by_region,
            "labels_read": False, "sample_score_values_read": False,
            "external_or_removed_reference_inputs_read": False,
            "frozen_Mx4_or_S1_unchanged": frozen_artifacts_unchanged,
            "Mx4p_registered_or_promoted": False,
            "uploads": 0,
        },
        "measure_before": {
            "Mx4p_defined_ids": 9347, "official_ids": 9379,
            "previous_max_abs_Pearson_r": 0.99553044,
            "previous_residual_variance_ratio": None,
        },
        "measure_after": {
            "original_Mx4p_variance_on_joint_support": original_variance,
            "heldout_residual_variance": residual_variance,
            "heldout_residual_variance_ratio": residual_variance_ratio,
            "original_Mx4p_distribution_on_joint_support": distribution(original_all),
            "heldout_residual_distribution": distribution(valid_residuals),
            "pooled_heldout_residual_correlations": overall_correlations,
            "regions": region_summaries,
        },
        "acceptance": {
            "exact_official_id_partition": len(official) == 9379,
            "registry_unchanged_and_Mx4p_unregistered": registry_unchanged and not any(
                item.get("id") == "Mx4p" for item in registry["ideas"]),
            "frozen_Mx4_and_S1_artifacts_unchanged": frozen_artifacts_unchanged,
            "all_jointly_defined_ids_have_out_of_fold_predictions": int(np.isfinite(residuals).sum()) == int(joint.sum()),
            "residual_variance_ratio_at_least_0_10": residual_variance_ratio is not None
                and residual_variance_ratio >= MIN_VARIANCE_RATIO,
            "pooled_residual_abs_pearson_below_0_8_with_both_predictors": all(
                value is not None and abs(value) < MAX_ABS_RESIDUAL_CORRELATION
                for value in overall_correlations.values()),
            "regional_residual_correlations_reported_descriptively": True,
            "distinct_innovation_flag": bool(distinct),
            "variance_ratio_threshold": MIN_VARIANCE_RATIO,
            "residual_abs_pearson_threshold": MAX_ABS_RESIDUAL_CORRELATION,
            "interpretation": "Residual signal meets the preregistered distinct-innovation screen; this remains diagnostic only and Mx4p stays unregistered."
            if distinct else "Residual innovation fails at least one preregistered threshold; treat Mx4p as redundant and retire it from consideration, without changing registered Mx4/S1.",
        },
        "provenance": {
            "official_geoid_region_sha256": hashlib.sha256(
                "\n".join(f"{geoid},{region}" for geoid, region in official).encode("utf-8")).hexdigest(),
            "input_artifacts": {idea: {"path": str(workspace / "ideas" / f"{idea}.parquet"),
                                       "sha256": digest} for idea, digest in artifact_hashes.items()},
            "harness_report_sha256": sha256_file(harness_path),
            "registry_sha256_before": registry_sha256_before,
            "registry_sha256_after": registry_sha256_after,
            "registry_unchanged": registry_unchanged,
            "registry_contains_Mx4p": False,
            "frozen_Mx4_S1_sha256_after": frozen_hashes_after,
            "script_sha256": sha256_file(Path(__file__).resolve()),
        },
        "residual_artifact": {"path": str(residual_path), "rows": table.num_rows,
                              "sha256": hashlib.sha256(payload).hexdigest(),
                              "columns": table.column_names, "deterministic_bytes": True},
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                           encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/mx4p-conditional-innovation.json")
    parser.add_argument("--residual-artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    residual_path = Path(args.residual_artifact) if args.residual_artifact else \
        workspace / "mx4p-conditional-innovation" / "mx4p-crossfit-residuals.parquet"
    report = run(workspace, report_path, residual_path)
    print(json.dumps({"report": str(report_path.relative_to(ROOT)),
                      "jointly_defined_ids": report["scope"]["jointly_defined_ids"],
                      "residual_variance_ratio": report["measure_after"]["heldout_residual_variance_ratio"],
                      "pooled_residual_correlations": report["measure_after"]["pooled_heldout_residual_correlations"],
                      "distinct_innovation_flag": report["acceptance"]["distinct_innovation_flag"],
                      "residual_artifact": report["residual_artifact"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
