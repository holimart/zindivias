#!/usr/bin/env python3
"""Discovery-only Camp Fire DINS comparison; never reads or writes scored candidates."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely
from shapely import Point, STRtree

ROOT = Path(__file__).resolve().parents[1]
REGION = "northern-ca"
INCIDENT_NUM = "CABTU 016737"
INCIDENT_NAME = "Camp"
CAMP_FOCAL_GEOIDS = (
    "06007001800", "06007002100", "06007002000",
    "06007001900", "06007002300", "06007002200",
)
LAYER_URL = (
    "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/"
    "POSTFIRE_MASTER_DATA_SHARE/FeatureServer/0"
)
DATASET_URL = "https://data.ca.gov/dataset/cal-fire-damage-inspection-dins-data"
CKAN_URL = "https://data.ca.gov/api/3/action/package_show?id=cal-fire-damage-inspection-dins-data"
GEOMETRY_KEY = (
    "us-west-2.opendata.source.coop/humane-intelligence/"
    "bias-bounty-mapping-equity-challenge/reference/northern-ca/"
    "northern-ca-census-acs-housing.parquet"
)
READ_FIELDS = "OBJECTID,DAMAGE,COUNTY,INCIDENTNAME,INCIDENTNUM"
CANDIDATE_ARTIFACTS = (
    "reports/candidates/C0_all_zero.csv", "reports/candidates/C1_region_constants.csv",
    "reports/candidates/C2_component_estimator_v0.csv", "reports/candidates/C3_denominator_x125.csv",
    "reports/candidates/P4_c1_shrink_alpha.csv", "reports/candidates/P5_region_shrink_residual.csv",
    "reports/candidates/P6_global_constant.csv", "reports/candidates/R-AZ_region_indicator.csv",
    "reports/candidates/R-CA_region_indicator.csv", "reports/candidates/R-OK_region_indicator.csv",
    "reports/candidates/R-TX_region_indicator.csv", "reports/candidates/V2_model.csv",
    "reports/candidates/A1_physical_equal.csv", "reports/candidates/A2_spectral.csv",
    "reports/candidates/A3_dawid_skene.csv",
    "reports/candidates/S_crossmethod_reliability_shrink_v1.csv",
    "reports/candidates/S2_h1b_assumption_bound_v1.csv",
    "reports/candidates/S2b_c2_expected_facility_mask_v1.csv",
    "reports/candidates/B_from_A.csv", "reports/candidates/V2_model_cgs.csv",
    "reports/candidates/A1_physical_equal_cgs.csv", "reports/candidates/A2_spectral_cgs.csv",
    "reports/candidates/A3_dawid_skene_cgs.csv", "reports/candidates/B_from_A_cgs.csv",
)
DAMAGE_DESTROYED = "Destroyed (>50%)"
DAMAGE_MAJOR = "Major (25-50%)"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch_json(url: str, timeout: int = 90) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "bias-bounty-discovery/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def download_camp_dins(folder: Path) -> dict[str, Any]:
    """Save an incident-filtered GeoJSON with only non-address fields and point geometry."""
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    raw_path = folder / "camp-fire-dins.geojson"
    query = {
        "where": f"INCIDENTNUM = '{INCIDENT_NUM}'",
        "outFields": READ_FIELDS,
        "returnGeometry": "true",
        "outSR": "4326",
        "orderByFields": "OBJECTID",
        "resultRecordCount": "2000",
        "f": "geojson",
    }
    query_url = LAYER_URL + "/query"
    count_url = query_url + "?" + urllib.parse.urlencode({
        "where": query["where"], "returnCountOnly": "true", "returnGeometry": "false", "f": "json"})
    count = int(fetch_json(count_url)["count"])
    features: list[dict[str, Any]] = []
    page_size = 2000
    for offset in range(0, count, page_size):
        params = {**query, "resultOffset": str(offset)}
        page = fetch_json(query_url + "?" + urllib.parse.urlencode(params))
        if page.get("type") != "FeatureCollection" or not isinstance(page.get("features"), list):
            raise ValueError(f"ArcGIS query returned no GeoJSON features: {page.get('error')}")
        features.extend(page["features"])
        if not page["features"]:
            raise ValueError(f"empty DINS page at offset {offset} of {count}")
    if len(features) != count:
        raise ValueError(f"DINS count/query mismatch: count={count}, downloaded={len(features)}")
    ids = [feature.get("id") for feature in features]
    if len(ids) != len(set(ids)) or any(not isinstance(value, int) for value in ids):
        raise ValueError("DINS OBJECTIDs missing or duplicated")
    for feature in features:
        props = feature.get("properties", {})
        if props.get("INCIDENTNUM") != INCIDENT_NUM or props.get("INCIDENTNAME") != INCIDENT_NAME:
            raise ValueError("incident filter returned an unexpected DINS record")
        if set(props) != set(READ_FIELDS.split(",")):
            raise ValueError(f"unexpected DINS properties/possible PII: {sorted(props)}")
        if feature.get("geometry", {}).get("type") != "Point":
            raise ValueError("DINS feature is missing point geometry")
    raw = {"type": "FeatureCollection", "features": features}
    raw_path.write_text(json.dumps(raw, separators=(",", ":"), ensure_ascii=False) + "\n",
                        encoding="utf-8")
    downloaded_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    package = fetch_json(CKAN_URL)["result"]
    layer = fetch_json(LAYER_URL + "?f=pjson")
    license_text = (f"Dataset catalogue metadata: {package.get('license_title')} "
                    f"(license id `{package.get('license_id')}`); version not specified. "
                    f"Catalogue license URL: {package.get('license_url')}. No separate licenseInfo "
                    f"is supplied by the ArcGIS layer.")
    manifest = {
        "dataset": "CAL FIRE Damage Inspection (DINS) Data; incident-filtered Camp Fire point records",
        "publisher": "California Department of Forestry and Fire Protection (CAL FIRE); California State Geoportal catalogue",
        "source_dataset_url": DATASET_URL,
        "metadata_api_url": CKAN_URL,
        "service_layer_url": LAYER_URL,
        "query_url_template": query_url + "?" + urllib.parse.urlencode(query),
        "source_query": query,
        "filter": {"INCIDENTNUM": INCIDENT_NUM, "INCIDENTNAME": INCIDENT_NAME},
        "downloaded_utc": downloaded_utc,
        "license": {
            "catalogue_license_id": package.get("license_id"),
            "catalogue_license_title": package.get("license_title"),
            "catalogue_license_url": package.get("license_url"),
            "is_open": package.get("isopen"),
            "recorded_terms": license_text,
            "version_caveat": "Catalogue says Creative Commons Attribution but does not identify a CC version; retain attribution and do not imply a more specific version.",
        },
        "publisher_data_limitations": (
            "Catalogue description says the database documents structures within the California State Responsibility Area "
            "inside or within 300 feet of a fire perimeter; inspection access and fire damage can prevent identification, "
            "so not all impacted structures may be identified and a small margin of error is expected."),
        "query_metadata": {
            "layer_name": layer.get("name"), "geometry_type": layer.get("geometryType"),
            "max_record_count": layer.get("maxRecordCount"),
            "selected_fields": READ_FIELDS.split(","), "geometry_out_sr": 4326,
            "address_and_parcel_fields_requested": False,
            "reported_count_at_download": count,
        },
        "files": [{"name": raw_path.name, "bytes": raw_path.stat().st_size,
                   "sha256": sha256_file(raw_path), "records": len(features)}],
    }
    manifest_path = folder / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    return manifest


def read_sample_geoids(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if not header or header[0] != "GEOID":
            raise ValueError("first official sample column must be GEOID")
        ids = [row[0] for row in reader]
    if len(ids) != len(set(ids)) or len(ids) != 591:
        raise ValueError("official NorCal sample has wrong or duplicate GEOIDs")
    return ids


def load_inputs(workspace: Path, geoids: list[str]):
    strata_path = workspace / "defined-flags-inputs" / f"{REGION}-strata-tract-table.parquet"
    features_path = workspace / "gap-estimator" / "features" / f"{REGION}-overture-tract-summary.parquet"
    strata_table = pq.read_table(strata_path, columns=[
        "GEOID", "COUNTYFP", "usfs_BuildingCount_sum", "mtbs_wildfire_last_year",
        "mtbs_wildfire_burned_pct_land", "usfs_edition"])
    feature_table = pq.read_table(features_path, columns=["GEOID", "overture_buildings"])
    strata_rows = {str(row["GEOID"]): row for row in strata_table.to_pylist()}
    feature_rows = {str(row["GEOID"]): row for row in feature_table.to_pylist()}
    if set(strata_rows) != set(geoids) or set(feature_rows) != set(geoids):
        raise ValueError("provided strata/features do not exactly match official NorCal GEOIDs")
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    info = fs.get_file_info(GEOMETRY_KEY)
    if info.type != pafs.FileType.File:
        raise FileNotFoundError(f"provided ACS tract geometry missing: s3://{GEOMETRY_KEY}")
    parquet = pq.ParquetFile(fs.open_input_file(GEOMETRY_KEY))
    geometry_table = parquet.read(columns=["GEOID", "geometry"])
    geometries = {str(row["GEOID"]): bytes(row["geometry"])
                  for row in geometry_table.to_pylist()}
    if set(geometries) != set(geoids) or len(geometries) != len(geoids):
        raise ValueError("provided tract geometry IDs do not match the official NorCal sample")
    return strata_rows, feature_rows, geometries, {
        "strata": {"path": str(strata_path), "sha256": sha256_file(strata_path),
                   "read_columns": ["GEOID", "COUNTYFP", "usfs_BuildingCount_sum",
                                    "mtbs_wildfire_last_year", "mtbs_wildfire_burned_pct_land", "usfs_edition"]},
        "overture": {"path": str(features_path), "sha256": sha256_file(features_path),
                     "read_columns": ["GEOID", "overture_buildings"]},
        "tract_geometry": {"uri": f"s3://{GEOMETRY_KEY}", "bytes": info.size,
                           "read_columns": ["GEOID", "geometry"], "rows": len(geometries)},
    }


def average_ranks(values: list[float]) -> np.ndarray:
    values_array = np.asarray(values)
    order = np.argsort(values_array, kind="mergesort")
    ranks = np.empty(len(values_array), dtype=float)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values_array[order[j]] == values_array[order[i]]:
            j += 1
        ranks[order[i:j]] = (i + 1 + j) / 2.0
        i = j
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) != len(y) or len(x) < 2:
        return None
    rx, ry = average_ranks(x), average_ranks(y)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def join_and_aggregate(features: list[dict[str, Any]], geoids: list[str],
                       geometries: dict[str, bytes]) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
    polys = shapely.from_wkb([geometries[g] for g in geoids])
    tree = STRtree(polys)
    counts = {g: Counter() for g in geoids}
    matched = 0
    multiple_matches = 0
    outside = 0
    for feature in features:
        props = feature["properties"]
        x, y = feature["geometry"]["coordinates"][:2]
        point = Point(float(x), float(y))
        candidate_ix = tree.query(point)
        containing = [int(ix) for ix in candidate_ix if polys[int(ix)].covers(point)]
        if not containing:
            outside += 1
            continue
        if len(containing) > 1:
            multiple_matches += 1
        ix = min(containing, key=lambda j: geoids[j])
        counts[geoids[ix]][str(props["DAMAGE"])] += 1
        matched += 1
    fields = sorted({key for value in counts.values() for key in value})
    result = {g: {field: int(counts[g].get(field, 0)) for field in fields} for g in geoids}
    return result, {"source_features": len(features), "matched_to_official_sample_tracts": matched,
                    "outside_official_sample_tracts": outside,
                    "points_intersecting_multiple_tracts_tie_broken_by_lexicographic_geoid": multiple_matches,
                    "tract_join_method": "point covered by provided tract polygon; if a point lies on/within multiple boundaries, assign to lexicographically smallest GEOID"}


def fmt(value: Any, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def make_markdown(report: dict[str, Any]) -> str:
    source = report["external_source"]
    d = report["decision"]
    lines = [
        "# Camp Fire DINS check of the post-fire stale-reference mechanism", "",
        f"**Result: {d['verdict'].upper()} — {d['verdict_summary']}**", "",
        "This is a Best Bias Discovery-only external-data check. It does not read or change any scored candidate. "
        "The data-source catalogue labels the dataset Creative Commons Attribution (CC BY; version not stated); "
        f"downloaded {source['downloaded_utc']} from [{source['dataset_url']}]({source['dataset_url']}) "
        f"via the filtered [CAL FIRE ArcGIS layer]({source['layer_url']}). Retrieval, filter, selected fields, "
        f"licence metadata, limitations and raw-file SHA-256 are recorded in "
        f"`{source['manifest_path']}` ({source['manifest_sha256']}).", "",
        "The Cal OES/CAL FIRE DINS catalogue says the database covers structures inside or within 300 feet of "
        "a fire perimeter in State Responsibility Areas and warns some impacted structures may not be identified "
        "because of fire/access limitations. Therefore no DINS record is not evidence that no structure existed, "
        "and DINS counts are not a complete post-fire census.", "",
        "## Data and method", "",
        f"The incident filter is `INCIDENTNUM = '{INCIDENT_NUM}'` (service incident name `{INCIDENT_NAME}`; "
        "start date in the service is 2018-11-08). Only DINS object ID, damage class, county, incident name/number, "
        "and point geometry were requested; no street/address or parcel fields were downloaded. The spatial join "
        "uses only official NorCal sample GEOIDs and the challenge-provided ACS tract geometries. For each tract, "
        "the provided count difference is `usfs_BuildingCount_sum - overture_buildings`; ratios are "
        "`difference / DINS destroyed count`, and relative error is `abs(difference - destroyed) / destroyed`. "
        "Destroyed is DINS `Destroyed (>50%)`; `Major (25-50%)` is tabulated separately, not included in destroyed.", "",
        f"DINS returned {report['dins_source']['total_camp_fire_records']:,} Camp Fire records; "
        f"{report['spatial_join']['matched_to_official_sample_tracts']:,} fell within the 591 official NorCal "
        f"tract geometries. Across these matched records there were "
        f"{report['dins_source']['matched_damage_counts'].get(DAMAGE_DESTROYED, 0):,} destroyed, "
        f"{report['dins_source']['matched_damage_counts'].get(DAMAGE_MAJOR, 0):,} major-damage, and "
        f"{report['dins_source']['matched_damage_counts'].get('No Damage', 0):,} no-damage records.", "",
        "## Six preregistered Butte tracts", "",
        "| GEOID | USFS − Overture | DINS destroyed | DINS major | Difference / destroyed | Relative error |", "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["focal_butte_tracts"]:
        lines.append(f"| `{row['GEOID']}` | {row['usfs_minus_overture']:.1f} | {row['dins_destroyed']} | "
                     f"{row['dins_major']} | {fmt(row['difference_over_destroyed'])} | {fmt(row['relative_error'])} |")
    lines += ["", f"Decision rule: lead only if relative error is at most 0.30 in at least 4/6 focal tracts "
              f"and the Spearman correlation is at least 0.60 over all tracts whose last provided MTBS burn year "
              f"is 2018. Focal passes: {d['focal_relative_error_passes']}/6; all-2018 Spearman: "
              f"{fmt(d['spearman_all_2018_tracts'], 4)} (n={d['n_all_2018_tracts']}); verdict: **{d['verdict']}**.", "",
              "## All tracts with last MTBS burn year 2018", "",
              "The analysis reports every qualifying tract, including zero Camp Fire DINS records, because the "
              "acceptance rule names the entire last-burn-2018 cohort. DINS is incident-specific, so zeros for "
              "tracts associated with other fires are not interpreted as absence of damage.", "",
              "| GEOID | County FIPS | USFS − Overture | DINS destroyed | DINS major | Difference / destroyed |", "|---|---|---:|---:|---:|---:|"]
    for row in report["all_last_burn_2018_tracts"]:
        lines.append(f"| `{row['GEOID']}` | {row['county_fips']} | {row['usfs_minus_overture']:.1f} | "
                     f"{row['dins_destroyed']} | {row['dins_major']} | {fmt(row['difference_over_destroyed'])} |")
    lines += ["", "![Scatter: provided USFS−Overture building-count difference versus Camp Fire DINS destroyed records, among NorCal tracts with last MTBS burn in 2018.](discovery-dins-mechanism.png)", "",
              f"Scatter source: `reports/discovery-dins-mechanism.png` (SHA-256 `{report['scatter']['sha256']}`). "
              f"Spearman uses average ranks and is {fmt(d['spearman_all_2018_tracts'], 4)} on the full "
              f"last-burn-2018 tract cohort. This is a descriptive association, not a validation of the hidden "
              "organiser target.", "", "## Opposite direction: Overture buildings with no Camp Fire DINS record", "",
              f"Across all {report['opposite_direction']['official_sample_tracts']} official NorCal tracts, "
              f"{report['opposite_direction']['tracts_with_overture_and_zero_camp_fire_dins']} have positive "
              "Overture building counts but no Camp Fire DINS point; "
              f"their provided Overture counts sum to {report['opposite_direction']['overture_buildings_in_those_tracts']:,}. "
              "These may include rebuilt structures, but can also reflect incident scope, inspection omission, or "
              "the DINS SRA/perimeter coverage rule. The analysis cannot distinguish those explanations.", "",
              "## Reproduction and provenance", "",
              "Raw incident-filtered GeoJSON is kept outside git in the authorized workspace, with catalogue and "
              "query details in its manifest. Rebuild the analysis and this report with:", "",
              "```bash",
              "COMPETITION_WORKSPACE=\"$COMPETITION_WORKSPACE\" \\",
              "  /home/martin/hddProjects/langywrap/uv run --quiet \\",
              "  --with numpy==2.5.4 --with pyarrow==26.0.0 --with shapely==2.2.0 \\",
              "  --with matplotlib==3.10.8 python scripts/discovery_dins_mechanism.py \\",
              "  --dins-file \"$COMPETITION_WORKSPACE/external-discovery-2026-10-29/camp-fire-dins.geojson\" \\",
              "  --out reports/discovery-dins-mechanism.json \\",
              "  --markdown reports/discovery-dins-mechanism.md \\",
              "  --scatter reports/discovery-dins-mechanism.png", "```", "",
              "The aggregate report contains the input hashes, the six-tract evidence, every 2018 cohort row, "
              "the DINS record totals, join accounting, ratio/relative-error values, Spearman statistic, and "
              "candidate-hash integrity check. JSON pointers for the central claims are "
              "`/decision`, `/focal_butte_tracts`, `/all_last_burn_2018_tracts`, `/spearman`, and "
              "`/opposite_direction`. Scored-candidate CSV hashes are recorded but their values are not read.", "",
              "All findings are bounded to this DINS release, the official NorCal tract sample, and the provided "
              "USFS/Overture/MTBS summaries. No Microsoft footprint files or other removed reference layers were "
              "downloaded, opened, joined, or used; no DINS information entered a scored candidate.", ""]
    return "\n".join(lines)


def make_scatter(report: dict[str, Any], path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = report["all_last_burn_2018_tracts"]
    x = [row["dins_destroyed"] for row in rows]
    y = [row["usfs_minus_overture"] for row in rows]
    fig, ax = plt.subplots(figsize=(8, 6), constrained_layout=True)
    ax.scatter(x, y, alpha=0.65, s=28, color="#3b6ea8", label="last MTBS burn year 2018")
    focal = set(CAMP_FOCAL_GEOIDS)
    for row in rows:
        if row["GEOID"] in focal:
            ax.scatter([row["dins_destroyed"]], [row["usfs_minus_overture"]],
                       color="#bb3e03", edgecolor="black", s=55, zorder=3)
            ax.annotate(row["GEOID"], (row["dins_destroyed"], row["usfs_minus_overture"]),
                        xytext=(3, 3), textcoords="offset points", fontsize=7)
    ax.axhline(0, color="grey", lw=0.8)
    ax.axvline(0, color="grey", lw=0.8)
    ax.set_xlabel("Camp Fire DINS destroyed records in tract")
    ax.set_ylabel("Provided USFS building count − Overture building count")
    ax.set_title("Camp Fire DINS versus provided building-count difference\nNorCal tracts with last MTBS burn year 2018")
    ax.legend(loc="best")
    ax.grid(alpha=0.2)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, metadata={"Software": "Matplotlib"})
    plt.close(fig)


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    raw_path = args.dins_file.resolve()
    manifest_path = raw_path.parent / "MANIFEST.json"
    if not raw_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("pass a raw DINS GeoJSON and its workspace MANIFEST.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if sha256_file(raw_path) != manifest["files"][0]["sha256"]:
        raise ValueError("raw DINS GeoJSON SHA-256 differs from workspace manifest")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    features = raw.get("features")
    if raw.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise ValueError("DINS raw input is not a GeoJSON FeatureCollection")
    geoids = read_sample_geoids(args.workspace / "samples" / f"{REGION}-sample-submission.csv")
    strata, overture, geometries, input_info = load_inputs(args.workspace, geoids)
    counts, join_info = join_and_aggregate(features, geoids, geometries)
    # Hash each frozen artifact by path only; do not parse or load prediction/score contents.
    candidate_hashes = {artifact: sha256_file(ROOT / artifact)
                        for artifact in CANDIDATE_ARTIFACTS}

    tract_rows = []
    for geoid in geoids:
        s, f, c = strata[geoid], overture[geoid], counts[geoid]
        destroyed = int(c.get(DAMAGE_DESTROYED, 0))
        major = int(c.get(DAMAGE_MAJOR, 0))
        diff = float(s["usfs_BuildingCount_sum"]) - float(f["overture_buildings"])
        denom = float(destroyed)
        tract_rows.append({
            "GEOID": geoid, "county_fips": str(s["COUNTYFP"]),
            "last_mtbs_burn_year": (int(s["mtbs_wildfire_last_year"])
                                     if s["mtbs_wildfire_last_year"] is not None else None),
            "mtbs_burned_pct_land": (float(s["mtbs_wildfire_burned_pct_land"])
                                      if s["mtbs_wildfire_burned_pct_land"] is not None else None),
            "overture_buildings": int(round(float(f["overture_buildings"]))),
            "provided_usfs_building_count": float(s["usfs_BuildingCount_sum"]),
            "usfs_minus_overture": diff,
            "dins_destroyed": destroyed, "dins_major": major,
            "dins_all_records": int(sum(c.values())),
            "difference_over_destroyed": diff / denom if denom else None,
            "relative_error": abs(diff - denom) / denom if denom else None,
            "usfs_edition": str(s["usfs_edition"]),
        })
    by_id = {row["GEOID"]: row for row in tract_rows}
    if not set(CAMP_FOCAL_GEOIDS).issubset(by_id):
        raise ValueError("focal Butte tracts missing from official sample")
    focal = [by_id[g] for g in CAMP_FOCAL_GEOIDS]
    focal_passes = sum(row["relative_error"] is not None and row["relative_error"] <= 0.30 for row in focal)
    all_2018 = [row for row in tract_rows if row["last_mtbs_burn_year"] == 2018]
    rho = spearman([float(r["dins_destroyed"]) for r in all_2018],
                   [float(r["usfs_minus_overture"]) for r in all_2018])
    verdict = "lead" if focal_passes >= 4 and rho is not None and rho >= 0.60 else "negative"
    summary = {
        "verdict": verdict,
        "focal_relative_error_passes": focal_passes,
        "focal_required_passes": 4,
        "focal_total": len(focal),
        "spearman_all_2018_tracts": rho,
        "n_all_2018_tracts": len(all_2018),
        "spearman_gate": 0.60,
        "relative_error_gate": 0.30,
        "verdict_summary": (
            "The registered mechanism rule passed: at least four focal tracts are within 30% and the full 2018-burn cohort Spearman is at least 0.60."
            if verdict == "lead" else
            "The registered mechanism rule did not pass; the DINS counts do not meet both focal error and all-2018 cohort Spearman thresholds."),
    }
    damaged = Counter()
    for g in geoids:
        for cls, n in counts[g].items():
            damaged[cls] += n
    # The reverse-direction check intentionally retains the full NorCal sample, not just 2018-burn tracts.
    no_dins = [r for r in tract_rows if r["dins_all_records"] == 0 and r["overture_buildings"] > 0]
    report: dict[str, Any] = {
        "analysis": "public CAL FIRE DINS Camp Fire incident comparison against provided USFS WRC minus Overture building-count differences; discovery only",
        "external_source": {
            "dataset_url": DATASET_URL, "layer_url": LAYER_URL,
            "downloaded_utc": manifest["downloaded_utc"],
            "license": manifest["license"],
            "manifest_path": str(manifest_path), "manifest_sha256": sha256_file(manifest_path),
            "raw_path": str(raw_path), "raw_sha256": sha256_file(raw_path),
            "incident_number": INCIDENT_NUM, "incident_name": INCIDENT_NAME,
        },
        "dins_source": {
            "total_camp_fire_records": len(features),
            "all_source_damage_counts": dict(sorted(Counter(
                f["properties"].get("DAMAGE") for f in features).items())),
            "matched_damage_counts": dict(sorted(damaged.items())),
            "published_limitations": manifest["publisher_data_limitations"],
            "address_fields_requested": False,
        },
        "spatial_join": join_info,
        "comparison": {
            "provided_count_difference_definition": "usfs_BuildingCount_sum minus Overture overture_buildings; not an organiser target",
            "destroyed_definition": DAMAGE_DESTROYED,
            "major_definition": DAMAGE_MAJOR,
            "ratio_definition": "(provided USFS building count - Overture building count) / DINS destroyed records; null when destroyed count is zero",
            "relative_error_definition": "abs((provided USFS building count - Overture building count) - DINS destroyed records) / DINS destroyed records; null when destroyed count is zero",
            "spearman_definition": "Spearman rank correlation with average ranks between DINS destroyed-record count and provided USFS minus Overture difference across all official NorCal tracts whose provided MTBS last-burn year equals 2018; zero DINS is retained",
        },
        "decision": summary,
        "spearman": {"statistic": rho, "n": len(all_2018),
                     "x": "Camp Fire DINS destroyed records per official NorCal tract",
                     "y": "provided USFS building count minus Overture buildings",
                     "cohort": "all official NorCal tracts with last provided MTBS burn year 2018",
                     "average_rank_ties": True},
        "focal_butte_tracts": focal,
        "all_last_burn_2018_tracts": all_2018,
        "opposite_direction": {
            "official_sample_tracts": len(tract_rows),
            "cohort": "all 591 official NorCal sample tracts, with no last-burn-year restriction",
            "tracts_with_overture_and_zero_camp_fire_dins": len(no_dins),
            "overture_buildings_in_those_tracts": sum(r["overture_buildings"] for r in no_dins),
            "tracts": [{"GEOID": r["GEOID"], "overture_buildings": r["overture_buildings"],
                        "dins_all_records": r["dins_all_records"], "last_mtbs_burn_year": r["last_mtbs_burn_year"]}
                       for r in no_dins],
            "interpretation_limit": "zero Camp Fire DINS records may reflect fire-incident mismatch, SRA/perimeter scope, inspection omissions, or later rebuilding; these data do not distinguish these explanations",
        },
        "scatter": {"path": str(args.scatter.resolve().relative_to(ROOT))
                    if args.scatter.resolve().is_relative_to(ROOT) else str(args.scatter.resolve())},
        "inputs": {
            "official_sample": {"path": str(args.workspace / "samples" / f"{REGION}-sample-submission.csv"),
                                "rows": len(geoids), "read_values": ["GEOID"], "score_values_used": False},
            **input_info,
            "dins_geojson": {"path": str(raw_path), "sha256": sha256_file(raw_path),
                             "manifest_sha256": sha256_file(manifest_path),
                             "properties_read": READ_FIELDS.split(","), "geometry_read": True},
        },
        "scored_candidates": {"csvs_changed": False, "csv_sha256": candidate_hashes,
                              "candidate_values_or_scores_read": False,
                              "external_dins_joined_to_scored_candidate": False},
        "analysis_script": {"path": str(Path(__file__).resolve().relative_to(ROOT)),
                            "sha256": sha256_file(Path(__file__).resolve())},
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                        "pyarrow": pa.__version__, "shapely": shapely.__version__},
    }
    make_scatter(report, args.scatter)
    report["scatter"]["sha256"] = sha256_file(args.scatter)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(make_markdown(report), encoding="utf-8")
    if any(sha256_file(ROOT / artifact) != before
           for artifact, before in candidate_hashes.items()):
        raise RuntimeError("discovery analysis changed a scored candidate")
    print(f"PASS DINS discovery analysis: Camp Fire records={len(features)}, matched={join_info['matched_to_official_sample_tracts']}; "
          f"focal passes={focal_passes}/6; 2018 n={len(all_2018)} Spearman={rho}; verdict={verdict}")
    print(f"Wrote {args.out}; scatter={args.scatter}; input DINS SHA-256={sha256_file(raw_path)}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ["COMPETITION_WORKSPACE"])
                        if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--dins-file", type=Path)
    parser.add_argument("--download", action="store_true",
                        help="download the filtered Camp Fire DINS GeoJSON to the dated workspace folder")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/discovery-dins-mechanism.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "reports/discovery-dins-mechanism.md")
    parser.add_argument("--scatter", type=Path, default=ROOT / "reports/discovery-dins-mechanism.png")
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    args.workspace = args.workspace.resolve()
    if args.download:
        folder = args.workspace / "external-discovery-2026-10-29"
        manifest = download_camp_dins(folder)
        args.dins_file = folder / "camp-fire-dins.geojson"
        print(f"Downloaded {manifest['files'][0]['records']} records; manifest {folder / 'MANIFEST.json'}")
    if args.dins_file is None:
        parser.error("pass --dins-file or use --download")
    analyze(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
