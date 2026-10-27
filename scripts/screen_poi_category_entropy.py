#!/usr/bin/env python3
"""Screen normalized primary-category entropy from provided Overture POIs by tract."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import official_scored_pairs  # noqa: E402
from audit_poi_categories import BUCKET, REGIONS, _bbox_centers, assign_points, load_sample_tracts  # noqa: E402
from audit_mx4p_conditional_innovation import (  # noqa: E402
    crossfit_region, pearson, distribution, read_emission, parquet_bytes,
)

EXPECTED_N = 9379
CATEGORY_REPORT = ROOT / "reports/poi-category-audit.json"
MIN_PLACES = 10
MIN_COVERAGE = 0.10
MIN_VARIANCE_RATIO = 0.10
MAX_ABS_RESIDUAL_CORRELATION = 0.80
MX4_SHA = "8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af"
S1_SHA = "632829cf29281aa16487707cb555f52b4579cb0edfc0dd3faae360fa8f1dda03"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def entropy_by_tract(category_counts: np.ndarray, unique_place_counts: np.ndarray,
                     *, min_places: int = MIN_PLACES) -> dict[str, np.ndarray]:
    counts = np.asarray(category_counts)
    places = np.asarray(unique_place_counts, dtype=np.int64)
    if counts.ndim != 2 or counts.shape[0] != places.size or counts.shape[1] < 1:
        raise ValueError("tract category counts must have shape (tracts, observed primary categories)")
    if np.any(counts < 0) or not np.issubdtype(counts.dtype, np.integer) or np.any(places < 0):
        raise ValueError("counts must be nonnegative integers")
    categorized = counts.sum(axis=1, dtype=np.int64)
    defined = (places >= min_places) & (categorized > 0)
    entropy = np.full(places.shape, np.nan, dtype=np.float64)
    for i in np.flatnonzero(defined):
        p = counts[i, counts[i] > 0].astype(np.float64) / float(categorized[i])
        entropy[i] = float(-np.sum(p * np.log(p)) / np.log(75.0))
    if np.any(defined & (~np.isfinite(entropy) | (entropy < 0.))):
        raise ValueError("defined normalized entropy must be finite and nonnegative")
    return {"entropy": entropy, "defined": defined,
            "unique_place_counts": places, "categorized_place_counts": categorized}


def read_region_places(fs: Any, region: str, geoids: list[str]
                       ) -> tuple[list[Counter[str]], np.ndarray, dict[str, Any]]:
    tract_geoids, _geoms, tree, tract_meta = load_sample_tracts(fs, region, geoids)
    tract_index = {geoid: i for i, geoid in enumerate(tract_geoids)}
    key = f"{BUCKET}/reference/{region}/{region}-overture-pois.parquet"
    info = fs.get_file_info(key)
    if info.type != pafs.FileType.File:
        raise FileNotFoundError(f"provided Overture POI Parquet not found: s3://{key}")
    parquet = pq.ParquetFile(fs.open_input_file(key))
    required = {"id", "categories", "bbox"}
    if not required.issubset(parquet.schema_arrow.names):
        raise ValueError("POI source lacks id, categories or bbox fields")
    categories_type = parquet.schema_arrow.field("categories").type
    if not pa.types.is_struct(categories_type) or "primary" not in categories_type.names:
        raise ValueError("provided categories.primary field is not present")
    tract_category_counts = [Counter() for _ in geoids]
    place_counts = np.zeros(len(geoids), dtype=np.int64)
    # Keep only ID -> assigned tract and primary category for cross-row duplicate validation.
    seen: dict[str, tuple[int, str | None]] = {}
    duplicate_rows = 0
    null_id_rows = 0
    null_primary_ids = 0
    unmatched_unique_ids = 0
    input_rows = 0
    assigned_unique_ids = 0
    selected_digest = hashlib.sha256()
    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=["id", "categories.primary", "bbox"])
        input_rows += table.num_rows
        x, y = _bbox_centers(table)
        tract_idxs = assign_points(tree, x, y)
        ids = table.column("id").to_pylist()
        category_struct = table.column("categories").combine_chunks()
        primary_values = category_struct.field("primary").to_pylist()
        if not (len(ids) == len(primary_values) == len(tract_idxs) == table.num_rows):
            raise ValueError(f"selected POI column lengths differ in {region} row group {rg}")
        bbox = table.column("bbox").combine_chunks()
        bbox_fields = [bbox.field(field).to_pylist() for field in ("xmin", "ymin", "xmax", "ymax")]
        for i, raw_id in enumerate(ids):
            if raw_id is None or not str(raw_id).strip():
                null_id_rows += 1
                continue
            place_id = str(raw_id)
            raw_primary = primary_values[i]
            primary = str(raw_primary).strip() if raw_primary is not None and str(raw_primary).strip() else None
            assigned_idx = int(tract_idxs[i])
            # Hash only the explicitly permitted selected fields in source row order.
            record = [place_id, primary, *[float(field[i]) for field in bbox_fields]]
            selected_digest.update(json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
            selected_digest.update(b"\n")
            if place_id in seen:
                duplicate_rows += 1
                old_idx, old_primary = seen[place_id]
                if (old_idx, old_primary) != (assigned_idx, primary):
                    raise ValueError(f"duplicate place ID has conflicting tract/category assignment: {place_id}")
                continue
            seen[place_id] = (assigned_idx, primary)
            if assigned_idx < 0:
                unmatched_unique_ids += 1
                continue
            assigned_unique_ids += 1
            place_counts[assigned_idx] += 1
            if primary is None:
                null_primary_ids += 1
            else:
                tract_category_counts[assigned_idx][primary] += 1
    if input_rows != parquet.metadata.num_rows:
        raise ValueError("not all POI source rows were read")
    return tract_category_counts, place_counts, {
        "source_uri": f"s3://{key}", "object_bytes": int(info.size),
        "rows": int(parquet.metadata.num_rows), "row_groups": int(parquet.metadata.num_row_groups),
        "selected_columns": ["id", "categories.primary", "bbox"],
        "official_tract_columns": ["GEOID", "geometry"],
        "selected_fields_sha256": selected_digest.hexdigest(),
        "unique_nonnull_place_ids": len(seen), "duplicate_rows_deduplicated": duplicate_rows,
        "null_id_rows_excluded": null_id_rows, "assigned_unique_place_ids": assigned_unique_ids,
        "unmatched_unique_place_ids": unmatched_unique_ids,
        "assigned_ids_with_null_primary": null_primary_ids,
        "category_count_sum": int(sum(sum(c.values()) for c in tract_category_counts)),
        "observed_primary_category_support": sorted({c for tract in tract_category_counts for c in tract}),
        "tract_assignment": tract_meta,
    }


def output_table(pairs: list[tuple[str, str]], primary_counts: np.ndarray,
                 summary: dict[str, np.ndarray], mx4: np.ndarray, mx4_mask: np.ndarray,
                 s1: np.ndarray, s1_mask: np.ndarray, joint: np.ndarray,
                 prediction: np.ndarray, residual: np.ndarray, folds: np.ndarray) -> pa.Table:
    n = len(pairs)
    arrays = (primary_counts, summary["entropy"], summary["defined"], summary["unique_place_counts"],
              summary["categorized_place_counts"], mx4, mx4_mask, s1, s1_mask, joint, prediction, residual, folds)
    if any(np.asarray(x).shape[0] != n for x in arrays):
        raise ValueError("output arrays must align with official GEOID-region pairs")
    expected = summary["defined"] & mx4_mask & s1_mask
    if not np.array_equal(joint, expected) or np.any(~joint & (np.isfinite(residual) | np.isfinite(prediction))):
        raise ValueError("crossfit output mask must equal exact defined entropy/predictor intersection")
    return pa.table({
        "GEOID": pa.array([g for g, _ in pairs], type=pa.string()),
        "region": pa.array([r for _, r in pairs], type=pa.string()),
        "unique_assigned_place_count": pa.array(summary["unique_place_counts"], type=pa.int64()),
        "primary_categorized_place_count": pa.array(summary["categorized_place_counts"], type=pa.int64()),
        "normalized_primary_category_entropy": pa.array([float(v) if ok else None for v, ok in zip(summary["entropy"], summary["defined"])], type=pa.float64()),
        "entropy_defined": pa.array(summary["defined"], type=pa.bool_()),
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
    category_report = json.loads(CATEGORY_REPORT.read_text(encoding="utf-8"))
    audit_categories = category_report.get("category_values_across_regions", [])
    if len(audit_categories) != 75 or len(set(audit_categories)) != 75:
        raise ValueError("frozen POI category audit baseline must retain its 75 enumerated values")
    pairs = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(pairs) != EXPECTED_N or len(set(pairs)) != EXPECTED_N
            or len({g for g, _ in pairs}) != EXPECTED_N or set(r for _, r in pairs) != set(REGIONS)):
        raise ValueError("official GEOID-region input must be an exact 9,379-ID/four-region partition")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in pairs).encode()).hexdigest()
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    category_counters: list[Counter[str] | None] = [None] * EXPECTED_N
    place_counts = np.zeros(EXPECTED_N, dtype=np.int64)
    source_regions: dict[str, Any] = {}
    for region in REGIONS:
        indices = np.asarray([i for i, (_, r) in enumerate(pairs) if r == region], dtype=np.int64)
        geoids = [pairs[i][0] for i in indices]
        counters, nplaces, meta = read_region_places(fs, region, geoids)
        for index, counter in zip(indices, counters):
            category_counters[int(index)] = counter
        place_counts[indices] = nplaces
        source_regions[region] = meta
    categories = sorted({category for counter in category_counters if counter is not None for category in counter})
    if not categories:
        raise ValueError("no nonnull provided primary categories were assigned to official tracts")
    category_index = {category: i for i, category in enumerate(categories)}
    category_counts = np.zeros((EXPECTED_N, len(categories)), dtype=np.int64)
    for i, counter in enumerate(category_counters):
        if counter is not None:
            for category, count in counter.items():
                category_counts[i, category_index[category]] = count
    entropy = entropy_by_tract(category_counts, place_counts)
    entropy_values = entropy["entropy"]
    entropy_mask = entropy["defined"]
    mx4_path, s1_path = workspace / "ideas/Mx4.parquet", workspace / "ideas/S1.parquet"
    mx4, mx4_mask, mx4_file_sha = read_emission(mx4_path, MX4_SHA, "Mx4", "F", "covariate", pairs)
    s1, s1_mask, s1_file_sha = read_emission(s1_path, S1_SHA, "S1", "F", "covariate:lag_mx4_f_covariate", pairs)
    if mx4_file_sha != MX4_SHA or s1_file_sha != S1_SHA:
        raise ValueError("frozen predictor artifact hashes differ from audited baseline")
    joint = entropy_mask & mx4_mask & s1_mask
    if not joint.any():
        raise ValueError("no eligible complete cases for cross-fit residualization")
    prediction = np.full(EXPECTED_N, np.nan)
    residual = np.full(EXPECTED_N, np.nan)
    fold_ids = np.full(EXPECTED_N, -1, dtype=np.int8)
    regional: dict[str, Any] = {}
    for region in REGIONS:
        idx = np.asarray([i for i, (_, r) in enumerate(pairs) if r == region], dtype=np.int64)
        ids = [pairs[i][0] for i in idx]
        pred, resid, fold_report, folds = crossfit_region(
            ids, region, entropy_values[idx], mx4[idx], s1[idx], joint[idx])
        prediction[idx], residual[idx], fold_ids[idx] = pred, resid, folds
        local = joint[idx]
        raw, rem = entropy_values[idx][local], resid[local]
        regional[region] = {
            "official_ids": int(idx.size), "tracts_with_at_least_10_unique_places": int((place_counts[idx] >= MIN_PLACES).sum()),
            "entropy_defined_ids": int(entropy_mask[idx].sum()), "joint_defined_ids": int(local.sum()),
            "eligible_coverage_fraction": float(entropy_mask[idx].sum() / idx.size),
            "entropy_distribution": distribution(raw), "crossfit_residual_distribution": distribution(rem),
            "residual_variance_ratio": float(rem.var(ddof=0) / raw.var(ddof=0)) if raw.var(ddof=0) > 0 else None,
            "residual_pearson_r_with_Mx4_F": pearson(rem, mx4[idx][local]),
            "residual_pearson_r_with_S1_lagged_Mx4_F": pearson(rem, s1[idx][local]),
            "folds": fold_report,
        }
    raw = entropy_values[joint]
    heldout = residual[joint]
    raw_variance = float(raw.var(ddof=0))
    residual_variance = float(heldout.var(ddof=0))
    variance_ratio = residual_variance / raw_variance if raw_variance > 0 else None
    correlations = {"Mx4_F": pearson(heldout, mx4[joint]),
                    "S1_lagged_Mx4_F": pearson(heldout, s1[joint])}
    corr_pass = all(v is not None and abs(v) < MAX_ABS_RESIDUAL_CORRELATION for v in correlations.values())
    coverage = float(entropy_mask.mean())
    entropy_bounded = bool(np.isfinite(entropy_values[entropy_mask]).all()
                           and np.all((entropy_values[entropy_mask] >= 0.) & (entropy_values[entropy_mask] <= 1.)))
    variance_pass = variance_ratio is not None and variance_ratio >= MIN_VARIANCE_RATIO
    coverage_pass = coverage >= MIN_COVERAGE
    registry_path = ROOT / "config/ideas.json"
    registry_before = sha256_file(registry_path)
    mx4_after, s1_after = sha256_file(mx4_path), sha256_file(s1_path)
    registry_after = sha256_file(registry_path)
    frozen_unchanged = mx4_after == MX4_SHA and s1_after == S1_SHA and registry_after == registry_before
    if not frozen_unchanged:
        raise AssertionError("frozen predictor artifacts or registry changed")
    table = output_table(pairs, category_counts, entropy, mx4, mx4_mask, s1, s1_mask,
                         joint, prediction, residual, fold_ids)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("entropy diagnostic artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    acceptance = {
        "exact_9379_official_id_alignment": table.num_rows == EXPECTED_N and len(set(pairs)) == EXPECTED_N,
        "eligible_tracts": int(entropy_mask.sum()), "eligible_coverage_fraction": coverage,
        "coverage_at_least_10_percent": bool(coverage_pass),
        "entropy_finite_and_bounded_0_1": entropy_bounded,
        "entropy_above_1_ids": int(np.count_nonzero(entropy_mask & (entropy_values > 1.))),
        "entropy_null_mask_preserved": bool(np.array_equal(np.isfinite(entropy_values), entropy_mask)),
        "crossfit_residual_variance_ratio": variance_ratio,
        "residual_variance_at_least_10_percent": bool(variance_pass),
        "pooled_residual_pearson_correlations": correlations,
        "absolute_residual_correlation_below_0_80_with_each_predictor": bool(corr_pass),
        "frozen_predictors_and_registry_unchanged": bool(frozen_unchanged),
        "distinct_screen_pass": bool(coverage_pass and entropy_bounded and variance_pass and corr_pass and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "screen": "Normalized Shannon entropy of primary-category counts among unique provided Overture places; five-fold cross-fit distinctness screen; unregistered",
        "definition": "For each official tract with at least 10 unique assigned nonnull Overture place IDs, count each ID once by its nonnull categories.primary value. Compute H/log(75) exactly as preregistered; the 75 is the fixed normalizer, not a category filter. Records with null primary contribute to unique-place support but not category frequencies; entropy remains null if fewer than 10 places or no usable primary category. Any computed value above 1 is retained as a boundedness-gate failure, never clipped.",
        "method": {"inputs": "Read only Overture id, categories.primary, and bbox; use bbox center assignment with the existing official ACS GEOID/geometry tract index. No Overture geometry, alternate category, names, sources, confidence, or other fields are read.",
                   "unique_places": "Deduplicate nonnull Overture IDs within region; conflicting duplicate ID tract/category assignments are errors.",
                   "entropy": "Shannon entropy -sum(p log p)/log(75), using every observed nonnull provided primary category; values are not clipped, and any value above 1 fails the requested boundedness gate.",
                   "cross_fit": "Five deterministic region-local GEOID folds; OLS with intercept, frozen Mx4 F and S1 lagged Mx4 F is trained on four folds and predicts the held-out fold. No labels, tuning, or imputation.",
                   "variance_ratio": "Pooled population variance (ddof=0) of held-out residuals divided by entropy variance on the same complete-case support.",
                   "correlation": "Pooled Pearson r between held-out residual and each frozen predictor.",
                   "unregistered": True},
        "measure_before": {"normalization_category_count": 75, "category_audit_enumerated_values": len(audit_categories), "category_audit_report_sha256": sha256_file(CATEGORY_REPORT),
                           "tract_entropy_or_residual_distinctness_previously_measured": False},
        "scope": {"official_ids": EXPECTED_N, "exact_official_geoid_region_partition": True,
                  "eligible_tracts": int(entropy_mask.sum()), "entropy_undefined_tracts": int((~entropy_mask).sum()),
                  "eligible_coverage_fraction": coverage, "joint_predictor_defined_ids": int(joint.sum()),
                  "places_with_at_least_10": int((place_counts >= MIN_PLACES).sum()),
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_reference_data_read": False, "external_data_read": False,
                  "non_primary_categories_read": False, "leaderboard_feedback_used": False,
                  "registry_integration": False, "uploads": 0, "regions": regional},
        "measure_after": {"unique_place_count_distribution": distribution(place_counts.astype(np.float64)),
                          "entropy_distribution": distribution(entropy_values[entropy_mask]),
                          "entropy_above_1_ids": int(np.count_nonzero(entropy_mask & (entropy_values > 1.))),
                          "primary_category_count_sum": int(category_counts.sum()),
                          "observed_primary_category_support": len(categories),
                          "category_count_by_index": {category: int(category_counts[:, i].sum()) for i, category in enumerate(categories)},
                          "crossfit_residual_distribution": distribution(heldout),
                          "entropy_variance": raw_variance, "crossfit_residual_variance": residual_variance,
                          "crossfit_residual_variance_ratio": variance_ratio,
                          "pooled_residual_pearson_correlations": correlations},
        "thresholds": {"minimum_unique_places_inclusive": MIN_PLACES, "minimum_eligible_coverage_inclusive": MIN_COVERAGE,
                       "minimum_residual_variance_ratio_inclusive": MIN_VARIANCE_RATIO,
                       "maximum_absolute_residual_predictor_correlation_exclusive": MAX_ABS_RESIDUAL_CORRELATION,
                       "entropy_bounds_inclusive": [0., 1.]},
        "acceptance": acceptance,
        "provenance": {"official_geoid_region_sha256": official_sha,
                       "poi_category_audit_report_sha256": sha256_file(CATEGORY_REPORT),
                       "observed_primary_category_support_sha256": hashlib.sha256(json.dumps(categories, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
                       "region_inputs": source_regions,
                       "Mx4_artifact": {"path": str(mx4_path), "sha256_before": MX4_SHA, "sha256_after": mx4_after},
                       "S1_artifact": {"path": str(s1_path), "sha256_before": S1_SHA, "sha256_after": s1_after},
                       "registry_sha256_before": registry_before, "registry_sha256_after": registry_after,
                       "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                           "sha256": hashlib.sha256(payload).hexdigest(),
                                           "columns": table.column_names, "deterministic_bytes": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "The bounded POI category entropy meets coverage, residual-variance and predictor-correlation gates; keep only as an unregistered diagnostic, not as target-accuracy evidence or promotion." if acceptance["distinct_screen_pass"] else
                         "The entropy screen fails at least one preregistered support or distinctness gate; do not integrate and retain only as an audit output.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/poi-category-entropy.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "poi-category-entropy/entropy-crossfit-residuals.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"acceptance": report["acceptance"],
                      "measure_after": {"crossfit_residual_variance_ratio": report["measure_after"]["crossfit_residual_variance_ratio"],
                                        "pooled_residual_pearson_correlations": report["measure_after"]["pooled_residual_pearson_correlations"]},
                      "output_artifact": report["provenance"]["output_artifact"], "report": str(ROOT / args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
