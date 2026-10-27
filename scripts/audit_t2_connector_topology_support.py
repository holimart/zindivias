#!/usr/bin/env python3
"""Audit T2 orphan-ramp carriageway support via strict Overture connector topology.

This is a provided-data support screen. It does not fit a model or read target/reference data.
Run one region per invocation, then merge four region checkpoints and a byte-identical rerun.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import audit_t2_orphan_ramp_support as baseline_audit  # noqa: E402
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
from bias60.ideas.t import _road_layer  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
NAMED = {"motorway", "trunk", "primary", "secondary"}
ROAD_COLUMNS = ["class", "subclass", "connectors", "geometry"]
CONFIG_PATH = ROOT / "config/bias60-harness-preregistered.json"
PREREG_PATH = ROOT / "config/t2-connector-topology-support-preregistered.json"
BASELINE_PATH = ROOT / "reports/t2-orphan-ramp-support-radius.json"
T2_SOURCE = ROOT / "src/bias60/ideas/t.py"
RADIUS_AUDIT_SOURCE = ROOT / "scripts/audit_t2_orphan_ramp_support_radius.py"
INPUTS_SOURCE = ROOT / "src/bias60/inputs.py"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sha_ids(geoids: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(str(g) for g in geoids).encode("utf-8")).hexdigest()


def connector_ids(connectors: Any) -> list[str]:
    """Same deterministic distinct connector ordering as the registered T2 exact reader."""
    items = [item for item in (connectors or [])
             if isinstance(item, dict) and item.get("connector_id")]
    items.sort(key=lambda item: float(item.get("at", 0.0)))
    return list(dict.fromkeys(str(item["connector_id"]) for item in items))


def is_link(road_class: Any, subclass: Any) -> bool:
    return ((isinstance(subclass, str) and "link" in subclass.casefold()) or
            (isinstance(road_class, str) and "_link" in road_class.casefold()))


def classify_orphan_links(
    incidence_counts: dict[str, tuple[int, int]],
    link_rows: list[tuple[int, str, tuple[str, str], bytes]],
) -> list[tuple[int, str, str, bytes]]:
    """Apply T2-v2's exact two-endpoint neighbor rule to source-row link candidates."""
    orphan: list[tuple[int, str, str, bytes]] = []
    for row_id, road_class, endpoints, wkb in link_rows:
        valid = True
        for connector in endpoints:
            all_n, named_n = incidence_counts[connector]
            other_n = all_n - 1
            named_neighbors = named_n - int(road_class in NAMED)
            if other_n <= 0 or named_neighbors > 0:
                valid = False
                break
        if valid:
            orphan.append((row_id, endpoints[0], endpoints[1], wkb))
    return orphan


def resolve_connector_pairs(
    orphan_endpoints: dict[str, set[int]],
    roads_by_connector: dict[str, set[int]],
    road_endpoints: dict[int, tuple[str, str]],
) -> dict[str, Any]:
    """Accept only one-link-endpoint to one-road rows with no second orphan contact."""
    road_target_connectors: dict[int, set[str]] = defaultdict(set)
    for connector, road_rows in roads_by_connector.items():
        for road_row in road_rows:
            road_target_connectors[road_row].add(connector)
    candidate_pairs: list[tuple[int, int, str, str]] = []
    accepted: list[tuple[int, int, str, str]] = []
    reasons: Counter[str] = Counter()
    endpoint_with_candidate = 0
    for connector in sorted(set(orphan_endpoints) | set(roads_by_connector)):
        links = orphan_endpoints.get(connector, set())
        roads = roads_by_connector.get(connector, set())
        if links and roads:
            endpoint_with_candidate += len(links)
        for link_row in sorted(links):
            for road_row in sorted(roads):
                endpoints = road_endpoints[road_row]
                other = endpoints[1] if endpoints[0] == connector else endpoints[0]
                pair = (link_row, road_row, connector, other)
                candidate_pairs.append(pair)
                why = []
                if len(links) != 1:
                    why.append("multiple_orphan_link_endpoints_at_connector")
                if len(roads) != 1:
                    why.append("multiple_eligible_roads_at_connector")
                if len(road_target_connectors[road_row]) != 1:
                    why.append("road_touches_multiple_orphan_endpoint_connectors")
                if why:
                    reasons.update(why)
                else:
                    accepted.append(pair)
    if len(accepted) != len(set(accepted)):
        raise ValueError("duplicate connector pair accepted")
    accepted_ambiguous = 0
    if any(len(road_target_connectors[row]) != 1 for _, row, _, _ in accepted):
        accepted_ambiguous += 1
    if accepted_ambiguous:
        raise ValueError("ambiguous connector pair accepted")
    cardinality = Counter()
    for connector, links in orphan_endpoints.items():
        link_n = len(links)
        road_n = len(roads_by_connector.get(connector, set()))
        if road_n == 0:
            cardinality["unmatched_orphan_endpoint"] += 1
        elif link_n == 1 and road_n == 1:
            cardinality["one_link_one_road"] += 1
        elif link_n == 1:
            cardinality["one_link_many_roads"] += 1
        elif road_n == 1:
            cardinality["many_links_one_road"] += 1
        else:
            cardinality["many_links_many_roads"] += 1
    potential_n = len(candidate_pairs)
    return {
        "accepted_pairs": accepted,
        "candidate_pair_count": potential_n,
        "accepted_pair_count": len(accepted),
        "ambiguous_pair_count": potential_n - len(accepted),
        "ambiguous_pair_rejection_reasons": dict(sorted(reasons.items())),
        "endpoint_occurrences_with_candidate": endpoint_with_candidate,
        "distinct_candidate_road_rows": len(road_target_connectors),
        "candidate_road_row_match_degrees": dict(Counter(map(len, road_target_connectors.values()))),
        "orphan_endpoint_connector_cardinality": dict(sorted(cardinality.items())),
        "accepted_ambiguous_pair_count": accepted_ambiguous,
        "unique_match_precision_proxy": (len(accepted) / potential_n if potential_n else None),
        "accepted_pair_sha256": hashlib.sha256("\n".join(
            f"{a}:{b}:{c}:{d}" for a, b, c, d in sorted(accepted)).encode()).hexdigest(),
        "candidate_pair_sha256": hashlib.sha256("\n".join(
            f"{a}:{b}:{c}:{d}" for a, b, c, d in sorted(candidate_pairs)).encode()).hexdigest(),
    }


def baseline_support_measure(official: list[tuple[str, str]]) -> dict[str, Any]:
    if sha256_file(BASELINE_PATH) != json.loads(PREREG_PATH.read_text())["measure_before"]["source_report_sha256"]:
        raise ValueError("frozen 2/5/10 km support report hash changed")
    prior = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    expected_regions = {r for _, r in official}
    if len(official) != 9379 or len({g for g, _ in official}) != 9379 or expected_regions != set(REGIONS):
        raise ValueError("authoritative 9,379-ID sample contract mismatch")
    baseline_region_ids = {}
    positive_by_radius: dict[str, set[str]] = {"2km": set(), "5km": set(), "10km": set()}
    per_region = {}
    for region in REGIONS:
        expected_ids = [g for g, r in official if r == region]
        block = prior["regions"][region]
        tracts = block["per_tract"]
        ids = [str(row["GEOID"]) for row in tracts]
        if ids != expected_ids or len(ids) != len(set(ids)):
            raise ValueError(f"{region}: radius-report GEOIDs/order do not reproduce official sample")
        baseline_region_ids[region] = ids
        per_region[region] = {"official_tracts": len(ids), "official_geoid_order_sha256": sha_ids(ids),
                              "radii": {}}
        for radius in ("2km", "5km", "10km"):
            support = [str(row["GEOID"]) for row in tracts if row["radii"][radius]["support_positive"]]
            summary = block["radius_results"][radius]
            if len(support) != int(summary["support_count"]):
                raise ValueError(f"{region} {radius}: support count fails reproduction")
            if abs(len(support) / len(ids) - float(summary["support_fraction"])) > 1e-15:
                raise ValueError(f"{region} {radius}: support fraction fails reproduction")
            positive_by_radius[radius].update(support)
            per_region[region]["radii"][radius] = {
                "support_count": len(support), "support_fraction": len(support) / len(ids),
                "support_geoids_sha256": sha_ids(support),
            }
    overall = {}
    official_ids = [g for g, _ in official]
    for radius in ("2km", "5km", "10km"):
        support = [g for g in official_ids if g in positive_by_radius[radius]]
        summary = prior["measure_after"]["radii"][radius]
        if len(support) != int(summary["support_count"]):
            raise ValueError(f"overall {radius}: support count fails reproduction")
        overall[radius] = {"support_count": len(support), "official_tracts": len(official_ids),
                           "support_fraction": len(support) / len(official_ids),
                           "support_geoids_sha256": sha_ids(support)}
    return {
        "source_report": "reports/t2-orphan-ramp-support-radius.json",
        "source_report_sha256": sha256_file(BASELINE_PATH),
        "official_tracts": len(official_ids), "official_geoid_order_sha256": sha_ids(official_ids),
        "overall": overall, "by_region": per_region,
        "counts_and_hashes_reproduced": True,
    }


def validate_radius_reruns(paths: list[Path], official: list[tuple[str, str]], baseline: dict[str, Any]) -> dict[str, Any]:
    """Cross-check fresh exact 2/5/10 km reader checkpoints against the frozen before state."""
    if len(paths) != 4:
        raise ValueError("provide one fresh radius rerun for each of the four regions")
    frozen = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    keyed = {}
    for path in paths:
        part = json.loads(path.read_text(encoding="utf-8"))
        region = part.get("region")
        if region in keyed:
            raise ValueError(f"duplicate radius rerun region {region}")
        if part["provenance"]["radius_audit_script_sha256"] != sha256_file(RADIUS_AUDIT_SOURCE):
            raise ValueError(f"{region}: radius checkpoint was not generated by the current audit script")
        keyed[region] = (part, path)
    if set(keyed) != set(REGIONS):
        raise ValueError("radius reruns do not cover the exact four regions")
    regions = {}
    overall_sets = {name: set() for name in ("2km", "5km", "10km")}
    for region in REGIONS:
        part, path = keyed[region]
        expected_ids = [g for g, r in official if r == region]
        tract_rows = part["per_tract"]
        ids = [str(row["GEOID"]) for row in tract_rows]
        if ids != expected_ids:
            raise ValueError(f"{region}: fresh radius rerun GEOID order differs from official sample")
        cells = {}
        for radius in ("2km", "5km", "10km"):
            supported = [str(row["GEOID"]) for row in tract_rows if row["radii"][radius]["support_positive"]]
            count = int(part["radius_results"][radius]["support_count"])
            if count != len(supported):
                raise ValueError(f"{region} {radius}: rerun per-tract support does not match its count")
            frozen_ids = [str(row["GEOID"]) for row in frozen["regions"][region]["per_tract"]
                          if row["radii"][radius]["support_positive"]]
            if supported != frozen_ids:
                raise ValueError(f"{region} {radius}: fresh radius support GEOID set differs from frozen report")
            if count != baseline["by_region"][region]["radii"][radius]["support_count"]:
                raise ValueError(f"{region} {radius}: fresh radius count differs from before-state reproduction")
            cells[radius] = {"support_count": count, "support_fraction": count / len(ids),
                             "support_geoids_sha256": sha_ids(supported)}
            overall_sets[radius].update(supported)
        regions[region] = {"checkpoint_path": str(path), "checkpoint_sha256": sha256_file(path),
                           "audit_script_sha256": part["provenance"]["radius_audit_script_sha256"],
                           "official_geoid_order_sha256": sha_ids(ids), "radii": cells}
    all_ids = [g for g, _ in official]
    overall = {}
    for radius, support_set in overall_sets.items():
        supported = [g for g in all_ids if g in support_set]
        frozen_count = baseline["overall"][radius]["support_count"]
        if len(supported) != frozen_count:
            raise ValueError(f"overall {radius}: fresh radius reruns differ from before-state support count")
        overall[radius] = {"support_count": len(supported), "support_fraction": len(supported) / len(all_ids),
                           "support_geoids_sha256": sha_ids(supported)}
    return {"reader_rerun_validated": True, "regions": regions, "overall": overall,
            "official_tracts": len(all_ids), "official_geoid_order_sha256": sha_ids(all_ids)}


def ruca_key(code: Any, covered: Any) -> str:
    return baseline_audit.classify_ruca(code, covered)


def audit_region(workspace: Path, region: str) -> dict[str, Any]:
    import pyarrow.parquet as pq
    import shapely
    from pyproj import Transformer

    config = load_config(CONFIG_PATH)
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    geoids = [g for g, r in official if r == region]
    expected_n = int(config["inputs"]["official_rows_by_region"][region])
    if len(official) != 9379 or len(set(g for g, _ in official)) != 9379 or len(geoids) != expected_n:
        raise ValueError("official ID scope differs from the preregistered partition")
    inputs = ProvidedInputs(config, workspace, region, geoids)
    seed = config["seeds"]["T2"]
    named = set(seed["named_classes"])
    parquet = _road_layer(inputs, ROAD_COLUMNS, seed["unfiltered_layer"])
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [all rows, named rows]
    link_rows: list[tuple[int, str, tuple[str, str], bytes]] = []
    rows_read = 0
    row_offset = 0
    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=ROAD_COLUMNS)
        classes = table["class"].to_pylist()
        subclasses = table["subclass"].to_pylist()
        conns = table["connectors"].to_pylist()
        wkbs = table["geometry"].to_pylist()
        for cls, sub, conn, wkb in zip(classes, subclasses, conns, wkbs):
            ids = connector_ids(conn)
            for connector in ids:
                counts[connector][0] += 1
                counts[connector][1] += int(isinstance(cls, str) and cls in named)
            if isinstance(cls, str) and is_link(cls, sub) and len(ids) >= 2 and wkb is not None:
                link_rows.append((row_offset, cls, (ids[0], ids[-1]), wkb))
            row_offset += 1
        rows_read += len(classes)
    if rows_read != int(parquet.metadata.num_rows) or row_offset != rows_read:
        raise ValueError(f"{region}: concatenated source row ordinal differs from Parquet footer")
    orphan_links = classify_orphan_links(counts, link_rows)
    orphan_endpoints: dict[str, set[int]] = defaultdict(set)
    for row_id, first, last, _ in orphan_links:
        orphan_endpoints[first].add(row_id)
        orphan_endpoints[last].add(row_id)
    target_connectors = set(orphan_endpoints)

    roads_by_connector: dict[str, set[int]] = defaultdict(set)
    road_endpoints: dict[int, tuple[str, str]] = {}
    road_wkb: dict[int, bytes] = {}
    complex_candidate_rows = set()
    row_offset = 0
    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=ROAD_COLUMNS)
        classes = table["class"].to_pylist()
        subclasses = table["subclass"].to_pylist()
        conns = table["connectors"].to_pylist()
        wkbs = table["geometry"].to_pylist()
        for cls, sub, conn, wkb in zip(classes, subclasses, conns, wkbs):
            ids = connector_ids(conn)
            if (isinstance(cls, str) and cls not in named and not is_link(cls, sub)
                    and wkb is not None and target_connectors.intersection(ids)):
                if len(ids) != 2:
                    complex_candidate_rows.add(row_offset)
                else:
                    endpoints = (ids[0], ids[1])
                    road_endpoints[row_offset] = endpoints
                    road_wkb[row_offset] = wkb
                    for connector in set(endpoints) & target_connectors:
                        roads_by_connector[connector].add(row_offset)
            row_offset += 1
    if row_offset != rows_read:
        raise ValueError(f"{region}: second pass source row ordinal differs")
    match = resolve_connector_pairs(orphan_endpoints, roads_by_connector, road_endpoints)
    accepted = match.pop("accepted_pairs")
    accepted_roads = sorted({road_row for _, road_row, _, _ in accepted})
    if len(accepted_roads) != len(accepted):
        raise ValueError("strict unique endpoint rule yielded reused road source rows")

    tracts_ll = inputs.tract_geometries()
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    tracts = shapely.transform(tracts_ll, transformer.transform, interleaved=False)
    tree = shapely.STRtree(tracts)
    attached_length_m = np.zeros(len(geoids), dtype=np.float64)
    road_rows_by_tract: list[set[int]] = [set() for _ in geoids]
    for start in range(0, len(accepted_roads), 2048):
        block_ids = accepted_roads[start:start + 2048]
        lines = shapely.transform(shapely.from_wkb([road_wkb[r] for r in block_ids]),
                                  transformer.transform, interleaved=False)
        pairs = tree.query(lines, predicate="intersects")
        if not pairs.size:
            continue
        pieces = shapely.intersection(lines[pairs[0]], tracts[pairs[1]])
        lengths = np.asarray(shapely.length(pieces), dtype=np.float64)
        for local, tract_id, length in zip(pairs[0].tolist(), pairs[1].tolist(), lengths.tolist()):
            if not math.isfinite(length) or length <= 0:
                continue
            road_row = block_ids[local]
            attached_length_m[tract_id] += float(length)
            road_rows_by_tract[tract_id].add(road_row)
    support = attached_length_m > 0.0
    if not np.isfinite(attached_length_m).all() or np.any(attached_length_m < 0):
        raise ValueError(f"{region}: invalid attached length")

    ruca = inputs.strata(["ruca_primary", "ruca_covered"])
    strata = [ruca_key(code, covered) for code, covered in
              zip(ruca["ruca_primary"], ruca["ruca_covered"])]
    strata_counts: dict[str, dict[str, int]] = defaultdict(lambda: {
        "official_tracts": 0, "positive_topology_support": 0,
    })
    for key, present in zip(strata, support):
        strata_counts[key]["official_tracts"] += 1
        strata_counts[key]["positive_topology_support"] += int(bool(present))
    ruca_table = {}
    for key, cell in sorted(strata_counts.items(), key=lambda item: (item[0] == "missing_or_uncovered", item[0])):
        n = cell["official_tracts"]
        ruca_table[key] = {**cell, "support_fraction": cell["positive_topology_support"] / n if n else 0.0}
    support_geoids = [g for g, present in zip(geoids, support) if present]
    accepted_link_rows = {link_row for link_row, _, _, _ in accepted}
    link_rows_covered = len(accepted_link_rows)
    endpoint_count = sum(len(rows) for rows in orphan_endpoints.values())
    endpoint_covered = int(match["endpoint_occurrences_with_candidate"])
    road_schema = hashlib.sha256("\n".join(
        f"{column}:{parquet.schema_arrow.field(column).type}" for column in ROAD_COLUMNS).encode()).hexdigest()
    road_object = config["inputs"]["package_layer_path_template"].format(
        bucket=config["inputs"]["package_bucket"], region=region,
        layer=seed["unfiltered_layer"])
    file_info = inputs.filesystem.get_file_info(road_object)
    return {
        "region": region,
        "official_geoids": geoids,
        "official_tracts": len(geoids),
        "official_geoid_order_sha256": sha_ids(geoids),
        "topology_support_geoids": support_geoids,
        "topology_support_count": len(support_geoids),
        "topology_support_fraction": len(support_geoids) / len(geoids),
        "topology_support_geoids_sha256": sha_ids(support_geoids),
        "positive_attached_length_sum_km": float(attached_length_m.sum() / 1000.0),
        "attached_source_road_tract_contributions": int(sum(len(x) for x in road_rows_by_tract)),
        "orphan_link_count": len(orphan_links),
        "orphan_link_endpoint_occurrences": endpoint_count,
        "orphan_links_with_unique_carriageway_match": link_rows_covered,
        "orphan_link_coverage_fraction": link_rows_covered / len(orphan_links) if orphan_links else None,
        "orphan_endpoint_occurrences_with_candidate": endpoint_covered,
        "orphan_endpoint_coverage_fraction": endpoint_covered / endpoint_count if endpoint_count else None,
        "eligible_complex_or_multi_connector_nonlink_rows_excluded": len(complex_candidate_rows),
        "endpoint_matching": {
            **match,
            "unique_match_precision_proxy_scope": "accepted source-row connector pairs / every eligible endpoint-to-road candidate pair; not measured against truth",
            "ambiguous_pair_accepted": False,
        },
        "ruca_support": ruca_table,
        "provenance": {
            "audit_script_sha256": sha256_file(Path(__file__).resolve()),
            "harness_config_sha256": sha256_file(CONFIG_PATH),
            "preregistration_sha256": sha256_file(PREREG_PATH),
            "provided_input_reader_sha256": sha256_file(INPUTS_SOURCE),
        },
        "source_provenance": {
            "provided_road_object": road_object,
            "road_rows": int(parquet.metadata.num_rows),
            "road_row_groups": int(parquet.metadata.num_row_groups),
            "road_object_bytes": int(file_info.size) if file_info.size is not None and file_info.size >= 0 else None,
            "selected_columns": ROAD_COLUMNS,
            "selected_schema_sha256": road_schema,
            "provided_RUCA_columns_read": ["ruca_primary", "ruca_covered"],
            "official_tract_geometry": "provided census-acs-housing GEOID,geometry",
            "crs_for_length": "EPSG:5070",
        },
        "data_use": {
            "labels_or_target_values_read": False,
            "removed_references_or_equivalents_read": False,
            "external_data_or_roads_read": False,
            "leaderboard_or_submission_information_used": False,
            "model_fit": False,
            "candidate_changes": False,
            "uploads": 0,
        },
    }


def merge_reports(workspace: Path, region_paths: list[Path], repeat_path: Path,
                  radius_paths: list[Path], out: Path) -> dict[str, Any]:
    config = load_config(CONFIG_PATH)
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(official) != 9379 or len({g for g, _ in official}) != 9379:
        raise ValueError("official sample is not the exact unique 9,379-ID scope")
    if len(region_paths) != 4:
        raise ValueError("provide exactly one checkpoint per official region")
    region_reports = [json.loads(path.read_text(encoding="utf-8")) for path in region_paths]
    keyed = {report["region"]: report for report in region_reports}
    path_by_region = {report["region"]: path for report, path in zip(region_reports, region_paths)}
    if len(keyed) != len(region_reports) or set(keyed) != set(REGIONS):
        raise ValueError("region checkpoints do not form the four-region partition")
    all_ids = []
    support_ids = []
    regional_support = {}
    for region in REGIONS:
        report = keyed[region]
        expected = [g for g, r in official if r == region]
        if report["official_geoids"] != expected:
            raise ValueError(f"{region}: checkpoint GEOID list/order differs from official partition")
        if report["provenance"]["audit_script_sha256"] != sha256_file(Path(__file__).resolve()):
            raise ValueError(f"{region}: checkpoint was not generated by the current audit script")
        if report["endpoint_matching"]["accepted_ambiguous_pair_count"] != 0:
            raise ValueError(f"{region}: ambiguous pair accepted")
        if report["endpoint_matching"]["ambiguous_pair_count"] != report["endpoint_matching"]["candidate_pair_count"] - report["endpoint_matching"]["accepted_pair_count"]:
            raise ValueError(f"{region}: endpoint match partition mismatch")
        regional_support[region] = report["topology_support_fraction"]
        all_ids.extend(report["official_geoids"])
        support_ids.extend(report["topology_support_geoids"])
    if all_ids != [g for g, _ in official] or len(set(all_ids)) != 9379:
        raise ValueError("regional checkpoint lists do not partition the exact official ID order")
    if len(set(support_ids)) != len(support_ids) or not set(support_ids).issubset(set(all_ids)):
        raise ValueError("topology support GEOIDs are duplicated or outside the official list")
    repeat_raw = repeat_path.read_bytes()
    match_repeat = next((path for path in region_paths if path.read_bytes() == repeat_raw), None)
    if match_repeat is None:
        raise ValueError("deterministic rerun checkpoint is not byte-identical to its region checkpoint")
    baseline = baseline_support_measure(official)
    radius_reexecution = validate_radius_reruns(radius_paths, official, baseline)
    total_support = len(support_ids)
    gate = baseline_audit.support_backed_gate(total_support, len(official), regional_support)
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    report = {
        "schema_version": 1,
        "audit": "T2 connector-topology orphan-ramp carriageway support; diagnostic only",
        "measure_before": baseline,
        "post_measure_validation": {
            "fresh_2_5_10km_radius_reader_rerun": radius_reexecution,
            "interpretation": "Fresh radius-audit reruns were used only to verify the frozen before-state counts and GEOID hashes; they did not alter the preregistered topology definition or gate.",
        },
        "measure_after": {
            "official_partition": {
                "exact_official_tracts": len(all_ids),
                "exact_9379_id_partition_verified": True,
                "official_order_sha256": sha_ids([g for g, _ in official]),
                "region_order_sha256": {r: keyed[r]["official_geoid_order_sha256"] for r in REGIONS},
            },
            "connector_topology_support": {
                "support_count": total_support,
                "support_fraction": total_support / len(official),
                "support_geoids_sha256": sha_ids([g for g, _ in official if g in set(support_ids)]),
                "region_gate_fractions": regional_support,
                "RUCA_tables": {r: keyed[r]["ruca_support"] for r in REGIONS},
                "regions": keyed,
                "support_gate": gate,
            },
            "connector_matching": {
                "candidate_pair_count": sum(keyed[r]["endpoint_matching"]["candidate_pair_count"] for r in REGIONS),
                "accepted_unique_pair_count": sum(keyed[r]["endpoint_matching"]["accepted_pair_count"] for r in REGIONS),
                "ambiguous_pair_count": sum(keyed[r]["endpoint_matching"]["ambiguous_pair_count"] for r in REGIONS),
                "accepted_ambiguous_pair_count": sum(keyed[r]["endpoint_matching"]["accepted_ambiguous_pair_count"] for r in REGIONS),
                "unique_match_precision_proxy": (
                    sum(keyed[r]["endpoint_matching"]["accepted_pair_count"] for r in REGIONS) /
                    sum(keyed[r]["endpoint_matching"]["candidate_pair_count"] for r in REGIONS)
                    if sum(keyed[r]["endpoint_matching"]["candidate_pair_count"] for r in REGIONS) else None),
                "per_region": {r: keyed[r]["endpoint_matching"] for r in REGIONS},
            },
            "determinism": {
                "rerun_region": json.loads(repeat_path.read_text(encoding="utf-8"))["region"],
                "rerun_checkpoint_path": str(repeat_path),
                "rerun_checkpoint_sha256": sha256_file(repeat_path),
                "byte_identical_to_region_checkpoint": True,
            },
            "acceptance": {
                "before_radius_counts_and_geoids_hashes_reproduced": (
                    baseline["counts_and_hashes_reproduced"] and radius_reexecution["reader_rerun_validated"]),
                "exact_official_9379_id_partition_verified": True,
                "deterministic_region_rerun_byte_identical": True,
                "ambiguous_pairs_accepted": 0,
                "all_ambiguous_pairs_rejected": True,
                "overall_80pct_each_region_50pct_gate_met": gate["passes"],
            },
            "decision": gate["decision"],
        },
        "method": prereg["topology_definition"],
        "preregistration": {
            "path": str(PREREG_PATH.relative_to(ROOT)),
            "sha256": sha256_file(PREREG_PATH),
            "registered_before_topology_measurement": True,
        },
        "provenance": {
            "audit_script_sha256": sha256_file(Path(__file__).resolve()),
            "baseline_radius_report_sha256": sha256_file(BASELINE_PATH),
            "harness_config_sha256": sha256_file(CONFIG_PATH),
            "provided_input_reader_sha256": sha256_file(INPUTS_SOURCE),
            "T2_idea_source_sha256": sha256_file(T2_SOURCE),
            "region_checkpoint_paths": {r: str(path_by_region[r]) for r in REGIONS},
            "region_checkpoint_sha256": {r: sha256_file(path_by_region[r]) for r in REGIONS},
        },
        "data_use": {
            "official_tracts": 9379,
            "labels_or_target_values_read": False,
            "removed_reference_layers_or_equivalents_read": False,
            "external_data_or_roads_read": False,
            "leaderboard_or_submission_information_used": False,
            "model_fit": False,
            "candidate_changes": False,
            "uploads": 0,
        },
        "reproduction": {
            "region": "python scripts/audit_t2_connector_topology_support.py --workspace $COMPETITION_WORKSPACE --region <REGION> --out $COMPETITION_WORKSPACE/t2-connector-topology-checkpoints/<REGION>.json",
            "merge": "python scripts/audit_t2_connector_topology_support.py --workspace $COMPETITION_WORKSPACE --merge-region-reports <4 topology checkpoints> --repeat-checkpoint <byte-identical topology rerun> --radius-region-reports <4 fresh 2/5/10 km audit reruns> --out reports/t2-connector-topology-support.json",
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--region", choices=REGIONS)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--merge-region-reports", nargs="+", type=Path)
    parser.add_argument("--repeat-checkpoint", type=Path)
    parser.add_argument("--radius-region-reports", nargs="+", type=Path)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    if args.merge_region_reports:
        if args.repeat_checkpoint is None or args.out is None or not args.radius_region_reports:
            parser.error("merge requires --repeat-checkpoint, --radius-region-reports and --out")
        report = merge_reports(args.workspace, args.merge_region_reports, args.repeat_checkpoint,
                               args.radius_region_reports, args.out)
        after = report["measure_after"]
        print(json.dumps({
            "official_tracts": after["official_partition"]["exact_official_tracts"],
            "support_count": after["connector_topology_support"]["support_count"],
            "support_fraction": after["connector_topology_support"]["support_fraction"],
            "by_region": after["connector_topology_support"]["region_gate_fractions"],
            "candidate_pairs": after["connector_matching"]["candidate_pair_count"],
            "unique_pairs": after["connector_matching"]["accepted_unique_pair_count"],
            "ambiguous_pairs": after["connector_matching"]["ambiguous_pair_count"],
            "support_gate_passes": after["acceptance"]["overall_80pct_each_region_50pct_gate_met"],
            "out": str(args.out),
        }, sort_keys=True))
        return 0
    if args.region is None or args.out is None:
        parser.error("region mode requires --region and --out")
    result = audit_region(args.workspace, args.region)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "region": args.region, "official_tracts": result["official_tracts"],
        "support_count": result["topology_support_count"],
        "support_fraction": result["topology_support_fraction"],
        "orphan_links": result["orphan_link_count"],
        "candidate_pairs": result["endpoint_matching"]["candidate_pair_count"],
        "unique_pairs": result["endpoint_matching"]["accepted_pair_count"],
        "ambiguous_pairs": result["endpoint_matching"]["ambiguous_pair_count"],
        "out": str(args.out),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
