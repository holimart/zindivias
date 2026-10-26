#!/usr/bin/env python3
"""Screen a preregistered log-scaled recovery discount of the frozen Tm2 gap."""
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
RECOVERY_Q95 = 0.2567608458
MIN_FIRE_Q95_CHANGE = 0.01


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def log_recovery_adjustment(tm2_gap: np.ndarray, tm2_defined: np.ndarray,
                            tm3_growth: np.ndarray, tm3_defined: np.ndarray,
                            normalizer: float = RECOVERY_Q95
                            ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return gap*(1-min(log1p(max(growth,0))/log1p(q95),1)) on joint rows only."""
    gap = np.asarray(tm2_gap, dtype=np.float64)
    gap_mask = np.asarray(tm2_defined, dtype=bool)
    growth = np.asarray(tm3_growth, dtype=np.float64)
    growth_mask = np.asarray(tm3_defined, dtype=bool)
    if gap.ndim != 1 or growth.ndim != 1 or gap.shape != growth.shape \
            or gap_mask.shape != gap.shape or growth_mask.shape != gap.shape:
        raise ValueError("Tm2/Tm3 values and defined masks must be aligned one-dimensional arrays")
    if not np.isfinite(normalizer) or normalizer <= 0.0:
        raise ValueError("log recovery normalizer must be positive and finite")
    if np.any(gap_mask & (~np.isfinite(gap) | (gap < 0.0) | (gap > 1.0))):
        raise ValueError("defined Tm2 gaps must be finite and in [0,1]")
    if np.any(growth_mask & ~np.isfinite(growth)):
        raise ValueError("defined Tm3 housing growth must be finite")
    joint = gap_mask & growth_mask
    adjusted = np.full(gap.shape, np.nan, dtype=np.float64)
    discount = np.full(gap.shape, np.nan, dtype=np.float64)
    if joint.any():
        nonnegative_growth = np.maximum(growth[joint], 0.0)
        discount[joint] = np.minimum(np.log1p(nonnegative_growth) / np.log1p(normalizer), 1.0)
        adjusted[joint] = gap[joint] * (1.0 - discount[joint])
    if np.any(joint & (~np.isfinite(adjusted) | (adjusted < 0.0) | (adjusted > 1.0))):
        raise AssertionError("log-recovery-adjusted gap escaped finite [0,1] bounds")
    if np.any(joint & ((discount < 0.0) | (discount > 1.0))):
        raise AssertionError("recovery discount escaped [0,1]")
    if np.any(joint & (adjusted > gap + 1e-15)):
        raise AssertionError("nonnegative recovery growth increased a Tm2 gap")
    return adjusted, joint, discount


def distribution(values: np.ndarray) -> dict[str, Any]:
    x = np.asarray(values, dtype=np.float64)
    if x.ndim != 1 or (x.size and not np.isfinite(x).all()):
        raise ValueError("distribution requires finite one-dimensional values")
    if not x.size:
        return {"n": 0, "min": None, "p50": None, "p90": None, "p95": None,
                "max": None, "mean": None}
    return {"n": int(x.size), "min": float(x.min()), "p50": float(np.quantile(x, .50)),
            "p90": float(np.quantile(x, .90)), "p95": float(np.quantile(x, .95)),
            "max": float(x.max()), "mean": float(x.mean())}


def read_emission(table: pa.Table, *, idea: str, component: str, channel: str,
                  official: list[tuple[str, str]]) -> tuple[np.ndarray, np.ndarray]:
    rows = [row for row in table.to_pylist()
            if row["idea"] == idea and row["component"] == component and row["channel"] == channel]
    if len(rows) != len(official):
        raise ValueError(f"{idea}/{component}/{channel}: expected {len(official)} official emission rows")
    ids = [str(row["GEOID"]) for row in rows]
    regions = [str(row["region"]) for row in rows]
    if list(zip(ids, regions)) != official:
        raise ValueError(f"{idea}/{component}/{channel}: rows do not exactly match official GEOID-region order")
    values = np.asarray([float(row["value"]) for row in rows], dtype=np.float64)
    defined = np.asarray([bool(row["defined"]) for row in rows], dtype=bool)
    if np.any(defined & ~np.isfinite(values)):
        raise ValueError(f"{idea}/{component}/{channel}: defined values must be finite")
    return values, defined


def output_table(official: list[tuple[str, str]], gap: np.ndarray, gap_defined: np.ndarray,
                 growth: np.ndarray, growth_defined: np.ndarray, fire_share: np.ndarray,
                 fire_defined: np.ndarray, adjusted: np.ndarray, adjusted_defined: np.ndarray,
                 discount: np.ndarray) -> pa.Table:
    n = len(official)
    arrays = (gap, gap_defined, growth, growth_defined, fire_share, fire_defined,
              adjusted, adjusted_defined, discount)
    if any(np.asarray(array).shape != (n,) for array in arrays):
        raise ValueError("output arrays must align with official GEOID-region pairs")
    if not np.array_equal(adjusted_defined, gap_defined & growth_defined):
        raise ValueError("adjusted feature must be defined exactly on the joint source mask")
    geoids = [pair[0] for pair in official]
    regions = [pair[1] for pair in official]
    fire_affected = fire_defined & (fire_share > 0.0)
    return pa.table({
        "GEOID": pa.array(geoids, type=pa.string()),
        "region": pa.array(regions, type=pa.string()),
        "tm2_building_gap": pa.array([float(v) if d else None for v, d in zip(gap, gap_defined)], type=pa.float64()),
        "tm2_defined": pa.array(gap_defined, type=pa.bool_()),
        "tm3_acs_housing_growth": pa.array([float(v) if d else None for v, d in zip(growth, growth_defined)], type=pa.float64()),
        "tm3_defined": pa.array(growth_defined, type=pa.bool_()),
        "tm2_postfire_burn_share": pa.array([float(v) if d else None for v, d in zip(fire_share, fire_defined)], type=pa.float64()),
        "fire_status_defined": pa.array(fire_defined, type=pa.bool_()),
        "fire_affected": pa.array(fire_affected, type=pa.bool_()),
        "log_recovery_discount": pa.array([float(v) if d else None for v, d in zip(discount, adjusted_defined)], type=pa.float64()),
        "log_recovery_adjusted_tm2_gap": pa.array([float(v) if d else None for v, d in zip(adjusted, adjusted_defined)], type=pa.float64()),
        "adjusted_defined": pa.array(adjusted_defined, type=pa.bool_()),
    })


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue().to_pybytes()


def group_effects(mask: np.ndarray, joint: np.ndarray, gap: np.ndarray,
                  adjusted: np.ndarray) -> dict[str, Any]:
    selected = np.asarray(mask, dtype=bool) & joint
    change = np.abs(adjusted[selected] - gap[selected])
    return {"joint_defined_ids": int(selected.sum()),
            "tm2_gap": distribution(gap[selected]),
            "adjusted_gap": distribution(adjusted[selected]),
            "absolute_change": distribution(change),
            "changed_ids": int(np.count_nonzero(change > 0.0))}


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    summary_path = ROOT / "reports/bias60-family-tm-summary.json"
    status_path = ROOT / "config/bias60-family-tm-status.json"
    baseline_path = ROOT / "reports/tm2-fire-recovery-adjustment.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status["ideas"]["Tm1"]["status"] != "pending_policy_review" \
            or status["ideas"]["Tm4"]["status"] != "pending_policy_review":
        raise ValueError("Tm1/Tm4 must remain policy-pending and excluded")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if (baseline["scope"]["jointly_defined_ids"] != 9362
            or baseline["after"]["nonzero_absolute_change_ids"] != 95
            or not np.isclose(baseline["after"]["fire_status"]["fire_affected_postfire_burn_share_gt_0"]["absolute_change"]["p95"],
                              0.0020461453649303976, rtol=0, atol=1e-15)):
        raise ValueError("linear-recovery baseline no longer matches the issued measured-before values")
    official = official_scored_pairs(workspace)
    if len(official) != 9379 or len(set(official)) != 9379 or len({g for g, _ in official}) != 9379:
        raise ValueError("official scored list must be an exact 9,379 GEOID-region partition")
    if set(region for _, region in official) != set(REGIONS):
        raise ValueError("official scored list contains unexpected regions")

    paths = {idea: workspace / "ideas" / f"{idea}.parquet" for idea in ("Tm2", "Tm3")}
    expected_hashes = {idea: summary["ideas"][idea]["sha256"] for idea in paths}
    tables = {}
    for idea, path in paths.items():
        if not path.is_file() or sha256_file(path) != expected_hashes[idea]:
            raise ValueError(f"{idea} frozen artifact absent or hash differs from family summary")
        tables[idea] = pq.read_table(path, columns=["GEOID", "region", "idea", "component", "channel", "value", "defined"])
    gap, gap_defined = read_emission(tables["Tm2"], idea="Tm2", component="B", channel="gap", official=official)
    fire_share, fire_defined = read_emission(
        tables["Tm2"], idea="Tm2", component="F", channel="covariate:postfire_burn_share", official=official)
    growth, growth_defined = read_emission(
        tables["Tm3"], idea="Tm3", component="B", channel="covariate:acs_housing_growth", official=official)
    if not np.array_equal(gap_defined, fire_defined):
        raise ValueError("Tm2 fire-status and gap masks differ")
    if int(gap_defined.sum()) != summary["ideas"]["Tm2"]["emissions"][0]["defined_rows"]:
        raise ValueError("Tm2 coverage disagrees with frozen family summary")
    if int(growth_defined.sum()) != summary["ideas"]["Tm3"]["emissions"][0]["defined_rows"]:
        raise ValueError("Tm3 coverage disagrees with frozen family summary")
    if np.any(gap_defined & ((gap < 0.0) | (gap > 1.0))):
        raise ValueError("Tm2 defined gap outside [0,1]")
    if np.any(fire_defined & ((fire_share < 0.0) | (fire_share > 1.0))):
        raise ValueError("Tm2 fire burn share outside [0,1]")

    adjusted, joint, discount = log_recovery_adjustment(gap, gap_defined, growth, growth_defined)
    if int(joint.sum()) != 9362:
        raise ValueError("joint Tm2/Tm3 coverage differs from the preregistered 9,362 IDs")
    delta = np.abs(adjusted[joint] - gap[joint])
    fire_affected = fire_defined & (fire_share > 0.0)
    non_fire = fire_defined & (fire_share == 0.0)
    fire_joint = fire_affected & joint
    nonfire_joint = non_fire & joint
    fire_change = np.abs(adjusted[fire_joint] - gap[fire_joint])
    if int(fire_joint.sum()) != 381:
        raise ValueError("fire-affected joint count differs from the issued 381-ID baseline")
    global_effects = {"joint_defined_ids": int(joint.sum()),
                      "tm2_gap": distribution(gap[joint]),
                      "adjusted_gap": distribution(adjusted[joint]),
                      "absolute_change": distribution(delta),
                      "changed_ids": int(np.count_nonzero(delta > 0.0)),
                      "changed_fraction": float(np.mean(delta > 0.0))}
    fire_conditional = group_effects(fire_affected, joint, gap, adjusted)
    non_fire_effects = group_effects(non_fire, joint, gap, adjusted)
    regions = {}
    for region in REGIONS:
        local = np.asarray([r == region for _, r in official], dtype=bool)
        local_joint = joint & local
        local_fire = fire_affected & local
        local_fire_joint = local_fire & joint
        local_delta = np.abs(adjusted[local_joint] - gap[local_joint])
        regions[region] = {
            "official_ids": int(local.sum()),
            "tm2_gap_defined_ids": int((gap_defined & local).sum()),
            "tm3_growth_defined_ids": int((growth_defined & local).sum()),
            "joint_defined_ids": int(local_joint.sum()),
            "fire_status_defined_ids": int((fire_defined & local).sum()),
            "fire_affected_ids": int(local_fire.sum()),
            "joint_fire_affected_ids": int(local_fire_joint.sum()),
            "non_fire_ids": int((non_fire & local).sum()),
            "changed_ids": int(np.count_nonzero(local_delta > 0.0)),
            "global_absolute_change": distribution(local_delta),
            "fire_affected": group_effects(local_fire, joint, gap, adjusted),
            "non_fire": group_effects(non_fire & local, joint, gap, adjusted),
        }

    table = output_table(official, gap, gap_defined, growth, growth_defined, fire_share,
                         fire_defined, adjusted, joint, discount)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("log-adjusted artifact serialization is not deterministic")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    bounded = bool(np.isfinite(adjusted[joint]).all()
                   and np.all((adjusted[joint] >= 0.0) & (adjusted[joint] <= 1.0)))
    monotone = bool(np.all(adjusted[joint] <= gap[joint] + 1e-15))
    exact_ids = bool(table.num_rows == 9379 and len(set(official)) == 9379
                     and np.array_equal(joint, gap_defined & growth_defined))
    fire_p95 = float(np.quantile(fire_change, .95)) if fire_change.size else None
    acceptance = {
        "exact_9379_id_alignment": exact_ids,
        "adjusted_output_defined_exactly_on_joint_source_mask": bool(np.array_equal(joint, gap_defined & growth_defined)),
        "adjusted_outputs_finite_and_bounded_0_to_1": bounded,
        "adjustment_monotone_nonincreasing_in_nonnegative_growth": monotone,
        "fire_affected_joint_ids": int(fire_joint.sum()),
        "fire_conditional_p95_absolute_change": fire_p95,
        "fire_conditional_p95_at_least_0_01": bool(fire_p95 is not None and fire_p95 >= MIN_FIRE_Q95_CHANGE),
        "minimum_fire_conditional_p95_absolute_change": MIN_FIRE_Q95_CHANGE,
        "acceptance_pass": bool(exact_ids and bounded and monotone and fire_p95 is not None
                                 and fire_p95 >= MIN_FIRE_Q95_CHANGE),
    }
    final_artifact_hash = sha256_file(artifact_path)
    input_hashes_after = {idea: sha256_file(path) for idea, path in paths.items()}
    if input_hashes_after != expected_hashes:
        raise AssertionError("frozen Tm2/Tm3 input artifact changed during the screen")
    report = {
        "schema_version": 1,
        "screen": "Tm2 postfire building-gap log-scaled recovery discount; label-free diagnostic only",
        "formula": "adjusted_gap = Tm2_gap * (1 - min(log1p(max(Tm3_ACS_housing_growth, 0)) / log1p(0.2567608458), 1))",
        "normalizer": {"tm3_growth_q95": RECOVERY_Q95, "source": "pre-measured Tm3 ACS-growth q95; fixed, no fitted parameter"},
        "fire_status_definition": "Fire-affected iff provided Tm2 postfire_burn_share is defined and >0; non-fire iff defined and equal to 0.",
        "missingness": "Adjusted value is null unless both provided Tm2 gap and Tm3 ACS-growth emissions are defined. No missing growth is filled; frozen Tm2 remains unchanged.",
        "scope": {
            "official_ids": len(official), "exact_id_alignment": exact_ids,
            "tm2_gap_defined_ids": int(gap_defined.sum()), "tm3_growth_defined_ids": int(growth_defined.sum()),
            "joint_defined_ids": int(joint.sum()), "adjusted_undefined_ids": int(len(official) - joint.sum()),
            "adjusted_coverage_fraction": float(joint.mean()),
            "fire_status_defined_ids": int(fire_defined.sum()),
            "fire_affected_tm2_defined_ids": int(fire_affected.sum()),
            "joint_fire_affected_ids": int(fire_joint.sum()), "joint_non_fire_ids": int(nonfire_joint.sum()),
            "labels_read": False, "sample_score_values_read": False,
            "Tm1_or_Tm4_inputs_used": False, "removed_or_equivalent_reference_data_read": False,
            "leaderboard_values_used": False, "candidate_modified": False,
            "frozen_Tm2_or_Tm3_modified": False, "uploads": 0,
        },
        "measure_before": {
            "Tm2_gap_defined_ids": int(gap_defined.sum()),
            "Tm2_gap_distribution": distribution(gap[gap_defined]),
            "Tm3_growth_defined_ids": int(growth_defined.sum()),
            "Tm3_growth_distribution": distribution(growth[growth_defined]),
            "prior_linear_interaction": {
                "global_joint_defined_ids": baseline["after"]["joint_defined_ids"],
                "global_changed_ids": baseline["after"]["nonzero_absolute_change_ids"],
                "global_absolute_change_p95": baseline["after"]["absolute_change_from_tm2"]["p95"],
                "fire_affected_joint_ids": baseline["after"]["fire_status"]["fire_affected_postfire_burn_share_gt_0"]["joint_defined_ids"],
                "fire_affected_absolute_change_p95": baseline["after"]["fire_status"]["fire_affected_postfire_burn_share_gt_0"]["absolute_change"]["p95"],
            },
        },
        "measure_after": {
            "global": global_effects,
            "fire_affected": fire_conditional,
            "non_fire": non_fire_effects,
            "regions": regions,
        },
        "acceptance": acceptance,
        "provenance": {
            "official_geoid_region_sha256": hashlib.sha256(
                json.dumps(official, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "frozen_inputs": {idea: {"path": str(paths[idea]), "sha256": expected_hashes[idea],
                                     "sha256_after": input_hashes_after[idea]} for idea in paths},
            "family_tm_summary_sha256": sha256_file(summary_path),
            "tm_policy_status_sha256": sha256_file(status_path),
            "prior_linear_screen_report_sha256": sha256_file(baseline_path),
            "selected_emissions": ["Tm2|B|gap", "Tm2|F|covariate:postfire_burn_share",
                                   "Tm3|B|covariate:acs_housing_growth"],
            "output_artifact": {"path": str(artifact_path), "rows": table.num_rows,
                                "sha256": final_artifact_hash, "deterministic_bytes": True,
                                "columns": table.column_names},
            "script_sha256": sha256_file(Path(__file__).resolve()),
        },
        "interpretation": "The fire-conditional q95 gate passes; retain this bounded adjustment as a separate diagnostic only, with no accuracy/promotion claim and no automatic change to frozen Tm2." if acceptance["acceptance_pass"] else
        "The fire-conditional q95 absolute-change gate fails or an invariant failed; retain frozen Tm2 unchanged and do not integrate the log-scaled adjustment.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/tm2-fire-recovery-log-scale.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report_path = ROOT / args.out
    artifact_path = Path(args.artifact) if args.artifact else workspace / "tm2-fire-recovery-log-scale" / "adjusted-tm2-log.parquet"
    report = run(workspace, report_path, artifact_path)
    print(json.dumps({"global": report["measure_after"]["global"],
                      "fire_affected": report["measure_after"]["fire_affected"],
                      "acceptance": report["acceptance"],
                      "output_artifact": report["provenance"]["output_artifact"],
                      "report": str(report_path.relative_to(ROOT))}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
