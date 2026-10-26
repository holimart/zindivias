#!/usr/bin/env python3
"""Screen bounded Tm5 POI p90-version × Mx4 F interaction on official GEOIDs."""
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
P90_VERSION_MAX = 13.0
MIN_COVERAGE = 0.9859
MIN_P95_CHANGE = 0.01
TM5_EMISSION = ("Tm5", "Tm", "Est", "covariate:tm5_poi_p90_version")
MX4_EMISSION = ("Mx4", "Mx", "F", "covariate")
EXPECTED_ARTIFACT_HASHES = {
    "Tm5": "ae363dff82f4ee47546485f45cd71250150a402479718795b0247585efdfe45e",
    "Mx4": "8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def feature_digest(official: list[tuple[str, str]], values: np.ndarray,
                   defined: np.ndarray) -> str:
    rows = [f"{g},{r},{int(ok)}," + (format(float(v), ".17g") if ok else "NULL")
            for (g, r), v, ok in zip(official, values, defined)]
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def normalize_interaction(p90_version: np.ndarray, p90_defined: np.ndarray,
                          mx4_f: np.ndarray, mx4_defined: np.ndarray,
                          version_max: float = P90_VERSION_MAX) -> dict[str, Any]:
    """Normalize log1p p90 version, then multiply by bounded Mx4 F on joint rows."""
    raw = np.asarray(p90_version, dtype=np.float64)
    raw_mask = np.asarray(p90_defined, dtype=bool)
    mx4 = np.asarray(mx4_f, dtype=np.float64)
    mx4_mask = np.asarray(mx4_defined, dtype=bool)
    if (raw.ndim != 1 or mx4.ndim != 1 or raw.shape != mx4.shape
            or raw_mask.shape != raw.shape or mx4_mask.shape != raw.shape):
        raise ValueError("Tm5/Mx4 values and defined masks must be aligned one-dimensional arrays")
    if not np.isfinite(version_max) or version_max <= 0:
        raise ValueError("p90 version maximum must be positive and finite")
    if np.any(raw_mask & (~np.isfinite(raw) | (raw < 0.0))):
        raise ValueError("defined Tm5 p90-version values must be finite and nonnegative")
    if np.any(mx4_mask & ~np.isfinite(mx4)):
        raise ValueError("defined Mx4 F values must be finite")
    normalized = np.full(raw.shape, np.nan, dtype=np.float64)
    normalized[raw_mask] = np.clip(np.log1p(raw[raw_mask]) / np.log1p(version_max), 0.0, 1.0)
    joint = raw_mask & mx4_mask
    mx4_clipped = np.full(mx4.shape, np.nan, dtype=np.float64)
    mx4_clipped[mx4_mask] = np.clip(mx4[mx4_mask], 0.0, 1.0)
    interaction = np.full(raw.shape, np.nan, dtype=np.float64)
    interaction[joint] = normalized[joint] * mx4_clipped[joint]
    if np.any(joint & (~np.isfinite(interaction) | (interaction < 0.0) | (interaction > 1.0))):
        raise AssertionError("p90-version interaction escaped [0,1]")
    return {"normalized_p90_version": normalized, "interaction": interaction,
            "defined": joint,
            "p90_clip_count": int(np.count_nonzero(raw_mask & (raw > version_max))),
            "mx4_clip_count": int(np.count_nonzero(mx4_mask & ((mx4 < 0.0) | (mx4 > 1.0))))}


def distribution(values: np.ndarray) -> dict[str, Any]:
    x = np.asarray(values, dtype=np.float64)
    if x.ndim != 1 or (x.size and not np.isfinite(x).all()):
        raise ValueError("distribution requires finite one-dimensional values")
    if not x.size:
        return {"n": 0, "min": None, "p50": None, "p90": None, "p95": None,
                "max": None, "mean": None}
    return {"n": int(x.size), "min": float(x.min()), "p50": float(np.quantile(x, .5)),
            "p90": float(np.quantile(x, .9)), "p95": float(np.quantile(x, .95)),
            "max": float(x.max()), "mean": float(x.mean())}


def read_emission(workspace: Path, idea: str, family: str, component: str, channel: str,
                  official: list[tuple[str, str]], expected_sha: str
                  ) -> tuple[np.ndarray, np.ndarray, str]:
    path = workspace / "ideas" / f"{idea}.parquet"
    if not path.is_file():
        raise FileNotFoundError(path)
    artifact_sha = sha256_file(path)
    if artifact_sha != expected_sha:
        raise ValueError(f"{idea} frozen artifact hash differs from prior registered screen")
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "family", "component",
                                         "channel", "value", "defined"],
                          filters=[("idea", "=", idea), ("component", "=", component),
                                   ("channel", "=", channel)])
    if table.num_rows != len(official):
        raise ValueError(f"{idea}/{component}/{channel}: expected one row per official GEOID")
    index = {pair: i for i, pair in enumerate(official)}
    values = np.full(len(official), np.nan, dtype=np.float64)
    defined = np.zeros(len(official), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in index or pair in seen:
            raise ValueError(f"{idea}: duplicate or out-of-scope official pair {pair}")
        if (row["idea"], row["family"], row["component"], row["channel"]) != (
                idea, family, component, channel):
            raise ValueError("selected feature metadata differs from requested registered emission")
        seen.add(pair)
        if bool(row["defined"]):
            if row["value"] is None:
                raise ValueError(f"defined {idea} source has null value at {pair[0]}")
            value = float(row["value"])
            if not np.isfinite(value):
                raise ValueError(f"defined {idea} source has nonfinite value at {pair[0]}")
            values[index[pair]] = value
            defined[index[pair]] = True
    if seen != set(official):
        raise ValueError(f"{idea}: selected emission does not exactly reconcile to official IDs")
    return values, defined, artifact_sha


def output_table(official: list[tuple[str, str]], p90: np.ndarray, p90_defined: np.ndarray,
                 mx4: np.ndarray, mx4_defined: np.ndarray, result: dict[str, Any]) -> pa.Table:
    n = len(official)
    arrays = (p90, p90_defined, mx4, mx4_defined,
              result["normalized_p90_version"], result["interaction"], result["defined"])
    if any(np.asarray(value).shape != (n,) for value in arrays):
        raise ValueError("output arrays must align with official GEOIDs")
    if not np.array_equal(result["defined"], p90_defined & mx4_defined):
        raise ValueError("interaction must be defined exactly on the pairwise source mask")
    normalized = np.asarray(result["normalized_p90_version"], dtype=np.float64)
    interaction = np.asarray(result["interaction"], dtype=np.float64)
    if (np.any(~np.isfinite(normalized[p90_defined]))
            or np.any((normalized[p90_defined] < 0.0) | (normalized[p90_defined] > 1.0))
            or np.any(~np.isfinite(interaction[result["defined"]]))
            or np.any((interaction[result["defined"]] < 0.0) | (interaction[result["defined"]] > 1.0))):
        raise ValueError("defined normalized or interaction outputs must be finite and in [0,1]")
    ids, regions = zip(*official)
    normalized = result["normalized_p90_version"]
    interaction = result["interaction"]
    return pa.table({
        "GEOID": pa.array(ids, type=pa.string()), "region": pa.array(regions, type=pa.string()),
        "tm5_poi_p90_version": pa.array([float(v) if ok else None for v, ok in zip(p90, p90_defined)], type=pa.float64()),
        "tm5_defined": pa.array(p90_defined, type=pa.bool_()),
        "mx4_f_source_share": pa.array([float(v) if ok else None for v, ok in zip(mx4, mx4_defined)], type=pa.float64()),
        "mx4_defined": pa.array(mx4_defined, type=pa.bool_()),
        "tm5_p90_version_normalized": pa.array([float(v) if ok else None for v, ok in zip(normalized, p90_defined)], type=pa.float64()),
        "interaction": pa.array([float(v) if ok else None for v, ok in zip(interaction, result["defined"])], type=pa.float64()),
        "interaction_defined": pa.array(result["defined"], type=pa.bool_()),
    })


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    baseline_path = ROOT / "reports/tm5-mx4-churn-interaction.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if (baseline["scope"]["official_ids"] != 9379
            or baseline["measure_after"]["changed_ids"] != 0
            or baseline["acceptance"]["p95_absolute_change_from_existing_tm5"] != 0.0):
        raise ValueError("version-zero interaction baseline no longer matches the measured zero-effect screen")
    expected_hashes = {idea: baseline["provenance"]["frozen_inputs"][idea]["sha256"]
                       for idea in ("Tm5", "Mx4")}
    if expected_hashes != EXPECTED_ARTIFACT_HASHES:
        raise ValueError("prior screen frozen input hashes differ from the assigned baseline")
    official = official_scored_pairs(workspace)
    if (len(official) != 9379 or len(set(official)) != 9379
            or len({g for g, _ in official}) != 9379 or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official GEOID-region partition must contain exactly 9,379 unique IDs")
    official_hash = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode("utf-8")).hexdigest()
    if official_hash != baseline["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official GEOID-region partition differs from previous Tm5/Mx4 screen")

    registry_path = ROOT / "config/ideas.json"
    candidates_path = ROOT / "reports/candidates.json"
    registry_hash_before = sha256_file(registry_path)
    candidates_hash_before = sha256_file(candidates_path)
    p90, p90_defined, tm5_sha = read_emission(
        workspace, *TM5_EMISSION, official, expected_hashes["Tm5"])
    mx4, mx4_defined, mx4_sha = read_emission(
        workspace, *MX4_EMISSION, official, expected_hashes["Mx4"])
    if int(p90_defined.sum()) != 9377 or int(mx4_defined.sum()) != 9379:
        raise ValueError("Tm5/Mx4 input coverage differs from prior registered measurement")
    p90_observed = p90[p90_defined]
    if not np.isclose(p90_observed.min(), 1.0, rtol=0, atol=1e-12) or not np.isclose(p90_observed.max(), 13.0, rtol=0, atol=1e-12):
        raise ValueError("Tm5 POI p90-version no longer spans the measured [1,13] range")

    result = normalize_interaction(p90, p90_defined, mx4, mx4_defined)
    joint = result["defined"]
    v = result["normalized_p90_version"]
    interaction = result["interaction"]
    change = np.abs(interaction[joint] - v[joint])
    coverage = float(joint.mean())
    p95_change = float(np.quantile(change, .95)) if change.size else None
    regions: dict[str, Any] = {}
    for region in REGIONS:
        local = np.asarray([r == region for _, r in official], dtype=bool)
        local_p90 = p90_defined & local
        local_mx4 = mx4_defined & local
        local_joint = joint & local
        local_change = np.abs(interaction[local_joint] - v[local_joint])
        regions[region] = {
            "official_ids": int(local.sum()),
            "tm5_defined_ids": int(local_p90.sum()),
            "mx4_defined_ids": int(local_mx4.sum()),
            "pairwise_defined_ids": int(local_joint.sum()),
            "pairwise_coverage_fraction": float(local_joint.sum() / local.sum()),
            "tm5_p90_version": distribution(p90[local_p90]),
            "normalized_tm5_p90_version": distribution(v[local_p90]),
            "interaction": distribution(interaction[local_joint]),
            "absolute_change_from_normalized_p90": distribution(local_change),
            "changed_ids": int(np.count_nonzero(local_change > 0.0)),
        }
    table = output_table(official, p90, p90_defined, mx4, mx4_defined, result)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("interaction artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    bounded = bool(np.isfinite(interaction[joint]).all()
                   and np.all((interaction[joint] >= 0.0) & (interaction[joint] <= 1.0)))
    exact_ids = bool(table.num_rows == 9379 and len(set(official)) == 9379)
    hashes_after = {"Tm5": sha256_file(workspace / "ideas/Tm5.parquet"),
                    "Mx4": sha256_file(workspace / "ideas/Mx4.parquet")}
    registry_hash_after = sha256_file(registry_path)
    candidates_hash_after = sha256_file(candidates_path)
    frozen_unchanged = (hashes_after == expected_hashes
                        and registry_hash_after == registry_hash_before
                        and candidates_hash_after == candidates_hash_before)
    if not frozen_unchanged:
        raise AssertionError("frozen Tm5/Mx4, registry, or candidates changed during diagnostic")
    acceptance = {
        "exact_9379_geoid_region_alignment": exact_ids,
        "pairwise_defined_ids": int(joint.sum()),
        "pairwise_coverage_fraction": coverage,
        "pairwise_coverage_at_least_98_59_percent": coverage >= MIN_COVERAGE,
        "all_outputs_finite_and_in_0_1": bounded,
        "p95_absolute_change_from_normalized_p90_version": p95_change,
        "p95_absolute_change_at_least_0_01": bool(p95_change is not None and p95_change >= MIN_P95_CHANGE),
        "frozen_Tm5_Mx4_registry_and_candidates_unchanged": frozen_unchanged,
    }
    acceptance["acceptance_pass"] = bool(exact_ids and coverage >= MIN_COVERAGE and bounded
                                         and p95_change is not None and p95_change >= MIN_P95_CHANGE
                                         and frozen_unchanged)
    report = {
        "schema_version": 1,
        "screen": "Tm5 POI p90-version × Mx4 F interaction; unregistered sensitivity",
        "definition": "v=clip(log1p(Tm5_poi_p90_version)/log1p(13),0,1); interaction=v*clip(Mx4_F,0,1), defined only where both source emissions are defined. Change is absolute interaction minus v.",
        "measure_before": {
            "version_zero_share_defined_ids": baseline["scope"]["Tm5_defined_ids"],
            "version_zero_share_maximum": baseline["measure_before"]["Tm5_source_distribution"]["max"],
            "version_zero_interaction_p95_absolute_change": baseline["acceptance"]["p95_absolute_change_from_existing_tm5"],
            "p90_version_source_range_min": float(p90_observed.min()),
            "p90_version_source_range_max": float(p90_observed.max()),
            "baseline_report_sha256": sha256_file(baseline_path),
        },
        "scope": {
            "official_ids": len(official), "exact_id_alignment": exact_ids,
            "tm5_p90_version_defined_ids": int(p90_defined.sum()),
            "mx4_f_defined_ids": int(mx4_defined.sum()),
            "pairwise_defined_ids": int(joint.sum()), "coverage_fraction": coverage,
            "undefined_ids": int((~joint).sum()), "Tm5_p90_clip_count": result["p90_clip_count"],
            "Mx4_clip_count": result["mx4_clip_count"],
            "labels_read": False, "sample_score_values_read": False,
            "Tm1_or_Tm4_inputs_or_artifacts_read": False,
            "removed_or_equivalent_reference_data_read": False, "external_data_read": False,
            "leaderboard_feedback_used": False, "registry_integration": False,
            "frozen_Tm5_or_Mx4_modified": False, "uploads": 0,
        },
        "measure_after": {
            "normalized_p90_input": distribution(v[joint]),
            "interaction": distribution(interaction[joint]),
            "absolute_change_from_normalized_p90": distribution(change),
            "changed_ids": int(np.count_nonzero(change > 0.0)),
            "p95_absolute_change_from_normalized_p90": p95_change,
            "regions": regions,
        },
        "acceptance_thresholds": {"minimum_pairwise_coverage": MIN_COVERAGE,
                                  "interaction_bounds_inclusive": [0.0, 1.0],
                                  "minimum_p95_absolute_change": MIN_P95_CHANGE},
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": official_hash,
            "prior_version_zero_screen_sha256": sha256_file(baseline_path),
            "frozen_inputs": {
                "Tm5": {"path": str(workspace / "ideas/Tm5.parquet"), "sha256": tm5_sha,
                        "sha256_after": hashes_after["Tm5"],
                        "p90_version_emission_sha256": feature_digest(official, p90, p90_defined)},
                "Mx4": {"path": str(workspace / "ideas/Mx4.parquet"), "sha256": mx4_sha,
                        "sha256_after": hashes_after["Mx4"],
                        "f_source_emission_sha256": feature_digest(official, mx4, mx4_defined)},
            },
            "registry_sha256_before": registry_hash_before,
            "registry_sha256_after": registry_hash_after,
            "candidates_manifest_sha256_before": candidates_hash_before,
            "candidates_manifest_sha256_after": candidates_hash_after,
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "columns": table.column_names, "deterministic_bytes": True},
        },
        "interpretation": "Thresholds passed; retain only as an unregistered sensitivity and do not integrate into candidates or registry." if acceptance["acceptance_pass"] else
        "The p95-change or another required gate failed; leave frozen Tm5/Mx4 unchanged and do not integrate.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm5-p90-version-mx4-interaction.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "tm5-p90-version-mx4-interaction" / "tm5-p90-version-mx4-interaction.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"], "scope": report["scope"],
                      "measure_after": {"p95_absolute_change_from_normalized_p90": report["measure_after"]["p95_absolute_change_from_normalized_p90"],
                                        "global": report["measure_after"]["absolute_change_from_normalized_p90"],
                                        "regions": {r: {"pairwise_defined_ids": d["pairwise_defined_ids"],
                                                        "coverage": d["pairwise_coverage_fraction"],
                                                        "p95_change": d["absolute_change_from_normalized_p90"]["p95"]}
                                                    for r, d in report["measure_after"]["regions"].items()}},
                      "output_artifact": report["provenance"]["output_artifact"],
                      "report": str(report_path.relative_to(ROOT))}, indent=2, sort_keys=True))
    return 0 if report["acceptance"]["acceptance_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
