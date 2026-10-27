#!/usr/bin/env python3
"""Descriptive heat-quartile contrast in provided named-road class-mix entropy."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import official_scored_pairs  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_ROWS = 9379
BOOTSTRAP_REPLICATES = 2000
BASE_SEED = 20261028
ENTROPY_RELATIVE_PATH = Path("named-road-class-entropy/class-entropy-crossfit.parquet")
ENTROPY_COLUMNS = ["GEOID", "region", "class_mix_entropy", "entropy_defined"]
HEAT_COLUMNS = ["GEOID", "epht_heat_days_summer", "epht_covered"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def seed_for(label: str) -> int:
    return BASE_SEED + int.from_bytes(hashlib.sha256(label.encode("utf-8")).digest()[:4], "big")


def finite_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None


def upper_quartile(heat: np.ndarray, covered: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    values = np.asarray(heat, dtype=np.float64)
    mask = np.asarray(covered, dtype=bool)
    if values.ndim != 1 or mask.shape != values.shape or not mask.any():
        raise ValueError("heat values and coverage mask must align and have covered support")
    if np.any(mask & ~np.isfinite(values)) or np.any(~mask & np.isfinite(values)):
        raise ValueError("heat coverage mask must exactly match finite heat values")
    threshold = float(np.quantile(values[mask], .75))
    upper = mask & (values >= threshold)  # ties are intentionally assigned to the upper group
    lower = mask & (values < threshold)
    if not upper.any() or not lower.any():
        raise ValueError("heat quartile split must define both groups")
    return threshold, upper, lower


def bootstrap_difference(top: np.ndarray, remaining: np.ndarray, seed: int,
                         replicates: int = BOOTSTRAP_REPLICATES) -> np.ndarray:
    top = np.asarray(top, dtype=np.float64)
    remaining = np.asarray(remaining, dtype=np.float64)
    if not top.size or not remaining.size or not np.isfinite(top).all() or not np.isfinite(remaining).all():
        raise ValueError("bootstrap groups must be nonempty and finite")
    if replicates != BOOTSTRAP_REPLICATES:
        raise ValueError("this screen is preregistered for exactly 2,000 bootstrap replicates")
    rng = np.random.default_rng(seed)
    estimates = np.empty(replicates, dtype=np.float64)
    chunk = 128
    for start in range(0, replicates, chunk):
        stop = min(replicates, start + chunk)
        i_top = rng.integers(0, len(top), size=(stop - start, len(top)), dtype=np.int32)
        i_other = rng.integers(0, len(remaining), size=(stop - start, len(remaining)), dtype=np.int32)
        estimates[start:stop] = top[i_top].mean(axis=1) - remaining[i_other].mean(axis=1)
    return estimates


def load_inputs(workspace: Path) -> tuple[list[tuple[str, str]], np.ndarray, np.ndarray,
                                          np.ndarray, np.ndarray, dict[str, Any]]:
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != EXPECTED_ROWS or len(set(official)) != EXPECTED_ROWS
            or len({g for g, _ in official}) != EXPECTED_ROWS or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official ID source is not an exact 9,379-row/four-region partition")
    locations = {pair: index for index, pair in enumerate(official)}
    entropy_path = workspace / ENTROPY_RELATIVE_PATH
    if not entropy_path.is_file():
        raise FileNotFoundError(f"completed named-road entropy artifact missing: {entropy_path}")
    entropy_table = pq.read_table(entropy_path, columns=ENTROPY_COLUMNS)
    if entropy_table.num_rows != EXPECTED_ROWS:
        raise ValueError("entropy artifact row count differs from official sample IDs")
    entropy = np.full(EXPECTED_ROWS, np.nan, dtype=np.float64)
    entropy_mask = np.zeros(EXPECTED_ROWS, dtype=bool)
    entropy_seen: set[tuple[str, str]] = set()
    for geoid, region, value, is_defined in zip(
            entropy_table.column("GEOID").to_pylist(), entropy_table.column("region").to_pylist(),
            entropy_table.column("class_mix_entropy").to_pylist(),
            entropy_table.column("entropy_defined").to_pylist()):
        pair = (str(geoid), str(region))
        if pair not in locations or pair in entropy_seen or not isinstance(is_defined, bool):
            raise ValueError(f"entropy artifact has an unexpected, duplicate, or invalid ID/mask: {pair}")
        entropy_seen.add(pair)
        index = locations[pair]
        value_finite = finite_value(value)
        if is_defined:
            if value_finite is None or not 0. <= value_finite <= 1.:
                raise ValueError(f"defined entropy value is missing, nonfinite, or outside [0,1]: {pair}")
            entropy[index] = value_finite
            entropy_mask[index] = True
        elif value_finite is not None:
            raise ValueError(f"undefined entropy must be null in artifact: {pair}")
    if entropy_seen != set(official) or not np.array_equal(np.isfinite(entropy), entropy_mask):
        raise ValueError("entropy artifact does not exactly align with official ID/null support")

    heat = np.full(EXPECTED_ROWS, np.nan, dtype=np.float64)
    heat_mask = np.zeros(EXPECTED_ROWS, dtype=bool)
    heat_meta: dict[str, Any] = {}
    for region in REGIONS:
        path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"provided heat strata missing: {path}")
        table = pq.read_table(path, columns=HEAT_COLUMNS)
        region_locations = {g: locations[(g, region)] for g, r in official if r == region}
        seen: set[str] = set()
        extra_provided_rows = 0
        covered_but_null = uncovered_but_finite = 0
        for geoid_value, heat_value, covered_value in zip(
                table.column("GEOID").to_pylist(), table.column("epht_heat_days_summer").to_pylist(),
                table.column("epht_covered").to_pylist()):
            geoid = str(geoid_value)
            if geoid not in region_locations:
                extra_provided_rows += 1
                continue
            if geoid in seen:
                raise ValueError(f"duplicate provided heat-strata GEOID for {region}: {geoid}")
            seen.add(geoid)
            value = finite_value(heat_value)
            covered = covered_value is True
            if covered and value is None:
                covered_but_null += 1
            if not covered and value is not None:
                uncovered_but_finite += 1
            if covered and value is not None:
                index = region_locations[geoid]
                heat[index] = value
                heat_mask[index] = True
        if seen != set(region_locations):
            raise ValueError(f"provided heat table does not exactly cover official IDs for {region}")
        heat_meta[region] = {"path": str(path), "sha256": sha256_file(path),
                             "columns_read": HEAT_COLUMNS, "provided_table_rows": table.num_rows,
                             "official_ids_joined": len(seen), "official_heat_covered_finite": int(sum(
                                 heat_mask[region_locations[g]] for g in region_locations)),
                             "official_heat_missing": int(sum(
                                 not heat_mask[region_locations[g]] for g in region_locations)),
                             "provided_nonofficial_rows_ignored": extra_provided_rows,
                             "covered_but_null_values": covered_but_null,
                             "uncovered_but_finite_values_ignored": uncovered_but_finite}
    return official, entropy, entropy_mask, heat, heat_mask, {
        "entropy_artifact": {"path": str(entropy_path), "sha256": sha256_file(entropy_path),
                             "columns_read": ENTROPY_COLUMNS, "rows": entropy_table.num_rows,
                             "defined_ids": int(entropy_mask.sum()),
                             "null_ids": int((~entropy_mask).sum())},
        "heat_strata_by_region": heat_meta,
    }


def analyze(official: list[tuple[str, str]], entropy: np.ndarray, entropy_mask: np.ndarray,
            heat: np.ndarray, heat_mask: np.ndarray) -> dict[str, Any]:
    n = len(official)
    if any(np.asarray(x).shape != (n,) for x in (entropy, entropy_mask, heat, heat_mask)):
        raise ValueError("all feature arrays must exactly align with official ID rows")
    if not np.array_equal(np.isfinite(entropy), entropy_mask) or not np.array_equal(np.isfinite(heat), heat_mask):
        raise ValueError("feature null masks must exactly match finite values")
    if np.any(entropy_mask & ((entropy < 0.) | (entropy > 1.))):
        raise ValueError("defined normalized road entropy must be within [0,1]")

    per_region: dict[str, Any] = {}
    regional_draws: list[np.ndarray] = []
    regional_differences: list[float] = []
    eligible_regions = 0
    top_support_regions = 0
    all_group_support = True
    for region in REGIONS:
        index = np.asarray([i for i, (_, r) in enumerate(official) if r == region], dtype=np.int64)
        threshold, upper, lower = upper_quartile(heat[index], heat_mask[index])
        joint_upper = upper & entropy_mask[index]
        joint_lower = lower & entropy_mask[index]
        upper_values = entropy[index][joint_upper]
        lower_values = entropy[index][joint_lower]
        upper_n, lower_n = int(len(upper_values)), int(len(lower_values))
        both_nonempty = upper_n > 0 and lower_n > 0
        all_group_support &= both_nonempty
        if both_nonempty:
            upper_mean = float(upper_values.mean())
            lower_mean = float(lower_values.mean())
            difference = upper_mean - lower_mean
            eligible_regions += 1
            if upper_n >= 30 and lower_n >= 30:
                top_support_regions += 1
            seed = seed_for(f"named-road-entropy-heat:{region}:upper-vs-remaining")
            draws = bootstrap_difference(upper_values, lower_values, seed)
            regional_draws.append(draws)
            regional_differences.append(difference)
            per_region[region] = {
                "official_ids": int(len(index)), "heat_covered_finite_ids": int(heat_mask[index].sum()),
                "heat_q75_threshold": threshold, "quartile_rule": "np.quantile(q=.75); values >= q75 assigned to upper group (ties included)",
                "heat_upper_group_heat_covered_ids": int(upper.sum()),
                "heat_remaining_group_heat_covered_ids": int(lower.sum()),
                "entropy_defined_upper_group_n": upper_n, "entropy_defined_remaining_group_n": lower_n,
                "entropy_mean_upper_heat_quartile": upper_mean,
                "entropy_mean_remaining_heat_covered": lower_mean,
                "upper_minus_remaining_mean_difference": difference,
                "both_groups_meet_n_30": bool(upper_n >= 30 and lower_n >= 30),
                "bootstrap_seed": seed,
            }
        else:
            per_region[region] = {
                "official_ids": int(len(index)), "heat_covered_finite_ids": int(heat_mask[index].sum()),
                "heat_q75_threshold": threshold,
                "heat_upper_group_heat_covered_ids": int(upper.sum()),
                "heat_remaining_group_heat_covered_ids": int(lower.sum()),
                "entropy_defined_upper_group_n": upper_n, "entropy_defined_remaining_group_n": lower_n,
                "entropy_mean_upper_heat_quartile": None, "entropy_mean_remaining_heat_covered": None,
                "upper_minus_remaining_mean_difference": None, "both_groups_meet_n_30": False,
                "reason": "entropy-defined observations missing from one or both heat groups"}
    if all_group_support and len(regional_draws) == len(REGIONS):
        observed = float(np.mean(regional_differences))  # equal region weight, regardless of tract count
        draw_matrix = np.vstack(regional_draws)
        equal_region_draws = draw_matrix.mean(axis=0)
        ci_low, ci_high = np.quantile(equal_region_draws, [.025, .975])
        ci = [float(ci_low), float(ci_high)]
        ci_excludes_zero = bool(ci_low > 0. or ci_high < 0.)
    else:
        observed, ci, ci_excludes_zero = None, None, False
    support_pass = top_support_regions >= 3
    effect_pass = observed is not None and abs(observed) >= .05
    gate_pass = bool(support_pass and effect_pass and ci_excludes_zero)
    return {
        "regions": per_region,
        "joint_heat_entropy_defined_ids": int(np.count_nonzero(heat_mask & entropy_mask)),
        "entropy_defined_ids": int(entropy_mask.sum()),
        "heat_covered_finite_ids": int(heat_mask.sum()),
        "equal_region_mean_difference_upper_minus_remaining": observed,
        "equal_region_bootstrap_95_ci": ci,
        "equal_region_bootstrap_ci_excludes_zero": ci_excludes_zero,
        "equal_region_bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "bootstrap_method": "Within each region, independently resample tracts with replacement within the upper heat quartile and remaining heat-covered group; average regional mean differences with equal region weight for each replicate; percentile 95% interval.",
        "regions_with_at_least_30_entropy_defined_tracts_in_each_group": top_support_regions,
        "regions_with_nonempty_entropy_defined_groups": eligible_regions,
        "support_gate_pass": support_pass,
        "effect_size_gate_pass_abs_difference_at_least_0_05": bool(effect_pass),
        "bootstrap_ci_gate_pass": ci_excludes_zero,
        "descriptive_screen_gate_pass": gate_pass,
        "interpretation": ("Descriptive heat-stratum entropy contrast met preregistered support, effect-size, and CI gates; no causal or accuracy claim." if gate_pass else
                           "No disparity claim: at least one preregistered support, effect-size, or CI gate failed."),
    }


def run(workspace: Path, report_path: Path) -> dict[str, Any]:
    official, entropy, entropy_mask, heat, heat_mask, provenance = load_inputs(workspace)
    result = analyze(official, entropy, entropy_mask, heat, heat_mask)
    partition_hash = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    baseline_path = ROOT / "reports/named-road-class-entropy.json"
    baseline_report = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_scope = baseline_report["scope"]
    baseline_ratio = float(baseline_report["acceptance"]["crossfit_residual_variance_ratio"])
    if (baseline_scope["official_ids"] != EXPECTED_ROWS
            or baseline_scope["positive_named_road_length_ids"] != int(entropy_mask.sum())
            or abs(baseline_ratio - 0.6607401765971582) > 1e-12):
        raise ValueError("completed named-road entropy baseline differs from the dispatched MEASURE-BEFORE values")
    provenance["named_road_entropy_baseline_report"] = {
        "path": str(baseline_path), "sha256": sha256_file(baseline_path),
        "official_ids": int(baseline_scope["official_ids"]),
        "entropy_defined_ids": int(baseline_scope["positive_named_road_length_ids"]),
        "entropy_coverage_fraction": float(baseline_scope["positive_length_coverage_fraction"]),
        "crossfit_residual_variance_ratio": baseline_ratio,
        "heat_stratified_contrast_measured_before": False}
    report = {
        "screen": "Provided named-road class-share entropy by provided summer-heat quartile; descriptive and unregistered",
        "measure_before": {"entropy_defined_ids": int(baseline_scope["positive_named_road_length_ids"]),
                           "official_ids": int(baseline_scope["official_ids"]),
                           "entropy_coverage_fraction": float(baseline_scope["positive_length_coverage_fraction"]),
                           "crossfit_residual_variance_ratio": baseline_ratio,
                           "heat_stratified_contrast_measured": False,
                           "source_report": str(baseline_path)},
        "pre_registration": {"heat_column": "epht_heat_days_summer", "coverage_column": "epht_covered",
                             "heat_group": "within-region q75 among finite heat-covered official tracts; ties included in upper group",
                             "entropy": "reuse class_mix_entropy from accepted named-road entropy artifact; no road source recomputation",
                             "contrast": "within-region tract-mean entropy, upper heat quartile minus remaining heat-covered tracts; equal-region mean across four regions",
                             "bootstrap_replicates": BOOTSTRAP_REPLICATES, "bootstrap_base_seed": BASE_SEED,
                             "support_gate": "at least three regions with >=30 entropy-defined observations in each group",
                             "effect_gate": "absolute equal-region mean difference >=0.05 and 95% bootstrap interval excludes zero",
                             "unregistered": True},
        "scope": {"official_rows": len(official), "official_geoid_region_sha256": partition_hash,
                  "entropy_undefined_ids": int((~entropy_mask).sum()), "heat_missing_ids": int((~heat_mask).sum()),
                  "joint_defined_ids": int(np.count_nonzero(entropy_mask & heat_mask)),
                  "labels_read": False, "sample_score_values_read": False,
                  "TIGER_or_equivalent_reference_read": False, "external_data_used": [],
                  "leaderboard_feedback_used": False, "estimator_or_registry_changed": False, "uploads": 0},
        "provenance": provenance,
        "measure_after": result,
        "implementation_sha256": sha256_file(Path(__file__).resolve()),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/named-road-entropy-heat-contrast.json")
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report = run(workspace, ROOT / args.out)
    print(json.dumps({"scope": report["scope"], "measure_after": report["measure_after"],
                      "provenance": report["provenance"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
