#!/usr/bin/env python3
"""Build two pre-registered V2 recompositions using only provided-data masks.

The recipe, share tolerance, mask selector, and zero-defined fallback are frozen
in config/bias-rulemask-corrected-combiners-preregistered-2026-10-29.json before
this builder is run. No sample score placeholders, labels, references, public
scores, or credentials are read.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
from bias60.rules_composite import composite_gap  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
TARGETS = {"maricopa-az": .55, "northern-ca": .37, "eastern-ok": .21, "south-central-tx": .28}
CONFIG_PATH = ROOT / "config/bias-rulemask-corrected-combiners-preregistered-2026-10-29.json"
REPORT_PATH = ROOT / "reports/bias-rulemask-corrected-combiners-audit-2026-10-29.json"
CANDIDATES = {
    "V2_rulemask_provided_presence_20261029": "provided_presence",
    "V2_rulemask_share_selected_20261029": "share_selected",
}
V2_PATH = ROOT / "reports/candidates/V2_model.csv"
ROAD_PATH = ROOT / "reports/candidates/road_topology_candidate.csv"
BUILDING_PATH = ROOT / "reports/candidates/building_morphology.csv"
POI_PATH = ROOT / "reports/candidates/poi_confidence_weighted.csv"
C2_PATH = ROOT / "reports/gap-estimates.csv"
V2_CGS_PATH = ROOT / "reports/candidates/V2_model_cgs.csv"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_table(path: Path, needed: tuple[str, ...] | list[str]) -> dict[str, dict[str, str]]:
    result = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not set(needed) <= set(reader.fieldnames or []):
            raise ValueError(f"{path}: missing columns {sorted(set(needed)-set(reader.fieldnames or []))}")
        for row in reader:
            geoid = row["GEOID"]
            if not geoid or geoid in result:
                raise ValueError(f"{path}: empty or duplicate GEOID {geoid!r}")
            result[geoid] = {k: row[k] for k in needed}
    return result


def sample_ids(workspace: Path) -> tuple[list[str], dict[str, str]]:
    ids, region_of = [], {}
    for region in REGIONS:
        path = workspace / "samples" / f"{region}-sample-submission.csv"
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if "GEOID" not in (reader.fieldnames or []):
                raise ValueError(f"{path}: missing GEOID")
            current = [r["GEOID"] for r in reader]
        if len(current) != len(set(current)):
            raise ValueError(f"{path}: duplicate GEOID")
        for g in current:
            if g in region_of:
                raise ValueError(f"duplicate official GEOID {g}")
            region_of[g] = region
        ids.extend(current)
    if len(ids) != 9379:
        raise ValueError(f"expected 9,379 official IDs, got {len(ids)}")
    return ids, region_of


def template_ids(workspace: Path) -> tuple[list[str], dict[str, str]]:
    path = workspace / "SampleSubmission.csv"
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not {"GEOID", "region"} <= set(reader.fieldnames or []):
            raise ValueError("platform template requires GEOID and region")
        pairs = [(r["GEOID"], r["region"]) for r in reader]
    ids = [g for g, _ in pairs]
    if len(ids) != 9794 or len(set(ids)) != 9794:
        raise ValueError("expected 9,794 unique platform template GEOIDs")
    return ids, dict(pairs)


def bool_flag(table: dict[str, dict[str, str]], geoid: str, field: str) -> bool:
    value = table[geoid][field]
    if value not in ("0", "1"):
        raise ValueError(f"nonbinary flag {field} for {geoid}: {value!r}")
    return value == "1"


def value(table: dict[str, dict[str, str]], geoid: str, field: str) -> float:
    try:
        v = float(table[geoid][field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid numeric {field} for {geoid}") from exc
    if not math.isfinite(v):
        raise ValueError(f"nonfinite {field} for {geoid}")
    return v


def score_share(flags: dict[str, dict[str, bool]], ids: list[str], region_of: dict[str, str]) -> dict[str, float]:
    out = {}
    for region in REGIONS:
        rids = [g for g in ids if region_of[g] == region]
        out[region] = sum(not all(flags[g].values()) for g in rids) / len(rids)
    return out


def mean_abs_pp(shares: dict[str, float]) -> float:
    return statistics.fmean(abs(shares[r] - TARGETS[r]) * 100.0 for r in REGIONS)


def describe(values: list[float]) -> dict[str, float | int]:
    x = sorted(values)
    if not x:
        raise ValueError("empty summary")
    return {"n": len(x), "mean": statistics.fmean(x), "p50": statistics.median(x),
            "p90": x[math.floor(.90 * (len(x)-1))], "min": x[0], "max": x[-1]}


def main_build(workspace: Path) -> dict[str, Any]:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if cfg.get("registered_before_candidate_build") is not True:
        raise ValueError("candidate recipe not marked pre-registered")
    pinned_paths = {
        "V2_model.csv": V2_PATH,
        "V2_model_cgs.csv": V2_CGS_PATH,
        "road_topology_candidate.csv": ROAD_PATH,
        "building_morphology.csv": BUILDING_PATH,
        "poi_confidence_weighted.csv": POI_PATH,
        "gap-estimates.csv": C2_PATH,
        "v2-defined-mask-audit.json": ROOT / "reports/v2-defined-mask-audit.json",
    }
    for name, path in pinned_paths.items():
        if sha256(path) != cfg["input_sha256"].get(name):
            raise ValueError(f"input hash differs from preregistration: {name}")
    ids, region_of = sample_ids(workspace)
    template, template_region = template_ids(workspace)
    v2 = read_table(V2_PATH, ("GEOID", "transport_gap", "building_gap", "poi_gap"))
    road = read_table(ROAD_PATH, ("GEOID", "overture_named_road_km_5070", "route_reference_transport_defined"))
    building = read_table(BUILDING_PATH, ("GEOID", "overture_building_count", "building_defined"))
    poi = read_table(POI_PATH, ("GEOID", "all_overture_poi_count", "raw_fire_count", "raw_ems_count", "raw_schools_count", "v2_raw_poi_defined"))
    c2 = read_table(C2_PATH, ("GEOID", "transport_defined", "building_defined", "poi_defined"))
    for source_name, source in (("V2", v2), ("road", road), ("building", building), ("POI", poi), ("C2", c2)):
        if set(ids) - set(source):
            raise ValueError(f"{source_name} misses official IDs: {len(set(ids)-set(source))}")

    # Candidate A: direct provided-object presence. This mask is not fitted to
    # the published shares; the share tolerance and pass/fail rule were frozen first.
    rule_flags: dict[str, dict[str, bool]] = {}
    v2_flags: dict[str, dict[str, bool]] = {}
    c2_flags: dict[str, dict[str, bool]] = {}
    for g in ids:
        road_present = value(road, g, "overture_named_road_km_5070") > 0
        building_present = value(building, g, "overture_building_count") > 0
        facility_present = any(value(poi, g, c) > 0 for c in ("raw_fire_count", "raw_ems_count", "raw_schools_count"))
        establishment_present = value(poi, g, "all_overture_poi_count") > 0
        rule_flags[g] = {"transport": road_present, "building": building_present,
                         "poi": facility_present or establishment_present}
        v2_flags[g] = {
            "transport": bool_flag(road, g, "route_reference_transport_defined"),
            "building": bool_flag(building, g, "building_defined"),
            "poi": bool_flag(poi, g, "v2_raw_poi_defined"),
        }
        c2_flags[g] = {component: bool_flag(c2, g, component + "_defined")
                        for component in ("transport", "building", "poi")}

    v2_shares = score_share(v2_flags, ids, region_of)
    c2_shares = score_share(c2_flags, ids, region_of)
    selector_mae = {"V2": mean_abs_pp(v2_shares), "C2": mean_abs_pp(c2_shares)}
    selected_mask_source = "V2" if selector_mae["V2"] <= selector_mae["C2"] else "C2"
    expected_selected = cfg["candidate_b_existing_mask_selection"]["baseline_only_measurements"]["selected_source"]
    if selected_mask_source != expected_selected:
        raise ValueError(f"mask selector changed from preregistration: {selected_mask_source}")
    selected_flags = v2_flags if selected_mask_source == "V2" else c2_flags

    prior_disagreements = [abs(v2_shares[r] - c2_shares[r]) * 100 for r in REGIONS]
    tolerance_pp = cfg["common_undefined_share_gate"]["single_global_tolerance_pp"]
    derived_tolerance = statistics.fmean(prior_disagreements)
    if abs(derived_tolerance - tolerance_pp) > 1e-8:
        raise ValueError(f"pre-registered tolerance mismatch: {derived_tolerance} != {tolerance_pp}")
    tolerance = tolerance_pp / 100.0

    # The component columns are the only V2 fields consumed. V2's frozen
    # coverage_gap_score is intentionally not read.
    predictions = {}
    component_values = {r: {c: [] for c in ("transport", "building", "poi")} for r in REGIONS}
    for g in ids:
        row = v2[g]
        comp = {c: value(v2, g, c + "_gap") for c in ("transport", "building", "poi")}
        if any(not 0 <= x <= 1 for x in comp.values()):
            raise ValueError(f"V2 component outside [0,1] for {g}")
        predictions[g] = comp
        for c, x in comp.items():
            component_values[region_of[g]][c].append(x)

    results = {}
    candidate_files = {}
    for candidate_id, mask_kind in CANDIDATES.items():
        masks = rule_flags if mask_kind == "provided_presence" else selected_flags
        shares = score_share(masks, ids, region_of)
        share_gates = {r: {"target": TARGETS[r], "actual": shares[r],
                           "difference_pp": abs(shares[r]-TARGETS[r])*100,
                           "tolerance_pp": tolerance_pp,
                           "passes": abs(shares[r]-TARGETS[r]) <= tolerance}
                       for r in REGIONS}
        raw_score: dict[str, float] = {}
        zero_defined: list[str] = []
        region_defined_scores = {r: [] for r in REGIONS}
        for g in ids:
            pairs = [(predictions[g][c], masks[g][c]) for c in ("transport", "building", "poi")]
            score, n_defined = composite_gap(*pairs)
            if score is None or n_defined == 0:
                zero_defined.append(g)
            else:
                raw_score[g] = score
                region_defined_scores[region_of[g]].append(score)
        if any(not region_defined_scores[r] for r in REGIONS):
            raise ValueError(f"{candidate_id}: no defined scores in a region for fallback")
        regional_medians = {r: statistics.median(region_defined_scores[r]) for r in REGIONS}
        scores = dict(raw_score)
        for g in zero_defined:
            scores[g] = regional_medians[region_of[g]]
        region_score = {r: [scores[g] for g in ids if region_of[g] == r] for r in REGIONS}
        if set(scores) != set(ids):
            raise ValueError(f"{candidate_id}: incomplete score coverage")
        output = ROOT / f"reports/candidates/{candidate_id}.csv"
        with output.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["GEOID", "coverage_gap_score"], lineterminator="\n")
            writer.writeheader()
            for g in template:
                if g in scores:
                    score = scores[g]
                elif template_region[g] == "eastern-wa":
                    score = 0.0
                else:
                    raise ValueError(f"official template ID missing: {g}")
                writer.writerow({"GEOID": g, "coverage_gap_score": format(score, ".17g")})
        with output.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            written_rows = list(reader)
            if reader.fieldnames != ["GEOID", "coverage_gap_score"]:
                raise ValueError(f"{candidate_id}: wrong output columns")
        if [r["GEOID"] for r in written_rows] != template:
            raise ValueError(f"{candidate_id}: output ID/order mismatch")
        for line, row in enumerate(written_rows, 2):
            score = float(row["coverage_gap_score"])
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError(f"{candidate_id}: invalid output score at line {line}")
        artifact_hash = sha256(output)
        candidate_files[candidate_id] = {"path": output, "sha256": artifact_hash}

        duplicate_of_v2 = False
        v2_cgs = read_table(V2_CGS_PATH, ("GEOID", "coverage_gap_score"))
        if len(v2_cgs) == len(template):
            duplicate_of_v2 = all(float(v2_cgs[g]["coverage_gap_score"]) == scores[g]
                                  for g in ids) and all(
                float(v2_cgs[g]["coverage_gap_score"]) == 0.0 for g in template if g not in scores)
        all_share_pass = all(x["passes"] for x in share_gates.values())
        gates_pass = all_share_pass and not duplicate_of_v2
        results[candidate_id] = {
            "candidate_id": candidate_id, "mask_kind": mask_kind,
            "mask_source": "provided object presence" if mask_kind == "provided_presence" else selected_mask_source,
            "artifact": str(output.relative_to(ROOT)), "sha256": artifact_hash,
            "rows": len(template), "official_rows": len(ids), "template_only_rows": len(template)-len(ids),
            "columns": ["GEOID", "coverage_gap_score"],
            "undefined_share_by_region": share_gates,
            "zero_defined_rows": len(zero_defined),
            "zero_defined_fallback": "same-region median of defined candidate composites",
            "regional_fallback_medians": regional_medians,
            "local_prediction_distribution": {r: describe(region_score[r]) for r in REGIONS},
            "components_unchanged_from_V2": True,
            "duplicate_of_existing_V2_cgs_values": duplicate_of_v2,
            "local_validation_pass": gates_pass,
            "upload_eligible": gates_pass,
            "forecast": cfg["forecast"],
            "uploads_performed": 0,
        }
        manifest = {**results[candidate_id], "preregistration_sha256": sha256(CONFIG_PATH),
                    "input_sha256": {"V2": sha256(V2_PATH), "road": sha256(ROAD_PATH),
                                     "building": sha256(BUILDING_PATH), "POI": sha256(POI_PATH),
                                     "C2_masks": sha256(C2_PATH)},
                    "mask_sha256": {"preexisting_v2_audit": sha256(ROOT/"reports/v2-defined-mask-audit.json")},
                    "public_scores_used_for_values_or_mask_choice": False}
        (ROOT / f"reports/candidates/{candidate_id}_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = {
        "schema_version": 1,
        "audit_id": "BIAS_RULEMASK_CORRECTED_COMBINERS_2026_10_29_V1",
        "preregistration": {"path": str(CONFIG_PATH.relative_to(ROOT)), "sha256": sha256(CONFIG_PATH)},
        "inputs": {"V2_component_artifact": {"path": str(V2_PATH.relative_to(ROOT)), "sha256": sha256(V2_PATH),
                                               "read_columns": ["GEOID", "transport_gap", "building_gap", "poi_gap"],
                                               "coverage_gap_score_read": False},
                    "rule_mask_inputs": {p.name: sha256(p) for p in (ROAD_PATH, BUILDING_PATH, POI_PATH)},
                    "existing_masks": {"V2": {"share_by_region": v2_shares, "mean_abs_error_pp": selector_mae["V2"]},
                                       "C2": {"share_by_region": c2_shares, "mean_abs_error_pp": selector_mae["C2"]}},
                    "mask_selected_by_preregistered_rule": selected_mask_source,
                    "share_disagreement_pp": {r: abs(v2_shares[r]-c2_shares[r])*100 for r in REGIONS},
                    "common_tolerance_pp": tolerance_pp},
        "candidates": results,
        "prohibitions": {"sample_score_placeholders_read": False, "labels_used": False,
                         "removed_or_equivalent_references_used": False, "public_scores_used_for_values": False,
                         "public_scores_used_for_mask_selection": False, "frozen_V2_component_values_changed": False,
                         "uploads_attempted": False},
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    if not args.workspace.is_dir():
        raise ValueError("use authorized $COMPETITION_WORKSPACE")
    report = main_build(args.workspace)
    for cid, data in report["candidates"].items():
        shares = {r: round(x["actual"]*100, 2) for r, x in data["undefined_share_by_region"].items()}
        print(f"{cid}: mask={data['mask_source']}, shares={shares}, "
              f"zero-defined={data['zero_defined_rows']}, duplicate-V2={data['duplicate_of_existing_V2_cgs_values']}, "
              f"eligible={data['upload_eligible']}, sha256={data['sha256']}")
    print(f"Wrote {REPORT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
