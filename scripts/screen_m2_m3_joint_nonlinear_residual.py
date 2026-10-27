#!/usr/bin/env python3
"""County-held-out two-feature spline residual correction with one fixed interaction."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx4p_conditional_innovation import read_emission, sha256_file  # noqa: E402
from bias60.inputs import official_scored_pairs  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_N = 9379
M2_SHA = "88aa794ff46b30824781d933cac2f6f4fb1898654c8c7726990e1ed70e605702"
M3_SHA = "f560064aa9271e6f00f31f16c67242e30d9001c2ca427882bc873f97e0ab5538"
RESIDUAL_SHA = "437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
RESIDUAL_REPORT = ROOT / "reports/s1-disagreement-orthogonalized.json"
MX4_FEATURE_REPORT = ROOT / "reports/mx4-population-weighted-feature.json"
M2_CHANNEL = "covariate:facility_wikidata_link_share"
M3_CHANNEL = "covariate:facility_incoming_address_mismatch_share"
KNOT_QUANTILES = (0.25, 0.50, 0.75)
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261029
MIN_POOLED_DELTA_R2 = 0.003
MIN_CI_LOWER = 0.0
MAX_REGION_DECLINE = 0.005


def county_fips(geoid: str) -> str:
    value = str(geoid)
    if len(value) != 11 or not value.isdigit():
        raise ValueError(f"GEOID must be exactly eleven numeric characters: {value!r}")
    return value[:5]


def mask_sha256(mask: np.ndarray) -> str:
    return hashlib.sha256(np.packbits(np.asarray(mask, dtype=np.uint8), bitorder="little").tobytes()).hexdigest()


def county_training_hash(counties: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(counties)).encode("utf-8")).hexdigest()


def _standardize(x: np.ndarray, mean: np.ndarray | None = None,
                 scale: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    values = np.asarray(x, dtype=np.float64)
    if values.ndim != 2 or not np.isfinite(values).all():
        raise ValueError("spline inputs must be a finite matrix")
    if mean is None:
        mean = values.mean(axis=0)
    if scale is None:
        sd = values.std(axis=0, ddof=0)
        scale = np.where(sd > 0., sd, 1.)
    return (values - mean) / scale, np.asarray(mean), np.asarray(scale)


def spline_features_train_only(train_x: np.ndarray, eval_x: np.ndarray
                               ) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Fixed cubic truncated-power basis; quantile knots and all scales use train rows only."""
    train = np.asarray(train_x, dtype=np.float64)
    evaluation = np.asarray(eval_x, dtype=np.float64)
    if (train.ndim != 2 or evaluation.ndim != 2 or train.shape[1] != 2
            or evaluation.shape[1] != 2 or train.shape[0] < 4 or not evaluation.shape[0]
            or not np.isfinite(train).all() or not np.isfinite(evaluation).all()):
        raise ValueError("spline fitting requires finite two-feature train/evaluation matrices")
    xmean = train.mean(axis=0)
    xsd = train.std(axis=0, ddof=0)
    xscale = np.where(xsd > 0., xsd, 1.)
    z_train = (train - xmean) / xscale
    z_eval = (evaluation - xmean) / xscale
    knots_by_feature: list[list[float]] = []
    train_columns: list[np.ndarray] = []
    eval_columns: list[np.ndarray] = []
    feature_names: list[str] = []
    for j, name in enumerate(("M2", "M3")):
        quantile_knots = np.quantile(z_train[:, j], KNOT_QUANTILES, method="linear")
        knots = sorted({float(k) for k in quantile_knots if z_train[:, j].min() < k < z_train[:, j].max()})
        knots_by_feature.append(knots)
        # Cubic polynomial terms plus one truncated cubic term per distinct internal training knot.
        train_columns.extend([z_train[:, j], z_train[:, j] ** 2, z_train[:, j] ** 3])
        eval_columns.extend([z_eval[:, j], z_eval[:, j] ** 2, z_eval[:, j] ** 3])
        feature_names.extend([f"{name}_z", f"{name}_z2", f"{name}_z3"])
        for k_index, knot in enumerate(knots):
            train_columns.append(np.maximum(z_train[:, j] - knot, 0.) ** 3)
            eval_columns.append(np.maximum(z_eval[:, j] - knot, 0.) ** 3)
            feature_names.append(f"{name}_truncated_cubic_k{k_index}")
    # Exactly one cross-feature interaction; no spline-by-spline interaction grid.
    train_columns.append(z_train[:, 0] * z_train[:, 1])
    eval_columns.append(z_eval[:, 0] * z_eval[:, 1])
    feature_names.append("M2_z_x_M3_z")
    raw_train = np.column_stack(train_columns)
    raw_eval = np.column_stack(eval_columns)
    fmean = raw_train.mean(axis=0)
    fsd = raw_train.std(axis=0, ddof=0)
    fscale = np.where(fsd > 0., fsd, 1.)
    train_basis = (raw_train - fmean) / fscale
    eval_basis = (raw_eval - fmean) / fscale
    details = {"input_training_mean": xmean.tolist(), "input_training_sd": xsd.tolist(),
        "input_applied_scale": xscale.tolist(), "knot_quantiles": list(KNOT_QUANTILES),
        "knots_standardized_by_feature": {"M2": knots_by_feature[0], "M3": knots_by_feature[1]},
        "basis_feature_names": feature_names, "basis_training_mean": fmean.tolist(),
        "basis_training_sd": fsd.tolist(), "basis_applied_scale": fscale.tolist(),
        "training_rows": int(train.shape[0]), "evaluation_rows": int(evaluation.shape[0]),
        "interaction_terms": ["M2_z_x_M3_z"]}
    return train_basis, eval_basis, details


def fit_fold(train_y: np.ndarray, train_x: np.ndarray, eval_x: np.ndarray
             ) -> tuple[np.ndarray, dict[str, Any]]:
    y = np.asarray(train_y, dtype=np.float64)
    x = np.asarray(train_x, dtype=np.float64)
    ev = np.asarray(eval_x, dtype=np.float64)
    if y.ndim != 1 or x.shape != (y.size, 2) or y.size < 4:
        raise ValueError("fold training target/features must align with two covariates")
    train_basis, eval_basis, details = spline_features_train_only(x, ev)
    design_train = np.column_stack((np.ones(y.size), train_basis))
    design_eval = np.column_stack((np.ones(ev.shape[0]), eval_basis))
    coefficients, _, rank, singular = np.linalg.lstsq(design_train, y, rcond=None)
    details.update({"coefficients_intercept_then_basis": coefficients.tolist(),
        "design_rank": int(rank), "design_columns": int(design_train.shape[1]),
        "minimum_singular_value": float(singular.min()) if singular.size else None,
        "maximum_singular_value": float(singular.max()) if singular.size else None,
        "fit": "ordinary least squares via np.linalg.lstsq; minimum-norm solution if fixed basis is rank deficient"})
    return design_eval @ coefficients, details


def residual_delta(y: np.ndarray, prediction: np.ndarray) -> dict[str, float | int | None]:
    target, pred = np.asarray(y, dtype=np.float64), np.asarray(prediction, dtype=np.float64)
    if target.ndim != 1 or pred.shape != target.shape or not target.size or not (np.isfinite(target).all() and np.isfinite(pred).all()):
        raise ValueError("held-out residuals and predictions must be aligned finite vectors")
    base_sse = float(np.dot(target, target))
    model_sse = float(np.sum((target - pred) ** 2))
    return {"n": int(target.size), "uncorrected_s1_baseline_sse": base_sse,
            "spline_correction_sse": model_sse,
            "delta_r2_vs_uncorrected_s1": (base_sse - model_sse) / base_sse if base_sse > 0. else None}


def county_block_bootstrap(records: list[dict[str, Any]], *, replicates: int = BOOTSTRAP_REPLICATES,
                           seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    if replicates < 100:
        raise ValueError("at least 100 bootstrap replicates are required")
    grouped = {r: [] for r in REGIONS}
    for row in records:
        grouped[row["region"]].append(row)
    if any(not rows for rows in grouped.values()):
        raise ValueError("county-block bootstrap requires supported counties in every region")
    rng = np.random.default_rng(seed)
    pooled = np.empty(replicates)
    region_draws = {r: np.empty(replicates) for r in REGIONS}
    for b in range(replicates):
        base_total = model_total = 0.
        for region in REGIONS:
            rows = grouped[region]
            draw = rng.integers(0, len(rows), size=len(rows))
            base = sum(float(rows[i]["baseline_sse"]) for i in draw)
            model = sum(float(rows[i]["model_sse"]) for i in draw)
            region_draws[region][b] = (base - model) / base if base > 0. else np.nan
            base_total += base
            model_total += model
        pooled[b] = (base_total - model_total) / base_total if base_total > 0. else np.nan
    def interval(values: np.ndarray) -> list[float]:
        finite = values[np.isfinite(values)]
        if finite.size < int(.99 * replicates):
            raise ValueError("too many degenerate county-block bootstrap draws")
        return [float(np.quantile(finite, .025)), float(np.quantile(finite, .975))]
    return {"replicates": int(replicates), "seed": int(seed),
        "resampling": "paired held-county OOF SSEs; sample counties with replacement within each region, retaining region county counts",
        "pooled_delta_r2_ci_95": interval(pooled),
        "regional_delta_r2_ci_95": {r: interval(v) for r, v in region_draws.items()},
        "degenerate_replicates": int(np.sum(~np.isfinite(pooled)))}


def run(workspace: Path, report_path: Path, manifest_path: Path) -> dict[str, Any]:
    before = json.loads(MX4_FEATURE_REPORT.read_text(encoding="utf-8"))
    corr = {row["feature"]: row["pearson_r"] for row in before["pearson_vs_existing_F_covariates"]}
    m2_corr = corr.get("M2|F|covariate:facility_wikidata_link_share")
    m3_corr = corr.get("M3|F|covariate:facility_incoming_address_mismatch_share")
    if (m2_corr is None or m3_corr is None or not np.isclose(m2_corr, -0.00153621867611425, rtol=0., atol=1e-15)
            or not np.isclose(m3_corr, -0.010295012637247832, rtol=0., atol=1e-15)):
        raise ValueError("M2/M3 MEASURE-BEFORE correlation values differ from the frozen source report")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N or set(r for _, r in official) != set(REGIONS):
        raise ValueError("official ID-region partition must contain exactly 9,379 tracts in four regions")
    geoids = np.asarray([g for g, _ in official], dtype=object)
    regions = np.asarray([r for _, r in official], dtype=object)
    counties = np.asarray([county_fips(g) for g in geoids], dtype=object)
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    m2_path, m3_path = workspace / "ideas/M2.parquet", workspace / "ideas/M3.parquet"
    m2, m2_defined, m2_sha = read_emission(m2_path, M2_SHA, "M2", "F", M2_CHANNEL, official)
    m3, m3_defined, m3_sha = read_emission(m3_path, M3_SHA, "M3", "F", M3_CHANNEL, official)
    if np.any(m2_defined & ((m2 < 0.) | (m2 > 1.))) or np.any(m3_defined & ((m3 < 0.) | (m3 > 1.))):
        raise ValueError("provided share covariates must lie in [0,1]")
    residual_path = workspace / "s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    residual_report = json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    if (residual_report["provenance"]["output_artifact"]["sha256"] != RESIDUAL_SHA
            or sha256_file(residual_path) != RESIDUAL_SHA):
        raise ValueError("frozen S1 residual report/artifact does not match pinned digest")
    table = pq.read_table(residual_path, columns=["GEOID", "region", "crossfit_residual", "crossfit_joint_defined"])
    if table.num_rows != EXPECTED_N:
        raise ValueError("frozen residual artifact row count differs from official scope")
    pos = {pair: i for i, pair in enumerate(official)}
    y = np.full(EXPECTED_N, np.nan)
    residual_defined = np.zeros(EXPECTED_N, dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in pos or pair in seen:
            raise ValueError(f"unexpected or duplicate frozen residual ID: {pair}")
        seen.add(pair)
        i = pos[pair]
        if not isinstance(row["crossfit_joint_defined"], bool):
            raise ValueError("frozen residual mask must be boolean")
        residual_defined[i] = row["crossfit_joint_defined"]
        if row["crossfit_residual"] is not None:
            y[i] = float(row["crossfit_residual"])
    if seen != set(official) or not np.array_equal(np.isfinite(y), residual_defined):
        raise ValueError("frozen residual values/mask do not exactly align to official IDs")
    support = residual_defined & m2_defined & m3_defined & np.isfinite(y) & np.isfinite(m2) & np.isfinite(m3)
    if not support.any():
        raise ValueError("no complete paired support for M2/M3 nonlinear screen")
    prediction = np.full(EXPECTED_N, np.nan)
    county_records: list[dict[str, Any]] = []
    manifest_rows: list[tuple[Any, ...]] = []
    fold_assignment = []
    for region in REGIONS:
        regional_support = support & (regions == region)
        regional_counties = sorted(set(counties[regional_support].tolist()))
        if len(regional_counties) < 2:
            raise ValueError(f"{region}: fewer than two counties with complete paired support")
        for held in regional_counties:
            test = regional_support & (counties == held)
            train = regional_support & (counties != held)
            if int(train.sum()) < 20 or not test.any():
                raise ValueError(f"{region}/{held}: insufficient fixed county-transfer train/test support")
            x_train = np.column_stack((m2[train], m3[train]))
            x_test = np.column_stack((m2[test], m3[test]))
            pred, fit = fit_fold(y[train], x_train, x_test)
            prediction[test] = pred
            training_counties = [str(c) for c in regional_counties if c != held]
            training_hash = county_training_hash(training_counties)
            base_sse = float(np.dot(y[test], y[test]))
            model_sse = float(np.sum((y[test] - pred) ** 2))
            delta = (base_sse - model_sse) / base_sse if base_sse > 0. else None
            record = {"region": region, "county_fips": str(held), "heldout_n": int(test.sum()),
                "training_n": int(train.sum()), "training_county_count": len(training_counties),
                "training_counties_sha256": training_hash, "baseline_sse": base_sse,
                "model_sse": model_sse, "out_of_county_delta_r2": delta, "training_fit": fit}
            county_records.append(record)
            fold_assignment.append((region, str(held), training_hash, len(training_counties), int(train.sum())))
            for geoid in geoids[test]:
                manifest_rows.append((str(geoid), region, str(held), training_hash, len(training_counties), int(train.sum())))
    if np.any(support & ~np.isfinite(prediction)) or np.any(~support & np.isfinite(prediction)):
        raise AssertionError("county-held-out prediction mask differs from exact complete support")
    pooled_baseline_sse = float(np.dot(y[support], y[support]))
    pooled_model_sse = float(np.sum((y[support] - prediction[support]) ** 2))
    pooled_delta = (pooled_baseline_sse - pooled_model_sse) / pooled_baseline_sse
    regions_result: dict[str, Any] = {}
    for region in REGIONS:
        use = support & (regions == region)
        base = float(np.dot(y[use], y[use]))
        model = float(np.sum((y[use] - prediction[use]) ** 2))
        regions_result[region] = {"n": int(use.sum()), "county_count": len(set(counties[use].tolist())),
            "uncorrected_baseline_sse": base, "spline_correction_sse": model,
            "delta_r2": (base-model)/base if base > 0. else None}
    bootstrap = county_block_bootstrap(county_records)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["GEOID", "region", "heldout_county_fips", "training_counties_sha256", "training_county_count", "training_support_n"])
        writer.writerows(sorted(manifest_rows, key=lambda row: (row[1], row[0])))
    manifest_sha = sha256_file(manifest_path)
    worst_decline = min(float(regions_result[r]["delta_r2"]) for r in REGIONS)
    positive_ci = bootstrap["pooled_delta_r2_ci_95"][0] > MIN_CI_LOWER
    no_excessive_regional_decline = worst_decline >= -MAX_REGION_DECLINE
    gate = bool(pooled_delta >= MIN_POOLED_DELTA_R2 and positive_ci and no_excessive_regional_decline)
    report = {
      "schema_version": 1,
      "screen": "County-held-out fixed two-feature cubic spline plus one M2×M3 interaction on frozen S1 residual; unregistered",
      "measure_before": {"M2_vs_Mx4_pearson_r": float(m2_corr), "M3_vs_Mx4_pearson_r": float(m3_corr),
        "county_heldout_joint_delta_r2_measured": False, "source_report_sha256": sha256_file(MX4_FEATURE_REPORT)},
      "method": {"outcome": "Frozen S1 orthogonalized-disagreement residual from pinned provided-feature artifact; never recomputed or fit as a target.",
        "predictors": {"M2": {"idea": "M2", "component": "F", "channel": M2_CHANNEL},
                       "M3": {"idea": "M3", "component": "F", "channel": M3_CHANNEL}},
        "basis": "For each feature, standardized train-only x, x^2, x^3 and truncated cubic max(x-k,0)^3 terms at distinct internal training-fold quantiles q25/q50/q75; duplicate/boundary knots are omitted. Every resulting basis column is standardized from the training rows only.",
        "interaction": "Exactly one product of the two train-standardized covariates (M2_z × M3_z); no spline-by-spline interaction grid.",
        "validation": "Leave one county (FIPS from first five tract GEOID digits) out within each official region; fit intercept plus both fixed additive spline bases and one product interaction on all other supported counties in that region; predict held-out county.",
        "baseline": "Uncorrected frozen S1 residual, represented by zero residual correction; baseline SSE is sum(residual^2) on identical held-out support.",
        "metric": "County-held-out delta-R2=(uncorrected baseline SSE - spline correction SSE)/uncorrected baseline SSE; pooled and region-specific results.",
        "uncertainty": "Paired county-block percentile bootstrap with replacement, sampled within official regions; 2,000 draws with fixed seed.",
        "rank_deficiency": "OLS uses numpy.linalg.lstsq minimum-norm solution if a fixed spline basis is rank-deficient; no term selection or tuning.",
        "support": "Intersection of frozen residual and both provided M2/M3 feature-defined masks; no imputation or outcome-dependent exclusions.",
        "labels_read": False, "removed_or_equivalent_references_read": False, "external_data_used": [],
        "leaderboard_feedback_used": False, "uploads": 0, "unregistered": True,
        "interpretation_limit": "Provided-feature residual association screen only; it does not establish hidden-target accuracy or authorize estimator integration."},
      "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": official_sha,
        "paired_support_ids": int(support.sum()), "paired_undefined_ids": int((~support).sum()),
        "residual_defined_ids": int(residual_defined.sum()), "M2_defined_ids": int(m2_defined.sum()), "M3_defined_ids": int(m3_defined.sum()),
        "regional_support": {r: int(np.sum(support & (regions == r))) for r in REGIONS},
        "residual_mask_sha256": mask_sha256(residual_defined), "M2_mask_sha256": mask_sha256(m2_defined),
        "M3_mask_sha256": mask_sha256(m3_defined), "paired_mask_sha256": mask_sha256(support),
        "county_assignment_sha256": hashlib.sha256("\n".join(f"{g},{r},{c}" for (g,r),c in zip(official,counties)).encode()).hexdigest(),
        "county_fold_count": len(county_records), "fold_records": fold_assignment,
        "labels_read": False, "sample_score_values_read": False, "removed_or_equivalent_references_read": False,
        "external_data_used": [], "changed_folds": False, "posthoc_tuning": False,
        "leaderboard_feedback_used": False, "uploads": 0},
      "measure_after": {"pooled": {"n": int(support.sum()), "uncorrected_baseline_sse": pooled_baseline_sse,
        "spline_correction_sse": pooled_model_sse, "delta_r2": pooled_delta},
        "regional": regions_result, "worst_regional_delta_r2": worst_decline,
        "county_block_bootstrap": bootstrap, "counties": county_records},
      "thresholds": {"pooled_delta_r2_minimum_inclusive": MIN_POOLED_DELTA_R2,
        "county_block_ci_lower_strictly_above": MIN_CI_LOWER,
        "maximum_region_decline_magnitude_inclusive": MAX_REGION_DECLINE,
        "knot_quantiles_fixed": list(KNOT_QUANTILES)},
      "acceptance": {"pooled_delta_r2_at_least_0_003": bool(pooled_delta >= MIN_POOLED_DELTA_R2),
        "county_block_ci_lower_above_zero": bool(positive_ci),
        "no_region_decline_exceeds_0_005": bool(no_excessive_regional_decline),
        "joint_nonlinear_residual_screen_pass": gate},
      "provenance": {"official_geoid_region_sha256": official_sha,
        "M2_artifact": {"path": str(m2_path), "sha256": m2_sha, "idea": "M2", "component": "F", "channel": M2_CHANNEL},
        "M3_artifact": {"path": str(m3_path), "sha256": m3_sha, "idea": "M3", "component": "F", "channel": M3_CHANNEL},
        "frozen_S1_residual_artifact": {"path": str(residual_path), "sha256": RESIDUAL_SHA, "rows": EXPECTED_N,
          "report_path": str(RESIDUAL_REPORT), "report_sha256": sha256_file(RESIDUAL_REPORT)},
        "MEASURE_BEFORE_report": {"path": str(MX4_FEATURE_REPORT), "sha256": sha256_file(MX4_FEATURE_REPORT)},
        "fold_manifest": {"path": str(manifest_path), "sha256": manifest_sha, "rows": len(manifest_rows),
          "columns": ["GEOID", "region", "heldout_county_fips", "training_counties_sha256", "training_county_count", "training_support_n"]},
        "script_sha256": sha256_file(Path(__file__).resolve())},
      "interpretation": "The fixed county-held-out spline correction passed all gates; remain descriptive-only, with no estimator integration." if gate else
        "The fixed county-held-out spline correction failed one or more preregistered gates; retain the uncorrected frozen S1 residual and do not integrate this correction."}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/m2-m3-joint-nonlinear-residual.json")
    parser.add_argument("--manifest", default="reports/m2-m3-joint-nonlinear-fold-manifest.csv")
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report = run(workspace, ROOT / args.out, ROOT / args.manifest)
    print(json.dumps({"scope": report["scope"], "measure_after": {
        "pooled": report["measure_after"]["pooled"], "regional": report["measure_after"]["regional"],
        "worst_regional_delta_r2": report["measure_after"]["worst_regional_delta_r2"],
        "county_block_bootstrap": report["measure_after"]["county_block_bootstrap"]},
        "acceptance": report["acceptance"], "fold_manifest": report["provenance"]["fold_manifest"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
