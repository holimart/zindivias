#!/usr/bin/env python3
"""Build T2-v2 from the allowed roads-unfiltered layer; never overwrite v1."""
from __future__ import annotations
import argparse, hashlib, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import ProvidedInputs, official_scored_pairs, load_config  # noqa: E402
from bias60.ideas.t_exact import t2_exact  # noqa: E402
REGIONS = ["maricopa-az", "northern-ca", "eastern-ok", "south-central-tx"]
SCHEMA_COLUMNS = ["GEOID", "region", "idea", "family", "component", "channel", "value", "defined"]

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    ap.add_argument("--region", choices=REGIONS)
    ap.add_argument("--merge-shards", action="store_true")
    args = ap.parse_args()
    if bool(args.region) == args.merge_shards:
        ap.error("specify exactly one of --region or --merge-shards")
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        raise SystemExit("COMPETITION_WORKSPACE is required")
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    config = load_config(ROOT / "config/bias60-harness-preregistered.json")
    pairs = official_scored_pairs(workspace)
    by_region = {r: [g for g, rr in pairs if rr == r] for r in REGIONS}
    shard_dir = workspace / "ideas/_family_t_exact_v2_shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    if args.region:
        region = args.region
        shard_path = shard_dir / f"T2-v2-{region}.parquet"
        if shard_path.exists():
            raise SystemExit(f"refusing to overwrite shard: {shard_path}")
        output = t2_exact(ProvidedInputs(config, workspace, region, by_region[region]))
        emissions = output.emissions
        rows = []
        for e in emissions:
            rows.extend({"GEOID": g, "region": region, "idea": "T2-v2", "family": "T",
                         "component": e.component, "channel": e.channel, "value": float(v),
                         "defined": bool(d)} for g, v, d in zip(by_region[region], e.values, e.defined))
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, shard_path, compression="zstd")
        meta = {"region": region, "official_geoids": len(by_region[region]),
                "emissions": {e.channel: {"defined_rows": int(e.defined.sum()),
                                          "undefined_rows": int((~e.defined).sum())}
                              for e in emissions},
                "diagnostics": output.diagnostics,
                "sha256": hashlib.sha256(shard_path.read_bytes()).hexdigest()}
        (shard_dir / f"T2-v2-{region}.json").write_text(json.dumps(meta, indent=2)+"\n")
        print(json.dumps(meta, indent=2))
        return 0

    rows = []
    coverage = {}
    for region in REGIONS:
        path = shard_dir / f"T2-v2-{region}.parquet"
        meta_path = shard_dir / f"T2-v2-{region}.json"
        if not path.is_file() or not meta_path.is_file():
            raise SystemExit(f"missing T2-v2 shard for {region}")
        meta = json.loads(meta_path.read_text())
        if hashlib.sha256(path.read_bytes()).hexdigest() != meta["sha256"]:
            raise SystemExit(f"T2-v2 shard hash mismatch for {region}")
        table = pq.read_table(path)
        if table.num_rows != len(by_region[region]) * 2:
            raise SystemExit(f"unexpected T2-v2 shard row count for {region}")
        actual = table["GEOID"].to_pylist()
        channels = table["channel"].to_pylist()
        for channel in ("covariate:orphan_link_segments", "covariate:attached_carriageway_length_km"):
            channel_geoids = [geoid for geoid, name in zip(actual, channels) if name == channel]
            if channel_geoids != by_region[region]:
                raise SystemExit(f"T2-v2 shard GEOID order mismatch for {region}/{channel}")
        rows.extend(table.to_pylist())
        coverage[region] = meta
    artifact = workspace / "ideas/T2-v2.parquet"
    if artifact.exists():
        raise SystemExit(f"refusing to overwrite versioned artifact: {artifact}")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), artifact, compression="zstd")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    entry = {"id":"T2-v2", "base_id":"T2", "version":2, "family":"T",
             "status":"implemented_exact_recipe", "path":"ideas/T2-v2.parquet",
             "sha256":digest, "official_rows":len(pairs), "defined_rows_by_region":
             {r:{c:x["defined_rows"] for c,x in coverage[r]["emissions"].items()} for r in REGIONS},
             "recipe":"orphan links have only non-named neighbors at both endpoint connectors; attached non-named carriageway length clipped to 2 km link buffer"}
    regpath = ROOT / "config/bias60-family-t-exact-registry.json"
    registry = json.loads(regpath.read_text())
    registry["entries"] = [entry if e.get("id")=="T2-v2" else e for e in registry["entries"]]
    regpath.write_text(json.dumps(registry, indent=2)+"\n")
    report_path = ROOT / "reports/bias60-family-t-exact-reconciliation.json"
    report = json.loads(report_path.read_text())
    report["T2_v2_artifact"] = entry
    report["T2_v2_coverage_by_region"] = coverage
    report["measure_after"]["exact_recipes"] = 2
    report["measure_after"]["v2_artifacts_emitted"] = [report["T1_v2_artifact"], entry]
    report["measure_after"]["coverage_and_orthogonality_refreshed"] = False
    report["scope"] = "T1-v2 and T2-v2 artifacts built; T3/T4 remain pending. Orthogonality refresh pending."
    report["implementation_progress"]["T2"] = "Implemented and built from provided roads-unfiltered rows; official coverage is recorded by region."
    report["validation"]["artifact_checks"] = "T2-v2 has two emissions for each official GEOID; regional shard hashes and GEOID order verified; v1 artifacts untouched."
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n")
    print(json.dumps({"artifact":str(artifact),"sha256":digest,"rows":len(rows),"exact_recipes":"2/4"},indent=2))
    return 0
if __name__ == "__main__": raise SystemExit(main())
