#!/usr/bin/env python3
"""Screen a leave-one-tract-out, region-local COUNTYFP median fallback for pooled Mx5."""
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
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
MIN_PEERS = 5
MIN_COVERAGE = 0.95


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def peer_median_impute(geoids: list[str], regions: list[str], counties: list[str],
                       observed_values: np.ndarray, observed_defined: np.ndarray,
                       minimum_peers: int = MIN_PEERS
                       ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Fill undefined rows from >=minimum_peers *observed* county peers, excluding self.

    Imputed rows never become donor peers. County keys are region-local, matching the frozen
    Mx4 county-context convention and preventing cross-region pooling of equal COUNTYFP strings.
    """
    n = len(geoids)
    values = np.asarray(observed_values, dtype=np.float64)
    defined = np.asarray(observed_defined, dtype=bool)
    if not (len(regions) == len(counties) == n) or values.shape != (n,) or defined.shape != (n,):
        raise ValueError("GEOID/region/COUNTYFP/value/defined columns must align")
    if minimum_peers < 1:
        raise ValueError("minimum_peers must be positive")
    if len(set(geoids)) != n:
        raise ValueError("duplicate official GEOID")
    if any(not str(region) or not str(county) for region, county in zip(regions, counties)):
        raise ValueError("region and COUNTYFP must be present for every official GEOID")
    if np.any(defined & (~np.isfinite(values) | (values < 0.0) | (values > 1.0))):
        raise ValueError("observed defined Mx5 shares must be finite and in [0,1]")
    if np.any(~defined & np.isfinite(values)):
        raise ValueError("undefined pooled Mx5 rows must remain null, not numeric/zero-filled")

    members: dict[tuple[str, str], list[int]] = {}
    for index, (region, county) in enumerate(zip(regions, counties)):
        members.setdefault((str(region), str(county)), []).append(index)
    output = values.copy()
    output_defined = defined.copy()
    imputed = np.zeros(n, dtype=bool)
    peer_counts = np.zeros(n, dtype=np.int64)
    for indices in members.values():
        observed_indices = [index for index in indices if defined[index]]
        for index in indices:
            peers = [peer for peer in observed_indices if peer != index]
            peer_counts[index] = len(peers)
            if defined[index] or len(peers) < minimum_peers:
                continue
            output[index] = float(np.median(values[peers]))
            output_defined[index] = True
            imputed[index] = True
    if np.any(imputed & (peer_counts < minimum_peers)):
        raise AssertionError("an imputed tract had fewer than the required observed peers")
    if np.any(~output_defined & np.isfinite(output)):
        raise AssertionError("remaining undefined tracts must stay null")
    return output, output_defined, imputed, peer_counts


def build_output_table(geoids: list[str], regions: list[str], counties: list[str],
                       observed_values: np.ndarray, observed_defined: np.ndarray,
                       output_values: np.ndarray, output_defined: np.ndarray,
                       imputed: np.ndarray, peer_counts: np.ndarray) -> pa.Table:
    n = len(geoids)
    arrays = [np.asarray(observed_values, dtype=np.float64),
              np.asarray(observed_defined, dtype=bool), np.asarray(output_values, dtype=np.float64),
              np.asarray(output_defined, dtype=bool), np.asarray(imputed, dtype=bool),
              np.asarray(peer_counts, dtype=np.int64)]
    if len(regions) != n or len(counties) != n or any(array.shape != (n,) for array in arrays):
        raise ValueError("Mx5 county-peer output columns must align")
    obs, obs_mask, vals, mask, imputed_mask, count = arrays
    if np.any(imputed_mask & (~mask | obs_mask | (count < MIN_PEERS))):
        raise ValueError("imputation flags must identify only newly defined values with >=5 peers")
    if np.any(obs_mask & ((obs != vals) | (~mask) | imputed_mask)):
        raise ValueError("observed pooled Mx5 values must be preserved exactly")
    if np.any(~mask & np.isfinite(vals)):
        raise ValueError("remaining undefined values must be null, never zero-filled")
    if np.any(mask & (~np.isfinite(vals) | (vals < 0.0) | (vals > 1.0))):
        raise ValueError("defined output values must be finite and in [0,1]")
    return pa.table({
        "GEOID": pa.array(geoids, type=pa.string()),
        "region": pa.array(regions, type=pa.string()),
        "COUNTYFP": pa.array(counties, type=pa.string()),
        "observed_share": pa.array([float(obs[i]) if obs_mask[i] else None for i in range(n)],
                                    type=pa.float64()),
        "observed_defined": pa.array(obs_mask, type=pa.bool_()),
        "county_peer_mx5_share": pa.array([float(vals[i]) if mask[i] else None for i in range(n)],
                                            type=pa.float64()),
        "defined": pa.array(mask, type=pa.bool_()),
        "imputed": pa.array(imputed_mask, type=pa.bool_()),
        "eligible_observed_peer_count": pa.array(count, type=pa.int64()),
    })


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def distribution(values: np.ndarray, defined: np.ndarray) -> dict[str, Any]:
    finite = np.asarray(values, dtype=np.float64)[np.asarray(defined, dtype=bool)]
    if not finite.size:
        return {"n": 0, "min": None, "p50": None, "p95": None, "max": None, "mean": None}
    if not np.isfinite(finite).all() or np.any((finite < 0.0) | (finite > 1.0)):
        raise ValueError("feature distribution includes nonfinite or out-of-range defined values")
    return {"n": int(finite.size), "min": float(finite.min()),
            "p50": float(np.quantile(finite, 0.5)), "p95": float(np.quantile(finite, 0.95)),
            "max": float(finite.max()), "mean": float(finite.mean())}


def build_report(workspace: Path, *, report_path: Path, out_path: Path) -> dict[str, Any]:
    source_report = json.loads(report_path.read_text(encoding="utf-8"))
    if (source_report.get("audit") != "Pooled Mx5 education-name taxonomy mismatch covariate coverage; label-free screen, not promotion"
            or not source_report.get("scope", {}).get("exact_geoid_partition")):
        raise ValueError("expected completed pooled-Mx5 source coverage report not found")
    expected_artifact = source_report["per_tract_artifact"]
    source_path = workspace / "mx5-pooled-provider-coverage" / "pooled-mx5.parquet"
    if sha256_file(source_path) != expected_artifact["sha256"]:
        raise ValueError("pooled-Mx5 source artifact hash differs from completed source report")
    frozen_mx5_path = workspace / "ideas" / "Mx5.parquet"
    frozen_mx5_sha = source_report["provenance"]["frozen_mx5_artifact"]["sha256"]
    if sha256_file(frozen_mx5_path) != frozen_mx5_sha:
        raise ValueError("frozen Mx5 artifact hash differs from source screen")

    config_path = ROOT / "config/bias60-harness-preregistered.json"
    config = load_config(config_path)
    official = official_scored_pairs(workspace)
    if (len(official) != 9379 or len({g for g, _ in official}) != 9379
            or len(set(official)) != 9379):
        raise ValueError("official GEOID/region list is not an exact 9,379-ID partition")
    official_hash = hashlib.sha256(
        json.dumps(official, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    if official_hash != source_report["provenance"]["official_geoid_partition_sha256"]:
        raise ValueError("official GEOID/region order/hash differs from pooled-Mx5 source screen")
    geoids = [geoid for geoid, _ in official]
    regions = [region for _, region in official]
    if set(regions) != set(REGIONS):
        raise ValueError("official ID list contains unexpected regions")
    position = {geoid: i for i, geoid in enumerate(geoids)}
    county_by_id: dict[str, str] = {}
    county_input_hashes: dict[str, str] = {}
    county_access: dict[str, Any] = {}
    for region in REGIONS:
        ids = [geoid for geoid, r in official if r == region]
        inputs = ProvidedInputs(config, workspace, region, ids)
        strata = inputs.strata(["COUNTYFP"])
        county_values = [str(value).strip() if value is not None else "" for value in strata["COUNTYFP"]]
        if len(county_values) != len(ids) or any(not value for value in county_values):
            raise ValueError(f"{region}: provided COUNTYFP missing or misaligned")
        for geoid, county in zip(ids, county_values):
            county_by_id[geoid] = county
        county_input_hashes[region] = hashlib.sha256(
            "\n".join(f"{geoid},{county}" for geoid, county in zip(ids, county_values)).encode()).hexdigest()
        county_access[region] = {key: sorted(set(values)) for key, values in inputs.accessed.items()}
    counties = [county_by_id[geoid] for geoid in geoids]

    source = pq.read_table(source_path, columns=["GEOID", "region", "pooled_taxonomy_mismatch_share", "defined"])
    if source.num_rows != len(official):
        raise ValueError("pooled-Mx5 source artifact row count differs from official partition")
    observed_values = np.full(len(official), np.nan, dtype=np.float64)
    observed_defined = np.zeros(len(official), dtype=bool)
    seen: set[str] = set()
    source_region_counts = {region: 0 for region in REGIONS}
    for row in source.to_pylist():
        geoid, region = str(row["GEOID"]), str(row["region"])
        if geoid not in position or regions[position[geoid]] != region or geoid in seen:
            raise ValueError(f"pooled-Mx5 artifact has unexpected/duplicate official row {geoid}")
        seen.add(geoid)
        index = position[geoid]
        source_region_counts[region] += 1
        flag = bool(row["defined"])
        raw = row["pooled_taxonomy_mismatch_share"]
        if flag:
            if raw is None or not np.isfinite(float(raw)) or not 0.0 <= float(raw) <= 1.0:
                raise ValueError(f"defined pooled Mx5 share invalid for {geoid}")
            observed_values[index] = float(raw)
        elif raw is not None:
            raise ValueError(f"undefined pooled Mx5 source was numeric, including possible zero fill: {geoid}")
        observed_defined[index] = flag
    if seen != set(geoids):
        raise ValueError("pooled-Mx5 source does not contain the exact official GEOID set")
    if source_region_counts != {region: source_report["after"]["regions"][region]["official_ids"]
                                for region in REGIONS}:
        raise ValueError("pooled-Mx5 source region counts differ from its audit report")
    if int(observed_defined.sum()) != source_report["scope"]["defined_ids"]:
        raise ValueError("observed pooled Mx5 coverage differs from source audit")

    output_values, output_defined, imputed, peer_counts = peer_median_impute(
        geoids, regions, counties, observed_values, observed_defined, minimum_peers=MIN_PEERS)
    table = build_output_table(geoids, regions, counties, observed_values, observed_defined,
                               output_values, output_defined, imputed, peer_counts)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("county-peer artifact serialization is not deterministic")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)

    total = len(official)
    observed_count = int(observed_defined.sum())
    imputed_count = int(imputed.sum())
    remaining_count = int((~output_defined).sum())
    final_count = int(output_defined.sum())
    if observed_count + imputed_count + remaining_count != total:
        raise AssertionError("observed/imputed/undefined partition does not sum to official IDs")
    per_region: dict[str, Any] = {}
    for region in REGIONS:
        mask = np.asarray([item == region for item in regions], dtype=bool)
        obs_mask = observed_defined & mask
        imp_mask = imputed & mask
        final_mask = output_defined & mask
        undefined_mask = ~output_defined & mask
        per_region[region] = {
            "official_ids": int(mask.sum()), "observed_defined_ids": int(obs_mask.sum()),
            "imputed_ids": int(imp_mask.sum()), "remaining_undefined_ids": int(undefined_mask.sum()),
            "observed_coverage_fraction": float(obs_mask.sum() / mask.sum()),
            "imputed_fraction_of_official_ids": float(imp_mask.sum() / mask.sum()),
            "remaining_undefined_fraction": float(undefined_mask.sum() / mask.sum()),
            "total_defined_ids": int(final_mask.sum()),
            "total_coverage_fraction": float(final_mask.sum() / mask.sum()),
            "imputed_share_distribution": distribution(output_values[mask], final_mask[mask]),
            "imputed_peer_count_min": int(peer_counts[imp_mask].min()) if imp_mask.any() else None,
            "imputed_peer_count_max": int(peer_counts[imp_mask].max()) if imp_mask.any() else None,
        }
    imputed_peer_counts_ok = bool(np.all(peer_counts[imputed] >= MIN_PEERS))
    undefined_null = bool(np.all(np.isnan(output_values[~output_defined])))
    observed_unchanged = bool(np.array_equal(output_values[observed_defined], observed_values[observed_defined]))
    bounded = bool(np.isfinite(output_values[output_defined]).all()
                    and np.all((output_values[output_defined] >= 0.0)
                               & (output_values[output_defined] <= 1.0)))
    coverage = final_count / total
    acceptance = {
        "exact_9379_id_partition": len(seen) == total,
        "total_coverage_at_least_95_percent": coverage >= MIN_COVERAGE,
        "every_imputed_value_has_at_least_5_other_observed_peers": imputed_peer_counts_ok,
        "undefined_values_remain_null_not_zero": undefined_null,
        "observed_values_preserved_exactly": observed_unchanged,
        "all_defined_values_finite_and_in_0_1": bounded,
        "acceptance_pass": bool(coverage >= MIN_COVERAGE and imputed_peer_counts_ok
                                and undefined_null and observed_unchanged and bounded),
    }
    report = {
        "schema_version": 1,
        "audit": "Pooled Mx5 county-peer median fallback: observed/imputed coverage sensitivity, not integration",
        "method": {"fallback": "For each source-undefined tract, median the pooled Mx5 shares of other source-defined tracts in the same (region, COUNTYFP) group, excluding the focal GEOID.",
                   "minimum_other_defined_peers": MIN_PEERS,
                   "donors": "Observed pooled-Mx5 values only; imputed values never donate to other rows.",
                   "zero_denominator": "Source undefined values remain null unless eligible county peer median exists; if it does not, final value remains null, never zero-filled.",
                   "observed_values": "Copied unchanged from the completed pooled-Mx5 artifact."},
        "scope": {"official_ids": total, "exact_geoid_partition": len(seen) == total,
                  "observed_defined_ids": observed_count, "imputed_ids": imputed_count,
                  "remaining_undefined_ids": remaining_count, "final_defined_ids": final_count,
                  "observed_coverage_fraction": observed_count / total,
                  "imputed_fraction_of_official_ids": imputed_count / total,
                  "remaining_undefined_fraction": remaining_count / total,
                  "total_coverage_fraction": coverage,
                  "county_groups_region_local": len(set(zip(regions, counties))),
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_reference_data_read": False,
                  "external_data_read": False, "leaderboard_feedback_used": False,
                  "frozen_mx5_modified": False, "uploads": 0},
        "regions": per_region,
        "acceptance_threshold": MIN_COVERAGE,
        "acceptance": acceptance,
        "inputs": {"pooled_mx5_artifact": {"path": str(source_path), "sha256": sha256_file(source_path),
                                              "rows": source.num_rows},
                   "pooled_mx5_source_report_sha256": sha256_file(report_path),
                   "frozen_mx5_artifact": {"path": str(frozen_mx5_path), "sha256": sha256_file(frozen_mx5_path)},
                   "countyfp_selected_values_sha256_by_region": county_input_hashes,
                   "countyfp_accessed_columns_by_region": county_access,
                   "official_geoid_region_sha256": official_hash,
                   "harness_config_sha256": sha256_file(config_path)},
        "output_artifact": {"path": str(out_path), "rows": table.num_rows,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "deterministic_bytes": True},
        "interpretation": ("Coverage screen passes its criteria; retain as a separately reviewed sensitivity and do not integrate automatically."
                           if acceptance["acceptance_pass"] else
                           "County-peer fallback misses at least one acceptance criterion; retain as a diagnostic sensitivity only and do not integrate."),
    }
    report_path_out = ROOT / "reports/mx5-county-peer-imputation.json"
    report_path_out.parent.mkdir(parents=True, exist_ok=True)
    report_path_out.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                               encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--source-report", default="reports/mx5-pooled-provider-coverage.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE is required")
    out = Path(args.artifact) if args.artifact else workspace / "mx5-county-peer-imputation" / "county-peer-mx5.parquet"
    report = build_report(workspace, report_path=ROOT / args.source_report, out_path=out)
    print(json.dumps({"observed_defined": report["scope"]["observed_defined_ids"],
                      "imputed": report["scope"]["imputed_ids"],
                      "remaining_undefined": report["scope"]["remaining_undefined_ids"],
                      "total_coverage": report["scope"]["total_coverage_fraction"],
                      "acceptance": report["acceptance"],
                      "artifact": report["output_artifact"],
                      "report": "reports/mx5-county-peer-imputation.json"}, indent=2, sort_keys=True))
    return 0 if report["acceptance"]["acceptance_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
