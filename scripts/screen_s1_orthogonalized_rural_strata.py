#!/usr/bin/env python3
"""Describe the frozen S1 disagreement residual by provided rural/urban strata."""
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

EXPECTED_N = 9379
RESIDUAL_SHA = "437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
RESIDUAL_REPORT = ROOT / "reports/s1-disagreement-orthogonalized.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rural_interaction(residual: np.ndarray, residual_defined: np.ndarray,
                      pct_urban: np.ndarray) -> dict[str, Any]:
    """Return residual×rural and group summaries; pct_urban < .5 defines rural."""
    values = np.asarray(residual, dtype=np.float64)
    mask = np.asarray(residual_defined, dtype=bool)
    urbanity = np.asarray(pct_urban, dtype=np.float64)
    if values.ndim != 1 or mask.shape != values.shape or urbanity.shape != values.shape:
        raise ValueError("residual, defined mask and pct_urban must align")
    if np.any(mask & ~np.isfinite(values)):
        raise ValueError("defined residuals must be finite")
    if np.any(~mask & np.isfinite(values)):
        raise ValueError("undefined residuals must remain null")
    urbanity_defined = np.isfinite(urbanity)
    if np.any(urbanity_defined & ((urbanity < 0.0) | (urbanity > 1.0))):
        raise ValueError("defined pct_urban values must be in [0,1]")
    rural = urbanity_defined & (urbanity < 0.5)
    urban = urbanity_defined & (urbanity >= 0.5)
    joint = mask & urbanity_defined
    interaction = np.full(values.shape, np.nan, dtype=np.float64)
    interaction[joint] = values[joint] * rural[joint].astype(np.float64)
    groups: dict[str, dict[str, Any]] = {}
    for name, selected in (("rural", rural), ("urban", urban)):
        use = mask & selected
        abs_values = np.abs(values[use])
        groups[name] = {
            "n": int(use.sum()),
            "median_absolute_residual": float(np.median(abs_values)) if abs_values.size else None,
            "absolute_residual_distribution": _distribution(abs_values),
            "interaction_nonzero_ids": int(np.count_nonzero(interaction[use])),
        }
    rural_median = groups["rural"]["median_absolute_residual"]
    urban_median = groups["urban"]["median_absolute_residual"]
    gap = abs(rural_median - urban_median) if rural_median is not None and urban_median is not None else None
    return {"rural": rural, "urban": urban, "joint_defined": joint,
            "interaction": interaction, "groups": groups,
            "absolute_median_gap": gap,
            "residual_defined_without_urbanity": int(np.count_nonzero(mask & ~urbanity_defined)),
            "urbanity_defined_without_residual": int(np.count_nonzero(urbanity_defined & ~mask))}


def _distribution(values: np.ndarray) -> dict[str, Any]:
    x = np.asarray(values, dtype=np.float64)
    if not x.size:
        return {"n": 0, "min": None, "p05": None, "p50": None,
                "p95": None, "max": None, "mean": None}
    q = np.quantile(x, [0.05, 0.5, 0.95])
    return {"n": int(x.size), "min": float(x.min()), "p05": float(q[0]),
            "p50": float(q[1]), "p95": float(q[2]), "max": float(x.max()),
            "mean": float(x.mean())}


def _load_residual(path: Path, expected_sha: str,
                   official: list[tuple[str, str]]) -> tuple[np.ndarray, np.ndarray]:
    if not path.is_file() or sha256_file(path) != expected_sha:
        raise ValueError("frozen orthogonalized S1 residual artifact is missing or hash-mismatched")
    table = pq.read_table(path, columns=["GEOID", "region", "crossfit_residual", "crossfit_joint_defined"])
    if table.num_rows != len(official):
        raise ValueError("residual artifact must contain one row per official tract")
    locations = {pair: i for i, pair in enumerate(official)}
    values = np.full(len(official), np.nan)
    defined = np.zeros(len(official), dtype=bool)
    seen: set[tuple[str, str]] = set()
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in locations or pair in seen:
            raise ValueError(f"duplicate or out-of-partition residual row {pair}")
        seen.add(pair)
        i = locations[pair]
        defined[i] = bool(row["crossfit_joint_defined"])
        value = row["crossfit_residual"]
        if defined[i]:
            if value is None or not np.isfinite(float(value)):
                raise ValueError(f"defined residual is null/nonfinite for {pair[0]}")
            values[i] = float(value)
        elif value is not None:
            raise ValueError(f"undefined residual must remain null for {pair[0]}")
    if seen != set(official):
        raise ValueError("residual artifact GEOID-region pairs do not exactly match official IDs")
    return values, defined


def _load_strata(workspace: Path, official: list[tuple[str, str]]) -> tuple[np.ndarray, dict[str, Any]]:
    pct_urban = np.full(len(official), np.nan)
    locations = {pair: i for i, pair in enumerate(official)}
    manifest: dict[str, Any] = {}
    for region in sorted({r for _, r in official}):
        path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        if not path.is_file():
            raise ValueError(f"provided strata table missing for {region}")
        table = pq.read_table(path, columns=["GEOID", "pct_urban"])
        seen: set[str] = set()
        for row in table.to_pylist():
            geoid = str(row["GEOID"])
            pair = (geoid, region)
            if pair not in locations:
                continue
            if geoid in seen:
                raise ValueError(f"duplicate provided stratum GEOID {geoid}")
            seen.add(geoid)
            value = row["pct_urban"]
            pct_urban[locations[pair]] = float(value) if value is not None else np.nan
        expected = {g for g, r in official if r == region}
        if seen != expected:
            raise ValueError(f"{region} rural/urban strata do not exactly match official GEOIDs")
        manifest[region] = {"path": str(path), "sha256": sha256_file(path),
                            "columns_read": ["GEOID", "pct_urban"], "official_ids": len(seen),
                            "pct_urban_defined_ids": int(np.isfinite(pct_urban[[locations[(g, region)] for g in expected]]).sum())}
    return pct_urban, manifest


def _output_table(official: list[tuple[str, str]], residual: np.ndarray,
                  residual_defined: np.ndarray, pct_urban: np.ndarray,
                  result: dict[str, Any]) -> pa.Table:
    rural, urban, joint = result["rural"], result["urban"], result["joint_defined"]
    return pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array([r for _, r in official], type=pa.string()),
        "crossfit_residual": pa.array([float(v) if ok else None for v, ok in zip(residual, residual_defined)], type=pa.float64()),
        "residual_defined": pa.array(residual_defined, type=pa.bool_()),
        "pct_urban": pa.array([float(v) if np.isfinite(v) else None for v in pct_urban], type=pa.float64()),
        "rural_indicator": pa.array([bool(v) if ok else None for v, ok in zip(rural, np.isfinite(pct_urban))], type=pa.bool_()),
        "urban_indicator": pa.array([bool(v) if ok else None for v, ok in zip(urban, np.isfinite(pct_urban))], type=pa.bool_()),
        "residual_x_rural": pa.array([float(v) if ok else None for v, ok in zip(result["interaction"], joint)], type=pa.float64()),
        "interaction_defined": pa.array(joint, type=pa.bool_()),
    })


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    source_report = json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    baseline = source_report["measure_after"]
    if (not np.isclose(baseline["maximum_within_region_NMI_residual_vs_Mx4_F"], 0.4904168102022829, rtol=0, atol=1e-12)
            or not np.isclose(baseline["crossfit_residual_variance_ratio"], 1.0244785067819135, rtol=0, atol=1e-12)
            or source_report["scope"]["coverage_fraction"] != 1.0):
        raise ValueError("frozen residual-screen baseline does not match the issued measurements")
    artifact_meta = source_report["provenance"]["output_artifact"]
    residual_path = Path(artifact_meta["path"])
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N or len({g for g, _ in official}) != EXPECTED_N:
        raise ValueError("official IDs must be an exact 9,379 GEOID-region partition")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    if official_sha != source_report["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official ID partition differs from the frozen residual screen")
    residual, residual_defined = _load_residual(residual_path, RESIDUAL_SHA, official)
    pct_urban, strata_manifest = _load_strata(workspace, official)
    result = rural_interaction(residual, residual_defined, pct_urban)
    rural_n = result["groups"]["rural"]["n"]
    urban_n = result["groups"]["urban"]["n"]
    adequate_support = rural_n >= 100 and urban_n >= 100
    gap = result["absolute_median_gap"]
    gap_pass = adequate_support and gap is not None and gap >= 0.001
    table = _output_table(official, residual, residual_defined, pct_urban, result)
    def serialize() -> bytes:
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False, write_statistics=False)
        return sink.getvalue().to_pybytes()
    payload = serialize()
    if payload != serialize():
        raise ValueError("rural/urban artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    report = {
        "schema_version": 1,
        "audit": "Provided rural/urban descriptive screen of frozen cross-fitted S1 Queen/Rook disagreement residual; unregistered and not a target-error claim",
        "method": {
            "residual_source": "Frozen orthogonalized Queen/Rook disagreement residual; consumed as emitted, not refit or recomputed.",
            "rural_definition": "Provided pct_urban < 0.5 is rural; pct_urban >= 0.5 is urban. Missing/nonfinite pct_urban is excluded from both groups.",
            "interaction": "crossfit_residual × 1[pct_urban < 0.5]; null unless both residual and rural/urban mapping are defined.",
            "metric": "Absolute difference between rural and urban medians of absolute cross-fitted residuals; descriptive only, not target error.",
            "nulls": "Preserve source residual nulls; do not impute missing pct_urban or residuals.",
        },
        "measure_before": {"maximum_within_region_residual_nmi": 0.4904168102022829,
                           "crossfit_residual_variance_ratio": 1.0244785067819135,
                           "residual_coverage_fraction": 1.0, "rural_urban_contrast_measured": False,
                           "source_report_sha256": sha256_file(RESIDUAL_REPORT),
                           "source_residual_artifact_sha256": RESIDUAL_SHA},
        "scope": {"official_ids": len(official), "exact_id_partition": True,
                  "residual_defined_ids": int(residual_defined.sum()),
                  "pct_urban_defined_ids": int(np.isfinite(pct_urban).sum()),
                  "interaction_defined_ids": int(result["joint_defined"].sum()),
                  "residual_defined_without_urbanity": result["residual_defined_without_urbanity"],
                  "urbanity_defined_without_residual": result["urbanity_defined_without_residual"],
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_reference_data_read": False, "external_inputs_read": False,
                  "leaderboard_feedback_used": False, "registry_integration": False, "uploads": 0},
        "measure_after": {"groups": result["groups"], "absolute_median_absolute_residual_gap": gap,
                          "rural_minus_urban_median_absolute_residual": (
                              result["groups"]["rural"]["median_absolute_residual"] - result["groups"]["urban"]["median_absolute_residual"]
                              if gap is not None else None),
                          "interaction_distribution": _distribution(result["interaction"][result["joint_defined"]]),
                          "group_support_at_least_100_each": adequate_support,
                          "absolute_median_gap_at_least_0_001": bool(gap_pass)},
        "acceptance": {"exact_id_and_stratum_alignment": True,
                       "both_groups_at_least_100": adequate_support,
                       "absolute_median_gap_at_least_0_001": bool(gap_pass),
                       "descriptive_gate_pass": bool(adequate_support and gap_pass)},
        "provenance": {"official_geoid_region_sha256": official_sha,
                       "source_report": str(RESIDUAL_REPORT), "source_report_sha256": sha256_file(RESIDUAL_REPORT),
                       "source_artifact": str(residual_path), "source_artifact_sha256": sha256_file(residual_path),
                       "strata_inputs": strata_manifest,
                       "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                           "sha256": hashlib.sha256(payload).hexdigest(),
                                           "columns": table.column_names},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "Descriptive rural/urban difference meets the preregistered support and magnitude gates; this is not evidence about target error." if gap_pass else
                         "Descriptive/inconclusive only: rural/urban group support or absolute median gap did not meet the preregistered threshold.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/s1-orthogonalized-rural-strata.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "s1-orthogonalized-rural-strata" / "rural-strata-interaction.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"acceptance": report["acceptance"], "measure_after": report["measure_after"],
                      "output_artifact": report["provenance"]["output_artifact"], "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
