#!/usr/bin/env python3
"""Audit one-at-a-time component-weight perturbations of frozen Track A outputs."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ("A1_physical_equal", "A2_spectral", "A3_dawid_skene")
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
REGION_COUNTS = {"maricopa-az": 1593, "northern-ca": 591, "eastern-ok": 1192, "south-central-tx": 6003}
COMPONENTS = ("transport_gap", "building_gap", "poi_gap")
COMPONENT_SHORT = {"transport_gap": "transport", "building_gap": "building", "poi_gap": "poi"}
SAMPLE_FILES = {region: f"samples/{region}-sample-submission.csv" for region in REGIONS}
OUTPUT_REL = "reports/track-a-weight-stability.json"
PERTURBATION = 0.10
UNSTABLE_P95_THRESHOLD = 0.02


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def quantile(values: Sequence[float], p: float) -> float:
    if not values:
        raise ValueError("quantile requires nonempty values")
    a = sorted(float(x) for x in values)
    if not all(math.isfinite(x) for x in a):
        raise ValueError("quantile values must be finite")
    pos = (len(a) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return a[lo]
    frac = pos - lo
    return a[lo] * (1.0 - frac) + a[hi] * frac


def read_official_ids(workspace: Path) -> tuple[list[str], dict[str, str], dict[str, str]]:
    ids: list[str] = []
    region_by_id: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for region in REGIONS:
        path = workspace / SAMPLE_FILES[region]
        if not path.is_file():
            raise FileNotFoundError(f"missing authoritative ID file: {path}")
        region_ids = []
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            header = f.readline()
            if header.partition(",")[0].strip() != "GEOID":
                raise ValueError(f"{path}: first column must be GEOID")
            for line_no, line in enumerate(f, start=2):
                if not line.strip():
                    continue
                geoid = line.partition(",")[0].strip()
                if len(geoid) != 11 or not geoid.isdigit():
                    raise ValueError(f"{path}:{line_no}: invalid GEOID {geoid!r}")
                region_ids.append(geoid)
        if len(region_ids) != REGION_COUNTS[region] or len(set(region_ids)) != len(region_ids):
            raise ValueError(f"{region}: authoritative ID count or uniqueness mismatch")
        for geoid in region_ids:
            if geoid in region_by_id:
                raise ValueError(f"duplicate official GEOID across regions: {geoid}")
            region_by_id[geoid] = region
        hashes[region] = hashlib.sha256(("\n".join(region_ids) + "\n").encode("ascii")).hexdigest()
        ids.extend(region_ids)
    if len(ids) != 9379 or len(set(ids)) != 9379:
        raise ValueError("authoritative samples must provide exactly 9,379 unique GEOIDs")
    return ids, region_by_id, hashes


def read_candidate(manifest_path: Path, candidate: str, official_ids: set[str]) -> tuple[dict[str, dict[str, float]], dict[str, Any], dict[str, Any]]:
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("candidate_id") != candidate:
        raise ValueError(f"{manifest_path}: candidate_id mismatch")
    artifact_rel = manifest.get("artifact")
    if not isinstance(artifact_rel, str) or not artifact_rel.startswith("reports/candidates/"):
        raise ValueError(f"{manifest_path}: unexpected registered artifact path")
    artifact = ROOT / artifact_rel
    artifact_hash = sha256_file(artifact)
    if artifact_hash != manifest.get("sha256"):
        raise ValueError(f"{candidate}: CSV hash does not match its registered manifest")
    if manifest.get("official_rows") != 9379 or manifest.get("rows") != 9794:
        raise ValueError(f"{candidate}: registered row counts do not match frozen Track A scope")
    expected_columns = ["GEOID", *COMPONENTS, "coverage_gap_score"]
    if manifest.get("columns") != expected_columns:
        raise ValueError(f"{candidate}: registered CSV schema mismatch")
    scores: dict[str, dict[str, float]] = {}
    all_ids: set[str] = set()
    row_count = 0
    with artifact.open("r", encoding="utf-8-sig", newline="") as f:
        header = f.readline().rstrip("\r\n").split(",")
        if header != expected_columns:
            raise ValueError(f"{candidate}: artifact header differs from registered manifest")
        for line_no, line in enumerate(f, start=2):
            if not line.strip():
                continue
            fields = line.rstrip("\r\n").split(",")
            if len(fields) != len(expected_columns):
                raise ValueError(f"{artifact}:{line_no}: malformed row")
            geoid = fields[0].strip()
            if len(geoid) != 11 or not geoid.isdigit() or geoid in all_ids:
                raise ValueError(f"{artifact}:{line_no}: invalid or duplicate GEOID {geoid!r}")
            all_ids.add(geoid)
            row_count += 1
            if geoid not in official_ids:
                continue
            try:
                values = {COMPONENTS[i]: float(fields[i + 1]) for i in range(3)}
                values["coverage_gap_score"] = float(fields[4])
            except ValueError as exc:
                raise ValueError(f"{candidate}:{line_no}: nonnumeric official component") from exc
            if not all(math.isfinite(x) and 0.0 <= x <= 1.0 for x in values.values()):
                raise ValueError(f"{candidate}:{line_no}: nonfinite or out-of-bounds official value")
            scores[geoid] = values
    if row_count != manifest["rows"]:
        raise ValueError(f"{candidate}: actual rows differ from manifest")
    if set(scores) != official_ids or len(scores) != 9379:
        raise ValueError(f"{candidate}: exact official GEOID coverage failed")
    if manifest.get("uploads") != 0 or manifest.get("uses_reference_layers") is not False or manifest.get("uses_leaderboard_values") is not False:
        raise ValueError(f"{candidate}: manifest does not certify required frozen safety scope")
    weights = manifest.get("diagnostics", {}).get("family_weights")
    if not isinstance(weights, dict) or not weights:
        raise ValueError(f"{candidate}: frozen registered weight metadata missing")
    manifest_meta = {
        "path": str(manifest_path.relative_to(ROOT)),
        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "candidate_id": candidate,
        "artifact_path": artifact_rel,
        "artifact_sha256": artifact_hash,
        "registered_rows": row_count,
        "official_rows": len(scores),
        "registered_family_weights": weights,
        "registered_composite_rule": manifest.get("diagnostics", {}).get("composite_rule"),
        "registered_method": manifest.get("method"),
    }
    return scores, manifest_meta, manifest


def infer_equal_defined_supports(components: Mapping[str, float], frozen_composite: float,
                                 tolerance: float = 2e-10) -> list[tuple[str, ...]]:
    """Enumerate A2/A3 masks consistent with the registered equal-mean output rule."""
    matches = []
    for size in range(1, len(COMPONENTS) + 1):
        for subset in itertools.combinations(COMPONENTS, size):
            value = sum(components[name] for name in subset) / len(subset)
            if abs(value - frozen_composite) <= tolerance:
                matches.append(subset)
    if not matches:
        raise ValueError("frozen equal-mean composite cannot be reproduced from component fields")
    return matches


def perturb_weights(values: Mapping[str, float], support: Sequence[str], component: str,
                    direction: int, relative_step: float = PERTURBATION) -> tuple[float, float, dict[str, float]]:
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    if not support:
        raise ValueError("cannot perturb an empty support")
    if component not in support:
        base = sum(values[name] for name in support) / len(support)
        return base, base, {name: 1.0 / len(support) for name in support}
    weights = {name: 1.0 / len(support) for name in support}
    base = sum(weights[name] * values[name] for name in support)
    changed = dict(weights)
    changed[component] *= 1.0 + direction * relative_step
    normalizer = sum(changed.values())
    changed = {name: weight / normalizer for name, weight in changed.items()}
    perturbed = sum(changed[name] * values[name] for name in support)
    return base, perturbed, changed


def summarize_deltas(values: Sequence[float]) -> dict[str, Any]:
    abs_values = [abs(float(v)) for v in values]
    if not abs_values or not all(math.isfinite(x) for x in abs_values):
        raise ValueError("perturbation deltas must be nonempty and finite")
    return {"n": len(abs_values), "p50_abs_change": quantile(abs_values, 0.50),
            "p95_abs_change": quantile(abs_values, 0.95), "max_abs_change": max(abs_values)}


def run_audit(workspace: Path) -> dict[str, Any]:
    ordered_ids, region_by_id, id_hashes = read_official_ids(workspace)
    official_ids = set(ordered_ids)
    all_scores: dict[str, dict[str, dict[str, float]]] = {}
    manifest_metadata: dict[str, dict[str, Any]] = {}
    manifests: dict[str, dict[str, Any]] = {}
    for candidate in CANDIDATES:
        path = ROOT / f"reports/candidates/{candidate}_manifest.json"
        scores, metadata, manifest = read_candidate(path, candidate, official_ids)
        all_scores[candidate] = scores
        manifest_metadata[candidate] = metadata
        manifests[candidate] = manifest

    # A2/A3 manifests specify equal means over defined R/B/POI outputs. Enumerate every
    # component mask consistent with each frozen output and use the largest possible
    # absolute perturbation when that mask is ambiguous. A1 uses its registered
    # Sca5/region-anchor composite; its source mask is not in the manifest, so all
    # three finite stored channels are treated as present and only the reweighting
    # delta is applied around (not substituted for) its frozen composite.
    support_by_candidate: dict[str, dict[str, list[tuple[str, ...]]]] = {c: {} for c in CANDIDATES}
    ambiguous_support_counts: dict[str, int] = {c: 0 for c in CANDIDATES}
    base_recomposition_deltas: dict[str, list[float]] = {c: [] for c in CANDIDATES}
    for candidate in CANDIDATES:
        rule = manifests[candidate].get("diagnostics", {}).get("composite_rule")
        for geoid in ordered_ids:
            row = all_scores[candidate][geoid]
            component_values = {name: row[name] for name in COMPONENTS}
            frozen = row["coverage_gap_score"]
            if rule == "equal mean over defined R, B, and POI estimates":
                support_options = infer_equal_defined_supports(component_values, frozen)
            else:
                support_options = [COMPONENTS]
            support_by_candidate[candidate][geoid] = support_options
            ambiguous_support_counts[candidate] += int(len(support_options) > 1)
            baseline_component_mean = sum(component_values[name] for name in support_options[0]) / len(support_options[0])
            base_recomposition_deltas[candidate].append(abs(baseline_component_mean - frozen))

    perturbation_deltas: dict[str, dict[str, dict[str, list[float]]]] = {
        candidate: {region: {f"{component}:{direction:+d}": []
                             for component in COMPONENTS for direction in (-1, 1)}
                    for region in REGIONS}
        for candidate in CANDIDATES
    }
    anchor_delta_out_of_bounds: dict[str, dict[str, dict[str, int]]] = {
        c: {r: {f"{component}:{direction:+d}": 0 for component in COMPONENTS for direction in (-1, 1)}
            for r in REGIONS} for c in CANDIDATES
    }
    component_mix_out_of_bounds: dict[str, dict[str, dict[str, int]]] = {
        c: {r: {f"{component}:{direction:+d}": 0 for component in COMPONENTS for direction in (-1, 1)}
            for r in REGIONS} for c in CANDIDATES
    }
    for candidate in CANDIDATES:
        for geoid in ordered_ids:
            region = region_by_id[geoid]
            row = all_scores[candidate][geoid]
            component_values = {name: row[name] for name in COMPONENTS}
            frozen = row["coverage_gap_score"]
            support_options = support_by_candidate[candidate][geoid]
            for component in COMPONENTS:
                for direction in (-1, 1):
                    deltas = []
                    perturbed_scores = []
                    anchored_scores = []
                    for support in support_options:
                        base, perturbed, _ = perturb_weights(component_values, support, component, direction)
                        deltas.append(perturbed - base)
                        perturbed_scores.append(perturbed)
                        anchored_scores.append(frozen + perturbed - base)
                    # Masks that cannot be distinguished from the frozen output are
                    # reported conservatively: keep the largest absolute possible effect.
                    delta = max(deltas, key=abs)
                    key = f"{component}:{direction:+d}"
                    perturbation_deltas[candidate][region][key].append(delta)
                    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in perturbed_scores):
                        component_mix_out_of_bounds[candidate][region][key] += 1
                    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in anchored_scores):
                        anchor_delta_out_of_bounds[candidate][region][key] += 1

    region_results: dict[str, Any] = {}
    for region in REGIONS:
        by_candidate = {}
        regional_p95s = []
        for candidate in CANDIDATES:
            scenario_results = {}
            for scenario, deltas in perturbation_deltas[candidate][region].items():
                summary = summarize_deltas(deltas)
                summary["out_of_bounds_frozen_anchor_plus_sensitivity_delta"] = anchor_delta_out_of_bounds[candidate][region][scenario]
                summary["out_of_bounds_reweighted_component_mixtures"] = component_mix_out_of_bounds[candidate][region][scenario]
                scenario_results[scenario] = summary
                regional_p95s.append(summary["p95_abs_change"])
            support_count_ranges = {}
            for component in COMPONENTS:
                region_options = [support_by_candidate[candidate][g]
                                  for g in ordered_ids if region_by_id[g] == region]
                support_count_ranges[component] = {
                    "minimum_support_n": sum(min(int(component in support) for support in options)
                                              for options in region_options),
                    "maximum_support_n": sum(max(int(component in support) for support in options)
                                              for options in region_options),
                }
            by_candidate[candidate] = {
                "support_n_range_by_component": support_count_ranges,
                "ambiguous_support_mask_tracts": sum(len(support_by_candidate[candidate][g]) > 1
                                                      for g in ordered_ids if region_by_id[g] == region),
                "baseline_component_recomposition_abs_delta": summarize_deltas(
                    [base_recomposition_deltas[candidate][i] for i, geoid in enumerate(ordered_ids)
                     if region_by_id[geoid] == region]),
                "scenarios": scenario_results,
            }
        maximum_p95 = max(regional_p95s)
        region_results[region] = {
            "official_ids": REGION_COUNTS[region],
            "maximum_scenario_p95_abs_change_across_A1_A2_A3": maximum_p95,
            "unstable_if_any_p95_gt_0_02": maximum_p95 > UNSTABLE_P95_THRESHOLD,
            "candidates": by_candidate,
        }
    bounded = all(value == 0 for candidates in component_mix_out_of_bounds.values()
                  for regions in candidates.values() for value in regions.values())
    all_ids_aligned = all(set(all_scores[c]) == official_ids for c in CANDIDATES)
    all_finite = all(math.isfinite(v) and 0.0 <= v <= 1.0
                     for candidate in CANDIDATES for geoid in ordered_ids
                     for v in all_scores[candidate][geoid].values())
    unstable_regions = [region for region, result in region_results.items()
                        if result["unstable_if_any_p95_gt_0_02"]]
    return {
        "schema_version": 1,
        "audit_id": "TRACK_A_WEIGHT_PERTURBATION_STABILITY_V1",
        "result": "PASS" if all_ids_aligned and all_finite else "FAIL",
        "measure_before": {
            "existing_weight_perturbation_envelope": "none recorded; baseline values and registered weights are frozen in the source candidate artifacts/manifests",
            "frozen_candidate_ids": list(CANDIDATES),
            "official_ids": len(ordered_ids),
        },
        "method": {
            "component_channels": list(COMPONENTS),
            "baseline_weights": "equal weights over active stored component channels; A2/A3 masks are all subsets consistent with their registered equal-mean composite rule, with worst-case absolute effects used when ambiguous; A1 retains all three finite channels and applies only the reweighting delta to its frozen Sca5/region-anchor composite",
            "perturbation": "one component weight is multiplied by 1 +/- 0.10; other active weights are held at their baseline values; all active weights are then renormalized to sum to one",
            "change": "perturbed component mixture minus baseline component mixture, added to frozen coverage_gap_score only as a sensitivity delta; this does not replace or rebuild a candidate; ambiguous equal-mean masks use the maximum absolute possible change",
            "quantile": "linear interpolation at (n-1)*p",
            "decision_rule": "flag a region unstable when any candidate/component/sign scenario has p95 absolute change > 0.02; report only, do not select or alter weights",
            "candidate_refit": False,
        },
        "scope": {
            "official_scored_ids": len(ordered_ids),
            "region_order": list(REGIONS),
            "candidate_order": list(CANDIDATES),
            "official_id_order_sha256": hashlib.sha256("\n".join(ordered_ids).encode("ascii")).hexdigest(),
            "official_id_order_sha256_by_region": id_hashes,
            "candidate_source_manifests": manifest_metadata,
            "candidate_modified_or_uploaded": False,
            "labels_or_removed_or_equivalent_references_used": False,
            "leaderboard_feedback_used": False,
        },
        "measure_after": {
            "exact_alignment": all_ids_aligned,
            "finite_and_bounded_source_values": all_finite,
            "finite_and_bounded_reweighted_component_mixtures": bounded,
            "out_of_bounds_reweighted_component_mixture_counts": component_mix_out_of_bounds,
            "out_of_bounds_frozen_anchor_plus_sensitivity_delta_counts": anchor_delta_out_of_bounds,
            "ambiguous_equal_mean_support_masks": ambiguous_support_counts,
            "per_region": region_results,
            "unstable_regions": unstable_regions,
            "overall_stability_result": "unstable" if unstable_regions else "within_threshold",
        },
        "acceptance": {
            "complete_exact_9379_geoid_alignment": all_ids_aligned and all(len(all_scores[c]) == 9379 for c in CANDIDATES),
            "source_hashes_match_registered_manifests": True,
            "all_perturbation_scenarios_reported": True,
            "all_source_values_finite_and_bounded": all_finite,
            "reweighted_component_mixtures_finite_and_bounded": bounded,
            "unstable_regions_flagged_at_p95_gt_0_02": True,
            "weights_and_candidates_unchanged": True,
        },
        "safety": {
            "inputs_limited_to_registered_A1_A2_A3_candidate_CSVs_manifests_and_authoritative_sample_GEOIDs": True,
            "sample_values_read": False,
            "source_candidate_artifacts_modified": False,
            "candidate_refit": False,
            "candidate_weights_modified": False,
            "candidate_registered_or_uploaded": False,
            "uploads": 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ.get("COMPETITION_WORKSPACE", ".")))
    parser.add_argument("--out", type=Path, default=ROOT / OUTPUT_REL)
    args = parser.parse_args()
    report = run_audit(args.workspace)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"],
                      "unstable_regions": report["measure_after"]["unstable_regions"],
                      "report": str(args.out)}, sort_keys=True))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
