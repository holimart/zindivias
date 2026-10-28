#!/usr/bin/env python3
"""Recompose frozen Track-A A1 component predictions under the RULES recipe.

Reads only the already-frozen A1 gap columns, its per-GEOID definedness audit,
and GEOID/order metadata from official samples and the platform template. It
never reads score placeholders, labels, references, public scores, or credentials.
The output is diagnostic and is ineligible for upload unless its hard component
masks independently match every published undefined share.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
from bias60.rules_composite import clip01, composite_gap  # noqa: E402

CANDIDATE_ID = "A1_rules_composite_20261028"
PREREG_PATH = ROOT / "config/bias-rules-corrected-combiner-preregistered.json"
SOURCE_PATH = ROOT / "reports/candidates/A1_physical_equal.csv"
MASK_PATH = ROOT / "reports/track-a-family-ablation-masks.csv"
OUTPUT_PATH = ROOT / f"reports/candidates/{CANDIDATE_ID}.csv"
MANIFEST_PATH = ROOT / f"reports/candidates/{CANDIDATE_ID}_manifest.json"
REPORT_PATH = ROOT / "reports/bias-rules-corrected-combiner-audit.json"
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
TARGETS = {"maricopa-az": .55, "northern-ca": .37,
           "eastern-ok": .21, "south-central-tx": .28}
COMPONENTS = ("transport_gap", "building_gap", "poi_gap")
MASK_FIELDS = {"transport_gap": "baseline_R_defined",
               "building_gap": "baseline_B_defined",
               "poi_gap": "baseline_POI_defined"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def summary(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot summarize an empty region")
    return {"n": len(ordered), "mean": statistics.fmean(ordered),
            "p50": statistics.median(ordered),
            "p90": ordered[math.floor(.9 * (len(ordered) - 1))],
            "min": ordered[0], "max": ordered[-1]}


def official_ids(workspace: Path) -> tuple[list[str], dict[str, str]]:
    ids: list[str] = []
    region_of: dict[str, str] = {}
    for region in REGIONS:
        path = workspace / "samples" / f"{region}-sample-submission.csv"
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if "GEOID" not in (reader.fieldnames or []):
                raise ValueError(f"{path}: missing GEOID")
            current = [row["GEOID"] for row in reader]
        if len(current) != len(set(current)):
            raise ValueError(f"{path}: duplicate official GEOIDs")
        for geoid in current:
            if geoid in region_of:
                raise ValueError(f"duplicate official GEOID {geoid}")
            region_of[geoid] = region
        ids.extend(current)
    if len(ids) != 9379:
        raise ValueError(f"expected 9,379 official GEOIDs, got {len(ids)}")
    return ids, region_of


def template_ids(workspace: Path) -> tuple[list[str], dict[str, str]]:
    path = workspace / "SampleSubmission.csv"
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not {"GEOID", "region"} <= set(reader.fieldnames or []):
            raise ValueError("template needs GEOID and region columns")
        pairs = [(row["GEOID"], row["region"]) for row in reader]
    ids = [g for g, _ in pairs]
    regions = dict(pairs)
    if len(ids) != 9794 or len(ids) != len(set(ids)):
        raise ValueError("platform template must contain 9,794 unique GEOIDs")
    return ids, regions


def build(workspace: Path) -> dict[str, Any]:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    if prereg.get("candidate_id") != CANDIDATE_ID:
        raise ValueError("candidate ID differs from preregistration")
    ids, region_of = official_ids(workspace)
    template, template_region = template_ids(workspace)
    source_rows, _ = read_rows(SOURCE_PATH)
    source = {row["GEOID"]: row for row in source_rows}
    if len(source) != len(source_rows):
        raise ValueError("duplicate A1 source GEOID")
    mask_rows, _ = read_rows(MASK_PATH)
    mask_groups: dict[str, list[dict[str, str]]] = {}
    for row in mask_rows:
        if row["candidate_id"] == "A1_physical_equal":
            mask_groups.setdefault(row["GEOID"], []).append(row)
    baseline_fields = ("baseline_R_defined", "baseline_B_defined", "baseline_F_defined",
                       "baseline_Est_defined", "baseline_POI_defined")
    mask = {}
    for geoid, group in mask_groups.items():
        first = group[0]
        if any(any(row[name] != first[name] for name in baseline_fields) for row in group[1:]):
            raise ValueError(f"A1 baseline definedness differs across ablation rows for {geoid}")
        mask[geoid] = first
    if set(ids) - set(source) or set(ids) - set(mask):
        raise ValueError("A1 prediction/mask GEOID coverage mismatch")

    region_values = {r: [] for r in REGIONS}
    region_components = {r: {c: [] for c in COMPONENTS} for r in REGIONS}
    region_sizes = {r: 0 for r in REGIONS}
    region_undefined = {r: 0 for r in REGIONS}
    recomposed: dict[str, float] = {}
    poi_mask_consistent = True
    max_arithmetic_residual = 0.0
    zero_defined_rows = []
    old_delta: dict[str, list[float]] = {r: [] for r in REGIONS}
    for geoid in ids:
        src, flags = source[geoid], mask[geoid]
        flags_bool = {}
        pairs = []
        any_undefined = False
        for component in COMPONENTS:
            raw = float(src[component])
            if not math.isfinite(raw) or not 0 <= raw <= 1:
                raise ValueError(f"invalid {component} value for {geoid}: {raw}")
            flag = flags[MASK_FIELDS[component]]
            if flag not in ("0", "1"):
                raise ValueError(f"nonbinary defined flag for {geoid}/{component}")
            defined = flag == "1"
            flags_bool[component] = defined
            value = clip01(raw)
            if not defined:
                any_undefined = True
                if value != 0.0:
                    raise ValueError(f"undefined {component} placeholder is nonzero for {geoid}")
            pairs.append((value, defined))
            region_components[region_of[geoid]][component].append(value)
        expected_poi = (flags["baseline_F_defined"] == "1" or
                        flags["baseline_Est_defined"] == "1")
        poi_mask_consistent &= flags_bool["poi_gap"] == expected_poi
        region = region_of[geoid]
        region_sizes[region] += 1
        region_undefined[region] += int(any_undefined)
        score, count = composite_gap(*pairs)
        if score is None or count == 0:
            # Diagnostic serialization only; this placeholder is explicitly not a score.
            zero_defined_rows.append(geoid)
            recomposed[geoid] = 0.0
            continue
        direct_mean = sum(value for value, defined in pairs if defined) / count
        residual = abs(score - direct_mean)
        max_arithmetic_residual = max(max_arithmetic_residual, residual)
        recomposed[geoid] = score
        region_values[region].append(score)
        old_delta[region].append(float(src["coverage_gap_score"]) - score)
    if not poi_mask_consistent:
        raise ValueError("POI mask differs from defined F/Est union")

    share_checks = {}
    for region, target in TARGETS.items():
        n = region_sizes[region]
        actual = region_undefined[region] / n
        share_checks[region] = {
            "published_target": target, "hard_mask_share": actual,
            "absolute_difference": abs(actual - target),
            "rounding_tolerance": .005,
            "passes": abs(actual - target) <= .005,
            "undefined_rows": region_undefined[region], "rows": n}
    candidate_ok = not zero_defined_rows and all(item["passes"] for item in share_checks.values())

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["GEOID", "coverage_gap_score"], lineterminator="\n")
        writer.writeheader()
        for geoid in template:
            if geoid in recomposed:
                value = recomposed[geoid]
            elif template_region[geoid] == "eastern-wa":
                value = 0.0
            else:
                raise ValueError(f"official template GEOID missing from model: {geoid}")
            writer.writerow({"GEOID": geoid, "coverage_gap_score": format(value, ".17g")})
    artifact_hash = sha256(OUTPUT_PATH)

    # Separate model-mask comparisons: these are diagnostics, not thresholds fitted here.
    v2 = json.loads((ROOT / "reports/v2-defined-mask-audit.json").read_text(encoding="utf-8"))
    v2_undefined = {}
    for region, data in v2["defined_mask_patterns"]["by_region"].items():
        counts = data["top_level_defined_mask_counts"]
        n = data["rows"]
        missing = sum(count for pattern, count in counts.items() if "0" in pattern)
        v2_undefined[region] = {"share": missing / n, "undefined_rows": missing, "rows": n}
    c2_rows, _ = read_rows(ROOT / "reports/gap-estimates.csv")
    c2 = {row["GEOID"]: row for row in c2_rows}
    c2_undefined = {}
    for region in REGIONS:
        regional = [g for g in ids if region_of[g] == region]
        missing = sum(any(c2[g][name + "_defined"] == "0" for name in ("transport", "building", "poi")) for g in regional)
        c2_undefined[region] = {"share": missing / len(regional), "undefined_rows": missing, "rows": len(regional)}

    report = {
        "schema_version": 1,
        "audit_id": "BIAS_RULES_CORRECTED_COMBINER_2026_10_28_V1",
        "preregistration": {"path": str(PREREG_PATH.relative_to(ROOT)), "sha256": sha256(PREREG_PATH)},
        "candidate": {"candidate_id": CANDIDATE_ID, "artifact": str(OUTPUT_PATH.relative_to(ROOT)),
                      "sha256": artifact_hash, "rows": 9794, "official_rows": 9379,
                      "template_only_rows": 415, "columns": ["GEOID", "coverage_gap_score"],
                      "upload_eligible": candidate_ok,
                      "upload_status": "blocked: hard component-definedness shares fail" if not candidate_ok else "eligible after steward timing/cap check",
                      "forecast": prereg["candidate_forecast"]},
        "source_artifacts": {
            "A1_components": {"path": str(SOURCE_PATH.relative_to(ROOT)), "sha256": sha256(SOURCE_PATH)},
            "A1_definedness_masks": {"path": str(MASK_PATH.relative_to(ROOT)), "sha256": sha256(MASK_PATH)},
            "mask_source_note": "baseline masks for A1_physical_equal from the frozen per-GEOID Track-A family-ablation mask audit"},
        "method": {
            "component_predictors": "frozen A1 physical-equal transport/building/POI gap columns; component values unchanged",
            "poi": "frozen POI gap/definedness; audit verifies POI defined iff F or Est is defined",
            "composite": "arithmetic mean of defined transport, building and POI gap predictions; undefined components omitted from denominator",
            "level_fix": "removed A1's region-specific additive Sca5/Dst5 level offset; no component or composite intercept/normalization added",
            "bounded_gap_interface": "component estimates are finite and in [0,1]; the combiner changes no component value",
            "deficit_only_scope": "the standalone one-sided O/E toy primitive is tested; the frozen A1 component artifact has no raw observed/reference pairs, so deficit-only semantics of every upstream predictor cannot be independently rederived here",
            "corrected_public_score_context_only": {"V2_model_cgs": 0.11102814, "A1_physical_equal_cgs": 0.309628459, "A2_spectral_cgs": 0.262578881,
                "use": "methodology-error signal only; not a selection, threshold, weight, or tuning target"},
            "zero_defined_rows": len(zero_defined_rows),
            "zero_defined_geoid_examples": zero_defined_rows[:10],
            "max_composite_recipe_residual": max_arithmetic_residual,
            "poi_mask_equals_F_or_Est_union": poi_mask_consistent,
            "old_A1_composite_minus_corrected_by_region": {r: summary(old_delta[r]) for r in REGIONS}},
        "published_undefined_share_validation": {
            "targets_source": "pre-registered organizer-published whole-percent shares",
            "rounding_tolerance": .005, "by_region": share_checks,
            "all_hard_masks_match": candidate_ok,
            "comparison_masks_not_used_to_build_candidate": {"V2_model": v2_undefined, "C2_estimator": c2_undefined},
            "calibrated_probability_caveat": "A Dst5 probability mean calibrated to these shares is not a validation of any candidate's hard component-definedness flags and is not substituted here."},
        "local_candidate_levels_by_region": {
            r: {"composite": summary(region_values[r]),
                "components": {c: summary(region_components[r][c]) for c in COMPONENTS}}
            for r in REGIONS},
        "validation": {"official_ids_exact_once": True, "all_scores_finite_bounded": True,
                       "composite_matches_recipe": max_arithmetic_residual <= 1e-12,
                       "undefined_placeholders_excluded": True,
                       "zero_defined_rows": len(zero_defined_rows),
                       "zero_defined_gate": len(zero_defined_rows) == 0,
                       "poi_mask_consistent": poi_mask_consistent,
                       "published_undefined_share_gate": candidate_ok,
                       "deterministic_rerun": "rerun command and compare artifact SHA before any upload"},
        "prohibitions": {"labels_used": False, "score_placeholders_read": False,
                         "removed_or_equivalent_references_used": False,
                         "leaderboard_values_used_by_builder": False, "public_score_tuning": False,
                         "upload_attempted": False},
    }
    MANIFEST_PATH.write_text(json.dumps({
        "candidate_id": CANDIDATE_ID, "artifact": str(OUTPUT_PATH.relative_to(ROOT)),
        "sha256": artifact_hash, "preregistration_sha256": report["preregistration"]["sha256"],
        "upload_eligible": candidate_ok, "forecast": prereg["candidate_forecast"],
        "source_sha256": {"A1": sha256(SOURCE_PATH), "A1_masks": sha256(MASK_PATH)},
        "report": str(REPORT_PATH.relative_to(ROOT))}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    if not args.workspace.is_dir():
        raise ValueError("use the authorized $COMPETITION_WORKSPACE")
    report = build(args.workspace)
    for region, result in report["published_undefined_share_validation"]["by_region"].items():
        print(f"{region}: hard undefined share={result['hard_mask_share']:.4%}; "
              f"published={result['published_target']:.0%}; pass={result['passes']}")
    print(f"candidate={CANDIDATE_ID} sha256={report['candidate']['sha256']} "
          f"upload_eligible={report['candidate']['upload_eligible']}")
    print(f"Wrote {REPORT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
