"""Physical/normative priors P1 and P3 from rule-clean provided layers only."""
from __future__ import annotations

import collections
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import shapely

from bias60.contracts import Emission, IdeaOutput, clip_one_minus_ratio
from bias60.ideas import implements
from bias60.ideas.g import TRANSFORMER, _metric_context, _open_layer
from bias60.ideas._common import quantiles

_ALLOWED_BUILDINGS = {"openstreetmap", "osm", "esri"}
_SCHOOL_CLASSES = {"education", "school"}
_NONRES = {"commercial", "industrial", "education", "government", "institutional",
           "transportation", "agricultural", "warehouse", "office", "retail", "public",
           "religious", "school", "hospital", "civic"}
CACHE_VERSION = 2


def _rule_clean_building(sources: Any) -> bool:
    names = {str(s.get("dataset") or "").strip().casefold()
             for s in (sources or []) if isinstance(s, dict)}
    names.discard("")
    return bool(names) and names.issubset(_ALLOWED_BUILDINGS) and bool(names & _ALLOWED_BUILDINGS)


def _rule_clean_poi(sources: Any) -> bool:
    return not any("microsoft" in str(s.get("dataset") or "").casefold()
                   for s in (sources or []) if isinstance(s, dict))


def _source_rows_meta(parquet, region: str, name: str) -> dict[str, Any]:
    return {"region": region, "layer": name, "rows_total": int(parquet.metadata.num_rows),
            "reader_version": CACHE_VERSION}


def _assign_points(points, tree, n_tracts: int):
    indices = np.full(len(points), -1, dtype=np.int32)
    left, right = tree.query(points, predicate="within")
    indices[left] = right
    return indices


def _building_arrays(inputs: Any, seed: dict[str, Any]):
    """Stream one provided building layer; cache rule-clean centroid/area/floor attributes."""
    floor_seed = inputs.config["seeds"]["P1"]
    shape_seed = inputs.config["seeds"]["P3"]
    cache_dir = inputs.workspace / inputs.config["inputs"]["ideas_cache_dir"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{inputs.region}-p-physical-buildings-v1.npz"
    columns = ["sources", "class", "height", "num_floors", "geometry"]
    parquet = _open_layer(inputs, "buildings", columns)
    if cache_path.is_file():
        z = np.load(cache_path, allow_pickle=False)
        meta = json.loads(str(z["metadata_json"]))
        if meta.get("rows_total") == parquet.metadata.num_rows and meta.get("reader_version") == CACHE_VERSION:
            inputs.accessed["caches"].append(cache_path.name)
            return (z["tract"].astype(np.int32), z["x"].astype(np.float64), z["y"].astype(np.float64),
                    z["area"].astype(np.float64), z["floor_area"].astype(np.float64),
                    z["class_code"].astype(np.int16), z["candidate_shape"].astype(bool), meta)
    tracts, tree = _metric_context(inputs)
    tract_ids: list[int] = []; xs: list[float] = []; ys: list[float] = []
    areas_out: list[float] = []; floor_out: list[float] = []; classes_out: list[int] = []
    candidates_out: list[bool] = []; n_scanned = n_allowed = n_excluded = n_invalid = 0
    class_names = sorted(_NONRES | {"residential", "unknown", "other", ""})
    class_ids = {name: i for i, name in enumerate(class_names)}
    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=columns)
        sources = table["sources"].to_pylist(); classes = table["class"].to_pylist()
        heights = table["height"].to_pylist(); floors = table["num_floors"].to_pylist()
        wkbs = table["geometry"].to_pylist(); n_scanned += len(wkbs)
        keep = [i for i, src in enumerate(sources) if _rule_clean_building(src) and wkbs[i] is not None]
        n_allowed += len(keep)
        n_excluded += sum(1 for src in sources if any("microsoft" in str(s.get("dataset") or "").casefold()
                                                       for s in (src or []) if isinstance(s, dict)))
        if not keep:
            continue
        geom = shapely.from_wkb([wkbs[i] for i in keep])
        metric = shapely.transform(geom, TRANSFORMER.transform, interleaved=False)
        valid = shapely.is_valid(metric) & ~shapely.is_empty(metric)
        area = shapely.area(metric); perimeter = shapely.length(metric)
        hull_area = shapely.area(shapely.convex_hull(metric)); centers = shapely.centroid(metric)
        left, right = tree.query(centers, predicate="within")
        for feature_i, tract_i in zip(left.tolist(), right.tolist()):
            row_i = keep[feature_i]; a = float(area[feature_i]); per = float(perimeter[feature_i])
            hull = float(hull_area[feature_i]); cls = str(classes[row_i] or "").casefold()
            if not valid[feature_i] or not np.isfinite(a) or a <= 0:
                n_invalid += 1; continue
            # Freeze floor conversions before computing E: explicit positive floor count; otherwise
            # height / 3.5m rounded, capped at 20; then the registered class default.
            nf = floors[row_i]; ht = heights[row_i]
            if nf is not None and np.isfinite(float(nf)) and float(nf) > 0:
                stories = min(20.0, max(1.0, float(nf)))
            elif ht is not None and np.isfinite(float(ht)) and float(ht) > 0:
                stories = min(float(floor_seed["maximum_inferred_floors"]),
                              max(1.0, round(float(ht) / float(floor_seed["floor_height_m"]))))
            else:
                stories = float(floor_seed["default_floors"].get(cls, floor_seed["default_floors"].get("unknown_large", 1)))
            nonres = cls in _NONRES or (cls in {"", "unknown"} and a >= float(floor_seed["unknown_large_min_area_m2"]))
            floor_area = a * stories if nonres else 0.0
            circularity = 4.0 * math.pi * a / (per * per) if per > 0 else 0.0
            solidity = a / hull if hull > 0 else 0.0
            candidate = _school_shape_candidate(area_m2=a, circularity=circularity, solidity=solidity,
                         building_class=cls, floors=nf, height_m=ht, seed=shape_seed)
            xy = centers[feature_i].coords[0]
            tract_ids.append(int(tract_i)); xs.append(float(xy[0])); ys.append(float(xy[1]))
            areas_out.append(a); floor_out.append(floor_area); classes_out.append(class_ids.get(cls, class_ids.get("other", 0)))
            candidates_out.append(candidate)
    arrays = (np.asarray(tract_ids, dtype=np.int32), np.asarray(xs, dtype=np.float64),
              np.asarray(ys, dtype=np.float64), np.asarray(areas_out, dtype=np.float64),
              np.asarray(floor_out, dtype=np.float64), np.asarray(classes_out, dtype=np.int16),
              np.asarray(candidates_out, dtype=bool))
    meta = {**_source_rows_meta(parquet, inputs.region, "buildings"), "rows_scanned": n_scanned,
            "rule_clean_rows": n_allowed, "microsoft_membership_rows_excluded_before_wkb": n_excluded,
            "assigned_valid_rows": len(tract_ids), "invalid_rows": n_invalid,
            "source_rule": "exclusive OSM/Esri; mixed and Microsoft memberships dropped before WKB decode",
            "crs": "EPSG:5070", "floor_area_rules_frozen": True}
    np.savez_compressed(cache_path, tract=arrays[0], x=arrays[1], y=arrays[2], area=arrays[3],
                        floor_area=arrays[4], class_code=arrays[5], candidate_shape=arrays[6],
                        metadata_json=np.array(json.dumps(meta, sort_keys=True)))
    return (*arrays, meta)


def _school_shape_candidate(*, area_m2: float, circularity: float, solidity: float,
                            building_class: str, floors: Any, height_m: Any,
                            seed: dict[str, Any]) -> bool:
    """Frozen P3 shape filters; separately tested false-positive gates."""
    if not (seed["school_area_min_m2"] <= area_m2 <= seed["school_area_max_m2"]):
        return False
    if building_class in {"commercial", "industrial", "warehouse", "retail"}:
        return False
    if circularity < seed["minimum_circularity"] or solidity < seed["minimum_solidity"]:
        return False
    h = float(height_m) if height_m is not None and np.isfinite(float(height_m)) else np.nan
    nf = float(floors) if floors is not None and np.isfinite(float(floors)) else np.nan
    if np.isfinite(h) and h > seed["maximum_height_m"]:
        return False
    if np.isfinite(nf) and nf > seed["maximum_floors"]:
        return False
    return True


def _place_counts(inputs: Any, seed: dict[str, Any]):
    cache_dir = inputs.workspace / inputs.config["inputs"]["ideas_cache_dir"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{inputs.region}-p-physical-places-v1.npz"
    cols = ["sources", "categories", "geometry"]
    parquet = _open_layer(inputs, "pois", cols)
    if cache_path.is_file():
        z = np.load(cache_path, allow_pickle=False); meta = json.loads(str(z["metadata_json"]))
        if meta.get("rows_total") == parquet.metadata.num_rows and meta.get("reader_version") == CACHE_VERSION:
            inputs.accessed["caches"].append(cache_path.name)
            return z["places"].astype(np.float64), z["schools"].astype(np.float64), z["richness"].astype(np.float64), meta
    tracts, tree = _metric_context(inputs); n = len(inputs.geoids)
    place = np.zeros(n); schools = np.zeros(n); categories = [set() for _ in range(n)]
    scanned = allowed = excluded = assigned = 0
    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=cols)
        sources = table["sources"].to_pylist(); cats = table["categories"].to_pylist(); wkbs = table["geometry"].to_pylist()
        scanned += len(wkbs)
        keep = [i for i, src in enumerate(sources) if _rule_clean_poi(src) and wkbs[i] is not None]
        allowed += len(keep)
        excluded += sum(1 for src in sources if any("microsoft" in str(s.get("dataset") or "").casefold()
                                                     for s in (src or []) if isinstance(s, dict)))
        if not keep: continue
        geom = shapely.from_wkb([wkbs[i] for i in keep]); metric = shapely.transform(geom, TRANSFORMER.transform, interleaved=False)
        valid = shapely.is_valid(metric) & ~shapely.is_empty(metric)
        left, right = tree.query(metric, predicate="within")
        for feature_i, tract_i in zip(left.tolist(), right.tolist()):
            if not valid[feature_i]: continue
            src_i = keep[feature_i]; place[tract_i] += 1; assigned += 1
            cat = cats[src_i] if isinstance(cats[src_i], dict) else {}
            primary = str(cat.get("primary") or "").strip().casefold()
            if primary: categories[tract_i].add(primary)
            if primary in _SCHOOL_CLASSES or primary.startswith("school"):
                schools[tract_i] += 1
    richness = np.asarray([len(x) for x in categories], dtype=np.float64)
    meta = {**_source_rows_meta(parquet, inputs.region, "pois"), "rows_scanned": scanned,
            "rule_clean_rows": allowed, "microsoft_membership_rows_excluded": excluded,
            "assigned_points": assigned, "source_rule": "provided places excluding Microsoft memberships",
            "category_field": "categories.primary"}
    np.savez_compressed(cache_path, places=place, schools=schools, richness=richness,
                        metadata_json=np.array(json.dumps(meta, sort_keys=True)))
    return place, schools, richness, meta


def _collector_roads(inputs: Any, seed: dict[str, Any]):
    cols = ["class", "subclass", "geometry"]
    parquet = _open_layer(inputs, "roads", cols); geoms = []; rows = 0; n_named = 0
    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=cols); classes = table["class"].to_pylist()
        subclasses = table["subclass"].to_pylist(); wkbs = table["geometry"].to_pylist(); rows += len(wkbs)
        eligible = [i for i, cls in enumerate(classes) if wkbs[i] is not None and (
                    str(cls or "").casefold() in seed["collector_classes"]
                    or str(subclasses[i] or "").casefold() in seed["collector_subclasses"])]
        n_named += len(eligible)
        if eligible:
            metric = shapely.transform(shapely.from_wkb([wkbs[i] for i in eligible]),
                                       TRANSFORMER.transform, interleaved=False)
            valid = shapely.is_valid(metric) & ~shapely.is_empty(metric)
            geoms.extend(metric[valid].tolist())
    return np.asarray(geoms, dtype=object), {"rows_scanned": rows, "collector_candidates": n_named,
             "collector_rule": "provided road class/subclass preregistered in config", "crs": "EPSG:5070"}


def _void_area_150m(x: float, y: float, buckets: dict[tuple[int, int], list[tuple[float, float, float]]],
                    radius: float) -> float:
    cell = radius
    ix, iy = math.floor(x / cell), math.floor(y / cell)
    built = 0.0
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for px, py, area in buckets.get((ix + dx, iy + dy), []):
                if (px - x) ** 2 + (py - y) ** 2 <= radius ** 2:
                    built += area
    return max(0.0, math.pi * radius * radius - built)


def _building_buckets(x: np.ndarray, y: np.ndarray, area: np.ndarray, radius: float):
    buckets: dict[tuple[int, int], list[tuple[float, float, float]]] = collections.defaultdict(list)
    cell = radius
    for px, py, a in zip(x.tolist(), y.tolist(), area.tolist()):
        buckets[(math.floor(px / cell), math.floor(py / cell))].append((px, py, a))
    return buckets


@implements("P1")
def p1(inputs: Any) -> IdeaOutput:
    seed = inputs.config["seeds"]["P1"]
    tract, x, y, area, floor_area, classes, candidate, bmeta = _building_arrays(inputs, seed)
    places, _, richness, pmeta = _place_counts(inputs, seed)
    n = len(inputs.geoids); fa = np.bincount(tract, weights=floor_area, minlength=n)
    # Completeness anchors are frozen as the upper quartile of distinct primary-category richness
    # among tracts with positive non-residential floor area. No label or reference count participates.
    anchored = (fa > 0) & (richness > 0)
    if anchored.any():
        q = float(np.quantile(richness[anchored], seed["anchor_quantile"]))
        selected = anchored & (richness >= q)
        rates = places[selected] / fa[selected]
        rates = rates[np.isfinite(rates) & (rates > 0)]
        kappa = float(np.median(rates)) if rates.size else 0.0
    else:
        q, selected, kappa = 0.0, np.zeros(n, dtype=bool), 0.0
    expectation = fa * kappa
    gap, defined = clip_one_minus_ratio(places, expectation)
    return IdeaOutput("P1", inputs.region, inputs.geoids,
         [Emission("Est", "gap", gap.astype(np.float64), defined.astype(np.bool_))],
         {"rows": n, "defined": int(defined.sum()), "floor_area_m2": quantiles(fa),
          "place_count": quantiles(places), "anchor_category_richness_q": q,
          "anchor_tracts": int(selected.sum()), "kappa_places_per_floor_m2": kappa,
          "expected_establishments": quantiles(expectation[expectation > 0]),
          "gap_distribution": quantiles(gap[defined]), "building_reader": bmeta,
          "places_reader": pmeta, "floor_rule": "floors field; else round(height/3.5m); else frozen class default"})


@implements("P3")
def p3(inputs: Any) -> IdeaOutput:
    seed = inputs.config["seeds"]["P3"]
    tract, x, y, area, floor_area, class_ids, candidate, bmeta = _building_arrays(inputs, seed)
    places, schools, richness, pmeta = _place_counts(inputs, seed)
    roads, rmeta = _collector_roads(inputs, seed)
    road_tree = shapely.STRtree(roads) if roads.size else None
    bucket_map = _building_buckets(x, y, area, float(seed["void_radius_m"]))
    n = len(inputs.geoids); candidates_by_tract = np.zeros(n, dtype=np.float64)
    candidate_idx = np.flatnonzero(candidate)
    passed_void = passed_road = 0
    candidate_points = shapely.points(x[candidate_idx], y[candidate_idx]) if candidate_idx.size else np.asarray([], dtype=object)
    for feature_i in candidate_idx.tolist():
        void = _void_area_150m(float(x[feature_i]), float(y[feature_i]), bucket_map, float(seed["void_radius_m"]))
        if void < float(seed["minimum_void_area_m2"]): continue
        passed_void += 1
        if road_tree is None: continue
        point = shapely.points(float(x[feature_i]), float(y[feature_i]))
        near = road_tree.query(point.buffer(float(seed["collector_distance_m"])), predicate="intersects")
        if len(near) == 0: continue
        passed_road += 1
        candidates_by_tract[int(tract[feature_i])] += 1
    # The witness is meaningful only when the provided, non-Microsoft school place count is zero.
    witness = np.where(schools == 0, candidates_by_tract, 0.0).astype(np.float64)
    defined = np.ones(n, dtype=np.bool_)
    return IdeaOutput("P3", inputs.region, inputs.geoids,
        [Emission("F", "covariate", witness, defined)],
        {"rows": n, "candidate_buildings": int(candidate.sum()), "passed_void_ring": passed_void,
         "passed_collector_adjacency": passed_road, "tracts_with_unwitnessed_school_candidate": int((witness > 0).sum()),
         "defined": n, "void_ring_rule": "150m disk around candidate centroid minus OSM/Esri footprint area from centroids in disk; require >= 20,000 m2",
         "collector_distance_m": seed["collector_distance_m"], "building_reader": bmeta,
         "places_reader": pmeta, "road_reader": rmeta,
         "note": "void area is a conservative centroid-footprint approximation; candidate output remains a covariate, not a school label"})
