#!/usr/bin/env python3
"""Screen the Tm5 building-p90-version × frozen Mx4 F interaction."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import official_scored_pairs  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
BUILDING_VERSION_MAX = 5.0
MIN_COVERAGE = 0.9859
MIN_P95_CHANGE = 0.01
TM5_SHA = "ae363dff82f4ee47546485f45cd71250150a402479718795b0247585efdfe45e"
MX4_SHA = "8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def feature_digest(official: list[tuple[str, str]], values: np.ndarray, defined: np.ndarray) -> str:
    rows = [f"{g},{r},{int(ok)}," + (format(float(v), ".17g") if ok else "NULL")
            for (g, r), v, ok in zip(official, values, defined)]
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def normalize_interaction(building_version: np.ndarray, building_defined: np.ndarray,
                          mx4_f: np.ndarray, mx4_defined: np.ndarray,
                          version_max: float = BUILDING_VERSION_MAX) -> dict[str, Any]:
    """Normalize log1p version to [0,1], multiply by clipped Mx4 F on joint support."""
    raw = np.asarray(building_version, dtype=np.float64)
    raw_mask = np.asarray(building_defined, dtype=bool)
    mx4 = np.asarray(mx4_f, dtype=np.float64)
    mx4_mask = np.asarray(mx4_defined, dtype=bool)
    if (raw.ndim != 1 or mx4.shape != raw.shape or raw_mask.shape != raw.shape
            or mx4_mask.shape != raw.shape):
        raise ValueError("Tm5/Mx4 values and defined masks must be aligned vectors")
    if not np.isfinite(version_max) or version_max <= 0:
        raise ValueError("building-version maximum must be positive and finite")
    if np.any(raw_mask & (~np.isfinite(raw) | (raw < 0.0))):
        raise ValueError("defined building p90-version must be finite and nonnegative")
    if np.any(mx4_mask & ~np.isfinite(mx4)):
        raise ValueError("defined Mx4 F values must be finite")
    normalized = np.full(raw.shape, np.nan, dtype=np.float64)
    normalized[raw_mask] = np.clip(np.log1p(raw[raw_mask]) / np.log1p(version_max), 0.0, 1.0)
    clipped_mx4 = np.full(mx4.shape, np.nan, dtype=np.float64)
    clipped_mx4[mx4_mask] = np.clip(mx4[mx4_mask], 0.0, 1.0)
    joint = raw_mask & mx4_mask
    interaction = np.full(raw.shape, np.nan, dtype=np.float64)
    interaction[joint] = normalized[joint] * clipped_mx4[joint]
    change = np.full(raw.shape, np.nan, dtype=np.float64)
    change[joint] = np.abs(interaction[joint] - normalized[joint])
    if (np.any(raw_mask & (~np.isfinite(normalized) | (normalized < 0.0) | (normalized > 1.0)))
            or np.any(joint & (~np.isfinite(interaction) | (interaction < 0.0) | (interaction > 1.0)))):
        raise ValueError("defined normalized and interaction outputs must be finite in [0,1]")
    return {"normalized": normalized, "clipped_mx4": clipped_mx4, "interaction": interaction,
            "change": change, "defined": joint,
            "building_clip_count": int(np.count_nonzero(raw_mask & (raw > version_max))),
            "mx4_clip_count": int(np.count_nonzero(mx4_mask & ((mx4 < 0.0) | (mx4 > 1.0))))}


def read_emission(path: Path, expected_sha: str, idea: str, component: str,
                  channel: str, official: list[tuple[str, str]]) -> tuple[np.ndarray, np.ndarray]:
    if not path.is_file() or sha256_file(path) != expected_sha:
        raise ValueError(f"{idea} frozen artifact missing or hash mismatch")
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "component", "channel", "value", "defined"],
                          filters=[("idea", "=", idea), ("component", "=", component), ("channel", "=", channel)])
    if table.num_rows != len(official):
        raise ValueError(f"{idea}/{component}/{channel}: expected one row per official GEOID")
    positions = {pair: i for i, pair in enumerate(official)}
    values = np.full(len(official), np.nan, dtype=np.float64)
    defined = np.zeros(len(official), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in positions or pair in seen:
            raise ValueError(f"unexpected or duplicate {idea} row {pair}")
        if (row["idea"], row["component"], row["channel"]) != (idea, component, channel):
            raise ValueError("selected feature metadata mismatch")
        seen.add(pair)
        if bool(row["defined"]):
            if row["value"] is None or not np.isfinite(float(row["value"])):
                raise ValueError(f"defined {idea} value is null/nonfinite at {pair[0]}")
            values[positions[pair]] = float(row["value"])
            defined[positions[pair]] = True
    if seen != set(official):
        raise ValueError(f"{idea} feature does not reconcile to exact official ID partition")
    return values, defined


def distribution(values: np.ndarray) -> dict[str, float | int | None]:
    x = np.asarray(values, dtype=np.float64)
    if x.ndim != 1 or (x.size and not np.isfinite(x).all()):
        raise ValueError("distribution requires finite one-dimensional values")
    if not x.size:
        return {"n": 0, "min": None, "p50": None, "p90": None, "p95": None, "max": None, "mean": None}
    return {"n": int(x.size), "min": float(x.min()), "p50": float(np.quantile(x, .5)),
            "p90": float(np.quantile(x, .9)), "p95": float(np.quantile(x, .95)),
            "max": float(x.max()), "mean": float(x.mean())}


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def output_table(official: list[tuple[str, str]], building: np.ndarray, building_defined: np.ndarray,
                 mx4: np.ndarray, mx4_defined: np.ndarray, result: dict[str, Any]) -> pa.Table:
    n = len(official)
    arrays = (building, building_defined, mx4, mx4_defined, result["normalized"],
              result["clipped_mx4"], result["interaction"], result["change"], result["defined"])
    if any(np.asarray(v).shape != (n,) for v in arrays):
        raise ValueError("output arrays must align to official GEOIDs")
    joint = np.asarray(result["defined"], dtype=bool)
    if not np.array_equal(joint, np.asarray(building_defined, bool) & np.asarray(mx4_defined, bool)):
        raise ValueError("interaction must preserve exact pairwise source-defined mask")
    ids, regions = zip(*official)
    normalized = np.asarray(result["normalized"])
    clipped = np.asarray(result["clipped_mx4"])
    interaction = np.asarray(result["interaction"])
    change = np.asarray(result["change"])
    return pa.table({
        "GEOID": pa.array(ids, type=pa.string()), "region": pa.array(regions, type=pa.string()),
        "tm5_buildings_p90_version": pa.array([float(v) if ok else None for v, ok in zip(building, building_defined)], type=pa.float64()),
        "tm5_buildings_defined": pa.array(building_defined, type=pa.bool_()),
        "mx4_f_source_share": pa.array([float(v) if ok else None for v, ok in zip(mx4, mx4_defined)], type=pa.float64()),
        "mx4_defined": pa.array(mx4_defined, type=pa.bool_()),
        "tm5_building_p90_version_normalized": pa.array([float(v) if ok else None for v, ok in zip(normalized, building_defined)], type=pa.float64()),
        "mx4_f_clipped": pa.array([float(v) if ok else None for v, ok in zip(clipped, mx4_defined)], type=pa.float64()),
        "interaction": pa.array([float(v) if ok else None for v, ok in zip(interaction, joint)], type=pa.float64()),
        "absolute_change_from_normalized_building_version": pa.array([float(v) if ok else None for v, ok in zip(change, joint)], type=pa.float64()),
        "interaction_defined": pa.array(joint, type=pa.bool_()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    poi_path = ROOT / "reports/tm5-p90-version-mx4-interaction.json"
    poi_sha = sha256_file(poi_path)
    poi = json.loads(poi_path.read_text(encoding="utf-8"))
    if (poi["scope"]["official_ids"] != 9379 or poi["acceptance"]["pairwise_coverage_fraction"] < MIN_COVERAGE
            or not np.isclose(poi["acceptance"]["p95_absolute_change_from_normalized_p90_version"], 0.9415887339401863, rtol=0, atol=1e-12)):
        raise ValueError("POI-p90 interaction baseline no longer matches the issued measure-before")
    expected_hashes = {idea: poi["provenance"]["frozen_inputs"][idea]["sha256"] for idea in ("Tm5", "Mx4")}
    if expected_hashes != {"Tm5": TM5_SHA, "Mx4": MX4_SHA}:
        raise ValueError("frozen source hashes differ from the completed POI-p90 interaction report")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != 9379 or len(set(official)) != 9379 or len({g for g, _ in official}) != 9379
            or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official scored IDs must be an exact 9,379-ID four-region partition")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode("utf-8")).hexdigest()
    if official_sha != poi["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official GEOID-region partition differs from the POI-p90 baseline")

    tm5_path, mx4_path = workspace / "ideas/Tm5.parquet", workspace / "ideas/Mx4.parquet"
    registry_path, candidates_path = ROOT / "config/ideas.json", ROOT / "reports/candidates.json"
    registry_before, candidates_before = sha256_file(registry_path), sha256_file(candidates_path)
    building, building_defined = read_emission(tm5_path, TM5_SHA, "Tm5", "F", "covariate:tm5_buildings_p90_version", official)
    mx4, mx4_defined = read_emission(mx4_path, MX4_SHA, "Mx4", "F", "covariate", official)
    if int(mx4_defined.sum()) != 9379:
        raise ValueError("Mx4 F defined coverage differs from the completed prior screen")
    observed = building[building_defined]
    if not observed.size or not np.isclose(observed.min(), 1.0, rtol=0, atol=1e-12) or not np.isclose(observed.max(), 5.0, rtol=0, atol=1e-12):
        raise ValueError("building p90-version channel no longer spans expected range [1,5]")

    result = normalize_interaction(building, building_defined, mx4, mx4_defined)
    joint = np.asarray(result["defined"], dtype=bool)
    normalized = np.asarray(result["normalized"], dtype=np.float64)
    interaction = np.asarray(result["interaction"], dtype=np.float64)
    change = np.asarray(result["change"], dtype=np.float64)
    p95 = float(np.quantile(change[joint], .95)) if joint.any() else None
    regions: dict[str, Any] = {}
    for region in REGIONS:
        local = np.asarray([r == region for _, r in official], dtype=bool)
        local_b = building_defined & local
        local_m = mx4_defined & local
        local_joint = joint & local
        regions[region] = {
            "official_ids": int(local.sum()), "tm5_building_defined_ids": int(local_b.sum()),
            "mx4_defined_ids": int(local_m.sum()), "pairwise_defined_ids": int(local_joint.sum()),
            "pairwise_coverage_fraction": float(local_joint.sum() / local.sum()),
            "building_p90_version": distribution(building[local_b]),
            "normalized_building_p90_version": distribution(normalized[local_b]),
            "interaction": distribution(interaction[local_joint]),
            "absolute_change_from_normalized_building_version": distribution(change[local_joint]),
        }
    table = output_table(official, building, building_defined, mx4, mx4_defined, result)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("building interaction artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    tm5_after, mx4_after = sha256_file(tm5_path), sha256_file(mx4_path)
    registry_after, candidates_after = sha256_file(registry_path), sha256_file(candidates_path)
    frozen_unchanged = (tm5_after == TM5_SHA and mx4_after == MX4_SHA
                        and registry_after == registry_before and candidates_after == candidates_before)
    if not frozen_unchanged:
        raise AssertionError("frozen Tm5/Mx4, registry, or candidates changed")
    coverage = float(joint.mean())
    bounded = bool(np.isfinite(interaction[joint]).all() and np.all((interaction[joint] >= 0) & (interaction[joint] <= 1))
                   and np.isfinite(normalized[building_defined]).all()
                   and np.all((normalized[building_defined] >= 0) & (normalized[building_defined] <= 1)))
    acceptance = {
        "exact_9379_id_alignment": table.num_rows == 9379 and len(set(official)) == 9379,
        "pairwise_defined_ids": int(joint.sum()), "pairwise_coverage_fraction": coverage,
        "coverage_at_least_98_59_percent": coverage >= MIN_COVERAGE,
        "all_finite_outputs_bounded_0_1": bounded,
        "p95_absolute_change_from_normalized_building_p90_version": p95,
        "p95_absolute_change_at_least_0_01": p95 is not None and p95 >= MIN_P95_CHANGE,
        "frozen_Tm5_Mx4_registry_and_candidates_unchanged": frozen_unchanged,
        "acceptance_pass": bool(coverage >= MIN_COVERAGE and bounded and p95 is not None
                                 and p95 >= MIN_P95_CHANGE and frozen_unchanged),
    }
    report = {
        "schema_version": 1,
        "screen": "Tm5 building p90-version × Mx4 F interaction; unregistered label-free sensitivity",
        "definition": "v=clip(log1p(tm5_buildings_p90_version)/log1p(5),0,1); interaction=v*clip(Mx4_F,0,1), defined only where both source emissions are defined. Change is |interaction-v| on pairwise-defined IDs.",
        "measure_before": {
            "POI_p90_Mx4_interaction_p95_absolute_change": 0.9415887339401863,
            "POI_p90_Mx4_pairwise_coverage_fraction": 0.9997867576500693,
            "building_p90_version_source_range_min": 1.0,
            "building_p90_version_source_range_max": 5.0,
            "building_p90_Mx4_interaction_previously_measured": False,
            "POI_p90_interaction_report_sha256": poi_sha,
        },
        "scope": {
            "official_ids": len(official), "exact_official_geoid_region_partition": True,
            "tm5_building_p90_defined_ids": int(building_defined.sum()), "mx4_f_defined_ids": int(mx4_defined.sum()),
            "pairwise_defined_ids": int(joint.sum()), "undefined_ids": int(len(official) - joint.sum()),
            "coverage_fraction": coverage, "building_version_clip_count": int(result["building_clip_count"]),
            "mx4_clip_count": int(result["mx4_clip_count"]),
            "labels_read": False, "sample_score_values_read": False, "Tm1_or_Tm4_inputs_read": False,
            "removed_or_equivalent_reference_data_read": False, "external_data_read": False,
            "leaderboard_feedback_used": False, "registry_integration": False,
            "frozen_Tm5_or_Mx4_modified": False, "uploads": 0,
        },
        "measure_after": {
            "building_p90_version_source_range": {"min": float(observed.min()), "max": float(observed.max())},
            "normalized_building_p90_version": distribution(normalized[building_defined]),
            "interaction": distribution(interaction[joint]),
            "absolute_change_from_normalized_building_p90_version": distribution(change[joint]),
            "p95_absolute_change_from_normalized_building_p90_version": p95,
            "regions": regions,
        },
        "thresholds": {"minimum_pairwise_coverage": MIN_COVERAGE, "minimum_p95_absolute_change": MIN_P95_CHANGE,
                       "normalized_and_interaction_bounds_inclusive": [0.0, 1.0]},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_sha,
            "POI_p90_interaction_report_sha256": poi_sha,
            "frozen_inputs": {
                "Tm5": {"path": str(tm5_path), "sha256_before": TM5_SHA, "sha256_after": tm5_after,
                        "building_p90_version_emission_sha256": feature_digest(official, building, building_defined)},
                "Mx4": {"path": str(mx4_path), "sha256_before": MX4_SHA, "sha256_after": mx4_after,
                        "F_emission_sha256": feature_digest(official, mx4, mx4_defined)},
            },
            "registry_sha256_before": registry_before, "registry_sha256_after": registry_after,
            "candidate_manifest_sha256_before": candidates_before, "candidate_manifest_sha256_after": candidates_after,
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "columns": table.column_names, "deterministic_bytes": True},
        },
        "interpretation": "Coverage, bounds, and p95-change gates pass; retain only as an unregistered sensitivity and do not integrate into candidates or registry." if acceptance["acceptance_pass"] else
        "One or more registered gates failed; retain only the existing unregistered diagnostics and leave frozen inputs unchanged.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm5-building-p90-mx4-interaction.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE is required and must exist")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "tm5-building-p90-mx4-interaction" / "building-p90-mx4-interaction.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "measure_after": {k: report["measure_after"][k] for k in (
                          "p95_absolute_change_from_normalized_building_p90_version", "regions")},
                      "output_artifact": report["provenance"]["output_artifact"],
                      "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
