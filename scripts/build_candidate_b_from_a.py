#!/usr/bin/env python3
"""Build frozen final-two companion B from the selected Track A candidate.

The official-tract composite alone is shrunk 50% toward its own provided-feature
mean within each official region. Component columns are copied unchanged because
B's frozen rule applies to the composite prediction, not component estimates.
Template-only rows receive the established deterministic zero fill.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
SCORES = ("transport_gap", "building_gap", "poi_gap", "coverage_gap_score")
EXPECTED_HEADER = ("GEOID", *SCORES)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if r.fieldnames is None:
            raise ValueError(f"missing header: {path}")
        rows = list(r)
        return list(r.fieldnames), rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--workspace", type=Path, required=True)
    args = ap.parse_args()
    repo, ws = args.repo.resolve(), args.workspace.resolve()
    source = repo / "reports/candidates/V2_model.csv"
    template = ws / "SampleSubmission.csv"
    output = repo / "reports/candidates/B_from_A.csv"
    report_path = repo / "reports/b-from-a-build.json"

    header, source_rows = read_csv(source)
    if tuple(header) != EXPECTED_HEADER:
        raise ValueError(f"unexpected source columns: {header}")
    template_header, template_rows = read_csv(template)
    if not {"GEOID", "region"}.issubset(template_header):
        raise ValueError("template must provide GEOID and region")
    if len(source_rows) != len(template_rows) or len({r["GEOID"] for r in source_rows}) != len(source_rows):
        raise ValueError("source row count or GEOID uniqueness mismatch")
    if [r["GEOID"] for r in source_rows] != [r["GEOID"] for r in template_rows]:
        raise ValueError("source GEOID order differs from platform template")

    official_region: dict[str, str] = {}
    for region in REGIONS:
        sample = ws / "samples" / f"{region}-sample-submission.csv"
        sample_header, sample_rows = read_csv(sample)
        if "GEOID" not in sample_header:
            raise ValueError(f"official sample lacks GEOID: {sample}")
        for row in sample_rows:
            geoid = row["GEOID"]
            if geoid in official_region:
                raise ValueError(f"duplicate official GEOID: {geoid}")
            official_region[geoid] = region
    if len(official_region) != 9379:
        raise ValueError(f"expected 9,379 official GEOIDs, found {len(official_region)}")

    groups: dict[str, list[float]] = {region: [] for region in REGIONS}
    for row, meta in zip(source_rows, template_rows):
        region = official_region.get(row["GEOID"])
        if region:
            if meta["region"] != region:
                raise ValueError(f"region mismatch for {row['GEOID']}: {meta['region']} != {region}")
            value = float(row["coverage_gap_score"])
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"invalid source composite for {row['GEOID']}")
            groups[region].append(value)
        elif meta["region"] != "eastern-wa":
            raise ValueError(f"nonofficial row outside deterministic template-only region: {row['GEOID']}")
    if sum(map(len, groups.values())) != 9379:
        raise ValueError("official sample IDs do not cover source")
    means = {region: math.fsum(values) / len(values) for region, values in groups.items()}

    out_rows: list[dict[str, str]] = []
    clipped = 0
    for row, meta in zip(source_rows, template_rows):
        out = dict(row)
        region = official_region.get(row["GEOID"])
        if region:
            raw = 0.5 * float(row["coverage_gap_score"]) + 0.5 * means[region]
            val = min(1.0, max(0.0, raw))
            clipped += int(val != raw)
            out["coverage_gap_score"] = format(val, ".17g")
        else:
            for col in SCORES:
                out[col] = "0"
        out_rows.append(out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
        w.writeheader()
        w.writerows(out_rows)

    report = {
        "candidate_id": "B_from_A",
        "source_candidate_id": "V2_model",
        "source_artifact": "reports/candidates/V2_model.csv",
        "source_sha256": sha256(source),
        "output_artifact": "reports/candidates/B_from_A.csv",
        "output_sha256": sha256(output),
        "formula": "B_i = 0.5 * V2_model.coverage_gap_score_i + 0.5 * mean(V2_model.coverage_gap_score over official GEOIDs in the same region)",
        "region_means_from_provided_feature_predictions": means,
        "official_rows": len(official_region),
        "template_rows": len(template_rows),
        "template_only_rows": len(template_rows) - len(official_region),
        "template_only_policy": "zero fill in all score columns for eastern-wa format-only rows; no score claim",
        "component_columns": "copied unchanged from A; fixed protocol shrinks composite only",
        "clipped_values": clipped,
        "labels_or_leaderboard_inputs_used": False,
        "deterministic": True,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output), "sha256": report["output_sha256"], "official_rows": 9379,
                      "template_rows": len(template_rows), "clipped_values": clipped,
                      "region_means": means}, sort_keys=True))


if __name__ == "__main__":
    main()
