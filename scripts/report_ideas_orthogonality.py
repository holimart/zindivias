#!/usr/bin/env python3
"""Write ``reports/ideas-orthogonality.json``: label-free correlation, VIF, condition number and
1 - |rho| clustering of the implemented estimators on the study tracts (spec section 3.3).

The report is built only from the idea parquets the harness produced, and every input parquet hash is
revalidated against ``reports/ideas-harness.json`` before a single statistic is computed, so the
orthogonality numbers cannot describe artifacts other than the ones on record.

Usage:
    COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" \\
      /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 \\
      --with shapely==2.2.0 --with pyproj==3.8.0 python scripts/report_ideas_orthogonality.py
      [--check-only] [--tamper CASE]
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

from bias60.harness import canonical_bytes, environment_versions, git_commit  # noqa: E402
from bias60.inputs import official_scored_pairs  # noqa: E402
from bias60.orthogonality import build_report, normalized_bytes  # noqa: E402
from bias60.spec import parse_spec  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/bias60-harness-preregistered.json")
    parser.add_argument("--registry", default="config/ideas.json")
    parser.add_argument("--harness-report", default="reports/ideas-harness.json")
    parser.add_argument("--out", default="reports/ideas-orthogonality.json")
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--ideas-dir", default=None)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--tamper", default=None,
                        choices=[None, "drop_column", "duplicate_column", "break_symmetry",
                                 "low_coverage"])
    args = parser.parse_args(argv)

    workspace = Path(args.workspace) if args.workspace else None
    if workspace is None or not workspace.is_dir():
        print("BLOCKED COMPETITION_WORKSPACE is required to read the idea parquets")
        return 2
    config_path = ROOT / args.config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["__sha256__"] = hashlib.sha256(canonical_bytes(config)).hexdigest()
    spec_parse = parse_spec(Path(config["spec"]["resolved_path"]))
    config["__spec__"] = spec_parse["spec"]

    harness_path = ROOT / args.harness_report
    if not harness_path.is_file():
        print(f"BLOCKED harness report missing: {harness_path} (run scripts/run_bias60_ideas.py)")
        return 2
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    registry = json.loads((ROOT / args.registry).read_text(encoding="utf-8"))
    implemented = [idea["id"] for idea in registry["ideas"] if idea["status"] == "implemented"]

    ideas_dir = Path(args.ideas_dir) if args.ideas_dir else workspace / "ideas"
    idea_paths: dict[str, Path] = {}
    for idea_id in implemented:
        entry = harness["per_idea"].get(idea_id)
        if entry is None:
            print(f"FAIL {idea_id} is implemented but absent from the harness report")
            return 1
        path = ideas_dir / f"{idea_id}.parquet"
        if not path.is_file():
            print(f"BLOCKED idea parquet missing: {path}")
            return 2
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            print(f"FAIL {idea_id}: parquet sha256 {digest[:16]} != recorded {entry['sha256'][:16]}")
            return 1
        idea_paths[idea_id] = path

    pairs = official_scored_pairs(workspace)
    geoids = [geoid for geoid, region in pairs if region in config["regions"]]

    report = build_report(config=config, idea_paths=idea_paths, geoids=geoids,
                         implemented_ids=implemented, git_commit=git_commit(ROOT),
                         versions=environment_versions(), tamper=args.tamper)
    report["inputs"] = {idea: {"path": str(path), "sha256": harness["per_idea"][idea]["sha256"]}
                        for idea, path in idea_paths.items()}
    payload = canonical_bytes(report)
    report["summary"]["report_sha256"] = hashlib.sha256(normalized_bytes(report)).hexdigest()
    payload = canonical_bytes(report)
    out_path = ROOT / args.out
    if args.check_only:
        if out_path.is_file():
            recorded = json.loads(out_path.read_text(encoding="utf-8"))
            if normalized_bytes(recorded) == normalized_bytes(report):
                print(f"unchanged {out_path}")
                return 0 if report["gates"]["all_passed"] else 1
        print(f"CHANGED {out_path}")
        return 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)

    summary = {
        "gates_all_passed": report["gates"]["all_passed"],
        "columns_evaluated": report["summary"]["columns_evaluated"],
        "columns_included": report["summary"]["columns_included"],
        "clusters": report["summary"]["clusters"],
        "pairs_flagged": report["summary"]["pairs_flagged"],
        "columns_flagged_vif": report["summary"]["columns_flagged_vif"],
        "condition_number": report["condition_number"],
        "mtmm": report["multitrait_multimethod"],
        "failed_gates": [name for name, value in report["gates"].items() if not value],
        "report_sha256": hashlib.sha256(payload).hexdigest(),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if report["gates"]["all_passed"]:
        print("PASS ideas orthogonality: every implemented estimator was measured label-free on the "
              "study tracts with correlation, VIF, condition number and 1 - |rho| clustering, and the "
              "merge thresholds were applied exactly")
        return 0
    print("FAIL ideas orthogonality: " + ", ".join(summary["failed_gates"]))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
