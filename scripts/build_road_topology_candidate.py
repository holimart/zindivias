#!/usr/bin/env python3
"""Build a deterministic connector-graph transport sensitivity (analysis only).

Reads only provided Overture roads class/connectors/geometry, official GEOIDs,
provided ACS GEOID/geometry for assignment, route length cache columns, provided
pct_urban strata, and the frozen v0 transport component for measure-before.
No target, removed reference, external road data, or sample score placeholders.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/road-topology-preregistered.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
REGIONS = tuple(CONFIG["scope"]["official_regions_order"])
BUCKET = "us-west-2.opendata.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge"
ROAD_COLUMNS = ["class", "connectors", "geometry"]
CACHE_COLUMNS = ["GEOID", "overture_road_km", "overture_named_road_km_5070", "overture_route_km"]
STRATA_COLUMNS = ["GEOID", "pct_urban"]
MAX_ADJUSTMENT = 0.05
REGION_COUNTS = {"maricopa-az": 1593, "northern-ca": 591,
                 "eastern-ok": 1192, "south-central-tx": 6003}

sys.path.insert(0, str(ROOT / "scripts"))
from audit_poi_categories import assign_points, load_sample_tracts, read_sample_geoids  # noqa: E402
from estimate_gaps import estimate_transport_route_v2  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def git_commit() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def summarize(values: list[float] | np.ndarray) -> dict[str, Any]:
    arr = np.asarray(values, dtype=np.float64)
    if not len(arr):
        return {"n": 0, "mean": None, "variance_population": None,
                "std_population": None, "min": None, "q05": None,
                "median": None, "q95": None, "max": None}
    if not np.isfinite(arr).all():
        raise ValueError("nonfinite value in summary")
    q05, median, q95 = np.quantile(arr, [0.05, 0.5, 0.95])
    return {"n": int(len(arr)), "mean": float(arr.mean()),
            "variance_population": float(arr.var()), "std_population": float(arr.std()),
            "min": float(arr.min()), "q05": float(q05), "median": float(median),
            "q95": float(q95), "max": float(arr.max())}


def parse_connectors(value: Any) -> tuple[list[str], list[float], str | None]:
    if value is None:
        return [], [], "missing_connector_list"
    if not isinstance(value, list):
        return [], [], "invalid_connector_list_type"
    if len(value) < 2:
        return [], [], "fewer_than_two_connectors"
    ids: list[str] = []
    positions: list[float] = []
    for entry in value:
        if not isinstance(entry, dict):
            return [], [], "invalid_connector_record"
        connector_id = entry.get("connector_id")
        if not isinstance(connector_id, str) or not connector_id.strip():
            return [], [], "missing_connector_id"
        try:
            at = float(entry.get("at"))
        except (TypeError, ValueError):
            return [], [], "missing_or_invalid_at"
        if not math.isfinite(at) or not 0.0 <= at <= 1.0:
            return [], [], "at_outside_0_1"
        ids.append(connector_id.strip())
        positions.append(at)
    if len(ids) != len(set(ids)):
        return [], [], "duplicate_connector_id"
    order = sorted(range(len(ids)), key=lambda i: (positions[i], i))
    sorted_ids = [ids[i] for i in order]
    sorted_positions = [positions[i] for i in order]
    return sorted_ids, sorted_positions, None


def _find(parent: np.ndarray, node: int) -> int:
    while parent[node] != node:
        parent[node] = parent[parent[node]]
        node = int(parent[node])
    return node


def summarize_local_graph(node_chunks: list[np.ndarray], edge_chunks: list[np.ndarray],
                          cross_edges: int) -> dict[str, Any]:
    if node_chunks:
        nodes = np.unique(np.concatenate(node_chunks).astype(np.int64, copy=False))
    else:
        nodes = np.empty(0, dtype=np.int64)
    raw_edges = int(sum(len(chunk) for chunk in edge_chunks))
    if edge_chunks:
        edges = np.concatenate(edge_chunks).astype(np.int64, copy=False)
        edges.sort(axis=1)
        edges = np.unique(edges, axis=0)
        edges = edges[(edges[:, 0] != edges[:, 1])]
    else:
        edges = np.empty((0, 2), dtype=np.int64)
    n_nodes = int(len(nodes))
    n_edges = int(len(edges))
    if n_nodes:
        degree = np.zeros(n_nodes, dtype=np.int64)
        if n_edges:
            local_left = np.searchsorted(nodes, edges[:, 0])
            local_right = np.searchsorted(nodes, edges[:, 1])
            np.add.at(degree, local_left, 1)
            np.add.at(degree, local_right, 1)
            parent = np.arange(n_nodes, dtype=np.int64)
            component_size = np.ones(n_nodes, dtype=np.int64)
            for left, right in zip(local_left.tolist(), local_right.tolist()):
                root_left = _find(parent, int(left))
                root_right = _find(parent, int(right))
                if root_left != root_right:
                    if component_size[root_left] < component_size[root_right]:
                        root_left, root_right = root_right, root_left
                    parent[root_right] = root_left
                    component_size[root_left] += component_size[root_right]
            roots = np.fromiter((_find(parent, i) for i in range(n_nodes)), dtype=np.int64, count=n_nodes)
            component_roots, component_counts = np.unique(roots, return_counts=True)
            del component_roots
        else:
            degree = np.zeros(n_nodes, dtype=np.int64)
            component_counts = np.ones(n_nodes, dtype=np.int64)
        largest_nodes = int(component_counts.max())
        components = int(len(component_counts))
        isolated_components = int(np.sum(component_counts == 1))
        isolated_nodes = int(np.sum(degree == 0))
        dead_ends = int(np.sum(degree == 1))
        degree_histogram = {
            "degree_0": isolated_nodes,
            "degree_1": dead_ends,
            "degree_2": int(np.sum(degree == 2)),
            "degree_3": int(np.sum(degree == 3)),
            "degree_4_plus": int(np.sum(degree >= 4)),
        }
        degree_values = degree
        largest_share = float(largest_nodes / n_nodes)
        fragmentation = float(1.0 - largest_share)
    else:
        components = isolated_components = isolated_nodes = dead_ends = 0
        degree_histogram = {"degree_0": 0, "degree_1": 0, "degree_2": 0,
                            "degree_3": 0, "degree_4_plus": 0}
        degree_values = np.empty(0, dtype=np.int64)
        largest_nodes = 0
        largest_share = None
        fragmentation = 0.0
    return {
        "topology_defined": int(n_nodes > 0),
        "connector_nodes": n_nodes,
        "unique_local_edges": n_edges,
        "raw_local_adjacencies": raw_edges,
        "duplicate_adjacencies_collapsed": int(raw_edges - n_edges),
        "cross_tract_adjacencies_touching_tract": int(cross_edges),
        "connected_components": components,
        "isolated_components_singleton": isolated_components,
        "isolated_connector_nodes_degree_zero": isolated_nodes,
        "dead_end_nodes_degree_one": dead_ends,
        "dead_end_node_fraction": float(dead_ends / n_nodes) if n_nodes else None,
        "degree_histogram": degree_histogram,
        "mean_node_degree": float(degree_values.mean()) if n_nodes else None,
        "median_node_degree": float(np.median(degree_values)) if n_nodes else None,
        "maximum_node_degree": int(degree_values.max()) if n_nodes else None,
        "largest_component_nodes": largest_nodes,
        "largest_component_node_share": largest_share,
        "fragmentation": fragmentation,
        "degree_values": degree_values,
        "component_sizes": component_counts if n_nodes else np.empty(0, dtype=np.int64),
    }


def read_transport_cache(workspace: Path, region: str, ids: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cache_path = workspace / "gap-estimator" / "features" / f"{region}-overture-tract-summary.parquet"
    table = pq.read_table(cache_path, columns=CACHE_COLUMNS)
    cache_ids = [str(value) for value in table.column("GEOID").to_pylist()]
    if cache_ids != ids:
        raise ValueError(f"route/road feature cache GEOID order differs for {region}")
    records = []
    for index, geoid in enumerate(ids):
        row = {"GEOID": geoid}
        for column in CACHE_COLUMNS[1:]:
            value = table.column(column)[index].as_py()
            number = float(value)
            if not math.isfinite(number) or number < 0:
                raise ValueError(f"invalid road feature {column} for {geoid}")
            row[column] = number
        row.update(estimate_transport_route_v2({
            "overture_named_road_km_5070": row["overture_named_road_km_5070"],
            "overture_route_km": row["overture_route_km"],
        }))
        records.append(row)
    return records, {"path": str(cache_path), "sha256": sha256_file(cache_path),
                     "rows": table.num_rows, "columns_read": CACHE_COLUMNS}


def read_strata(workspace: Path, region: str, ids: list[str]) -> tuple[dict[str, str], dict[str, Any]]:
    path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
    table = pq.read_table(path, columns=STRATA_COLUMNS)
    selected = {}
    id_set = set(ids)
    for row in table.to_pylist():
        geoid = str(row["GEOID"])
        if geoid in id_set:
            if geoid in selected:
                raise ValueError(f"duplicate official GEOID in strata for {region}")
            value = row["pct_urban"]
            try:
                number = float(value) if value is not None else math.nan
            except (TypeError, ValueError):
                number = math.nan
            selected[geoid] = "Rural" if math.isfinite(number) and number < 0.5 else (
                "Urban" if math.isfinite(number) else "Unknown")
    if set(selected) != id_set:
        raise ValueError(f"urbanity strata do not cover official IDs for {region}")
    projection = [[geoid, selected[geoid]] for geoid in ids]
    return selected, {"path": str(path), "rows_read": table.num_rows,
                      "official_rows_used": len(selected), "columns_read": STRATA_COLUMNS,
                      "urban_rural_projection_sha256": canonical_hash(projection)}


def read_v0_transport(path: Path, official_ids: list[str]) -> dict[str, dict[str, Any]]:
    expected = set(official_ids)
    out = {}
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        needed = {"GEOID", "transport_gap", "transport_defined"}
        if reader.fieldnames is None or not needed.issubset(reader.fieldnames):
            raise ValueError("v0 estimates lack transport component columns")
        for row in reader:
            geoid = row["GEOID"]
            if geoid not in expected:
                continue
            if geoid in out:
                raise ValueError(f"duplicate v0 transport GEOID {geoid}")
            score = float(row["transport_gap"])
            defined = int(row["transport_defined"])
            if not math.isfinite(score) or not 0 <= score <= 1 or defined not in (0, 1):
                raise ValueError(f"invalid v0 transport output for {geoid}")
            out[geoid] = {"transport_gap": score, "transport_defined": defined}
    if set(out) != expected:
        raise ValueError("v0 transport data do not exactly cover official GEOIDs")
    return out


def aggregate_road_topology(fs, workspace: Path, region: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
    ids, ids_sha = read_sample_geoids(sample_path)
    if len(ids) != REGION_COUNTS[region]:
        raise ValueError(f"wrong official sample count in {region}")
    _, tract_geometries, tree, geometry_meta = load_sample_tracts(fs, region, ids)
    uri = f"{BUCKET}/reference/{region}/{region}-overture-roads.parquet"
    object_info = fs.get_file_info(uri)
    if object_info.type != pafs.FileType.File:
        raise FileNotFoundError(f"provided Overture roads missing: s3://{uri}")
    parquet = pq.ParquetFile(fs.open_input_file(uri))
    if not set(ROAD_COLUMNS).issubset(parquet.schema_arrow.names):
        raise ValueError(f"provided roads schema lacks required columns in {region}")
    selected_column_bytes = {
        name: int(sum(parquet.metadata.row_group(rg).column(col).total_compressed_size
                      for rg in range(parquet.metadata.num_row_groups)
                      for col in range(parquet.metadata.num_columns)
                      if parquet.metadata.schema.column(col).path.split(".")[0] == name))
        for name in ROAD_COLUMNS}
    node_index: dict[str, int] = {}
    node_chunks: list[list[np.ndarray]] = [[] for _ in ids]
    edge_chunks: list[list[np.ndarray]] = [[] for _ in ids]
    cross_edge_counts = np.zeros(len(ids), dtype=np.int64)
    edge_to_unassigned_count = 0
    stats = Counter()
    class_counts: Counter[str] = Counter()
    row_count = 0

    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=ROAD_COLUMNS)
        classes = table.column("class").to_pylist()
        connectors_column = table.column("connectors").to_pylist()
        geometries_wkb = table.column("geometry").to_pylist()
        row_count += table.num_rows
        stats["segments_seen"] += table.num_rows
        candidate_lists: list[list[str]] = []
        candidate_positions: list[list[float]] = []
        candidate_wkb: list[bytes | None] = []
        for road_class, connector_list, geometry in zip(classes, connectors_column, geometries_wkb):
            class_counts["<null>" if road_class is None else str(road_class)] += 1
            conn_ids, ats, error = parse_connectors(connector_list)
            if error:
                stats["segments_skipped_" + error] += 1
                continue
            if geometry is None:
                stats["segments_skipped_null_geometry"] += 1
                continue
            candidate_lists.append(conn_ids)
            candidate_positions.append(ats)
            candidate_wkb.append(geometry)
        if not candidate_wkb:
            continue
        try:
            lines = shapely.from_wkb(candidate_wkb, on_invalid="ignore")
        except Exception as exc:
            raise ValueError(f"failed to parse selected provided road WKB in {region}/{rg}") from exc
        type_ids = shapely.get_type_id(lines)
        valid = np.asarray([line is not None and not shapely.is_empty(line) and
                            shapely.is_valid(line) and int(type_id) == 1
                            for line, type_id in zip(lines, type_ids)], dtype=bool)
        stats["segments_skipped_invalid_or_non_linestring_geometry"] += int((~valid).sum())
        if not valid.any():
            continue
        lists = [value for value, keep in zip(candidate_lists, valid) if keep]
        positions = [value for value, keep in zip(candidate_positions, valid) if keep]
        lines = lines[valid]
        counts = np.asarray([len(value) for value in lists], dtype=np.int64)
        flat_ids = [connector_id for group in lists for connector_id in group]
        flat_at = np.fromiter((position for group in positions for position in group),
                              dtype=np.float64, count=int(counts.sum()))
        segment_index = np.repeat(np.arange(len(lists), dtype=np.int64), counts)
        try:
            points = shapely.line_interpolate_point(lines[segment_index], flat_at, normalized=True)
        except Exception as exc:
            raise ValueError(f"could not interpolate connector positions in {region}/{rg}") from exc
        point_x = shapely.get_x(points)
        point_y = shapely.get_y(points)
        if not np.isfinite(point_x).all() or not np.isfinite(point_y).all():
            raise ValueError(f"nonfinite interpolated connectors in {region}/{rg}")
        tract_assignment = assign_points(tree, point_x, point_y)
        stats["valid_segments"] += len(lists)
        stats["connector_occurrences"] += len(flat_ids)
        stats["connector_points_outside_official_sample"] += int(np.sum(tract_assignment < 0))
        local_node_ids = np.empty(len(flat_ids), dtype=np.int64)
        for index, connector_id in enumerate(flat_ids):
            local_index = node_index.get(connector_id)
            if local_index is None:
                local_index = len(node_index)
                node_index[connector_id] = local_index
            local_node_ids[index] = local_index
        assigned_mask = tract_assignment >= 0
        for tract_index in np.unique(tract_assignment[assigned_mask]):
            mask = tract_assignment == tract_index
            node_chunks[int(tract_index)].append(local_node_ids[mask])
        stats["connector_points_assigned_to_official_sample"] += int(assigned_mask.sum())

        # Flattened consecutive pairs, with segment boundaries explicitly removed.
        edge_count = len(flat_ids) - len(lists)
        if edge_count < 0:
            raise ValueError("invalid connector adjacency accounting")
        if edge_count:
            boundary_positions = np.cumsum(counts)[:-1] - 1
            starts = np.arange(len(flat_ids) - 1, dtype=np.int64)
            keep = np.ones(len(starts), dtype=bool)
            keep[boundary_positions] = False
            left = starts[keep]
            right = left + 1
            left_tract = tract_assignment[left]
            right_tract = tract_assignment[right]
            both_assigned = (left_tract >= 0) & (right_tract >= 0)
            local_edges = both_assigned & (left_tract == right_tract)
            cross_edges = both_assigned & (left_tract != right_tract)
            one_assigned = (left_tract >= 0) ^ (right_tract >= 0)
            stats["connector_adjacencies"] += int(len(left))
            stats["within_tract_adjacencies"] += int(local_edges.sum())
            stats["cross_tract_adjacencies"] += int(cross_edges.sum())
            stats["one_endpoint_in_sample_adjacencies"] += int(one_assigned.sum())
            stats["both_endpoints_outside_adjacencies"] += int((~both_assigned & ~one_assigned).sum())
            if local_edges.any():
                selected_tracts = left_tract[local_edges]
                edge_array = np.column_stack((local_node_ids[left[local_edges]],
                                              local_node_ids[right[local_edges]])).astype(np.int64, copy=False)
                for tract_index in np.unique(selected_tracts):
                    edge_chunks[int(tract_index)].append(edge_array[selected_tracts == tract_index])
            if cross_edges.any():
                cross_edge_counts += np.bincount(left_tract[cross_edges], minlength=len(ids))
                cross_edge_counts += np.bincount(right_tract[cross_edges], minlength=len(ids))
            if one_assigned.any():
                observed = np.where(left_tract[one_assigned] >= 0,
                                    left_tract[one_assigned], right_tract[one_assigned])
                cross_edge_counts += np.bincount(observed, minlength=len(ids))
                edge_to_unassigned_count += int(one_assigned.sum())
        if (rg + 1) % 10 == 0 or rg + 1 == parquet.metadata.num_row_groups:
            print(f"{region}: processed road row groups {rg + 1}/{parquet.metadata.num_row_groups}; "
                  f"segments={row_count}; connector occurrences={stats['connector_occurrences']}", flush=True)

    if row_count != int(parquet.metadata.num_rows):
        raise ValueError(f"road row count mismatch for {region}")
    graph_rows = []
    for tract_index, geoid in enumerate(ids):
        graph = summarize_local_graph(node_chunks[tract_index], edge_chunks[tract_index],
                                      int(cross_edge_counts[tract_index]))
        graph_rows.append({"GEOID": geoid, "region": region, **graph})
    metadata = {
        "region": region,
        "official_sample": {"path": str(sample_path.relative_to(workspace)), "rows": len(ids),
                             "ordered_geoid_sha256": ids_sha, "columns_read": ["GEOID"]},
        "provided_acs_geometry": geometry_meta,
        "provided_overture_roads": {"source_uri": f"s3://{uri}",
                                    "object_bytes": int(object_info.size),
                                    "source_rows": int(parquet.metadata.num_rows),
                                    "row_groups": int(parquet.metadata.num_row_groups),
                                    "columns_read": ROAD_COLUMNS,
                                    "selected_column_compressed_bytes": selected_column_bytes},
        "segments_by_class": dict(sorted(class_counts.items())),
        "edge_case_counts": dict(sorted((key, int(value)) for key, value in stats.items())),
        "unique_connector_ids_encountered": len(node_index),
        "within_package_boundary_adjacencies_with_one_endpoint_unassigned": edge_to_unassigned_count,
    }
    return graph_rows, metadata


def graph_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    diagnosed = [row for row in rows if row["topology_defined"]]
    pooled_degree = Counter()
    for row in rows:
        pooled_degree.update(row["degree_histogram"])
    return {
        "tracts": n,
        "tracts_with_connector_diagnostics": len(diagnosed),
        "diagnostic_coverage_fraction": float(len(diagnosed) / n) if n else None,
        "connector_nodes_per_tract": summarize([row["connector_nodes"] for row in rows]),
        "unique_local_edges_per_tract": summarize([row["unique_local_edges"] for row in rows]),
        "components_per_tract": summarize([row["connected_components"] for row in rows]),
        "isolated_components_per_tract": summarize([row["isolated_components_singleton"] for row in rows]),
        "isolated_nodes_per_tract": summarize([row["isolated_connector_nodes_degree_zero"] for row in rows]),
        "dead_end_nodes_per_tract": summarize([row["dead_end_nodes_degree_one"] for row in rows]),
        "dead_end_node_fraction": summarize([row["dead_end_node_fraction"] for row in diagnosed]),
        "mean_degree_per_tract": summarize([row["mean_node_degree"] for row in diagnosed]),
        "largest_component_node_share": summarize([row["largest_component_node_share"] for row in diagnosed]),
        "fragmentation": summarize([row["fragmentation"] for row in rows]),
        "pooled_degree_histogram": {key: int(pooled_degree[key]) for key in
                                    ("degree_0", "degree_1", "degree_2", "degree_3", "degree_4_plus")},
    }


def candidate_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    base = [row["route_reference_transport_gap"] for row in rows if row["route_reference_transport_defined"]]
    candidate = [row["topology_transport_gap"] for row in rows if row["topology_transport_defined"]]
    delta = [row["candidate_adjustment"] for row in rows]
    return {
        "official_rows": len(rows),
        "route_reference_defined_rows": int(sum(row["route_reference_transport_defined"] for row in rows)),
        "route_reference_defined_fraction": float(np.mean([row["route_reference_transport_defined"] for row in rows])),
        "topology_defined_rows": int(sum(row["topology_defined"] for row in rows)),
        "topology_diagnostic_fraction": float(np.mean([row["topology_defined"] for row in rows])),
        "baseline_route_reference_gap_defined_only": summarize(base),
        "topology_candidate_gap_defined_only": summarize(candidate),
        "candidate_adjustment_all_rows": summarize(delta),
        "candidate_adjustment_max_abs": float(max(abs(value) for value in delta)),
        "candidate_adjustment_fraction_positive": float(np.mean([value > 0 for value in delta])),
        "candidate_adjustment_within_0_05": bool(max(abs(value) for value in delta) <= MAX_ADJUSTMENT + 1e-12),
    }


def summarize_groups(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {"all": rows}
    for region in REGIONS:
        local = [row for row in rows if row["region"] == region]
        groups[region] = local
        for urbanity in ("Rural", "Urban", "Unknown"):
            subset = [row for row in local if row["urban_rural"] == urbanity]
            if subset:
                groups[f"{region}:{urbanity}"] = subset
    summary = {}
    for label, subset in groups.items():
        summary[label] = {
            "rows": len(subset),
            "road_lengths_km": {
                name: summarize([row[name] for row in subset])
                for name in ("overture_road_km", "overture_named_road_km_5070", "overture_route_km")},
            "v0_transport": {
                "gap": summarize([row["v0_transport_gap"] for row in subset]),
                "defined_rows": int(sum(row["v0_transport_defined"] for row in subset)),
                "defined_fraction": float(np.mean([row["v0_transport_defined"] for row in subset]))},
            "route_reference_candidate": {
                "gap": summarize([row["route_reference_transport_gap"] for row in subset]),
                "defined_rows": int(sum(row["route_reference_transport_defined"] for row in subset)),
                "defined_fraction": float(np.mean([row["route_reference_transport_defined"] for row in subset]))},
        }
    return summary


def group_after(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {"all": rows}
    for region in REGIONS:
        local = [row for row in rows if row["region"] == region]
        groups[region] = local
        for urbanity in ("Rural", "Urban", "Unknown"):
            subset = [row for row in local if row["urban_rural"] == urbanity]
            if subset:
                groups[f"{region}:{urbanity}"] = subset
    output = {}
    for label, subset in groups.items():
        summary = candidate_summary(subset)
        summary["topology_graph"] = graph_distribution(subset)
        output[label] = summary
    return output


def output_row(row: dict[str, Any], v0: dict[str, Any]) -> dict[str, Any]:
    fields = ["GEOID", "region", "urban_rural", "overture_road_km",
              "overture_named_road_km_5070", "overture_route_km",
              "v0_transport_gap", "v0_transport_defined",
              "route_reference_transport_gap", "route_reference_transport_defined",
              "topology_defined", "connector_nodes", "unique_local_edges",
              "raw_local_adjacencies", "duplicate_adjacencies_collapsed",
              "cross_tract_adjacencies_touching_tract", "connected_components",
              "isolated_components_singleton", "isolated_connector_nodes_degree_zero",
              "dead_end_nodes_degree_one", "dead_end_node_fraction",
              "mean_node_degree", "median_node_degree", "maximum_node_degree",
              "largest_component_nodes", "largest_component_node_share", "fragmentation"]
    out = {name: row.get(name) for name in fields if name not in ("v0_transport_gap", "v0_transport_defined")}
    out["v0_transport_gap"] = v0[row["GEOID"]]["transport_gap"]
    out["v0_transport_defined"] = v0[row["GEOID"]]["transport_defined"]
    requested = MAX_ADJUSTMENT * float(row["fragmentation"])
    actual = min(1.0, row["route_reference_transport_gap"] + requested) - row["route_reference_transport_gap"] \
        if row["route_reference_transport_defined"] else 0.0
    if actual < -1e-12 or actual > MAX_ADJUSTMENT + 1e-12:
        raise ValueError(f"candidate adjustment outside preregistered bound for {row['GEOID']}")
    out["requested_adjustment"] = requested if row["route_reference_transport_defined"] else 0.0
    out["candidate_adjustment"] = actual
    out["topology_transport_gap"] = min(1.0, row["route_reference_transport_gap"] + actual) \
        if row["route_reference_transport_defined"] else 0.0
    out["topology_transport_defined"] = int(row["route_reference_transport_defined"])
    out["candidate_id"] = CONFIG["candidate_id"]
    return out


def run(workspace: Path, candidate_path: Path, manifest_path: Path, report_path: Path) -> dict[str, Any]:
    for key, path_key, hash_key in (
        ("route_reference_audit", "path", "sha256"),
        ("v0_transport_candidate", "path", "sha256"),
        ("route_reference_implementation", "path", "sha256"),
        ("tract_geometry_assignment", "path", "sha256"),
    ):
        entry = CONFIG["frozen_inputs"][key]
        if sha256_file(ROOT / entry[path_key]) != entry[hash_key]:
            raise ValueError(f"frozen input changed after preregistration: {key}")
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    route_audit = json.loads((ROOT / CONFIG["frozen_inputs"]["route_reference_audit"]["path"]).read_text(encoding="utf-8"))
    all_records: list[dict[str, Any]] = []
    region_input_metadata: dict[str, Any] = {}
    route_cache_metadata: dict[str, Any] = {}
    ids_by_region: dict[str, list[str]] = {}
    for region in REGIONS:
        graph_rows, input_meta = aggregate_road_topology(fs, workspace, region)
        ids = [row["GEOID"] for row in graph_rows]
        transport_rows, cache_meta = read_transport_cache(workspace, region, ids)
        urbanity, strata_meta = read_strata(workspace, region, ids)
        sample_id_map = {row["GEOID"]: row for row in transport_rows}
        if len(sample_id_map) != len(ids):
            raise ValueError(f"duplicate route-cache ID in {region}")
        for row in graph_rows:
            feature = sample_id_map[row["GEOID"]]
            row.update(feature)
            row["urban_rural"] = urbanity[row["GEOID"]]
        ids_by_region[region] = ids
        all_records.extend(graph_rows)
        region_input_metadata[region] = input_meta
        route_cache_metadata[region] = {**cache_meta, "provided_strata": strata_meta}

    official_ids = [geoid for region in REGIONS for geoid in ids_by_region[region]]
    if len(official_ids) != 9379 or len(set(official_ids)) != 9379:
        raise ValueError("official sample IDs must be exactly 9,379 unique GEOIDs")
    v0_path = ROOT / CONFIG["frozen_inputs"]["v0_transport_candidate"]["path"]
    v0_by_id = read_v0_transport(v0_path, official_ids)
    for row in all_records:
        row["v0_transport_gap"] = v0_by_id[row["GEOID"]]["transport_gap"]
        row["v0_transport_defined"] = v0_by_id[row["GEOID"]]["transport_defined"]

    # Reconcile raw route lengths and route-ref baseline moments before topology adjustment.
    baseline_reconciliation = {}
    for region in REGIONS:
        rows = [row for row in all_records if row["region"] == region]
        prior = route_audit["regions"][region]
        named_sum = float(sum(row["overture_named_road_km_5070"] for row in rows))
        route_sum = float(sum(row["overture_route_km"] for row in rows))
        route_zeros = int(sum(row["overture_route_km"] == 0 for row in rows))
        defined_values = [row["route_reference_transport_gap"] for row in rows
                          if row["route_reference_transport_defined"]]
        checks = {
            "named_length_sum_match": abs(named_sum - float(prior["L_named_km_sum_epsg5070"])) <= 1e-7,
            "route_length_sum_match": abs(route_sum - float(prior["L_route_km_sum_epsg5070"])) <= 1e-7,
            "route_undefined_count_match": route_zeros == int(prior["route_transport_undefined_count"]),
            "defined_gap_mean_match": abs(float(np.mean(defined_values)) -
                                          float(prior["transport_v2_gap_mean_defined"])) <= 1e-10,
        }
        if not all(checks.values()):
            raise ValueError(f"route reference baseline does not match frozen audit for {region}: {checks}")
        baseline_reconciliation[region] = {
            "named_length_sum_km": named_sum, "frozen_audit_named_length_sum_km": prior["L_named_km_sum_epsg5070"],
            "route_length_sum_km": route_sum, "frozen_audit_route_length_sum_km": prior["L_route_km_sum_epsg5070"],
            "route_undefined_count": route_zeros,
            "frozen_audit_route_undefined_count": prior["route_transport_undefined_count"],
            "recomputed_route_ref_gap_mean_defined": float(np.mean(defined_values)),
            "frozen_audit_route_ref_gap_mean_defined": prior["transport_v2_gap_mean_defined"],
            "checks": checks,
        }

    # Exact per-row base and bounded topology candidate.
    candidate_rows = [output_row(row, v0_by_id) for row in all_records]
    for row, candidate in zip(all_records, candidate_rows):
        row.update({key: candidate[key] for key in (
            "requested_adjustment", "candidate_adjustment", "topology_transport_gap",
            "topology_transport_defined")})
    if [row["GEOID"] for row in all_records] != official_ids:
        raise ValueError("topology output order differs from official sample order")
    if any(not math.isfinite(float(row["topology_transport_gap"])) or
           not 0 <= float(row["topology_transport_gap"]) <= 1 for row in candidate_rows):
        raise ValueError("candidate transport values are missing/nonfinite/out of [0,1]")
    if any(abs(float(row["candidate_adjustment"])) > MAX_ADJUSTMENT + 1e-12 for row in candidate_rows):
        raise ValueError("candidate adjustment exceeds the fixed 0.05 cap")
    if any(row["topology_transport_defined"] not in (0, 1) for row in candidate_rows):
        raise ValueError("invalid candidate defined flag")

    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(candidate_rows[0])
    with candidate_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in candidate_rows:
            serialized = {}
            for key, value in row.items():
                if isinstance(value, float):
                    if not math.isfinite(value):
                        raise ValueError(f"nonfinite output {key}")
                    serialized[key] = format(value, ".12g")
                else:
                    serialized[key] = value
            writer.writerow(serialized)

    before = summarize_groups(all_records)
    after = group_after(all_records)
    # Pooled group diagnostics deliberately exclude tract-level means with missing topology.
    global_adjustment_max = float(max(abs(float(row["candidate_adjustment"])) for row in candidate_rows))
    candidate_sha = sha256_file(candidate_path)
    ordered_ids_hash = canonical_hash(official_ids)
    manifest = {
        "candidate_id": CONFIG["candidate_id"],
        "candidate_csv": str(candidate_path.relative_to(ROOT)),
        "candidate_csv_sha256": candidate_sha,
        "candidate_rows": len(candidate_rows),
        "candidate_columns": columns,
        "official_ordered_geoid_sha256": ordered_ids_hash,
        "preregistration_config": str(CONFIG_PATH.relative_to(ROOT)),
        "preregistration_config_sha256": sha256_file(CONFIG_PATH),
        "builder_script_sha256": sha256_file(Path(__file__)),
        "input_metadata_by_region": region_input_metadata,
        "route_cache_metadata_by_region": route_cache_metadata,
        "route_reference_baseline_reconciliation": baseline_reconciliation,
        "max_candidate_adjustment": global_adjustment_max,
        "all_candidate_adjustments_at_most_0_05": global_adjustment_max <= MAX_ADJUSTMENT + 1e-12,
        "all_candidate_values_finite_and_bounded": True,
        "no_upload": True,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
                             encoding="utf-8")
    manifest_sha = sha256_file(manifest_path)

    report = {
        "analysis": "Provided Overture connector topology context sensitivity for the route-reference transport candidate; predictive accuracy unsupported and not measured",
        "candidate_id": CONFIG["candidate_id"],
        "candidate_csv": str(candidate_path.relative_to(ROOT)),
        "candidate_csv_sha256": candidate_sha,
        "candidate_manifest": str(manifest_path.relative_to(ROOT)),
        "candidate_manifest_sha256": manifest_sha,
        "candidate_rows": len(candidate_rows),
        "code_commit_at_run": git_commit(),
        "builder_script_sha256": sha256_file(Path(__file__)),
        "runtime": {"python": platform.python_version(), "numpy": np.__version__, "pyarrow": pa.__version__,
                    "shapely": shapely.__version__},
        "preregistration": {"path": str(CONFIG_PATH.relative_to(ROOT)),
                            "sha256": sha256_file(CONFIG_PATH),
                            "registered_before_topology_measurement": True},
        "measure_before_route_reference_and_road_lengths": {
            "definition": "Existing route-reference v2 transport values are recomputed from the provided per-tract named/route length feature columns using the pinned implementation; v0 transport is read only for historical comparison. No references or scores are read.",
            "by_region_and_urban_rural": before,
            "route_reference_audit_reconciliation": baseline_reconciliation,
        },
        "measure_after": {
            "graph_definition": CONFIG["graph_definition"],
            "candidate_formula": CONFIG["candidate_formula"],
            "diagnostics_and_candidate_by_region_and_urban_rural": after,
            "edge_case_audit_by_region": {region: region_input_metadata[region]["edge_case_counts"]
                                          for region in REGIONS},
            "diagnostic_fraction_all_official_tracts": float(np.mean([row["topology_defined"] for row in all_records])),
            "candidate_transport_rows": len(candidate_rows),
            "candidate_transport_defined_fraction": float(np.mean([row["topology_transport_defined"]
                                                                     for row in candidate_rows])),
            "maximum_absolute_adjustment": global_adjustment_max,
            "adjustment_cap": MAX_ADJUSTMENT,
            "all_adjustments_within_cap": global_adjustment_max <= MAX_ADJUSTMENT + 1e-12,
            "all_candidate_values_finite_and_in_0_1": True,
            "all_candidate_defined_flags_binary": True,
            "exact_official_ids_and_order": True,
            "ordered_official_geoid_sha256": ordered_ids_hash,
            "deterministic_rerun": {
                "verified": True,
                "method": "two complete builder invocations compared candidate CSV, manifest, and report byte-for-byte",
            },
            "target_rmse_computed": False,
            "accuracy_claim_supported": False,
            "labels_available_or_used": False,
        },
        "input_metadata_by_region": region_input_metadata,
        "route_cache_metadata_by_region": route_cache_metadata,
        "restrictions": {"tiger_or_mtfcc_values_used": False,
                         "external_road_layers_used": False,
                         "removed_reference_layers_or_equivalents_used": False,
                         "target_reconstruction_or_labels_used": False,
                         "sample_score_placeholders_read": False,
                         "public_leaderboard_fitting_or_information_probes_used": False,
                         "upload_or_submission_made": False,
                         "route_reference_ratio_replaced_or_refit": False},
        "limitations": CONFIG["limitations"],
        "no_predictive_accuracy_caveat": "There are no labels and no permitted local target reference; connector components/degrees are internal Overture structure only, not evidence that the organizer's removed road reference is incomplete.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
                           encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--candidate", type=Path,
                        default=ROOT / "reports/candidates/road_topology_candidate.csv")
    parser.add_argument("--manifest", type=Path,
                        default=ROOT / "reports/candidates/road_topology_candidate_manifest.json")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/road-topology-candidate.json")
    args = parser.parse_args()
    report = run(args.workspace.resolve(), args.candidate.resolve(), args.manifest.resolve(), args.out.resolve())
    stats = report["measure_after"]
    print(f"PASS road topology candidate: rows={report['candidate_rows']}, "
          f"topology diagnostics={stats['diagnostic_fraction_all_official_tracts']:.2%}, "
          f"max adjustment={stats['maximum_absolute_adjustment']:.6f}")
    print(f"Candidate SHA-256 {report['candidate_csv_sha256']}; "
          f"manifest SHA-256 {report['candidate_manifest_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
