#!/usr/bin/env python3
"""Cold-rebuild the current code-review pair from public features and strata."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
from importlib.metadata import version

import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import estimate_gaps as estimator  # noqa: E402

COLUMNS = ("GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score")
REGIONS = estimator.REGIONS


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_template(path: Path) -> tuple[list[str], dict[str, str]]:
    # Only the provided template's GEOID/region fields are read; score cells are ignored.
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader, [])
        if "GEOID" not in header or "region" not in header:
            raise ValueError("template projection must contain GEOID and region")
        gi, ri = header.index("GEOID"), header.index("region")
        ids: list[str] = []
        regions: dict[str, str] = {}
        for row in reader:
            geoid, region = row[gi], row[ri]
            if not geoid or geoid in regions:
                raise ValueError(f"template has empty or duplicate GEOID: {geoid!r}")
            ids.append(geoid)
            regions[geoid] = region
    if len(ids) != 9794 or set(regions.values()) != {*REGIONS, "eastern-wa"}:
        raise ValueError(f"expected 9,794 IDs in five template regions; found {len(ids)}")
    return ids, regions


def make_pair_rows(
    template_ids: list[str], template_regions: dict[str, str],
    official_region: dict[str, str], region_means: dict[str, float], alpha: float,
) -> dict[str, list[dict[str, str]]]:
    if len(official_region) != 9379 or set(official_region) - set(template_ids):
        raise ValueError("official IDs must be exactly 9,379 IDs contained in the template")
    if any(template_regions[g] != r for g, r in official_region.items()):
        raise ValueError("template region labels disagree with authoritative samples")
    if set(region_means) != set(REGIONS) or not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError("invalid region means or P4 alpha")
    p4_official = []
    p4_by_id: dict[str, dict[str, str]] = {}
    for geoid in (g for r in REGIONS for g, rg in official_region.items() if rg == r):
        region = official_region[geoid]
        value = alpha * region_means[region]
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"P4 value outside [0,1] at {geoid}")
        row = {"GEOID": geoid, **{c: format(value, ".17g") for c in COLUMNS[1:]}}
        p4_official.append(row)
        p4_by_id[geoid] = row
    if len(p4_official) != 9379:
        raise AssertionError("official P4 row count changed")
    # This is the committed P4 east-WA template-fill policy: pooled mean of
    # each transformed field over official rows, not an eastern-WA estimate.
    wa_fill = {column: sum(float(row[column]) for row in p4_official) / len(p4_official)
               for column in COLUMNS[1:]}
    c0_rows, p4_rows = [], []
    for geoid in template_ids:
        c0_rows.append({"GEOID": geoid, **{c: "0" for c in COLUMNS[1:]}})
        if geoid in p4_by_id:
            p4_rows.append(p4_by_id[geoid])
        else:
            if template_regions[geoid] != "eastern-wa":
                raise ValueError(f"unexpected template-only GEOID {geoid}")
            p4_rows.append({"GEOID": geoid,
                            **{c: format(wa_fill[c], ".17g") for c in COLUMNS[1:]}})
    return {"C0": c0_rows, "P4": p4_rows}


def write_candidate(path: Path, rows: list[dict[str, str]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return sha256(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True,
                        help="isolated reproduction workspace, with defined-flags-inputs/ and an empty feature cache")
    parser.add_argument("--calibration", type=Path, default=ROOT / "config/calibration.json")
    parser.add_argument("--input-config", type=Path, default=ROOT / "config/reproduction-inputs.json")
    parser.add_argument("--template-projection", type=Path,
                        default=ROOT / "data/reproduction/template-geoid-region.csv")
    parser.add_argument("--manifest", type=Path, default=ROOT / "reports/candidates.json")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/reproduce-run.json")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    data_root = workspace / "defined-flags-inputs"
    feature_root = workspace / "gap-estimator" / "features"
    calibration = json.loads(args.calibration.resolve().read_text(encoding="utf-8"))
    input_config = json.loads(args.input_config.resolve().read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.resolve().read_text(encoding="utf-8"))
    manifest_entries = {item["candidate_id"]: item for item in manifest["candidate_set"]}
    configured = {item["candidate_id"]: item for item in calibration["review_pair"]}
    if set(configured) != {"C0", "P4"}:
        raise ValueError("review pair must be C0/P4 for this frozen reproduction package")
    for cid, item in configured.items():
        if cid not in manifest_entries or manifest_entries[cid]["sha256"] != item["sha256"]:
            raise ValueError(f"calibration/config hash disagrees with candidate manifest for {cid}")
    alpha = float(calibration["lb_calibrated_constants"]["alpha_c1"]["value"])

    template_ids, template_regions = read_template(args.template_projection.resolve())
    template_projection_hash = sha256(args.template_projection.resolve())
    if (len(template_ids) != input_config["template_projection"]["rows"]
            or template_projection_hash != input_config["template_projection"]["sha256"]):
        raise ValueError("committed template GEOID/region projection hash/row mismatch")

    sample_by_region: dict[str, list[dict]] = {}
    strata_by_region: dict[str, dict[str, dict]] = {}
    official_region: dict[str, str] = {}
    input_hashes: dict[str, dict[str, str]] = {}
    feature_info: dict[str, dict] = {}
    public_fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    for region in REGIONS:
        sample, strata = estimator.load_sample_and_strata(region, data_root)
        sample_by_region[region] = sample
        strata_by_region[region] = strata
        for row in sample:
            geoid = row["GEOID"]
            if geoid in official_region:
                raise ValueError(f"duplicate official GEOID {geoid}")
            official_region[geoid] = region
        entry = input_config["regions"][region]
        sample_path = data_root / f"{region}-sample-submission.csv"
        strata_path = data_root / f"{region}-strata-tract-table.parquet"
        hashes = {"sample": sha256(sample_path), "strata": sha256(strata_path)}
        if hashes["sample"] != entry["sample"]["sha256"] or hashes["strata"] != entry["strata"]["sha256"]:
            raise ValueError(f"sample/strata input hash mismatch for {region}: {hashes}")
        input_hashes[region] = hashes

        # Force feature aggregation every run: the code-review command is a
        # cold-cache reproduction, independent of any previous derived cache.
        cache_path = feature_root / f"{region}-overture-tract-summary.parquet"
        info = estimator.build_feature_cache(
            public_fs, region, cache_path, [row["GEOID"] for row in sample]
        )
        metadata_path = cache_path.with_suffix(".json")
        metadata_path.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        feature_table = pq.read_table(cache_path)
        feature_map = {str(row["GEOID"]): row for row in feature_table.to_pylist()}
        expected_ids = [row["GEOID"] for row in sample]
        if set(feature_map) != set(expected_ids) or len(feature_map) != len(expected_ids):
            raise ValueError(f"rebuilt feature IDs do not exactly match official samples: {region}")
        estimated = [estimator.estimate_one(feature_map[g], strata[g]) for g in expected_ids]
        if any(row["coverage_gap_score"] is None for row in estimated):
            raise ValueError(f"null C1 source prediction in {region}")
        region_mean = sum(float(row["coverage_gap_score"]) for row in estimated) / len(estimated)
        if not math.isfinite(region_mean) or not 0 <= region_mean <= 1:
            raise ValueError(f"invalid C1 region mean for {region}")
        feature_info[region] = {
            "feature_cache_sha256": info["feature_cache_sha256"],
            "feature_cache_rows": info["tracts"],
            "aggregate_schema_version": info["aggregate_schema_version"],
            "elapsed_seconds": info["elapsed_seconds"],
            "region_C1_mean": region_mean,
            "source_objects": {
                "acs": {"uri": info["acs_source_uri"], "rows": info["acs_rows"]},
                "buildings": {"uri": info["buildings"]["source_uri"],
                              "bytes": info["buildings"]["object_bytes"],
                              "elapsed_seconds": info["buildings"]["elapsed_seconds"]},
                "pois": {"uri": info["pois"]["source_uri"],
                         "bytes": info["pois"]["object_bytes"],
                         "elapsed_seconds": info["pois"]["elapsed_seconds"]},
                "roads": {"uri": info["roads"]["source_uri"],
                          "bytes": info["roads"]["object_bytes"],
                          "elapsed_seconds": info["roads"]["elapsed_seconds"]},
            },
        }

    if len(official_region) != 9379 or not set(official_region).issubset(template_ids):
        raise ValueError("expected 9,379 official IDs contained in the platform template projection")
    if any(template_regions[g] != r for g, r in official_region.items()):
        raise ValueError("template projection metadata disagrees with authoritative samples")
    template_only = [g for g in template_ids if g not in official_region]
    if len(template_only) != 415 or any(template_regions[g] != "eastern-wa" for g in template_only):
        raise ValueError("expected exactly 415 eastern-WA template-only rows")

    pair_rows = make_pair_rows(template_ids, template_regions, official_region,
                               {r: feature_info[r]["region_C1_mean"] for r in REGIONS}, alpha)
    output_hashes = {}
    for cid, rows in pair_rows.items():
        path = ROOT / configured[cid]["artifact"]
        digest = write_candidate(path, rows)
        if digest != configured[cid]["sha256"]:
            raise ValueError(f"rebuilt {cid} SHA-256 differs from frozen manifest: {digest}")
        if digest != manifest_entries[cid]["sha256"]:
            raise ValueError(f"rebuilt {cid} SHA-256 differs from reports/candidates.json")
        output_hashes[cid] = {
            "artifact": configured[cid]["artifact"],
            "sha256": digest,
            "manifest_sha256": manifest_entries[cid]["sha256"],
            "rows": len(rows),
            "columns": list(COLUMNS),
            "matches_manifest": True,
        }
        print(f"{cid} SHA-256 {digest} (matches reports/candidates.json)")

    report = {
        "method": "cold reproduction from public Source Cooperative Overture/ACS objects, official sample IDs and strata",
        "run_started_utc": datetime.now(timezone.utc).isoformat(),
        "code_review_pair": list(configured),
        "review_pair_is_final_private_selection": False,
        "review_pair_note": calibration["review_pair_status"],
        "no_labels_or_removed_references_used": True,
        "sample_score_placeholders_read": False,
        "credentials_or_tokens_read": False,
        "paid_apis_or_private_packages_used": False,
        "fixed_seed": 0,
        "randomness_used": False,
        "calibration_file": str(args.calibration.resolve().relative_to(ROOT)),
        "calibration_sha256": sha256(args.calibration.resolve()),
        "calibrated_constants": calibration["lb_calibrated_constants"],
        "official_rows": len(official_region),
        "template_rows": len(template_ids),
        "eastern_wa_template_only_rows": len(template_only),
        "template_projection": {
            "path": str(args.template_projection.resolve().relative_to(ROOT)),
            "sha256": template_projection_hash,
            "columns_used": ["GEOID", "region"],
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "numpy": version("numpy"),
            "pyarrow": pa.__version__,
            "shapely": version("shapely"),
            "pyproj": version("pyproj"),
        },
        "public_input_hashes": input_hashes,
        "cold_feature_rebuild": True,
        "feature_rebuilds": feature_info,
        "candidate_hashes": output_hashes,
        "score_scope_note": "Official score scope is the 9,379 authoritative sample IDs. The 415 eastern-WA records are present only for platform-format completion and use zero (C0) or pooled official-row P4 values; they are not target estimates.",
    }
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(f"PASS cold candidate reproduction: C0/P4 hashes match; official={len(official_region)}; template={len(template_ids)}")
    print(f"Report state: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
