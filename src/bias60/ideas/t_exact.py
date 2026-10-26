"""Pure graph primitives for recipe-exact Family T v2 topology estimators.

These helpers intentionally operate only on feature metadata and metric-coordinate geometry passed
by the provided-data reader. They do not open any source themselves.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence


def deflection_degrees(vector_a: Sequence[float], vector_b: Sequence[float]) -> float:
    """Return road deflection at a junction (0° is straight continuation).

    Vectors point from the shared connector outward along each segment. Thus collinear opposite
    vectors are 0° deflection, perpendicular vectors are 90°, and same-direction vectors are
    180°. Degenerate tangents are rejected rather than silently joined.
    """
    ax, ay = float(vector_a[0]), float(vector_a[1])
    bx, by = float(vector_b[0]), float(vector_b[1])
    an, bn = math.hypot(ax, ay), math.hypot(bx, by)
    if an == 0.0 or bn == 0.0:
        raise ValueError("stroke tangent vectors must be non-zero")
    cosine = max(-1.0, min(1.0, -(ax * bx + ay * by) / (an * bn)))
    return math.degrees(math.acos(cosine))


def stroke_components(identities: Sequence[str | None],
                      endpoint_connectors: Sequence[tuple[str | None, str | None]],
                      endpoint_tangents: Sequence[tuple[Sequence[float], Sequence[float]]],
                      *, threshold_degrees: float = 30.0) -> list[int]:
    """Label connected named-stroke components using identity and strict deflection < threshold.

    Each feature contributes its two endpoint connector IDs and outward tangent vectors in the
    same order. Components never join different semantic identities, even at a shared connector.
    """
    n = len(identities)
    if len(endpoint_connectors) != n or len(endpoint_tangents) != n:
        raise ValueError("identity, connector and tangent arrays must have equal lengths")
    if not 0.0 < threshold_degrees <= 180.0:
        raise ValueError("threshold_degrees must be in (0, 180]")
    parent = list(range(n))
    rank = [0] * n

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1

    at_connector: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for edge, identity in enumerate(identities):
        if identity is None:
            continue
        for endpoint, connector in enumerate(endpoint_connectors[edge]):
            if connector:
                at_connector[(identity, connector)].append((edge, endpoint))

    for incidents in at_connector.values():
        for left in range(len(incidents)):
            edge_a, end_a = incidents[left]
            for right in range(left + 1, len(incidents)):
                edge_b, end_b = incidents[right]
                if edge_a == edge_b:
                    continue
                angle = deflection_degrees(endpoint_tangents[edge_a][end_a],
                                           endpoint_tangents[edge_b][end_b])
                # Keep an exactly-threshold turn out despite inverse-trig round-off.
                if angle < threshold_degrees - 1e-12:
                    union(edge_a, edge_b)
    roots: dict[int, int] = {}
    labels = []
    for edge in range(n):
        root = find(edge)
        if root not in roots:
            roots[root] = len(roots)
        labels.append(roots[root])
    return labels


def t1_exact(inputs):
    """Compute exact T1 per-tract class-drop scores from the provided roads-unfiltered layer."""
    import numpy as np
    import shapely
    from pyproj import Transformer
    from bias60.contracts import Emission, IdeaOutput
    from bias60.ideas.t import _identity, _road_layer

    seed = inputs.config["seeds"]["T1"]
    named_classes = set(seed["named_classes"])
    threshold = float(seed["outside_named_share_threshold"])
    tracts = shapely.transform(inputs.tract_geometries(),
                               Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform,
                               interleaved=False)
    tree = shapely.STRtree(tracts)
    columns = ["class", "names", "routes", "connectors", "geometry"]
    parquet = _road_layer(inputs, columns, seed["unfiltered_layer"])
    identities: list[str | None] = []
    classes: list[str | None] = []
    connectors: list[tuple[str | None, str | None]] = []
    tangents: list[tuple[tuple[float, float], tuple[float, float]]] = []
    full_lengths: list[float] = []
    tract_lengths: list[dict[int, float]] = []
    rows_read = rows_kept = 0

    for group in range(parquet.metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=columns)
        row_classes = table.column("class").to_pylist()
        names = table.column("names").to_pylist()
        routes = table.column("routes").to_pylist()
        conns = table.column("connectors").to_pylist()
        wkbs = table.column("geometry").to_pylist()
        rows_read += len(row_classes)
        selected = [i for i, (cls, wkb) in enumerate(zip(row_classes, wkbs))
                    if isinstance(cls, str) and wkb is not None
                    and _identity(names[i], routes[i]) is not None]
        if not selected:
            continue
        lines = shapely.transform(shapely.from_wkb([wkbs[i] for i in selected]),
                                  Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform,
                                  interleaved=False)
        lengths = shapely.length(lines).tolist()
        coord_arrays = shapely.get_coordinates(lines, return_index=True)
        coords, line_ids = coord_arrays
        line_coords: list[list[tuple[float, float]]] = [[] for _ in selected]
        for (x, y), local_id in zip(coords.tolist(), line_ids.tolist()):
            line_coords[local_id].append((x, y))
        pairs = tree.query(lines, predicate="intersects")
        local_tract_lengths: list[dict[int, float]] = [defaultdict(float) for _ in selected]
        if pairs.size:
            clipped = shapely.length(shapely.intersection(lines[pairs[0]], tracts[pairs[1]]))
            for local_id, tract_id, metres in zip(pairs[0].tolist(), pairs[1].tolist(), clipped.tolist()):
                if np.isfinite(metres) and metres > 0:
                    local_tract_lengths[local_id][tract_id] += float(metres)
        for local_id, source_id in enumerate(selected):
            points = line_coords[local_id]
            if len(points) < 2:
                continue
            row_conns = [item for item in (conns[source_id] or [])
                         if isinstance(item, dict) and item.get("connector_id")
                         and isinstance(item.get("at"), (int, float))]
            row_conns.sort(key=lambda item: float(item["at"]))
            ends = (str(row_conns[0]["connector_id"]), str(row_conns[-1]["connector_id"])) if len(row_conns) >= 2 else (None, None)
            start = (points[1][0] - points[0][0], points[1][1] - points[0][1])
            end = (points[-2][0] - points[-1][0], points[-2][1] - points[-1][1])
            identities.append(_identity(names[source_id], routes[source_id]))
            classes.append(row_classes[source_id])
            connectors.append(ends)
            tangents.append((start, end))
            full_lengths.append(float(lengths[local_id]))
            tract_lengths.append(dict(local_tract_lengths[local_id]))
            rows_kept += 1

    components = stroke_components(identities, connectors, tangents,
                                   threshold_degrees=30.0)
    totals: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    in_tract: dict[int, dict[int, list[float]]] = defaultdict(dict)
    for i, component in enumerate(components):
        length = full_lengths[i]
        named_length = length if classes[i] in named_classes else 0.0
        totals[component][0] += length
        totals[component][1] += named_length
        for tract_id, tract_length in tract_lengths[i].items():
            values = in_tract[component].setdefault(tract_id, [0.0, 0.0, 0.0])
            values[0] += tract_length
            if classes[i] in named_classes:
                values[1] += tract_length
            else:
                values[2] += tract_length

    n = len(inputs.geoids)
    named_in = np.zeros(n, dtype=np.float64)
    drop_in = np.zeros(n, dtype=np.float64)
    for component, per_tract in in_tract.items():
        stroke_total, stroke_named = totals[component]
        for tract_id, (total_t, named_t, drop_t) in per_tract.items():
            outside_total = stroke_total - total_t
            outside_named = stroke_named - named_t
            if outside_total > 0 and outside_named / outside_total >= threshold and drop_t > 0:
                named_in[tract_id] += named_t
                drop_in[tract_id] += drop_t
    denominator = named_in + drop_in
    defined = denominator > 0
    score = np.zeros(n, dtype=np.float64)
    score[defined] = drop_in[defined] / denominator[defined]
    return IdeaOutput("T1-v2", inputs.region, list(inputs.geoids), [
        Emission("R", "gap", score, defined,
                 {"mapping": "L_drop/(L_named+L_drop); stroke outside named share >= 0.70"})
    ], {"identity": "same names.primary, fallback same routes.network/ref", "deflection_limit_degrees": 30.0,
        "deflection_comparison": "strictly less than 30 degrees", "outside_named_share_threshold": threshold,
        "rows_read": rows_read, "segments_with_identity_and_geometry": rows_kept,
        "length_crs": "EPSG:5070", "no_target_or_reference_data_read": True})
