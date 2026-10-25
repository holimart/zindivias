#!/usr/bin/env python3
"""Run the implemented 60-estimator ideas end to end and record one parquet per idea.

Each implemented idea is a function ``provided inputs -> per-tract value for its component``. The
harness validates the output contract, writes ``$COMPETITION_WORKSPACE/ideas/<id>.parquet``, and
records every SHA-256 in ``reports/ideas-harness.json``.

Usage:
    COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" \\
      /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 \\
      --with shapely==2.2.0 --with pyproj==3.8.0 python scripts/run_bias60_ideas.py
      [--check-only] [--regions r1,r2] [--only Tm1,Sc5] [--tamper CASE]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bias60.determinism import canonical_bytes, normalized_bytes  # noqa: E402
from bias60.harness import (build_report, check_only, environment_versions,  # noqa: E402
                            git_commit, validate_registry_document)
from bias60.inputs import official_scored_pairs  # noqa: E402
from bias60.spec import parse_spec  # noqa: E402


def without_content_sha(document: dict) -> dict:
    """Drop the self-referential content hash before comparing two versions of a report."""
    copy = json.loads(json.dumps(document))
    copy.get("summary", {}).pop("report_sha256", None)
    return copy


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    config["__path__"] = str(path.relative_to(ROOT)) if str(path).startswith(str(ROOT)) else str(path)
    config["__sha256__"] = hashlib.sha256(canonical_bytes(config)).hexdigest()
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/bias60-harness-preregistered.json")
    parser.add_argument("--registry", default="config/ideas.json")
    parser.add_argument("--out", default="reports/ideas-harness.json")
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--ideas-dir", default=None)
    parser.add_argument("--regions", default=None)
    parser.add_argument("--only", default=None)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--tamper", default=None,
                        choices=[None, "drop_idea", "gap_out_of_bounds", "duplicate_geoid",
                                 "undeclared_column", "forbidden_source", "nondeterministic_rerun"])
    args = parser.parse_args(argv)

    workspace = Path(args.workspace) if args.workspace else None
    if workspace is None or not workspace.is_dir():
        print("BLOCKED COMPETITION_WORKSPACE is required for the provided-data caches")
        return 2
    config = load_config(ROOT / args.config)
    registry_path = ROOT / args.registry
    if not registry_path.is_file():
        print(f"BLOCKED registry missing: {registry_path} (run scripts/build_ideas_registry.py)")
        return 2
    registry_document = json.loads(registry_path.read_text(encoding="utf-8"))
    spec_parse = parse_spec(Path(config["spec"]["resolved_path"]))
    registry_gates = validate_registry_document(registry_document, config, spec_parse)
    if not all(registry_gates.values()):
        print("FAIL registry gates: " + ", ".join(name for name, value in registry_gates.items()
                                                 if not value))
        return 1
    config["__spec__"] = spec_parse["spec"]

    regions = args.regions.split(",") if args.regions else list(config["regions"])
    if regions != list(config["regions"]) and args.regions is None:
        print("FAIL region list does not match the preregistration")
        return 1
    pairs = official_scored_pairs(workspace)
    geoids_by_region: dict[str, list[str]] = {region: [] for region in regions}
    for geoid, region in pairs:
        if region in geoids_by_region:
            geoids_by_region[region].append(geoid)
    expected_rows = config["inputs"]["official_rows_by_region"]
    for region in regions:
        expected = expected_rows.get(region)
        if expected is not None and len(geoids_by_region[region]) != expected:
            print(f"FAIL {region}: {len(geoids_by_region[region])} official GEOIDs, expected {expected}")
            return 1

    ideas_dir = Path(args.ideas_dir) if args.ideas_dir else workspace / "ideas"
    report, payloads = build_report(config=config, registry_document=registry_document,
                                    workspace=workspace, regions=regions,
                                    geoids_by_region=geoids_by_region, ideas_dir=ideas_dir,
                                    tamper=args.tamper)
    if args.only:
        wanted = set(args.only.split(","))
        report["per_idea"] = {key: value for key, value in report["per_idea"].items()
                              if key in wanted}
        report["gates"]["every_idea_wrote_one_parquet"] = bool(report["per_idea"]) and all(
            entry["sha256"] and entry["rows"] > 0 for entry in report["per_idea"].values())
        report["gates"]["all_passed"] = all(report["gates"].values())

    out_path = ROOT / args.out
    if args.check_only:
        if not out_path.is_file():
            print(f"CHANGED {out_path} (missing)")
            return 1
        recorded = json.loads(out_path.read_text(encoding="utf-8"))
        recorded_hashes = {idea: entry["sha256"] for idea, entry in recorded["per_idea"].items()}
        problems = check_only(report, payloads, recorded_hashes)
        if normalized_bytes(without_content_sha(recorded)) != normalized_bytes(
                without_content_sha(report)):
            problems.append("report body differs from the committed report")
        if problems:
            print("CHANGED " + "; ".join(problems))
            return 1
        print(f"unchanged {out_path}")
        return 0 if report["gates"]["all_passed"] else 1

    report["summary"]["report_sha256"] = hashlib.sha256(normalized_bytes(report)).hexdigest()
    payload_bytes = canonical_bytes(report)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload_bytes)

    summary = {
        "gates_all_passed": report["gates"]["all_passed"],
        "ideas_implemented": report["summary"]["ideas_implemented"],
        "components_covered": report["summary"]["components_covered"],
        "official_scored_rows": report["scope"]["official_scored_rows"],
        "parquet_files": {Path(entry["path"]).name: entry["sha256"][:16]
                          for entry in report["per_idea"].values()},
        "rows_per_idea": {idea: entry["rows"] for idea, entry in report["per_idea"].items()},
        "failed_gates": [name for name, value in report["gates"].items() if not value],
        "report_sha256": hashlib.sha256(payload_bytes).hexdigest(),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if report["gates"]["all_passed"]:
        print("PASS bias60 harness: every implemented idea produced one contract-valid parquet over "
              "the official scored tracts, no forbidden source or undeclared column was read, and the "
              "run is reproducible")
        return 0
    print("FAIL bias60 harness: " + ", ".join(summary["failed_gates"]))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
