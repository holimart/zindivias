#!/usr/bin/env python3
"""Reconcile official regional sample contracts against legacy SampleSubmission.csv."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
BASE_URL = "https://data.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge/reference"
AUTHORITATIVE_COLUMNS = [
    "GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score",
]
REGION_COUNTS = {
    "maricopa-az": 1593,
    "northern-ca": 591,
    "eastern-ok": 1192,
    "south-central-tx": 6003,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_contract(path: Path, expected_columns: list[str] | None = None):
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        if len(columns) != len(set(columns)):
            raise ValueError(f"duplicate columns in {path}: {columns}")
        if expected_columns is not None and columns != expected_columns:
            raise ValueError(f"unexpected columns in {path}: {columns}")
        if "GEOID" not in columns:
            raise ValueError(f"GEOID missing in {path}")
        # Intentionally inspect only GEOID (plus legacy region); sample score
        # placeholders are not labels and are never read for analysis.
        ids, regions = [], []
        for row in reader:
            geoid = row.get("GEOID", "")
            if not geoid:
                raise ValueError(f"blank GEOID in {path}")
            ids.append(geoid)
            if "region" in columns:
                regions.append(row.get("region", ""))
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate GEOIDs in {path}")
    return columns, ids, regions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"])
                        if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--out", type=Path, default=Path("reports/sample-contract-audit.json"))
    parser.add_argument("--reconciliation", type=Path,
                        default=Path("reports/sample-id-reconciliation.csv"))
    parser.add_argument("--legacy-only-ids", type=Path,
                        default=Path("reports/legacy-only-sample-ids.csv"))
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    workspace = args.workspace.resolve()
    sample_dir = workspace / "samples"
    authoritative_by_region: dict[str, list[str]] = {}
    sample_metadata = []
    mirror_checks = {}
    for region in REGIONS:
        name = f"{region}-sample-submission.csv"
        path = sample_dir / name
        columns, ids, _ = read_contract(path, AUTHORITATIVE_COLUMNS)
        if len(ids) != REGION_COUNTS[region]:
            raise ValueError(f"{region}: expected {REGION_COUNTS[region]} IDs, got {len(ids)}")
        authoritative_by_region[region] = ids
        mirror = workspace / "defined-flags-inputs" / name
        mirror_checks[region] = {
            "exists": mirror.is_file(),
            "sha256_matches_authoritative_download": mirror.is_file() and sha256(mirror) == sha256(path),
        }
        sample_metadata.append({
            "region": region,
            "path": f"samples/{name}",
            "source_url": f"{BASE_URL}/{region}/{name}",
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "rows": len(ids),
            "unique_geoids": len(set(ids)),
            "columns": columns,
            "geoid_read_as": "string",
        })

    official_ids = [geoid for region in REGIONS for geoid in authoritative_by_region[region]]
    if len(official_ids) != 9379 or len(set(official_ids)) != 9379:
        raise ValueError(f"expected 9,379 unique official GEOIDs, got {len(official_ids)}")
    maricopa_special = "35023970000"
    if maricopa_special not in authoritative_by_region["maricopa-az"]:
        raise ValueError("official Maricopa sample omits New Mexico tract 35023970000")

    legacy_path = workspace / "SampleSubmission.csv"
    legacy_columns, legacy_ids, legacy_regions = read_contract(legacy_path)
    legacy_by_region: dict[str, list[str]] = defaultdict(list)
    for geoid, region in zip(legacy_ids, legacy_regions):
        legacy_by_region[region].append(geoid)
    official_set, legacy_set = set(official_ids), set(legacy_ids)
    authoritative_only = sorted(official_set - legacy_set)
    legacy_only = sorted(legacy_set - official_set)
    eastern_wa_only = sorted(set(legacy_by_region["eastern-wa"]))
    if len(legacy_ids) != 9794 or len(legacy_set) != 9794:
        raise ValueError(f"expected 9,794 unique legacy rows, got {len(legacy_ids)}")
    if len(legacy_only) != 415 or len(eastern_wa_only) != 415:
        raise ValueError(f"expected 415 eastern-wa-only GEOIDs, got {len(eastern_wa_only)}")
    if authoritative_only or set(legacy_only) != set(eastern_wa_only):
        raise ValueError("legacy/official set differences do not match exactly the eastern-wa rows")

    args.legacy_only_ids.parent.mkdir(parents=True, exist_ok=True)
    with args.legacy_only_ids.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["GEOID"])
        writer.writerows((geoid,) for geoid in legacy_only)

    reconciliation_rows = []
    for region in (*REGIONS, "eastern-wa"):
        official = set(authoritative_by_region.get(region, []))
        legacy = set(legacy_by_region.get(region, []))
        reconciliation_rows.append({
            "region": region,
            "authoritative_rows": len(official),
            "legacy_rows": len(legacy),
            "overlap": len(official & legacy),
            "authoritative_only": len(official - legacy),
            "legacy_only": len(legacy - official),
        })
    reconciliation_rows.append({
        "region": "TOTAL",
        "authoritative_rows": len(official_set),
        "legacy_rows": len(legacy_set),
        "overlap": len(official_set & legacy_set),
        "authoritative_only": len(authoritative_only),
        "legacy_only": len(legacy_only),
    })
    args.reconciliation.parent.mkdir(parents=True, exist_ok=True)
    with args.reconciliation.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(reconciliation_rows[0]),
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(reconciliation_rows)

    audit = {
        "method": "GEOID/header-only reconciliation; sample score placeholders were not read",
        "authoritative_source": "official rules: reference/<region>/<region>-sample-submission.csv",
        "authoritative_columns": AUTHORITATIVE_COLUMNS,
        "official_rules_required_submission_columns": ["GEOID", "coverage_gap_score"],
        "official_rules_optional_component_columns": ["transport_gap", "building_gap", "poi_gap"],
        "submission_contract_note": "The official rule text requires GEOID and coverage_gap_score and says component scores may be included. Each authoritative regional sample has all five columns listed above; local frozen candidates use that five-column order.",
        "authoritative_samples": sample_metadata,
        "authoritative_rows": len(official_ids),
        "authoritative_unique_geoids": len(official_set),
        "authoritative_region_counts": REGION_COUNTS,
        "maricopa_includes_new_mexico_geoid": maricopa_special,
        "legacy_sample": {
            "path": "SampleSubmission.csv",
            "bytes": legacy_path.stat().st_size,
            "sha256": sha256(legacy_path),
            "rows": len(legacy_ids),
            "unique_geoids": len(legacy_set),
            "columns": legacy_columns,
            "region_counts": dict(sorted(Counter(legacy_regions).items())),
        },
        "id_set_reconciliation": {
            "overlap": len(official_set & legacy_set),
            "authoritative_only_count": len(authoritative_only),
            "authoritative_only_geoids": authoritative_only,
            "legacy_only_count": len(legacy_only),
            "legacy_only_geoids_csv": str(args.legacy_only_ids),
            "legacy_only_geoids_csv_bytes": args.legacy_only_ids.stat().st_size,
            "legacy_only_geoids_csv_sha256": sha256(args.legacy_only_ids),
            "legacy_only_region": "eastern-wa",
            "legacy_only_are_exactly_eastern_wa": set(legacy_only) == set(eastern_wa_only),
            "reconciliation_csv": str(args.reconciliation),
            "reconciliation_csv_bytes": args.reconciliation.stat().st_size,
            "reconciliation_csv_sha256": sha256(args.reconciliation),
        },
        "legacy_columns_not_in_authoritative_sample": [
            column for column in legacy_columns if column not in AUTHORITATIVE_COLUMNS
        ],
        "mirror_checks_defined_flags_inputs": mirror_checks,
        "platform_contract_status": "unresolved; previous steward-recorded processing error named missing eastern-wa IDs, while official rules and regional samples specify the 9,379-ID contract; no upload performed by this audit",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(f"PASS official {len(official_ids)} IDs; legacy {len(legacy_ids)}; "
          f"overlap {len(official_set & legacy_set)}; legacy-only {len(legacy_only)}")
    print(f"Wrote {args.reconciliation}, {args.legacy_only_ids}, {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
