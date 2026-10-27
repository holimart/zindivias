"""Exact geometric-crossing portion of Family T3 from provided Overture inputs."""
from __future__ import annotations

from collections.abc import Sequence


def _flag_values(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).casefold() for entry in value if isinstance(entry, dict)
            for item in (entry.get("values") or []) if item is not None}


def _grade_separation(road_flags: object, level_rules: object, flag_names: set[str]) -> bool:
    flagged = bool(_flag_values(road_flags) & flag_names)
    levels = [entry.get("value") for entry in (level_rules or [])
              if isinstance(entry, dict) and entry.get("value") is not None]
    return flagged or any(float(level) != 0. for level in levels)


def _is_rail(rail_flags: object) -> bool:
    return any("rail" in value for value in _flag_values(rail_flags))


def _point_parts(geometry: object) -> list[object]:
    """Return point members of a Shapely intersection, ignoring overlapping linework."""
    if geometry is None or geometry.is_empty:
        return []
    kind = geometry.geom_type
    if kind == "Point":
        return [geometry]
    if kind in ("MultiPoint", "GeometryCollection"):
        return [point for part in geometry.geoms for point in _point_parts(part)]
    return []


def crossing_counts(geometries: Sequence[object], classes: Sequence[str | None],
                    connectors: Sequence[set[str]], rail: Sequence[bool],
                    grade_marked: Sequence[bool], tract_geometries: Sequence[object],
                    *, eligible_classes: set[str], named_classes: set[str]
                    ) -> tuple[list[float], dict[str, int], list[object], list[bool]]:
    """Count proper geometric road crossings per tract, requiring no shared connector.

    A crossing is eligible when at least one member is a configured tertiary/unclassified
    non-named road. Rail-flagged roads are excluded. Each point intersection is assigned to all
    official tract polygons it intersects; a pair may contribute more than one crossing point.
    """
    import numpy as np
    import shapely

    n = len(geometries)
    if not n or any(len(x) != n for x in (classes, connectors, rail, grade_marked)):
        raise ValueError("road geometry and metadata arrays must have equal nonzero lengths")
    tracts = np.asarray(tract_geometries, dtype=object)
    if tracts.ndim != 1 or not len(tracts):
        raise ValueError("official tract geometries must be a nonempty one-dimensional array")
    lines = np.asarray(geometries, dtype=object)
    if np.any(shapely.is_empty(lines)) or np.any(~shapely.is_valid(lines)):
        raise ValueError("road lines must be nonempty valid geometries")
    if not all(isinstance(road_class, str) for road_class in classes):
        raise ValueError("locally intersecting road records must have a road class")
    eligible = np.asarray([cls in eligible_classes and cls not in named_classes for cls in classes], dtype=bool)
    rail_mask = np.asarray(rail, dtype=bool)
    marked = np.asarray(grade_marked, dtype=bool)
    tree = shapely.STRtree(lines)
    candidates = np.flatnonzero(eligible & ~rail_mask)
    crossing_points: list[object] = []
    crossing_grade_marked: list[bool] = []
    seen_pairs: set[tuple[int, int]] = set()
    geometric_candidates = no_shared_connector_pairs = rail_excluded_pairs = 0
    for candidate in candidates:
        hits = tree.query(lines[candidate], predicate="crosses")
        for partner_raw in hits.tolist():
            partner = int(partner_raw)
            if partner == candidate:
                continue
            pair = (min(int(candidate), partner), max(int(candidate), partner))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            if not (eligible[pair[0]] or eligible[pair[1]]):
                continue
            geometric_candidates += 1
            if rail_mask[pair[0]] or rail_mask[pair[1]]:
                rail_excluded_pairs += 1
                continue
            if connectors[pair[0]] & connectors[pair[1]]:
                continue
            no_shared_connector_pairs += 1
            intersection = shapely.intersection(lines[pair[0]], lines[pair[1]])
            points = _point_parts(intersection)
            if points:
                marked_pair = bool(marked[pair[0]] or marked[pair[1]])
                crossing_points.extend(points)
                crossing_grade_marked.extend([marked_pair] * len(points))
    counts = np.zeros(len(tracts), dtype=np.float64)
    marked_counts = np.zeros(len(tracts), dtype=np.float64)
    if crossing_points:
        point_array = np.asarray(crossing_points, dtype=object)
        point_tree = shapely.STRtree(tracts)
        pairs = point_tree.query(point_array, predicate="intersects")
        if pairs.size:
            counts += np.bincount(pairs[1], minlength=len(tracts))
            point_marked = np.asarray(crossing_grade_marked, dtype=bool)
            marked_pairs = pairs[0][point_marked[pairs[0]]]
            if marked_pairs.size:
                marked_tracts = pairs[1][point_marked[pairs[0]]]
                marked_counts += np.bincount(marked_tracts, minlength=len(tracts))
    diagnostics = {"locally_relevant_road_segments": int(n),
                   "eligible_nonnamed_segments": int(eligible.sum()),
                   "geometric_crossing_pairs_involving_eligible_segment": int(geometric_candidates),
                   "rail_excluded_crossing_pairs": int(rail_excluded_pairs),
                   "crossing_pairs_without_shared_connector": int(no_shared_connector_pairs),
                   "counted_geometric_crossing_points": int(len(crossing_points)),
                   "grade_metadata_marked_crossing_points": int(np.count_nonzero(crossing_grade_marked)),
                   "official_tract_crossing_assignments": int(counts.sum()),
                   "official_tract_metadata_marked_crossing_assignments": int(marked_counts.sum())}
    return counts.tolist(), diagnostics, crossing_points, crossing_grade_marked


def t3_exact(inputs):
    """Compute crossing-point density per land km² using allowed provided roads and tracts."""
    import numpy as np
    import shapely
    from pyproj import Transformer
    from bias60.contracts import Emission, IdeaOutput

    seed = inputs.config["seeds"]["T3"]
    named = set(seed["named_classes"])
    eligible_classes = set(seed["eligible_classes"])
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    tracts = shapely.transform(inputs.tract_geometries(), transformer.transform, interleaved=False)
    tract_tree = shapely.STRtree(tracts)
    road_geometries: list[object] = []
    road_classes: list[str] = []
    road_connectors: list[set[str]] = []
    road_rail: list[bool] = []
    road_grade: list[bool] = []
    rows_read = local_road_rows = 0
    road_columns = ["class", "connectors", "road_flags", "rail_flags", "level_rules", "geometry"]
    for table in inputs.iter_layer_batches(seed["unfiltered_layer"], road_columns):
        classes = table.column("class").to_pylist()
        connectors = table.column("connectors").to_pylist()
        road_flags = table.column("road_flags").to_pylist()
        rail_flags = table.column("rail_flags").to_pylist()
        level_rules = table.column("level_rules").to_pylist()
        wkbs = table.column("geometry").to_pylist()
        rows_read += len(classes)
        selected = [i for i, (cls, wkb) in enumerate(zip(classes, wkbs))
                    if isinstance(cls, str) and wkb is not None]
        if not selected:
            continue
        metric = shapely.transform(shapely.from_wkb([wkbs[i] for i in selected]),
                                   transformer.transform, interleaved=False)
        types = shapely.get_type_id(metric)
        linear = np.isin(types, [1, 5])
        if not linear.any():
            continue
        metric = metric[linear]
        selected = [selected[j] for j in np.flatnonzero(linear)]
        pairs = tract_tree.query(metric, predicate="intersects")
        if not pairs.size:
            continue
        local_rows = np.unique(pairs[0])
        local_road_rows += int(len(local_rows))
        for j in local_rows.tolist():
            source_i = selected[int(j)]
            row_connectors = connectors[source_i] or []
            connector_ids = {str(item["connector_id"]) for item in row_connectors
                             if isinstance(item, dict) and item.get("connector_id")}
            road_geometries.append(metric[int(j)])
            road_classes.append(classes[source_i])
            road_connectors.append(connector_ids)
            road_rail.append(_is_rail(rail_flags[source_i]))
            road_grade.append(_grade_separation(road_flags[source_i], level_rules[source_i],
                                                set(seed["grade_separation_flags"])))
    counts, crossing_diag, crossing_points, crossing_marked = crossing_counts(
        road_geometries, road_classes, road_connectors, road_rail, road_grade, tracts,
        eligible_classes=eligible_classes, named_classes=named)

    # The Overture infrastructure layer is read only for explicit bridge-feature corroboration.
    # It does not alter the specified no-shared-connector crossing count.
    bridge_geometries: list[object] = []
    infrastructure_rows = bridge_feature_rows = 0
    for table in inputs.iter_layer_batches("infrastructure", ["class", "subtype", "geometry"]):
        classes = table.column("class").to_pylist()
        subtypes = table.column("subtype").to_pylist()
        wkbs = table.column("geometry").to_pylist()
        infrastructure_rows += len(classes)
        selected = [i for i, (cls, subtype, wkb) in enumerate(zip(classes, subtypes, wkbs))
                    if wkb is not None and "bridge" in f"{cls or ''} {subtype or ''}".casefold()]
        bridge_feature_rows += len(selected)
        if selected:
            bridge_geometries.extend(shapely.transform(shapely.from_wkb([wkbs[i] for i in selected]),
                transformer.transform, interleaved=False).tolist())
    bridge_crossing_points = 0
    if bridge_geometries and crossing_points:
        bridge_tree = shapely.STRtree(np.asarray(bridge_geometries, dtype=object))
        bridge_pairs = bridge_tree.query(np.asarray(crossing_points, dtype=object), predicate="intersects")
        if bridge_pairs.size:
            bridge_crossing_points = int(len(np.unique(bridge_pairs[0])))

    area = np.asarray(inputs.strata([seed["area_column"]])[seed["area_column"]], dtype=np.float64)
    if area.shape != (len(inputs.geoids),):
        raise ValueError("land-area strata must exactly align with official tract IDs")
    defined = np.isfinite(area) & (area > 0.)
    density = np.zeros(len(area), dtype=np.float64)
    density[defined] = np.asarray(counts, dtype=np.float64)[defined] / (area[defined] / 1_000_000.)
    return IdeaOutput("T3-v2", inputs.region, list(inputs.geoids), [
        Emission("R", "covariate:grade_sep_per_km2", density, defined,
                 {"quantity": "proper nonshared-connector geometric crossing points involving an eligible non-named segment, per land km²"})
    ], {"rows_read": rows_read, "locally_relevant_road_rows_read": local_road_rows,
        "eligible_classes": sorted(eligible_classes), "named_classes": sorted(named),
        "land_area_column": seed["area_column"], "road_columns_read": road_columns,
        "infrastructure_rows_read": infrastructure_rows, "bridge_infrastructure_features": bridge_feature_rows,
        "crossing_points_intersecting_bridge_infrastructure": bridge_crossing_points,
        "crossing_rule": "proper geometric line crossings; no common connector ID; at least one eligible nonnamed class; rail-flagged segments excluded",
        "grade_metadata_used_as_corroboration_not_a_filter": True,
        "no_target_or_reference_data_read": True, **crossing_diag})
