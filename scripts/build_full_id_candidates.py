#!/usr/bin/env python3
"""Extend frozen C0-C3 candidates to the steward-required 9,794-row template.

Only GEOID and region metadata are read from the legacy Data-page sample; its
score placeholders are never read. The 9,379 four-region candidate values are
preserved verbatim. Eastern-WA rows have no permitted feature inputs and are
filled only with documented, pooled four-region predictions (or zero for C0).
This is template-completion plumbing, not a claim that eastern-WA is scored.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
TEMPLATE_REGIONS = (*REGIONS, "eastern-wa")
CANDIDATES = ("C0", "C1", "C2", "C3")
COLUMNS = ["GEOID", "transport_gap", "building_gap", "poi_gap", "coverage_gap_score"]
SCORE_COLUMNS = COLUMNS[1:]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        return list(reader), list(reader.fieldnames or [])


def csv_bytes(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def mean_value(rows: list[dict[str, str]], column: str) -> float:
    values = [float(row[column]) for row in rows]
    if not values or any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
        raise ValueError(f"invalid or empty values for {column}")
    return sum(values) / len(values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--template", type=Path,
                        help="defaults to $workspace/SampleSubmission.csv")
    parser.add_argument("--manifest", type=Path, default=ROOT / "reports/candidates.json")
    parser.add_argument("--out-audit", type=Path,
                        default=ROOT / "reports/full-id-candidate-audit.json")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    template_path = (args.template or workspace / "SampleSubmission.csv").resolve()
    manifest_path = args.manifest.resolve()
    audit_path = args.out_audit.resolve()

    # Official ID order is still derived exclusively from the four authoritative
    # regional samples; the legacy template supplies platform order and labels.
    official_ids: list[str] = []
    official_region: dict[str, str] = {}
    official_sample_hashes = {}
    for region in REGIONS:
        sample_path = workspace / "samples" / f"{region}-sample-submission.csv"
        sample_rows, header = read_csv(sample_path)
        if header != COLUMNS:
            raise ValueError(f"unexpected official sample header in {sample_path}: {header}")
        ids = [row["GEOID"] for row in sample_rows]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError(f"empty or duplicate official IDs in {sample_path}")
        for geoid in ids:
            if geoid in official_region:
                raise ValueError(f"duplicate official GEOID across regions: {geoid}")
            official_region[geoid] = region
        official_ids.extend(ids)
        official_sample_hashes[region] = {"rows": len(ids), "sha256": sha256_file(sample_path)}
    if len(official_ids) != 9379:
        raise ValueError(f"expected 9,379 official regional IDs, found {len(official_ids)}")

    template_rows, template_columns = read_csv(template_path)
    if not {"GEOID", "region"}.issubset(template_columns):
        raise ValueError(f"template must contain GEOID and region metadata: {template_columns}")
    template_ids = [row["GEOID"] for row in template_rows]
    if (len(template_rows) != 9794 or any(not geoid for geoid in template_ids)
            or len(set(template_ids)) != len(template_ids)):
        raise ValueError(f"expected 9,794 unique nonempty template GEOIDs, got {len(template_rows)}")
    template_region = {row["GEOID"]: row["region"] for row in template_rows}
    counts = {region: sum(template_region[geoid] == region for geoid in template_ids)
              for region in TEMPLATE_REGIONS}
    if sum(counts.values()) != len(template_ids) or set(template_region.values()) != set(TEMPLATE_REGIONS):
        raise ValueError(f"unexpected template regions/counts: {counts}")
    if any(template_region.get(geoid) != region for geoid, region in official_region.items()):
        raise ValueError("legacy template region metadata conflicts with authoritative regional IDs")
    extra_ids = [geoid for geoid in template_ids if geoid not in official_region]
    if (len(extra_ids) != 415 or any(template_region[geoid] != "eastern-wa" for geoid in extra_ids)
            or set(template_ids) != set(official_ids) | set(extra_ids)):
        raise ValueError("template must contain exactly the 9,379 official IDs plus 415 eastern-WA IDs")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = {row["candidate_id"]: row for row in manifest.get("candidate_set", [])}
    if set(CANDIDATES) - set(entries):
        raise ValueError(f"candidate manifest missing {sorted(set(CANDIDATES) - set(entries))}")

    source_rows: dict[str, list[dict[str, str]]] = {}
    projection_hashes = {}
    for candidate_id in CANDIDATES:
        item = entries[candidate_id]
        if not item.get("pre_registered"):
            raise ValueError(f"candidate is not pre-registered: {candidate_id}")
        path = ROOT / item["artifact"]
        current_hash = sha256_file(path)
        if current_hash != item.get("sha256"):
            raise ValueError(f"manifest hash mismatch before rebuild: {candidate_id}")
        rows, header = read_csv(path)
        if header != COLUMNS:
            raise ValueError(f"unexpected source candidate columns for {candidate_id}: {header}")
        row_map = {row.get("GEOID", ""): row for row in rows}
        if len(row_map) != len(rows) or not set(official_ids).issubset(row_map):
            raise ValueError(f"source candidate has duplicate/missing official IDs: {candidate_id}")
        source = [row_map[geoid] for geoid in official_ids]
        for row in source:
            for column in SCORE_COLUMNS:
                value = float(row[column])
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError(f"invalid {candidate_id}/{column} for {row['GEOID']}")
        if candidate_id == "C0" and any(float(row[column]) != 0.0 for row in source for column in SCORE_COLUMNS):
            raise ValueError("C0 official rows must remain all zero")
        source_rows[candidate_id] = source
        projection_hashes[candidate_id] = sha256_bytes(csv_bytes(source))

    pooled_c2_composite = mean_value(source_rows["C2"], "coverage_gap_score")
    rebuilt: dict[str, list[dict[str, str]]] = {}
    wa_fill_values = {}
    for candidate_id in CANDIDATES:
        source_by_id = {row["GEOID"]: row for row in source_rows[candidate_id]}
        if candidate_id == "C0":
            fill = {column: 0.0 for column in SCORE_COLUMNS}
            fill_policy = "all-zero baseline; no eastern-WA feature or target inference"
        elif candidate_id == "C1":
            fill = {column: pooled_c2_composite for column in SCORE_COLUMNS}
            fill_policy = "all five score fields filled with the pooled mean C2 composite over the 9,379 official rows; no eastern-WA features"
        else:
            fill = {column: mean_value(source_rows[candidate_id], column)
                    for column in SCORE_COLUMNS}
            fill_policy = "each score field filled with its unweighted four-region mean over the 9,379 official rows; no eastern-WA features"
        wa_fill_values[candidate_id] = {
            "policy": fill_policy,
            "values": {column: fill[column] for column in SCORE_COLUMNS},
            "rows": len(extra_ids),
            "region": "eastern-wa",
            "scored_target_claim": False,
        }
        source_by_id = {**source_by_id}
        output_rows = []
        for geoid in template_ids:
            if geoid in source_by_id:
                # Preserve the four-region candidate values as parsed strings.
                output_rows.append({column: source_by_id[geoid][column] for column in COLUMNS})
            else:
                output_rows.append({"GEOID": geoid,
                                    **{column: format(fill[column], ".17g")
                                       for column in SCORE_COLUMNS}})
        rebuilt[candidate_id] = output_rows

    audit_candidates = {}
    for candidate_id in CANDIDATES:
        item = entries[candidate_id]
        path = ROOT / item["artifact"]
        rows = rebuilt[candidate_id]
        if [row["GEOID"] for row in rows] != template_ids:
            raise AssertionError(f"output order mismatch for {candidate_id}")
        for row in rows:
            for column in SCORE_COLUMNS:
                value = float(row[column])
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError(f"invalid rebuilt {candidate_id}/{column} for {row['GEOID']}")
        path.write_bytes(csv_bytes(rows))
        digest = sha256_file(path)
        per_region_means = {}
        for region in TEMPLATE_REGIONS:
            group = [row for row in rows if template_region[row["GEOID"]] == region]
            per_region_means[region] = {
                column: round(sum(float(row[column]) for row in group) / len(group), 9)
                for column in SCORE_COLUMNS
            }
        item["sha256"] = digest
        item["rows"] = len(rows)
        item["columns"] = COLUMNS
        item["per_region_means"] = {
            region: per_region_means[region]["coverage_gap_score"]
            for region in TEMPLATE_REGIONS
        }
        if isinstance(item.get("defined_rates"), dict):
            item["defined_rates"]["eastern-wa"] = None
        item["eastern_wa_fill"] = wa_fill_values[candidate_id]
        description_suffix = (
            "Eastern-WA template rows are unscored extras filled per eastern_wa_fill; "
            "no accuracy claim is made for those rows."
        )
        description = item.get("description", "")
        if description_suffix not in description:
            item["description"] = f"{description} {description_suffix}".strip()
        item["pre_registered"] = True
        audit_candidates[candidate_id] = {
            "artifact": item["artifact"],
            "sha256": digest,
            "rows": len(rows),
            "unique_ids": len({row["GEOID"] for row in rows}),
            "exact_template_order_once": [row["GEOID"] for row in rows] == template_ids,
            "validity_rate": 1.0,
            "per_region_rows": counts,
            "per_region_means": per_region_means,
            "official_source_projection_sha256": projection_hashes[candidate_id],
            "eastern_wa_fill": wa_fill_values[candidate_id],
        }

    manifest["platform_template"] = {
        "source": "$COMPETITION_WORKSPACE/SampleSubmission.csv",
        "sha256": sha256_file(template_path),
        "rows": len(template_rows),
        "columns_read": ["GEOID", "region"],
        "sample_score_placeholders_read": False,
        "region_counts": counts,
        "official_scored_region_count": 9379,
        "extra_template_rows": 415,
        "extra_template_region": "eastern-wa",
        "interpretation": "Use the steward-confirmed 9,794-row Data-page template for platform processing. The authoritative four regional samples still define 9,379 scored IDs; the additional eastern-WA rows are template-only and filled with documented proxies, not target evidence.",
    }
    manifest["candidate_set"] = [entries[item["candidate_id"]] for item in manifest["candidate_set"]]
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    audit = {
        "method": "full platform-template ID completion; exact authoritative 9,379 candidate rows preserved; 415 eastern-WA rows use explicit pooled fills",
        "input_template": {
            "path": "$COMPETITION_WORKSPACE/SampleSubmission.csv",
            "sha256": sha256_file(template_path),
            "rows": len(template_rows),
            "columns_read": ["GEOID", "region"],
            "score_placeholders_read": False,
            "region_counts": counts,
        },
        "official_samples": official_sample_hashes,
        "official_ids": len(official_ids),
        "extra_ids": {"count": len(extra_ids), "region": "eastern-wa",
                       "scored_target_claim": False},
        "manifest": {"path": str(manifest_path.relative_to(ROOT)),
                     "sha256": sha256_file(manifest_path)},
        "candidate_results": audit_candidates,
        "no_label_caveat": "No labels, removed references, external data, or sample score placeholders were used. Eastern-WA rows are outside the official four-region scored list; pooled fills only satisfy the steward-directed platform template and are not target estimates or accuracy claims.",
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("PASS rebuilt C0-C3: exact 9,794 template GEOIDs once; 9,379 official rows preserved; "
          "415 eastern-WA rows documented; all score outputs finite and bounded")
    for candidate_id in CANDIDATES:
        print(f"{candidate_id} SHA-256 {audit_candidates[candidate_id]['sha256']}")
    print(f"Audit: {audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
