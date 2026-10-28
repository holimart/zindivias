#!/usr/bin/env python3
"""Compare original, zero-filled and explicit-missing S2 facility semantics.

The audit is read-only and limited to the provided Overture/strata inputs and
frozen feature-only S2 artifacts. Numeric zero means a zero in the provided
tract summary, not a claim about removed reference data.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import audit_shrink_h1b_rule_levels as h1b  # noqa: E402
import build_shrink_s2_assumption_bound as s2  # noqa: E402

REGIONS = h1b.REGIONS
FACILITIES = h1b.FACILITIES
REPORT = ROOT / "reports/s2-missingness-semantics.json"
SCORES = ROOT / "reports/s2-missingness-semantics-scores.csv"
S2_PATH = ROOT / "reports/candidates/S2_h1b_assumption_bound_v1.csv"
S2B_PATH = ROOT / "reports/candidates/S2b_c2_expected_facility_mask_v1.csv"
C2_PATH = ROOT / "reports/candidates/C2_component_estimator_v0.csv"
ESTIMATES_PATH = ROOT / "reports/gap-estimates.csv"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def missing_numeric(value: Any) -> bool:
    if value is None:
        return True
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def rankdata(values: list[float]) -> list[float]:
    """Average-tie, one-based ranks (SciPy-compatible Spearman convention)."""
    ordered = sorted(enumerate(map(float, values)), key=lambda pair: pair[1])
    ranks = [0.0] * len(ordered)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average = ((start + 1) + end) / 2.0
        for pos in range(start, end):
            ranks[ordered[pos][0]] = average
        start = end
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b) or len(a) < 2:
        return None
    ra, rb = rankdata(a), rankdata(b)
    ma, mb = math.fsum(ra) / len(ra), math.fsum(rb) / len(rb)
    da, db = [x - ma for x in ra], [x - mb for x in rb]
    va, vb = math.fsum(x * x for x in da), math.fsum(x * x for x in db)
    if va == 0 or vb == 0:
        return None
    return math.fsum(x * y for x, y in zip(da, db)) / math.sqrt(va * vb)


def compose_variant(c2row: dict[str, str], estimates: dict[str, str],
                    feature: dict[str, Any], mode: str) -> tuple[float, dict[str, bool]]:
    """S2 score with one of three count-null policies; only facility count states vary."""
    if mode not in {"original", "zero_filled", "explicit_missing_indicator"}:
        raise ValueError(f"unknown missingness mode {mode}")
    transformed = dict(feature)
    indicator: dict[str, bool] = {}
    for kind in FACILITIES:
        key = f"overture_poi_{kind}"
        is_missing = missing_numeric(transformed.get(key))
        indicator[kind] = is_missing
        if is_missing:
            if mode == "original":
                raise ValueError(f"original S2 requires a numeric facility count: {key}")
            # S2 drops raw zeros from its facilities half. Zero-fill applies that
            # existing rule; the indicator variant instead records unknown state
            # explicitly and excludes it (it never treats unknown as evidence).
            transformed[key] = 0.0
    components, flags = s2.adjusted_components(
        {name: s2.to_float(c2row[f"{name}_gap"], f"C2 {name}")
         for name in ("transport", "building", "poi")},
        estimates,
        transformed,
    )
    score = s2.component_composite(components, flags)
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("variant produced an invalid S2 score")
    return score, indicator


def read_inputs(workspace: Path) -> dict[str, Any]:
    h1b_prereg = json.loads((ROOT / "config/shrink-h1b-rule-level-preregistered.json").read_text())
    s2_prereg = json.loads((ROOT / "config/shrink-s2-assumption-bound-preregistered.json").read_text())
    pinned = {
        "S2": (S2_PATH, "44ba76abcd9e1fb0aa7194e3c0fadf567debce40e29ed4bdf93ef02ae9514577"),
        "S2b": (S2B_PATH, "5761e2c17f25f66bbc9e1d769a04b76545e4cc660a8ca9481c30b89739b7ae6f"),
        "C2": (C2_PATH, s2_prereg["frozen_inputs"]["C2_components"]["sha256"]),
        "gap_estimates": (ESTIMATES_PATH, s2_prereg["frozen_inputs"]["gap_estimates"]["sha256"]),
    }
    hashes = {}
    for name, (path, expected) in pinned.items():
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"frozen {name} hash mismatch")
        hashes[name] = actual

    official_order, region_by_id, ids_by_region = h1b.read_official_ids(workspace)
    official_ids = set(region_by_id)
    c2, template_order = s2.read_csv_official(C2_PATH, official_ids,
        ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"], full_order=True)
    s2data, s2_order = s2.read_csv_official(S2_PATH, official_ids,
        ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"], full_order=True)
    s2b, s2b_order = s2.read_csv_official(S2B_PATH, official_ids,
        ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"], full_order=True)
    if not (len(template_order) == 9794 and len(set(template_order)) == 9794 and template_order == s2_order == s2b_order):
        raise ValueError("frozen C2/S2/S2b template order mismatch")
    if any(set(mapping) != official_ids for mapping in (c2, s2data, s2b)):
        raise ValueError("candidate inputs do not cover exact 9,379 official IDs")
    estimates = h1b.load_csv_map(ESTIMATES_PATH, h1b.GAP_COLUMNS, official_ids)
    if set(estimates) != official_ids:
        raise ValueError("gap estimates do not cover exact official IDs")

    features: dict[str, dict[str, Any]] = {}
    strata: dict[str, dict[str, Any]] = {}
    input_hashes: dict[str, Any] = {"candidate_artifacts": hashes, "feature_and_strata_by_region": {}}
    for region in REGIONS:
        fpath = workspace / "gap-estimator/features" / f"{region}-overture-tract-summary.parquet"
        spath = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        feature_pin = h1b_prereg["pinned_inputs"]["provided_overture_tract_summaries"][region]["sha256"]
        strata_pin = h1b_prereg["pinned_inputs"]["provided_strata"][region]["sha256"]
        if sha256_file(fpath) != feature_pin or sha256_file(spath) != strata_pin:
            raise ValueError(f"provided feature/strata hash mismatch: {region}")
        ids = set(ids_by_region[region])
        features.update(h1b.load_parquet_map(fpath, h1b.FEATURE_COLUMNS, ids))
        strata.update(h1b.load_parquet_map(spath, h1b.STRATA_COLUMNS, ids))
        input_hashes["feature_and_strata_by_region"][region] = {
            "overture_sha256": feature_pin, "strata_sha256": strata_pin,
        }
    if set(features) != official_ids or set(strata) != official_ids:
        raise ValueError("feature/strata rows do not have exact official ID coverage")
    input_hashes["official_sample_geoid_sha256"] = {}
    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        actual_sample_hash = sha256_file(sample_path)
        expected_sample_hash = s2_prereg["frozen_inputs"]["official_samples"][region]["sha256"]
        if actual_sample_hash != expected_sample_hash:
            raise ValueError(f"official sample GEOID file hash mismatch: {region}")
        input_hashes["official_sample_geoid_sha256"][region] = actual_sample_hash
    return {"official_order": official_order, "ids_by_region": ids_by_region,
            "official_ids": official_ids, "c2": c2, "s2": s2data, "s2b": s2b,
            "estimates": estimates, "features": features, "strata": strata,
            "template_order": template_order, "input_hashes": input_hashes}


def run_audit(workspace: Path) -> dict[str, Any]:
    data = read_inputs(workspace)
    predictions: dict[str, dict[str, float]] = {mode: {} for mode in (
        "original", "zero_filled", "explicit_missing_indicator")}
    state_counts: dict[str, Any] = {}
    missing_mask_by_id: dict[str, dict[str, bool]] = {}
    row_counts: dict[str, int] = {}
    for region, ids in data["ids_by_region"].items():
        absent_rows = [g for g in ids if g not in data["features"]]
        row_counts[region] = {"expected": len(ids), "observed": len(ids) - len(absent_rows),
                              "absent_feature_rows": len(absent_rows)}
        state_counts[region] = {}
        for kind in FACILITIES:
            pos = zero = missing = 0
            for geoid in ids:
                value = data["features"][geoid].get(f"overture_poi_{kind}")
                if missing_numeric(value):
                    missing += 1
                elif float(value) == 0.0:
                    zero += 1
                elif float(value) > 0.0:
                    pos += 1
                else:
                    raise ValueError(f"negative {kind} input count at {geoid}")
            state_counts[region][kind] = {"positive_numeric_count": pos, "zero_numeric_count": zero,
                                          "null_or_nonfinite_count": missing,
                                          "total_official_rows": len(ids)}

    # All scenarios are evaluated in official sample order and compared by GEOID.
    for geoid, region in data["official_order"]:
        frozen = float(data["s2"][geoid]["coverage_gap_score"])
        for mode in predictions:
            value, indicator = compose_variant(data["c2"][geoid], data["estimates"][geoid],
                                               data["features"][geoid], mode)
            predictions[mode][geoid] = value
            if mode == "explicit_missing_indicator":
                missing_mask_by_id[geoid] = indicator
        if not math.isfinite(frozen) or not 0 <= frozen <= 1:
            raise ValueError(f"frozen S2 score invalid at {geoid}")

    # We count mask prevalence separately from the number of indicator bits applied.
    indicator_prevalence = {kind: sum(state_counts[r][kind]["null_or_nonfinite_count"] for r in REGIONS)
                             for kind in FACILITIES}
    summaries: dict[str, Any] = {}
    for region, ids in data["ids_by_region"].items():
        frozen_scores = [float(data["s2"][g]["coverage_gap_score"]) for g in ids]
        summaries[region] = {}
        for mode, scores in predictions.items():
            values = [scores[g] for g in ids]
            deltas = [x - y for x, y in zip(values, frozen_scores)]
            finite = all(math.isfinite(x) and 0 <= x <= 1 for x in values)
            summaries[region][mode] = {
                "coverage": len(scores.keys() & set(ids)),
                "max_abs_score_delta_vs_S2": max(map(abs, deltas), default=0.0),
                "mean_signed_score_delta_vs_S2": math.fsum(deltas) / len(deltas),
                "spearman_rank_vs_S2": spearman(frozen_scores, values),
                "finite_bounded_0_1": finite,
                "prediction_vector_sha256": hashlib.sha256("".join(
                    f"{g},{format(scores[g], '.17g')}\n" for g in ids).encode()).hexdigest(),
            }

    all_score_rows = []
    for geoid, region in data["official_order"]:
        all_score_rows.append({"GEOID": geoid, "region": region,
                               "S2_frozen": format(float(data["s2"][geoid]["coverage_gap_score"]), ".17g"),
                               "original": format(predictions["original"][geoid], ".17g"),
                               "zero_filled": format(predictions["zero_filled"][geoid], ".17g"),
                               "explicit_missing_indicator": format(predictions["explicit_missing_indicator"][geoid], ".17g"),
                               **{f"{kind}_missing": int(missing_mask_by_id[geoid][kind]) for kind in FACILITIES}})
    SCORES.parent.mkdir(parents=True, exist_ok=True)
    with SCORES.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_score_rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(all_score_rows)

    s2_s2b_by_region = {}
    for region, ids in data["ids_by_region"].items():
        diffs = [abs(float(data["s2"][g]["coverage_gap_score"]) - float(data["s2b"][g]["coverage_gap_score"])) for g in ids]
        s2_s2b_by_region[region] = {"n": len(ids), "p95_abs_difference": float(np.quantile(diffs, .95)),
                                    "max_abs_difference": max(diffs, default=0.0)}
    all_mask_diffs = [abs(float(data["s2"][g]["coverage_gap_score"]) - float(data["s2b"][g]["coverage_gap_score"]))
                      for g, _ in data["official_order"]]

    original_rebuild_max_delta = max(
        summaries[region]["original"]["max_abs_score_delta_vs_S2"] for region in REGIONS
    )
    if original_rebuild_max_delta > 1e-10:
        raise ValueError(f"original S2 recomputation mismatch: {original_rebuild_max_delta}")
    for region, values in state_counts.items():
        expected_rows = row_counts[region]["expected"]
        for kind, counts in values.items():
            if sum(counts[key] for key in ("positive_numeric_count", "zero_numeric_count", "null_or_nonfinite_count")) != expected_rows:
                raise ValueError(f"input state counts do not partition {region}/{kind}")

    report = {
        "schema_version": 1,
        "analysis_id": "S2_MISSINGNESS_SEMANTICS_SCREEN_V1",
        "result": "PASS",
        "scope": {"official_rows": len(data["official_ids"]), "official_unique_ids": len(data["official_ids"]),
                  "region_coverage": row_counts, "four_region_summaries": summaries,
                  "template_rows": len(data["template_order"]),
                  "template_only_rows_excluded": len(data["template_order"]) - len(data["official_ids"])},
        "measure_before": {"S2_sha256": data["input_hashes"]["candidate_artifacts"]["S2"],
                           "S2b_sha256": data["input_hashes"]["candidate_artifacts"]["S2b"],
                           "S2_S2b_all_region_p95_abs_difference": float(np.quantile(all_mask_diffs, .95)),
                           "S2_S2b_p95_abs_difference_by_region": s2_s2b_by_region},
        "provided_input_states_by_region_and_facility_type": state_counts,
        "variants": {
            "original": "Run frozen S2 implementation on the provided raw per-tract count fields; nonnumeric/missing values are rejected by that implementation.",
            "zero_filled": "Replace only null/nonfinite facility count cells by 0, then apply frozen S2's raw-zero drop rule.",
            "explicit_missing_indicator": "Keep a per-type is_missing sidecar mask; unknown count cells are excluded from facilities rather than interpreted as measured zeros. Numeric zero cells remain numeric zero and follow the frozen drop rule.",
            "observed_input_note": "A numeric zero is a zero in the supplied tract aggregate; it does not prove the removed reference count is zero. The supplied summary has no row-level raw-feature observation indicator beyond nullness; source-tag missing-share is not interpreted as an absent facility observation."
        },
        "missing_indicator_prevalence_by_facility_type": indicator_prevalence,
        "input_hashes": data["input_hashes"],
        "code_sha256": {"audit_script": sha256_file(Path(__file__).resolve()),
                         "S2_recipe": sha256_file(ROOT / "scripts/build_shrink_s2_assumption_bound.py"),
                         "feature_input_loader": sha256_file(ROOT / "scripts/audit_shrink_h1b_rule_levels.py")},
        "scores_artifact": {"path": str(SCORES.relative_to(ROOT)), "sha256": sha256_file(SCORES),
                            "rows": len(all_score_rows), "columns": list(all_score_rows[0])},
        "validation": {"original_reproduces_frozen_S2_max_delta": original_rebuild_max_delta,
                       "all_variants_exact_official_ids": all(len(x) == len(data["official_ids"]) for x in predictions.values()),
                       "duplicate_ids": 0, "missing_ids": 0,
                       "all_variant_scores_finite_bounded_0_1": all(v[mode]["finite_bounded_0_1"] for v in summaries.values() for mode in predictions),
                       "no_labels_removed_references_external_data_or_scores": True,
                       "no_upload_or_candidate_choice": True},
        "interpretation": "Sensitivity only. No target accuracy is inferred; if all count fields are numeric, the experiment cannot establish whether a numeric zero means no facility or an unobserved source record.",
        "reproduction": {"command": "uv run --with-requirements requirements.txt python scripts/audit_s2_missingness_semantics.py --workspace $COMPETITION_WORKSPACE",
                         "tests": "uv run --with-requirements requirements.txt --with pytest python -m pytest -q tests/test_s2_missingness_semantics.py"},
    }
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    report = run_audit(args.workspace.resolve())
    print(json.dumps({"result": report["result"], "official_rows": report["scope"]["official_rows"],
                      "report": str(REPORT), "scores_sha256": report["scores_artifact"]["sha256"]}, sort_keys=True))


if __name__ == "__main__":
    main()
