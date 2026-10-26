"""Cross-theme facility witnesses (X1) using only provided Overture data."""
from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

from bias60.contracts import Emission, IdeaOutput
from bias60.ideas import implements
from bias60.ideas.g import TRANSFORMER
from bias60.ideas.p_physical import _rule_clean_building, _rule_clean_poi
from bias60.ideas._common import quantiles


def soft_facility_definedness(witness_counts: np.ndarray) -> np.ndarray:
    """Poisson-arrival soft probability that at least one facility witness exists."""
    counts = np.asarray(witness_counts, dtype=np.float64)
    if counts.ndim != 2 or counts.shape[1] != 3:
        raise ValueError("expected one fire/EMS/schools witness count per tract")
    if np.any(~np.isfinite(counts)) or np.any(counts < 0):
        raise ValueError("witness counts must be finite and nonnegative")
    per_type = -np.expm1(-counts)
    return 1.0 - np.prod(1.0 - per_type, axis=1)


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _strings(child)


def _facility_type(building_class: Any, names: Any, seed: dict[str, Any]) -> set[str]:
    cls = str(building_class or "").casefold().replace("-", "_").replace(" ", "_")
    text = " ".join(_strings(names)).casefold()
    found: set[str] = set()
    for facility_type, spec in seed["building_witnesses"].items():
        if cls in spec["classes"]:
            found.add(facility_type)
        if text and any(re.search(pattern, text) for pattern in spec["name_patterns"]):
            if not any(re.search(pattern, text) for pattern in spec["false_positive_patterns"]):
                found.add(facility_type)
    return found


def _read_x1(inputs, seed):
    import pyarrow.parquet as pq
    import shapely
    tracts = shapely.transform(inputs.tract_geometries(), TRANSFORMER.transform, interleaved=False)
    tree = shapely.STRtree(tracts); n = len(inputs.geoids)
    witness = np.zeros((n, 3), dtype=np.float64)
    order = ["fire", "ems", "schools"]
    counts = {name: np.zeros(n, dtype=np.float64) for name in order}
    paths = {}
    for layer, columns in (("buildings", seed["building_columns"]), ("pois", seed["poi_columns"])):
        path = inputs.layer_path(layer); paths[layer] = path
        pf = pq.ParquetFile(inputs.filesystem.open_input_file(path))
        missing = sorted(set(columns) - set(pf.schema_arrow.names))
        if missing: raise ValueError(f"{layer} lacks X1 columns {missing}")
        inputs.accessed["parquet_columns"].extend(f"{inputs.region}-overture-{layer}.parquet:{c}" for c in columns)
        if layer == "buildings":
            scanned = assigned = excluded = 0
            for rg in range(pf.metadata.num_row_groups):
                tab = pf.read_row_group(rg, columns=columns); scanned += tab.num_rows
                sources = tab.column("sources").to_pylist(); classes = tab.column("class").to_pylist()
                names = tab.column("names").to_pylist(); wkbs = tab.column("geometry").to_pylist()
                keep = [i for i, src in enumerate(sources) if _rule_clean_building(src) and wkbs[i] is not None]
                excluded += len(wkbs) - len(keep)
                if not keep: continue
                geoms = shapely.from_wkb([wkbs[i] for i in keep])
                geoms = shapely.transform(geoms, TRANSFORMER.transform, interleaved=False)
                centers = shapely.centroid(geoms); left, right = tree.query(centers, predicate="within")
                assigned += len(left)
                for local, tract_id in zip(left.tolist(), right.tolist()):
                    row = keep[local]
                    for facility in _facility_type(classes[row], names[row], seed):
                        witness[int(tract_id), order.index(facility)] += 1.0
            building_meta = {"rows_total": int(pf.metadata.num_rows), "rows_scanned": scanned,
                             "assigned_centroids": assigned, "rows_excluded_or_null": excluded,
                             "source_filter": "exclusive provided OSM/Esri memberships; Microsoft and mixed rows excluded"}
        else:
            scanned = assigned = excluded = 0
            for rg in range(pf.metadata.num_row_groups):
                tab = pf.read_row_group(rg, columns=columns); scanned += tab.num_rows
                sources = tab.column("sources").to_pylist(); categories = tab.column("categories").to_pylist()
                wkbs = tab.column("geometry").to_pylist()
                keep = [i for i, src in enumerate(sources) if _rule_clean_poi(src) and wkbs[i] is not None]
                excluded += len(wkbs) - len(keep)
                if not keep: continue
                points = shapely.from_wkb([wkbs[i] for i in keep])
                points = shapely.transform(points, TRANSFORMER.transform, interleaved=False)
                left, right = tree.query(points, predicate="within"); assigned += len(left)
                for local, tract_id in zip(left.tolist(), right.tolist()):
                    row = keep[local]; primary = (categories[row] or {}).get("primary") if categories[row] else None
                    if not isinstance(primary, str): continue
                    primary = primary.strip().casefold()
                    for idx, facility in enumerate(order):
                        if primary in seed["poi_category_sets"][facility]: counts[facility][int(tract_id)] += 1
            poi_meta = {"rows_total": int(pf.metadata.num_rows), "rows_scanned": scanned,
                        "assigned_points": assigned, "rows_excluded_or_null": excluded,
                        "assignment": "provided point geometries strictly within one ACS tract",
                        "microsoft_membership_used": False}
    witnesses = witness * (np.stack([counts[x] for x in order], axis=1) == 0)
    return witnesses, counts, {"buildings": building_meta, "pois": poi_meta}


@implements("X1")
def x1(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["X1"]
    witnesses, poi_counts, readers = _read_x1(inputs, seed)
    values = soft_facility_definedness(witnesses)
    defined = np.ones(len(values), dtype=np.bool_)
    return IdeaOutput("X1", inputs.region, list(inputs.geoids),
        [Emission("F", "covariate", values, defined,
                  {"quantity": "soft probability of at least one fire/EMS/school facility reference from cross-theme witnesses"})],
        {"rows": len(values), "defined": int(defined.sum()), "probability_distribution": quantiles(values),
         "facility_witness_counts": {name: quantiles(witnesses[:, i]) for i, name in enumerate(["fire", "ems", "schools"])},
         "overture_place_counts": {name: quantiles(arr) for name, arr in poi_counts.items()},
         "readers": readers,
         "probability_mapping": "per type p_k=1-exp(-n_k) under a unit-rate Poisson witness model; P(any type)=1-product(1-p_k); uncalibrated soft proxy, not a hard defined flag"})
