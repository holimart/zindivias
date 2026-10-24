#!/usr/bin/env python3
"""Fetch hash-pinned official sample IDs and public strata from Source Cooperative."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, dest: Path, expected_sha256: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{dest.name}.", dir=dest.parent, delete=False) as tmp:
        temp_path = Path(tmp.name)
        request = Request(url, headers={"User-Agent": "bias-bounty-reproduction/1.0"})
        try:
            with urlopen(request, timeout=90) as response:
                while block := response.read(1024 * 1024):
                    tmp.write(block)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    actual = sha256(temp_path)
    if actual != expected_sha256:
        temp_path.unlink(missing_ok=True)
        raise ValueError(f"public input hash mismatch for {url}: expected {expected_sha256}, got {actual}")
    os.replace(temp_path, dest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/reproduction-inputs.json")
    args = parser.parse_args()
    config = json.loads(args.config.resolve().read_text(encoding="utf-8"))
    base_url = config["source_base_url"].rstrip("/")
    dest_root = args.workspace.resolve() / config["workspace_directory"]
    fetched = []
    for region, record in config["regions"].items():
        for kind in ("sample", "strata"):
            entry = record[kind]
            relative = Path(entry["remote_path"])
            dest = dest_root / f"{region}-{('sample-submission.csv' if kind == 'sample' else 'strata-tract-table.parquet')}"
            # Force a fresh public-bucket fetch each reproduction; never trust a cache.
            download(f"{base_url}/{relative.as_posix()}", dest, entry["sha256"])
            fetched.append({"region": region, "kind": kind, "path": str(dest),
                            "sha256": sha256(dest), "bytes": dest.stat().st_size})
            print(f"Fetched {region} {kind}: {dest.stat().st_size} bytes; SHA-256 {fetched[-1]['sha256']}")
    print(f"PASS public reproduction inputs: {len(fetched)} hash-pinned files; no credentials used")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
