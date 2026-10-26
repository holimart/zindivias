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


def _x3_commercial_class(value: Any, seed: dict[str, Any]) -> bool:
    cls = str(value or "").casefold().replace("-", "_").replace(" ", "_")
    return cls in set(seed["commercial_building_classes"])


def x3_expected_missing(empty_area: np.ndarray, occupied_area: np.ndarray,
                        occupied_places: np.ndarray) -> np.ndarray:
    empty = np.asarray(empty_area, dtype=np.float64); occupied = np.asarray(occupied_area, dtype=np.float64)
    places = np.asarray(occupied_places, dtype=np.float64)
    if empty.shape != occupied.shape or empty.shape != places.shape or np.any(empty < 0) or np.any(occupied < 0) or np.any(places < 0):
        raise ValueError("X3 areas and occupied-place counts must be aligned nonnegative arrays")
    return empty * np.divide(places, occupied, out=np.zeros_like(empty), where=occupied > 0)


@implements("X3")
def x3(inputs) -> IdeaOutput:
    import pyarrow.parquet as pq
    import shapely
    from pyproj import Transformer
    seed = inputs.config["seeds"]["X3"]
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    tracts = shapely.transform(inputs.tract_geometries(), transformer.transform, interleaved=False)
    tract_tree = shapely.STRtree(tracts); n = len(inputs.geoids)
    poi_path = inputs.layer_path("pois"); poi_cols = seed["poi_columns"]
    poi_pf = pq.ParquetFile(inputs.filesystem.open_input_file(poi_path)); ems_points=[]; poi_tract_counts=np.zeros(n,dtype=np.int32)
    inputs.accessed["parquet_columns"].extend(f"{inputs.region}-overture-pois.parquet:{c}" for c in poi_cols)
    for rg in range(poi_pf.metadata.num_row_groups):
        tab=poi_pf.read_row_group(rg,columns=poi_cols); src=tab.column("sources").to_pylist()
        cats=tab.column("categories").to_pylist(); wkbs=tab.column("geometry").to_pylist()
        keep=[i for i,w in enumerate(wkbs) if w is not None and _rule_clean_poi(src[i]) and cats[i]
              and cats[i].get("primary") in set(seed["commercial_poi_categories"])]
        if keep:
            points=shapely.from_wkb([wkbs[i] for i in keep]); points=shapely.transform(points,transformer.transform,interleaved=False)
            left,right=tract_tree.query(points,predicate="within")
            np.add.at(poi_tract_counts,right,1);ems_points.extend(list(points))
    poi_tree=shapely.STRtree(ems_points) if ems_points else None
    mixed=poi_tract_counts>0
    total_area=np.zeros(n);empty_area=np.zeros(n);occupied_area=np.zeros(n);occupied_places=np.zeros(n)
    building_path=inputs.layer_path("buildings"); bcols=seed["building_columns"]
    bpf=pq.ParquetFile(inputs.filesystem.open_input_file(building_path))
    missing=sorted(set(bcols)-set(bpf.schema_arrow.names))
    if missing:raise ValueError(f"buildings lack X3 columns {missing}")
    inputs.accessed["parquet_columns"].extend(f"{inputs.region}-overture-buildings.parquet:{c}" for c in bcols)
    scanned=candidates=occupied=0;assignments=0
    for rg in range(bpf.metadata.num_row_groups):
        tab=bpf.read_row_group(rg,columns=bcols);src=tab.column("sources").to_pylist();classes=tab.column("class").to_pylist()
        wkbs=tab.column("geometry").to_pylist();scanned+=len(wkbs)
        keep=[i for i,w in enumerate(wkbs) if w is not None and _rule_clean_building(src[i])]
        if not keep:continue
        geoms=shapely.from_wkb([wkbs[i] for i in keep]);geoms=shapely.transform(geoms,transformer.transform,interleaved=False)
        areas=np.asarray(shapely.area(geoms),dtype=np.float64);centers=shapely.centroid(geoms)
        left,right=tract_tree.query(centers,predicate="within");assignments+=len(left)
        if not len(left):continue
        near_building=np.zeros(len(keep),dtype=bool);near_count=np.zeros(len(keep),dtype=np.int32)
        if poi_tree is not None:
            pairs=poi_tree.query(centers,predicate="dwithin",distance=float(seed["poi_distance_m"]))
            if pairs.size:
                near_building[pairs[0]]=True;np.add.at(near_count,pairs[0],1)
        for local,tract_id in zip(left.tolist(),right.tolist()):
            original=keep[local];tract_id=int(tract_id)
            is_commercial=_x3_commercial_class(classes[original],seed)
            is_candidate=is_commercial or (areas[local]>=float(seed["large_building_area_m2"]) and mixed[tract_id])
            if not is_candidate:continue
            candidates+=1;total_area[tract_id]+=areas[local]
            if near_building[local]:
                occupied+=1;occupied_area[tract_id]+=areas[local];occupied_places[tract_id]+=max(1,int(near_count[local]))
            else:empty_area[tract_id]+=areas[local]
    share=np.divide(empty_area,total_area,out=np.zeros_like(empty_area),where=total_area>0)
    expected=x3_expected_missing(empty_area,occupied_area,occupied_places);defined=np.ones(n,dtype=np.bool_)
    return IdeaOutput("X3",inputs.region,list(inputs.geoids),
        [Emission("Est","covariate",expected,defined)],
        {"rows":n,"defined":n,"empty_commercial_area_share":quantiles(share),"expected_missing_establishments":quantiles(expected),
         "commercial_candidates":candidates,"occupied_commercial_buildings":occupied,"buildings_scanned":scanned,
         "assigned_building_centroids":assignments,"provided_commercial_pois":int(poi_tract_counts.sum()),
         "poi_distance_m":float(seed["poi_distance_m"]),"large_building_area_m2":float(seed["large_building_area_m2"]),
         "commercial_classes":sorted(seed["commercial_building_classes"]),"microsoft_membership_used":False})


def _x5_semantics(feature_class: Any, subtype: Any, names: Any, source_tags: Any) -> tuple[bool, bool]:
    """Return (highway-number bridge/toll, emergency/helipad) semantic witnesses."""
    tag_text = " ".join(_strings(source_tags))
    if isinstance(source_tags, dict): tag_text += " " + " ".join(str(key) for key in source_tags)
    text = " ".join(_strings([feature_class, subtype, names])) + " " + tag_text
    text = text.casefold()
    highway_ref = bool(re.search(r"\b(?:i[- ]?\d+|us[- ]?\d+|u\.s\. ?\d+|(?:state )?(?:route|highway) ?\d+|sr[- ]?\d+|hwy ?\d+)\b", text))
    road = ("toll" in text) or (highway_ref and "bridge" in text)
    emergency = any(token in text for token in ("helipad", "heliport", "ambulance", "ems", "fire station", "emergency"))
    if "emergency room" in text or "emergency_room" in text or "urgent care" in text: emergency = False
    return road, emergency


@implements("X5")
def x5(inputs) -> IdeaOutput:
    import pyarrow.parquet as pq
    import shapely
    from pyproj import Transformer
    seed = inputs.config["seeds"]["X5"]
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    tracts = shapely.transform(inputs.tract_geometries(), transformer.transform, interleaved=False)
    tract_tree = shapely.STRtree(tracts); n = len(inputs.geoids)
    ems_categories = set(seed["ems_categories"]); ems_points = []
    poi_path = inputs.layer_path("pois"); poi_columns = seed["poi_columns"]
    poi_pf = pq.ParquetFile(inputs.filesystem.open_input_file(poi_path))
    inputs.accessed["parquet_columns"].extend(f"{inputs.region}-overture-pois.parquet:{c}" for c in poi_columns)
    for rg in range(poi_pf.metadata.num_row_groups):
        tab = poi_pf.read_row_group(rg, columns=poi_columns); categories = tab.column("categories").to_pylist()
        sources = tab.column("sources").to_pylist(); wkbs = tab.column("geometry").to_pylist()
        keep = [i for i, w in enumerate(wkbs) if w is not None and _rule_clean_poi(sources[i])
                and categories[i] and categories[i].get("primary") in ems_categories]
        if keep:
            points = shapely.from_wkb([wkbs[i] for i in keep])
            points = shapely.transform(points, transformer.transform, interleaved=False)
            ems_points.extend(list(points))
    ems_tree = shapely.STRtree(ems_points) if ems_points else None
    infra_path = inputs.layer_path("infrastructure"); columns = seed["infrastructure_columns"]
    pf = pq.ParquetFile(inputs.filesystem.open_input_file(infra_path))
    missing = sorted(set(columns) - set(pf.schema_arrow.names))
    if missing: raise ValueError(f"infrastructure lacks declared X5 columns {missing}")
    inputs.accessed["parquet_columns"].extend(f"{inputs.region}-overture-infrastructure.parquet:{c}" for c in columns)
    road_counts = np.zeros(n, dtype=np.float64); ems_counts = np.zeros(n, dtype=np.float64)
    scanned = assigned = 0; road_features = emergency_features = 0
    for rg in range(pf.metadata.num_row_groups):
        tab = pf.read_row_group(rg, columns=columns); wkbs = tab.column("geometry").to_pylist(); scanned += len(wkbs)
        classes = tab.column("class").to_pylist(); subtypes = tab.column("subtype").to_pylist()
        names = tab.column("names").to_pylist(); tags = tab.column("source_tags").to_pylist()
        keep = [i for i, w in enumerate(wkbs) if w is not None]
        if not keep: continue
        geoms = shapely.from_wkb([wkbs[i] for i in keep]); geoms = shapely.transform(geoms, transformer.transform, interleaved=False)
        centers = shapely.centroid(geoms); left, right = tract_tree.query(centers, predicate="within")
        assigned += len(left); candidates=[]
        for local, tract_id in zip(left.tolist(), right.tolist()):
            row = keep[local]; is_road, is_ems = _x5_semantics(classes[row], subtypes[row], names[row], tags[row])
            if is_road: road_counts[int(tract_id)] += 1.; road_features += 1
            if is_ems: candidates.append((local, int(tract_id))); emergency_features += 1
        if candidates:
            points = centers[np.asarray([i for i, _ in candidates], dtype=np.int64)]
            near = np.zeros(len(candidates), dtype=bool)
            if ems_tree is not None:
                pairs, distances = ems_tree.query_nearest(points, max_distance=float(seed["ems_distance_m"]),
                                                           return_distance=True, all_matches=False)
                near[np.unique(pairs[0])] = True
            for j, (_, tract_id) in enumerate(candidates):
                if not near[j]: ems_counts[tract_id] += 1.
    values_f = -np.expm1(-ems_counts); defined = np.ones(n, dtype=np.bool_)
    return IdeaOutput("X5", inputs.region, list(inputs.geoids),
        [Emission("R", "covariate", road_counts, defined), Emission("F", "covariate", values_f, defined)],
        {"rows": n, "defined": n, "road_witness_count": quantiles(road_counts),
         "emergency_without_near_ems_probability": quantiles(values_f), "infrastructure_rows_total": int(pf.metadata.num_rows),
         "infrastructure_rows_scanned": scanned, "infrastructure_rows_assigned": assigned,
         "road_semantic_witnesses": road_features, "emergency_features_checked": emergency_features,
         "provided_ems_places": len(ems_points), "ems_distance_m": float(seed["ems_distance_m"]),
         "semantic_rule": "highway-number bridges/toll features as R witnesses; emergency/helipad infrastructure without a provided Overture EMS place within 500m as soft F witness",
         "microsoft_poi_memberships_used": False})


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
