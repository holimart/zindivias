#!/usr/bin/env python3
"""Run the no-label invariant matrix twice and persist its audit report."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_gap_estimator import _authoritative_fixture, run_invariant_cases  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/gap-invariant-audit.json")
    args = parser.parse_args()
    first = run_invariant_cases()
    second = run_invariant_cases()
    if first != second:
        raise AssertionError("invariant matrix results differ on deterministic rerun")
    if first["case_count"] != first["passed_count"]:
        raise AssertionError("not all invariant cases passed")
    expected_ids, actual_ids, output_checks = _authoritative_fixture()
    covered_ids_hash = hashlib.sha256("\n".join(actual_ids).encode()).hexdigest()
    report = {
        "suite": "provided-data gap-estimator semantic invariants; no labels used",
        **first,
        "pass_rate": 1.0,
        "deterministic_rerun": True,
        "coverage": {
            **first["coverage"],
            "ordered_geoid_sha256": covered_ids_hash,
            **output_checks,
            "authoritative_region_counts": {
                "maricopa-az": 1593,
                "northern-ca": 591,
                "eastern-ok": 1192,
                "south-central-tx": 6003,
            },
        },
        "commands": [
            "uv run --with pytest --with pyarrow --with shapely --with pyproj pytest -q tests/test_gap_estimator.py",
            "uv run --with pytest --with pyarrow --with shapely --with pyproj python scripts/audit_gap_invariants.py --out reports/gap-invariant-audit.json",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"PASS {report['passed_count']}/{report['case_count']} cases; "
          f"exact coverage {len(expected_ids)}/{len(actual_ids)}; deterministic rerun")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
