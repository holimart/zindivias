#!/usr/bin/env python3
"""Screen leave-one-tract-out population-weighted Mx4 county context."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_COUNTS = {"maricopa-az": 1593, "northern-ca": 591,
                   "eastern-ok": 1192, "south-central-tx": 6003}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def distribution(values: np.ndarray) -> dict[str, float | int | None]:
    x = np.asarray(values, dtype=np.float64)
    if not x.size:
        return {"n": 0, "p50": None, "p95": None, "max": None, "mean": None}
    if not np.isfinite(x).all():
        raise ValueError("distribution requires finite peer-defined changes")
    return {"n": int(x.size), "p50": float(np.quantile(x, 0.50)),
            "p95": float(np.quantile(x, 0.95)), "max": float(x.max()),
            "mean": float(x.mean())}


def leave_one_out_population_context(geoids: list[str], regions: list[str], countyfp: list[str],
                                    shares: np.ndarray, population: np.ndarray,
                                    frozen_building_county_share: np.ndarray) -> dict[str, Any]:
    """Compute peer population-weighted share; remove each focal tract from numerator and denominator."""
    n = len(geoids)
    share = np.asarray(shares, dtype=np.float64)
    pop = np.asarray(population, dtype=np.float64)
    frozen = np.asarray(frozen_building_county_share, dtype=np.float64)
    if not (len(regions) == len(countyfp) == n) or share.shape != (n,) or pop.shape != (n,) or frozen.shape != (n,):
        raise ValueError("GEOID, region, COUNTYFP, share, population, and frozen county value must align")
    if len(set(geoids)) != n:
        raise ValueError("duplicate GEOID")
    if not np.isfinite(frozen).all() or np.any((frozen < 0.0) | (frozen > 1.0)):
        raise ValueError("frozen Mx4 county share must be finite and in [0,1]")
    if np.any(np.isfinite(pop) & (pop < 0.0)):
        raise ValueError("provided pop_total cannot be negative")
    if np.any(np.isfinite(share) & ((share < 0.0) | (share > 1.0))):
        raise ValueError("defined Mx4 tract shares must be in [0,1]")

    usable = np.isfinite(share) & np.isfinite(pop) & (pop > 0.0)
    groups: dict[tuple[str, str], list[int]] = {}
    for i, (region, county) in enumerate(zip(regions, countyfp)):
        groups.setdefault((str(region), str(county)), []).append(i)
    peer = np.full(n, np.nan, dtype=np.float64)
    county_population = np.zeros(n, dtype=np.float64)
    county_weighted_share = np.zeros(n, dtype=np.float64)
    for positions in groups.values():
        idx = np.asarray(positions, dtype=np.int64)
        contributing = idx[usable[idx]]
        total_pop = float(pop[contributing].sum())
        weighted_share_sum = float(np.dot(pop[contributing], share[contributing]))
        county_population[idx] = total_pop
        county_weighted_share[idx] = weighted_share_sum
        for i in positions:
            focal_pop = float(pop[i]) if usable[i] else 0.0
            focal_weighted_share = focal_pop * float(share[i]) if usable[i] else 0.0
            peer_denominator = total_pop - focal_pop
            if peer_denominator > 0.0:
                peer[i] = (weighted_share_sum - focal_weighted_share) / peer_denominator
    defined = np.isfinite(peer)
    if np.any(defined & ((peer < 0.0) | (peer > 1.0))):
        raise ValueError("peer population-weighted share escaped [0,1]")
    difference = np.full(n, np.nan, dtype=np.float64)
    difference[defined] = np.abs(peer[defined] - frozen[defined])
    return {"usable_share_and_population": usable, "peer_population_share": peer,
            "peer_defined": defined, "absolute_change_from_frozen": difference,
            "county_population_weight": county_population,
            "county_population_weighted_share_sum": county_weighted_share,
            "county_group_count": len(groups)}


def _read_official_ids(workspace: Path) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    regions: list[str] = []
    for region in REGIONS:
        path = workspace / "samples" / f"{region}-sample-submission.csv"
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.reader(stream)
            header = next(reader)
            if "GEOID" not in header:
                raise ValueError(f"{path}: missing official GEOID column")
            geoid_index = header.index("GEOID")
            current = [str(row[geoid_index]) for row in reader]
        if len(current) != EXPECTED_COUNTS[region] or len(set(current)) != len(current):
            raise ValueError(f"{region}: invalid official GEOID count or duplicates")
        ids.extend(current)
        regions.extend([region] * len(current))
    if len(ids) != 9379 or len(set(ids)) != 9379:
        raise ValueError("official study GEOIDs are not an exact 9,379-ID partition")
    return ids, regions


def _read_frozen_mx4(path: Path, ids: list[str], regions: list[str], expected_sha256: str
                     ) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    if not path.is_file() or sha256_file(path) != expected_sha256:
        raise ValueError("existing registered Mx4 artifact missing or hash-mismatched")
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "component", "channel", "value", "defined"])
    id_positions = {geoid: i for i, geoid in enumerate(ids)}
    expected_regions = dict(zip(ids, regions))
    tract_share = np.full(len(ids), np.nan, dtype=np.float64)
    share_defined = np.zeros(len(ids), dtype=bool)
    frozen_county_share = np.full(len(ids), np.nan, dtype=np.float64)
    county_defined = np.zeros(len(ids), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        geoid = str(row["GEOID"])
        component = str(row["component"])
        if geoid not in id_positions or row["idea"] != "Mx4" or row["region"] != expected_regions[geoid]:
            raise ValueError("Mx4 artifact contains an unexpected idea, region, or GEOID")
        if component not in {"B", "F"} or row["channel"] != "covariate":
            raise ValueError("Mx4 artifact contains an unexpected component/channel")
        key = (geoid, component)
        if key in seen:
            raise ValueError(f"duplicate Mx4 emission {key}")
        seen.add(key)
        i = id_positions[geoid]
        value = float(row["value"])
        if row["defined"]:
            if not np.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"defined Mx4 {component} value invalid for {geoid}")
            if component == "B":
                tract_share[i], share_defined[i] = value, True
            else:
                frozen_county_share[i], county_defined[i] = value, True
        elif value != 0.0:
            raise ValueError(f"undefined Mx4 {component} emission must retain frozen zero placeholder")
    if len(seen) != 2 * len(ids) or not county_defined.all():
        raise ValueError("Mx4 must have B and F emissions per official ID, with all county F values defined")
    return tract_share, share_defined, frozen_county_share, {
        "path": str(path), "rows": table.num_rows, "sha256": sha256_file(path),
        "per_tract_share_defined": int(share_defined.sum()),
        "frozen_county_share_defined": int(county_defined.sum()),
    }


def _read_strata(path: Path, ids: list[str]) -> dict[str, dict[str, Any]]:
    required = ["GEOID", "COUNTYFP", "pop_total"]
    schema = set(pq.ParquetFile(path).schema_arrow.names)
    missing = set(required) - schema
    if missing:
        raise ValueError(f"{path}: missing permitted strata columns {sorted(missing)}")
    table = pq.read_table(path, columns=required)
    wanted = set(ids)
    result = {}
    for row in table.to_pylist():
        geoid = str(row["GEOID"])
        if geoid not in wanted:
            continue
        if geoid in result:
            raise ValueError(f"{path}: duplicate official GEOID {geoid}")
        if row["COUNTYFP"] is None:
            raise ValueError(f"{geoid}: missing COUNTYFP")
        result[geoid] = {"COUNTYFP": str(row["COUNTYFP"]), "pop_total": row["pop_total"]}
    if set(result) != wanted:
        raise ValueError(f"{path}: missing official GEOIDs")
    return result


def _artifact_payload(ids: list[str], regions: list[str], counties: list[str],
                      population: np.ndarray, share: np.ndarray, frozen: np.ndarray,
                      result: dict[str, Any]) -> bytes:
    peer, defined, difference = (result["peer_population_share"], result["peer_defined"],
                                result["absolute_change_from_frozen"])
    table = pa.table({
        "GEOID": pa.array(ids, type=pa.string()), "region": pa.array(regions, type=pa.string()),
        "COUNTYFP": pa.array(counties, type=pa.string()),
        "pop_total": pa.array(population, type=pa.float64()),
        "mx4_tract_esri_only_share": pa.array(share, type=pa.float64()),
        "mx4_frozen_building_weighted_county_share": pa.array(frozen, type=pa.float64()),
        "peer_population_weighted_share": pa.array([float(peer[i]) if defined[i] else None for i in range(len(ids))], type=pa.float64()),
        "peer_defined": pa.array(defined, type=pa.bool_()),
        "absolute_change_from_frozen": pa.array([float(difference[i]) if defined[i] else None for i in range(len(ids))], type=pa.float64()),
    })
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue()


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    config_path = ROOT / "config/bias60-harness-preregistered.json"
    harness_path = ROOT / "reports/ideas-harness.json"
    peer_report_path = ROOT / "reports/mx4-peer-only-context-sensitivity.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    peer_report = json.loads(peer_report_path.read_text(encoding="utf-8"))
    seed = config["seeds"]["Mx4"]
    mx4_entry = harness["per_idea"]["Mx4"]
    if mx4_entry["path"] != "ideas/Mx4.parquet" or not mx4_entry["geoid_partition_exact"]:
        raise ValueError("frozen Mx4 harness entry does not certify official ID coverage")
    if seed["county_column"] != "COUNTYFP" or seed["tract_share_column"] != "overture_building_esri_source_share":
        raise ValueError("Mx4 registered share/county specification changed unexpectedly")

    ids, regions = _read_official_ids(workspace)
    region_of = dict(zip(ids, regions))
    mx4_path = workspace / mx4_entry["path"]
    share, share_defined, frozen, mx4_meta = _read_frozen_mx4(mx4_path, ids, regions, mx4_entry["sha256"])
    population = np.full(len(ids), np.nan, dtype=np.float64)
    counties = [""] * len(ids)
    source_manifest = {}
    positions = {geoid: i for i, geoid in enumerate(ids)}
    strata_dir = workspace / "defined-flags-inputs"
    for region in REGIONS:
        region_ids = [geoid for geoid in ids if region_of[geoid] == region]
        strata_path = strata_dir / f"{region}-strata-tract-table.parquet"
        rows = _read_strata(strata_path, region_ids)
        for geoid, row in rows.items():
            i = positions[geoid]
            counties[i] = row["COUNTYFP"]
            value = row["pop_total"]
            population[i] = float(value) if value is not None else np.nan
        region_population = population[[positions[g] for g in region_ids]]
        if np.any(np.isfinite(region_population) & (region_population < 0.0)):
            raise ValueError(f"{region}: provided pop_total contains a negative value")
        source_manifest[region] = {
            "official_ids": len(region_ids),
            "strata_table": {"path": str(strata_path), "sha256": sha256_file(strata_path),
                             "columns_read": ["GEOID", "COUNTYFP", "pop_total"]},
        }
    if not np.isfinite(frozen).all():
        raise ValueError("invalid frozen county share input")

    result = leave_one_out_population_context(ids, regions, counties, share, population, frozen)
    if result["county_group_count"] != 297:
        raise ValueError(f"expected 297 region-local county groups, found {result['county_group_count']}")
    n = len(ids)
    peer_defined = result["peer_defined"]
    coverage = float(peer_defined.sum() / n)
    changes = result["absolute_change_from_frozen"]
    overall = distribution(changes[peer_defined])
    per_region = {}
    for region in REGIONS:
        mask = np.asarray([value == region for value in regions], dtype=bool)
        defined = mask & peer_defined
        per_region[region] = {
            "official_ids": int(mask.sum()), "peer_defined": int(defined.sum()),
            "peer_undefined": int(mask.sum() - defined.sum()),
            "coverage_fraction": float(defined.sum() / mask.sum()),
            "absolute_change_from_frozen": distribution(changes[defined]),
        }
    coverage_pass = coverage >= 0.9859
    p95_pass = overall["p95"] is not None and overall["p95"] >= 0.01
    payload = _artifact_payload(ids, regions, counties, population, share, frozen, result)
    if payload != _artifact_payload(ids, regions, counties, population, share, frozen, result):
        raise ValueError("population-weighted Parquet serialization is not byte deterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    report = {
        "schema_version": 1,
        "audit": "Mx4 leave-one-tract-out population-weighted county context; bounded sensitivity screen, not promotion",
        "method": {
            "share": "registered Mx4 per-tract B covariate (overture_building_esri_source_share), read from the frozen Mx4 artifact",
            "candidate": "Within each region-local COUNTYFP group, compute sum(pop_total * Mx4 tract share) / sum(pop_total) over usable tracts, excluding the focal tract from both numerator and denominator. A tract contributes only when its Mx4 share is defined and pop_total is finite and positive; unusable focal tracts contribute zero and are also excluded from peers when their share/population is unusable.",
            "comparison": "absolute difference between peer-only population-weighted share and frozen Mx4 building-weighted county F value; frozen Mx4 remains untouched",
            "undefined": "peer context undefined if remaining eligible population denominator is zero; retained as null in Parquet, never zero-filled",
            "percentiles": "NumPy linear quantiles over peer-defined official tracts only",
            "scope": "Only official GEOIDs, frozen Mx4 B/F values, provided COUNTYFP and pop_total were used; no building count weights were reread or recalculated.",
        },
        "measure_before": {
            "building_weighted_peer_coverage": peer_report["scope"]["peer_defined"],
            "official_ids": peer_report["scope"]["official_ids"],
            "coverage_fraction": peer_report["scope"]["peer_defined_coverage_fraction"],
            "p95_absolute_change_from_frozen": peer_report["absolute_change"]["p95"],
            "source_report": "reports/mx4-peer-only-context-sensitivity.json",
            "source_report_sha256": sha256_file(peer_report_path),
            "frozen_mx4_unchanged": (peer_report["acceptance"]["existing_feature_unchanged"]
                                      and peer_report["inputs"]["mx4_artifact"]["sha256"] == mx4_entry["sha256"]),
        },
        "scope": {"official_ids": n, "exact_id_partition": True,
                  "county_groups": result["county_group_count"],
                  "peer_defined": int(peer_defined.sum()), "peer_undefined": n - int(peer_defined.sum()),
                  "peer_defined_coverage_fraction": coverage,
                  "mx4_frozen_artifact_modified": False,
                  "sample_score_values_read": False, "labels_read": False,
                  "external_or_removed_reference_data_read": False, "uploads": 0},
        "inputs": {
            "mx4_artifact": {**mx4_meta, "expected_sha256": mx4_entry["sha256"]},
            "harness_report": {"path": "reports/ideas-harness.json", "sha256": sha256_file(harness_path)},
            "harness_config": {"path": "config/bias60-harness-preregistered.json", "sha256": sha256_file(config_path)},
            "strata": source_manifest,
            "official_ids_sha256": hashlib.sha256("\n".join(f"{g},{r}" for g, r in zip(ids, regions)).encode()).hexdigest(),
            "Mx4_seed": {"county_column": seed["county_column"],
                          "tract_share_column": seed["tract_share_column"]},
        },
        "absolute_change_from_frozen": overall,
        "regions": per_region,
        "acceptance": {"peer_coverage_minimum": 0.9859, "p95_change_minimum": 0.01,
                       "peer_coverage_pass": coverage_pass, "p95_change_pass": p95_pass,
                       "acceptance_pass": coverage_pass and p95_pass,
                       "frozen_mx4_unchanged": True},
        "per_tract_artifact": {"path": str(artifact_path), "rows": n,
                               "sha256": hashlib.sha256(payload).hexdigest(),
                               "deterministic_bytes": True},
        "provenance": {"script_sha256": sha256_file(Path(__file__).resolve()),
                       "selected_inputs_only": ["official GEOID", "Mx4 registered tract Esri-only share", "COUNTYFP", "pop_total"],
                       "no_building_weights_used_for_candidate": True},
        "determinism": {"calculation_group_order_fixed": True, "serialized_bytes_repeatable": True},
        "interpretation": ("Population-weighted peer context meets both sensitivity thresholds; this remains a label-free screen, not promotion. Keep frozen Mx4 unchanged pending separate review."
                           if coverage_pass and p95_pass else
                           "Population-weighted peer context misses at least one sensitivity threshold; retain frozen building-weighted Mx4 unchanged."),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/mx4-population-weighted-context.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "mx4-population-weighted-context/population-weighted-peer-mx4.parquet"
    report_path = ROOT / args.out
    report = run(workspace, report_path, artifact)
    print(json.dumps({"out": str(report_path.relative_to(ROOT)),
                      "coverage": report["scope"]["peer_defined_coverage_fraction"],
                      "peer_defined": report["scope"]["peer_defined"],
                      "absolute_change_from_frozen": report["absolute_change_from_frozen"],
                      "regions": report["regions"],
                      "acceptance_pass": report["acceptance"]["acceptance_pass"],
                      "artifact": report["per_tract_artifact"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
