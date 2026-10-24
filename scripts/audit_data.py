#!/usr/bin/env python3
"""Inventory local challenge data and public bucket metadata without bulk downloads."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

BUCKET = "us-west-2.opendata.source.coop/humane-intelligence/bias-bounty-mapping-equity-challenge"
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
PREFIXES = ("reference", "strata")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def csv_info(path: Path) -> dict:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        rows = 0
        nulls = dict.fromkeys(fields, 0)
        ids = []
        for row in reader:
            rows += 1
            for field in fields:
                if row.get(field) in (None, ""):
                    nulls[field] += 1
            if "GEOID" in row and len(ids) < 5:
                ids.append(row["GEOID"])
    return {"format": "csv", "rows": rows, "columns": fields,
            "null_counts": nulls, "geoid_samples": ids}


def local_inventory(root: Path) -> list[dict]:
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        suffix = path.suffix.lower()
        record = {"path": str(path.relative_to(root)), "bytes": path.stat().st_size,
                  "sha256": sha256(path)}
        if suffix == ".csv":
            record.update(csv_info(path))
        elif suffix in (".parquet", ".geoparquet"):
            record.update(parquet_info(path))
        else:
            record["format"] = suffix.lstrip(".") or "unknown"
        files.append(record)
    return files


def parquet_info(source) -> dict:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return {"format": "parquet", "schema": None,
                "note": "pyarrow unavailable; metadata not opened"}
    parquet = pq.ParquetFile(source)
    schema = [{"name": field.name, "type": str(field.type), "nullable": field.nullable}
              for field in parquet.schema_arrow]
    metadata = parquet.metadata.metadata or {}
    geo = metadata.get(b"geo")
    geo_meta = json.loads(geo.decode("utf-8")) if geo else None
    return {"format": "parquet", "rows": parquet.metadata.num_rows,
            "row_groups": parquet.metadata.num_row_groups, "schema": schema,
            "geometry": (geo_meta.get("columns") if geo_meta else None),
            "geo_metadata": geo_meta}


def public_inventory(sample_submission: Path | None = None) -> list[dict]:
    import pyarrow.fs as pafs

    filesystem = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    files = []
    sample_geoids_by_region = {}
    if sample_submission:
        with sample_submission.open("r", newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                sample_geoids_by_region.setdefault(row["region"], set()).add(row["GEOID"])
    joins = {}
    for prefix in PREFIXES:
        for region in REGIONS:
            directory = f"{BUCKET}/{prefix}/{region}"
            for info in sorted(filesystem.get_file_info(pafs.FileSelector(directory)),
                               key=lambda item: item.path):
                if info.type != pafs.FileType.File:
                    continue
                path = info.path
                suffix = Path(path).suffix.lower()
                record = {"path": f"s3://{path}", "bytes": info.size,
                          "region": region, "family": prefix,
                          "format": suffix.lstrip(".") or "unknown",
                          "checksum": None,
                          "checksum_note": "anonymous listing exposes no content SHA-256"}
                if suffix == ".parquet":
                    # ParquetFile reads the footer by ranged S3 requests; no full object download.
                    record.update(parquet_info(filesystem.open_input_file(path)))
                    if (sample_geoids_by_region and prefix == "reference" and
                            path.endswith("census-acs-housing.parquet")):
                        import pyarrow.parquet as pq
                        table = pq.read_table(filesystem.open_input_file(path), columns=["GEOID"])
                        tract_ids = set(table.column("GEOID").to_pylist())
                        sample_ids = sample_geoids_by_region.get(region, set())
                        joins[region] = {
                            "tract_key": "GEOID", "tract_rows": len(tract_ids),
                            "sample_rows_for_region": len(sample_ids),
                            "matched_sample_geoids": len(sample_ids & tract_ids),
                            "unmatched_sample_geoids": len(sample_ids - tract_ids),
                            "match_fraction": (len(sample_ids & tract_ids) /
                                               len(sample_ids) if sample_ids else None),
                        }
                elif suffix == ".csv":
                    record["schema_note"] = "CSV listed by object metadata; not bulk-read"
                files.append(record)
    return files, joins


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path,
                        help="inventory files already present locally")
    parser.add_argument("--public-bucket", action="store_true",
                        help="list four authorized regions and inspect Parquet footers")
    parser.add_argument("--sample-submission", type=Path,
                        help="compare sample GEOIDs to each region's ACS tract key")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.data_root and not args.public_bucket:
        parser.error("choose --data-root and/or --public-bucket")
    result = {"files": []}
    if args.data_root:
        root = args.data_root.expanduser().resolve()
        if not root.is_dir():
            parser.error(f"data root is not a directory: {root}")
        result["data_root"] = str(root)
        result["files"].extend(local_inventory(root))
    if args.public_bucket:
        result["bucket"] = f"s3://{BUCKET}"
        bucket_files, joins = public_inventory(args.sample_submission)
        result["files"].extend(bucket_files)
        if args.sample_submission:
            result["sample_submission"] = str(args.sample_submission.resolve())
            result["sample_geoid_joins"] = joins
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"Inventoried {len(result['files'])} files; report: {args.out}")


if __name__ == "__main__":
    main()
