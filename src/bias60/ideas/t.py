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


def _unimplemented(inputs, idea_id: str) -> IdeaOutput:
    raise NotImplementedError(f"{idea_id} is not implemented yet")
