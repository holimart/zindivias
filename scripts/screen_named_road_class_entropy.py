#!/usr/bin/env python3
"""Screen entropy of provided Overture named-road class length shares by official tract."""
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
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import official_scored_pairs  # noqa: E402
from audit_poi_categories import BUCKET, REGIONS  # noqa: E402
from audit_poi_categories import load_sample_tracts  # noqa: E402
from audit_mx4p_conditional_innovation import (  # noqa: E402
    distribution, pearson, parquet_bytes, read_emission,
)

EXPECTED_N = 9379
ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary")
EPSG_METRIC = "EPSG:5070"
MIN_COVERAGE = 0.50
MIN_VARIANCE_RATIO = 0.10
MAX_ABS_CORRELATION = 0.80
MX4_SHA = "8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af"
S1_SHA = "632829cf29281aa16487707cb555f52b4579cb0edfc0dd3faae360fa8f1dda03"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def entropy_from_class_lengths(class_lengths: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lengths = np.asarray(class_lengths, dtype=np.float64)
    if lengths.ndim != 2 or lengths.shape[1] != 4:
        raise ValueError("named-road class lengths must have shape (tracts,4)")
    if np.any(~np.isfinite(lengths)) or np.any(lengths < 0):
        raise ValueError("class lengths must be finite and nonnegative")
    totals = lengths.sum(axis=1)
    defined = totals > 0
    entropy = np.full(len(lengths), np.nan, dtype=np.float64)
    for i in np.flatnonzero(defined):
        shares = lengths[i, lengths[i] > 0] / totals[i]
        entropy[i] = -float(np.sum(shares * np.log(shares))) / np.log(4.0)
    if np.any(defined & (~np.isfinite(entropy) | (entropy < -1e-12) | (entropy > 1.0 + 1e-12))):
        raise ValueError("defined normalized class entropy is outside [0,1]")
    entropy[defined] = np.clip(entropy[defined], 0.0, 1.0)
    return entropy, defined, totals


def aggregate_region_roads(fs: Any, region: str, geoids: list[str]) -> tuple[np.ndarray, dict[str, Any]]:
    tract_ids, tract_geoms, _tree_unused, tract_meta = load_sample_tracts(fs, region, geoids)
    if tract_ids != geoids:
        raise ValueError("tract assignment order differs from official GEOIDs")
    transformer = Transformer.from_crs("EPSG:4326", EPSG_METRIC, always_xy=True)
    metric_tracts = shapely.transform(np.asarray(tract_geoms, dtype=object), transformer.transform,
                                      interleaved=False)
    metric_tree = shapely.STRtree(metric_tracts)
    key = f"{BUCKET}/reference/{region}/{region}-overture-roads.parquet"
    info = fs.get_file_info(key)
    if info.type != pafs.FileType.File:
        raise FileNotFoundError(f"provided roads Parquet missing: s3://{key}")
    parquet = pq.ParquetFile(fs.open_input_file(key))
    for field in ("class", "geometry"):
        if field not in parquet.schema_arrow.names:
            raise ValueError(f"provided roads source lacks {field}")
    lengths = np.zeros((len(geoids), 4), dtype=np.float64)
    class_index = {name: i for i, name in enumerate(ROAD_CLASSES)}
    selected_digest = hashlib.sha256()
    total_rows = named_rows = null_geometry = 0
    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=["class", "geometry"])
        classes = table.column("class").to_pylist()
        wkbs = table.column("geometry").to_pylist()
        total_rows += len(classes)
        selected = [i for i, value in enumerate(classes) if value in class_index]
        named_rows += len(selected)
        if not selected:
            continue
        selected_wkb = [wkbs[i] for i in selected]
        for i, class_value, wkb in zip(selected, (classes[i] for i in selected), selected_wkb):
            if wkb is None:
                null_geometry += 1
                raise ValueError(f"null geometry on provided named road ({class_value}) in {region}")
            selected_digest.update(str(class_value).encode("utf-8") + b"\0" + bytes(wkb) + b"\n")
        lines = shapely.from_wkb(selected_wkb)
        if np.any(shapely.is_empty(lines)) or np.any(~shapely.is_valid(lines)):
            raise ValueError(f"invalid geometry among provided named roads in {region}")
        metric_lines = shapely.transform(lines, transformer.transform, interleaved=False)
        road_idx, tract_idx = metric_tree.query(metric_lines, predicate="intersects")
        if road_idx.size:
            clipped = shapely.intersection(metric_lines[road_idx], metric_tracts[tract_idx])
            segment_lengths = shapely.length(clipped)
            valid = np.isfinite(segment_lengths) & (segment_lengths > 0)
            for class_name, category in class_index.items():
                use = valid & np.fromiter((classes[selected[int(j)]] == class_name for j in road_idx),
                                          dtype=bool, count=len(road_idx))
                if use.any():
                    lengths[:, category] += np.bincount(tract_idx[use], weights=segment_lengths[use],
                                                         minlength=len(geoids))
    if total_rows != parquet.metadata.num_rows:
        raise ValueError("not all provided roads source rows were scanned")
    lengths /= 1000.0
    return lengths, {
        "source_uri": f"s3://{key}", "object_bytes": int(info.size),
        "rows": int(parquet.metadata.num_rows), "row_groups": int(parquet.metadata.num_row_groups),
        "selected_columns": ["class", "geometry"], "selected_named_classes": list(ROAD_CLASSES),
        "named_class_rows": named_rows, "null_named_road_geometries": null_geometry,
        "metric_crs": EPSG_METRIC, "length_unit": "kilometres",
        "selected_named_class_rows_sha256": selected_digest.hexdigest(),
        "tract_assignment": tract_meta,
    }


def load_pct_urban(workspace: Path, official: list[tuple[str, str]]) -> tuple[np.ndarray, dict[str, Any]]:
    values = np.full(len(official), np.nan, dtype=np.float64)
    locations = {pair: i for i, pair in enumerate(official)}
    metadata: dict[str, Any] = {}
    for region in REGIONS:
        path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"provided strata input missing: {path}")
        table = pq.read_table(path, columns=["GEOID", "pct_urban"])
        seen: set[str] = set()
        for geoid, pct in zip(table.column("GEOID").to_pylist(), table.column("pct_urban").to_pylist()):
            geoid = str(geoid)
            pair = (geoid, region)
            if pair not in locations:
                continue
            if geoid in seen:
                raise ValueError(f"duplicate provided pct_urban GEOID: {geoid}")
            seen.add(geoid)
            values[locations[pair]] = float(pct) if pct is not None else np.nan
        expected = {g for g, r in official if r == region}
        if seen != expected:
            raise ValueError(f"provided pct_urban table does not exactly match {region} official GEOIDs")
        metadata[region] = {"path": str(path), "sha256": sha256_file(path),
                            "columns_read": ["GEOID", "pct_urban"],
                            "official_ids": len(seen), "defined_ids": int(np.isfinite(values[[locations[(g, region)] for g in expected]]).sum())}
    if np.any(np.isfinite(values) & ((values < 0) | (values > 1))):
        raise ValueError("defined provided pct_urban values must be in [0,1]")
    return values, metadata


def crossfit_region(geoids: list[str], region: str, target: np.ndarray, predictors: np.ndarray,
                    mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[dict[str, Any]]]:
    n = len(geoids)
    if region not in REGIONS or len(set(geoids)) != n:
        raise ValueError("cross-fit requires a known region and unique GEOIDs")
    y = np.asarray(target, dtype=np.float64)
    x = np.asarray(predictors, dtype=np.float64)
    defined = np.asarray(mask, dtype=bool)
    if y.shape != (n,) or x.shape[0] != n or x.ndim != 2 or defined.shape != (n,):
        raise ValueError("cross-fit inputs must align")
    if np.any(defined & (~np.isfinite(y) | ~np.isfinite(x).all(axis=1))):
        raise ValueError("cross-fit joint rows must be finite")
    folds = np.asarray([int.from_bytes(hashlib.sha256(f"named-road-entropy-v1|{region}|{g}".encode()).digest()[:8], "big") % 5
                        for g in geoids], dtype=np.int8)
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
                           "coefficient_order": ["region_intercept", "log_total_named_road_km", "pct_urban", "Mx4_F", "S1_lagged_Mx4_F"],
                           "coefficients": [float(v) for v in beta], "design_rank": int(rank),
                           "design_columns": int(train_design.shape[1]),
                           "minimum_singular_value": float(singular.min()) if singular.size else None})
    if np.any(defined & (~np.isfinite(prediction) | ~np.isfinite(residual))) or np.any(~defined & np.isfinite(residual)):
        raise ValueError("cross-fit residual mask does not preserve source support")
    return prediction, residual, folds, diagnostics


def output_table(official: list[tuple[str, str]], class_lengths: np.ndarray,
                 entropy: np.ndarray, entropy_mask: np.ndarray, total: np.ndarray,
                 pct_urban: np.ndarray, mx4: np.ndarray, mx4_mask: np.ndarray,
                 s1: np.ndarray, s1_mask: np.ndarray, joint: np.ndarray,
                 prediction: np.ndarray, residual: np.ndarray, folds: np.ndarray) -> pa.Table:
    if class_lengths.shape != (len(official), 4):
        raise ValueError("class lengths must align with official GEOID-region rows")
    if not np.array_equal(joint, entropy_mask & np.isfinite(pct_urban) & mx4_mask & s1_mask):
        raise ValueError("joint mask must preserve all exact source null partitions")
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        **{f"{name}_km": pa.array(class_lengths[:, i], type=pa.float64()) for i, name in enumerate(ROAD_CLASSES)},
        "total_named_road_km": pa.array(total, type=pa.float64()),
        "class_mix_entropy": pa.array([float(v) if ok else None for v, ok in zip(entropy, entropy_mask)], type=pa.float64()),
        "entropy_defined": pa.array(entropy_mask, type=pa.bool_()),
        "pct_urban": pa.array([float(v) if np.isfinite(v) else None for v in pct_urban], type=pa.float64()),
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
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N or len({g for g, _ in official}) != EXPECTED_N or set(r for _, r in official) != set(REGIONS):
        raise ValueError("official input must be an exact 9,379-ID/four-region partition")
    partition_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    class_lengths = np.zeros((EXPECTED_N, 4), dtype=np.float64)
    input_meta: dict[str, Any] = {}
    for region in REGIONS:
        indices = np.asarray([i for i, (_, r) in enumerate(official) if r == region], dtype=np.int64)
        geoids = [official[i][0] for i in indices]
        local_lengths, metadata = aggregate_region_roads(fs, region, geoids)
        class_lengths[indices] = local_lengths
        input_meta[region] = metadata
    entropy, entropy_mask, total = entropy_from_class_lengths(class_lengths)
    pct_urban, strata_meta = load_pct_urban(workspace, official)
    mx4_path, s1_path = workspace / "ideas/Mx4.parquet", workspace / "ideas/S1.parquet"
    mx4, mx4_mask, mx4_hash = read_emission(mx4_path, MX4_SHA, "Mx4", "F", "covariate", official)
    s1, s1_mask, s1_hash = read_emission(s1_path, S1_SHA, "S1", "F", "covariate:lag_mx4_f_covariate", official)
    if mx4_hash != MX4_SHA or s1_hash != S1_SHA:
        raise ValueError("frozen Mx4/S1 artifacts differ from expected hashes")
    log_total = np.full(EXPECTED_N, np.nan)
    log_total[entropy_mask] = np.log(total[entropy_mask])
    predictors = np.column_stack((log_total, pct_urban, mx4, s1))
    joint = entropy_mask & np.isfinite(pct_urban) & mx4_mask & s1_mask
    if not joint.any():
        raise ValueError("no complete cases for named-road class entropy cross-fit")
    prediction = np.full(EXPECTED_N, np.nan)
    residual = np.full(EXPECTED_N, np.nan)
    folds = np.full(EXPECTED_N, -1, dtype=np.int8)
    regional: dict[str, Any] = {}
    for region in REGIONS:
        indices = np.asarray([i for i, (_, r) in enumerate(official) if r == region], dtype=np.int64)
        geoids = [official[i][0] for i in indices]
        pred, resid, fold, diagnostics = crossfit_region(geoids, region, entropy[indices], predictors[indices], joint[indices])
        prediction[indices], residual[indices], folds[indices] = pred, resid, fold
        mask = joint[indices]
        regional[region] = {
            "official_ids": len(indices), "positive_named_road_length_ids": int(entropy_mask[indices].sum()),
            "eligible_joint_ids": int(mask.sum()), "positive_length_coverage": float(entropy_mask[indices].mean()),
            "entropy_distribution": distribution(entropy[indices][entropy_mask[indices]]),
            "crossfit_residual_distribution": distribution(resid[mask]),
            "residual_variance_ratio": float(resid[mask].var(ddof=0) / entropy[indices][mask].var(ddof=0)),
            "residual_pearson_correlations": {
                "log_total_named_road_km": pearson(resid[mask], log_total[indices][mask]),
                "pct_urban": pearson(resid[mask], pct_urban[indices][mask]),
                "Mx4_F": pearson(resid[mask], mx4[indices][mask]),
                "S1_lagged_Mx4_F": pearson(resid[mask], s1[indices][mask]),
            }, "folds": diagnostics,
        }
    raw, heldout = entropy[joint], residual[joint]
    variance_ratio = float(heldout.var(ddof=0) / raw.var(ddof=0)) if raw.var(ddof=0) > 0 else None
    correlations = {
        "log_total_named_road_km": pearson(heldout, log_total[joint]),
        "pct_urban": pearson(heldout, pct_urban[joint]),
        "Mx4_F": pearson(heldout, mx4[joint]),
        "S1_lagged_Mx4_F": pearson(heldout, s1[joint]),
    }
    coverage = float(entropy_mask.mean())
    coverage_pass = coverage >= MIN_COVERAGE
    bounded = bool(np.isfinite(entropy[entropy_mask]).all() and np.all((entropy[entropy_mask] >= 0) & (entropy[entropy_mask] <= 1)))
    variance_pass = variance_ratio is not None and variance_ratio >= MIN_VARIANCE_RATIO
    correlation_pass = all(v is not None and abs(v) < MAX_ABS_CORRELATION for v in correlations.values())
    registry_path = ROOT / "config/ideas.json"
    registry_before = sha256_file(registry_path)
    frozen_unchanged = (sha256_file(mx4_path) == MX4_SHA and sha256_file(s1_path) == S1_SHA
                        and sha256_file(registry_path) == registry_before)
    table = output_table(official, class_lengths, entropy, entropy_mask, total, pct_urban,
                         mx4, mx4_mask, s1, s1_mask, joint, prediction, residual, folds)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("diagnostic Parquet serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    acceptance = {
        "exact_official_9379_id_alignment": table.num_rows == EXPECTED_N and len(set(official)) == EXPECTED_N,
        "exact_entropy_null_mask_alignment": bool(np.array_equal(np.isfinite(entropy), entropy_mask)),
        "positive_named_road_length_coverage": coverage, "coverage_at_least_50_percent": bool(coverage_pass),
        "entropy_finite_and_bounded_0_1": bounded,
        "crossfit_residual_variance_ratio": variance_ratio,
        "residual_variance_at_least_10_percent": bool(variance_pass),
        "residual_pearson_correlations": correlations,
        "absolute_correlation_below_0_80_with_each_predictor": bool(correlation_pass),
        "frozen_predictors_and_registry_unchanged": bool(frozen_unchanged),
        "distinct_screen_pass": bool(coverage_pass and bounded and variance_pass and correlation_pass and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "screen": "Provided Overture motorway/trunk/primary/secondary class-length-share Shannon entropy; five-fold cross-fit distinctness screen; unregistered",
        "measure_before": {"feature_inventory": "Frozen transport summaries contain total named-road length; no normalized four-class length-share entropy or distinctness report was present.",
                           "tract_entropy_or_crossfit_distinctness_previously_measured": False},
        "definition": "For each official tract, clip provided Overture motorway, trunk, primary, and secondary line geometries to provided official tract geometry after EPSG:5070 transform. Let p_c be each class's share of total named-road length; entropy is -sum(p_c log p_c)/log(4). It is null where total length is zero.",
        "method": {"road_inputs": "Only the provided Overture roads class and geometry columns are read; routes, names, topology, TIGER/equivalent references, and other road data are not read.",
                   "cross_fit": "Five deterministic GEOID folds within each region. Region-specific OLS with intercept uses log total named-road km, provided pct_urban, frozen Mx4 F, and frozen S1 lagged Mx4 F; each held-out fold is predicted by the other four folds. No labels, tuning, or imputation.",
                   "variance_ratio": "Pooled population variance (ddof=0) of held-out entropy residual divided by entropy variance on the same complete-case support.",
                   "correlations": "Pooled Pearson correlations of held-out residual with each of the four adjustment predictors; regional values are descriptive.",
                   "undefined": "Zero total named-road length remains null; missing predictor values are not imputed and are excluded only from joint cross-fit support.",
                   "unregistered": True},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": partition_sha,
                  "positive_named_road_length_ids": int(entropy_mask.sum()), "positive_length_coverage_fraction": coverage,
                  "entropy_undefined_ids": int((~entropy_mask).sum()), "joint_crossfit_ids": int(joint.sum()),
                  "regions": regional, "labels_read": False, "sample_score_values_read": False,
                  "TIGER_or_equivalent_reference_read": False, "external_road_data_read": False,
                  "route_or_topology_inputs_read": False, "leaderboard_feedback_used": False,
                  "registry_integration": False, "uploads": 0},
        "measure_after": {"entropy_distribution": distribution(entropy[entropy_mask]),
                          "class_length_km_sum": {name: float(class_lengths[:, i].sum()) for i, name in enumerate(ROAD_CLASSES)},
                          "total_named_road_km_distribution": distribution(total[entropy_mask]),
                          "residual_variance_ratio": variance_ratio, "residual_pearson_correlations": correlations,
                          "crossfit_residual_distribution": distribution(heldout)},
        "thresholds": {"minimum_positive_length_coverage_inclusive": MIN_COVERAGE,
                       "minimum_residual_variance_ratio_inclusive": MIN_VARIANCE_RATIO,
                       "maximum_absolute_residual_predictor_correlation_exclusive": MAX_ABS_CORRELATION,
                       "entropy_bounds_inclusive": [0, 1]},
        "acceptance": acceptance,
        "provenance": {"road_inputs": input_meta, "pct_urban_inputs": strata_meta,
                       "official_geoid_region_sha256": partition_sha,
                       "Mx4": {"sha256": MX4_SHA, "path": str(mx4_path)},
                       "S1": {"sha256": S1_SHA, "path": str(s1_path)},
                       "registry_sha256_before_after": {"before": registry_before, "after": sha256_file(registry_path)},
                       "output_artifact": {"path": str(artifact_path), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": table.num_rows, "columns": table.column_names,
                                           "deterministic_serialization": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The feature passed all preregistered coverage, boundedness, residual-variance and correlation gates; retain as an unregistered diagnostic only." if acceptance["distinct_screen_pass"] else
                         "The feature fails at least one preregistered support/distinctness gate; retire as an unsupported or redundant unregistered diagnostic.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/named-road-class-entropy.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "named-road-class-entropy/class-entropy-crossfit.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"acceptance": report["acceptance"], "measure_after": report["measure_after"],
                      "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
