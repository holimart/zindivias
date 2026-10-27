#!/usr/bin/env python3
"""Combine conservative T1 cross-identity joins with angle thresholds (diagnostic only)."""
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
import audit_t1_cross_identity_join_safety as safety_audit  # noqa: E402
import audit_t1_geometry_stroke_merge as geometry_audit  # noqa: E402
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
from bias60.ideas.t import _identity, _road_layer  # noqa: E402
from bias60.ideas.t_exact import stroke_components  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
THRESHOLDS = (15.0, 30.0, 45.0)
SAFETY_REPORT = ROOT / "reports/t1-cross-identity-join-safety.json"
ANGLE_REPORT = ROOT / "reports/bias60-t1-angle-continuity-sensitivity.json"
SAFETY_SCRIPT = ROOT / "scripts/audit_t1_cross_identity_join_safety.py"
ANGLE_SCRIPT = ROOT / "scripts/screen_t1_angle_continuity_sensitivity.py"
INPUTS_SOURCE = ROOT / "src/bias60/inputs.py"
T1_SOURCE = ROOT / "src/bias60/ideas/t.py"
T1_EXACT_SOURCE = ROOT / "src/bias60/ideas/t_exact.py"
HARNESS_CONFIG = ROOT / "config/bias60-harness-preregistered.json"
SAFETY_REPORT_SHA256 = "a52d21fbdac28f4264011f73bc196b488bdb5e372f350d87829283df38001197"
ANGLE_REPORT_SHA256 = "751a94c824330be04d6edec723489a4e9773b1d603bf77eda86bcaba4dbde0ff"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def average_ranks(values: Sequence[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim != 1 or not np.isfinite(arr).all():
        raise ValueError("rank correlation inputs must be finite vectors")
    order = np.argsort(arr, kind="mergesort")
    ranks = np.empty(arr.size, dtype=np.float64)
    start = 0
    while start < arr.size:
        end = start + 1
        while end < arr.size and arr[order[end]] == arr[order[start]]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2.0
        start = end
    return ranks


def spearman_rho(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("rank correlation vectors must have equal length >=2")
    a, b = average_ranks(left), average_ranks(right)
    if np.std(a) == 0.0 or np.std(b) == 0.0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def summarize(values: Sequence[float]) -> dict[str, float | int | None]:
    arr = np.asarray(values, dtype=np.float64)
    if not arr.size:
        return {"n": 0, "min": None, "q25": None, "median": None,
                "q75": None, "q90": None, "q95": None, "max": None}
    qs = np.quantile(arr, [0, .25, .5, .75, .9, .95, 1])
    return {"n": int(arr.size), "min": float(qs[0]), "q25": float(qs[1]),
            "median": float(qs[2]), "q75": float(qs[3]), "q90": float(qs[4]),
            "q95": float(qs[5]), "max": float(qs[6])}


def verify_frozen_reports() -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    safety = json.loads(SAFETY_REPORT.read_text(encoding="utf-8"))
    angle = json.loads(ANGLE_REPORT.read_text(encoding="utf-8"))
    hashes = {
        "cross_identity_safety_report_sha256": sha256_file(SAFETY_REPORT),
        "angle_only_report_sha256": sha256_file(ANGLE_REPORT),
        "cross_identity_safety_script_sha256": sha256_file(SAFETY_SCRIPT),
        "angle_only_script_sha256": sha256_file(ANGLE_SCRIPT),
        "provided_input_reader_sha256": sha256_file(INPUTS_SOURCE),
        "T1_source_sha256": sha256_file(T1_SOURCE),
        "T1_exact_source_sha256": sha256_file(T1_EXACT_SOURCE),
        "harness_config_sha256": sha256_file(HARNESS_CONFIG),
    }
    if hashes["cross_identity_safety_report_sha256"] != SAFETY_REPORT_SHA256:
        raise ValueError("frozen unique-join safety report SHA-256 changed")
    if hashes["angle_only_report_sha256"] != ANGLE_REPORT_SHA256:
        raise ValueError("frozen angle-only report SHA-256 changed")
    if safety["provenance"]["analysis_script_sha256"] != hashes["cross_identity_safety_script_sha256"]:
        raise ValueError("unique-join safety script hash differs from its frozen report")
    if angle["provenance"]["sensitivity_script_sha256"] != hashes["angle_only_script_sha256"]:
        raise ValueError("angle-only script hash differs from its frozen report")
    for key in ("T1_source_sha256", "T1_exact_source_sha256", "harness_config_sha256"):
        if safety["provenance"][key] != hashes[key] or angle["provenance"][key] != hashes[key]:
            raise ValueError(f"frozen reports disagree with current source hash: {key}")
    if angle["provenance"]["provided_input_reader_sha256"] != hashes["provided_input_reader_sha256"]:
        raise ValueError("provided-input reader hash differs from frozen angle-only report")
    if not safety["measure_after"]["acceptance"]["all_accepted_pairs_unambiguous_and_one_to_one"]:
        raise ValueError("frozen safety report did not pass its uniqueness gate")
    if not angle["measure_after"]["acceptance"]["identity_T1_outputs_reproduced"]:
        raise ValueError("frozen angle-only report did not reproduce identity T1")
    return safety, angle, hashes


def audit_region(workspace: Path, region: str, safety_report: dict[str, Any],
                 angle_report: dict[str, Any]) -> dict[str, Any]:
    import pyarrow.parquet as pq
    import shapely
    from pyproj import Transformer

    config = load_config(HARNESS_CONFIG)
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(official) != 9379 or len({g for g, _ in official}) != 9379:
        raise ValueError("official sample must contain exactly 9,379 unique GEOIDs")
    geoids = [g for g, r in official if r == region]
    if len(geoids) != int(config["inputs"]["official_rows_by_region"][region]):
        raise ValueError(f"{region}: official GEOID count mismatch")
    inputs = ProvidedInputs(config, workspace, region, geoids)
    source_tracts = inputs.tract_geometries()
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    tracts = shapely.transform(source_tracts, transformer.transform, interleaved=False)
    tree = shapely.STRtree(tracts)
    seed = config["seeds"]["T1"]
    columns = ["class", "names", "routes", "connectors", "geometry"]
    parquet = _road_layer(inputs, columns, seed["unfiltered_layer"])

    identities: list[str | None] = []
    evidences: list[frozenset[tuple[str, ...]]] = []
    classes: list[str] = []
    connectors: list[tuple[str | None, str | None]] = []
    tangents: list[tuple[tuple[float, float], tuple[float, float]]] = []
    geometry_hashes: list[str] = []
    full_lengths: list[float] = []
    tract_lengths: list[dict[int, float]] = []
    source_row_ids: list[int] = []
    raw_digest = hashlib.sha256()
    rows_read = row_offset = 0
    rows_without_connectors = rows_without_tracts = 0
    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=columns)
        row_classes = table.column("class").to_pylist()
        names = table.column("names").to_pylist()
        routes = table.column("routes").to_pylist()
        conn_rows = table.column("connectors").to_pylist()
        wkbs = table.column("geometry").to_pylist()
        for record in zip(row_classes, names, routes, conn_rows, wkbs):
            digest_source_row(raw_digest, list(record))
        rows_read += len(row_classes)
        chosen = [i for i, (cls, wkb) in enumerate(zip(row_classes, wkbs))
                  if isinstance(cls, str) and wkb is not None and _identity(names[i], routes[i]) is not None]
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
                row_connectors = [item for item in (conn_rows[source_id] or [])
                                  if isinstance(item, dict) and item.get("connector_id")
                                  and isinstance(item.get("at"), (int, float))]
                row_connectors.sort(key=lambda item: float(item["at"]))
                if len(row_connectors) >= 2:
                    ends = (str(row_connectors[0]["connector_id"]), str(row_connectors[-1]["connector_id"]))
                else:
                    ends = (None, None)
                    rows_without_connectors += 1
                identities.append(identity)
                evidences.append(safety_audit.route_name_evidence(names[source_id], routes[source_id]))
                classes.append(row_classes[source_id])
                connectors.append(ends)
                tangents.append(geometry_audit.metric_tangent(points))
                geometry_hashes.append(geometry_audit.sha256_bytes(
                    shapely.to_wkb(shapely.normalize(metric_lines[local_id]))))
                full_lengths.append(float(lengths[local_id]))
                tract_lengths.append(dict(local_lengths[local_id]))
                source_row_ids.append(row_offset + source_id)
                if not local_lengths[local_id]:
                    rows_without_tracts += 1
        row_offset += len(row_classes)
    if rows_read != int(parquet.metadata.num_rows) or row_offset != rows_read:
        raise ValueError(f"{region}: Parquet row count/footer mismatch")
    if not identities or any(value is None for value in identities):
        raise ValueError(f"{region}: missing identified road rows")

    registered = inputs.idea_emissions(["T1"])["T1"]
    t1_cov, t1_defined = registered[("R", "covariate")]
    t1_artifact_sha = sha256_file(workspace / "ideas/T1.parquet")
    t1v2_artifact_sha = sha256_file(workspace / "ideas/T1-v2.parquet")
    angle_input_hashes = angle_report["measure_after"]["input_hashes_by_region"][region]
    safety_before = safety_report["regions"][region]["measure_before_reproduction"]
    if (t1_artifact_sha != angle_input_hashes["T1_artifact_sha256"]
            or t1_artifact_sha != safety_before["registered_T1_artifact_sha256"]
            or t1v2_artifact_sha != angle_input_hashes["T1_v2_reference_artifact_sha256"]
            or t1v2_artifact_sha != safety_before["registered_T1_v2_artifact_sha256"]):
        raise ValueError(f"{region}: registered T1/T1-v2 artifact hashes differ from frozen reports")
    exact = geometry_audit._exact_identity_t1(
        identities, classes, tract_lengths, len(geoids),
        outside_share_threshold=float(seed["outside_named_share_threshold"]))
    identity_cov_km = exact["eligible_drop_length_m"] / 1000.0
    identity_mask = np.asarray(exact["defined"], dtype=bool)
    t1_cov = np.asarray(t1_cov, dtype=np.float64)
    t1_defined = np.asarray(t1_defined, dtype=bool)
    if not np.array_equal(identity_mask, t1_defined) or not np.allclose(
            identity_cov_km[identity_mask], t1_cov[identity_mask], rtol=0.0, atol=1e-12):
        raise ValueError(f"{region}: frozen identity-only covariate did not reproduce")
    if not np.allclose(identity_cov_km, t1_cov, rtol=0.0, atol=1e-12):
        raise ValueError(f"{region}: identity covariate differs on full official ID vector")

    safety_tract = safety_report["regions"][region]["per_tract"]
    angle_tract = angle_report["regions"][region]["per_tract"]
    if [x["GEOID"] for x in safety_tract] != geoids or [x["GEOID"] for x in angle_tract] != geoids:
        raise ValueError(f"{region}: frozen report GEOID coverage/order differs")
    if any(not math.isclose(identity_cov_km[i], safety_tract[i]["T1_exact_identity_class_drop_km"],
                            rel_tol=0.0, abs_tol=1e-12) for i in range(len(geoids))):
        raise ValueError(f"{region}: identity covariate differs from unique-join safety report")
    if any(not math.isclose(identity_cov_km[i], angle_tract[i]["identity_drop_length_km"],
                            rel_tol=0.0, abs_tol=1e-12) for i in range(len(geoids))):
        raise ValueError(f"{region}: identity covariate differs from angle-only report")

    # Reproduce the angle-only 30-degree endpoint variant before measuring interactions.
    same30_labels = stroke_components(identities, connectors, tangents, threshold_degrees=30.0)
    same30 = geometry_audit._aggregate(
        same30_labels, classes, full_lengths, tract_lengths, len(geoids),
        outside_share_threshold=float(seed["outside_named_share_threshold"]))
    if any(not math.isclose(same30["eligible_drop_length_m"][i] / 1000.0,
                            angle_tract[i]["angle_continuity_drop_length_km"],
                            rel_tol=0.0, abs_tol=1e-12) for i in range(len(geoids))):
        raise ValueError(f"{region}: same-identity 30-degree angle-only output failed reproduction")

    raw_road_hash = raw_digest.hexdigest()
    angle_hashes = angle_report["measure_after"]["input_hashes_by_region"][region]
    if raw_road_hash != angle_hashes["provided_road_payload_sha256"]:
        raise ValueError(f"{region}: provided road payload hash differs from angle-only report")
    geoid_hash = sha256_bytes("\n".join(geoids).encode())
    if geoid_hash != angle_hashes["official_GEOIDs_sha256"]:
        raise ValueError(f"{region}: official GEOID hash differs from angle-only report")
    mask_hash = sha256_bytes(b"\n".join(shapely.to_wkb(tract) for tract in source_tracts))
    if mask_hash != angle_hashes["official_mask_wkb_sha256"]:
        raise ValueError(f"{region}: official tract mask hash differs from angle-only report")

    variants: dict[str, Any] = {}
    total_per_tract = [{"GEOID": geoid, "identity_only_class_drop_km": float(identity_cov_km[i]),
                        "identity_only_defined": bool(identity_mask[i])} for i, geoid in enumerate(geoids)]
    for threshold in THRESHOLDS:
        same_labels = stroke_components(identities, connectors, tangents, threshold_degrees=threshold)
        same_only = geometry_audit._aggregate(
            same_labels, classes, full_lengths, tract_lengths, len(geoids),
            outside_share_threshold=float(seed["outside_named_share_threshold"]))
        safe = safety_audit.cross_identity_safety(
            identities, connectors, tangents, evidences, same_labels, threshold_degrees=threshold)
        combined_labels = safety_audit.union_component_labels(same_labels, safe["accepted_pair_details"])
        combined = geometry_audit._aggregate(
            combined_labels, classes, full_lengths, tract_lengths, len(geoids),
            outside_share_threshold=float(seed["outside_named_share_threshold"]))
        alt_km = combined["eligible_drop_length_m"] / 1000.0
        deltas = alt_km - identity_cov_km
        rho = spearman_rho(identity_cov_km, alt_km)
        component_identities: dict[int, set[str]] = defaultdict(set)
        for label, identity in zip(combined_labels, identities):
            component_identities[label].add(str(identity))
        cross_components = sum(len(v) > 1 for v in component_identities.values())
        accepted_details = []
        for edge in safe["accepted_pair_details"]:
            accepted_details.append({
                **edge,
                "left_source_road_row": source_row_ids[edge["left_row"]],
                "right_source_road_row": source_row_ids[edge["right_row"]],
            })
        # Source row indices are selected identified-geometry ordinals, stable in stream order.
        # Avoid presenting them as raw Parquet row ordinals (filtered rows are omitted).
        if cross_components != len(safe["accepted_pair_details"]):
            raise AssertionError("combined component count does not equal accepted safe joins")
        if safe["accepted_ambiguous_merges"] or safe["accepted_many_to_one_component_merges"]:
            raise AssertionError("safety helper returned an ambiguous or many-to-one accepted join")
        if threshold == 30.0:
            frozen_alt = safety_report["regions"][region]["per_tract"]
            safety_counts = safety_report["regions"][region]["cross_identity_safety"]
            if safe["unique_safe_endpoint_pairs"] != safety_counts["unique_safe_endpoint_pairs"]:
                raise ValueError(f"{region}: 30-degree unique-join count differs from safety report")
            if any(not math.isclose(alt_km[i], frozen_alt[i]["cross_identity_alternative_class_drop_km"],
                                    rel_tol=0.0, abs_tol=1e-12) for i in range(len(geoids))):
                raise ValueError(f"{region}: combined 30-degree output differs from frozen safety diagnostic")
        variants[str(int(threshold))] = {
            "angle_limit_degrees_strictly_less_than": threshold,
            "same_identity_angle_only_defined_tracts": int(same_only["defined"].sum()),
            "combined_defined_tracts": int(combined["defined"].sum()),
            "cross_identity_safety": {
                "compatible_cross_identity_endpoint_pairs": safe["otherwise_compatible_endpoint_pairs"],
                "route_name_evidence_endpoint_pairs": safe["strict_route_name_evidence_endpoint_pairs"],
                "accepted_unique_pairs": safe["unique_safe_endpoint_pairs"],
                "accepted_fraction_of_compatible_pairs": safe["unique_safe_fraction_of_compatible_pairs"],
                "rejected_ambiguous_or_conflicting_evidence_pairs": safe["evidence_backed_candidate_pairs_rejected_as_ambiguous_or_conflicting"],
                "ambiguous_accepted_merges": safe["accepted_ambiguous_merges"],
                "many_to_one_accepted_merges": safe["accepted_many_to_one_component_merges"],
                "cross_identity_component_merges": cross_components,
                "accepted_pairs": accepted_details,
                "accepted_pairs_are_one_to_one": safe["accepted_ambiguous_merges"] == 0
                    and safe["accepted_many_to_one_component_merges"] == 0,
            },
            "rank_comparison": {
                "metric": "Spearman rank correlation with average ranks for ties",
                "n_official_geoids": len(geoids),
                "defined_identity_covariates": int(identity_mask.sum()),
                "defined_combined_covariates": int(combined["defined"].sum()),
                "undefined values are zero as in registered covariate vectors": True,
                "rho_identity_only_vs_combined": rho,
            },
            "delta_vs_identity_only_class_drop_km": summarize(deltas),
            "delta_on_both_defined_support_km": summarize([
                float(deltas[i]) for i in range(len(geoids))
                if identity_mask[i] and combined["defined"][i]]),
            "tracts_with_nonzero_covariate_delta": int(np.count_nonzero(np.abs(deltas) > 1e-12)),
            "per_tract": [{
                "GEOID": geoid,
                "identity_only_class_drop_km": float(identity_cov_km[i]),
                "identity_only_defined": bool(identity_mask[i]),
                "combined_class_drop_km": float(alt_km[i]),
                "combined_defined": bool(combined["defined"][i]),
                "delta_vs_identity_only_km": float(deltas[i]),
                "delta_when_both_defined_km": (float(deltas[i]) if identity_mask[i] and combined["defined"][i] else None),
            } for i, geoid in enumerate(geoids)],
        }
        for i, (row, geoid) in enumerate(zip(total_per_tract, geoids)):
            row[f"angle_{int(threshold)}_combined_class_drop_km"] = float(alt_km[i])
            row[f"angle_{int(threshold)}_combined_defined"] = bool(combined["defined"][i])
            row[f"angle_{int(threshold)}_delta_km"] = float(deltas[i])

    return {
        "schema_version": 1,
        "region": region,
        "scope": {
            "official_geoids": geoids,
            "official_geoids_sha256": geoid_hash,
            "official_tracts": len(geoids),
            "source_rows_read": rows_read,
            "identified_geometry_rows": len(identities),
            "labels_or_targets_read": False,
            "removed_references_or_equivalents_read": False,
            "external_data_read": False,
            "leaderboard_information_used": False,
            "target_fitting_performed": False,
            "candidate_artifacts_changed": False,
            "uploads": 0,
        },
        "measure_before_reproduction": {
            "identity_covariate_matches_registered_T1": True,
            "registered_T1_artifact_hash_matches_frozen_reports": True,
            "registered_T1_v2_artifact_hash_matches_frozen_reports": True,
            "registered_T1_artifact_sha256": t1_artifact_sha,
            "registered_T1_v2_artifact_sha256": t1v2_artifact_sha,
            "identity_covariate_matches_safety_report": True,
            "identity_covariate_matches_angle_only_report": True,
            "same_identity_30_degree_output_matches_angle_only_report": True,
            "combined_30_degree_output_matches_safety_report": True,
            "source_road_payload_sha256_matches_angle_only_report": True,
            "official_GEOID_hash_matches_angle_only_report": True,
            "tract_mask_hash_matches_angle_only_report": True,
            "provided_road_payload_sha256": raw_road_hash,
            "official_mask_wkb_sha256": mask_hash,
        },
        "variants": variants,
        "per_tract": total_per_tract,
        "provenance": {
            "analysis_script_sha256": sha256_file(Path(__file__).resolve()),
            "safety_report_sha256": sha256_file(SAFETY_REPORT),
            "angle_only_report_sha256": sha256_file(ANGLE_REPORT),
            "official_id_sha256": geoid_hash,
            "provided_road_payload_sha256": raw_road_hash,
        },
    }


def combine(paths: Sequence[Path], output: Path, repeat_path: Path | None = None) -> dict[str, Any]:
    safety_report, angle_report, frozen_hashes = verify_frozen_reports()
    regions: dict[str, Any] = {}
    for path in paths:
        part = json.loads(path.read_text(encoding="utf-8"))
        region = part["region"]
        if region in regions:
            raise ValueError(f"duplicate region checkpoint: {region}")
        regions[region] = part
    if set(regions) != set(REGIONS):
        raise ValueError(f"expected exactly four official regions; found {sorted(regions)}")
    geoids = [g for region in REGIONS for g in regions[region]["scope"]["official_geoids"]]
    if len(geoids) != 9379 or len(set(geoids)) != 9379:
        raise ValueError("regional outputs must contain exactly 9,379 unique official IDs")
    current_script_hash = sha256_file(Path(__file__).resolve())
    for region in REGIONS:
        part = regions[region]
        if part["provenance"]["analysis_script_sha256"] != current_script_hash:
            raise ValueError(f"{region}: checkpoint was not produced by the current analysis script")
        if [row["GEOID"] for row in part["per_tract"]] != part["scope"]["official_geoids"]:
            raise ValueError(f"{region}: exact GEOID order/coverage mismatch")
        if not all(part["measure_before_reproduction"].values()):
            raise ValueError(f"{region}: a frozen baseline reproduction check failed")
        for threshold in THRESHOLDS:
            variant = part["variants"][str(int(threshold))]
            safety = variant["cross_identity_safety"]
            if safety["ambiguous_accepted_merges"] or safety["many_to_one_accepted_merges"]:
                raise ValueError(f"{region}/{threshold}: unsafe accepted merge")
    repeat = {"checked": False, "exact_match": None, "region": None, "sha256": None}
    if repeat_path:
        rerun = json.loads(repeat_path.read_text(encoding="utf-8"))
        region = rerun["region"]
        if region not in regions or rerun != regions[region]:
            raise ValueError("determinism rerun differs from original regional checkpoint")
        repeat = {"checked": True, "exact_match": True, "region": region,
                  "sha256": sha256_file(repeat_path)}
    aggregate = {}
    for threshold in THRESHOLDS:
        key = str(int(threshold))
        rho_values = {region: regions[region]["variants"][key]["rank_comparison"]["rho_identity_only_vs_combined"]
                      for region in REGIONS}
        aggregate[key] = {
            "per_region_spearman_rank_correlations": rho_values,
            "accepted_unique_pairs": sum(regions[r]["variants"][key]["cross_identity_safety"]["accepted_unique_pairs"] for r in REGIONS),
            "compatible_cross_identity_endpoint_pairs": sum(regions[r]["variants"][key]["cross_identity_safety"]["compatible_cross_identity_endpoint_pairs"] for r in REGIONS),
            "route_name_evidence_endpoint_pairs": sum(regions[r]["variants"][key]["cross_identity_safety"]["route_name_evidence_endpoint_pairs"] for r in REGIONS),
            "ambiguous_accepted_merges": 0,
            "many_to_one_accepted_merges": 0,
            "tracts_with_nonzero_delta": sum(regions[r]["variants"][key]["tracts_with_nonzero_covariate_delta"] for r in REGIONS),
        }
    report = {
        "schema_version": 1,
        "audit": "T1 cross-identity join × angle-threshold interaction; provided-data diagnostic only",
        "scope": {
            "regions": list(REGIONS),
            "official_tracts": len(geoids),
            "labels_or_targets_read": False,
            "removed_references_or_equivalents_read": False,
            "external_data_read": False,
            "leaderboard_information_used": False,
            "target_fitting_performed": False,
            "candidate_artifacts_changed": False,
            "uploads": 0,
        },
        "measure_before": {
            "frozen_identity_only_T1_covariate_reproduced": True,
            "cross_identity_safety_report": "reports/t1-cross-identity-join-safety.json",
            "cross_identity_safety_report_sha256": frozen_hashes["cross_identity_safety_report_sha256"],
            "angle_only_report": "reports/bias60-t1-angle-continuity-sensitivity.json",
            "angle_only_report_sha256": frozen_hashes["angle_only_report_sha256"],
            "cross_identity_safety_script_sha256": frozen_hashes["cross_identity_safety_script_sha256"],
            "angle_only_script_sha256": frozen_hashes["angle_only_script_sha256"],
            "provided_input_reader_sha256": frozen_hashes["provided_input_reader_sha256"],
            "T1_source_sha256": frozen_hashes["T1_source_sha256"],
            "T1_exact_source_sha256": frozen_hashes["T1_exact_source_sha256"],
            "harness_config_sha256": frozen_hashes["harness_config_sha256"],
            "all_regions_reproduced_frozen_identity_and_angle_only_baselines": all(
                all(regions[r]["measure_before_reproduction"].values()) for r in REGIONS),
            "per_region_measurements": {r: regions[r]["measure_before_reproduction"] for r in REGIONS},
        },
        "measure_after": {
            "combined_angle_threshold_variants": aggregate,
            "exact_official_ID_coverage": len(geoids) == 9379 and len(set(geoids)) == 9379,
            "zero_ambiguous_or_many_to_one_merges_at_all_thresholds": all(
                regions[r]["variants"][key]["cross_identity_safety"]["ambiguous_accepted_merges"] == 0
                and regions[r]["variants"][key]["cross_identity_safety"]["many_to_one_accepted_merges"] == 0
                for r in REGIONS for key in ("15", "30", "45")),
            "deterministic_rerun": repeat,
            "acceptance": {
                "exact_9_379_ID_coverage": len(geoids) == 9379 and len(set(geoids)) == 9379,
                "all_baseline_reports_and_hashes_reproduced": all(
                    all(regions[r]["measure_before_reproduction"].values()) for r in REGIONS),
                "zero_ambiguous_and_many_to_one_merges": all(
                    regions[r]["variants"][key]["cross_identity_safety"]["ambiguous_accepted_merges"] == 0
                    and regions[r]["variants"][key]["cross_identity_safety"]["many_to_one_accepted_merges"] == 0
                    for r in REGIONS for key in ("15", "30", "45")),
                "deterministic_rerun_exact_match": repeat["exact_match"] is True,
            },
            "interpretation": "Correlation and covariate deltas compare diagnostics to the frozen identity-only provided-data covariate; they are not target-accuracy evidence and do not alter candidates.",
        },
        "method": {
            "outcome": "T1 eligible class-drop road length covariate in km",
            "baseline": "frozen exact-identity T1; undefined values retain the registered zero vector value for ranking",
            "variants": "for each strict angle limit, union same-identity endpoint-continuous components with only reciprocal-unique route/name-evidenced cross-identity pairs passing the same angle limit; recompute T1 aggregation",
            "angle_limits_degrees_strictly_less_than": list(THRESHOLDS),
            "rank_correlation": "per-region Spearman rho with average ranks for ties across every official GEOID",
            "cross_identity_safety": "same strict literal route/name evidence and reciprocal one-to-one component safety policy as the frozen unique-join audit, re-evaluated at each angle limit",
            "length_crs": "EPSG:5070",
        },
        "regions": regions,
        "provenance": {
            "analysis_script_sha256": sha256_file(Path(__file__).resolve()),
            "safety_report_sha256": frozen_hashes["cross_identity_safety_report_sha256"],
            "angle_only_report_sha256": frozen_hashes["angle_only_report_sha256"],
            "input_hashes_by_region": {r: {
                "official_geoids_sha256": regions[r]["scope"]["official_geoids_sha256"],
                "provided_road_payload_sha256": regions[r]["measure_before_reproduction"]["provided_road_payload_sha256"],
                "official_mask_wkb_sha256": regions[r]["measure_before_reproduction"]["official_mask_wkb_sha256"],
            } for r in REGIONS},
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if len(payload.encode()) > 50_000_000:
        raise ValueError("combined report exceeds 50 MB")
    output.write_text(payload, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--region", choices=REGIONS)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/t1-cross-identity-angle-interaction.json")
    parser.add_argument("--merge-region-reports", nargs="+", type=Path)
    parser.add_argument("--repeat-checkpoint", type=Path)
    args = parser.parse_args()
    if args.merge_region_reports:
        result = combine(args.merge_region_reports, args.out, args.repeat_checkpoint)
        print(json.dumps({"official_tracts": result["scope"]["official_tracts"],
                          "variants": result["measure_after"]["combined_angle_threshold_variants"],
                          "output": str(args.out)}, sort_keys=True))
        return 0
    if args.workspace is None or args.region is None:
        parser.error("--workspace and --region are required for regional runs")
    safety, angle, _ = verify_frozen_reports()
    result = audit_region(args.workspace, args.region, safety, angle)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"region": args.region,
                      "accepted_pairs_by_angle": {key: value["cross_identity_safety"]["accepted_unique_pairs"]
                                                   for key, value in result["variants"].items()},
                      "spearman_by_angle": {key: value["rank_comparison"]["rho_identity_only_vs_combined"]
                                             for key, value in result["variants"].items()},
                      "output": str(args.out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
