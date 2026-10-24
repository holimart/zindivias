#!/usr/bin/env python3
"""Create a deterministic, explicitly unvalidated defined-flag estimate.

This is a rule-based screen, not a reconstruction of removed reference layers.
It consumes the official regional sample IDs and supplied strata tables only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
FLAGS = ("transport_defined", "building_defined", "poi_defined_fire",
         "poi_defined_ems", "poi_defined_schools", "poi_defined_cbp")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_strata(path: Path) -> dict[str, dict]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow required; run with `uv run --with pyarrow`") from exc
    table = pq.read_table(path, columns=["GEOID", "pop_total", "pct_urban",
                                        "usfs_BuildingCount_sum", "carbonplan_buildings"])
    records = table.to_pylist()
    result = {str(row["GEOID"]): row for row in records}
    if len(result) != len(records):
        raise ValueError(f"duplicate GEOID in strata table: {path}")
    return result


def estimate_flags(row: dict, use_building_proxy: bool = True) -> dict[str, int]:
    pop = row["pop_total"]
    urban = row["pct_urban"]
    if pop is None:
        raise ValueError("required population predictor is missing; refusing zero fill")
    pop = int(pop)
    urban = (float(urban) if urban is not None and math.isfinite(float(urban))
             else None)
    # A missing urban fraction contributes no positive urban evidence; it is
    # not imputed as 0. Population remains independently observed.
    def urban_at_least(threshold: float) -> bool:
        return urban is not None and urban >= threshold
    # Rule-based likelihood cutoffs only. They are not asserted to equal the
    # unavailable organiser-reference denominator-presence labels.
    flags = {
        "transport_defined": int(pop >= 1000 or urban_at_least(0.15)),
        "poi_defined_fire": int(pop >= 1000 or urban_at_least(0.35)),
        "poi_defined_ems": int(pop >= 1000 or urban_at_least(0.35)),
        "poi_defined_schools": int(pop >= 250 or urban_at_least(0.10)),
        "poi_defined_cbp": int(pop >= 500 or urban_at_least(0.20)),
    }
    if use_building_proxy:
        candidates = [row.get("usfs_BuildingCount_sum"), row.get("carbonplan_buildings")]
        observed = [float(value) for value in candidates
                    if value is not None and math.isfinite(float(value))]
        if observed:
            flags["building_defined"] = int(max(observed) > 0)
        else:
            # Missing source values are not treated as zero: use the separate
            # population fallback and report that source absence in audit.
            flags["building_defined"] = int(pop >= 1)
    else:
        flags["building_defined"] = int(pop >= 1000 or urban_at_least(0.15))
    # Do not emit an undefined composite when every heuristic misses. This is
    # an explicit conservative inclusion fallback, not evidence of a CBP count.
    if not any(flags.values()):
        flags["poi_defined_cbp"] = 1
    return flags


def estimate_components(flags: dict[str, int]) -> dict[str, float | int]:
    # Neutral component placeholders isolate and test the inclusion/divisor
    # contract. No score accuracy is claimed and no sample score is consumed.
    neutral = 0.5
    facility_names = ("poi_defined_fire", "poi_defined_ems", "poi_defined_schools")
    facility_values = [neutral for name in facility_names if flags[name] == 1]
    cbp_defined = flags["poi_defined_cbp"] == 1
    poi_halves = []
    if facility_values:
        poi_halves.append(sum(facility_values) / len(facility_values))
    if cbp_defined:
        poi_halves.append(neutral)
    poi_defined = int(bool(poi_halves))
    component_values = {
        "transport_gap_estimate": neutral,
        "building_gap_estimate": neutral,
        "poi_gap_estimate": sum(poi_halves) / len(poi_halves) if poi_halves else None,
    }
    included = [component_values[name] for name, flag in (
        ("transport_gap_estimate", flags["transport_defined"]),
        ("building_gap_estimate", flags["building_defined"]),
        ("poi_gap_estimate", poi_defined)) if flag and component_values[name] is not None]
    composite = sum(included) / len(included) if included else None
    return {**component_values, "poi_defined": poi_defined,
            "coverage_gap_score_estimate": composite,
            "defined_component_count": len(included)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True,
                        help="workspace directory holding official per-region samples and strata Parquets")
    parser.add_argument("--out", type=Path, default=Path("reports/defined-flag-audit.json"))
    args = parser.parse_args()
    rows = []
    per_region = {}
    input_hashes = {}
    for region in REGIONS:
        sample_path = args.data_root / f"{region}-sample-submission.csv"
        strata_path = args.data_root / f"{region}-strata-tract-table.parquet"
        if not sample_path.is_file() or not strata_path.is_file():
            raise FileNotFoundError(f"missing authorized inputs for {region}: {sample_path} / {strata_path}")
        input_hashes[str(sample_path.name)] = {"bytes": sample_path.stat().st_size,
                                               "sha256": file_sha256(sample_path)}
        input_hashes[str(strata_path.name)] = {"bytes": strata_path.stat().st_size,
                                               "sha256": file_sha256(strata_path)}
        strata = read_strata(strata_path)
        with sample_path.open("r", newline="", encoding="utf-8-sig") as stream:
            sample = list(csv.DictReader(stream))
        ids = [item.get("GEOID") for item in sample]
        if any(not geoid for geoid in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"missing/duplicate sample GEOID in {sample_path}")
        if not set(ids).issubset(strata):
            raise ValueError(f"{region}: {len(set(ids) - set(strata))} sample IDs absent from strata")
        region_rows = []
        for geoid in ids:
            feature_row = strata[geoid]
            flags = estimate_flags(feature_row)
            composite = estimate_components(flags)
            output = {"region": region, "GEOID": geoid, **flags, **composite,
                      "defined_flag_fallback": int(flags["poi_defined_cbp"] == 1 and
                                                    not any(value for name, value in flags.items()
                                                            if name != "poi_defined_cbp"))}
            # Include the exact component values used for the consistency check.
            assert all(value in (0, 1) for value in flags.values())
            region_rows.append(output)
        per_region[region] = audit_region(region_rows, strata, ids)
        per_region[region]["pct_urban_missing"] = sum(
            strata[geoid]["pct_urban"] is None for geoid in ids)
        rows.extend(region_rows)
    if len(rows) != 9379 or len({row["GEOID"] for row in rows}) != 9379:
        raise ValueError(f"expected 9,379 unique authoritative IDs, got {len(rows)}")
    report = {
        "method": "deterministic rule-based screening estimates; not reference labels and not accuracy validated",
        "feature_sources_used": ["provided regional strata tables: population/urbanization and building proxy fields"],
        "feature_sources_not_used": ["removed TIGER/Line, Microsoft building footprints, USGS facilities, CBP"],
        "no_accuracy_claim": True,
        "inputs": input_hashes,
        "feature_source_ablation_note": "The contrast is strata demographic-only fallback versus demographics plus supplied strata building proxies. Raw Overture geometry was not tract-aggregated in this diagnostic; no Overture-derived per-tract counts or accuracy claim are implied.",
        "gap_estimate_note": "0.5 neutral component placeholders isolate inclusion/divisor arithmetic; not scored predictions",
        "rules": {
            "transport_defined": "1 when pop_total >= 1000 OR pct_urban >= 0.15",
            "poi_defined_fire, poi_defined_ems": "1 when pop_total >= 1000 OR pct_urban >= 0.35",
            "poi_defined_schools": "1 when pop_total >= 250 OR pct_urban >= 0.10",
            "poi_defined_cbp": "1 when pop_total >= 500 OR pct_urban >= 0.20; if all component heuristics are 0, conservatively include CBP as an unresolved-composite fallback (not evidence of a count)",
            "building_defined": "1 when either observed supplied strata building proxy is >0; if both are null/non-finite, use pop_total >= 1 (never null-to-zero)",
            "missing_pct_urban": "missing urban fraction supplies no positive urban evidence; do not impute zero; other observed predictors remain usable",
            "poi_defined": "1 when either the facility half has at least one flagged type or the CBP half is flagged",
            "coverage_gap_score_estimate": "mean of flagged transport, building and POI component estimates; POI is mean of available facility and CBP halves",
        },
        "rows": len(rows),
        "duplicate_ids": 0,
        "flag_columns": list(FLAGS) + ["poi_defined"],
        "flag_domain": [0, 1],
        "null_counts": {name: sum(row[name] is None for row in rows)
                         for name in list(FLAGS) + ["poi_defined", "coverage_gap_score_estimate"]},
        "defined_flag_fallback_rows": sum(row["defined_flag_fallback"] for row in rows),
        "composite_max_abs_error": max((region["component_inclusion"]["mean_error_max_abs"]
                                         for region in per_region.values()), default=0.0),
        "per_region": per_region,
        "feature_source_ablations": ablation_summary(rows, args.data_root),
        "estimates": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Audited {len(rows)} authoritative IDs across {len(per_region)} regions; "
          f"maximum composite error {report['composite_max_abs_error']:.3g}; report: {args.out}")


def audit_region(rows: list[dict], strata: dict[str, dict], ids: list[str]) -> dict:
    flag_counts = {name: dict(sorted(Counter(row[name] for row in rows).items()))
                   for name in list(FLAGS) + ["poi_defined"]}
    max_error = 0.0
    for row in rows:
        included = [row[name] for name, flag in (
            ("transport_gap_estimate", row["transport_defined"]),
            ("building_gap_estimate", row["building_defined"]),
            ("poi_gap_estimate", row["poi_defined"])) if flag and row[name] is not None]
        expected = sum(included) / len(included) if included else None
        actual = row["coverage_gap_score_estimate"]
        error = abs(expected - actual) if expected is not None and actual is not None else 0.0
        max_error = max(max_error, error)
    return {"sample_rows": len(ids), "unique_sample_ids": len(set(ids)),
            "strata_rows": len(strata), "sample_ids_absent_from_strata": 0,
            "flag_counts": flag_counts,
            "null_counts": {name: sum(row[name] is None for row in rows)
                            for name in list(FLAGS) + ["poi_defined", "coverage_gap_score_estimate"]},
            "component_inclusion": {"all_rows_have_component_estimate": all(
                row["coverage_gap_score_estimate"] is not None for row in rows),
                "mean_error_max_abs": max_error}}


def ablation_summary(rows: list[dict], data_root: Path) -> dict:
    # The main estimates use population/urban fields and supplied building
    # proxy fields. This ablation isolates their contribution without labels.
    import pyarrow.parquet as pq
    result = {"main_demography_plus_strata_building_proxy": {},
              "demography_only_building_fallback": {}}
    for region in REGIONS:
        table = pq.read_table(data_root / f"{region}-strata-tract-table.parquet",
                              columns=["GEOID", "pop_total", "pct_urban"])
        basic = {str(item["GEOID"]): item for item in table.to_pylist()}
        region_rows = [row for row in rows if row["region"] == region]
        alternative = [estimate_flags(basic[row["GEOID"]], use_building_proxy=False)
                       for row in region_rows]
        result["main_demography_plus_strata_building_proxy"][region] = {
            name: sum(row[name] for row in region_rows) for name in list(FLAGS)}
        result["demography_only_building_fallback"][region] = {
            name: sum(row[name] for row in alternative) for name in list(FLAGS)}
    return result


if __name__ == "__main__":
    main()
