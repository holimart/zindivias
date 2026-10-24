#!/usr/bin/env python3
"""Estimate Overture building source mix from deterministic row-group samples.

Only the provided Overture buildings ``sources`` column is read. The sample is
regional and descriptive (no tract assignment); source memberships may overlap
and are not features for any scored prediction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
BUCKET = "us-west-2.opendata.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge"
BASE_SEED = 20261024
ROW_SAMPLE_FRACTION = 0.10


def seed_for(region: str, row_group_index: int) -> int:
    label = f"{region}:{row_group_index}"
    return BASE_SEED + int.from_bytes(hashlib.sha256(label.encode("utf-8")).digest()[:4], "big")


def sample_row_indices(region: str, row_group_index: int,
                       row_count: int) -> tuple[list[int], int]:
    if row_count < 1:
        raise ValueError("row_count must be positive")
    sample_n = max(1, math.ceil(row_count * ROW_SAMPLE_FRACTION))
    seed = seed_for(region, row_group_index)
    selected = sorted(random.Random(seed).sample(range(row_count), sample_n))
    return selected, seed


def classify_building_sources(sources: Any) -> tuple[set[str], str]:
    names = {
        str(source["dataset"]).strip().casefold()
        for source in (sources or [])
        if isinstance(source, dict) and source.get("dataset") and str(source["dataset"]).strip()
    }
    has_ms = any(name.startswith("microsoft") for name in names)
    has_osm = any(name == "osm" or name.startswith("openstreetmap") for name in names)
    has_esri = any(name.startswith("esri") for name in names)
    known = {name for name in names if name.startswith("microsoft") or name.startswith("esri")
             or name == "osm" or name.startswith("openstreetmap")}
    has_other = bool(names - known)
    if not names:
        exclusive = "no_dataset"
    elif has_ms and has_osm and not has_esri and not has_other:
        exclusive = "microsoft_osm"
    elif has_ms and not has_osm and not has_esri and not has_other:
        exclusive = "microsoft_only"
    elif has_osm and not has_ms and not has_esri and not has_other:
        exclusive = "osm_only"
    elif has_esri and not has_ms and not has_osm and not has_other:
        exclusive = "esri_only"
    else:
        exclusive = "other_or_mixed"
    return names, exclusive


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_uri(region: str) -> str:
    return f"s3://{BUCKET}/reference/{region}/{region}-overture-buildings.parquet"


def run(out_path: Path) -> dict[str, Any]:
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    region_results: dict[str, Any] = {}
    for region in REGIONS:
        key = f"{BUCKET}/reference/{region}/{region}-overture-buildings.parquet"
        info = fs.get_file_info(key)
        if info.type != pafs.FileType.File:
            raise FileNotFoundError(f"provided Overture buildings file unavailable: s3://{key}")
        parquet = pq.ParquetFile(fs.open_input_file(key))
        if "sources" not in parquet.schema_arrow.names:
            raise ValueError(f"sources column missing for {region}")
        membership = Counter()
        exclusive = Counter()
        dataset_names = Counter()
        sampled_rows = null_sources_rows = 0
        sampled_group_rows = []
        for group_index in range(parquet.metadata.num_row_groups):
            group = parquet.read_row_group(group_index, columns=["sources"])
            all_sources = group.column("sources").combine_chunks()
            sample_indices, seed = sample_row_indices(region, group_index, len(all_sources))
            source_rows = all_sources.take(pa.array(sample_indices, type=pa.int64())).to_pylist()
            sampled_group_rows.append({"row_group": group_index, "input_rows": len(all_sources),
                                       "sampled_rows": len(source_rows), "sample_seed": seed})
            for sources in source_rows:
                sampled_rows += 1
                if sources is None:
                    null_sources_rows += 1
                names, class_name = classify_building_sources(sources)
                exclusive[class_name] += 1
                for name in names:
                    dataset_names[name] += 1
                if any(name.startswith("microsoft") for name in names):
                    membership["microsoft_any"] += 1
                if any(name == "osm" or name.startswith("openstreetmap") for name in names):
                    membership["openstreetmap_any"] += 1
                if any(name.startswith("esri") for name in names):
                    membership["esri_any"] += 1
                known = {name for name in names if name.startswith("microsoft") or name.startswith("esri")
                         or name == "osm" or name.startswith("openstreetmap")}
                if names - known:
                    membership["other_dataset_any"] += 1
                if not names:
                    membership["no_dataset_named"] += 1
        if sampled_rows == 0:
            raise ValueError(f"empty row-group sample for {region}")
        exclusive_keys = ("microsoft_only", "microsoft_osm", "osm_only", "esri_only",
                          "other_or_mixed", "no_dataset")
        region_results[region] = {
            "source_uri": source_uri(region),
            "object_bytes": info.size,
            "input_rows": parquet.metadata.num_rows,
            "row_groups_total": parquet.metadata.num_row_groups,
            "sample_design": "all row groups are sampling strata; within each group, simple random sample without replacement of ceil(10% of records), using a deterministic region/group-specific seed",
            "row_sample_fraction_per_group": ROW_SAMPLE_FRACTION,
            "sampled_row_groups": sampled_group_rows,
            "sampled_row_group_count": len(sampled_group_rows),
            "sampled_rows": sampled_rows,
            "null_sources_rows": null_sources_rows,
            "dataset_membership_counts": dict(sorted(dataset_names.items())),
            "dataset_membership_shares": {
                name: count / sampled_rows for name, count in sorted(dataset_names.items())
            },
            "provider_membership_counts": {name: membership[name] for name in (
                "microsoft_any", "openstreetmap_any", "esri_any", "other_dataset_any", "no_dataset_named")},
            "provider_membership_shares": {name: membership[name] / sampled_rows for name in (
                "microsoft_any", "openstreetmap_any", "esri_any", "other_dataset_any", "no_dataset_named")},
            "exclusive_source_class_counts": {name: exclusive[name] for name in exclusive_keys},
            "exclusive_source_class_shares": {name: exclusive[name] / sampled_rows for name in exclusive_keys},
            "memberships_overlap": True,
        }
    report = {
        "method": "Deterministic regional row-group sample of provided Overture building sources metadata",
        "sampling": {
            "row_group_fraction": 1.0,
            "record_sample_fraction_within_each_group": ROW_SAMPLE_FRACTION,
            "seed_base": BASE_SEED,
            "seed_rule": "BASE_SEED plus the low four bytes of SHA-256(region:row_group_index)",
            "source_column_only": True,
            "sample_score_placeholders_read": False,
            "labels_or_removed_reference_layers_read": False,
            "external_data_used": [],
            "use_in_scored_predictions": False,
        },
        "definitions": {
            "microsoft_any": "any source dataset name beginning with Microsoft",
            "openstreetmap_any": "any source dataset named OSM or beginning with OpenStreetMap",
            "esri_any": "any source dataset name beginning with Esri",
            "other_dataset_any": "any dataset name outside the Microsoft, OpenStreetMap/OSM, and Esri groups",
            "exclusive_classes": ["microsoft_only", "microsoft_osm", "osm_only", "esri_only", "other_or_mixed", "no_dataset"],
            "overlap_caveat": "Provider memberships overlap; only the exclusive classes form a partition.",
        },
        "scope": "The sampling frame is each region-specific provided Overture buildings object; no bbox, geometry, or exact official-tract assignment is read.",
        "limitations": [
            "This is a descriptive within-object sample of row groups, not a tract-level accuracy or coverage estimate; a small number of regional-object rows may not fall inside the exact official tract sample.",
            "Source attribution does not establish geometric equality, completeness, reference vintage, or which provider uniquely contributed each geometry.",
            "Row-group sampling uncertainty and spatial clustering are not modelled; the selected groups and fixed seeds are published for reproduction.",
        ],
        "regions": region_results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "reports/building-source-mix.json")
    args = parser.parse_args()
    report = run(args.out)
    for region, info in report["regions"].items():
        exclusive = info["exclusive_source_class_shares"]
        print(f"{region}: sampled {info['sampled_rows']} rows from {info['sampled_row_group_count']} row groups; "
              f"MS-only={exclusive['microsoft_only']:.3f}, MS+OSM={exclusive['microsoft_osm']:.3f}, "
              f"OSM-only={exclusive['osm_only']:.3f}")
    print(f"Report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
