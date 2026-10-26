#!/usr/bin/env python3
"""Cross-fit Queen/Rook disagreement after frozen Mx4 F and S1-lag baselines."""
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
from audit_mx4p_conditional_innovation import deterministic_folds  # noqa: E402
from audit_s1_mx4_lagged_innovation import measure_region_nmi  # noqa: E402
from audit_s1_queen_rook_disagreement import (  # noqa: E402
    REGIONS, EXPECTED_N, MIN_COVERAGE, MIN_RESIDUAL_RATIO, MAX_REGION_NMI,
    MX4_SHA, S1_SHA, absolute_disagreement, load_feature, parquet_bytes,
    sha256_file, distribution,
)
from screen_mx4_peer_dispersion import FEATURES, read_feature  # noqa: E402

N_FOLDS = 5
RAW_NMI = 0.8162118656676006
RAW_RESIDUAL_RATIO = 1.1851634404575098


def crossfit_residualize(geoids: list[str], region: str, target: np.ndarray,
                         mx4_f: np.ndarray, s1_lag: np.ndarray,
                         joint_defined: np.ndarray, n_folds: int = N_FOLDS
                         ) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], np.ndarray]:
    """Fit target ~ intercept + Mx4 F + S1 lag on other GEOID folds; return held-out residuals."""
    y, x1, x2 = (np.asarray(v, dtype=np.float64) for v in (target, mx4_f, s1_lag))
    mask = np.asarray(joint_defined, dtype=bool)
    n = len(geoids)
    if n_folds < 2 or len(set(geoids)) != n or any(v.shape != (n,) for v in (y, x1, x2, mask)):
        raise ValueError("unique GEOIDs, predictors, target and mask must align; use at least two folds")
    if np.any(mask & (~np.isfinite(y) | ~np.isfinite(x1) | ~np.isfinite(x2))):
        raise ValueError("jointly defined rows must have finite target and both predictors")
    if np.any(~mask & np.isfinite(y)):
        raise ValueError("undefined target rows must remain null")
    fold_ids = deterministic_folds(geoids, region, n_folds)
    prediction = np.full(n, np.nan, dtype=np.float64)
    residual = np.full(n, np.nan, dtype=np.float64)
    reports: list[dict[str, Any]] = []
    for fold in range(n_folds):
        test = mask & (fold_ids == fold)
        train = mask & (fold_ids != fold)
        if not test.any() or int(train.sum()) < 3:
            raise ValueError(f"{region} fold {fold}: insufficient train/test support")
        design_train = np.column_stack((np.ones(int(train.sum())), x1[train], x2[train]))
        design_test = np.column_stack((np.ones(int(test.sum())), x1[test], x2[test]))
        coef, _, rank, singular = np.linalg.lstsq(design_train, y[train], rcond=None)
        prediction[test] = design_test @ coef
        residual[test] = y[test] - prediction[test]
        reports.append({"fold": fold, "train_defined_ids": int(train.sum()),
                        "heldout_defined_ids": int(test.sum()), "design_rank": int(rank),
                        "design_columns": int(design_train.shape[1]),
                        "coefficients_intercept_mx4_f_s1_lag": [float(v) for v in coef],
                        "heldout_residual_variance": float(residual[test].var(ddof=0)),
                        "minimum_singular_value": float(singular.min()) if singular.size else None})
    if np.any(mask & (~np.isfinite(prediction) | ~np.isfinite(residual))) or np.any(~mask & np.isfinite(residual)):
        raise AssertionError("cross-fit prediction/residual null masks must equal joint support")
    return prediction, residual, reports, fold_ids


def output_table(official: list[tuple[str, str]], queen: np.ndarray, queen_mask: np.ndarray,
                 rook: np.ndarray, rook_mask: np.ndarray, disagreement: np.ndarray,
                 mx4: np.ndarray, mx4_mask: np.ndarray, s1_lag: np.ndarray,
                 s1_mask: np.ndarray, joint: np.ndarray, prediction: np.ndarray,
                 residual: np.ndarray, folds: np.ndarray) -> pa.Table:
    n = len(official)
    arrays = (queen, queen_mask, rook, rook_mask, disagreement, mx4, mx4_mask,
              s1_lag, s1_mask, joint, prediction, residual, folds)
    if any(np.asarray(v).shape != (n,) for v in arrays):
        raise ValueError("output fields must align with official GEOID-region pairs")
    if np.any(joint & (~np.isfinite(disagreement) | ~np.isfinite(prediction) | ~np.isfinite(residual))):
        raise ValueError("defined residualization rows must be finite")
    if np.any(~joint & np.isfinite(residual)):
        raise ValueError("undefined residuals must remain null")
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "Queen_innovation": pa.array([float(v) if ok else None for v, ok in zip(queen, queen_mask)], type=pa.float64()),
        "Queen_defined": pa.array(queen_mask, type=pa.bool_()),
        "Rook_innovation": pa.array([float(v) if ok else None for v, ok in zip(rook, rook_mask)], type=pa.float64()),
        "Rook_defined": pa.array(rook_mask, type=pa.bool_()),
        "absolute_Queen_Rook_disagreement": pa.array([float(v) if ok else None for v, ok in zip(disagreement, queen_mask & rook_mask)], type=pa.float64()),
        "disagreement_defined": pa.array(queen_mask & rook_mask, type=pa.bool_()),
        "Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(mx4, mx4_mask)], type=pa.float64()),
        "Mx4_F_defined": pa.array(mx4_mask, type=pa.bool_()),
        "S1_lagged_Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(s1_lag, s1_mask)], type=pa.float64()),
        "S1_lagged_Mx4_F_defined": pa.array(s1_mask, type=pa.bool_()),
        "crossfit_joint_defined": pa.array(joint, type=pa.bool_()),
        "fold": pa.array(folds, type=pa.int8()),
        "crossfit_prediction": pa.array([float(v) if ok else None for v, ok in zip(prediction, joint)], type=pa.float64()),
        "crossfit_residual": pa.array([float(v) if ok else None for v, ok in zip(residual, joint)], type=pa.float64()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    source_report_path = ROOT / "reports/s1-queen-rook-disagreement.json"
    source_report = json.loads(source_report_path.read_text(encoding="utf-8"))
    if (not np.isclose(source_report["measure_after"]["maximum_within_region_NMI"], RAW_NMI, rtol=0, atol=1e-12)
            or not np.isclose(source_report["measure_after"]["pooled_crossfit_residual_variance_ratio"], RAW_RESIDUAL_RATIO, rtol=0, atol=1e-12)
            or source_report["scope"]["disagreement_defined_ids"] != EXPECTED_N):
        raise ValueError("raw Queen/Rook disagreement baselines differ from issued measurements")
    queen_path = workspace / "s1-mx4-lagged-innovation" / "s1-mx4-lagged-innovation.parquet"
    rook_path = workspace / "s1-rook-mx4-innovation" / "s1-rook-mx4-innovation.parquet"
    queen_sha = source_report["provenance"]["queen_artifact"]["sha256_before"]
    rook_sha = source_report["provenance"]["rook_artifact"]["sha256_before"]
    if (sha256_file(queen_path) != queen_sha or sha256_file(rook_path) != rook_sha
            or source_report["provenance"]["queen_artifact"]["sha256_after"] != queen_sha
            or source_report["provenance"]["rook_artifact"]["sha256_after"] != rook_sha):
        raise ValueError("frozen Queen/Rook source diagnostic artifact hash mismatch")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N or len({g for g, _ in official}) != EXPECTED_N:
        raise ValueError("official IDs must be an exact 9,379 GEOID-region partition")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    if official_sha != source_report["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official ID partition differs from frozen source diagnostics")

    queen, queen_mask = load_feature(queen_path, queen_sha, official, "innovation", "innovation_defined")
    rook, rook_mask = load_feature(rook_path, rook_sha, official, "rook_innovation", "rook_innovation_defined")
    mx4, mx4_mask = load_feature(queen_path, queen_sha, official, "Mx4_F", "Mx4_F_defined")
    rook_mx4, rook_mx4_mask = load_feature(rook_path, rook_sha, official, "Mx4_F", "Mx4_F_defined")
    if not np.array_equal(mx4_mask, rook_mx4_mask) or not np.array_equal(mx4[mx4_mask], rook_mx4[rook_mx4_mask]):
        raise ValueError("Queen/Rook diagnostics disagree on frozen Mx4 F values")
    s1_path = workspace / "ideas/S1.parquet"
    s1_lag, s1_mask, s1_sha = read_feature(s1_path, S1_SHA, *FEATURES["s1_lag"], official)
    if s1_sha != S1_SHA or int(s1_mask.sum()) != EXPECTED_N:
        raise ValueError("frozen S1 lagged-Mx4-F emission hash or coverage mismatch")
    disagreement, disagreement_mask = absolute_disagreement(queen, queen_mask, rook, rook_mask)
    joint = disagreement_mask & mx4_mask & s1_mask

    registry_path = ROOT / "config/ideas.json"
    mx4_path = workspace / "ideas/Mx4.parquet"
    registry_before = sha256_file(registry_path)
    if sha256_file(mx4_path) != MX4_SHA or sha256_file(s1_path) != S1_SHA:
        raise ValueError("frozen Mx4/S1 feature artifact hash mismatch")
    prediction = np.full(len(official), np.nan)
    residual = np.full(len(official), np.nan)
    fold_ids = np.full(len(official), -1, dtype=np.int8)
    region_reports: dict[str, Any] = {}
    for region in REGIONS:
        idx = np.asarray([i for i, (_, r) in enumerate(official) if r == region], dtype=np.int64)
        ids = [official[i][0] for i in idx]
        local_joint = joint[idx]
        pred, resid, folds_report, local_folds = crossfit_residualize(
            ids, region, disagreement[idx], mx4[idx], s1_lag[idx], local_joint)
        prediction[idx], residual[idx], fold_ids[idx] = pred, resid, local_folds
        nmi = measure_region_nmi(mx4[idx], mx4_mask[idx], resid, local_joint)
        if nmi["nmi"] is None:
            raise ValueError(f"{region}: residual NMI has insufficient overlap")
        source_values = disagreement[idx][local_joint]
        residual_values = resid[local_joint]
        source_var = float(source_values.var(ddof=0)) if source_values.size else 0.0
        residual_var = float(residual_values.var(ddof=0)) if residual_values.size else 0.0
        region_reports[region] = {
            "official_ids": int(idx.size), "queen_defined_ids": int(queen_mask[idx].sum()),
            "rook_defined_ids": int(rook_mask[idx].sum()), "disagreement_defined_ids": int(disagreement_mask[idx].sum()),
            "crossfit_joint_defined_ids": int(local_joint.sum()), "coverage_fraction": float(disagreement_mask[idx].mean()),
            "source_disagreement_distribution": distribution(source_values),
            "crossfit_residual_distribution": distribution(residual_values),
            "crossfit_residual_variance_ratio": residual_var / source_var if source_var > 0 else None,
            "maximum_within_region_NMI_residual_vs_Mx4_F": nmi,
            "folds": folds_report,
        }
    candidate = disagreement[joint]
    residual_values = residual[joint]
    candidate_var = float(candidate.var(ddof=0)) if candidate.size else 0.0
    residual_var = float(residual_values.var(ddof=0)) if residual_values.size else 0.0
    ratio = residual_var / candidate_var if candidate_var > 0 else None
    nmi_by_region = {r: float(region_reports[r]["maximum_within_region_NMI_residual_vs_Mx4_F"]["nmi"]) for r in REGIONS}
    max_region = max(REGIONS, key=nmi_by_region.__getitem__)
    coverage = float(disagreement_mask.mean())

    frozen_after = {"Mx4": sha256_file(mx4_path), "S1": sha256_file(s1_path),
                    "Queen": sha256_file(queen_path), "Rook": sha256_file(rook_path)}
    registry_after = sha256_file(registry_path)
    frozen_unchanged = (frozen_after == {"Mx4": MX4_SHA, "S1": S1_SHA,
                                         "Queen": queen_sha, "Rook": rook_sha}
                        and registry_after == registry_before)
    table = output_table(official, queen, queen_mask, rook, rook_mask, disagreement,
                         mx4, mx4_mask, s1_lag, s1_mask, joint, prediction, residual, fold_ids)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("orthogonalized artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    exact = len(official) == EXPECTED_N and table.num_rows == EXPECTED_N
    coverage_pass = coverage >= MIN_COVERAGE
    ratio_pass = ratio is not None and ratio >= MIN_RESIDUAL_RATIO
    nmi_pass = max(nmi_by_region.values()) < MAX_REGION_NMI
    acceptance = {
        "exact_9379_official_geoid_region_partition": bool(exact),
        "Queen_Rook_alignment_and_nulls_preserved": bool(np.array_equal(queen_mask, rook_mask) and np.array_equal(disagreement_mask, queen_mask & rook_mask)),
        "coverage_fraction": coverage, "coverage_at_least_98_59_percent": bool(coverage_pass),
        "crossfit_residual_variance_ratio": ratio, "residual_variance_at_least_10_percent": bool(ratio_pass),
        "maximum_within_region_NMI_residual_vs_Mx4_F": nmi_by_region[max_region],
        "region_with_maximum_residual_NMI": max_region, "maximum_region_NMI_below_0_80": bool(nmi_pass),
        "frozen_Mx4_S1_Queen_Rook_and_registry_unchanged": bool(frozen_unchanged),
        "distinct_screen_pass": bool(exact and coverage_pass and ratio_pass and nmi_pass and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "audit": "Cross-fitted residual of absolute Queen/Rook S1 disagreement after frozen Mx4 F and S1 lagged-Mx4 F; unregistered label-free screen",
        "method": {
            "candidate": "Reuse the frozen Queen and Rook innovations without recomputation; absolute disagreement = |Queen innovation − Rook innovation|, defined only where both source diagnostics are defined.",
            "residualization": "Five deterministic region-local GEOID folds. For each fold, fit OLS with intercept, frozen Mx4 F, and frozen S1 lagged-Mx4 F on the other four folds only; residual is held-out disagreement minus held-out prediction.",
            "NMI": "Within-region arithmetic normalized mutual information between frozen Mx4 F and the cross-fitted residual, using the existing deterministic empirical-decile/category encoders; measured on complete-case residual support.",
            "variance_ratio": "Pooled population variance (ddof=0) of cross-fitted held-out residuals divided by raw Queen/Rook disagreement variance on the same complete-case support; region-specific ratios descriptive.",
            "nulls": "Source disagreement nulls remain null; residuals are defined only where Queen, Rook, Mx4 F and S1 lagged-Mx4 F are all defined.",
            "unregistered": True,
        },
        "measure_before": {
            "raw_disagreement_maximum_within_region_NMI": RAW_NMI,
            "raw_disagreement_crossfit_residual_variance_ratio": RAW_RESIDUAL_RATIO,
            "raw_disagreement_coverage_fraction": 1.0,
            "source_report_sha256": sha256_file(source_report_path),
            "no_orthogonalized_residual_screen_previously_measured": True,
        },
        "scope": {
            "official_ids": len(official), "exact_official_geoid_region_partition": exact,
            "queen_defined_ids": int(queen_mask.sum()), "rook_defined_ids": int(rook_mask.sum()),
            "disagreement_defined_ids": int(disagreement_mask.sum()), "disagreement_undefined_ids": int((~disagreement_mask).sum()),
            "crossfit_joint_defined_ids": int(joint.sum()), "coverage_fraction": coverage,
            "labels_read": False, "sample_score_values_read": False,
            "removed_or_equivalent_reference_data_read": False, "external_inputs_read": False,
            "leaderboard_feedback_used": False, "frozen_Mx4_S1_modified": False,
            "registry_integration": False, "uploads": 0,
        },
        "measure_after": {
            "raw_disagreement_variance_on_joint_support": candidate_var,
            "crossfit_residual_variance": residual_var,
            "crossfit_residual_variance_ratio": ratio,
            "crossfit_residual_distribution": distribution(residual_values),
            "maximum_within_region_NMI_residual_vs_Mx4_F": nmi_by_region[max_region],
            "region_with_maximum_residual_NMI": max_region,
            "region_NMI": nmi_by_region,
            "regions": region_reports,
        },
        "thresholds": {"minimum_coverage_inclusive": MIN_COVERAGE,
                       "minimum_crossfit_residual_variance_ratio_inclusive": MIN_RESIDUAL_RATIO,
                       "maximum_within_region_NMI_exclusive": MAX_REGION_NMI},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_sha,
            "queen_rook_source_report_sha256": sha256_file(source_report_path),
            "queen_artifact": {"path": str(queen_path), "sha256_before": queen_sha, "sha256_after": frozen_after["Queen"]},
            "rook_artifact": {"path": str(rook_path), "sha256_before": rook_sha, "sha256_after": frozen_after["Rook"]},
            "Mx4_artifact": {"path": str(mx4_path), "sha256_before": MX4_SHA, "sha256_after": frozen_after["Mx4"]},
            "S1_artifact": {"path": str(s1_path), "sha256_before": S1_SHA, "sha256_after": frozen_after["S1"],
                            "lagged_Mx4_F_emission_sha256": hashlib.sha256("\n".join(
                                f"{g},{r},{int(ok)}," + (format(float(v), ".17g") if ok else "NULL")
                                for (g, r), v, ok in zip(official, s1_lag, s1_mask)).encode()).hexdigest()},
            "registry_sha256_before": registry_before, "registry_sha256_after": registry_after,
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "columns": table.column_names, "deterministic_bytes": True},
        },
        "interpretation": "Orthogonalized disagreement passes all gates; retain as unregistered diagnostic sensitivity only." if acceptance["distinct_screen_pass"] else
        "Orthogonalized disagreement fails one or more thresholds; retain the Queen and Rook source diagnostics separately, without integrating this residual or changing frozen features.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/s1-disagreement-orthogonalized.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "s1-disagreement-orthogonalized" / "orthogonalized-disagreement.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "measure_after": {k: report["measure_after"][k] for k in (
                          "crossfit_residual_variance_ratio", "maximum_within_region_NMI_residual_vs_Mx4_F",
                          "region_with_maximum_residual_NMI", "region_NMI")},
                      "output_artifact": report["provenance"]["output_artifact"],
                      "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
