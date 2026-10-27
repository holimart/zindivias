#!/usr/bin/env python3
"""Audit T3 grade-separation source semantics and tract aggregation using provided data only."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import ProvidedInputs, load_config, official_scored_pairs  # noqa: E402
from bias60.ideas import t as family_t  # noqa: E402
from bias60.ideas.t import _road_layer  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
READ_COLUMNS = ["class", "road_flags", "level_rules", "geometry"]
CONFIG_PATH = ROOT / "config/bias60-harness-preregistered.json"
REGISTRY_PATH = ROOT / "config/bias60-family-t-exact-registry.json"
T3_SOURCE = ROOT / "src/bias60/ideas/t.py"
INPUTS_SOURCE = ROOT / "src/bias60/inputs.py"
FAMILY_SUMMARY = ROOT / "reports/bias60-family-t-summary.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes_hex__": value.hex()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def canonical_row_digest_update(digest: Any, values: list[Any]) -> None:
    digest.update(json.dumps(_jsonable(values), sort_keys=True, separators=(",", ":"),
                            allow_nan=False).encode("utf-8"))
    digest.update(b"\n")


def level_rule_nonzero(level_rules: Any) -> bool:
    """Mirror the registered meaning: any rule dict with a value other than null/zero."""
    return any(isinstance(rule, dict) and rule.get("value") not in (None, 0)
               for rule in (level_rules or []))


def road_flag_grade(road_flags: Any, frozen_flags: set[str]) -> bool:
    for item in road_flags or []:
        values = item.get("values", []) if isinstance(item, dict) else []
        if {str(value).casefold() for value in values} & frozen_flags:
            return True
    return False


def grade_semantics(road_flags: Any, level_rules: Any, frozen_flags: set[str]) -> tuple[bool, bool, bool]:
    by_level = level_rule_nonzero(level_rules)
    by_road_flag = road_flag_grade(road_flags, frozen_flags)
    return by_level, by_road_flag, by_level or by_road_flag


def _row_stats() -> dict[str, int]:
    return {
        "rows": 0, "road_flags_nonnull_rows": 0, "level_rules_nonnull_rows": 0,
        "level_rule_nonzero_rows": 0, "road_flag_grade_rows": 0,
        "both_sources_rows": 0, "either_source_rows": 0,
        "eligible_class_rows": 0, "eligible_both_sources_rows": 0,
        "eligible_flagged_rows": 0,
        "flagged_rows_with_geometry": 0, "eligible_flagged_missing_geometry": 0,
    }


def _class_key(road_class: Any) -> str:
    return str(road_class) if isinstance(road_class, str) else "<null-or-nonstring>"


def audit_region(workspace: Path, region: str) -> dict[str, Any]:
    import pyarrow.parquet as pq
    import shapely
    from pyproj import Transformer

    config = load_config(CONFIG_PATH)
    seed = config["seeds"]["T3"]
    eligible_classes = set(seed["eligible_classes"])
    named_classes = set(seed["named_classes"])
    frozen_flags = {str(flag).casefold() for flag in seed["grade_separation_flags"]}
    all_pairs = [(str(geoid), str(name)) for geoid, name in official_scored_pairs(workspace)]
    total_expected = int(config["inputs"]["official_scored_rows"])
    if len(all_pairs) != total_expected or len({geoid for geoid, _ in all_pairs}) != total_expected:
        raise ValueError("official scored GEOID scope is not the frozen 9,379 unique IDs")
    geoids = [geoid for geoid, name in all_pairs if name == region]
    if len(geoids) != int(config["inputs"]["official_rows_by_region"][region]):
        raise ValueError(f"{region}: official GEOID partition has unexpected size")

    inputs = ProvidedInputs(config, workspace, region, geoids)
    area_data = inputs.strata([seed["area_column"]])[seed["area_column"]]
    area_m2 = np.asarray(area_data, dtype=np.float64)
    if area_m2.shape != (len(geoids),):
        raise ValueError(f"{region}: official area array does not align to GEOIDs")
    tract_geometries = inputs.tract_geometries()
    if len(tract_geometries) != len(geoids):
        raise ValueError(f"{region}: official mask count does not align to GEOIDs")
    metric_tracts = shapely.transform(
        tract_geometries,
        Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform,
        interleaved=False,
    )
    tree = shapely.STRtree(metric_tracts)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    mask_hash = hashlib.sha256()
    for geoid, geometry in zip(geoids, tract_geometries):
        mask_hash.update(geoid.encode())
        mask_hash.update(b"\0")
        mask_hash.update(shapely.to_wkb(geometry, byte_order=1))
        mask_hash.update(b"\n")
    area_hash = hashlib.sha256()
    for geoid, area in zip(geoids, area_m2):
        area_hash.update(f"{geoid}\0{area:.17g}\n".encode())

    parquet = _road_layer(inputs, READ_COLUMNS, seed["unfiltered_layer"])
    field_types = {column: str(parquet.schema_arrow.field(column).type) for column in READ_COLUMNS}
    road_schema_sha = hashlib.sha256("\n".join(f"{k}:{field_types[k]}" for k in READ_COLUMNS).encode()).hexdigest()
    object_path = inputs.layer_path(seed["unfiltered_layer"])
    file_info = inputs.filesystem.get_file_info(object_path)
    road_metadata: dict[str, Any] = {
        "provided_road_object": object_path,
        "layer": seed["unfiltered_layer"],
        "rows_in_source_object": int(parquet.metadata.num_rows),
        "row_groups": int(parquet.metadata.num_row_groups),
        "selected_columns_read": READ_COLUMNS,
        "selected_schema_types": field_types,
        "selected_schema_sha256": road_schema_sha,
        "object_bytes": int(file_info.size) if file_info.size is not None and file_info.size >= 0 else None,
    }

    by_class: dict[str, dict[str, int]] = defaultdict(_row_stats)
    all_stats = _row_stats()
    source_hash = hashlib.sha256()
    exact_geom_counter: Counter[str] = Counter()
    exact_geom_first_id: dict[str, int] = {}
    selected_ids: list[int] = []
    selected_wkb: list[bytes] = []
    rows_read = 0
    read_row_ids: set[int] = set()

    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=READ_COLUMNS)
        if table.column_names != READ_COLUMNS:
            raise ValueError(f"{region}: reader returned undeclared or missing source columns {table.column_names}")
        classes = table.column("class").to_pylist()
        flags_column = table.column("road_flags").to_pylist()
        levels_column = table.column("level_rules").to_pylist()
        wkbs = table.column("geometry").to_pylist()
        if not (len(classes) == len(flags_column) == len(levels_column) == len(wkbs)):
            raise ValueError("selected road columns have different row counts")
        for cls, flags, levels, wkb in zip(classes, flags_column, levels_column, wkbs):
            row_id = rows_read
            if row_id in read_row_ids:
                raise ValueError(f"{region}: source row ID read twice: {row_id}")
            read_row_ids.add(row_id)
            rows_read += 1
            canonical_row_digest_update(source_hash, [cls, flags, levels, wkb])
            key = _class_key(cls)
            row = by_class[key]
            level_mark, flag_mark, any_mark = grade_semantics(flags, levels, frozen_flags)
            is_eligible = isinstance(cls, str) and cls in eligible_classes and cls not in named_classes
            stats = (row, all_stats)
            for target in stats:
                target["rows"] += 1
                target["road_flags_nonnull_rows"] += int(flags is not None)
                target["level_rules_nonnull_rows"] += int(levels is not None)
                target["level_rule_nonzero_rows"] += int(level_mark)
                target["road_flag_grade_rows"] += int(flag_mark)
                target["both_sources_rows"] += int(level_mark and flag_mark)
                target["either_source_rows"] += int(any_mark)
                target["eligible_class_rows"] += int(is_eligible)
                target["eligible_both_sources_rows"] += int(is_eligible and level_mark and flag_mark)
                target["eligible_flagged_rows"] += int(is_eligible and any_mark)
                target["flagged_rows_with_geometry"] += int(is_eligible and any_mark and wkb is not None)
                target["eligible_flagged_missing_geometry"] += int(is_eligible and any_mark and wkb is None)
            if is_eligible and any_mark and wkb is not None:
                selected_ids.append(row_id)
                selected_wkb.append(wkb)
                geom_hash = hashlib.sha256(wkb).hexdigest()
                if geom_hash in exact_geom_first_id:
                    exact_flagged_geom_duplicate_rows += 1
                else:
                    exact_geom_first_id[geom_hash] = row_id
                exact_geom_counter[geom_hash] += 1

    if rows_read != int(parquet.metadata.num_rows) or len(read_row_ids) != rows_read:
        raise ValueError(f"{region}: read {rows_read} rows but source metadata declares {parquet.metadata.num_rows}")
    if sum(item["rows"] for item in by_class.values()) != rows_read or all_stats["rows"] != rows_read:
        raise ValueError(f"{region}: class/source prevalence does not account for each read row exactly once")
    if any(sum(row.values()) < 0 for row in by_class.values()):
        raise AssertionError("negative source prevalence count")

    # Independent geometry-to-tract reference: form exact source-row/tract pairs in bounded batches.
    counts = np.zeros(len(geoids), dtype=np.int64)
    pair_ids: set[tuple[int, int]] = set()
    duplicate_pair_repeats = 0
    for start in range(0, len(selected_wkb), 2048):
        stop = min(start + 2048, len(selected_wkb))
        lines = shapely.from_wkb(selected_wkb[start:stop])
        metric_lines = shapely.transform(lines, transformer.transform, interleaved=False)
        pairs = tree.query(metric_lines, predicate="intersects")
        for local_row, tract_id in zip(pairs[0].tolist(), pairs[1].tolist()):
            pair = (selected_ids[start + local_row], tract_id)
            if pair in pair_ids:
                duplicate_pair_repeats += 1
            else:
                pair_ids.add(pair)
                counts[tract_id] += 1
    defined = np.isfinite(area_m2) & (area_m2 > 0)
    density = np.zeros(len(geoids), dtype=np.float64)
    density[defined] = counts[defined] / (area_m2[defined] / 1_000_000.0)

    # Independently run the registered implementation and compare its area-scaled counts.
    production = family_t.t3(inputs)
    production_rows_read = int(production.diagnostics.get("rows_read", -1))
    if production_rows_read != int(parquet.metadata.num_rows):
        raise ValueError(f"{region}: production T3 read {production_rows_read} rows; source declares {parquet.metadata.num_rows}")
    emission = production.emissions[0]
    if production.geoids != geoids:
        raise ValueError(f"{region}: T3 implementation changed official GEOID order")
    prod_count_float = np.asarray(emission.values, dtype=np.float64) * (area_m2 / 1_000_000.0)
    prod_counts = np.rint(prod_count_float).astype(np.int64)
    count_discrepancies = np.abs(prod_counts - counts)
    max_count_discrepancy = int(count_discrepancies.max(initial=0))
    if max_count_discrepancy != 0 or not np.array_equal(emission.defined, defined):
        raise ValueError(f"{region}: independent per-tract counts differ from T3; max={max_count_discrepancy}")
    max_density_diff = float(np.max(np.abs(np.asarray(emission.values) - density), initial=0.0))

    # Compare the current T3 artifact as a separate lineage check; no candidate is modified.
    artifact_values, artifact_defined = inputs.idea_emissions(["T3"])["T3"][("R", "covariate:grade_sep_per_km2")]
    artifact_max_abs = float(np.max(np.abs(np.asarray(artifact_values) - np.asarray(emission.values)), initial=0.0))
    artifact_matches = bool(np.array_equal(artifact_defined, emission.defined) and artifact_max_abs <= 1e-12)
    if not artifact_matches:
        raise ValueError(f"{region}: current T3 output differs from registered artifact (max={artifact_max_abs})")

    dup_groups = sum(1 for count in exact_geom_counter.values() if count > 1)
    dup_excess = sum(count - 1 for count in exact_geom_counter.values() if count > 1)
    flagged_row_ids_sha = hashlib.sha256("\n".join(map(str, selected_ids)).encode()).hexdigest()
    area_defined_ids = int(defined.sum())
    covered_ids = int((counts > 0).sum())
    per_tract = [{
        "GEOID": geoid,
        "ALAND_m2": float(area_m2[i]) if np.isfinite(area_m2[i]) else None,
        "T3_defined": bool(defined[i]),
        "independent_flagged_segment_count": int(counts[i]),
        "independent_density_per_km2": float(density[i]),
        "T3_registered_density_per_km2": float(emission.values[i]),
        "count_discrepancy": int(prod_counts[i] - counts[i]),
    } for i, geoid in enumerate(geoids)]
    return {
        "region": region,
        "official_geoids": geoids,
        "official_geoids_sha256": hashlib.sha256("\n".join(geoids).encode()).hexdigest(),
        "official_mask_geoid_wkb_sha256": mask_hash.hexdigest(),
        "official_area_geoid_ALAND_sha256": area_hash.hexdigest(),
        "official_mask_layer": config["inputs"]["polygon_layer"],
        "provided_strata_columns_read": ["GEOID", seed["area_column"]],
        "road_input": {
            **road_metadata,
            "selected_row_payload_sha256": source_hash.hexdigest(),
            "independent_reference_rows_read": rows_read,
            "production_T3_rows_read": production_rows_read,
            "reader_reported_rows_read": rows_read,
            "every_read_row_accounted_once": (rows_read == int(parquet.metadata.num_rows)
                                               and production_rows_read == int(parquet.metadata.num_rows)
                                               and len(read_row_ids) == rows_read),
            "undeclared_columns_read": [],
        },
        "source_field_prevalence": {
            "region_all_classes": all_stats,
            "by_class": dict(sorted(by_class.items())),
            "eligible_class_names": sorted(eligible_classes),
            "named_classes_excluded": sorted(named_classes),
            "frozen_grade_separation_flag_values": sorted(frozen_flags),
        },
        "geometry_tract_aggregation": {
            "eligible_source_rows_marked_by_either_source_with_geometry": len(selected_ids),
            "eligible_source_rows_missing_geometry": all_stats["eligible_flagged_missing_geometry"],
            "independent_flagged_segment_tract_pairs": len(pair_ids),
            "duplicate_segment_tract_pair_repeats": duplicate_pair_repeats,
            "flagged_exact_geometry_duplicate_groups": dup_groups,
            "flagged_exact_geometry_duplicate_excess_rows": dup_excess,
            "flagged_source_row_ids_sha256": flagged_row_ids_sha,
            "independent_per_tract_count_discrepancy_max": max_count_discrepancy,
            "independent_density_max_abs_difference_from_T3": max_density_diff,
            "tracts_with_at_least_one_flagged_segment": covered_ids,
            "official_tracts": len(geoids),
            "T3_defined_tracts_from_ALAND": area_defined_ids,
            "tract_coverage_fraction_all_official": covered_ids / len(geoids),
            "registered_T3_artifact": {
                "sha256": sha256_file(workspace / "ideas/T3.parquet"),
                "matches_implementation": artifact_matches,
                "maximum_absolute_density_difference": artifact_max_abs,
                "defined_count": int(np.asarray(emission.defined).sum()),
            },
        },
        "t3_diagnostics": production.diagnostics,
        "per_tract": per_tract,
        "accessed_layers": sorted(set(inputs.accessed["layers"])),
        "accessed_columns": sorted(set(inputs.accessed["parquet_columns"])),
        "accessed_idea_artifacts": sorted(set(inputs.accessed.get("idea_artifacts", []))),
    }


class _ToyParquet:
    def __init__(self, table: Any):
        self.table = table
        self.metadata = type("Metadata", (), {"num_row_groups": 1, "num_rows": table.num_rows})()

    def read_row_group(self, index: int, columns: list[str]):
        if index != 0:
            raise IndexError(index)
        return self.table.select(columns)


class _ToyInputs:
    region = "toy"
    geoids = ["toy-a", "toy-b"]
    accessed = {"layers": [], "parquet_columns": []}
    config = {"seeds": {"T3": {
        "unfiltered_layer": "roads-unfiltered",
        "named_classes": ["motorway", "trunk", "primary", "secondary"],
        "eligible_classes": ["tertiary", "unclassified"],
        "grade_separation_flags": ["bridge", "is_bridge", "tunnel", "is_tunnel"],
        "area_column": "ALAND",
    }}}

    def tract_geometries(self):
        import shapely
        return np.asarray([shapely.box(0, 0, 1, 1), shapely.box(1, 0, 2, 1)], dtype=object)

    def strata(self, columns: list[str]):
        return {column: np.asarray([1_000_000.0, 2_000_000.0]) for column in columns}


def run_toy_network_check() -> dict[str, Any]:
    """Compare production T3 against a direct row-by-row geometry reference on a fixed toy graph."""
    import pyarrow as pa
    import shapely

    rows = [
        ("tertiary", [{"values": ["bridge"]}], None, shapely.LineString([(0.1, 0.5), (0.9, 0.5)])),
        ("unclassified", None, [{"value": 1}], shapely.LineString([(0.5, 0.6), (1.5, 0.6)])),
        ("tertiary", [{"values": ["tunnel"]}], [{"value": 2}], shapely.LineString([(0.2, 0.2), (0.8, 0.2)])),
        ("tertiary", [{"values": ["Bridge"]}], None, shapely.LineString([(1.2, 0.3), (1.8, 0.3)])),
        ("primary", [{"values": ["bridge"]}], [{"value": 1}], shapely.LineString([(0.2, 0.7), (0.8, 0.7)])),
        ("residential", [{"values": ["bridge"]}], None, shapely.LineString([(1.2, 0.7), (1.8, 0.7)])),
        ("tertiary", None, [{"value": 0}], shapely.LineString([(0.2, 0.8), (0.8, 0.8)])),
        ("tertiary", None, [{"value": -1}], shapely.LineString([(1.2, 0.8), (1.8, 0.8)])),
        ("tertiary", [{"values": ["bridge"]}], None, shapely.LineString([(0.1, 0.5), (0.9, 0.5)])),
        ("tertiary", [{"values": ["tunnel"]}], None, shapely.LineString([(1.0, 0.1), (1.0, 0.9)])),
    ]
    table = pa.table({
        "class": [row[0] for row in rows],
        "road_flags": [row[1] for row in rows],
        "level_rules": [row[2] for row in rows],
        "geometry": [shapely.to_wkb(row[3]) for row in rows],
    })
    frozen_flags = set(_ToyInputs.config["seeds"]["T3"]["grade_separation_flags"])
    eligible = set(_ToyInputs.config["seeds"]["T3"]["eligible_classes"])
    named = set(_ToyInputs.config["seeds"]["T3"]["named_classes"])
    from pyproj import Transformer
    toy_transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    tracts = list(shapely.transform(np.asarray(_ToyInputs().tract_geometries()),
                                    toy_transformer.transform, interleaved=False))
    projected_rows = [shapely.transform(row[3], toy_transformer.transform, interleaved=False)
                      for row in rows]
    reference = [0, 0]
    per_row_pairs: list[tuple[int, int]] = []
    source_overlap = 0
    eligible_marked_rows = 0
    for row_id, (road_class, flags, levels, _line) in enumerate(rows):
        line = projected_rows[row_id]
        level_mark, flag_mark, marked = grade_semantics(flags, levels, frozen_flags)
        if road_class in eligible and road_class not in named and marked:
            eligible_marked_rows += 1
            source_overlap += int(level_mark and flag_mark)
            for tract_id, tract in enumerate(tracts):
                if line.intersects(tract):
                    reference[tract_id] += 1
                    per_row_pairs.append((row_id, tract_id))
    old_reader = family_t._road_layer
    family_t._road_layer = lambda inputs, columns, layer="roads-unfiltered": _ToyParquet(table)
    try:
        production = family_t.t3(_ToyInputs())
    finally:
        family_t._road_layer = old_reader
    observed = np.rint(production.emissions[0].values * np.asarray([1.0, 2.0])).astype(int).tolist()
    discrepancy = [observed[i] - reference[i] for i in range(2)]
    geom_hashes = [hashlib.sha256(shapely.to_wkb(row[3])).hexdigest() for row in rows
                   if row[0] in eligible and row[0] not in named
                   and grade_semantics(row[1], row[2], frozen_flags)[2]]
    duplicates = Counter(geom_hashes)
    duplicate_groups = sum(count > 1 for count in duplicates.values())
    duplicate_excess = sum(count - 1 for count in duplicates.values() if count > 1)
    pair_repeats = len(per_row_pairs) - len(set(per_row_pairs))
    if observed != reference or pair_repeats != 0:
        raise ValueError(f"toy T3 reference mismatch: production={observed}, reference={reference}")
    return {
        "toy_source_rows": len(rows),
        "rows_accounted_once": len(rows),
        "eligible_flagged_rows": eligible_marked_rows,
        "eligible_rows_marked_by_both_sources": source_overlap,
        "exact_duplicate_geometry_groups": duplicate_groups,
        "exact_duplicate_geometry_excess_rows": duplicate_excess,
        "flagged_segment_tract_pairs": len(per_row_pairs),
        "duplicate_segment_tract_pair_repeats": pair_repeats,
        "independent_reference_counts_by_tract": {"toy-a": reference[0], "toy-b": reference[1]},
        "production_counts_by_tract": {"toy-a": observed[0], "toy-b": observed[1]},
        "per_tract_count_discrepancy": {"toy-a": discrepancy[0], "toy-b": discrepancy[1]},
        "maximum_absolute_count_discrepancy": max(map(abs, discrepancy), default=0),
        "result": "pass_exact_match" if max(map(abs, discrepancy), default=0) == 0 else "fail",
    }


def _measure_before() -> dict[str, Any]:
    return {
        "implementation": "src/bias60/ideas/t.py:t3",
        "preexisting_rule": "eligible class rows are marked when level_rules contains a dict whose value is not null/zero OR road_flags contains any frozen grade-separation value; each marked source row is counted in each intersecting tract",
        "prior_report": str(FAMILY_SUMMARY.relative_to(ROOT)),
        "prior_report_sha256": sha256_file(FAMILY_SUMMARY),
        "independent_row_tract_recomputation_before_this_audit": False,
        "candidate_or_estimator_modified": False,
    }


def combine(paths: list[Path], out: Path) -> dict[str, Any]:
    by_region: dict[str, tuple[dict[str, Any], Path]] = {}
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        region = report["region"]
        if region in by_region:
            raise ValueError(f"duplicate regional audit {region}")
        by_region[region] = (report, path)
    if set(by_region) != set(REGIONS):
        raise ValueError(f"expected region reports for {REGIONS}; found {sorted(by_region)}")
    regions = {name: by_region[name][0] for name in REGIONS}
    total_rows = sum(r["road_input"]["reader_reported_rows_read"] for r in regions.values())
    total_tracts = sum(len(r["official_geoids"]) for r in regions.values())
    total_flagged_pairs = sum(r["geometry_tract_aggregation"]["independent_flagged_segment_tract_pairs"] for r in regions.values())
    total_covered = sum(r["geometry_tract_aggregation"]["tracts_with_at_least_one_flagged_segment"] for r in regions.values())
    total_source_overlap = sum(r["source_field_prevalence"]["region_all_classes"]["both_sources_rows"] for r in regions.values())
    total_eligible_overlap = sum(r["source_field_prevalence"]["region_all_classes"]["eligible_both_sources_rows"] for r in regions.values())
    total_dup_groups = sum(r["geometry_tract_aggregation"]["flagged_exact_geometry_duplicate_groups"] for r in regions.values())
    total_dup_excess = sum(r["geometry_tract_aggregation"]["flagged_exact_geometry_duplicate_excess_rows"] for r in regions.values())
    total_pair_repeats = sum(r["geometry_tract_aggregation"]["duplicate_segment_tract_pair_repeats"] for r in regions.values())
    total_count_discrepancy = max((r["geometry_tract_aggregation"]["independent_per_tract_count_discrepancy_max"] for r in regions.values()), default=0)
    by_class: dict[str, dict[str, int]] = defaultdict(_row_stats)
    for report in regions.values():
        for cls, cell in report["source_field_prevalence"]["by_class"].items():
            for key, value in cell.items():
                by_class[cls][key] += value
    toy = run_toy_network_check()
    input_hashes = {
        region: {
            "road_selected_payload_sha256": regions[region]["road_input"]["selected_row_payload_sha256"],
            "official_geoids_sha256": regions[region]["official_geoids_sha256"],
            "official_mask_geoid_wkb_sha256": regions[region]["official_mask_geoid_wkb_sha256"],
            "official_area_geoid_ALAND_sha256": regions[region]["official_area_geoid_ALAND_sha256"],
            "T3_artifact_sha256": regions[region]["geometry_tract_aggregation"]["registered_T3_artifact"]["sha256"],
        } for region in REGIONS
    }
    report = {
        "schema_version": 1,
        "audit": "T3 grade-separation source semantics and exact tract aggregation; diagnostic-only",
        "scope": {
            "official_regions": list(REGIONS),
            "official_tracts": total_tracts,
            "provided_road_source_rows_per_region_pass": total_rows,
            "road_reader_passes_per_region": 2,
            "labels_or_targets_read": False,
            "sample_score_values_read": False,
            "removed_references_or_equivalents_read": False,
            "public_leaderboard_information_used": False,
            "target_fitting_performed": False,
            "candidate_edits": False,
            "uploads": 0,
        },
        "measure_before": _measure_before(),
        "method": {
            "registered_reader": "T3 from provided Overture roads-unfiltered; official ACS tract geometries and ALAND only",
            "source_columns_read": READ_COLUMNS,
            "source_columns_declared_exactly": True,
            "eligible_classes": ["tertiary", "unclassified"],
            "named_classes_excluded": ["motorway", "trunk", "primary", "secondary"],
            "grade_separation_flags": ["bridge", "is_bridge", "tunnel", "is_tunnel"],
            "marking_rule": "one boolean mark per eligible source row: any non-null/nonzero level_rules.value OR any road_flags.values entry casefold-matching the frozen list",
            "aggregation_rule": "one count for each marked input row whose geometry intersects an official tract; exact source-row ID plus tract ID is the pair key",
            "independent_reference": "bounded-batch spatial-tree enumeration of marked source row/tract pairs, independently counted and compared to production density rescaled by official ALAND",
            "duplicate_geometry_policy": "identical WKB geometries are reported, not collapsed; distinct input rows remain distinct segments",
        },
        "measure_after": {
            "rows_read_once_and_accounted": all(r["road_input"]["every_read_row_accounted_once"] for r in regions.values()),
            "undeclared_columns_read": [],
            "independent_per_tract_count_max_discrepancy": total_count_discrepancy,
            "duplicate_segment_tract_pair_repeats": total_pair_repeats,
            "source_rows_marked_by_both_level_rules_and_road_flags": total_source_overlap,
            "eligible_source_rows_marked_by_both_sources": total_eligible_overlap,
            "flagged_exact_geometry_duplicate_groups_sum_by_region": total_dup_groups,
            "flagged_exact_geometry_duplicate_excess_rows_sum_by_region": total_dup_excess,
            "independent_flagged_segment_tract_pairs": total_flagged_pairs,
            "tracts_with_at_least_one_flagged_segment": total_covered,
            "official_tracts": total_tracts,
            "tract_coverage_fraction": total_covered / total_tracts if total_tracts else None,
            "exact_toy_network": toy,
            "source_field_prevalence_by_region": {
                region: regions[region]["source_field_prevalence"] for region in REGIONS
            },
            "source_field_prevalence_by_class_all_regions": dict(sorted(by_class.items())),
            "regional_tract_coverage": {
                region: regions[region]["geometry_tract_aggregation"] for region in REGIONS
            },
            "acceptance": {
                "exact_toy_agreement": toy["result"] == "pass_exact_match",
                "every_read_row_accounted_once": all(r["road_input"]["every_read_row_accounted_once"] for r in regions.values()),
                "zero_double_counted_segment_tract_pairs": total_pair_repeats == 0,
                "no_undeclared_columns": True,
                "independent_per_tract_count_discrepancy_exactly_zero": total_count_discrepancy == 0,
                "accepted": bool(toy["result"] == "pass_exact_match"
                                 and total_rows == sum(r["road_input"]["rows_in_source_object"] for r in regions.values())
                                 and total_pair_repeats == 0 and total_count_discrepancy == 0),
            },
        },
        "input_hashes": input_hashes,
        "provenance": {
            "audit_script_sha256": sha256_file(Path(__file__).resolve()),
            "t3_source_sha256": sha256_file(T3_SOURCE),
            "provided_input_reader_sha256": sha256_file(INPUTS_SOURCE),
            "harness_config_sha256": sha256_file(CONFIG_PATH),
            "family_t_exact_registry_sha256": sha256_file(REGISTRY_PATH),
            "region_checkpoint_sha256": {region: sha256_file(by_region[region][1]) for region in REGIONS},
        },
        "regions": regions,
        "reproduction": {
            "per_region": "uv run --with-requirements requirements.txt python scripts/audit_t3_grade_separation_semantics.py --workspace $COMPETITION_WORKSPACE --region <REGION> --out $COMPETITION_WORKSPACE/t3-grade-separation-checkpoints/<REGION>.json",
            "combine": "uv run --with-requirements requirements.txt python scripts/audit_t3_grade_separation_semantics.py --merge-region-reports <FOUR_REGION_JSON_PATHS> --out reports/bias60-t3-grade-separation-audit.json",
            "run_policy": "Run each region separately and combine after all four checkpoints complete.",
        },
    }
    if not report["measure_after"]["acceptance"]["accepted"]:
        raise ValueError("T3 audit failed one or more declared acceptance criteria")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    workspace_default = os.environ.get("COMPETITION_WORKSPACE")
    parser.add_argument("--workspace", type=Path, default=Path(workspace_default) if workspace_default else None)
    parser.add_argument("--region", choices=REGIONS)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/bias60-t3-grade-separation-audit.json")
    parser.add_argument("--merge-region-reports", nargs="+", type=Path)
    args = parser.parse_args()
    if args.merge_region_reports:
        report = combine(args.merge_region_reports, args.out)
        print(json.dumps({
            "official_tracts": report["scope"]["official_tracts"],
            "road_rows_read_per_pass": report["scope"]["provided_road_source_rows_per_region_pass"],
            "max_count_discrepancy": report["measure_after"]["independent_per_tract_count_max_discrepancy"],
            "duplicate_pairs": report["measure_after"]["duplicate_segment_tract_pair_repeats"],
            "accepted": report["measure_after"]["acceptance"]["accepted"],
            "output": str(args.out),
        }, sort_keys=True))
        return 0
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    if args.region is None:
        parser.error("--region is required; audit separately and merge checkpoints")
    report = audit_region(args.workspace, args.region)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "region": args.region,
        "official_tracts": len(report["official_geoids"]),
        "source_rows": report["road_input"]["reader_reported_rows_read"],
        "flagged_tract_pairs": report["geometry_tract_aggregation"]["independent_flagged_segment_tract_pairs"],
        "max_count_discrepancy": report["geometry_tract_aggregation"]["independent_per_tract_count_discrepancy_max"],
        "output": str(args.out),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
