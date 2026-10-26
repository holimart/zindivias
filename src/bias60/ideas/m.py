"""Family M: feature metadata and provenance covariates from provided Overture layers only.

No reference sources, labels, lab records or leaderboard values are read. Raw Overture rows are
streamed by row group; only tract-aggregated summaries are emitted.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

import numpy as np

from bias60.contracts import Emission, IdeaOutput
from bias60.ideas import implements
from bias60.ideas.sc_schema import normalize_names
from bias60.ideas._common import quantiles

WAY_VERSION = re.compile(r"^w\d+@(\d+)$", re.IGNORECASE)
FACILITY_NAME = re.compile(
    r"\b(fire\s+(?:station|department|company|rescue)|firehouse|ems\s+(?:station|base)|"
    r"ambulance\s+(?:station|service)|emergency\s+medical|elementary\s+school|"
    r"middle\s+school|high\s+school|primary\s+school|school)\b", re.IGNORECASE)
NAMED_CLASSES = frozenset({"motorway", "trunk", "primary", "secondary"})


def _iter_batches(inputs: Any, layer: str, columns: list[str]):
    """Stream exactly the declared columns from a provided Overture layer."""
    yield from inputs.iter_layer_batches(layer, columns)


def _spatial_index(inputs: Any):
    import shapely
    polygons = inputs.tract_geometries()
    return polygons, shapely.STRtree(polygons)


def _bbox_assignments(bboxes: list[Any], tree: Any, row_count: int) -> np.ndarray:
    """Assign bbox centers to at most one official tract; ties use stable tract index order."""
    import shapely

    assigned = np.full(row_count, -1, dtype=np.int64)
    valid_indices: list[int] = []
    xs: list[float] = []
    ys: list[float] = []
    for index, bbox in enumerate(bboxes):
        if not isinstance(bbox, dict):
            continue
        try:
            xmin, ymin = float(bbox["xmin"]), float(bbox["ymin"])
            xmax, ymax = float(bbox["xmax"]), float(bbox["ymax"])
        except (KeyError, TypeError, ValueError):
            continue
        if not np.isfinite([xmin, ymin, xmax, ymax]).all() or xmax < xmin or ymax < ymin:
            continue
        valid_indices.append(index)
        xs.append((xmin + xmax) / 2.0)
        ys.append((ymin + ymax) / 2.0)
    if not valid_indices:
        return assigned
    points = shapely.points(np.asarray(xs), np.asarray(ys))
    pairs = tree.query(points, predicate="intersects")
    for point_index, tract_index in zip(pairs[0].tolist(), pairs[1].tolist()):
        source_index = valid_indices[point_index]
        if assigned[source_index] < 0 or tract_index < assigned[source_index]:
            assigned[source_index] = tract_index
    return assigned


def _covariate(component: str, measure: str, values: np.ndarray, defined: np.ndarray,
               quantity: str) -> Emission:
    return Emission(component, f"covariate:{measure}", np.asarray(values, dtype=np.float64),
                    np.asarray(defined, dtype=np.bool_), {"quantity": quantity})


def _blank(n: int) -> np.ndarray:
    return np.zeros(n, dtype=np.float64)


def _normalized_dataset(sources: Any) -> set[str]:
    return {str(item.get("dataset") or "").strip().casefold()
            for item in (sources or []) if isinstance(item, dict)}


def _brand_link(brand: Any) -> bool:
    if not isinstance(brand, dict):
        return False
    if str(brand.get("wikidata") or "").strip():
        return True
    names = brand.get("names")
    return bool(isinstance(names, dict) and str(names.get("primary") or "").strip())


def _facility_name(names: Any) -> bool:
    return any(FACILITY_NAME.search(name) for name in normalize_names(names))


@implements("M1")
def m1(inputs) -> IdeaOutput:
    """Length-weighted OSM way edit depth for named and tertiary road classes."""
    import shapely
    from pyproj import Transformer

    n = len(inputs.geoids)
    geoids = list(inputs.geoids)
    polygons, _ = _spatial_index(inputs)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    metric_polygons = shapely.transform(polygons, transformer.transform, interleaved=False)
    tree = shapely.STRtree(metric_polygons)
    length = {"named": _blank(n), "tertiary": _blank(n)}
    version_length = {"named": _blank(n), "tertiary": _blank(n)}
    low_tertiary_length = _blank(n)
    rows = rows_classified = rows_versioned = 0
    rows_with_non_way_ids = 0

    for batch in _iter_batches(inputs, "roads", ["class", "sources", "geometry"]):
        classes = batch.column("class").to_pylist()
        sources = batch.column("sources").to_pylist()
        wkbs = batch.column("geometry").to_pylist()
        rows += len(classes)
        selected_indices: list[int] = []
        selected_versions: list[float] = []
        selected_group: list[str] = []
        for index, klass in enumerate(classes):
            road_class = str(klass or "").casefold()
            group = "named" if road_class in NAMED_CLASSES else "tertiary" if road_class == "tertiary" else None
            if group is None:
                continue
            rows_classified += 1
            versions: set[int] = set()
            for source in sources[index] or []:
                if not isinstance(source, dict) or str(source.get("dataset") or "").casefold() != "openstreetmap":
                    continue
                record_id = str(source.get("record_id") or "")
                match = WAY_VERSION.fullmatch(record_id)
                if match:
                    versions.add(int(match.group(1)))
                elif record_id:
                    rows_with_non_way_ids += 1
            if not versions or wkbs[index] is None:
                continue
            selected_indices.append(index)
            selected_versions.append(float(np.mean(sorted(versions))))
            selected_group.append(group)
            rows_versioned += 1
        for start in range(0, len(selected_indices), 40000):
            indices = selected_indices[start:start + 40000]
            if not indices:
                continue
            lines = shapely.from_wkb([wkbs[index] for index in indices])
            lines = shapely.transform(lines, transformer.transform, interleaved=False)
            pairs = tree.query(lines, predicate="intersects")
            if pairs.size == 0:
                continue
            clipped = shapely.intersection(lines[pairs[0]], metric_polygons[pairs[1]])
            lengths = shapely.length(clipped)
            for line_index, tract_index, piece_length in zip(pairs[0].tolist(), pairs[1].tolist(), lengths.tolist()):
                if not np.isfinite(piece_length) or piece_length <= 0.0:
                    continue
                source_index = start + line_index
                group = selected_group[source_index]
                version = selected_versions[source_index]
                length[group][tract_index] += piece_length
                version_length[group][tract_index] += piece_length * version
                if group == "tertiary" and version <= 2.0:
                    low_tertiary_length[tract_index] += piece_length

    named_defined = length["named"] > 0.0
    tertiary_defined = length["tertiary"] > 0.0
    named_mean = _blank(n)
    tertiary_mean = _blank(n)
    low_share = _blank(n)
    named_mean[named_defined] = version_length["named"][named_defined] / length["named"][named_defined]
    tertiary_mean[tertiary_defined] = version_length["tertiary"][tertiary_defined] / length["tertiary"][tertiary_defined]
    low_share[tertiary_defined] = low_tertiary_length[tertiary_defined] / length["tertiary"][tertiary_defined]
    return IdeaOutput("M1", inputs.region, geoids, [
        _covariate("R", "named_mean_version", named_mean, named_defined,
                   "length-weighted mean parsed OSM way version on named-class roads"),
        _covariate("R", "tertiary_mean_version", tertiary_mean, tertiary_defined,
                   "length-weighted mean parsed OSM way version on tertiary roads"),
        _covariate("R", "low_version_tertiary_share", low_share, tertiary_defined,
                   "share of OSM tertiary road length with parsed way version <= 2"),
    ], {
        "rows_scanned": rows, "named_or_tertiary_features": rows_classified,
        "features_with_parsed_osm_way_version": rows_versioned,
        "non_way_osm_record_ids_not_used": rows_with_non_way_ids,
        "version_parser": "OpenStreetMap source record_id matching ^w<digits>@<version>$; version is parsed only from documented way IDs",
        "multiple_way_versions_per_geometry": "deduplicate IDs and use their arithmetic mean; clip geometry length to official tract polygons",
        "coverage_named": int(named_defined.sum()), "coverage_tertiary": int(tertiary_defined.sum()),
        "no_target_or_reference_data_read": True,
    })


@implements("M2")
def m2(inputs) -> IdeaOutput:
    """Wikidata linkage shares in places and facility-like/infrastructure metadata."""
    n = len(inputs.geoids)
    _, tree = _spatial_index(inputs)
    total_poi = np.zeros(n, dtype=np.int64)
    linked_poi = np.zeros(n, dtype=np.int64)
    facility_poi = np.zeros(n, dtype=np.int64)
    facility_linked_poi = np.zeros(n, dtype=np.int64)
    infrastructure = np.zeros(n, dtype=np.int64)
    linked_infrastructure = np.zeros(n, dtype=np.int64)
    scanned_poi = scanned_infrastructure = outside = 0
    for batch in _iter_batches(inputs, "pois", ["bbox", "brand", "names"]):
        bboxes = batch.column("bbox").to_pylist(); brands = batch.column("brand").to_pylist()
        names = batch.column("names").to_pylist(); assigned = _bbox_assignments(bboxes, tree, len(bboxes))
        scanned_poi += len(bboxes)
        for i, tract in enumerate(assigned.tolist()):
            if tract < 0:
                outside += 1; continue
            total_poi[tract] += 1
            linked = bool(isinstance(brands[i], dict) and str(brands[i].get("wikidata") or "").strip())
            linked_poi[tract] += int(linked)
            if _facility_name(names[i]):
                facility_poi[tract] += 1
                facility_linked_poi[tract] += int(linked)
    for batch in _iter_batches(inputs, "infrastructure", ["bbox", "wikidata"]):
        bboxes = batch.column("bbox").to_pylist(); wikidata = batch.column("wikidata").to_pylist()
        assigned = _bbox_assignments(bboxes, tree, len(bboxes)); scanned_infrastructure += len(bboxes)
        for i, tract in enumerate(assigned.tolist()):
            if tract < 0:
                outside += 1; continue
            infrastructure[tract] += 1
            linked_infrastructure[tract] += int(bool(str(wikidata[i] or "").strip()))
    all_defined = total_poi > 0
    facility_denominator = facility_poi + infrastructure
    facility_defined = facility_denominator > 0
    all_share = _blank(n); facility_share = _blank(n)
    all_share[all_defined] = linked_poi[all_defined] / total_poi[all_defined]
    facility_share[facility_defined] = ((facility_linked_poi + linked_infrastructure)[facility_defined]
                                        / facility_denominator[facility_defined])
    return IdeaOutput("M2", inputs.region, list(inputs.geoids), [
        _covariate("Est", "all_wikidata_link_share", all_share, all_defined,
                   "share of assigned Overture places with a nonempty brand.wikidata link"),
        _covariate("F", "facility_wikidata_link_share", facility_share, facility_defined,
                   "combined linked share for facility-name places and provided infrastructure records"),
    ], {
        "rows_scanned_pois": scanned_poi, "rows_scanned_infrastructure": scanned_infrastructure,
        "assigned_pois": int(total_poi.sum()), "assigned_infrastructure": int(infrastructure.sum()),
        "outside_or_unassigned_rows": outside, "poi_defined_tracts": int(all_defined.sum()),
        "facility_defined_tracts": int(facility_defined.sum()),
        "facility_regex": FACILITY_NAME.pattern,
        "facility_link_share_denominator": "facility-name-regex places plus all provided infrastructure records; numerator is brand.wikidata places plus infrastructure.wikidata",
        "no_target_or_reference_data_read": True,
    })


def _address_key(addresses: Any) -> str | None:
    if not isinstance(addresses, list):
        return None
    first_locality = None
    for address in addresses:
        if not isinstance(address, dict):
            continue
        postcode = re.sub(r"\D", "", str(address.get("postcode") or ""))
        if len(postcode) >= 5:
            return f"zip:{postcode[:5]}"
        locality = re.sub(r"\s+", " ", str(address.get("locality") or "").strip().casefold())
        if locality and first_locality is None:
            first_locality = locality
    return f"locality:{first_locality}" if first_locality else None


def _neighbors(polygons: Any) -> list[set[int]]:
    import shapely
    tree = shapely.STRtree(polygons)
    pairs = tree.query(polygons, predicate="touches")
    output = [set() for _ in range(len(polygons))]
    for left, right in zip(pairs[0].tolist(), pairs[1].tolist()):
        if left != right:
            output[left].add(right)
    return output


@implements("M3")
def m3(inputs) -> IdeaOutput:
    """Flag address metadata inconsistent with local and adjacent tract postcode/locality support."""
    n = len(inputs.geoids)
    polygons, tree = _spatial_index(inputs)
    neighbors = _neighbors(polygons)
    records: list[tuple[int, str | None, bool]] = []
    local_counts: list[Counter[str]] = [Counter() for _ in range(n)]
    scanned = outside = 0
    for batch in _iter_batches(inputs, "pois", ["bbox", "addresses", "names"]):
        bboxes = batch.column("bbox").to_pylist(); addresses = batch.column("addresses").to_pylist()
        names = batch.column("names").to_pylist(); assigned = _bbox_assignments(bboxes, tree, len(bboxes))
        scanned += len(bboxes)
        for i, tract in enumerate(assigned.tolist()):
            if tract < 0:
                outside += 1; continue
            key = _address_key(addresses[i])
            records.append((tract, key, _facility_name(names[i])))
            if key is not None:
                local_counts[tract][key] += 1
    local_keys = [set(counts) for counts in local_counts]
    neighbor_keys = [set().union(*(local_keys[j] for j in neighbors[i]))
                     if neighbors[i] else set() for i in range(n)]
    dominant = [None if not counts else sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
                for counts in local_counts]
    total = np.zeros(n, dtype=np.int64); mismatched = np.zeros(n, dtype=np.int64)
    facility_total = np.zeros(n, dtype=np.int64); facility_mismatched = np.zeros(n, dtype=np.int64)
    neighbor_foreign = np.zeros(n, dtype=np.int64)
    for tract, key, facility in records:
        if key is None:
            continue
        total[tract] += 1
        foreign = key != dominant[tract] and key not in neighbor_keys[tract]
        mismatched[tract] += int(foreign)
        neighbor_foreign[tract] += int(key in neighbor_keys[tract])
        if facility:
            facility_total[tract] += 1
            facility_mismatched[tract] += int(foreign)
    defined = total > 0; facility_defined = facility_total > 0
    all_share = _blank(n); facility_share = _blank(n)
    all_share[defined] = mismatched[defined] / total[defined]
    facility_share[facility_defined] = facility_mismatched[facility_defined] / facility_total[facility_defined]
    return IdeaOutput("M3", inputs.region, list(inputs.geoids), [
        _covariate("Est", "address_mismatch_share", all_share, defined,
                   "share of address-bearing places whose postcode/locality differs from the local dominant key and is not observed in adjacent tracts"),
        _covariate("F", "facility_address_mismatch_share", facility_share, facility_defined,
                   "same mismatch share restricted to facility-name-regex places"),
    ], {
        "rows_scanned": scanned, "assigned_address_records": int(total.sum()),
        "outside_or_unassigned_rows": outside, "tracts_with_address_coverage": int(defined.sum()),
        "facility_address_records": int(facility_total.sum()), "tracts_with_facility_address_coverage": int(facility_defined.sum()),
        "neighbor_foreign_records_diagnostic": int(neighbor_foreign.sum()),
        "address_key_rule": "first valid 5-digit ZIP prefix; otherwise normalized locality; no key for missing address",
        "locality_rule": "majority key within tract, ties lexical; a non-dominant key also observed in any edge-touching tract is excluded as a possible cross-boundary address",
        "no_target_or_reference_data_read": True,
    })


@implements("M4")
def m4(inputs) -> IdeaOutput:
    """AllThePlaces chain-brand share and count per provided ACS housing unit."""
    n = len(inputs.geoids)
    _, tree = _spatial_index(inputs)
    total = np.zeros(n, dtype=np.int64); chain = np.zeros(n, dtype=np.int64); scanned = outside = 0
    for batch in _iter_batches(inputs, "pois", ["bbox", "brand", "sources"]):
        bboxes = batch.column("bbox").to_pylist(); brands = batch.column("brand").to_pylist()
        sources = batch.column("sources").to_pylist(); assigned = _bbox_assignments(bboxes, tree, len(bboxes))
        scanned += len(bboxes)
        for i, tract in enumerate(assigned.tolist()):
            if tract < 0:
                outside += 1; continue
            total[tract] += 1
            datasets = _normalized_dataset(sources[i])
            chain[tract] += int(_brand_link(brands[i]) and "alltheplaces" in datasets)
    housing = np.asarray(inputs.acs_housing_units(), dtype=np.float64)
    if housing.shape != (n,):
        raise ValueError("M4 ACS housing-unit vector is not aligned with official GEOIDs")
    share_defined = total > 0
    density_defined = np.isfinite(housing) & (housing > 0.0)
    share = _blank(n); per_housing = _blank(n)
    share[share_defined] = chain[share_defined] / total[share_defined]
    density_defined &= np.isfinite(housing)
    per_housing[density_defined] = chain[density_defined] / housing[density_defined]
    return IdeaOutput("M4", inputs.region, list(inputs.geoids), [
        _covariate("Est", "chain_brand_share", share, share_defined,
                   "AllThePlaces-sourced places with brand metadata / all assigned places"),
        _covariate("Est", "chain_count_per_housing_unit", per_housing, density_defined,
                   "AllThePlaces-sourced brand places per provided ACS housing unit; ACS is only a covariate"),
    ], {
        "rows_scanned": scanned, "assigned_places": int(total.sum()), "chain_brand_places": int(chain.sum()),
        "outside_or_unassigned_rows": outside, "tracts_with_place_coverage": int(share_defined.sum()),
        "housing_units_defined": int(density_defined.sum()), "housing_source": "provided census-acs-housing housing_units",
        "brand_rule": "brand.wikidata or brand.names.primary nonempty, and sources.dataset includes AllThePlaces",
        "no_housing_unit_denominator_floor_used": True, "no_target_or_reference_data_read": True,
    })


def _multi_property(sources: Any) -> bool:
    paths = {str(source.get("property") or "").strip() for source in (sources or [])
             if isinstance(source, dict) and str(source.get("property") or "").strip()}
    return len(paths) > 1


def _property_share(inputs: Any, layer: str, n: int) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    _, tree = _spatial_index(inputs)
    counts = np.zeros(n, dtype=np.int64); enriched = np.zeros(n, dtype=np.int64)
    scanned = outside = with_sources = 0
    for batch in _iter_batches(inputs, layer, ["bbox", "sources"]):
        bboxes = batch.column("bbox").to_pylist(); sources = batch.column("sources").to_pylist()
        assigned = _bbox_assignments(bboxes, tree, len(bboxes)); scanned += len(bboxes)
        for i, tract in enumerate(assigned.tolist()):
            if tract < 0:
                outside += 1; continue
            counts[tract] += 1
            with_sources += int(bool(sources[i]))
            enriched[tract] += int(_multi_property(sources[i]))
    defined = counts > 0
    share = _blank(n)
    share[defined] = enriched[defined] / counts[defined]
    return share, defined, {"rows_scanned": scanned, "assigned_features": int(counts.sum()),
                            "features_with_sources": with_sources, "multi_property_features": int(enriched.sum()),
                            "outside_or_unassigned_rows": outside, "defined_tracts": int(defined.sum()),
                            "assignment": "bbox center to one provided ACS tract; boundary ties to first tract index",
                            "property_rule": "count a feature if its sources list has >1 distinct nonempty property path"}


@implements("M5")
def m5(inputs) -> IdeaOutput:
    """Feature share with multiple distinct Overture source property paths."""
    n = len(inputs.geoids)
    building_share, building_defined, building_info = _property_share(inputs, "buildings", n)
    road_share, road_defined, road_info = _property_share(inputs, "roads", n)
    return IdeaOutput("M5", inputs.region, list(inputs.geoids), [
        _covariate("B", "multi_property_share", building_share, building_defined,
                   "share of assigned provided Overture buildings with more than one source property path"),
        _covariate("R", "multi_property_share", road_share, road_defined,
                   "share of assigned provided Overture roads with more than one source property path"),
    ], {"buildings": building_info, "roads": road_info, "no_target_or_reference_data_read": True})
