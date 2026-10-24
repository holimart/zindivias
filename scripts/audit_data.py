#!/usr/bin/env python3
"""Inventory locally available challenge data without fetching remote packages."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


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


def parquet_info(path: Path) -> dict:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return {"format": "parquet", "schema": None,
                "note": "pyarrow unavailable; metadata not opened"}
    parquet = pq.ParquetFile(path)
    schema = [{"name": field.name, "type": str(field.type), "nullable": field.nullable}
              for field in parquet.schema_arrow]
    return {"format": "parquet", "rows": parquet.metadata.num_rows,
            "row_groups": parquet.metadata.num_row_groups, "schema": schema}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = args.data_root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"data root is not a directory: {root}")
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
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"data_root": str(root), "files": files},
                                   indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Inventoried {len(files)} files under {root}; report: {args.out}")


if __name__ == "__main__":
    main()
