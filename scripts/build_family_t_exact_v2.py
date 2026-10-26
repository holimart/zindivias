#!/usr/bin/env python3
"""Build the recipe-exact T1-v2 artifact from provided inputs only.

T2-v2 through T4-v2 remain explicitly pending. Existing v1 files are never opened for writing.
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
sys.path.insert(0, str(ROOT / "scripts"))

from bias60.inputs import ProvidedInputs, official_scored_pairs, load_config  # noqa: E402
from bias60.ideas.t_exact import t1_exact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--region", choices=["maricopa-az", "northern-ca", "eastern-ok", "south-central-tx"])
    parser.add_argument("--merge-shards", action="store_true")
    args = parser.parse_args()
    if args.region and args.merge_shards:
        parser.error("--region cannot be combined with --merge-shards")
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        raise SystemExit("COMPETITION_WORKSPACE is required")
    artifact = workspace / "ideas/T1-v2.parquet"
    if args.region is None and artifact.exists():
        raise SystemExit(f"refusing to overwrite versioned artifact: {artifact}")
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    config = load_config(ROOT / "config/bias60-harness-preregistered.json")
    all_pairs = official_scored_pairs(workspace)
    by_region: dict[str, list[str]] = {region: [] for region in config["regions"]}
    for geoid, region in all_pairs:
        by_region[region].append(geoid)
    expected = config["inputs"]["official_rows_by_region"]
    if sum(map(len, by_region.values())) != config["inputs"]["official_scored_rows"]:
        raise ValueError("official GEOID count differs from frozen 9,379-row scope")
    rows: list[dict] = []
    coverage: dict[str, dict] = {}
    regions = config["regions"] if args.merge_shards or not args.region else [args.region]
    if args.merge_shards:
        shard_dir = workspace / "ideas/_family_t_exact_v2_shards"
        for region in regions:
            shard_path = shard_dir / f"T1-v2-{region}.parquet"
            metadata_path = shard_dir / f"T1-v2-{region}.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if hashlib.sha256(shard_path.read_bytes()).hexdigest() != metadata["sha256"]:
                raise ValueError(f"{region}: T1-v2 shard hash mismatch")
            if metadata["official_rows"] != len(by_region[region]):
                raise ValueError(f"{region}: T1-v2 shard has wrong row count")
            shard = pq.read_table(shard_path)
            if shard.column("GEOID").to_pylist() != by_region[region]:
                raise ValueError(f"{region}: T1-v2 shard GEOID order mismatch")
            rows.extend(shard.to_pylist())
            coverage[region] = {"official_rows": metadata["official_rows"],
                                "defined_rows": metadata["defined_rows"],
                                "undefined_rows": metadata["official_rows"] - metadata["defined_rows"],
                                "diagnostics": metadata["diagnostics"]}
    for region in ([] if args.merge_shards else regions):
        geoids = by_region[region]
        if len(geoids) != expected[region]:
            raise ValueError(f"{region}: unexpected official row count {len(geoids)}")
        inputs = ProvidedInputs(config, workspace, region, geoids)
        output = t1_exact(inputs)
        if len(output.geoids) != len(geoids) or output.geoids != geoids:
            raise ValueError(f"{region}: T1-v2 GEOID order/partition mismatch")
        emission = output.emissions[0]
        if not np.isfinite(emission.values).all() or (emission.values[~emission.defined] != 0).any():
            raise ValueError(f"{region}: invalid T1-v2 numeric values")
        rows.extend({"GEOID": geoid, "region": region, "idea": "T1-v2", "family": "T",
                     "component": emission.component, "channel": emission.channel,
                     "value": float(value), "defined": bool(defined)}
                    for geoid, value, defined in zip(geoids, emission.values, emission.defined))
        coverage[region] = {"official_rows": len(geoids),
                            "defined_rows": int(emission.defined.sum()),
                            "undefined_rows": int((~emission.defined).sum()),
                            "diagnostics": output.diagnostics}
        print(f"{region}: {int(emission.defined.sum())}/{len(geoids)} defined")
        del inputs, output
    table = pa.Table.from_pylist(rows, schema=pa.schema([
        ("GEOID", pa.string()), ("region", pa.string()), ("idea", pa.string()),
        ("family", pa.string()), ("component", pa.string()), ("channel", pa.string()),
        ("value", pa.float64()), ("defined", pa.bool_()),
    ]))
    expected_pairs = [(geoid, region) for region in regions for geoid in by_region[region]]
    if table.num_rows != len(expected_pairs) or table.column("GEOID").to_pylist() != [x[0] for x in expected_pairs]:
        raise ValueError("T1-v2 output does not exactly match the selected official GEOID partition/order")
    if args.region:
        shard_dir = workspace / "ideas/_family_t_exact_v2_shards"
        shard_dir.mkdir(parents=True, exist_ok=True)
        shard_path = shard_dir / f"T1-v2-{args.region}.parquet"
        if shard_path.exists():
            raise SystemExit(f"refusing to overwrite existing shard: {shard_path}")
        pq.write_table(table, shard_path, compression="zstd")
        metadata = {"region": args.region, "official_rows": table.num_rows,
                    "defined_rows": coverage[args.region]["defined_rows"],
                    "diagnostics": coverage[args.region]["diagnostics"],
                    "sha256": hashlib.sha256(shard_path.read_bytes()).hexdigest()}
        (shard_dir / f"T1-v2-{args.region}.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"shard": str(shard_path), **metadata}, indent=2))
        return 0
    if table.num_rows != 9379 or table.column("GEOID").to_pylist() != [x[0] for x in all_pairs]:
        raise ValueError("T1-v2 artifact does not exactly match the official GEOID partition/order")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    temporary = artifact.with_suffix(".parquet.tmp")
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(artifact)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    entry = {"id": "T1-v2", "base_id": "T1", "version": 2, "family": "T",
             "status": "implemented_exact_recipe", "path": "ideas/T1-v2.parquet",
             "sha256": digest, "official_rows": table.num_rows,
             "defined_rows": sum(v["defined_rows"] for v in coverage.values()),
             "defined_rows_by_region": {region: v["defined_rows"] for region, v in coverage.items()},
             "recipe": "same name.primary or route identity; join shared connectors only below 30-degree deflection; per-tract drop ratio for strokes with >=70% named-class length outside the tract"}
    registry_path = ROOT / "config/bias60-family-t-exact-registry.json"
    registry = {"family": "T", "version": 2, "entries": [entry,
        {"id": "T2-v2", "base_id": "T2", "version": 2, "status": "pending"},
        {"id": "T3-v2", "base_id": "T3", "version": 2, "status": "pending"},
        {"id": "T4-v2", "base_id": "T4", "version": 2, "status": "pending"}]}
    registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    report_path = ROOT / "reports/bias60-family-t-exact-reconciliation.json"
    old = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    old.update({"version": "exact-recipe-reconciliation-progress-v3",
                "scope": "provided-data exact T1-v2 build; T2-T4 remain pending",
                "prohibitions": {"provided_row_payload_read": True,
                                 "labels_or_reference_layers_used": False,
                                 "leaderboard_or_upload_used": False,
                                 "v1_artifacts_overwritten": False},
                "measure_after": {"exact_recipes": 1, "exact_recipes_total": 4,
                                  "v2_artifacts_emitted": [entry],
                                  "coverage_and_orthogonality_refreshed": False},
                "T1_v2_coverage_by_region": coverage,
                "T1_v2_artifact": entry,
                "validation": {
                    "test_command": "uv run --python 3.12.3 --with numpy==2.5.4 --with pyarrow==26.0.0 --with shapely==2.2.0 --with pyproj==3.8.0 --with pytest python -m pytest tests/test_family_t_exact.py tests/test_bias60_family_t.py -q",
                    "test_output": "14 passed",
                    "artifact_checks": "9,379 unique official GEOIDs; 2,057 defined; SHA-256 verified; all T1-T4 v1 hashes re-verified unchanged",
                    "orthogonality_refresh": "not run; remaining T2-v2 through T4-v2 are incomplete"
                },
                "implementation_progress": {
                    "T1": "Implemented and built T1-v2 using the strict <30-degree identity-connected stroke rule; defined coverage is reported by region.",
                    "T2": "No v2 implementation yet.",
                    "T3": "No v2 implementation yet.",
                    "T4": "No v2 implementation yet; supplied centroid interpretation remains unresolved.",
                }})
    report_path.write_text(json.dumps(old, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": str(artifact), "sha256": digest,
                      "registry": str(registry_path.relative_to(ROOT)),
                      "rows": table.num_rows, "defined": entry["defined_rows"],
                      "exact_recipes": "1/4"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
