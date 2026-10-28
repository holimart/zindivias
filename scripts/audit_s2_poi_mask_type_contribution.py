#!/usr/bin/env python3
"""Attribute frozen S2's zero-count facility-mask sensitivity by facility type.

Uses only the frozen per-type feature estimates, provided Overture category
counts, frozen S2 candidate bytes, and official GEOID lists. Shapley marginal
attributions average one-type-at-a-time mask restorations over all orders, so
per-type contributions add exactly to the combined retain-zero endpoint.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from itertools import combinations, permutations
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_s2_poi_facility_mask_endpoints as endpoint  # noqa: E402

REGIONS = endpoint.REGIONS
FACILITIES = endpoint.FACILITIES
PERMUTATIONS = tuple(permutations(FACILITIES))
REPORT = ROOT / "reports/s2-poi-mask-type-contribution.json"
SCORES = ROOT / "reports/s2-poi-mask-type-contribution-scores.csv"
TOL = 1e-12


def score_for_mask(base: Mapping[str, float], estimates: Mapping[str, Any],
                   counts: Mapping[str, float], retained_zero_types: frozenset[str]) -> float:
    """Recompose one fixed S2 scenario; only named zero-count masks are lifted."""
    included_gaps = []
    for kind in FACILITIES:
        defined = int(estimates[f"{kind}_defined"])
        count = float(counts[kind])
        if defined and (count != 0.0 or kind in retained_zero_types):
            included_gaps.append(float(estimates[f"{kind}_gap"]))
    poi_halves = []
    if included_gaps:
        poi_halves.append(math.fsum(included_gaps) / len(included_gaps))
    if int(estimates["cbp_defined"]):
        poi_halves.append(float(estimates["cbp_gap"]))
    poi_defined = int(bool(poi_halves))
    poi_gap = math.fsum(poi_halves) / len(poi_halves) if poi_halves else 0.0
    return endpoint.composite_score(base, estimates, poi_gap, poi_defined)


def shapley_attribution(score_by_mask: Mapping[frozenset[str], float]) -> dict[str, float]:
    """Average each type's one-at-a-time score increment over six mask orders."""
    if len(score_by_mask) != 1 << len(FACILITIES):
        raise ValueError("Shapley score table must cover all facility coalitions")
    attribution = {kind: 0.0 for kind in FACILITIES}
    for order in PERMUTATIONS:
        coalition: frozenset[str] = frozenset()
        before = float(score_by_mask[coalition])
        for kind in order:
            coalition = coalition | {kind}
            after = float(score_by_mask[coalition])
            attribution[kind] += (after - before) / len(PERMUTATIONS)
            before = after
    return attribution


def signed_quantile(values: Sequence[float], p: float) -> float:
    return endpoint.linear_quantile(values, p)


def summarize_contribution(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        raise ValueError("cannot summarize empty contribution vector")
    finite = [float(x) for x in values]
    if not all(math.isfinite(x) for x in finite):
        raise ValueError("non-finite type contribution")
    absolute = [abs(x) for x in finite]
    return {
        "n": len(finite),
        "mean_signed_delta": math.fsum(finite) / len(finite),
        "minimum_signed_delta": min(finite),
        "p05_signed_delta": signed_quantile(finite, 0.05),
        "median_signed_delta": signed_quantile(finite, 0.5),
        "p95_signed_delta": signed_quantile(finite, 0.95),
        "maximum_signed_delta": max(finite),
        "median_absolute_delta": signed_quantile(absolute, 0.5),
        "p95_absolute_delta": signed_quantile(absolute, 0.95),
        "maximum_absolute_delta": max(absolute),
        "tract_share_absolute_delta_gt_0_05": sum(x > 0.05 for x in absolute) / len(absolute),
    }


def vector_hash(ids: Sequence[str], scores: Sequence[float]) -> str:
    return endpoint.score_vector_hash(ids, scores)


def csv_sha256(path: Path) -> str:
    return endpoint.sha256_file(path)


def run(workspace: Path) -> dict[str, Any]:
    s2_digest = csv_sha256(endpoint.S2_PATH)
    if s2_digest != endpoint.S2_SHA256:
        raise ValueError("frozen S2 candidate hash mismatch")
    if csv_sha256(endpoint.S2_PREREG) != endpoint.S2_PREREG_SHA256:
        raise ValueError("S2 preregistration hash mismatch")
    if csv_sha256(endpoint.H1B_PREREG) != endpoint.H1B_PREREG_SHA256:
        raise ValueError("H1b feature preregistration hash mismatch")
    s2_prereg = json.loads(endpoint.S2_PREREG.read_text(encoding="utf-8"))
    h1b_prereg = json.loads(endpoint.H1B_PREREG.read_text(encoding="utf-8"))
    if not s2_prereg.get("registered_before_s2_value_computation"):
        raise ValueError("frozen S2 recipe is not registered")
    if csv_sha256(endpoint.ESTIMATES_PATH) != s2_prereg["frozen_inputs"]["gap_estimates"]["sha256"]:
        raise ValueError("frozen gap-estimates input hash mismatch")
    registry_before = csv_sha256(endpoint.REGISTRY_PATH)
    registry = json.loads(endpoint.REGISTRY_PATH.read_text(encoding="utf-8"))
    cards = [x for x in registry.get("candidate_set", []) if x.get("candidate_id") == endpoint.S2_ID]
    if len(cards) != 1 or cards[0].get("sha256") != s2_digest or cards[0].get("upload_authorized") is not False:
        raise ValueError("S2 registry record mismatch or candidate is unexpectedly authorized")

    ids_by_region: dict[str, list[str]] = {}
    owner: dict[str, str] = {}
    sample_hashes: dict[str, str] = {}
    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        digest = csv_sha256(sample_path)
        if digest != endpoint.SAMPLE_HASHES[region]:
            raise ValueError(f"official GEOID sample hash mismatch: {region}")
        ids = endpoint.read_official_ids(sample_path)
        if len(ids) != endpoint.REGION_COUNTS[region]:
            raise ValueError(f"official sample row count mismatch: {region}")
        ids_by_region[region] = ids
        sample_hashes[region] = digest
        for geoid in ids:
            if geoid in owner:
                raise ValueError(f"duplicate official GEOID across regions: {geoid}")
            owner[geoid] = region
    official_ids = set(owner)
    if len(official_ids) != 9379:
        raise ValueError("official scope must contain exactly 9,379 unique GEOIDs")

    s2data, s2_rows = endpoint.read_s2_official(endpoint.S2_PATH, official_ids)
    estimates = endpoint.read_estimates(endpoint.ESTIMATES_PATH, official_ids)
    features, feature_hashes = endpoint.read_facility_counts(
        workspace, ids_by_region, h1b_prereg["pinned_inputs"]["provided_overture_tract_summaries"])
    if set(estimates) != official_ids or set(features) != official_ids:
        raise ValueError("per-type estimates or Overture counts do not cover exact official IDs")

    per_region: dict[str, Any] = {}
    all_score_rows: list[dict[str, Any]] = []
    total_max_recomp_error = 0.0
    total_max_naive_error = 0.0
    for region in REGIONS:
        ids = ids_by_region[region]
        baseline_scores: list[float] = []
        combined_scores: list[float] = []
        combined_deltas: list[float] = []
        single_deltas = {kind: [] for kind in FACILITIES}
        single_scores = {kind: [] for kind in FACILITIES}
        shapley_deltas = {kind: [] for kind in FACILITIES}
        coalitions = [frozenset(mask) for mask_size in range(len(FACILITIES) + 1)
                      for mask in combinations(FACILITIES, mask_size)]
        recomposition_errors: list[float] = []
        naive_additive_errors: list[float] = []
        for geoid in ids:
            base, estimate, count = s2data[geoid], estimates[geoid], features[geoid]
            # Verify that the frozen mask and composite reproduce frozen S2.
            old_poi, old_defined, _ = endpoint.poi_variant(estimate, count, retain_zero=False)
            if abs(old_poi - float(base["poi_gap"])) > endpoint.TOL:
                raise ValueError(f"frozen POI component mismatch at {geoid}")
            old_score = endpoint.composite_score(base, estimate, old_poi, old_defined)
            if abs(old_score - float(base["coverage_gap_score"])) > endpoint.TOL:
                raise ValueError(f"frozen composite mismatch at {geoid}")

            by_mask = {}
            for coalition in coalitions:
                score = score_for_mask(base, estimate, count, coalition)
                if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                    raise ValueError(f"out-of-bounds scenario score at {geoid}")
                by_mask[coalition] = score
            base_score = by_mask[frozenset()]
            full_score = by_mask[frozenset(FACILITIES)]
            if abs(base_score - old_score) > endpoint.TOL:
                raise ValueError(f"empty coalition does not match frozen S2 at {geoid}")
            shapley = shapley_attribution(by_mask)
            combined_delta = full_score - base_score
            recomposition_error = abs(math.fsum(shapley.values()) - combined_delta)
            naive_delta_sum = math.fsum(by_mask[frozenset((kind,))] - base_score for kind in FACILITIES)
            naive_error = abs(naive_delta_sum - combined_delta)
            recomposition_errors.append(recomposition_error)
            naive_additive_errors.append(naive_error)
            baseline_scores.append(base_score)
            combined_scores.append(full_score)
            combined_deltas.append(combined_delta)
            for kind in FACILITIES:
                single_score = by_mask[frozenset((kind,))]
                single_delta = single_score - base_score
                single_scores[kind].append(single_score)
                single_deltas[kind].append(single_delta)
                shapley_deltas[kind].append(shapley[kind])
            all_score_rows.append({
                "GEOID": geoid,
                "region": region,
                "frozen_S2": format(base_score, ".17g"),
                "combined_retain_zero": format(full_score, ".17g"),
                "combined_delta": format(combined_delta, ".17g"),
                **{f"single_{kind}_delta": format(single_deltas[kind][-1], ".17g") for kind in FACILITIES},
                **{f"shapley_{kind}_delta": format(shapley[kind], ".17g") for kind in FACILITIES},
            })
        if len(baseline_scores) != endpoint.REGION_COUNTS[region]:
            raise ValueError(f"incomplete scored IDs for {region}")
        max_recomp = max(recomposition_errors, default=0.0)
        max_naive = max(naive_additive_errors, default=0.0)
        total_max_recomp_error = max(total_max_recomp_error, max_recomp)
        total_max_naive_error = max(total_max_naive_error, max_naive)
        by_type = {}
        for kind in FACILITIES:
            by_type[kind] = {
                "single_type_restoration_delta_vs_frozen_S2": summarize_contribution(single_deltas[kind]),
                "additive_shapley_attribution": summarize_contribution(shapley_deltas[kind]),
                "single_type_scenario_sha256": vector_hash(ids, single_scores[kind]),
                "shapley_delta_vector_sha256": vector_hash(ids, shapley_deltas[kind]),
            }
        per_region[region] = {
            "official_rows": len(ids),
            "frozen_S2_vector_sha256": vector_hash(ids, baseline_scores),
            "combined_retain_zero_vector_sha256": vector_hash(ids, combined_scores),
            "combined_retain_zero_delta_summary": summarize_contribution(combined_deltas),
            "facility_type": by_type,
            "attribution_validation": {
                "max_abs_error_sum_shapley_vs_combined_delta": max_recomp,
                "max_abs_error_sum_independent_single_type_deltas_vs_combined": max_naive,
                "finite_scenario_scores_in_0_1": True,
            },
        }

    if len(all_score_rows) != 9379 or len({r["GEOID"] for r in all_score_rows}) != 9379:
        raise ValueError("output rows do not cover each official GEOID exactly once")
    if total_max_recomp_error > TOL:
        raise ValueError(f"Shapley recomposition error {total_max_recomp_error} exceeds {TOL}")

    SCORES.parent.mkdir(parents=True, exist_ok=True)
    with SCORES.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(all_score_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(all_score_rows)
    scores_digest = csv_sha256(SCORES)
    registry_after = csv_sha256(endpoint.REGISTRY_PATH)
    s2_after = csv_sha256(endpoint.S2_PATH)
    if s2_after != s2_digest or registry_after != registry_before:
        raise ValueError("S2 candidate or registry bytes changed during audit")

    report = {
        "schema_version": 1,
        "analysis_id": "S2_POI_FACILITY_MASK_TYPE_CONTRIBUTION_V1",
        "result": "PASS",
        "design": {
            "baseline": "Frozen S2: include a per-type facility component iff its frozen defined flag is 1 and provided Overture category count is nonzero.",
            "single_type_restoration": "For each type, restore only that zero-count type while all other S2 rules and facility masks remain frozen; report its stand-alone composite delta.",
            "additive_attribution": "Shapley allocation of the combined retain-zero composite delta over all 3! sequential one-type-at-a-time mask-restoration orders; this is order-neutral and sums exactly to the combined endpoint.",
            "fixed_components": "Transport, building, frozen per-type gaps/defined flags, CBP half, and all component-composite rules are unchanged. Only fire/EMS/school zero-count inclusion masks vary.",
            "delta_sign": "counterfactual scenario composite minus frozen S2 composite",
            "threshold": "strict |delta| > 0.05; linear-interpolated quantiles at (n-1)*p",
        },
        "provenance": {
            "S2": {"sha256": s2_digest, "rows": s2_rows, "official_rows_used": 9379},
            "S2_preregistration_sha256": csv_sha256(endpoint.S2_PREREG),
            "H1b_preregistration_sha256": csv_sha256(endpoint.H1B_PREREG),
            "frozen_per_type_components_sha256": csv_sha256(endpoint.ESTIMATES_PATH),
            "provided_Overture_feature_hashes_by_region": feature_hashes,
            "official_sample_GEOID_hashes_by_region": sample_hashes,
            "candidate_registry_sha256_before": registry_before,
            "candidate_registry_sha256_after": registry_after,
            "S2_sha256_after": s2_after,
            "audit_script_sha256": csv_sha256(Path(__file__).resolve()),
            "endpoint_input_helper_sha256": csv_sha256(Path(endpoint.__file__).resolve()),
        },
        "scope": {"official_geoids": len(all_score_rows), "unique_geoids": len({x["GEOID"] for x in all_score_rows}),
                  "missing_ids": 0, "extra_ids": 0, "duplicate_ids": 0,
                  "region_counts": {region: len(ids_by_region[region]) for region in REGIONS},
                  "scores_finite_bounded_0_1": True},
        "input_masks": "frozen per-type flags are from reports/gap-estimates.csv; provided counts are only overture_poi_fire, overture_poi_ems, overture_poi_schools",
        "per_region": per_region,
        "global_attribution_validation": {
            "max_abs_error_sum_shapley_vs_combined_delta": total_max_recomp_error,
            "required_max_abs_error": TOL,
            "passes_recomposition": total_max_recomp_error <= TOL,
            "max_abs_error_sum_independent_single_type_deltas_vs_combined": total_max_naive_error,
            "interpretation": "Independent stand-alone mask changes can interact through the facilities-half mean; Shapley values provide a fully additive, order-neutral attribution while the standalone deltas remain available separately.",
        },
        "scores_artifact": {"path": str(SCORES.relative_to(ROOT)), "sha256": scores_digest,
                            "rows": len(all_score_rows), "columns": list(all_score_rows[0])},
        "interpretation": {"sensitivity_only": True, "accuracy_claim": False,
                           "target_accuracy_identifiable": False, "candidate_selection": False,
                           "upload_authorized": False, "upload_performed": False},
        "forbidden_information_used": {"labels": False, "removed_or_equivalent_references": False,
                                        "external_data": False, "leaderboard_or_scorecard_values": False,
                                        "target_score_inference": False, "uploads": 0},
        "reproduction": {"command": "uv run --with-requirements requirements.txt python scripts/audit_s2_poi_mask_type_contribution.py --workspace $COMPETITION_WORKSPACE",
                         "tests": "uv run --with-requirements requirements.txt --with pytest python -m pytest -q tests/test_s2_poi_mask_type_contribution.py"},
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
    report = run(args.workspace.resolve())
    print(json.dumps({"result": report["result"], "official_geoids": report["scope"]["official_geoids"],
                      "max_recomposition_error": report["global_attribution_validation"]["max_abs_error_sum_shapley_vs_combined_delta"],
                      "report": str(REPORT), "scores_sha256": report["scores_artifact"]["sha256"]}, sort_keys=True))


if __name__ == "__main__":
    main()
