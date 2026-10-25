"""Geometry estimators G1-G5 using only provided Overture and ACS inputs.

Microsoft-sourced building geometries are never decoded or used: building-derived ideas retain
only rows whose source memberships are exclusively OpenStreetMap/Esri. See per-reader diagnostics.
"""
from __future__ import annotations

import collections
import json
import math
from typing import Any

import numpy as np
import shapely
from pyproj import Transformer

from bias60.contracts import Emission, IdeaOutput
from bias60.ideas import implements

NAMED_CLASSES = {"motorway", "trunk", "primary", "secondary"}
BUILDING_SOURCES = {"openstreetmap", "osm", "esri"}
FORBIDDEN_SOURCE_MARKERS = {"microsoft"}
TRANSFORMER = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)


def _metric_context(inputs: Any):
    tracts = shapely.transform(inputs.tract_geometries(), TRANSFORMER.transform, interleaved=False)
    tree = shapely.STRtree(tracts)
    return tracts, tree


def _record_columns(inputs: Any, layer: str, columns: list[str]) -> None:
    inputs.accessed["parquet_columns"].append(
        f"{inputs.region}-overture-{layer}.parquet:{','.join(columns)}")


def _open_layer(inputs: Any, layer: str, columns: list[str]):
    import pyarrow.parquet as pq
    path = inputs.layer_path(layer)
    parquet = pq.ParquetFile(inputs.filesystem.open_input_file(path))
    missing = set(columns) - set(parquet.schema_arrow.names)
    if missing:
        raise ValueError(f"provided {layer} layer lacks {sorted(missing)}")
    _record_columns(inputs, layer, columns)
    return parquet


def _named_road_rows(inputs: Any, columns: list[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    parquet = _open_layer(inputs, "roads", columns)
    rows: list[dict[str, Any]] = []
    scanned = named = 0
    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=columns)
        records = table.to_pylist()
        scanned += len(records)
        for row in records:
            if row.get("class") not in NAMED_CLASSES:
                continue
            named += 1
            if row.get("geometry") is None:
                raise ValueError("null geometry in named-class provided road segment")
            row["metric_geometry"] = shapely.transform(
                shapely.from_wkb(row["geometry"]), TRANSFORMER.transform, interleaved=False)
            rows.append(row)
    return rows, {"rows_scanned": scanned, "named_rows": named}


def _road_length_summary(inputs: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    columns = ["id", "class", "geometry"]
    rows, meta = _named_road_rows(inputs, columns)
    tracts, tree = _metric_context(inputs)
    buffers = shapely.buffer(tracts, 25.0)
    buffer_tree = shapely.STRtree(buffers)
    boundaries = shapely.buffer(shapely.boundary(tracts), 25.0)
    boundary_tree = shapely.STRtree(boundaries)
    total = np.zeros(len(tracts), dtype=np.float64)
    boundary = np.zeros(len(tracts), dtype=np.float64)
    for start in range(0, len(rows), 2048):
        lines = np.asarray([row["metric_geometry"] for row in rows[start:start + 2048]], dtype=object)
        pairs = buffer_tree.query(lines, predicate="intersects")
        if pairs.size:
            line_i, tract_i = pairs
            clipped = shapely.intersection(lines[line_i], buffers[tract_i])
            lengths = shapely.length(clipped)
            valid = np.isfinite(lengths) & (lengths > 0)
            total += np.bincount(tract_i[valid], weights=lengths[valid], minlength=len(tracts))
        pairs = boundary_tree.query(lines, predicate="intersects")
        if pairs.size:
            line_i, tract_i = pairs
            clipped = shapely.intersection(lines[line_i], boundaries[tract_i])
            lengths = shapely.length(clipped)
            valid = np.isfinite(lengths) & (lengths > 0)
            boundary += np.bincount(tract_i[valid], weights=lengths[valid], minlength=len(tracts))
    return total, boundary, tracts, {**meta, "boundary_buffer_m": 25.0,
                                     "crs": "EPSG:5070", "assignment": "line lengths clipped to tract plus 25m boundary buffer"}


@implements("G1")
def g1(inputs: Any) -> IdeaOutput:
    total, near, _, meta = _road_length_summary(inputs)
    defined = total > 0.0
    share = np.zeros(total.shape, dtype=np.float64)
    share[defined] = np.clip(near[defined] / total[defined], 0.0, 1.0)
    gap = 0.5 * share
    return IdeaOutput("G1", inputs.region, inputs.geoids,
                      [Emission("R", "gap", gap, defined),
                       Emission("R", "covariate", share, defined)],
                      {**meta, "defined_rows": int(defined.sum()),
                       "near_boundary_length_m": float(near.sum()),
                       "denominator_length_m": float(total.sum()),
                       "side_prior": 0.5, "share_distribution": _quantiles(share[defined])})


def _primary_name(names: Any) -> str | None:
    if isinstance(names, dict) and isinstance(names.get("primary"), str):
        value = names["primary"].strip().casefold()
        return value or None
    return None


def _route_keys(routes: Any) -> frozenset[str]:
    keys: set[str] = set()
    for route in routes or []:
        if not isinstance(route, dict):
            continue
        ref = route.get("ref")
        network = route.get("network")
        name = route.get("name")
        if isinstance(ref, str) and ref.strip():
            keys.add(f"{str(network or '').strip().casefold()}:{ref.strip().casefold()}")
        if isinstance(name, str) and name.strip():
            keys.add(f"name:{name.strip().casefold()}")
    return frozenset(keys)


def _travel_heading(restrictions: Any) -> str | None:
    denied: set[str] = set()
    for row in restrictions or []:
        if isinstance(row, dict) and str(row.get("access_type", "")).casefold() == "denied":
            heading = ((row.get("when") or {}).get("heading") or "").casefold()
            if heading in {"forward", "backward"}:
                denied.add(heading)
    if denied == {"backward"}:
        return "forward"
    if denied == {"forward"}:
        return "backward"
    return None


def _bearing(line: Any) -> float | None:
    coords = np.asarray(line.coords, dtype=np.float64)
    if len(coords) < 2:
        return None
    delta = coords[-1] - coords[0]
    if np.linalg.norm(delta) == 0:
        return None
    return math.atan2(float(delta[1]), float(delta[0]))


def _is_ramp_or_frontage(row: dict[str, Any]) -> bool:
    values = [str(row.get("subclass") or ""), _primary_name(row.get("names")) or ""]
    values.extend(str(route.get("name") or "") for route in row.get("routes") or [] if isinstance(route, dict))
    text = " ".join(values).casefold()
    return any(token in text for token in ("ramp", "frontage", "service road", "motorway link", "highway link"))


def pair_dual_carriageways(rows: list[dict[str, Any]], *, max_distance_m: float = 60.0,
                           max_heading_deviation_deg: float = 30.0) -> list[tuple[int, int]]:
    """Greedy deterministic one-to-one pairing with explicit one-way and ramp/frontage guards."""
    eligible = [i for i, row in enumerate(rows) if row.get("travel_heading") in {"forward", "backward"}
                and not _is_ramp_or_frontage(row)]
    if len(eligible) < 2:
        return []
    geoms = np.asarray([rows[i]["metric_geometry"] for i in eligible], dtype=object)
    tree = shapely.STRtree(geoms)
    pairs = tree.query(geoms, predicate="dwithin", distance=max_distance_m)
    candidates: list[tuple[float, int, int]] = []
    cosine_limit = math.cos(math.radians(180.0 - max_heading_deviation_deg))
    for left_local, right_local in zip(pairs[0].tolist(), pairs[1].tolist()):
        if left_local >= right_local:
            continue
        i, j = eligible[left_local], eligible[right_local]
        a, b = rows[i], rows[j]
        if a["travel_heading"] == b["travel_heading"]:
            continue
        name_a, name_b = _primary_name(a.get("names")), _primary_name(b.get("names"))
        same_name = bool(name_a and name_a == name_b)
        same_route = bool(_route_keys(a.get("routes")) & _route_keys(b.get("routes")))
        if not (same_name or same_route):
            continue
        ba, bb = _bearing(a["metric_geometry"]), _bearing(b["metric_geometry"])
        if ba is None or bb is None:
            continue
        if a["travel_heading"] == "backward":
            ba += math.pi
        if b["travel_heading"] == "backward":
            bb += math.pi
        if math.cos(ba - bb) > cosine_limit:
            continue
        hausdorff = float(shapely.hausdorff_distance(a["metric_geometry"], b["metric_geometry"]))
        if not np.isfinite(hausdorff) or hausdorff >= max_distance_m:
            continue
        candidates.append((hausdorff, i, j))
    selected: list[tuple[int, int]] = []
    used: set[int] = set()
    for _, i, j in sorted(candidates):
        if i not in used and j not in used:
            used.update((i, j))
            selected.append((i, j))
    return selected


@implements("G2")
def g2(inputs: Any) -> IdeaOutput:
    cols = ["id", "class", "subclass", "names", "routes", "access_restrictions", "geometry"]
    rows, meta = _named_road_rows(inputs, cols)
    for row in rows:
        row["travel_heading"] = _travel_heading(row.get("access_restrictions"))
    pairs = pair_dual_carriageways(rows)
    tracts, tree = _metric_context(inputs)
    all_lengths = np.zeros(len(tracts), dtype=np.float64)
    paired_lengths = np.zeros(len(tracts), dtype=np.float64)
    pair_lines = [rows[i]["metric_geometry"] for pair in pairs for i in pair]
    for start in range(0, len(rows), 2048):
        lines = np.asarray([row["metric_geometry"] for row in rows[start:start + 2048]], dtype=object)
        qi, ti = tree.query(lines, predicate="intersects")
        if qi.size:
            lengths = shapely.length(shapely.intersection(lines[qi], tracts[ti]))
            valid = np.isfinite(lengths) & (lengths > 0)
            all_lengths += np.bincount(ti[valid], weights=lengths[valid], minlength=len(tracts))
    if pair_lines:
        for start in range(0, len(pair_lines), 2048):
            lines = np.asarray(pair_lines[start:start + 2048], dtype=object)
            qi, ti = tree.query(lines, predicate="intersects")
            if qi.size:
                lengths = shapely.length(shapely.intersection(lines[qi], tracts[ti]))
                valid = np.isfinite(lengths) & (lengths > 0)
                paired_lengths += np.bincount(ti[valid], weights=lengths[valid], minlength=len(tracts))
    defined = all_lengths > 0
    share = np.zeros(all_lengths.shape, dtype=np.float64)
    share[defined] = np.clip(paired_lengths[defined] / all_lengths[defined], 0.0, 1.0)
    return IdeaOutput("G2", inputs.region, inputs.geoids,
                      [Emission("R", "gap", share, defined)],
                      {**meta, "defined_rows": int(defined.sum()), "candidate_pairs_greedy": len(pairs),
                       "paired_length_m": float(paired_lengths.sum()), "named_length_m": float(all_lengths.sum()),
                       "paired_share_distribution": _quantiles(share[defined]),
                       "pairing_rule": "one-to-one; dwithin/Hausdorff<60m; opposite geometry bearing and explicit one-way headings; same primary name or route; ramp/frontage/link excluded"})


def _allowed_building(sources: Any) -> bool:
    datasets = {str(s.get("dataset") or "").strip().casefold() for s in (sources or []) if isinstance(s, dict)}
    datasets.discard("")
    return bool(datasets) and not any(any(marker in d for marker in FORBIDDEN_SOURCE_MARKERS) for d in datasets) \
        and datasets.issubset(BUILDING_SOURCES) and bool(datasets & BUILDING_SOURCES)


def _allowed_poi(sources: Any) -> bool:
    return not any("microsoft" in str(s.get("dataset") or "").casefold()
                   for s in (sources or []) if isinstance(s, dict))


def _load_buildings(inputs: Any, *, area_limits: bool = False) -> tuple[list[list[float]], list[list[tuple[float, float]]], dict[str, Any]]:
    """Load only OSM/Esri-only footprints; Microsoft membership rows are discarded before WKB decode."""
    cols = ["sources", "class", "geometry"]
    parquet = _open_layer(inputs, "buildings", cols)
    cache_dir = inputs.workspace / inputs.config["inputs"]["ideas_cache_dir"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{inputs.region}-g-allowed-building-centroids.npz"
    if cache_path.is_file():
        cached = np.load(cache_path, allow_pickle=False)
        cache_meta = json.loads(str(cached["metadata_json"]))
        if (cache_meta.get("rows_total") == parquet.metadata.num_rows
                and cache_meta.get("region") == inputs.region and cache_meta.get("reader_version") == 1):
            tract_ids = cached["tract_index"]
            areas = cached["area_m2"]
            xs, ys = cached["x_m"], cached["y_m"]
            eligible = cached["area_eligible"]
            by_area: list[list[float]] = [[] for _ in inputs.geoids]
            by_centroid: list[list[tuple[float, float]]] = [[] for _ in inputs.geoids]
            for j, tract_i in enumerate(tract_ids.tolist()):
                if not area_limits or eligible[j]:
                    by_area[tract_i].append(float(areas[j]))
                    by_centroid[tract_i].append((float(xs[j]), float(ys[j])))
            inputs.accessed["caches"].append(cache_path.name)
            return by_area, by_centroid, {k: v for k, v in cache_meta.items() if k != "reader_version"}
    tracts, tree = _metric_context(inputs)
    areas_by_tract: list[list[float]] = [[] for _ in inputs.geoids]
    centroids_by_tract: list[list[tuple[float, float]]] = [[] for _ in inputs.geoids]
    scanned = allowed = assigned = excluded_microsoft = invalid = 0
    tract_ids: list[int] = []; area_values: list[float] = []; x_values: list[float] = []; y_values: list[float] = []; area_eligible: list[bool] = []
    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=cols)
        srcs = table["sources"].to_pylist(); classes = table["class"].to_pylist()
        wkbs = table["geometry"].to_pylist(); scanned += len(wkbs)
        eligible = [i for i in range(len(wkbs)) if _allowed_building(srcs[i]) and wkbs[i] is not None]
        excluded_microsoft += sum(1 for i, src in enumerate(srcs)
                                 if any("microsoft" in str(s.get("dataset") or "").casefold()
                                        for s in (src or []) if isinstance(s, dict)))
        if not eligible:
            continue
        allowed += len(eligible)
        geometries = shapely.from_wkb([wkbs[i] for i in eligible])
        metric = shapely.transform(geometries, TRANSFORMER.transform, interleaved=False)
        valid = shapely.is_valid(metric) & ~shapely.is_empty(metric)
        areas = shapely.area(metric); centers = shapely.centroid(metric)
        qi, ti = tree.query(centers, predicate="within")
        for feature_id, tract_id in zip(qi.tolist(), ti.tolist()):
            src_row = eligible[feature_id]
            area = float(areas[feature_id])
            if not valid[feature_id] or not np.isfinite(area) or area <= 0:
                invalid += 1; continue
            cls = str(classes[src_row] or "").casefold()
            area_values.append(area)
            xy = centers[feature_id].coords[0]
            x_values.append(float(xy[0])); y_values.append(float(xy[1])); tract_ids.append(int(tract_id))
            area_eligible.append((not cls or any(word in cls for word in ("residential", "unknown", "building"))) and area < 5000.0)
            if not area_limits or area_eligible[-1]:
                areas_by_tract[tract_id].append(area)
                centroids_by_tract[tract_id].append((float(xy[0]), float(xy[1])))
            assigned += 1
    meta = {
        "rows_scanned": scanned, "osm_esri_only_rows": allowed, "microsoft_rows_excluded": excluded_microsoft,
        "features_assigned": assigned, "invalid_features": invalid, "area_crs": "EPSG:5070",
        "source_filter": "exclusive OpenStreetMap/OSM/Esri source set; Microsoft and mixed-source rows excluded",
        "assignment": "footprint centroid strictly within one official tract"}
    np.savez_compressed(cache_path, tract_index=np.asarray(tract_ids, dtype=np.int32),
                        area_m2=np.asarray(area_values, dtype=np.float64), x_m=np.asarray(x_values, dtype=np.float64),
                        y_m=np.asarray(y_values, dtype=np.float64), area_eligible=np.asarray(area_eligible, dtype=bool),
                        metadata_json=np.array(json.dumps({**meta, "rows_total": parquet.metadata.num_rows,
                                                          "reader_version": 1}, sort_keys=True)))
    return areas_by_tract, centroids_by_tract, meta


def _peer_area_references(inputs: Any, areas: list[list[float]]) -> tuple[np.ndarray, dict[str, Any]]:
    strata = inputs.strata(["STATEFP", "COUNTYFP", "ruca_primary"])
    state = [str(x or "") for x in strata["STATEFP"]]
    county = [str(x or "") for x in strata["COUNTYFP"]]
    ruca = [str(x if x is not None else "missing") for x in strata["ruca_primary"]]
    own_medians = np.asarray([float(np.median(x)) if x else np.nan for x in areas])
    group_members: dict[tuple[str, str, str], list[int]] = collections.defaultdict(list)
    ruca_members: dict[str, list[int]] = collections.defaultdict(list)
    for i, value in enumerate(own_medians):
        if np.isfinite(value):
            group_members[(state[i], county[i], ruca[i])].append(i)
            ruca_members[ruca[i]].append(i)
    refs = np.full(len(areas), np.nan, dtype=np.float64)
    for i in range(len(areas)):
        peers = [own_medians[j] for j in group_members.get((state[i], county[i], ruca[i]), []) if j != i]
        if peers:
            refs[i] = float(np.median(peers))
        else:
            peers = [own_medians[j] for j in ruca_members.get(ruca[i], []) if j != i]
            if peers:
                refs[i] = float(np.median(peers))
    regional = float(np.nanmedian(own_medians)) if np.isfinite(own_medians).any() else np.nan
    refs[~np.isfinite(refs)] = regional
    return refs, {"peer_reference": "leave-one-tract-out median of tract median footprint area within STATEFP/COUNTYFP/RUCA; fallback same RUCA then regional", "peer_tracts": int(np.isfinite(own_medians).sum()), "reference_median_m2": float(np.nanmedian(refs)) if np.isfinite(refs).any() else None, "frozen_before_deficit": True}


@implements("G3")
def g3(inputs: Any) -> IdeaOutput:
    areas, _, build_meta = _load_buildings(inputs, area_limits=True)
    references, peer_meta = _peer_area_references(inputs, areas)
    deficit = np.zeros(len(areas), dtype=np.float64)
    candidate_n = np.zeros(len(areas), dtype=np.float64)
    defined = np.isfinite(references) & (references > 0)
    for i, values in enumerate(areas):
        if not values or not defined[i]:
            defined[i] = False; continue
        ratios = np.minimum(50.0, np.maximum(1.0, np.asarray(values) / references[i]))
        candidate_n[i] = len(ratios)
        deficit[i] = np.maximum(ratios - 1.0, 0.0).sum()
    defined &= candidate_n > 0
    gap = np.zeros(len(areas), dtype=np.float64)
    gap[defined] = deficit[defined] / (candidate_n[defined] + deficit[defined])
    return IdeaOutput("G3", inputs.region, inputs.geoids,
                      [Emission("B", "gap", gap, defined)],
                      {**build_meta, **peer_meta, "residential_or_unknown_area_limit_m2": 5000.0,
                       "ratio_cap": 50.0, "defined_rows": int(defined.sum()),
                       "deficit_distribution": _quantiles(gap[defined]),
                       "microsoft_geometry_or_reference_used": False})


def _load_poi_masks(inputs: Any) -> tuple[list[set[tuple[int, int]]], dict[str, Any]]:
    cols = ["sources", "categories", "geometry"]
    parquet = _open_layer(inputs, "pois", cols)
    tracts, tree = _metric_context(inputs)
    masks: list[set[tuple[int, int]]] = [set() for _ in inputs.geoids]
    scanned = excluded_microsoft = assigned = 0
    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=cols)
        sources = table["sources"].to_pylist(); cats = table["categories"].to_pylist(); wkbs = table["geometry"].to_pylist()
        scanned += len(wkbs)
        eligible = []
        for i, src in enumerate(sources):
            if not _allowed_poi(src):
                excluded_microsoft += 1; continue
            cat = str((cats[i] or {}).get("primary") or "").casefold()
            if cat not in {"park", "school"} or wkbs[i] is None:
                continue
            eligible.append(i)
        if not eligible:
            continue
        geom = shapely.from_wkb([wkbs[i] for i in eligible])
        metric = shapely.transform(geom, TRANSFORMER.transform, interleaved=False)
        qi, ti = tree.query(metric, predicate="within")
        for k, tract_i in zip(qi.tolist(), ti.tolist()):
            x, y = metric[k].coords[0]
            masks[tract_i].add(_grid_cell(float(x), float(y))); assigned += 1
    return masks, {"rows_scanned": scanned, "park_school_points": assigned,
                   "microsoft_source_rows_excluded": excluded_microsoft}


def _load_water_masks(inputs: Any) -> tuple[list[Any], dict[str, Any]]:
    cols = ["class", "subtype", "geometry"]
    parquet = _open_layer(inputs, "infrastructure", cols)
    # This package layer is OSM infrastructure; use only explicit water-related classes/subtypes.
    water = []
    scanned = 0
    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=cols)
        classes = table["class"].to_pylist(); subtypes = table["subtype"].to_pylist(); wkbs = table["geometry"].to_pylist()
        scanned += len(wkbs)
        for cls, sub, wkb in zip(classes, subtypes, wkbs):
            text = f"{cls or ''} {sub or ''}".casefold()
            if wkb is None or not any(x in text for x in ("water", "river", "lake", "reservoir", "wetland", "canal", "dam")):
                continue
            geom = shapely.transform(shapely.from_wkb(wkb), TRANSFORMER.transform, interleaved=False)
            water.append(geom.buffer(100.0))
    return water, {"rows_scanned": scanned, "water_features_buffered": len(water), "mask_distance_m": 100.0}


def _grid_cell(x: float, y: float, size: float = 100.0) -> tuple[int, int]:
    return (math.floor(x / size), math.floor(y / size))


def _inside_tract(x: float, y: float, geom: Any) -> bool:
    return bool(geom.covers(shapely.Point(x, y)))


def _ring_voids(occupied: collections.Counter[tuple[int, int]], tract: Any,
                mask_cells: set[tuple[int, int]], water_geoms: list[Any], size: float = 100.0) -> tuple[int, int, float]:
    candidates: set[tuple[int, int]] = set()
    for ix, iy in occupied:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                candidates.add((ix + dx, iy + dy))
    if not candidates:
        return 0, 0, 0.0
    cells = np.asarray(sorted(candidates), dtype=np.int64)
    xs, ys = (cells[:, 0] + 0.5) * size, (cells[:, 1] + 0.5) * size
    points = shapely.points(xs, ys)
    keep = shapely.covers(tract, points)
    if mask_cells:
        keep &= np.fromiter((tuple(cell) not in mask_cells for cell in cells), dtype=bool, count=len(cells))
    water_tree = (water_geoms if hasattr(water_geoms, "query") else
                  shapely.STRtree(water_geoms) if water_geoms else None)
    if water_tree is not None and keep.any():
        water_pairs = water_tree.query(points[keep], predicate="within")
        if water_pairs.size:
            blocked = np.zeros(int(keep.sum()), dtype=bool)
            blocked[water_pairs[0]] = True
            keep_indices = np.flatnonzero(keep)
            keep[keep_indices[blocked]] = False
    selected = cells[keep]
    if not len(selected):
        return 0, 0, 0.0
    ring = np.zeros(len(selected), dtype=np.int8)
    local_total = np.zeros(len(selected), dtype=np.float64)
    local_n = np.zeros(len(selected), dtype=np.int8)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if (dx, dy) == (0, 0):
                continue
            values = np.fromiter((occupied.get((int(x) + dx, int(y) + dy), 0)
                                  for x, y in selected), dtype=np.int32, count=len(selected))
            ring += (values > 0)
            local_total += values
            local_n += (values > 0)
    should_mask = ring >= 5
    selected = selected[should_mask]
    local_total, local_n = local_total[should_mask], local_n[should_mask]
    if not len(selected):
        return 0, 0, 0.0
    void_mask = np.fromiter((occupied.get((int(x), int(y)), 0) == 0 for x, y in selected),
                            dtype=bool, count=len(selected))
    expected = float(np.sum(local_total[void_mask] / np.maximum(local_n[void_mask], 1)))
    return int(len(selected)), int(void_mask.sum()), expected


@implements("G4")
def g4(inputs: Any) -> IdeaOutput:
    _, building_centroids, bmeta = _load_buildings(inputs)
    tracts, _ = _metric_context(inputs)
    poi_masks, pmeta = _load_poi_masks(inputs)
    water, wmeta = _load_water_masks(inputs)
    water_index = shapely.STRtree(water) if water else []
    occupied_by_tract: list[collections.Counter[tuple[int, int]]] = []
    for points in building_centroids:
        counts: collections.Counter[tuple[int, int]] = collections.Counter()
        for x, y in points:
            counts[_grid_cell(x, y)] += 1
        occupied_by_tract.append(counts)
    void_share = np.zeros(len(tracts), dtype=np.float64)
    expected_missing = np.zeros(len(tracts), dtype=np.float64)
    defined = np.zeros(len(tracts), dtype=bool)
    should_counts = []; void_counts = []
    for i, geom in enumerate(tracts):
        should, voids, expected = _ring_voids(occupied_by_tract[i], geom, poi_masks[i], water_index)
        should_counts.append(should); void_counts.append(voids)
        if should > 0:
            defined[i] = True
            void_share[i] = voids / should
            expected_missing[i] = expected
    return IdeaOutput("G4", inputs.region, inputs.geoids,
                      [Emission("B", "covariate", void_share, defined)],
                      {**bmeta, **pmeta, **wmeta, "grid_cell_m": 100.0,
                       "ring_occupancy_minimum": 5, "defined_rows": int(defined.sum()),
                       "should_be_built_cells": int(sum(should_counts)), "void_cells": int(sum(void_counts)),
                       "expected_missing_count": float(expected_missing.sum()),
                       "void_share_distribution": _quantiles(void_share[defined]),
                       "microsoft_building_rows_excluded": bmeta["microsoft_rows_excluded"]})


def _load_place_points(inputs: Any) -> tuple[dict[tuple[float, float], list[tuple[int, str]]], np.ndarray, dict[str, Any]]:
    cols = ["sources", "names", "geometry"]
    parquet = _open_layer(inputs, "pois", cols)
    tracts, tree = _metric_context(inputs)
    stacks: dict[tuple[float, float], list[tuple[int, str]]] = collections.defaultdict(list)
    counts = np.zeros(len(tracts), dtype=np.float64)
    scanned = excluded = assigned = 0
    for rg in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(rg, columns=cols)
        sources = table["sources"].to_pylist(); names = table["names"].to_pylist(); wkbs = table["geometry"].to_pylist()
        scanned += len(wkbs)
        keep = [i for i in range(len(wkbs)) if _allowed_poi(sources[i]) and wkbs[i] is not None]
        excluded += len(wkbs) - len(keep)
        if not keep:
            continue
        points = shapely.from_wkb([wkbs[i] for i in keep])
        metric = shapely.transform(points, TRANSFORMER.transform, interleaved=False)
        qi, ti = tree.query(metric, predicate="within")
        for j, tract_i in zip(qi.tolist(), ti.tolist()):
            row_i = keep[j]
            x, y = points[j].coords[0]
            name = _primary_name(names[row_i])
            stacks[(float(x), float(y))].append((int(tract_i), name or ""))
            counts[tract_i] += 1.0; assigned += 1
    return stacks, counts, {"rows_scanned": scanned, "points_assigned": assigned,
                             "microsoft_source_rows_excluded": excluded,
                             "assignment": "point strictly within one ACS tract; exact coordinate uses source lon/lat"}


@implements("G5")
def g5(inputs: Any) -> IdeaOutput:
    stacks, place_counts, pmeta = _load_place_points(inputs)
    _, bcentroids, bmeta = _load_buildings(inputs)
    tracts, tree = _metric_context(inputs)
    building_counts = np.asarray([len(x) for x in bcentroids], dtype=np.float64)
    stacked_inside = np.zeros(len(tracts), dtype=np.float64)
    exported = np.zeros(len(tracts), dtype=np.float64)
    stack_count = 0
    stack_points = []
    for (lon, lat), memberships in stacks.items():
        names = {name for _, name in memberships if name}
        if len(names) < 5:
            continue
        mass = len(memberships)
        owners = {tract_i for tract_i, _ in memberships}
        for owner in owners:
            stacked_inside[owner] += sum(1 for tract_i, _ in memberships if tract_i == owner)
        stack_points.append((shapely.Point(lon, lat), mass, owners)); stack_count += 1
    metric_points = shapely.transform(np.asarray([x[0] for x in stack_points], dtype=object),
                                      TRANSFORMER.transform, interleaved=False) if stack_points else np.asarray([], dtype=object)
    total_allocated = 0.0
    for point, (_, mass, owners) in zip(metric_points, stack_points):
        near = tree.query(point.buffer(3000.0), predicate="intersects").tolist()
        recipients = [i for i in near if i not in owners and building_counts[i] > 0]
        denom = float(sum(building_counts[i] for i in recipients))
        if denom <= 0:
            continue
        for i in recipients:
            allocation = mass * building_counts[i] / denom
            exported[i] += allocation; total_allocated += allocation
    defined_share = place_counts > 0
    stacked_share = np.zeros(len(tracts), dtype=np.float64)
    stacked_share[defined_share] = np.clip(stacked_inside[defined_share] / place_counts[defined_share], 0.0, 1.0)
    defined_export = building_counts > 0
    return IdeaOutput("G5", inputs.region, inputs.geoids,
                      [Emission("Est", "covariate", stacked_share, defined_share),
                       Emission("F", "covariate", exported, defined_export)],
                      {**pmeta, "building_rows": bmeta["features_assigned"],
                       "microsoft_building_rows_excluded": bmeta["microsoft_rows_excluded"],
                       "coordinate_stacks_ge5_names": stack_count, "radius_m": 3000.0,
                       "tracts_with_place_counts": int(defined_share.sum()),
                       "tracts_with_building_allocators": int(defined_export.sum()),
                       "total_exported_mass_allocated": total_allocated,
                       "stacked_share_distribution": _quantiles(stacked_share[defined_share]),
                       "exact_coordinate_stacking": True})


def _quantiles(values: np.ndarray) -> dict[str, float | int] | None:
    if not len(values):
        return None
    return {"count": int(len(values)), "min": float(np.min(values)),
            "p50": float(np.quantile(values, .5)), "p90": float(np.quantile(values, .9)),
            "max": float(np.max(values)), "mean": float(np.mean(values))}


IMPLEMENTATIONS = {"G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
