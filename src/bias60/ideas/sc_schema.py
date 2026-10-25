"""Schema and definition completeness ideas Sc1-Sc4, using package roads and Overture POIs only."""
from __future__ import annotations

import re
from typing import Any

import numpy as np

from bias60.contracts import Emission, IdeaOutput
from bias60.ideas import implements
from bias60.ideas._common import quantiles


def normalize_names(value: Any) -> tuple[str, ...]:
    """Return meaningful name/ref strings from Overture scalar, list, or nested name records."""
    found: list[str] = []
    if isinstance(value, str):
        text = value.strip()
        if text:
            found.append(text)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(normalize_names(item))
    elif isinstance(value, dict):
        for key in ("primary", "common", "rules", "name", "value", "ref"):
            if key in value:
                if key == "common" and isinstance(value[key], dict):
                    for item in value[key].values():
                        found.extend(normalize_names(item))
                else:
                    found.extend(normalize_names(value[key]))
    unique = tuple(dict.fromkeys(x for x in found if x))
    return unique


def _named_route(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(_named_route(x) for x in value)
    if isinstance(value, dict):
        return any(normalize_names(value.get(k)) for k in ("ref", "name", "symbol"))
    return False


def _unknown_segment_selected(klass: Any, names: Any, routes: Any, length_m: float,
                             seed: dict[str, Any]) -> bool:
    if str(klass or "").casefold() not in seed["unknown_classes"]:
        return False
    return bool(normalize_names(names) or _named_route(routes) or length_m > seed["long_segment_m"])


def _is_fire_station(name: str) -> bool:
    s = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
    if any(x in s for x in ("grill", "restaurant", "bar ", "pub ", "firehouse subs", "museum", "theatre")):
        return False
    return bool(re.search(r"\b(fire department|fire station|fire company|fire and rescue|fire rescue|firehouse)\b", s))


def _is_ems_station(name: str) -> bool:
    s = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
    if any(x in s for x in ("emergency room", "emergency medicine", "urgent care", "hospital", "grill", "restaurant")):
        return False
    return bool(re.search(r"\b(ambulance|ems station|emergency medical services|paramedic station)\b", s))


def _is_school(name: str) -> bool:
    s = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
    if any(x in s for x in ("school of rock", "driving school", "beauty school", "dance school", "school supply")):
        return False
    return bool(re.search(r"\b(elementary|middle|high|primary|secondary|charter|public|private|montessori) school\b|\bschool\b", s))


def _facility_type(name: str) -> str | None:
    for typ, matcher in (("fire", _is_fire_station), ("ems", _is_ems_station), ("schools", _is_school)):
        if matcher(name):
            return typ
    return None


def _source_allowed(sources: Any) -> bool:
    for source in sources or []:
        dataset = str((source or {}).get("dataset") or "").strip().casefold()
        if dataset == "microsoft":
            return False
    return True


def _category_values(primary: Any, alternate: Any, taxonomy: Any, basic: Any) -> dict[str, set[str]]:
    def values(v: Any) -> set[str]:
        if isinstance(v, str):
            return {v.strip().casefold()} if v.strip() else set()
        if isinstance(v, (list, tuple)):
            out: set[str] = set()
            for item in v:
                out.update(values(item))
            return out
        return set()
    tax = (taxonomy or {}) if isinstance(taxonomy, dict) else {}
    return {
        "primary": values(primary),
        "primary_alternate": values(primary) | values(alternate),
        "taxonomy_hierarchy": values(tax.get("hierarchy")) | values(tax.get("primary")) | values(tax.get("alternates")),
        "basic_category": values(basic),
    }


def _semantic_type_match(typ: str, categories: set[str], category_sets: dict[str, list[str]]) -> bool:
    return bool(categories & set(category_sets[typ]))


def _spatial_data(inputs: Any):
    import shapely
    from pyproj import Transformer
    geo = inputs.tract_geometries()
    tr = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    metric = shapely.transform(geo, tr.transform, interleaved=False)
    return metric, shapely.STRtree(metric), tr


def _scan_road_measure(inputs: Any, layer: str, seed: dict[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    import pyarrow.parquet as pq
    import shapely
    from collections import defaultdict
    path = inputs.layer_path(layer)
    pf = pq.ParquetFile(inputs.filesystem.open_input_file(path))
    cols = seed["road_columns"]
    schema = set(pf.schema_arrow.names)
    missing = sorted(set(cols) - schema)
    if missing:
        raise ValueError(f"{layer} road schema missing columns: {missing}")
    inputs.accessed["parquet_columns"].extend(f"{inputs.region}-overture-{layer}.parquet:{c}" for c in cols)
    tracts, tree, transformer = _spatial_data(inputs)
    n = len(inputs.geoids)
    named = np.zeros(n, dtype=np.float64)
    unnamed = np.zeros(n, dtype=np.float64)
    unknown_major = np.zeros(n, dtype=np.float64)
    rows_scanned = rows_selected = routes = names_present = 0
    classes = set(seed.get("named_classes", []))
    for rg in range(pf.metadata.num_row_groups):
        tab = pf.read_row_group(rg, columns=cols)
        cl = tab.column("class").to_pylist(); nm = tab.column("names").to_pylist()
        rt = tab.column("routes").to_pylist() if "routes" in cols else [None] * len(cl)
        wkbs = tab.column("geometry").to_pylist(); rows_scanned += len(cl)
        keep: list[int] = []; modes: list[str] = []
        for i, klass in enumerate(cl):
            klass = str(klass or "").casefold()
            if layer == seed.get("named_layer") and klass in classes:
                is_named = bool(normalize_names(nm[i]))
                keep.append(i); modes.append("named" if is_named else "unnamed")
                names_present += int(is_named)
            elif layer == seed.get("unknown_layer") and klass in seed.get("unknown_classes", []):
                route = _named_route(rt[i]); name = bool(normalize_names(nm[i]))
                if route: routes += 1
                if name: names_present += 1
                # Geometry length is checked after WKB decoding; route/name candidates are selected now.
                if route or name:
                    keep.append(i); modes.append("unknown-route")
                else:
                    keep.append(i); modes.append("unknown-length")
        rows_selected += len(keep)
        for start in range(0, len(keep), 50000):
            ids = keep[start:start + 50000]
            if not ids: continue
            valid = [i for i in ids if wkbs[i] is not None]
            if not valid: continue
            lines = shapely.from_wkb([wkbs[i] for i in valid])
            lines = shapely.transform(lines, transformer.transform, interleaved=False)
            full_lengths = shapely.length(lines)
            indexes = np.arange(start, start + len(ids))
            id_to_mode = {ids[j]: modes[indexes[j] - start] for j in range(len(ids))}
            candidates = [(valid[j], lines[j], id_to_mode[valid[j]]) for j in range(len(valid))]
            pairs = tree.query(lines, predicate="intersects")
            if pairs.size == 0: continue
            li, ti = pairs
            clips = shapely.intersection(lines[li], tracts[ti])
            lengths = shapely.length(clips)
            # Sc4 uses the unfiltered whole-segment length predicate OR any parsed route/name.
            for k, (line_i, tract_i, length) in enumerate(zip(li.tolist(), ti.tolist(), lengths.tolist())):
                if not np.isfinite(length) or length <= 0: continue
                source_i = valid[line_i]
                mode = id_to_mode[source_i]
                if layer == seed.get("unknown_layer"):
                    full = float(full_lengths[line_i])
                    if _unknown_segment_selected(cl[source_i], nm[source_i], rt[source_i], full, seed):
                        unknown_major[tract_i] += length / 1000.0
                elif mode == "named": named[tract_i] += length / 1000.0
                else: unnamed[tract_i] += length / 1000.0
    return {"named_km": named, "unnamed_km": unnamed, "unknown_major_km": unknown_major}, {
        "region": inputs.region, "layer": layer, "rows_total": int(pf.metadata.num_rows),
        "rows_scanned": rows_scanned, "rows_selected": rows_selected,
        "route_or_name_candidates": routes, "nonempty_name_values": names_present,
        "crs": "EPSG:5070", "microsoft_road_memberships_used": False,
    }


@implements("Sc1")
def sc1(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Sc1"]
    m, d = _scan_road_measure(inputs, seed["named_layer"], seed)
    denom = m["named_km"] + m["unnamed_km"]
    defined = np.isfinite(denom) & (denom > 0)
    gap = np.zeros(len(inputs.geoids), dtype=np.float64)
    gap[defined] = np.clip(m["unnamed_km"][defined] / denom[defined], 0.0, 1.0)
    return IdeaOutput("Sc1", inputs.region, list(inputs.geoids), [Emission("R", "gap", gap, defined,
        {"quantity": "unnamed named-class length / all named-class length"})],
        {"rows": len(gap), "defined": int(defined.sum()), "unnamed_km": float(m["unnamed_km"].sum()),
         "named_km": float(m["named_km"].sum()), "reader": d,
         "namedness_rule": "any nonempty normalized primary/common/rule name value"})


@implements("Sc4")
def sc4(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Sc4"]
    m, d = _scan_road_measure(inputs, seed["unknown_layer"], seed)
    values = m["unknown_major_km"]
    defined = np.ones(len(values), dtype=bool)
    return IdeaOutput("Sc4", inputs.region, list(inputs.geoids), [Emission("R", "covariate", values, defined,
        {"quantity": "km of unknown/road class with route/name or whole-segment length > 2 km"})],
        {"rows": len(values), "defined": len(values), "candidate_km": float(values.sum()),
         "reader": d, "route_name_or_length_rule": seed["route_name_or_length_rule"]})


@implements("Sc2")
def sc2(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Sc2"]
    data, meta = _scan_pois(inputs, seed)
    witnesses = data["witnesses"]
    defined = np.ones(len(inputs.geoids), dtype=bool)
    return IdeaOutput("Sc2", inputs.region, list(inputs.geoids), [Emission("F", "covariate", witnesses, defined,
        {"quantity": "semantically screened facility-name witnesses in unclassified/generic places, gated by zero Overture count for type"})],
        {"rows": len(witnesses), "defined": len(witnesses), "witness_count": float(witnesses.sum()),
         "by_type": data["witness_by_type"], "reader": meta,
         "false_positive_guards": seed["false_positive_guards"]})


@implements("Sc3")
def sc3(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Sc3"]
    data, meta = _scan_pois(inputs, seed)
    union = data["union_counts"]
    gaps = data["definition_gaps"]
    defined = data["union_defined"]
    mean = np.zeros(len(inputs.geoids), dtype=np.float64); spread = np.zeros_like(mean)
    for i in range(len(mean)):
        local = gaps[:, :, i].ravel()
        ok = np.isfinite(local)
        if ok.any():
            mean[i] = float(local[ok].mean()); spread[i] = float(local[ok].max() - local[ok].min())
    return IdeaOutput("Sc3", inputs.region, list(inputs.geoids), [
        Emission("F", "gap", mean, defined, {"mapping": "mean of four schema-exclusion shares relative to union-category candidates; not organiser target gap"}),
        Emission("F", "covariate", spread, defined, {"quantity": "range of four category-schema exclusion shares"})],
        {"rows": len(mean), "defined": int(defined.sum()), "union_candidates": int(union.sum()),
         "definitions": seed["definitions"], "definition_gaps": {name: quantiles(gaps[j][np.isfinite(gaps[j])]) for j, name in enumerate(seed["definitions"])},
         "reader": meta, "interpretation": "schema sensitivity proxy only; denominator is package-observed union, never a removed reference layer"})


def _scan_pois(inputs: Any, seed: dict[str, Any]):
    import pyarrow.parquet as pq
    import shapely
    path = inputs.layer_path("pois")
    pf = pq.ParquetFile(inputs.filesystem.open_input_file(path))
    cols = seed["poi_columns"]
    if not set(cols).issubset(pf.schema_arrow.names):
        raise ValueError(f"POI schema missing columns: {sorted(set(cols)-set(pf.schema_arrow.names))}")
    inputs.accessed["parquet_columns"].extend(f"{inputs.region}-overture-pois.parquet:{c}" for c in cols)
    tracts, tree, transformer = _spatial_data(inputs)
    n = len(inputs.geoids); defs = seed["definitions"]
    count = {t: {name: np.zeros(n, dtype=np.float64) for name in defs} for t in seed["types"]}
    witness_by_type = {t: np.zeros(n, dtype=np.float64) for t in seed["types"]}
    rows_scanned = assigned = microsoft = bad_geom = unassigned = 0
    for rg in range(pf.metadata.num_row_groups):
        tab = pf.read_row_group(rg, columns=cols)
        names = tab.column("names").to_pylist(); cats = tab.column("categories").to_pylist()
        basic = tab.column("basic_category").to_pylist(); taxonomy = tab.column("taxonomy").to_pylist()
        sources = tab.column("sources").to_pylist(); wkbs = tab.column("geometry").to_pylist(); rows_scanned += len(wkbs)
        valid = [i for i, w in enumerate(wkbs) if w is not None and _source_allowed(sources[i])]
        excluded_ms = sum(1 for i in range(len(wkbs)) if not _source_allowed(sources[i]))
        microsoft += excluded_ms
        bad_geom += len(wkbs) - excluded_ms - len(valid)
        if not valid: continue
        points = shapely.from_wkb([wkbs[i] for i in valid]); points = shapely.transform(points, transformer.transform, interleaved=False)
        pi, ti = tree.query(points, predicate="within"); assigned += len(pi)
        unassigned += len(points) - len(pi)
        for point_idx, tract_idx in zip(pi.tolist(), ti.tolist()):
            i = valid[point_idx]; cat = cats[i] or {}; primary = cat.get("primary"); alt = cat.get("alternate") or []
            category_sets = _category_values(primary, alt, taxonomy[i], basic[i])
            for typ in seed["types"]:
                for definition in defs:
                    if _semantic_type_match(typ, category_sets[definition], seed["facility_category_sets"]):
                        count[typ][definition][tract_idx] += 1.0
            candidate = _facility_type(" ".join(normalize_names(names[i])))
            if candidate and candidate in seed["types"]:
                generic = not str(primary or "").strip() or str(primary).strip().casefold() in seed.get("generic_categories", [])
                witness_def = seed.get("witness_category_definition", defs[0])
                if generic and count[candidate][witness_def][tract_idx] == 0:
                    witness_by_type[candidate][tract_idx] += 1.0
    per_def_total = np.vstack([sum(count[t][d] for t in seed["types"]) for d in defs])
    union_by_type = np.vstack([np.max(np.vstack([count[t][d] for d in defs]), axis=0)
                               for t in seed["types"]])
    union_total = union_by_type.sum(axis=0)
    gap_by_definition = np.full((len(defs), len(seed["types"]), n), np.nan, dtype=np.float64)
    for j, definition in enumerate(defs):
        for k, typ in enumerate(seed["types"]):
            valid_union = union_by_type[k] > 0
            gap_by_definition[j, k, valid_union] = 1.0 - count[typ][definition][valid_union] / union_by_type[k, valid_union]
    witness = np.zeros(n, dtype=np.float64)
    by_type_report = {}
    for typ in seed["types"]:
        zero = count[typ][seed.get("witness_category_definition", defs[0])] == 0
        witness_by_type[typ] *= zero
        witness += witness_by_type[typ]
        by_type_report[typ] = {"tracts_with_witness": int(np.sum(witness_by_type[typ] > 0)),
                               "witnesses": float(witness_by_type[typ].sum())}
    return {"witnesses": witness, "witness_by_type": by_type_report,
            "union_counts": union_total, "definition_gaps": gap_by_definition,
            "union_defined": union_total > 0,
            "counts_by_type_definition": count}, {
        "region": inputs.region, "layer": "pois", "rows_total": int(pf.metadata.num_rows),
        "rows_scanned": rows_scanned, "assigned_points": assigned,
        "microsoft_membership_rows_excluded": microsoft, "null_geometry_rows": bad_geom,
        "boundary_or_outside_points": unassigned,
        "definition_counts": {d: float(per_def_total[j].sum()) for j, d in enumerate(defs)},
        "point_assignment": "provided EPSG:4326 point strictly within provided ACS tract; boundary/outside excluded",
        "source_filter": "exclude Microsoft memberships before point geometry decode",
    }
