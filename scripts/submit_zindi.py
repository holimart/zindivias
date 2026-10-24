#!/usr/bin/env python3
"""Validate and ledger a pre-registered Zindi candidate; dry-run is the default.

This lane-safe tool does not implement browser/API upload. Live mode is refused;
operators use the official portal only after membership and authorization checks.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True,
            stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def read_sample_ids(sample_dir: Path) -> tuple[list[str], list[dict]]:
    ids: list[str] = []
    details = []
    expected_header = None
    seen = set()
    for region in REGIONS:
        path = sample_dir / f"{region}-sample-submission.csv"
        if not path.is_file():
            raise ValueError(f"missing authoritative sample: {path}")
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            header = reader.fieldnames or []
            if not header or header[0] != "GEOID" or "coverage_gap_score" not in header:
                raise ValueError(f"invalid authoritative sample header: {path}: {header}")
            if expected_header is None:
                expected_header = header
            if header != expected_header:
                raise ValueError(f"sample header mismatch in {path}")
            region_ids = []
            for row in reader:
                geoid = row.get("GEOID", "")
                if not geoid or geoid in seen:
                    raise ValueError(f"empty/duplicate official GEOID in {path}: {geoid!r}")
                seen.add(geoid)
                region_ids.append(geoid)
            ids.extend(region_ids)
            details.append({"region": region, "rows": len(region_ids)})
    if len(ids) != 9379 or len(seen) != 9379:
        raise ValueError(f"expected 9,379 authoritative IDs, got {len(ids)}")
    return ids, details


def validate_candidate(candidate_path: Path, sample_dir: Path,
                       manifest_path: Path, candidate_id: str) -> tuple[dict, dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidates = {item["candidate_id"]: item for item in manifest.get("candidate_set", [])}
    if candidate_id not in candidates:
        raise ValueError(f"candidate not found in manifest: {candidate_id}")
    registration = candidates[candidate_id]
    if not registration.get("pre_registered"):
        raise ValueError(f"candidate is not frozen/pre-registered: {candidate_id}")
    registered_path = (ROOT / registration["artifact"]).resolve()
    if candidate_path.resolve() != registered_path:
        raise ValueError("artifact path differs from the pre-registered manifest")
    artifact_hash = sha256(candidate_path)
    if artifact_hash != registration.get("sha256"):
        raise ValueError("artifact SHA-256 differs from the pre-registered manifest")

    expected_ids, per_region = read_sample_ids(sample_dir)
    with candidate_path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != registration.get("columns"):
            raise ValueError(f"candidate columns differ from manifest: {reader.fieldnames}")
        if len(reader.fieldnames or []) != len(set(reader.fieldnames or [])):
            raise ValueError("candidate has duplicate column names")
        if "GEOID" not in reader.fieldnames:
            raise ValueError("candidate missing GEOID")
        rows = list(reader)
    if len(rows) != len(expected_ids):
        raise ValueError(f"candidate row count {len(rows)} != {len(expected_ids)}")
    actual_ids = [row.get("GEOID", "") for row in rows]
    if actual_ids != expected_ids:
        raise ValueError("candidate GEOIDs/order do not exactly match concatenated official samples")
    score_columns = [column for column in reader.fieldnames if column != "GEOID"]
    null_counts = {column: 0 for column in score_columns}
    for line, row in enumerate(rows, start=2):
        for column in score_columns:
            value = row.get(column)
            if value is None or value.strip() == "":
                null_counts[column] += 1
                raise ValueError(f"blank {column} at CSV line {line}")
            try:
                numeric = float(value)
            except ValueError as exc:
                raise ValueError(f"non-numeric {column} at CSV line {line}") from exc
            if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
                raise ValueError(f"{column} outside finite [0,1] at CSV line {line}")
            if candidate_id == "C0" and numeric != 0.0:
                raise ValueError(f"C0 is not all-zero at {column}, line {line}")
    return registration, {
        "rows": len(rows), "unique_ids": len(set(actual_ids)),
        "columns": list(reader.fieldnames or []), "score_columns": score_columns,
        "null_counts": null_counts, "per_region": per_region,
        "sha256": artifact_hash,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", default="C0")
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--manifest", type=Path, default=ROOT / "reports/candidates.json")
    parser.add_argument("--sample-dir", type=Path)
    parser.add_argument("--ledger", type=Path, default=ROOT / "reports/submissions.jsonl")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", default=True,
                       help="validate and ledger only (default)")
    modes.add_argument("--submit", action="store_true",
                       help="refused: live upload must be done through reviewed official portal")
    args = parser.parse_args()

    # The presence/path of the secret file is only inspected for live-mode
    # messaging. Token contents are never opened, printed, copied or logged.
    token_file = os.environ.get("ZINDI_AUTH_TOKEN_FILE")
    if args.submit:
        configured = bool(token_file and Path(token_file).is_file())
        reason = ("ZINDI_AUTH_TOKEN_FILE is not configured or points to no file" if not configured
                  else "live upload is intentionally disabled in this dry-run helper")
        print(f"STOP: {reason}; no token was read and no upload was attempted.", file=sys.stderr)
        return 3

    sample_dir = args.sample_dir
    if sample_dir is None:
        workspace = os.environ.get("COMPETITION_WORKSPACE")
        if not workspace:
            parser.error("set COMPETITION_WORKSPACE or pass --sample-dir")
        sample_dir = Path(workspace) / "defined-flags-inputs"
    artifact = args.artifact or ROOT / "reports/candidates/C0_all_zero.csv"
    manifest = args.manifest.resolve()
    registration, validation = validate_candidate(
        artifact.resolve(), sample_dir.resolve(), manifest, args.candidate)
    try:
        manifest_relative = manifest.relative_to(ROOT)
    except ValueError:
        manifest_relative = manifest
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidate_id": args.candidate,
        "artifact": str(artifact.resolve().relative_to(ROOT)),
        "sha256": validation["sha256"],
        "manifest": {"path": str(manifest_relative), "sha256": sha256(manifest)},
        "command": " ".join(sys.argv),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "mode": "dry_run_validated",
            "auth_token_file_configured": bool(token_file),
        },
        "git_commit": git_commit(),
        "platform_submission_id": None,
        "status": "dry_run_validated",
        "our_score": None,
        "score_split": None,
        "leaderboard_position": None,
        "score_source": None,
        "validation": validation,
        "live_upload": False,
    }
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    with args.ledger.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(f"DRY RUN PASS {args.candidate}: {validation['rows']} rows, "
          f"{validation['unique_ids']} unique IDs, SHA-256 {validation['sha256']}; "
          f"ledger: {args.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
