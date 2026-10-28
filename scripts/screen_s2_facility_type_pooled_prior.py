#!/usr/bin/env python3
"""Screen preregistered support-weighted pooling for zero-count S2 facilities.

All scored comparisons are descriptive feature-only sensitivities; no labels,
removed references, C0, leaderboard scores, or scorecards are read.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import audit_shrink_h1b_rule_levels as h1b  # noqa: E402
import build_shrink_s2_assumption_bound as s2build  # noqa: E402

REGIONS = h1b.REGIONS
FACILITIES = h1b.FACILITIES
KAPPAS = (0, 2, 5)
BINS = (("low", 0.0, 0.5), ("medium", 0.5, 1.0), ("high", 1.0, math.inf))
CONFIG = ROOT / "config/s2-facility-type-pooled-prior-preregistered.json"
REPORT = ROOT / "reports/s2-facility-type-pooled-prior.json"
SCORES = ROOT / "reports/s2-facility-type-pooled-prior-scores.csv"
S2_PATH = ROOT / "reports/candidates/S2_h1b_assumption_bound_v1.csv"
S2B_PATH = ROOT / "reports/candidates/S2b_c2_expected_facility_mask_v1.csv"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pooled_rate(raw_count_sum: float, support_sum: float) -> float:
    """Region/type provided-data count rate, capped as a presence-rate prior."""
    raw, support = float(raw_count_sum), float(support_sum)
    if not (math.isfinite(raw) and math.isfinite(support)) or raw < 0 or support <= 0:
        raise ValueError("pooled counts must be finite/nonnegative and support positive")
    return min(1.0, raw / support)


def posterior_rate(raw_count: float, support: float, prior_rate: float, kappa: float) -> float:
    """Gamma-Poisson-style support-weighted blend on a [0,1] rate scale."""
    count, lam, prior, strength = map(float, (raw_count, support, prior_rate, kappa))
    if not all(map(math.isfinite, (count, lam, prior, strength))):
        raise ValueError("posterior inputs must be finite")
    if count < 0 or lam <= 0 or not 0 <= prior <= 1 or strength < 0:
        raise ValueError("invalid count, support, prior rate, or prior strength")
    return min(1.0, max(0.0, (count + strength * prior) / (lam + strength)))


def support_bin(value: float) -> str:
    x = float(value)
    if not math.isfinite(x) or x < 0:
        raise ValueError("support must be finite and nonnegative")
    if x < 0.5:
        return "low"
    if x < 1.0:
        return "medium"
    return "high"


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "max_abs": None, "p95_abs": None, "mean_signed": None}
    a = sorted(abs(float(v)) for v in values)
    signed = [float(v) for v in values]
    if not all(map(math.isfinite, a + signed)):
        raise ValueError("summary has nonfinite values")
    ix = max(0, math.ceil(0.95 * len(a)) - 1)
    return {"n": len(signed), "max_abs": max(a), "p95_abs": a[ix],
            "mean_signed": math.fsum(signed) / len(signed)}


def read_inputs(workspace: Path) -> dict[str, Any]:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not cfg.get("registered_before_screen_outputs"):
        raise ValueError("screen config was not preregistered")
    for rel, key in (("reports/candidates/S2_h1b_assumption_bound_v1.csv", "S2"),
                     ("reports/candidates/S2b_c2_expected_facility_mask_v1.csv", "S2b"),
                     ("reports/gap-estimates.csv", "gap_estimates")):
        if sha256(ROOT / rel) != cfg["frozen_inputs"][key]["sha256"]:
            raise ValueError(f"frozen input hash mismatch: {rel}")
    official_order, region_by_id, ids_by_region = h1b.read_official_ids(workspace)
    official_ids = set(region_by_id)
    s2, s2_order = s2build.read_csv_official(S2_PATH, official_ids,
        ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"], full_order=True)
    s2b, s2b_order = s2build.read_csv_official(S2B_PATH, official_ids,
        ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"], full_order=True)
    if len(s2_order) != 9794 or len(set(s2_order)) != 9794 or s2_order != s2b_order:
        raise ValueError("S2/S2b platform template row order differs or has duplicates")
    if set(s2) != official_ids or set(s2b) != official_ids:
        raise ValueError("S2/S2b must each cover all 9,379 official IDs")
    estimates = h1b.load_csv_map(ROOT / "reports/gap-estimates.csv", h1b.GAP_COLUMNS, official_ids)
    if set(estimates) != official_ids:
        raise ValueError("per-type frozen estimates do not cover exact official IDs")

    pin = json.loads((ROOT / "config/shrink-h1b-rule-level-preregistered.json").read_text())
    features: dict[str, dict[str, Any]] = {}
    strata: dict[str, dict[str, Any]] = {}
    feature_hashes: dict[str, dict[str, str]] = {}
    for region in REGIONS:
        allowed = set(ids_by_region[region])
        frel = pin["pinned_inputs"]["provided_overture_tract_summaries"][region]
        srel = pin["pinned_inputs"]["provided_strata"][region]
        fpath = workspace / frel["path"].replace("$COMPETITION_WORKSPACE/", "")
        spath = workspace / srel["path"].replace("$COMPETITION_WORKSPACE/", "")
        if sha256(fpath) != frel["sha256"] or sha256(spath) != srel["sha256"]:
            raise ValueError(f"pinned input hash mismatch for {region}")
        features.update(h1b.load_parquet_map(fpath, h1b.FEATURE_COLUMNS, allowed))
        strata.update(h1b.load_parquet_map(spath, h1b.STRATA_COLUMNS, allowed))
        feature_hashes[region] = {"overture_sha256": sha256(fpath), "strata_sha256": sha256(spath)}
    if set(features) != official_ids or set(strata) != official_ids:
        raise ValueError("provided features/strata must cover exact official IDs")
    expected = {g: h1b.expected_counts(features[g], strata[g]) for g in official_ids}
    return {"cfg": cfg, "official_order": official_order, "region_by_id": region_by_id,
            "ids_by_region": ids_by_region, "s2": s2, "s2b": s2b, "s2_order": s2_order,
            "estimates": estimates, "features": features, "strata": strata,
            "expected": expected, "feature_hashes": feature_hashes}


def regional_prior_rates(ids_by_region: dict[str, list[str]], features: dict[str, dict[str, Any]],
                         expected: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    rates: dict[str, dict[str, float]] = {}
    for region, ids in ids_by_region.items():
        rates[region] = {}
        for kind in FACILITIES:
            raw_sum = math.fsum(float(features[g][f"overture_poi_{kind}"]) for g in ids)
            support_sum = math.fsum(float(expected[g][kind]) for g in ids)
            rates[region][kind] = pooled_rate(raw_sum, support_sum)
    return rates


def poi_score(geoid: str, row: dict[str, str], estimates: dict[str, str],
              feature: dict[str, Any], expected: dict[str, float],
              prior_rates: dict[str, float], kappa: int,
              only_kind: str | None = None) -> tuple[float, int]:
    """Recompose POI with pooled imputation for selected raw-zero types."""
    facility_values: list[float] = []
    for kind in FACILITIES:
        if only_kind is not None and kind != only_kind:
            # Unchanged local defined types remain; S2-dropped zero types stay omitted.
            pass
        raw = float(feature[f"overture_poi_{kind}"])
        if not math.isfinite(raw) or raw < 0:
            raise ValueError(f"invalid provided Overture count {geoid}/{kind}")
        defined = h1b.flag(estimates[f"{kind}_defined"], f"{kind}_defined")
        gap = float(estimates[f"{kind}_gap"])
        if not math.isfinite(gap) or not 0 <= gap <= 1:
            raise ValueError(f"invalid per-type gap {geoid}/{kind}")
        if raw > 0:
            if defined:
                facility_values.append(gap)
        elif kappa > 0 and (only_kind is None or kind == only_kind):
            rate = posterior_rate(0.0, expected[kind], prior_rates[kind], kappa)
            facility_values.append(1.0 - rate)
        # At kappa=0, raw-zero types retain the frozen S2 drop rule.
    halves: list[float] = []
    if facility_values:
        halves.append(math.fsum(facility_values) / len(facility_values))
    cbp_defined = h1b.flag(estimates["cbp_defined"], "cbp_defined")
    cbp_gap = float(estimates["cbp_gap"])
    if cbp_defined:
        if not math.isfinite(cbp_gap) or not 0 <= cbp_gap <= 1:
            raise ValueError(f"invalid CBP-proxy gap {geoid}")
        halves.append(cbp_gap)
    if not halves:
        return 0.0, 0
    return math.fsum(halves) / len(halves), 1


def score_from_components(geoid: str, s2row: dict[str, str], estimates: dict[str, str],
                          poi: float, poi_defined: int) -> float:
    components = []
    for name in ("transport", "building"):
        defined = h1b.flag(estimates[f"{name}_defined"], f"{name}_defined")
        value = float(s2row[f"{name}_gap"])
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"invalid S2 {name} for {geoid}")
        if defined:
            components.append(value)
    if poi_defined:
        components.append(poi)
    value = math.fsum(components) / len(components) if components else 0.0
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"candidate score outside [0,1] for {geoid}")
    return value


def run_screen(workspace: Path) -> dict[str, Any]:
    data = read_inputs(workspace)
    priors = regional_prior_rates(data["ids_by_region"], data["features"], data["expected"])
    base: dict[str, float] = {}
    full: dict[int, dict[str, float]] = {0: {}, 2: {}, 5: {}}
    support_means: dict[str, float] = {}
    for geoid, region in data["official_order"]:
        row, est = data["s2"][geoid], data["estimates"][geoid]
        baseline = float(row["coverage_gap_score"])
        if not math.isfinite(baseline) or not 0 <= baseline <= 1:
            raise ValueError(f"invalid frozen S2 score: {geoid}")
        base[geoid] = baseline
        support_means[geoid] = math.fsum(data["expected"][geoid][k] for k in FACILITIES) / len(FACILITIES)
        poi0, flag0 = poi_score(geoid, row, est, data["features"][geoid], data["expected"][geoid], priors[region], 0)
        rebuilt0 = score_from_components(geoid, row, est, poi0, flag0)
        if abs(rebuilt0 - baseline) > 1e-10:
            raise ValueError(f"kappa=0 screen does not reproduce frozen S2 for {geoid}: {rebuilt0} vs {baseline}")
        full[0][geoid] = baseline
        for kappa in (2, 5):
            poi, poi_flag = poi_score(geoid, row, est, data["features"][geoid], data["expected"][geoid], priors[region], kappa)
            full[kappa][geoid] = score_from_components(geoid, row, est, poi, poi_flag)

    score_rows = []
    for geoid, region in data["official_order"]:
        score_rows.append({"GEOID": geoid, "region": region, "s2_frozen": format(base[geoid], ".17g"),
                           "kappa_0": format(full[0][geoid], ".17g"),
                           "kappa_2": format(full[2][geoid], ".17g"),
                           "kappa_5": format(full[5][geoid], ".17g")})
    SCORES.parent.mkdir(parents=True, exist_ok=True)
    with SCORES.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["GEOID", "region", "s2_frozen", "kappa_0", "kappa_2", "kappa_5"], lineterminator="\n")
        w.writeheader(); w.writerows(score_rows)

    overall: dict[str, Any] = {}
    for kappa in KAPPAS:
        grouped: dict[str, list[float]] = defaultdict(list)
        by_region: dict[str, list[float]] = defaultdict(list)
        for geoid, region in data["official_order"]:
            delta = full[kappa][geoid] - base[geoid]
            grouped[f"{region}/{support_bin(support_means[geoid])}"].append(delta)
            by_region[region].append(delta)
        overall[str(kappa)] = {"all": summarize([full[kappa][g] - base[g] for g, _ in data["official_order"]]),
                               "by_region": {r: summarize(v) for r, v in sorted(by_region.items())},
                               "by_region_mean_facility_support_bin": {g: summarize(v) for g, v in sorted(grouped.items())}}

    type_attribution: dict[str, Any] = {}
    for kappa in (2, 5):
        type_attribution[str(kappa)] = {}
        for kind in FACILITIES:
            groups: dict[str, list[float]] = defaultdict(list)
            zero_counts: dict[str, int] = defaultdict(int)
            for geoid, region in data["official_order"]:
                poi, poi_flag = poi_score(geoid, data["s2"][geoid], data["estimates"][geoid], data["features"][geoid],
                                          data["expected"][geoid], priors[region], kappa, only_kind=kind)
                value = score_from_components(geoid, data["s2"][geoid], data["estimates"][geoid], poi, poi_flag)
                support = data["expected"][geoid][kind]
                group = f"{region}/{support_bin(support)}"
                groups[group].append(value - base[geoid])
                if float(data["features"][geoid][f"overture_poi_{kind}"]) == 0:
                    zero_counts[group] += 1
            type_attribution[str(kappa)][kind] = {
                group: {**summarize(vals), "zero_count_rows": zero_counts[group]}
                for group, vals in sorted(groups.items())
            }

    mask_comparison: dict[str, Any] = {}
    for region, ids in data["ids_by_region"].items():
        deltas = [float(data["s2b"][g]["coverage_gap_score"]) - float(data["s2"][g]["coverage_gap_score"]) for g in ids]
        mask_comparison[region] = {**summarize(deltas), "share_abs_delta_gt_0_05": sum(abs(x) > .05 for x in deltas) / len(deltas)}

    supports: dict[str, Any] = {}
    for region, ids in data["ids_by_region"].items():
        supports[region] = {}
        for kind in FACILITIES:
            vals = [data["expected"][g][kind] for g in ids]
            raw_zero = [g for g in ids if float(data["features"][g][f"overture_poi_{kind}"]) == 0]
            supports[region][kind] = {
                "prior_rate": priors[region][kind],
                "total_raw_count": math.fsum(float(data["features"][g][f"overture_poi_{kind}"]) for g in ids),
                "total_expected_support": math.fsum(vals),
                "zero_count_rows": len(raw_zero),
                "zero_count_by_support_bin": {b: sum(support_bin(data["expected"][g][kind]) == b for g in raw_zero) for b, _, _ in BINS},
            }

    input_hashes = {
        "S2_sha256": sha256(S2_PATH), "S2b_sha256": sha256(S2B_PATH),
        "gap_estimates_sha256": sha256(ROOT / "reports/gap-estimates.csv"),
        "feature_strata_by_region": data["feature_hashes"],
        "preregistration_sha256": sha256(CONFIG),
    }
    score_hash = sha256(SCORES)
    report = {
        "schema_version": 1,
        "analysis_id": "S2_FACILITY_TYPE_POOLED_PRIOR_SCREEN_V1",
        "preregistered_config": "config/s2-facility-type-pooled-prior-preregistered.json",
        "result": "PASS",
        "scope": {"official_rows": 9379, "template_rows_in_S2_inputs": 9794,
                  "official_regions": {r: len(v) for r, v in data["ids_by_region"].items()},
                  "output_rows": len(score_rows), "all_official_ids_covered": True},
        "method": {
            "strengths": list(KAPPAS), "prior_rates_by_region_type": priors,
            "prior_formula": "min(1, sum Overture raw counts / sum frozen expected facility support), by region and type",
            "posterior_formula": "(raw_count + kappa * pooled_rate) / (lambda + kappa); zero-count imputed gap=1-posterior_rate",
            "kappa_zero": "exact frozen S2 mask; raw-zero facility types remain dropped",
            "support_bins": {"low": "lambda < 0.5", "medium": "0.5 <= lambda < 1.0", "high": "lambda >= 1.0"},
            "only_provided_features_and_frozen_C2_support": True,
            "labels_or_reference_layers_or_scores_used": False,
        },
        "inputs": input_hashes,
        "frozen_S2_vs_S2b_baseline": mask_comparison,
        "facility_support_and_zero_handling": supports,
        "candidate_score_shift_vs_S2": overall,
        "single_type_attribution_vs_S2": type_attribution,
        "validation": {"all_scores_finite_and_in_0_1": True, "deterministic": True,
                       "kappa_0_reproduces_frozen_S2": True, "no_target_accuracy_claim": True},
        "outputs": {"score_table": str(SCORES.relative_to(ROOT)), "score_table_sha256": score_hash,
                    "report": str(REPORT.relative_to(ROOT))},
        "interpretation": "Sensitivity of the frozen feature-only estimator to the preregistered support-pooled assumption; not evidence of improved target accuracy.",
    }
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", type=Path, required=True)
    report = run_screen(ap.parse_args().workspace.resolve())
    print(json.dumps({"result": report["result"], "official_rows": report["scope"]["official_rows"],
                      "kappa": report["method"]["strengths"],
                      "scores_sha256": report["outputs"]["score_table_sha256"],
                      "report": str(REPORT)}, sort_keys=True))


if __name__ == "__main__":
    main()
