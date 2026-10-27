#!/usr/bin/env python3
"""Label-free regional distribution audit of frozen A1/A2/A3 predictions."""
from __future__ import annotations
import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_DEFAULT = os.environ.get("COMPETITION_WORKSPACE", "")
FINAL_TWO = ROOT / "reports/final-two.json"
CANDIDATES = ("A1_physical_equal", "A2_spectral", "A3_dawid_skene")
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
FIELDS = ("transport_gap", "building_gap", "poi_gap", "coverage_gap_score")
EXPECTED_HEADER = ("GEOID", *FIELDS)
SMD_LIMIT = 0.5


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_registered_metadata(path: Path) -> tuple[dict[str, dict[str, Any]], str]:
    """Project only candidate registration metadata; skip leaderboard fields lexically."""
    helper_path = ROOT / "scripts/audit_track_a_feature_support.py"
    spec = importlib.util.spec_from_file_location("track_a_feature_support_helper", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the shared safe final-two metadata projector")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper.load_final_two_registrations(path, expected_ids=CANDIDATES)


def official_ids(workspace: Path) -> tuple[dict[str, str], dict[str, list[str]], dict[str, str]]:
    region_by_id: dict[str, str] = {}
    ordered_by_region: dict[str, list[str]] = {}
    id_hashes: dict[str, str] = {}
    for region in REGIONS:
        sample = workspace / "samples" / f"{region}-sample-submission.csv"
        with sample.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.reader(stream)
            header = next(reader, [])
            if "GEOID" not in header:
                raise ValueError(f"{sample}: missing GEOID column")
            index = header.index("GEOID")
            ids = [row[index] for row in reader]
        if not ids or any(not geoid for geoid in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"{region}: empty or duplicate authoritative GEOID")
        ordered_by_region[region] = ids
        id_hashes[region] = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
        for geoid in ids:
            if geoid in region_by_id:
                raise ValueError(f"GEOID occurs in multiple scored regions: {geoid}")
            region_by_id[geoid] = region
    if len(region_by_id) != 9379:
        raise ValueError(f"expected exactly 9,379 scored GEOIDs; found {len(region_by_id)}")
    return region_by_id, ordered_by_region, id_hashes


def quantile_linear(values: list[float], probability: float) -> float:
    """Type-7/linear sample quantile, matching common statistical software defaults."""
    if not values:
        raise ValueError("quantile of empty values")
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    fraction = index - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def finite_number(raw: str) -> tuple[float | None, bool]:
    """Return a finite value if present and whether the field was numeric at all."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, False
    if not math.isfinite(value):
        return None, True
    return value, True


def pooled_stats(values: Iterable[float]) -> dict[str, Any]:
    data = list(values)
    if not data:
        raise ValueError("no finite official predictions for pooled distribution")
    if any(not math.isfinite(value) for value in data):
        raise ValueError("pooled statistics require finite predictions")
    mean = math.fsum(data) / len(data)
    variance = math.fsum((value - mean) ** 2 for value in data) / len(data)
    return {"finite_ids": len(data), "mean": mean, "standard_deviation": math.sqrt(variance),
            "standard_deviation_ddof": 0}


def regional_distribution(values: list[float | None], pooled: dict[str, Any]) -> dict[str, Any]:
    finite = [value for value in values if value is not None]
    n = len(values)
    finite_count = len(finite)
    if not finite:
        return {"scored_ids": n, "finite_ids": 0, "finite_share": 0.0,
                "nonfinite_ids": n, "non_numeric_ids": None, "out_of_bounds_ids": None,
                "mean": None, "median": None, "iqr": None, "p5": None, "p95": None,
                "standardized_mean_difference_from_pooled": None,
                "absolute_smd_exceeds_0_5": None}
    mean = math.fsum(finite) / finite_count
    median = quantile_linear(finite, 0.5)
    q25, q75 = quantile_linear(finite, 0.25), quantile_linear(finite, 0.75)
    pooled_sd = pooled["standard_deviation"]
    difference = mean - pooled["mean"]
    if pooled_sd == 0:
        smd = 0.0 if difference == 0 else None
        exceeds = difference != 0
    else:
        smd = difference / pooled_sd
        exceeds = abs(smd) > SMD_LIMIT
    return {"scored_ids": n, "finite_ids": finite_count, "finite_share": finite_count / n,
            "nonfinite_ids": n - finite_count, "mean": mean, "median": median,
            "iqr": q75 - q25, "p5": quantile_linear(finite, 0.05),
            "p95": quantile_linear(finite, 0.95),
            "standardized_mean_difference_from_pooled": smd,
            "absolute_smd_exceeds_0_5": exceeds}


def read_registered_candidate(path: Path, registration: dict[str, Any],
                              region_by_id: dict[str, str], ids_by_region: dict[str, list[str]]) -> tuple[dict[str, dict[str, float | None]], dict[str, Any]]:
    expected_artifact = registration.get("artifact")
    expected_hash = registration.get("sha256")
    if tuple(registration.get("columns", ())) != EXPECTED_HEADER:
        raise ValueError("final-two registration does not match frozen candidate columns")
    if expected_artifact != str(path.relative_to(ROOT)):
        raise ValueError("final-two artifact path differs from the frozen A-candidate path")
    actual_hash = file_sha256(path)
    if actual_hash != expected_hash:
        raise ValueError(f"candidate artifact SHA-256 mismatch: {path}")
    scored: dict[str, dict[str, float | None]] = {}
    all_ids: set[str] = set()
    scored_order_by_region: dict[str, list[str]] = {region: [] for region in ids_by_region}
    row_count = 0
    non_scored_rows = 0
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != EXPECTED_HEADER:
            raise ValueError(f"{path}: unexpected candidate column order")
        for row in reader:
            row_count += 1
            geoid = row["GEOID"]
            if not geoid or geoid in all_ids:
                raise ValueError(f"{path}: empty or duplicate GEOID {geoid!r}")
            all_ids.add(geoid)
            if geoid not in region_by_id:
                # Do not parse or summarize any template-only prediction values.
                non_scored_rows += 1
                continue
            if geoid in scored:
                raise ValueError(f"{path}: duplicate scored GEOID {geoid}")
            scored_order_by_region[region_by_id[geoid]].append(geoid)
            scored[geoid] = {}
            for field in FIELDS:
                value, numeric = finite_number(row[field])
                scored[geoid][field] = value
                scored[geoid][f"__numeric__{field}"] = 1.0 if numeric else 0.0
    missing = set(region_by_id) - set(scored)
    extra_scored = set(scored) - set(region_by_id)
    if missing or extra_scored:
        raise ValueError(f"{path}: scored ID alignment failed (missing={len(missing)}, extra={len(extra_scored)})")
    if row_count != registration.get("rows"):
        raise ValueError(f"{path}: registered row count mismatch")
    order_matches_by_region = {region: scored_order_by_region[region] == ids_by_region[region]
                                for region in ids_by_region}
    order_matches = all(order_matches_by_region.values())
    if not order_matches:
        raise ValueError(f"{path}: official scored GEOID order differs within one or more authoritative region samples")
    return scored, {"path": expected_artifact, "sha256": actual_hash,
                    "registered_rows": registration["rows"], "scored_rows": len(scored),
                    "ordered_scored_ids_match_official_order": order_matches,
                    "ordered_scored_ids_match_official_order_by_region": order_matches_by_region,
                    "non_scored_rows_excluded_without_numeric_value_parsing": non_scored_rows,
                    "missing_scored_ids": 0, "duplicate_scored_ids": 0,
                    "columns": list(EXPECTED_HEADER)}


def summarize(workspace: Path, output: Path) -> dict[str, Any]:
    region_by_id, ids_by_region, ordered_id_hashes = official_ids(workspace)
    registrations, final_two_sha = load_registered_metadata(FINAL_TWO)
    all_metrics: dict[str, Any] = {}
    alignment: dict[str, Any] = {}
    for candidate in CANDIDATES:
        registration = registrations[candidate]
        path = ROOT / registration["artifact"]
        data, alignment[candidate] = read_registered_candidate(path, registration, region_by_id, ids_by_region)
        manifest_path = ROOT / "reports/candidates" / f"{candidate}_manifest.json"
        manifest_hash = file_sha256(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_manifest_hash = registration.get("parameter_provenance", {}).get("manifest_sha256")
        if manifest_hash != expected_manifest_hash:
            raise ValueError(f"{candidate}: candidate manifest hash differs from final-two")
        if (manifest.get("artifact") != registration["artifact"] or
                manifest.get("sha256") != registration["sha256"] or
                manifest.get("rows") != registration["rows"] or
                tuple(manifest.get("columns", ())) != EXPECTED_HEADER or
                manifest.get("official_rows") != 9379 or
                manifest.get("template_only_rows") != 415):
            raise ValueError(f"{candidate}: manifest artifact/row accounting mismatch")
        alignment[candidate]["candidate_manifest"] = {"path": str(manifest_path.relative_to(ROOT)),
            "sha256": manifest_hash, "manifest_template_only_rows": manifest["template_only_rows"],
            "observed_non_scored_rows": alignment[candidate]["non_scored_rows_excluded_without_numeric_value_parsing"],
            "template_only_row_count_matches": manifest["template_only_rows"] == alignment[candidate]["non_scored_rows_excluded_without_numeric_value_parsing"]}
        if not alignment[candidate]["candidate_manifest"]["template_only_row_count_matches"]:
            raise ValueError(f"{candidate}: non-scored rows differ from frozen template-only manifest count")
        candidate_metrics: dict[str, Any] = {}
        for field in FIELDS:
            pooled_values = [data[geoid][field] for geoid in region_by_id
                             if data[geoid][field] is not None]
            pooled = pooled_stats(pooled_values)
            by_region: dict[str, Any] = {}
            for region, ids in ids_by_region.items():
                vals = [data[geoid][field] for geoid in ids]
                summary = regional_distribution(vals, pooled)
                summary["non_numeric_ids"] = sum(data[g][f"__numeric__{field}"] == 0 for g in ids)
                summary["out_of_bounds_ids"] = sum(v is not None and not 0 <= v <= 1 for v in vals)
                by_region[region] = summary
            candidate_metrics[field] = {
                "pooled_scored_distribution": pooled,
                "regional_summary": by_region,
                "smd_definition": "(finite regional mean - finite pooled scored-tract mean) / population SD of all finite scored-tract values; ddof=0",
                "quantile_definition": "linear/type-7 quantile of finite values only",
                "flag_rule": "absolute standardized mean difference > 0.5; diagnostic only, no correction",
                "flagged_regions": [region for region, stats in by_region.items()
                                    if stats["absolute_smd_exceeds_0_5"] is True],
            }
        all_metrics[candidate] = candidate_metrics
    report = {
        "schema_version": 1,
        "audit": "Frozen Track A regional prediction distribution-shift screen; diagnostic only, not accuracy or bias evidence.",
        "scope": {"candidates": list(CANDIDATES), "official_scored_geoids": len(region_by_id),
                  "regions": list(REGIONS), "measures": list(FIELDS),
                  "sample_score_placeholders_read": False, "target_labels_read": False,
                  "leaderboard_values_used": False, "candidate_values_reweighted_or_corrected": False,
                  "candidates_rebuilt_modified_or_uploaded": False},
        "measure_before": {"candidate_finite_and_bounded_order_checks_exist": True,
                           "unified_regional_distribution_shift_table_recorded": False},
        "method": {"regional_summaries": ["median", "IQR (Q75-Q25)", "p5", "p95", "finite share"],
                   "pooled_reference": "all finite values across the 9,379 official scored GEOIDs",
                   "smd_threshold": {"absolute_value_greater_than": SMD_LIMIT,
                                     "interpretation": "flag only; do not reweight, adjust, or select candidates"},
                   "nonfinite_policy": "report in finite share; exclude only nonfinite values from quantiles, means and pooled SD; do not impute"},
        "inputs": {"final_two_metadata_sha256": final_two_sha,
                   "candidate_artifacts": alignment,
                   "official_id_projection_sha256_by_region": ordered_id_hashes,
                   "official_ids_source": "$COMPETITION_WORKSPACE/samples/<region>-sample-submission.csv; GEOID column only"},
        "exact_scored_id_alignment": {"expected_scored_ids": 9379,
             "candidate_artifact_rows": {candidate: alignment[candidate]["registered_rows"] for candidate in CANDIDATES},
             "template_only_rows_excluded_without_numeric_value_parsing": {candidate: alignment[candidate]["non_scored_rows_excluded_without_numeric_value_parsing"] for candidate in CANDIDATES},
             "missing_or_duplicate_scored_ids": {candidate: 0 for candidate in CANDIDATES},
             "ordered_scored_ids_match_official_order": {candidate: alignment[candidate]["ordered_scored_ids_match_official_order"] for candidate in CANDIDATES},
             "ordered_scored_ids_match_official_order_by_region": {candidate: alignment[candidate]["ordered_scored_ids_match_official_order_by_region"] for candidate in CANDIDATES},
             "ordered_official_ids_sha256_by_region": ordered_id_hashes},
        "distributions": all_metrics,
        "acceptance": {"all_three_registered_candidates_hash_verified": True,
                       "all_three_candidate_manifest_hashes_verified": True,
                       "exact_scored_id_alignment": all(alignment[c]["scored_rows"] == 9379 and alignment[c]["ordered_scored_ids_match_official_order"] for c in CANDIDATES),
                       "template_only_rows_match_manifest": all(alignment[c]["candidate_manifest"]["template_only_row_count_matches"] for c in CANDIDATES),
                       "all_four_regions_and_four_fields_summarized": True,
                       "all_values_finite_and_bounded": all(
                           stats["finite_ids"] == stats["scored_ids"] and stats["out_of_bounds_ids"] == 0
                           for candidate_metrics in all_metrics.values()
                           for field_metrics in candidate_metrics.values()
                           for stats in field_metrics["regional_summary"].values()),
                       "all_nonfinite_and_out_of_bounds_values_explicit": True,
                       "smd_threshold_applied_without_correction": True,
                       "deterministic_hashes_recorded": True,
                       "no_labels_or_leaderboard_feedback_used": True},
        "limitations": ["Regional prediction-distribution differences describe the frozen estimates only; without target labels they do not establish regional accuracy, bias, or causal differences.",
                        "SMD uses finite estimates only and standardizes by the population SD of the pooled finite scored-tract distribution.",
                        "The 415 template-only rows are excluded from numerical prediction parsing and statistics."],
    }
    report["provenance"] = {"analysis_script_sha256": file_sha256(Path(__file__).resolve()),
                             "safe_final_two_metadata_projector_sha256": file_sha256(ROOT / "scripts/audit_track_a_feature_support.py")}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(WORKSPACE_DEFAULT))
    parser.add_argument("--out", type=Path, default=ROOT / "reports/track-a-region-distribution.json")
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error("--workspace or COMPETITION_WORKSPACE must be an existing directory")
    report = summarize(args.workspace, args.out)
    flags = {candidate: {field: metrics[field]["flagged_regions"]
                         for field in FIELDS if metrics[field]["flagged_regions"]}
             for candidate, metrics in report["distributions"].items()}
    print(json.dumps({"acceptance": report["acceptance"], "flagged_regions": flags,
                      "output": str(args.out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
