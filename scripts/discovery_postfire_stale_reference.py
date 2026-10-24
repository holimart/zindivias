#!/usr/bin/env python3
"""Audit post-fire building-count disagreement using challenge-provided data only.

This is a Best Bias Discovery analysis, not a scored-candidate builder. It
compares provided Overture building summaries with the provided USFS WRC
building-count field, then stratifies by the provided MTBS burn summary. It
never uses sample score placeholders, labels, external evidence, or removed
organiser reference layers. The map geometry is read from the challenge's
provided NorCal ACS-geometry Parquet object in Source Cooperative.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/zindi-bias-bounty-mplconfig")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PatchCollection
from matplotlib.patches import Polygon as MplPolygon
import numpy as np
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
REGION = "northern-ca"
REPLICATES = 50_000
BOOTSTRAP_SEED = 20261024
RATIO_CUTOFF = 0.8
FOCAL_CAMP = (
    "06007001800", "06007002100", "06007002000",
    "06007001900", "06007002300", "06007002200",
)
OTHER_FIRE_EXAMPLES = ("06089010603", "06033000704")
STRATA_COLUMNS = (
    "GEOID", "COUNTYFP", "pop_total", "usfs_BuildingCount_sum", "usfs_edition",
    "mtbs_wildfire_burned_pct_land", "mtbs_wildfire_last_year", "mtbs_wildfire_ever",
    "mtbs_covered",
)
SCORE_CANDIDATES = (
    "C0_all_zero.csv", "C1_region_constants.csv", "C2_component_estimator_v0.csv",
    "C3_denominator_x125.csv", "P4_c1_shrink_alpha.csv",
    "P5_region_shrink_residual.csv", "P6_global_constant.csv",
)
GEOMETRY_KEY = (
    "us-west-2.opendata.source.coop/humane-intelligence/"
    "bias-bounty-mapping-equity-challenge/reference/northern-ca/"
    "northern-ca-census-acs-housing.parquet"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def geoid_digest(ids: list[str]) -> str:
    return hashlib.sha256(("\n".join(ids) + "\n").encode("utf-8")).hexdigest()


def read_geoid_only(path: Path) -> list[str]:
    # Only the first (GEOID) CSV field is accessed; placeholder score fields
    # are not used as data or labels.
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if not header or header[0] != "GEOID":
            raise ValueError(f"expected GEOID in first sample column: {path}")
        ids = [row[0] for row in reader]
    if len(ids) != len(set(ids)) or any(not value for value in ids):
        raise ValueError(f"duplicate/empty official GEOIDs in {path}")
    return ids


def finite_number(value, name: str) -> float:
    if value is None:
        raise ValueError(f"null required value: {name}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"nonfinite required value: {name}={value}")
    return number


def pct_share(under: int, n: int) -> float:
    if n <= 0:
        raise ValueError("empty burn class")
    return under / n


def difference_bootstrap(high_under: int, high_n: int, unburned_under: int,
                         unburned_n: int) -> dict:
    """Stratified nonparametric bootstrap of two Bernoulli tract proportions.

    For a binary outcome, resampling observed tracts with replacement is
    exactly equivalent to drawing Binomial(n, observed share) per replicate.
    """
    if min(high_n, unburned_n) <= 0:
        raise ValueError("bootstrap requires nonempty burn groups")
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    high_draw = rng.binomial(high_n, high_under / high_n, size=REPLICATES) / high_n
    unburned_draw = rng.binomial(
        unburned_n, unburned_under / unburned_n, size=REPLICATES
    ) / unburned_n
    low, high = np.quantile(high_draw - unburned_draw, [0.025, 0.975])
    return {
        "difference_high_burn_minus_unburned": high_under / high_n - unburned_under / unburned_n,
        "bootstrap_95_percentile_ci": [float(low), float(high)],
        "replicates": REPLICATES,
        "seed": BOOTSTRAP_SEED,
        "method": "independent within-group tract bootstrap; exact Binomial resampling for binary below-0.8 indicator",
    }


def get_provided_geometries(official_ids: list[str]) -> tuple[dict[str, object], dict]:
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    info = fs.get_file_info(GEOMETRY_KEY)
    if info.type != pafs.FileType.File:
        raise FileNotFoundError(f"provided challenge geometry object unavailable: s3://{GEOMETRY_KEY}")
    # Read only GEOID and geometry columns; the adjacent housing_units column
    # is not accessed or used.
    parquet = pq.ParquetFile(fs.open_input_file(GEOMETRY_KEY))
    table = parquet.read(columns=["GEOID", "geometry"])
    records = table.to_pylist()
    geometry_by_id = {str(row["GEOID"]): bytes(row["geometry"]) for row in records}
    if len(geometry_by_id) != len(records) or set(geometry_by_id) != set(official_ids):
        raise ValueError("provided NorCal geometry IDs do not exactly match official sample")
    digest = hashlib.sha256()
    for geoid in official_ids:
        digest.update(geoid.encode("ascii"))
        digest.update(b"\0")
        digest.update(geometry_by_id[geoid])
        digest.update(b"\n")
    metadata = {
        "uri": f"s3://{GEOMETRY_KEY}",
        "object_bytes": info.size,
        "rows": len(records),
        "read_columns": ["GEOID", "geometry"],
        "housing_units_column_read": False,
        "selected_geoid_geometry_sha256_in_official_sample_order": digest.hexdigest(),
        "crs": "EPSG:4326 per GeoParquet default when CRS metadata is absent",
    }
    return geometry_by_id, metadata


def polygon_patches(geometry):
    parts = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
    for part in parts:
        if part.is_empty:
            continue
        yield MplPolygon(np.asarray(part.exterior.coords), closed=True)


def draw_map(records: list[dict], geometry_wkb: dict[str, bytes], path: Path) -> None:
    ids = [row["GEOID"] for row in records]
    raw_geometries = shapely.from_wkb([geometry_wkb[geoid] for geoid in ids])
    # GeoParquet's absent CRS field defaults to WGS84; project for a readable
    # regional map. Only supplied tract polygons are used.
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    projected = shapely.transform(raw_geometries, transformer.transform, interleaved=False)
    projected_by_id = {geoid: projected[i] for i, geoid in enumerate(ids)}
    rec_by_id = {row["GEOID"]: row for row in records}

    fig, (ax_all, ax_butte) = plt.subplots(1, 2, figsize=(12.5, 6.8), constrained_layout=True)
    cmap = plt.get_cmap("YlOrRd")
    norm = matplotlib.colors.Normalize(vmin=0, vmax=100, clip=True)

    def add_panel(ax, selected_ids, title, label_focus=False):
        patches, values, low_patches = [], [], []
        for geoid in selected_ids:
            geometry = projected_by_id[geoid]
            value = min(100.0, max(0.0, rec_by_id[geoid]["burn_pct"]))
            for patch in polygon_patches(geometry):
                patches.append(patch)
                values.append(value)
                if rec_by_id[geoid]["ratio"] < RATIO_CUTOFF:
                    low_patches.append(MplPolygon(np.asarray(patch.get_xy()), closed=True))
        collection = PatchCollection(patches, cmap=cmap, norm=norm,
                                     linewidths=0.25, edgecolor="#f4f1e9")
        collection.set_array(np.asarray(values))
        ax.add_collection(collection)
        if low_patches:
            outline = PatchCollection(low_patches, facecolor="none", edgecolor="#3b1f73",
                                      linewidths=0.85, zorder=3)
            ax.add_collection(outline)
        geoms = [projected_by_id[geoid] for geoid in selected_ids]
        x0 = min(g.bounds[0] for g in geoms); y0 = min(g.bounds[1] for g in geoms)
        x1 = max(g.bounds[2] for g in geoms); y1 = max(g.bounds[3] for g in geoms)
        padx, pady = (x1-x0)*0.025, (y1-y0)*0.025
        ax.set_xlim(x0-padx, x1+padx); ax.set_ylim(y0-pady, y1+pady)
        ax.set_aspect("equal", adjustable="box")
        ax.set_title(title, fontsize=11)
        ax.set_axis_off()
        if label_focus:
            # Numbered annotations map directly to the table in the report.
            for index, geoid in enumerate(FOCAL_CAMP, start=1):
                centroid = projected_by_id[geoid].representative_point()
                ax.scatter([centroid.x], [centroid.y], s=20, marker="o",
                           facecolor="white", edgecolor="#3b1f73", zorder=4)
                ax.annotate(str(index), (centroid.x, centroid.y), xytext=(2, 2),
                            textcoords="offset points", fontsize=7, weight="bold", zorder=5)
        return collection

    all_ids = ids
    butte_ids = [row["GEOID"] for row in records if row["COUNTYFP"] == "007"]
    add_panel(ax_all, all_ids, "Northern California sample\nfill = provided MTBS burn percentage", False)
    add_panel(ax_butte, butte_ids, "Butte County tracts\nnumbered markers = six 2018-burn focus GEOIDs", True)
    colorbar = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), ax=[ax_all, ax_butte],
                            fraction=0.035, pad=0.02)
    colorbar.set_label("Provided MTBS burned-area percentage (display clipped at 100%)")
    fig.suptitle("Post-fire building-count disagreement in the provided data", fontsize=14)
    fig.text(0.5, 0.01,
             "Purple outline: Overture buildings / provided USFS WRC building count < 0.8. "
             "Burn percentages >100 are excluded from the binned analysis; no organiser labels used.",
             ha="center", fontsize=8)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, metadata={"Software": "Matplotlib"}, facecolor="white")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"])
                        if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/discovery-postfire.json")
    parser.add_argument("--map", type=Path, default=ROOT / "reports/discovery-postfire-map.png")
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    workspace = args.workspace.resolve()
    sample_path = workspace / "samples" / f"{REGION}-sample-submission.csv"
    strata_path = workspace / "defined-flags-inputs" / f"{REGION}-strata-tract-table.parquet"
    features_path = workspace / "gap-estimator" / "features" / f"{REGION}-overture-tract-summary.parquet"
    official_ids = read_geoid_only(sample_path)
    if len(official_ids) != 591:
        raise ValueError(f"expected 591 official NorCal IDs; found {len(official_ids)}")

    strata_table = pq.read_table(strata_path, columns=list(STRATA_COLUMNS))
    feature_table = pq.read_table(features_path, columns=["GEOID", "overture_buildings"])
    strata_rows = strata_table.to_pylist(); feature_rows = feature_table.to_pylist()
    strata = {str(row["GEOID"]): row for row in strata_rows}
    features = {str(row["GEOID"]): row for row in feature_rows}
    official_set = set(official_ids)
    if len(strata) != len(strata_rows) or set(strata) != official_set:
        raise ValueError("NorCal strata IDs do not exactly match official sample IDs")
    if len(features) != len(feature_rows) or set(features) != official_set:
        raise ValueError("NorCal Overture feature IDs do not exactly match official sample IDs")

    scored_hashes_before = {
        name: sha256_file(ROOT / "reports/candidates" / name)
        for name in SCORE_CANDIDATES
    }
    records = []
    for geoid in official_ids:
        s, f = strata[geoid], features[geoid]
        overture = finite_number(f["overture_buildings"], f"{geoid}.overture_buildings")
        usfs = finite_number(s["usfs_BuildingCount_sum"], f"{geoid}.usfs_BuildingCount_sum")
        if usfs <= 0:
            raise ValueError(f"nonpositive provided USFS building count for {geoid}")
        burn = finite_number(s["mtbs_wildfire_burned_pct_land"], f"{geoid}.mtbs_wildfire_burned_pct_land")
        population = finite_number(s["pop_total"], f"{geoid}.pop_total")
        ratio = overture / usfs
        records.append({
            "GEOID": geoid,
            "COUNTYFP": str(s["COUNTYFP"]),
            "overture_buildings": int(overture),
            "usfs_BuildingCount_sum": usfs,
            "ratio": ratio,
            "burn_pct": burn,
            "last_burn_year": (int(s["mtbs_wildfire_last_year"])
                               if s["mtbs_wildfire_last_year"] is not None else None),
            "mtbs_wildfire_ever": bool(s["mtbs_wildfire_ever"]),
            "mtbs_covered": bool(s["mtbs_covered"]),
            "pop_total": int(population),
            "usfs_edition": str(s["usfs_edition"]),
        })
    by_id = {row["GEOID"]: row for row in records}
    if set(FOCAL_CAMP + OTHER_FIRE_EXAMPLES) - set(by_id):
        raise ValueError("one or more preregistered named GEOIDs absent from official NorCal IDs")

    # The supplied percentage is cumulative and can exceed 100 after repeat
    # burns. Keep those tracts in named/high-burn evidence but exclude them from
    # mutually exclusive 0-100 percentage bins instead of silently clipping.
    def burn_class(value: float) -> str | None:
        if value < 0 or value > 100:
            return None
        if value == 0:
            return "unburned_0"
        if value <= 5:
            return "gt0_to5"
        if value <= 20:
            return "gt5_to20"
        if value < 50:
            return "gt20_lt50"
        return "50_to100"

    class_labels = {
        "unburned_0": "0% (unburned)",
        "gt0_to5": ">0–5%",
        "gt5_to20": ">5–20%",
        "gt20_lt50": ">20–<50%",
        "50_to100": "50–100%",
    }
    valid_bins = [row for row in records if burn_class(row["burn_pct"]) is not None]
    bins = []
    for key in class_labels:
        group = [row for row in valid_bins if burn_class(row["burn_pct"]) == key]
        below = [row for row in group if row["ratio"] < RATIO_CUTOFF]
        bins.append({"class": class_labels[key], "n": len(group),
                     "below_ratio_cutoff_n": len(below),
                     "share_ratio_below_0_8": pct_share(len(below), len(group))})
    excluded_over_100 = [row for row in records if row["burn_pct"] > 100]
    excluded_negative = [row for row in records if row["burn_pct"] < 0]
    if [row["n"] for row in bins] != [449, 28, 32, 32, 36]:
        raise ValueError(f"burn-class bins changed unexpectedly: {[row['n'] for row in bins]}")

    unburned = [row for row in valid_bins if burn_class(row["burn_pct"]) == "unburned_0"]
    high_burn = [row for row in valid_bins if burn_class(row["burn_pct"]) == "50_to100"]
    unburned_below = sum(row["ratio"] < RATIO_CUTOFF for row in unburned)
    high_burn_below = sum(row["ratio"] < RATIO_CUTOFF for row in high_burn)
    bootstrap = difference_bootstrap(high_burn_below, len(high_burn),
                                     unburned_below, len(unburned))

    camp_rows = []
    for geoid in FOCAL_CAMP:
        row = by_id[geoid]
        camp_rows.append({
            "GEOID": geoid,
            "county_fips": row["COUNTYFP"],
            "overture_buildings": row["overture_buildings"],
            "provided_usfs_wrc_building_count": row["usfs_BuildingCount_sum"],
            "overture_to_usfs_ratio": row["ratio"],
            "mtbs_burned_pct_land": row["burn_pct"],
            "mtbs_last_burn_year": row["last_burn_year"],
            "pop_total": row["pop_total"],
        })
        if row["last_burn_year"] != 2018 or not 0.6 <= row["ratio"] <= 0.8:
            raise ValueError(f"Camp Fire focus row outside expected evidence bounds: {geoid}")
    other_rows = []
    for geoid in OTHER_FIRE_EXAMPLES:
        row = by_id[geoid]
        other_rows.append({
            "GEOID": geoid,
            "county_fips": row["COUNTYFP"],
            "overture_to_usfs_ratio": row["ratio"],
            "mtbs_burned_pct_land": row["burn_pct"],
            "mtbs_last_burn_year": row["last_burn_year"],
            "pop_total": row["pop_total"],
        })

    butte = [row for row in records if row["COUNTYFP"] == "007"]
    butte_low = sorted([row for row in butte if row["ratio"] < RATIO_CUTOFF],
                       key=lambda row: row["GEOID"])
    butte_summary = {
        "county": "Butte County, California (provided COUNTYFP 007)",
        "tracts": len(butte),
        "below_ratio_0_8_n": len(butte_low),
        "below_ratio_0_8_population_sum": sum(row["pop_total"] for row in butte_low),
        "below_ratio_0_8_geoids": [row["GEOID"] for row in butte_low],
        "below_ratio_0_8_population_is_tract_total_not_people_confirmed_affected": True,
    }
    if len(butte) != 54 or len(butte_low) != 6 or butte_summary["below_ratio_0_8_population_sum"] != 6977:
        raise ValueError(f"Butte check changed unexpectedly: {butte_summary}")

    geometry_by_id, geometry_meta = get_provided_geometries(official_ids)
    map_path = args.map.resolve()
    draw_map(records, geometry_by_id, map_path)
    if any(sha256_file(ROOT / "reports/candidates" / name) != before
           for name, before in scored_hashes_before.items()):
        raise RuntimeError("a scored candidate CSV changed during discovery build")

    report = {
        "analysis": "post-fire building-count disagreement in Northern California using challenge-provided Overture, USFS WRC strata fields, MTBS burn summary, and ACS tract geometry",
        "no_labels_or_removed_references_used": True,
        "external_public_data_used": [],
        "sample_score_placeholders_used": False,
        "interpretation": "The observed ratio supports the hypothesis that a 2018-vintage Microsoft-footprint derivative may be stale after wildfire, leaving a provided risk-building count above the Overture count. This tract-level analysis does not independently verify the supplied count vintage and does not claim that the hidden organiser target uses a 2018 Microsoft release or this USFS WRC field.",
        "ratio_definition": "Overture feature-cache overture_buildings / provided strata usfs_BuildingCount_sum; ratio<0.8 is the prespecified disagreement indicator, not an organiser score or validated coverage gap.",
        "burn_definition": "Provided mtbs_wildfire_burned_pct_land and mtbs_wildfire_last_year; categories require burned percentage in [0,100]. Values >100 can occur with repeated burns and are listed as excluded from the categorical table, not clipped.",
        "focal_butte_2018_burn_geoids": camp_rows,
        "additional_named_examples": other_rows,
        "northern_ca_burn_class_table": bins,
        "burn_class_exclusions": {
            "official_northern_ca_rows": len(records),
            "included_in_mutually_exclusive_0_to_100_bins": len(valid_bins),
            "burn_percentage_over_100_n": len(excluded_over_100),
            "burn_percentage_negative_n": len(excluded_negative),
            "burn_percentage_missing_n": sum(row["mtbs_wildfire_burned_pct_land"] is None for row in strata_rows),
        },
        "high_burn_vs_unburned_below_0_8": {
            "high_burn_class": "50–100%",
            "high_burn_n": len(high_burn),
            "high_burn_below_cutoff_n": high_burn_below,
            "high_burn_share_below_cutoff": pct_share(high_burn_below, len(high_burn)),
            "unburned_n": len(unburned),
            "unburned_below_cutoff_n": unburned_below,
            "unburned_share_below_cutoff": pct_share(unburned_below, len(unburned)),
            **bootstrap,
        },
        "butte_county_summary": butte_summary,
        "provided_data_narrative": {
            "potential_impact": "If emergency or recovery planners treat this provided building-count ratio as current-map completeness, post-fire removed structures could be misread as missing mapped buildings, potentially distorting address inventories, dispatch geocoding, evacuation planning, or recovery-resource estimates.",
            "observed_impact": "No dispatch outcomes, evacuation routes, recovery decisions, or affected individuals were observed. The 6,977 value is summed provided tract population for six flagged tracts, not a count of people confirmed affected.",
            "causal_limit": "The analysis is descriptive and does not prove that fire caused the ratio difference or that the hidden organiser score uses the provided USFS WRC attribute.",
        },
        "map": {
            "path": str(map_path.relative_to(ROOT)) if map_path.is_relative_to(ROOT) else str(map_path),
            "sha256": sha256_file(map_path),
            "description": "Two-panel tract map: all NorCal sample polygons and Butte County tracts; fill is provided MTBS burn percentage (display clipped to 100), purple outline is Overture/USFS ratio<0.8, numbered markers are the six focal GEOIDs in report order.",
            "geometry_source": geometry_meta,
        },
        "scored_candidate_csvs_changed": False,
        "scored_candidate_csv_sha256": scored_hashes_before,
        "inputs": {
            "official_sample": {
                "path": str(sample_path), "rows": len(official_ids),
                "geoid_sequence_sha256": geoid_digest(official_ids),
                "placeholder_score_columns_accessed": False,
            },
            "provided_strata": {
                "path": str(strata_path), "sha256": sha256_file(strata_path),
                "read_columns": list(STRATA_COLUMNS),
                "provided_usfs_edition_values": sorted({row["usfs_edition"] for row in strata_rows}),
            },
            "provided_overture_feature_cache": {
                "path": str(features_path), "sha256": sha256_file(features_path),
                "read_columns": ["GEOID", "overture_buildings"],
            },
            "challenge_geometry": geometry_meta,
        },
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pyarrow": pa.__version__,
            "matplotlib": matplotlib.__version__,
            "shapely": shapely.__version__,
        },
    }
    out_path = args.out.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"PASS post-fire audit: NorCal {len(records)} official tracts; burn bins {[row['n'] for row in bins]}; "
          f"50-100% below-0.8 share {high_burn_below}/{len(high_burn)}; "
          f"unburned {unburned_below}/{len(unburned)}; Butte 6/54 population sum 6,977")
    print(f"Difference {bootstrap['difference_high_burn_minus_unburned']:.6f}; "
          f"95% bootstrap CI {bootstrap['bootstrap_95_percentile_ci']}")
    print(f"Report {out_path}; map {map_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
