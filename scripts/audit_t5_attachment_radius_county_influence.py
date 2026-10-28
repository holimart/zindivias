#!/usr/bin/env python3
"""Leave-one-county-out influence audit from the frozen T5 radius-screen report only."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REL = "reports/t5-attachment-radius-screen.json"
OUT_REL = "reports/t5-attachment-radius-county-influence.json"
SOURCE_SHA256 = "8bc9426e1368ce597fce8bb3ed6c0d07adf3fcc98137fd7ffbbe23f5f2214dc1"
EXPECTED_REGION_FOLDS = {
    "maricopa-az": 16,
    "northern-ca": 24,
    "eastern-ok": 73,
    "south-central-tx": 185,
}
EXPECTED_TOTAL_FOLDS = 298
REPRO_TOLERANCE = 1e-10


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite_nonnegative(value: Any, name: str, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result) or result < 0.0 or (positive and result <= 0.0):
        qualifier = "positive and finite" if positive else "finite and nonnegative"
        raise ValueError(f"{name} must be {qualifier}")
    return result


def calculate_leave_one_county_out(folds: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Reaggregate paired fold SSEs, omitting each county from pooled totals.

    Influence is the signed change in pooled ΔR² after omission minus the
    full-sample pooled ΔR². Sign reversal means a strictly opposite nonzero
    sign; exact-zero omissions are counted separately.
    """
    rows = list(folds)
    if not rows:
        raise ValueError("at least one county fold is required")
    keys: set[tuple[str, str]] = set()
    by_region: dict[str, list[dict[str, Any]]] = {r: [] for r in EXPECTED_REGION_FOLDS}
    total_baseline = total_model = 0.0
    total_n = 0
    normalized = []
    for row in rows:
        region = row.get("region")
        county = row.get("heldout_county_fips")
        if region not in by_region:
            raise ValueError(f"unexpected region in county fold: {region!r}")
        county = str(county)
        if len(county) != 5 or not county.isdigit():
            raise ValueError(f"heldout_county_fips must be five digits: {county!r}")
        key = (region, county)
        if key in keys:
            raise ValueError(f"duplicate held-out county fold: {key}")
        keys.add(key)
        baseline = _finite_nonnegative(row.get("baseline_sse"), f"{key} baseline_sse", positive=True)
        model = _finite_nonnegative(row.get("model_sse"), f"{key} model_sse")
        try:
            heldout_n = int(row["heldout_n"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{key} heldout_n must be a positive integer") from exc
        if heldout_n <= 0 or heldout_n != row["heldout_n"]:
            raise ValueError(f"{key} heldout_n must be a positive integer")
        clean = {"region": region, "heldout_county_fips": county, "heldout_n": heldout_n,
                 "baseline_sse": baseline, "model_sse": model}
        normalized.append(clean)
        by_region[region].append(clean)
        total_baseline += baseline
        total_model += model
        total_n += heldout_n

    if total_baseline <= 0.0:
        raise ValueError("pooled baseline SSE must be positive")
    full_delta = (total_baseline - total_model) / total_baseline
    county_rows = []
    for row in sorted(normalized, key=lambda r: (r["region"], r["heldout_county_fips"])):
        omitted_baseline = total_baseline - row["baseline_sse"]
        omitted_model = total_model - row["model_sse"]
        if omitted_baseline <= 0.0:
            raise ValueError("leave-one-county-out baseline SSE must remain positive")
        loo_delta = (omitted_baseline - omitted_model) / omitted_baseline
        influence = loo_delta - full_delta
        county_rows.append({
            **row,
            "pooled_baseline_sse_without_county": omitted_baseline,
            "pooled_model_sse_without_county": omitted_model,
            "pooled_delta_r2_without_county": loo_delta,
            "influence_delta_r2": influence,
            "absolute_influence_delta_r2": abs(influence),
            "sign_reversal": (full_delta * loo_delta) < 0.0,
            "exact_zero_after_omission": loo_delta == 0.0,
        })

    max_row = max(county_rows, key=lambda r: (r["absolute_influence_delta_r2"], r["region"], r["heldout_county_fips"]))
    return {
        "pooled_baseline_sse_recomputed": total_baseline,
        "pooled_model_sse_recomputed": total_model,
        "pooled_delta_r2_recomputed": full_delta,
        "heldout_n_sum": total_n,
        "county_fold_count": len(county_rows),
        "county_fold_counts_by_region": {region: len(by_region[region]) for region in EXPECTED_REGION_FOLDS},
        "leave_one_county_out_pooled_deltas": county_rows,
        "sign_reversal_count": sum(row["sign_reversal"] for row in county_rows),
        "exact_zero_after_omission_count": sum(row["exact_zero_after_omission"] for row in county_rows),
        "max_absolute_influence_delta_r2": max_row["absolute_influence_delta_r2"],
        "max_influence_county": {k: max_row[k] for k in (
            "region", "heldout_county_fips", "pooled_delta_r2_without_county",
            "influence_delta_r2", "absolute_influence_delta_r2")},
        "minimum_leave_one_county_out_delta_r2": min(row["pooled_delta_r2_without_county"] for row in county_rows),
        "maximum_leave_one_county_out_delta_r2": max(row["pooled_delta_r2_without_county"] for row in county_rows),
    }


def build_report(source_path: Path = ROOT / SOURCE_REL) -> dict[str, Any]:
    if not source_path.is_file():
        raise FileNotFoundError(f"frozen source report does not exist: {source_path}")
    source_hash = sha256_file(source_path)
    if source_hash != SOURCE_SHA256:
        raise ValueError(f"frozen source report SHA-256 mismatch: {source_hash}")
    source = json.loads(source_path.read_text(encoding="utf-8"))

    folds = source["measure_after"]["outer_county_folds"]
    result = calculate_leave_one_county_out(folds)
    source_scope = source["scope"]
    source_summary = source["measure_after"]["pooled_outer_oof"]
    source_regional = source["measure_after"]["regional_outer_oof"]
    source_fold_counts = source_scope["county_fold_counts"]
    errors = []

    if result["county_fold_count"] != EXPECTED_TOTAL_FOLDS:
        errors.append("source does not contain exactly 298 unique outer county folds")
    if result["county_fold_counts_by_region"] != EXPECTED_REGION_FOLDS:
        errors.append("county fold coverage by region does not match frozen scope")
    if source_fold_counts != EXPECTED_REGION_FOLDS:
        errors.append("frozen source scope county counts do not match its fold rows")
    if result["heldout_n_sum"] != source_scope["paired_residual_support"]:
        errors.append("held-out county tracts do not sum to the frozen paired residual support")
    if source_scope["exact_official_alignment"] is not True or source_scope["sample_score_placeholders_read"] is not False:
        errors.append("frozen source report scope/safety flags are not as expected")

    delta_diff = abs(result["pooled_delta_r2_recomputed"] - source_summary["delta_r2"])
    if delta_diff > REPRO_TOLERANCE:
        errors.append(f"recomputed pooled delta-R2 differs from frozen source by {delta_diff:.17g}")
    baseline_diff = abs(result["pooled_baseline_sse_recomputed"] - source_summary["baseline_sse"])
    model_diff = abs(result["pooled_model_sse_recomputed"] - source_summary["model_sse"])
    if baseline_diff > REPRO_TOLERANCE or model_diff > REPRO_TOLERANCE:
        errors.append("reaggregated pooled SSEs differ from the frozen source summary")

    regional_reproduction = {}
    for region in EXPECTED_REGION_FOLDS:
        rows = [row for row in folds if row["region"] == region]
        baseline = sum(float(row["baseline_sse"]) for row in rows)
        model = sum(float(row["model_sse"]) for row in rows)
        delta = (baseline - model) / baseline
        diff = abs(delta - float(source_regional[region]["delta_r2"]))
        regional_reproduction[region] = {"recomputed_delta_r2": delta,
                                         "source_delta_r2": source_regional[region]["delta_r2"],
                                         "absolute_difference": diff,
                                         "matches_within_1e-10": diff <= REPRO_TOLERANCE}
        if diff > REPRO_TOLERANCE:
            errors.append(f"regional delta-R2 does not reproduce for {region}")

    t5_exact = (source["feature_coverage"]["150m_recomputation_matches_frozen_T5"] is True
                and source["feature_coverage"]["150m_max_abs_difference"] == 0.0
                and source["provenance"]["frozen_T5_150m_feature"]["registered_recipe_distance_m"] == 150.0)
    if not t5_exact:
        errors.append("frozen source does not verify exact 150m T5 feature reconciliation")

    selected_counts = source["selected_radius"]["outer_county_fold_selection_counts"]
    if selected_counts != {"75": 187, "150": 0, "300": 7, "600": 104}:
        errors.append("frozen nested-radius selection counts differ from the dispatched baseline")

    # Verify each fold's own ΔR² also agrees with its stored paired SSEs.
    fold_delta_checks = []
    for row in folds:
        calculated = (float(row["baseline_sse"]) - float(row["model_sse"])) / float(row["baseline_sse"])
        diff = abs(calculated - float(row["delta_r2"]))
        fold_delta_checks.append(diff <= REPRO_TOLERANCE)
    if not all(fold_delta_checks):
        errors.append("at least one source county fold's stored delta-R2 does not match its SSE pair")

    source_safety = source["safety"]
    safe = (source_safety["target_labels_read"] is False
            and source_safety["removed_or_equivalent_reference_layers_read"] is False
            and source_safety["external_data_read"] is False
            and source_safety["leaderboard_or_scores_accessed"] is False
            and source_safety["candidate_artifacts_modified"] is False
            and source_safety["uploads"] == 0)
    if not safe:
        errors.append("frozen source safety flags do not establish the required non-mutating scope")

    return {
        "schema_version": 1,
        "analysis_id": "T5_ATTACHMENT_RADIUS_COUNTY_INFLUENCE_V1",
        "result": "PASS" if not errors else "FAIL",
        "intent": "Localize influence in the already-measured nested T5 radius-selection association using only paired outer county-fold SSEs from the frozen source report.",
        "source_report": {"path": SOURCE_REL, "sha256": source_hash, "expected_sha256": SOURCE_SHA256,
                          "json_pointer": "/measure_after/outer_county_folds"},
        "measure_before": {
            "source_pooled_delta_r2": source_summary["delta_r2"],
            "source_pooled_95pct_county_block_bootstrap_ci": source["measure_after"]["paired_county_block_bootstrap"]["pooled_delta_r2_ci_95"],
            "source_regional_delta_r2": {region: source_regional[region]["delta_r2"] for region in EXPECTED_REGION_FOLDS},
            "source_county_folds": source_scope["county_fold_counts"],
            "source_selected_radius_fold_counts": selected_counts,
            "frozen_T5_150m_exact_reconciliation": t5_exact,
            "frozen_T5_150m_max_abs_difference": source["feature_coverage"]["150m_max_abs_difference"],
            "source_nested_screen_pass": source["acceptance"]["screen_pass"],
        },
        "measure_after": result,
        "reproduction_checks": {
            "pooled_delta_r2_absolute_difference": delta_diff,
            "pooled_sse_baseline_absolute_difference": baseline_diff,
            "pooled_sse_model_absolute_difference": model_diff,
            "regional_delta_r2": regional_reproduction,
            "all_298_fold_delta_r2_values_match_paired_sse": all(fold_delta_checks),
            "tolerance": REPRO_TOLERANCE,
        },
        "acceptance": {
            "pooled_delta_r2_reproduced_within_1e-10": delta_diff <= REPRO_TOLERANCE,
            "all_regional_delta_r2_values_reproduced_within_1e-10": all(v["matches_within_1e-10"] for v in regional_reproduction.values()),
            "complete_unique_298_county_coverage": result["county_fold_count"] == EXPECTED_TOTAL_FOLDS and result["county_fold_counts_by_region"] == EXPECTED_REGION_FOLDS,
            "county_omission_values_emitted": len(result["leave_one_county_out_pooled_deltas"]) == EXPECTED_TOTAL_FOLDS,
            "frozen_T5_150m_exactly_matches_source": t5_exact,
            "source_report_hash_verified": source_hash == SOURCE_SHA256,
            "audit_checks_passed": not errors,
            "errors": errors,
        },
        "interpretation": {
            "sign_reversal_definition": "strictly opposite nonzero signs between full-sample pooled ΔR² and the leave-one-county-out pooled ΔR²; exact zeros counted separately",
            "influence_definition": "leave-one-county-out pooled ΔR² minus full-sample pooled ΔR²; max influence is the largest absolute change",
            "target_accuracy_claim": False,
            "conclusion": "This is a non-mutating county influence diagnostic on a frozen feature-only association with the S1 disagreement residual; it does not measure target accuracy or authorize a new estimator.",
        },
        "safety": {"source_input_count": 1, "only_frozen_per_county_fold_sse_report_read": True,
                   "labels_or_reference_layers_read": False, "external_data_read": False,
                   "sample_scores_or_leaderboard_used": False, "candidate_edits": 0, "uploads": 0},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, default=ROOT / SOURCE_REL)
    parser.add_argument("--out", type=Path, default=ROOT / OUT_REL)
    args = parser.parse_args()
    report = build_report(args.source_report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "counties": report["measure_after"]["county_fold_count"],
                      "pooled_delta_r2": report["measure_after"]["pooled_delta_r2_recomputed"],
                      "sign_reversals": report["measure_after"]["sign_reversal_count"],
                      "max_abs_influence": report["measure_after"]["max_absolute_influence_delta_r2"],
                      "report": str(args.out), "errors": report["acceptance"]["errors"]}, sort_keys=True))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
