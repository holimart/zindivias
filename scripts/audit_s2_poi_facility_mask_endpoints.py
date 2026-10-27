#!/usr/bin/env python3
"""Audit S2's zero-count POI-facility mask using frozen, feature-only arrays.

Compares the frozen S2 rule (drop a fire/EMS/school type when its provided
Overture count is zero) with a counterfactual that retains every frozen-defined
type, including zero-count types. All other S2 components and inclusion masks
are held fixed. No labels, organizer references, target fitting, scorecards, or
leaderboard data are read; S2 and its registry entry are never modified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
REGION_COUNTS = {"maricopa-az": 1593, "northern-ca": 591, "eastern-ok": 1192, "south-central-tx": 6003}
S2_ID = "S2_h1b_assumption_bound_v1"
S2_PATH = ROOT / "reports/candidates/S2_h1b_assumption_bound_v1.csv"
S2_SHA256 = "44ba76abcd9e1fb0aa7194e3c0fadf567debce40e29ed4bdf93ef02ae9514577"
S2_PREREG = ROOT / "config/shrink-s2-assumption-bound-preregistered.json"
S2_PREREG_SHA256 = "d0a85a584dd80990a9a81022f82b66f653250dac097832669676384ddae5d479"
H1B_PREREG = ROOT / "config/shrink-h1b-rule-level-preregistered.json"
H1B_PREREG_SHA256 = "d2ca6ad5a6e7701a85068c355598490d9d2444e86e1f9490b0c6b15e39881c9e"
ESTIMATES_PATH = ROOT / "reports/gap-estimates.csv"
REGISTRY_PATH = ROOT / "reports/candidates.json"
OUT_PATH = ROOT / "reports/s2-poi-facility-mask-endpoints.json"
FACILITIES = ("fire", "ems", "schools")
SAMPLE_HASHES = {
    "maricopa-az": "1640b801d56ab308e68c65583b88cfb9e1555b05a6d0b922eaba22ac09960a31",
    "northern-ca": "f38017b5284667b80b451685fd146388c108e1b30308301289c7a96ecfe60902",
    "eastern-ok": "56cb40fba4008d646bc63c62c91e211871459224b3de8b3a886ff813803fd0d4",
    "south-central-tx": "afa59c0eb30c40be25ce82098f144447591b79cb9692e669b2f2fe7b7b1ee58e",
}
SAMPLE_HEADER = "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score"
S2_HEADER = "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score"
TOL = 1e-10


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_official_ids(path: Path) -> list[str]:
    """Read only sample GEOID cells; never inspect supplied score placeholders."""
    ids: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        if stream.readline().rstrip("\r\n") != SAMPLE_HEADER:
            raise ValueError(f"unexpected official sample header: {path}")
        for line_no, line in enumerate(stream, 2):
            if not line.strip():
                continue
            geoid, comma, _unread = line.partition(",")
            geoid = geoid.rstrip("\r\n")
            if not comma or len(geoid) != 11 or not geoid.isdigit():
                raise ValueError(f"invalid official GEOID at {path}:{line_no}")
            ids.append(geoid)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"empty or duplicate official GEOIDs in {path}")
    return ids


def read_s2_official(path: Path, official_ids: set[str]) -> tuple[dict[str, dict[str, float]], int]:
    """Read frozen S2 numeric fields only for official IDs; skip extra-row values."""
    if sha256_file(path) != S2_SHA256:
        raise ValueError("frozen S2 artifact SHA-256 mismatch")
    data: dict[str, dict[str, float]] = {}
    all_ids: set[str] = set()
    total_rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        if stream.readline().rstrip("\r\n") != S2_HEADER:
            raise ValueError(f"unexpected S2 component header: {path}")
        for line_no, raw in enumerate(stream, 2):
            if not raw.strip():
                continue
            geoid, comma, remainder = raw.rstrip("\r\n").partition(",")
            if not comma or not geoid or geoid in all_ids:
                raise ValueError(f"malformed or duplicate S2 GEOID at line {line_no}")
            all_ids.add(geoid)
            total_rows += 1
            if geoid not in official_ids:
                # Do not parse component/composite values outside scored scope.
                continue
            texts = remainder.split(",")
            if len(texts) != 4:
                raise ValueError(f"malformed official S2 row at line {line_no}")
            try:
                values = [float(x) for x in texts]
            except ValueError as exc:
                raise ValueError(f"non-numeric official S2 row at line {line_no}") from exc
            if not all(math.isfinite(x) and 0.0 <= x <= 1.0 for x in values):
                raise ValueError(f"S2 values must be finite and in [0,1] at line {line_no}")
            data[geoid] = dict(zip(("transport_gap", "building_gap", "poi_gap", "coverage_gap_score"), values))
    if total_rows != 9794 or len(all_ids) != 9794 or len(data) != 9379 or set(data) != official_ids:
        raise ValueError("S2 full artifact ID/official scope contract mismatch")
    return data, total_rows


def read_estimates(path: Path, official_ids: set[str]) -> dict[str, dict[str, Any]]:
    """Read only per-type facility/CBP arrays and fixed transport/building flags."""
    columns = ["GEOID", "transport_defined", "building_defined", "cbp_gap", "cbp_defined"]
    for kind in FACILITIES:
        columns.extend([f"{kind}_gap", f"{kind}_defined"])
    out: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if header is None or len(header) != len(set(header)) or any(name not in header for name in columns):
            raise ValueError("gap-estimates file lacks required frozen feature-only arrays")
        ix = {name: header.index(name) for name in columns}
        for line_no, row in enumerate(reader, 2):
            if len(row) != len(header):
                raise ValueError(f"malformed frozen gap-estimate row {line_no}")
            geoid = row[ix["GEOID"]]
            if geoid not in official_ids:
                continue
            if geoid in out:
                raise ValueError(f"duplicate official GEOID in gap estimates: {geoid}")
            record: dict[str, Any] = {}
            for name in columns:
                if name == "GEOID":
                    continue
                text = row[ix[name]]
                if name.endswith("_defined"):
                    try:
                        value = int(text)
                    except ValueError as exc:
                        raise ValueError(f"invalid frozen flag {name} for {geoid}") from exc
                    if value not in (0, 1):
                        raise ValueError(f"nonbinary frozen flag {name} for {geoid}")
                else:
                    try:
                        value = float(text)
                    except ValueError as exc:
                        raise ValueError(f"invalid frozen component {name} for {geoid}") from exc
                    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                        raise ValueError(f"invalid frozen component range {name} for {geoid}")
                record[name] = value
            out[geoid] = record
    if set(out) != official_ids:
        raise ValueError("frozen gap-estimate rows do not match official IDs")
    return out


def read_facility_counts(workspace: Path, ids_by_region: Mapping[str, Sequence[str]], pins: Mapping[str, Any]) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    counts: dict[str, dict[str, float]] = {}
    hashes: dict[str, str] = {}
    for region in REGIONS:
        path = workspace / "gap-estimator/features" / f"{region}-overture-tract-summary.parquet"
        digest = sha256_file(path)
        if digest != pins[region]["sha256"]:
            raise ValueError(f"provided Overture feature-summary hash mismatch for {region}")
        hashes[region] = digest
        table = pq.read_table(path, columns=["GEOID", *(f"overture_poi_{kind}" for kind in FACILITIES)])
        allowed = set(ids_by_region[region])
        geoid_column = table["GEOID"].to_pylist()
        for index, raw_geoid in enumerate(geoid_column):
            geoid = str(raw_geoid)
            if geoid not in allowed:
                continue
            if geoid in counts:
                raise ValueError(f"duplicate official facility-count GEOID: {geoid}")
            values = {}
            for kind in FACILITIES:
                try:
                    value = float(table[f"overture_poi_{kind}"][index].as_py())
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid provided Overture {kind} count for {geoid}") from exc
                if not math.isfinite(value) or value < 0.0:
                    raise ValueError(f"invalid provided Overture {kind} count for {geoid}")
                values[kind] = value
            counts[geoid] = values
    expected = {g for region in REGIONS for g in ids_by_region[region]}
    if set(counts) != expected:
        raise ValueError("provided facility-count IDs do not match official GEOIDs")
    return counts, hashes


def poi_variant(estimates: Mapping[str, Any], counts: Mapping[str, float], *, retain_zero: bool) -> tuple[float, int, dict[str, bool]]:
    """Recompose the S2 POI component with the frozen versus retain-zero mask."""
    included: dict[str, bool] = {}
    facility_gaps: list[float] = []
    for kind in FACILITIES:
        count = float(counts[kind])
        defined = int(estimates[f"{kind}_defined"])
        gap = float(estimates[f"{kind}_gap"])
        keep = bool(defined and (retain_zero or count != 0.0))
        included[kind] = keep
        if keep:
            facility_gaps.append(gap)
    halves: list[float] = []
    if facility_gaps:
        halves.append(sum(facility_gaps) / len(facility_gaps))
    if int(estimates["cbp_defined"]):
        halves.append(float(estimates["cbp_gap"]))
    if not halves:
        return 0.0, 0, included
    return sum(halves) / len(halves), 1, included


def composite_score(s2: Mapping[str, float], estimates: Mapping[str, Any], poi_gap: float, poi_defined: int) -> float:
    parts: list[float] = []
    if int(estimates["transport_defined"]):
        parts.append(float(s2["transport_gap"]))
    elif float(s2["transport_gap"]) != 0.0:
        raise ValueError("undefined S2 transport component is nonzero")
    if int(estimates["building_defined"]):
        parts.append(float(s2["building_gap"]))
    elif float(s2["building_gap"]) != 0.0:
        raise ValueError("undefined S2 building component is nonzero")
    if poi_defined:
        parts.append(float(poi_gap))
    score = sum(parts) / len(parts) if parts else 0.0
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError("scenario composite outside finite [0,1]")
    return score


def linear_quantile(values: Sequence[float], p: float) -> float:
    if not values or not 0.0 <= p <= 1.0:
        raise ValueError("quantile requires values and probability in [0,1]")
    ordered = sorted(float(x) for x in values)
    position = (len(ordered) - 1) * p
    lower, upper = math.floor(position), math.ceil(position)
    frac = position - lower
    return ordered[lower] * (1.0 - frac) + ordered[upper] * frac


def score_vector_hash(geoids: Sequence[str], values: Sequence[float]) -> str:
    if len(geoids) != len(values):
        raise ValueError("scenario vector ID/value length mismatch")
    body = "".join(f"{geoid},{float(value):.17g}\n" for geoid, value in zip(geoids, values))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def summarize_delta(deltas: Sequence[float]) -> dict[str, float | int]:
    absolute = [abs(float(x)) for x in deltas]
    return {
        "n": len(absolute),
        "median_absolute_delta": linear_quantile(absolute, 0.5),
        "p95_absolute_delta": linear_quantile(absolute, 0.95),
        "maximum_absolute_delta": max(absolute),
        "tract_share_absolute_delta_gt_0_05": sum(x > 0.05 for x in absolute) / len(absolute),
    }


def build(workspace: Path) -> tuple[dict[str, Any], str]:
    s2_digest = sha256_file(S2_PATH)
    if s2_digest != S2_SHA256:
        raise ValueError("frozen S2 artifact hash changed")
    if sha256_file(S2_PREREG) != S2_PREREG_SHA256 or sha256_file(H1B_PREREG) != H1B_PREREG_SHA256:
        raise ValueError("frozen S2/H1b preregistration hash mismatch")
    s2_prereg = json.loads(S2_PREREG.read_text(encoding="utf-8"))
    h1b_prereg = json.loads(H1B_PREREG.read_text(encoding="utf-8"))
    if not s2_prereg.get("registered_before_s2_value_computation"):
        raise ValueError("S2 preregistration is not frozen")
    if sha256_file(ESTIMATES_PATH) != s2_prereg["frozen_inputs"]["gap_estimates"]["sha256"]:
        raise ValueError("frozen gap-estimates hash mismatch")
    registry_digest = sha256_file(REGISTRY_PATH)
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    cards = [x for x in registry.get("candidate_set", []) if x.get("candidate_id") == S2_ID]
    if len(cards) != 1 or cards[0].get("sha256") != S2_SHA256 or cards[0].get("upload_authorized") is not False:
        raise ValueError("S2 registry identity, hash, or authorization mismatch")

    ids_by_region: dict[str, list[str]] = {}
    sample_hashes: dict[str, str] = {}
    owner: dict[str, str] = {}
    for region in REGIONS:
        sample = workspace / "samples" / f"{region}-sample-submission.csv"
        digest = sha256_file(sample)
        if digest != SAMPLE_HASHES[region]:
            raise ValueError(f"official sample hash mismatch for {region}")
        ids = read_official_ids(sample)
        if len(ids) != REGION_COUNTS[region]:
            raise ValueError(f"official sample row count mismatch for {region}")
        ids_by_region[region] = ids
        sample_hashes[region] = digest
        for geoid in ids:
            if geoid in owner:
                raise ValueError(f"duplicate official GEOID across regions: {geoid}")
            owner[geoid] = region
    official_ids = set(owner)
    if len(official_ids) != 9379:
        raise ValueError("official ID scope must contain 9,379 unique GEOIDs")

    s2, s2_rows = read_s2_official(S2_PATH, official_ids)
    estimates_hash = sha256_file(ESTIMATES_PATH)
    estimates = read_estimates(ESTIMATES_PATH, official_ids)
    features, feature_hashes = read_facility_counts(
        workspace, ids_by_region, h1b_prereg["pinned_inputs"]["provided_overture_tract_summaries"])

    frozen_scores_by_region: dict[str, list[float]] = {}
    retained_scores_by_region: dict[str, list[float]] = {}
    deltas_by_region: dict[str, list[float]] = {}
    rows_by_region: dict[str, list[dict[str, Any]]] = {}
    all_ordered_ids: list[str] = []
    all_frozen_scores: list[float] = []
    all_retained_scores: list[float] = []
    baseline_count = {k: 0 for k in FACILITIES}
    zero_count = {k: 0 for k in FACILITIES}
    reintroduced_count = {k: 0 for k in FACILITIES}
    retained_new_poi_defined = 0
    for region in REGIONS:
        geoids = ids_by_region[region]
        frozen_scores: list[float] = []
        retained_scores: list[float] = []
        deltas: list[float] = []
        records: list[dict[str, Any]] = []
        for geoid in geoids:
            e, f, base = estimates[geoid], features[geoid], s2[geoid]
            frozen_poi, frozen_poi_defined, frozen_included = poi_variant(e, f, retain_zero=False)
            retain_poi, retain_poi_defined, retain_included = poi_variant(e, f, retain_zero=True)
            # Confirm exact reconstruction of the frozen S2 POI component and composite.
            if abs(frozen_poi - base["poi_gap"]) > TOL:
                raise ValueError(f"frozen-mask POI component does not reconcile to S2 for {geoid}")
            frozen_score = composite_score(base, e, frozen_poi, frozen_poi_defined)
            if abs(frozen_score - base["coverage_gap_score"]) > TOL:
                raise ValueError(f"frozen-mask composite does not reconcile to S2 for {geoid}")
            retained_score = composite_score(base, e, retain_poi, retain_poi_defined)
            delta = retained_score - base["coverage_gap_score"]
            frozen_scores.append(frozen_score)
            retained_scores.append(retained_score)
            deltas.append(delta)
            for kind in FACILITIES:
                baseline_count[kind] += int(frozen_included[kind])
                if f[kind] == 0.0:
                    zero_count[kind] += 1
                    reintroduced_count[kind] += int(retain_included[kind] and not frozen_included[kind])
            if not frozen_poi_defined and retain_poi_defined:
                retained_new_poi_defined += 1
            records.append({
                "GEOID": geoid,
                "frozen_mask_score": frozen_score,
                "retain_zero_components_score": retained_score,
                "delta_retain_zero_minus_frozen": delta,
                "frozen_poi_gap": frozen_poi,
                "frozen_poi_defined": frozen_poi_defined,
                "retain_zero_poi_gap": retain_poi,
                "retain_zero_poi_defined": retain_poi_defined,
                "zero_count_defined_facilities_retained": [k for k in FACILITIES if f[k] == 0.0 and retain_included[k]],
            })
        frozen_scores_by_region[region] = frozen_scores
        retained_scores_by_region[region] = retained_scores
        deltas_by_region[region] = deltas
        rows_by_region[region] = records
        all_ordered_ids.extend(geoids)
        all_frozen_scores.extend(frozen_scores)
        all_retained_scores.extend(retained_scores)
    if len(all_ordered_ids) != 9379 or len(set(all_ordered_ids)) != 9379:
        raise ValueError("scenario IDs do not align exactly to official 9,379-ID scope")

    region_results = {}
    for region in REGIONS:
        frozen_scores = frozen_scores_by_region[region]
        retained_scores = retained_scores_by_region[region]
        deltas = deltas_by_region[region]
        if len(frozen_scores) != REGION_COUNTS[region] or len(retained_scores) != REGION_COUNTS[region]:
            raise ValueError(f"scenario vector count mismatch for {region}")
        region_results[region] = {
            "official_rows": len(ids_by_region[region]),
            "frozen_mask_scenario_sha256": score_vector_hash(ids_by_region[region], frozen_scores),
            "retain_zero_components_scenario_sha256": score_vector_hash(ids_by_region[region], retained_scores),
            "delta_summary_vs_frozen_S2": summarize_delta(deltas),
            "geoids": ids_by_region[region],
            "frozen_mask_scores": frozen_scores,
            "retain_zero_components_scores": retained_scores,
        }

    report = {
        "schema_version": 1,
        "analysis_id": "S2_POI_FACILITY_ZERO_COUNT_MASK_ENDPOINTS_V1",
        "design": {
            "frozen_mask": "include each frozen-defined fire/EMS/school component iff its provided Overture category count is nonzero; compose facilities-half mean and frozen CBP-proxy half exactly as S2",
            "retain_zero_components": "include every frozen-defined fire/EMS/school component irrespective of provided Overture category count, including zero-count types; leave CBP-proxy, transport/building components, and their frozen defined masks unchanged",
            "composite": "arithmetic mean of defined transport, building and POI components; zero fallback only if none is defined",
            "delta": "retained-zero scenario composite minus frozen S2 coverage_gap_score",
            "quantile_method": "linear interpolation at (n-1)*p; threshold is strict absolute delta > 0.05",
        },
        "provenance": {
            "frozen_S2": {"path": "reports/candidates/S2_h1b_assumption_bound_v1.csv", "sha256": s2_digest, "rows": s2_rows, "official_rows_used": 9379},
            "S2_preregistration": {"path": "config/shrink-s2-assumption-bound-preregistered.json", "sha256": sha256_file(S2_PREREG)},
            "H1b_feature_pins": {"path": "config/shrink-h1b-rule-level-preregistered.json", "sha256": sha256_file(H1B_PREREG)},
            "frozen_per_type_components": {"path": "reports/gap-estimates.csv", "sha256": estimates_hash, "columns_used": ["transport_defined", "building_defined", "fire_gap", "fire_defined", "ems_gap", "ems_defined", "schools_gap", "schools_defined", "cbp_gap", "cbp_defined"], "aggregate_score_columns_used": False},
            "provided_Overture_feature_fields": {"columns_used": ["GEOID", "overture_poi_fire", "overture_poi_ems", "overture_poi_schools"], "region_sha256": feature_hashes},
            "official_samples": {"columns_used": ["GEOID"], "region_sha256": sample_hashes},
            "candidate_registry_sha256_before_after": registry_digest,
        },
        "scope": {"official_geoids": len(all_ordered_ids), "unique_geoids": len(set(all_ordered_ids)),
                  "missing_ids": 0, "extra_ids": 0, "duplicate_ids": 0,
                  "region_counts": {r: len(ids_by_region[r]) for r in REGIONS},
                  "score_placeholders_read": False, "out_of_scope_S2_values_parsed": False},
        "scenario_hashes": {
            "serialization": "SHA-256 of UTF-8 concatenated lines `GEOID,<score formatted .17g>\\n` in official regional/sample order",
            "frozen_mask": score_vector_hash(all_ordered_ids, all_frozen_scores),
            "retain_zero_components": score_vector_hash(all_ordered_ids, all_retained_scores),
        },
        "facility_mask_counts": {
            "provided_zero_count_tracts_by_type": zero_count,
            "frozen_mask_included_defined_tract_types_by_type": baseline_count,
            "defined_zero_count_tract_types_reintroduced": reintroduced_count,
            "tracts_with_newly_defined_POI_component": retained_new_poi_defined,
        },
        "per_region": region_results,
        "interpretation": {
            "is_sensitivity_only": True,
            "target_accuracy_identifiable": False,
            "accuracy_claim": False,
            "upload_authorized": False,
            "upload_performed": False,
        },
        "safety": {"S2_candidate_modified": False, "candidate_registry_modified": False,
                    "labels_or_sample_score_placeholders_read": False,
                    "removed_or_equivalent_reference_layers_read": False,
                    "external_data_read": False, "leaderboard_or_scorecards_accessed": False,
                    "target_score_inference_or_fitting": False, "uploads": 0},
    }
    if sha256_file(S2_PATH) != s2_digest or sha256_file(REGISTRY_PATH) != registry_digest:
        raise ValueError("frozen S2 artifact or registry changed during audit")
    encoded = (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    OUT_PATH.write_bytes(encoded)
    return report, hashlib.sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    report, digest = build(args.workspace)
    summaries = {region: report["per_region"][region]["delta_summary_vs_frozen_S2"] for region in REGIONS}
    print(json.dumps({"report": str(OUT_PATH), "sha256": digest,
                      "official_geoids": report["scope"]["official_geoids"],
                      "scenario_hashes": report["scenario_hashes"], "per_region": summaries}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
