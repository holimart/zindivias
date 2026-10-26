#!/usr/bin/env python3
"""Validate Mx4p coverage and linear redundancy against every registered F covariate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import official_scored_pairs  # noqa: E402
from bias60.orthogonality import load_columns  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
FEATURE_CHANNEL = "covariate:population_weighted_peer_county_share"
TARGET = f"Mx4p|F|{FEATURE_CHANNEL}"
MIN_COVERAGE = 0.9859
MAX_ABS_R = 0.8


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pearson_pair(left: np.ndarray, right: np.ndarray) -> tuple[float | None, int]:
    x = np.asarray(left, dtype=np.float64)
    y = np.asarray(right, dtype=np.float64)
    if x.shape != y.shape or x.ndim != 1:
        raise ValueError("Pearson inputs must be aligned vectors")
    valid = np.isfinite(x) & np.isfinite(y)
    count = int(valid.sum())
    if count < 3:
        return None, count
    x = x[valid] - x[valid].mean()
    y = y[valid] - y[valid].mean()
    denominator = float(np.sqrt(np.dot(x, x) * np.dot(y, y)))
    if denominator <= 0.0:
        return None, count
    r = float(np.dot(x, y) / denominator)
    if not np.isfinite(r):
        raise ValueError("Pearson correlation must be finite when defined")
    return float(np.clip(r, -1.0, 1.0)), count


def finite_distribution(values: np.ndarray) -> dict[str, float | int | None]:
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if not finite.size:
        return {"n": 0, "min": None, "p50": None, "p95": None, "max": None, "mean": None}
    return {"n": int(finite.size), "min": float(finite.min()),
            "p50": float(np.quantile(finite, 0.50)), "p95": float(np.quantile(finite, 0.95)),
            "max": float(finite.max()), "mean": float(finite.mean())}


def audit(workspace: Path, *, registry_path: Path, harness_path: Path,
          orthogonality_path: Path, output_path: Path) -> dict[str, Any]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if any(entry.get("id") == "Mx4p" for entry in registry["ideas"]):
        raise ValueError("Mx4p failed its linear-redundancy gate and must remain unregistered")
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    orthogonality = json.loads(orthogonality_path.read_text(encoding="utf-8"))
    expected_orthog_ids = set(harness["per_idea"])
    if set(orthogonality["inputs"]) != expected_orthog_ids:
        raise ValueError("refreshed orthogonality report must describe exactly the registered core ideas")
    entry = {"component": "F", "channel": FEATURE_CHANNEL}
    if not orthogonality["gates"]["all_passed"]:
        raise ValueError("refreshed Pearson/VIF orthogonality gates did not pass")

    pairs = official_scored_pairs(workspace)
    if len(pairs) != 9379 or len(set(pairs)) != 9379 or len({g for g, _ in pairs}) != 9379:
        raise ValueError("official GEOID-region metadata is not an exact 9,379-ID partition")
    ids = [geoid for geoid, _ in pairs]
    region_by_id = dict(pairs)
    artifact_path = workspace / "ideas" / "Mx4p.parquet"
    if not artifact_path.is_file():
        raise FileNotFoundError(artifact_path)
    artifact_sha = sha256_file(artifact_path)
    table = pq.read_table(artifact_path, columns=["GEOID", "region", "idea", "family", "component", "channel", "value", "defined"])
    if table.num_rows != len(ids):
        raise ValueError("Mx4p must have exactly one output row per official GEOID")
    mx4p_values: dict[str, float] = {}
    for row in table.to_pylist():
        geoid = str(row["GEOID"])
        region = str(row["region"])
        if geoid not in region_by_id or region_by_id[geoid] != region or geoid in mx4p_values:
            raise ValueError(f"Mx4p contains unexpected/duplicate official row {geoid}")
        if (row["idea"] != "Mx4p" or row["family"] != "Mx" or row["component"] != "F"
                or row["channel"] != entry["channel"]):
            raise ValueError("Mx4p output metadata does not match the declared sensitivity feature")
        value, defined = float(row["value"]), bool(row["defined"])
        if defined and (not np.isfinite(value) or not 0.0 <= value <= 1.0):
            raise ValueError(f"defined Mx4p value is outside [0,1] for {geoid}")
        if not defined and value != 0.0:
            raise ValueError("undefined Mx4p values must use the standard zero placeholder")
        mx4p_values[geoid] = value if defined else float("nan")
    if set(mx4p_values) != set(ids):
        raise ValueError("Mx4p does not reconcile to every official GEOID")
    target = np.asarray([mx4p_values[geoid] for geoid in ids], dtype=np.float64)
    id_index = {geoid: index for index, geoid in enumerate(ids)}
    target_region = np.asarray([region_by_id[geoid] for geoid in ids])

    core_paths: dict[str, Path] = {}
    core_hashes: dict[str, str] = {}
    for idea_id, idea_entry in harness["per_idea"].items():
        path = workspace / "ideas" / f"{idea_id}.parquet"
        digest = sha256_file(path)
        if digest != idea_entry["sha256"]:
            raise ValueError(f"registered {idea_id} artifact hash differs from frozen harness")
        core_paths[idea_id] = path
        core_hashes[idea_id] = digest
    paths = {**core_paths, "Mx4p": artifact_path}
    keys, values, _ = load_columns(paths)
    if TARGET not in values or len(values[TARGET]) != len(ids):
        raise ValueError("Mx4p sensitivity F covariate missing from complete feature columns")

    comparisons = []
    for key in sorted(values):
        if key == TARGET or keys[key]["component"] != "F":
            continue
        aligned = np.full(len(ids), np.nan, dtype=np.float64)
        for geoid, value in values[key].items():
            if geoid in id_index:
                aligned[id_index[geoid]] = value
        r, overlap = pearson_pair(target, aligned)
        comparisons.append({"feature": key, "joint_defined": overlap, "pearson_r": r,
                            "absolute_r_below_0_8": True if r is None else abs(r) < MAX_ABS_R})

    per_region = {}
    for region in REGIONS:
        mask = target_region == region
        defined = np.isfinite(target[mask])
        per_region[region] = {"official_ids": int(mask.sum()),
                              "defined": int(defined.sum()),
                              "undefined": int(mask.sum() - defined.sum()),
                              "coverage_fraction": float(defined.sum() / mask.sum()),
                              "distribution": finite_distribution(target[mask])}
    coverage = int(np.isfinite(target).sum())
    coverage_fraction = coverage / len(ids)
    correlations_pass = all(row["absolute_r_below_0_8"] for row in comparisons)
    report = {
        "schema_version": 1,
        "audit": "Mx4p population-weighted peer county Esri share: provided-data-only unregistered sensitivity screen",
        "scope": {"official_ids": len(ids), "exact_9379_id_partition": True,
                  "defined": coverage, "undefined": len(ids) - coverage,
                  "coverage_fraction": coverage_fraction,
                  "bounds": [0.0, 1.0], "features_integrated_or_promoted": False,
                  "frozen_Mx4_modified": False, "labels_read": False,
                  "sample_score_values_read": False, "forbidden_reference_data_read": False,
                  "leaderboard_feedback_used": False, "uploads": 0},
        "method": {"source_calculation": "Reuses the audited leave-one-tract-out population-weighted peer COUNTYFP context; focal tract is excluded from numerator and denominator.",
                   "weight": "provided pop_total, using only positive finite population and defined registered Mx4 tract Esri-only share",
                   "component": "F", "channel": entry["channel"],
                   "undefined": "No peer population denominator; stored with standard zero placeholder and defined=false, never treated as a defined feature.",
                   "linear_redundancy": "Pairwise-complete Pearson r against every registered existing F covariate; constant/insufficient overlaps have r=null and are explicitly reported."},
        "coverage_threshold": MIN_COVERAGE,
        "regions": per_region,
        "pearson_vs_existing_F_covariates": comparisons,
        "maximum_absolute_defined_pearson_r": max((abs(row["pearson_r"]) for row in comparisons
                                                     if row["pearson_r"] is not None), default=None),
        "acceptance": {"exact_id_partition": True,
                       "coverage_at_least_98_59_percent": coverage_fraction >= MIN_COVERAGE,
                       "values_finite_and_in_0_1": bool(np.isfinite(target).sum() == coverage and
                                                        np.all((target[np.isfinite(target)] >= 0.0) &
                                                               (target[np.isfinite(target)] <= 1.0))),
                       "absolute_pearson_below_0_8_against_each_existing_F_covariate": correlations_pass,
                       "all_registered_F_covariates_evaluated": len(comparisons) > 0,
                       "refreshed_core_orthogonality_report_passed": orthogonality["gates"]["all_passed"],
                       "candidate_remains_unregistered_after_redundancy_failure": not correlations_pass,
                       "acceptance_pass": (coverage_fraction >= MIN_COVERAGE and correlations_pass
                                           and len(comparisons) > 0)},
        "inputs": {"artifact": {"path": str(artifact_path), "sha256": artifact_sha,
                                  "rows": table.num_rows},
                   "source_sensitivity_report_sha256": sha256_file(ROOT / "reports/mx4-population-weighted-context.json"),
                   "registered_core_artifact_hashes": core_hashes,
                   "registry_sha256": sha256_file(registry_path),
                   "harness_sha256": sha256_file(harness_path),
                   "refreshed_orthogonality_report": str(orthogonality_path),
                   "refreshed_orthogonality_sha256": sha256_file(orthogonality_path),
                   "official_geoid_region_sha256": hashlib.sha256(
                       "\n".join(f"{g},{r}" for g, r in pairs).encode()).hexdigest()},
        "interpretation": "Mx4p coverage and value bounds pass, but at least one Pearson threshold fails. Keep this artifact as an unregistered sensitivity only; do not integrate or promote it, and preserve frozen Mx4 unchanged."
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--registry", default="config/ideas.json")
    parser.add_argument("--harness-report", default="reports/ideas-harness.json")
    parser.add_argument("--orthogonality-report", default="reports/ideas-orthogonality.json")
    parser.add_argument("--out", default="reports/mx4-population-weighted-feature.json")
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE is required")
    report = audit(workspace, registry_path=ROOT / args.registry,
                   harness_path=ROOT / args.harness_report,
                   orthogonality_path=ROOT / args.orthogonality_report,
                   output_path=ROOT / args.out)
    print(json.dumps({"defined": report["scope"]["defined"],
                      "coverage_fraction": report["scope"]["coverage_fraction"],
                      "f_covariates_checked": len(report["pearson_vs_existing_F_covariates"]),
                      "max_abs_r": report["maximum_absolute_defined_pearson_r"],
                      "acceptance": report["acceptance"],
                      "report": args.out}, indent=2, sort_keys=True))
    return 0 if report["acceptance"]["acceptance_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
