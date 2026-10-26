#!/usr/bin/env python3
"""Measure scale-sensitive disagreement between completed Queen and Rook S1 innovations."""
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
from audit_s1_mx4_lagged_innovation import measure_region_nmi  # noqa: E402
from audit_mx4p_conditional_innovation import deterministic_folds  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_N = 9379
N_FOLDS = 5
MIN_COVERAGE = 0.9859
MIN_RESIDUAL_RATIO = 0.10
MAX_REGION_NMI = 0.80
MAX_ABS_DISAGREEMENT = 2.0
QUEEN_BASELINE_NMI = 0.7486023400935238
QUEEN_BASELINE_RATIO = 0.1764466342822903
ROOK_BASELINE_NMI = 0.7417147567330962
ROOK_BASELINE_RATIO = 0.17446774950974947
MX4_SHA = "8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af"
S1_SHA = "632829cf29281aa16487707cb555f52b4579cb0edfc0dd3faae360fa8f1dda03"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pearson(left: np.ndarray, right: np.ndarray) -> float | None:
    x, y = np.asarray(left, dtype=np.float64), np.asarray(right, dtype=np.float64)
    if x.shape != y.shape or x.ndim != 1:
        raise ValueError("Pearson inputs must be aligned vectors")
    valid = np.isfinite(x) & np.isfinite(y)
    if int(valid.sum()) < 3:
        return None
    x, y = x[valid], y[valid]
    x, y = x - x.mean(), y - y.mean()
    denom = float(np.sqrt(np.dot(x, x) * np.dot(y, y)))
    return None if denom <= 0 else float(np.clip(np.dot(x, y) / denom, -1.0, 1.0))


def distribution(values: np.ndarray) -> dict[str, float | int | None]:
    x = np.asarray(values, dtype=np.float64)
    x = x[np.isfinite(x)]
    if not x.size:
        return {"n": 0, "min": None, "p05": None, "p50": None, "p95": None,
                "max": None, "mean": None, "variance": None}
    return {"n": int(x.size), "min": float(x.min()),
            "p05": float(np.quantile(x, .05)), "p50": float(np.quantile(x, .5)),
            "p95": float(np.quantile(x, .95)), "max": float(x.max()),
            "mean": float(x.mean()), "variance": float(x.var(ddof=0))}


def absolute_disagreement(queen: np.ndarray, queen_defined: np.ndarray,
                          rook: np.ndarray, rook_defined: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    q, r = np.asarray(queen, dtype=np.float64), np.asarray(rook, dtype=np.float64)
    qm, rm = np.asarray(queen_defined, dtype=bool), np.asarray(rook_defined, dtype=bool)
    if q.ndim != 1 or any(v.shape != q.shape for v in (r, qm, rm)):
        raise ValueError("Queen/Rook values and masks must be aligned vectors")
    if np.any(qm & ~np.isfinite(q)) or np.any(rm & ~np.isfinite(r)):
        raise ValueError("defined source innovations must be finite")
    if np.any(~qm & np.isfinite(q)) or np.any(~rm & np.isfinite(r)):
        raise ValueError("undefined source innovations must remain null")
    defined = qm & rm
    disagreement = np.full(q.shape, np.nan, dtype=np.float64)
    disagreement[defined] = np.abs(q[defined] - r[defined])
    if np.any(defined & (disagreement > MAX_ABS_DISAGREEMENT)):
        raise ValueError("absolute disagreement must be bounded [0,2]")
    return disagreement, defined


def crossfit_region(geoids: list[str], region: str, target: np.ndarray,
                    mx4: np.ndarray, queen: np.ndarray, rook: np.ndarray,
                    joint: np.ndarray, n_folds: int = N_FOLDS
                    ) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], np.ndarray]:
    """Five-fold region-local OLS residualization on Mx4 F and both source innovations."""
    y, x0, x1, x2 = (np.asarray(v, dtype=np.float64) for v in (target, mx4, queen, rook))
    mask = np.asarray(joint, dtype=bool)
    n = len(geoids)
    if any(v.shape != (n,) for v in (y, x0, x1, x2, mask)):
        raise ValueError("crossfit inputs must align with region GEOIDs")
    if np.any(mask & (~np.isfinite(y) | ~np.isfinite(x0) | ~np.isfinite(x1) | ~np.isfinite(x2))):
        raise ValueError("jointly defined crossfit rows must be finite")
    fold_ids = deterministic_folds(geoids, region, n_folds)
    predictions = np.full(n, np.nan)
    residuals = np.full(n, np.nan)
    reports: list[dict[str, Any]] = []
    for fold in range(n_folds):
        test = mask & (fold_ids == fold)
        train = mask & (fold_ids != fold)
        if not test.any() or int(train.sum()) < 5:
            raise ValueError(f"{region} fold {fold}: insufficient train/test support")
        design_train = np.column_stack((np.ones(int(train.sum())), x0[train], x1[train], x2[train]))
        design_test = np.column_stack((np.ones(int(test.sum())), x0[test], x1[test], x2[test]))
        coef, _, rank, singular = np.linalg.lstsq(design_train, y[train], rcond=None)
        predictions[test] = design_test @ coef
        residuals[test] = y[test] - predictions[test]
        reports.append({"fold": fold, "train_rows": int(train.sum()), "heldout_rows": int(test.sum()),
                        "design_rank": int(rank), "design_columns": int(design_train.shape[1]),
                        "coefficients_intercept_mx4_queen_rook": [float(x) for x in coef],
                        "heldout_residual_variance": float(residuals[test].var(ddof=0)),
                        "minimum_singular_value": float(singular.min()) if singular.size else None})
    if np.any(mask & (~np.isfinite(predictions) | ~np.isfinite(residuals))) or np.any(~mask & np.isfinite(residuals)):
        raise AssertionError("cross-fit outputs must preserve exact joint definedness")
    return predictions, residuals, reports, fold_ids


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def load_feature(path: Path, expected_sha: str, official: list[tuple[str, str]],
                 value_column: str, defined_column: str) -> tuple[np.ndarray, np.ndarray]:
    if not path.is_file() or sha256_file(path) != expected_sha:
        raise ValueError(f"frozen source artifact missing or hash mismatch: {path}")
    table = pq.read_table(path, columns=["GEOID", "region", value_column, defined_column])
    if table.num_rows != len(official):
        raise ValueError(f"{path.name}: expected one row per official GEOID")
    positions = {pair: i for i, pair in enumerate(official)}
    values = np.full(len(official), np.nan, dtype=np.float64)
    defined = np.zeros(len(official), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in positions or pair in seen:
            raise ValueError(f"unexpected or duplicate source row {pair}")
        seen.add(pair)
        flag, raw = bool(row[defined_column]), row[value_column]
        if flag:
            value = float(raw) if raw is not None else np.nan
            if not np.isfinite(value):
                raise ValueError(f"defined source value is null/nonfinite for {pair[0]}")
            values[positions[pair]] = value
            defined[positions[pair]] = True
        elif raw is not None:
            raise ValueError(f"undefined source value must remain null for {pair[0]}")
    if seen != set(official):
        raise ValueError(f"{path.name}: source partition differs from official IDs")
    return values, defined


def output_table(official: list[tuple[str, str]], mx4: np.ndarray, mx4_defined: np.ndarray,
                 queen: np.ndarray, queen_defined: np.ndarray, rook: np.ndarray,
                 rook_defined: np.ndarray, disagreement: np.ndarray, defined: np.ndarray,
                 prediction: np.ndarray, residual: np.ndarray, folds: np.ndarray) -> pa.Table:
    n = len(official)
    vectors = (mx4, mx4_defined, queen, queen_defined, rook, rook_defined,
               disagreement, defined, prediction, residual, folds)
    if any(np.asarray(v).shape != (n,) for v in vectors):
        raise ValueError("output vectors must align to exact official IDs")
    if np.any(defined & (~np.isfinite(disagreement) | (disagreement < 0) | (disagreement > MAX_ABS_DISAGREEMENT))):
        raise ValueError("defined absolute disagreement must be finite and bounded [0,2]")
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(mx4, mx4_defined)], type=pa.float64()),
        "Mx4_F_defined": pa.array(mx4_defined, type=pa.bool_()),
        "Queen_innovation": pa.array([float(v) if ok else None for v, ok in zip(queen, queen_defined)], type=pa.float64()),
        "Queen_innovation_defined": pa.array(queen_defined, type=pa.bool_()),
        "Rook_innovation": pa.array([float(v) if ok else None for v, ok in zip(rook, rook_defined)], type=pa.float64()),
        "Rook_innovation_defined": pa.array(rook_defined, type=pa.bool_()),
        "absolute_Queen_Rook_disagreement": pa.array([float(v) if ok else None for v, ok in zip(disagreement, defined)], type=pa.float64()),
        "disagreement_defined": pa.array(defined, type=pa.bool_()),
        "crossfit_joint_defined": pa.array(defined & mx4_defined, type=pa.bool_()),
        "fold": pa.array(folds, type=pa.int8()),
        "crossfit_prediction": pa.array([float(v) if ok else None for v, ok in zip(prediction, defined)], type=pa.float64()),
        "crossfit_residual": pa.array([float(v) if ok else None for v, ok in zip(residual, defined)], type=pa.float64()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    queen_report_path = ROOT / "reports/s1-mx4-lagged-innovation.json"
    rook_report_path = ROOT / "reports/s1-rook-mx4-innovation.json"
    queen_report = json.loads(queen_report_path.read_text(encoding="utf-8"))
    rook_report = json.loads(rook_report_path.read_text(encoding="utf-8"))
    if (not np.isclose(queen_report["measure_after"]["maximum_within_region_NMI"], QUEEN_BASELINE_NMI, rtol=0, atol=1e-12)
            or not np.isclose(queen_report["measure_after"]["pooled_remaining_variance_ratio"], QUEEN_BASELINE_RATIO, rtol=0, atol=1e-12)
            or not np.isclose(rook_report["measure_after"]["rook_maximum_within_region_NMI"], ROOK_BASELINE_NMI, rtol=0, atol=1e-12)
            or not np.isclose(rook_report["measure_after"]["pooled_rook_remaining_variance_ratio"], ROOK_BASELINE_RATIO, rtol=0, atol=1e-12)):
        raise ValueError("completed Queen/Rook baseline measures differ from the issued values")
    if queen_report["scope"]["innovation_defined_ids"] != EXPECTED_N or rook_report["scope"]["rook_innovation_defined_ids"] != EXPECTED_N:
        raise ValueError("Queen and Rook source screens must each define all official IDs")
    queen_artifact = workspace / "s1-mx4-lagged-innovation" / "s1-mx4-lagged-innovation.parquet"
    rook_artifact = workspace / "s1-rook-mx4-innovation" / "s1-rook-mx4-innovation.parquet"
    queen_sha = queen_report["output_artifact"]["sha256"]
    rook_sha = rook_report["output_artifact"]["sha256"]
    if sha256_file(queen_artifact) != queen_sha or sha256_file(rook_artifact) != rook_sha:
        raise ValueError("Queen/Rook innovation artifact hashes differ from completed audit reports")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N or len({g for g, _ in official}) != EXPECTED_N:
        raise ValueError("official IDs must form an exact 9,379 GEOID-region partition")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    if official_sha != queen_report["provenance"]["official_geoid_region_sha256"] or official_sha != rook_report["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("Queen/Rook source partitions differ from official GEOIDs")

    queen, queen_defined = load_feature(queen_artifact, queen_sha, official, "innovation", "innovation_defined")
    rook, rook_defined = load_feature(rook_artifact, rook_sha, official, "rook_innovation", "rook_innovation_defined")
    mx4, mx4_defined = load_feature(queen_artifact, queen_sha, official, "Mx4_F", "Mx4_F_defined")
    rook_mx4, rook_mx4_defined = load_feature(rook_artifact, rook_sha, official, "Mx4_F", "Mx4_F_defined")
    if not np.array_equal(mx4_defined, rook_mx4_defined) or not np.array_equal(mx4[mx4_defined], rook_mx4[rook_mx4_defined]):
        raise ValueError("Queen and Rook artifacts disagree on the frozen Mx4 F predictor")
    if np.any(mx4_defined & ((mx4 < 0.0) | (mx4 > 1.0))):
        raise ValueError("defined frozen Mx4 F must be in [0,1]")
    disagreement, joint = absolute_disagreement(queen, queen_defined, rook, rook_defined)
    if np.any(joint & (disagreement < 0.0)):
        raise ValueError("absolute Queen/Rook disagreement cannot be negative")

    registry_path = ROOT / "config/ideas.json"
    registry_before = sha256_file(registry_path)
    mx4_path, s1_path = workspace / "ideas/Mx4.parquet", workspace / "ideas/S1.parquet"
    if sha256_file(mx4_path) != MX4_SHA or sha256_file(s1_path) != S1_SHA:
        raise ValueError("frozen Mx4/S1 artifacts differ from completed source audit hashes")
    predictions = np.full(len(official), np.nan)
    residuals = np.full(len(official), np.nan)
    folds = np.full(len(official), -1, dtype=np.int8)
    region_report: dict[str, Any] = {}
    nmi_values: dict[str, float] = {}
    for region in REGIONS:
        idx = np.asarray([i for i, (_, r) in enumerate(official) if r == region], dtype=np.int64)
        ids = [official[i][0] for i in idx]
        local_joint = joint[idx] & mx4_defined[idx]
        pred, resid, fold_rows, local_folds = crossfit_region(
            ids, region, disagreement[idx], mx4[idx], queen[idx], rook[idx], local_joint)
        predictions[idx], residuals[idx], folds[idx] = pred, resid, local_folds
        nmi = measure_region_nmi(mx4[idx], mx4_defined[idx], disagreement[idx], joint[idx])
        if nmi["nmi"] is None:
            raise ValueError(f"{region}: insufficient NMI overlap")
        nmi_values[region] = float(nmi["nmi"])
        local = disagreement[idx][local_joint]
        region_report[region] = {
            "official_ids": len(idx), "queen_innovation_defined_ids": int(queen_defined[idx].sum()),
            "rook_innovation_defined_ids": int(rook_defined[idx].sum()),
            "disagreement_defined_ids": int(joint[idx].sum()),
            "crossfit_joint_defined_ids": int(local_joint.sum()),
            "coverage_fraction": float(joint[idx].mean()),
            "disagreement_distribution": distribution(disagreement[idx][joint[idx]]),
            "maximum_within_region_NMI_vs_Mx4_F": nmi,
            "crossfit_residual_variance_ratio": (float(resid[local_joint].var(ddof=0) / local.var(ddof=0))
                                                  if local.size and float(local.var(ddof=0)) > 0 else None),
            "pearson_r_disagreement_vs_Mx4_F": pearson(disagreement[idx][local_joint], mx4[idx][local_joint]),
            "folds": fold_rows,
        }
    complete = joint & mx4_defined
    candidate = disagreement[complete]
    residual = residuals[complete]
    candidate_var = float(candidate.var(ddof=0)) if candidate.size else 0.0
    residual_var = float(residual.var(ddof=0)) if residual.size else 0.0
    ratio = residual_var / candidate_var if candidate_var > 0 else None
    coverage = float(joint.mean())
    max_nmi_region = max(REGIONS, key=lambda region: nmi_values[region])

    registry_after = sha256_file(registry_path)
    mx4_after, s1_after = sha256_file(mx4_path), sha256_file(s1_path)
    queen_after, rook_after = sha256_file(queen_artifact), sha256_file(rook_artifact)
    frozen_unchanged = (registry_before == registry_after and mx4_after == MX4_SHA and s1_after == S1_SHA
                        and queen_after == queen_sha and rook_after == rook_sha)
    table = output_table(official, mx4, mx4_defined, queen, queen_defined, rook, rook_defined,
                         disagreement, joint, predictions, residuals, folds)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("disagreement artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    bounded = bool(np.isfinite(disagreement[joint]).all()
                   and np.all((disagreement[joint] >= 0.0) & (disagreement[joint] <= MAX_ABS_DISAGREEMENT)))
    max_nmi = nmi_values[max_nmi_region]
    acceptance = {
        "exact_9379_official_geoid_region_partition": len(official) == EXPECTED_N and table.num_rows == EXPECTED_N,
        "queen_rook_alignment_and_null_semantics_preserved": int(np.sum(queen_defined != rook_defined)) == 0,
        "disagreement_finite_and_bounded_0_2": bounded,
        "coverage_fraction": coverage,
        "coverage_at_least_98_59_percent": coverage >= MIN_COVERAGE,
        "maximum_region_NMI_below_0_80": max_nmi < MAX_REGION_NMI,
        "crossfit_residual_variance_ratio": ratio,
        "residual_variance_at_least_10_percent": ratio is not None and ratio >= MIN_RESIDUAL_RATIO,
        "frozen_Mx4_S1_registry_and_source_diagnostics_unchanged": frozen_unchanged,
        "distinct_disagreement_screen_pass": bool(coverage >= MIN_COVERAGE and max_nmi < MAX_REGION_NMI
                                                   and ratio is not None and ratio >= MIN_RESIDUAL_RATIO
                                                   and bounded and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "audit": "Absolute Queen/Rook S1 Mx4 innovation disagreement; unregistered label-free feature-combination screen",
        "method": {
            "candidate": "For each official GEOID with both completed innovation diagnostics defined, absolute difference |Queen innovation − Rook innovation|. No recomputation of either source diagnostic; null unless both are defined.",
            "NMI": "Within-region arithmetic normalized mutual information against frozen Mx4 F, using the same deterministic encoders as the completed S1 nonlinear screen.",
            "crossfit": "Five region-local deterministic SHA-256 folds. For each fold fit OLS with intercept, frozen Mx4 F, Queen innovation, and Rook innovation on other folds; predict held-out absolute disagreement. No feature selection, tuning, labels, or imputation.",
            "residual_variance_ratio": "Pooled population variance (ddof=0) of held-out disagreement residuals divided by disagreement variance on the same candidate/Mx4-complete support; regional values are descriptive.",
            "boundedness": "Source innovations are differences between shares in [0,1], hence each is in [-1,1] and absolute disagreement is in [0,2].",
        },
        "measure_before": {
            "queen_maximum_region_NMI": QUEEN_BASELINE_NMI,
            "queen_residual_variance_ratio": QUEEN_BASELINE_RATIO,
            "rook_maximum_region_NMI": ROOK_BASELINE_NMI,
            "rook_residual_variance_ratio": ROOK_BASELINE_RATIO,
            "no_disagreement_feature_previously_measured": True,
        },
        "scope": {
            "official_ids": len(official), "exact_official_geoid_region_partition": True,
            "queen_defined_ids": int(queen_defined.sum()), "rook_defined_ids": int(rook_defined.sum()),
            "disagreement_defined_ids": int(joint.sum()), "disagreement_undefined_ids": int(len(official) - joint.sum()),
            "coverage_fraction": coverage,
            "labels_read": False, "sample_score_values_read": False,
            "removed_or_equivalent_reference_data_read": False, "external_inputs_read": False,
            "leaderboard_feedback_used": False, "frozen_Mx4_S1_modified": False,
            "registry_integration": False, "uploads": 0,
        },
        "measure_after": {
            "disagreement_distribution": distribution(disagreement[joint]),
            "pooled_disagreement_variance": candidate_var,
            "pooled_crossfit_residual_variance": residual_var,
            "pooled_crossfit_residual_variance_ratio": ratio,
            "maximum_within_region_NMI": max_nmi,
            "region_with_maximum_NMI": max_nmi_region,
            "regions": region_report,
        },
        "thresholds": {"minimum_coverage": MIN_COVERAGE, "maximum_region_NMI_exclusive": MAX_REGION_NMI,
                       "minimum_crossfit_residual_variance_ratio": MIN_RESIDUAL_RATIO,
                       "disagreement_bounds_inclusive": [0.0, MAX_ABS_DISAGREEMENT]},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_sha,
            "queen_source_report_sha256": sha256_file(queen_report_path),
            "rook_source_report_sha256": sha256_file(rook_report_path),
            "queen_artifact": {"path": str(queen_artifact), "sha256_before": queen_sha, "sha256_after": queen_after},
            "rook_artifact": {"path": str(rook_artifact), "sha256_before": rook_sha, "sha256_after": rook_after},
            "frozen_Mx4": {"path": str(mx4_path), "sha256_before": MX4_SHA, "sha256_after": mx4_after},
            "frozen_S1": {"path": str(s1_path), "sha256_before": S1_SHA, "sha256_after": s1_after},
            "registry_sha256_before": registry_before, "registry_sha256_after": registry_after,
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "columns": table.column_names, "deterministic_bytes": True},
        },
        "interpretation": "The combined disagreement meets the registered coverage, residual-variance, and NMI gates; keep as an unregistered sensitivity only, without modifying or integrating Queen S1, Rook diagnostics, Mx4, or registry." if acceptance["distinct_disagreement_screen_pass"] else
        "One or more registered disagreement gates failed; retain the Queen and Rook diagnostics separately and leave all frozen inputs unchanged.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/s1-queen-rook-disagreement.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "s1-queen-rook-disagreement" / "queen-rook-disagreement.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "measure_after": {k: report["measure_after"][k] for k in (
                          "pooled_crossfit_residual_variance_ratio", "maximum_within_region_NMI",
                          "region_with_maximum_NMI", "disagreement_distribution")},
                      "output_artifact": report["provenance"]["output_artifact"], "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
