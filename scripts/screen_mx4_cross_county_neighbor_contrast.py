#!/usr/bin/env python3
"""Screen focal Mx4 F minus population-weighted different-county Queen-neighbor median."""
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
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
from bias60.ideas.s import _queen_neighbors  # noqa: E402
from audit_mx4p_conditional_innovation import crossfit_region, pearson, distribution  # noqa: E402
from screen_mx4_peer_dispersion import (  # noqa: E402
    REGIONS, N_FOLDS, MIN_RESIDUAL_VARIANCE_RATIO, MAX_ABS_PEARSON,
    MX4_SHA, S1_SHA, STRATA_SHA, FEATURES, feature_digest, weighted_quantile, read_feature,
    read_strata, parquet_bytes,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cross_county_neighbor_contrast(official: list[tuple[str, str]], counties: list[str],
                                   shares: np.ndarray, share_defined: np.ndarray,
                                   population: np.ndarray,
                                   neighbors: list[np.ndarray]) -> dict[str, Any]:
    """Focal minus weighted median among same-region Queen neighbors in other counties."""
    n = len(official)
    share = np.asarray(shares, dtype=np.float64)
    share_mask = np.asarray(share_defined, dtype=bool)
    pop = np.asarray(population, dtype=np.float64)
    if len(counties) != n or len(neighbors) != n or any(v.shape != (n,) for v in (share, share_mask, pop)):
        raise ValueError("official IDs, counties, features, and Queen adjacency must align")
    if np.any(share_mask & (~np.isfinite(share) | (share < 0.0) | (share > 1.0))):
        raise ValueError("defined Mx4 F tract shares must be finite and in [0,1]")
    if np.any(np.isfinite(pop) & (pop < 0.0)):
        raise ValueError("provided pop_total cannot be negative")
    if any(np.any((adj < 0) | (adj >= n)) for adj in neighbors):
        raise ValueError("Queen neighbor index outside official partition")
    usable = share_mask & np.isfinite(pop) & (pop > 0.0)
    peer_median = np.full(n, np.nan)
    contrast = np.full(n, np.nan)
    peer_count = np.zeros(n, dtype=np.int32)
    peer_population = np.zeros(n, dtype=np.float64)
    for focal, adjacent in enumerate(neighbors):
        if not share_mask[focal]:
            continue
        donors = np.asarray(sorted(set(int(j) for j in adjacent
            if j != focal and official[j][1] == official[focal][1]
            and counties[j] != counties[focal] and usable[j])), dtype=np.int64)
        if not donors.size:
            continue
        median = weighted_quantile(share[donors], pop[donors], 0.5)
        peer_median[focal] = median
        contrast[focal] = share[focal] - median
        peer_count[focal] = len(donors)
        peer_population[focal] = float(pop[donors].sum(dtype=np.float64))
    defined = np.isfinite(contrast)
    if np.any(defined & ((contrast < -1.0) | (contrast > 1.0))):
        raise ValueError("defined cross-county contrast must be in [-1,1]")
    return {"peer_median": peer_median, "contrast": contrast, "defined": defined,
            "peer_count": peer_count, "peer_population": peer_population,
            "usable_donor": usable, "cross_county_peer_edges": int(sum(
                sum(1 for j in adj if counties[i] != counties[int(j)]
                    and official[i][1] == official[int(j)][1]) for i, adj in enumerate(neighbors)))}


def output_table(official: list[tuple[str, str]], counties: list[str], population: np.ndarray,
                 share: np.ndarray, share_defined: np.ndarray, candidate: dict[str, Any],
                 predictors: dict[str, np.ndarray], predictor_masks: dict[str, np.ndarray],
                 joint: np.ndarray, prediction: np.ndarray, residual: np.ndarray,
                 fold_ids: np.ndarray) -> pa.Table:
    n = len(official)
    arrays = [population, share, share_defined, candidate["peer_median"], candidate["contrast"],
              candidate["defined"], candidate["peer_count"], candidate["peer_population"], joint,
              prediction, residual, fold_ids, *predictors.values(), *predictor_masks.values()]
    if len(counties) != n or any(np.asarray(v).shape != (n,) for v in arrays):
        raise ValueError("output values and masks must align with exact official IDs")
    defined = np.asarray(candidate["defined"], dtype=bool)
    contrast = np.asarray(candidate["contrast"], dtype=np.float64)
    if np.any(defined & (~np.isfinite(contrast) | (contrast < -1.0) | (contrast > 1.0))):
        raise ValueError("defined focal contrast must be finite and in [-1,1]")
    if not np.array_equal(np.asarray(joint, bool), defined & predictor_masks["mx4_f"] & predictor_masks["s1_lag"]):
        raise ValueError("crossfit support must equal candidate and both predictor defined masks")
    if np.any(joint & (~np.isfinite(prediction) | ~np.isfinite(residual))) or np.any(~joint & np.isfinite(residual)):
        raise ValueError("cross-fit values must be finite exactly on joint support")
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "COUNTYFP": pa.array(counties, type=pa.string()),
        "pop_total": pa.array([float(v) if np.isfinite(v) else None for v in population], type=pa.float64()),
        "mx4_f_focal_share": pa.array([float(v) if ok else None for v, ok in zip(share, share_defined)], type=pa.float64()),
        "mx4_f_defined": pa.array(share_defined, type=pa.bool_()),
        "peer_weighted_median_mx4_f": pa.array([float(v) if ok else None for v, ok in zip(candidate["peer_median"], defined)], type=pa.float64()),
        "focal_peer_contrast": pa.array([float(v) if ok else None for v, ok in zip(contrast, defined)], type=pa.float64()),
        "candidate_defined": pa.array(defined, type=pa.bool_()),
        "eligible_peer_count": pa.array(candidate["peer_count"], type=pa.int32()),
        "eligible_peer_population": pa.array(candidate["peer_population"], type=pa.float64()),
        "Mx4_F_predictor": pa.array([float(v) if ok else None for v, ok in zip(predictors["mx4_f"], predictor_masks["mx4_f"])], type=pa.float64()),
        "Mx4_F_predictor_defined": pa.array(predictor_masks["mx4_f"], type=pa.bool_()),
        "S1_lagged_Mx4_F_predictor": pa.array([float(v) if ok else None for v, ok in zip(predictors["s1_lag"], predictor_masks["s1_lag"])], type=pa.float64()),
        "S1_lagged_Mx4_F_predictor_defined": pa.array(predictor_masks["s1_lag"], type=pa.bool_()),
        "crossfit_joint_defined": pa.array(joint, type=pa.bool_()),
        "fold": pa.array(fold_ids, type=pa.int8()),
        "crossfit_prediction": pa.array([float(v) if ok else None for v, ok in zip(prediction, joint)], type=pa.float64()),
        "crossfit_residual": pa.array([float(v) if ok else None for v, ok in zip(residual, joint)], type=pa.float64()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    prior_path = ROOT / "reports/mx4-peer-dispersion.json"
    median_path = ROOT / "reports/mx4-population-weighted-median.json"
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    median = json.loads(median_path.read_text(encoding="utf-8"))
    if (not np.isclose(prior["measure_after"]["crossfit_residual_variance_ratio"], 0.05630732109802699, rtol=0, atol=1e-14)
            or not np.allclose([prior["measure_after"]["candidate_pearson_r_with_Mx4_F"], prior["measure_after"]["candidate_pearson_r_with_S1_lagged_Mx4_F"]],
                               [0.9702608059818014, 0.9568959948174032], rtol=0, atol=1e-12)
            or not np.isclose(median["measure_after"]["pooled_crossfit_residual_variance_ratio"], 0.05846601972131671, rtol=0, atol=1e-14)):
        raise ValueError("peer dispersion/median before measurements differ from issued baselines")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != 9379 or len(set(official)) != 9379 or len({g for g, _ in official}) != 9379
            or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official ID list must be an exact four-region partition of 9,379 GEOIDs")
    official_hash = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode("utf-8")).hexdigest()
    if official_hash != prior["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official IDs differ from the established peer-dispersion audit")

    registry_path = ROOT / "config/ideas.json"
    registry_before = sha256_file(registry_path)
    mx4_path, s1_path = workspace / "ideas/Mx4.parquet", workspace / "ideas/S1.parquet"
    mx4_f, mx4_f_defined, mx4_sha = read_feature(mx4_path, MX4_SHA, *FEATURES["mx4_f"], official)
    s1_lag, s1_lag_defined, s1_sha = read_feature(s1_path, S1_SHA, *FEATURES["s1_lag"], official)
    if mx4_sha != MX4_SHA or s1_sha != S1_SHA:
        raise AssertionError("frozen Mx4/S1 hashes differ from registered source digests")
    if int(mx4_f_defined.sum()) != 9379:
        raise ValueError("frozen Mx4 F coverage differs from prior screen")

    counties = [""] * len(official)
    population = np.full(len(official), np.nan, dtype=np.float64)
    strata_manifest: dict[str, Any] = {}
    positions = {g: i for i, (g, _) in enumerate(official)}
    for region in REGIONS:
        region_pairs = [(g, r) for g, r in official if r == region]
        rows, metadata = read_strata(workspace, region, region_pairs)
        strata_manifest[region] = metadata
        for geoid, (county, pop) in rows.items():
            idx = positions[geoid]
            counties[idx], population[idx] = county, pop
    config = load_config(ROOT / "config/bias60-harness-preregistered.json")
    neighbors = [np.asarray([], dtype=np.int64) for _ in official]
    for region in REGIONS:
        local_pairs = [(g, r) for g, r in official if r == region]
        local_inputs = ProvidedInputs(config, workspace, region, [g for g, _ in local_pairs])
        local_neighbors = _queen_neighbors(local_inputs)
        global_positions = {g: positions[g] for g, _ in local_pairs}
        for (geoid, _), adjacent in zip(local_pairs, local_neighbors):
            neighbors[global_positions[geoid]] = np.asarray(
                sorted(global_positions[local_pairs[int(j)][0]] for j in adjacent), dtype=np.int64)
    candidate = cross_county_neighbor_contrast(official, counties, mx4_f, mx4_f_defined,
                                               population, neighbors)
    defined = np.asarray(candidate["defined"], dtype=bool)
    coverage = float(defined.mean())
    if not defined.any():
        raise ValueError("no focal tracts have eligible different-county Queen neighbors")

    predictors = {"mx4_f": mx4_f, "s1_lag": s1_lag}
    predictor_masks = {"mx4_f": mx4_f_defined, "s1_lag": s1_lag_defined}
    joint = defined & mx4_f_defined & s1_lag_defined
    predictions = np.full(len(official), np.nan)
    residuals = np.full(len(official), np.nan)
    fold_ids = np.full(len(official), -1, dtype=np.int8)
    regions_report: dict[str, Any] = {}
    for region in REGIONS:
        idx = np.asarray([i for i, (_, r) in enumerate(official) if r == region], dtype=np.int64)
        ids = [official[i][0] for i in idx]
        local_joint = joint[idx]
        pred, resid, fold_report, folds = crossfit_region(
            ids, region, candidate["contrast"][idx], mx4_f[idx], s1_lag[idx], local_joint, n_folds=N_FOLDS)
        predictions[idx], residuals[idx], fold_ids[idx] = pred, resid, folds
        x = candidate["contrast"][idx][local_joint]
        e = resid[local_joint]
        original_var = float(x.var(ddof=0)) if x.size else 0.0
        residual_var = float(e.var(ddof=0)) if e.size else 0.0
        regions_report[region] = {
            "official_ids": int(idx.size), "candidate_defined_ids": int(defined[idx].sum()),
            "candidate_undefined_ids": int((~defined[idx]).sum()), "crossfit_joint_defined_ids": int(local_joint.sum()),
            "coverage_fraction": float(defined[idx].mean()),
            "candidate_contrast_distribution": distribution(candidate["contrast"][idx][defined[idx]]),
            "eligible_cross_county_peer_count_distribution": distribution(candidate["peer_count"][idx][defined[idx]]),
            "crossfit_residual_variance_ratio": residual_var / original_var if original_var > 0 else None,
            "pearson_r_candidate_with_Mx4_F": pearson(x, mx4_f[idx][local_joint]) if local_joint.any() else None,
            "pearson_r_candidate_with_S1_lagged_Mx4_F": pearson(x, s1_lag[idx][local_joint]) if local_joint.any() else None,
            "tracts_with_no_eligible_peers": int(np.count_nonzero(~defined[idx])),
            "folds": fold_report,
        }
    candidate_values = candidate["contrast"][joint]
    residual_values = residuals[joint]
    candidate_var = float(candidate_values.var(ddof=0)) if candidate_values.size else 0.0
    residual_var = float(residual_values.var(ddof=0)) if residual_values.size else 0.0
    ratio = residual_var / candidate_var if candidate_var > 0 else None
    correlations = {"mx4_f": pearson(candidate_values, mx4_f[joint]),
                    "s1_lagged_mx4_f": pearson(candidate_values, s1_lag[joint])}
    registry_after = sha256_file(registry_path)
    frozen_after = {"Mx4": sha256_file(mx4_path), "S1": sha256_file(s1_path)}
    frozen_unchanged = frozen_after == {"Mx4": MX4_SHA, "S1": S1_SHA} and registry_after == registry_before
    if not frozen_unchanged:
        raise AssertionError("frozen Mx4/S1 or registry changed during diagnostic")
    table = output_table(official, counties, population, mx4_f, mx4_f_defined, candidate,
                         predictors, predictor_masks, joint, predictions, residuals, fold_ids)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("focal contrast artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    bounded = bool(np.isfinite(candidate["contrast"][defined]).all()
                   and np.all((candidate["contrast"][defined] >= -1.0) & (candidate["contrast"][defined] <= 1.0)))
    exact_ids = table.num_rows == 9379 and len(set(official)) == 9379
    coverage_pass = coverage >= 0.10
    ratio_pass = ratio is not None and ratio >= MIN_RESIDUAL_VARIANCE_RATIO
    corr_pass = all(value is not None and abs(value) < MAX_ABS_PEARSON for value in correlations.values())
    acceptance = {
        "exact_9379_official_geoid_region_partition": exact_ids,
        "candidate_defined_ids": int(defined.sum()), "coverage_fraction": coverage,
        "coverage_at_least_10_percent": coverage_pass,
        "contrast_finite_and_bounded_minus1_1": bounded,
        "crossfit_joint_defined_ids": int(joint.sum()),
        "crossfit_residual_variance_ratio": ratio,
        "residual_variance_at_least_10_percent": bool(ratio_pass),
        "pearson_r_candidate_with_Mx4_F": correlations["mx4_f"],
        "pearson_r_candidate_with_S1_lagged_Mx4_F": correlations["s1_lagged_mx4_f"],
        "absolute_correlations_below_0_80": bool(corr_pass),
        "frozen_Mx4_S1_registry_unchanged": bool(frozen_unchanged),
        "distinct_screen_pass": bool(exact_ids and coverage_pass and bounded and ratio_pass and corr_pass and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "audit": "Focal Mx4 F minus population-weighted median among different-county Queen neighbors; unregistered label-free sensitivity",
        "method": {
            "candidate": "Focal Mx4 F minus the population-weighted empirical median among eligible Queen-adjacent tracts in the same region with different provided COUNTYFP.",
            "eligible_peer": "Mx4 F defined, finite provided pop_total > 0, Queen-adjacent in the same region, and COUNTYFP different from focal. Stable ascending weighted median; focal GEOID excluded.",
            "nulls": "Candidate undefined if focal Mx4 F is undefined or no eligible different-county Queen neighbor exists; missing/nonpositive population excluded without imputation.",
            "crossfit": "Five region-local SHA-256 folds using mx4p-conditional-innovation-v1|region|GEOID. Fit intercept + frozen Mx4 F + frozen S1 lagged-Mx4 F OLS on four folds and predict held-out candidate contrast.",
            "residual_variance_ratio": "Pooled population variance (ddof=0) of held-out residuals divided by candidate variance on the same complete-case contrast/Mx4 F/S1-lag support.",
            "pearson": "Candidate correlations with both frozen predictors are measured on the complete-case cross-fit support.",
        },
        "measure_before": {
            "peer_iqr_crossfit_residual_variance_ratio": 0.05630732109802699,
            "peer_iqr_pearson_r_with_Mx4_F": 0.9702608059818014,
            "peer_iqr_pearson_r_with_S1_lagged_Mx4_F": 0.9568959948174032,
            "peer_median_crossfit_residual_variance_ratio": 0.05846601972131671,
            "cross_county_neighbor_contrast_previously_measured": False,
            "peer_dispersion_report_sha256": sha256_file(prior_path),
            "peer_median_report_sha256": sha256_file(median_path),
        },
        "scope": {
            "official_ids": len(official), "exact_id_partition": exact_ids,
            "candidate_defined_ids": int(defined.sum()), "candidate_undefined_ids": int((~defined).sum()),
            "candidate_coverage_fraction": coverage,
            "crossfit_joint_defined_ids": int(joint.sum()), "crossfit_joint_undefined_ids": int(len(official) - joint.sum()),
            "cross_county_queen_peer_edges": int(candidate["cross_county_peer_edges"]),
            "eligible_peer_count_distribution": distribution(candidate["peer_count"][defined]),
            "usable_population_weighted_donor_tracts": int(np.count_nonzero(candidate["usable_donor"])),
            "nonpositive_or_missing_population_donor_tracts": int(len(official) - np.count_nonzero(candidate["usable_donor"])),
            "labels_read": False, "sample_score_values_read": False,
            "removed_or_equivalent_reference_data_read": False, "external_inputs_read": False,
            "leaderboard_feedback_used": False, "frozen_Mx4_S1_modified": False,
            "registry_integration": False, "uploads": 0,
        },
        "measure_after": {
            "contrast_distribution": distribution(candidate["contrast"][defined]),
            "cross_county_peer_count_distribution": distribution(candidate["peer_count"][defined]),
            "cross_county_neighbor_edges": int(candidate["cross_county_peer_edges"]),
            "candidate_variance_on_crossfit_support": candidate_var,
            "crossfit_residual_variance": residual_var,
            "crossfit_residual_variance_ratio": ratio,
            "crossfit_residual_distribution": distribution(residual_values),
            "candidate_pearson_r_with_Mx4_F": correlations["mx4_f"],
            "candidate_pearson_r_with_S1_lagged_Mx4_F": correlations["s1_lagged_mx4_f"],
            "regions": regions_report,
        },
        "thresholds": {"minimum_coverage": 0.10, "contrast_bounds_inclusive": [-1.0, 1.0],
                       "minimum_crossfit_residual_variance_ratio": MIN_RESIDUAL_VARIANCE_RATIO,
                       "maximum_absolute_predictor_pearson_r_exclusive": MAX_ABS_PEARSON},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_hash,
            "frozen_inputs": {
                "Mx4": {"path": str(mx4_path), "sha256_before": mx4_sha, "sha256_after": frozen_after["Mx4"],
                        "F_predictor_emission_sha256": feature_digest(official, mx4_f, mx4_f_defined)},
                "S1": {"path": str(s1_path), "sha256_before": s1_sha, "sha256_after": frozen_after["S1"],
                       "lagged_F_predictor_emission_sha256": feature_digest(official, s1_lag, s1_lag_defined)},
            },
            "strata_inputs": strata_manifest,
            "registry_sha256_before": registry_before, "registry_sha256_after": registry_after,
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "columns": table.column_names, "deterministic_bytes": True},
        },
        "interpretation": "The cross-county Queen-neighbor contrast passes all distinctness gates; retain only as an unregistered diagnostic, not a promoted feature." if acceptance["distinct_screen_pass"] else
        "One or more cross-county coverage, boundedness, residual-variance, or predictor-correlation gates failed; retire this candidate and preserve frozen inputs.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/mx4-cross-county-neighbor-contrast.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE is required and must exist")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "mx4-cross-county-neighbor-contrast" / "cross-county-neighbor-contrast.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "measure_after": {k: report["measure_after"][k] for k in (
                          "crossfit_residual_variance_ratio", "candidate_pearson_r_with_Mx4_F",
                          "candidate_pearson_r_with_S1_lagged_Mx4_F")},
                      "output_artifact": report["provenance"]["output_artifact"],
                      "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
