#!/usr/bin/env python3
"""Audit T4's inclusive degree-product threshold against fractional boundary mass.

Uses only the provided roads-unfiltered layer, provided tract geometries, the
frozen T4 recipe/configuration and its previously emitted feature artifact.
No reference layers, labels, score placeholders, target fitting, or leaderboard
information are accessed. This is a feature-estimator sensitivity audit, not
accuracy evidence.
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
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
from bias60.ideas.t import _metric_tracts, _road_layer  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
REGION_COUNTS = {"maricopa-az": 1593, "northern-ca": 591, "eastern-ok": 1192, "south-central-tx": 6003}
SAMPLE_SHA256 = {
    "maricopa-az": "1640b801d56ab308e68c65583b88cfb9e1555b05a6d0b922eaba22ac09960a31",
    "northern-ca": "f38017b5284667b80b451685fd146388c108e1b30308301289c7a96ecfe60902",
    "eastern-ok": "56cb40fba4008d646bc63c62c91e211871459224b3de8b3a886ff813803fd0d4",
    "south-central-tx": "afa59c0eb30c40be25ce82098f144447591b79cb9692e669b2f2fe7b7b1ee58e",
}
CONFIG_PATH = ROOT / "config/bias60-harness-preregistered.json"
FAMILY_SUMMARY_PATH = ROOT / "reports/bias60-family-t-summary.json"
OUT_PATH = ROOT / "reports/bias60-t4-top-decile-ties.json"
TOL = 1e-12


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def inclusive_selection(histogram: Mapping[int, tuple[int, float, float]], top_fraction: float) -> dict[str, Any]:
    """Match t4() exactly: ceil ascending rank then include every score >= threshold."""
    if not 0.0 < top_fraction < 1.0:
        raise ValueError("top_fraction must be in (0,1)")
    total_edges = sum(int(v[0]) for v in histogram.values())
    if total_edges <= 0:
        return {"defined": False, "total_edges": 0, "threshold_score": None,
                "edges_strictly_above_threshold": 0, "boundary_tie_edges": 0,
                "selected_edges": 0, "selected_edge_share": 0.0,
                "all_top_length": 0.0, "nonnamed_top_length": 0.0, "gap": None}
    rank = max(1, int(math.ceil(total_edges * (1.0 - top_fraction))))
    cumulative = 0
    threshold = None
    for score in sorted(histogram, reverse=True):
        cumulative += int(histogram[score][0])
        threshold = int(score)
        if cumulative >= rank:
            break
    assert threshold is not None
    above = sum(int(v[0]) for score, v in histogram.items() if score > threshold)
    ties = int(histogram[threshold][0])
    selected = above + ties
    all_length = sum(float(v[1]) for score, v in histogram.items() if score >= threshold)
    nonnamed_length = sum(float(v[2]) for score, v in histogram.items() if score >= threshold)
    gap = nonnamed_length / all_length if all_length > 0.0 else 0.0
    return {"defined": True, "total_edges": total_edges, "rank_in_descending_order_1based": rank,
            "threshold_score": threshold, "edges_strictly_above_threshold": above,
            "boundary_tie_edges": ties, "selected_edges": selected,
            "selected_edge_share": selected / total_edges,
            "all_top_length": all_length, "nonnamed_top_length": nonnamed_length,
            "gap": gap}


def fractional_selection(histogram: Mapping[int, tuple[int, float, float]], top_fraction: float) -> dict[str, Any]:
    """Select exactly n*top_fraction edges, fractionally weighting boundary ties."""
    if not 0.0 < top_fraction < 1.0:
        raise ValueError("top_fraction must be in (0,1)")
    total_edges = sum(int(v[0]) for v in histogram.values())
    target_edges = total_edges * top_fraction
    if total_edges <= 0:
        return {"defined": False, "target_edges": 0.0, "boundary_score": None,
                "edges_strictly_above_boundary": 0, "boundary_tie_edges": 0,
                "boundary_weight": 0.0, "gap": None}
    above_count = 0
    boundary_score = None
    boundary_weight = 0.0
    for score in sorted(histogram, reverse=True):
        count = int(histogram[score][0])
        if above_count + count + 1e-15 >= target_edges:
            boundary_score = int(score)
            boundary_weight = min(1.0, max(0.0, (target_edges - above_count) / count))
            break
        above_count += count
    if boundary_score is None:
        raise AssertionError("failed to find fractional top-decile boundary")
    boundary_count = int(histogram[boundary_score][0])
    all_length = sum(float(v[1]) for score, v in histogram.items() if score > boundary_score)
    nonnamed_length = sum(float(v[2]) for score, v in histogram.items() if score > boundary_score)
    all_length += boundary_weight * float(histogram[boundary_score][1])
    nonnamed_length += boundary_weight * float(histogram[boundary_score][2])
    gap = nonnamed_length / all_length if all_length > 0.0 else 0.0
    return {"defined": True, "target_edges": target_edges,
            "boundary_score": boundary_score,
            "edges_strictly_above_boundary": above_count,
            "boundary_tie_edges": boundary_count,
            "boundary_weight": boundary_weight,
            "all_top_length": all_length, "nonnamed_top_length": nonnamed_length,
            "gap": gap}


def analyze_histogram(histogram: Mapping[int, tuple[int, float, float]], top_fraction: float) -> dict[str, Any]:
    current = inclusive_selection(histogram, top_fraction)
    fractional = fractional_selection(histogram, top_fraction)
    if not current["defined"]:
        return {"inclusive": current, "fractional": fractional, "gap_delta_inclusive_minus_fractional": None,
                "edge_share_excess_over_target": None}
    selected_share = current["selected_edge_share"]
    return {"inclusive": current, "fractional": fractional,
            "gap_delta_inclusive_minus_fractional": current["gap"] - fractional["gap"],
            "edge_share_excess_over_target": selected_share - top_fraction}


def linear_quantile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    vals = sorted(float(x) for x in values)
    pos = (len(vals) - 1) * p
    low, high = int(math.floor(pos)), int(math.ceil(pos))
    w = pos - low
    return vals[low] * (1.0 - w) + vals[high] * w


def read_frozen_t4(path: Path, expected_sha: str, expected_pairs: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    import pyarrow.parquet as pq
    if sha256_file(path) != expected_sha:
        raise ValueError("frozen T4 feature-artifact SHA-256 mismatch")
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "component", "channel", "value", "defined"])
    actual_pairs = list(zip(table["GEOID"].to_pylist(), table["region"].to_pylist()))
    if actual_pairs != expected_pairs or len(actual_pairs) != 9379:
        raise ValueError("frozen T4 artifact ID/order coverage mismatch")
    rows: dict[str, dict[str, Any]] = {}
    for geoid, region, idea, component, channel, value, defined in zip(
            table["GEOID"].to_pylist(), table["region"].to_pylist(), table["idea"].to_pylist(),
            table["component"].to_pylist(), table["channel"].to_pylist(), table["value"].to_pylist(),
            table["defined"].to_pylist()):
        if geoid in rows or idea != "T4" or component != "R" or channel != "gap":
            raise ValueError(f"invalid or duplicate T4 feature row for {geoid}")
        value = float(value)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"frozen T4 feature score outside [0,1] for {geoid}")
        rows[geoid] = {"region": region, "value": value, "defined": bool(defined)}
    return rows


def collect_region(inputs: ProvidedInputs, seed: Mapping[str, Any], frozen: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import shapely
    parquet = _road_layer(inputs, ["class", "connectors", "geometry"], seed["unfiltered_layer"])
    degrees: dict[str, int] = defaultdict(int)
    road_rows_read = 0
    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=["class", "connectors"])
        classes = table.column("class").to_pylist()
        connector_rows = table.column("connectors").to_pylist()
        road_rows_read += len(classes)
        for cls, connector_row in zip(classes, connector_rows):
            if cls not in seed["eligible_classes"]:
                continue
            ids = [str(item["connector_id"]) for item in (connector_row or [])
                   if isinstance(item, dict) and item.get("connector_id")]
            for connector in (ids[0], ids[-1]) if len(ids) >= 2 else ids:
                degrees[connector] += 1

    tracts, transformer = _metric_tracts(inputs)
    tree = shapely.STRtree(tracts)
    hist: list[dict[int, list[float]]] = [defaultdict(lambda: [0.0, 0.0, 0.0]) for _ in inputs.geoids]
    road_rows_second_pass = 0
    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=["class", "connectors", "geometry"])
        classes = table.column("class").to_pylist()
        connector_rows = table.column("connectors").to_pylist()
        wkbs = table.column("geometry").to_pylist()
        road_rows_second_pass += len(classes)
        chosen = [i for i, wkb in enumerate(wkbs)
                  if wkb is not None and connector_rows[i] and classes[i] in seed["eligible_classes"]]
        if not chosen:
            continue
        lines = shapely.transform(shapely.from_wkb([wkbs[i] for i in chosen]),
                                  transformer.transform, interleaved=False)
        pairs = tree.query(lines, predicate="intersects")
        if not pairs.size:
            continue
        clipped_lengths = shapely.length(shapely.intersection(lines[pairs[0]], tracts[pairs[1]]))
        for local, tract_idx, length in zip(pairs[0].tolist(), pairs[1].tolist(), clipped_lengths.tolist()):
            row = chosen[local]
            ids = [str(item["connector_id"]) for item in (connector_rows[row] or [])
                   if isinstance(item, dict) and item.get("connector_id")]
            if len(ids) < 2 or not math.isfinite(length) or length <= 0.0:
                continue
            score = int(degrees[ids[0]] * degrees[ids[-1]])
            bucket = hist[tract_idx][score]
            bucket[0] += 1.0
            bucket[1] += float(length)
            if classes[row] not in seed["named_classes"]:
                bucket[2] += float(length)

    rows: list[dict[str, Any]] = []
    defined_count = 0
    exceed_count = 0
    current_shares: list[float] = []
    deltas: list[float] = []
    absolute_deltas: list[float] = []
    edge_total = 0
    tied_boundary_tracts = 0
    for idx, geoid in enumerate(inputs.geoids):
        histogram = {int(score): (int(values[0]), float(values[1]), float(values[2]))
                     for score, values in hist[idx].items()}
        result = analyze_histogram(histogram, float(seed["top_fraction"]))
        current, fractional = result["inclusive"], result["fractional"]
        base = frozen[geoid]
        if current["defined"] != base["defined"]:
            raise ValueError(f"T4 definedness mismatch for {geoid}")
        if current["defined"]:
            if abs(current["gap"] - base["value"]) > TOL:
                raise ValueError(f"reconstructed inclusive T4 gap differs from frozen feature for {geoid}")
            defined_count += 1
            edge_total += current["total_edges"]
            current_shares.append(current["selected_edge_share"])
            delta = result["gap_delta_inclusive_minus_fractional"]
            deltas.append(delta)
            absolute_deltas.append(abs(delta))
            if result["edge_share_excess_over_target"] > 0.05:
                exceed_count += 1
            if current["boundary_tie_edges"] > 1:
                tied_boundary_tracts += 1
        else:
            if histogram or base["value"] != 0.0:
                raise ValueError(f"undefined T4 tract has edges or nonzero frozen value: {geoid}")
            delta = None
        rows.append({
            "GEOID": geoid,
            "defined": bool(current["defined"]),
            "total_edges": current["total_edges"],
            "inclusive_rank_in_descending_order_1based": current.get("rank_in_descending_order_1based"),
            "inclusive_threshold_score": current["threshold_score"],
            "inclusive_edges_strictly_above_threshold": current["edges_strictly_above_threshold"],
            "inclusive_boundary_tie_edges": current["boundary_tie_edges"],
            "inclusive_selected_edges": current["selected_edges"],
            "inclusive_selected_edge_share": current["selected_edge_share"],
            "nominal_target_edge_share": float(seed["top_fraction"]),
            "excess_edge_share_over_target": result["edge_share_excess_over_target"],
            "fractional_boundary_score": fractional["boundary_score"],
            "fractional_edges_strictly_above_boundary": fractional["edges_strictly_above_boundary"],
            "fractional_boundary_tie_edges": fractional["boundary_tie_edges"],
            "fractional_boundary_weight": fractional["boundary_weight"],
            "inclusive_gap": current["gap"],
            "fractional_gap": fractional["gap"],
            "gap_delta_inclusive_minus_fractional": delta,
        })
    n = len(inputs.geoids)
    diagnostics = {
        "official_tracts": n,
        "defined_tracts": defined_count,
        "undefined_tracts": n - defined_count,
        "road_rows_read_first_pass": road_rows_read,
        "road_rows_read_second_pass": road_rows_second_pass,
        "connector_ids_with_nonzero_incidence": len(degrees),
        "edge_tract_segments": edge_total,
        "tracts_with_boundary_tie_mass": tied_boundary_tracts,
        "tracts_selected_over_15pct_of_edges": exceed_count,
        "any_tract_exceeds_nominal_10pct_by_more_than_5pp": exceed_count > 0,
        "selected_edge_share": {
            "median": linear_quantile(current_shares, 0.5),
            "p95": linear_quantile(current_shares, 0.95),
            "maximum": max(current_shares) if current_shares else None,
        },
        "excess_edge_share_over_nominal_10pct": {
            "median": linear_quantile([x - float(seed["top_fraction"]) for x in current_shares], 0.5),
            "p95": linear_quantile([x - float(seed["top_fraction"]) for x in current_shares], 0.95),
            "maximum": max((x - float(seed["top_fraction"]) for x in current_shares), default=None),
        },
        "gap_delta_inclusive_minus_fractional": {
            "defined_tracts": len(deltas),
            "median_signed": linear_quantile(deltas, 0.5),
            "p05_signed": linear_quantile(deltas, 0.05),
            "p95_signed": linear_quantile(deltas, 0.95),
            "median_absolute": linear_quantile(absolute_deltas, 0.5),
            "p95_absolute": linear_quantile(absolute_deltas, 0.95),
            "maximum_absolute": max(absolute_deltas) if absolute_deltas else None,
        },
    }
    return rows, diagnostics


def run(workspace: Path) -> tuple[dict[str, Any], str]:
    config_hash = sha256_file(CONFIG_PATH)
    config = load_config(CONFIG_PATH)
    seed = config["seeds"]["T4"]
    top_fraction = float(seed["top_fraction"])
    summary_hash = sha256_file(FAMILY_SUMMARY_PATH)
    family_summary = json.loads(FAMILY_SUMMARY_PATH.read_text(encoding="utf-8"))
    t4_summary = family_summary["outputs"]["T4"]
    frozen_path = Path(t4_summary["artifact"])
    expected_path = (workspace / "ideas/T4.parquet").resolve()
    if frozen_path.resolve() != expected_path:
        raise ValueError("family T4 artifact path is not the expected workspace feature artifact")
    pairs = official_scored_pairs(workspace)
    if len(pairs) != 9379 or len(set(g for g, _ in pairs)) != 9379:
        raise ValueError("official scored GEOID scope/uniqueness mismatch")
    ids_by_region = {region: [] for region in REGIONS}
    for geoid, region in pairs:
        if region not in ids_by_region:
            raise ValueError(f"unexpected official region {region}")
        ids_by_region[region].append(geoid)
    sample_hashes = {}
    for region in REGIONS:
        sample_path = workspace / config["inputs"]["official_samples_template"].format(region=region)
        sample_hashes[region] = sha256_file(sample_path)
        if sample_hashes[region] != SAMPLE_SHA256[region] or len(ids_by_region[region]) != REGION_COUNTS[region]:
            raise ValueError(f"official sample count/hash mismatch for {region}")
    frozen = read_frozen_t4(frozen_path, t4_summary["sha256"], pairs)
    expected_pairs = [(geoid, region) for region in REGIONS for geoid in ids_by_region[region]]
    if pairs != expected_pairs:
        raise ValueError("official ID order differs from frozen region/sample ordering")

    all_tract_rows = []
    by_region = {}
    input_metadata = {}
    for region in REGIONS:
        geoids = ids_by_region[region]
        inputs = ProvidedInputs(config, workspace, region, geoids)
        # Capture only the provided road-object footer metadata and audited columns.
        roads = _road_layer(inputs, ["class", "connectors", "geometry"], seed["unfiltered_layer"])
        info = inputs.filesystem.get_file_info(inputs.layer_path(seed["unfiltered_layer"]))
        input_metadata[region] = {
            "layer": seed["unfiltered_layer"],
            "parquet_rows": int(roads.metadata.num_rows),
            "row_groups": int(roads.metadata.num_row_groups),
            "remote_size_bytes": int(info.size) if info.size is not None else None,
            "columns_read": ["class", "connectors", "geometry"],
            "geometries": "provided census-acs-housing polygons only; no reference layer",
        }
        # collect_region obtains its own ParquetFile through the guarded provided-input layer path.
        rows, diag = collect_region(inputs, seed, frozen)
        by_region[region] = {"diagnostics": diag, "tracts": rows}
        all_tract_rows.extend({"region": region, **row} for row in rows)
        del inputs, roads
    if len(all_tract_rows) != 9379 or len({row["GEOID"] for row in all_tract_rows}) != 9379:
        raise ValueError("output does not account for all 9,379 unique official GEOIDs")

    report = {
        "schema_version": 1,
        "analysis_id": "BIAS60_T4_TOP_DECILE_TIE_MASS_SENSITIVITY_V1",
        "recipe": {
            "idea": "T4",
            "weight": "degree(u) * degree(v), where endpoint connector incidence is counted over eligible provided roads",
            "eligible_classes": list(seed["eligible_classes"]),
            "named_classes": list(seed["named_classes"]),
            "top_fraction": top_fraction,
            "current_threshold_rule": "rank=max(1,ceil(total_edges*(1-top_fraction))) in descending-score accumulation; select every tract-edge segment whose integer degree product is >= threshold",
            "rank_direction_finding": "The frozen implementation accumulates from highest to lowest but uses rank ceil(n*(1-top_fraction)); with top_fraction=0.1 this selects roughly the top 90% before tie expansion, not a top decile. Therefore observed excess is not attributable to ties alone. This audit leaves the candidate implementation unchanged.",
            "fractional_rule": "select exactly total_edges*top_fraction; take all score groups above the boundary and uniform fractional edge mass from the boundary tie group, applying the same fraction to its all/non-named lengths",
            "gap_rule": "non-named clipped length / all clipped length among selected tract-edge segments",
            "delta_sign": "inclusive T4 gap minus exact fractional-boundary gap",
            "quantile_method": "linear interpolation at (n-1)*p over defined tracts",
        },
        "provenance": {
            "harness_config": {"path": "config/bias60-harness-preregistered.json", "sha256": config_hash},
            "family_summary": {"path": "reports/bias60-family-t-summary.json", "sha256": summary_hash},
            "frozen_T4_feature_artifact": {"path": str(frozen_path), "sha256": t4_summary["sha256"], "rows": int(t4_summary["rows"])},
            "official_sample_sha256": sample_hashes,
            "provided_road_inputs": input_metadata,
            "leaderboard_or_target_data_used": False,
        },
        "scope": {
            "official_geoids": 9379,
            "unique_geoids": 9379,
            "region_counts": {region: len(ids_by_region[region]) for region in REGIONS},
            "sample_score_placeholders_read": False,
            "extra_or_missing_ids": 0,
            "duplicate_ids": 0,
        },
        "summary_by_region": {region: by_region[region]["diagnostics"] for region in REGIONS},
        "tract_results_by_region": {region: by_region[region]["tracts"] for region in REGIONS},
        "decision": {
            "threshold_excess_definition": "inclusive selected edge count / total tract-edge segments - nominal top_fraction",
            "exceedance_rule": "any defined tract with inclusive selected edge share > 0.15 (more than 5 percentage points above nominal 10%)",
            "selection_rank_direction_finding": "Frozen T4 uses descending accumulation with rank ceil(n*(1-top_fraction)); this is a rank-direction mismatch for a top-decile selector and explains the widespread high selected shares. Tie expansion is also fully accounted for, but should not be conflated with this rank-direction issue.",
            "any_tract_exceeds_10pct_by_more_than_5pp": any(
                by_region[r]["diagnostics"]["any_tract_exceeds_nominal_10pct_by_more_than_5pp"] for r in REGIONS),
            "total_tracts_exceeding_15pct": sum(by_region[r]["diagnostics"]["tracts_selected_over_15pct_of_edges"] for r in REGIONS),
            "accuracy_claim": False,
        },
        "safety": {"candidate_artifacts_modified": False, "labels_or_reference_data_read": False,
                    "removed_or_equivalent_layers_read": False, "leaderboard_or_scorecards_accessed": False,
                    "target_fitting_performed": False, "uploads": 0},
    }
    encoded = (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    OUT_PATH.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    return report, digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    report, digest = run(args.workspace)
    print(json.dumps({"report": str(OUT_PATH), "sha256": digest,
                      "official_geoids": report["scope"]["official_geoids"],
                      "tracts_over_15pct": report["decision"]["total_tracts_exceeding_15pct"],
                      "any_exceeds_5pp": report["decision"]["any_tract_exceeds_10pct_by_more_than_5pp"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
