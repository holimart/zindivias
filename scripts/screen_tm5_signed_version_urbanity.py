#!/usr/bin/env python3
"""Screen rural/urban heterogeneity in frozen signed Tm5 version-rank gaps."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import official_scored_pairs  # noqa: E402
from screen_tm5_building_p90_mx4_interaction import REGIONS, sha256_file, parquet_bytes  # noqa: E402

EXPECTED_N = 9379
RANK_REPORT = ROOT / "reports/tm5-poi-building-version-discordance.json"
SIGNED_REPORT = ROOT / "reports/tm5-signed-poi-building-version-asymmetry.json"
SIGNED_REPORT_SHA = "d932b671f056947a27d3021e93d1197855988fed2cb326c023ad775705cef40d"
RANK_ARTIFACT_SHA = "8ed28f993f75f0fa7ed872341101b2bcd6c881f80d79245fb2184a2573bcf7fb"
SIGNED_ARTIFACT_SHA = "387e964b134021aa643786fc2d0157b4c48f4ef3d6b3e7ffd53b651111a4fc46"
MIN_GROUP_N = 30
MIN_REGIONS_WITH_SUPPORT = 2
MIN_ABS_CONTRAST = 0.05
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261034


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def signed_gap_from_ranks(poi_rank: np.ndarray, building_rank: np.ndarray,
                          joint_defined: np.ndarray) -> np.ndarray:
    poi = np.asarray(poi_rank, dtype=np.float64)
    building = np.asarray(building_rank, dtype=np.float64)
    joint = np.asarray(joint_defined, dtype=bool)
    if poi.ndim != 1 or building.shape != poi.shape or joint.shape != poi.shape:
        raise ValueError("frozen ranks and joint mask must be aligned one-dimensional arrays")
    if np.any(joint & (~np.isfinite(poi) | ~np.isfinite(building))):
        raise ValueError("joint-defined frozen ranks must be finite")
    if np.any(~joint & (np.isfinite(poi) & np.isfinite(building))):
        raise ValueError("joint-undefined ranks cannot both be defined")
    if np.any(joint & ((poi < 0.) | (poi > 1.) | (building < 0.) | (building > 1.))):
        raise ValueError("frozen within-region midranks must be in [0,1]")
    result = np.full(poi.shape, np.nan, dtype=np.float64)
    result[joint] = poi[joint] - building[joint]
    if np.any(joint & ((result < -1.) | (result > 1.))):
        raise ValueError("signed POI-minus-building rank gaps must be in [-1,1]")
    return result


def summarize_groups(official: list[tuple[str, str]], signed_gap: np.ndarray,
                     joint_defined: np.ndarray, pct_urban: np.ndarray) -> dict[str, Any]:
    n = len(official)
    gap = np.asarray(signed_gap, dtype=np.float64)
    joint = np.asarray(joint_defined, dtype=bool)
    urbanity = np.asarray(pct_urban, dtype=np.float64)
    if gap.shape != (n,) or joint.shape != (n,) or urbanity.shape != (n,):
        raise ValueError("signed gap, source mask and pct_urban must align to official IDs")
    if np.any(joint & ~np.isfinite(gap)) or np.any(~joint & np.isfinite(gap)):
        raise ValueError("signed-gap nulls must exactly preserve frozen joint mask")
    if np.any(np.isfinite(urbanity) & ((urbanity < 0.) | (urbanity > 1.))):
        raise ValueError("provided pct_urban must be in [0,1]")
    result: dict[str, Any] = {"by_region": {}, "groups": {}}
    for region in REGIONS:
        region_mask = np.asarray([r == region for _, r in official], dtype=bool)
        known = region_mask & joint & np.isfinite(urbanity)
        rural = known & (urbanity < 0.5)
        urban = known & (urbanity >= 0.5)
        rural_values, urban_values = gap[rural], gap[urban]
        if not rural_values.size or not urban_values.size:
            raise ValueError(f"{region} must contain both urbanity groups")
        rural_mean, urban_mean = float(rural_values.mean()), float(urban_values.mean())
        contrast = rural_mean - urban_mean
        result["by_region"][region] = {
            "official_ids": int(region_mask.sum()), "joint_defined_ids": int((region_mask & joint).sum()),
            "urbanity_defined_joint_ids": int(known.sum()), "rural_n": int(rural_values.size),
            "urban_n": int(urban_values.size), "rural_mean_signed_gap": rural_mean,
            "urban_mean_signed_gap": urban_mean, "signed_rural_minus_urban_mean_contrast": contrast,
            "absolute_contrast": abs(contrast),
            "both_groups_n_at_least_30": bool(rural_values.size >= MIN_GROUP_N and urban_values.size >= MIN_GROUP_N),
        }
        result["groups"][region] = (rural_values.copy(), urban_values.copy())
    max_region = max(REGIONS, key=lambda region: result["by_region"][region]["absolute_contrast"])
    result["max_region"] = max_region
    result["max_absolute_contrast"] = result["by_region"][max_region]["absolute_contrast"]
    result["max_region_signed_contrast"] = result["by_region"][max_region]["signed_rural_minus_urban_mean_contrast"]
    result["regions_meeting_support"] = [region for region in REGIONS
                                         if result["by_region"][region]["both_groups_n_at_least_30"]]
    return result


def bootstrap_contrasts(groups: dict[str, tuple[np.ndarray, np.ndarray]], *,
                        replicates: int = BOOTSTRAP_REPLICATES,
                        seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    if tuple(groups) != REGIONS:
        raise ValueError("bootstrap groups must include all four official regions in canonical order")
    if replicates < 100:
        raise ValueError("at least 100 bootstrap replicates are required")
    rng = np.random.default_rng(seed)
    by_region = {region: np.empty(replicates, dtype=np.float64) for region in REGIONS}
    max_absolute = np.empty(replicates, dtype=np.float64)
    for b in range(replicates):
        for region in REGIONS:
            rural, urban = (np.asarray(v, dtype=np.float64) for v in groups[region])
            if (rural.size == 0 or urban.size == 0 or not np.isfinite(rural).all()
                    or not np.isfinite(urban).all()):
                raise ValueError(f"{region} bootstrap groups must be nonempty and finite")
            # Stratified tract bootstrap: independently resample within each region × stratum.
            rmean = float(rng.choice(rural, size=rural.size, replace=True).mean())
            umean = float(rng.choice(urban, size=urban.size, replace=True).mean())
            by_region[region][b] = rmean - umean
        max_absolute[b] = max(abs(float(by_region[r][b])) for r in REGIONS)
    regional_ci = {region: [float(v) for v in np.quantile(draws, [.025, .975])]
                   for region, draws in by_region.items()}
    max_ci = [float(v) for v in np.quantile(max_absolute, [.025, .975])]
    return {"replicates": int(replicates), "seed": int(seed),
            "resampling": "with-replacement tract resampling independently within each region×rural/urban stratum; each replicate recomputes four signed mean contrasts and their maximum absolute contrast",
            "regional_signed_contrast_ci95_percentile": regional_ci,
            "max_absolute_contrast_ci95_percentile": max_ci,
            "max_absolute_contrast_bootstrap_distribution": {
                "mean": float(max_absolute.mean()), "sd": float(max_absolute.std(ddof=0)),
                "min": float(max_absolute.min()), "max": float(max_absolute.max())},
            "max_absolute_ci_excludes_zero": bool(max_ci[0] > 0. or max_ci[1] < 0.)}


def load_rank_source(path: Path, expected_sha: str, official: list[tuple[str, str]]) -> dict[str, np.ndarray]:
    if not path.is_file() or sha256_file(path) != expected_sha:
        raise ValueError("frozen midrank source artifact missing or hash differs from validated report")
    columns = ["GEOID", "region", "poi_version_defined", "poi_within_region_midpercentile",
               "building_version_defined", "building_within_region_midpercentile", "joint_defined"]
    table = pq.read_table(path, columns=columns)
    if table.num_rows != len(official):
        raise ValueError("frozen rank artifact must contain exactly one row per official ID")
    locations = {pair: i for i, pair in enumerate(official)}
    poi = np.full(len(official), np.nan)
    building = np.full(len(official), np.nan)
    pmask = np.zeros(len(official), dtype=bool)
    bmask = np.zeros(len(official), dtype=bool)
    joint = np.zeros(len(official), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in locations or pair in seen:
            raise ValueError(f"unexpected or duplicate frozen-rank ID: {pair}")
        seen.add(pair)
        i = locations[pair]
        pmask[i] = bool(row["poi_version_defined"])
        bmask[i] = bool(row["building_version_defined"])
        joint[i] = bool(row["joint_defined"])
        for col, mask, values in (("poi_within_region_midpercentile", pmask, poi),
                                  ("building_within_region_midpercentile", bmask, building)):
            value = row[col]
            if mask[i]:
                if value is None or not np.isfinite(float(value)):
                    raise ValueError(f"defined frozen rank {col} must be finite")
                values[i] = float(value)
            elif value is not None:
                raise ValueError(f"undefined frozen rank {col} must remain null")
    if seen != set(official) or not np.array_equal(joint, pmask & bmask):
        raise ValueError("rank artifact IDs or joint mask fail exact alignment")
    return {"poi_rank": poi, "building_rank": building, "poi_defined": pmask,
            "building_defined": bmask, "joint_defined": joint}


def load_pct_urban(workspace: Path, official: list[tuple[str, str]]) -> tuple[np.ndarray, dict[str, Any]]:
    values = np.full(len(official), np.nan, dtype=np.float64)
    positions = {pair: i for i, pair in enumerate(official)}
    manifest: dict[str, Any] = {}
    for region in REGIONS:
        path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"provided pct_urban table missing for {region}")
        table = pq.read_table(path, columns=["GEOID", "pct_urban"])
        seen: set[str] = set()
        for geoid, pct in zip(table.column("GEOID").to_pylist(), table.column("pct_urban").to_pylist()):
            geoid = str(geoid)
            pair = (geoid, region)
            if pair not in positions:
                continue
            if geoid in seen:
                raise ValueError(f"duplicate provided pct_urban GEOID {geoid}")
            seen.add(geoid)
            values[positions[pair]] = float(pct) if pct is not None else np.nan
        expected_ids = {g for g, r in official if r == region}
        if seen != expected_ids:
            raise ValueError(f"{region} pct_urban input must exactly cover official regional GEOIDs")
        idx = [positions[(g, region)] for g in expected_ids]
        manifest[region] = {"path": str(path), "sha256": sha256_file(path),
                            "columns_read": ["GEOID", "pct_urban"], "official_ids": len(seen),
                            "pct_urban_defined_ids": int(np.isfinite(values[idx]).sum())}
    if np.any(np.isfinite(values) & ((values < 0.) | (values > 1.))):
        raise ValueError("finite provided pct_urban values must be in [0,1]")
    return values, manifest


def output_table(official: list[tuple[str, str]], source: dict[str, np.ndarray],
                 gap: np.ndarray, pct_urban: np.ndarray) -> pa.Table:
    joint = source["joint_defined"]
    rural = np.isfinite(pct_urban) & (pct_urban < .5)
    urban = np.isfinite(pct_urban) & (pct_urban >= .5)
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "poi_within_region_midpercentile": pa.array([float(v) if ok else None for v, ok in zip(source["poi_rank"], source["poi_defined"])], type=pa.float64()),
        "building_within_region_midpercentile": pa.array([float(v) if ok else None for v, ok in zip(source["building_rank"], source["building_defined"])], type=pa.float64()),
        "joint_defined": pa.array(joint, type=pa.bool_()),
        "signed_poi_minus_building_rank_gap": pa.array([float(v) if ok else None for v, ok in zip(gap, joint)], type=pa.float64()),
        "pct_urban": pa.array([float(v) if np.isfinite(v) else None for v in pct_urban], type=pa.float64()),
        "rural_indicator": pa.array([bool(v) if known else None for v, known in zip(rural, np.isfinite(pct_urban))], type=pa.bool_()),
        "urban_indicator": pa.array([bool(v) if known else None for v, known in zip(urban, np.isfinite(pct_urban))], type=pa.bool_()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    rank_report = json.loads(RANK_REPORT.read_text(encoding="utf-8"))
    signed_report = json.loads(SIGNED_REPORT.read_text(encoding="utf-8"))
    if sha256_file(SIGNED_REPORT) != SIGNED_REPORT_SHA:
        raise ValueError("signed-asymmetry measure-before report hash changed")
    if (sha256_file(RANK_REPORT) != signed_report["provenance"]["source_rank_report"]["sha256"]
            or rank_report["provenance"]["output_artifact"]["sha256"] != RANK_ARTIFACT_SHA
            or signed_report["provenance"]["output_artifact"]["sha256"] != SIGNED_ARTIFACT_SHA):
        raise ValueError("rank source report/artifact hashes do not reconcile to the frozen signed screen")
    before_regions = signed_report["measure_after"]["regional_signed_means_and_ci95"]
    baseline_means_near_zero_ci_contains_zero = all(
        abs(before_regions[r]["mean_signed_gap_and_ci95"]["mean"]) < 1e-15
        and before_regions[r]["mean_signed_gap_and_ci95"]["ci95_percentile"][0] <= 0.
        <= before_regions[r]["mean_signed_gap_and_ci95"]["ci95_percentile"][1]
        for r in REGIONS)
    if (rank_report["scope"]["official_ids"] != EXPECTED_N
            or rank_report["scope"]["joint_defined_ids"] != 9377
            or signed_report["scope"]["joint_defined_ids"] != 9377
            or not baseline_means_near_zero_ci_contains_zero
            or not np.isclose(signed_report["measure_after"]["p95_absolute_gap"], .7056666666666667, rtol=0., atol=1e-12)):
        raise ValueError("frozen rank or signed-asymmetry measure-before differs from validated baseline")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N
            or len({g for g, _ in official}) != EXPECTED_N or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official scored sample must be exactly 9,379 unique GEOID-region rows")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    if official_sha != rank_report["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official ID-region partition differs from frozen rank audit")
    rank_path = workspace / "tm5-poi-building-version-discordance/version-discordance.parquet"
    signed_path = workspace / "tm5-signed-poi-building-version-asymmetry/signed-rank-gap.parquet"
    source = load_rank_source(rank_path, RANK_ARTIFACT_SHA, official)
    gap = signed_gap_from_ranks(source["poi_rank"], source["building_rank"], source["joint_defined"])
    joint = source["joint_defined"]
    if not np.array_equal(joint, source["poi_defined"] & source["building_defined"]):
        raise ValueError("frozen joint mask is not the exact source-channel mask intersection")
    signed_table = pq.read_table(signed_path, columns=["GEOID", "region", "joint_defined", "signed_poi_minus_building_rank_gap"])
    if sha256_file(signed_path) != SIGNED_ARTIFACT_SHA or signed_table.num_rows != EXPECTED_N:
        raise ValueError("frozen signed-gap artifact missing or hash differs from prior screen")
    signed_pairs = list(zip(signed_table.column("GEOID").to_pylist(), signed_table.column("region").to_pylist()))
    signed_joint = np.asarray(signed_table.column("joint_defined").to_pylist(), dtype=bool)
    signed_gap = np.asarray([float(v) if v is not None else np.nan for v in signed_table.column("signed_poi_minus_building_rank_gap").to_pylist()])
    signed_lookup = {pair: (bool(mask), float(value) if np.isfinite(value) else None)
                     for pair, mask, value in zip(signed_pairs, signed_joint, signed_gap)}
    if set(signed_lookup) != set(official) or len(signed_lookup) != EXPECTED_N:
        raise ValueError("frozen signed-gap artifact IDs differ from official sample")
    for i, pair in enumerate(official):
        mask, value = signed_lookup[pair]
        if mask != bool(joint[i]) or (mask and not np.isclose(value, gap[i], rtol=0., atol=1e-15)) or (not mask and value is not None):
            raise ValueError("recomputed signed gap differs from frozen signed-rank artifact")
    pct_urban, strata_manifest = load_pct_urban(workspace, official)
    summary = summarize_groups(official, gap, joint, pct_urban)
    bootstrap = bootstrap_contrasts(summary["groups"])
    selected_region = summary["max_region"]
    selected_ci = bootstrap["regional_signed_contrast_ci95_percentile"][selected_region]
    support_regions = summary["regions_meeting_support"]
    support_pass = len(support_regions) >= MIN_REGIONS_WITH_SUPPORT
    effect_pass = summary["max_absolute_contrast"] >= MIN_ABS_CONTRAST
    ci_excludes_zero = bool(selected_ci[0] > 0. or selected_ci[1] < 0.)
    pass_screen = bool(support_pass and effect_pass and ci_excludes_zero)
    registry_path = ROOT / "config/ideas.json"
    registry_before = sha256_file(registry_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registered_ids = {item.get("id") for item in registry.get("ideas", [])}
    if "tm5_signed_version_urbanity" in registered_ids:
        raise ValueError("this diagnostic must remain unregistered")
    table = output_table(official, source, gap, pct_urban)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("diagnostic Parquet serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    registry_after = sha256_file(registry_path)
    registry_unchanged = registry_after == registry_before
    if not registry_unchanged:
        raise AssertionError("idea registry changed during the diagnostic screen")
    acceptance = {
        "exact_official_id_and_joint_null_alignment": table.num_rows == EXPECTED_N and bool(np.array_equal(np.isfinite(gap), joint)),
        "joint_defined_ids": int(joint.sum()), "joint_coverage_fraction": float(joint.mean()),
        "regions_with_each_urbanity_group_n_at_least_30": support_regions,
        "at_least_two_regions_meet_group_support": bool(support_pass),
        "max_absolute_rural_minus_urban_mean_signed_gap_contrast": float(summary["max_absolute_contrast"]),
        "max_contrast_region": selected_region,
        "max_contrast_signed_direction": "rural_higher" if summary["max_region_signed_contrast"] > 0 else "urban_higher" if summary["max_region_signed_contrast"] < 0 else "equal",
        "maximum_absolute_contrast_at_least_0_05": bool(effect_pass),
        "diagnostic_remains_unregistered_and_registry_unchanged": bool(registry_unchanged and "tm5_signed_version_urbanity" not in registered_ids),
        "max_absolute_contrast_bootstrap_ci95": bootstrap["max_absolute_contrast_ci95_percentile"],
        "max_absolute_contrast_ci_excludes_zero": bootstrap["max_absolute_ci_excludes_zero"],
        "selected_region_signed_contrast_ci95": selected_ci,
        "selected_region_signed_contrast_ci_excludes_zero": ci_excludes_zero,
        "heterogeneity_screen_pass": pass_screen,
    }
    report = {
        "schema_version": 1,
        "screen": "Rural/urban contrast in signed within-region POI-minus-building p90 version midrank gaps; unregistered descriptive screen",
        "definition": "Reuse frozen within-region POI and building p90-version midpercentiles. For each jointly defined official GEOID, signed gap = POI midpercentile − building midpercentile. Within each region and provided urbanity stratum calculate the mean signed gap; contrast = rural mean − urban mean. Primary statistic = maximum absolute regional contrast.",
        "measure_before": {"four_unconditional_regional_signed_means": {r: signed_report["measure_after"]["regional_signed_means_and_ci95"][r]["mean_signed_gap_and_ci95"]["mean"] for r in REGIONS},
                           "four_unconditional_regional_ci95": {r: signed_report["measure_after"]["regional_signed_means_and_ci95"][r]["mean_signed_gap_and_ci95"]["ci95_percentile"] for r in REGIONS},
                           "source_signed_report_sha256": sha256_file(SIGNED_REPORT),
                           "source_rank_artifact_sha256": RANK_ARTIFACT_SHA,
                           "within_region_urbanity_contrast_measured": False},
        "method": {"rank_reuse": "Use validated prior within-region midranks and exact joint-defined mask; no re-ranking, recomputation from raw Tm5, or imputation.",
                   "urbanity": "Read only provided GEOID and pct_urban; rural is pct_urban<0.5, urban is pct_urban>=0.5. Missing pct_urban excluded from both strata.",
                   "bootstrap": "2,000 deterministic percentile-bootstrap draws; independently resample tracts with replacement within each region×stratum. Report regional signed-contrast CIs and the bootstrap percentile CI for the maximum absolute regional contrast.",
                   "selection_ci": "For the observed maximum-contrast region, report its signed-contrast bootstrap CI; acceptance requires that interval exclude zero, in addition to the preregistered magnitude and support gates.",
                   "seed": BOOTSTRAP_SEED, "unregistered": True,
                   "interpretation_limit": "Descriptive provenance-rank heterogeneity only; no target-error, accuracy, causal or feature-promotion claim."},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": official_sha,
                  "joint_defined_ids": int(joint.sum()), "joint_undefined_ids": int((~joint).sum()),
                  "urbanity_defined_joint_ids": int(np.count_nonzero(joint & np.isfinite(pct_urban))),
                  "joint_defined_without_urbanity": int(np.count_nonzero(joint & ~np.isfinite(pct_urban))),
                  "regions": summary["by_region"],
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_reference_data_read": False, "external_data_read": False,
                  "leaderboard_feedback_used": False, "registry_changes": not registry_unchanged, "feature_registration": False, "uploads": 0},
        "measure_after": {"maximum_absolute_rural_minus_urban_mean_signed_gap_contrast": float(summary["max_absolute_contrast"]),
                          "maximum_contrast_region": selected_region,
                          "maximum_contrast_signed_rural_minus_urban": float(summary["max_region_signed_contrast"]),
                          "max_contrast_ci95_percentile": bootstrap["max_absolute_contrast_ci95_percentile"],
                          "regional_signed_contrast_ci95_percentile": bootstrap["regional_signed_contrast_ci95_percentile"],
                          "bootstrap_replicates": bootstrap["replicates"], "bootstrap_seed": bootstrap["seed"]},
        "thresholds": {"minimum_group_support_inclusive": MIN_GROUP_N,
                       "minimum_regions_meeting_support_inclusive": MIN_REGIONS_WITH_SUPPORT,
                       "minimum_max_absolute_contrast_inclusive": MIN_ABS_CONTRAST,
                       "selected_region_signed_contrast_ci_excludes_zero": True},
        "acceptance": acceptance,
        "provenance": {"official_geoid_region_sha256": official_sha,
                       "frozen_rank_report": {"path": str(RANK_REPORT), "sha256": sha256_file(RANK_REPORT)},
                       "frozen_rank_artifact": {"path": str(rank_path), "sha256": sha256_file(rank_path),
                                                "sha256_expected": RANK_ARTIFACT_SHA,
                                                "columns_read": ["GEOID", "region", "poi_version_defined", "poi_within_region_midpercentile", "building_version_defined", "building_within_region_midpercentile", "joint_defined"]},
                       "frozen_signed_report": {"path": str(SIGNED_REPORT), "sha256": sha256_file(SIGNED_REPORT)},
                       "frozen_signed_artifact": {"path": str(signed_path), "sha256": sha256_file(signed_path), "columns_read": ["GEOID", "region", "joint_defined", "signed_poi_minus_building_rank_gap"]},
                       "strata_inputs": strata_manifest,
                       "registry_sha256_before_after": {"before": registry_before, "after": registry_after},
                       "output_artifact": {"path": str(artifact_path), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": table.num_rows, "columns": table.column_names,
                                           "deterministic_serialization": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "At least two regions have adequate support, and the maximum absolute rural/urban signed-gap contrast meets both the 0.05 magnitude gate and the selected-region CI criterion. Keep unregistered and descriptive only." if pass_screen else
                         "At least one preregistered support, 0.05 effect-size, or selected-region CI gate fails. Retain the absolute-discordance diagnostic only; this screen does not support an urbanity-specific signed feature."}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm5-signed-version-urbanity.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "tm5-signed-version-urbanity/signed-version-urbanity.parquet"
    result = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"acceptance": result["acceptance"], "measure_after": result["measure_after"],
                      "regions": result["scope"]["regions"],
                      "output_artifact": result["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
