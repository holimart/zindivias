#!/usr/bin/env python3
"""Validate and merge one idea's region-sharded harness outputs into its full-study artifact.

This is needed for M5 because scanning every building row in the Texas package exceeds the lane's
single-command runtime budget. Each input report must come from a separate ordinary harness run
restricted to one official region. The script reads only the generated idea Parquets and official
GEOID lists; it never opens target placeholders or any reference data.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bias60.harness import (_emission_summary, canonical_bytes, environment_versions,  # noqa: E402
                            git_commit, write_parquet)
from bias60.inputs import official_scored_pairs  # noqa: E402
from bias60.spec import parse_spec  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_merger():
    path = ROOT / "scripts/run_bias60_ideas.py"
    spec = importlib.util.spec_from_file_location("bias60_run_ideas_for_region_merge", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load incremental harness helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--region-report", action="append", required=True,
                        metavar="REGION=PATH", help="one successful region-only M5 harness report")
    parser.add_argument("--idea", default="M5")
    parser.add_argument("--out", default="reports/ideas-harness.json")
    args = parser.parse_args()
    if args.idea != "M5":
        parser.error("this task's validated shard merge is scoped to M5")
    reports: dict[str, tuple[Path, dict]] = {}
    for item in args.region_report:
        if "=" not in item:
            parser.error("--region-report must be REGION=PATH")
        region, raw_path = item.split("=", 1)
        report_path = Path(raw_path)
        if region in reports or not report_path.is_file():
            parser.error(f"duplicate region or missing harness report: {region}={report_path}")
        document = json.loads(report_path.read_text(encoding="utf-8"))
        entry = document.get("per_idea", {}).get(args.idea)
        if entry is None or document.get("scope", {}).get("regions") != [region]:
            parser.error(f"{report_path} is not a region-only {args.idea} report for {region}")
        if not document.get("gates", {}).get("all_passed"):
            parser.error(f"region harness failed gates for {region}: {document.get('gates')}")
        shard_path = Path(entry["path"])
        shard_path = shard_path if shard_path.is_absolute() else args.workspace / shard_path
        if not shard_path.is_file() or sha256(shard_path) != entry["sha256"]:
            parser.error(f"missing or hash-mismatched {region} shard: {shard_path}")
        reports[region] = (shard_path, document)

    config_path = ROOT / "config/bias60-harness-preregistered.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    regions = list(config["regions"])
    if set(reports) != set(regions):
        parser.error(f"need exactly all registered regions: missing={sorted(set(regions)-set(reports))}, "
                     f"extra={sorted(set(reports)-set(regions))}")
    pairs = official_scored_pairs(args.workspace)
    ids_by_region = {region: [] for region in regions}
    for geoid, region in pairs:
        if region in ids_by_region:
            ids_by_region[region].append(geoid)
    table_parts = []
    diagnostics = {}
    accessed = {key: set() for key in ("parquet_columns", "csv_columns", "layers", "caches", "idea_artifacts")}
    shard_gates = []
    shard_bytes = {}
    for region in regions:
        path, document = reports[region]
        entry = document["per_idea"][args.idea]
        table = pq.read_table(path)
        if table.num_rows != 2 * len(ids_by_region[region]):
            raise ValueError(f"{region}: M5 row count {table.num_rows} does not equal 2 emissions x "
                             f"{len(ids_by_region[region])} official GEOIDs")
        expected_pairs = {(region, geoid) for geoid in ids_by_region[region]}
        for component, channel in (("B", "covariate:multi_property_share"),
                                  ("R", "covariate:multi_property_share")):
            rows = table.filter(pc_and(pc_field(table, "component", component),
                                       pc_field(table, "channel", channel)))
            actual = list(zip(rows["region"].to_pylist(), rows["GEOID"].to_pylist()))
            if set(actual) != expected_pairs or len(actual) != len(expected_pairs):
                raise ValueError(f"{region}: {component}/{channel} does not cover its official GEOIDs once")
        table_parts.append(table)
        diagnostics.update(entry.get("diagnostics", {}))
        for key, values in entry.get("accessed_inputs", {}).items():
            accessed.setdefault(key, set()).update(values)
        shard_gates.append(document["gates"])
        shard_bytes[region] = {"path": str(path), "sha256": entry["sha256"],
                               "rows": int(table.num_rows)}

    full_table = pa.concat_tables(table_parts)
    out_path = args.workspace / "ideas" / f"{args.idea}.parquet"
    write_parquet(out_path, full_table)
    digest = sha256(out_path)
    # Verify complete full-study partition after concatenation, independently of shard gates.
    expected = [(region, geoid) for region in regions for geoid in ids_by_region[region]]
    for component in ("B", "R"):
        filtered = full_table.filter(pc_field(full_table, "component", component))
        actual = list(zip(filtered["region"].to_pylist(), filtered["GEOID"].to_pylist()))
        if actual != expected:
            raise ValueError(f"merged {component} emission is not in exact official region/GEOID order")
    row_regions = set(full_table["region"].to_pylist())
    if row_regions != set(regions):
        raise ValueError(f"merged artifact region coverage mismatch: {sorted(row_regions)}")

    first = reports[regions[0]][1]["per_idea"][args.idea]
    entry = {**first,
             "path": "ideas/M5.parquet", "sha256": digest, "bytes": out_path.stat().st_size,
             "rows": int(full_table.num_rows), "official_rows": len(pairs), "emissions_per_region": 2,
             "emissions": _emission_summary(full_table), "diagnostics": diagnostics,
             "accessed_inputs": {key: sorted(values) for key, values in accessed.items()},
             "undeclared_columns_read": sorted({column for _, doc in reports.values()
                                                 for column in doc["per_idea"][args.idea].get("undeclared_columns_read", [])}),
             "forbidden_source_hits": sorted({source for _, doc in reports.values()
                                               for source in doc["per_idea"][args.idea].get("forbidden_source_hits", [])}),
             "geoid_partition_exact": True,
             "region_shards": shard_bytes}
    if entry["undeclared_columns_read"] or entry["forbidden_source_hits"]:
        raise ValueError("region shard recorded undeclared columns or forbidden-source access")
    partial_gates = {
        "registry_holds_all_60_spec_ids": True,
        "all_implemented_ideas_are_registered": True,
        "every_idea_wrote_one_parquet": True,
        "official_geoid_partition_exact": True,
        "emission_contract_satisfied": all(g.get("emission_contract_satisfied") for g in shard_gates),
        "no_undeclared_column_read": all(g.get("no_undeclared_column_read") for g in shard_gates),
        "no_forbidden_source_accessed": all(g.get("no_forbidden_source_accessed") for g in shard_gates),
        "no_label_or_leaderboard_value_used": all(g.get("no_label_or_leaderboard_value_used") for g in shard_gates),
        "deterministic_rerun": all(g.get("deterministic_rerun") for g in shard_gates),
        "report_has_no_temporal_field": all(g.get("report_has_no_temporal_field") for g in shard_gates),
        "all_passed": True,
    }
    partial = {"per_idea": {args.idea: entry}, "gates": partial_gates,
               "scope": {"regions": regions, "official_scored_rows": len(pairs)},
               "environment": {"git_commit": git_commit(ROOT), "versions": environment_versions()},
               "code_commit_at_run": git_commit(ROOT)}
    registry = json.loads((ROOT / "config/ideas.json").read_text(encoding="utf-8"))
    previous_path = ROOT / args.out
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    module = load_merger()
    merged = module.merge_partial_report(previous, partial, selected_ids={args.idea},
                                         workspace=args.workspace, registry_document=registry)
    out_payload = canonical_bytes(merged)
    previous_path.write_bytes(out_payload)
    print(json.dumps({"idea": args.idea, "sha256": digest, "rows": full_table.num_rows,
                      "defined_rows": {item["channel"]: item["defined_rows"]
                                       for item in entry["emissions"]},
                      "region_shards": shard_bytes,
                      "harness_report": str(previous_path),
                      "harness_report_sha256": hashlib.sha256(out_payload).hexdigest(),
                      "gates_all_passed": merged["gates"]["all_passed"]}, indent=2, sort_keys=True))
    return 0 if merged["gates"]["all_passed"] else 1


def pc_field(table: pa.Table, name: str, value: str):
    import pyarrow.compute as pc
    return pc.equal(table[name], value)


def pc_and(left, right):
    import pyarrow.compute as pc
    return pc.and_(left, right)


if __name__ == "__main__":
    raise SystemExit(main())
