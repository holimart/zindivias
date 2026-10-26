#!/usr/bin/env python3
"""Read provided-data footers and record exact-recipe input feasibility for Family T v2.

No parquet row payload is opened. This preflight is not an estimator implementation or a claim of
recipe conformance; it validates which required supplied fields exist before exact computation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ["maricopa-az", "northern-ca", "eastern-ok", "south-central-tx"]
KNOWN_V1_GAPS = {
    "T1": "v1 has no <30-degree stroke-link rule",
    "T2": "v1 emits link-segment in-tract length rather than attached carriageway length within 2 km",
    "T3": "v1 does not enumerate geometric crossings without shared connectors",
    "T4": "v1 uses connector degree-product rather than sampled edge betweenness",
}


def recipe_input_assessment(roads: set[str], infrastructure: set[str], tracts: set[str],
                            strata: set[str]) -> dict[str, Any]:
    """Return explicit required/available fields without interpreting them as estimator results."""
    required = {
        "T1": {"roads-unfiltered": {"class", "names", "routes", "connectors", "geometry"}},
        "T2": {"roads-unfiltered": {"class", "subclass", "connectors", "geometry"}},
        "T3": {"roads-unfiltered": {"class", "connectors", "road_flags", "rail_flags",
                                     "level_rules", "geometry"},
               "infrastructure": {"geometry", "subtype", "class"}},
        "T4": {"roads-unfiltered": {"class", "connectors", "geometry"},
               "tracts": {"GEOID", "geometry"}, "strata": {"GEOID", "pop_total"}},
    }
    layers = {"roads-unfiltered": roads, "infrastructure": infrastructure,
              "tracts": tracts, "strata": strata}
    result: dict[str, Any] = {}
    for idea, layer_requirements in required.items():
        missing = {layer: sorted(columns - layers[layer])
                   for layer, columns in layer_requirements.items()
                   if columns - layers[layer]}
        result[idea] = {
            "required_columns": {layer: sorted(columns)
                                 for layer, columns in layer_requirements.items()},
            "missing_columns": missing,
            "required_fields_present": not missing,
        }
    # No supplied point/population-weighted centroid geometry can be claimed from these tables.
    result["T4"]["centroid_geometry_note"] = (
        "The supplied ACS tract table exposes polygon geometry and the provided strata table "
        "exposes pop_total; no population-centroid coordinate/point column is present. A tract "
        "polygon centroid is derivable, but is not a population centroid without an additional "
        "population surface, which this preflight does not access. Exact T4 therefore needs an "
        "explicitly frozen interpretation of 'population centroid' using only supplied fields, or "
        "must remain non-exact."
    )
    return result


def schema_for(fs: Any, path: str) -> set[str]:
    import pyarrow.parquet as pq
    return set(pq.ParquetFile(fs.open_input_file(path)).schema_arrow.names)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/bias60-family-t-exact-reconciliation.json")
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        raise SystemExit("COMPETITION_WORKSPACE is required")
    import pyarrow.fs as pafs
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    import pyarrow.parquet as pq
    base = ROOT / "config/bias60-harness-preregistered.json"
    config = json.loads(base.read_text(encoding="utf-8"))
    package = config["inputs"]["package_bucket"]
    per_region: dict[str, Any] = {}
    for region in REGIONS:
        road_path = f"{package}/reference/{region}/{region}-overture-roads-unfiltered.parquet"
        infra_path = f"{package}/reference/{region}/{region}-overture-infrastructure.parquet"
        tract_path = f"{package}/reference/{region}/{region}-census-acs-housing.parquet"
        strata_path = workspace / config["inputs"]["strata_cache_template"].format(region=region)
        road_fields = schema_for(fs, road_path)
        infra_fields = schema_for(fs, infra_path)
        tract_fields = schema_for(fs, tract_path)
        strata_fields = set(pq.ParquetFile(strata_path).schema_arrow.names)
        per_region[region] = {
            "input_assessment": recipe_input_assessment(road_fields, infra_fields,
                                                         tract_fields, strata_fields),
            "row_payload_read": False,
        }
    harness = json.loads((ROOT / "reports/ideas-harness.json").read_text(encoding="utf-8"))
    v1_artifacts = {}
    for idea in ("T1", "T2", "T3", "T4"):
        entry = harness["per_idea"][idea]
        v1_artifacts[idea] = {
            "path": entry["path"], "sha256": entry["sha256"],
            "official_rows": entry["official_rows"],
            "geoid_partition_exact": entry["geoid_partition_exact"],
            "emissions": [{"component": emission["component"],
                           "channel": emission["channel"], "rows": emission["rows"],
                           "defined_rows": emission["defined_rows"]}
                          for emission in entry["emissions"]],
            "exact_recipe_conformance": False,
        }
    out = {
        "family": "T",
        "version": "exact-recipe-reconciliation-progress-v1",
        "scope": "provided-data schema preflight; no v2 estimator execution yet",
        "official_scored_rows": config["inputs"]["official_scored_rows"],
        "regions": per_region,
        "v1_artifacts_preserved": v1_artifacts,
        "measure_before": {
            "exact_recipes": 0,
            "v1_preserved": True,
            "known_gaps": KNOWN_V1_GAPS,
        },
        "measure_after": {"exact_recipes": 0, "v2_artifacts_emitted": [],
                           "coverage_and_orthogonality_refreshed": False},
        "prohibitions": {"row_payload_read": False, "labels_or_reference_layers_used": False,
                         "leaderboard_or_upload_used": False},
    }
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    out_path.write_text(payload, encoding="utf-8")
    print(json.dumps({"out": str(out_path.relative_to(ROOT)),
                      "sha256": hashlib.sha256(payload.encode()).hexdigest(),
                      "regions": len(per_region), "exact_recipes": 0,
                      "all_required_fields_present": all(
                          assessment["required_fields_present"]
                          for region in per_region.values()
                          for assessment in region["input_assessment"].values())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
