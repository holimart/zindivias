#!/usr/bin/env python3
"""Build immutable T3-v2 regional shards and merge the official artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
from bias60.ideas.t3_exact import t3_exact  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--region", choices=REGIONS)
    parser.add_argument("--merge-shards", action="store_true")
    args = parser.parse_args()
    if args.region and args.merge_shards:
        parser.error("--region cannot be combined with --merge-shards")
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    config = load_config(ROOT / "config/bias60-harness-preregistered.json")
    all_pairs = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(all_pairs) != 9379 or len(set(all_pairs)) != 9379:
        raise ValueError("official scored GEOIDs must be exactly 9,379 unique pairs")
    by_region = {region: [g for g, r in all_pairs if r == region] for region in REGIONS}
    expected_rows = config["inputs"]["official_rows_by_region"]
    if any(len(by_region[r]) != expected_rows[r] for r in REGIONS):
        raise ValueError("official per-region row counts differ from frozen recipe")
    shard_dir = workspace / "ideas/_family_t_exact_v2_shards"
    artifact = workspace / "ideas/T3-v2.parquet"

    if not args.merge_shards:
        if not args.region:
            parser.error("select --region or --merge-shards")
        region = args.region
        shard_path = shard_dir / f"T3-v2-{region}.parquet"
        metadata_path = shard_dir / f"T3-v2-{region}.json"
        if shard_path.exists() or metadata_path.exists():
            parser.error(f"refusing to overwrite existing T3-v2 shard for {region}")
        geoids = by_region[region]
        inputs = ProvidedInputs(config, workspace, region, geoids)
        output = t3_exact(inputs)
        if output.geoids != geoids or len(output.emissions) != 1:
            raise ValueError(f"{region}: T3-v2 output does not match official input order")
        emission = output.emissions[0]
        if (emission.component != "R" or emission.channel != "covariate:grade_sep_per_km2"
                or len(emission.values) != len(geoids) or len(emission.defined) != len(geoids)
                or not np.isfinite(emission.values).all()
                or np.any(emission.values[~emission.defined] != 0.)):
            raise ValueError(f"{region}: T3-v2 emission contract failed")
        rows = [{"GEOID": g, "region": region, "idea": "T3-v2", "family": "T",
                 "component": "R", "channel": emission.channel, "value": float(v),
                 "defined": bool(d)} for g, v, d in zip(geoids, emission.values, emission.defined)]
        schema = pa.schema([("GEOID", pa.string()), ("region", pa.string()), ("idea", pa.string()),
                            ("family", pa.string()), ("component", pa.string()),
                            ("channel", pa.string()), ("value", pa.float64()), ("defined", pa.bool_())])
        table = pa.Table.from_pylist(rows, schema=schema)
        if table.column("GEOID").to_pylist() != geoids:
            raise ValueError(f"{region}: T3-v2 shard GEOID order mismatch")
        shard_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, shard_path, compression="zstd")
        metadata = {"idea": "T3-v2", "region": region, "official_rows": table.num_rows,
                    "defined_rows": int(emission.defined.sum()),
                    "undefined_rows": int((~emission.defined).sum()),
                    "defined_fraction": float(emission.defined.mean()),
                    "diagnostics": output.diagnostics,
                    "accessed": inputs.accessed,
                    "sha256": sha256(shard_path)}
        metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"shard": str(shard_path), **metadata}, indent=2))
        return 0

    if artifact.exists():
        parser.error(f"refusing to overwrite existing versioned artifact: {artifact}")
    rows = []
    coverage = {}
    for region in REGIONS:
        shard_path = shard_dir / f"T3-v2-{region}.parquet"
        metadata_path = shard_dir / f"T3-v2-{region}.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if sha256(shard_path) != metadata["sha256"]:
            raise ValueError(f"{region}: T3-v2 shard hash mismatch")
        table = pq.read_table(shard_path)
        if table.num_rows != len(by_region[region]) or table.column("GEOID").to_pylist() != by_region[region]:
            raise ValueError(f"{region}: T3-v2 shard ID coverage/order mismatch")
        if table.column("region").to_pylist() != [region] * len(by_region[region]):
            raise ValueError(f"{region}: T3-v2 shard region column mismatch")
        rows.extend(table.to_pylist())
        coverage[region] = {"official_rows": metadata["official_rows"],
                            "defined_rows": metadata["defined_rows"],
                            "undefined_rows": metadata["undefined_rows"],
                            "defined_fraction": metadata["defined_fraction"],
                            "diagnostics": metadata["diagnostics"],
                            "input_access": metadata["accessed"]}
    schema = pa.schema([("GEOID", pa.string()), ("region", pa.string()), ("idea", pa.string()),
                        ("family", pa.string()), ("component", pa.string()),
                        ("channel", pa.string()), ("value", pa.float64()), ("defined", pa.bool_())])
    table = pa.Table.from_pylist(rows, schema=schema)
    if table.num_rows != 9379 or list(zip(table.column("GEOID").to_pylist(), table.column("region").to_pylist())) != all_pairs:
        raise ValueError("merged T3-v2 artifact does not exactly match official GEOID-region order")
    pq.write_table(table, artifact, compression="zstd")
    artifact_hash = sha256(artifact)
    defined_total = int(sum(item["defined_rows"] for item in coverage.values()))
    entry = {"id": "T3-v2", "base_id": "T3", "version": 2, "family": "T",
             "status": "implemented_exact_recipe", "path": "ideas/T3-v2.parquet",
             "sha256": artifact_hash, "official_rows": table.num_rows,
             "defined_rows": defined_total,
             "defined_rows_by_region": {r: coverage[r]["defined_rows"] for r in REGIONS},
             "recipe": "count proper geometric crossing points without shared connector IDs, with at least one tertiary/unclassified non-named road and no rail-flagged road; divide by provided ALAND km²"}
    registry_path = ROOT / "config/bias60-family-t-exact-registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    entries = {item["id"]: item for item in registry["entries"]}
    if set(entries) != {"T1-v2", "T2-v2", "T3-v2", "T4-v2"}:
        raise ValueError("versioned Family T registry unexpectedly changed")
    entries["T3-v2"] = entry
    entries["T4-v2"].update({"status": "blocked_missing_population_centroids",
                             "exact_recipe_conformance": False,
                             "reason": "No provided population-centroid point/coordinate field exists. The supplied pop_total is tract-level only; polygon centroids are not population centroids, and constructing them requires an unprovided population surface."})
    registry["entries"] = [entries[f"T{i}-v2"] for i in range(1, 5)]
    registry["version"] = 2
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path = ROOT / "reports/bias60-family-t-exact-reconciliation.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    old_entries = [entries[f"T{i}-v2"] for i in (1, 2)]
    report["version"] = "exact-recipe-reconciliation-progress-v7"
    report["scope"] = "T1-v2, T2-v2, and T3-v2 official artifacts emitted; T4-v2 remains non-exact because permitted population-centroid coordinates are unavailable. Orthogonality refresh through T3 recorded; T4 excluded."
    report.setdefault("measure_after", {})
    report["measure_after"].update({"exact_recipes": 3, "exact_recipes_total": 4,
        "v2_artifacts_emitted": old_entries + [entry],
        "coverage_and_orthogonality_refreshed": False,
        "orthogonality_refresh_limitation": "The exact-recipe T4 feature cannot be emitted without a permitted population-centroid layer; pairwise comparisons among T1-v2/T2-v2/T3-v2 are reported separately."})
    report["T3_v2_artifact"] = entry
    report["T3_v2_coverage_by_region"] = coverage
    report.setdefault("implementation_progress", {}).update({
        "T3": "Implemented and built T3-v2 using actual proper geometric crossings with no shared connector, at least one eligible nonnamed road, rail exclusion, and land-area density; v1 remains unchanged.",
        "T4": "Exact recipe blocked: provided strata contain only tract-level pop_total and tract polygon geometry, not population-centroid points/coordinates. A polygon centroid is not a population centroid; no external population surface was accessed or approximated."})
    report.setdefault("T4_exact_recipe_blocker", {"status": "blocked_missing_permitted_input",
        "required": "population centroid point/coordinates for sampled edge betweenness",
        "provided": ["tract polygon geometry", "tract-level pop_total"],
        "why_insufficient": "Neither input identifies the spatial population centroid within each tract; creating one requires a population surface not in the permitted provided fields.",
        "v1_preserved": True, "T4_v2_emitted": False, "exactness_claimed": False})
    report.setdefault("validation", {}).update({
        "T3_v2_test_command": "uv run --python 3.12.3 --with numpy==2.5.4 --with pyarrow==26.0.0 --with shapely==2.2.0 --with pyproj==3.8.0 --with pytest python -m pytest tests/test_t3_exact.py tests/test_family_t_exact.py -q",
        "T3_v2_coverage_by_region": coverage,
        "T3_v2_artifact": {"path": "ideas/T3-v2.parquet", "sha256": artifact_hash,
                            "rows": table.num_rows, "defined_rows": defined_total},
        "T4_v2": "not emitted; specific permitted-input impossibility documented; no exactness claim",
        "v1_artifacts_overwritten": False})
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": str(artifact), "sha256": artifact_hash,
                      "official_rows": table.num_rows, "defined_rows": defined_total,
                      "exact_recipes": "3/4; T4 specifically blocked by unavailable population centroids",
                      "registry": str(registry_path.relative_to(ROOT))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
