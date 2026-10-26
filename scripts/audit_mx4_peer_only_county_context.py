#!/usr/bin/env python3
"""Audit Mx4 county-context self-influence using declared covariate inputs only."""
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


def distribution(values: np.ndarray) -> dict[str, float | int]:
    x = np.asarray(values, dtype=np.float64)
    if x.size == 0 or not np.isfinite(x).all():
        raise ValueError("distribution requires finite peer-defined changes")
    return {"n": int(x.size), "p50": float(np.quantile(x, 0.50)),
            "p95": float(np.quantile(x, 0.95)), "max": float(x.max()),
            "mean": float(x.mean())}


def peer_only_county_context(geoids: list[str], regions: list[str], countyfp: list[str],
                             shares: np.ndarray, weights: np.ndarray,
                             existing_county_share: np.ndarray
                             ) -> dict[str, Any]:
    """Reproduce weighted Mx4 county means and remove each usable focal tract in turn.

    A tract with missing share or nonpositive/nonfinite weight is excluded from the Mx4
    weighted sum. Such a tract has zero focal contribution; its peer context is the full
    weighted mean of the other usable tracts in that county group.
    """
    n = len(geoids)
    share = np.asarray(shares, dtype=np.float64)
    weight = np.asarray(weights, dtype=np.float64)
    original = np.asarray(existing_county_share, dtype=np.float64)
    if not (len(regions) == len(countyfp) == n) or share.shape != (n,) or weight.shape != (n,) or original.shape != (n,):
        raise ValueError("GEOID, region, COUNTYFP, share, weight, and existing values must align")
    if len(set(geoids)) != n:
        raise ValueError("duplicate GEOID in Mx4 input")
    if not np.isfinite(original).all():
        raise ValueError("existing Mx4 county context must be defined for all official IDs")
    invalid_share = np.isfinite(share) & ((share < 0.0) | (share > 1.0))
    if invalid_share.any():
        raise ValueError("finite Mx4 per-tract shares must lie in [0,1]")

    usable = np.isfinite(share) & np.isfinite(weight) & (weight > 0.0)
    group_indices: dict[tuple[str, str], list[int]] = {}
    for i, key in enumerate(zip(regions, countyfp)):
        group_indices.setdefault((str(key[0]), str(key[1])), []).append(i)
    group_weight = np.zeros(n, dtype=np.float64)
    group_weighted_share = np.zeros(n, dtype=np.float64)
    peer = np.full(n, np.nan, dtype=np.float64)
    reproduced = np.full(n, np.nan, dtype=np.float64)
    group_for = [None] * n
    for group, positions in group_indices.items():
        idx = np.asarray(positions, dtype=np.int64)
        valid_idx = idx[usable[idx]]
        total_weight = float(weight[valid_idx].sum())
        weighted_share = float(np.dot(weight[valid_idx], share[valid_idx]))
        if total_weight > 0.0:
            reproduced[idx] = weighted_share / total_weight
        group_weight[idx] = total_weight
        group_weighted_share[idx] = weighted_share
        for i in positions:
            group_for[i] = group
            focal_weight = float(weight[i]) if usable[i] else 0.0
            focal_contribution = focal_weight * float(share[i]) if usable[i] else 0.0
            peer_denominator = total_weight - focal_weight
            if peer_denominator > 0.0:
                peer[i] = (weighted_share - focal_contribution) / peer_denominator

    if not np.isfinite(reproduced).all():
        raise ValueError("Mx4 baseline has a county with no usable building-weighted share")
    if not np.allclose(reproduced, original, rtol=1e-12, atol=1e-12):
        max_error = float(np.max(np.abs(reproduced - original)))
        raise ValueError(f"recomputed county means do not reproduce frozen Mx4 F output; max error={max_error}")
    peer_defined = np.isfinite(peer)
    change = np.full(n, np.nan, dtype=np.float64)
    change[peer_defined] = np.abs(original[peer_defined] - peer[peer_defined])
    if np.any(change[peer_defined] < 0.0) or np.any(change[peer_defined] > 1.0):
        raise ValueError("absolute share changes are outside [0,1]")
    return {"usable_focal": usable, "peer_only_share": peer,
            "peer_defined": peer_defined, "absolute_change": change,
            "reproduced_existing_share": reproduced,
            "county_group_count": len(group_indices),
            "county_group_for_row": group_for,
            "county_group_weight": group_weight,
            "county_group_weighted_share": group_weighted_share}


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
    if len(ids) != 9379 or len(set(ids)) != len(ids):
        raise ValueError("official study GEOIDs are not an exact 9,379-ID partition")
    return ids, regions


def _table_by_geoid(path: Path, columns: list[str], id_set: set[str]) -> dict[str, dict[str, Any]]:
    table = pq.read_table(path, columns=columns)
    result = {}
    for row in table.to_pylist():
        geoid = str(row["GEOID"])
        if geoid not in id_set:
            continue
        if geoid in result:
            raise ValueError(f"{path}: duplicate official GEOID {geoid}")
        result[geoid] = row
    if set(result) != id_set:
        raise ValueError(f"{path}: expected exact official GEOID coverage, got {len(result)} rows")
    return result


def _read_mx4(path: Path, ids: list[str], regions: list[str], expected_hash: str
              ) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    if not path.is_file() or sha256_file(path) != expected_hash:
        raise ValueError("existing registered Mx4 artifact missing or hash-mismatched")
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "component", "channel", "value", "defined"])
    row_map: dict[tuple[str, str], tuple[float, bool]] = {}
    expected_regions = dict(zip(ids, regions))
    id_set = set(ids)
    for row in table.to_pylist():
        geoid = str(row["GEOID"])
        if row["idea"] != "Mx4" or geoid not in id_set or row["region"] != expected_regions.get(geoid):
            raise ValueError("Mx4 artifact has unexpected idea or GEOID")
        key = (geoid, str(row["component"]))
        if key in row_map or row["channel"] != "covariate":
            raise ValueError(f"Mx4 artifact duplicate component or unexpected channel for {key}")
        row_map[key] = (float(row["value"]), bool(row["defined"]))
    values = {"F": np.full(len(ids), np.nan, dtype=np.float64),
              "B": np.full(len(ids), np.nan, dtype=np.float64)}
    masks = {component: np.zeros(len(ids), dtype=bool) for component in ("F", "B")}
    for i, geoid in enumerate(ids):
        for component in ("F", "B"):
            value, defined = row_map[(geoid, component)]
            if defined:
                if not np.isfinite(value):
                    raise ValueError(f"Mx4 defined {component} value is nonfinite for {geoid}")
                values[component][i] = value
                masks[component][i] = True
        if not expected_regions[geoid]:
            raise ValueError("empty official region")
    if len(row_map) != 2 * len(ids) or not masks["F"].all():
        raise ValueError("Mx4 artifact must have two component rows per official GEOID and defined F context")
    return values["B"], values["F"], {"rows": table.num_rows, "sha256": sha256_file(path),
                                       "B_defined": int(masks["B"].sum()),
                                       "F_defined": int(masks["F"].sum())}


def _parquet_payload(ids: list[str], regions: list[str], countyfp: list[str],
                     shares: np.ndarray, weights: np.ndarray, old: np.ndarray,
                     result: dict[str, Any]) -> bytes:
    table = pa.table({
        "GEOID": pa.array(ids, type=pa.string()),
        "region": pa.array(regions, type=pa.string()),
        "COUNTYFP": pa.array(countyfp, type=pa.string()),
        "tract_esri_only_share": pa.array(shares, type=pa.float64()),
        "building_weight": pa.array(weights, type=pa.float64()),
        "existing_mx4_county_share": pa.array(old, type=pa.float64()),
        "peer_only_county_share": pa.array(result["peer_only_share"], type=pa.float64()),
        "peer_defined": pa.array(result["peer_defined"], type=pa.bool_()),
        "absolute_change": pa.array(result["absolute_change"], type=pa.float64()),
    })
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue()


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    config_path = ROOT / "config/bias60-harness-preregistered.json"
    harness_path = ROOT / "reports/ideas-harness.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    seed = config["seeds"]["Mx4"]
    idea_info = harness["per_idea"]["Mx4"]
    expected_hash = idea_info["sha256"]
    if idea_info["path"] != "ideas/Mx4.parquet" or not idea_info["geoid_partition_exact"]:
        raise ValueError("registered Mx4 harness entry does not certify exact official-ID partition")
    declared_inputs = idea_info["accessed_inputs"]["parquet_columns"]
    for field in ("GEOID", seed["tract_share_column"], seed["weight_column"]):
        if not any(item.endswith("-overture-tract-summary.parquet:" + field) for item in declared_inputs):
            raise ValueError(f"Mx4 harness did not declare feature column {field}")
    if not any(item.endswith("-strata-tract-table.parquet:" + seed["county_column"]) for item in declared_inputs):
        raise ValueError("Mx4 harness did not declare COUNTYFP")

    ids, regions = _read_official_ids(workspace)
    id_set = set(ids)
    index = {geoid: i for i, geoid in enumerate(ids)}
    shares = np.full(len(ids), np.nan, dtype=np.float64)
    weights = np.full(len(ids), np.nan, dtype=np.float64)
    counties = [""] * len(ids)
    source_manifest = {}
    mx4_path = workspace / idea_info["path"]
    shares_from_mx4, existing, mx4_meta = _read_mx4(mx4_path, ids, regions, expected_hash)
    feature_dir = workspace / "gap-estimator" / "features"
    strata_dir = workspace / "defined-flags-inputs"
    region_of = dict(zip(ids, regions))
    for region in REGIONS:
        feature_path = feature_dir / f"{region}-overture-tract-summary.parquet"
        strata_path = strata_dir / f"{region}-strata-tract-table.parquet"
        features = _table_by_geoid(feature_path, ["GEOID", seed["tract_share_column"], seed["weight_column"]],
                                   {g for g in ids if region_of[g] == region})
        strata = _table_by_geoid(strata_path, ["GEOID", seed["county_column"]],
                                 {g for g in ids if region_of[g] == region})
        for geoid, feature in features.items():
            i = index[geoid]
            shares[i] = float(feature[seed["tract_share_column"]]) if feature[seed["tract_share_column"]] is not None else np.nan
            weights[i] = float(feature[seed["weight_column"]]) if feature[seed["weight_column"]] is not None else np.nan
            raw_county = strata[geoid][seed["county_column"]]
            if raw_county is None:
                raise ValueError(f"{geoid}: missing COUNTYFP")
            counties[i] = str(raw_county)
        source_manifest[region] = {
            "official_ids": sum(region_of[g] == region for g in ids),
            "feature_file": {"path": str(feature_path), "sha256": sha256_file(feature_path),
                             "columns_read": ["GEOID", seed["tract_share_column"], seed["weight_column"]]},
            "strata_file": {"path": str(strata_path), "sha256": sha256_file(strata_path),
                            "columns_read": ["GEOID", seed["county_column"]]},
        }
    if not np.allclose(shares, shares_from_mx4, equal_nan=True, rtol=0.0, atol=0.0):
        raise ValueError("declared per-tract share input differs from Mx4's stored tract covariate")
    calculated = peer_only_county_context(ids, regions, counties, shares, weights, existing)
    if calculated["county_group_count"] != 297:
        raise ValueError(f"expected frozen Mx4's 297 county groups, found {calculated['county_group_count']}")

    n = len(ids)
    peer_count = int(calculated["peer_defined"].sum())
    coverage = peer_count / n
    delta = calculated["absolute_change"]
    per_region = {}
    for region in REGIONS:
        mask = np.asarray([r == region for r in regions])
        defined = mask & calculated["peer_defined"]
        per_region[region] = {
            "official_ids": int(mask.sum()), "peer_defined": int(defined.sum()),
            "peer_undefined": int(mask.sum() - defined.sum()),
            "coverage_fraction": float(defined.sum() / mask.sum()),
            "absolute_change": distribution(delta[defined]),
        }
    overall = distribution(delta[calculated["peer_defined"]])
    coverage_pass = coverage >= 0.9859
    p95_pass = overall["p95"] >= 0.01
    payload = _parquet_payload(ids, regions, counties, shares, weights, existing, calculated)
    payload_again = _parquet_payload(ids, regions, counties, shares, weights, existing, calculated)
    if payload != payload_again:
        raise ValueError("peer-only Parquet serialization is not byte deterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    report = {
        "schema_version": 1,
        "audit": "Mx4 peer-only county context leave-one-tract-out sensitivity; measurement only, not promotion",
        "method": {
            "before": "Mx4 group building-weighted share_sum and weight_sum by region-local COUNTYFP including the focal tract; broadcast share_sum/weight_sum to each tract.",
            "after": "For each tract, remove its usable building weight and weighted Esri-only share from the county totals; peer share=(county weighted sum-focal weight*focal share)/(county weight-focal weight). A missing-share/nonpositive-weight focal has zero contribution. Peer result is undefined when peer denominator is zero.",
            "grouping": "Mx4 is executed separately per official region and groups by the declared COUNTYFP column; 297 region-local county groups, unchanged.",
            "percentiles": "NumPy linear quantiles over peer-defined official tracts only.",
        },
        "scope": {"official_ids": n, "exact_id_partition": True,
                  "county_groups": calculated["county_group_count"],
                  "peer_defined": peer_count, "peer_undefined": n - peer_count,
                  "peer_defined_coverage_fraction": coverage,
                  "sample_score_values_read": False, "labels_read": False,
                  "external_or_removed_reference_data_read": False,
                  "mx4_output_modified": False, "uploads": 0},
        "inputs": {
            "mx4_artifact": {"path": str(mx4_path), **mx4_meta,
                             "expected_sha256": expected_hash},
            "harness_report": {"path": "reports/ideas-harness.json", "sha256": sha256_file(harness_path)},
            "harness_config": {"path": "config/bias60-harness-preregistered.json",
                               "sha256": sha256_file(config_path)},
            "regions": source_manifest,
            "mx4_seed": seed,
            "official_ids_by_region": EXPECTED_COUNTS,
        },
        "baseline_reproduction": {"existing_mx4_county_values_reproduced": True,
                                   "tract_share_values_match_mx4_B_output": True,
                                   "max_absolute_reproduction_error": float(np.max(np.abs(calculated["reproduced_existing_share"] - existing))),
                                   "focal_usable_rows": int(calculated["usable_focal"].sum())},
        "absolute_change": overall,
        "regions": per_region,
        "acceptance": {"peer_coverage_minimum": 0.9859, "p95_change_minimum": 0.01,
                       "peer_coverage_pass": coverage_pass, "p95_change_pass": p95_pass,
                       "acceptance_pass": coverage_pass and p95_pass,
                       "existing_feature_unchanged": True},
        "per_tract_artifact": {"path": str(artifact_path), "rows": n,
                               "sha256": hashlib.sha256(payload).hexdigest(),
                               "deterministic_bytes": True},
        "determinism": {"calculation_repeatable": True, "serialized_bytes_repeatable": True},
        "interpretation": ("Peer-only sensitivity meets both coverage and p95 acceptance thresholds. This is a sensitivity screen only; keep the existing Mx4 feature unchanged."
                           if coverage_pass and p95_pass else
                           "Peer-only screen misses at least one preregistered threshold; report the sensitivity and keep the existing Mx4 feature unchanged."),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                           encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/mx4-peer-only-context-sensitivity.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact_path = Path(args.artifact) if args.artifact else workspace / "mx4-peer-only-context-sensitivity/peer-only-mx4.parquet"
    report_path = ROOT / args.out
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"out": str(report_path.relative_to(ROOT)),
                      "coverage": report["scope"]["peer_defined_coverage_fraction"],
                      "peer_defined": report["scope"]["peer_defined"],
                      "absolute_change": report["absolute_change"],
                      "regions": report["regions"],
                      "acceptance_pass": report["acceptance"]["acceptance_pass"],
                      "artifact": report["per_tract_artifact"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
