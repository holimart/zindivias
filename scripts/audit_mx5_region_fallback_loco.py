#!/usr/bin/env python3
"""Test regional Mx5 fallback medians for leave-one-donor-county-out sensitivity."""
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
from bias60.inputs import official_scored_pairs  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
MIN_REMAINING_DONORS = 20
MAX_ABS_SHIFT = 0.10
EXPECTED_FALLBACKS = 57


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def median(values: np.ndarray) -> float | None:
    x = np.asarray(values, dtype=np.float64)
    if x.ndim != 1:
        raise ValueError("median input must be a vector")
    if not x.size:
        return None
    if not np.isfinite(x).all() or np.any((x < 0.0) | (x > 1.0)):
        raise ValueError("donor values must be finite and in [0,1]")
    return float(np.median(x))


def leave_one_county_out(donor_geoids: list[str], donor_counties: list[str],
                         donor_values: np.ndarray, fallback_geoids: list[str],
                         fallback_values: np.ndarray, *,
                         minimum_donors: int = MIN_REMAINING_DONORS,
                         maximum_shift: float = MAX_ABS_SHIFT) -> list[dict[str, Any]]:
    """Recompute a region's fallback median after dropping each donor county entirely."""
    values = np.asarray(donor_values, dtype=np.float64)
    bases = np.asarray(fallback_values, dtype=np.float64)
    if len(donor_geoids) != len(donor_counties) or values.shape != (len(donor_geoids),):
        raise ValueError("donor GEOIDs, counties, and values must align")
    if len(set(donor_geoids)) != len(donor_geoids) or not fallback_geoids or len(set(fallback_geoids)) != len(fallback_geoids):
        raise ValueError("donor and fallback GEOIDs must be nonempty and unique")
    if bases.shape != (len(fallback_geoids),):
        raise ValueError("fallback GEOIDs and baseline values must align")
    if minimum_donors < 1 or not np.isfinite(maximum_shift) or maximum_shift < 0.0:
        raise ValueError("minimum donors and maximum shift must be valid")
    if not np.isfinite(values).all() or np.any((values < 0.0) | (values > 1.0)):
        raise ValueError("original observed donor values must be finite and in [0,1]")
    if not np.isfinite(bases).all() or np.any((bases < 0.0) | (bases > 1.0)):
        raise ValueError("fallback baseline values must be finite and in [0,1]")
    county = np.asarray([str(value).strip() for value in donor_counties], dtype=object)
    if any(not value for value in county):
        raise ValueError("every observed donor needs a COUNTYFP")
    full_median = median(values)
    if full_median is None or not np.all(bases == full_median):
        raise ValueError("fallback baseline must equal median of original observed donors")

    rows: list[dict[str, Any]] = []
    for county_id in sorted(set(county.tolist())):
        keep = county != county_id
        remaining = int(keep.sum())
        recomputed = median(values[keep]) if remaining else None
        count_pass = remaining >= minimum_donors
        shift = abs(recomputed - full_median) if recomputed is not None else None
        shift_pass = shift is not None and shift <= maximum_shift
        for geoid, baseline in zip(fallback_geoids, bases):
            rows.append({
                "GEOID": str(geoid), "dropped_COUNTYFP": county_id,
                "baseline_region_median": float(baseline),
                "original_observed_donor_count": int(values.size),
                "dropped_observed_donor_count": int((~keep).sum()),
                "remaining_observed_donor_count": remaining,
                "recomputed_region_median": recomputed,
                "absolute_shift": shift,
                "at_least_20_observed_donors_remain": count_pass,
                "absolute_shift_at_most_0_10": shift_pass,
                "stable_recomputation": bool(count_pass and shift_pass),
            })
    return rows


def summarize(values: np.ndarray) -> dict[str, Any]:
    x = np.asarray(values, dtype=np.float64)
    x = x[np.isfinite(x)]
    if not x.size:
        return {"n": 0, "min": None, "p50": None, "p95": None, "max": None, "mean": None}
    return {"n": int(x.size), "min": float(x.min()), "p50": float(np.quantile(x, .5)),
            "p95": float(np.quantile(x, .95)), "max": float(x.max()), "mean": float(x.mean())}


def output_table(rows: list[dict[str, Any]]) -> pa.Table:
    if not rows:
        raise ValueError("leave-one-county-out artifact requires at least one deletion result")
    keys = ("GEOID", "region", "dropped_COUNTYFP", "baseline_region_median", "original_observed_donor_count",
            "dropped_observed_donor_count", "remaining_observed_donor_count", "recomputed_region_median",
            "absolute_shift", "at_least_20_observed_donors_remain", "absolute_shift_at_most_0_10",
            "stable_recomputation")
    if any(any(key not in row for key in keys) for row in rows):
        raise ValueError("LOCO rows are missing declared fields")
    return pa.table({
        "GEOID": pa.array([row["GEOID"] for row in rows], type=pa.string()),
        "region": pa.array([row["region"] for row in rows], type=pa.string()),
        "dropped_COUNTYFP": pa.array([row["dropped_COUNTYFP"] for row in rows], type=pa.string()),
        "baseline_region_median": pa.array([row["baseline_region_median"] for row in rows], type=pa.float64()),
        "original_observed_donor_count": pa.array([row["original_observed_donor_count"] for row in rows], type=pa.int32()),
        "dropped_observed_donor_count": pa.array([row["dropped_observed_donor_count"] for row in rows], type=pa.int32()),
        "remaining_observed_donor_count": pa.array([row["remaining_observed_donor_count"] for row in rows], type=pa.int32()),
        "recomputed_region_median": pa.array([row["recomputed_region_median"] for row in rows], type=pa.float64()),
        "absolute_shift": pa.array([row["absolute_shift"] for row in rows], type=pa.float64()),
        "at_least_20_observed_donors_remain": pa.array([row["at_least_20_observed_donors_remain"] for row in rows], type=pa.bool_()),
        "absolute_shift_at_most_0_10": pa.array([row["absolute_shift_at_most_0_10"] for row in rows], type=pa.bool_()),
        "stable_recomputation": pa.array([row["stable_recomputation"] for row in rows], type=pa.bool_()),
    })


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def read_strata(workspace: Path, official: list[tuple[str, str]]) -> tuple[np.ndarray, dict[str, str]]:
    positions = {pair: i for i, pair in enumerate(official)}
    county = np.full(len(official), "", dtype=object)
    seen: set[tuple[str, str]] = set()
    input_hashes: dict[str, str] = {}
    for region in REGIONS:
        path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        if not path.is_file():
            raise FileNotFoundError(path)
        input_hashes[region] = sha256_file(path)
        table = pq.read_table(path, columns=["GEOID", "COUNTYFP"])
        for row in table.to_pylist():
            pair = (str(row["GEOID"]), region)
            if pair not in positions:  # Some strata tables include additional non-scored rows.
                continue
            if pair in seen:
                raise ValueError(f"duplicate official COUNTYFP row {pair}")
            if row["COUNTYFP"] is None:
                raise ValueError(f"missing COUNTYFP for official GEOID {pair[0]}")
            county[positions[pair]] = str(row["COUNTYFP"]).strip()
            seen.add(pair)
    if seen != set(official):
        raise ValueError("COUNTYFP rows do not exactly match official GEOID/region partition")
    return county, input_hashes


def run(workspace: Path, report_path: Path, detail_path: Path) -> dict[str, Any]:
    source_report_path = ROOT / "reports/mx5-region-fallback.json"
    source_report = json.loads(source_report_path.read_text(encoding="utf-8"))
    if source_report.get("audit") != "Pooled Mx5 hierarchical region-median fallback after county imputation; label-free sensitivity, not integration":
        raise ValueError("unexpected prior Mx5 region-fallback report")
    if not source_report.get("acceptance", {}).get("acceptance_pass"):
        raise ValueError("prior region fallback must pass its frozen coverage/value gates")
    if source_report["measure_after"]["region_imputed_ids"] != EXPECTED_FALLBACKS:
        raise ValueError("prior region fallback no longer has exactly 57 imputed values")
    if (source_report["measure_after"]["final_defined_ids"] != 9379
            or source_report["measure_after"]["final_coverage_fraction"] != 1.0):
        raise ValueError("prior region fallback no longer has complete official-ID coverage")
    official = official_scored_pairs(workspace)
    if len(official) != 9379 or len(set(official)) != 9379 or len({g for g, _ in official}) != 9379:
        raise ValueError("official GEOID/region list must be an exact 9,379-ID partition")
    if set(region for _, region in official) != set(REGIONS):
        raise ValueError("official GEOID list has missing or unexpected regions")
    official_sha = hashlib.sha256(json.dumps(official, ensure_ascii=False,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
    if official_sha != source_report["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official GEOID/region hash differs from source fallback audit")
    position = {pair: i for i, pair in enumerate(official)}
    regions = [r for _, r in official]
    county, strata_hashes = read_strata(workspace, official)

    prior_path = workspace / "mx5-region-fallback" / "region-fallback-mx5.parquet"
    prior_sha = sha256_file(prior_path)
    if prior_sha != source_report["output_artifact"]["sha256"]:
        raise ValueError("regional fallback artifact hash differs from its report")
    prior_table = pq.read_table(prior_path, columns=["GEOID", "region", "observed_share", "observed_defined",
                                                     "county_peer_share", "county_defined", "county_imputed",
                                                     "region_median_if_imputed", "region_observed_donor_count",
                                                     "region_imputed", "final_share", "final_defined"])
    if prior_table.num_rows != len(official):
        raise ValueError("source fallback artifact row count differs from official partition")
    fallback_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for row in prior_table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in position or pair in fallback_rows:
            raise ValueError(f"source fallback artifact has duplicate/out-of-scope pair {pair}")
        fallback_rows[pair] = row
    if set(fallback_rows) != set(official):
        raise ValueError("source fallback artifact does not match exact official ID partition")
    fallback_pairs = [pair for pair in official if bool(fallback_rows[pair]["region_imputed"])]
    if len(fallback_pairs) != EXPECTED_FALLBACKS:
        raise ValueError(f"expected exactly 57 region-imputed rows, found {len(fallback_pairs)}")

    county_artifact_path = workspace / "mx5-county-peer-imputation" / "county-peer-mx5.parquet"
    expected_county_sha = source_report["provenance"]["county_peer_artifact"]["sha256"]
    county_sha = sha256_file(county_artifact_path)
    if county_sha != expected_county_sha:
        raise ValueError("county-peer source artifact hash differs from region-fallback audit")
    county_table = pq.read_table(county_artifact_path, columns=["GEOID", "region", "observed_share", "observed_defined"])
    if county_table.num_rows != len(official):
        raise ValueError("original-observation donor artifact has unexpected row count")
    observed: dict[tuple[str, str], tuple[float | None, bool]] = {}
    for row in county_table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in position or pair in observed:
            raise ValueError(f"original Mx5 donor artifact has duplicate/out-of-scope pair {pair}")
        defined = bool(row["observed_defined"])
        value = float(row["observed_share"]) if defined and row["observed_share"] is not None else None
        if defined and (value is None or not np.isfinite(value) or not 0.0 <= value <= 1.0):
            raise ValueError(f"invalid originally observed donor value for {pair[0]}")
        if not defined and row["observed_share"] is not None:
            raise ValueError(f"undefined original Mx5 value is not null for {pair[0]}")
        observed[pair] = (value, defined)
    if set(observed) != set(official):
        raise ValueError("original observation donor artifact fails official-ID reconciliation")

    # Assert hierarchy provenance: observed and county tiers exactly match the prior artifact.
    for pair in official:
        prior = fallback_rows[pair]
        original_value, original_defined = observed[pair]
        if bool(prior["observed_defined"]) != original_defined:
            raise ValueError(f"observed mask changed at {pair[0]}")
        if original_defined and float(prior["observed_share"]) != original_value:
            raise ValueError(f"observed value changed at {pair[0]}")
        if bool(prior["county_imputed"]) != (bool(prior["county_defined"]) and not original_defined):
            raise ValueError(f"county imputation tier is inconsistent at {pair[0]}")

    per_region: dict[str, Any] = {}
    all_loco_rows: list[dict[str, Any]] = []
    id_summaries: list[dict[str, Any]] = []
    unstable_regions: set[str] = set()
    for region in REGIONS:
        donors = [pair for pair in official if pair[1] == region and observed[pair][1]]
        donor_values = np.asarray([float(observed[pair][0]) for pair in donors], dtype=np.float64)
        donor_counties = [str(county[position[pair]]).strip() for pair in donors]
        if any(not c for c in donor_counties):
            raise ValueError(f"{region}: original observed donors have missing COUNTYFP")
        targets = [pair for pair in fallback_pairs if pair[1] == region]
        if not targets:
            per_region[region] = {"official_ids": regions.count(region), "fallback_geoids": [],
                                  "region_imputed_ids": 0, "original_observed_donor_ids": len(donors),
                                  "donor_counties": len(set(donor_counties)), "loco_recomputations": 0,
                                  "loco_shift_distribution": summarize(np.asarray([], dtype=np.float64)),
                                  "minimum_remaining_observed_donors": None,
                                  "all_recomputations_retain_at_least_20_donors": True,
                                  "all_recomputations_shift_at_most_0_10": True}
            continue
        full_median = median(donor_values)
        bases = np.asarray([float(fallback_rows[pair]["region_median_if_imputed"]) for pair in targets])
        if full_median is None or not np.all(bases == full_median):
            raise ValueError(f"{region}: stored fallback values do not equal observed-donor median")
        rows = leave_one_county_out([pair[0] for pair in donors], donor_counties, donor_values,
                                    [pair[0] for pair in targets], bases)
        all_loco_rows.extend([{**row, "region": region} for row in rows])
        shifts = np.asarray([row["absolute_shift"] if row["absolute_shift"] is not None else np.nan
                             for row in rows], dtype=np.float64)
        remaining = [int(row["remaining_observed_donor_count"]) for row in rows]
        all_counts_pass = all(row["at_least_20_observed_donors_remain"] for row in rows)
        all_shifts_pass = all(row["absolute_shift_at_most_0_10"] for row in rows)
        if not all_counts_pass or not all_shifts_pass:
            unstable_regions.add(region)
        per_region[region] = {
            "official_ids": regions.count(region), "region_imputed_ids": len(targets),
            "fallback_geoids": [pair[0] for pair in targets],
            "full_observed_donor_count": int(len(donors)),
            "donor_counties": int(len(set(donor_counties))),
            "full_region_median": full_median,
            "loco_recomputations": len(set(donor_counties)),
            "loco_row_shifts": summarize(shifts),
            "minimum_remaining_observed_donors": min(remaining) if remaining else None,
            "all_recomputations_retain_at_least_20_donors": all_counts_pass,
            "all_recomputations_shift_at_most_0_10": all_shifts_pass,
            "unstable": bool(region in unstable_regions),
        }
        for target_index, pair in enumerate(targets):
            target_rows = [row for row in rows if row["GEOID"] == pair[0]]
            target_shifts = np.asarray([row["absolute_shift"] if row["absolute_shift"] is not None else np.nan
                                        for row in target_rows], dtype=np.float64)
            max_i = int(np.nanargmax(target_shifts)) if np.isfinite(target_shifts).any() else None
            base = float(bases[target_index])
            stable = bool(target_rows and all(row["stable_recomputation"] for row in target_rows))
            id_summaries.append({
                "GEOID": pair[0], "region": region, "COUNTYFP": str(county[position[pair]]),
                "baseline_fallback_value": base,
                "original_observed_donor_count": len(donors),
                "original_observed_donor_counties": len(set(donor_counties)),
                "loco_recomputations": len(target_rows),
                "absolute_shift_p95": float(np.nanquantile(target_shifts, .95)) if np.isfinite(target_shifts).any() else None,
                "absolute_shift_max": float(np.nanmax(target_shifts)) if np.isfinite(target_shifts).any() else None,
                "max_shift_dropped_COUNTYFP": target_rows[max_i]["dropped_COUNTYFP"] if max_i is not None else None,
                "minimum_remaining_observed_donors": min(row["remaining_observed_donor_count"] for row in target_rows),
                "stable_under_every_county_deletion": stable,
                "restored_to_null_if_unstable": not stable,
            })

    shift_all = np.asarray([row["absolute_shift"] if row["absolute_shift"] is not None else np.nan
                            for row in all_loco_rows], dtype=np.float64)
    finite_shifts = shift_all[np.isfinite(shift_all)]
    p95_shift = float(np.quantile(finite_shifts, .95)) if finite_shifts.size else None
    max_shift = float(finite_shifts.max()) if finite_shifts.size else None
    min_remaining = min((row["remaining_observed_donor_count"] for row in all_loco_rows), default=None)
    all_donors_pass = bool(all(row["at_least_20_observed_donors_remain"] for row in all_loco_rows))
    p95_pass = p95_shift is not None and p95_shift <= MAX_ABS_SHIFT
    overall_stable = bool(p95_pass and all_donors_pass)

    # A separate sensitivity output preserves the observed/county tiers, but withholds
    # fallback values from any region whose own LOCO deletions breach either gate.
    output_rows: list[dict[str, Any]] = []
    fallback_pair_set = set(fallback_pairs)
    stable_by_region = {region: region not in unstable_regions for region in REGIONS}
    stable_count = 0
    restored_count = 0
    for pair in official:
        row = fallback_rows[pair]
        observed_flag = bool(row["observed_defined"])
        county_flag = bool(row["county_defined"])
        region_flag = bool(row["region_imputed"])
        preserve_higher = observed_flag or (county_flag and not observed_flag)
        if region_flag and pair in fallback_pair_set and stable_by_region[pair[1]]:
            candidate_value = float(row["final_share"])
            candidate_defined = True
            restored_null = False
            stable_count += 1
        elif region_flag:
            candidate_value = None
            candidate_defined = False
            restored_null = True
            restored_count += 1
        else:
            candidate_value = float(row["final_share"]) if bool(row["final_defined"]) else None
            candidate_defined = bool(row["final_defined"])
            restored_null = False
        if preserve_higher and (candidate_defined != bool(row["final_defined"])
                                or candidate_value != float(row["final_share"])):
            raise AssertionError("observed/county tiers must remain exactly preserved")
        output_rows.append({
            "GEOID": pair[0], "region": pair[1],
            "observed_share": row["observed_share"], "observed_defined": observed_flag,
            "county_peer_share": row["county_peer_share"], "county_defined": county_flag,
            "county_imputed": bool(row["county_imputed"]),
            "original_region_fallback_share": row["region_median_if_imputed"],
            "region_fallback_stable": bool(region_flag and stable_by_region[pair[1]]),
            "region_fallback_restored_null": restored_null,
            "sensitivity_final_share": candidate_value,
            "sensitivity_final_defined": candidate_defined,
        })
    output_table_sensitivity = pa.table({
        "GEOID": pa.array([r["GEOID"] for r in output_rows], type=pa.string()),
        "region": pa.array([r["region"] for r in output_rows], type=pa.string()),
        "observed_share": pa.array([r["observed_share"] for r in output_rows], type=pa.float64()),
        "observed_defined": pa.array([r["observed_defined"] for r in output_rows], type=pa.bool_()),
        "county_peer_share": pa.array([r["county_peer_share"] for r in output_rows], type=pa.float64()),
        "county_defined": pa.array([r["county_defined"] for r in output_rows], type=pa.bool_()),
        "county_imputed": pa.array([r["county_imputed"] for r in output_rows], type=pa.bool_()),
        "original_region_fallback_share": pa.array([r["original_region_fallback_share"] for r in output_rows], type=pa.float64()),
        "region_fallback_stable": pa.array([r["region_fallback_stable"] for r in output_rows], type=pa.bool_()),
        "region_fallback_restored_null": pa.array([r["region_fallback_restored_null"] for r in output_rows], type=pa.bool_()),
        "sensitivity_final_share": pa.array([r["sensitivity_final_share"] for r in output_rows], type=pa.float64()),
        "sensitivity_final_defined": pa.array([r["sensitivity_final_defined"] for r in output_rows], type=pa.bool_()),
    })
    if output_table_sensitivity.num_rows != len(official):
        raise AssertionError("sensitivity output must preserve exact official IDs")
    payload = parquet_bytes(output_table_sensitivity)
    if payload != parquet_bytes(output_table_sensitivity):
        raise ValueError("sensitivity Parquet bytes are nondeterministic")
    detail_table = output_table(all_loco_rows)
    detail_payload = parquet_bytes(detail_table)
    if detail_payload != parquet_bytes(detail_table):
        raise ValueError("LOCO detail Parquet bytes are nondeterministic")
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    detail_path.write_bytes(detail_payload)
    sensitivity_path = workspace / "mx5-region-fallback-loco" / "fallback-stability-mx5.parquet"
    sensitivity_path.parent.mkdir(parents=True, exist_ok=True)
    sensitivity_path.write_bytes(payload)

    frozen_path = workspace / "ideas" / "Mx5.parquet"
    frozen_before = sha256_file(frozen_path)
    if frozen_before != source_report["provenance"]["frozen_Mx5_artifact"]["sha256"]:
        raise ValueError("frozen Mx5 hash differs from previous fallback audit")
    registry_path = ROOT / "config/ideas.json"
    registry_sha_before = sha256_file(registry_path)
    frozen_after = sha256_file(frozen_path)
    source_after = sha256_file(prior_path)
    county_after = sha256_file(county_artifact_path)
    registry_sha_after = sha256_file(registry_path)
    if frozen_before != frozen_after or prior_sha != source_after or county_sha != county_after or registry_sha_before != registry_sha_after:
        raise AssertionError("read-only screen modified frozen Mx5, prior tiers, county inputs, or registry")

    observed_ids = sum(bool(fallback_rows[pair]["observed_defined"]) for pair in official)
    county_imputed_ids = sum(bool(fallback_rows[pair]["county_imputed"]) for pair in official)
    original_nulls = sum(not bool(fallback_rows[pair]["final_defined"]) for pair in official)
    tier_preserved = all(
        output_rows[i]["observed_share"] == fallback_rows[pair]["observed_share"]
        and output_rows[i]["county_peer_share"] == fallback_rows[pair]["county_peer_share"]
        and output_rows[i]["observed_defined"] == bool(fallback_rows[pair]["observed_defined"])
        and output_rows[i]["county_defined"] == bool(fallback_rows[pair]["county_defined"])
        for i, pair in enumerate(official))
    acceptance = {
        "exact_9379_official_geoid_region_partition": len(official) == 9379 and len(output_rows) == 9379,
        "exactly_57_original_region_fallback_values_audited": len(fallback_pairs) == EXPECTED_FALLBACKS,
        "p95_absolute_leave_one_county_out_shift_at_most_0_10": p95_pass,
        "every_recomputation_retains_at_least_20_observed_donors": all_donors_pass,
        "observed_and_county_tiers_preserved": tier_preserved,
        "unstable_region_fallbacks_restored_to_null": restored_count == sum(
            len([pair for pair in fallback_pairs if pair[1] == region]) for region in unstable_regions),
        "frozen_Mx5_prior_tiers_and_registry_unchanged": frozen_before == frozen_after and prior_sha == source_after and registry_sha_before == registry_sha_after,
    }
    acceptance["acceptance_pass"] = all(acceptance.values())
    # Per-ID records make the 57 GEOIDs and their stress tests auditable in the JSON report.
    report = {
        "schema_version": 1,
        "audit": "Mx5 regional fallback leave-one-donor-county-out stability; label-free sensitivity, not integration",
        "method": {
            "candidate": "The 57 already identified regional fallback rows only; recompute their same-region median from original observed pooled-Mx5 shares after excluding every observed donor in one COUNTYFP at a time.",
            "donors": "Only `observed_defined=true` original values from the source county-peer artifact; county-peer-imputed and regional-fallback values never donate.",
            "stability_unit": "One scenario per (fallback GEOID, dropped donor COUNTYFP); regional fallback targets share their region's recomputed median.",
            "p95": "NumPy linear p95 over all finite absolute shifts across the 57 fallback GEOIDs × all donor-county deletions in their region.",
            "unstable_handling": "In the separate sensitivity output, preserve observed and county tiers exactly; restore a region fallback to null if any donor-county deletion leaves fewer than 20 donors or shifts its regional median by more than 0.10.",
            "not_integrated": True,
        },
        "measure_before": {
            "official_ids": 9379, "full_fallback_coverage": 9379,
            "region_fallback_ids": EXPECTED_FALLBACKS,
            "original_observed_donors_minimum_per_region": min(
                source_report["measure_after"]["regions"][r]["observed_donor_count"] for r in REGIONS),
            "minimum_donors_required_in_previous_fallback": source_report["acceptance_thresholds"]["minimum_other_observed_region_peers"],
            "frozen_Mx5_sha256": source_report["provenance"]["frozen_Mx5_artifact"]["sha256"],
        },
        "scope": {
            "official_ids": len(official), "exact_id_partition": True,
            "region_fallback_ids_tested": len(fallback_pairs),
            "original_observed_ids": int(observed_ids), "county_imputed_ids": int(county_imputed_ids),
            "previous_remaining_null_ids": int(original_nulls),
            "labels_read": False, "sample_score_values_read": False,
            "external_or_removed_reference_data_read": False, "leaderboard_feedback_used": False,
            "frozen_Mx5_modified": False, "registry_integration": False, "uploads": 0,
        },
        "thresholds": {"p95_absolute_shift_maximum": MAX_ABS_SHIFT,
                       "minimum_observed_donors_after_each_county_deletion": MIN_REMAINING_DONORS},
        "measure_after": {
            "loco_recomputation_rows": len(all_loco_rows),
            "overall_absolute_shift_distribution": summarize(finite_shifts),
            "p95_absolute_shift": p95_shift, "maximum_absolute_shift": max_shift,
            "minimum_remaining_observed_donors": min_remaining,
            "all_recomputations_retain_at_least_20_observed_donors": all_donors_pass,
            "all_57_values_stable": overall_stable,
            "stable_region_fallback_ids": stable_count,
            "restored_null_region_fallback_ids": restored_count,
            "regions": per_region,
            "fallback_geoid_details": id_summaries,
        },
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_sha,
            "source_region_fallback_report_sha256": sha256_file(source_report_path),
            "source_region_fallback_artifact": {"path": str(prior_path), "sha256": prior_sha,
                                                 "sha256_after": source_after},
            "original_observed_county_peer_artifact": {"path": str(county_artifact_path),
                                                        "sha256": county_sha, "sha256_after": county_after},
            "frozen_Mx5_artifact": {"path": str(frozen_path), "sha256": frozen_before,
                                    "sha256_after": frozen_after},
            "county_inputs": {region: {"path": str(workspace / "defined-flags-inputs" /
                                                         f"{region}-strata-tract-table.parquet"),
                                       "sha256": digest, "columns_read": ["GEOID", "COUNTYFP"]}
                              for region, digest in strata_hashes.items()},
            "registry_sha256_before": registry_sha_before, "registry_sha256_after": registry_sha_after,
            "loco_detail_artifact": {"path": str(detail_path), "rows": detail_table.num_rows,
                                     "sha256": sha256_file(detail_path), "columns": detail_table.column_names,
                                     "deterministic_bytes": True},
            "sensitivity_artifact": {"path": str(sensitivity_path), "rows": output_table_sensitivity.num_rows,
                                     "sha256": sha256_file(sensitivity_path),
                                     "columns": output_table_sensitivity.column_names,
                                     "deterministic_bytes": True},
            "script_sha256": sha256_file(Path(__file__).resolve()),
        },
        "interpretation": "The regional fallback meets the LOCO robustness criteria; preserve its 57 medians only as a separately reviewed sensitivity, with observed/county tiers unchanged and no automatic Mx5 integration." if acceptance["acceptance_pass"] else
        "The regional fallback fails at least one LOCO robustness criterion. Restore unstable regional fallback values to null in this separate sensitivity view; preserve original observed/county tiers and frozen Mx5 unchanged.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/mx5-region-fallback-loco.json")
    parser.add_argument("--detail-artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    detail_path = Path(args.detail_artifact) if args.detail_artifact else workspace / "mx5-region-fallback-loco" / "county-deletion-shifts.parquet"
    report = run(workspace, report_path, detail_path)
    print(json.dumps({"acceptance": report["acceptance"],
                      "measure_after": {key: report["measure_after"][key] for key in (
                          "loco_recomputation_rows", "overall_absolute_shift_distribution",
                          "minimum_remaining_observed_donors", "stable_region_fallback_ids",
                          "restored_null_region_fallback_ids")},
                      "artifacts": {"details": report["provenance"]["loco_detail_artifact"],
                                    "sensitivity": report["provenance"]["sensitivity_artifact"]},
                      "report": str(report_path.relative_to(ROOT))}, indent=2, sort_keys=True))
    return 0 if report["acceptance"]["acceptance_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
