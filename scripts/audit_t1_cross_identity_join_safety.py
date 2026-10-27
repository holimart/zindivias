#!/usr/bin/env python3
"""Screen conservative T1 cross-identity endpoint continuations; diagnostic only."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import audit_t1_geometry_stroke_merge as baseline_audit  # noqa: E402
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
from bias60.ideas.t import NAMED_CLASSES, _identity, _road_layer  # noqa: E402
from bias60.ideas.t_exact import deflection_degrees  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
THRESHOLD_DEGREES = 30.0
BASE_REPORT = ROOT / "reports/t1-geometry-stroke-merge.json"
BASE_AUDIT = ROOT / "scripts/audit_t1_geometry_stroke_merge.py"
T1_SOURCE = ROOT / "src/bias60/ideas/t.py"
T1_EXACT_SOURCE = ROOT / "src/bias60/ideas/t_exact.py"
HARNESS_CONFIG = ROOT / "config/bias60-harness-preregistered.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def normalize_token(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    token = " ".join(value.split()).casefold()
    return token or None


def route_name_evidence(names: Any, routes: Any) -> frozenset[tuple[str, ...]]:
    """Literal, case/whitespace-normalized primary-name and route/ref evidence only.

    `label_ref` intentionally allows an exact primary-name == route-ref match across the two
    Overture fields. Route signatures additionally retain their network. No fuzzy aliases,
    geography, class, or external gazetteers are used as evidence.
    """
    evidence: set[tuple[str, ...]] = set()
    primary = names.get("primary") if isinstance(names, dict) else None
    name = normalize_token(primary)
    if name:
        evidence.add(("name", name))
        evidence.add(("label_ref", name))
    for item in routes or []:
        if not isinstance(item, dict):
            continue
        network = normalize_token(item.get("network"))
        ref = normalize_token(item.get("ref") or item.get("name"))
        if ref:
            evidence.add(("label_ref", ref))
            if network:
                evidence.add(("route", network, ref))
    return frozenset(evidence)


def cross_identity_safety(
    identities: Sequence[str | None],
    connectors: Sequence[tuple[str | None, str | None]],
    tangents: Sequence[tuple[Sequence[float], Sequence[float]]],
    evidence: Sequence[frozenset[tuple[str, ...]]],
    baseline_components: Sequence[int],
    *, threshold_degrees: float = THRESHOLD_DEGREES,
) -> dict[str, Any]:
    """Select only reciprocal-unique, evidence-backed, one-to-one component joins.

    Any candidate component pair with multiple endpoint witnesses, multiple candidate partners
    for either component, or a shared connector competing with another evidence-backed partner
    is rejected. Each baseline component can participate in at most one accepted cross-identity
    join, which rules out many-to-one chains.
    """
    n = len(identities)
    if not (len(connectors) == len(tangents) == len(evidence) == len(baseline_components) == n):
        raise ValueError("cross-identity input arrays must have equal length")
    by_connector: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row, pair in enumerate(connectors):
        for end, connector in enumerate(pair):
            if connector:
                by_connector[connector].append((row, end))

    compatible: list[dict[str, Any]] = []
    evidence_backed: list[dict[str, Any]] = []
    for connector, incidents in by_connector.items():
        for i, (row_a, end_a) in enumerate(incidents):
            for row_b, end_b in incidents[i + 1:]:
                if row_a == row_b or identities[row_a] == identities[row_b]:
                    continue
                angle = deflection_degrees(tangents[row_a][end_a], tangents[row_b][end_b])
                if angle >= threshold_degrees - 1e-12:
                    continue
                left, right = (row_a, row_b) if row_a < row_b else (row_b, row_a)
                edge = {
                    "left_row": left,
                    "right_row": right,
                    "left_end": end_a if row_a < row_b else end_b,
                    "right_end": end_b if row_a < row_b else end_a,
                    "connector": connector,
                    "angle_degrees": float(angle),
                    "left_component": int(baseline_components[left]),
                    "right_component": int(baseline_components[right]),
                }
                compatible.append(edge)
                shared = tuple(sorted(evidence[left] & evidence[right]))
                if shared:
                    evidence_backed.append({**edge, "shared_evidence": [list(token) for token in shared]})

    # Group candidate witnesses by component pair. More than one witness is ambiguous even when
    # the same two semantic components touch repeatedly or share several road-row endpoints.
    pair_groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for edge in evidence_backed:
        a, b = edge["left_component"], edge["right_component"]
        pair_groups[(min(a, b), max(a, b))].append(edge)
    singleton_groups = {pair: rows[0] for pair, rows in pair_groups.items() if len(rows) == 1}
    component_neighbors: dict[int, set[int]] = defaultdict(set)
    component_edge_counts: dict[int, int] = defaultdict(int)
    # Even a multi-witness (ambiguous) partner blocks acceptance of another apparent match.
    for a, b in pair_groups:
        component_neighbors[a].add(b)
        component_neighbors[b].add(a)
        component_edge_counts[a] += 1
        component_edge_counts[b] += 1

    accepted: list[dict[str, Any]] = []
    for (a, b), edge in singleton_groups.items():
        if len(component_neighbors[a]) == len(component_neighbors[b]) == 1 and component_edge_counts[a] == component_edge_counts[b] == 1:
            accepted.append(edge)
    accepted.sort(key=lambda e: (e["connector"], e["left_row"], e["right_row"]))

    accepted_rows = [row for edge in accepted for row in (edge["left_row"], edge["right_row"])]
    accepted_components = [component for edge in accepted
                           for component in (edge["left_component"], edge["right_component"])]
    if len(set(accepted_components)) != len(accepted_components):
        raise AssertionError("accepted cross-identity joins are not component-disjoint")
    if len(set(accepted_rows)) != len(accepted_rows):
        raise AssertionError("accepted cross-identity joins reuse a road row")
    return {
        "otherwise_compatible_endpoint_pairs": len(compatible),
        "strict_route_name_evidence_endpoint_pairs": len(evidence_backed),
        "unique_safe_endpoint_pairs": len(accepted),
        "unique_safe_fraction_of_compatible_pairs": len(accepted) / len(compatible) if compatible else 0.0,
        "evidence_backed_component_pairs": len(pair_groups),
        "ambiguous_evidence_component_pairs_multiple_witnesses": sum(len(rows) > 1 for rows in pair_groups.values()),
        "evidence_components_with_multiple_candidate_partners": sum(len(v) > 1 for v in component_neighbors.values()),
        "evidence_backed_candidate_pairs_rejected_as_ambiguous_or_conflicting": len(evidence_backed) - len(accepted),
        "accepted_ambiguous_merges": 0,
        "accepted_many_to_one_component_merges": 0,
        "accepted_pair_details": accepted,
        "compatible_pair_sample": compatible[:100],
    }


def union_component_labels(labels: Sequence[int], accepted_edges: Sequence[dict[str, Any]]) -> list[int]:
    unique = sorted(set(map(int, labels)))
    parent = {item: item for item in unique}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for edge in accepted_edges:
        a, b = int(edge["left_component"]), int(edge["right_component"])
        ra, rb = find(a), find(b)
        if ra == rb:
            raise ValueError("cross-identity edge collapses within a baseline component")
        parent[rb] = ra
    roots: dict[int, int] = {}
    return [roots.setdefault(find(int(label)), len(roots)) for label in labels]


def _baseline_region_result(
    region: str, geoids: list[str], rows_read: int, identities: list[str | None],
    classes: list[str], connectors: list[tuple[str | None, str | None]],
    tangents: list[tuple[tuple[float, float], tuple[float, float]]],
    geometry_hashes: list[str], full_lengths: list[float],
    tract_lengths: list[dict[int, float]], rows_without_connectors: int,
    rows_without_tracts: int, threshold: float,
) -> tuple[dict[str, Any], dict[str, np.ndarray], dict[str, np.ndarray], list[int], dict[str, Any]]:
    merge_diag = baseline_audit.stroke_diagnostics(
        identities, connectors, tangents, geometry_hashes, threshold_degrees=THRESHOLD_DEGREES)
    labels = merge_diag.pop("component_labels")
    exact = baseline_audit._exact_identity_t1(
        identities, classes, tract_lengths, len(geoids), outside_share_threshold=threshold)
    merged = baseline_audit._aggregate(
        labels, classes, full_lengths, tract_lengths, len(geoids), outside_share_threshold=threshold)
    both = exact["defined"] & merged["defined"]
    delta = np.full(len(geoids), np.nan, dtype=np.float64)
    delta[both] = merged["gap"][both] - exact["gap"][both]
    tract_rows = []
    for i, geoid in enumerate(geoids):
        tract_rows.append({
            "GEOID": geoid,
            "exact_identity_defined": bool(exact["defined"][i]),
            "exact_identity_named_length_km": float(exact["named_length_m"][i] / 1000.0),
            "exact_identity_eligible_class_drop_km": float(exact["eligible_drop_length_m"][i] / 1000.0),
            "exact_identity_gap": float(exact["gap"][i]) if exact["defined"][i] else None,
            "merged_defined": bool(merged["defined"][i]),
            "merged_eligible_named_km": float(merged["eligible_named_length_m"][i] / 1000.0),
            "merged_eligible_class_drop_km": float(merged["eligible_drop_length_m"][i] / 1000.0),
            "merged_gap": float(merged["gap"][i]) if merged["defined"][i] else None,
            "gap_change_merged_minus_exact": float(delta[i]) if both[i] else None,
        })
    summary = {
        "region": region,
        "official_tracts": len(geoids),
        "source_road_rows_read": rows_read,
        "identity_geometry_rows": len(identities),
        "identified_rows_without_endpoint_connectors": rows_without_connectors,
        "identified_rows_without_tract_intersection": rows_without_tracts,
        "exact_identity_reproduction": {
            "matches_registered_T1": None,
            "maximum_absolute_gap_difference": None,
            "defined_tracts": int(exact["defined"].sum()),
            "gap_distribution": baseline_audit._distribution(exact["gap"], exact["defined"]),
        },
        "geometric_merge": {
            "matches_registered_T1_v2": None,
            "maximum_absolute_gap_difference_vs_registered_T1_v2": None,
            "threshold_degrees_strictly_less_than": THRESHOLD_DEGREES,
            "outside_named_share_threshold": threshold,
            "defined_tracts": int(merged["defined"].sum()),
            "undefined_tracts": int((~merged["defined"]).sum()),
            "gap_distribution": baseline_audit._distribution(merged["gap"], merged["defined"]),
            "eligible_named_length_km_distribution": baseline_audit._distribution(
                merged["eligible_named_length_m"] / 1000.0, merged["defined"]),
            "eligible_class_drop_length_km_distribution": baseline_audit._distribution(
                merged["eligible_drop_length_m"] / 1000.0, merged["defined"]),
            "defined_mask_changes_vs_exact": {
                "newly_defined": int(np.sum(~exact["defined"] & merged["defined"])),
                "newly_undefined": int(np.sum(exact["defined"] & ~merged["defined"])),
                "same_defined": int(np.sum(exact["defined"] == merged["defined"])),
            },
            "gap_change_on_paired_defined_support": baseline_audit._distribution(delta, both),
            "mean_signed_gap_change_all_paired": float(np.mean(delta[both])) if both.any() else None,
            "mean_absolute_gap_change_all_paired": float(np.mean(np.abs(delta[both]))) if both.any() else None,
        },
        "endpoint_diagnostics": merge_diag,
        "per_tract": tract_rows,
    }
    return summary, exact, merged, labels, merge_diag


def audit_region(workspace: Path, region: str, prior: dict[str, Any]) -> dict[str, Any]:
    import pyarrow.parquet as pq
    import shapely
    from pyproj import Transformer

    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(official) != 9379 or len({g for g, _ in official}) != 9379:
        raise ValueError("official scored ID set must contain exactly 9,379 unique GEOIDs")
    if {r for _, r in official} != set(REGIONS):
        raise ValueError("official region partition differs from the frozen four-region scope")
    geoids = [g for g, r in official if r == region]
    config = load_config(HARNESS_CONFIG)
    inputs = ProvidedInputs(config, workspace, region, geoids)
    source_tracts = inputs.tract_geometries()
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    tracts = shapely.transform(source_tracts, transformer.transform, interleaved=False)
    tree = shapely.STRtree(tracts)
    seed = config["seeds"]["T1"]
    columns = ["class", "names", "routes", "connectors", "geometry"]
    parquet = _road_layer(inputs, columns, seed["unfiltered_layer"])

    identities: list[str | None] = []
    evidence: list[frozenset[tuple[str, ...]]] = []
    classes: list[str] = []
    connectors: list[tuple[str | None, str | None]] = []
    tangents: list[tuple[tuple[float, float], tuple[float, float]]] = []
    geometry_hashes: list[str] = []
    full_lengths: list[float] = []
    tract_lengths: list[dict[int, float]] = []
    source_row_ids: list[int] = []
    rows_read = rows_without_connectors = rows_without_tracts = 0
    row_offset = 0
    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=columns)
        row_classes = table.column("class").to_pylist()
        names = table.column("names").to_pylist()
        routes = table.column("routes").to_pylist()
        conn_rows = table.column("connectors").to_pylist()
        wkbs = table.column("geometry").to_pylist()
        rows_read += len(row_classes)
        chosen = [i for i, (road_class, wkb) in enumerate(zip(row_classes, wkbs))
                  if isinstance(road_class, str) and wkb is not None
                  and _identity(names[i], routes[i]) is not None]
        if chosen:
            raw_lines = shapely.from_wkb([wkbs[i] for i in chosen])
            metric_lines = shapely.transform(raw_lines, transformer.transform, interleaved=False)
            lengths = shapely.length(metric_lines).tolist()
            coords, line_ids = shapely.get_coordinates(metric_lines, return_index=True)
            points_by_line: list[list[tuple[float, float]]] = [[] for _ in chosen]
            for (x, y), line_id in zip(coords.tolist(), line_ids.tolist()):
                points_by_line[line_id].append((x, y))
            pairs = tree.query(metric_lines, predicate="intersects")
            local_lengths: list[dict[int, float]] = [defaultdict(float) for _ in chosen]
            if pairs.size:
                clipped = shapely.length(shapely.intersection(metric_lines[pairs[0]], tracts[pairs[1]]))
                for local_id, tract_id, length in zip(pairs[0].tolist(), pairs[1].tolist(), clipped.tolist()):
                    if np.isfinite(length) and length > 0.0:
                        local_lengths[local_id][tract_id] += float(length)
            for local_id, source_id in enumerate(chosen):
                points = points_by_line[local_id]
                if len(points) < 2 or not np.isfinite(lengths[local_id]) or lengths[local_id] <= 0.0:
                    continue
                identity = _identity(names[source_id], routes[source_id])
                row_conns = [item for item in (conn_rows[source_id] or [])
                             if isinstance(item, dict) and item.get("connector_id")
                             and isinstance(item.get("at"), (int, float))]
                row_conns.sort(key=lambda item: float(item["at"]))
                if len(row_conns) >= 2:
                    ends = (str(row_conns[0]["connector_id"]), str(row_conns[-1]["connector_id"]))
                else:
                    ends = (None, None)
                    rows_without_connectors += 1
                identities.append(identity)
                evidence.append(route_name_evidence(names[source_id], routes[source_id]))
                classes.append(row_classes[source_id])
                connectors.append(ends)
                tangents.append(baseline_audit.metric_tangent(points))
                geometry_hashes.append(baseline_audit.sha256_bytes(
                    shapely.to_wkb(shapely.normalize(metric_lines[local_id]))))
                full_lengths.append(float(lengths[local_id]))
                tract_lengths.append(dict(local_lengths[local_id]))
                source_row_ids.append(row_offset + source_id)
                if not local_lengths[local_id]:
                    rows_without_tracts += 1
        row_offset += len(row_classes)

    if row_offset != rows_read or rows_read != int(parquet.metadata.num_rows):
        raise ValueError(f"{region}: road row count does not match Parquet footer")
    if not identities or any(item is None for item in identities):
        raise ValueError(f"{region}: expected identified road geometry rows")
    threshold = float(seed["outside_named_share_threshold"])
    baseline_result, exact, merged, base_labels, merge_diag = _baseline_region_result(
        region, geoids, rows_read, identities, classes, connectors, tangents, geometry_hashes,
        full_lengths, tract_lengths, rows_without_connectors, rows_without_tracts, threshold)

    # Independently verify output vectors against the registered T1 and T1-v2 artifacts.
    registered = inputs.idea_emissions(["T1", "T1-v2"])
    t1_gap, t1_gap_mask = registered["T1"][("R", "gap")]
    t1_cov, t1_cov_mask = registered["T1"][("R", "covariate")]
    t1v2_gap, t1v2_mask = registered["T1-v2"][("R", "gap")]
    exact_gap_match = np.array_equal(exact["defined"], t1_gap_mask) and np.allclose(
        exact["gap"][exact["defined"]], t1_gap[exact["defined"]], rtol=0.0, atol=1e-12)
    exact_cov_match = np.array_equal(exact["defined"], t1_cov_mask) and np.allclose(
        exact["eligible_drop_length_m"][exact["defined"]] / 1000.0,
        t1_cov[exact["defined"]], rtol=0.0, atol=1e-12)
    merged_gap_match = np.array_equal(merged["defined"], t1v2_mask) and np.allclose(
        merged["gap"][merged["defined"]], t1v2_gap[merged["defined"]], rtol=0.0, atol=1e-12)
    exact_gap_diff = float(np.max(np.abs(exact["gap"][exact["defined"]] - t1_gap[exact["defined"]]), initial=0.0))
    exact_cov_diff = float(np.max(np.abs(exact["eligible_drop_length_m"][exact["defined"]] / 1000.0 - t1_cov[exact["defined"]]), initial=0.0))
    merged_gap_diff = float(np.max(np.abs(merged["gap"][merged["defined"]] - t1v2_gap[merged["defined"]]), initial=0.0))
    if not (exact_gap_match and exact_cov_match and merged_gap_match):
        raise ValueError(f"{region}: registered baseline mismatch gap={exact_gap_match}, cov={exact_cov_match}, T1v2={merged_gap_match}")
    baseline_result["exact_identity_reproduction"].update({
        "matches_registered_T1": bool(exact_gap_match),
        "maximum_absolute_gap_difference": exact_gap_diff,
        "matches_registered_T1_class_drop_covariate": bool(exact_cov_match),
        "maximum_absolute_class_drop_covariate_difference_km": exact_cov_diff,
    })
    baseline_result["geometric_merge"].update({
        "matches_registered_T1_v2": bool(merged_gap_match),
        "maximum_absolute_gap_difference_vs_registered_T1_v2": merged_gap_diff,
    })
    expected = prior["regions"][region]["region_result"]
    # Compare the original baseline fields exactly; the added registered-covariate check is extra.
    expected_base = json.loads(json.dumps(expected))
    actual_base = json.loads(json.dumps(baseline_result))
    actual_base["exact_identity_reproduction"].pop("matches_registered_T1_class_drop_covariate")
    actual_base["exact_identity_reproduction"].pop("maximum_absolute_class_drop_covariate_difference_km")
    if actual_base != expected_base:
        raise ValueError(f"{region}: frozen exact-identity/geometric outputs differ from prior T1 audit")

    safety = cross_identity_safety(identities, connectors, tangents, evidence, base_labels)
    if safety["otherwise_compatible_endpoint_pairs"] != merge_diag["cross_identity_compatible_endpoint_pairs_not_merged"]:
        raise AssertionError(f"{region}: cross-identity candidate enumeration differs from frozen baseline")
    alternative_labels = union_component_labels(base_labels, safety["accepted_pair_details"])
    alternative = baseline_audit._aggregate(
        alternative_labels, classes, full_lengths, tract_lengths, len(geoids),
        outside_share_threshold=threshold)
    alt_component_identities: dict[int, set[str]] = defaultdict(set)
    for label, identity in zip(alternative_labels, identities):
        alt_component_identities[label].add(str(identity))
    cross_identity_component_merges = sum(len(values) > 1 for values in alt_component_identities.values())
    if cross_identity_component_merges != len(safety["accepted_pair_details"]):
        raise AssertionError("alternative topology contains an unaccounted cross-identity component merge")
    accepted_source_rows = [
        {**edge, "left_source_road_row": source_row_ids[edge["left_row"]],
         "right_source_road_row": source_row_ids[edge["right_row"]]}
        for edge in safety["accepted_pair_details"]
    ]
    accepted_source_digest = digest_json(sorted(
        [[min(row["left_source_road_row"], row["right_source_road_row"]),
          max(row["left_source_road_row"], row["right_source_road_row"]), row["connector"]]
         for row in accepted_source_rows]))

    per_tract = []
    for i, geoid in enumerate(geoids):
        exact_drop_km = float(exact["eligible_drop_length_m"][i] / 1000.0)
        same_drop_km = float(merged["eligible_drop_length_m"][i] / 1000.0)
        alt_drop_km = float(alternative["eligible_drop_length_m"][i] / 1000.0)
        per_tract.append({
            "GEOID": geoid,
            "T1_exact_identity_defined": bool(exact["defined"][i]),
            "T1_exact_identity_class_drop_km": exact_drop_km,
            "same_identity_geometric_defined": bool(merged["defined"][i]),
            "same_identity_geometric_class_drop_km": same_drop_km,
            "cross_identity_alternative_defined": bool(alternative["defined"][i]),
            "cross_identity_alternative_class_drop_km": alt_drop_km,
            "cross_identity_delta_vs_same_identity_geometric_km": alt_drop_km - same_drop_km,
            "cross_identity_delta_vs_exact_identity_km": alt_drop_km - exact_drop_km,
            "delta_vs_same_identity_when_both_defined_km": (
                alt_drop_km - same_drop_km if alternative["defined"][i] and merged["defined"][i] else None),
        })

    base_report_sha = sha256_file(BASE_REPORT)
    prior_script_hash = sha256_file(BASE_AUDIT)
    t1_sha, exact_sha, config_sha = (sha256_file(T1_SOURCE), sha256_file(T1_EXACT_SOURCE),
                                     sha256_file(HARNESS_CONFIG))
    prior_region_file = Path(prior["provenance"]["region_reports"][region]["path"])
    prior_region_hash = sha256_file(prior_region_file)
    if prior_region_hash != prior["provenance"]["region_reports"][region]["sha256"]:
        raise ValueError(f"{region}: frozen region checkpoint SHA differs from prior report")
    previous_region_prov = prior["regions"][region]["provenance"]
    if (previous_region_prov["analysis_script_sha256"] != prior_script_hash
            or previous_region_prov["t1_source_sha256"] != t1_sha
            or previous_region_prov["t1_exact_source_sha256"] != exact_sha
            or previous_region_prov["harness_config_sha256"] != config_sha):
        raise ValueError(f"{region}: frozen baseline provenance hashes no longer match current source files")

    return {
        "schema_version": 1,
        "region": region,
        "scope": {
            "official_geoids": geoids,
            "official_tracts": len(geoids),
            "official_geoids_sha256": baseline_audit.sha256_bytes("\n".join(geoids).encode()),
            "labels_or_targets_read": False,
            "removed_references_or_equivalents_read": False,
            "external_road_layers_read": False,
            "leaderboard_information_used": False,
            "target_fitting_performed": False,
            "candidate_artifacts_changed": False,
            "uploads": 0,
        },
        "measure_before_reproduction": {
            "exact_identity_and_geometric_outputs_match_prior_report": True,
            "exact_identity_gap_matches_registered_T1": bool(exact_gap_match),
            "exact_identity_class_drop_covariate_matches_registered_T1": bool(exact_cov_match),
            "geometric_output_matches_registered_T1_v2": bool(merged_gap_match),
            "exact_identity_gap_max_abs_diff": exact_gap_diff,
            "exact_identity_class_drop_max_abs_diff_km": exact_cov_diff,
            "geometric_gap_max_abs_diff": merged_gap_diff,
            "baseline_region_report_sha256": prior_region_hash,
            "baseline_aggregate_report_sha256": base_report_sha,
            "baseline_analysis_script_sha256": prior_script_hash,
            "T1_source_sha256": t1_sha,
            "T1_exact_source_sha256": exact_sha,
            "harness_config_sha256": config_sha,
            "registered_T1_artifact_sha256": sha256_file(workspace / "ideas/T1.parquet"),
            "registered_T1_v2_artifact_sha256": sha256_file(workspace / "ideas/T1-v2.parquet"),
            "official_id_coverage_exact": len(geoids) == int(config["inputs"]["official_rows_by_region"][region]),
        },
        "frozen_baseline": {
            "endpoint_diagnostics": baseline_result["endpoint_diagnostics"],
            "exact_identity_reproduction": baseline_result["exact_identity_reproduction"],
            "same_identity_geometric_merge": baseline_result["geometric_merge"],
            "per_tract": baseline_result["per_tract"],
        },
        "cross_identity_safety": {
            **safety,
            "accepted_pairs_with_source_row_ids": accepted_source_rows,
            "accepted_source_pair_digest_sha256": accepted_source_digest,
            "accepted_cross_identity_component_merges": cross_identity_component_merges,
            "accepted_pairs_have_no_ambiguous_or_many_to_one_merges": (
                safety["accepted_ambiguous_merges"] == 0
                and safety["accepted_many_to_one_component_merges"] == 0
                and len(set(accepted_source_rows[i][key] for i in range(len(accepted_source_rows))
                            for key in ("left_source_road_row", "right_source_road_row")))
                    == 2 * len(accepted_source_rows)),
            "route_name_evidence_policy": "literal normalized primary-name or route-ref match, or exact normalized (network,ref); no fuzzy match; candidate must be <30-degree shared-connector pair, reciprocal unique among evidence-backed alternatives, single witness per baseline component pair, and each component participates in at most one accepted join",
        },
        "alternative": {
            "method": "union only accepted safety-screen pairs on top of the frozen same-identity <30-degree components, then recompute the T1 outside-named-share aggregation",
            "covariate": "eligible class-drop road length (km), diagnostic alternative only",
            "accepted_pair_count": len(safety["accepted_pair_details"]),
            "defined_tracts": int(alternative["defined"].sum()),
            "newly_defined_vs_same_identity": int(np.sum(~merged["defined"] & alternative["defined"])),
            "newly_undefined_vs_same_identity": int(np.sum(merged["defined"] & ~alternative["defined"])),
            "covariate_delta_vs_same_identity_geometric_distribution_km": baseline_audit._distribution(
                np.asarray([row["cross_identity_delta_vs_same_identity_geometric_km"] for row in per_tract]),
                np.ones(len(per_tract), dtype=bool)),
            "covariate_delta_vs_exact_identity_distribution_km": baseline_audit._distribution(
                np.asarray([row["cross_identity_delta_vs_exact_identity_km"] for row in per_tract]),
                np.ones(len(per_tract), dtype=bool)),
            "paired_defined_delta_distribution_km": baseline_audit._distribution(
                np.asarray([row["delta_vs_same_identity_when_both_defined_km"] or 0.0 for row in per_tract]),
                np.asarray([row["delta_vs_same_identity_when_both_defined_km"] is not None for row in per_tract])),
            "cross_identity_component_merges": cross_identity_component_merges,
        },
        "per_tract": per_tract,
        "provenance": {
            "analysis_script_sha256": sha256_file(Path(__file__).resolve()),
            "baseline_analysis_script_sha256": prior_script_hash,
            "baseline_report_sha256": base_report_sha,
            "baseline_region_report_sha256": prior_region_hash,
            "T1_source_sha256": t1_sha,
            "T1_exact_source_sha256": exact_sha,
            "harness_config_sha256": config_sha,
        },
    }


def combine(paths: Sequence[Path], output: Path, repeat_path: Path | None = None) -> dict[str, Any]:
    prior = json.loads(BASE_REPORT.read_text(encoding="utf-8"))
    regions = {}
    checkpoints = {}
    for path in paths:
        part = json.loads(path.read_text(encoding="utf-8"))
        region = part["region"]
        if region in regions:
            raise ValueError(f"duplicate region checkpoint {region}")
        regions[region] = part
        checkpoints[region] = path
    if set(regions) != set(REGIONS):
        raise ValueError(f"expected exactly four official regions, found {sorted(regions)}")
    ids = [g for region in REGIONS for g in regions[region]["scope"]["official_geoids"]]
    if len(ids) != 9379 or len(set(ids)) != 9379:
        raise ValueError("regional reports do not cover exactly 9,379 unique official GEOIDs")
    for region in REGIONS:
        part = regions[region]
        if [row["GEOID"] for row in part["per_tract"]] != part["scope"]["official_geoids"]:
            raise ValueError(f"{region}: per-tract coverage/order mismatch")
        if not part["measure_before_reproduction"]["exact_identity_and_geometric_outputs_match_prior_report"]:
            raise ValueError(f"{region}: frozen baseline reproduction was not successful")
        safety = part["cross_identity_safety"]
        if not safety["accepted_pairs_have_no_ambiguous_or_many_to_one_merges"]:
            raise ValueError(f"{region}: accepted merge set has an ambiguity/many-to-one conflict")

    repeat = {"checked": False, "exact_match": None, "region": None, "sha256": None}
    if repeat_path:
        rerun = json.loads(repeat_path.read_text(encoding="utf-8"))
        region = rerun["region"]
        if region not in regions or rerun != regions[region]:
            raise ValueError("determinism checkpoint differs from its original region report")
        repeat = {"checked": True, "exact_match": True, "region": region,
                  "sha256": sha256_file(repeat_path)}

    compatible = sum(r["cross_identity_safety"]["otherwise_compatible_endpoint_pairs"] for r in regions.values())
    evidence_n = sum(r["cross_identity_safety"]["strict_route_name_evidence_endpoint_pairs"] for r in regions.values())
    safe_n = sum(r["cross_identity_safety"]["unique_safe_endpoint_pairs"] for r in regions.values())
    alternative_defined = sum(r["alternative"]["defined_tracts"] for r in regions.values())
    report = {
        "schema_version": 1,
        "audit": "T1 unique cross-identity endpoint join safety; provided-data diagnostic only",
        "scope": {
            "official_regions": list(REGIONS),
            "official_tracts": len(ids),
            "labels_or_targets_read": False,
            "removed_references_or_equivalents_read": False,
            "external_data_read": False,
            "leaderboard_information_used": False,
            "target_fitting_performed": False,
            "candidate_artifacts_changed": False,
            "uploads": 0,
        },
        "measure_before": {
            "baseline_report": "reports/t1-geometry-stroke-merge.json",
            "baseline_report_sha256": sha256_file(BASE_REPORT),
            "baseline_analysis_script_sha256": sha256_file(BASE_AUDIT),
            "all_regions_reproduced_exact_identity_and_geometric_outputs": all(
                r["measure_before_reproduction"]["exact_identity_and_geometric_outputs_match_prior_report"]
                for r in regions.values()),
            "all_regions_provenance_hashes_reproduced": all(
                r["measure_before_reproduction"]["baseline_region_report_sha256"] ==
                prior["provenance"]["region_reports"][region]["sha256"] for region, r in regions.items()),
            "official_geoids_covered_once": len(ids) == 9379 and len(set(ids)) == 9379,
            "region_coverage": {region: regions[region]["measure_before_reproduction"] for region in REGIONS},
        },
        "measure_after": {
            "candidate_pool": {
                "otherwise_compatible_cross_identity_endpoint_pairs": compatible,
                "strict_route_name_evidence_endpoint_pairs": evidence_n,
                "unique_safe_pairs": safe_n,
                "unique_safe_fraction_of_compatible_pairs": safe_n / compatible if compatible else 0.0,
                "ambiguous_or_conflicting_evidence_candidates": sum(
                    r["cross_identity_safety"]["evidence_backed_candidate_pairs_rejected_as_ambiguous_or_conflicting"]
                    for r in regions.values()),
            },
            "accepted_join_safety": {
                "ambiguous_merges": sum(r["cross_identity_safety"]["accepted_ambiguous_merges"] for r in regions.values()),
                "many_to_one_merges": sum(r["cross_identity_safety"]["accepted_many_to_one_component_merges"] for r in regions.values()),
                "cross_identity_component_merges": sum(r["cross_identity_safety"]["accepted_cross_identity_component_merges"] for r in regions.values()),
                "all_accepted_pairs_are_unique": all(r["cross_identity_safety"]["accepted_pairs_have_no_ambiguous_or_many_to_one_merges"] for r in regions.values()),
            },
            "alternative_defined_tracts": alternative_defined,
            "per_region": {
                region: {
                    "candidate_counts": {key: regions[region]["cross_identity_safety"][key] for key in (
                        "otherwise_compatible_endpoint_pairs", "strict_route_name_evidence_endpoint_pairs",
                        "unique_safe_endpoint_pairs", "unique_safe_fraction_of_compatible_pairs",
                        "ambiguous_evidence_component_pairs_multiple_witnesses",
                        "evidence_components_with_multiple_candidate_partners",
                        "evidence_backed_candidate_pairs_rejected_as_ambiguous_or_conflicting")},
                    "accepted_pair_details": regions[region]["cross_identity_safety"]["accepted_pairs_with_source_row_ids"],
                    "accepted_pair_source_digest_sha256": regions[region]["cross_identity_safety"]["accepted_source_pair_digest_sha256"],
                    "alternative_covariate_delta_summary_km": regions[region]["alternative"]["covariate_delta_vs_same_identity_geometric_distribution_km"],
                    "alternative_defined_tracts": regions[region]["alternative"]["defined_tracts"],
                    "per_tract_covariate_deltas": regions[region]["per_tract"],
                }
                for region in REGIONS
            },
            "determinism_rerun": repeat,
            "acceptance": {
                "exact_official_ID_coverage": len(ids) == 9379 and len(set(ids)) == 9379,
                "frozen_exact_identity_and_geometric_outputs_and_hashes_reproduced": all(
                    r["measure_before_reproduction"]["exact_identity_and_geometric_outputs_match_prior_report"]
                    for r in regions.values()),
                "all_accepted_pairs_unambiguous_and_one_to_one": all(
                    r["cross_identity_safety"]["accepted_pairs_have_no_ambiguous_or_many_to_one_merges"]
                    for r in regions.values()),
                "deterministic_rerun_exact_match": repeat["exact_match"] is True,
            },
            "interpretation": "Diagnostic only. Even an evidence-backed, unique merge is not accuracy evidence and does not change candidates.",
        },
        "method": {
            "compatible_pair_rule": "different frozen T1 semantic identities, shared endpoint connector, outward-tangent deflection strictly <30 degrees",
            "strict_evidence": "literal normalized primary-name equality, normalized route-ref/name equality, or exact normalized route network/ref equality",
            "safety_rule": "reject ambiguous component-pair witnesses and any component with more than one evidence-backed partner; accepted component pairs are disjoint one-to-one",
            "covariate": "eligible class-drop length in km; report identity-only and same-identity geometric baselines, then cross-identity diagnostic delta",
            "source_row_IDs": "zero-based source row ordinal in concatenated Parquet row-group order; only accepted pairs are listed",
            "distance_metric": "provided road geometry in EPSG:5070; strict <30 degree continuity as frozen T1-v2",
            "no_target_or_reference_data_read": True,
        },
        "regions": regions,
        "provenance": {
            "analysis_script_sha256": sha256_file(Path(__file__).resolve()),
            "baseline_report_sha256": sha256_file(BASE_REPORT),
            "baseline_analysis_script_sha256": sha256_file(BASE_AUDIT),
            "region_checkpoint_sha256": {region: sha256_file(checkpoints[region]) for region in REGIONS},
            "T1_source_sha256": sha256_file(T1_SOURCE),
            "T1_exact_source_sha256": sha256_file(T1_EXACT_SOURCE),
            "harness_config_sha256": sha256_file(HARNESS_CONFIG),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if len(payload.encode()) > 50_000_000:
        raise ValueError("report exceeds 50 MB bound")
    output.write_text(payload, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--region", choices=REGIONS)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/t1-cross-identity-join-safety.json")
    parser.add_argument("--merge-region-reports", nargs="+", type=Path)
    parser.add_argument("--repeat-checkpoint", type=Path)
    args = parser.parse_args()
    if args.merge_region_reports:
        report = combine(args.merge_region_reports, args.out, args.repeat_checkpoint)
        print(json.dumps({
            "official_tracts": report["scope"]["official_tracts"],
            "compatible_pairs": report["measure_after"]["candidate_pool"]["otherwise_compatible_cross_identity_endpoint_pairs"],
            "unique_safe_pairs": report["measure_after"]["candidate_pool"]["unique_safe_pairs"],
            "output": str(args.out),
        }, sort_keys=True))
        return 0
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    if args.region is None:
        parser.error("--region is required; run per region, then combine")
    prior = json.loads(BASE_REPORT.read_text(encoding="utf-8"))
    part = audit_region(args.workspace, args.region, prior)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(part, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "region": args.region,
        "compatible_pairs": part["cross_identity_safety"]["otherwise_compatible_endpoint_pairs"],
        "evidence_pairs": part["cross_identity_safety"]["strict_route_name_evidence_endpoint_pairs"],
        "unique_safe_pairs": part["cross_identity_safety"]["unique_safe_endpoint_pairs"],
        "output": str(args.out),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
