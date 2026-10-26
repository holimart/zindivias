#!/usr/bin/env python3
"""Screen a label-free rook-neighbor Mx4 F innovation using provided tract polygons only."""
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
import shapely

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import ProvidedInputs, official_scored_pairs  # noqa: E402
from audit_s1_mx4_lagged_innovation import (  # noqa: E402
    CURRENT, LAGGED, N_BINS, decile_codes, distribution, leave_one_out_bin_mean_residual,
    measure_region_nmi, parquet_bytes, read_feature, sha256_file,
)

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
MIN_COVERAGE = 0.9859
MIN_REMAINING_VARIANCE_RATIO = 0.10
MAX_NMI = 0.80


def feature_digest(geoids: list[str], regions: list[str], values: np.ndarray,
                   defined: np.ndarray) -> str:
    rows = [f"{g},{r},{int(ok)}," + (format(float(v), ".17g") if ok else "NULL")
            for g, r, v, ok in zip(geoids, regions, values, defined)]
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def geometry_digest(geoids: list[str], geometries: Any) -> str:
    if len(geoids) != len(geometries):
        raise ValueError("geometry and GEOID vectors must align")
    digest = hashlib.sha256()
    for geoid, geometry in zip(geoids, geometries):
        digest.update(geoid.encode("utf-8"))
        digest.update(b"\0")
        digest.update(shapely.to_wkb(geometry, byte_order=1, output_dimension=2))
        digest.update(b"\n")
    return digest.hexdigest()


def build_rook_adjacency(geometries: Any) -> list[np.ndarray]:
    """Return symmetric shared-edge neighbors; point-only queen contacts are excluded."""
    polygons = np.asarray(geometries, dtype=object)
    n = len(polygons)
    if polygons.ndim != 1 or n == 0:
        raise ValueError("a nonempty one-dimensional polygon vector is required")
    if np.any(shapely.is_empty(polygons)) or np.any(~shapely.is_valid(polygons)):
        raise ValueError("tract polygons must be nonempty and valid")
    pairs = shapely.STRtree(polygons).query(polygons, predicate="touches")
    left, right = pairs[0], pairs[1]
    upper = left < right
    left, right = left[upper], right[upper]
    if len(left):
        shared = shapely.intersection(shapely.boundary(polygons[left]),
                                      shapely.boundary(polygons[right]))
        shared_edge = shapely.length(shared) > 0.0
        left, right = left[shared_edge], right[shared_edge]
    neighbors = [set() for _ in range(n)]
    for a, b in zip(left.tolist(), right.tolist()):
        neighbors[a].add(b)
        neighbors[b].add(a)
    result = [np.asarray(sorted(row), dtype=np.int64) for row in neighbors]
    for i, adjacent in enumerate(result):
        if i in adjacent or len(set(adjacent.tolist())) != len(adjacent):
            raise AssertionError("rook adjacency cannot contain self or duplicate edges")
        if any(i not in result[j] for j in adjacent.tolist()):
            raise AssertionError("rook adjacency must be symmetric")
    return result


def rook_neighbor_mean(values: np.ndarray, defined: np.ndarray,
                       neighbors: list[np.ndarray]) -> dict[str, np.ndarray]:
    """Average only defined Mx4 values over shared-edge neighbors; focal tract is excluded."""
    x = np.asarray(values, dtype=np.float64)
    mask = np.asarray(defined, dtype=bool)
    if x.ndim != 1 or mask.shape != x.shape or len(neighbors) != len(x):
        raise ValueError("feature values, masks, and adjacency must align")
    if np.any(mask & ~np.isfinite(x)):
        raise ValueError("defined Mx4 values must be finite")
    lag = np.full(x.shape, np.nan, dtype=np.float64)
    lag_defined = np.zeros(x.shape, dtype=bool)
    neighbor_count = np.zeros(x.shape, dtype=np.int32)
    defined_neighbor_count = np.zeros(x.shape, dtype=np.int32)
    for i, adjacent in enumerate(neighbors):
        a = np.asarray(adjacent, dtype=np.int64)
        if a.ndim != 1 or np.any((a < 0) | (a >= len(x))) or i in a:
            raise ValueError("adjacency indices must be valid and exclude each focal tract")
        if len(np.unique(a)) != len(a):
            raise ValueError("adjacency entries must be unique")
        neighbor_count[i] = len(a)
        usable = a[mask[a]]
        defined_neighbor_count[i] = len(usable)
        if len(usable):
            lag[i] = float(np.mean(x[usable], dtype=np.float64))
            lag_defined[i] = True
    if np.any(lag_defined & ~np.isfinite(lag)) or np.any(~lag_defined & np.isfinite(lag)):
        raise AssertionError("rook lag definedness and null values disagree")
    return {"lag": lag, "defined": lag_defined, "neighbor_count": neighbor_count,
            "defined_neighbor_count": defined_neighbor_count}


def load_features(workspace: Path, official: list[tuple[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
    harness_path = ROOT / "reports/ideas-harness.json"
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    geoids = [g for g, _ in official]
    regions = [r for _, r in official]
    expected = {"Mx4": CURRENT, "S1": LAGGED}
    data: dict[str, Any] = {}
    hashes: dict[str, Any] = {}
    for idea, (idea_id, family, component, channel) in expected.items():
        entry = harness.get("per_idea", {}).get(idea)
        if not entry or not entry.get("geoid_partition_exact") or entry.get("forbidden_source_hits"):
            raise ValueError(f"harness does not certify clean exact-ID output for {idea}")
        path = workspace / "ideas" / f"{idea}.parquet"
        values, defined, artifact_sha = read_feature(path, idea_id, family, component, channel,
                                                     official, entry["sha256"])
        data[idea] = {"values": values, "defined": defined}
        hashes[idea] = {"path": str(path), "sha256": artifact_sha,
                        "selected_emission_sha256": feature_digest(geoids, regions, values, defined)}
    return data, {"harness_path": harness_path, "harness": harness, "inputs": hashes}


def read_provided_geometries(config: dict[str, Any], workspace: Path,
                             official: list[tuple[str, str]]) -> tuple[dict[str, np.ndarray], dict[str, str], dict[str, list[str]]]:
    by_region: dict[str, np.ndarray] = {}
    hashes: dict[str, str] = {}
    accesses: dict[str, list[str]] = {}
    for region in REGIONS:
        ids = [g for g, r in official if r == region]
        inputs = ProvidedInputs(config, workspace, region, ids)
        geoms = inputs.tract_geometries()
        if len(geoms) != len(ids):
            raise ValueError(f"{region}: provided polygon count does not match official GEOIDs")
        if inputs.accessed["layers"] != ["census-acs-housing"]:
            raise ValueError(f"{region}: geometry reader touched unexpected layers {inputs.accessed['layers']}")
        if inputs.accessed["parquet_columns"] != [f"{region}-census-acs-housing:GEOID,geometry"]:
            raise ValueError(f"{region}: geometry reader touched unexpected columns")
        by_region[region] = np.asarray(geoms, dtype=object)
        hashes[region] = geometry_digest(ids, geoms)
        accesses[region] = list(inputs.accessed["parquet_columns"])
    return by_region, hashes, accesses


def output_table(geoids: list[str], regions: list[str], current: np.ndarray,
                 current_defined: np.ndarray, queen_lag: np.ndarray, queen_defined: np.ndarray,
                 neighbor_count: np.ndarray, defined_neighbor_count: np.ndarray,
                 rook_result: dict[str, Any]) -> pa.Table:
    n = len(geoids)
    columns = (current, current_defined, queen_lag, queen_defined, neighbor_count,
               defined_neighbor_count, rook_result["lag"], rook_result["defined"],
               rook_result["innovation"], rook_result["defined_innovation"],
               rook_result["decile"], rook_result["peer_bin_mean"], rook_result["peer_count"])
    if len(regions) != n or any(np.asarray(x).shape != (n,) for x in columns):
        raise ValueError("output columns must align with the official GEOID partition")
    rook_lag = np.asarray(rook_result["lag"], dtype=np.float64)
    lag_mask = np.asarray(rook_result["defined"], dtype=bool)
    residual = np.asarray(rook_result["innovation"], dtype=np.float64)
    residual_mask = np.asarray(rook_result["defined_innovation"], dtype=bool)
    if np.any(lag_mask & ~np.isfinite(rook_lag)) or np.any(~lag_mask & np.isfinite(rook_lag)):
        raise ValueError("rook lag null semantics are invalid")
    if np.any(residual_mask & ~np.isfinite(residual)) or np.any(~residual_mask & np.isfinite(residual)):
        raise ValueError("rook innovation null semantics are invalid")
    return pa.table({
        "GEOID": pa.array(geoids, type=pa.string()),
        "region": pa.array(regions, type=pa.string()),
        "Mx4_F": pa.array([float(x) if ok else None for x, ok in zip(current, current_defined)], type=pa.float64()),
        "Mx4_F_defined": pa.array(current_defined, type=pa.bool_()),
        "Queen_S1_lagged_Mx4_F": pa.array([float(x) if ok else None for x, ok in zip(queen_lag, queen_defined)], type=pa.float64()),
        "Queen_S1_lagged_Mx4_F_defined": pa.array(queen_defined, type=pa.bool_()),
        "rook_neighbor_count": pa.array(neighbor_count, type=pa.int32()),
        "rook_defined_neighbor_count": pa.array(defined_neighbor_count, type=pa.int32()),
        "rook_lagged_Mx4_F": pa.array([float(x) if ok else None for x, ok in zip(rook_lag, lag_mask)], type=pa.float64()),
        "rook_lagged_Mx4_F_defined": pa.array(lag_mask, type=pa.bool_()),
        "current_Mx4_empirical_decile": pa.array(rook_result["decile"], type=pa.int16()),
        "peer_bin_mean_rook_Mx4_F": pa.array([float(x) if ok else None
                                                for x, ok in zip(rook_result["peer_bin_mean"], residual_mask)], type=pa.float64()),
        "peer_count": pa.array(rook_result["peer_count"], type=pa.int32()),
        "rook_innovation": pa.array([float(x) if ok else None for x, ok in zip(residual, residual_mask)], type=pa.float64()),
        "rook_innovation_defined": pa.array(residual_mask, type=pa.bool_()),
    })


def audit(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    registry_path = ROOT / "config/ideas.json"
    harness_path = ROOT / "reports/ideas-harness.json"
    queen_report_path = ROOT / "reports/s1-mx4-lagged-innovation.json"
    registry_sha_before = sha256_file(registry_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registered = {entry["id"] for entry in registry["ideas"] if entry.get("status") == "implemented"}
    if not {"Mx4", "S1"}.issubset(registered):
        raise ValueError("frozen Mx4 and Queen S1 must remain registered inputs")
    if any(entry.get("id") == "S1-rook-Mx4-innovation" for entry in registry["ideas"]):
        raise ValueError("rook innovation must remain an unregistered diagnostic")
    queen_report = json.loads(queen_report_path.read_text(encoding="utf-8"))
    if queen_report.get("audit") != "S1 lagged-Mx4 F leave-one-out empirical-decile innovation; label-free unregistered sensitivity screen":
        raise ValueError("unexpected Queen innovation baseline report")
    if not queen_report.get("acceptance", {}).get("distinct_innovation_flag"):
        raise ValueError("the frozen Queen innovation baseline must be a passing completed screen")

    official = official_scored_pairs(workspace)
    if (len(official) != 9379 or len(set(official)) != 9379
            or len({geoid for geoid, _ in official}) != 9379
            or set(region for _, region in official) != set(REGIONS)):
        raise ValueError("official GEOID/region list must be an exact 9,379-ID partition")
    geoids = [g for g, _ in official]
    regions = [r for _, r in official]
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode("utf-8")).hexdigest()
    config = json.loads((ROOT / "config/bias60-harness-preregistered.json").read_text(encoding="utf-8"))
    features, provenance = load_features(workspace, official)
    current, current_mask = features["Mx4"]["values"], features["Mx4"]["defined"]
    queen, queen_mask = features["S1"]["values"], features["S1"]["defined"]
    geometries, geometry_hashes, geometry_columns = read_provided_geometries(config, workspace, official)

    n = len(official)
    rook_lag = np.full(n, np.nan, dtype=np.float64)
    rook_defined = np.zeros(n, dtype=bool)
    rook_neighbor_count = np.zeros(n, dtype=np.int32)
    rook_defined_neighbor_count = np.zeros(n, dtype=np.int32)
    innovation = np.full(n, np.nan, dtype=np.float64)
    innovation_defined = np.zeros(n, dtype=bool)
    deciles = np.full(n, -1, dtype=np.int16)
    peer_mean = np.full(n, np.nan, dtype=np.float64)
    peer_count = np.zeros(n, dtype=np.int32)
    region_reports: dict[str, Any] = {}
    geometry_stats: dict[str, Any] = {}
    queen_region_nmis: dict[str, float] = {}
    all_rook_values: list[np.ndarray] = []
    all_innovation_values: list[np.ndarray] = []
    all_queen_values: list[np.ndarray] = []
    all_queen_innovation_values: list[np.ndarray] = []

    for region in REGIONS:
        indices = np.flatnonzero(np.asarray(regions) == region)
        local_geoids = [geoids[i] for i in indices]
        local_current, local_mask = current[indices], current_mask[indices]
        local_queen, local_queen_mask = queen[indices], queen_mask[indices]
        local_geometries = geometries[region]
        neighbors = build_rook_adjacency(local_geometries)
        local_lag = rook_neighbor_mean(local_current, local_mask, neighbors)
        result = leave_one_out_bin_mean_residual(local_geoids, local_current, local_mask,
                                                  local_lag["lag"], local_lag["defined"], bins=N_BINS)
        rook_lag[indices] = local_lag["lag"]
        rook_defined[indices] = local_lag["defined"]
        rook_neighbor_count[indices] = local_lag["neighbor_count"]
        rook_defined_neighbor_count[indices] = local_lag["defined_neighbor_count"]
        innovation[indices] = result["innovation"]
        innovation_defined[indices] = result["defined"]
        deciles[indices] = result["current_mx4_decile"]
        peer_mean[indices] = result["peer_bin_mean"]
        peer_count[indices] = result["peer_count"]
        if np.any(result["defined"] & ~local_lag["defined"]):
            raise AssertionError("defined rook innovation must have a defined rook lag")

        stats = measure_region_nmi(local_current, local_mask, result["innovation"], result["defined"])
        queen_result = leave_one_out_bin_mean_residual(local_geoids, local_current, local_mask,
                                                       local_queen, local_queen_mask, bins=N_BINS)
        queen_stats = measure_region_nmi(local_current, local_mask, queen_result["innovation"],
                                         queen_result["defined"])
        if stats["nmi"] is None or queen_stats["nmi"] is None:
            raise ValueError(f"{region}: insufficient overlap to calculate innovation NMI")
        queen_region_nmis[region] = float(queen_stats["nmi"])
        rook_defined_n = int(result["defined"].sum())
        region_rook = result["innovation"][result["defined"]]
        region_lag = local_lag["lag"][result["defined"]]
        region_queen_innov = queen_result["innovation"][queen_result["defined"]]
        region_queen_lag = local_queen[queen_result["defined"]]
        rook_variance = float(region_rook.var(ddof=0)) if rook_defined_n else 0.0
        lag_variance = float(region_lag.var(ddof=0)) if rook_defined_n else 0.0
        region_reports[region] = {
            "official_ids": len(indices),
            "Mx4_F_defined_ids": int(local_mask.sum()),
            "Queen_S1_lag_defined_ids": int(local_queen_mask.sum()),
            "rook_neighbors_min": int(local_lag["neighbor_count"].min()),
            "rook_neighbors_p50": float(np.quantile(local_lag["neighbor_count"], .5)),
            "rook_neighbors_max": int(local_lag["neighbor_count"].max()),
            "rook_lag_defined_ids": int(local_lag["defined"].sum()),
            "rook_innovation_defined_ids": rook_defined_n,
            "rook_innovation_undefined_ids": int(len(indices) - rook_defined_n),
            "rook_innovation_coverage_fraction": float(rook_defined_n / len(indices)),
            "rook_peer_count_min": int(result["peer_count"][result["defined"]].min()) if rook_defined_n else None,
            "rook_peer_count_max": int(result["peer_count"][result["defined"]].max()) if rook_defined_n else None,
            "rook_lag_distribution_on_innovation_support": distribution(region_lag, np.ones(len(region_lag), bool)),
            "rook_innovation_distribution": distribution(region_rook, np.ones(len(region_rook), bool)),
            "rook_remaining_variance_ratio": rook_variance / lag_variance if lag_variance > 0 else None,
            "rook_innovation_NMI_vs_current_Mx4_F": stats,
            "queen_innovation_defined_ids": int(queen_result["defined"].sum()),
            "queen_innovation_NMI_vs_current_Mx4_F": queen_stats,
            "queen_remaining_variance_ratio": float(region_queen_innov.var(ddof=0) / region_queen_lag.var(ddof=0))
            if region_queen_lag.size and region_queen_lag.var(ddof=0) > 0 else None,
        }
        geometry_stats[region] = {
            "official_ids": len(indices), "rook_undirected_edges": int(sum(map(len, neighbors)) // 2),
            "isolated_tracts": int(sum(len(adj) == 0 for adj in neighbors)),
            "geometry_sha256": geometry_hashes[region],
            "provided_layer": "census-acs-housing",
            "columns_read": geometry_columns[region],
        }
        all_rook_values.append(region_lag)
        all_innovation_values.append(region_rook)
        all_queen_values.append(local_queen[queen_result["defined"]])
        all_queen_innovation_values.append(region_queen_innov)

    coverage = float(innovation_defined.sum() / n)
    pooled_lag = np.concatenate(all_rook_values)
    pooled_innovation = np.concatenate(all_innovation_values)
    lag_variance = float(pooled_lag.var(ddof=0)) if pooled_lag.size else 0.0
    innovation_variance = float(pooled_innovation.var(ddof=0)) if pooled_innovation.size else 0.0
    variance_ratio = innovation_variance / lag_variance if lag_variance > 0 else None
    max_region = max(REGIONS, key=lambda r: float(region_reports[r]["rook_innovation_NMI_vs_current_Mx4_F"]["nmi"]))
    max_nmi = float(region_reports[max_region]["rook_innovation_NMI_vs_current_Mx4_F"]["nmi"])

    # Reproduce the prior Queen diagnostic from frozen S1 and Mx4 inputs as a before-check.
    queen_lag_all = np.concatenate(all_queen_values)
    queen_innov_all = np.concatenate(all_queen_innovation_values)
    queen_ratio = float(queen_innov_all.var(ddof=0) / queen_lag_all.var(ddof=0))
    source_queen = queen_report["measure_after"]
    if (int(sum(region_reports[r]["queen_innovation_defined_ids"] for r in REGIONS)) != queen_report["scope"]["innovation_defined_ids"]
            or not np.isclose(max(queen_region_nmis.values()), source_queen["maximum_within_region_NMI"], rtol=0, atol=1e-12)
            or not np.isclose(queen_ratio, source_queen["pooled_remaining_variance_ratio"], rtol=0, atol=1e-12)):
        raise ValueError("frozen Queen S1 baseline did not reproduce from current Mx4/S1 artifacts")

    joined = {"lag": rook_lag, "defined": rook_defined, "innovation": innovation,
              "defined_innovation": innovation_defined, "decile": deciles,
              "peer_bin_mean": peer_mean, "peer_count": peer_count}
    output = output_table(geoids, regions, current, current_mask, queen, queen_mask,
                          rook_neighbor_count, rook_defined_neighbor_count, joined)
    payload = parquet_bytes(output)
    if payload != parquet_bytes(output):
        raise ValueError("rook innovation artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    registry_sha_after = sha256_file(registry_path)
    frozen_after = {idea: sha256_file(Path(info["path"])) for idea, info in provenance["inputs"].items()}
    frozen_unchanged = registry_sha_after == registry_sha_before and all(
        frozen_after[idea] == provenance["inputs"][idea]["sha256"] for idea in ("Mx4", "S1"))
    if not frozen_unchanged:
        raise AssertionError("rook screen modified frozen Mx4/S1 or registry")
    acceptance = {
        "exact_9379_official_geoid_region_partition": output.num_rows == 9379 and len(set(geoids)) == 9379,
        "rook_innovation_coverage_at_least_98_59_percent": coverage >= MIN_COVERAGE,
        "pooled_rook_residual_variance_ratio_at_least_10_percent": variance_ratio is not None and variance_ratio >= MIN_REMAINING_VARIANCE_RATIO,
        "maximum_region_rook_innovation_NMI_below_0_80": max_nmi < MAX_NMI,
        "rook_graph_is_symmetric_and_self_excluding": True,
        "provided_ACS_polygons_only": all(geometry_columns[r] == [f"{r}-census-acs-housing:GEOID,geometry"] for r in REGIONS),
        "frozen_Queen_S1_Mx4_and_registry_unchanged": frozen_unchanged,
    }
    acceptance["acceptance_pass"] = all(acceptance.values())
    report = {
        "schema_version": 1,
        "audit": "S1 rook-neighbor Mx4 F leave-one-out empirical-decile innovation; label-free unregistered sensitivity",
        "method": {
            "rook_adjacency": "Provided ACS tract polygons only; STRtree candidate pairs must touch and have positive shared-boundary length. Corner-only queen contacts are excluded; adjacency is symmetric and self-excluding.",
            "rook_lag": "For each tract, mean of defined current Mx4 F among its rook neighbors; the focal tract is not included. Null if no defined rook neighbor.",
            "innovation": "Within each named region, bin current Mx4 F at empirical linear-decile thresholds (ties share bins); subtract the mean rook lag of other pairwise-defined tracts in the same current-Mx4 decile, excluding the focal tract. Singleton bins remain null.",
            "NMI": "Within-region arithmetic normalized mutual information 2*MI/(H(Mx4 F)+H(innovation)), using the same deterministic feature encoder as the prior S1 nonlinear screen.",
            "variance_ratio": "Population variance of pooled rook innovations divided by population variance of rook lag values on the innovation-defined support.",
            "input_scope": "Only provided census-acs-housing GEOID/geometry columns and frozen Mx4 F plus Queen S1 lagged-Mx4 F emissions were read. No other S1 channels.",
            "unregistered": True,
        },
        "scope": {
            "official_ids": n, "exact_official_geoid_region_partition": True,
            "rook_innovation_defined_ids": int(innovation_defined.sum()),
            "rook_innovation_undefined_ids": int(n - innovation_defined.sum()),
            "coverage_fraction": coverage, "labels_read": False,
            "sample_score_values_read": False, "removed_or_equivalent_reference_data_read": False,
            "external_layers_read": False, "leaderboard_feedback_used": False,
            "frozen_Queen_S1_or_Mx4_modified": False, "registry_changed_or_feature_registered": False,
            "uploads": 0,
        },
        "measure_before": {
            "queen_maximum_within_region_NMI": source_queen["maximum_within_region_NMI"],
            "queen_coverage_fraction": queen_report["scope"]["coverage_fraction"],
            "queen_pooled_remaining_variance_ratio": source_queen["pooled_remaining_variance_ratio"],
            "queen_source_report_sha256": sha256_file(queen_report_path),
            "queen_recomputed_maximum_region_NMI": max(queen_region_nmis.values()),
            "queen_recomputed_pooled_remaining_variance_ratio": queen_ratio,
        },
        "measure_after": {
            "rook_maximum_within_region_NMI": max_nmi, "region_with_maximum_NMI": max_region,
            "pooled_rook_innovation_variance": innovation_variance,
            "pooled_rook_lag_variance_on_innovation_support": lag_variance,
            "pooled_rook_remaining_variance_ratio": variance_ratio,
            "rook_lag_distribution_on_innovation_support": distribution(pooled_lag, np.ones(len(pooled_lag), bool)),
            "rook_innovation_distribution": distribution(pooled_innovation, np.ones(len(pooled_innovation), bool)),
            "regions": region_reports, "geometry": geometry_stats,
        },
        "acceptance_thresholds": {"minimum_coverage_inclusive": MIN_COVERAGE,
                                   "minimum_remaining_variance_ratio_inclusive": MIN_REMAINING_VARIANCE_RATIO,
                                   "maximum_region_NMI_exclusive": MAX_NMI, "empirical_deciles": N_BINS},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_sha,
            "geometry_source": "Provided challenge package ACS housing tract layer, anonymous public Source Cooperative object path from frozen harness config",
            "geometry_layer_columns": {r: geometry_columns[r] for r in REGIONS},
            "geometry_wkb_sha256_by_region": geometry_hashes,
            "frozen_input_artifacts": {idea: {**provenance["inputs"][idea], "sha256_after": frozen_after[idea]}
                                       for idea in ("Mx4", "S1")},
            "registry_sha256_before": registry_sha_before, "registry_sha256_after": registry_sha_after,
            "harness_report_sha256": sha256_file(provenance["harness_path"]),
            "preregistered_harness_config_sha256": sha256_file(ROOT / "config/bias60-harness-preregistered.json"),
            "script_sha256": sha256_file(Path(__file__).resolve()),
        },
        "output_artifact": {"path": str(artifact_path), "rows": output.num_rows,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "columns": output.column_names, "deterministic_bytes": True},
        "interpretation": "The rook-neighbor innovation meets the registered distinctness gates; retain only as an unregistered sensitivity and do not modify or integrate frozen Queen S1 or Mx4." if acceptance["acceptance_pass"] else
        "The rook-neighbor variant fails one or more registered thresholds; retain the Queen diagnostic only, with frozen S1/Mx4 and registry unchanged.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/s1-rook-mx4-innovation.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "s1-rook-mx4-innovation" / "s1-rook-mx4-innovation.parquet"
    report = audit(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"],
                      "measure_after": {key: report["measure_after"][key] for key in (
                          "rook_maximum_within_region_NMI", "region_with_maximum_NMI",
                          "pooled_rook_remaining_variance_ratio")},
                      "coverage_fraction": report["scope"]["coverage_fraction"],
                      "output_artifact": report["output_artifact"],
                      "report": str(report_path.relative_to(ROOT))}, indent=2, sort_keys=True))
    return 0 if report["acceptance"]["acceptance_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
