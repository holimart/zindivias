#!/usr/bin/env python3
"""Screen a bounded Tm2 fire-loss discount using registered Tm3 ACS housing growth."""
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
MIN_Q95_CHANGE = 0.01


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def recovery_adjustment(tm2_gap: np.ndarray, tm2_defined: np.ndarray,
                        tm3_housing_growth: np.ndarray, tm3_defined: np.ndarray
                        ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Apply gap * (1 - clip(max(growth, 0), 0, 1)) only on jointly defined rows."""
    gap = np.asarray(tm2_gap, dtype=np.float64)
    gap_mask = np.asarray(tm2_defined, dtype=bool)
    growth = np.asarray(tm3_housing_growth, dtype=np.float64)
    growth_mask = np.asarray(tm3_defined, dtype=bool)
    if gap.ndim != 1 or growth.ndim != 1 or gap.shape != growth.shape \
            or gap_mask.shape != gap.shape or growth_mask.shape != gap.shape:
        raise ValueError("Tm2/Tm3 values and defined masks must be aligned one-dimensional arrays")
    if np.any(gap_mask & (~np.isfinite(gap) | (gap < 0.0) | (gap > 1.0))):
        raise ValueError("defined Tm2 gaps must be finite and in [0,1]")
    if np.any(growth_mask & ~np.isfinite(growth)):
        raise ValueError("defined Tm3 housing growth must be finite")
    joint = gap_mask & growth_mask
    capped_growth = np.full(gap.shape, np.nan, dtype=np.float64)
    capped_growth[joint] = np.clip(np.maximum(growth[joint], 0.0), 0.0, 1.0)
    adjusted = np.full(gap.shape, np.nan, dtype=np.float64)
    adjusted[joint] = gap[joint] * (1.0 - capped_growth[joint])
    if np.any(joint & (~np.isfinite(adjusted) | (adjusted < 0.0) | (adjusted > 1.0))):
        raise AssertionError("recovery-adjusted gap escaped finite [0,1] bounds")
    if np.any(joint & (adjusted > gap + 1e-15)):
        raise AssertionError("nonnegative growth adjustment increased a gap")
    return adjusted, joint, capped_growth


def distribution(values: np.ndarray) -> dict[str, Any]:
    x = np.asarray(values, dtype=np.float64)
    if x.ndim != 1 or (x.size and not np.isfinite(x).all()):
        raise ValueError("distribution requires finite one-dimensional values")
    if not x.size:
        return {"n": 0, "min": None, "p50": None, "p90": None,
                "p95": None, "max": None, "mean": None}
    return {"n": int(x.size), "min": float(x.min()), "p50": float(np.quantile(x, .50)),
            "p90": float(np.quantile(x, .90)), "p95": float(np.quantile(x, .95)),
            "max": float(x.max()), "mean": float(x.mean())}


def emission(table: pa.Table, *, idea: str, component: str, channel: str,
             geoids: list[str], regions: list[str]) -> tuple[np.ndarray, np.ndarray]:
    rows = [row for row in table.to_pylist()
            if row["idea"] == idea and row["component"] == component and row["channel"] == channel]
    if len(rows) != len(geoids):
        raise ValueError(f"{idea}/{component}/{channel}: expected exactly {len(geoids)} emission rows")
    if [str(row["GEOID"]) for row in rows] != geoids:
        raise ValueError(f"{idea}/{component}/{channel}: GEOID order differs from official partition")
    if [str(row["region"]) for row in rows] != regions:
        raise ValueError(f"{idea}/{component}/{channel}: region values differ from official partition")
    values = np.asarray([float(row["value"]) for row in rows], dtype=np.float64)
    defined = np.asarray([bool(row["defined"]) for row in rows], dtype=bool)
    if np.any(defined & ~np.isfinite(values)):
        raise ValueError(f"{idea}/{component}/{channel}: defined emission has nonfinite value")
    return values, defined


def output_table(geoids: list[str], regions: list[str], gap: np.ndarray, gap_defined: np.ndarray,
                 growth: np.ndarray, growth_defined: np.ndarray, fire_share: np.ndarray,
                 fire_defined: np.ndarray, adjusted: np.ndarray, adjusted_defined: np.ndarray,
                 capped_growth: np.ndarray) -> pa.Table:
    n = len(geoids)
    columns = (gap, gap_defined, growth, growth_defined, fire_share, fire_defined,
               adjusted, adjusted_defined, capped_growth)
    if len(regions) != n or any(np.asarray(column).shape != (n,) for column in columns):
        raise ValueError("output arrays do not align with official GEOIDs")
    if not np.array_equal(adjusted_defined, gap_defined & growth_defined):
        raise ValueError("adjusted output must be defined only on the Tm2/Tm3 intersection")
    if np.any(adjusted_defined & (adjusted > gap + 1e-15)):
        raise ValueError("adjusted gap must not exceed its Tm2 baseline")
    return pa.table({
        "GEOID": pa.array(geoids, type=pa.string()),
        "region": pa.array(regions, type=pa.string()),
        "tm2_building_gap": pa.array([float(gap[i]) if gap_defined[i] else None for i in range(n)],
                                      type=pa.float64()),
        "tm2_defined": pa.array(gap_defined, type=pa.bool_()),
        "tm3_acs_housing_growth": pa.array([float(growth[i]) if growth_defined[i] else None
                                             for i in range(n)], type=pa.float64()),
        "tm3_defined": pa.array(growth_defined, type=pa.bool_()),
        "tm2_postfire_burn_share": pa.array([float(fire_share[i]) if fire_defined[i] else None
                                              for i in range(n)], type=pa.float64()),
        "fire_status_defined": pa.array(fire_defined, type=pa.bool_()),
        "fire_affected": pa.array([bool(fire_defined[i] and fire_share[i] > 0.0)
                                    for i in range(n)], type=pa.bool_()),
        "clipped_recovery_growth": pa.array([float(capped_growth[i]) if adjusted_defined[i] else None
                                              for i in range(n)], type=pa.float64()),
        "recovery_adjusted_tm2_gap": pa.array([float(adjusted[i]) if adjusted_defined[i] else None
                                                for i in range(n)], type=pa.float64()),
        "adjusted_defined": pa.array(adjusted_defined, type=pa.bool_()),
    })


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def summarize_group(label: str, group: np.ndarray, joint: np.ndarray, gap: np.ndarray,
                    adjusted: np.ndarray) -> dict[str, Any]:
    mask = np.asarray(group, dtype=bool) & joint
    delta = np.abs(adjusted[mask] - gap[mask])
    return {"joint_defined_ids": int(mask.sum()),
            "tm2_gap": distribution(gap[mask]),
            "recovery_adjusted_gap": distribution(adjusted[mask]),
            "absolute_change": distribution(delta)}


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    source_summary_path = ROOT / "reports/bias60-family-tm-summary.json"
    status_path = ROOT / "config/bias60-family-tm-status.json"
    source_summary = json.loads(source_summary_path.read_text(encoding="utf-8"))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status["ideas"]["Tm1"]["status"] != "pending_policy_review" \
            or status["ideas"]["Tm4"]["status"] != "pending_policy_review":
        raise ValueError("Tm1/Tm4 policy-pending guard changed; this screen must use only Tm2/Tm3")
    official = official_scored_pairs(workspace)
    if len(official) != 9379 or len({geoid for geoid, _ in official}) != 9379:
        raise ValueError("official scored list is not an exact 9,379-GEOID partition")
    geoids = [geoid for geoid, _ in official]
    regions = [region for _, region in official]
    if set(regions) != set(REGIONS):
        raise ValueError("official scored list contains an unexpected region")

    artifacts: dict[str, Path] = {idea: workspace / "ideas" / f"{idea}.parquet"
                                  for idea in ("Tm2", "Tm3")}
    expected_hashes = {idea: source_summary["ideas"][idea]["sha256"] for idea in artifacts}
    tables: dict[str, pa.Table] = {}
    for idea, path in artifacts.items():
        if not path.is_file() or sha256_file(path) != expected_hashes[idea]:
            raise ValueError(f"{idea} artifact absent or hash differs from frozen family-Tm summary")
        tables[idea] = pq.read_table(path, columns=["GEOID", "region", "idea", "component",
                                                    "channel", "value", "defined"])
    tm2_gap, tm2_defined = emission(tables["Tm2"], idea="Tm2", component="B", channel="gap",
                                    geoids=geoids, regions=regions)
    fire_share, fire_defined = emission(
        tables["Tm2"], idea="Tm2", component="F", channel="covariate:postfire_burn_share",
        geoids=geoids, regions=regions)
    growth, growth_defined = emission(
        tables["Tm3"], idea="Tm3", component="B", channel="covariate:acs_housing_growth",
        geoids=geoids, regions=regions)
    if not np.array_equal(tm2_defined, fire_defined):
        raise ValueError("Tm2 postfire classification and building-gap defined masks differ")
    if np.any(tm2_defined & ((tm2_gap < 0.0) | (tm2_gap > 1.0))):
        raise ValueError("Tm2 defined building gaps are outside [0,1]")
    if np.any(fire_defined & ((fire_share < 0.0) | (fire_share > 1.0))):
        raise ValueError("Tm2 postfire burn-share covariate is outside [0,1]")

    adjusted, adjusted_defined, capped_growth = recovery_adjustment(
        tm2_gap, tm2_defined, growth, growth_defined)
    joint = tm2_defined & growth_defined
    delta = np.abs(adjusted[joint] - tm2_gap[joint])
    change_stats = distribution(delta)
    q95_change = change_stats["p95"]
    fire_affected = fire_defined & (fire_share > 0.0)
    non_fire = fire_defined & (fire_share <= 0.0)
    by_fire_status = {
        "fire_affected_postfire_burn_share_gt_0": summarize_group("fire", fire_affected, joint,
                                                                 tm2_gap, adjusted),
        "non_fire_postfire_burn_share_eq_0": summarize_group("nonfire", non_fire, joint,
                                                              tm2_gap, adjusted),
    }
    by_region = {}
    for region in REGIONS:
        region_mask = np.asarray([value == region for value in regions], dtype=bool)
        fire_mask = fire_affected & region_mask
        nonfire_mask = non_fire & region_mask
        by_region[region] = {
            "official_ids": int(region_mask.sum()),
            "tm2_defined_ids": int((tm2_defined & region_mask).sum()),
            "tm3_housing_growth_defined_ids": int((growth_defined & region_mask).sum()),
            "joint_defined_ids": int((joint & region_mask).sum()),
            "fire_affected_ids": int(fire_mask.sum()),
            "non_fire_ids": int(nonfire_mask.sum()),
            "tm2_gap": distribution(tm2_gap[joint & region_mask]),
            "tm3_acs_housing_growth": distribution(growth[joint & region_mask]),
            "recovery_adjusted_gap": distribution(adjusted[joint & region_mask]),
            "absolute_change": distribution(np.abs(adjusted[joint & region_mask]
                                                   - tm2_gap[joint & region_mask])),
            "fire_affected": summarize_group("fire", fire_mask, joint, tm2_gap, adjusted),
            "non_fire": summarize_group("nonfire", nonfire_mask, joint, tm2_gap, adjusted),
        }

    observed_defined_tm2 = int(tm2_defined.sum())
    observed_defined_tm3 = int(growth_defined.sum())
    joint_count = int(joint.sum())
    if observed_defined_tm2 != source_summary["ideas"]["Tm2"]["emissions"][0]["defined_rows"]:
        raise ValueError("Tm2 gap coverage disagrees with frozen family summary")
    if observed_defined_tm3 != source_summary["ideas"]["Tm3"]["emissions"][0]["defined_rows"]:
        raise ValueError("Tm3 ACS growth coverage disagrees with frozen family summary")
    if joint_count != int(adjusted_defined.sum()):
        raise AssertionError("adjusted feature mask differs from joint source mask")

    table = output_table(geoids, regions, tm2_gap, tm2_defined, growth, growth_defined,
                         fire_share, fire_defined, adjusted, adjusted_defined, capped_growth)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("adjustment artifact serialization is not deterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    all_outputs_bounded = bool(np.isfinite(adjusted[joint]).all()
                               and np.all((adjusted[joint] >= 0.0) & (adjusted[joint] <= 1.0)))
    monotonic = bool(np.all(adjusted[joint] <= tm2_gap[joint] + 1e-15))
    exact_aligned = bool(len(geoids) == 9379 and len(set(geoids)) == 9379
                         and table.num_rows == len(geoids)
                         and np.array_equal(adjusted_defined, tm2_defined & growth_defined))
    acceptance = {
        "exact_9379_id_alignment": exact_aligned,
        "all_defined_adjusted_outputs_finite_and_in_0_1": all_outputs_bounded,
        "monotonic_discount_never_increases_tm2_gap": monotonic,
        "q95_absolute_change_at_least_0_01": bool(q95_change is not None and q95_change >= MIN_Q95_CHANGE),
        "q95_absolute_change_threshold": MIN_Q95_CHANGE,
        "q95_absolute_change": q95_change,
        "acceptance_pass": bool(exact_aligned and all_outputs_bounded and monotonic
                                and q95_change is not None and q95_change >= MIN_Q95_CHANGE),
    }
    report = {
        "schema_version": 1,
        "screen": "Tm2 postfire building-gap adjusted by bounded Tm3 ACS housing growth; diagnostic only",
        "formula": "adjusted_gap = Tm2_gap * (1 - clip(max(Tm3_ACS_housing_growth, 0), 0, 1))",
        "missingness": "Adjusted value is undefined unless both registered source emissions are defined; no missing growth is filled with zero, and Tm2 remains unchanged in its source artifact.",
        "fire_status_definition": "Fire-affected iff the registered Tm2 postfire_burn_share covariate is defined and >0; non-fire iff defined and equal to 0.",
        "scope": {"official_ids": len(geoids), "exact_id_alignment": exact_aligned,
                  "tm2_gap_defined_ids": observed_defined_tm2,
                  "tm2_gap_undefined_ids": len(geoids) - observed_defined_tm2,
                  "tm3_acs_growth_defined_ids": observed_defined_tm3,
                  "tm3_acs_growth_undefined_ids": len(geoids) - observed_defined_tm3,
                  "jointly_defined_ids": joint_count,
                  "adjusted_undefined_ids": len(geoids) - joint_count,
                  "adjusted_coverage_fraction": joint_count / len(geoids),
                  "fire_status_defined_ids": int(fire_defined.sum()),
                  "fire_affected_tm2_defined_ids": int(fire_affected.sum()),
                  "non_fire_tm2_defined_ids": int(non_fire.sum()),
                  "joint_fire_affected_ids": int((fire_affected & joint).sum()),
                  "joint_non_fire_ids": int((non_fire & joint).sum()),
                  "labels_read": False, "sample_score_values_read": False,
                  "Tm1_or_Tm4_inputs_used": False, "candidate_modified": False,
                  "frozen_Tm2_or_Tm3_artifact_modified": False, "uploads": 0},
        "before": {"Tm2_building_gap": {"defined_ids": observed_defined_tm2,
                                           "official_ids": len(geoids),
                                           "coverage_fraction": observed_defined_tm2 / len(geoids),
                                           "distribution": distribution(tm2_gap[tm2_defined])},
                   "Tm3_acs_housing_growth": {"defined_ids": observed_defined_tm3,
                                               "official_ids": len(geoids),
                                               "coverage_fraction": observed_defined_tm3 / len(geoids),
                                               "distribution": distribution(growth[growth_defined])}},
        "after": {"joint_defined_ids": joint_count,
                  "adjusted_gap_distribution": distribution(adjusted[joint]),
                  "absolute_change_from_tm2": change_stats,
                  "nonzero_absolute_change_ids": int(np.count_nonzero(delta > 0.0)),
                  "nonzero_absolute_change_fraction": float(np.mean(delta > 0.0)) if delta.size else 0.0,
                  "fire_status": by_fire_status,
                  "regions": by_region},
        "acceptance": acceptance,
        "provenance": {"official_geoid_region_sha256": hashlib.sha256(
                           json.dumps(official, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
                       "tm2_artifact": {"path": str(artifacts["Tm2"]),
                                        "sha256": sha256_file(artifacts["Tm2"])},
                       "tm3_artifact": {"path": str(artifacts["Tm3"]),
                                        "sha256": sha256_file(artifacts["Tm3"])},
                       "family_tm_summary_sha256": sha256_file(source_summary_path),
                       "tm_status_sha256": sha256_file(status_path),
                       "selected_emissions": ["Tm2|B|gap", "Tm2|F|covariate:postfire_burn_share",
                                              "Tm3|B|covariate:acs_housing_growth"]},
        "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "deterministic_bytes": True},
        "interpretation": ("All declared coverage/bounds/alignment and material-change criteria pass. This remains a separate diagnostic; do not integrate automatically."
                           if acceptance["acceptance_pass"] else
                           "The q95 absolute-change acceptance gate fails or an invariant failed. Retain frozen Tm2 unchanged; do not integrate this adjustment."),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                           encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm2-fire-recovery-adjustment.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    output = Path(args.artifact) if args.artifact else workspace / "tm2-fire-recovery-adjustment" / "adjusted-tm2.parquet"
    report_path = ROOT / args.out
    report = run(workspace, report_path, output)
    print(json.dumps({"scope": report["scope"], "acceptance": report["acceptance"],
                      "artifact": report["output_artifact"], "report": str(report_path.relative_to(ROOT))},
                     indent=2, sort_keys=True))
    return 0 if report["acceptance"]["acceptance_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
