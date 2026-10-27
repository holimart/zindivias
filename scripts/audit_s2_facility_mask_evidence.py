#!/usr/bin/env python3
"""Evidence audit and pre-registered S2b facility-mask counterfactual.

Uses C2's frozen feature-only expected facility counts, a stated Poisson
translation for presence probabilities, and provided ACS/Overture structure.
It does not access organizer references, labels, leaderboard data, or external
sources. S2 and its source candidate remain unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import estimate_gaps as c2_recipe  # noqa: E402
import audit_shrink_h1b_rule_levels as h1b  # noqa: E402
import build_shrink_s2_assumption_bound as s2build  # noqa: E402
from bias60.inputs import ProvidedInputs, load_config  # noqa: E402

REGIONS = h1b.REGIONS
FACILITIES = h1b.FACILITIES
PREREG_PATH = ROOT / "config/shrink-s2-facility-mask-evidence-preregistered.json"
H1B_PREREG_PATH = ROOT / "config/shrink-h1b-rule-level-preregistered.json"
S2_PREREG_PATH = ROOT / "config/shrink-s2-assumption-bound-preregistered.json"
S2_REPORT_PATH = ROOT / "reports/shrink-s2-assumption-bound-comparison.json"
REGISTRY_PATH = ROOT / "reports/candidates.json"
OUT_PATH = ROOT / "reports/s2-facility-mask-evidence.json"
CANDIDATE_ID = "S2b_c2_expected_facility_mask_v1"
CANDIDATE_REL = f"reports/candidates/{CANDIDATE_ID}.csv"
CANDIDATE_PATH = ROOT / CANDIDATE_REL
REPORT_SCHEMA = 1
PREREG_SHA256 = "28bf71510cc069de9b214f60a32f384512e9fe7b1dd9d1a59b3b94229ac4fa9c"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_counts(strata: dict[str, Any]) -> tuple[dict[str, float], float, str]:
    """C2 v0 estimate_one facility proxy denominators, scale=1."""
    urban, source = c2_recipe.urban_fraction(strata)
    pop = float(strata["pop_total"])
    if not math.isfinite(pop) or pop < 0.0:
        raise ValueError("C2 expected counts require finite nonnegative population")
    return {
        "fire": 0.15 + pop / 30000.0 + 0.45 * urban,
        "ems": 0.15 + pop / 40000.0 + 0.45 * urban,
        "schools": 0.20 + pop / 2500.0 + 0.20 * urban,
    }, float(urban), source


def poisson_presence(expected_count: float) -> tuple[float, float]:
    """Poisson bridge, explicitly a distributional assumption, not C2-native."""
    lam = float(expected_count)
    if not math.isfinite(lam) or lam < 0.0:
        raise ValueError("expected count must be finite and nonnegative")
    p_zero = math.exp(-lam)
    return 1.0 - p_zero, p_zero


def retain_facility_type(raw_overture_count: float, expected_count: float,
                         c2_defined: int, threshold: float = 0.5) -> bool:
    """S2b rule: zero raw count drops only if C2 expectation is strictly < .5."""
    raw = float(raw_overture_count)
    exp = float(expected_count)
    if not math.isfinite(raw) or raw < 0.0 or not math.isfinite(exp) or exp < 0.0:
        raise ValueError("facility count and expected count must be finite and nonnegative")
    if int(c2_defined) not in (0, 1):
        raise ValueError("C2 defined flag must be binary")
    if raw == 0.0:
        expected_defined = exp >= threshold
        if bool(c2_defined) != expected_defined:
            raise ValueError("zero-count C2 defined flag does not match its expected-count threshold")
        return expected_defined
    return bool(c2_defined)


def shared_boundary_neighbors(metric_polygons: Any, min_shared_boundary_m: float = 1e-6) -> list[list[int]]:
    """Return same-region tract neighbors sharing positive-length polygon boundary."""
    polygons = np.asarray(metric_polygons, dtype=object)
    tree = shapely.STRtree(polygons)
    pairs = tree.query(polygons, predicate="touches")
    neighbors = [set() for _ in polygons]
    if pairs.size:
        left, right = pairs
        chosen = left < right
        left, right = left[chosen], right[chosen]
        boundaries = shapely.boundary(polygons)
        lengths = shapely.length(shapely.intersection(boundaries[left], boundaries[right]))
        for i, j, length in zip(left.tolist(), right.tolist(), np.asarray(lengths).tolist()):
            if math.isfinite(float(length)) and float(length) > min_shared_boundary_m:
                neighbors[i].add(j)
                neighbors[j].add(i)
    return [sorted(row) for row in neighbors]


def numeric_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "minimum": None, "p25": None, "median": None, "p75": None, "p95": None, "maximum": None, "mean": None}
    a = np.asarray(values, dtype=np.float64)
    if not np.isfinite(a).all():
        raise ValueError("summary values must be finite")
    return {"n": len(a), "minimum": float(a.min()), "p25": float(np.quantile(a, .25)),
            "median": float(np.quantile(a, .5)), "p75": float(np.quantile(a, .75)),
            "p95": float(np.quantile(a, .95)), "maximum": float(a.max()), "mean": float(a.mean())}


def load_preregistered_inputs(workspace: Path, official_order: list[tuple[str, str]],
                              ids_by_region: dict[str, list[str]], prereg: dict[str, Any]):
    c2_data, template_order = s2build.read_csv_official(
        s2build.C2_PATH, set(g for g, _ in official_order),
        ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"], full_order=True)
    s_data, s_order = s2build.read_csv_official(
        s2build.S_PATH, set(g for g, _ in official_order), ["GEOID", "coverage_gap_score"], full_order=True)
    s2_data, s2_order = s2build.read_csv_official(
        s2build.CANDIDATE_PATH, set(g for g, _ in official_order),
        ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"], full_order=True)
    if not (s2_order == template_order == s_order) or len(set(template_order)) != 9794:
        raise ValueError("frozen S/S2/C2 platform-template GEOID orders differ")
    if not set(g for g, _ in official_order).issubset(set(template_order)) or len(set(template_order) - set(g for g, _ in official_order)) != 415:
        raise ValueError("frozen candidate template does not contain the exact 9,379 official IDs plus 415 format-only IDs")
    official_ids = set(g for g, _ in official_order)
    if any(set(data) != official_ids for data in (c2_data, s_data, s2_data)):
        raise ValueError("S/S2/C2 must exactly cover the official 9,379 IDs")
    estimates = h1b.load_csv_map(h1b.ESTIMATES_PATH, h1b.GAP_COLUMNS, official_ids)
    if set(estimates) != official_ids:
        raise ValueError("per-type C2 recipe gap estimates do not exactly cover official IDs")

    h1b_prereg = json.loads(H1B_PREREG_PATH.read_text(encoding="utf-8"))
    pconfig = json.loads(S2_PREREG_PATH.read_text(encoding="utf-8"))
    features: dict[str, dict[str, Any]] = {}
    strata: dict[str, dict[str, Any]] = {}
    input_hashes: dict[str, Any] = {}
    for region in REGIONS:
        fpath = workspace / "gap-estimator/features" / f"{region}-overture-tract-summary.parquet"
        spath = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        fp = h1b_prereg["pinned_inputs"]["provided_overture_tract_summaries"][region]["sha256"]
        stp = h1b_prereg["pinned_inputs"]["provided_strata"][region]["sha256"]
        if sha256_file(fpath) != fp or sha256_file(spath) != stp:
            raise ValueError(f"pinned feature/strata hash mismatch for {region}")
        allowed = set(ids_by_region[region])
        features.update(h1b.load_parquet_map(fpath, h1b.FEATURE_COLUMNS, allowed))
        strata.update(h1b.load_parquet_map(spath, h1b.STRATA_COLUMNS, allowed))
        input_hashes[region] = {
            "feature_summary_sha256": sha256_file(fpath), "feature_columns_read": h1b.FEATURE_COLUMNS,
            "strata_sha256": sha256_file(spath), "strata_columns_read": h1b.STRATA_COLUMNS,
            "official_rows": len(allowed),
        }
    if set(features) != official_ids or set(strata) != official_ids:
        raise ValueError("feature/strata coverage mismatch")
    return c2_data, s_data, s2_data, estimates, features, strata, template_order, input_hashes


def structure_by_region(workspace: Path, ids_by_region: dict[str, list[str]],
                        features: dict[str, dict[str, Any]], strata: dict[str, dict[str, Any]],
                        expected_by_id: dict[str, dict[str, float]],
                        urban_by_id: dict[str, float]):
    details: dict[str, dict[str, Any]] = {}
    for region in REGIONS:
        geoids = ids_by_region[region]
        inputs = ProvidedInputs(load_config(s2build.ROOT / "config/bias60-harness-preregistered.json"),
                                workspace, region, geoids)
        polygons = inputs.tract_geometries()
        projector = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
        metric = shapely.transform(polygons, projector.transform, interleaved=False)
        neighbor_ix = shared_boundary_neighbors(metric)
        local: dict[str, Any] = {"neighbor_counts": {}}
        # Each tract-type zero gets area/population/urbanity and observed same-type
        # neighboring Overture counts; geometry only creates within-region adjacency.
        for kind in FACILITIES:
            counts = np.asarray([float(features[g][f"overture_poi_{kind}"]) for g in geoids], dtype=np.float64)
            if not np.isfinite(counts).all() or np.any(counts < 0.0):
                raise ValueError(f"invalid provided Overture {kind} counts")
            expected = np.asarray([expected_by_id[g][kind] for g in geoids], dtype=np.float64)
            zero = counts == 0.0
            positive_neighbor_tracts = np.zeros(len(geoids), dtype=np.int64)
            positive_neighbor_counts = np.zeros(len(geoids), dtype=np.float64)
            neighbor_n = np.zeros(len(geoids), dtype=np.int64)
            for i, adjacent in enumerate(neighbor_ix):
                neighbor_n[i] = len(adjacent)
                positive = [j for j in adjacent if counts[j] > 0]
                positive_neighbor_tracts[i] = len(positive)
                positive_neighbor_counts[i] = float(sum(counts[j] for j in positive))
            area = np.asarray([float(strata[g]["ALAND"]) / 1_000_000.0 for g in geoids])
            pop = np.asarray([float(strata[g]["pop_total"]) for g in geoids])
            urban = np.asarray([urban_by_id[g] for g in geoids])
            if not (np.isfinite(area).all() and np.isfinite(pop).all() and np.isfinite(urban).all()):
                raise ValueError(f"nonfinite provided structural data for {region}/{kind}")
            local["neighbor_counts"][kind] = {
                "zero_count_tracts": int(zero.sum()),
                "zero_count_with_any_neighbor": int(np.sum(zero & (neighbor_n > 0))),
                "zero_count_with_positive_same_type_neighbor": int(np.sum(zero & (positive_neighbor_tracts > 0))),
                "zero_count_share_with_positive_same_type_neighbor": float(np.mean(positive_neighbor_tracts[zero] > 0)) if np.any(zero) else None,
                "zero_count_neighbor_tracts": numeric_summary(neighbor_n[zero].astype(float).tolist()),
                "zero_count_positive_neighbor_tracts": numeric_summary(positive_neighbor_tracts[zero].astype(float).tolist()),
                "zero_count_positive_neighbor_overture_counts_sum": numeric_summary(positive_neighbor_counts[zero].tolist()),
                "nonzero_count_neighbor_tracts": numeric_summary(neighbor_n[~zero].astype(float).tolist()),
                "nonzero_count_positive_neighbor_tracts": numeric_summary(positive_neighbor_tracts[~zero].astype(float).tolist()),
                "nonzero_count_positive_neighbor_overture_counts_sum": numeric_summary(positive_neighbor_counts[~zero].tolist()),
            }
            local.setdefault("structural_covariates", {})[kind] = {
                "zero_count": {"tract_area_km2": numeric_summary(area[zero].tolist()),
                               "population": numeric_summary(pop[zero].tolist()),
                               "urban_fraction": numeric_summary(urban[zero].tolist()),
                               "C2_expected_reference_count": numeric_summary(expected[zero].tolist()),
                               "Poisson_P_reference_ge_1": numeric_summary((1.0 - np.exp(-expected[zero])).tolist()),
                               "Poisson_P_reference_zero": numeric_summary(np.exp(-expected[zero]).tolist())},
                "nonzero_count": {"tract_area_km2": numeric_summary(area[~zero].tolist()),
                                  "population": numeric_summary(pop[~zero].tolist()),
                                  "urban_fraction": numeric_summary(urban[~zero].tolist()),
                                  "C2_expected_reference_count": numeric_summary(expected[~zero].tolist()),
                                  "Poisson_P_reference_ge_1": numeric_summary((1.0 - np.exp(-expected[~zero])).tolist()),
                                  "Poisson_P_reference_zero": numeric_summary(np.exp(-expected[~zero]).tolist())},
            }
        details[region] = local
    return details


def calc_s2b_row(geoid: str, c2row: dict[str, str], estimates: dict[str, str],
                 features: dict[str, Any], strata: dict[str, Any]) -> dict[str, Any]:
    counts, urban, urban_source = expected_counts(strata)
    expected_poisson = {kind: poisson_presence(lam) for kind, lam in counts.items()}
    c2_components = {name: s2build.to_float(c2row[f"{name}_gap"], f"C2 {name}_gap")
                      for name in ("transport", "building", "poi")}
    if any(not 0.0 <= value <= 1.0 for value in c2_components.values()):
        raise ValueError(f"C2 component outside [0,1] for {geoid}")
    transport_defined = s2build.binary_flag(estimates["transport_defined"], "transport_defined")
    building_defined = s2build.binary_flag(estimates["building_defined"], "building_defined")
    share = s2build.to_float(features.get("overture_building_microsoft_source_share"),
                              "overture_building_microsoft_source_share", allow_missing=True)
    if share is not None and not 0.0 <= share <= 1.0:
        raise ValueError("Microsoft-source share outside [0,1]")
    building = c2_components["building"] * (1.0 - share) if share is not None else c2_components["building"]
    retained: list[tuple[float, int]] = []
    masks: dict[str, Any] = {}
    for kind in FACILITIES:
        raw = float(features[f"overture_poi_{kind}"])
        defined = s2build.binary_flag(estimates[f"{kind}_defined"], f"{kind}_defined")
        gap = s2build.to_float(estimates[f"{kind}_gap"], f"{kind}_gap")
        if not 0.0 <= gap <= 1.0:
            raise ValueError(f"C2 per-type {kind} gap out of range")
        keep = retain_facility_type(raw, counts[kind], defined)
        retained.append((gap, int(keep)))
        masks[kind] = {"raw_overture_count": raw, "expected_reference_count": counts[kind],
                       "poisson_probability_reference_ge_1": expected_poisson[kind][0],
                       "poisson_probability_reference_zero": expected_poisson[kind][1],
                       "C2_type_defined": defined, "S2b_type_retained": keep,
                       "C2_per_type_gap": gap,
                       "drop_reason": "C2 expected count <0.5" if raw == 0.0 and not keep else None}
    cbp_gap = s2build.to_float(estimates["cbp_gap"], "cbp_gap")
    cbp_defined = s2build.binary_flag(estimates["cbp_defined"], "cbp_defined")
    poi, poi_defined = h1b.compose_poi(retained, cbp_gap, cbp_defined)
    components = {"transport": c2_components["transport"], "building": building, "poi": poi}
    flags = {"transport": transport_defined, "building": building_defined, "poi": int(poi_defined)}
    for name, value in list(components.items()):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"S2b {name} component outside [0,1]")
        if not flags[name]:
            components[name] = 0.0
    score = s2build.component_composite(components, flags)
    return {"GEOID": geoid, "components": components, "flags": flags, "score": score,
            "expected_counts": counts, "urban_fraction": urban, "urban_fraction_source": urban_source,
            "facility_mask": masks}


def update_registry(raw_registry: str, card: dict[str, Any]) -> str:
    registry = json.loads(raw_registry)
    cards = registry["candidate_set"]
    existing = [(index, item) for index, item in enumerate(cards) if item.get("candidate_id") == CANDIDATE_ID]
    if existing:
        if len(existing) != 1:
            raise ValueError("duplicate S2b registry cards")
        index, previous = existing[0]
        if (previous.get("artifact") != card.get("artifact") or
                previous.get("sha256") != card.get("sha256") or
                previous.get("preregistered_card_sha256") != card.get("preregistered_card_sha256") or
                previous.get("upload_authorized") is not False):
            raise ValueError("S2b registry candidate/preregistration identity differs from frozen artifact")
        if previous == card:
            return raw_registry
        cards[index] = card
        return json.dumps(registry, indent=2, ensure_ascii=False) + chr(10)
    cards.append(card)
    return json.dumps(registry, indent=2, ensure_ascii=False) + "\n"


def run(workspace: Path, report_path: Path = OUT_PATH) -> dict[str, Any]:
    prereg_hash = sha256_file(PREREG_PATH)
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    if prereg["registered_before_s2b_value_computation"] is not True or prereg_hash != PREREG_SHA256:
        raise ValueError("S2b preregistration status/hash mismatch")
    expected_hashes = prereg["frozen_inputs"]
    pinned_paths = {
        "S": s2build.S_PATH, "S2": s2build.CANDIDATE_PATH,
        "C2": s2build.C2_PATH, "gap_estimates": s2build.ESTIMATES_PATH,
        "C2_recipe": ROOT / "scripts/estimate_gaps.py",
        "S2_preregistration": S2_PREREG_PATH,
        "H1b_preregistration": H1B_PREREG_PATH,
        "prior_mask_endpoint_audit": ROOT / "reports/s2-poi-facility-mask-endpoints.json",
    }
    pinned_hashes = {}
    for key, path in pinned_paths.items():
        actual = sha256_file(path)
        pinned_hashes[key] = actual
        if actual != expected_hashes[key]["sha256"]:
            raise ValueError(f"frozen input SHA mismatch: {key}")
    prior_mask_report = json.loads((ROOT / "reports/s2-poi-facility-mask-endpoints.json").read_text(encoding="utf-8"))
    prior_mask_deltas = {region: prior_mask_report["per_region"][region]["delta_summary_vs_frozen_S2"]
                         for region in REGIONS}
    official_order, geoid_to_region, ids_by_region = h1b.read_official_ids(workspace)
    official_ids = set(geoid_to_region)
    h1b_prereg = json.loads(H1B_PREREG_PATH.read_text(encoding="utf-8"))
    for region in REGIONS:
        sample = workspace / "samples" / f"{region}-sample-submission.csv"
        if sha256_file(sample) != prereg["frozen_inputs"]["official_samples"][region]["sha256"]:
            raise ValueError(f"official sample checksum mismatch: {region}")
    c2_data, s_data, s2_data, estimates, features, strata, template_order, input_hashes = load_preregistered_inputs(
        workspace, official_order, ids_by_region, prereg)

    expected_by_id: dict[str, dict[str, float]] = {}
    urban_by_id: dict[str, float] = {}
    for geoid in official_ids:
        exp, urban, _source = expected_counts(strata[geoid])
        expected_by_id[geoid] = exp
        urban_by_id[geoid] = urban
    s2b_rows = {g: calc_s2b_row(g, c2_data[g], estimates[g], features[g], strata[g]) for g in official_ids}
    # Confirm the preregistered criterion is exactly C2's zero-count definedness boundary.
    zero_count_by_type = {kind: 0 for kind in FACILITIES}
    retained_zero_by_type = {kind: 0 for kind in FACILITIES}
    dropped_zero_by_type = {kind: 0 for kind in FACILITIES}
    for geoid in official_ids:
        for kind in FACILITIES:
            info = s2b_rows[geoid]["facility_mask"][kind]
            if info["raw_overture_count"] == 0.0:
                zero_count_by_type[kind] += 1
                retained_zero_by_type[kind] += int(info["S2b_type_retained"])
                dropped_zero_by_type[kind] += int(not info["S2b_type_retained"])
                if info["S2b_type_retained"] != (info["expected_reference_count"] >= 0.5 and info["C2_type_defined"] == 1):
                    raise AssertionError("S2b zero-count mask differs from preregistered rule")
    expected_zero_counts = {"ems": 7282, "fire": 5892, "schools": 1088}
    if zero_count_by_type != expected_zero_counts:
        raise ValueError(f"zero category count totals differ from prior audit: {zero_count_by_type}")

    # Structure diagnostics use only provided same-region tract polygons and inputs.
    structure = structure_by_region(workspace, ids_by_region, features, strata, expected_by_id, urban_by_id)

    per_zero_type: list[dict[str, Any]] = []
    for geoid, region in official_order:
        row = s2b_rows[geoid]
        for kind in FACILITIES:
            info = row["facility_mask"][kind]
            if info["raw_overture_count"] != 0.0:
                continue
            st = strata[geoid]
            p_ge1, p_zero = poisson_presence(info["expected_reference_count"])
            per_zero_type.append({
                "GEOID": geoid, "region": region, "facility_type": kind,
                "raw_overture_count": 0, "C2_expected_reference_count": info["expected_reference_count"],
                "Poisson_P_reference_count_ge_1": p_ge1, "Poisson_P_reference_count_zero": p_zero,
                "C2_per_type_gap": info["C2_per_type_gap"], "C2_per_type_defined": bool(info["C2_type_defined"]),
                "S2b_retained": info["S2b_type_retained"], "drop_reason": info["drop_reason"],
                "ALAND_km2": float(st["ALAND"]) / 1_000_000.0,
                "population": float(st["pop_total"]), "urban_fraction": row["urban_fraction"],
            })
    if {kind: sum(r["facility_type"] == kind for r in per_zero_type) for kind in FACILITIES} != expected_zero_counts:
        raise ValueError("per-zero-type evidence rows do not match required counts")

    by_region_type = {}
    for region in REGIONS:
        by_region_type[region] = {}
        ids = ids_by_region[region]
        for kind in FACILITIES:
            rows = [r for r in per_zero_type if r["region"] == region and r["facility_type"] == kind]
            lam = [float(r["C2_expected_reference_count"]) for r in rows]
            p_ge1 = [float(r["Poisson_P_reference_count_ge_1"]) for r in rows]
            p_zero = [float(r["Poisson_P_reference_count_zero"]) for r in rows]
            by_region_type[region][kind] = {
                "zero_count_tracts": len(rows),
                "retained_by_S2b": sum(bool(r["S2b_retained"]) for r in rows),
                "dropped_by_S2b": sum(not bool(r["S2b_retained"]) for r in rows),
                "C2_expected_reference_count": numeric_summary(lam),
                "Poisson_P_reference_count_ge_1": numeric_summary(p_ge1),
                "Poisson_P_reference_count_zero": numeric_summary(p_zero),
                "share_P_ge_1_at_least_half": float(np.mean(np.asarray(p_ge1) >= 0.5)) if rows else None,
                "share_C2_expected_count_ge_0_5": float(np.mean(np.asarray(lam) >= 0.5)) if rows else None,
            }

    models = {"S_crossmethod_reliability_shrink_v1": s_data,
              "S2_h1b_assumption_bound_v1": s2_data,
              "C2_component_estimator_v0": c2_data}
    side_by_side = {}
    candidate_means: dict[str, dict[str, float]] = {region: {} for region in REGIONS}
    for region in REGIONS:
        ids = ids_by_region[region]
        rows_s2b = [s2b_rows[g] for g in ids]
        means = {}
        for name, data in models.items():
            vals = [s2build.to_float(data[g]["coverage_gap_score"], f"{name} score") for g in ids]
            means[name] = {"mean_composite_level": float(np.mean(vals)),
                           "share_changed_vs_S2": float(np.mean([abs(vals[i] - s2build.to_float(s2_data[g]["coverage_gap_score"], "S2 score")) > 1e-12 for i, g in enumerate(ids)])),
                           "share_changed_vs_C2": float(np.mean([abs(vals[i] - s2build.to_float(c2_data[g]["coverage_gap_score"], "C2 score")) > 1e-12 for i, g in enumerate(ids)]))}
        s2b_scores = [r["score"] for r in rows_s2b]
        s2b_components = {name: s2build.summarize([r["components"][name] for r in rows_s2b],
                                                   [r["flags"][name] for r in rows_s2b])
                          for name in ("transport", "building", "poi")}
        means["S2b_c2_expected_facility_mask_v1"] = {
            "mean_composite_level": float(np.mean(s2b_scores)),
            "component_means": s2b_components,
            "share_changed_vs_S2": float(np.mean([abs(s2b_rows[g]["score"] - s2build.to_float(s2_data[g]["coverage_gap_score"], "S2 score")) > 1e-12 for g in ids])),
            "share_changed_vs_C2": float(np.mean([abs(s2b_rows[g]["score"] - s2build.to_float(c2_data[g]["coverage_gap_score"], "C2 score")) > 1e-12 for g in ids])),
        }
        for name in models:
            means[name]["component_means"] = None if name.startswith("S_") else {
                comp: s2build.summarize([s2build.to_float(models[name][g][f"{comp}_gap"], f"{name} {comp}") for g in ids],
                                        [s2build.binary_flag(estimates[g][f"{comp}_defined"], f"{comp} defined") for g in ids])
                for comp in ("transport", "building", "poi")}
        candidate_means[region] = {name: info["mean_composite_level"] for name, info in means.items()}
        side_by_side[region] = {"official_tracts": len(ids), "models": means,
                                "facility_zero_masks": {kind: {
                                    "zero_count": sum(float(features[g][f"overture_poi_{kind}"]) == 0.0 for g in ids),
                                    "retained_by_S2b": sum(float(features[g][f"overture_poi_{kind}"]) == 0.0 and s2b_rows[g]["facility_mask"][kind]["S2b_type_retained"] for g in ids),
                                    "dropped_by_S2b": sum(float(features[g][f"overture_poi_{kind}"]) == 0.0 and not s2b_rows[g]["facility_mask"][kind]["S2b_type_retained"] for g in ids)} for kind in FACILITIES}}

    # Create distinct full-template S2b artifact; only GEOID/order are read for extra rows.
    candidate_path = CANDIDATE_PATH
    with candidate_path.open("w", newline="", encoding="utf-8") as stream:
        import csv
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"])
        for geoid in template_order:
            if geoid in official_ids:
                row = s2b_rows[geoid]
                writer.writerow([geoid, *(format(row["components"][name], ".17g") for name in ("transport", "building", "poi")), format(row["score"], ".17g")])
            else:
                writer.writerow([geoid, "0", "0", "0", "0"])
    candidate_sha = sha256_file(candidate_path)
    registered_prereg_sha = prereg_hash
    s2b_card = {
        "candidate_id": CANDIDATE_ID, "name": "S2b C2 expected-count facility mask scenario",
        "artifact": CANDIDATE_REL, "sha256": candidate_sha, "rows": len(template_order),
        "columns": ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"],
        "pre_registered": True, "preregistered_card_path": "config/shrink-s2-facility-mask-evidence-preregistered.json",
        "preregistered_card_sha256": registered_prereg_sha,
        "build_script_sha256": sha256_file(Path(__file__).resolve()), "purpose": "improvement",
        "description": "Distinct S2 sensitivity candidate using the preregistered C2 expected-count threshold for zero-Overture facility types; not accuracy evidence.",
        "predicted_score": None,
        "predicted_score_scope": "Label-free output values only; organizer-target score and RMSE are not identifiable.",
        "predicted_score_basis": "Frozen C2 feature-only per-type gaps and C2 expected-count mask; no fitted coefficients or target information.",
        "target_RMSE_forecast": None,
        "target_RMSE_forecast_limitation": "No permitted USGS reference counts or labels; no accuracy inference.",
        "assumptions": ["C2 expected counts are feature-derived proxy levels, not observed USGS counts.",
                        "Poisson presence probabilities are an explicit distributional bridge, not empirical/reference probabilities.",
                        "415 eastern-WA template-only rows are fixed zero fills and excluded from comparisons."],
        "parameters": {"fitted_coefficients": [], "S_style_shrink_applied": False,
                       "facility_zero_mask": "drop iff C2 expected count <0.5; keep C2 per-type gap otherwise"},
        "eastern_wa_fill": {"region": "eastern-wa", "rows": len(template_order)-len(official_ids), "value": 0.0, "scored_target_claim": False},
        "column_checks": {"unique_columns": True, "all_rows_have_columns": True, "finite_bounded_scores": True},
        "upload_authorized": False, "registration_status": "frozen_no_upload",
    }
    raw_registry = REGISTRY_PATH.read_text(encoding="utf-8")
    updated_registry = update_registry(raw_registry, s2b_card)
    json.loads(updated_registry)
    REGISTRY_PATH.write_text(updated_registry, encoding="utf-8")

    report = {
        "schema_version": REPORT_SCHEMA,
        "analysis_id": "S2_FACILITY_MASK_EVIDENCE_AND_S2B_V1",
        "preregistration": {"path": str(PREREG_PATH.relative_to(ROOT)), "sha256": prereg_hash,
                            "registered_before_s2b_value_computation": True,
                            "preregistration_commit": "baca0ac3d0168d45ab0ec0dc8b767f82bd5bf692"},
        "frozen_mask_measure_before": {
            "zero_count_tracts_by_type": zero_count_by_type,
            "S2_rule": "drop type iff raw Overture count=0; no independent reference evidence",
            "retaining_all_zero_components_delta_vs_frozen_S2_by_region": prior_mask_deltas,
            "materiality_source": "reports/s2-poi-facility-mask-endpoints.json",
            "source_sha256": pinned_hashes["prior_mask_endpoint_audit"],
        },
        "C2_expected_count_model": {
            "source_code": "scripts/estimate_gaps.py::estimate_one scale=1.0",
            "formulas": {"fire": "0.15 + pop_total/30000 + 0.45*urban_fraction",
                         "ems": "0.15 + pop_total/40000 + 0.45*urban_fraction",
                         "schools": "0.20 + pop_total/2500 + 0.20*urban_fraction"},
            "probability_assumption": "Poisson count with lambda equal to C2 expected count; P(N>=1)=1-exp(-lambda), P(N=0)=exp(-lambda). This is not native C2 probability or empirical USGS probability.",
            "mask_threshold": 0.5,
            "zero_count_retained_by_type": retained_zero_by_type,
            "zero_count_dropped_by_type": dropped_zero_by_type,
            "zero_type_counts_verified": zero_count_by_type == expected_zero_counts,
            "by_type_overall": {kind: {
                "zero_count_tracts": sum(r["facility_type"] == kind for r in per_zero_type),
                "retained_by_S2b": sum(r["facility_type"] == kind and r["S2b_retained"] for r in per_zero_type),
                "dropped_by_S2b": sum(r["facility_type"] == kind and not r["S2b_retained"] for r in per_zero_type),
                "C2_expected_reference_count": numeric_summary([r["C2_expected_reference_count"] for r in per_zero_type if r["facility_type"] == kind]),
                "Poisson_P_reference_count_ge_1": numeric_summary([r["Poisson_P_reference_count_ge_1"] for r in per_zero_type if r["facility_type"] == kind]),
                "Poisson_P_reference_count_zero": numeric_summary([r["Poisson_P_reference_count_zero"] for r in per_zero_type if r["facility_type"] == kind]),
                "share_C2_expected_count_ge_0_5": float(np.mean([r["C2_expected_reference_count"] >= 0.5 for r in per_zero_type if r["facility_type"] == kind])),
            } for kind in FACILITIES},
            "zero_tract_type_rows": per_zero_type,
        },
        "structure_only_plausibility": {
            "source": "Provided Overture tract summaries, provided ACS strata, and provided ACS tract polygons only.",
            "adjacency_rule": "same-region polygon pairs sharing positive boundary length in EPSG:5070; point-only touches excluded",
            "interpretation_limit": "These covariates and neighbor Overture counts can indicate sparse/dense structural context but cannot observe or validate a USGS zero.",
            "by_region": structure,
        },
        "scope": {
            "official_geoids": len(official_order), "official_geoids_unique": len(official_ids),
            "official_id_region_order_sha256": hashlib.sha256("\\n".join(f"{g},{r}" for g, r in official_order).encode("utf-8")).hexdigest(),
            "official_region_counts": {r: len(ids_by_region[r]) for r in REGIONS},
            "template_rows": len(template_order), "template_unique_geoids": len(set(template_order)),
            "template_only_format_rows": len(set(template_order) - official_ids),
            "S_S2_C2_official_coverage_exact": True,
            "candidate_S2b_official_coverage_exact": True,
            "sample_score_placeholders_read": False,
        },
        "candidate": {"candidate_id": CANDIDATE_ID, "artifact": CANDIDATE_REL,
                      "sha256": candidate_sha, "rows": len(template_order),
                      "official_scored_rows": len(official_ids),
                      "formatting_only_rows": len(template_order)-len(official_ids),
                      "upload_authorized": False, "upload_performed": False,
                      "registry_sha256": sha256_file(REGISTRY_PATH)},
        "S_vs_S2_vs_S2b_vs_C2_by_region": side_by_side,
        "recommendation": {
            "recommended_for_steward_consideration": CANDIDATE_ID,
            "reason": "S2b replaces the unverified rule that every Overture-zero type is a USGS zero with the preregistered C2 expected-count boundary: only zero-count types with C2 expected count <0.5 are dropped; the C2 recipe itself marks zero-count types defined at expected count >=0.5.",
            "caveat": "C2 expected counts and Poisson probabilities are assumptions from provided features, not measured USGS counts. This recommendation is internal model coherence only, not target-accuracy evidence. Steward makes final choice; no upload is authorized here.",
            "S2b_regional_means": candidate_means,
        },
        "provenance": {"frozen_input_sha256": pinned_hashes,
                       "feature_and_strata_sha256_by_region": input_hashes,
                       "C2_template_order_rows": len(template_order),
                       "C2_template_unique_ids": len(set(template_order)),
                       "analysis_script_sha256": sha256_file(Path(__file__).resolve()),
                       "S2_build_script_sha256": sha256_file(Path(s2build.__file__).resolve()),
                       "registry_entry_upload_authorized": False},
        "safety": {"provided_data_only": True, "sample_score_placeholders_read": False,
                   "labels_read": False, "removed_or_equivalent_references_read": False,
                   "external_data_read": False, "leaderboard_or_scorecards_accessed": False,
                   "C0_read_or_used": False, "S_modified": False, "S2_modified": False,
                   "upload_authorized": False, "uploads": 0,
                   "target_accuracy_identifiable": False},
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    report = run(args.workspace, args.out)
    print(json.dumps({"candidate_sha256": report["candidate"]["sha256"],
                      "zero_count_by_type": report["C2_expected_count_model"]["by_type_overall"],
                      "recommended": report["recommendation"]["recommended_for_steward_consideration"],
                      "registry_upload_authorized": False, "uploads": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
