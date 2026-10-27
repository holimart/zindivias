#!/usr/bin/env python3
"""Measure S2 sensitivity to perturbing its assumed Microsoft overlap share.

This is a feature-only diagnostic. It never modifies S2/C2, reads score
placeholders, uses labels/references, or accesses leaderboard information. The
only candidate score values used are official-scope S2/C2 component outputs;
source-share and the frozen definedness inputs needed to preserve S2's
composite divisor come from supplied feature data and the already-frozen
feature-only recipe.
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
OFFSETS = (-0.10, -0.05, 0.0, 0.05, 0.10)
REGION_COUNTS = {"maricopa-az": 1593, "northern-ca": 591, "eastern-ok": 1192, "south-central-tx": 6003}
S2_ID = "S2_h1b_assumption_bound_v1"
S2_PATH = ROOT / f"reports/candidates/{S2_ID}.csv"
S2_SHA256 = "44ba76abcd9e1fb0aa7194e3c0fadf567debce40e29ed4bdf93ef02ae9514577"
C2_PATH = ROOT / "reports/candidates/C2_component_estimator_v0.csv"
C2_SHA256 = "5728fbbaa2e062fbd14199190a260132c95b2b858da539d7a9580cb2abc62514"
GAP_PATH = ROOT / "reports/gap-estimates.csv"
GAP_SHA256 = "0e2b94210fbd3e262c847dc4d2af364eeb4186f35361982bad51dd2e780c8852"
H1B_PREREG = ROOT / "config/shrink-h1b-rule-level-preregistered.json"
S2_PREREG = ROOT / "config/shrink-s2-assumption-bound-preregistered.json"
REGISTRY_PATH = ROOT / "reports/candidates.json"
OUT_PATH = ROOT / "reports/s2-building-overlap-sensitivity.json"
SAMPLE_HASHES = {
    "maricopa-az": "1640b801d56ab308e68c65583b88cfb9e1555b05a6d0b922eaba22ac09960a31",
    "northern-ca": "f38017b5284667b80b451685fd146388c108e1b30308301289c7a96ecfe60902",
    "eastern-ok": "56cb40fba4008d646bc63c62c91e211871459224b3de8b3a886ff813803fd0d4",
    "south-central-tx": "afa59c0eb30c40be25ce82098f144447591b79cb9692e669b2f2fe7b7b1ee58e",
}
SCORE_COLUMNS = ("transport_gap", "building_gap", "poi_gap", "coverage_gap_score")
SCORE_HEADER = ["GEOID", *SCORE_COLUMNS]
SAMPLE_HEADER = "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score"
FEATURE_COLUMNS = ("GEOID", "overture_building_microsoft_source_share", "overture_poi_fire", "overture_poi_ems", "overture_poi_schools")
MASK_COLUMNS = ("transport_defined", "building_defined", "poi_defined", "fire_defined", "ems_defined", "schools_defined", "cbp_defined")
FACILITIES = ("fire", "ems", "schools")
TOL = 1e-10


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_sample_ids(path: Path) -> list[str]:
    """Read only the GEOID field; sample score placeholders are never parsed."""
    ids = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        if f.readline().rstrip("\r\n") != SAMPLE_HEADER:
            raise ValueError(f"unexpected official sample header: {path}")
        for line_no, line in enumerate(f, 2):
            if not line.strip():
                continue
            geoid, comma, _unused = line.partition(",")
            geoid = geoid.rstrip("\r\n")
            if not comma or len(geoid) != 11 or not geoid.isdigit():
                raise ValueError(f"invalid GEOID field at {path}:{line_no}")
            ids.append(geoid)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"empty/duplicate official IDs in {path}")
    return ids


def read_candidate(path: Path, expected_sha: str, official_ids: set[str]) -> tuple[dict[str, dict[str, float]], list[str]]:
    if sha256_file(path) != expected_sha:
        raise ValueError(f"frozen candidate hash mismatch: {path}")
    rows: dict[str, dict[str, float]] = {}
    full_order: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        if f.readline().rstrip("\r\n") != ",".join(SCORE_HEADER):
            raise ValueError(f"unexpected component artifact header: {path}")
        for line_no, raw in enumerate(f, 2):
            geoid, comma, remaining = raw.rstrip("\r\n").partition(",")
            if not comma or not geoid or geoid in seen:
                raise ValueError(f"empty/duplicate ID or malformed row in {path}:{line_no}")
            full_order.append(geoid)
            seen.add(geoid)
            if geoid not in official_ids:
                # Formatting-only component values are intentionally not parsed.
                continue
            values = remaining.split(",")
            if len(values) != len(SCORE_COLUMNS):
                raise ValueError(f"wrong official score width in {path}:{line_no}")
            parsed = {}
            for name, text in zip(SCORE_COLUMNS, values):
                try:
                    value = float(text)
                except ValueError as exc:
                    raise ValueError(f"invalid {name} in {path}:{line_no}") from exc
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError(f"non-finite/out-of-range {name} in {path}:{line_no}")
                parsed[name] = value
            rows[geoid] = parsed
    if len(full_order) != 9794 or len(seen) != 9794:
        raise ValueError(f"expected 9,794 unique rows in {path}")
    if set(rows) != official_ids or len(rows) != 9379:
        raise ValueError(f"official candidate coverage mismatch in {path}")
    return rows, full_order


def read_masks(path: Path, official_ids: set[str]) -> dict[str, dict[str, int]]:
    """Read only frozen definedness flags; no component estimates are consumed."""
    out: dict[str, dict[str, int]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None or len(header) != len(set(header)):
            raise ValueError("invalid gap-estimate header")
        required = ("GEOID", *MASK_COLUMNS)
        if any(name not in header for name in required):
            raise ValueError("gap-estimate file lacks frozen definedness masks")
        gi = header.index("GEOID")
        indices = {name: header.index(name) for name in MASK_COLUMNS}
        for line_no, row in enumerate(reader, 2):
            if len(row) != len(header):
                raise ValueError(f"malformed mask row at line {line_no}")
            geoid = row[gi]
            if geoid not in official_ids:
                continue
            if geoid in out:
                raise ValueError(f"duplicate official mask GEOID at line {line_no}")
            flags = {}
            for name, i in indices.items():
                try:
                    value = int(row[i])
                except ValueError as exc:
                    raise ValueError(f"invalid definedness flag {name} at line {line_no}") from exc
                if value not in (0, 1):
                    raise ValueError(f"non-binary definedness flag {name} at line {line_no}")
                flags[name] = value
            out[geoid] = flags
    if set(out) != official_ids:
        raise ValueError("frozen definedness-mask IDs do not match official IDs")
    return out


def read_features(workspace: Path, ids_by_region: Mapping[str, Sequence[str]], pins: Mapping[str, Any]) -> tuple[dict[str, dict[str, float | None]], dict[str, str]]:
    features: dict[str, dict[str, float | None]] = {}
    hashes: dict[str, str] = {}
    for region in REGIONS:
        path = workspace / "gap-estimator/features" / f"{region}-overture-tract-summary.parquet"
        actual_hash = sha256_file(path)
        if actual_hash != pins[region]["sha256"]:
            raise ValueError(f"provided feature-summary hash mismatch for {region}")
        hashes[region] = actual_hash
        table = pq.read_table(path, columns=list(FEATURE_COLUMNS))
        allowed = set(ids_by_region[region])
        geoid_values = table["GEOID"].to_pylist()
        share_values = table["overture_building_microsoft_source_share"].to_pylist()
        facility_values = {kind: table[f"overture_poi_{kind}"].to_pylist() for kind in FACILITIES}
        for i, geoid in enumerate(geoid_values):
            if geoid not in allowed:
                continue
            if geoid in features:
                raise ValueError(f"duplicate official feature GEOID {geoid}")
            share_raw = share_values[i]
            share = None if share_raw is None else float(share_raw)
            if share is not None and not math.isfinite(share):
                share = None
            if share is not None and not 0 <= share <= 1:
                raise ValueError(f"Microsoft source share outside [0,1] for {geoid}")
            entry: dict[str, float | None] = {"source_share": share}
            for kind in FACILITIES:
                raw = facility_values[kind][i]
                try:
                    count = float(raw)
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"invalid provided {kind} count for {geoid}") from exc
                if not math.isfinite(count) or count < 0:
                    raise ValueError(f"invalid provided {kind} count for {geoid}")
                entry[f"poi_{kind}"] = count
            features[geoid] = entry
    expected_ids = set().union(*(set(ids_by_region[r]) for r in REGIONS))
    if set(features) != expected_ids:
        raise ValueError("provided feature input IDs do not match official IDs")
    return features, hashes


def derive_poi_defined(mask: Mapping[str, int], feature: Mapping[str, float | None]) -> int:
    facilities_defined = any(
        float(feature[f"poi_{kind}"]) != 0.0 and bool(mask[f"{kind}_defined"])
        for kind in FACILITIES
    )
    return int(facilities_defined or bool(mask["cbp_defined"]))


def scenario_score(c2_building: float, share: float | None, delta: float,
                   components: Mapping[str, float], defined: Mapping[str, int]) -> float:
    if not math.isfinite(delta):
        raise ValueError("share perturbation must be finite")
    adjusted_building = c2_building if share is None else c2_building * (1.0 - min(1.0, max(0.0, share + delta)))
    values = {
        "transport": float(components["transport_gap"]),
        "building": adjusted_building,
        "poi": float(components["poi_gap"]),
    }
    used = [values[name] for name in ("transport", "building", "poi") if defined[name]]
    score = sum(used) / len(used) if used else 0.0
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("scenario score outside finite [0,1]")
    return score


def linear_quantile(values: Sequence[float], probability: float) -> float:
    if not values or not 0 <= probability <= 1:
        raise ValueError("quantile requires values and probability in [0,1]")
    ordered = sorted(float(v) for v in values)
    position = (len(ordered) - 1) * probability
    lo = int(math.floor(position))
    hi = int(math.ceil(position))
    fraction = position - lo
    return ordered[lo] * (1.0 - fraction) + ordered[hi] * fraction


def summarize_deltas(deltas: Sequence[float]) -> dict[str, float | int]:
    values = [abs(float(x)) for x in deltas]
    return {
        "n": len(values),
        "median_abs_delta": linear_quantile(values, 0.5),
        "p95_abs_delta": linear_quantile(values, 0.95),
        "max_abs_delta": max(values),
        "share_abs_delta_gt_0_05": sum(x > 0.05 for x in values) / len(values),
    }


def make_report(ids_by_region: Mapping[str, Sequence[str]],
                source_records: Mapping[str, Mapping[str, Any]],
                feature_records: Mapping[str, Mapping[str, Any]],
                masks: Mapping[str, Mapping[str, int]],
                *, input_hashes: Mapping[str, str], registry_sha256: str,
                sample_hashes: Mapping[str, str], feature_hashes: Mapping[str, str]) -> dict[str, Any]:
    curves: dict[str, Any] = {}
    p95_values = []
    missing_shares: dict[str, int] = {}
    for region in REGIONS:
        geoids = list(ids_by_region[region])
        region_scenarios: dict[str, Any] = {}
        missing_shares[region] = 0
        for delta in OFFSETS:
            vectors: list[float] = []
            deltas: list[float] = []
            for geoid in geoids:
                s2 = source_records[geoid]["s2"]
                c2 = source_records[geoid]["c2"]
                feature = feature_records[geoid]
                share = feature["source_share"]
                if share is None:
                    missing_shares[region] += int(delta == OFFSETS[0])
                defined = {
                    "transport": int(masks[geoid]["transport_defined"]),
                    "building": int(masks[geoid]["building_defined"]),
                    "poi": derive_poi_defined(masks[geoid], feature),
                }
                score = scenario_score(c2["building_gap"], share, delta, s2, defined)
                if delta == 0 and abs(score - s2["coverage_gap_score"]) > TOL:
                    raise ValueError(f"zero-offset recomposition differs from frozen S2 for {geoid}")
                vectors.append(score)
                deltas.append(score - s2["coverage_gap_score"])
            stats = summarize_deltas(deltas)
            p95_values.append(stats["p95_abs_delta"])
            region_scenarios[f"{delta:+.2f}"] = {
                "share_offset": delta,
                "clipping": "clip(original share + offset, 0, 1); missing share leaves frozen C2 building component unchanged",
                "summary": stats,
                "score_vector": vectors,
            }
        curves[region] = {"official_rows": len(geoids), "geoids": geoids, "scenarios": region_scenarios}
    return {
        "schema_version": 1,
        "analysis_id": "S2_BUILDING_OVERLAP_ASSUMPTION_SENSITIVITY_V1",
        "candidate_id": S2_ID,
        "sensitivity_design": {
            "offsets": list(OFFSETS),
            "share_rule": "For finite provided share s, recompute building_gap = frozen C2 building_gap * (1 - clip(s + offset, 0, 1)); missing share preserves the frozen C2 building component. Other frozen S2 components and S2 defined masks are held fixed.",
            "composite_rule": "Arithmetic mean over the frozen S2 defined transport/building/POI components, with zero fallback only if no component is defined.",
            "delta_metric": "scenario composite score minus frozen S2 coverage_gap_score, summarized as absolute deltas",
            "quantile_method": "linear interpolation at (n-1)*p",
            "materiality_rule": "material if any region-offset p95 absolute score delta exceeds 0.05",
        },
        "inputs": {
            "S2_artifact": {"path": "reports/candidates/S2_h1b_assumption_bound_v1.csv", "sha256": input_hashes["S2"]},
            "C2_artifact": {"path": "reports/candidates/C2_component_estimator_v0.csv", "sha256": input_hashes["C2"]},
            "definedness_masks": {"path": "reports/gap-estimates.csv", "sha256": input_hashes["definedness_masks"], "columns_used": list(MASK_COLUMNS), "score_values_used": False, "use": "preserve the frozen S2 component inclusion masks and validate the unchanged C2 composite divisor"},
            "provided_feature_fields": {"columns_used": list(FEATURE_COLUMNS), "region_file_sha256": dict(feature_hashes), "use": "perturb only overture_building_microsoft_source_share; three facility counts are consulted only to reconstruct the already-frozen S2 POI definedness mask, held constant across scenarios"},
            "official_samples": {"columns_used": ["GEOID"], "region_file_sha256": dict(sample_hashes)},
            "candidate_registry_sha256": registry_sha256,
        },
        "scope": {"official_tracts": sum(len(ids_by_region[r]) for r in REGIONS),
                  "region_counts": {r: len(ids_by_region[r]) for r in REGIONS},
                  "unique_geoids": sum(len(ids_by_region[r]) for r in REGIONS),
                  "missing_ids": 0, "extra_ids": 0, "duplicate_ids": 0,
                  "missing_share_rows_by_region": missing_shares},
        "curve_by_region": curves,
        "interpretation": {
            "materiality": "material" if any(value > 0.05 for value in p95_values) else "not_material_by_preregistered_threshold",
            "max_region_offset_p95_abs_delta": max(p95_values),
            "target_accuracy_identifiable": False,
            "target_accuracy_claim": False,
            "descriptive_only": True,
            "upload_authorized": False,
            "upload_performed": False,
        },
        "safety": {
            "candidate_source_modified": False,
            "candidate_registry_modified": False,
            "leaderboard_or_scorecard_accessed": False,
            "labels_or_sample_score_placeholders_read": False,
            "removed_or_equivalent_references_read": False,
            "external_data_read": False,
            "target_score_inference_performed": False,
            "upload_authorized": False,
            "upload_performed": False,
        },
    }


def build(workspace: Path) -> tuple[dict[str, Any], str]:
    s2_prereg = json.loads(S2_PREREG.read_text(encoding="utf-8"))
    h1b_prereg = json.loads(H1B_PREREG.read_text(encoding="utf-8"))
    if not s2_prereg.get("registered_before_s2_value_computation"):
        raise ValueError("S2 preregistration is not frozen")
    input_hashes = {"S2": sha256_file(S2_PATH), "C2": sha256_file(C2_PATH), "definedness_masks": sha256_file(GAP_PATH)}
    if input_hashes["S2"] != S2_SHA256:
        raise ValueError("frozen S2 hash mismatch")
    if input_hashes["C2"] != s2_prereg["frozen_inputs"]["C2_components"]["sha256"] or input_hashes["C2"] != C2_SHA256:
        raise ValueError("frozen C2 hash mismatch")
    if input_hashes["definedness_masks"] != s2_prereg["frozen_inputs"]["gap_estimates"]["sha256"]:
        raise ValueError("frozen feature-only definedness-mask input hash mismatch")
    registry_sha = sha256_file(REGISTRY_PATH)
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    cards = [x for x in registry.get("candidate_set", []) if x.get("candidate_id") == S2_ID]
    if len(cards) != 1 or cards[0].get("sha256") != S2_SHA256 or cards[0].get("upload_authorized") is not False:
        raise ValueError("frozen S2 registry identity/hash/authorization mismatch")

    ids_by_region: dict[str, list[str]] = {}
    sample_hashes: dict[str, str] = {}
    owner: dict[str, str] = {}
    for region in REGIONS:
        sample = workspace / "samples" / f"{region}-sample-submission.csv"
        ids_by_region[region] = read_sample_ids(sample)
        sample_hashes[region] = sha256_file(sample)
        if sample_hashes[region] != SAMPLE_HASHES[region] or len(ids_by_region[region]) != REGION_COUNTS[region]:
            raise ValueError(f"official sample contract mismatch: {region}")
        for geoid in ids_by_region[region]:
            if geoid in owner:
                raise ValueError(f"duplicate official GEOID across regions: {geoid}")
            owner[geoid] = region
    official_ids = set(owner)
    if len(official_ids) != 9379:
        raise ValueError("official scope must contain exactly 9,379 unique IDs")
    s2_data, s2_order = read_candidate(S2_PATH, S2_SHA256, official_ids)
    c2_data, c2_order = read_candidate(C2_PATH, C2_SHA256, official_ids)
    if s2_order != c2_order or len(set(s2_order) - official_ids) != 415:
        raise ValueError("S2/C2 full-template ordering or 415-row scope contract mismatch")
    masks = read_masks(GAP_PATH, official_ids)
    feature_pins = h1b_prereg["pinned_inputs"]["provided_overture_tract_summaries"]
    features, feature_hashes = read_features(workspace, ids_by_region, feature_pins)

    source_records: dict[str, dict[str, Any]] = {}
    for geoid in official_ids:
        s2 = s2_data[geoid]
        c2 = c2_data[geoid]
        mask = masks[geoid]
        feature = features[geoid]
        poi_defined = derive_poi_defined(mask, feature)
        defined = {"transport": mask["transport_defined"], "building": mask["building_defined"], "poi": poi_defined}
        original_share = feature["source_share"]
        expected_building = c2["building_gap"] if original_share is None else c2["building_gap"] * (1 - original_share)
        expected_building = expected_building if defined["building"] else 0.0
        if abs(expected_building - s2["building_gap"]) > TOL:
            raise ValueError(f"frozen S2 building component does not match source-share recipe for {geoid}")
        if not defined["transport"] and s2["transport_gap"] != 0.0:
            raise ValueError(f"undefined S2 transport component is nonzero for {geoid}")
        if not defined["poi"] and s2["poi_gap"] != 0.0:
            raise ValueError(f"undefined S2 POI component is nonzero for {geoid}")
        component_values = {"transport": s2["transport_gap"], "building": s2["building_gap"], "poi": s2["poi_gap"]}
        used = [component_values[k] for k in ("transport", "building", "poi") if defined[k]]
        base = sum(used) / len(used) if used else 0.0
        if abs(base - s2["coverage_gap_score"]) > TOL:
            raise ValueError(f"frozen S2 composite/mask identity mismatch for {geoid}")
        c2_used = [c2[k + "_gap"] for k in ("transport", "building", "poi") if mask[k + "_defined"]]
        c2_base = sum(c2_used) / len(c2_used) if c2_used else 0.0
        if abs(c2_base - c2["coverage_gap_score"]) > TOL:
            raise ValueError(f"frozen C2 composite/mask identity mismatch for {geoid}")
        source_records[geoid] = {"s2": s2, "c2": c2}

    report = make_report(ids_by_region, source_records, features, masks,
                         input_hashes=input_hashes, registry_sha256=registry_sha,
                         sample_hashes=sample_hashes, feature_hashes=feature_hashes)
    # Verify immutable inputs immediately before emission.
    for key, path in (("S2", S2_PATH), ("C2", C2_PATH), ("definedness_masks", GAP_PATH)):
        if sha256_file(path) != input_hashes[key]:
            raise ValueError(f"input changed during analysis: {key}")
    if sha256_file(REGISTRY_PATH) != registry_sha:
        raise ValueError("candidate registry changed during analysis")
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
    print(json.dumps({"report": str(OUT_PATH), "sha256": digest,
                      "official_ids": report["scope"]["official_tracts"],
                      "materiality": report["interpretation"]["materiality"],
                      "max_regional_p95_abs_delta": report["interpretation"]["max_region_offset_p95_abs_delta"],
                      "upload_authorized": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
