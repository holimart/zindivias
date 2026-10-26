#!/usr/bin/env python3
"""Validate frozen candidate CSVs against the provided platform template.

Reads only GEOID from the platform template and regional sample files; sample score
placeholders are never accessed. Candidate output values are checked for schema,
order, completeness, finiteness, bounds, and the registered SHA-256.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = {
    "A1_physical_equal": ("reports/candidates/A1_physical_equal.csv", "6af088b83ff3481d420e11b14d903fc510c2b4be2cea285c762ca12d500862de"),
    "A2_spectral": ("reports/candidates/A2_spectral.csv", "f88573996566a1975726845a49f42fb2cd3781d5f0e83d55944fc65647692997"),
    "A3_dawid_skene": ("reports/candidates/A3_dawid_skene.csv", "6d588f2f7a45a58b128860a049ac19995b21902f7f98d3b2ab39d1d9fbb073b3"),
    "V2_model": ("reports/candidates/V2_model.csv", "3193ee78a64004f007829f1f9c12d4aa8d4f7c15136771e8da9108fdcea06f12"),
}
COLUMNS = ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"]
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")


def read_selected_column(path: Path, selected: str) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if selected not in header:
            raise ValueError(f"{path}: missing {selected}")
        idx = header.index(selected)
        return [row[idx] for row in reader]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def validate(workspace: Path, candidate_ids: list[str]) -> dict:
    started = time.perf_counter()
    usage0 = resource.getrusage(resource.RUSAGE_SELF)
    template_path = workspace / "SampleSubmission.csv"
    template_ids = read_selected_column(template_path, "GEOID")
    if len(template_ids) != 9794 or len(set(template_ids)) != 9794:
        raise ValueError("platform template must contain 9,794 unique GEOIDs")
    official_ids: list[str] = []
    for region in REGIONS:
        official_ids.extend(read_selected_column(
            workspace / "samples" / f"{region}-sample-submission.csv", "GEOID"))
    if len(official_ids) != 9379 or len(set(official_ids)) != 9379:
        raise ValueError("official samples must contain 9,379 unique GEOIDs")
    if not set(official_ids) <= set(template_ids):
        raise ValueError("official sample GEOIDs are missing from platform template")

    validated = []
    for candidate_id in candidate_ids:
        relpath, expected_sha = CANDIDATES[candidate_id]
        path = ROOT / relpath
        actual_sha = sha256(path)
        if actual_sha != expected_sha:
            raise ValueError(f"{candidate_id}: SHA-256 mismatch {actual_sha}")
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != COLUMNS:
                raise ValueError(f"{candidate_id}: unexpected columns {reader.fieldnames}")
            rows = list(reader)
        ids = [row["GEOID"] for row in rows]
        if ids != template_ids:
            raise ValueError(f"{candidate_id}: GEOID order differs from platform template")
        if len(set(ids)) != len(ids):
            raise ValueError(f"{candidate_id}: duplicate GEOIDs")
        for i, row in enumerate(rows, start=2):
            for column in COLUMNS[1:]:
                raw = row[column]
                if raw is None or raw.strip() == "":
                    raise ValueError(f"{candidate_id}: blank {column} at CSV line {i}")
                value = float(raw)
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError(f"{candidate_id}: invalid {column} at CSV line {i}")
        validated.append({
            "candidate_id": candidate_id,
            "artifact": relpath,
            "sha256": actual_sha,
            "rows": len(rows),
            "columns": COLUMNS,
            "official_ids": len(official_ids),
            "template_order_exact": True,
            "no_duplicate_or_missing_ids": True,
            "finite_bounded_score_fields": True,
        })
    usage1 = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "schema_version": 1,
        "status": "all_gates_passed",
        "scope": {"official_ids": 9379, "template_ids": 9794, "template_only_rows": 415},
        "template_input": "Only GEOID was read from SampleSubmission.csv; no score columns were accessed.",
        "sample_inputs": "Only GEOID was read from the four authoritative regional sample CSVs; no score columns/placeholders were accessed.",
        "candidates": validated,
        "profile": {
            "command": "python3 scripts/validate_track_a_upload_candidates.py --workspace \"$COMPETITION_WORKSPACE\" --out reports/track-a-upload-validation.json",
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "wall_seconds": time.perf_counter() - started,
            "user_cpu_seconds": usage1.ru_utime - usage0.ru_utime,
            "system_cpu_seconds": usage1.ru_stime - usage0.ru_stime,
            "max_rss_kib": usage1.ru_maxrss,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ.get("COMPETITION_WORKSPACE", "")))
    parser.add_argument("--candidate", action="append", choices=sorted(CANDIDATES), dest="candidate_ids")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/track-a-upload-validation.json")
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error("--workspace or COMPETITION_WORKSPACE must point to the authorized workspace")
    result = validate(args.workspace, args.candidate_ids or list(CANDIDATES))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "validated": [x["candidate_id"] for x in result["candidates"]], "profile": result["profile"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
