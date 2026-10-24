#!/usr/bin/env python3
"""Preregistered fixed-prior uncertainty envelope for the frozen model-only v2 estimator.

Sensitivity screen only: it promotes nothing, ranks nothing, selects no winner,
computes no accuracy estimate, reads no labels, no removed reference layer or
equivalent, no sample-submission score value, no leaderboard-derived constant,
and performs no upload.

The frozen v2 composite averages three top-level components. Two carry
mechanism-derived prior scalars: the fixed building residual prior and the
establishments CBP rate; and each fire/EMS/schools facility gap is a fixed
logistic presence prior times a count-based expected-miss factor. This audit
recomputes the per-tract composite under a small preregistered grid of +-10%
multiplicative perturbations of those priors only, and reports the per-tract
delta distribution with its quantiles, the region / urban-rural / tribal stratum
mean shifts, and the per-tract score envelope. Every input value and the
perturbation rule are declared and verified before any variant output is
computed, and every preregistered variant is reported with identical fields.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from audit_v2_component_masks import (  # noqa: E402  (reuse of the frozen rule implementations)
    EXPECTED_ROWS,
    REGIONS,
    clip01,
    composite_mean,
    official_scored_ids,
    parse_flag,
    parse_float,
    presence_probability,
    read_table,
    sha256_file,
    urbanity,
)

CONFIG_PATH = ROOT / "config" / "v2-prior-envelope-preregistered.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
MASK_CONFIG_PATH = ROOT / "config" / "v2-component-mask-audit-preregistered.json"
GATES = CONFIG["gates"]
PRIORS = CONFIG["central_priors"]["facility_presence_prior"]
COEFFICIENTS = ("intercept", "log_density", "urban_fraction")
FAMILIES = ("fire", "ems", "schools")
QUANTILE_LEVELS = tuple(CONFIG["measurement"]["quantiles_reported"])
BASELINE = "baseline"


# ------------------------------------------------------------------ deterministic helpers
def quantiles(values: list[float]) -> dict[str, float | None]:
    """Nearest-rank quantiles of a plain list (deterministic, no numpy dependency)."""
    if not values:
        return {level: None for level in QUANTILE_LEVELS}
    ordered = sorted(values)
    total = len(ordered)
    output: dict[str, float | None] = {}
    for level in QUANTILE_LEVELS:
        if level == "max":
            output[level] = ordered[-1]
        elif level.startswith("p"):
            percentile = float(level[1:])
            if not 0.0 < percentile <= 100.0:
                raise ValueError(f"quantile label {level!r} is not a 1-100 percentile")
            rank = max(1, math.ceil(percentile / 100.0 * total))
            output[level] = ordered[min(total - 1, rank - 1)]
        else:  # pragma: no cover - guarded by the config
            raise ValueError(f"unsupported quantile label {level!r}")
    return output


def mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def probability_with_overrides(family: str, strata: dict[str, float],
                              overrides: dict[str, float]) -> float:
    """Frozen presence prior with optional multiplicative coefficient overrides."""
    coefficients = dict(PRIORS[family])
    coefficients.update(overrides)
    population = float(strata["pop_total"])
    land_area = float(strata["ALAND"])
    if population < 0 or land_area < 0:
        raise ValueError(f"negative pop_total or ALAND ({population}, {land_area})")
    low, high = CONFIG["perturbation_rule"]["logit_clamp"]
    area_km2 = max(land_area / 1_000_000.0, 0.01)
    density_index = math.log1p((population / area_km2) / 100.0)
    logit = (float(coefficients["intercept"])
             + float(coefficients["log_density"]) * density_index
             + float(coefficients["urban_fraction"]) * urbanity(strata))
    return 1.0 / (1.0 + math.exp(-max(low, min(high, logit))))


def parse_variant(variant_id: str) -> dict[str, Any]:
    """Translate one preregistered variant id into a perturbation specification."""
    if variant_id == BASELINE:
        return {"id": variant_id, "kind": BASELINE, "target": "none", "factor": 1.0,
                "family": None, "coefficient": None}
    match = re.fullmatch(r"(.+)\.x(\d+\.\d{2})", variant_id)
    if match is None:
        raise ValueError(f"variant {variant_id!r} must look like <target>.x0.90 or <target>.x1.10")
    target, factor = match.group(1), float(match.group(2))
    if sorted(CONFIG["perturbation_rule"]["factors"]) != [0.9, 1.1] or factor not in (0.9, 1.1):
        raise ValueError(f"variant {variant_id!r} uses factor {factor} outside the registered grid")
    if target == "building_gap":
        return {"id": variant_id, "kind": "building", "target": "building_gap", "factor": factor,
                "family": None, "coefficient": None}
    if target == "establishments_cbp_k":
        return {"id": variant_id, "kind": "establishments", "target": "establishments_cbp_k",
                "factor": factor, "family": None, "coefficient": None}
    if target == "all_priors":
        return {"id": variant_id, "kind": "all", "target": "all_priors", "factor": factor,
                "family": None, "coefficient": None}
    if target.startswith("presence_"):
        remainder = target[len("presence_"):].split(".")
        family = remainder[0]
        if family not in FAMILIES:
            raise ValueError(f"variant {variant_id!r} names unknown family {family!r}")
        if len(remainder) == 1:
            return {"id": variant_id, "kind": "presence_scale", "target": target, "factor": factor,
                    "family": family, "coefficient": None}
        coefficient = remainder[1]
        if len(remainder) != 2 or coefficient not in COEFFICIENTS:
            raise ValueError(f"variant {variant_id!r} names unknown coefficient {coefficient!r}")
        return {"id": variant_id, "kind": "presence_coefficient", "target": target,
                "factor": factor, "family": family, "coefficient": coefficient}
    raise ValueError(f"variant {variant_id!r} names unknown target {target!r}")


def tribal_flag(value: Any) -> bool | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number not in (0.0, 1.0):
        raise ValueError(f"tribal_any outside boolean domain: {value!r}")
    return bool(number)


def load_strata(workspace: Path, ids: list[str]) -> dict[str, dict[str, float]]:
    import pyarrow.parquet as pq

    template = CONFIG["frozen_inputs"]["provided_strata"]["path_template"]
    columns = list(CONFIG["frozen_inputs"]["provided_strata"]["columns_read"])
    strata: dict[str, dict[str, float]] = {}
    for region in REGIONS:
        path = workspace / template.format(region=region)
        if not path.is_file():
            raise FileNotFoundError(f"missing provided strata table {path}")
        for row in pq.ParquetFile(path).read(columns=columns).to_pylist():
            geoid = str(row["GEOID"])
            if geoid in strata:
                raise ValueError(f"duplicate strata GEOID {geoid} in {path}")
            strata[geoid] = {key: (None if row[key] is None else float(row[key]))
                             for key in columns if key != "GEOID"}
    missing = [geoid for geoid in ids if geoid not in strata]
    if missing:
        raise ValueError(f"{len(missing)} official GEOIDs absent from provided strata, e.g. {missing[:3]}")
    return strata


def verify_frozen_shas(workspace: Path) -> dict[str, Any]:
    """Fail closed when any declared input differs from its preregistered SHA-256."""
    results: dict[str, Any] = {}
    for name in ("transport_component", "building_component", "poi_component"):
        declared = CONFIG["frozen_inputs"][name]
        path = ROOT / declared["path"]
        actual = sha256_file(path)
        if actual != declared["sha256"]:
            raise ValueError(f"frozen artifact changed: {declared['path']} {actual} != {declared['sha256']}")
        results[declared["path"]] = actual
    template = CONFIG["frozen_inputs"]["official_samples"]["path_template"]
    for region, digest in CONFIG["frozen_inputs"]["official_samples"]["sha256"].items():
        path = workspace / template.format(region=region)
        actual = sha256_file(path)
        if actual != digest:
            raise ValueError(f"official sample changed: {path} {actual} != {digest}")
        results[template.format(region=region)] = actual
    results[str(MASK_CONFIG_PATH.relative_to(ROOT))] = sha256_file(MASK_CONFIG_PATH)
    results[str(CONFIG_PATH.relative_to(ROOT))] = sha256_file(CONFIG_PATH)
    return results


# ------------------------------------------------------------------ tract loading
def load_tracts(workspace: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pairs = official_scored_ids(workspace)
    ids = [geoid for geoid, _ in pairs]
    region_of = dict(pairs)
    strata = load_strata(workspace, ids)

    transport_cfg = CONFIG["frozen_inputs"]["transport_component"]
    building_cfg = CONFIG["frozen_inputs"]["building_component"]
    poi_cfg = CONFIG["frozen_inputs"]["poi_component"]
    transport = read_table(ROOT / transport_cfg["path"],
                          ["GEOID", transport_cfg["gap_column"], transport_cfg["defined_column"]])
    building = read_table(ROOT / building_cfg["path"],
                         ["GEOID", building_cfg["gap_column"], building_cfg["defined_column"],
                          *building_cfg["input_columns"]])
    poi = read_table(ROOT / poi_cfg["path"],
                    ["GEOID", poi_cfg["gap_column"], poi_cfg["defined_column"],
                     poi_cfg["facility_half_column"], poi_cfg["establishments_half_column"],
                     *poi_cfg["family_gap_columns"], *poi_cfg["family_count_columns"],
                     *poi_cfg["input_columns"]])
    coverage = {name: table.summary(ids)
                for name, table in (("transport", transport), ("building", building), ("poi", poi))}
    for name, facts in coverage.items():
        if facts["missing_official_ids"] or facts["extra_non_official_ids"] or facts["duplicate_ids"]:
            raise ValueError(f"{name} artifact ID coverage mismatch: {facts}")

    cbp_k = float(CONFIG["central_priors"]["establishments_cbp_k"])
    tracts: list[dict[str, Any]] = []
    verification = {"zero_count_rows_checked": 0, "max_abs_gap_minus_presence": 0.0,
                    "geoid_of_max": None}
    for geoid in ids:
        tract_strata = strata[geoid]
        t_row, b_row, p_row = transport.rows[geoid], building.rows[geoid], poi.rows[geoid]
        t_gap = parse_float(t_row[transport_cfg["gap_column"]], "transport gap", geoid)
        t_flag = parse_flag(t_row[transport_cfg["defined_column"]], "transport defined", geoid)
        b_gap = parse_float(b_row[building_cfg["gap_column"]], "building gap", geoid)
        b_flag = parse_flag(b_row[building_cfg["defined_column"]], "building defined", geoid)
        poi_flag = parse_flag(p_row[poi_cfg["defined_column"]], "poi defined", geoid)
        poi_gap = parse_float(p_row[poi_cfg["gap_column"]], "poi gap", geoid)
        est_gap = parse_float(p_row[poi_cfg["establishments_half_column"]],
                             "establishments half gap", geoid)
        if not 0.0 <= est_gap <= 1.0 or not 0.0 <= poi_gap <= 1.0:
            raise ValueError(f"POI half or POI component outside [0,1] at {geoid}")
        family_gaps: dict[str, float] = {}
        central_probability: dict[str, float] = {}
        for family in FAMILIES:
            gap = parse_float(p_row[f"v2_raw_{family}_gap"], f"v2_raw_{family}_gap", geoid)
            if not 0.0 <= gap <= 1.0:
                raise ValueError(f"family gap for {family} outside [0,1] at {geoid}: {gap}")
            probability = presence_probability(family, tract_strata)
            if probability != probability_with_overrides(family, tract_strata, {}):
                raise AssertionError(f"presence prior reproduction differs at {geoid}/{family}")
            if probability <= 0.0:
                raise ValueError(f"presence prior is not positive at {geoid}/{family}")
            family_gaps[family] = gap
            central_probability[family] = probability
            if (parse_float(p_row[f"raw_{family}_count"], f"raw_{family}_count", geoid) == 0.0
                    and parse_float(p_row["unclassified_category_count"],
                                    "unclassified_category_count", geoid) == 0.0):
                residual = abs(gap - probability)
                verification["zero_count_rows_checked"] += 1
                if residual > verification["max_abs_gap_minus_presence"]:
                    verification["max_abs_gap_minus_presence"] = residual
                    verification["geoid_of_max"] = geoid
        pop_total = float(tract_strata["pop_total"])
        poi_count = parse_float(p_row["all_overture_poi_count"], "all_overture_poi_count", geoid)
        est_flag = int(pop_total > 0 or poi_count > 0)
        urban = urbanity(tract_strata)
        central_score, defined_count = composite_mean([(t_gap, t_flag), (b_gap, b_flag),
                                                      (poi_gap, poi_flag)])
        tracts.append({
            "geoid": geoid,
            "region": region_of[geoid],
            "urban": bool(urban >= 0.5),
            "pct_urban_missing": tract_strata.get("pct_urban") is None,
            "tribal": tribal_flag(tract_strata.get("tribal_any")),
            "strata": tract_strata,
            "transport_gap": t_gap, "transport_flag": t_flag,
            "building_gap": b_gap, "building_flag": b_flag,
            "family_gaps": family_gaps,
            "establishments_gap": est_gap, "establishments_flag": est_flag,
            "poi_flag": poi_flag, "poi_count": poi_count, "pop_total": pop_total,
            "central_probability": central_probability,
            "central_score": central_score if defined_count else 0.0,
            "defined_count": defined_count,
            "defined_components": t_flag + b_flag + poi_flag,
            "frozen_flags": (t_flag, b_flag, poi_flag),
        })
    if verification["zero_count_rows_checked"] == 0:
        raise AssertionError("no zero-count tracts available to verify the prior transfer rule")
    facts = {
        "official_scored_geoids": len(ids),
        "unique_official_geoids": len({row["geoid"] for row in tracts}),
        "region_rows": {region: sum(1 for row in tracts if row["region"] == region)
                        for region in REGIONS},
        "expected_region_rows": dict(EXPECTED_ROWS),
        "id_coverage_per_artifact": coverage,
        "pct_urban_missing_fallback_rows": sum(1 for row in tracts if row["pct_urban_missing"]),
        "urban_rows": sum(1 for row in tracts if row["urban"]),
        "rural_rows": sum(1 for row in tracts if not row["urban"]),
        "tribal_true_rows": sum(1 for row in tracts if row["tribal"] is True),
        "tribal_false_rows": sum(1 for row in tracts if row["tribal"] is False),
        "tribal_unknown_rows": sum(1 for row in tracts if row["tribal"] is None),
        "zero_defined_rows": sum(1 for row in tracts if row["defined_count"] == 0),
        "establishments_half_defined_rows": sum(1 for row in tracts if row["establishments_flag"]),
        "presence_prior_transfer_verification": {
            "rule": CONFIG["perturbation_rule"]["presence_prior_reproduction_check"],
            **verification,
        },
        "cbp_k_used": cbp_k,
    }
    return tracts, facts


# ------------------------------------------------------------------ variant evaluation
def perturbations(tract: dict[str, Any], spec: dict[str, Any],
                 probabilities: dict[tuple[str, str, float], dict[str, float]]) -> tuple[float, float, dict[str, float]]:
    """Return (building factor, cbp_k factor, per-family gap scale) for one variant."""
    kind, factor = spec["kind"], spec["factor"]
    building_factor, k_factor = 1.0, 1.0
    scales = {family: 1.0 for family in FAMILIES}
    if kind == "building":
        building_factor = factor
    elif kind == "establishments":
        k_factor = factor
    elif kind == "all":
        building_factor, k_factor = factor, factor
        scales = {family: factor for family in FAMILIES}
    elif kind == "presence_scale":
        base = tract["central_probability"][spec["family"]]
        scales[spec["family"]] = clip01(factor * base) / base
    elif kind == "presence_coefficient":
        base = tract["central_probability"][spec["family"]]
        perturbed = probabilities[(spec["family"], spec["coefficient"], factor)][tract["geoid"]]
        scales[spec["family"]] = clip01(perturbed) / base
    elif kind != BASELINE:  # pragma: no cover - guarded by parse_variant
        raise ValueError(f"unknown variant kind {kind!r}")
    return building_factor, k_factor, scales


def variant_score(tract: dict[str, Any], building_factor: float, k_factor: float,
                 scales: dict[str, float]) -> tuple[float, int]:
    """Recompute the frozen composite with perturbed priors, keeping every flag frozen."""
    cbp_k = float(CONFIG["central_priors"]["establishments_cbp_k"])
    building_gap = clip01(tract["building_gap"] * building_factor) if tract["building_flag"] else 0.0
    if tract["establishments_flag"]:
        if k_factor != 1.0 and tract["pop_total"] > 0.0:
            establishments_gap = clip01(1.0 - tract["poi_count"] / (cbp_k * k_factor * tract["pop_total"]))
        else:
            establishments_gap = tract["establishments_gap"]
    else:
        establishments_gap = 0.0
    family_values = [tract["family_gaps"][family] * scales[family] for family in FAMILIES]
    for value in family_values:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"perturbed family gap {value} outside [0,1] at {tract['geoid']}")
    facility_gap = sum(family_values) / len(family_values)
    halves = [facility_gap]
    if tract["establishments_flag"]:
        halves.append(establishments_gap)
    poi_gap = sum(halves) / len(halves)
    components = [(tract["transport_gap"], tract["transport_flag"]),
                  (building_gap, tract["building_flag"]),
                  (poi_gap, tract["poi_flag"])]
    score, _ = composite_mean(components)
    return score, sum(flag for _, flag in components)


def strata_keys(tract: dict[str, Any]) -> dict[str, str]:
    tribal = {True: "tribal_any_true", False: "tribal_any_false",
              None: "tribal_any_unknown"}[tract["tribal"]]
    urban = "urban" if tract["urban"] else "rural"
    return {
        "region": f"region:{tract['region']}",
        "urban_rural": f"urban_rural:{urban}",
        "tribal": f"tribal:{tribal}",
        "tribal_within_urban_rural": f"tribal_within_urban_rural:{tribal}:{urban}",
    }


def precompute_probabilities(tracts: list[dict[str, Any]],
                            variants: list[dict[str, Any]]) -> dict[tuple[str, str, float], dict[str, float]]:
    probabilities: dict[tuple[str, str, float], dict[str, float]] = {}
    for spec in variants:
        if spec["kind"] != "presence_coefficient":
            continue
        key = (spec["family"], spec["coefficient"], spec["factor"])
        if key in probabilities:
            continue
        overrides = {spec["coefficient"]: float(PRIORS[spec["family"]][spec["coefficient"]]) * spec["factor"]}
        probabilities[key] = {tract["geoid"]: probability_with_overrides(
            spec["family"], tract["strata"], overrides) for tract in tracts}
    return probabilities


def evaluate(tracts: list[dict[str, Any]], variant_ids: list[str]) -> dict[str, Any]:
    specs = [parse_variant(name) for name in variant_ids]
    if specs[0]["kind"] != BASELINE:
        raise ValueError("the first preregistered variant must be the baseline")
    probabilities = precompute_probabilities(tracts, specs)
    central = {tract["geoid"]: tract["central_score"] for tract in tracts}
    lower = dict(central)
    upper = dict(central)
    variant_rows: list[dict[str, Any]] = []
    for spec in specs:
        abs_deltas: list[float] = []
        signed: dict[str, list[float]] = defaultdict(list)
        absolute: dict[str, list[float]] = defaultdict(list)
        scores: list[float] = []
        nonfinite = out_of_range = flag_mismatch = zero_defined = clipped = 0
        worst = 0.0
        worst_geoid: str | None = None
        for tract in tracts:
            building_factor, k_factor, scales = perturbations(tract, spec, probabilities)
            if building_factor != 1.0 and tract["building_gap"] * building_factor > 1.0:
                clipped += 1
            score, defined = variant_score(tract, building_factor, k_factor, scales)
            if defined != tract["defined_components"]:
                flag_mismatch += 1
            if defined == 0:
                zero_defined += 1
            if not math.isfinite(score):
                nonfinite += 1
            elif not 0.0 <= score <= 1.0:
                out_of_range += 1
            scores.append(score)
            delta = score - central[tract["geoid"]]
            magnitude = abs(delta)
            abs_deltas.append(magnitude)
            if magnitude > worst:
                worst, worst_geoid = magnitude, tract["geoid"]
            for dimension, group in strata_keys(tract).items():
                signed[f"{dimension}|{group}"].append(delta)
                absolute[f"{dimension}|{group}"].append(magnitude)
            geoid = tract["geoid"]
            lower[geoid] = min(lower[geoid], score)
            upper[geoid] = max(upper[geoid], score)
        variant_rows.append({
            "variant": spec["id"],
            "perturbed_prior": spec["target"],
            "factor": spec["factor"],
            "family": spec["family"],
            "coefficient": spec["coefficient"],
            "rows": len(tracts),
            "nonfinite_scores": nonfinite,
            "scores_outside_zero_one": out_of_range,
            "definedness_flag_mismatches": flag_mismatch,
            "zero_defined_rows": zero_defined,
            "prior_clip_events": clipped,
            "max_abs_per_tract_delta": worst,
            "geoid_of_max_abs_delta": worst_geoid,
            "mean_abs_per_tract_delta": mean(abs_deltas),
            "mean_signed_per_tract_delta": mean([score - central[tract["geoid"]]
                                                 for score, tract in zip(scores, tracts)]),
            "delta_quantiles": quantiles(abs_deltas),
            "score_mean": mean(scores),
            "score_min": min(scores) if scores else None,
            "score_max": max(scores) if scores else None,
            "stratum_mean_delta": {
                key: {"tracts": len(values),
                      "mean_signed_delta": mean(values),
                      "mean_abs_delta": mean(absolute[key]),
                      "max_abs_per_tract_delta": max(absolute[key]) if absolute[key] else None}
                for key, values in sorted(signed.items())},
        })
    widths = [(upper[geoid] - lower[geoid], geoid) for geoid in central]
    widest_width, widest_geoid = max(widths) if widths else (None, None)
    strata_max: dict[str, dict[str, Any]] = {}
    for row in variant_rows:
        for key, facts in row["stratum_mean_delta"].items():
            value = abs(facts["mean_signed_delta"] or 0.0)
            current = strata_max.get(key)
            if current is None or value > current["max_abs_mean_delta"]:
                strata_max[key] = {"max_abs_mean_delta": value, "variant": row["variant"]}
    return {
        "variants": variant_rows,
        "envelope": {
            "variants_included": len(variant_rows),
            "per_tract_envelope_width_quantiles": quantiles([width for width, _ in widths]),
            "mean_per_tract_envelope_width": mean([width for width, _ in widths]),
            "widest_tract": {"geoid": widest_geoid, "width": widest_width,
                             "lower": lower.get(widest_geoid), "upper": upper.get(widest_geoid)},
            "global_score_lower": min(lower.values()) if lower else None,
            "global_score_upper": max(upper.values()) if upper else None,
            "stratum_max_abs_mean_delta": dict(sorted(strata_max.items())),
        },
    }


# ------------------------------------------------------------------ report assembly
def preregistration_block() -> dict[str, Any]:
    """Declared inputs and perturbation rule, assembled and verified before any output."""
    return {
        "declared_before_outputs": True,
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "config_sha256": sha256_file(CONFIG_PATH),
        "frozen_rule_authority": CONFIG["frozen_inputs"]["frozen_rule_authority"],
        "central_priors": CONFIG["central_priors"],
        "perturbation_rule": CONFIG["perturbation_rule"],
        "variant_grid": CONFIG["variant_grid"],
        "variant_count": len(CONFIG["variant_grid"]),
        "strata": CONFIG["strata"],
        "measurement": CONFIG["measurement"],
        "gates": GATES,
        "prohibitions": CONFIG["prohibitions"],
        "input_values": {
            "official_samples": CONFIG["frozen_inputs"]["official_samples"],
            "provided_strata": CONFIG["frozen_inputs"]["provided_strata"],
            "component_artifacts": {name: CONFIG["frozen_inputs"][name] for name in
                                    ("transport_component", "building_component", "poi_component")},
        },
    }


def build_report(workspace: Path) -> dict[str, Any]:
    declared = preregistration_block()          # 1. declare
    shas = verify_frozen_shas(workspace)        # 2. verify the declared inputs
    tracts, facts = load_tracts(workspace)      # 3. load only the declared columns
    measured = evaluate(tracts, list(CONFIG["variant_grid"]))  # 4. only then compute outputs
    baseline_row = measured["variants"][0]
    if baseline_row["variant"] != BASELINE:
        raise AssertionError("baseline variant is not first")

    variants = measured["variants"]
    nonfinite = sum(row["nonfinite_scores"] for row in variants)
    out_of_range = sum(row["scores_outside_zero_one"] for row in variants)
    flag_mismatch = sum(row["definedness_flag_mismatches"] for row in variants)
    zero_defined = sum(row["zero_defined_rows"] for row in variants)
    coverage_exact = all(
        facts["id_coverage_per_artifact"][name]["missing_official_ids"] == 0
        and facts["id_coverage_per_artifact"][name]["extra_non_official_ids"] == 0
        and facts["id_coverage_per_artifact"][name]["duplicate_ids"] == 0
        for name in facts["id_coverage_per_artifact"])
    deltas = [row["max_abs_per_tract_delta"] for row in variants]
    strata_shifts = measured["envelope"]["stratum_max_abs_mean_delta"]
    acceptance = {
        "every_variant_has_all_official_geoids": all(
            row["rows"] == GATES["official_geoids_per_variant"] == facts["official_scored_geoids"]
            for row in variants),
        "all_variant_scores_finite": nonfinite == 0,
        "all_variant_scores_in_zero_one": out_of_range == 0,
        "definedness_masks_identical_to_frozen": flag_mismatch == 0 and zero_defined
        == facts["zero_defined_rows"] * len(variants),
        "all_preregistered_variants_reported": [row["variant"] for row in variants] == list(
            CONFIG["variant_grid"]),
        "baseline_reproduces_frozen_composite": (
            baseline_row["max_abs_per_tract_delta"] is not None
            and baseline_row["max_abs_per_tract_delta"]
            <= GATES["max_abs_baseline_vs_frozen_composite_residual"]),
        "max_delta_quantified": all(value is not None for value in deltas),
        "stratum_shifts_quantified": bool(strata_shifts) and all(
            facts["max_abs_mean_delta"] is not None and facts["variant"] in CONFIG["variant_grid"]
            for facts in strata_shifts.values()) and all(
            group["mean_signed_delta"] is not None and group["mean_abs_delta"] is not None
            for row in variants for group in row["stratum_mean_delta"].values()),
        "no_winner_selected": CONFIG["measurement"]["no_winner_policy"] != "",
        "zero_defined_rows_allowed": facts["zero_defined_rows"] == 0,
        "declared_inputs_verified_by_sha256": bool(shas),
        "prior_transfer_rule_verified_on_zero_count_rows": (
            facts["presence_prior_transfer_verification"]["zero_count_rows_checked"] > 0
            and facts["presence_prior_transfer_verification"]["max_abs_gap_minus_presence"] <= 1e-11),
    }
    acceptance["all_passed"] = all(value for value in acceptance.values())
    return {
        "schema_version": CONFIG["schema_version"],
        "audit_id": CONFIG["audit_id"],
        "backlog_item": CONFIG["backlog_item"],
        "intent": CONFIG["intent"],
        "disposition": "sensitivity screen only; no promotion, no ranking, no submission, no upload",
        "preregistration": declared,
        "input_verification": {
            "sha256_verified": shas,
            "id_coverage_and_order": facts["id_coverage_per_artifact"],
            "presence_prior_transfer_verification": facts["presence_prior_transfer_verification"],
            "baseline_variant_vs_frozen_emitted_composite": {
                "gate": GATES["max_abs_baseline_vs_frozen_composite_residual"],
                "max_abs_per_tract_delta": baseline_row["max_abs_per_tract_delta"],
                "geoid_of_max": baseline_row["geoid_of_max_abs_delta"],
                "note": ("The baseline variant applies no perturbation, so this residual is the "
                         "12-significant-digit round-trip floor of the frozen component artifacts, "
                         "measured independently by the mask audit at 1.85e-12."),
            },
        },
        "scope": {
            "official_scored_geoids": facts["official_scored_geoids"],
            "unique_official_geoids": facts["unique_official_geoids"],
            "region_rows": facts["region_rows"],
            "expected_region_rows": facts["expected_region_rows"],
            "urban_rows": facts["urban_rows"],
            "rural_rows": facts["rural_rows"],
            "pct_urban_missing_fallback_rows": facts["pct_urban_missing_fallback_rows"],
            "tribal_true_rows": facts["tribal_true_rows"],
            "tribal_false_rows": facts["tribal_false_rows"],
            "tribal_unknown_rows": facts["tribal_unknown_rows"],
            "establishments_half_defined_rows": facts["establishments_half_defined_rows"],
            "zero_defined_rows": facts["zero_defined_rows"],
            "central_score_reference": "the composite emitted by the frozen artifacts for each tract",
            "candidate_score_csv": None,
            "candidate_note": ("No assembled v2 candidate CSV exists yet; the envelope is measured on the "
                               "frozen component artifacts' own composite, which is the same arithmetic a "
                               "candidate is emitted with. Rerun unchanged after candidate generation."),
        },
        "variants": variants,
        "envelope": measured["envelope"],
        "no_selection": {
            "winner_selected": False,
            "variants_expected": len(CONFIG["variant_grid"]),
            "variants_reported": len(variants),
            "ranking_emitted": False,
            "ordering": "variants are emitted in preregistered grid order, never by score",
            "note": CONFIG["measurement"]["no_winner_policy"],
        },
        "acceptance": acceptance,
        "prohibitions": CONFIG["prohibitions"],
    }


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
    except Exception as exc:  # pragma: no cover - defensive
        return f"unavailable: {exc}"


def payload_of(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=False, allow_nan=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=os.environ.get("COMPETITION_WORKSPACE"),
                       help="competition workspace holding official samples and provided strata")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/v2-prior-envelope.json")
    args = parser.parse_args()
    if not args.workspace:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    workspace = Path(args.workspace).resolve()

    report = build_report(workspace)
    payload = payload_of(report)
    if payload_of(build_report(workspace)) != payload:  # deterministic within one process
        print("FAIL v2 prior envelope: report is not reproducible within one process")
        return 1

    reproducibility = {
        "command": (f"uv run --with pyarrow==26.0.0 --with numpy==2.5.4 --with pyproj==3.8.0 "
                    f"--with shapely==2.2.0 python scripts/audit_v2_prior_envelope.py "
                    f"--workspace \"$COMPETITION_WORKSPACE\" --out reports/v2-prior-envelope.json"),
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "reused_rule_module": "scripts/audit_v2_component_masks.py",
        "reused_rule_module_sha256": sha256_file(ROOT / "scripts/audit_v2_component_masks.py"),
        "git_commit": git_commit(),
    }
    report["reproducibility"] = reproducibility
    payload = payload_of(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    baseline = report["variants"][0]
    peak = max(report["variants"], key=lambda row: row["max_abs_per_tract_delta"])
    print(f"official tracts audited: {report['scope']['official_scored_geoids']}")
    print(f"variants reported: {report['no_selection']['variants_reported']} "
          f"(preregistered {report['no_selection']['variants_expected']})")
    print(f"baseline vs frozen composite residual: {baseline['max_abs_per_tract_delta']:.3e}")
    print(f"max per-tract delta over variants: {peak['max_abs_per_tract_delta']:.6f} "
          f"({peak['variant']} at {peak['geoid_of_max_abs_delta']})")
    print(f"per-tract envelope width max: "
          f"{report['envelope']['per_tract_envelope_width_quantiles']['max']:.6f}")
    print(f"report: {args.out} sha256 {digest}")
    if not report["acceptance"]["all_passed"]:
        failed = sorted(key for key, value in report["acceptance"].items()
                        if isinstance(value, bool) and not value)
        print(f"FAIL v2 prior envelope gates not met: {failed}")
        return 1
    print("PASS v2 prior envelope: all preregistered variants reported, finite bounded scores, "
          "frozen masks preserved, no winner selected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
