#!/usr/bin/env python3
"""Audit region composition of the official tribal and rural scorecard strata."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("eastern-ok", "maricopa-az", "northern-ca", "south-central-tx")
REGION_LABELS = {
    "eastern-ok": "Eastern Oklahoma",
    "maricopa-az": "Maricopa, Arizona",
    "northern-ca": "Northern California",
    "south-central-tx": "South-Central Texas",
}
STRATA_COLUMNS = ("GEOID", "tribal_any", "pct_urban")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_sample_ids(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "GEOID" not in reader.fieldnames:
            raise ValueError(f"sample file lacks GEOID: {path}")
        ids = [row["GEOID"].strip() for row in reader]
    if not ids or any(not geoid for geoid in ids) or len(set(ids)) != len(ids):
        raise ValueError(f"sample IDs are empty or duplicated: {path}")
    return ids


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _share(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 8) if denominator else None


def summarize_composition(region_rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Return region prevalence and within-stratum region shares from sampled rows."""
    regional: dict[str, Any] = {}
    by_stratum = {
        name: {region: 0 for region in REGIONS}
        for name in ("all_sample", "tribal_any", "non_tribal", "rural", "urban")
    }
    missing_tribal: dict[str, int] = {}
    missing_urbanity: dict[str, int] = {}

    for region in REGIONS:
        rows = region_rows[region]
        tribal_values = [row["tribal_any"] for row in rows if row["tribal_any"] is not None]
        rural_values = [row["rural"] for row in rows if row["rural"] is not None]
        tribal_n = sum(tribal_values)
        nontribal_n = len(tribal_values) - tribal_n
        rural_n = sum(rural_values)
        urban_n = len(rural_values) - rural_n
        regional[region] = {
            "label": REGION_LABELS[region],
            "official_sample_tracts": len(rows),
            "tribal_any": {"tracts": tribal_n, "share_of_region": _share(tribal_n, len(tribal_values)),
                           "valid_tracts": len(tribal_values)},
            "non_tribal": {"tracts": nontribal_n,
                            "share_of_region": _share(nontribal_n, len(tribal_values)),
                            "valid_tracts": len(tribal_values)},
            "rural_pct_urban_lt_0_5": {"tracts": rural_n,
                                       "share_of_region": _share(rural_n, len(rural_values)),
                                       "valid_tracts": len(rural_values)},
            "urban_pct_urban_ge_0_5": {"tracts": urban_n,
                                       "share_of_region": _share(urban_n, len(rural_values)),
                                       "valid_tracts": len(rural_values)},
        }
        missing_tribal[region] = len(rows) - len(tribal_values)
        missing_urbanity[region] = len(rows) - len(rural_values)
        by_stratum["all_sample"][region] = len(rows)
        by_stratum["tribal_any"][region] = tribal_n
        by_stratum["non_tribal"][region] = nontribal_n
        by_stratum["rural"][region] = rural_n
        by_stratum["urban"][region] = urban_n

    totals: dict[str, int] = {}
    compositions: dict[str, Any] = {}
    labels = {
        "all_sample": "All official sample tracts",
        "tribal_any": "tribal_any = true",
        "non_tribal": "tribal_any = false",
        "rural": "pct_urban < 0.5",
        "urban": "pct_urban >= 0.5",
    }
    for stratum in by_stratum:
        total = sum(by_stratum[stratum].values())
        totals[stratum] = total
        region_counts = by_stratum[stratum]
        dominant = max(REGIONS, key=lambda region: region_counts[region]) if total else None
        compositions[stratum] = {
            "label": labels[stratum],
            "total_tracts": total,
            "by_region": {
                region: {
                    "label": REGION_LABELS[region],
                    "tracts": region_counts[region],
                    "share_of_stratum": _share(region_counts[region], total),
                }
                for region in REGIONS
            },
            "dominant_region": dominant,
            "dominant_region_share": _share(region_counts[dominant], total) if dominant else None,
        }

    return {
        "regional_counts_and_prevalence": regional,
        "stratum_composition": compositions,
        "missing": {"tribal_any_by_region": missing_tribal,
                    "pct_urban_by_region": missing_urbanity},
        "total_tracts": sum(len(rows) for rows in region_rows.values()),
        "valid_tribal_tracts": totals["tribal_any"] + totals["non_tribal"],
        "valid_urbanity_tracts": totals["rural"] + totals["urban"],
    }


def run(workspace: Path, out: Path) -> dict[str, Any]:
    data_root = workspace / "defined-flags-inputs"
    region_rows: dict[str, list[dict[str, Any]]] = {}
    input_manifest: dict[str, Any] = {}
    all_sample_ids: list[str] = []
    sample_total = 0

    for region in REGIONS:
        sample_path = data_root / f"{region}-sample-submission.csv"
        strata_path = data_root / f"{region}-strata-tract-table.parquet"
        if not sample_path.is_file() or not strata_path.is_file():
            raise FileNotFoundError(f"required official sample/strata missing for {region}")
        sample_ids = read_sample_ids(sample_path)
        sample_total += len(sample_ids)
        all_sample_ids.extend(sample_ids)
        table = pq.read_table(strata_path, columns=list(STRATA_COLUMNS))
        strata_rows = table.to_pylist()
        strata_by_id = {row["GEOID"]: row for row in strata_rows}
        if len(strata_by_id) != len(strata_rows):
            raise ValueError(f"duplicate GEOIDs in strata table: {strata_path}")
        missing = sorted(set(sample_ids) - strata_by_id.keys())
        if missing:
            raise ValueError(f"{region} official sample GEOIDs missing from strata: {missing[:5]}")

        rows = []
        for geoid in sample_ids:
            source = strata_by_id[geoid]
            tribal = source["tribal_any"]
            pct_urban = _finite_float(source["pct_urban"])
            rows.append({
                "GEOID": geoid,
                "tribal_any": bool(tribal) if tribal is not None else None,
                "rural": (pct_urban < 0.5) if pct_urban is not None else None,
            })
        region_rows[region] = rows
        input_manifest[region] = {
            "sample": {"path": str(sample_path), "rows": len(sample_ids),
                       "sha256": sha256_file(sample_path),
                       "read_columns": ["GEOID"]},
            "strata": {"path": str(strata_path), "table_rows": len(strata_rows),
                       "official_sample_rows_joined": len(rows),
                       "extra_strata_rows_excluded": len(strata_rows) - len(set(sample_ids)),
                       "sha256": sha256_file(strata_path),
                       "read_columns": list(STRATA_COLUMNS)},
        }

    if sample_total != 9379:
        raise ValueError(f"expected 9,379 official sample IDs; observed {sample_total}")
    if len(set(all_sample_ids)) != sample_total:
        raise ValueError("duplicate GEOIDs across official regional samples")
    composition = summarize_composition(region_rows)
    route_audit_path = ROOT / "reports/transport-route-ref-audit.json"
    measure_before_rural = {
        "eastern-ok": 412, "maricopa-az": 174,
        "northern-ca": 147, "south-central-tx": 1005,
    }
    strict_rural = {
        region: composition["regional_counts_and_prevalence"][region]["rural_pct_urban_lt_0_5"]["tracts"]
        for region in REGIONS
    }
    rural_missing = composition["missing"]["pct_urban_by_region"]
    report = {
        "schema_version": 1,
        "purpose": "Quantify region composition of the official tribal and rural strata; no target-score analysis.",
        "definitions": {
            "tribal_any": "Provided boolean tribal_any column; no legal-status inference.",
            "rural": "Provided pct_urban < 0.5; pct_urban >= 0.5 is urban.",
            "composition_share": "Within each named stratum, fraction of valid official sample tracts in each region.",
            "sample_scope": "Official four-region sample GEOIDs only; sample score-placeholder values are not used.",
        },
        "input_manifest": input_manifest,
        "unique_official_geoid_count": len(set(all_sample_ids)),
        **composition,
        "rural_measure_before_reconciliation": {
            "counts_listed_in_backlog": measure_before_rural,
            "strict_pct_urban_lt_0_5_counts": strict_rural,
            "pct_urban_missing_counts": rural_missing,
            "counts_listed_equal_strict_plus_missing": {
                region: measure_before_rural[region] == strict_rural[region] + rural_missing[region]
                for region in REGIONS
            },
            "interpretation": "Missing pct_urban is not less than 0.5 and was excluded from strict rural composition; the listed prior counts equal strict-rural counts plus every missing row, so missing values must not silently be treated as rural.",
        },
        "transport_road_taxonomy_context": {
            "organizer_disclosed_overture_to_tiger_named_road_length_ratio_range": [0.71, 1.59],
            "source": "Official competition rules describe the cross-region range but do not map values to regions.",
            "route_ref_dependency": "bias-est-transport-route-ref",
            "route_ref_audit_path": str(route_audit_path),
            "route_ref_audit_present": route_audit_path.is_file(),
            "per_region_l_named_over_l_route": {region: None for region in REGIONS},
            "status": "Per-region ratio measurement is pending the route-reference prerequisite; this report does not infer region-specific values from the disclosed range.",
        },
        "interpretation": {
            "composition_finding": "The tribal_any stratum is heavily concentrated in Eastern Oklahoma, while non-tribal tracts are concentrated in South-Central Texas; unstratified tribal contrasts therefore also compare different regional mixtures.",
            "road_taxonomy_caveat": "The official 0.71-1.59 range establishes that named-road length ratios vary across regions, but without the route-reference audit this analysis cannot quantify the per-region ratios or attribute scorecard disparity to the taxonomy.",
            "proposed_metric_fix": "Report stratum metrics within each region and show region-by-stratum counts; where a pooled summary is needed, standardize both groups to the same predeclared region weights. Report transport ratios within region under one fixed route taxonomy, not as a cross-region coverage comparison.",
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    workspace_env = os.environ.get("COMPETITION_WORKSPACE")
    parser.add_argument("--workspace", type=Path,
                        default=Path(workspace_env) if workspace_env else None)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/discovery-strata-confound.json")
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    result = run(args.workspace.resolve(), args.out.resolve())
    tribal = result["stratum_composition"]["tribal_any"]
    rural = result["stratum_composition"]["rural"]
    print(f"PASS strata confound composition: {result['total_tracts']} official IDs; "
          f"tribal dominant={tribal['dominant_region']} "
          f"({tribal['dominant_region_share']:.1%}); "
          f"rural dominant={rural['dominant_region']} "
          f"({rural['dominant_region_share']:.1%}); report {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
