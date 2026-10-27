#!/usr/bin/env python3
"""Screen normalized entropy of disjoint provided POI provenance-class shares."""
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
from audit_mx4p_conditional_innovation import distribution, pearson, parquet_bytes, read_emission  # noqa: E402

EXPECTED_N = 9379
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
SOURCE_CLASSES = (
    "meta_not_microsoft", "microsoft_not_meta", "meta_and_microsoft",
    "other_without_meta_microsoft", "no_upstream",
)
SHARE_COLUMNS = tuple(f"overture_poi_{name}_source_share" for name in SOURCE_CLASSES)
COUNT_COLUMN = "overture_poi_all"
MIN_COVERAGE = 0.9859
MIN_VARIANCE_RATIO = 0.10
MAX_ABS_CORRELATION = 0.80
MX4_SHA = "8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af"
S1_SHA = "632829cf29281aa16487707cb555f52b4579cb0edfc0dd3faae360fa8f1dda03"
SOURCE_REPORT = ROOT / "reports/source-provenance.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def entropy_from_shares(shares: np.ndarray, poi_count: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(shares, dtype=np.float64)
    counts = np.asarray(poi_count, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 5 or counts.shape != (values.shape[0],):
        raise ValueError("source shares must have shape (tracts,5) and align with POI counts")
    if np.any(np.isfinite(counts) & (counts < 0)):
        raise ValueError("provided POI counts must be nonnegative")
    finite = np.isfinite(values).all(axis=1)
    if np.any(finite[:, None] & ((values < -1e-12) | (values > 1. + 1e-12))):
        raise ValueError("defined source shares must lie in [0,1]")
    sums = values.sum(axis=1)
    if np.any(finite & (np.abs(sums - 1.) > 1e-9)):
        raise ValueError("the five exclusive source shares must sum to one")
    if np.any(finite & (~np.isfinite(counts) | (counts <= 0))):
        raise ValueError("finite source shares require positive finite POI count")
    defined = finite & np.isfinite(counts) & (counts > 0)
    if not np.array_equal(finite, np.isfinite(counts) & (counts > 0)):
        raise ValueError("source-share defined mask must exactly match positive POI-count support")
    entropy = np.full(len(counts), np.nan, dtype=np.float64)
    for i in np.flatnonzero(defined):
        p = values[i, values[i] > 0]
        entropy[i] = -float(np.sum(p * np.log(p))) / np.log(5.)
    if np.any(defined & (~np.isfinite(entropy) | (entropy < -1e-12) | (entropy > 1. + 1e-12))):
        raise ValueError("defined normalized source entropy is outside [0,1]")
    entropy[defined] = np.clip(entropy[defined], 0., 1.)
    return entropy, defined


def load_region_cache(workspace: Path, region: str, expected_geoids: list[str],
                      source_report: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    feature_path = workspace / "gap-estimator/features" / f"{region}-overture-tract-summary.parquet"
    sidecar_path = feature_path.with_suffix(".json")
    if not feature_path.is_file() or not sidecar_path.is_file():
        raise FileNotFoundError(f"existing source-provenance feature cache/sidecar missing for {region}")
    feature_sha, sidecar_sha = sha256_file(feature_path), sha256_file(sidecar_path)
    metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))
    prior_cache = source_report["regions"][region]["feature_cache"]
    # Route extension later rewrote the cache while declaring the source-mix cache as its base.
    if metadata.get("route_extension_base_feature_cache_sha256") != prior_cache.get("sha256"):
        raise ValueError(f"{region} current cache does not descend from the audited source-mix cache")
    if metadata.get("feature_cache_sha256") != feature_sha:
        raise ValueError(f"{region} cache sidecar hash does not match the existing feature cache")
    if metadata.get("feature_cache") != str(feature_path):
        raise ValueError(f"{region} cache sidecar points to an unexpected cache path")
    columns = ["GEOID", COUNT_COLUMN, *SHARE_COLUMNS]
    table = pq.read_table(feature_path, columns=columns)
    if table.num_rows != len(expected_geoids):
        raise ValueError(f"{region} cache row count does not match official GEOIDs")
    cache_ids = [str(value) for value in table.column("GEOID").to_pylist()]
    if len(set(cache_ids)) != len(cache_ids) or set(cache_ids) != set(expected_geoids):
        raise ValueError(f"{region} cache GEOID set differs from official sample")
    positions = {geoid: i for i, geoid in enumerate(cache_ids)}
    order = np.asarray([positions[g] for g in expected_geoids], dtype=np.int64)
    poi_count = np.asarray(table.column(COUNT_COLUMN).to_pylist(), dtype=np.float64)[order]
    shares = np.column_stack([np.asarray(table.column(name).to_pylist(), dtype=np.float64)[order]
                              for name in SHARE_COLUMNS])
    # Reconcile cache shares back to the provenance source-row counts; this proves the
    # route-extended cache retained the five audited mutually exclusive classes.
    source_meta = metadata.get("source_mix", {}).get("pois", {})
    class_counts = source_meta.get("assigned_source_counts", {})
    if not source_meta or source_meta.get("source_classes_reconciled_to_core_features") is False:
        raise ValueError(f"{region} source-provenance cache lacks a reconciled POI class audit")
    weighted = np.nansum(shares * poi_count[:, None], axis=0)
    expected = np.asarray([class_counts.get(f"class_{name}") for name in SOURCE_CLASSES], dtype=np.float64)
    if not np.isfinite(expected).all() or not np.allclose(weighted, expected, rtol=0., atol=1e-6):
        raise ValueError(f"{region} cached class shares do not reconcile to audited source counts")
    return shares, poi_count, {
        "feature_cache_path": str(feature_path), "feature_cache_sha256": feature_sha,
        "sidecar_path": str(sidecar_path), "sidecar_sha256": sidecar_sha,
        "source_report_cache_sha256": prior_cache["sha256"],
        "route_extension_base_cache_sha256": metadata["route_extension_base_feature_cache_sha256"],
        "columns_read": columns, "rows": table.num_rows,
        "official_geoid_exact_set": True, "class_share_weighted_totals": weighted.tolist(),
        "class_counts_from_source_sidecar": expected.tolist(),
        "source_classes_reconciled": True,
    }


def crossfit_region(geoids: list[str], region: str, target: np.ndarray,
                    predictors: np.ndarray, mask: np.ndarray
                    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[dict[str, Any]]]:
    n = len(geoids)
    if region not in REGIONS or len(set(geoids)) != n:
        raise ValueError("cross-fit requires a known region and unique GEOIDs")
    y = np.asarray(target, dtype=np.float64)
    x = np.asarray(predictors, dtype=np.float64)
    defined = np.asarray(mask, dtype=bool)
    if y.shape != (n,) or x.ndim != 2 or x.shape[0] != n or defined.shape != (n,):
        raise ValueError("cross-fit inputs must align with region GEOIDs")
    if np.any(defined & (~np.isfinite(y) | ~np.isfinite(x).all(axis=1))):
        raise ValueError("jointly defined cross-fit rows must be finite")
    folds = np.asarray([
        int.from_bytes(hashlib.sha256(f"poi-source-class-entropy-v1|{region}|{g}".encode()).digest()[:8], "big") % 5
        for g in geoids
    ], dtype=np.int8)
    prediction = np.full(n, np.nan)
    residual = np.full(n, np.nan)
    diagnostics = []
    for fold in range(5):
        test, train = defined & (folds == fold), defined & (folds != fold)
        if int(test.sum()) == 0 or int(train.sum()) <= x.shape[1]:
            raise ValueError(f"{region} fold {fold} has insufficient fit support")
        train_design = np.column_stack((np.ones(int(train.sum())), x[train]))
        test_design = np.column_stack((np.ones(int(test.sum())), x[test]))
        beta, _, rank, singular = np.linalg.lstsq(train_design, y[train], rcond=None)
        prediction[test] = test_design @ beta
        residual[test] = y[test] - prediction[test]
        diagnostics.append({"fold": fold, "train_n": int(train.sum()), "heldout_n": int(test.sum()),
                            "coefficient_order": ["region_intercept", "log1p_poi_count", "Mx4_F", "S1_lagged_Mx4_F"],
                            "coefficients": [float(v) for v in beta], "design_rank": int(rank),
                            "design_columns": int(train_design.shape[1]),
                            "minimum_singular_value": float(singular.min()) if singular.size else None})
    if np.any(defined & (~np.isfinite(prediction) | ~np.isfinite(residual))) or np.any(~defined & np.isfinite(residual)):
        raise ValueError("cross-fit outputs do not preserve the exact joint source mask")
    return prediction, residual, folds, diagnostics


def make_output(official: list[tuple[str, str]], shares: np.ndarray, poi_count: np.ndarray,
                entropy: np.ndarray, entropy_mask: np.ndarray, mx4: np.ndarray, mx4_mask: np.ndarray,
                s1: np.ndarray, s1_mask: np.ndarray, joint: np.ndarray,
                prediction: np.ndarray, residual: np.ndarray, folds: np.ndarray) -> pa.Table:
    n = len(official)
    if any(np.asarray(value).shape[0] != n for value in
           (shares, poi_count, entropy, entropy_mask, mx4, mx4_mask, s1, s1_mask, joint, prediction, residual, folds)):
        raise ValueError("diagnostic columns do not align with exact official GEOIDs")
    if not np.array_equal(joint, entropy_mask & mx4_mask & s1_mask):
        raise ValueError("cross-fit mask differs from entropy/Mx4/S1 mask intersection")
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        **{f"{name}_share": pa.array([float(v) if ok else None for v, ok in zip(shares[:, i], entropy_mask)], type=pa.float64())
           for i, name in enumerate(SOURCE_CLASSES)},
        "provided_poi_count": pa.array(poi_count, type=pa.float64()),
        "source_class_entropy": pa.array([float(v) if ok else None for v, ok in zip(entropy, entropy_mask)], type=pa.float64()),
        "entropy_defined": pa.array(entropy_mask, type=pa.bool_()),
        "log1p_poi_count": pa.array([float(np.log1p(v)) if np.isfinite(v) else None for v in poi_count], type=pa.float64()),
        "Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(mx4, mx4_mask)], type=pa.float64()),
        "Mx4_F_defined": pa.array(mx4_mask, type=pa.bool_()),
        "S1_lagged_Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(s1, s1_mask)], type=pa.float64()),
        "S1_lagged_Mx4_F_defined": pa.array(s1_mask, type=pa.bool_()),
        "joint_defined": pa.array(joint, type=pa.bool_()),
        "fold": pa.array(folds, type=pa.int8()),
        "crossfit_prediction": pa.array([float(v) if ok else None for v, ok in zip(prediction, joint)], type=pa.float64()),
        "crossfit_residual": pa.array([float(v) if ok else None for v, ok in zip(residual, joint)], type=pa.float64()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    source_report = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    if source_report["source_definitions"]["place_exclusive_classes"] != list(SOURCE_CLASSES):
        raise ValueError("five source classes differ from the frozen source-provenance definitions")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N
            or len({g for g, _ in official}) != EXPECTED_N or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official input must be an exact 9,379-ID/four-region partition")
    partition_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    all_shares = np.full((EXPECTED_N, 5), np.nan)
    poi_count = np.full(EXPECTED_N, np.nan)
    cache_metadata: dict[str, Any] = {}
    for region in REGIONS:
        indices = np.asarray([i for i, (_, reg) in enumerate(official) if reg == region], dtype=np.int64)
        ids = [official[i][0] for i in indices]
        shares, counts, meta = load_region_cache(workspace, region, ids, source_report)
        all_shares[indices] = shares
        poi_count[indices] = counts
        cache_metadata[region] = meta
    entropy, entropy_mask = entropy_from_shares(all_shares, poi_count)
    mx4_path, s1_path = workspace / "ideas/Mx4.parquet", workspace / "ideas/S1.parquet"
    mx4, mx4_mask, mx4_hash = read_emission(mx4_path, MX4_SHA, "Mx4", "F", "covariate", official)
    s1, s1_mask, s1_hash = read_emission(s1_path, S1_SHA, "S1", "F", "covariate:lag_mx4_f_covariate", official)
    if mx4_hash != MX4_SHA or s1_hash != S1_SHA:
        raise ValueError("frozen predictor artifact hashes differ from audited baselines")
    log_count = np.log1p(poi_count)
    predictors = np.column_stack((log_count, mx4, s1))
    joint = entropy_mask & mx4_mask & s1_mask
    prediction = np.full(EXPECTED_N, np.nan)
    residual = np.full(EXPECTED_N, np.nan)
    folds = np.full(EXPECTED_N, -1, dtype=np.int8)
    regional: dict[str, Any] = {}
    for region in REGIONS:
        indices = np.asarray([i for i, (_, reg) in enumerate(official) if reg == region], dtype=np.int64)
        ids = [official[i][0] for i in indices]
        pred, resid, fold, fold_info = crossfit_region(ids, region, entropy[indices], predictors[indices], joint[indices])
        prediction[indices], residual[indices], folds[indices] = pred, resid, fold
        selected = joint[indices]
        x = predictors[indices]
        regional[region] = {
            "official_ids": int(len(indices)), "entropy_defined_ids": int(entropy_mask[indices].sum()),
            "joint_crossfit_ids": int(selected.sum()), "coverage_fraction": float(entropy_mask[indices].mean()),
            "entropy_distribution": distribution(entropy[indices][entropy_mask[indices]]),
            "crossfit_residual_distribution": distribution(resid[selected]),
            "residual_variance_ratio": float(resid[selected].var(ddof=0) / entropy[indices][selected].var(ddof=0)),
            "residual_pearson_correlations": {
                "log1p_poi_count": pearson(resid[selected], log_count[indices][selected]),
                "Mx4_F": pearson(resid[selected], mx4[indices][selected]),
                "S1_lagged_Mx4_F": pearson(resid[selected], s1[indices][selected]),
            }, "folds": fold_info,
        }
    y, resid = entropy[joint], residual[joint]
    variance_ratio = float(resid.var(ddof=0) / y.var(ddof=0)) if y.var(ddof=0) > 0 else None
    correlations = {
        "log1p_poi_count": pearson(resid, log_count[joint]),
        "Mx4_F": pearson(resid, mx4[joint]),
        "S1_lagged_Mx4_F": pearson(resid, s1[joint]),
    }
    coverage = float(entropy_mask.mean())
    coverage_pass = coverage >= MIN_COVERAGE
    bounded = bool(np.isfinite(entropy[entropy_mask]).all() and np.all((entropy[entropy_mask] >= 0.) & (entropy[entropy_mask] <= 1.)))
    variance_pass = variance_ratio is not None and variance_ratio >= MIN_VARIANCE_RATIO
    correlation_pass = all(v is not None and abs(v) < MAX_ABS_CORRELATION for v in correlations.values())
    registry_path = ROOT / "config/ideas.json"
    registry_before = sha256_file(registry_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if any(item.get("id") == "poi_source_class_entropy" for item in registry.get("ideas", [])):
        raise ValueError("POI source-class entropy must remain unregistered")
    frozen_unchanged = (sha256_file(mx4_path) == MX4_SHA and sha256_file(s1_path) == S1_SHA
                        and sha256_file(registry_path) == registry_before)
    table = make_output(official, all_shares, poi_count, entropy, entropy_mask, mx4, mx4_mask,
                        s1, s1_mask, joint, prediction, residual, folds)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("diagnostic Parquet serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    acceptance = {
        "exact_official_9379_id_alignment": table.num_rows == EXPECTED_N and len(set(official)) == EXPECTED_N,
        "exact_entropy_null_mask_alignment": bool(np.array_equal(np.isfinite(entropy), entropy_mask)),
        "entropy_defined_ids": int(entropy_mask.sum()), "coverage_fraction": coverage,
        "coverage_at_least_98_59_percent": bool(coverage_pass), "entropy_finite_and_bounded_0_1": bounded,
        "crossfit_residual_variance_ratio": variance_ratio,
        "residual_variance_at_least_10_percent": bool(variance_pass),
        "residual_pearson_correlations": correlations,
        "absolute_correlation_below_0_80_with_each_predictor": bool(correlation_pass),
        "frozen_predictors_and_registry_unchanged": bool(frozen_unchanged),
        "distinct_screen_pass": bool(coverage_pass and bounded and variance_pass and correlation_pass and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "screen": "Normalized entropy of the five provided mutually exclusive POI source-class shares; five-fold cross-fit distinctness diagnostic; unregistered",
        "measure_before": {"source_provenance_report": "Five disjoint per-tract POI provenance classes and shares are documented; no tract entropy or residual distinctness result was in the report.",
                           "source_provenance_report_sha256": sha256_file(SOURCE_REPORT),
                           "tract_entropy_or_crossfit_distinctness_previously_measured": False},
        "definition": "For each tract with a defined five-class partition, H/log(5) = -sum(p_c log p_c)/log(5), with 0 log 0 terms set to zero. Shares are taken directly from the existing discovery-only provided source-provenance feature cache; no categories are reclassified or filtered. Null source-share rows remain null.",
        "method": {"source_classes": list(SOURCE_CLASSES), "source_share_columns": list(SHARE_COLUMNS),
                   "cross_fit": "Five deterministic GEOID folds within each region. Region-specific OLS with intercept, log1p provided Overture POI count, frozen Mx4 F, and frozen S1 lagged Mx4 F is trained on four folds and predicts the held-out fold. No labels, tuning, or imputation.",
                   "variance_ratio": "Pooled population variance (ddof=0) of held-out entropy residual divided by entropy variance on the same complete-case support.",
                   "correlations": "Pooled Pearson correlations of held-out residual with each adjustment predictor; regional diagnostics are descriptive.",
                   "nulls": "Preserve source cache null masks; entropy undefined unless all five shares and positive POI count are defined; joint cross-fit additionally requires both frozen emissions.",
                   "interpretation_limit": "Source-class mix describes recorded upstream provenance only and does not prove data correctness, quality, completeness, or local mapping activity.",
                   "unregistered": True},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": partition_sha,
                  "entropy_defined_ids": int(entropy_mask.sum()), "entropy_undefined_ids": int((~entropy_mask).sum()),
                  "coverage_fraction": coverage, "joint_crossfit_ids": int(joint.sum()), "regions": regional,
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_reference_layers_read": False, "external_data_read": False,
                  "leaderboard_feedback_used": False, "registry_integration": False, "uploads": 0},
        "measure_after": {"entropy_distribution": distribution(entropy[entropy_mask]),
                          "class_share_sums_distribution": distribution(all_shares[entropy_mask].sum(axis=1)),
                          "residual_variance_ratio": variance_ratio,
                          "residual_pearson_correlations": correlations,
                          "crossfit_residual_distribution": distribution(resid)},
        "thresholds": {"minimum_coverage_inclusive": MIN_COVERAGE,
                       "minimum_residual_variance_ratio_inclusive": MIN_VARIANCE_RATIO,
                       "maximum_absolute_residual_predictor_correlation_exclusive": MAX_ABS_CORRELATION,
                       "entropy_bounds_inclusive": [0., 1.]},
        "acceptance": acceptance,
        "provenance": {"cache_inputs": cache_metadata,
                       "source_provenance_report_sha256": sha256_file(SOURCE_REPORT),
                       "official_geoid_region_sha256": partition_sha,
                       "Mx4": {"sha256": MX4_SHA, "path": str(mx4_path)},
                       "S1": {"sha256": S1_SHA, "path": str(s1_path)},
                       "registry_sha256_before_after": {"before": registry_before, "after": sha256_file(registry_path)},
                       "output_artifact": {"path": str(artifact_path), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": table.num_rows, "columns": table.column_names,
                                           "deterministic_serialization": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "All preregistered support, boundedness, residual-variance, and predictor-correlation gates pass; retain only as an unregistered intrinsic-provenance diagnostic." if acceptance["distinct_screen_pass"] else
                         "The feature fails at least one preregistered support/distinctness gate; retire as redundant or unsupported and do not integrate.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/poi-source-class-entropy.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "poi-source-class-entropy/source-class-entropy-crossfit.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"acceptance": report["acceptance"], "measure_after": report["measure_after"],
                      "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
