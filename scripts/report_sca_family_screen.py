#!/usr/bin/env python3
"""Build the Sca1-Sca5 evidence report from hash-verified harness and orthogonality outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.determinism import normalized_bytes  # noqa: E402
from bias60.harness import canonical_bytes  # noqa: E402
from bias60.inputs import sha256  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--harness", default="reports/ideas-harness.json")
    parser.add_argument("--orthogonality", default="reports/ideas-orthogonality.json")
    parser.add_argument("--sca-harness", default="reports/sca-harness.json")
    parser.add_argument("--out", default="reports/sca-family-screen.json")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    workspace = Path(args.workspace) if args.workspace else None
    if workspace is None or not workspace.is_dir():
        print("BLOCKED COMPETITION_WORKSPACE is required")
        return 2
    harness = json.loads((ROOT / args.harness).read_text())
    orth = json.loads((ROOT / args.orthogonality).read_text())
    sca_harness = json.loads((ROOT / args.sca_harness).read_text())
    if not harness["gates"]["all_passed"] or not orth["gates"]["all_passed"]:
        raise ValueError("canonical harness and orthogonality gates must pass")
    expected = [f"Sca{i}" for i in range(1, 6)]
    ideas = []
    for idea_id in expected:
        entry = harness["per_idea"].get(idea_id)
        if entry is None or not entry["geoid_partition_exact"]:
            raise ValueError(f"{idea_id} missing from the complete harness or has an inexact partition")
        path = workspace / entry["path"]
        if sha256(path) != entry["sha256"]:
            raise ValueError(f"{idea_id} artifact hash does not match harness record")
        table = pq.read_table(path, columns=["GEOID", "region", "component", "channel", "defined"])
        rows_by_emission = {}
        for component, channel, region, defined in zip(
                table["component"].to_pylist(), table["channel"].to_pylist(),
                table["region"].to_pylist(), table["defined"].to_pylist()):
            key = (component, channel, region)
            current = rows_by_emission.setdefault(key, [0, 0])
            current[0] += 1
            current[1] += int(defined)
        emissions = []
        regions = {}
        for component, channel in sorted({(key[0], key[1]) for key in rows_by_emission}):
            total_rows = sum(value[0] for key, value in rows_by_emission.items()
                             if key[:2] == (component, channel))
            total_defined = sum(value[1] for key, value in rows_by_emission.items()
                                if key[:2] == (component, channel))
            emissions.append({"component": component, "channel": channel, "rows": total_rows,
                              "defined_rows": total_defined})
        for region, diagnostic in entry["diagnostics"].items():
            region_emissions = [value for key, value in rows_by_emission.items() if key[2] == region]
            region_rows = max((value[0] for value in region_emissions), default=0)
            region_defined = min((value[1] for value in region_emissions), default=0)
            regions[region] = {"rows": region_rows, "defined": region_defined,
                               "coverage": round(region_defined / region_rows, 6) if region_rows else 0.0,
                               "diagnostics": diagnostic}
        ideas.append({"id": idea_id, "status": "computed", "path": entry["path"],
                      "sha256": entry["sha256"], "rows": entry["rows"],
                      "official_rows": entry["official_rows"],
                      "geoid_partition_exact": entry["geoid_partition_exact"],
                      "emissions": emissions, "regions": regions})
    ortho_summary = orth["summary"]
    flagged_pairs = orth["pairs_exceeding_merge_threshold"]
    report = {
        "schema_version": 1, "family": "Sca", "status": "computed_all_five",
        "scope": {"official_scored_tracts": 9379, "ideas": expected, "computed": expected,
                  "coverage_is_defined_fraction_of_all_official_tracts": True},
        "constraints": {"provided_datasets_only": True, "labels_read": False,
                        "reference_layers_read": False, "leaderboard_accessed": False, "uploads": 0},
        "ideas": ideas,
        "redundancy": {"pairs_exceeding_abs_rho_0_8": flagged_pairs,
                       "merge_action": "All thresholded pairs are reported for downstream combiner grouping; no estimator is silently dropped."},
        "orthogonality": {
            "report": args.orthogonality,
            "sha256": sha256(ROOT / args.orthogonality),
            "summary": {"all_passed": ortho_summary["all_passed"],
                        "clusters": ortho_summary["clusters"],
                        "columns_evaluated": ortho_summary["columns_evaluated"],
                        "columns_included": ortho_summary["columns_included"],
                        "columns_flagged_vif": ortho_summary["columns_flagged_vif"],
                        "pairs_flagged": len(flagged_pairs),
                        "report_sha256": ortho_summary["report_sha256"]},
            "all_flagged_pairs": flagged_pairs,
            "vif_flags": orth["columns_exceeding_vif_threshold"],
            "note": "Every implemented estimator was measured label-free on official scored tracts; Sca outputs are included.",
        },
        "inputs": {"harness_report": args.harness,
                   "harness_report_sha256": sha256(ROOT / args.harness),
                   "sca_subset_report": args.sca_harness,
                   "sca_subset_report_sha256": sha256(ROOT / args.sca_harness)},
    }
    report["report_sha256"] = hashlib.sha256(normalized_bytes(report)).hexdigest()
    payload = canonical_bytes(report)
    out = ROOT / args.out
    if args.check_only:
        if not out.is_file() or normalized_bytes(json.loads(out.read_text())) != normalized_bytes(report):
            print(f"CHANGED {out}")
            return 1
        print(f"unchanged {out}")
        return 0
    out.write_bytes(payload)
    print(json.dumps({"ideas_computed": len(ideas), "rows": {item["id"]: item["rows"] for item in ideas},
                      "sha256": hashlib.sha256(payload).hexdigest()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
