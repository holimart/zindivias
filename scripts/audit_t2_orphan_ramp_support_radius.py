#!/usr/bin/env python3
"""Measure T2 attached-road support at the frozen 2, 5, and 10 km radii.

Discovery-only support diagnostic. It uses the provided unfiltered road layer, official
tract masks, provided RUCA strata, and registered T2 artifacts; it never fits a model.
"""
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
import audit_t2_orphan_ramp_support as baseline_audit  # noqa: E402
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
from bias60.ideas.t import _road_layer  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
RADII_M = (2000.0, 5000.0, 10000.0)
RADIUS_NAMES = {2000.0: "2km", 5000.0: "5km", 10000.0: "10km"}
CONFIG_PATH = ROOT / "config/bias60-harness-preregistered.json"
T2_SOURCE = ROOT / "src/bias60/ideas/t.py"
T2_EXACT_SOURCE = ROOT / "src/bias60/ideas/t_exact.py"
INPUTS_SOURCE = ROOT / "src/bias60/inputs.py"
PRIOR_REPORT = ROOT / "reports/t2-orphan-ramp-support.json"
ROAD_COLUMNS = ["class", "subclass", "connectors", "geometry"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode()).hexdigest()


def sha_ids(values: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


def provenance_digest(values: Sequence[Sequence[int]]) -> str:
    """Hash sorted distinct source-row pairs in a stable, documented representation."""
    pairs = sorted({(int(pair[0]), int(pair[1])) for pair in values})
    payload = "\n".join(f"{left}:{right}" for left, right in pairs)
    return hashlib.sha256(payload.encode()).hexdigest()


def distance_summary(values: Sequence[float]) -> dict[str, Any]:
    arr = np.asarray(values, dtype=np.float64)
    if not arr.size:
        return {"n_unique_in_tract_contributions": 0, "min_m": None, "median_m": None,
                "q90_m": None, "q95_m": None, "max_m": None}
    q = np.quantile(arr, [0, .5, .9, .95, 1])
    return {"n_unique_in_tract_contributions": int(arr.size), "min_m": float(q[0]),
            "median_m": float(q[1]), "q90_m": float(q[2]),
            "q95_m": float(q[3]), "max_m": float(q[4])}


def _metadata_and_ruCA(inputs: ProvidedInputs, workspace: Path, region: str,
                       parquet: Any, geoids: list[str], config: dict[str, Any]) -> dict[str, Any]:
    import pyarrow.parquet as pq

    ruca = inputs.strata(["ruca_primary", "ruca_covered", "ruca_primary_desc"])
    strata = [baseline_audit.classify_ruca(code, covered) for code, covered in
              zip(ruca["ruca_primary"], ruca["ruca_covered"])]
    road_schema_hash = hashlib.sha256("\n".join(
        f"{column}:{parquet.schema_arrow.field(column).type}" for column in ROAD_COLUMNS).encode()).hexdigest()
    road_object = config["inputs"]["package_layer_path_template"].format(
        bucket=config["inputs"]["package_bucket"], region=region,
        layer=config["seeds"]["T2"]["unfiltered_layer"])
    road_info = inputs.filesystem.get_file_info(road_object)
    strata_file = workspace / config["inputs"]["strata_cache_template"].format(region=region)
    strata_schema = pq.ParquetFile(strata_file).schema_arrow
    ruca_digest = sha256_json([
        [geoid, strata[index], bool(ruca["ruca_covered"][index]),
         str(ruca["ruca_primary_desc"][index]) if ruca["ruca_primary_desc"][index] is not None else None]
        for index, geoid in enumerate(geoids)
    ])
    ruca_schema_hash = hashlib.sha256("\n".join(
        f"{column}:{strata_schema.field(column).type}"
        for column in ("GEOID", "ruca_primary", "ruca_covered", "ruca_primary_desc")
    ).encode()).hexdigest()
    descriptions: dict[str, set[str]] = defaultdict(set)
    for key, description in zip(strata, ruca["ruca_primary_desc"]):
        if description is not None and str(description).strip():
            descriptions[key].add(str(description).strip())
    return {
        "ruca": ruca,
        "strata": strata,
        "descriptions": descriptions,
        "road_object": road_object,
        "road_metadata": {
            "rows": int(parquet.metadata.num_rows),
            "row_groups": int(parquet.metadata.num_row_groups),
            "object_bytes": int(road_info.size) if road_info.size is not None and road_info.size >= 0 else None,
            "columns_read": ROAD_COLUMNS,
            "selected_schema_sha256": road_schema_hash,
        },
        "strata_file": strata_file,
        "ruca_fields_sha256": ruca_digest,
        "ruca_schema_sha256": ruca_schema_hash,
    }


def _check_before_against_registered_and_prior(
    region: str, workspace: Path, geoids: list[str], meta: dict[str, Any],
    attached_2km_m: np.ndarray,
    t2_defined: np.ndarray, t2_v1_values: np.ndarray,
    t2_length_defined: np.ndarray, t2_length_values_km: np.ndarray,
    t2_segments_defined: np.ndarray,
    prior: dict[str, Any],
) -> dict[str, Any]:
    before = prior["regions"][region]["region_result"]
    prior_scope = prior["regions"][region]["scope"]
    prior_inputs = prior["regions"][region]["inputs"]
    prior_metadata = prior_inputs["provided_road_metadata"]
    if sha_ids(geoids) != prior_scope["official_geoid_order_sha256"]:
        raise ValueError(f"{region}: official GEOID order hash differs from support audit")
    for key, actual in (("rows", meta["road_metadata"]["rows"]),
                        ("row_groups", meta["road_metadata"]["row_groups"]),
                        ("object_bytes", meta["road_metadata"]["object_bytes"]),
                        ("selected_schema_sha256", meta["road_metadata"]["selected_schema_sha256"])):
        if prior_metadata.get(key) != actual:
            raise ValueError(f"{region}: prior support-audit road metadata {key} changed")
    if prior_inputs["provided_RUCA_selected_fields_sha256"] != meta["ruca_fields_sha256"]:
        raise ValueError(f"{region}: selected provided RUCA fields hash differs from prior support audit")
    if prior_inputs["provided_RUCA_selected_schema_sha256"] != meta["ruca_schema_sha256"]:
        raise ValueError(f"{region}: RUCA selected schema hash differs from prior support audit")

    t2_sha = sha256_file(workspace / "ideas/T2.parquet")
    t2v2_sha = sha256_file(workspace / "ideas/T2-v2.parquet")
    if before["T2_v1_registered_artifact"]["artifact_sha256"] != t2_sha:
        raise ValueError(f"{region}: T2 artifact SHA differs from prior support audit")
    if before["T2_v2_registered_artifact_reproduction"]["artifact_sha256"] != t2v2_sha:
        raise ValueError(f"{region}: T2-v2 artifact SHA differs from prior support audit")
    radius2_support = baseline_audit.positive_attached_support(t2_length_defined, attached_2km_m / 1000.0)
    count = int(radius2_support.sum())
    if count != int(before["eligible_attached_support_count"]):
        raise ValueError(f"{region}: 2 km support count {count} differs from prior support audit")
    if not np.array_equal(t2_length_defined, np.asarray(t2_segments_defined, dtype=bool)):
        raise ValueError(f"{region}: 2 km registered attached-length/orphan masks differ")
    if not np.array_equal(t2_defined, np.ones(len(geoids), dtype=bool)):
        raise ValueError(f"{region}: baseline T2-v1 native definedness no longer matches support audit")
    # The 2 km alternate implementation must reproduce the registered T2-v2 length vector exactly.
    difference = float(np.max(np.abs(attached_2km_m / 1000.0 - t2_length_values_km), initial=0.0))
    if difference > 1e-12 or not np.array_equal(t2_length_defined, np.ones(len(geoids), dtype=bool)):
        raise ValueError(f"{region}: 2 km radius recomputation differs from T2-v2; max={difference}")
    strata_table = baseline_audit.support_table(
        meta["strata"], t2_defined, radius2_support, np.asarray(t2_v1_values) > 0.0)
    for key, cells in strata_table.items():
        cells["ruca_primary_desc_values"] = sorted(meta["descriptions"].get(key, set()))
    if strata_table != before["RUCA_support"]:
        raise ValueError(f"{region}: reproduced 2 km RUCA partition differs from prior support audit")
    if (count != int(before["eligible_attached_support_count"])
            or abs(count / len(geoids) - float(before["eligible_attached_support_fraction"])) > 1e-15):
        raise ValueError(f"{region}: 2 km support rate differs from prior audit")
    return {
        "matches_prior_2km_support_count_and_fraction": True,
        "matches_prior_2km_RUCA_stratum_partition": True,
        "matches_registered_T2_v2_attached_length_values_and_mask": True,
        "maximum_absolute_T2_v2_length_difference_km": difference,
        "matches_prior_road_schema_and_object_metadata": True,
        "matches_prior_RUCA_selected_field_and_schema_hashes": True,
        "matches_prior_official_GEOID_order_hash": True,
        "T2_artifact_sha256": t2_sha,
        "T2_v2_artifact_sha256": t2v2_sha,
        "official_GEOID_order_sha256": sha_ids(geoids),
        "RUCA_selected_fields_sha256": meta["ruca_fields_sha256"],
        "RUCA_selected_schema_sha256": meta["ruca_schema_sha256"],
        "road_selected_schema_sha256": meta["road_metadata"]["selected_schema_sha256"],
        "road_object_bytes": meta["road_metadata"]["object_bytes"],
    }


def audit_region(workspace: Path, region: str, prior: dict[str, Any]) -> dict[str, Any]:
    import pyarrow.parquet as pq
    import shapely
    from pyproj import Transformer

    config = load_config(CONFIG_PATH)
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    total = int(config["inputs"]["official_scored_rows"])
    if len(official) != total or len({g for g, _ in official}) != total:
        raise ValueError(f"official scored scope must contain {total} unique GEOIDs")
    if {r for _, r in official} != set(REGIONS):
        raise ValueError("official sample region partition differs from the frozen scope")
    geoids = [g for g, r in official if r == region]
    if len(geoids) != int(config["inputs"]["official_rows_by_region"][region]):
        raise ValueError(f"{region}: unexpected official GEOID count")

    inputs = ProvidedInputs(config, workspace, region, geoids)
    seed = config["seeds"]["T2"]
    named = set(seed["named_classes"])
    parquet = _road_layer(inputs, ROAD_COLUMNS, seed["unfiltered_layer"])
    meta = _metadata_and_ruCA(inputs, workspace, region, parquet, geoids, config)
    incidence: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    links: list[tuple[int, Any, str, str, bytes]] = []
    rows_read = 0
    row_offset = 0

    # Reproduce exact T2-v2 connector/orphan eligibility and retain stable source row numbers.
    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=ROAD_COLUMNS)
        classes, subclasses = table["class"].to_pylist(), table["subclass"].to_pylist()
        conns, wkbs = table["connectors"].to_pylist(), table["geometry"].to_pylist()
        rows_read += len(classes)
        for cls, sub, row_conns, wkb in zip(classes, subclasses, conns, wkbs):
            ordered = sorted((item for item in (row_conns or [])
                              if isinstance(item, dict) and item.get("connector_id")),
                             key=lambda item: float(item.get("at", 0.0)))
            ids = list(dict.fromkeys(str(item["connector_id"]) for item in ordered))
            for connector in ids:
                incidence[connector][int(cls in named)] += 1
            is_link = ((isinstance(sub, str) and "link" in sub.casefold()) or
                       (isinstance(cls, str) and "_link" in cls.casefold()))
            if is_link and len(ids) >= 2 and wkb is not None:
                links.append((row_offset, cls, ids[0], ids[-1], wkb))
            row_offset += 1

    if rows_read != int(parquet.metadata.num_rows) or row_offset != rows_read:
        raise ValueError(f"{region}: source row count differs from Parquet footer")
    orphan_links: list[tuple[int, str, str, bytes]] = []
    target_connectors: set[str] = set()
    for source_row, road_class, first, last, wkb in links:
        endpoints_ok = True
        for connector in (first, last):
            named_count, all_count = incidence[connector][1], sum(incidence[connector])
            named_neighbors = named_count - int(road_class in named)
            other_neighbors = all_count - 1
            if named_neighbors > 0 or other_neighbors <= 0:
                endpoints_ok = False
                break
        if endpoints_ok:
            orphan_links.append((source_row, first, last, wkb))
            target_connectors.update((first, last))

    attached: dict[str, dict[int, bytes]] = defaultdict(dict)
    row_offset = 0
    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=ROAD_COLUMNS)
        classes, subclasses = table["class"].to_pylist(), table["subclass"].to_pylist()
        conns, wkbs = table["connectors"].to_pylist(), table["geometry"].to_pylist()
        for cls, sub, row_conns, wkb in zip(classes, subclasses, conns, wkbs):
            source_row = row_offset
            is_link = ((isinstance(sub, str) and "link" in sub.casefold()) or
                       (isinstance(cls, str) and "_link" in cls.casefold()))
            if isinstance(cls, str) and cls not in named and not is_link and wkb is not None:
                for item in row_conns or []:
                    connector = str(item.get("connector_id")) if isinstance(item, dict) else ""
                    if connector in target_connectors:
                        attached[connector][source_row] = wkb
            row_offset += 1

    source_tracts = inputs.tract_geometries()
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    tracts = shapely.transform(source_tracts, transformer.transform, interleaved=False)
    tree = shapely.STRtree(tracts)
    n = len(geoids)
    lengths_m = {radius: np.zeros(n, dtype=np.float64) for radius in RADII_M}
    orphan_counts = np.zeros(n, dtype=np.float64)
    tract_roads = {radius: [set() for _ in range(n)] for radius in RADII_M}
    tract_links = {radius: [set() for _ in range(n)] for radius in RADII_M}
    tract_pairs = {radius: [set() for _ in range(n)] for radius in RADII_M}
    tract_min_distance = {radius: np.full(n, np.inf, dtype=np.float64) for radius in RADII_M}
    piece_distances: dict[float, dict[tuple[int, int, int], float]] = {radius: {} for radius in RADII_M}

    for link_row, first, last, link_wkb in orphan_links:
        link_geom = shapely.transform(shapely.from_wkb(link_wkb), transformer.transform,
                                      interleaved=False)
        link_tracts = tree.query(link_geom, predicate="intersects")
        if link_tracts.size:
            orphan_counts[link_tracts] += 1.0
        nearby = dict(attached[first])
        nearby.update(attached[last])
        if not nearby:
            continue
        row_ids = list(nearby)
        road_geoms = shapely.transform(shapely.from_wkb([nearby[row_id] for row_id in row_ids]),
                                       transformer.transform, interleaved=False)
        for radius in RADII_M:
            clipped = shapely.intersection(road_geoms, shapely.buffer(link_geom, radius))
            pairs = tree.query(clipped, predicate="intersects")
            if not pairs.size:
                continue
            intersections = shapely.intersection(clipped[pairs[0]], tracts[pairs[1]])
            piece_lengths = np.asarray(shapely.length(intersections), dtype=np.float64)
            piece_dists = np.asarray(shapely.distance(intersections, link_geom), dtype=np.float64)
            lengths_m[radius] += np.bincount(pairs[1], weights=piece_lengths, minlength=n)
            for local_road, tract_id, metres, piece_dist in zip(
                    pairs[0].tolist(), pairs[1].tolist(), piece_lengths.tolist(), piece_dists.tolist()):
                if not math.isfinite(metres) or metres <= 0.0:
                    continue
                road_row = int(row_ids[local_road])
                tract_roads[radius][tract_id].add(road_row)
                tract_links[radius][tract_id].add(link_row)
                tract_pairs[radius][tract_id].add((link_row, road_row))
                tract_min_distance[radius][tract_id] = min(
                    tract_min_distance[radius][tract_id], float(piece_dist))
                key = (link_row, road_row, int(tract_id))
                if key not in piece_distances[radius] or piece_dist < piece_distances[radius][key]:
                    piece_distances[radius][key] = float(piece_dist)

    # Read frozen v1/v2 artifacts and RUCA masks only; no labels or score columns are used.
    artifacts = inputs.idea_emissions(["T2", "T2-v2"])
    t2_value, t2_defined = artifacts["T2"][("R", "covariate:orphan_link_length_km")]
    t2_segments, t2_segment_defined = artifacts["T2"][("R", "covariate:orphan_link_segments")]
    registered_length, registered_length_defined = artifacts["T2-v2"][("R", "covariate:attached_carriageway_length_km")]
    registered_v2_segments, registered_v2_segment_defined = artifacts["T2-v2"][("R", "covariate:orphan_link_segments")]
    if not np.array_equal(t2_defined, t2_segment_defined):
        raise ValueError(f"{region}: registered T2-v1 feature masks differ")
    if not np.array_equal(registered_length_defined, registered_v2_segment_defined):
        raise ValueError(f"{region}: registered T2-v2 feature masks differ")
    if geoids != inputs.geoids or len(t2_value) != n:
        raise ValueError(f"{region}: registered artifact GEOID order/length mismatch")
    support_by_radius: dict[float, np.ndarray] = {}
    ruca_tables: dict[str, Any] = {}
    for radius in RADII_M:
        defined_mask = np.asarray(registered_length_defined, dtype=bool)
        support = baseline_audit.positive_attached_support(defined_mask, lengths_m[radius] / 1000.0)
        support_by_radius[radius] = support
        prior_radius = [value for value in RADII_M if value < radius]
        if prior_radius:
            previous = prior_radius[-1]
            if np.any(lengths_m[radius] + 1e-8 < lengths_m[previous]):
                raise ValueError(f"{region}: attached length decreased at larger buffer radius")
            if np.any(support_by_radius[previous] & ~support):
                raise ValueError(f"{region}: support mask is not monotone with buffer radius")
        table = baseline_audit.support_table(meta["strata"], t2_defined, support,
                                             np.asarray(t2_value, dtype=np.float64) > 0.0)
        for key, cell in table.items():
            cell["ruca_primary_desc_values"] = sorted(meta["descriptions"].get(key, set()))
        ruca_tables[RADIUS_NAMES[radius]] = table
    before_checks = _check_before_against_registered_and_prior(
        region, workspace, geoids, meta, lengths_m[2000.0],
        np.asarray(t2_defined, dtype=bool), np.asarray(t2_value, dtype=np.float64),
        np.asarray(registered_length_defined, dtype=bool), np.asarray(registered_length, dtype=np.float64),
        np.asarray(registered_v2_segment_defined, dtype=bool),
        prior,
    )
    # The registered artifact contains the 2 km value; check every tract before reporting alternatives.
    max_registered_diff = float(np.max(np.abs(lengths_m[2000.0] / 1000.0 - registered_length), initial=0.0))
    if max_registered_diff > 1e-12:
        raise ValueError(f"{region}: 2 km exact length output differs from registered T2-v2; max={max_registered_diff}")
    max_segment_diff = float(np.max(np.abs(orphan_counts - registered_v2_segments), initial=0.0))
    if max_segment_diff > 0.0 or not np.array_equal(registered_v2_segment_defined, np.ones(n, dtype=bool)):
        raise ValueError(f"{region}: recomputed orphan-link segment counts differ from T2-v2; max={max_segment_diff}")

    per_tract = []
    for i, geoid in enumerate(geoids):
        by_radius: dict[str, Any] = {}
        for radius in RADII_M:
            name = RADIUS_NAMES[radius]
            road_ids = sorted(tract_roads[radius][i])
            link_ids = sorted(tract_links[radius][i])
            pairs = sorted(tract_pairs[radius][i])
            min_dist = float(tract_min_distance[radius][i])
            by_radius[name] = {
                "attached_length_km": float(lengths_m[radius][i] / 1000.0),
                "support_positive": bool(support_by_radius[radius][i]),
                "eligible_attached_road_count": len(road_ids),
                "eligible_attached_road_source_row_ids": road_ids,
                "qualifying_orphan_link_count": len(link_ids),
                "qualifying_orphan_link_source_row_ids": link_ids,
                "eligible_orphan_link_road_pair_count": len(pairs),
                "eligible_orphan_link_road_pairs_sha256": provenance_digest(pairs),
                "nearest_contributing_road_distance_to_orphan_link_m": min_dist if math.isfinite(min_dist) else None,
            }
        raw_code = meta["ruca"]["ruca_primary"][i]
        if raw_code is None or not math.isfinite(float(raw_code)):
            numeric_code = None
        else:
            numeric_code = int(raw_code) if float(raw_code).is_integer() else float(raw_code)
        per_tract.append({
            "GEOID": geoid,
            "ruca_primary": numeric_code,
            "ruca_covered": bool(meta["ruca"]["ruca_covered"][i]),
            "ruca_stratum": meta["strata"][i],
            "T2_v1_defined": bool(t2_defined[i]),
            "T2_v1_orphan_link_length_km": float(t2_value[i]),
            "T2_v2_attached_length_defined": bool(registered_length_defined[i]),
            "radii": by_radius,
        })

    region_summaries: dict[str, Any] = {}
    for radius in RADII_M:
        name = RADIUS_NAMES[radius]
        support = support_by_radius[radius]
        stratum_delta = {}
        for key, cell in ruca_tables[name].items():
            baseline_cell = ruca_tables["2km"].get(key, {"official_tracts": 0, "attached_support_positive": 0, "attached_support_fraction": 0.0})
            stratum_delta[key] = {
                "coverage_delta_count_vs_2km": cell["attached_support_positive"] - baseline_cell["attached_support_positive"],
                "coverage_delta_fraction_vs_2km": cell["attached_support_fraction"] - baseline_cell["attached_support_fraction"],
            }
        region_summaries[name] = {
            "radius_m": radius,
            "official_tracts": n,
            "support_count": int(support.sum()),
            "support_fraction": float(support.mean()),
            "coverage_delta_count_vs_2km": int(support.sum() - support_by_radius[2000.0].sum()) if radius != 2000 else 0,
            "coverage_delta_fraction_vs_2km": float(support.mean() - support_by_radius[2000.0].mean()) if radius != 2000 else 0.0,
            "attached_length_total_km": float(lengths_m[radius].sum() / 1000.0),
            "RUCA_support": ruca_tables[name],
            "RUCA_coverage_delta_vs_2km": stratum_delta,
            "distance_to_orphan_link_for_positive_in_tract_contributions": distance_summary(list(piece_distances[radius].values())),
        }
    return {
        "region": region,
        "scope": {
            "official_geoids": geoids,
            "official_tracts": n,
            "official_geoid_order_sha256": sha_ids(geoids),
            "labels_or_targets_read": False,
            "sample_score_values_read": False,
            "removed_references_or_equivalents_read": False,
            "external_data_or_roads_read": False,
            "leaderboard_information_used": False,
            "fitting_performed": False,
            "candidate_artifacts_changed": False,
            "uploads": 0,
        },
        "inputs": {
            "provided_road_object": meta["road_object"],
            "provided_road_metadata": meta["road_metadata"],
            "source_row_id_convention": "zero-based ordinal over concatenated Parquet row groups; sorted IDs recorded per tract for source rows and orphan links that contribute positive in-tract length",
            "provided_official_mask_layer": "census-acs-housing:GEOID,geometry",
            "official_mask_wkb_sha256": hashlib.sha256(b"\n".join(shapely.to_wkb(g) for g in source_tracts)).hexdigest(),
            "provided_RUCA_cache": str(meta["strata_file"]),
            "provided_RUCA_cache_columns_read": ["GEOID", "ruca_primary", "ruca_covered", "ruca_primary_desc"],
            "provided_RUCA_selected_fields_sha256": meta["ruca_fields_sha256"],
            "provided_RUCA_selected_schema_sha256": meta["ruca_schema_sha256"],
            "registered_T2_artifact_sha256": sha256_file(workspace / "ideas/T2.parquet"),
            "registered_T2_v2_artifact_sha256": sha256_file(workspace / "ideas/T2-v2.parquet"),
        },
        "measure_before_2km_reproduction": {
            **before_checks,
            "prior_support_audit_region_support_count": int(prior["regions"][region]["region_result"]["eligible_attached_support_count"]),
            "prior_support_audit_region_support_fraction": float(prior["regions"][region]["region_result"]["eligible_attached_support_fraction"]),
            "T2_v1_defined_count": int(np.sum(t2_defined)),
            "T2_v2_attached_length_defined_count": int(np.sum(registered_length_defined)),
            "maximum_absolute_attached_length_difference_km": max_registered_diff,
            "support_count": int(support_by_radius[2000.0].sum()),
            "support_fraction": float(support_by_radius[2000.0].mean()),
            "RUCA_support": ruca_tables["2km"],
        },
        "radius_results": region_summaries,
        "eligible_road_distance_provenance": {
            name: {
                "distance_definition": "EPSG:5070 geometry distance from the positive-length in-tract intersection of the radius-clipped attached road to its qualifying orphan-link source geometry; rows are grouped by unique link/road/tract contribution",
                "unique_link_road_tract_contributions": distance_summary(list(piece_distances[radius].values())),
                "contributing_road_source_rows": int(len({row for rows in tract_roads[radius] for row in rows})),
                "contributing_orphan_link_source_rows": int(len({row for rows in tract_links[radius] for row in rows})),
                "contributing_link_road_pair_provenance_sha256": provenance_digest(
                    [pair for tract_set in tract_pairs[radius] for pair in tract_set]),
            }
            for radius, name in ((r, RADIUS_NAMES[r]) for r in RADII_M)
        },
        "per_tract": per_tract,
        "provenance": {
            "radius_audit_script_sha256": sha256_file(Path(__file__).resolve()),
            "baseline_support_report_sha256": sha256_file(PRIOR_REPORT),
            "T2_source_sha256": sha256_file(T2_SOURCE),
            "T2_exact_source_sha256": sha256_file(T2_EXACT_SOURCE),
            "provided_input_reader_sha256": sha256_file(INPUTS_SOURCE),
            "harness_config_sha256": sha256_file(CONFIG_PATH),
        },
    }


def _aggregate_strata(regions: dict[str, dict[str, Any]], radius_name: str) -> dict[str, Any]:
    cells: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "official_tracts": 0, "T2_defined": 0, "attached_support_positive": 0,
        "T2_orphan_link_length_positive": 0, "ruca_primary_desc_values": set(),
    })
    for report in regions.values():
        for key, cell in report["radius_results"][radius_name]["RUCA_support"].items():
            out = cells[key]
            for count_key in ("official_tracts", "T2_defined", "attached_support_positive", "T2_orphan_link_length_positive"):
                out[count_key] += int(cell[count_key])
            out["ruca_primary_desc_values"].update(cell["ruca_primary_desc_values"])
    result = {}
    for key in sorted(cells, key=lambda k: (k == "missing_or_uncovered", int(k) if k.isdigit() else 1000, k)):
        cell = cells[key]
        n = cell["official_tracts"]
        result[key] = {
            **{k: cell[k] for k in ("official_tracts", "T2_defined", "attached_support_positive", "T2_orphan_link_length_positive")},
            "T2_defined_fraction": cell["T2_defined"] / n if n else 0.0,
            "attached_support_fraction": cell["attached_support_positive"] / n if n else 0.0,
            "orphan_link_length_positive_fraction": cell["T2_orphan_link_length_positive"] / n if n else 0.0,
            "ruca_primary_desc_values": sorted(cell["ruca_primary_desc_values"]),
        }
    return result


def combine(paths: Sequence[Path], out: Path, repeat_path: Path | None = None) -> dict[str, Any]:
    prior = json.loads(PRIOR_REPORT.read_text(encoding="utf-8"))
    by_region: dict[str, tuple[dict[str, Any], Path]] = {}
    for path in paths:
        part = json.loads(path.read_text(encoding="utf-8"))
        region = part["region"]
        if region in by_region:
            raise ValueError(f"duplicate region checkpoint {region}")
        by_region[region] = (part, path)
    if set(by_region) != set(REGIONS):
        raise ValueError(f"expected one report for each official region; found {sorted(by_region)}")
    regions = {r: by_region[r][0] for r in REGIONS}
    all_ids = [g for r in REGIONS for g in regions[r]["scope"]["official_geoids"]]
    if len(all_ids) != 9379 or len(set(all_ids)) != 9379:
        raise ValueError("regional checkpoints do not form exact unique official GEOID partition")
    for r in REGIONS:
        report = regions[r]
        ids = report["scope"]["official_geoids"]
        if len(report["per_tract"]) != len(ids) or [x["GEOID"] for x in report["per_tract"]] != ids:
            raise ValueError(f"{r}: per-tract rows do not exactly cover official GEOIDs")
        if not all(report["measure_before_2km_reproduction"][k] for k in (
                "matches_prior_2km_support_count_and_fraction",
                "matches_prior_2km_RUCA_stratum_partition",
                "matches_registered_T2_v2_attached_length_values_and_mask",
                "matches_prior_road_schema_and_object_metadata",
                "matches_prior_RUCA_selected_field_and_schema_hashes",
                "matches_prior_official_GEOID_order_hash")):
            raise ValueError(f"{r}: 2 km baseline reproduction failed")

    repeat = {"checked": False, "exact_match": None, "region": None, "sha256": None}
    if repeat_path is not None:
        repeated = json.loads(repeat_path.read_text(encoding="utf-8"))
        region = repeated["region"]
        if region not in regions or repeated != regions[region]:
            raise ValueError("determinism rerun JSON is not identical to its original region report")
        repeat = {"checked": True, "exact_match": True, "region": region,
                  "sha256": sha256_file(repeat_path)}

    official_n = len(all_ids)
    radius_summary: dict[str, Any] = {}
    all_pass = []
    for name, radius in (("2km", 2000), ("5km", 5000), ("10km", 10000)):
        count = sum(r["radius_results"][name]["support_count"] for r in regions.values())
        fraction = count / official_n
        aggregate_strata = _aggregate_strata(regions, name)
        baseline_strata = _aggregate_strata(regions, "2km")
        aggregate_strata_delta = {}
        for key, cell in aggregate_strata.items():
            old = baseline_strata.get(key, {"attached_support_positive": 0, "attached_support_fraction": 0.0})
            aggregate_strata_delta[key] = {
                "coverage_delta_count_vs_2km": cell["attached_support_positive"] - old["attached_support_positive"],
                "coverage_delta_fraction_vs_2km": cell["attached_support_fraction"] - old["attached_support_fraction"],
            }
        regional = {r: regions[r]["radius_results"][name]["support_fraction"] for r in REGIONS}
        gate = baseline_audit.support_backed_gate(count, official_n, regional)
        if gate["passes"]:
            all_pass.append(name)
        region_table = {}
        for r in REGIONS:
            current = regions[r]["radius_results"][name]
            before = regions[r]["radius_results"]["2km"]
            region_table[r] = {
                **current,
                "coverage_delta_count_vs_2km": current["support_count"] - before["support_count"],
                "coverage_delta_fraction_vs_2km": current["support_fraction"] - before["support_fraction"],
            }
        radius_summary[name] = {
            "radius_m": radius,
            "official_tracts": official_n,
            "support_count": count,
            "support_fraction": fraction,
            "coverage_delta_count_vs_2km": count - sum(r["radius_results"]["2km"]["support_count"] for r in regions.values()),
            "coverage_delta_fraction_vs_2km": fraction - sum(r["radius_results"]["2km"]["support_count"] for r in regions.values()) / official_n,
            "regional": region_table,
            "RUCA_support": aggregate_strata,
            "RUCA_coverage_delta_vs_2km": aggregate_strata_delta,
            "support_backed_gate": gate,
        }
    # Retest exact before counts and RUCA partitions against the prior 2 km report as a whole.
    prior_count = int(prior["overall"]["attached_support_count"])
    if radius_summary["2km"]["support_count"] != prior_count:
        raise ValueError("combined 2 km support count differs from checked-in support audit")
    for r in REGIONS:
        prior_result = prior["regions"][r]["region_result"]
        if regions[r]["radius_results"]["2km"]["RUCA_support"] != prior_result["RUCA_support"]:
            raise ValueError(f"{r}: combined 2 km RUCA support table differs from checked-in report")
    acceptance = {
        "exact_9379_official_GEOIDs_covered_once": len(all_ids) == 9379 and len(set(all_ids)) == 9379,
        "2km_counts_hashes_and_region_RUCA_partitions_reproduced": True,
        "deterministic_rerun_exact_match": repeat["exact_match"] is True,
        "radii_meeting_at_least_80pct_overall_and_50pct_each_region": all_pass,
        "a_preregistered_expanded_radius_meets_gate": bool(set(all_pass) & {"5km", "10km"}),
    }
    report = {
        "schema_version": 1,
        "audit": "T2 orphan-ramp attached non-link support spatial radius sensitivity; provided-data diagnostic only",
        "scope": {
            "official_regions": list(REGIONS),
            "official_tracts": official_n,
            "labels_or_targets_read": False,
            "removed_references_or_equivalents_read": False,
            "external_data_or_roads_read": False,
            "leaderboard_information_used": False,
            "fitting_performed": False,
            "candidate_artifacts_changed": False,
            "uploads": 0,
        },
        "measure_before": {
            "registered_2km_support_audit_report": "reports/t2-orphan-ramp-support.json",
            "registered_2km_support_audit_report_sha256": sha256_file(PRIOR_REPORT),
            "support_count": radius_summary["2km"]["support_count"],
            "support_fraction": radius_summary["2km"]["support_fraction"],
            "T2_v1_defined_count": sum(r["measure_before_2km_reproduction"]["T2_v1_defined_count"] for r in regions.values()),
            "regional": {r: regions[r]["measure_before_2km_reproduction"] for r in REGIONS},
            "matches_prior_audit": True,
        },
        "measure_after": {
            "radius_definition": "same exact orphan-link and endpoint-attached non-named non-link road selection at all radii; buffer in EPSG:5070 around each qualifying orphan link; clip intersecting attached-road geometry to official tract masks; support positive iff resulting attached length is >0 m",
            "radii": radius_summary,
            "distance_and_road_provenance": {r: regions[r]["eligible_road_distance_provenance"] for r in REGIONS},
            "determinism_rerun": repeat,
            "acceptance": acceptance,
            "decision": "support gate met at " + ", ".join(all_pass) if all_pass else "no tested radius meets the >=80% overall and >=50% per-region support gate; no model fit",
        },
        "method": {
            "registered_support_gate": "at least 80% overall and at least 50% in every region",
            "radii_m": list(RADII_M),
            "named_classes": sorted(load_config(CONFIG_PATH)["seeds"]["T2"]["named_classes"]),
            "orphan_link_rule": "link class/subclass with two distinct endpoint connectors, each joined to other roads, and no named-class neighbor at either endpoint",
            "eligible_attached_road_rule": "non-named, non-link provided road sharing one or both endpoint connectors of a qualifying orphan link; geometry contributes only when positive in-tract length remains after the radius buffer intersection",
            "source_row_id": "zero-based ordinal in concatenated Parquet row-group order; exact road object path, selected columns/schema, and derived source-row/provenance hashes are recorded per region",
            "RUCA": "provided ruca_primary and ruca_covered; original codes preserved, including 99; missing/uncovered category remains explicit",
            "interpretation": "support coverage sensitivity only; not a prediction or target-accuracy claim",
        },
        "regions": regions,
        "provenance": {
            "radius_audit_script_sha256": sha256_file(Path(__file__).resolve()),
            "baseline_support_report_sha256": sha256_file(PRIOR_REPORT),
            "baseline_region_checkpoint_hashes": prior["provenance"]["region_reports"],
            "region_checkpoint_sha256": {r: sha256_file(by_region[r][1]) for r in REGIONS},
            "T2_source_sha256": sha256_file(T2_SOURCE),
            "T2_exact_source_sha256": sha256_file(T2_EXACT_SOURCE),
            "provided_input_reader_sha256": sha256_file(INPUTS_SOURCE),
            "harness_config_sha256": sha256_file(CONFIG_PATH),
        },
        "reproduction": {
            "per_region": "uv run --with-requirements requirements.txt python scripts/audit_t2_orphan_ramp_support_radius.py --workspace $COMPETITION_WORKSPACE --region <REGION> --out $COMPETITION_WORKSPACE/t2-orphan-ramp-radius-checkpoints/<REGION>.json",
            "combine": "uv run --with-requirements requirements.txt python scripts/audit_t2_orphan_ramp_support_radius.py --merge-region-reports <FOUR_REGION_JSON_PATHS> --repeat-checkpoint <identical-rerun.json> --out reports/t2-orphan-ramp-support-radius.json",
            "run_policy": "one region per invocation; repeat one region and compare byte-for-byte; combine only complete regional checkpoints",
        },
    }
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if len(payload.encode()) > 50_000_000:
        raise ValueError("report exceeds 50 MB bound")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    workspace_env = os.environ.get("COMPETITION_WORKSPACE")
    parser.add_argument("--workspace", type=Path, default=Path(workspace_env) if workspace_env else None)
    parser.add_argument("--region", choices=REGIONS)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/t2-orphan-ramp-support-radius.json")
    parser.add_argument("--merge-region-reports", nargs="+", type=Path)
    parser.add_argument("--repeat-checkpoint", type=Path)
    args = parser.parse_args()
    if args.merge_region_reports:
        report = combine(args.merge_region_reports, args.out, args.repeat_checkpoint)
        print(json.dumps({"official_tracts": report["scope"]["official_tracts"],
                          "support_by_radius": {r: v["support_count"] for r, v in report["measure_after"]["radii"].items()},
                          "passing_radii": report["measure_after"]["acceptance"]["radii_meeting_at_least_80pct_overall_and_50pct_each_region"],
                          "output": str(args.out)}, sort_keys=True))
        return 0
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    if args.region is None:
        parser.error("--region is required; run separately, then combine")
    prior = json.loads(PRIOR_REPORT.read_text(encoding="utf-8"))
    part = audit_region(args.workspace, args.region, prior)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(part, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"region": args.region,
                      "support": {r: part["radius_results"][r]["support_count"] for r in ("2km", "5km", "10km")},
                      "coverage": {r: part["radius_results"][r]["support_fraction"] for r in ("2km", "5km", "10km")},
                      "output": str(args.out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
