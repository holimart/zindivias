#!/usr/bin/env python3
"""Non-mutating T4 screen for descending top-decile rank direction and tie mass.

Replays the frozen T4 edge histogram from provided roads and tract geometry,
then compares the frozen descending rank with the corrected exact fractional
highest-decile selector. Frozen feature/candidate files are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
import audit_t4_top_decile_ties as prior  # noqa: E402

REGIONS = prior.REGIONS
REGION_COUNTS = prior.REGION_COUNTS
CONFIG_PATH = ROOT / "config/bias60-harness-preregistered.json"
FAMILY_SUMMARY_PATH = ROOT / "reports/bias60-family-t-summary.json"
PRIOR_REPORT_PATH = ROOT / "reports/bias60-t4-top-decile-ties.json"
OUT_PATH = ROOT / "reports/bias60-t4-top-decile-rank-fix.json"
TOL = 1e-12


def corrected_rank_inclusive(histogram: Mapping[int, tuple[int, float, float]],
                              top_fraction: float) -> dict[str, Any]:
    """Correct descending rank to ceil(n*top_fraction), retaining whole ties."""
    if not 0.0 < top_fraction < 1.0:
        raise ValueError("top_fraction must be in (0,1)")
    n = sum(int(v[0]) for v in histogram.values())
    if n <= 0:
        return {"defined": False, "total_edges": 0, "rank_1based": None,
                "threshold_score": None, "edges_above_threshold": 0,
                "boundary_tie_edges": 0, "selected_edges": 0,
                "selected_edge_share": None, "tie_expansion_edges": 0,
                "tie_expansion_share": None, "gap": None}
    rank = max(1, int(np.ceil(n * top_fraction)))
    cumulative = 0
    threshold = None
    for score in sorted(histogram, reverse=True):
        cumulative += int(histogram[score][0])
        threshold = int(score)
        if cumulative >= rank:
            break
    assert threshold is not None
    above = sum(int(v[0]) for score, v in histogram.items() if score > threshold)
    tie_count = int(histogram[threshold][0])
    selected = above + tie_count
    total_length = sum(float(v[1]) for score, v in histogram.items() if score >= threshold)
    nonnamed_length = sum(float(v[2]) for score, v in histogram.items() if score >= threshold)
    share = selected / n
    target = n * top_fraction
    return {
        "defined": True, "total_edges": n, "rank_1based": rank,
        "threshold_score": threshold, "edges_above_threshold": above,
        "boundary_tie_edges": tie_count, "selected_edges": selected,
        "selected_edge_share": share,
        "tie_expansion_edges": selected - target,
        "tie_expansion_share": share - top_fraction,
        "gap": nonnamed_length / total_length if total_length > 0.0 else 0.0,
    }


def corrected_exact_fractional(histogram: Mapping[int, tuple[int, float, float]],
                               top_fraction: float) -> dict[str, Any]:
    """Exact fractional-boundary selection; exactly n*top_fraction edge mass."""
    result = prior.fractional_selection(histogram, top_fraction)
    if not result["defined"]:
        return {**result, "selected_edge_share": None, "edge_share_error": None}
    share = result["target_edges"] / sum(int(v[0]) for v in histogram.values())
    if abs(share - top_fraction) > TOL:
        raise AssertionError("fractional top-decile edge share differs beyond 1e-12")
    return {**result, "selected_edge_share": share, "edge_share_error": share - top_fraction}


def output_sha256(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        value = format(float(row["corrected_exact_fractional_score"]), ".17g") if row["defined"] else ""
        lines.append(f"{row['GEOID']},{row['region']},{int(row['defined'])},{value}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def q(values: list[float], p: float) -> float | None:
    return prior.linear_quantile(values, p)


def summarize_region(rows: list[dict[str, Any]]) -> dict[str, Any]:
    defined = [row for row in rows if row["defined"]]
    frozen_share = [float(row["frozen_selected_edge_share"]) for row in defined]
    inclusive_share = [float(row["corrected_rank_inclusive_selected_edge_share"]) for row in defined]
    share_errors = [float(row["corrected_rank_inclusive_edge_share_error"]) for row in defined]
    deltas = [float(row["score_delta_vs_frozen"]) for row in defined]
    abs_deltas = [abs(value) for value in deltas]
    tie_expansion = [float(row["corrected_rank_tie_expansion_share"]) for row in defined]
    return {
        "official_geoids": len(rows), "defined_geoids": len(defined),
        "undefined_geoids": len(rows) - len(defined),
        "frozen_selected_edge_share": {"median": q(frozen_share, .5), "p95": q(frozen_share, .95),
                                       "maximum": max(frozen_share, default=None)},
        "corrected_rank_inclusive_selected_edge_share": {"median": q(inclusive_share, .5),
            "p95": q(inclusive_share, .95), "maximum": max(inclusive_share, default=None)},
        "corrected_rank_inclusive_edge_share_error_vs_exact_10pct": {
            "median": q(share_errors, .5), "p95": q(share_errors, .95), "maximum": max(share_errors, default=None)},
        "corrected_exact_fractional_selected_edge_share": {"minimum": min(
            (float(row["corrected_exact_fractional_selected_edge_share"]) for row in defined), default=None),
            "maximum": max((float(row["corrected_exact_fractional_selected_edge_share"]) for row in defined), default=None)},
        "tie_expansion_share_after_rank_direction_fix": {"median": q(tie_expansion, .5),
            "p95": q(tie_expansion, .95), "maximum": max(tie_expansion, default=None),
            "tracts_with_positive_expansion": sum(value > TOL for value in tie_expansion)},
        "exact_fractional_score_delta_vs_frozen": {"median_signed": q(deltas, .5),
            "p05_signed": q(deltas, .05), "p95_signed": q(deltas, .95),
            "median_absolute": q(abs_deltas, .5), "p95_absolute": q(abs_deltas, .95),
            "maximum_absolute": max(abs_deltas, default=None)},
    }


def run(workspace: Path, out_path: Path = OUT_PATH) -> dict[str, Any]:
    config_hash = prior.sha256_file(CONFIG_PATH)
    if config_hash != "12a15e11cd0459299e407e5f1f7056fa16a6a8ddad5d7bfd826b20dbe476d558":
        raise ValueError("frozen T4 harness config hash changed")
    config = load_config(CONFIG_PATH)
    seed = config["seeds"]["T4"]
    fraction = float(seed["top_fraction"])
    prior_report = json.loads(PRIOR_REPORT_PATH.read_text(encoding="utf-8"))
    if prior_report["scope"]["official_geoids"] != 9379 or prior_report["decision"]["total_tracts_exceeding_15pct"] != 9373:
        raise ValueError("frozen T4 baseline audit scope or pre-fix finding changed")
    family_summary = json.loads(FAMILY_SUMMARY_PATH.read_text(encoding="utf-8"))
    t4_summary = family_summary["outputs"]["T4"]
    frozen_path = Path(t4_summary["artifact"])
    if frozen_path.resolve() != (workspace / "ideas/T4.parquet").resolve():
        raise ValueError("frozen T4 artifact path differs from expected workspace artifact")
    pairs = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(pairs) != 9379 or len(set(pairs)) != 9379 or len(set(g for g, _ in pairs)) != 9379:
        raise ValueError("official T4 scope is not 9,379 unique GEOIDs")
    ids_by_region: dict[str, list[str]] = {region: [] for region in REGIONS}
    for geoid, region in pairs:
        if region not in ids_by_region:
            raise ValueError(f"unexpected official region {region}")
        ids_by_region[region].append(geoid)
    for region in REGIONS:
        if len(ids_by_region[region]) != REGION_COUNTS[region]:
            raise ValueError(f"official sample count mismatch in {region}")
    frozen = prior.read_frozen_t4(frozen_path, t4_summary["sha256"], pairs)

    all_rows: list[dict[str, Any]] = []
    road_inputs: dict[str, Any] = {}
    for region in REGIONS:
        ids = ids_by_region[region]
        inputs = ProvidedInputs(config, workspace, region, ids)
        roads = prior._road_layer(inputs, ["class", "connectors", "geometry"], seed["unfiltered_layer"])
        info = inputs.filesystem.get_file_info(inputs.layer_path(seed["unfiltered_layer"]))
        road_inputs[region] = {
            "layer": seed["unfiltered_layer"], "parquet_rows": int(roads.metadata.num_rows),
            "row_groups": int(roads.metadata.num_row_groups),
            "remote_size_bytes": int(info.size) if info.size is not None else None,
            "columns_read": ["class", "connectors", "geometry"],
            "tract_geometry_source": "provided census-acs-housing polygons only",
        }
        # Reuses the frozen-histogram reconstruction; it asserts exact equality to frozen T4.
        tract_rows, _diagnostics = prior.collect_region(inputs, seed, frozen)
        for row in tract_rows:
            base = frozen[row["GEOID"]]
            if row["defined"]:
                rank_share = (row["fractional_edges_strictly_above_boundary"] +
                              row["fractional_boundary_tie_edges"]) / row["total_edges"]
                exact_share = row["fractional_edges_strictly_above_boundary"] / row["total_edges"] + (
                    row["fractional_boundary_weight"] * row["fractional_boundary_tie_edges"] / row["total_edges"])
                if abs(exact_share - fraction) > TOL:
                    raise AssertionError(f"fractional exact-top share fails 1e-12 for {row['GEOID']}")
                # Corrected fractional-boundary selection is the score output. The corrected
                # integer-rank inclusive view isolates tie expansion after fixing rank direction.
                score = float(row["fractional_gap"])
                score_delta = score - float(base["value"])
                corrected_selected = row["fractional_edges_strictly_above_boundary"] + row["fractional_boundary_tie_edges"]
                corrected_rank_share = corrected_selected / row["total_edges"]
                item = {
                    "GEOID": row["GEOID"], "region": region, "defined": True,
                    "total_edges": row["total_edges"],
                    "frozen_rank_1based": row["inclusive_rank_in_descending_order_1based"],
                    "corrected_rank_1based": max(1, int(np.ceil(row["total_edges"] * fraction))),
                    "frozen_selected_edge_share": row["inclusive_selected_edge_share"],
                    "corrected_rank_inclusive_selected_edges": corrected_selected,
                    "corrected_rank_inclusive_selected_edge_share": corrected_rank_share,
                    "corrected_rank_inclusive_edge_share_error": corrected_rank_share - fraction,
                    "corrected_rank_tie_expansion_share": corrected_rank_share - fraction,
                    "corrected_exact_fractional_selected_edge_share": exact_share,
                    "fractional_boundary_score": row["fractional_boundary_score"],
                    "fractional_edges_above_boundary": row["fractional_edges_strictly_above_boundary"],
                    "fractional_boundary_tie_edges": row["fractional_boundary_tie_edges"],
                    "fractional_boundary_weight": row["fractional_boundary_weight"],
                    "frozen_score": float(base["value"]),
                    "corrected_exact_fractional_score": score,
                    "score_delta_vs_frozen": score_delta,
                }
            else:
                if base["defined"]:
                    raise ValueError(f"frozen T4 definedness mismatch for {row['GEOID']}")
                item = {
                    "GEOID": row["GEOID"], "region": region, "defined": False,
                    "total_edges": 0, "frozen_rank_1based": None, "corrected_rank_1based": None,
                    "frozen_selected_edge_share": None, "corrected_rank_inclusive_selected_edges": 0,
                    "corrected_rank_inclusive_selected_edge_share": None,
                    "corrected_rank_inclusive_edge_share_error": None,
                    "corrected_rank_tie_expansion_share": None,
                    "corrected_exact_fractional_selected_edge_share": None,
                    "fractional_boundary_score": None, "fractional_edges_above_boundary": 0,
                    "fractional_boundary_tie_edges": 0, "fractional_boundary_weight": 0.0,
                    "frozen_score": float(base["value"]),
                    "corrected_exact_fractional_score": None, "score_delta_vs_frozen": None,
                }
            all_rows.append(item)
        del inputs, roads
    if len(all_rows) != 9379 or [row["GEOID"] for row in all_rows] != [g for g, _ in pairs]:
        raise ValueError("derived T4 view misses or reorders an official GEOID")
    if sum(bool(row["defined"]) for row in all_rows) != 9373:
        raise ValueError("T4 definedness is not exactly 9,373 defined / 6 undefined")
    if not all(row["frozen_selected_edge_share"] > 0.15 for row in all_rows if row["defined"]):
        raise ValueError("frozen measure-before rank direction finding no longer reconciles")
    regional = {region: summarize_region([row for row in all_rows if row["region"] == region]) for region in REGIONS}
    exact_values = [row["corrected_exact_fractional_selected_edge_share"] for row in all_rows if row["defined"]]
    if any(abs(float(value) - fraction) > TOL for value in exact_values):
        raise AssertionError("exact fractional selected edge share exceeds tolerance")
    report = {
        "schema_version": 1,
        "analysis_id": "BIAS60_T4_TOP_DECILE_RANK_DIRECTION_FIX_V1",
        "measure_before": {
            "official_geoids": 9379, "defined": 9373, "undefined": 6,
            "frozen_T4_artifact_sha256": t4_summary["sha256"],
            "frozen_rank_rule": "ceil(n*(1-top_fraction)) accumulated descending, then include all scores at/above boundary",
            "frozen_defined_tracts_selected_over_15pct": 9373,
            "frozen_selected_edge_share_minimum": min(row["frozen_selected_edge_share"] for row in all_rows if row["defined"]),
            "frozen_id_order_and_values_reconciled": True,
        },
        "method": {
            "weight": "endpoint connector incidence degree(u)*degree(v), eligible provided road classes from frozen T4 recipe",
            "fraction": fraction,
            "rank_direction_fix": "descending accumulation uses target rank ceil(n*top_fraction), not ceil(n*(1-top_fraction))",
            "corrected_score_rule": "exact fractional highest top_fraction edge mass; all score groups above boundary plus a uniform fractional weight on the boundary tie group, applied to total and non-named clipped lengths",
            "tie_diagnostic_rule": "separately compute corrected-direction integer-rank inclusive share; its excess above top_fraction is tie expansion, not rank-direction error",
            "frozen_score_delta": "corrected exact-fractional T4 gap minus frozen T4 gap",
            "tolerance": TOL,
        },
        "measure_after": {
            "official_geoids": len(all_rows), "unique_geoids": len({row["GEOID"] for row in all_rows}),
            "missing_or_extra_geoids": 0, "undefined_preserved": sum(not row["defined"] for row in all_rows),
            "defined": sum(row["defined"] for row in all_rows),
            "corrected_exact_fractional_output_sha256": output_sha256(all_rows),
            "exact_top_fraction_share_max_abs_error": max(abs(float(v) - fraction) for v in exact_values),
            "by_region": regional,
            "tract_results": all_rows,
        },
        "provenance": {
            "corrected_screen_code_sha256": prior.sha256_file(Path(__file__).resolve()),
            "frozen_T4_artifact": {"path": str(frozen_path), "sha256": t4_summary["sha256"], "rows": t4_summary["rows"]},
            "prior_tie_audit": {"path": str(PRIOR_REPORT_PATH), "sha256": prior.sha256_file(PRIOR_REPORT_PATH)},
            "family_summary": {"path": str(FAMILY_SUMMARY_PATH), "sha256": prior.sha256_file(FAMILY_SUMMARY_PATH)},
            "harness_config": {"path": "config/bias60-harness-preregistered.json", "sha256": config_hash},
            "official_sample_sha256": prior.SAMPLE_SHA256,
            "provided_road_inputs": road_inputs,
        },
        "safety": {
            "candidate_artifacts_modified": False, "frozen_T4_artifact_modified": False,
            "labels_or_reference_data_read": False, "removed_or_equivalent_layers_read": False,
            "external_data_read": False, "leaderboard_or_scores_accessed": False,
            "target_fitting": False, "uploads": 0,
        },
        "decision": {
            "screen_pass": True,
            "interpretation": "Direction bug is reproduced and corrected in a non-mutating derived view. Exact fractional top-decile selection passes the 1e-12 toy and real-output boundary checks. Corrected inclusive rank still expands at ties; tie expansion is reported separately.",
            "promotion": "not authorized; this is an estimator bug screen and does not establish target accuracy or authorize candidate mutation/integration",
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    report = run(args.workspace, args.out)
    print(json.dumps({"report": str(args.out), "defined": report["measure_after"]["defined"],
                      "undefined": report["measure_after"]["undefined_preserved"],
                      "output_sha256": report["measure_after"]["corrected_exact_fractional_output_sha256"],
                      "region_scores": {r: report["measure_after"]["by_region"][r]["exact_fractional_score_delta_vs_frozen"]
                                        for r in REGIONS}}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
