#!/usr/bin/env python3
"""Build format-only, Data-page-column-order Bias candidate files from frozen sources."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_REL = "config/bias-column-order-rescore-preregistered.json"
REGISTRY_REL = "reports/candidates.json"
TEMPLATE_REL = "SampleSubmission.csv"
REPORT_REL = "reports/bias-column-order-cgs-build.json"
EXPECTED_SOURCE_COLUMNS = ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"]
OUTPUT_COLUMNS = ["GEOID", "coverage_gap_score"]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def template_ids(path: Path) -> tuple[list[str], str]:
    ids = []
    with path.open("rb") as f:
        header = f.readline().decode("utf-8-sig").rstrip("\r\n").split(",")
        if header[:2] != OUTPUT_COLUMNS:
            raise ValueError(f"{path}: Data-page header does not begin {OUTPUT_COLUMNS}")
        for line_no, raw in enumerate(f, start=2):
            if not raw.strip():
                continue
            first = raw.partition(b",")[0].strip()
            try:
                geoid = first.decode("ascii")
            except UnicodeDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: GEOID is not ASCII") from exc
            if len(geoid) != 11 or not geoid.isdigit():
                raise ValueError(f"{path}:{line_no}: invalid GEOID {geoid!r}")
            ids.append(geoid)
    if len(ids) != 9794 or len(set(ids)) != len(ids):
        raise ValueError("Data-page template must contain exactly 9,794 unique GEOIDs")
    return ids, digest(("\n".join(ids) + "\n").encode("ascii"))


def read_source(path: Path, expected_ids: list[str], expected_hash: str) -> tuple[list[tuple[str, str]], str]:
    source_hash = sha256_file(path)
    if source_hash != expected_hash:
        raise ValueError(f"frozen source hash mismatch for {path}: {source_hash}")
    out: list[tuple[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != EXPECTED_SOURCE_COLUMNS:
            raise ValueError(f"{path}: expected source columns {EXPECTED_SOURCE_COLUMNS}, got {reader.fieldnames}")
        for line_no, row in enumerate(reader, start=2):
            geoid = row["GEOID"]
            value = row["coverage_gap_score"]
            if geoid is None or value is None or value.strip() == "":
                raise ValueError(f"{path}:{line_no}: blank GEOID or frozen composite value")
            out.append((geoid, value))
    if len(out) != len(expected_ids) or [row[0] for row in out] != expected_ids:
        raise ValueError(f"{path}: frozen source order/IDs differ from Data-page template GEOID order")
    return out, source_hash


def atomic_write_if_same_or_new(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"refusing to overwrite different frozen artifact: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as f:
        tmp = Path(f.name)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def build(repo: Path, workspace: Path) -> dict[str, Any]:
    config_path = repo / CONFIG_REL
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes)
    if config.get("task") != "bias-column-order-rescore" or config.get("purpose") != "calibration":
        raise ValueError("unexpected preregistration config")
    ids, id_hash = template_ids(workspace / TEMPLATE_REL)
    registry = json.loads((repo / REGISTRY_REL).read_text(encoding="utf-8"))
    candidates = {x["candidate_id"]: x for x in registry.get("candidate_set", [])}
    output_records = []
    for spec in config["sources"]:
        source_id = spec["source_candidate_id"]
        candidate = candidates.get(source_id)
        if candidate is None or candidate.get("pre_registered") is not True:
            raise ValueError(f"source candidate {source_id} is not pre-registered")
        if candidate.get("artifact") != spec["source_artifact"] or candidate.get("sha256") != spec["source_sha256"]:
            raise ValueError(f"source candidate registry differs from preregistration for {source_id}")
        if candidate.get("purpose") != "calibration":
            raise ValueError(f"source candidate {source_id} is not a calibration candidate")
        card_path = repo / spec["pre_registered_card_path"]
        if not card_path.is_file() or sha256_file(card_path) != spec["pre_registered_card_sha256"]:
            raise ValueError(f"frozen preregistered card hash mismatch for {source_id}")
        source_rows, source_hash = read_source(repo / spec["source_artifact"], ids, spec["source_sha256"])
        # csv.writer only serializes the two copied source fields. The score string
        # is passed through unchanged; no float parsing, rounding, or arithmetic occurs.
        import io
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(OUTPUT_COLUMNS)
        writer.writerows(source_rows)
        csv_bytes = stream.getvalue().encode("utf-8")
        artifact_path = repo / spec["output_artifact"]
        atomic_write_if_same_or_new(artifact_path, csv_bytes)
        artifact_hash = digest(csv_bytes)
        manifest = {
            "schema_version": 1,
            "candidate_id": spec["candidate_id"],
            "source_candidate_id": source_id,
            "artifact": spec["output_artifact"],
            "sha256": artifact_hash,
            "rows": len(source_rows),
            "columns": OUTPUT_COLUMNS,
            "purpose": "calibration",
            "preregistered_card_path": spec["pre_registered_card_path"],
            "preregistered_card_sha256": spec["pre_registered_card_sha256"],
            "format_fix_only": True,
            "source_artifact": spec["source_artifact"],
            "source_sha256": source_hash,
            "source_columns": EXPECTED_SOURCE_COLUMNS,
            "copied_value_column": "coverage_gap_score",
            "copied_values_byte_identical": True,
            "template_geoid_count": len(ids),
            "template_geoid_order_sha256": id_hash,
            "no_score_values_read_from_template": True,
            "candidate_values_recomputed": False,
            "upload_performed": False,
        }
        manifest_path = repo / f"reports/candidates/{spec['candidate_id']}_manifest.json"
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        atomic_write_if_same_or_new(manifest_path, manifest_bytes)
        output_records.append({
            "candidate_id": spec["candidate_id"],
            "source_candidate_id": source_id,
            "source_artifact": spec["source_artifact"],
            "source_sha256": source_hash,
            "artifact": spec["output_artifact"],
            "sha256": artifact_hash,
            "manifest": str(manifest_path.relative_to(repo)),
            "manifest_sha256": digest(manifest_bytes),
            "rows": len(source_rows),
            "columns": OUTPUT_COLUMNS,
            "copied_values_byte_identical": True,
        })
    report = {
        "schema_version": 1,
        "task": "BIAS_COLUMN_ORDER_CGS_BUILD",
        "config": CONFIG_REL,
        "config_sha256": digest(config_bytes),
        "template_id_count": len(ids),
        "template_geoid_order_sha256": id_hash,
        "template_score_values_read": False,
        "output_column_order": OUTPUT_COLUMNS,
        "source_values_changed": False,
        "public_scores_used": False,
        "upload_performed": False,
        "candidates": output_records,
    }
    report_path = repo / REPORT_REL
    report_bytes = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_if_same_or_new(report_path, report_bytes)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=ROOT)
    ap.add_argument("--workspace", type=Path, default=Path(os.environ.get("COMPETITION_WORKSPACE", ".")))
    args = ap.parse_args()
    report = build(args.repo, args.workspace)
    print(json.dumps({"candidates": len(report["candidates"]), "rows_per_candidate": report["template_id_count"],
                      "output_columns": report["output_column_order"], "upload_performed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
