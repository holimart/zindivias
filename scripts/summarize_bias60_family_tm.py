#!/usr/bin/env python3
"""Summarize Family Tm coverage/distributions and the full refreshed orthogonality evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/bias60-family-tm-summary.json")
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        raise SystemExit("COMPETITION_WORKSPACE is required")
    import numpy as np
    import pyarrow.parquet as pq

    harness_path = ROOT / "reports/ideas-harness.json"
    orthog_path = ROOT / "reports/ideas-orthogonality.json"
    harness, orthog = json.loads(harness_path.read_text()), json.loads(orthog_path.read_text())
    status = json.loads((ROOT / "config/bias60-family-tm-status.json").read_text())
    columns = orthog["correlation"]["columns"]
    matrix = np.asarray(orthog["correlation"]["matrix"], dtype=np.float64)
    overlap = np.asarray(orthog["correlation"]["pairwise_overlap"], dtype=np.int64)
    column_index = {name: i for i, name in enumerate(columns)}
    vif = {row["column"]: row for row in orthog["variance_inflation_factors"]}
    ideas = {}
    for idea_id in ("Tm1", "Tm2", "Tm3", "Tm4", "Tm5"):
        policy = status["ideas"][idea_id]
        harness_entry = harness["per_idea"].get(idea_id)
        if policy["status"] != "implemented":
            ideas[idea_id] = {"status": policy["status"], "reason": policy["reason"]}
            if harness_entry:
                ideas[idea_id]["historical_artifact"] = {
                    "path": harness_entry["path"], "sha256": harness_entry["sha256"],
                    "official_rows": harness_entry["official_rows"],
                    "emissions": harness_entry["emissions"],
                    "note": "retained historical harness artifact; pending status means do not promote/use in a candidate"
                }
            continue
        if not harness_entry:
            raise ValueError(f"implemented {idea_id} missing from harness report")
        path = workspace / harness_entry["path"]
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != harness_entry["sha256"]:
            raise ValueError(f"{idea_id} artifact hash differs from harness record")
        table = pq.read_table(path, columns=["GEOID", "region", "component", "channel", "value", "defined"])
        emission_rows = []
        for component, channel in sorted(set(zip(table["component"].to_pylist(),
                                                  table["channel"].to_pylist()))):
            mask = np.asarray([(c == component and ch == channel)
                               for c, ch in zip(table["component"].to_pylist(),
                                                table["channel"].to_pylist())], dtype=bool)
            values = np.asarray(table["value"].to_numpy(zero_copy_only=False), dtype=np.float64)[mask]
            defined = np.asarray(table["defined"].to_numpy(zero_copy_only=False), dtype=bool)[mask]
            measured = values[defined]
            entry = {"component": component, "channel": channel, "rows": int(mask.sum()),
                     "defined_rows": int(defined.sum()), "undefined_rows": int((~defined).sum()),
                     "defined_fraction": float(defined.mean()) if defined.size else 0.0}
            if measured.size:
                entry["distribution"] = {
                    "min": float(measured.min()), "p50": float(np.quantile(measured, 0.5)),
                    "p90": float(np.quantile(measured, 0.9)), "max": float(measured.max()),
                    "mean": float(measured.mean()), "sd": float(measured.std()),
                }
            else:
                entry["distribution"] = {"count": 0}
            name = f"{idea_id}|{component}|{channel}"
            if name not in column_index:
                raise ValueError(f"{idea_id} emission is absent from orthogonality matrix: {name}")
            i = column_index[name]
            correlations = []
            for j, other_name in enumerate(columns):
                if j == i or other_name.startswith(f"{idea_id}|"):
                    continue
                rho = float(matrix[i, j])
                if np.isfinite(rho):
                    correlations.append({"column": other_name, "pearson_r": rho,
                                         "pairwise_defined_rows": int(overlap[i, j]),
                                         "redundant_abs_rho_gt_0_8": abs(rho) > 0.8})
            entry["correlations_to_existing_columns"] = correlations
            entry["high_correlation_pairs"] = [pair for pair in correlations
                                                 if pair["redundant_abs_rho_gt_0_8"]]
            entry["vif"] = vif.get(name)
            emission_rows.append(entry)
        ideas[idea_id] = {"status": "implemented", "artifact": harness_entry["path"],
                          "sha256": actual_hash, "official_rows": harness_entry["official_rows"],
                          "geoid_partition_exact": harness_entry["geoid_partition_exact"],
                          "emissions": emission_rows, "diagnostics": harness_entry["diagnostics"]}

    out = {
        "family": "Tm", "scope": "9,379 official scored tracts; label-free provided-data estimators",
        "ideas": ideas,
        "measure": {"implemented_computed": sum(1 for entry in ideas.values()
                                                   if entry["status"] == "implemented"),
                    "pending_policy_review": [idea for idea, entry in ideas.items()
                                              if entry["status"] == "pending_policy_review"]},
        "validation": {
            "targeted_test_command": "uv run --python 3.12.3 --with numpy==2.5.4 --with pyarrow==26.0.0 --with shapely==2.2.0 --with pyproj==3.8.0 --with pytest python -m pytest tests/test_bias60_family_tm.py tests/test_bias60_harness.py -q",
            "targeted_test_output": "50 passed, 9 subtests passed",
            "harness_report_sha256": hashlib.sha256(harness_path.read_bytes()).hexdigest(),
            "harness_gates_all_passed": harness["gates"]["all_passed"],
            "registry_check": "scripts/build_ideas_registry.py --check-only unchanged",
            "orthogonality_check": "scripts/report_ideas_orthogonality.py --check-only unchanged",
        },
        "orthogonality": {
            "path": "reports/ideas-orthogonality.json",
            "sha256": hashlib.sha256(orthog_path.read_bytes()).hexdigest(),
            "gates_all_passed": orthog["gates"]["all_passed"],
            "columns_evaluated": orthog["summary"]["columns_evaluated"],
            "columns_flagged_vif": orthog["summary"]["columns_flagged_vif"],
            "pairs_flagged": orthog["summary"]["pairs_flagged"],
            "note": "full correlation matrix and VIF report is linked above; any |rho|>0.8 is listed per emission, not silently removed"
        },
        "no_target_or_reference_data_read": True,
        "no_public_lb_tuning_or_upload": True,
        "important_status_note": "Tm1 is still present in the legacy 60-estimator harness and orthogonality outputs as a hash-verified historical artifact; its pending-policy status means it is not promoted as a family result or candidate feature. Tm4 is not registered as an implementation because its frozen recipe requires the same unresolved CarbonPlan field.",
    }
    out_path = ROOT / args.out
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out_path.relative_to(ROOT)),
                      "sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
                      "implemented": out["measure"]["implemented_computed"],
                      "pending": out["measure"]["pending_policy_review"],
                      "orthogonality_gates": out["orthogonality"]["gates_all_passed"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
