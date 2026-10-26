#!/usr/bin/env python3
"""Materialize the registered Mx4p screen from the completed permitted-input sensitivity artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import official_scored_pairs  # noqa: E402

FEATURE_CHANNEL = "covariate:population_weighted_peer_county_share"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def output_table(geoids: list[str], regions: list[str], values: list[float],
                 defined: list[bool]) -> pa.Table:
    if not (len(geoids) == len(regions) == len(values) == len(defined)):
        raise ValueError("Mx4p output columns must align")
    numeric = np.asarray(values, dtype=np.float64)
    mask = np.asarray(defined, dtype=bool)
    if len(set(geoids)) != len(geoids):
        raise ValueError("Mx4p contains duplicate GEOIDs")
    if np.any(mask & (~np.isfinite(numeric) | (numeric < 0.0) | (numeric > 1.0))):
        raise ValueError("defined Mx4p values must be finite and within [0,1]")
    if np.any(~mask & (numeric != 0.0)):
        raise ValueError("undefined Mx4p values must use the standard zero placeholder")
    return pa.table({
        "GEOID": pa.array(geoids, type=pa.string()),
        "region": pa.array(regions, type=pa.string()),
        "idea": pa.array(["Mx4p"] * len(geoids), type=pa.string()),
        "family": pa.array(["Mx"] * len(geoids), type=pa.string()),
        "component": pa.array(["F"] * len(geoids), type=pa.string()),
        "channel": pa.array([FEATURE_CHANNEL] * len(geoids), type=pa.string()),
        "value": pa.array(numeric, type=pa.float64()),
        "defined": pa.array(mask, type=pa.bool_()),
    })


def parquet_bytes(table: pa.Table) -> bytes:
    import io
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False,
                   write_statistics=False)
    return sink.getvalue()


def build(workspace: Path, report_path: Path, out_path: Path) -> dict:
    audit = json.loads(report_path.read_text(encoding="utf-8"))
    if audit.get("audit") != "Mx4 leave-one-tract-out population-weighted county context; bounded sensitivity screen, not promotion":
        raise ValueError("unexpected Mx4 population-weighted source audit")
    if not audit.get("acceptance", {}).get("acceptance_pass") or not audit["scope"].get("exact_id_partition"):
        raise ValueError("source sensitivity audit did not pass its exact-ID and coverage gates")
    if not audit["acceptance"].get("frozen_mx4_unchanged") or audit["scope"].get("mx4_frozen_artifact_modified"):
        raise ValueError("source audit does not confirm frozen Mx4 was preserved")
    artifact = workspace / "mx4-population-weighted-context" / "population-weighted-peer-mx4.parquet"
    expected_sha = audit["per_tract_artifact"]["sha256"]
    actual_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if actual_sha != expected_sha:
        raise ValueError("source population-weighted artifact hash differs from its audit report")
    pairs = official_scored_pairs(workspace)
    if len(pairs) != 9379 or len({geoid for geoid, _ in pairs}) != 9379:
        raise ValueError("official GEOID partition is not exactly 9,379 unique IDs")
    id_to_region = dict(pairs)
    ids = [geoid for geoid, _ in pairs]
    regions = [region for _, region in pairs]
    source = pq.read_table(artifact, columns=["GEOID", "region", "peer_population_weighted_share", "peer_defined"])
    values_by_id: dict[str, tuple[str, float, bool]] = {}
    for row in source.to_pylist():
        geoid = str(row["GEOID"])
        region = str(row["region"])
        if geoid not in id_to_region or id_to_region[geoid] != region or geoid in values_by_id:
            raise ValueError(f"source artifact contains an unexpected or duplicate GEOID/region: {geoid}")
        is_defined = bool(row["peer_defined"])
        value = float(row["peer_population_weighted_share"]) if is_defined else 0.0
        if is_defined and (not np.isfinite(value) or not 0.0 <= value <= 1.0):
            raise ValueError(f"source peer share is invalid for {geoid}")
        if not is_defined and row["peer_population_weighted_share"] is not None:
            raise ValueError(f"undefined source peer share is not null for {geoid}")
        values_by_id[geoid] = (region, value, is_defined)
    if set(values_by_id) != set(ids):
        raise ValueError("population-weighted source artifact does not cover exact official GEOIDs")
    values = [values_by_id[geoid][1] for geoid in ids]
    defined = [values_by_id[geoid][2] for geoid in ids]
    table = output_table(ids, regions, values, defined)
    payload = parquet_bytes(table)
    if payload != parquet_bytes(table):
        raise ValueError("Mx4p Parquet serialization is not deterministic")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)
    return {"path": str(out_path), "rows": table.num_rows, "sha256": sha256_bytes(payload),
            "source_path": str(artifact), "source_sha256": actual_sha,
            "defined": int(sum(defined)), "undefined": len(defined) - int(sum(defined))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--audit-report", default="reports/mx4-population-weighted-context.json")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    out_path = Path(args.out) if args.out else workspace / "ideas" / "Mx4p.parquet"
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    report = build(workspace, ROOT / args.audit_report, out_path)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
