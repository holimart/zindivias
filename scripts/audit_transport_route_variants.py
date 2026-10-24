#!/usr/bin/env python3
"""Compare the preregistered Overture route-denominator variants by region.

Reads only official sample GEOIDs, permitted workspace feature-cache columns,
existing v0 defined flags, and aggregate disclosed context from the route audit.
No target labels, sample score placeholders, reference layers, or LB values are read.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
VARIANTS = (
    {"id": "state_us_interstate", "label": "state + US + Interstate only",
     "route_column": "overture_route_core_km",
     "families": ["state", "us", "interstate"]},
    {"id": "plus_fm_rm", "label": "state + US + Interstate + FM/RM",
     "route_column": "overture_route_plus_fm_rm_km",
     "families": ["state", "us", "interstate", "fm_rm"]},
    {"id": "plus_fm_rm_cr", "label": "state + US + Interstate + FM/RM + CR",
     "route_column": "overture_route_km",
     "families": ["state", "us", "interstate", "fm_rm", "county"]},
)
DISCLOSED_ANY_UNDEFINED = {
    "maricopa-az": 0.55,
    "northern-ca": 0.37,
    "eastern-ok": 0.21,
    "south-central-tx": 0.28,
}
DISCLOSED_RATIO_BAND = (0.71, 1.59)
LANE_RATIO_BAND = (0.60, 1.70)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_official_ids(path: Path) -> list[str]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader, [])
        if "GEOID" not in header:
            raise ValueError(f"official sample has no GEOID column: {path}")
        index = header.index("GEOID")
        ids = [row[index] for row in reader]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate official GEOID in {path}")
    return ids


def read_v0_flags(path: Path) -> dict[str, dict[str, int]]:
    """Read only GEOID and defined flags from the local estimator output."""
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader, [])
        wanted = ("GEOID", "transport_defined", "building_defined", "poi_defined")
        if any(name not in header for name in wanted):
            raise ValueError(f"v0 estimate file is missing required defined flags: {path}")
        indexes = [header.index(name) for name in wanted]
        result = {}
        for row in reader:
            geoid, *flags = (row[index] for index in indexes)
            if geoid in result:
                raise ValueError(f"duplicate GEOID in v0 estimates: {geoid}")
            parsed = [int(value) for value in flags]
            if any(value not in (0, 1) for value in parsed):
                raise ValueError(f"non-binary defined flag for {geoid}")
            result[geoid] = dict(zip(wanted[1:], parsed))
    return result


def distance_to_band(value: float, band: tuple[float, float]) -> float:
    return max(band[0] - value, 0.0, value - band[1])


def compare_variant(route_values: np.ndarray, named_values: np.ndarray,
                    v0_flags: list[dict[str, int]], disclosed_any: float) -> dict:
    if (not np.isfinite(route_values).all() or np.any(route_values < 0) or
            not np.isfinite(named_values).all() or np.any(named_values < 0)):
        raise ValueError("route or named feature contains a negative or non-finite value")
    route_sum = float(route_values.sum())
    named_sum = float(named_values.sum())
    if route_sum <= 0:
        raise ValueError("variant has zero total route length")
    route_defined = route_values > 0
    any_defined = np.asarray([
        bool(route_defined[index] and flags["building_defined"] and flags["poi_defined"])
        for index, flags in enumerate(v0_flags)
    ], dtype=bool)
    any_share = float((~any_defined).mean())
    ratio = named_sum / route_sum
    return {
        "L_named_km_sum_epsg5070": named_sum,
        "L_route_km_sum_epsg5070": route_sum,
        "sum_L_named_over_sum_L_route": ratio,
        "ratio_within_disclosed_0_71_to_1_59": bool(
            DISCLOSED_RATIO_BAND[0] <= ratio <= DISCLOSED_RATIO_BAND[1]),
        "distance_to_disclosed_ratio_band": distance_to_band(ratio, DISCLOSED_RATIO_BAND),
        "ratio_within_lane_0_60_to_1_70": bool(
            LANE_RATIO_BAND[0] <= ratio <= LANE_RATIO_BAND[1]),
        "route_undefined_count": int((~route_defined).sum()),
        "route_undefined_share": float((~route_defined).mean()),
        "variant_any_component_undefined_count": int((~any_defined).sum()),
        "variant_any_component_undefined_share": any_share,
        "disclosed_any_component_undefined_share": disclosed_any,
        "any_undefined_abs_error": abs(any_share - disclosed_any),
        "any_undefined_within_0_10": bool(abs(any_share - disclosed_any) <= 0.10),
    }


def select_variant(summaries: dict[str, dict]) -> dict:
    """Use disclosed aggregate criteria only; ratio-band coverage is primary."""
    ranking = []
    for variant in VARIANTS:
        variant_id = variant["id"]
        rows = summaries[variant_id]
        all_any_pass = all(row["any_undefined_within_0_10"] for row in rows.values())
        ratios_in_disclosed = sum(row["ratio_within_disclosed_0_71_to_1_59"]
                                  for row in rows.values())
        ratios_in_lane = sum(row["ratio_within_lane_0_60_to_1_70"]
                             for row in rows.values())
        ratio_distance = sum(row["distance_to_disclosed_ratio_band"]
                             for row in rows.values())
        any_mae = float(np.mean([row["any_undefined_abs_error"]
                                 for row in rows.values()]))
        # Choose the variant that fits the disclosed ratio band in most regions;
        # then minimize band distance, then any-share MAE. No label/LB score enters.
        ranking.append({
            "variant_id": variant_id,
            "all_four_any_undefined_checks_pass": all_any_pass,
            "regions_within_disclosed_ratio_band": ratios_in_disclosed,
            "regions_within_lane_ratio_band": ratios_in_lane,
            "sum_distance_to_disclosed_ratio_band": ratio_distance,
            "mean_absolute_any_undefined_error": any_mae,
        })
    ranking.sort(key=lambda row: (
        not row["all_four_any_undefined_checks_pass"],
        -row["regions_within_disclosed_ratio_band"],
        row["sum_distance_to_disclosed_ratio_band"],
        row["mean_absolute_any_undefined_error"],
        next(i for i, item in enumerate(VARIANTS) if item["id"] == row["variant_id"]),
    ))
    return {"chosen_variant": ranking[0]["variant_id"], "ranking": ranking}


def run(workspace: Path, repo: Path, v0_path: Path, route_audit_path: Path,
        output_path: Path) -> dict:
    feature_root = workspace / "gap-estimator" / "features"
    sample_root = workspace / "defined-flags-inputs"
    v0_flags_by_id = read_v0_flags(v0_path)
    prior_route_audit = json.loads(route_audit_path.read_text(encoding="utf-8"))
    if prior_route_audit.get("rows") != 9379:
        raise ValueError("route audit does not report the 9,379 official tracts")

    region_results: dict[str, dict] = {}
    variant_results = {variant["id"]: {} for variant in VARIANTS}
    cache_hashes: dict[str, str] = {}
    all_ids: list[str] = []
    for region in REGIONS:
        sample_path = sample_root / f"{region}-sample-submission.csv"
        ids = read_official_ids(sample_path)
        if set(ids) & set(all_ids):
            raise ValueError(f"official GEOIDs overlap between regions at {region}")
        all_ids.extend(ids)
        missing_flags = set(ids) - set(v0_flags_by_id)
        if missing_flags:
            raise ValueError(f"v0 defined flags missing official IDs in {region}: {len(missing_flags)}")
        flags = [v0_flags_by_id[geoid] for geoid in ids]
        feature_path = feature_root / f"{region}-overture-tract-summary.parquet"
        required_columns = ["GEOID", "overture_named_road_km_5070"] + [
            variant["route_column"] for variant in VARIANTS
        ]
        table = pq.read_table(feature_path, columns=list(dict.fromkeys(required_columns)))
        feature_ids = [str(value) for value in table.column("GEOID").to_pylist()]
        if feature_ids != ids:
            raise ValueError(f"feature cache IDs/order do not match official sample for {region}")
        named = np.asarray(table.column("overture_named_road_km_5070").to_numpy(), dtype=float)
        disclosed = prior_route_audit["regions"][region][
            "disclosed_any_component_undefined_share"]
        if disclosed != DISCLOSED_ANY_UNDEFINED[region]:
            raise ValueError(f"disclosed any-undefined context changed for {region}")
        rows = {}
        for variant in VARIANTS:
            route = np.asarray(table.column(variant["route_column"]).to_numpy(), dtype=float)
            result = compare_variant(route, named, flags, disclosed)
            variant_results[variant["id"]][region] = result
            rows[variant["id"]] = result
        region_results[region] = {
            "official_tracts": len(ids),
            "v0_any_component_undefined_share": float(np.mean([
                not (row["transport_defined"] and row["building_defined"] and row["poi_defined"])
                for row in flags
            ])),
            "variants": rows,
        }
        cache_hashes[region] = sha256(feature_path)

    if len(all_ids) != 9379 or set(all_ids) != set(v0_flags_by_id):
        raise ValueError("official sample GEOIDs and v0 flags must exactly match 9,379 rows")
    selection = select_variant(variant_results)
    chosen = next(item for item in VARIANTS if item["id"] == selection["chosen_variant"])
    report = {
        "title": "Overture route-reference transport denominator sensitivity",
        "rows": len(all_ids),
        "variant_definitions": [
            {"id": variant["id"], "label": variant["label"],
             "route_length_feature": variant["route_column"],
             "included_network_families": variant["families"],
             "formula": "clip(1 - L_named/L_route, 0, 1); undefined when L_route=0"}
            for variant in VARIANTS
        ],
        "ratio_bands": {
            "organizer_disclosed_cross_region_context": list(DISCLOSED_RATIO_BAND),
            "lane_acceptance_check": list(LANE_RATIO_BAND),
            "interpretation": "The organizer disclosed a cross-region range, not region-specific target ratios; in-band counts/distances are comparison diagnostics only.",
        },
        "selection_rule": {
            "criterion_order": [
                "require all four hybrid any-undefined checks within 0.10 when possible",
                "maximize number of regional ratios within the disclosed 0.71-1.59 band",
                "minimize summed distance outside that band",
                "minimize macro mean absolute error to disclosed any-undefined shares",
            ],
            "scores_or_labels_used": False,
            "leaderboard_used": False,
            "selection": selection,
            "chosen_variant": chosen["id"],
            "chosen_label": chosen["label"],
            "rationale": "All three variants pass the four regional any-undefined tolerances. The chosen cumulative state+US+I+FM/RM+CR variant places the most regions within the disclosed ratio band; adding FM/RM brings Texas into band, and adding explicit CR brings Northern California into band. Maricopa remains above band. This choice prioritizes aggregate ratio-band fit while retaining the stated undefined-share tolerance; it is not an accuracy or leaderboard result.",
            "tradeoff": "The state+US+I-only variant has the smallest mean absolute any-undefined error, but only one region ratio is inside the disclosed range. The selected full variant's mean absolute any-undefined error is reported rather than hidden.",
        },
        "regions": region_results,
        "aggregate_variant_metrics": selection["ranking"],
        "data_use": {
            "feature_columns_read": ["GEOID", "overture_named_road_km_5070",
                                     "overture_route_core_km", "overture_route_plus_fm_rm_km",
                                     "overture_route_km"],
            "v0_columns_read": ["GEOID", "transport_defined", "building_defined", "poi_defined"],
            "official_sample_columns_read": ["GEOID"],
            "removed_reference_layers_or_equivalents_read": False,
            "labels_or_sample_score_placeholders_read": False,
            "leaderboard_or_submission_scores_read": False,
            "disclosed_aggregate_statistics_used_only_for_variant_selection": True,
            "per_geoid_outputs_written": False,
            "no_candidate_or_submission_created": True,
        },
        "inputs": {
            "v0_defined_flags": {"path": str(v0_path.relative_to(repo)), "sha256": sha256(v0_path)},
            "route_reference_audit": {"path": str(route_audit_path.relative_to(repo)),
                                       "sha256": sha256(route_audit_path)},
            "feature_cache_sha256_by_region": cache_hashes,
        },
        "limitations": [
            "Disclosed ratio endpoints are not mapped to individual regions, so band fit is not a comparison to region-specific reference labels.",
            "Hybrid any-undefined shares replace only the transport defined flag while retaining v0 building/POI defined flags; they are not a full v2 composite.",
            "Aggregate undefined percentages are used only as the row-authorized selection context, not as per-tract labels or score-fitting targets.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        help="authorized workspace root; defaults to COMPETITION_WORKSPACE")
    parser.add_argument("--v0-estimates", type=Path, default=Path("reports/gap-estimates.csv"))
    parser.add_argument("--route-audit", type=Path, default=Path("reports/transport-route-ref-audit.json"))
    parser.add_argument("--out", type=Path, default=Path("reports/transport-route-variants.json"))
    args = parser.parse_args()
    workspace = args.workspace or (Path(os.environ["COMPETITION_WORKSPACE"])
                                   if os.environ.get("COMPETITION_WORKSPACE") else None)
    if workspace is None:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    repo = Path(__file__).resolve().parents[1]
    report = run(workspace.resolve(), repo, args.v0_estimates.resolve(),
                 args.route_audit.resolve(), args.out.resolve())
    print(f"route variant report: {args.out.resolve()}")
    print(f"chosen variant: {report['selection_rule']['chosen_variant']}")
    for variant in report["aggregate_variant_metrics"]:
        print(json.dumps(variant, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
