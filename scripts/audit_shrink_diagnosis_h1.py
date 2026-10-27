#!/usr/bin/env python3
"""Measure rule-anchored H1 component/definedness diagnostics from frozen provided-data outputs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
RULES_ANY_UNDEFINED = {
    "maricopa-az": 0.55,
    "northern-ca": 0.37,
    "eastern-ok": 0.21,
    "south-central-tx": 0.28,
}
COMPONENTS = {
    "transport": ("transport_gap", "transport_defined"),
    "building": ("building_gap", "building_defined"),
    "poi": ("poi_gap", "poi_defined"),
    "fire": ("fire_gap", "fire_defined"),
    "ems": ("ems_gap", "ems_defined"),
    "schools": ("schools_gap", "schools_defined"),
    "establishments_half": ("cbp_gap", "cbp_defined"),
    "composite": ("coverage_gap_score", None),
}
GAP_AUDIT = ROOT / "reports/gap-estimator-audit.json"
ROUTE_AUDIT = ROOT / "reports/transport-route-ref-audit.json"
SOURCE_REPORT = ROOT / "reports/source-provenance.json"
GAP_ESTIMATES = ROOT / "reports/gap-estimates.csv"
METHODOLOGY_SOURCE = ROOT / "docs/competition.md"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def distribution(values: Sequence[float]) -> dict[str, Any]:
    vals = sorted(float(x) for x in values)
    if not vals:
        return {"n": 0, "mean": None, "min": None, "q05": None, "q25": None, "median": None,
                "q75": None, "q95": None, "max": None, "exact_zero": 0, "exact_one": 0}

    def quantile(q: float) -> float:
        pos = (len(vals) - 1) * q
        lo, hi = math.floor(pos), math.ceil(pos)
        return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)

    return {
        "n": len(vals), "mean": sum(vals) / len(vals), "min": vals[0], "q05": quantile(.05), "q25": quantile(.25),
        "median": quantile(.5), "q75": quantile(.75), "q95": quantile(.95), "max": vals[-1],
        "exact_zero": sum(x == 0.0 for x in vals), "exact_one": sum(x == 1.0 for x in vals),
        "fraction_at_zero": sum(x == 0.0 for x in vals) / len(vals),
        "fraction_at_one": sum(x == 1.0 for x in vals) / len(vals),
    }


def load_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "GEOID" not in reader.fieldnames:
            raise ValueError(f"{path}: GEOID column absent")
        rows: dict[str, dict[str, str]] = {}
        for row in reader:
            geoid = row["GEOID"]
            if geoid in rows:
                raise ValueError(f"{path}: duplicate GEOID {geoid}")
            rows[geoid] = row
        return rows


def summarize_region(rows: Sequence[dict[str, str]], reference_any_undefined: float,
                     route_record: dict[str, Any], source_record: dict[str, Any]) -> dict[str, Any]:
    n = len(rows)
    result: dict[str, Any] = {"official_tracts": n, "components": {}}
    defined_flags = {}
    for component, (gap_col, flag_col) in COMPONENTS.items():
        values = []
        mask = []
        for row in rows:
            value = float(row[gap_col])
            if not math.isfinite(value) or value < 0.0 or value > 1.0:
                raise ValueError(f"{component}: non-finite or out-of-range gap")
            values.append(value)
            if component == "composite":
                mask.append(any(row[f"{key}_defined"] == "1" for key in ("transport", "building", "poi")))
            else:
                mask.append(True if flag_col is None else row[flag_col] == "1")
        defined_flags[component] = mask
        defined_values = [v for v, flag in zip(values, mask) if flag]
        component_report = {
            "defined_rows": sum(mask),
            "undefined_rows": n - sum(mask),
            "defined_rate": sum(mask) / n,
            "defined_only_gap_distribution": distribution(defined_values),
            "all_rows_numeric_gap_distribution": distribution(values),
            "clipped_to_unit_interval": True,
        }
        if flag_col:
            component_report["flag_column"] = flag_col
        component_report["gap_column"] = gap_col
        result["components"][component] = component_report
    any_undefined = [not all(defined_flags[c][i] for c in ("transport", "building", "poi"))
                     for i in range(n)]
    own_rate = sum(any_undefined) / n
    result["definedness_comparison"] = {
        "our_any_top_level_component_undefined_rate": own_rate,
        "published_organizer_any_component_undefined_rate": reference_any_undefined,
        "ours_minus_published_rate": own_rate - reference_any_undefined,
        "ours_minus_published_percentage_points": 100.0 * (own_rate - reference_any_undefined),
        "comparison_scope": "aggregate rates only; component-specific organizer definedness is not disclosed",
    }
    ratio = route_record["sum_L_named_over_sum_L_route"]
    result["transport_scale_context"] = {
        "C2_transport_gap_distribution_defined_only": result["components"]["transport"]["defined_only_gap_distribution"],
        "provided_overture_named_over_route_network_length_ratio": ratio,
        "organizer_disclosed_cross_region_reference_ratio_range": [0.71, 1.59],
        "inside_disclosed_range_as_a_numeric_screen_only": 0.71 <= ratio <= 1.59,
        "not_comparable_as_reference_ratio": True,
        "limitation": "This is named Overture road length divided by an Overture route-network denominator, not Overture/TIGER by region; the disclosed reference ratio is cross-region context, not a regional label.",
    }
    sources = source_record["source_inputs"]
    source_mix = {}
    for theme in ("buildings", "pois"):
        theme_data = sources[theme]
        assigned = int(theme_data["assigned_to_official_sample"])
        counts = {str(k): int(v) for k, v in theme_data["dataset_membership_counts"].items()}
        source_mix[theme] = {
            "assigned_overture_records": assigned,
            "dataset_membership_counts_overlapping": counts,
            "membership_count_over_assigned_records": {
                key: (count / assigned if assigned else None) for key, count in counts.items()
            },
            "memberships_overlap": True,
            "interpretation": "Overture source provenance within provided features, not fraction of correct records or target signal",
        }
    result["feature_source_mix"] = {
        "transport": {
            "prediction_inputs": ["provided Overture roads", "provided strata ALAND/population/urbanity"],
            "per_road_dataset_membership_audit_available": False,
        },
        "building": {
            "prediction_inputs": ["provided Overture buildings", "provided strata ALAND/population/urbanity"],
            **source_mix["buildings"],
        },
        "poi": {
            "prediction_inputs": ["provided Overture places", "provided strata population/urbanity"],
            **source_mix["pois"],
        },
        "removed_reference_layers_or_external_sources_used": False,
    }
    return result


def build_report(workspace: Path) -> dict[str, Any]:
    from bias60.inputs import official_scored_pairs

    official_pairs = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    official_by_geoid = {g: r for g, r in official_pairs}
    if len(official_pairs) != 9379 or len(official_by_geoid) != 9379:
        raise ValueError("official GEOID set must have exactly 9,379 unique IDs")
    predictions = load_rows(GAP_ESTIMATES)
    if set(predictions) != set(official_by_geoid):
        raise ValueError("gap-estimates.csv does not exactly match official GEOIDs")
    with GAP_AUDIT.open(encoding="utf-8") as f:
        gap_audit = json.load(f)
    with ROUTE_AUDIT.open(encoding="utf-8") as f:
        route_audit = json.load(f)
    with SOURCE_REPORT.open(encoding="utf-8") as f:
        source_report = json.load(f)
    if gap_audit.get("no_accuracy_claim") is not True:
        raise ValueError("gap-estimator audit provenance did not identify diagnostic-only estimates")
    if gap_audit["source_constraints"]["removed_or_external_layers_used"]:
        raise ValueError("frozen estimator audit reports removed/external layer use")
    regions = {}
    for region in REGIONS:
        region_geoids = [g for g, r in official_pairs if r == region]
        rows = [predictions[g] for g in region_geoids]
        if len(rows) != int(gap_audit["per_region"][region]["rows"]):
            raise ValueError(f"{region}: row count differs from frozen gap audit")
        regions[region] = summarize_region(
            rows, RULES_ANY_UNDEFINED[region], route_audit["regions"][region],
            source_report["regions"][region])
        # Guard against drift from the previously audited flags and aggregate any-undefined rate.
        previous = gap_audit["per_region"][region]
        observed_rate = regions[region]["definedness_comparison"]["our_any_top_level_component_undefined_rate"]
        if not math.isclose(observed_rate, previous["fraction_with_any_undefined_component"], abs_tol=1e-6):
            raise ValueError(f"{region}: recomputed definedness differs from frozen audit")
    return {
        "schema_version": 1,
        "diagnosis": "H1 rule-anchored diagnostic phase; no labels or target reconstruction",
        "status": "H1 measured; H2 reliability and candidate S remain pending",
        "scope": {
            "official_regions": list(REGIONS), "official_tracts": len(official_pairs),
            "labels_or_sample_score_placeholders_read": False,
            "removed_reference_layers_or_equivalents_read": False,
            "external_data_read": False, "leaderboard_or_submission_scores_read": False,
            "target_fitting": False, "candidate_artifacts_changed": False, "uploads": 0,
        },
        "H1": {
            "verdict": "mixed regional definedness mismatch; not sufficient to attribute frozen-candidate error globally",
            "rule_context": "Organizer discloses only regional shares with at least one reference component undefined, not per-component rates or tract-level masks.",
            "known_limits": [
                "The published aggregate any-undefined figures cannot validate our transport/building/POI masks individually.",
                "The supplied route-length ratio is not the organizer's Overture/TIGER ratio; it is an internal Overture named/route screen only.",
                "Distribution/clipping and provider mix are properties of feature-derived proxies, not evidence about hidden reference scores.",
            ],
            "region_evidence": regions,
            "interpretation": "Our aggregate any-undefined rate is too low in Maricopa and Northern California and too high in Eastern Oklahoma and South-Central Texas. The sign reversal argues against a single global missingness correction; regional H1 concerns remain, but hidden component masks and target errors are not identifiable from published aggregate rates.",
        },
        "H2": {
            "status": "pending",
            "verdict": None,
            "reason": "This progress phase measured H1 diagnostics only; no reliability-weighted combination or signal-share shrink factor has been estimated.",
            "candidate_S_registered": False,
            "no_c_or_region_weights_selected": True,
        },
        "provenance": {
            "gap_estimates_csv_sha256": sha256_file(GAP_ESTIMATES),
            "gap_estimator_audit_sha256": sha256_file(GAP_AUDIT),
            "route_reference_audit_sha256": sha256_file(ROUTE_AUDIT),
            "source_provenance_report_sha256": sha256_file(SOURCE_REPORT),
            "official_sample_sha256_by_region": {
                r: sha256_file(workspace / "samples" / f"{r}-sample-submission.csv") for r in REGIONS
            },
            "official_rules_sha256": sha256_file(workspace / "RULES.md"),
            "methodology_rules_doc_sha256": sha256_file(METHODOLOGY_SOURCE),
            "analysis_script_sha256": sha256_file(Path(__file__).resolve()),
        },
        "reproduction": "uv run --with-requirements requirements.txt python scripts/audit_shrink_diagnosis_h1.py --workspace \"$COMPETITION_WORKSPACE\" --out reports/shrink-diagnosis.json",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/shrink-diagnosis.json")
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    report = build_report(args.workspace)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    summary = {r: report["H1"]["region_evidence"][r]["definedness_comparison"] for r in REGIONS}
    print(json.dumps({"official_tracts": report["scope"]["official_tracts"], "h1_verdict": report["H1"]["verdict"],
                      "definedness": summary, "output": str(args.out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
