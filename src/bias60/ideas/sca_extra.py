"""Sca2-Sca5: scale aggregation, assignment sensitivity and soft composites.

All geometry is from the provided ACS polygons and Overture layers. No labels or removed reference
layers are read. Sca5 treats component definedness probabilities as explicit, uncalibrated soft
proxies; it enumerates all three-component patterns and conditions on at least one component being
defined.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import shapely

from bias60.contracts import Emission, IdeaOutput
from bias60.ideas import implements
from bias60.ideas.g import TRANSFORMER
from bias60.ideas.p_physical import _rule_clean_building, _rule_clean_poi
from bias60.ideas._common import quantiles


def first_ring_neighbors(tract_geometries: Any) -> list[list[int]]:
    """Queen-contiguity first ring, excluding self and with sorted deterministic indices."""
    geometries = np.asarray(tract_geometries, dtype=object)
    tree = shapely.STRtree(geometries)
    left, right = tree.query(geometries, predicate="touches")
    neighbors = [set() for _ in range(len(geometries))]
    for a, b in zip(left.tolist(), right.tolist()):
        if a != b:
            neighbors[int(a)].add(int(b))
    return [sorted(values) for values in neighbors]


def multiscale_log_oe_slope(observed: np.ndarray, expected: np.ndarray, areas: np.ndarray,
                            county_ids: list[str], neighbors: list[list[int]]) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Slope of log(O/E) against log(area) for tract, tract+first-ring and county totals.

    All sums are over the official tract partition. Rows with a zero observed count at any scale,
    invalid area/expectation, or a degenerate log-area axis are undefined rather than pseudocounted.
    """
    observed = np.asarray(observed, dtype=np.float64)
    expected = np.asarray(expected, dtype=np.float64)
    areas = np.asarray(areas, dtype=np.float64)
    n = len(observed)
    if expected.shape != (n,) or areas.shape != (n,) or len(county_ids) != n or len(neighbors) != n:
        raise ValueError("multi-scale arrays and adjacency must have the same number of tracts")
    if np.any(np.isfinite(observed) & (observed < 0)) or np.any(np.isfinite(expected) & (expected < 0)):
        raise ValueError("observed and expected counts must be nonnegative")
    county_members: dict[str, list[int]] = {}
    for index, county in enumerate(county_ids):
        county_members.setdefault(str(county), []).append(index)
    slope = np.zeros(n, dtype=np.float64)
    defined = np.zeros(n, dtype=np.bool_)
    tract_totals = np.zeros((n, 3, 3), dtype=np.float64)  # (tract, scale, O/E/area)
    for index in range(n):
        first_ring = sorted({index, *neighbors[index]})
        county = county_members[str(county_ids[index])]
        groups = ([index], first_ring, county)
        points = []
        valid = True
        for members in groups:
            o = float(np.sum(observed[members]))
            e = float(np.sum(expected[members]))
            a = float(np.sum(areas[members]))
            tract_totals[index, len(points)] = (o, e, a)
            if not (np.isfinite(o) and o > 0 and np.isfinite(e) and e > 0
                    and np.isfinite(a) and a > 0):
                valid = False
                break
            points.append((math.log(a), math.log(o / e)))
        if not valid:
            continue
        x = np.asarray([point[0] for point in points], dtype=np.float64)
        y = np.asarray([point[1] for point in points], dtype=np.float64)
        if np.unique(x).size < 3:
            continue
        centered = x - float(x.mean())
        denominator = float(np.dot(centered, centered))
        if denominator <= 1e-15:
            continue
        slope[index] = float(np.dot(centered, y - float(y.mean())) / denominator)
        defined[index] = np.isfinite(slope[index])
    metadata = {
        "scale_order": ["tract", "tract_plus_first_ring", "county"],
        "adjacency": "Queen contiguity: polygons that touch at an edge or vertex; self excluded",
        "county_scope": "only official scored GEOIDs in the same state+county",
        "count_conservation": {
            "tract_observed_sum": float(np.sum(observed[np.isfinite(observed)])),
            "county_observed_sum_once": float(sum(np.sum(observed[members])
                                                   for members in county_members.values())),
            "tract_expected_sum": float(np.sum(expected[np.isfinite(expected)])),
            "county_expected_sum_once": float(sum(np.sum(expected[members])
                                                   for members in county_members.values())),
        },
        "tract_scale_totals": tract_totals,
    }
    return slope, defined, metadata


@implements("Sca2")
def sca2(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Sca2"]
    p2 = inputs.config["seeds"]["P2"]
    observed = np.asarray(inputs.features([p2["observed_fire_column"]])
                          [p2["observed_fire_column"]], dtype=np.float64)
    strata = inputs.strata([p2["area_column"], p2["population_column"], p2["ruca_column"],
                            seed["county_column"], seed["state_column"]])
    area_m2 = np.asarray(strata[p2["area_column"]], dtype=np.float64)
    population = np.asarray(strata[p2["population_column"]], dtype=np.float64)
    ruca = np.asarray(strata[p2["ruca_column"]], dtype=np.float64)
    area_km2 = area_m2 / 1_000_000.0
    standards = np.full(area_km2.shape, np.nan, dtype=np.float64)
    for density_class, codes in p2["ruca_classes"].items():
        standards[np.isin(ruca, np.asarray(codes, dtype=np.float64))] = float(
            p2["standard_area_km2"][density_class])
    expected = np.full(area_km2.shape, np.nan, dtype=np.float64)
    valid = (np.isfinite(area_km2) & (area_km2 > 0) & np.isfinite(population)
             & (population >= 0) & np.isfinite(standards) & (standards > 0)
             & np.isfinite(observed) & (observed >= 0))
    expected[valid] = area_km2[valid] / standards[valid]
    p2_artifact = inputs.idea_emissions(["P2"])["P2"].get(("F", "gap"))
    if p2_artifact is None:
        raise ValueError("Sca2 requires the existing P2 per-tract gap artifact")
    p2_gap, p2_defined = p2_artifact
    reconstructed = np.zeros(len(observed), dtype=np.float64)
    reconstructed[valid] = np.clip(1.0 - observed[valid] / expected[valid], 0.0, 1.0)
    if not np.array_equal(p2_defined, valid) or not np.allclose(
            p2_gap[valid], reconstructed[valid], rtol=0.0, atol=1e-12):
        raise ValueError("P2 artifact does not match its registered O/E inputs")
    polygons = inputs.tract_geometries()
    neighbors = first_ring_neighbors(polygons)
    counties = []
    for state, county in zip(strata[seed["state_column"]], strata[seed["county_column"]]):
        state_text = str(state).zfill(2)
        county_text = str(county).zfill(3)
        counties.append(state_text + county_text)
    scale_observed = np.where(valid, observed, 0.0)
    scale_expected = np.where(valid, expected, 0.0)
    values, defined, aggregation = multiscale_log_oe_slope(
        scale_observed, scale_expected, area_m2, counties, neighbors)
    finite_slopes = values[defined]
    diagnostics = {
        "rows": len(values), "defined": int(defined.sum()),
        "undefined_reasons": {"nonpositive_O_or_invalid_E_area_or_degenerate_scale":
                              int((~defined).sum())},
        "gap_inputs": {"idea": "P2", "artifact_validated_against_raw_O_E": True,
                       "observed": quantiles(observed[valid]),
                       "expected": quantiles(expected[valid])},
        "slope_distribution": quantiles(finite_slopes),
        "first_ring_neighbor_count": quantiles(np.asarray([len(x) for x in neighbors], dtype=float)),
        "scale_order": aggregation["scale_order"], "adjacency": aggregation["adjacency"],
        "county_scope": aggregation["county_scope"],
        "count_conservation": {key: value for key, value in aggregation["count_conservation"].items()},
        "no_pseudocount": True,
    }
    return IdeaOutput("Sca2", inputs.region, list(inputs.geoids),
                      [Emission("C", "covariate", values, defined)], diagnostics)


def assignment_variants(feature_geometries: Any, tract_geometries: Any, tract_geoids: list[str],
                        *, kind: str) -> dict[str, Any]:
    """Assign points/polygons/lines by centroid, intersection/clipping, and largest overlap.

    ``kind`` is ``count`` for footprints/points or ``length`` for road lines. For length, the
    intersection variant clips linework to each tract; centroid/largest variants assign full feature
    length once. Ties in largest-overlap always go to the lexicographically smallest GEOID.
    """
    geoms = np.asarray(feature_geometries, dtype=object)
    tracts = np.asarray(tract_geometries, dtype=object)
    if kind not in {"count", "length"} or len(tracts) != len(tract_geoids):
        raise ValueError("invalid assignment geometry kind or tract GEOID partition")
    n = len(tracts)
    centroid_values = np.zeros(n, dtype=np.float64)
    intersect_values = np.zeros(n, dtype=np.float64)
    largest_values = np.zeros(n, dtype=np.float64)
    clipped_values = np.zeros(n, dtype=np.float64)
    if not len(geoms):
        return {"centroid": centroid_values, "intersects": intersect_values,
                "largest_overlap": largest_values, "clipped": clipped_values,
                "features_intersecting": 0, "centroid_assigned": 0,
                "largest_assigned": 0, "intersect_pairs": 0}
    weights = np.asarray(shapely.length(geoms) if kind == "length" else np.ones(len(geoms)),
                         dtype=np.float64)
    valid = shapely.is_valid(geoms) & ~shapely.is_empty(geoms) & np.isfinite(weights) & (weights > 0)
    geoms = geoms[valid]
    weights = weights[valid]
    if not len(geoms):
        return {"centroid": centroid_values, "intersects": intersect_values,
                "largest_overlap": largest_values, "clipped": clipped_values,
                "features_intersecting": 0, "centroid_assigned": 0,
                "largest_assigned": 0, "intersect_pairs": 0}
    tree = shapely.STRtree(tracts)
    centers = shapely.centroid(geoms)
    center_feature, center_tract = tree.query(centers, predicate="within")
    np.add.at(centroid_values, center_tract,
              weights[center_feature])
    feature_ids, tract_ids = tree.query(geoms, predicate="intersects")
    best: dict[int, tuple[float, str, int]] = {}
    if len(feature_ids):
        intersections = shapely.intersection(geoms[feature_ids], tracts[tract_ids])
        if kind == "length":
            overlap = np.asarray(shapely.length(intersections), dtype=np.float64)
            np.add.at(intersect_values, tract_ids, overlap)
            clipped_values = intersect_values.copy()
        else:
            overlap = np.asarray(shapely.area(intersections), dtype=np.float64)
            np.add.at(intersect_values, tract_ids, 1.0)
        # The feature order returned by STRtree is not contractual: an explicit score/GEOID order
        # makes tie-breaking reproducible for boundary-crossing inputs.
        for feature, tract, amount in zip(feature_ids.tolist(), tract_ids.tolist(), overlap.tolist()):
            tract = int(tract); feature = int(feature); amount = float(amount)
            key = (amount, tract_geoids[tract], tract)
            prior = best.get(feature)
            if (prior is None or amount > prior[0] + 1e-9
                    or (abs(amount - prior[0]) <= 1e-9 and tract_geoids[tract] < prior[1])):
                best[feature] = key
        for feature, (_, _, tract) in best.items():
            largest_values[tract] += weights[feature]
    return {"centroid": centroid_values, "intersects": intersect_values,
            "largest_overlap": largest_values, "clipped": clipped_values,
            "features_intersecting": len(set(feature_ids.tolist())),
            "centroid_assigned": int(len(center_feature)),
            "largest_assigned": int(sum(1 for feature in best)),
            "intersect_pairs": int(len(feature_ids)),
            "input_valid_features": int(len(geoms)),
            "largest_conserves_intersecting_features": int(sum(1 for feature in best))
            == len(set(feature_ids.tolist()))}


def _metric_tracts(inputs) -> tuple[np.ndarray, Any]:
    geometries = shapely.transform(inputs.tract_geometries(), TRANSFORMER.transform,
                                   interleaved=False)
    return np.asarray(geometries, dtype=object), shapely.STRtree(geometries)


def _scan_assignment_layer(inputs, layer: str, columns: list[str], tracts: np.ndarray,
                           *, kind: str, source_filter: str | None = None,
                           named_classes: set[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    import pyarrow.parquet as pq

    path = inputs.layer_path(layer)
    parquet = pq.ParquetFile(inputs.filesystem.open_input_file(path))
    missing = sorted(set(columns) - set(parquet.schema_arrow.names))
    if missing:
        raise ValueError(f"{layer} lacks Sca3 columns {missing}")
    inputs.accessed["parquet_columns"].extend(f"{inputs.region}-overture-{layer}.parquet:{c}"
                                                for c in columns)
    aggregate = {name: np.zeros(len(tracts), dtype=np.float64)
                 for name in ("centroid", "intersects", "largest_overlap", "clipped")}
    rows_scanned = rows_kept = rows_null = rows_intersecting = 0
    for row_group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(row_group, columns=columns)
        geoms = table["geometry"].to_pylist()
        rows_scanned += len(geoms)
        if source_filter:
            sources = table["sources"].to_pylist()
        else:
            sources = None
        if named_classes:
            classes = table["class"].to_pylist()
        else:
            classes = None
        keep = []
        for index, wkb in enumerate(geoms):
            if wkb is None:
                rows_null += 1
                continue
            if sources is not None:
                allowed = (_rule_clean_building(sources[index]) if source_filter == "building"
                           else _rule_clean_poi(sources[index]))
                if not allowed:
                    continue
            if classes is not None and str(classes[index] or "").casefold() not in named_classes:
                continue
            keep.append(index)
        if not keep:
            continue
        shapes = shapely.from_wkb([geoms[index] for index in keep])
        metric = shapely.transform(shapes, TRANSFORMER.transform, interleaved=False)
        valid = shapely.is_valid(metric) & ~shapely.is_empty(metric)
        metric = metric[valid]
        rows_kept += len(metric)
        variants = assignment_variants(metric, tracts, list(inputs.geoids), kind=kind)
        rows_intersecting += int(variants["features_intersecting"])
        unit_scale = 1_000.0 if kind == "length" else 1.0
        for name in aggregate:
            aggregate[name] += variants[name] / unit_scale
    meta = {"region": inputs.region, "layer": layer, "rows_total": int(parquet.metadata.num_rows),
            "rows_scanned": rows_scanned, "rule_clean_valid_geometries": rows_kept,
            "features_intersecting_official_tracts": rows_intersecting,
            "null_geometry_rows": rows_null, "assignment": ["centroid", "intersects", "largest_overlap"],
            "intersects_policy": "line length is clipped to tract; point/polygon features count in each intersecting tract",
            "largest_overlap_tie_break": "lexicographically smallest official GEOID",
            "count_conservation": "largest-overlap assigns each intersecting feature exactly once"}
    return aggregate, meta


@implements("Sca3")
def sca3(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Sca3"]
    tracts, _ = _metric_tracts(inputs)
    building, building_meta = _scan_assignment_layer(
        inputs, "buildings", seed["building_columns"], tracts, kind="count", source_filter="building")
    pois, poi_meta = _scan_assignment_layer(
        inputs, "pois", seed["poi_columns"], tracts, kind="count", source_filter="poi")
    roads, road_meta = _scan_assignment_layer(
        inputs, "roads", seed["road_columns"], tracts, kind="length",
        named_classes=set(seed["road_named_classes"]))
    # Retain rule-clean source counts for Sca5's soft presence proxies; no target or reference data.
    cache_dir = inputs.workspace / inputs.config["inputs"]["ideas_cache_dir"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{inputs.region}-sca3-assignment-v1.npz"
    np.savez_compressed(cache_path,
        building_centroid_count=building["centroid"], building_largest_count=building["largest_overlap"],
        poi_centroid_count=pois["centroid"], road_centroid_km=roads["centroid"],
        road_intersect_km=roads["intersects"], road_largest_km=roads["largest_overlap"],
        metadata_json=np.array("sca3 assignment cache v1"))
    inputs.accessed["caches"].append(cache_path.name)
    b_centroid, b_intersects = building["centroid"], building["intersects"]
    r_centroid, r_clipped = roads["centroid"], roads["intersects"]
    b_sensitivity = np.abs(b_centroid - b_intersects) / np.maximum(b_centroid, 1.0)
    road_floor_km = float(seed["road_denominator_floor_m"]) / 1000.0
    r_sensitivity = np.abs(r_centroid - r_clipped) / np.maximum(r_centroid, road_floor_km)
    defined = np.ones(len(inputs.geoids), dtype=np.bool_)
    diagnostics = {
        "rows": len(inputs.geoids), "defined": len(inputs.geoids),
        "building_sensitivity": quantiles(b_sensitivity),
        "road_sensitivity": quantiles(r_sensitivity),
        "building_variants": {"centroid_count": quantiles(b_centroid),
                              "intersects_count": quantiles(b_intersects),
                              "largest_overlap_count": quantiles(building["largest_overlap"])},
        "poi_variants": {"centroid_count": quantiles(pois["centroid"]),
                         "intersects_count": quantiles(pois["intersects"]),
                         "largest_overlap_count": quantiles(pois["largest_overlap"])},
        "road_variants_km": {"centroid_full_length": quantiles(r_centroid),
                             "intersects_clipped_length": quantiles(r_clipped),
                             "largest_overlap_full_length": quantiles(roads["largest_overlap"])},
        "count_conservation": {
            "building_intersecting_features": building_meta["features_intersecting_official_tracts"],
            "building_largest_overlap_total": int(building["largest_overlap"].sum()),
            "poi_intersecting_features": poi_meta["features_intersecting_official_tracts"],
            "poi_largest_overlap_total": int(pois["largest_overlap"].sum()),
            "road_largest_overlap_km": float(roads["largest_overlap"].sum()),
            "road_clipped_km": float(roads["intersects"].sum()),
            "largest_overlap_building_conserved": int(building["largest_overlap"].sum())
                == building_meta["features_intersecting_official_tracts"],
            "largest_overlap_poi_conserved": int(pois["largest_overlap"].sum())
                == poi_meta["features_intersecting_official_tracts"],
        },
        "readers": {"buildings": building_meta, "pois": poi_meta, "roads": road_meta},
        "denominator_floors": {"building_features": 1.0,
                                "road_length_km": road_floor_km},
        "rule_clean_buildings": "exclusive provided OSM/Esri memberships; mixed/Microsoft rows excluded",
        "rule_clean_pois": "provided Overture POIs with Microsoft memberships excluded",
        "no_labels_or_reference_layers": True,
    }
    return IdeaOutput("Sca3", inputs.region, list(inputs.geoids),
        [Emission("B", "covariate", b_sensitivity.astype(np.float64), defined),
         Emission("R", "covariate", r_sensitivity.astype(np.float64), defined)], diagnostics)


def mean_nearest_neighbor_index(points: np.ndarray, area_m2: float) -> float | None:
    """Clark-Evans R using exact planar nearest neighbors; n<2 is explicitly undefined."""
    xy = np.asarray(points, dtype=np.float64)
    if xy.ndim != 2 or xy.shape[1] != 2:
        raise ValueError("points must be an N x 2 coordinate array")
    if len(xy) < 2 or not np.isfinite(area_m2) or area_m2 <= 0 or not np.isfinite(xy).all():
        return None
    # Spatial hash with exact branch-and-bound stopping: once the nearest candidate is no farther
    # than the closest unvisited cell boundary, no omitted point can be closer.
    n = len(xy)
    cell_size = max(1e-6, math.sqrt(float(area_m2) / n))
    cells: dict[tuple[int, int], list[int]] = {}
    cell_ids = np.floor(xy / cell_size).astype(np.int64)
    for index, (cx, cy) in enumerate(cell_ids.tolist()):
        cells.setdefault((cx, cy), []).append(index)
    nearest = np.full(n, np.inf, dtype=np.float64)
    for index, ((cx, cy), (x, y)) in enumerate(zip(cell_ids.tolist(), xy.tolist())):
        radius = 0
        best_sq = np.inf
        while True:
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if radius and max(abs(dx), abs(dy)) != radius:
                        continue
                    for other in cells.get((cx + dx, cy + dy), []):
                        if other == index:
                            continue
                        delta_x = float(xy[other, 0] - x)
                        delta_y = float(xy[other, 1] - y)
                        best_sq = min(best_sq, delta_x * delta_x + delta_y * delta_y)
            if np.isfinite(best_sq):
                left = x - (cx - radius) * cell_size
                right = (cx + radius + 1) * cell_size - x
                bottom = y - (cy - radius) * cell_size
                top = (cy + radius + 1) * cell_size - y
                unvisited_lower_bound = min(left, right, bottom, top)
                if best_sq <= unvisited_lower_bound * unvisited_lower_bound:
                    break
            radius += 1
        nearest[index] = math.sqrt(best_sq)
    mean_observed = float(nearest.mean())
    return 2.0 * mean_observed * math.sqrt(n / float(area_m2))


def _append_by_tract(chunks: list[list[np.ndarray]], coordinates: np.ndarray,
                     tract_ids: np.ndarray) -> None:
    for tract in np.unique(tract_ids):
        selected = coordinates[tract_ids == tract]
        if len(selected):
            chunks[int(tract)].append(np.asarray(selected, dtype=np.float64))


def _tract_point_arrays(chunks: list[list[np.ndarray]]) -> list[np.ndarray]:
    return [np.concatenate(values, axis=0) if values else np.empty((0, 2), dtype=np.float64)
            for values in chunks]


@implements("Sca4")
def sca4(inputs) -> IdeaOutput:
    import pyarrow.parquet as pq

    seed = inputs.config["seeds"]["Sca4"]
    tracts, tree = _metric_tracts(inputs)
    area = np.asarray(shapely.area(tracts), dtype=np.float64)
    n = len(inputs.geoids)
    place_chunks: list[list[np.ndarray]] = [[] for _ in range(n)]
    building_chunks: list[list[np.ndarray]] = [[] for _ in range(n)]
    readers = {}
    for layer, columns, source_filter in (
            ("pois", seed["poi_columns"], "poi"),
            ("buildings", seed["building_columns"], "building")):
        path = inputs.layer_path(layer)
        parquet = pq.ParquetFile(inputs.filesystem.open_input_file(path))
        missing = sorted(set(columns) - set(parquet.schema_arrow.names))
        if missing:
            raise ValueError(f"{layer} lacks Sca4 columns {missing}")
        inputs.accessed["parquet_columns"].extend(
            f"{inputs.region}-overture-{layer}.parquet:{column}" for column in columns)
        chunks = place_chunks if layer == "pois" else building_chunks
        scanned = kept = assigned = null_geometry = nonpoint = 0
        for row_group in range(parquet.metadata.num_row_groups):
            table = parquet.read_row_group(row_group, columns=columns)
            wkbs = table["geometry"].to_pylist()
            sources = table["sources"].to_pylist()
            scanned += len(wkbs)
            valid_indices = [index for index, wkb in enumerate(wkbs)
                             if wkb is not None and ( _rule_clean_poi(sources[index])
                                 if source_filter == "poi" else _rule_clean_building(sources[index]))]
            null_geometry += sum(1 for value in wkbs if value is None)
            if not valid_indices:
                continue
            geometry = shapely.from_wkb([wkbs[index] for index in valid_indices])
            geometry = shapely.transform(geometry, TRANSFORMER.transform, interleaved=False)
            valid = shapely.is_valid(geometry) & ~shapely.is_empty(geometry)
            geometry = geometry[valid]
            if layer == "pois":
                point_mask = np.asarray(shapely.get_type_id(geometry)) == 0
                nonpoint += int((~point_mask).sum())
                geometry = geometry[point_mask]
            if not len(geometry):
                continue
            if layer == "buildings":
                coordinates = shapely.get_coordinates(shapely.centroid(geometry))
            else:
                coordinates = shapely.get_coordinates(geometry)
            left, right = tree.query(shapely.points(coordinates[:, 0], coordinates[:, 1]),
                                     predicate="within")
            _append_by_tract(chunks, coordinates[left], right.astype(np.int64))
            kept += len(geometry)
            assigned += len(left)
        readers[layer] = {"rows_total": int(parquet.metadata.num_rows), "rows_scanned": scanned,
                          "rule_clean_nonnull_geometries": kept,
                          "assigned_to_official_tracts": assigned,
                          "unassigned_or_boundary": int(kept - assigned),
                          "null_geometry_rows": null_geometry,
                          "nonpoint_poi_geometries": nonpoint,
                          "source_rule": ("exclusive provided OSM/Esri buildings" if layer == "buildings"
                                          else "provided POIs excluding Microsoft memberships")}
        if nonpoint:
            raise ValueError(f"Sca4 expected Point geometries in the provided POI layer, saw {nonpoint}")
    place_points = _tract_point_arrays(place_chunks)
    building_points = _tract_point_arrays(building_chunks)
    place_index = np.zeros(n, dtype=np.float64)
    building_index = np.zeros(n, dtype=np.float64)
    defined = np.zeros(n, dtype=np.bool_)
    place_counts = np.asarray([len(points) for points in place_points], dtype=np.int64)
    building_counts = np.asarray([len(points) for points in building_points], dtype=np.int64)
    for tract in range(n):
        p_index = mean_nearest_neighbor_index(place_points[tract], float(area[tract]))
        b_index = mean_nearest_neighbor_index(building_points[tract], float(area[tract]))
        if p_index is None or b_index is None or b_index <= 0:
            continue
        place_index[tract], building_index[tract] = p_index, b_index
        defined[tract] = True
    values = np.zeros(n, dtype=np.float64)
    values[defined] = place_index[defined] / building_index[defined]
    diagnostics = {
        "rows": n, "defined": int(defined.sum()),
        "undefined_reasons": {"places_or_buildings_n_below_2_or_invalid_area": int((~defined).sum())},
        "places_n": quantiles(place_counts.astype(np.float64)),
        "buildings_n": quantiles(building_counts.astype(np.float64)),
        "places_clark_evans_R": quantiles(place_index[defined]),
        "buildings_clark_evans_R": quantiles(building_index[defined]),
        "places_to_buildings_R_ratio": quantiles(values[defined]),
        "small_n_policy": "undefined if either point pattern has fewer than 2 points in a tract",
        "clark_evans_formula": "R = 2 * mean(nearest-neighbour distance) * sqrt(n / tract_area_m2)",
        "tract_area": "provided ACS polygon projected to EPSG:5070",
        "readers": readers,
        "no_reference_or_label_inputs": True,
    }
    return IdeaOutput("Sca4", inputs.region, list(inputs.geoids),
                      [Emission("Est", "covariate", values, defined)], diagnostics)


def expected_definedness_composite(gaps: np.ndarray, probabilities: np.ndarray
                                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Enumerate all 2^3 masks and return E[mean(g_c | d_c=1) | any d_c=1]."""
    gaps = np.asarray(gaps, dtype=np.float64)
    probabilities = np.asarray(probabilities, dtype=np.float64)
    if gaps.ndim != 2 or gaps.shape[1] != 3 or probabilities.shape != gaps.shape:
        raise ValueError("gaps and probabilities must both have shape (n, 3)")
    if np.any(~np.isfinite(gaps)) or np.any((gaps < 0) | (gaps > 1)):
        raise ValueError("component gap estimates must be finite and in [0, 1]")
    if np.any(~np.isfinite(probabilities)) or np.any((probabilities < 0) | (probabilities > 1)):
        raise ValueError("definedness probabilities must be finite and in [0, 1]")
    n = len(gaps)
    weighted = np.zeros(n, dtype=np.float64)
    mass = np.zeros(n, dtype=np.float64)
    pattern_mass = np.zeros((n, 8), dtype=np.float64)
    for mask in range(8):
        members = [component for component in range(3) if mask & (1 << component)]
        weight = np.ones(n, dtype=np.float64)
        for component in range(3):
            p = probabilities[:, component]
            weight *= p if component in members else (1.0 - p)
        pattern_mass[:, mask] = weight
        if not members:
            continue
        mass += weight
        weighted += weight * gaps[:, members].mean(axis=1)
    output = np.zeros(n, dtype=np.float64)
    defined = mass > 0.0
    output[defined] = weighted[defined] / mass[defined]
    return output, defined, pattern_mass


def _component_gap_means(inputs, idea_outputs: dict[str, dict[tuple[str, str], tuple[np.ndarray, np.ndarray]]],
                         n: int) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, int]]:
    sums = {component: np.zeros(n, dtype=np.float64) for component in ("B", "R", "F", "Est")}
    counts = {component: np.zeros(n, dtype=np.int32) for component in sums}
    sources = {component: 0 for component in sums}
    for idea_id, emissions in idea_outputs.items():
        if idea_id in {"Sca5", "Dst4", "Dst5"}:
            continue
        for (component, channel), (values, defined) in emissions.items():
            if component not in sums or channel != "gap":
                continue
            mask = np.asarray(defined, dtype=bool) & np.isfinite(values)
            sums[component][mask] += np.asarray(values, dtype=np.float64)[mask]
            counts[component][mask] += 1
            sources[component] += 1
    means = {}
    valid = {}
    for component in sums:
        valid[component] = counts[component] > 0
        means[component] = np.divide(sums[component], counts[component],
                                     out=np.zeros(n, dtype=np.float64), where=valid[component])
    return means, valid, sources


@implements("Sca5")
def sca5(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Sca5"]
    registry = inputs.config.get("__registry__", {}).get("ideas", [])
    idea_ids = [entry["id"] for entry in registry if entry.get("status") == "implemented"
                and entry["id"] not in {"Sca5", "Dst4", "Dst5"}]
    if not idea_ids:
        raise ValueError("Sca5 requires the implemented idea registry to find gap estimates")
    outputs = inputs.idea_emissions(idea_ids)
    n = len(inputs.geoids)
    gap_means, gap_valid, gap_sources = _component_gap_means(inputs, outputs, n)
    if not gap_sources["B"] or not gap_sources["R"] or not (gap_sources["F"] or gap_sources["Est"]):
        raise ValueError("Sca5 requires gap estimators for road, building, and POI components")
    poi_gap = np.zeros(n, dtype=np.float64)
    poi_halves = np.zeros(n, dtype=np.int8)
    for component in ("F", "Est"):
        mask = gap_valid[component]
        poi_gap[mask] += gap_means[component][mask]
        poi_halves[mask] += 1
    poi_valid = poi_halves > 0
    poi_gap[poi_valid] /= poi_halves[poi_valid]
    gaps = np.column_stack((gap_means["R"], gap_means["B"], poi_gap))
    component_valid = np.column_stack((gap_valid["R"], gap_valid["B"], poi_valid))

    p2_seed = inputs.config["seeds"]["P2"]
    strata = inputs.strata([p2_seed["area_column"], p2_seed["population_column"],
                            p2_seed["ruca_column"], seed["county_column"], seed["state_column"]])
    area_km2 = np.asarray(strata[p2_seed["area_column"]], dtype=np.float64) / 1_000_000.0
    population = np.asarray(strata[p2_seed["population_column"]], dtype=np.float64)
    ruca = np.asarray(strata[p2_seed["ruca_column"]], dtype=np.float64)
    standards = np.full(n, np.nan, dtype=np.float64)
    for density_class, codes in p2_seed["ruca_classes"].items():
        standards[np.isin(ruca, np.asarray(codes, dtype=np.float64))] = float(
            p2_seed["standard_area_km2"][density_class])
    lambda_fire = np.zeros(n, dtype=np.float64)
    valid_rate = (np.isfinite(area_km2) & (area_km2 > 0) & np.isfinite(population)
                  & (population >= 0) & np.isfinite(standards) & (standards > 0))
    lambda_fire[valid_rate] = area_km2[valid_rate] / standards[valid_rate]
    p_fire = -np.expm1(-lambda_fire)

    p3_count = outputs.get("P3", {}).get(("F", "covariate"))
    x1_probability = outputs.get("X1", {}).get(("F", "covariate"))
    x4_count = outputs.get("X4", {}).get(("R", "covariate"))
    if p3_count is None or x1_probability is None or x4_count is None:
        raise ValueError("Sca5 requires registered P3 school-witness and X1 definedness-probability inputs")
    p3_values = p3_count[0]
    x1_values = x1_probability[0]
    x4_values = x4_count[0]
    if (np.any(~np.isfinite(p3_values)) or np.any(p3_values < 0)
            or np.any(~np.isfinite(x1_values)) or np.any((x1_values < 0) | (x1_values > 1))
            or np.any(~np.isfinite(x4_values)) or np.any(x4_values < 0)):
        raise ValueError("Sca5 upstream probability/witness inputs violate their registered domains")
    p_school = -np.expm1(-p3_values / float(seed["school_witness_scale"]))
    p_facility = (p_fire + p_school + x1_values) / 3.0

    cache_path = inputs.workspace / inputs.config["inputs"]["ideas_cache_dir"] / (
        f"{inputs.region}-sca3-assignment-v1.npz")
    if not cache_path.is_file():
        raise FileNotFoundError(f"Sca5 requires Sca3's rule-clean geometry cache: {cache_path}")
    cached = np.load(cache_path, allow_pickle=False)
    if str(cached["metadata_json"]) != "sca3 assignment cache v1":
        raise ValueError("Sca3 assignment cache version mismatch")
    building_witnesses = np.asarray(cached["building_centroid_count"], dtype=np.float64)
    place_witnesses = np.asarray(cached["poi_centroid_count"], dtype=np.float64)
    if building_witnesses.shape != (n,) or place_witnesses.shape != (n,):
        raise ValueError("Sca3 soft-presence witness counts are not aligned to official GEOIDs")
    p_building = -np.expm1(-building_witnesses / float(seed["building_witness_scale"]))
    p_establishments = -np.expm1(-place_witnesses / float(seed["place_witness_scale"]))
    p_poi = (p_facility + p_establishments) / 2.0

    road_seed = inputs.config["seeds"]["P4"]
    road_km = np.asarray(inputs.features([road_seed["road_column"]])[road_seed["road_column"]],
                         dtype=np.float64)
    road_area_km2 = np.asarray(strata[road_seed["area_column"]], dtype=np.float64) / 1_000_000.0
    road_ruca = np.asarray(strata[road_seed["ruca_column"]], dtype=np.float64)
    rural = np.isin(road_ruca, np.asarray(road_seed["rural_ruca"], dtype=np.float64))
    p4_applicable = inputs.region in set(road_seed["applicable_regions"])
    expected_road_km = np.maximum(road_km, 0.0)
    p4_rho = None
    p4_rows = np.zeros(n, dtype=bool)
    if p4_applicable:
        density = np.divide(road_km, road_area_km2,
                            out=np.full(n, np.nan, dtype=np.float64), where=road_area_km2 > 0)
        rural_valid = rural & np.isfinite(density) & (density >= 0)
        if rural_valid.any():
            threshold = float(np.quantile(density[rural_valid], float(road_seed["anchor_quantile"])))
            anchors = rural_valid & (density >= threshold)
            p4_rho = float(np.median(density[anchors]))
            if np.isfinite(p4_rho) and p4_rho > 0:
                p4_rows = rural_valid
                expected_road_km[p4_rows] = p4_rho * road_area_km2[p4_rows]
    p4_road = -np.expm1(-np.maximum(expected_road_km, 0.0)
                         / float(seed["road_exposure_scale_km"]))
    p_x4 = -np.expm1(-x4_values / float(seed["x4_witness_scale"]))
    p_road = (p4_road + p_x4) / 2.0
    probabilities = np.column_stack((p_road, p_building, p_poi))
    if np.any(~np.isfinite(probabilities)) or np.any((probabilities < 0) | (probabilities > 1)):
        raise ValueError("Sca5 component definedness probabilities must be normalized to [0, 1]")
    values, any_component_defined, pattern_mass = expected_definedness_composite(gaps, probabilities)
    defined = any_component_defined & component_valid.all(axis=1)
    values[~defined] = 0.0
    diagnostics = {
        "rows": n, "defined": int(defined.sum()),
        "undefined_reasons": {"missing_component_gap_estimate_or_zero_probability_mass": int((~defined).sum())},
        "component_order": ["R", "B", "POI"],
        "component_gap_sources": gap_sources,
        "component_gap_distribution": {"R": quantiles(gaps[component_valid[:, 0], 0]),
                                       "B": quantiles(gaps[component_valid[:, 1], 1]),
                                       "POI": quantiles(gaps[component_valid[:, 2], 2])},
        "component_definedness_probability": {"R": quantiles(probabilities[:, 0]),
                                              "B": quantiles(probabilities[:, 1]),
                                              "POI": quantiles(probabilities[:, 2])},
        "probability_inputs": {
            "facility": "fixed arithmetic mean of P2 Poisson nonempty probability, scaled P3 school-witness probability, and registered X1 soft probability",
            "building": "scaled Poisson nonempty proxy from Sca3 exclusive OSM/Esri centroid-assigned footprint count",
            "road": "mean of scaled P4/observed named-road-km proxy and scaled X4 witness proxy",
            "establishments": "scaled Poisson nonempty proxy from Sca3 non-Microsoft provided-place count; mean with facility proxy",
            "assumption": "fixed convex probability mixtures; component events are not asserted independent, and probabilities are uncalibrated proxies, never hard flags",
            "fixed_scales": {"school_witness_count": float(seed["school_witness_scale"]),
                             "building_witness_count": float(seed["building_witness_scale"]),
                             "place_witness_count": float(seed["place_witness_scale"]),
                             "road_exposure_km": float(seed["road_exposure_scale_km"]),
                             "X4_witness_count": float(seed["x4_witness_scale"])},
            "P4_anchor_density_km_per_km2": p4_rho,
            "P4_expected_road_rows": int(p4_rows.sum()),
            "X1_probability_used": True,
            "P3_soft_count_used": True,
        },
        "pattern_probability_mass": {"min": float(pattern_mass.sum(axis=1).min()),
                                     "max": float(pattern_mass.sum(axis=1).max()),
                                     "all_eight_patterns_enumerated": True},
        "composite_formula": "sum_{nonempty d in {0,1}^3} P(d)*mean(g_c for d_c=1) / P(any d_c=1)",
        "gap_composite": "R and B use the mean of their defined gap-channel idea outputs; POI averages available F and Est gap-channel means",
        "no_hard_definedness_flags": True,
        "no_labels_or_reference_layers": True,
    }
    inputs.accessed.setdefault("caches", []).append(cache_path.name)
    return IdeaOutput("Sca5", inputs.region, list(inputs.geoids),
                      [Emission("C", "covariate", values.astype(np.float64), defined)], diagnostics)
