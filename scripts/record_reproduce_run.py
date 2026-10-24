#!/usr/bin/env python3
"""Attach the raw /usr/bin/time -v capture to the reproduction audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--time-file", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "reports/reproduce-run.json")
    args = parser.parse_args()
    report_path = args.report.resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    raw_time = args.time_file.resolve().read_text(encoding="utf-8")
    if not raw_time.strip() or "Maximum resident set size" not in raw_time:
        raise ValueError("missing complete /usr/bin/time -v output")
    report["profile"] = {
        "tool": "/usr/bin/time -v",
        "raw_output": raw_time,
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                           encoding="utf-8")
    print(f"Recorded /usr/bin/time -v output in {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
