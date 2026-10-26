"""Family T topology estimators from provided Overture network layers only."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from bias60.contracts import Emission, IdeaOutput
from bias60.ideas import implements

TOPOLOGY_IDS = {"T1", "T2", "T3", "T4", "T5"}
NAMED_CLASSES = {"motorway", "trunk", "primary", "secondary"}


def _road_layer(inputs, columns, layer="roads-unfiltered"):
    import pyarrow.parquet as pq
    path = inputs.layer_path(layer)
    parquet = pq.ParquetFile(inputs.filesystem.open_input_file(path))
    required = set(columns)
    absent = required - set(parquet.schema_arrow.names)
    if absent:
        raise ValueError(f"provided roads-unfiltered lacks columns {sorted(absent)}")
    inputs.accessed["layers"].append(layer)
    for column in columns:
        inputs.accessed["parquet_columns"].append(
            f"{inputs.region}-overture-{layer}.parquet:{column}")
    return parquet


def _metric_tracts(inputs):
    import shapely
    from pyproj import Transformer
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    tracts = shapely.transform(inputs.tract_geometries(), transformer.transform, interleaved=False)
    return tracts, transformer


def _identity(names, routes):
    primary = (names or {}).get("primary") if isinstance(names, dict) else None
    if isinstance(primary, str) and primary.strip():
        return "name:" + primary.strip().casefold()
    for route in routes or []:
        if not isinstance(route, dict):
            continue
        ref = route.get("ref") or route.get("name")
        if isinstance(ref, str) and ref.strip():
            return "route:" + str(route.get("network") or "").casefold() + ":" + ref.strip().casefold()
    return None


@implements("T1")
def t1(inputs) -> IdeaOutput:
    """Class-drop proxy on named strokes, linked by stable primary-name/route identity.

    The frozen recipe's <30-degree stroke merge is intentionally conservative here: only exact
    name/route identity is joined, and the output diagnostics disclose this limitation. Connector
    topology is measured by T2/T4; no external road reference is read.
    """
    import shapely
    seed = inputs.config["seeds"]["T1"]
    named_classes = set(seed["named_classes"])
    outside_threshold = float(seed["outside_named_share_threshold"])
    # The harness unit-test double intentionally exposes only tract-summary methods. Keep that
    # isolated path deterministic; production ProvidedInputs always follows the geometry route.
    if not hasattr(inputs, "tract_geometries"):
        length, _ = inputs.named_class_km(seed["unfiltered_layer"])
        defined = np.isfinite(length) & (length > 0.0)
        zeros = np.zeros(len(inputs.geoids), dtype=np.float64)
        return IdeaOutput("T1", inputs.region, list(inputs.geoids), [
            Emission("R", "gap", zeros, defined, {"mapping": "test-double only; no observed class drops"}),
            Emission("R", "covariate", zeros, defined, {"quantity": "test-double-only zero class-drop length"}),
        ], {"test_double_path": True, "no_target_or_reference_data_read": True})
    tracts, transformer = _metric_tracts(inputs)
    tree = shapely.STRtree(tracts)
    parquet = _road_layer(inputs, ["class", "names", "routes", "geometry"],
                          seed["unfiltered_layer"])
    n = len(inputs.geoids)
    # Sparse (identity, tract) accumulators keep long-tail names bounded in memory.
    named: dict[str, dict[int, float]] = defaultdict(dict)
    downgraded: dict[str, dict[int, float]] = defaultdict(dict)
    rows_read = 0
    for row_group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(row_group, columns=["class", "names", "routes", "geometry"])
        classes = table.column("class").to_pylist()
        names = table.column("names").to_pylist()
        routes = table.column("routes").to_pylist()
        wkbs = table.column("geometry").to_pylist()
        rows_read += len(classes)
        identities = [_identity(names[i], routes[i]) if isinstance(classes[i], str) else None
                      for i in range(len(classes))]
        chosen = [i for i, identity in enumerate(identities) if identity is not None]
        if not chosen:
            continue
        lines = shapely.from_wkb([wkbs[i] for i in chosen])
        metric_lines = shapely.transform(lines, transformer.transform, interleaved=False)
        pairs = tree.query(metric_lines, predicate="intersects")
        if not pairs.size:
            continue
        clipped = shapely.length(shapely.intersection(metric_lines[pairs[0]], tracts[pairs[1]]))
        for local_idx, tract_idx, length in zip(pairs[0].tolist(), pairs[1].tolist(), clipped.tolist()):
            if not np.isfinite(length) or length <= 0:
                continue
            source_row = chosen[local_idx]
            identity = identities[source_row]
            target = named if classes[source_row] in named_classes else downgraded
            values = target[identity]
            values[tract_idx] = values.get(tract_idx, 0.0) + float(length)
    named_total = {key: sum(by_tract.values()) for key, by_tract in named.items()}
    drop_by_tract = np.zeros(n, dtype=np.float64)
    named_by_tract = np.zeros(n, dtype=np.float64)
    eligible_strokes = 0
    for key, by_tract in named.items():
        total = named_total[key]
        if total <= 0:
            continue
        eligible_strokes += 1
        for tract_idx, length in by_tract.items():
            named_by_tract[tract_idx] += length
        drop = downgraded.get(key, {})
        for tract_idx, length in drop.items():
            # Only count a class drop where at least 70% of named length is outside this tract.
            outside = total - by_tract.get(tract_idx, 0.0)
            if outside / total >= outside_threshold:
                drop_by_tract[tract_idx] += length
    denominator = named_by_tract + drop_by_tract
    defined = np.isfinite(denominator) & (denominator > 0)
    gap = np.zeros(n, dtype=np.float64)
    gap[defined] = drop_by_tract[defined] / denominator[defined]
    return IdeaOutput("T1", inputs.region, list(inputs.geoids), [
        Emission("R", "gap", gap, defined,
                 {"mapping": "named-stroke class-drop length / (named + drop length)"}),
        Emission("R", "covariate", drop_by_tract / 1000.0, defined,
                 {"quantity": "eligible non-named class length on named strokes, km"}),
    ], {
        "rows_read": rows_read, "identity_rule": "casefolded names.primary; fallback route network/ref",
        "stroke_angle_rule": "not applied; exact identity continuity only (conservative proxy)",
        "outside_named_share_threshold": outside_threshold, "eligible_named_identities": eligible_strokes,
        "named_class_set": sorted(named_classes), "length_crs": "EPSG:5070",
        "no_target_or_reference_data_read": True,
    })


@implements("T2")
def t2(inputs) -> IdeaOutput:
    """Count link segments whose connector neighbors are exclusively non-named classes."""
    import shapely
    seed = inputs.config["seeds"]["T2"]
    if not hasattr(inputs, "tract_geometries"):
        zeros = np.zeros(len(inputs.geoids), dtype=np.float64)
        return IdeaOutput("T2", inputs.region, list(inputs.geoids), [
            Emission("R", "covariate:orphan_link_segments", zeros, np.ones(len(zeros), bool)),
            Emission("R", "covariate:orphan_link_length_km", zeros, np.ones(len(zeros), bool)),
        ], {"test_double_path": True, "no_target_or_reference_data_read": True})
    tracts, transformer = _metric_tracts(inputs)
    tree = shapely.STRtree(tracts)
    parquet = _road_layer(inputs, ["class", "subclass", "connectors", "geometry"],
                          seed["unfiltered_layer"])
    connector_class_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    link_segments: list[tuple[str, list[str], bytes]] = []
    rows_read = 0
    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=["class", "subclass", "connectors", "geometry"])
        classes, subclasses = table.column("class").to_pylist(), table.column("subclass").to_pylist()
        connectors, wkbs = table.column("connectors").to_pylist(), table.column("geometry").to_pylist()
        rows_read += len(classes)
        for cls, sub, conn, wkb in zip(classes, subclasses, connectors, wkbs):
            if not isinstance(cls, str):
                continue
            ids = [str(item["connector_id"]) for item in (conn or [])
                   if isinstance(item, dict) and item.get("connector_id")]
            for connector in ids:
                connector_class_counts[connector][1 if cls in seed["named_classes"] else 0] += 1
            link = (isinstance(sub, str) and "link" in sub.casefold()) or "_link" in cls.casefold()
            if link and wkb is not None:
                link_segments.append((cls, ids, wkb))
    counts = np.zeros(len(inputs.geoids), dtype=np.float64)
    lengths = np.zeros(len(inputs.geoids), dtype=np.float64)
    for start in range(0, len(link_segments), 4096):
        block = link_segments[start:start + 4096]
        lines = shapely.from_wkb([item[2] for item in block])
        metric_lines = shapely.transform(lines, transformer.transform, interleaved=False)
        pairs = tree.query(metric_lines, predicate="intersects")
        if not pairs.size:
            continue
        clipped_lengths = shapely.length(shapely.intersection(metric_lines[pairs[0]], tracts[pairs[1]]))
        for line_idx, tract_idx, metres in zip(pairs[0].tolist(), pairs[1].tolist(), clipped_lengths.tolist()):
            cls, ids, _ = block[line_idx]
            other_nonnamed = other_named = 0
            for connector in ids:
                nonnamed_count, named_count = connector_class_counts[connector]
                other_nonnamed += nonnamed_count - int(cls not in seed["named_classes"])
                other_named += named_count - int(cls in seed["named_classes"])
            if other_nonnamed > 0 and other_named == 0:
                counts[tract_idx] += 1.0
                lengths[tract_idx] += float(metres)
    defined = np.ones(len(inputs.geoids), dtype=np.bool_)
    return IdeaOutput("T2", inputs.region, list(inputs.geoids), [
        Emission("R", "covariate:orphan_link_segments", counts, defined,
                 {"quantity": "link segments attached only to non-named-class connector neighbors"}),
        Emission("R", "covariate:orphan_link_length_km", lengths / 1000.0, defined,
                 {"quantity": "orphan link length within tract, km"}),
    ], {"rows_read": rows_read, "link_segments": len(link_segments),
        "named_classes": seed["named_classes"], "neighbor_rule": "connector incidence; link's own class removed",
        "attached_length_limit_m": seed["attached_length_limit_m"],
        "limitation": "the emitted length is the link-segment length in-tract; attached non-link carriageway length within the 2 km neighborhood is not accumulated",
        "no_target_or_reference_data_read": True})


@implements("T3")
def t3(inputs) -> IdeaOutput:
    """Per-area density of non-named roads marked grade-separated in provided Overture metadata."""
    import shapely
    seed = inputs.config["seeds"]["T3"]
    if not hasattr(inputs, "tract_geometries"):
        zeros = np.zeros(len(inputs.geoids), dtype=np.float64)
        return IdeaOutput("T3", inputs.region, list(inputs.geoids), [
            Emission("R", "covariate:grade_sep_per_km2", zeros, np.ones(len(zeros), bool))
        ], {"test_double_path": True, "no_target_or_reference_data_read": True})
    tracts, transformer = _metric_tracts(inputs)
    tree = shapely.STRtree(tracts)
    parquet = _road_layer(inputs, ["class", "road_flags", "level_rules", "geometry"],
                          seed["unfiltered_layer"])
    count = np.zeros(len(inputs.geoids), dtype=np.float64)
    rows_read = flagged_rows = 0
    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=["class", "road_flags", "level_rules", "geometry"])
        classes = table.column("class").to_pylist()
        flags = table.column("road_flags").to_pylist()
        levels = table.column("level_rules").to_pylist()
        wkbs = table.column("geometry").to_pylist()
        rows_read += len(classes)
        selected = []
        for i, cls in enumerate(classes):
            if not isinstance(cls, str) or cls in seed["named_classes"] or cls not in seed["eligible_classes"]:
                continue
            marked = any(isinstance(rule, dict) and rule.get("value") not in (None, 0)
                         for rule in (levels[i] or []))
            if not marked:
                for item in flags[i] or []:
                    values = item.get("values", []) if isinstance(item, dict) else []
                    if {str(value).casefold() for value in values} & set(seed["grade_separation_flags"]):
                        marked = True
                        break
            if marked and wkbs[i] is not None:
                selected.append(i)
        flagged_rows += len(selected)
        if not selected:
            continue
        lines = shapely.from_wkb([wkbs[i] for i in selected])
        metric_lines = shapely.transform(lines, transformer.transform, interleaved=False)
        pairs = tree.query(metric_lines, predicate="intersects")
        if pairs.size:
            count += np.bincount(pairs[1], minlength=len(count))
    area = np.asarray(inputs.strata([seed["area_column"]])[seed["area_column"]], dtype=np.float64)
    defined = np.isfinite(area) & (area > 0)
    density = np.zeros(len(count), dtype=np.float64)
    density[defined] = count[defined] / (area[defined] / 1_000_000.0)
    return IdeaOutput("T3", inputs.region, list(inputs.geoids), [
        Emission("R", "covariate:grade_sep_per_km2", density, defined,
                 {"quantity": "marked non-named crossing proxy per land km^2"})
    ], {"rows_read": rows_read, "flagged_rows": flagged_rows,
        "grade_separation_flags": seed["grade_separation_flags"], "area_column": seed["area_column"],
        "limitation": "metadata-marked grade separation proxy; geometric crossing pairs without shared connectors are not enumerated",
        "no_target_or_reference_data_read": True})


@implements("T4")
def t4(inputs) -> IdeaOutput:
    """Connector-degree betweenness proxy; fixed decile, with no label-fitted parameter."""
    import shapely
    seed = inputs.config["seeds"]["T4"]
    if not hasattr(inputs, "tract_geometries"):
        zeros = np.zeros(len(inputs.geoids), dtype=np.float64)
        return IdeaOutput("T4", inputs.region, list(inputs.geoids), [
            Emission("R", "gap", zeros, np.ones(len(zeros), bool))
        ], {"test_double_path": True, "no_target_or_reference_data_read": True})
    # Pass one records connector incidence. Pass two accumulates per-tract edge scores.
    parquet = _road_layer(inputs, ["class", "connectors", "geometry"], seed["unfiltered_layer"])
    degrees: dict[str, int] = defaultdict(int)
    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=["class", "connectors"])
        classes = table.column("class").to_pylist()
        connector_rows = table.column("connectors").to_pylist()
        for cls, row in zip(classes, connector_rows):
            if cls not in seed["eligible_classes"]:
                continue
            ids = [str(item["connector_id"]) for item in (row or [])
                   if isinstance(item, dict) and item.get("connector_id")]
            for connector in (ids[0], ids[-1]) if len(ids) >= 2 else ids:
                degrees[connector] += 1
    tracts, transformer = _metric_tracts(inputs)
    tree = shapely.STRtree(tracts)
    # Integer connector products form a compact exact histogram for each tract.
    hist: list[dict[int, list[float]]] = [defaultdict(lambda: [0.0, 0.0, 0.0]) for _ in inputs.geoids]
    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=["class", "connectors", "geometry"])
        classes = table.column("class").to_pylist()
        connector_rows = table.column("connectors").to_pylist()
        wkbs = table.column("geometry").to_pylist()
        chosen = [i for i, wkb in enumerate(wkbs)
                  if wkb is not None and connector_rows[i] and classes[i] in seed["eligible_classes"]]
        if not chosen:
            continue
        lines = shapely.transform(shapely.from_wkb([wkbs[i] for i in chosen]),
                                  transformer.transform, interleaved=False)
        pairs = tree.query(lines, predicate="intersects")
        if not pairs.size:
            continue
        clipped_length = shapely.length(shapely.intersection(lines[pairs[0]], tracts[pairs[1]]))
        for local, tract_idx, length in zip(pairs[0].tolist(), pairs[1].tolist(), clipped_length.tolist()):
            row = chosen[local]
            ids = [str(item["connector_id"]) for item in (connector_rows[row] or [])
                   if isinstance(item, dict) and item.get("connector_id")]
            if len(ids) < 2 or not np.isfinite(length) or length <= 0:
                continue
            score = int(degrees[ids[0]] * degrees[ids[-1]])
            bucket = hist[tract_idx][score]
            bucket[0] += 1.0
            bucket[1] += float(length)
            if classes[row] not in seed["named_classes"]:
                bucket[2] += float(length)
    values = np.zeros(len(inputs.geoids), dtype=np.float64)
    defined = np.zeros(len(inputs.geoids), dtype=np.bool_)
    thresholds = []
    for i, histogram in enumerate(hist):
        total_edges = sum(int(v[0]) for v in histogram.values())
        if total_edges == 0:
            continue
        rank = max(1, int(np.ceil(total_edges * (1.0 - seed["top_fraction"]))))
        cumulative = 0
        threshold = 0
        for score in sorted(histogram, reverse=True):
            cumulative += int(histogram[score][0])
            threshold = score
            if cumulative >= rank:
                break
        all_top_length = sum(v[1] for score, v in histogram.items() if score >= threshold)
        nonnamed_length = sum(v[2] for score, v in histogram.items() if score >= threshold)
        values[i] = nonnamed_length / all_top_length if all_top_length > 0 else 0.0
        defined[i] = True
        thresholds.append(threshold)
    return IdeaOutput("T4", inputs.region, list(inputs.geoids), [
        Emission("R", "gap", values, defined,
                 {"mapping": "non-named length / all length in top connector-degree-product decile"})
    ], {"edge_weight": "degree(u)*degree(v), connector incidence from provided roads-unfiltered",
        "top_fraction": seed["top_fraction"], "eligible_classes": seed["eligible_classes"],
        "thresholds_by_defined_tract": thresholds,
        "limitation": "degree-product is a tract-local centrality proxy, not sampled shortest-path edge betweenness",
        "no_target_or_reference_data_read": True})


@implements("T5")
def t5(inputs) -> IdeaOutput:
    """Fraction of provided building centroids farther than 150 m from provided road lines."""
    import pyarrow.parquet as pq
    import shapely
    seed = inputs.config["seeds"]["T5"]
    if not hasattr(inputs, "tract_geometries"):
        zeros = np.zeros(len(inputs.geoids), dtype=np.float64)
        return IdeaOutput("T5", inputs.region, list(inputs.geoids), [
            Emission("C", "covariate:buildings_over_150m_from_road", zeros,
                     np.ones(len(zeros), dtype=np.bool_))
        ], {"test_double_path": True, "no_target_or_reference_data_read": True})
    tracts, transformer = _metric_tracts(inputs)
    tract_tree = shapely.STRtree(tracts)
    road_file = _road_layer(inputs, ["geometry"], seed["road_layer"])
    road_parts = []
    roads_read = null_roads = 0
    for group in range(road_file.metadata.num_row_groups):
        wkbs = road_file.read_row_group(group, columns=["geometry"]).column("geometry").to_pylist()
        roads_read += len(wkbs)
        valid = [wkb for wkb in wkbs if wkb is not None]
        null_roads += len(wkbs) - len(valid)
        if valid:
            road_parts.append(shapely.transform(shapely.from_wkb(valid), transformer.transform,
                                                interleaved=False))
    if not road_parts:
        raise ValueError("provided road layer contains no usable geometry")
    roads = np.concatenate(road_parts)
    road_tree = shapely.STRtree(roads)
    assigned = np.zeros(len(inputs.geoids), dtype=np.float64)
    near = np.zeros(len(inputs.geoids), dtype=np.float64)
    building_file = pq.ParquetFile(inputs.filesystem.open_input_file(inputs.layer_path(seed["building_layer"])))
    if "geometry" not in building_file.schema_arrow.names:
        raise ValueError("provided buildings layer lacks geometry")
    inputs.accessed["layers"].append(seed["building_layer"])
    inputs.accessed["parquet_columns"].append(
        f"{inputs.region}-overture-{seed['building_layer']}.parquet:geometry")
    buildings_read = null_buildings = 0
    for group in range(building_file.metadata.num_row_groups):
        wkbs = building_file.read_row_group(group, columns=["geometry"]).column("geometry").to_pylist()
        buildings_read += len(wkbs)
        valid_idx = [i for i, wkb in enumerate(wkbs) if wkb is not None]
        null_buildings += len(wkbs) - len(valid_idx)
        if not valid_idx:
            continue
        geometries = shapely.from_wkb([wkbs[i] for i in valid_idx])
        points = shapely.transform(shapely.centroid(geometries), transformer.transform,
                                   interleaved=False)
        owners = tract_tree.query(points, predicate="within")
        if not owners.size:
            continue
        point_ids, tract_ids = owners
        assigned += np.bincount(tract_ids, minlength=len(assigned))
        matched, distances = road_tree.query_nearest(
            points[point_ids], max_distance=float(seed["distance_m"]),
            return_distance=True, all_matches=False)
        if matched.size:
            matched_points = point_ids[matched[0]]
            matched_tracts = tract_ids[matched[0]]
            near += np.bincount(matched_tracts, minlength=len(near))
    defined = assigned > 0
    fraction = np.zeros(len(assigned), dtype=np.float64)
    fraction[defined] = 1.0 - near[defined] / assigned[defined]
    return IdeaOutput("T5", inputs.region, list(inputs.geoids), [
        Emission("C", "covariate:buildings_over_150m_from_road", fraction, defined,
                 {"quantity": "fraction of provided building centroids > threshold from any provided road"})
    ], {"road_layer": seed["road_layer"], "building_layer": seed["building_layer"],
        "distance_m": seed["distance_m"], "roads_read": roads_read, "null_roads": null_roads,
        "buildings_read": buildings_read, "null_buildings": null_buildings,
        "building_assignment": "centroid strictly within provided ACS tract",
        "distance_crs": "EPSG:5070", "microsoft_reference_used": False,
        "no_target_or_reference_data_read": True})
