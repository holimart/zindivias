#!/usr/bin/env python3
"""Measure the provided-data T1 covariate sensitivity to same-identity <30° stroke joins."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import audit_t1_geometry_stroke_merge as geometry_audit  # noqa: E402
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
from bias60.ideas.t import _identity, _road_layer  # noqa: E402
from bias60.ideas.t_exact import deflection_degrees, stroke_components  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
THRESHOLD = 30.0
READ_COLUMNS = ["class", "names", "routes", "connectors", "geometry"]
CONFIG_PATH = ROOT / "config/bias60-harness-preregistered.json"
T1_SOURCE = ROOT / "src/bias60/ideas/t.py"
T1_EXACT_SOURCE = ROOT / "src/bias60/ideas/t_exact.py"
INPUTS_SOURCE = ROOT / "src/bias60/inputs.py"
CANDIDATE_REGISTRY = ROOT / "reports/candidates.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes_hex__": value.hex()}
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def digest_source_row(digest: Any, row: list[Any]) -> None:
    digest.update(json.dumps(jsonable(row), sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode())
    digest.update(b"\n")


def summarize(values: np.ndarray) -> dict[str, float | int | None]:
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return {"n": 0, "min": None, "q25": None, "median": None,
                "q75": None, "q90": None, "q95": None, "max": None}
    q = np.quantile(values, [0, .25, .5, .75, .9, .95, 1])
    return {"n": int(values.size), "min": float(q[0]), "q25": float(q[1]),
            "median": float(q[2]), "q75": float(q[3]), "q90": float(q[4]),
            "q95": float(q[5]), "max": float(q[6])}


def angle_statistics(workspace: Path, region: str, official_geoids: list[str],
                     expected_identity_rows: int, expected_component_count: int) -> dict[str, Any]:
    """Stream the declared T1 fields and count candidate endpoint pairs / deflections."""
    import shapely
    from pyproj import Transformer

    config = load_config(CONFIG_PATH)
    inputs = ProvidedInputs(config, workspace, region, official_geoids)
    seed = config["seeds"]["T1"]
    parquet = _road_layer(inputs, READ_COLUMNS, seed["unfiltered_layer"])
    row_digest = hashlib.sha256()
    rows_read = 0
    identities: list[str] = []
    connectors: list[tuple[str | None, str | None]] = []
    tangents: list[tuple[tuple[float, float], tuple[float, float]]] = []
    classes: list[str] = []
    geometry_rows = 0
    without_two_connectors = 0
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)

    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=READ_COLUMNS)
        if table.column_names != READ_COLUMNS:
            raise ValueError(f"{region}: T1 reader requested undeclared columns")
        row_classes = table.column("class").to_pylist()
        names = table.column("names").to_pylist()
        routes = table.column("routes").to_pylist()
        conn_rows = table.column("connectors").to_pylist()
        wkbs = table.column("geometry").to_pylist()
        rows_read += len(row_classes)
        for record in zip(row_classes, names, routes, conn_rows, wkbs):
            digest_source_row(row_digest, list(record))
        chosen = [i for i, (cls, wkb) in enumerate(zip(row_classes, wkbs))
                  if isinstance(cls, str) and wkb is not None
                  and _identity(names[i], routes[i]) is not None]
        if not chosen:
            continue
        lines = shapely.transform(shapely.from_wkb([wkbs[i] for i in chosen]),
                                  transformer.transform, interleaved=False)
        lengths = shapely.length(lines).tolist()
        coords, line_ids = shapely.get_coordinates(lines, return_index=True)
        points_by_line: list[list[tuple[float, float]]] = [[] for _ in chosen]
        for (x, y), line_id in zip(coords.tolist(), line_ids.tolist()):
            points_by_line[line_id].append((x, y))
        for local_id, source_id in enumerate(chosen):
            points = points_by_line[local_id]
            if len(points) < 2 or not np.isfinite(lengths[local_id]) or lengths[local_id] <= 0:
                continue
            row_connectors = [item for item in (conn_rows[source_id] or [])
                              if isinstance(item, dict) and item.get("connector_id")
                              and isinstance(item.get("at"), (int, float))]
            row_connectors.sort(key=lambda item: float(item["at"]))
            if len(row_connectors) >= 2:
                ends = (str(row_connectors[0]["connector_id"]),
                        str(row_connectors[-1]["connector_id"]))
            else:
                ends = (None, None)
                without_two_connectors += 1
            start = (points[1][0] - points[0][0], points[1][1] - points[0][1])
            end = (points[-2][0] - points[-1][0], points[-2][1] - points[-1][1])
            if math.hypot(*start) == 0 or math.hypot(*end) == 0:
                continue
            identity = _identity(names[source_id], routes[source_id])
            if identity is None:
                continue
            identities.append(identity)
            connectors.append(ends)
            tangents.append((start, end))
            classes.append(row_classes[source_id])
            geometry_rows += 1

    if rows_read != int(parquet.metadata.num_rows):
        raise ValueError(f"{region}: source rows {rows_read} != footer count {parquet.metadata.num_rows}")
    if geometry_rows != expected_identity_rows:
        raise ValueError(f"{region}: angle scan kept {geometry_rows} identity rows; baseline kept {expected_identity_rows}")

    by_identity_connector: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for row, (identity, pair) in enumerate(zip(identities, connectors)):
        for endpoint, connector in enumerate(pair):
            if connector:
                by_identity_connector[(identity, connector)].append((row, endpoint))

    possible_observations = 0
    eligible_observations = 0
    all_angles: list[float] = []
    eligible_angles: list[float] = []
    candidate_pairs: set[tuple[int, int]] = set()
    merged_pairs: set[tuple[int, int]] = set()
    for incidents in by_identity_connector.values():
        for left in range(len(incidents)):
            row_a, end_a = incidents[left]
            for right in range(left + 1, len(incidents)):
                row_b, end_b = incidents[right]
                if row_a == row_b:
                    continue
                pair = (min(row_a, row_b), max(row_a, row_b))
                angle = deflection_degrees(tangents[row_a][end_a], tangents[row_b][end_b])
                possible_observations += 1
                all_angles.append(angle)
                candidate_pairs.add(pair)
                if angle < THRESHOLD - 1e-12:
                    eligible_observations += 1
                    eligible_angles.append(angle)
                    merged_pairs.add(pair)
    components = stroke_components(identities, connectors, tangents,
                                   threshold_degrees=THRESHOLD)
    component_sizes = Counter(components)
    if len(component_sizes) != expected_component_count:
        raise ValueError(f"{region}: component count {len(component_sizes)} differs from identity/angle baseline {expected_component_count}")
    segments_in_merged_components = sum(size for size in component_sizes.values() if size > 1)
    effective_unions = geometry_rows - len(component_sizes)
    bins = [0, 5, 10, 15, 20, 25, 30, 45, 60, 90, 120, 150, 180]
    histogram = {}
    if all_angles:
        hist, edges = np.histogram(np.asarray(all_angles), bins=bins)
        histogram = {f"[{edges[i]:g},{edges[i+1]:g}{']' if i == len(hist)-1 else ')'}": int(hist[i])
                     for i in range(len(hist))}
    return {
        "road_rows_read": rows_read,
        "source_object_rows": int(parquet.metadata.num_rows),
        "source_selected_columns": READ_COLUMNS,
        "source_selected_payload_sha256": row_digest.hexdigest(),
        "identified_geometry_segments": geometry_rows,
        "angle_component_count": len(component_sizes),
        "identified_rows_without_two_endpoint_connectors": without_two_connectors,
        "same_identity_shared_endpoint_pair_observations": possible_observations,
        "eligible_pair_observations_strictly_below_30_degrees": eligible_observations,
        "pair_observation_merge_fraction": eligible_observations / possible_observations if possible_observations else 0.0,
        "unique_same_identity_candidate_segment_pairs": len(candidate_pairs),
        "unique_candidate_pairs_joined_by_angle_rule": len(merged_pairs),
        "unique_segment_pair_merge_fraction": len(merged_pairs) / len(candidate_pairs) if candidate_pairs else 0.0,
        "segments_in_nontrivial_angle_components": segments_in_merged_components,
        "fraction_of_identity_segments_in_nontrivial_components": segments_in_merged_components / geometry_rows if geometry_rows else 0.0,
        "effective_union_operations": effective_unions,
        "component_reduction_fraction": effective_unions / geometry_rows if geometry_rows else 0.0,
        "deflection_angle_degrees_all_candidate_pairs": summarize(np.asarray(all_angles)),
        "deflection_angle_degrees_merged_pairs": summarize(np.asarray(eligible_angles)),
        "deflection_angle_histogram_degrees": histogram,
    }


def audit_region(workspace: Path, region: str) -> dict[str, Any]:
    config = load_config(CONFIG_PATH)
    official = [(str(geoid), str(name)) for geoid, name in official_scored_pairs(workspace)]
    if len(official) != 9379 or len({geoid for geoid, _ in official}) != 9379:
        raise ValueError("official GEOID scope must be exactly 9,379 unique IDs")
    geoids = [geoid for geoid, name in official if name == region]
    if len(geoids) != int(config["inputs"]["official_rows_by_region"][region]):
        raise ValueError(f"{region}: unexpected official GEOID count")

    # This registered, label-free diagnostic independently reproduces both existing T1 outputs.
    baseline = geometry_audit.audit_region(workspace, region)
    base_result = baseline["region_result"]
    angles = angle_statistics(
        workspace, region, geoids, base_result["identity_geometry_rows"],
        base_result["endpoint_diagnostics"]["component_count"],
    )

    # Check exact identity T1's registered drop-length covariate, not just its gap output.
    inputs = ProvidedInputs(config, workspace, region, geoids)
    emissions = inputs.idea_emissions(["T1"])["T1"]
    t1_covariate, t1_covariate_defined = emissions[("R", "covariate")]
    tract_baseline = base_result["per_tract"]
    if [row["GEOID"] for row in tract_baseline] != geoids:
        raise ValueError(f"{region}: identity baseline GEOID order differs from official list")
    exact_drop_km = np.asarray([row["exact_identity_eligible_class_drop_km"] for row in tract_baseline], dtype=np.float64)
    merged_drop_km = np.asarray([row["merged_eligible_class_drop_km"] for row in tract_baseline], dtype=np.float64)
    baseline_defined = np.asarray([row["exact_identity_defined"] for row in tract_baseline], dtype=bool)
    if not np.array_equal(baseline_defined, np.asarray(t1_covariate_defined, dtype=bool)):
        raise ValueError(f"{region}: registered T1 covariate defined mask differs from identity recomputation")
    covariate_diff = np.asarray(t1_covariate, dtype=np.float64) - exact_drop_km
    max_before_cov_diff = float(np.max(np.abs(covariate_diff), initial=0.0))
    if max_before_cov_diff > 1e-12:
        raise ValueError(f"{region}: T1 identity drop-length covariate differs from registered T1; max={max_before_cov_diff}")
    signed_delta_km = merged_drop_km - exact_drop_km
    absolute_delta_km = np.abs(signed_delta_km)

    exact_named = np.asarray([row["exact_identity_named_length_km"] for row in tract_baseline], dtype=np.float64)
    merged_named = np.asarray([row["merged_eligible_named_km"] for row in tract_baseline], dtype=np.float64)
    exact_gap = np.asarray([row["exact_identity_gap"] if row["exact_identity_gap"] is not None else 0.0
                            for row in tract_baseline], dtype=np.float64)
    merged_gap = np.asarray([row["merged_gap"] if row["merged_gap"] is not None else 0.0
                             for row in tract_baseline], dtype=np.float64)
    exact_defined = baseline_defined
    merged_defined = np.asarray([row["merged_defined"] for row in tract_baseline], dtype=bool)
    both_defined = exact_defined & merged_defined
    per_tract = []
    for i, geoid in enumerate(geoids):
        per_tract.append({
            "GEOID": geoid,
            "T1_identity_defined": bool(exact_defined[i]),
            "T1_registered_drop_length_km": float(t1_covariate[i]),
            "identity_drop_length_km": float(exact_drop_km[i]),
            "angle_continuity_drop_length_km": float(merged_drop_km[i]),
            "signed_drop_length_change_km": float(signed_delta_km[i]),
            "absolute_drop_length_change_km": float(absolute_delta_km[i]),
            "identity_named_length_km": float(exact_named[i]),
            "angle_continuity_eligible_named_length_km": float(merged_named[i]),
            "angle_continuity_defined": bool(merged_defined[i]),
            "identity_gap": float(exact_gap[i]) if exact_defined[i] else None,
            "angle_continuity_gap": float(merged_gap[i]) if merged_defined[i] else None,
        })

    baseline_emission_hashes = {
        item.split(":", 1)[0]: item.split(":", 1)[1]
        for item in baseline["scope"].get("accessed_idea_artifacts", [])
    }
    t1_sha = sha256_file(workspace / "ideas/T1.parquet")
    t1v2_sha = sha256_file(workspace / "ideas/T1-v2.parquet")
    if baseline_emission_hashes.get("T1") != t1_sha or baseline_emission_hashes.get("T1-v2") != t1v2_sha:
        raise ValueError(f"{region}: registered artifact hashes differ from baseline reader log")
    candidate_registry_sha = sha256_file(CANDIDATE_REGISTRY)
    return {
        "region": region,
        "official_geoids": geoids,
        "official_geoids_sha256": hashlib.sha256("\n".join(geoids).encode()).hexdigest(),
        "source_road_object": baseline["scope"]["source_road_object"],
        "official_mask_wkb_sha256": baseline["scope"]["tract_mask_wkb_sha256"],
        "segments": {
            "source_rows_read": base_result["source_road_rows_read"],
            "identity_geometry_segments": base_result["identity_geometry_rows"],
            "segments_without_endpoint_connectors": base_result["identified_rows_without_endpoint_connectors"],
            "segments_without_tract_intersection": base_result["identified_rows_without_tract_intersection"],
            **angles,
        },
        "identity_only_measure_before": {
            "registered_T1_artifact_sha256": t1_sha,
            "registered_T1_v2_reference_sha256": t1v2_sha,
            "identity_gap_matches_registered_T1": base_result["exact_identity_reproduction"]["matches_registered_T1"],
            "identity_gap_max_abs_difference": base_result["exact_identity_reproduction"]["maximum_absolute_gap_difference"],
            "identity_covariate_matches_registered_T1": max_before_cov_diff <= 1e-12,
            "identity_covariate_max_abs_difference_km": max_before_cov_diff,
            "defined_tracts": int(exact_defined.sum()),
            "coverage_fraction": float(exact_defined.mean()),
            "named_length_total_km": float(exact_named.sum()),
            "drop_length_total_km": float(exact_drop_km.sum()),
            "named_length_per_tract_km": summarize(exact_named),
            "drop_length_per_tract_km": summarize(exact_drop_km),
            "gap_distribution_defined_tracts": base_result["exact_identity_reproduction"]["gap_distribution"],
        },
        "angle_continuity_measure_after": {
            "defined_tracts": int(merged_defined.sum()),
            "coverage_fraction": float(merged_defined.mean()),
            "named_length_total_km": float(merged_named.sum()),
            "drop_length_total_km": float(merged_drop_km.sum()),
            "named_length_per_tract_km": summarize(merged_named),
            "drop_length_per_tract_km": summarize(merged_drop_km),
            "gap_distribution_defined_tracts": base_result["geometric_merge"]["gap_distribution"],
            "absolute_drop_length_change_km": summarize(absolute_delta_km),
            "signed_drop_length_change_km": summarize(signed_delta_km),
            "mean_absolute_drop_length_change_km": float(absolute_delta_km.mean()),
            "tracts_with_nonzero_drop_length_change": int(np.sum(absolute_delta_km > 0.0)),
            "tracts_newly_defined": int(np.sum(~exact_defined & merged_defined)),
            "tracts_newly_undefined": int(np.sum(exact_defined & ~merged_defined)),
            "covariate_defined_mask_change_count": int(np.sum(exact_defined != merged_defined)),
            "gap_change_on_paired_defined_support": base_result["geometric_merge"]["gap_change_on_paired_defined_support"],
        },
        "per_tract": per_tract,
        "provenance": {
            "T1_source_sha256": sha256_file(T1_SOURCE),
            "T1_exact_source_sha256": sha256_file(T1_EXACT_SOURCE),
            "provided_input_reader_sha256": sha256_file(INPUTS_SOURCE),
            "harness_config_sha256": sha256_file(CONFIG_PATH),
            "candidate_registry_sha256": candidate_registry_sha,
            "registered_artifact_hashes_from_reader": baseline_emission_hashes,
        },
        "scope": {
            "official_tracts": len(geoids),
            "labels_or_targets_read": False,
            "removed_references_or_equivalents_read": False,
            "external_road_layers_read": False,
            "leaderboard_information_used": False,
            "target_fitting_performed": False,
            "candidate_artifacts_mutated": False,
            "uploads": 0,
            "source_columns_read": READ_COLUMNS,
        },
    }


def run_toy_geometry_case(angle_degrees: float) -> dict[str, Any]:
    """Return a hand-constructed EPSG:5070 two-tract length sensitivity case."""
    import shapely

    theta = math.radians(angle_degrees)
    # Two 10 m directed pieces at connector j; the second reaches left into tract A.
    primary = shapely.LineString([(130.0, 50.0), (100.0, 50.0)])
    tertiary = shapely.LineString([(100.0, 50.0),
                                   (100.0 - 10.0 * math.cos(theta),
                                    50.0 + 10.0 * math.sin(theta))])
    remote_named = shapely.LineString([(10.0, 10.0), (60.0, 10.0)])
    tracts = [shapely.box(0, 0, 100, 100), shapely.box(100, 0, 200, 100)]
    lines = [primary, tertiary, remote_named]
    classes = ["primary", "tertiary", "primary"]
    ids = ["road:x"] * 3
    connectors = [("remote", "j"), ("j", "drop-end"), ("a", "b")]
    tangents = [geometry_audit.metric_tangent(list(line.coords)) for line in lines]
    full_lengths = [float(line.length) for line in lines]
    tract_lengths = []
    for line in lines:
        local = {}
        for tract_id, tract in enumerate(tracts):
            clipped = line.intersection(tract).length
            if clipped > 0:
                local[tract_id] = float(clipped)
        tract_lengths.append(local)
    exact = geometry_audit._exact_identity_t1(ids, classes, tract_lengths, 2,
                                               outside_share_threshold=0.7)
    labels = stroke_components(ids, connectors, tangents, threshold_degrees=THRESHOLD)
    merged = geometry_audit._aggregate(labels, classes, full_lengths, tract_lengths, 2,
                                       outside_share_threshold=0.7)
    exact_a = float(exact["eligible_drop_length_m"][0])
    merged_a = float(merged["eligible_drop_length_m"][0])
    exact_b = float(exact["eligible_drop_length_m"][1])
    merged_b = float(merged["eligible_drop_length_m"][1])
    expected_tertiary = float(tertiary.intersection(tracts[0]).length)
    return {
        "angle_degrees": angle_degrees,
        "exact_identity_drop_m": [exact_a, exact_b],
        "angle_continuity_drop_m": [merged_a, merged_b],
        "expected_eligible_tract_a_drop_m": expected_tertiary if angle_degrees < THRESHOLD else 0.0,
        "geometry_length_error_m": abs(expected_tertiary - 10.0),
        "within_1e-9_m": abs(expected_tertiary - 10.0) <= 1e-9,
        "merged_components": len(set(labels)),
        "source_segments": len(lines),
    }


def toy_geometry_checks() -> dict[str, Any]:
    below = run_toy_geometry_case(29.0)
    threshold = run_toy_geometry_case(30.0)
    above = run_toy_geometry_case(31.0)
    return {
        "cases": {"29_degrees": below, "30_degrees": threshold, "31_degrees": above},
        "strict_threshold_behavior": (
            below["angle_continuity_drop_m"][0] > 0.0
            and threshold["angle_continuity_drop_m"][0] == 0.0
            and above["angle_continuity_drop_m"][0] == 0.0
        ),
        "all_lengths_within_1e-9_m": all(case["within_1e-9_m"] for case in (below, threshold, above)),
    }


def combine(paths: list[Path], out: Path, repeat_path: Path | None = None) -> dict[str, Any]:
    by_region: dict[str, tuple[dict[str, Any], Path]] = {}
    for path in paths:
        part = json.loads(path.read_text(encoding="utf-8"))
        region = part["region"]
        if region in by_region:
            raise ValueError(f"duplicate region report {region}")
        by_region[region] = (part, path)
    if set(by_region) != set(REGIONS):
        raise ValueError(f"expected {REGIONS}; found {sorted(by_region)}")
    regions = {region: by_region[region][0] for region in REGIONS}
    all_ids = [geoid for region in REGIONS for geoid in regions[region]["official_geoids"]]
    if len(all_ids) != 9379 or len(set(all_ids)) != 9379:
        raise ValueError("regional results are not an exact unique 9,379-ID partition")
    for region in REGIONS:
        report = regions[region]
        if report["scope"]["official_tracts"] != len(report["official_geoids"]):
            raise ValueError(f"{region}: per-tract outputs do not cover all official IDs")
        if [row["GEOID"] for row in report["per_tract"]] != report["official_geoids"]:
            raise ValueError(f"{region}: per-tract row order differs from official GEOIDs")
        if not report["identity_only_measure_before"]["identity_covariate_matches_registered_T1"]:
            raise ValueError(f"{region}: identity-only covariate does not reproduce T1")
    repeat_result = {"checked": False, "exact_match": None, "path": None, "sha256": None}
    if repeat_path is not None:
        repeated = json.loads(repeat_path.read_text(encoding="utf-8"))
        repeated_region = repeated["region"]
        if repeated_region not in regions or repeated != regions[repeated_region]:
            raise ValueError("determinism repeat JSON differs from its original regional report")
        repeat_result = {"checked": True, "exact_match": True, "region": repeated_region,
                         "path": str(repeat_path), "sha256": sha256_file(repeat_path)}

    total_segments = sum(r["segments"]["identified_geometry_segments"] for r in regions.values())
    candidate_pairs = sum(r["segments"]["unique_same_identity_candidate_segment_pairs"] for r in regions.values())
    joined_pairs = sum(r["segments"]["unique_candidate_pairs_joined_by_angle_rule"] for r in regions.values())
    official_n = 9379
    exact_cov = sum(r["identity_only_measure_before"]["defined_tracts"] for r in regions.values())
    angle_cov = sum(r["angle_continuity_measure_after"]["defined_tracts"] for r in regions.values())
    exact_named_total = sum(r["identity_only_measure_before"]["named_length_total_km"] for r in regions.values())
    exact_drop_total = sum(r["identity_only_measure_before"]["drop_length_total_km"] for r in regions.values())
    angle_named_total = sum(r["angle_continuity_measure_after"]["named_length_total_km"] for r in regions.values())
    angle_drop_total = sum(r["angle_continuity_measure_after"]["drop_length_total_km"] for r in regions.values())
    delta_abs = np.concatenate([np.asarray([x["absolute_drop_length_change_km"] for x in r["per_tract"]]) for r in regions.values()])
    delta_signed = np.concatenate([np.asarray([x["signed_drop_length_change_km"] for x in r["per_tract"]]) for r in regions.values()])
    toy = toy_geometry_checks()
    candidate_sha = sha256_file(CANDIDATE_REGISTRY)
    if any(r["provenance"]["candidate_registry_sha256"] != candidate_sha for r in regions.values()):
        raise ValueError("candidate registry changed while sensitivity reports were produced")
    result = {
        "schema_version": 1,
        "audit": "T1 named-stroke angle-continuity sensitivity; provided-road diagnostic only",
        "scope": {
            "official_regions": list(REGIONS),
            "official_tracts": official_n,
            "provided_road_layer_only": True,
            "labels_or_targets_read": False,
            "removed_reference_layers_or_equivalents_read": False,
            "leaderboard_information_used": False,
            "target_fitting_performed": False,
            "candidate_artifacts_mutated": False,
            "uploads": 0,
        },
        "method": {
            "identity_baseline": "frozen T1 exact normalized names.primary, fallback route network/ref, with outside named share >=0.70",
            "angle_alternative": "same-identity endpoint segments join only when outward-tangent deflection is strictly <30 degrees; no cross-identity join; lengths measured in EPSG:5070",
            "covariate": "per-tract eligible class-drop length in km; zero-filled length values are compared on every official ID, with identity and angle coverage masks reported separately",
            "segment_merge_fraction_definition": "unique distinct same-identity segment pairs that share at least one endpoint connector and meet <30 degrees divided by unique same-identity pairs sharing an endpoint connector; also report segments in nontrivial angle components / all identity geometry segments",
            "interpretation_limit": "sensitivity of a frozen provided-road proxy only; no accuracy or target claim",
        },
        "measure_before": {
            "T1_identity_outputs_reproduced": True,
            "all_identity_gap_outputs_match_registered_T1": all(r["identity_only_measure_before"]["identity_gap_matches_registered_T1"] for r in regions.values()),
            "all_identity_drop_length_covariates_match_registered_T1": all(r["identity_only_measure_before"]["identity_covariate_matches_registered_T1"] for r in regions.values()),
            "registered_T1_artifact_sha256_by_region": {r: regions[r]["identity_only_measure_before"]["registered_T1_artifact_sha256"] for r in REGIONS},
            "named_length_total_km": exact_named_total,
            "eligible_drop_length_total_km": exact_drop_total,
            "defined_tracts": exact_cov,
            "defined_fraction": exact_cov / official_n,
            "regional": {r: regions[r]["identity_only_measure_before"] for r in REGIONS},
        },
        "measure_after": {
            "identified_geometry_segments": total_segments,
            "unique_same_identity_endpoint_candidate_pairs": candidate_pairs,
            "unique_candidate_pairs_merged_strictly_below_30_degrees": joined_pairs,
            "unique_candidate_pair_merge_fraction": joined_pairs / candidate_pairs if candidate_pairs else 0.0,
            "fraction_of_identity_segments_in_nontrivial_components": (
                sum(r["segments"]["segments_in_nontrivial_angle_components"] for r in regions.values()) / total_segments if total_segments else 0.0),
            "effective_union_component_reduction_fraction": (
                sum(r["segments"]["effective_union_operations"] for r in regions.values()) / total_segments if total_segments else 0.0),
            "deflection_angle_distributions_by_region": {r: {
                "all_candidate_pairs": regions[r]["segments"]["deflection_angle_degrees_all_candidate_pairs"],
                "merged_pairs": regions[r]["segments"]["deflection_angle_degrees_merged_pairs"],
                "histogram": regions[r]["segments"]["deflection_angle_histogram_degrees"],
            } for r in REGIONS},
            "named_length_total_km": angle_named_total,
            "eligible_drop_length_total_km": angle_drop_total,
            "defined_tracts": angle_cov,
            "defined_fraction": angle_cov / official_n,
            "regions": {r: regions[r]["angle_continuity_measure_after"] for r in REGIONS},
            "per_tract_absolute_drop_length_change_km": summarize(delta_abs),
            "per_tract_signed_drop_length_change_km": summarize(delta_signed),
            "tracts_with_nonzero_covariate_change": int(np.sum(delta_abs > 0.0)),
            "toy_geometry_cases": toy,
            "determinism_repeat": repeat_result,
            "input_hashes_by_region": {r: {
                "provided_road_payload_sha256": regions[r]["segments"]["source_selected_payload_sha256"],
                "official_GEOIDs_sha256": regions[r]["official_geoids_sha256"],
                "official_mask_wkb_sha256": regions[r]["official_mask_wkb_sha256"],
                "T1_artifact_sha256": regions[r]["identity_only_measure_before"]["registered_T1_artifact_sha256"],
                "T1_v2_reference_artifact_sha256": regions[r]["identity_only_measure_before"]["registered_T1_v2_reference_sha256"],
            } for r in REGIONS},
            "acceptance": {
                "identity_T1_outputs_reproduced": all(r["identity_only_measure_before"]["identity_gap_matches_registered_T1"] and r["identity_only_measure_before"]["identity_covariate_matches_registered_T1"] for r in regions.values()),
                "all_9379_official_ids_covered_exactly_once": len(all_ids) == 9379 and len(set(all_ids)) == 9379,
                "deterministic_rerun_exact_match": repeat_result["exact_match"] is True,
                "toy_geometry_cases_within_1e_9_m": toy["all_lengths_within_1e-9_m"] and toy["strict_threshold_behavior"],
                "candidate_artifacts_unchanged": all(not r["scope"]["candidate_artifacts_mutated"] for r in regions.values()),
                "accepted": bool(all(r["identity_only_measure_before"]["identity_gap_matches_registered_T1"] and r["identity_only_measure_before"]["identity_covariate_matches_registered_T1"] for r in regions.values())
                                 and len(all_ids) == 9379 and len(set(all_ids)) == 9379
                                 and repeat_result["exact_match"] is True
                                 and toy["all_lengths_within_1e-9_m"] and toy["strict_threshold_behavior"]
                                 and all(not r["scope"]["candidate_artifacts_mutated"] for r in regions.values())),
            },
        },
        "regions": regions,
        "provenance": {
            "sensitivity_script_sha256": sha256_file(Path(__file__).resolve()),
            "T1_source_sha256": sha256_file(T1_SOURCE),
            "T1_exact_source_sha256": sha256_file(T1_EXACT_SOURCE),
            "provided_input_reader_sha256": sha256_file(INPUTS_SOURCE),
            "harness_config_sha256": sha256_file(CONFIG_PATH),
            "candidate_registry_sha256": candidate_sha,
            "regional_checkpoint_sha256": {r: sha256_file(by_region[r][1]) for r in REGIONS},
        },
        "reproduction": {
            "per_region": "uv run --with-requirements requirements.txt python scripts/screen_t1_angle_continuity_sensitivity.py --workspace $COMPETITION_WORKSPACE --region <REGION> --out $COMPETITION_WORKSPACE/t1-angle-continuity-checkpoints/<REGION>.json",
            "combine": "uv run --with-requirements requirements.txt python scripts/screen_t1_angle_continuity_sensitivity.py --merge-region-reports <FOUR_REGION_JSON_PATHS> --repeat-checkpoint <identical-rerun.json> --out reports/bias60-t1-angle-continuity-sensitivity.json",
            "run_policy": "Run one region per invocation; repeat one checkpoint byte-for-byte; combine after all regions and rerun check complete.",
        },
    }
    if not result["measure_after"]["acceptance"]["accepted"]:
        raise ValueError("sensitivity report failed an acceptance criterion")
    payload = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if len(payload.encode()) > 20_000_000:
        raise ValueError("combined report exceeds 20 MB bound")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    workspace_default = os.environ.get("COMPETITION_WORKSPACE")
    parser.add_argument("--workspace", type=Path, default=Path(workspace_default) if workspace_default else None)
    parser.add_argument("--region", choices=REGIONS)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/bias60-t1-angle-continuity-sensitivity.json")
    parser.add_argument("--merge-region-reports", nargs="+", type=Path)
    parser.add_argument("--repeat-checkpoint", type=Path)
    args = parser.parse_args()
    if args.merge_region_reports:
        report = combine(args.merge_region_reports, args.out, args.repeat_checkpoint)
        print(json.dumps({"official_tracts": report["scope"]["official_tracts"],
                          "identity_defined": report["measure_before"]["defined_tracts"],
                          "angle_defined": report["measure_after"]["defined_tracts"],
                          "candidate_pair_merge_fraction": report["measure_after"]["unique_candidate_pair_merge_fraction"],
                          "absolute_delta_max_km": report["measure_after"]["per_tract_absolute_drop_length_change_km"]["max"],
                          "accepted": report["measure_after"]["acceptance"]["accepted"],
                          "output": str(args.out)}, sort_keys=True))
        return 0
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    if args.region is None:
        parser.error("--region is required; audit separately and combine checkpoints")
    report = audit_region(args.workspace, args.region)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    result = report["angle_continuity_measure_after"]
    print(json.dumps({"region": args.region,
                      "official_tracts": report["scope"]["official_tracts"],
                      "identified_segments": report["segments"]["identified_geometry_segments"],
                      "pair_merge_fraction": report["segments"]["unique_segment_pair_merge_fraction"],
                      "absolute_delta_max_km": result["absolute_drop_length_change_km"]["max"],
                      "output": str(args.out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
