#!/usr/bin/env python3
"""Audit T1 named-stroke endpoint continuity using the provided Overture road layer only.

The script compares the frozen exact-identity T1 output with the registered recipe's
same-identity, shared-connector joins under a strict deflection < 30 degree rule. It is a
label-free diagnostic; it never changes a scored candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
from bias60.ideas.t import NAMED_CLASSES, _identity, _road_layer  # noqa: E402
from bias60.ideas.t_exact import deflection_degrees, stroke_components  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
THRESHOLD_DEGREES = 30.0
OUTSIDE_NAMED_SHARE = 0.7
T1_SOURCE = ROOT / "src/bias60/ideas/t.py"
EXACT_SOURCE = ROOT / "src/bias60/ideas/t_exact.py"
HARNESS_CONFIG = ROOT / "config/bias60-harness-preregistered.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def metric_tangent(points: Sequence[Sequence[float]]) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return outward vectors at the first and last vertex, matching T1-v2's convention."""
    if len(points) < 2:
        raise ValueError("a road line needs at least two coordinate vertices")
    start = (float(points[1][0] - points[0][0]), float(points[1][1] - points[0][1]))
    end = (float(points[-2][0] - points[-1][0]), float(points[-2][1] - points[-1][1]))
    if math.hypot(*start) == 0.0 or math.hypot(*end) == 0.0:
        raise ValueError("endpoint tangent is degenerate")
    return start, end


def stroke_diagnostics(
    identities: Sequence[str | None],
    connectors: Sequence[tuple[str | None, str | None]],
    tangents: Sequence[tuple[Sequence[float], Sequence[float]]],
    geometry_hashes: Sequence[str],
    *, threshold_degrees: float = THRESHOLD_DEGREES,
) -> dict[str, Any]:
    """Count eligible pairwise endpoint joins and ambiguity/duplicate diagnostics.

    An ambiguous junction is an identity+connector node with more than two distinct incident
    road rows or more than one eligible continuation pairing. Duplicate geometry means identical
    normalized projected WKB; every row after the first in such a group is a duplicate row.
    Candidate joins across identities are counted separately and are never passed to union.
    """
    if not (len(identities) == len(connectors) == len(tangents) == len(geometry_hashes)):
        raise ValueError("row-level identity, connector, tangent and geometry arrays must align")
    by_identity_connector: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    by_connector: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row, (identity, pair) in enumerate(zip(identities, connectors)):
        for end, connector in enumerate(pair):
            if not connector:
                continue
            by_connector[connector].append((row, end))
            if identity is not None:
                by_identity_connector[(identity, connector)].append((row, end))

    eligible_pairs: list[tuple[int, int, str]] = []
    ambiguous = 0
    ambiguous_degree = 0
    junction_count = 0
    cross_identity_candidates = 0
    for (identity, connector), incidents in by_identity_connector.items():
        incident_rows = sorted({row for row, _ in incidents})
        candidates = []
        for left in range(len(incidents)):
            row_a, end_a = incidents[left]
            for right in range(left + 1, len(incidents)):
                row_b, end_b = incidents[right]
                if row_a == row_b:
                    continue
                angle = deflection_degrees(tangents[row_a][end_a], tangents[row_b][end_b])
                if angle < threshold_degrees - 1e-12:
                    candidates.append((min(row_a, row_b), max(row_a, row_b)))
        # Each edge pair at a shared connector is one join. Self-loop endpoint pairs are excluded.
        candidates = sorted(set(candidates))
        if len(incident_rows) > 1:
            junction_count += 1
        if len(incident_rows) > 2:
            ambiguous_degree += 1
        if len(incident_rows) > 2 or len(candidates) > 1:
            ambiguous += 1
        eligible_pairs.extend((a, b, connector) for a, b in candidates)

    # Count geometrically compatible cross-identity pairs as a guardrail; they must not merge.
    for incidents in by_connector.values():
        for left in range(len(incidents)):
            row_a, end_a = incidents[left]
            for right in range(left + 1, len(incidents)):
                row_b, end_b = incidents[right]
                if row_a == row_b or identities[row_a] == identities[row_b]:
                    continue
                angle = deflection_degrees(tangents[row_a][end_a], tangents[row_b][end_b])
                if angle < threshold_degrees - 1e-12:
                    cross_identity_candidates += 1

    duplicate_groups: dict[str, list[int]] = defaultdict(list)
    for row, digest in enumerate(geometry_hashes):
        duplicate_groups[digest].append(row)
    duplicate_groups = {key: rows for key, rows in duplicate_groups.items() if len(rows) > 1}
    duplicate_rows = sum(len(rows) - 1 for rows in duplicate_groups.values())
    duplicate_cross_identity_groups = sum(
        1 for rows in duplicate_groups.values()
        if len({identities[row] for row in rows}) > 1
    )

    labels = stroke_components(identities, connectors, tangents,
                               threshold_degrees=threshold_degrees)
    component_identities: dict[int, set[str]] = defaultdict(set)
    for label, identity in zip(labels, identities):
        if identity is not None:
            component_identities[label].add(identity)
    cross_identity_merges = sum(len(values) > 1 for values in component_identities.values())
    if cross_identity_merges:
        raise AssertionError("stroke component crossed semantic identities")
    return {
        "within_identity_shared_endpoint_nodes": junction_count,
        "eligible_within_identity_endpoint_joins": len(eligible_pairs),
        "ambiguous_junctions": ambiguous,
        "ambiguous_junctions_degree_gt_2": ambiguous_degree,
        "eligible_join_pair_sample": [
            {"left_row": a, "right_row": b, "connector": connector}
            for a, b, connector in eligible_pairs[:100]
        ],
        "cross_identity_compatible_endpoint_pairs_not_merged": cross_identity_candidates,
        "cross_identity_component_merges": cross_identity_merges,
        "duplicate_geometry_groups": len(duplicate_groups),
        "duplicate_geometry_excess_rows": duplicate_rows,
        "duplicate_geometry_groups_spanning_identities": duplicate_cross_identity_groups,
        "component_count": len(set(labels)),
        "component_labels": labels,
    }


def _aggregate(
    component_labels: Sequence[int],
    classes: Sequence[str],
    full_lengths_m: Sequence[float],
    tract_lengths_m: Sequence[dict[int, float]],
    n_tracts: int,
    *,
    outside_share_threshold: float,
) -> dict[str, np.ndarray]:
    totals: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    local: dict[int, dict[int, list[float]]] = defaultdict(dict)
    for row, component in enumerate(component_labels):
        length = float(full_lengths_m[row])
        named = length if classes[row] in NAMED_CLASSES else 0.0
        totals[component][0] += length
        totals[component][1] += named
        for tract_id, tract_length in tract_lengths_m[row].items():
            values = local[component].setdefault(tract_id, [0.0, 0.0, 0.0])
            values[0] += tract_length
            if classes[row] in NAMED_CLASSES:
                values[1] += tract_length
            else:
                values[2] += tract_length

    eligible_named = np.zeros(n_tracts, dtype=np.float64)
    eligible_drop = np.zeros(n_tracts, dtype=np.float64)
    for component, by_tract in local.items():
        full_total, full_named = totals[component]
        for tract_id, (total_here, named_here, drop_here) in by_tract.items():
            outside_total = full_total - total_here
            outside_named = full_named - named_here
            if (outside_total > 0.0 and outside_named / outside_total >= outside_share_threshold
                    and drop_here > 0.0):
                eligible_named[tract_id] += named_here
                eligible_drop[tract_id] += drop_here
    denominator = eligible_named + eligible_drop
    defined = np.isfinite(denominator) & (denominator > 0.0)
    gap = np.zeros(n_tracts, dtype=np.float64)
    gap[defined] = eligible_drop[defined] / denominator[defined]
    return {"eligible_named_length_m": eligible_named, "eligible_drop_length_m": eligible_drop,
            "gap": gap, "defined": defined}


def _exact_identity_t1(
    identities: Sequence[str], classes: Sequence[str], tract_lengths_m: Sequence[dict[int, float]],
    n_tracts: int, *, outside_share_threshold: float,
) -> dict[str, np.ndarray]:
    named_total: dict[str, float] = defaultdict(float)
    named_by_tract: dict[str, dict[int, float]] = defaultdict(dict)
    drop_by_tract: dict[str, dict[int, float]] = defaultdict(dict)
    for identity, road_class, lengths in zip(identities, classes, tract_lengths_m):
        target = named_by_tract if road_class in NAMED_CLASSES else drop_by_tract
        for tract_id, length in lengths.items():
            values = target[identity]
            values[tract_id] = values.get(tract_id, 0.0) + float(length)
            if road_class in NAMED_CLASSES:
                named_total[identity] += float(length)
    named_in = np.zeros(n_tracts, dtype=np.float64)
    eligible_drop = np.zeros(n_tracts, dtype=np.float64)
    for identity, by_tract in named_by_tract.items():
        total_named = named_total[identity]
        if total_named <= 0.0:
            continue
        for tract_id, named_here in by_tract.items():
            named_in[tract_id] += named_here
        for tract_id, drop_here in drop_by_tract.get(identity, {}).items():
            outside_share = (total_named - by_tract.get(tract_id, 0.0)) / total_named
            if outside_share >= outside_share_threshold:
                eligible_drop[tract_id] += drop_here
    denominator = named_in + eligible_drop
    defined = np.isfinite(denominator) & (denominator > 0.0)
    gap = np.zeros(n_tracts, dtype=np.float64)
    gap[defined] = eligible_drop[defined] / denominator[defined]
    return {"named_length_m": named_in, "eligible_drop_length_m": eligible_drop,
            "gap": gap, "defined": defined}


def _distribution(values: np.ndarray, mask: np.ndarray) -> dict[str, float | int | None]:
    selected = np.asarray(values, dtype=np.float64)[np.asarray(mask, dtype=bool)]
    if not len(selected):
        return {"n": 0, "min": None, "q25": None, "median": None, "q75": None, "max": None}
    q = np.quantile(selected, [0.0, 0.25, 0.5, 0.75, 1.0])
    return {"n": int(len(selected)), "min": float(q[0]), "q25": float(q[1]),
            "median": float(q[2]), "q75": float(q[3]), "max": float(q[4])}


def audit_region(workspace: Path, region: str) -> dict[str, Any]:
    import pyarrow.parquet as pq
    import shapely
    from pyproj import Transformer

    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(official) != 9379 or len({g for g, _ in official}) != 9379:
        raise ValueError("official scored ID set must contain exactly 9,379 unique GEOIDs")
    if {r for _, r in official} != set(REGIONS):
        raise ValueError("official region set differs from the frozen four-region scope")
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
    classes: list[str] = []
    connectors: list[tuple[str | None, str | None]] = []
    tangents: list[tuple[tuple[float, float], tuple[float, float]]] = []
    geometry_hashes: list[str] = []
    full_lengths: list[float] = []
    tract_lengths: list[dict[int, float]] = []
    rows_read = 0
    rows_without_endpoint_connectors = 0
    rows_without_tract_intersections = 0
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
        if not chosen:
            continue
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
            road_identity = _identity(names[source_id], routes[source_id])
            row_connectors = [item for item in (conn_rows[source_id] or [])
                              if isinstance(item, dict) and item.get("connector_id")
                              and isinstance(item.get("at"), (int, float))]
            row_connectors.sort(key=lambda item: float(item["at"]))
            if len(row_connectors) >= 2:
                end_connectors = (str(row_connectors[0]["connector_id"]),
                                  str(row_connectors[-1]["connector_id"]))
            else:
                end_connectors = (None, None)
                rows_without_endpoint_connectors += 1
            normalized = shapely.to_wkb(shapely.normalize(metric_lines[local_id]))
            identities.append(road_identity)
            classes.append(row_classes[source_id])
            connectors.append(end_connectors)
            tangents.append(metric_tangent(points))
            geometry_hashes.append(sha256_bytes(normalized))
            full_lengths.append(float(lengths[local_id]))
            tract_lengths.append(dict(local_lengths[local_id]))
            if not local_lengths[local_id]:
                rows_without_tract_intersections += 1

    if not identities:
        raise ValueError(f"{region}: no identified geometries found in provided roads-unfiltered")
    threshold = float(seed["outside_named_share_threshold"])
    if any(identity is None for identity in identities):
        raise AssertionError("T1 geometry rows must have a nonempty name/route identity")
    merge_diag = stroke_diagnostics(identities, connectors, tangents, geometry_hashes,
                                    threshold_degrees=THRESHOLD_DEGREES)
    merged_labels = merge_diag.pop("component_labels")
    exact = _exact_identity_t1(identities, classes, tract_lengths, len(geoids),
                               outside_share_threshold=threshold)
    merged = _aggregate(merged_labels, classes, full_lengths, tract_lengths, len(geoids),
                        outside_share_threshold=threshold)

    # Compare both independent aggregations with their existing, label-free registered artifacts.
    registered = inputs.idea_emissions(["T1", "T1-v2"])
    frozen_values, frozen_defined = registered["T1"][("R", "gap")]
    exact_agreement = bool(
        np.array_equal(exact["defined"], frozen_defined)
        and np.allclose(exact["gap"][exact["defined"]], frozen_values[exact["defined"]],
                        rtol=0.0, atol=1e-12)
    )
    max_frozen_difference = float(np.max(np.abs(
        exact["gap"][exact["defined"]] - frozen_values[exact["defined"]]), initial=0.0))
    if not exact_agreement:
        raise ValueError(f"{region}: exact identity reimplementation differs from frozen T1; max={max_frozen_difference}")
    merged_values, merged_defined = registered["T1-v2"][("R", "gap")]
    merged_agreement = bool(
        np.array_equal(merged["defined"], merged_defined)
        and np.allclose(merged["gap"][merged["defined"]], merged_values[merged["defined"]],
                        rtol=0.0, atol=1e-12)
    )
    max_merged_difference = float(np.max(np.abs(
        merged["gap"][merged["defined"]] - merged_values[merged["defined"]]), initial=0.0))
    if not merged_agreement:
        raise ValueError(f"{region}: geometric merge differs from registered T1-v2; max={max_merged_difference}")

    both_defined = exact["defined"] & merged["defined"]
    gap_change = np.full(len(geoids), np.nan, dtype=np.float64)
    gap_change[both_defined] = merged["gap"][both_defined] - exact["gap"][both_defined]
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
            "gap_change_merged_minus_exact": float(gap_change[i]) if both_defined[i] else None,
        })
    merged_support = merged["defined"]
    summary = {
        "region": region,
        "official_tracts": len(geoids),
        "source_road_rows_read": rows_read,
        "identity_geometry_rows": len(identities),
        "identified_rows_without_endpoint_connectors": rows_without_endpoint_connectors,
        "identified_rows_without_tract_intersection": rows_without_tract_intersections,
        "exact_identity_reproduction": {
            "matches_registered_T1": exact_agreement,
            "maximum_absolute_gap_difference": max_frozen_difference,
            "defined_tracts": int(exact["defined"].sum()),
            "gap_distribution": _distribution(exact["gap"], exact["defined"]),
        },
        "geometric_merge": {
            "matches_registered_T1_v2": merged_agreement,
            "maximum_absolute_gap_difference_vs_registered_T1_v2": max_merged_difference,
            "threshold_degrees_strictly_less_than": THRESHOLD_DEGREES,
            "outside_named_share_threshold": threshold,
            "defined_tracts": int(merged["defined"].sum()),
            "undefined_tracts": int((~merged["defined"]).sum()),
            "gap_distribution": _distribution(merged["gap"], merged_support),
            "eligible_named_length_km_distribution": _distribution(merged["eligible_named_length_m"] / 1000.0,
                                                                      merged_support),
            "eligible_class_drop_length_km_distribution": _distribution(merged["eligible_drop_length_m"] / 1000.0,
                                                                          merged_support),
            "defined_mask_changes_vs_exact": {
                "newly_defined": int(np.sum(~exact["defined"] & merged["defined"])),
                "newly_undefined": int(np.sum(exact["defined"] & ~merged["defined"])),
                "same_defined": int(np.sum(exact["defined"] == merged["defined"])),
            },
            "gap_change_on_paired_defined_support": _distribution(gap_change, both_defined),
            "mean_signed_gap_change_all_paired": float(np.mean(gap_change[both_defined])) if both_defined.any() else None,
            "mean_absolute_gap_change_all_paired": float(np.mean(np.abs(gap_change[both_defined]))) if both_defined.any() else None,
        },
        "endpoint_diagnostics": merge_diag,
        "per_tract": tract_rows,
    }
    if merge_diag["cross_identity_component_merges"] != 0:
        raise AssertionError("cross-identity merge count must be zero")
    layer_template = config["inputs"]["package_layer_path_template"]
    road_path = layer_template.format(bucket=config["inputs"]["package_bucket"],
                                      region=region, layer=seed["unfiltered_layer"])
    return {
        "schema_version": 1,
        "audit": "T1 exact-identity versus geometric named-stroke continuity merge; diagnostic only",
        "scope": {
            "region": region,
            "official_geoids": geoids,
            "official_geoids_sha256": sha256_bytes("\n".join(geoids).encode()),
            "tract_mask_wkb_sha256": sha256_bytes(b"\n".join(shapely.to_wkb(tract) for tract in source_tracts)),
            "source_road_object": road_path,
            "source_road_layer": "provided roads-unfiltered only",
            "accessed_layers": sorted(set(inputs.accessed["layers"])),
            "accessed_columns": sorted(set(inputs.accessed["parquet_columns"])),
            "accessed_idea_artifacts": sorted(set(inputs.accessed.get("idea_artifacts", []))),
            "labels_or_targets_read": False,
            "removed_references_read": False,
            "external_road_layers_read": False,
            "leaderboard_feedback_used": False,
            "candidate_artifacts_or_outputs_changed": False,
            "uploads": 0,
        },
        "method": {
            "exact_identity": "frozen T1 name.primary, else route network/ref; global identity aggregation",
            "merged_identity": "same identity only; union rows sharing connector endpoint when outward-tangent deflection is strictly <30 degrees",
            "deflection_convention": "0 degrees is straight continuation; 30 degrees exactly is excluded",
            "ambiguous_junction_definition": "same-identity connector node with >2 distinct incident rows or >1 eligible pairwise continuation",
            "duplicate_geometry_definition": "identical normalized EPSG:5070 WKB among identified road rows; report excess rows, do not silently remove them",
            "gap_rule": "exact identity retains all named length and only outside-share-eligible class-drop length; geometric merge uses eligible stroke named plus class-drop length after the outside-share gate",
            "named_classes": sorted(NAMED_CLASSES),
            "length_crs": "EPSG:5070",
        },
        "region_result": summary,
        "provenance": {
            "analysis_script_sha256": sha256_file(Path(__file__).resolve()),
            "t1_source_sha256": sha256_file(T1_SOURCE),
            "t1_exact_source_sha256": sha256_file(EXACT_SOURCE),
            "harness_config_sha256": sha256_file(HARNESS_CONFIG),
        },
    }


def combine_reports(paths: Sequence[Path], out: Path) -> dict[str, Any]:
    by_region: dict[str, tuple[dict[str, Any], Path]] = {}
    for path in paths:
        part = json.loads(path.read_text(encoding="utf-8"))
        region = part["scope"]["region"]
        if region in by_region:
            raise ValueError(f"duplicate region report: {region}")
        by_region[region] = (part, path)
    if set(by_region) != set(REGIONS):
        raise ValueError(f"need one report for each official region, found {sorted(by_region)}")
    region_results = {region: by_region[region][0]["region_result"] for region in REGIONS}
    report = {
        "schema_version": 1,
        "audit": "T1 exact-identity versus geometric named-stroke continuity merge; diagnostic only",
        "scope": {
            "regions": list(REGIONS),
            "official_tracts": sum(len(by_region[r][0]["scope"]["official_geoids"]) for r in REGIONS),
            "labels_or_targets_read": False,
            "removed_references_read": False,
            "external_road_layers_read": False,
            "leaderboard_feedback_used": False,
            "candidate_artifacts_or_outputs_changed": False,
            "uploads": 0,
        },
        "reproduction": {
            "per_region": "uv run --with-requirements requirements.txt python scripts/audit_t1_geometry_stroke_merge.py --workspace $COMPETITION_WORKSPACE --region <REGION> --out $COMPETITION_WORKSPACE/t1-geometry-stroke-merge-checkpoints/<REGION>.json",
            "combine": "uv run --with-requirements requirements.txt python scripts/audit_t1_geometry_stroke_merge.py --merge-region-reports <FOUR_REGION_JSON_PATHS> --out reports/t1-geometry-stroke-merge.json",
            "run_policy": "Run one region per invocation; combine only after all four region JSON files are present.",
        },
        "rollup": {
            "eligible_within_identity_endpoint_joins": sum(
                region_results[r]["endpoint_diagnostics"]["eligible_within_identity_endpoint_joins"]
                for r in REGIONS),
            "ambiguous_junctions": sum(
                region_results[r]["endpoint_diagnostics"]["ambiguous_junctions"]
                for r in REGIONS),
            "duplicate_geometry_excess_rows": sum(
                region_results[r]["endpoint_diagnostics"]["duplicate_geometry_excess_rows"]
                for r in REGIONS),
            "cross_identity_component_merges": sum(
                region_results[r]["endpoint_diagnostics"]["cross_identity_component_merges"]
                for r in REGIONS),
            "all_exact_identity_outputs_match_registered_T1": all(
                region_results[r]["exact_identity_reproduction"]["matches_registered_T1"]
                for r in REGIONS),
            "all_geometric_outputs_match_registered_T1_v2": all(
                region_results[r]["geometric_merge"]["matches_registered_T1_v2"]
                for r in REGIONS),
        },
        "regions": {region: by_region[region][0] for region in REGIONS},
        "provenance": {
            "analysis_script_sha256": sha256_file(Path(__file__).resolve()),
            "region_reports": {
                region: {"path": str(by_region[region][1]), "sha256": sha256_file(by_region[region][1])}
                for region in REGIONS
            },
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__import__("os").environ.get("COMPETITION_WORKSPACE", "")))
    parser.add_argument("--region", choices=REGIONS)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/t1-geometry-stroke-merge.json")
    parser.add_argument("--merge-region-reports", nargs="+", type=Path,
                        help="combine the four per-region JSON outputs into --out")
    args = parser.parse_args()
    if args.merge_region_reports:
        report = combine_reports(args.merge_region_reports, args.out)
        print(json.dumps({"regions": report["scope"]["regions"], "official_tracts": report["scope"]["official_tracts"],
                          "output": str(args.out)}, sort_keys=True))
        return 0
    if not str(args.workspace):
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    if args.region is None:
        parser.error("--region is required; run each region separately, then combine with --merge-region-reports")
    report = audit_region(args.workspace, args.region)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    result = report["region_result"]
    print(json.dumps({"region": args.region, "eligible_joins": result["endpoint_diagnostics"]["eligible_within_identity_endpoint_joins"],
                      "ambiguous_junctions": result["endpoint_diagnostics"]["ambiguous_junctions"],
                      "duplicate_geometry_rows": result["endpoint_diagnostics"]["duplicate_geometry_excess_rows"],
                      "exact_defined": result["exact_identity_reproduction"]["defined_tracts"],
                      "merged_defined": result["geometric_merge"]["defined_tracts"], "output": str(args.out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
