#!/usr/bin/env python3
"""Build the pre-registered all-zero public-LB diagnostic from official samples."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("reports/candidates/C0_all_zero.csv"))
    args = parser.parse_args()
    rows = []
    header = None
    seen = set()
    for region in REGIONS:
        path = args.sample_dir / f"{region}-sample-submission.csv"
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if header is None:
                header = reader.fieldnames
            if reader.fieldnames != header or not header or "GEOID" not in header:
                raise ValueError(f"sample schema mismatch: {path}")
            for source in reader:
                geoid = source["GEOID"]
                if not geoid or geoid in seen:
                    raise ValueError(f"empty or duplicate authoritative GEOID: {geoid!r}")
                seen.add(geoid)
                # Preserve all official sample columns and IDs, but never consume
                # the sample's placeholder target values as labels.
                rows.append({name: geoid if name == "GEOID" else "0" for name in header})
    if len(rows) != 9379:
        raise ValueError(f"expected 9,379 authoritative IDs, got {len(rows)}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"built C0 all-zero candidate: {len(rows)} rows, {len(header)} columns at {args.out}")


if __name__ == "__main__":
    main()
