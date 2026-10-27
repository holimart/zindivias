#!/usr/bin/env python3
"""Rule-anchored, feature-only level/mask audit for the single shrink candidate.

Reads only official GEOID columns, the frozen C2 recipe outputs, supplied Overture
tract summaries and supplied ACS-derived strata. It never reads organizer reference
counts, scorecards, labels, sample score values, or leaderboard information.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
PREREG_PATH = ROOT / "config/shrink-h1b-rule-level-preregistered.json"
ESTIMATES_PATH = ROOT / "reports/gap-estimates.csv"
C2_PATH = ROOT / "reports/candidates/C2_component_estimator_v0.csv"
ESTIMATOR_SOURCE = ROOT / "scripts/estimate_gaps.py"
REPORT_PATH = ROOT / "reports/shrink-h1b-rule-level-audit.json"
FEATURE_COLUMNS = [
    "GEOID", "overture_road_km", "overture_named_road_km_5070",
    "overture_buildings", "overture_building_microsoft_source_share",
    "overture_poi_all", "overture_poi_fire", "overture_poi_ems",
    "overture_poi_schools", "overture_poi_unclassified",
    "overture_unclassified_fire_share", "overture_unclassified_ems_share",
    "overture_unclassified_schools_share",
]
STRATA_COLUMNS = ["GEOID", "ALAND", "pop_total", "pop_urban", "pct_urban"]
GAP_COLUMNS = [
    "GEOID", "transport_gap", "transport_defined", "building_gap", "building_defined",
    "poi_gap", "poi_defined", "fire_gap", "fire_defined", "ems_gap", "ems_defined",
    "schools_gap", "schools_defined", "cbp_gap", "cbp_defined",
]
COMPONENTS = {
    "transport": ("transport_gap", "transport_defined"),
    "building": ("building_gap", "building_defined"),
    "poi": ("poi_gap", "poi_defined"),
}
FACILITIES = ("fire", "ems", "schools")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def numeric(value: Any, name: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        if allow_none and value in ("", "NA", "nan"):
            return None
        raise ValueError(f"{name}: invalid numeric {value!r}") from exc
    if not math.isfinite(result):
        if allow_none:
            return None
        raise ValueError(f"{name}: non-finite numeric {value!r}")
    return result


def flag(value: Any, name: str) -> int:
    parsed = int(value)
    if parsed not in (0, 1):
        raise ValueError(f"{name}: expected binary flag, got {value!r}")
    return parsed


def clip_gap(observed: float, expected: float, defined: int) -> float:
    if not defined:
        return 0.0
    return min(1.0, max(0.0, 1.0 - observed / max(expected, 0.5)))


def compose_poi(facility_gaps: list[tuple[float, int]], cbp_gap: float, cbp_defined: int) -> tuple[float, int]:
    facility_values = [float(value) for value, defined in facility_gaps if defined]
    halves = []
    if facility_values:
        halves.append(sum(facility_values) / len(facility_values))
    if cbp_defined:
        halves.append(float(cbp_gap))
    if not halves:
        return 0.0, 0
    return sum(halves) / len(halves), 1


def summary(values: list[float], defined: list[int]) -> dict[str, Any]:
    kept = [float(v) for v, d in zip(values, defined) if d]
    return {
        "defined_rows": len(kept),
        "undefined_rows": len(values) - len(kept),
        "defined_mean": float(sum(kept) / len(kept)) if kept else None,
        "defined_gap_mass_sum": float(sum(kept)),
        "positive_defined_rows": sum(v > 0.0 for v in kept),
        "all_official_prediction_mass_sum": float(sum(values)),
    }


def load_csv_map(path: Path, columns: list[str], allowed_ids: set[str] | None = None,
                 *, ignore_values_outside_scope: bool = False) -> dict[str, dict[str, str]]:
    out = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not set(columns).issubset(reader.fieldnames):
            raise ValueError(f"{path}: required columns absent: {columns}")
        for row in reader:
            geoid = str(row["GEOID"])
            if allowed_ids is not None and geoid not in allowed_ids:
                if ignore_values_outside_scope:
                    continue  # Do not parse or use any out-of-scope score fields.
                continue
            if geoid in out:
                raise ValueError(f"{path}: duplicate official GEOID {geoid}")
            out[geoid] = {column: row[column] for column in columns if column != "GEOID"}
    return out


def load_parquet_map(path: Path, columns: list[str], allowed_ids: set[str]) -> dict[str, dict[str, Any]]:
    import pyarrow.parquet as pq

    table = pq.read_table(path, columns=columns)
    out = {}
    for row in table.to_pylist():
        geoid = str(row["GEOID"])
        if geoid not in allowed_ids:
            continue
        if geoid in out:
            raise ValueError(f"{path}: duplicate official GEOID {geoid}")
        out[geoid] = {column: row[column] for column in columns if column != "GEOID"}
    return out


def read_official_ids(workspace: Path) -> tuple[list[tuple[str, str]], dict[str, str], dict[str, str]]:
    from bias60.inputs import official_scored_pairs

    ordered = [(str(geoid), str(region)) for geoid, region in official_scored_pairs(workspace)]
    ids_by_region = {region: [g for g, r in ordered if r == region] for region in REGIONS}
    if (len(ordered) != 9379 or len({g for g, _ in ordered}) != 9379
            or set(ids_by_region) != set(REGIONS)
            or {r: len(ids_by_region[r]) for r in REGIONS} != {
                "maricopa-az": 1593, "northern-ca": 591,
                "eastern-ok": 1192, "south-central-tx": 6003}):
        raise ValueError("official sample ID partition differs from frozen contract")
    return ordered, {g: r for g, r in ordered}, ids_by_region


def urban_fraction(strata: dict[str, Any]) -> float:
    raw = numeric(strata.get("pct_urban"), "pct_urban", allow_none=True)
    if raw is not None:
        return min(1.0, max(0.0, raw))
    pop = numeric(strata.get("pop_total"), "pop_total")
    pop_urban = numeric(strata.get("pop_urban"), "pop_urban")
    if pop > 0:
        return min(1.0, max(0.0, pop_urban / pop))
    return 0.5


def expected_counts(features: dict[str, Any], strata: dict[str, Any]) -> dict[str, float]:
    pop = numeric(strata.get("pop_total"), "pop_total")
    aland = numeric(strata.get("ALAND"), "ALAND")
    area_km2 = max(aland / 1_000_000.0, 0.01)
    urban = urban_fraction(strata)
    return {
        "transport": 0.35 + 0.55 * math.sqrt(area_km2) + 0.0007 * pop + 2.0 * urban,
        "building": max(0.5, 0.30 * pop + 4.0 * math.sqrt(area_km2) + 5.0 * urban),
        "fire": 0.15 + pop / 30000.0 + 0.45 * urban,
        "ems": 0.15 + pop / 40000.0 + 0.45 * urban,
        "schools": 0.20 + pop / 2500.0 + 0.20 * urban,
        "establishments": 0.20 + pop / 70.0 + 0.50 * urban,
    }


def effective_facility_count(features: dict[str, Any], facility: str) -> tuple[float, float]:
    raw = numeric(features.get(f"overture_poi_{facility}"), f"overture_poi_{facility}")
    unknown = numeric(features.get("overture_poi_unclassified"), "overture_poi_unclassified")
    share = numeric(features.get(f"overture_unclassified_{facility}_share"),
                     f"overture_unclassified_{facility}_share")
    if not 0 <= share <= 1:
        raise ValueError(f"invalid unclassified share for {facility}")
    return raw, raw + unknown * share


def audit_region(region: str, ids: list[str], estimates: dict[str, dict[str, str]],
                 c2: dict[str, dict[str, str]], features: dict[str, dict[str, Any]],
                 strata: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for geoid in ids:
        if geoid not in estimates or geoid not in c2 or geoid not in features or geoid not in strata:
            raise ValueError(f"{region}: missing official GEOID input {geoid}")
        e, c, f, s = estimates[geoid], c2[geoid], features[geoid], strata[geoid]
        est_values = {name: numeric(e[f"{name}_gap"], f"{name}_gap") for name in (
            "transport", "building", "poi", "fire", "ems", "schools", "cbp")}
        est_flags = {name: flag(e[f"{name}_defined"], f"{name}_defined") for name in (
            "transport", "building", "poi", "fire", "ems", "schools", "cbp")}
        c2_values = {name: numeric(c[f"{name}_gap"], f"C2 {name}_gap") for name in (
            "transport", "building", "poi")}
        for name in COMPONENTS:
            if abs(c2_values[name] - est_values[name]) > 1e-12:
                raise ValueError(f"{region} {geoid}: C2 and gap-estimate component mismatch: {name}")
        expected = expected_counts(f, s)
        obs_road = numeric(f["overture_road_km"], "overture_road_km")
        obs_building = numeric(f["overture_buildings"], "overture_buildings")
        poi_all = numeric(f["overture_poi_all"], "overture_poi_all")
        proxy_observed = {
            "transport": obs_road,
            "building": obs_building,
            "establishments": poi_all,
        }
        proxy_flags = {
            "transport": int(expected["transport"] >= 6.0),
            "building": int(obs_building > 0 or expected["building"] >= 1.0),
            "establishments": int(poi_all > 0 or expected["establishments"] >= 1.0),
        }
        for facility in FACILITIES:
            raw, effective = effective_facility_count(f, facility)
            proxy_observed[facility] = effective
            proxy_flags[facility] = int(raw > 0 or expected[facility] >= 0.50)
        for name in proxy_flags:
            source_name = "cbp" if name == "establishments" else name
            if est_flags[source_name] != proxy_flags[name]:
                raise ValueError(f"{region} {geoid}: defined flag disagrees with pinned recipe proxy for {name}")
            expected_name = "establishments" if name == "establishments" else name
            if est_flags[source_name]:
                proxy_gap = clip_gap(proxy_observed[name], expected[expected_name], est_flags[source_name])
                source_gap = est_values[source_name]
                if abs(proxy_gap - source_gap) > 1e-10:
                    raise ValueError(f"{region} {geoid}: recipe gap disagrees with proxy formula for {name}")
            elif est_values[source_name] != 0.0:
                raise ValueError(f"{region} {geoid}: undefined recipe placeholder is nonzero for {name}")
        for name in COMPONENTS:
            if est_flags[name] and not math.isclose(est_values[name], c2_values[name], abs_tol=1e-12):
                raise ValueError(f"{region} {geoid}: C2 component not equal to its defined recipe value")

        shares = []
        for facility in FACILITIES:
            shares.append((facility, numeric(f[f"overture_poi_{facility}"], f"overture_poi_{facility}")))
        rows.append({
            "GEOID": geoid, "est": est_values, "flags": est_flags, "c2": c2_values,
            "features": f, "strata": s, "expected": expected,
            "observed_proxy": proxy_observed, "proxy_flags": proxy_flags,
            "facility_raw_counts": dict(shares),
        })

    component_summaries = {}
    for name, (gap_col, defined_col) in COMPONENTS.items():
        vals = [row["c2"][name] for row in rows]
        defs = [row["flags"][name] for row in rows]
        component_summaries[name] = summary(vals, defs)
        component_summaries[name]["C2_component_column"] = gap_col
        component_summaries[name]["defined_flag_source"] = f"reports/gap-estimates.csv:{defined_col}"

    deficit_proxy = {}
    for name in ("transport", "building", "fire", "ems", "schools", "establishments"):
        source_name = "cbp" if name == "establishments" else name
        at_or_above = [row for row in rows if row["observed_proxy"][name] >= row["expected"][name]]
        mass_total = sum(row["est"][source_name] for row in rows if row["flags"][source_name])
        mass_over = sum(row["est"][source_name] for row in at_or_above if row["flags"][source_name])
        positive_over = sum(row["est"][source_name] > 1e-12 for row in at_or_above if row["flags"][source_name])
        if mass_over > 1e-9 or positive_over:
            raise ValueError(f"{region} {name}: positive recipe gap where Overture >= internal expected proxy")
        deficit_proxy[name] = {
            "recipe_component": source_name,
            "defined_rows": sum(row["flags"][source_name] for row in rows),
            "tracts_at_or_above_internal_expected_count": len(at_or_above),
            "fraction_at_or_above_internal_expected_count": len(at_or_above) / len(rows),
            "recipe_gap_mass_on_at_or_above_rows": float(mass_over),
            "positive_gap_rows_on_at_or_above_rows": int(positive_over),
            "total_defined_recipe_gap_mass": float(mass_total),
            "actual_hidden_reference_clipped_mass": None,
            "interpretation": "current recipe clips against its own population/land/urbanity expected-count proxy; this proxy is not an observed or validated organizer reference count",
        }

    building_count = sum(numeric(row["features"]["overture_buildings"], "overture_buildings") for row in rows)
    microsoft_count_equiv = 0.0
    microsoft_known_count = 0.0
    microsoft_unknown_building_count = 0.0
    microsoft_scenario_removed_mass = 0.0
    microsoft_scenario_known_gap_mass = 0.0
    for row in rows:
        n_build = numeric(row["features"]["overture_buildings"], "overture_buildings")
        share = numeric(row["features"].get("overture_building_microsoft_source_share"),
                        "overture_building_microsoft_source_share", allow_none=True)
        if share is None:
            microsoft_unknown_building_count += n_build
            continue
        if not 0.0 <= share <= 1.0:
            raise ValueError("Microsoft source share outside [0,1]")
        microsoft_known_count += n_build
        microsoft_count_equiv += n_build * share
        if row["flags"]["building"]:
            microsoft_scenario_known_gap_mass += row["c2"]["building"]
            microsoft_scenario_removed_mass += row["c2"]["building"] * share
    ms_share = microsoft_count_equiv / microsoft_known_count if microsoft_known_count else None
    building_mass = component_summaries["building"]["defined_gap_mass_sum"]
    building_scenario_retained = microsoft_scenario_known_gap_mass - microsoft_scenario_removed_mass

    poi_all_total = sum(numeric(row["features"]["overture_poi_all"], "overture_poi_all") for row in rows)
    poi_all_zero = sum(numeric(row["features"]["overture_poi_all"], "overture_poi_all") == 0 for row in rows)
    facility_results = {}
    baseline_poi_mass = sum(row["est"]["poi"] for row in rows if row["flags"]["poi"])
    baseline_poi_defined = sum(row["flags"]["poi"] for row in rows)
    per_type_stress = {}
    for facility in FACILITIES:
        raw_zero_rows = [row for row in rows
                         if numeric(row["features"][f"overture_poi_{facility}"], f"overture_poi_{facility}") == 0]
        gap_mass_zero = sum(row["est"][facility] for row in raw_zero_rows if row["flags"][facility])
        defined_zero = sum(row["flags"][facility] for row in raw_zero_rows)
        undefined_zero = len(raw_zero_rows) - defined_zero
        after_values = []
        after_defined = 0
        for row in rows:
            active = [(row["est"][name], row["flags"][name] and not (
                name == facility and numeric(row["features"][f"overture_poi_{name}"], name) == 0))
                for name in FACILITIES]
            score, defined = compose_poi(active, row["est"]["cbp"], row["flags"]["cbp"])
            if defined:
                after_values.append(score)
                after_defined += 1
        after_mass = sum(after_values)
        per_type_stress[facility] = {
            "overture_exact_category_zero_tracts": len(raw_zero_rows),
            "overture_exact_category_zero_fraction": len(raw_zero_rows) / len(rows),
            "recipe_defined_on_overture_zero_tracts": defined_zero,
            "recipe_undefined_on_overture_zero_tracts": undefined_zero,
            "per_type_recipe_gap_mass_on_overture_zero_tracts": float(gap_mass_zero),
            "aggregate_poi_mass_before": float(baseline_poi_mass),
            "aggregate_poi_mass_after_drop_type_where_overture_zero": float(after_mass),
            "aggregate_poi_mass_removed_under_this_stress_test": float(baseline_poi_mass - after_mass),
            "poi_defined_rows_before": baseline_poi_defined,
            "poi_defined_rows_after": after_defined,
            "reference_zero_tracts": None,
            "actual_USGS_undefined_mass": None,
            "stress_test_assumption": "raw Overture exact-category count 0 implies USGS reference count 0; explicitly unverified",
        }
        facility_results[facility] = {
            "overture_raw_category_total": sum(numeric(row["features"][f"overture_poi_{facility}"], facility) for row in rows),
            "overture_raw_category_zero_tracts": len(raw_zero_rows),
            "recipe_defined_on_zero_tracts": defined_zero,
            "recipe_undefined_on_zero_tracts": undefined_zero,
            "recipe_per_type_gap_mass_on_zero_tracts": float(gap_mass_zero),
            "total_defined_per_type_gap_mass": float(sum(row["est"][facility] for row in rows if row["flags"][facility])),
            "true_USGS_zero_count": None,
            "actual_rule_mass_removed": None,
            "assumption_bound_POI_counterfactual": per_type_stress[facility],
        }

    all_facility_zero_mask = {
        "fire": lambda row: numeric(row["features"]["overture_poi_fire"], "fire") == 0,
        "ems": lambda row: numeric(row["features"]["overture_poi_ems"], "ems") == 0,
        "schools": lambda row: numeric(row["features"]["overture_poi_schools"], "schools") == 0,
    }
    combined_after_values = []
    combined_after_defined = 0
    for row in rows:
        active = [(row["est"][name], row["flags"][name] and not all_facility_zero_mask[name](row))
                  for name in FACILITIES]
        value, defined = compose_poi(active, row["est"]["cbp"], row["flags"]["cbp"])
        if defined:
            combined_after_values.append(value)
            combined_after_defined += 1
    combined_after_mass = sum(combined_after_values)

    establishments_mass = sum(row["est"]["cbp"] for row in rows if row["flags"]["cbp"])
    establishments_at_or_above = [row for row in rows
                                  if row["observed_proxy"]["establishments"] >= row["expected"]["establishments"]]
    establishments = {
        "overture_all_places_total": float(poi_all_total),
        "overture_all_places_zero_tracts": int(poi_all_zero),
        "overture_all_places_mean_per_official_tract": float(poi_all_total / len(rows)),
        "internal_expected_establishment_count_proxy": "0.20 + pop_total/70 + 0.50*urban_fraction",
        "tracts_overture_all_places_at_or_above_internal_proxy": len(establishments_at_or_above),
        "recipe_defined_CBP_proxy_rows": sum(row["flags"]["cbp"] for row in rows),
        "recipe_CBP_proxy_gap_mass": float(establishments_mass),
        "recipe_CBP_proxy_gap_mass_on_overproxy_rows": float(sum(
            row["est"]["cbp"] for row in establishments_at_or_above if row["flags"]["cbp"])),
        "CBP_reference_counts_read": False,
        "actual_O_count_vs_CBP_excess_mass": None,
        "actual_rule_mass_removed": None,
        "maximum_candidate_POI_mass_at_risk": float(component_summaries["poi"]["defined_gap_mass_sum"]),
    }

    named_road_zero = sum(numeric(row["features"]["overture_named_road_km_5070"], "overture_named_road_km_5070") == 0 for row in rows)
    input_road_zero = sum(numeric(row["features"]["overture_road_km"], "overture_road_km") == 0 for row in rows)
    transport = {
        "overture_named_class_road_zero_tracts": int(named_road_zero),
        "overture_model_road_length_zero_tracts": int(input_road_zero),
        "recipe_transport_defined_rows": component_summaries["transport"]["defined_rows"],
        "recipe_transport_gap_mass": component_summaries["transport"]["defined_gap_mass_sum"],
        "recipe_transport_gap_mass_on_overture_named_road_zero_tracts": float(sum(
            row["c2"]["transport"] for row in rows
            if numeric(row["features"]["overture_named_road_km_5070"], "named road km") == 0
            and row["flags"]["transport"])),
        "TIGER_S1100_S1200_reference_presence_read": False,
        "actual_transport_undefined_tracts": None,
        "actual_rule_mass_removed": None,
        "maximum_candidate_transport_mass_at_risk": component_summaries["transport"]["defined_gap_mass_sum"],
    }

    rule_mass_table = []
    for name in ("transport", "building", "fire", "ems", "schools", "establishments"):
        proxy = deficit_proxy[name]
        rule_mass_table.append({
            "rule": "deficit_only_clip_against_C2_internal_proxy",
            "component_or_half": name,
            "current_defined_recipe_gap_mass": proxy["total_defined_recipe_gap_mass"],
            "tracts_at_or_above_internal_proxy": proxy["tracts_at_or_above_internal_expected_count"],
            "recipe_gap_mass_on_at_or_above_rows": proxy["recipe_gap_mass_on_at_or_above_rows"],
            "maximum_mass_at_risk_if_all_current_positive_mass_were_clipped": proxy["total_defined_recipe_gap_mass"],
            "actual_hidden_reference_mass_moved": None,
            "identification": "internal-proxy clip is already zero; organizer-reference comparison unavailable",
        })
    rule_mass_table.append({
        "rule": "Microsoft_source_overlap_counterfactual",
        "component_or_half": "building",
        "current_defined_recipe_gap_mass": building_mass,
        "microsoft_source_weighted_share": ms_share,
        "source_labeled_building_count_equivalent": float(microsoft_count_equiv),
        "source_known_building_count": float(microsoft_known_count),
        "hypothetical_mass_removed_if_each_Microsoft_share_is_exact_reference_overlap": float(microsoft_scenario_removed_mass),
        "hypothetical_mass_retained_under_that_assumption": float(building_scenario_retained),
        "actual_hidden_reference_mass_moved": None,
        "identification": "upstream source membership does not prove footprint-level equality, coverage, vintage, or exact reference overlap",
    })
    for facility in FACILITIES:
        data = facility_results[facility]
        rule_mass_table.append({
            "rule": "undefined_facility_type_if_USGS_reference_zero",
            "component_or_half": facility,
            "current_defined_recipe_gap_mass_on_overture_zero_mask": data["recipe_per_type_gap_mass_on_zero_tracts"],
            "overture_zero_tracts": data["overture_raw_category_zero_tracts"],
            "recipe_defined_on_overture_zero_tracts": data["recipe_defined_on_zero_tracts"],
            "stress_test_POI_mass_removed_if_overture_zero_equals_USGS_zero": data["assumption_bound_POI_counterfactual"]["aggregate_poi_mass_removed_under_this_stress_test"],
            "actual_hidden_reference_mass_moved": None,
            "identification": "zero Overture count is not evidence of zero USGS count; stress test is not scored or promoted",
        })
    rule_mass_table.append({
        "rule": "transport_defined_only_if_TIGER_S1100_S1200_present",
        "component_or_half": "transport",
        "current_defined_recipe_gap_mass": transport["recipe_transport_gap_mass"],
        "overture_named_road_zero_tracts": transport["overture_named_class_road_zero_tracts"],
        "maximum_mass_at_risk_if_all_transport_scores_were_undefined": transport["maximum_candidate_transport_mass_at_risk"],
        "actual_hidden_reference_mass_moved": None,
        "identification": "TIGER highway presence is not observed; Overture named-road absence cannot establish the reference mask",
    })
    rule_mass_table.append({
        "rule": "all_Overture_places_vs_CBP_establishments",
        "component_or_half": "POI_establishments",
        "current_defined_recipe_gap_mass": establishments_mass,
        "overture_all_places_total": establishments["overture_all_places_total"],
        "tracts_at_or_above_internal_expected_count_proxy": len(establishments_at_or_above),
        "maximum_mass_at_risk": establishments_mass,
        "actual_hidden_reference_mass_moved": None,
        "identification": "CBP reference counts unavailable/forbidden; all-place counts alone do not establish Overtures>=CBP",
    })

    source_total = sum(numeric(row["features"]["overture_poi_all"], "overture_poi_all") for row in rows)
    return {
        "region": region,
        "official_tracts": len(rows),
        "component_mass": component_summaries,
        "deficit_only_proxy_audit": deficit_proxy,
        "building_Microsoft_source": {
            "overture_buildings_total": float(building_count),
            "source_share_known_building_count": float(microsoft_known_count),
            "source_share_unknown_building_count": float(microsoft_unknown_building_count),
            "Microsoft_sourced_building_count_equivalent": float(microsoft_count_equiv),
            "Microsoft_sourced_weighted_share": ms_share,
            "C2_building_gap_mass": float(building_mass),
            "hypothetical_mass_removed_if_source_share_equals_exact_reference_overlap": float(microsoft_scenario_removed_mass),
            "hypothetical_remaining_building_mass_under_that_assumption": float(building_scenario_retained),
            "actual_MS_reference_overlap_count": None,
            "actual_rule_mass_removed": None,
            "interpretation": "provided Overture source-membership diagnostic only; not proof that Microsoft US reference counts match these records",
        },
        "poi_establishments": establishments,
        "poi_facility_zero_masks": facility_results,
        "poi_facility_combined_zero_counterfactual": {
            "baseline_poi_mass": float(baseline_poi_mass),
            "baseline_poi_defined_tracts": int(baseline_poi_defined),
            "counterfactual_poi_mass_after_dropping_all_types_with_zero_raw_Overture_count": float(combined_after_mass),
            "counterfactual_poi_defined_tracts": int(combined_after_defined),
            "mass_removed_under_unverified_zero_equals_reference_zero_assumption": float(baseline_poi_mass - combined_after_mass),
            "actual_hidden_reference_mass_moved": None,
        },
        "transport_TIGER_mask": transport,
        "provided_feature_count_checks": {
            "overture_all_places_total": float(source_total),
            "rule_has_no_CBP_reference_input": True,
        },
        "rule_mass_table": rule_mass_table,
        "data_use": {
            "official_sample_ids_only": True,
            "sample_score_placeholders_read": False,
            "hidden_labels_or_target_values_read": False,
            "TIGER_reference_layer_or_equivalent_read": False,
            "Microsoft_US_Building_Footprints_or_equivalent_read": False,
            "USGS_facility_reference_or_equivalent_read": False,
            "CBP_reference_or_equivalent_read": False,
            "external_data_or_numerical_counts_read": False,
            "leaderboard_or_scorecard_information_used": False,
            "target_or_region_moment_fitting": False,
            "candidate_artifacts_modified": False,
            "candidate_registration_modified": False,
            "uploads": 0,
        },
    }


def run_audit(workspace: Path, out: Path = REPORT_PATH) -> dict[str, Any]:
    from bias60.inputs import official_scored_pairs

    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    if not prereg.get("registered_before_h1b_measurement"):
        raise ValueError("H1b rule-level protocol is not registered")
    pinned = prereg["pinned_inputs"]
    if sha256_file(C2_PATH) != pinned["C2_feature_only_prediction_csv"]["sha256"]:
        raise ValueError("frozen C2 artifact hash mismatch")
    if sha256_file(ESTIMATES_PATH) != pinned["C2_recipe_gap_estimates_csv"]["sha256"]:
        raise ValueError("frozen gap-estimates input hash mismatch")
    if sha256_file(ESTIMATOR_SOURCE) != pinned["C2_recipe_source"]["sha256"]:
        raise ValueError("C2 recipe source hash mismatch")
    official_order = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(official_order) != 9379 or len({g for g, _ in official_order}) != 9379:
        raise ValueError("official 9,379 GEOID scope mismatch")
    ids_by_region = {r: [g for g, reg in official_order if reg == r] for r in REGIONS}
    if set(ids_by_region) != set(REGIONS):
        raise ValueError("official region partition mismatch")
    official_ids = {g for g, _ in official_order}
    all_estimates = load_csv_map(ESTIMATES_PATH, GAP_COLUMNS, official_ids)
    if set(all_estimates) != official_ids:
        raise ValueError("gap-estimates GEOID coverage mismatch")
    all_c2 = load_csv_map(C2_PATH, ["GEOID", "transport_gap", "building_gap", "poi_gap"],
                          official_ids, ignore_values_outside_scope=True)
    if set(all_c2) != official_ids:
        raise ValueError("C2 artifact official GEOID coverage mismatch")

    regions: dict[str, Any] = {}
    all_rule_rows = []
    input_provenance = {}
    for region in REGIONS:
        feature_path = workspace / "gap-estimator" / "features" / f"{region}-overture-tract-summary.parquet"
        strata_path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        feature_expected = pinned["provided_overture_tract_summaries"][region]["sha256"]
        strata_expected = pinned["provided_strata"][region]["sha256"]
        if sha256_file(feature_path) != feature_expected or sha256_file(strata_path) != strata_expected:
            raise ValueError(f"{region}: provided feature/strata input hash mismatch")
        allowed = set(ids_by_region[region])
        features = load_parquet_map(feature_path, FEATURE_COLUMNS, allowed)
        strata = load_parquet_map(strata_path, STRATA_COLUMNS, allowed)
        estimates = {g: all_estimates[g] for g in ids_by_region[region]}
        c2 = {g: all_c2[g] for g in ids_by_region[region]}
        if set(features) != allowed or set(strata) != allowed:
            raise ValueError(f"{region}: provided feature/strata rows do not cover exact official IDs")
        result = audit_region(region, ids_by_region[region], estimates, c2, features, strata)
        regions[region] = result
        all_rule_rows.extend({"region": region, **row} for row in result["rule_mass_table"])
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        input_provenance[region] = {
            "official_sample_sha256": sha256_file(sample_path),
            "official_geoids": len(ids_by_region[region]),
            "feature_summary_path": str(feature_path),
            "feature_summary_sha256": sha256_file(feature_path),
            "feature_columns_read": FEATURE_COLUMNS,
            "strata_path": str(strata_path),
            "strata_sha256": sha256_file(strata_path),
            "strata_columns_read": STRATA_COLUMNS,
        }
    overall_components = {}
    for component in COMPONENTS:
        rowsum = sum(regions[r]["component_mass"][component]["defined_gap_mass_sum"] for r in REGIONS)
        ndefined = sum(regions[r]["component_mass"][component]["defined_rows"] for r in REGIONS)
        overall_components[component] = {
            "defined_rows": ndefined,
            "defined_gap_mass_sum": float(rowsum),
            "defined_mean": float(rowsum / ndefined) if ndefined else None,
            "positive_defined_rows": sum(regions[r]["component_mass"][component]["positive_defined_rows"] for r in REGIONS),
        }
    report = {
        "schema_version": 1,
        "audit": "H1b rule-anchored C2 level and mask diagnostic; feature-only, not target evaluation",
        "preregistration": {
            "path": str(PREREG_PATH.relative_to(ROOT)),
            "sha256": sha256_file(PREREG_PATH),
            "registered_before_h1b_measurement": True,
            "thresholds_or_fitted_parameters_added": False,
        },
        "scope": {
            "official_regions": list(REGIONS),
            "official_tracts": len(official_order),
            "official_region_counts": {r: len(ids_by_region[r]) for r in REGIONS},
            "exact_official_GEOID_partition_verified": True,
            "official_order_sha256": hashlib.sha256("\n".join(g for g, _ in official_order).encode()).hexdigest(),
        },
        "measure_before": {
            "C2_component_mass_by_region": {r: regions[r]["component_mass"] for r in REGIONS},
            "C2_component_mass_overall": overall_components,
            "source_H1_C2_means_note": "All means here are recomputed from the frozen C2 feature-only component CSV over the existing recipe's defined flags; no organizer reference values are available.",
        },
        "rule_level_measurements": {
            "per_region": regions,
            "per_rule_component_region_table": all_rule_rows,
            "interpretation": "Numeric mass values are sums of frozen C2/recipe gap predictions or explicitly labeled proxy counterfactuals. Null actual-reference mass means it is not identifiable from permitted provided data, not zero.",
        },
        "decision": {
            "H1b_verdict": "inconclusive for organizer-reference levels/masks; internal proxy rules are already clipped, and the only additional source/zero-count signals do not identify the removed reference counts or undefined masks",
            "recommendation": "retain S_crossmethod_reliability_shrink_v1 as the sole shrink candidate; do not build or register S2",
            "reason_S2_not_built": [
                "TIGER S1100/S1200 tract presence is absent, so the organizer transport-defined mask and any hidden-reference undefined mass cannot be identified.",
                "Microsoft-sourced Overture membership is measurable, but it does not prove exact overlap, completeness, vintage, or count equality with the removed Microsoft US Building Footprints reference; the source-share correction is only a sensitivity.",
                "CBP and USGS counts are absent/forbidden. Overture all-place counts and exact-category zeros cannot prove Overture>=CBP or USGS-zero; facility stress tests therefore cannot be promoted to S2.",
                "The C2 estimator already clips its own feature-derived expected-count ratios at zero when Overture counts meet/exceed those internal proxies. Those proxies are assumptions, not validated reference counts. No rule correction has an admissible identified effect on hidden-target levels.",
                "The one-candidate cap is already occupied by S; an unsupported second shrink artifact would violate the row's candidate budget without providing evidence of a target-level improvement."
            ],
            "S2_built": False,
            "S2_registered": False,
            "existing_S_candidate_changed": False,
            "upload_authorized": False,
            "upload_performed": False,
        },
        "provenance": {
            "audit_script_sha256": sha256_file(Path(__file__).resolve()),
            "preregistration_sha256": sha256_file(PREREG_PATH),
            "C2_feature_only_prediction_sha256": sha256_file(C2_PATH),
            "C2_recipe_gap_estimates_sha256": sha256_file(ESTIMATES_PATH),
            "C2_recipe_source_sha256": sha256_file(ESTIMATOR_SOURCE),
            "region_inputs": input_provenance,
        },
        "data_use": {
            "provided_data_only": True,
            "official_sample_score_placeholders_read": False,
            "labels_or_target_values_read": False,
            "removed_references_or_equivalents_read": False,
            "external_data_or_numerical_counts_read": False,
            "public_or_private_leaderboard_scores_or_scorecards_read": False,
            "C0_all_zero_candidate_read_or_used": False,
            "target_or_region_moment_fit": False,
            "candidate_or_registry_edits": False,
            "uploads": 0,
        },
        "limitations": [
            "Internal expected counts are deterministic recipe priors derived from provided population, land, urbanity, and Overture counts; they are not organizer reference counts or independent validation.",
            "Microsoft source share is Overture provenance, not proof that each record matches a reference footprint, and not an observation of Microsoft-reference coverage.",
            "The raw-zero facility scenarios assume Overture count zero implies USGS count zero; actual reference-definedness cannot be measured under the competition restriction.",
            "Without labels or permitted reference counts, no corrected component level is a hidden-target accuracy claim."
        ],
        "reproduction": {
            "command": "uv run --with-requirements requirements.txt python scripts/audit_shrink_h1b_rule_levels.py --workspace $COMPETITION_WORKSPACE --out reports/shrink-h1b-rule-level-audit.json",
            "determinism": "No random operations; report is sorted-key JSON with input hashes and no generation timestamp."
        }
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    report = run_audit(args.workspace, args.out)
    print(json.dumps({
        "official_tracts": report["scope"]["official_tracts"],
        "H1b_verdict": report["decision"]["H1b_verdict"],
        "S2_built": report["decision"]["S2_built"],
        "overall_component_mass": report["measure_before"]["C2_component_mass_overall"],
        "report": str(args.out),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
