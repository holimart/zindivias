#!/usr/bin/env python3
"""Create a label-free coverage/distribution/correlation/VIF summary for family M."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.determinism import canonical_bytes  # noqa: E402
from bias60.inputs import official_scored_pairs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--harness-report", type=Path, default=ROOT / "reports/ideas-harness.json")
    parser.add_argument("--orthogonality-report", type=Path, default=ROOT / "reports/ideas-orthogonality.json")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/bias60-family-m-summary.json")
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    harness = json.loads(args.harness_report.read_text(encoding="utf-8"))
    orth = json.loads(args.orthogonality_report.read_text(encoding="utf-8"))
    if not harness.get("gates", {}).get("all_passed") or not orth.get("gates", {}).get("all_passed"):
        raise ValueError("family M summary requires passing harness and orthogonality reports")
    pairs = official_scored_pairs(args.workspace)
    expected_pairs = set(pairs)
    n_rows = len(pairs)
    metrics: dict[str, dict] = {}
    columns: dict[str, dict] = {}
    for idea in ("M1", "M2", "M3", "M4", "M5"):
        entry = harness["per_idea"][idea]
        path = args.workspace / entry["path"]
        if not path.is_file():
            raise ValueError(f"missing family M artifact {path}")
        import hashlib
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            raise ValueError(f"family M artifact hash mismatch for {idea}")
        table = pq.read_table(path)
        if table.num_rows != entry["rows"]:
            raise ValueError(f"family M row count mismatch for {idea}")
        for emission in entry["emissions"]:
            component, channel = emission["component"], emission["channel"]
            mask = pc.and_(pc.equal(table["component"], component), pc.equal(table["channel"], channel))
            data = table.filter(mask)
            actual = list(zip(data["GEOID"].to_pylist(), data["region"].to_pylist()))
            if len(actual) != n_rows or set(actual) != expected_pairs:
                raise ValueError(f"{idea}/{component}/{channel} does not cover the official partition exactly")
            values = np.asarray(data["value"].to_numpy(), dtype=np.float64)
            defined = np.asarray(data["defined"].to_numpy(), dtype=np.bool_)
            finite = defined & np.isfinite(values)
            if int(finite.sum()) != int(defined.sum()):
                raise ValueError(f"{idea}/{component}/{channel} has nonfinite defined values")
            used = values[finite]
            name = f"{idea}|{component}|{channel}"
            columns[name] = {"idea": idea, "component": component, "channel": channel}
            metrics[name] = {
                "rows": int(len(values)), "defined_rows": int(defined.sum()),
                "coverage": float(defined.mean()),
                "distribution_defined_only": ({
                    "min": float(np.min(used)), "q25": float(np.quantile(used, 0.25)),
                    "median": float(np.median(used)), "mean": float(np.mean(used)),
                    "standard_deviation": float(np.std(used)),
                    "q75": float(np.quantile(used, 0.75)), "max": float(np.max(used)),
                    "distinct_values": int(np.unique(used).size),
                } if used.size else None),
                "artifact_sha256": entry["sha256"],
            }
    corr_columns = orth["correlation"]["columns"]
    corr_matrix = orth["correlation"]["matrix"]
    overlap_matrix = orth["correlation"]["pairwise_overlap"]
    corr_index = {name: index for index, name in enumerate(corr_columns)}
    correlation = []
    names = sorted(metrics)
    for left_i, left in enumerate(names):
        for right in names[left_i + 1:]:
            if left in corr_index and right in corr_index:
                i, j = corr_index[left], corr_index[right]
                raw_rho = corr_matrix[i][j]
                rho = None if raw_rho is None else float(raw_rho)
                overlap = int(overlap_matrix[i][j])
            else:
                rho, overlap = None, 0
            correlation.append({"left": left, "right": right, "spearman_rho": rho,
                                "pairwise_defined_rows": overlap})
    vif_by_column = {item["column"]: item for item in orth["variance_inflation_factors"]}
    flagged_vif = {item["column"]: item["vif"] for item in orth["columns_exceeding_vif_threshold"]}
    degenerate = {item["column"]: item for item in orth["degenerate_columns"]}
    for name, item in metrics.items():
        vif_entry = vif_by_column.get(name)
        item["orthogonality"] = {
            "vif": vif_entry.get("vif") if vif_entry else None,
            "vif_flagged": name in flagged_vif,
            "degenerate": degenerate.get(name),
        }
    summary = {
        "report_type": "bias60-family-m-study-tract-summary",
        "method": "label-free descriptive audit; only generated idea Parquets and their harness/orthogonality reports are read",
        "official_scored_rows": n_rows,
        "family_ideas": ["M1", "M2", "M3", "M4", "M5"],
        "harness_report_sha256": harness["summary"]["report_sha256"],
        "orthogonality_report_sha256": orth["summary"]["report_sha256"],
        "metrics": metrics,
        "pairwise_spearman_correlations": correlation,
        "orthogonality_context": {
            "columns_included": orth["summary"]["columns_included"],
            "columns_degenerate": orth["summary"]["columns_degenerate"],
            "columns_flagged_vif": orth["summary"]["columns_flagged_vif"],
            "condition_number": orth["condition_number"],
            "all_orthogonality_gates_passed": orth["gates"]["all_passed"],
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(summary)
    args.out.write_bytes(payload)
    print(json.dumps({"out": str(args.out), "sha256": __import__("hashlib").sha256(payload).hexdigest(),
                      "metrics": len(metrics), "correlations": len(correlation),
                      "all_metrics_cover_official_partition": True}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
