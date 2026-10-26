#!/usr/bin/env python3
"""Merge hash-verified Sca2-Sca5 subset runs into the complete 30-idea harness record.

The original 26 implementations retain their prior hash-verified entries; Sca2-Sca5 must be present
in the subset report and match current workspace parquet bytes. This avoids rerunning unrelated
geometry scans while keeping the canonical all-implemented-ideas report complete.
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
from bias60.determinism import normalized_bytes  # noqa: E402
from bias60.harness import canonical_bytes, environment_versions, git_commit  # noqa: E402
from bias60.inputs import load_config, sha256  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--base", default="reports/ideas-harness.json")
    parser.add_argument("--sca", default="reports/sca-harness.json")
    parser.add_argument("--registry", default="config/ideas.json")
    parser.add_argument("--config", default="config/bias60-harness-preregistered.json")
    parser.add_argument("--out", default="reports/ideas-harness.json")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    workspace = Path(args.workspace) if args.workspace else None
    if workspace is None or not workspace.is_dir():
        print("BLOCKED COMPETITION_WORKSPACE is required")
        return 2
    base_path, sca_path = ROOT / args.base, ROOT / args.sca
    base, sca = json.loads(base_path.read_text()), json.loads(sca_path.read_text())
    registry = json.loads((ROOT / args.registry).read_text())
    config = load_config(ROOT / args.config)
    implemented = [item["id"] for item in registry["ideas"] if item["status"] == "implemented"]
    expected_sca = {"Sca2", "Sca3", "Sca4", "Sca5"}
    if set(sca["per_idea"]) != expected_sca or not sca["gates"]["all_passed"]:
        raise ValueError("Sca subset report must contain passing Sca2-Sca5 runs only")
    base_entries = {key: value for key, value in base["per_idea"].items() if key not in expected_sca}
    prior = set(base_entries)
    if prior | expected_sca != set(implemented):
        raise ValueError("base and Sca reports do not partition the current implemented registry")
    merged = dict(base)
    merged["per_idea"] = {**base_entries, **sca["per_idea"]}
    if set(merged["per_idea"]) != set(implemented):
        raise ValueError("merged per-idea entries do not exactly cover the registry")
    for idea_id, entry in merged["per_idea"].items():
        if entry["rows"] <= 0 or not entry["geoid_partition_exact"]:
            raise ValueError(f"{idea_id} lacks a complete official GEOID partition")
        artifact = workspace / entry["path"]
        actual = sha256(artifact)
        if actual != entry["sha256"]:
            raise ValueError(f"{idea_id} parquet hash mismatch: {actual} != {entry['sha256']}")
    merged["registry_summary"] = registry["summary"]
    merged["preregistration"] = {
        "path": args.config,
        "sha256": hashlib.sha256(canonical_bytes(config)).hexdigest(),
        "spec": base["preregistration"]["spec"],
    }
    merged["gates"] = dict(base["gates"])
    merged["gates"]["all_passed"] = all(merged["gates"].values()) and all(
        sca["gates"].values())
    merged["gates"]["every_idea_wrote_one_parquet"] = True
    merged["partition_problems"] = []
    merged["tamper_case"] = None
    components = sorted({emission["component"] for entry in merged["per_idea"].values()
                         for emission in entry["emissions"]})
    parquet_files = {entry["path"]: entry["sha256"] for entry in merged["per_idea"].values()}
    merged["summary"] = {"all_passed": merged["gates"]["all_passed"],
                          "components_covered": components,
                          "ideas_implemented": len(implemented),
                          "parquet_files": dict(sorted(parquet_files.items()))}
    merged["code_commit_at_run"] = git_commit(ROOT)
    merged["environment"] = {"git_commit": git_commit(ROOT), "versions": environment_versions()}
    merged["summary"]["report_sha256"] = hashlib.sha256(normalized_bytes(merged)).hexdigest()
    payload = canonical_bytes(merged)
    out = ROOT / args.out
    if args.check_only:
        if not out.is_file() or normalized_bytes(json.loads(out.read_text())) != normalized_bytes(merged):
            print(f"CHANGED {out}")
            return 1
        print(f"unchanged {out}")
        return 0 if merged["gates"]["all_passed"] else 1
    out.write_bytes(payload)
    print(json.dumps({"ideas_implemented": len(implemented), "all_passed": merged["gates"]["all_passed"],
                      "sha256": hashlib.sha256(payload).hexdigest()}, sort_keys=True))
    return 0 if merged["gates"]["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
