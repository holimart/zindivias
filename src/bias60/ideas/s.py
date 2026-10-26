"""Family S: spatial context diagnostics built only from provided inputs and idea outputs."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from bias60.contracts import Emission, IdeaOutput
from bias60.ideas import implements
from bias60.inputs import ProvidedInputs, official_scored_pairs, sha256
from bias60.ideas.sc_schema import _category_values, _semantic_type_match

SPATIAL_IDS = {"S1", "S2", "S3", "S4", "S5"}
COMPONENTS = ("R", "B", "F", "Est")


def _prerequisite_ids(inputs: Any) -> list[str]:
    return [item["id"] for item in inputs.config.get("__registry__", {}).get("ideas", [])
            if item.get("status") == "implemented" and item["id"] not in SPATIAL_IDS]


def _queen_neighbors(inputs: Any) -> list[np.ndarray]:
    import shapely
    polygons = inputs.tract_geometries()
    pairs = shapely.STRtree(polygons).query(polygons, predicate="touches")
    neighbors = [set() for _ in range(len(polygons))]
    for left, right in zip(pairs[0].tolist(), pairs[1].tolist()):
        if left != right:
            neighbors[left].add(right)
    return [np.asarray(sorted(row), dtype=np.int64) for row in neighbors]


def _lag(values: np.ndarray, defined: np.ndarray, neighbors: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    result = np.zeros(len(values), dtype=np.float64)
    valid = np.zeros(len(values), dtype=np.bool_)
    for row, adjacent in enumerate(neighbors):
        if adjacent.size == 0:
            continue
        usable = adjacent[defined[adjacent]]
        if usable.size:
            result[row] = float(np.mean(values[usable]))
            valid[row] = True
    return result, valid


def _moran_i(values: np.ndarray, defined: np.ndarray, neighbors: list[np.ndarray]) -> float | None:
    use = defined & np.isfinite(values)
    indices = np.flatnonzero(use)
    if indices.size < 3:
        return None
    mean = float(np.mean(values[use]))
    centered = values - mean
    numerator = 0.0
    s0 = 0
    for i in indices.tolist():
        adjacent = neighbors[i]
        adjacent = adjacent[use[adjacent]] if adjacent.size else adjacent
        if adjacent.size:
            numerator += float(centered[i] * np.sum(centered[adjacent]))
            s0 += int(adjacent.size)
    denominator = float(np.sum(centered[use] ** 2))
    if s0 == 0 or denominator <= 0.0:
        return None
    return float((indices.size / s0) * numerator / denominator)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


@implements("S1")
def s1(inputs) -> IdeaOutput:
    """Queen-neighbour lag of every previously emitted estimator channel, plus Moran diagnostics."""
    neighbors = _queen_neighbors(inputs)
    artifacts = inputs.idea_emissions(_prerequisite_ids(inputs))
    emissions = []
    moran: dict[str, float | None] = {}
    for idea_id, channels in sorted(artifacts.items()):
        for (component, source_channel), (values, defined) in sorted(channels.items()):
            lag, lag_defined = _lag(values, defined, neighbors)
            label = f"{idea_id}|{component}|{source_channel}"
            channel = f"covariate:lag_{_slug(label)}"
            emissions.append(Emission(component, channel, lag, lag_defined,
                                      {"quantity": f"queen-neighbour mean of {label}; self excluded"}))
            moran[label] = _moran_i(values, defined, neighbors)
    if not emissions:
        raise ValueError("S1 requires at least one prior idea artifact")
    return IdeaOutput("S1", inputs.region, list(inputs.geoids), emissions, {
        "source_idea_count": len(artifacts), "source_channel_count": len(emissions),
        "moran_i_by_source_channel": moran,
        "contiguity": "queen; polygon touches; self excluded; mean over defined neighbours",
        "no_target_or_reference_data_read": True,
    })


def _county_pool(counts: np.ndarray, state: np.ndarray, county: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    values = np.asarray(counts, dtype=np.float64)
    states = np.asarray(state).astype(str)
    counties = np.asarray(county).astype(str)
    defined = np.isfinite(values) & (states != "") & (states != "None") & (counties != "") & (counties != "None")
    pooled = np.zeros(values.shape, dtype=np.float64)
    lambdas = np.zeros(values.shape, dtype=np.float64)
    groups: dict[str, list[int]] = defaultdict(list)
    for index in np.flatnonzero(defined):
        groups[f"{states[index]}-{counties[index]}"].append(int(index))
    county_count = 0
    for indices in groups.values():
        local = values[indices]
        mean = float(np.mean(local))
        # Poisson sampling variance is count_i; excess county variance estimates tau^2.
        tau2 = max(0.0, float(np.var(local, ddof=1)) - mean) if len(indices) > 1 else 0.0
        for index in indices:
            sampling_variance = max(float(values[index]), 1.0)
            weight = tau2 / (tau2 + sampling_variance) if tau2 + sampling_variance > 0 else 0.0
            pooled[index] = weight * values[index] + (1.0 - weight) * mean
            lambdas[index] = weight
        county_count += 1
    return pooled, defined, {"counties": county_count, "mean_lambda": float(np.mean(lambdas[defined])) if defined.any() else 0.0,
                             "lambda_distribution": {"min": float(np.min(lambdas[defined])) if defined.any() else 0.0,
                                                     "median": float(np.median(lambdas[defined])) if defined.any() else 0.0,
                                                     "max": float(np.max(lambdas[defined])) if defined.any() else 0.0}}


@implements("S2")
def s2(inputs) -> IdeaOutput:
    """County empirical-Bayes pooling for two provided Overture tract counts."""
    seed = inputs.config["seeds"]["S2"]
    features = inputs.features(seed["count_columns"])
    strata = inputs.strata(seed["county_columns"])
    state = strata[seed["county_columns"][0]]
    county = strata[seed["county_columns"][1]]
    emissions = []
    diagnostics = {}
    for column in seed["count_columns"]:
        pooled, defined, stats = _county_pool(features[column], state, county)
        channel = "covariate:county_eb_" + _slug(column.removeprefix("overture_"))
        emissions.append(Emission("all", channel, pooled, defined,
                                  {"quantity": f"county-pooled provided count {column}"}))
        diagnostics[column] = stats
    return IdeaOutput("S2", inputs.region, list(inputs.geoids), emissions, {
        "count_columns": seed["count_columns"], "county_key": "STATEFP + COUNTYFP",
        "variance_model": "Poisson sampling variance max(count, 1); excess within-county sample variance estimates tau squared",
        "pooling": "lambda * tract count + (1-lambda) * county mean; no fitted target weights",
        "by_count": diagnostics, "no_target_or_reference_data_read": True,
    })


def _facility_types(categories: Any, basic: Any, taxonomy: Any, seed: dict[str, Any]) -> set[str]:
    category = categories if isinstance(categories, dict) else {}
    values = _category_values(category.get("primary"), category.get("alternate"), taxonomy, basic)
    return {kind for kind in seed["types"]
            if _semantic_type_match(kind, values[seed["definition"]], seed["category_sets"])}


def _boundary_leaks(points: Any, types_by_point: list[set[str]], polygons: Any,
                    types: list[str], distance_m: float, decay_m: float) -> dict[str, np.ndarray]:
    import shapely
    n = len(polygons)
    result = {kind: np.zeros(n, dtype=np.float64) for kind in types}
    if len(points) == 0:
        return result
    tree = shapely.STRtree(polygons)
    owners = np.full(len(points), -1, dtype=np.int64)
    inside = tree.query(points, predicate="within")
    for point_index, tract_index in zip(inside[0].tolist(), inside[1].tolist()):
        if owners[point_index] < 0 or tract_index < owners[point_index]:
            owners[point_index] = tract_index
    pairs = tree.query(points, predicate="dwithin", distance=distance_m)
    if pairs.size == 0:
        return result
    distances = shapely.distance(points[pairs[0]], polygons[pairs[1]])
    observed = {kind: np.zeros(n, dtype=np.int64) for kind in types}
    for point_index, owner in enumerate(owners.tolist()):
        if owner >= 0:
            for kind in types_by_point[point_index]:
                observed[kind][owner] += 1
    for point_index, tract_index, distance in zip(pairs[0].tolist(), pairs[1].tolist(), distances.tolist()):
        if tract_index == owners[point_index] or distance > distance_m:
            continue
        weight = float(np.exp(-distance / decay_m))
        for kind in types_by_point[point_index]:
            if observed[kind][tract_index] == 0:
                result[kind][tract_index] += weight
    return {kind: np.minimum(values, 1.0) for kind, values in result.items()}


@implements("S3")
def s3(inputs) -> IdeaOutput:
    """Estimate package-only facility point leakage across tract boundaries."""
    import shapely
    from pyproj import Transformer
    seed = inputs.config["seeds"]["S3"]
    geographies = inputs.tract_geometries()
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    polygons = shapely.transform(geographies, transformer.transform, interleaved=False)
    columns = seed["poi_columns"]
    points: list[Any] = []
    point_types: list[set[str]] = []
    rows_scanned = bad_geometry = 0
    for batch in inputs.iter_layer_batches(seed["poi_layer"], columns):
        basic_values = batch.column("basic_category").to_pylist()
        category_values = batch.column("categories").to_pylist()
        taxonomy_values = batch.column("taxonomy").to_pylist()
        geometry_values = batch.column("geometry").to_pylist()
        rows_scanned += len(geometry_values)
        for basic, category, taxonomy, wkb in zip(basic_values, category_values, taxonomy_values, geometry_values):
            types = _facility_types(category, basic, taxonomy, seed)
            if not types or wkb is None:
                continue
            try:
                point = shapely.from_wkb(wkb)
                if point is None or shapely.is_empty(point):
                    bad_geometry += 1
                    continue
                point = shapely.transform(point, transformer.transform, interleaved=False)
                points.append(point)
                point_types.append(types)
            except (ValueError, TypeError):
                bad_geometry += 1
    leaks = _boundary_leaks(np.asarray(points, dtype=object), point_types, polygons, seed["types"],
                            seed["distance_m"], seed["decay_m"])
    emissions = [Emission("F", f"covariate:boundary_leak_{kind}", leaks[kind],
                          np.ones(len(inputs.geoids), dtype=np.bool_),
                          {"quantity": f"capped sum exp(-distance/{seed['decay_m']}) for external provided {kind} places"})
                 for kind in seed["types"]]
    diagnostics = {kind: {"positive_tracts": int(np.sum(values > 0)),
                          "sum": float(np.sum(values)), "max": float(np.max(values)) if values.size else 0.0}
                   for kind, values in leaks.items()}
    return IdeaOutput("S3", inputs.region, list(inputs.geoids), emissions, {
        "poi_rows_scanned": rows_scanned, "candidate_facility_points": len(points),
        "invalid_candidate_geometries": bad_geometry, "distance_m": seed["distance_m"],
        "decay_m": seed["decay_m"], "leakage_by_type": diagnostics,
        "point_assignment": "provided ACS tract polygons in EPSG:5070; outside facilities contribute only to adjacent tracts with zero same-type places",
        "no_target_or_reference_data_read": True,
    })


def _read_component_gap_medians(inputs, regions: list[str], geoids_by_region: dict[str, list[str]]) -> dict[str, dict[str, np.ndarray]]:
    import pyarrow.parquet as pq
    region_positions = {region: {geoid: index for index, geoid in enumerate(geoids_by_region[region])}
                        for region in regions}
    columns: dict[str, dict[str, list[np.ndarray]]] = {
        region: {component: [] for component in COMPONENTS} for region in regions}
    for idea_id in _prerequisite_ids(inputs):
        path = Path(inputs.idea_output_dir) / f"{idea_id}.parquet"
        inputs._require_openable(str(path))
        if not path.is_file():
            raise FileNotFoundError(f"missing prerequisite idea artifact: {path}")
        parquet = pq.ParquetFile(path)
        digest = sha256(path)
        inputs.accessed.setdefault("idea_artifacts", []).append(f"{idea_id}:{digest}")
        for batch in parquet.iter_batches(batch_size=65536, columns=[
                "GEOID", "region", "idea", "component", "channel", "value", "defined"]):
            geoid_values = batch.column(0).to_pylist()
            region_values = batch.column(1).to_pylist()
            idea_values = batch.column(2).to_pylist()
            component_values = batch.column(3).to_pylist()
            channel_values = batch.column(4).to_pylist()
            value_values = batch.column(5).to_numpy(zero_copy_only=False).astype(np.float64)
            defined_values = batch.column(6).to_numpy(zero_copy_only=False).astype(np.bool_)
            for region in regions:
                positions = region_positions[region]
                selected_by_component = {component: [] for component in COMPONENTS}
                for index, row in enumerate(zip(region_values, idea_values, component_values,
                                                channel_values, defined_values)):
                    r, idea, component, channel, defined = row
                    if r == region and idea == idea_id and channel == "gap" and defined \
                            and component in selected_by_component:
                        selected_by_component[component].append(index)
                for component, selected in selected_by_component.items():
                    if not selected:
                        continue
                    aligned = np.full(len(geoids_by_region[region]), np.nan, dtype=np.float64)
                    for index in selected:
                        geoid = geoid_values[index]
                        if geoid in positions:
                            aligned[positions[geoid]] = value_values[index]
                    columns[region][component].append(aligned)
    output = {}
    for region in regions:
        output[region] = {}
        for component in COMPONENTS:
            matrices = columns[region][component]
            if matrices:
                with np.errstate(invalid="ignore"):
                    output[region][component] = np.nanmedian(np.column_stack(matrices), axis=1)
            else:
                output[region][component] = np.full(len(geoids_by_region[region]), np.nan)
    return output


def _standardize_features(features: list[np.ndarray]) -> np.ndarray:
    matrix = np.column_stack(features).astype(np.float64)
    for column in range(matrix.shape[1]):
        values = matrix[:, column]
        finite = np.isfinite(values)
        median = float(np.median(values[finite])) if finite.any() else 0.0
        values[~finite] = median
        scale = float(np.std(values))
        matrix[:, column] = (values - float(np.mean(values))) / (scale if scale > 1e-12 else 1.0)
    return matrix


def _peer_indices(target: np.ndarray, pool: np.ndarray, k: int, block: int = 128) -> np.ndarray:
    result = np.empty((len(target), min(k, len(pool))), dtype=np.int64)
    take = result.shape[1]
    for start in range(0, len(target), block):
        stop = min(len(target), start + block)
        delta = target[start:stop, None, :] - pool[None, :, :]
        distances = np.einsum("ijk,ijk->ij", delta, delta)
        chosen = np.argpartition(distances, take - 1, axis=1)[:, :take]
        for row in range(stop - start):
            order = np.lexsort((chosen[row], distances[row, chosen[row]]))
            result[start + row] = chosen[row, order]
    return result


@implements("S4")
def s4(inputs) -> IdeaOutput:
    """Cross-region nearest-peer differences for component-median existing gap estimates."""
    seed = inputs.config["seeds"]["S4"]
    regions = list(inputs.config["regions"])
    pairs = official_scored_pairs(inputs.workspace)
    geoids_by_region = {region: [] for region in regions}
    for geoid, region in pairs:
        if region in geoids_by_region:
            geoids_by_region[region].append(geoid)
    feature_matrices = {}
    for region in regions:
        region_inputs = ProvidedInputs(inputs.config, inputs.workspace, region, geoids_by_region[region])
        feature = region_inputs.features(seed["feature_columns"])
        strata = region_inputs.strata(seed["strata_columns"])
        land = np.maximum(np.asarray(strata["ALAND"], dtype=np.float64) / 1_000_000.0, 1e-6)
        population = np.maximum(np.asarray(strata["pop_total"], dtype=np.float64), 0.0)
        buildings = np.maximum(np.asarray(feature["overture_buildings"], dtype=np.float64), 0.0)
        roads = np.maximum(np.asarray(feature["overture_named_road_km_5070"], dtype=np.float64), 0.0)
        ruca = np.asarray(strata["ruca_primary"], dtype=np.float64)
        feature_matrices[region] = [np.log1p(population / land), np.log1p(buildings / land),
                                    np.log1p(roads / land), ruca]
        for kind in ("parquet_columns", "csv_columns", "layers", "caches"):
            inputs.accessed[kind].extend(region_inputs.accessed.get(kind, []))
    all_features = _standardize_features([np.concatenate([feature_matrices[r][j] for r in regions])
                                          for j in range(len(seed["feature_columns"]) + 2)])
    boundaries = {}
    cursor = 0
    for region in regions:
        size = len(geoids_by_region[region])
        boundaries[region] = all_features[cursor:cursor + size]
        cursor += size
    values_by_region = _read_component_gap_medians(inputs, regions, geoids_by_region)
    output_values = {component: np.zeros(len(inputs.geoids), dtype=np.float64) for component in COMPONENTS}
    output_defined = {component: np.zeros(len(inputs.geoids), dtype=np.bool_) for component in COMPONENTS}
    target_features = boundaries[inputs.region]
    peer_features = np.concatenate([boundaries[region] for region in regions if region != inputs.region], axis=0)
    peer_regions = [region for region in regions if region != inputs.region]
    nearest = _peer_indices(target_features, peer_features, seed["peer_k"])
    own = values_by_region[inputs.region]
    for component in COMPONENTS:
        peers = values_by_region
        matrix = np.concatenate([peers[region][component] for region in peer_regions])
        for row, choices in enumerate(nearest):
            value = own[component][row]
            peer_values = matrix[choices]
            peer_values = peer_values[np.isfinite(peer_values)]
            if np.isfinite(value) and peer_values.size:
                output_values[component][row] = value - float(np.median(peer_values))
                output_defined[component][row] = True
    emissions = [Emission(component, "covariate:peer_gap_delta", output_values[component], output_defined[component],
                          {"quantity": "component median prior-gap estimates minus median of cross-region nearest peers"})
                 for component in COMPONENTS]
    return IdeaOutput("S4", inputs.region, list(inputs.geoids), emissions, {
        "peer_k": seed["peer_k"], "peer_scope": "other three provided study regions only; no lab regions",
        "features": ["log population density", "log Overture building density", "log named-road density", "RUCA primary"],
        "peer_features_standardized": "global finite-value mean/std; median imputation for missing values",
        "gap_value": "per-tract median across previously emitted gap channels for each component",
        "component_coverage": {component: int(output_defined[component].sum()) for component in COMPONENTS},
        "no_target_or_reference_data_read": True,
    })


def _local_geary(values: np.ndarray, defined: np.ndarray, neighbors: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    use = defined & np.isfinite(values)
    n = int(use.sum())
    out = np.zeros(len(values), dtype=np.float64)
    valid_out = np.zeros(len(values), dtype=np.bool_)
    if n < 3:
        return out, valid_out
    centered = values - float(np.mean(values[use]))
    denominator = float(np.sum(centered[use] ** 2))
    valid_neighbors: list[np.ndarray] = []
    s0 = 0
    for i, adjacent in enumerate(neighbors):
        valid = adjacent[use[adjacent]] if adjacent.size else adjacent
        valid_neighbors.append(valid)
        if use[i] and valid.size:
            s0 += int(valid.size)
    if denominator <= 0.0 or s0 == 0:
        return out, valid_out
    factor = (n - 1.0) / (2.0 * s0 * denominator)
    for i, adjacent in enumerate(valid_neighbors):
        if not use[i] or adjacent.size == 0:
            continue
        out[i] = factor * float(np.sum((values[i] - values[adjacent]) ** 2))
        valid_out[i] = True
    return out, valid_out


@implements("S5")
def s5(inputs) -> IdeaOutput:
    """Local Geary disagreement across neighbouring tracts' already-emitted gap estimates."""
    neighbors = _queen_neighbors(inputs)
    artifacts = inputs.idea_emissions(_prerequisite_ids(inputs))
    rows: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    sources = []
    for idea_id, channels in sorted(artifacts.items()):
        for (component, channel), (values, defined) in sorted(channels.items()):
            if channel != "gap":
                continue
            local, valid = _local_geary(values, defined, neighbors)
            rows.append(local)
            masks.append(valid)
            sources.append(f"{idea_id}|{component}|{channel}")
    if not rows:
        raise ValueError("S5 requires prior gap-channel idea artifacts")
    matrix = np.vstack(rows)
    mask = np.vstack(masks)
    values = np.zeros(matrix.shape[1], dtype=np.float64)
    defined = mask.any(axis=0)
    values[defined] = np.sum(np.where(mask, matrix, 0.0), axis=0)[defined] / mask.sum(axis=0)[defined]
    return IdeaOutput("S5", inputs.region, list(inputs.geoids), [
        Emission("C", "covariate:local_geary_gap", values, defined,
                 {"quantity": "mean local Geary contribution across prior defined gap channels"})
    ], {
        "gap_channels": len(rows), "source_channels": sources,
        "contiguity": "queen; normalized local Geary contribution; pairwise defined neighbours",
        "limit": "gap outputs are used because existing artifacts do not retain unclipped O/E ratios; no ratio is reconstructed",
        "no_target_or_reference_data_read": True,
    })


# Keep registration imports visible in module-level coverage checks.
__all__ = ["s1", "s2", "s3", "s4", "s5"]
