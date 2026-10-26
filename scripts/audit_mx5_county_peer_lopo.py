#!/usr/bin/env python3
"""Leave-one-observed-donor-out stability audit for county-imputed Mx5 medians."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import official_scored_pairs  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_TOTAL = 9379
EXPECTED_OBSERVED = 8311
EXPECTED_COUNTY_IMPUTED = 1011
EXPECTED_REGION_IMPUTED = 57
MINIMUM_ORIGINAL_DONORS = 5
MINIMUM_REMAINING_DONORS = 4
MAX_P95_SHIFT = 0.10


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_mask(official: list[tuple[str, str]], mask: np.ndarray) -> str:
    values = np.asarray(mask, dtype=bool)
    if values.shape != (len(official),):
        raise ValueError("mask must align to official GEOID-region partition")
    text = "\n".join(f"{g},{r},{int(flag)}" for (g, r), flag in zip(official, values))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def county_median_lopo(donor_geoids: list[str], donor_values: np.ndarray,
                       baseline_values: np.ndarray,
                       *, minimum_remaining: int = MINIMUM_REMAINING_DONORS
                       ) -> tuple[np.ndarray, np.ndarray]:
    """Recompute a county's observed-donor median after deleting each donor once."""
    donor = np.asarray(donor_values, dtype=np.float64)
    base = np.asarray(baseline_values, dtype=np.float64)
    if (donor.ndim != 1 or base.ndim != 1 or len(donor_geoids) != donor.size
            or not base.size):
        raise ValueError("donor IDs and donor/recipient numeric values must align")
    if len(set(donor_geoids)) != len(donor_geoids):
        raise ValueError("donor GEOIDs must be unique")
    if not donor.size:
        raise ValueError("LOPO requires at least one donor and one county-imputed recipient")
    if minimum_remaining < 1 or donor.size - 1 < minimum_remaining:
        raise ValueError("each deletion must retain the required number of donors")
    if (not np.isfinite(donor).all() or np.any((donor < 0.0) | (donor > 1.0))
            or not np.isfinite(base).all() or np.any((base < 0.0) | (base > 1.0))):
        raise ValueError("donor and baseline shares must be finite and in [0,1]")
    full_median = float(np.median(donor))
    if not np.array_equal(base, np.full(base.size, full_median, dtype=np.float64)):
        raise ValueError("each recipient baseline must equal the original observed-donor county median")

    recipient_max = np.full(base.size, -np.inf, dtype=np.float64)
    deleted_values: list[float] = []
    for donor_index, _donor_geoid in enumerate(donor_geoids):
        remaining_values = np.concatenate((donor[:donor_index], donor[donor_index + 1:]))
        if remaining_values.size != donor.size - 1:
            raise AssertionError("donor deletion did not remove exactly one observed value")
        recomputed = float(np.median(remaining_values))
        shift = abs(recomputed - full_median)
        if not np.isfinite(shift) or shift > 1.0:
            raise AssertionError("absolute share shift must be finite and at most one")
        deleted_values.append(shift)
        recipient_max = np.maximum(recipient_max, shift)
    return recipient_max, np.repeat(np.asarray(deleted_values, dtype=np.float64), base.size)


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def detail_table(rows: list[dict[str, Any]]) -> pa.Table:
    if not rows:
        raise ValueError("no county-imputed recipient summaries to serialize")
    return pa.table({
        "GEOID": pa.array([r["GEOID"] for r in rows], type=pa.string()),
        "region": pa.array([r["region"] for r in rows], type=pa.string()),
        "COUNTYFP": pa.array([r["COUNTYFP"] for r in rows], type=pa.string()),
        "baseline_county_median": pa.array([r["baseline_county_median"] for r in rows], type=pa.float64()),
        "original_observed_donor_count": pa.array([r["original_observed_donor_count"] for r in rows], type=pa.int32()),
        "minimum_remaining_observed_donors": pa.array([r["minimum_remaining_observed_donors"] for r in rows], type=pa.int32()),
        "donor_deletion_scenarios": pa.array([r["donor_deletion_scenarios"] for r in rows], type=pa.int32()),
        "maximum_absolute_shift": pa.array([r["maximum_absolute_shift"] for r in rows], type=pa.float64()),
        "recipient_max_shift_at_most_0_10": pa.array([r["recipient_max_shift_at_most_0_10"] for r in rows], type=pa.bool_()),
    })


def run(workspace: Path, report_path: Path, detail_path: Path) -> dict[str, Any]:
    source_report_path = ROOT / "reports/mx5-county-peer-imputation.json"
    source_report_sha = sha256_file(source_report_path)
    source_report = json.loads(source_report_path.read_text(encoding="utf-8"))
    if source_report.get("audit") != "Pooled Mx5 county-peer median fallback: observed/imputed coverage sensitivity, not integration":
        raise ValueError("unexpected Mx5 county-imputation source audit")
    if source_report.get("scope", {}).get("imputed_ids") != EXPECTED_COUNTY_IMPUTED:
        raise ValueError("source audit no longer reports exactly 1,011 county imputations")
    if source_report.get("scope", {}).get("observed_defined_ids") != EXPECTED_OBSERVED:
        raise ValueError("source audit no longer reports 8,311 observed values")
    if source_report.get("method", {}).get("minimum_other_defined_peers") != MINIMUM_ORIGINAL_DONORS:
        raise ValueError("source county-imputation donor minimum changed")
    region_report_path = ROOT / "reports/mx5-region-fallback.json"
    region_report = json.loads(region_report_path.read_text(encoding="utf-8"))
    if region_report.get("measure_after", {}).get("region_imputed_ids") != EXPECTED_REGION_IMPUTED:
        raise ValueError("regional leave-one-county-out baseline no longer has 57 fallback values")
    if not region_report.get("acceptance", {}).get("acceptance_pass"):
        raise ValueError("regional fallback baseline no longer passes its stability audit")
    regional_loco_report_path = ROOT / "reports/mx5-region-fallback-loco.json"
    regional_loco = json.loads(regional_loco_report_path.read_text(encoding="utf-8"))
    regional_p95 = regional_loco["measure_after"]["p95_absolute_shift"]
    regional_max = regional_loco["measure_after"]["maximum_absolute_shift"]
    if regional_p95 > 0.10:
        raise ValueError("regional tier leave-one-county-out p95 no longer passes 0.10")

    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    # The canonical scored partition is the same ordered input bound by the source audit.
    if (len(official) != EXPECTED_TOTAL or len(set(official)) != EXPECTED_TOTAL
            or len({g for g, _ in official}) != EXPECTED_TOTAL
            or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official GEOID/region list is not an exact 9,379-ID partition")
    official_sha = hashlib.sha256(json.dumps(official, ensure_ascii=False,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
    if official_sha != source_report["inputs"]["official_geoid_region_sha256"]:
        raise ValueError("official GEOID/region partition differs from county-imputation audit")

    source_path = workspace / "mx5-county-peer-imputation" / "county-peer-mx5.parquet"
    expected_source_sha = source_report["output_artifact"]["sha256"]
    if sha256_file(source_path) != expected_source_sha:
        raise ValueError("county-tier artifact hash differs from completed county-imputation report")
    mx5_path = workspace / "ideas" / "Mx5.parquet"
    expected_mx5_sha = source_report["inputs"]["frozen_mx5_artifact"]["sha256"]
    if sha256_file(mx5_path) != expected_mx5_sha:
        raise ValueError("frozen Mx5 artifact hash differs from source report")
    registry_path = ROOT / "config/ideas.json"
    registry_sha_before = sha256_file(registry_path)

    cols = ["GEOID", "region", "COUNTYFP", "observed_share", "observed_defined",
            "county_peer_mx5_share", "defined", "imputed", "eligible_observed_peer_count"]
    table = pq.read_table(source_path, columns=cols)
    if table.num_rows != EXPECTED_TOTAL:
        raise ValueError("county-tier artifact row count differs from official partition")
    positions = {pair: i for i, pair in enumerate(official)}
    n = len(official)
    counties = [""] * n
    observed = np.full(n, np.nan, dtype=np.float64)
    observed_mask = np.zeros(n, dtype=bool)
    county_values = np.full(n, np.nan, dtype=np.float64)
    county_mask = np.zeros(n, dtype=bool)
    imputed = np.zeros(n, dtype=bool)
    source_peer_counts = np.zeros(n, dtype=np.int64)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in positions or pair in seen:
            raise ValueError(f"county-tier artifact has unexpected/duplicate official row {pair}")
        seen.add(pair)
        i = positions[pair]
        if row["COUNTYFP"] is None or not str(row["COUNTYFP"]).strip():
            raise ValueError(f"missing COUNTYFP for {pair[0]}")
        counties[i] = str(row["COUNTYFP"]).strip()
        observed_mask[i] = bool(row["observed_defined"])
        county_mask[i] = bool(row["defined"])
        imputed[i] = bool(row["imputed"])
        source_peer_counts[i] = int(row["eligible_observed_peer_count"])
        for key, arr, mask in (("observed_share", observed, observed_mask),
                               ("county_peer_mx5_share", county_values, county_mask)):
            raw = row[key]
            is_defined = bool(row["observed_defined"] if key == "observed_share" else row["defined"])
            if is_defined:
                if raw is None or not np.isfinite(float(raw)) or not 0.0 <= float(raw) <= 1.0:
                    raise ValueError(f"defined value {key} invalid for {pair[0]}")
                arr[i] = float(raw)
            elif raw is not None:
                raise ValueError(f"undefined value {key} must remain null for {pair[0]}")
    if seen != set(official):
        raise ValueError("county-tier artifact does not preserve exact official GEOID/region set")
    if np.any(observed_mask & (~county_mask | imputed)) or np.any(imputed & (observed_mask | ~county_mask)):
        raise ValueError("observed/county-imputed tier masks overlap or disagree")
    if not np.array_equal(county_values[observed_mask], observed[observed_mask]):
        raise ValueError("observed county-tier values do not preserve original observed Mx5 values")
    if int(observed_mask.sum()) != EXPECTED_OBSERVED or int(imputed.sum()) != EXPECTED_COUNTY_IMPUTED:
        raise ValueError("observed/county-imputed tier counts differ from frozen baseline")
    if int((~county_mask).sum()) != EXPECTED_REGION_IMPUTED:
        raise ValueError("county-tier artifact's remaining nulls differ from 57 regional fallbacks")
    if not np.all(source_peer_counts[imputed] >= MINIMUM_ORIGINAL_DONORS):
        raise ValueError("county-imputed rows do not retain the original five-donor criterion")

    # Confirm selected COUNTYFP values still match the prior provided-data provenance hashes.
    county_hashes: dict[str, str] = {}
    regions = [r for _, r in official]
    for region in REGIONS:
        entries = [(g, counties[i]) for i, (g, r) in enumerate(official) if r == region]
        county_hash = hashlib.sha256("\n".join(f"{g},{county}" for g, county in entries).encode()).hexdigest()
        county_hashes[region] = county_hash
        if county_hash != source_report["inputs"]["countyfp_selected_values_sha256_by_region"][region]:
            raise ValueError(f"{region} COUNTYFP assignment differs from original source audit")

    observed_mask_hash_before = hash_mask(official, observed_mask)
    county_mask_hash_before = hash_mask(official, imputed)
    defined_mask_hash_before = hash_mask(official, county_mask)
    recipient_summary: list[dict[str, Any]] = []
    scenario_shift_chunks: list[np.ndarray] = []
    county_summaries: list[dict[str, Any]] = []
    donor_counts_by_recipient: list[int] = []
    remaining_counts: list[int] = []
    for region in REGIONS:
        county_ids = sorted({counties[i] for i, r in enumerate(regions) if r == region})
        for county_id in county_ids:
            members = [i for i, (r, c) in enumerate(zip(regions, counties)) if r == region and c == county_id]
            donor_indices = [i for i in members if observed_mask[i]]
            recipients = [i for i in members if imputed[i]]
            if not recipients:
                continue
            if len(donor_indices) < MINIMUM_ORIGINAL_DONORS:
                raise ValueError(f"county {region}/{county_id}: imputed recipient has fewer than five observed donors")
            donor_geoids = [official[i][0] for i in donor_indices]
            recipient_geoids = [official[i][0] for i in recipients]
            donor_values = observed[donor_indices]
            baseline = county_values[recipients]
            if not np.all(baseline == float(np.median(donor_values))):
                raise ValueError(f"county {region}/{county_id}: imputed baseline is not observed-donor median")
            local_max, local_shifts = county_median_lopo(
                donor_geoids, donor_values, baseline,
                minimum_remaining=MINIMUM_REMAINING_DONORS)
            scenario_shift_chunks.append(local_shifts)
            donor_counts_by_recipient.extend([len(donor_indices)] * len(recipients))
            remaining_counts.append(len(donor_indices) - 1)
            shifts = local_shifts
            for geoid, max_shift in zip(recipient_geoids, local_max):
                recipient_summary.append({
                    "GEOID": geoid, "region": region, "COUNTYFP": county_id,
                    "original_observed_donor_count": len(donor_indices),
                    "minimum_remaining_observed_donors": len(donor_indices) - 1,
                    "donor_deletion_scenarios": len(donor_indices),
                    "baseline_county_median": float(np.median(donor_values)),
                    "maximum_absolute_shift": float(max_shift),
                    "recipient_max_shift_at_most_0_10": bool(max_shift <= MAX_P95_SHIFT),
                })
            county_summaries.append({
                "region": region, "COUNTYFP": county_id,
                "observed_donor_count": len(donor_indices), "county_imputed_recipient_count": len(recipients),
                "deletion_recipient_scenario_count": int(local_shifts.size),
                "minimum_remaining_observed_donors": len(donor_indices) - 1,
                "max_absolute_shift": float(max(shifts)),
                "p95_absolute_shift": float(np.quantile(np.asarray(shifts), .95)),
            })
    if len(recipient_summary) != EXPECTED_COUNTY_IMPUTED:
        raise ValueError("LOPO scenarios do not cover each and only county-imputed GEOID")
    shifts_all = np.concatenate(scenario_shift_chunks) if scenario_shift_chunks else np.asarray([], dtype=np.float64)
    if not shifts_all.size or not np.isfinite(shifts_all).all():
        raise ValueError("no finite donor-deletion shifts were measured")

    # Re-read hashes/masks to verify no frozen source or tier assignment changed in this audit.
    source_sha_after = sha256_file(source_path)
    mx5_sha_after = sha256_file(mx5_path)
    registry_sha_after = sha256_file(registry_path)
    observed_mask_hash_after = hash_mask(official, observed_mask.copy())
    county_mask_hash_after = hash_mask(official, imputed.copy())
    defined_mask_hash_after = hash_mask(official, county_mask.copy())
    masks_unchanged = (observed_mask_hash_before == observed_mask_hash_after
                       and county_mask_hash_before == county_mask_hash_after
                       and defined_mask_hash_before == defined_mask_hash_after)
    sources_unchanged = (source_sha_after == expected_source_sha and mx5_sha_after == expected_mx5_sha
                         and registry_sha_after == registry_sha_before)

    output = detail_table(recipient_summary)
    payload = parquet_bytes(output)
    if payload != parquet_bytes(output):
        raise ValueError("LOPO detail Parquet is not deterministic")
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    detail_path.write_bytes(payload)

    p95_shift = float(np.quantile(shifts_all, .95))
    max_shift = float(shifts_all.max())
    min_remaining = int(min(remaining_counts))
    p95_recipient_max = float(np.quantile([r["maximum_absolute_shift"] for r in recipient_summary], .95))
    acceptance = {
        "exact_9379_official_geoid_tier_partition": len(seen) == EXPECTED_TOTAL and len(recipient_summary) == EXPECTED_COUNTY_IMPUTED,
        "county_imputed_geoid_count_preserved": int(imputed.sum()) == EXPECTED_COUNTY_IMPUTED,
        "observed_values_and_tier_masks_unchanged": bool(masks_unchanged and sources_unchanged),
        "minimum_remaining_observed_donors_at_least_4": min_remaining >= MINIMUM_REMAINING_DONORS,
        "p95_absolute_peer_deletion_shift_at_most_0_10": p95_shift <= MAX_P95_SHIFT,
        "stable_county_median_tier": bool(masks_unchanged and sources_unchanged
                                          and min_remaining >= MINIMUM_REMAINING_DONORS
                                          and p95_shift <= MAX_P95_SHIFT),
    }
    report = {
        "schema_version": 1,
        "audit": "Mx5 county-median leave-one-observed-donor-tract-out stability; provided-data-only sensitivity audit",
        "method": {
            "population": "Each original county-imputed GEOID is held fixed as a recipient; for each originally observed donor in its region-local COUNTYFP, remove that donor once and recompute the median of the remaining original observed donor values. Imputed values never donate.",
            "metric": "Absolute difference between the original county median and recomputed median for each recipient × donor-deletion scenario; global p95/max are over all such scenarios. Report per-recipient maximum over its county's donor deletions.",
            "undefined_and_observed": "Only the 1,011 source county-imputed recipient IDs are measured. Original observed values, undefined values, exact official GEOIDs, and county-imputed tier assignment are not changed; regional-tier rows are not donors or recipients.",
            "median": "NumPy median of the finite original observed donor Mx5 shares; no labels or refitting.",
        },
        "measure_before": {
            "official_ids": EXPECTED_TOTAL,
            "observed_defined_ids": EXPECTED_OBSERVED,
            "county_imputed_ids": EXPECTED_COUNTY_IMPUTED,
            "minimum_original_observed_peers_per_county_imputation": MINIMUM_ORIGINAL_DONORS,
            "regional_tier_fallback_ids": EXPECTED_REGION_IMPUTED,
            "regional_tier_leave_one_county_out_p95_shift": regional_p95,
            "regional_tier_leave_one_county_out_max_shift": regional_max,
            "regional_tier_stability_report_sha256": sha256_file(regional_loco_report_path),
            "county_peer_imputation_report_sha256": source_report_sha,
        },
        "measure_after": {
            "county_imputed_recipients": len(recipient_summary),
            "donor_deletion_recipient_scenarios": int(shifts_all.size),
            "absolute_shift_distribution": {
                "n": int(shifts_all.size), "min": float(shifts_all.min()),
                "p50": float(np.quantile(shifts_all, .50)), "p95": p95_shift,
                "max": max_shift, "mean": float(shifts_all.mean()),
            },
            "recipient_maximum_shift_distribution": {
                "n": len(recipient_summary), "min": float(min(r["maximum_absolute_shift"] for r in recipient_summary)),
                "p50": float(np.quantile([r["maximum_absolute_shift"] for r in recipient_summary], .5)),
                "p95": p95_recipient_max,
                "max": float(max(r["maximum_absolute_shift"] for r in recipient_summary)),
            },
            "recipient_maximum_shift_over_0_10_count": int(sum(not r["recipient_max_shift_at_most_0_10"] for r in recipient_summary)),
            "recipient_maximum_shift_over_0_10_geoids": [r["GEOID"] for r in recipient_summary if not r["recipient_max_shift_at_most_0_10"]],
            "original_donor_count_distribution_by_recipient": {
                "min": int(min(donor_counts_by_recipient)),
                "p50": float(np.median(donor_counts_by_recipient)),
                "max": int(max(donor_counts_by_recipient)),
            },
            "minimum_remaining_observed_donor_count": min_remaining,
            "observed_values_preserved_exactly": bool(np.array_equal(county_values[observed_mask], observed[observed_mask])),
            "undefined_rows_preserved_as_null": bool(np.all(np.isnan(county_values[~county_mask]))),
            "observed_mask_sha256_before": observed_mask_hash_before,
            "observed_mask_sha256_after": observed_mask_hash_after,
            "county_imputed_mask_sha256_before": county_mask_hash_before,
            "county_imputed_mask_sha256_after": county_mask_hash_after,
            "county_defined_mask_sha256_before": defined_mask_hash_before,
            "county_defined_mask_sha256_after": defined_mask_hash_after,
            "county_summaries": county_summaries,
            "recipient_maximum_shifts": recipient_summary,
        },
        "scope": {
            "official_ids": EXPECTED_TOTAL, "exact_geoid_region_partition": True,
            "labels_read": False, "sample_score_values_read": False,
            "external_or_removed_reference_data_read": False,
            "leaderboard_feedback_used": False, "frozen_Mx5_modified": False,
            "registry_changed": False, "uploads": 0,
        },
        "thresholds": {"maximum_p95_absolute_shift": MAX_P95_SHIFT,
                       "minimum_original_observed_donors": MINIMUM_ORIGINAL_DONORS,
                       "minimum_donors_after_one_deletion": MINIMUM_REMAINING_DONORS},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_sha,
            "county_peer_input": {"path": str(source_path), "sha256_before": expected_source_sha,
                                   "sha256_after": source_sha_after},
            "frozen_mx5": {"path": str(mx5_path), "sha256_before": expected_mx5_sha,
                            "sha256_after": mx5_sha_after},
            "registry_sha256_before": registry_sha_before, "registry_sha256_after": registry_sha_after,
            "countyfp_selected_values_sha256_by_region": county_hashes,
            "regional_fallback_report_sha256": sha256_file(region_report_path),
            "source_report_sha256": source_report_sha,
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "detail_artifact": {"path": str(detail_path), "rows": output.num_rows,
                                "sha256": hashlib.sha256(payload).hexdigest(), "deterministic_bytes": True},
        },
        "interpretation": (f"County medians pass the registered global stability test (p95={p95_shift:.6g}; minimum remaining donors={min_remaining}); {sum(not r['recipient_max_shift_at_most_0_10'] for r in recipient_summary)} recipients nevertheless have a per-recipient maximum shift above 0.10 and are flagged as local sensitivity outliers. Retain this diagnostic separately without changing frozen Mx5."
                           if acceptance["stable_county_median_tier"] else
                           "County medians fail the registered donor-deletion stability test; mark the affected county imputations unstable and leave frozen Mx5 unchanged."),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/mx5-county-peer-lopo.json")
    parser.add_argument("--detail", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    detail_path = Path(args.detail) if args.detail else workspace / "mx5-county-peer-lopo" / "county-peer-deletion-shifts.parquet"
    report = run(workspace, report_path, detail_path)
    print(json.dumps({"acceptance": report["acceptance"], "measure_after": {
        key: report["measure_after"][key] for key in (
            "county_imputed_recipients", "donor_deletion_recipient_scenarios",
            "absolute_shift_distribution", "recipient_maximum_shift_distribution",
            "minimum_remaining_observed_donor_count")},
        "detail_artifact": report["provenance"]["detail_artifact"], "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
