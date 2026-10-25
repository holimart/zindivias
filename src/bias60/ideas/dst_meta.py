"""Dst4/5 post-estimator aggregation and organizer-moment calibration."""
from __future__ import annotations

import math

import numpy as np
import pyarrow.parquet as pq

from bias60.contracts import Emission, IdeaOutput
from bias60.ideas import implements
from bias60.ideas._common import as_float, quantiles


def _component_vector(inputs, seed: dict) -> tuple[np.ndarray, list[str]]:
    index = {geoid: position for position, geoid in enumerate(inputs.geoids)}
    component_matrices = {component: [] for component in seed["components"]}
    for idea_id in seed["source_ideas"]:
        path = inputs.workspace / "ideas" / f"{idea_id}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"Dst4 requires prior registered idea artifact: {path}")
        table = pq.read_table(path, columns=["GEOID", "region", "component", "channel", "value", "defined"])
        for row in table.to_pylist():
            if row["region"] != inputs.region or row["channel"] != "gap":
                continue
            component = row["component"]
            if component not in component_matrices:
                continue
            component_matrices[component].append((row["GEOID"], float(row["value"])
                                                  if row["defined"] else float("nan")))
    matrix = np.full((len(inputs.geoids), len(seed["components"])), np.nan, dtype=np.float64)
    for column, component in enumerate(seed["components"]):
        value_lists: list[list[float]] = [[] for _ in inputs.geoids]
        for geoid, value in component_matrices[component]:
            if geoid in index and math.isfinite(value):
                value_lists[index[geoid]].append(value)
        for row, values in enumerate(value_lists):
            if values:
                matrix[row, column] = float(np.mean(values))
    # Fixed gap g=clip(1-O/E,0,1) implies log(O/E)=log(1-g) wherever the ratio is identifiable.
    epsilon = float(seed["gap_log_epsilon"])
    matrix = np.log(np.clip(1.0 - matrix, epsilon, 1.0))
    return matrix, seed["components"]


def _fit_diagonal_mixture(values: np.ndarray, max_iter: int = 200,
                          variance_floor: float = 1e-3) -> tuple[np.ndarray, dict]:
    """Deterministic two-class diagonal Gaussian mixture; lower log(O/E) class is incomplete."""
    x = np.asarray(values, dtype=np.float64)
    if x.ndim != 2 or x.shape[0] < 4 or not np.isfinite(x).all():
        raise ValueError("mixture requires at least four complete finite rows")
    center = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    z = (x - center) / scale
    score = z.mean(axis=1)
    order = np.argsort(score, kind="mergesort")
    means = np.stack([z[order[len(order) // 4]], z[order[(3 * len(order)) // 4]]])
    variances = np.maximum(np.tile(z.var(axis=0), (2, 1)), variance_floor)
    weights = np.array([0.5, 0.5], dtype=np.float64)
    responsibility = np.full((len(z), 2), 0.5, dtype=np.float64)
    iterations = 0
    for iterations in range(1, max_iter + 1):
        logp = np.empty((len(z), 2), dtype=np.float64)
        for k in range(2):
            logp[:, k] = (math.log(max(weights[k], 1e-12)) - 0.5 * np.sum(
                np.log(2.0 * math.pi * variances[k]) + (z - means[k]) ** 2 / variances[k], axis=1))
        logp -= logp.max(axis=1, keepdims=True)
        responsibility = np.exp(logp)
        responsibility /= responsibility.sum(axis=1, keepdims=True)
        mass = responsibility.sum(axis=0)
        new_weights = mass / len(z)
        new_means = (responsibility.T @ z) / mass[:, None]
        new_variances = np.maximum(
            (responsibility.T @ (z ** 2) / mass[:, None]) - new_means ** 2, variance_floor)
        delta = max(float(np.max(np.abs(new_means - means))),
                    float(np.max(np.abs(new_variances - variances))))
        weights, means, variances = new_weights, new_means, new_variances
        if delta < 1e-8:
            break
    incomplete_class = int(np.argmin(means.mean(axis=1)))
    return responsibility[:, incomplete_class], {
        "iterations": iterations, "weights": weights.tolist(), "means_standardized": means.tolist(),
        "variances_standardized": variances.tolist(), "incomplete_class": incomplete_class,
        "fit_rows": int(len(z)), "feature_center": center.tolist(), "feature_scale": scale.tolist(),
    }


@implements("Dst4")
def dst4(inputs) -> IdeaOutput:
    seed = inputs.config["seeds"]["Dst4"]
    matrix, components = _component_vector(inputs, seed)
    strata = inputs.strata([seed["ruca_column"]])
    ruca = as_float(strata[seed["ruca_column"]])
    # Fit separate urban / suburban / rural mixtures to avoid learning geography as completeness.
    groups = np.full(ruca.shape, -1, dtype=np.int8)
    groups[np.isin(ruca, [1, 2, 3])] = 0
    groups[np.isin(ruca, [4, 5, 6])] = 1
    groups[np.isin(ruca, [7, 8, 9, 10])] = 2
    values = np.zeros(len(inputs.geoids), dtype=np.float64)
    defined = np.zeros(len(inputs.geoids), dtype=np.bool_)
    diagnostics = {"rows": int(len(inputs.geoids)), "components": components,
                   "source_ideas": seed["source_ideas"], "groups": {},
                   "undefined_reasons": {"incomplete_component_vector_or_unknown_ruca": 0}}
    for group_id, group_name in enumerate(("urban", "suburban", "rural")):
        mask = (groups == group_id) & np.isfinite(matrix).all(axis=1)
        positions = np.flatnonzero(mask)
        if len(positions) < int(seed["minimum_group_rows"]):
            diagnostics["groups"][group_name] = {"fit_rows": int(len(positions)), "status": "below_frozen_fit_floor"}
            continue
        posterior, details = _fit_diagonal_mixture(
            matrix[positions], int(seed["max_em_iterations"]), float(seed["variance_floor"]))
        values[positions] = posterior
        defined[positions] = True
        diagnostics["groups"][group_name] = {"status": "fitted", **details}
    diagnostics["defined"] = int(defined.sum())
    diagnostics["undefined_reasons"]["incomplete_component_vector_or_unknown_ruca"] = int((~defined).sum())
    diagnostics["gap_distribution"] = quantiles(values[defined])
    return IdeaOutput(
        idea_id="Dst4", region=inputs.region, geoids=list(inputs.geoids),
        emissions=[Emission(component="C", channel="gap", values=values, defined=defined,
                            metadata={"model": "region x RUCA-stratified two-class diagonal Gaussian mixture"})],
        diagnostics=diagnostics)


def calibrate_probability_mean(probability: np.ndarray, target_mean: float,
                               epsilon: float = 1e-9, iterations: int = 100) -> tuple[np.ndarray, float]:
    """Monotone logit-intercept calibration whose mean matches an organizer-published rate."""
    p = np.asarray(probability, dtype=np.float64)
    if not 0.0 < target_mean < 1.0 or not np.isfinite(p).all():
        raise ValueError("calibration requires finite probabilities and target in (0,1)")
    clipped = np.clip(p, epsilon, 1.0 - epsilon)
    logits = np.log(clipped / (1.0 - clipped))
    low, high = -40.0, 40.0
    for _ in range(iterations):
        offset = (low + high) / 2.0
        mean = float(np.mean(1.0 / (1.0 + np.exp(-np.clip(logits + offset, -700, 700)))))
        if mean < target_mean:
            low = offset
        else:
            high = offset
    offset = (low + high) / 2.0
    calibrated = 1.0 / (1.0 + np.exp(-np.clip(logits + offset, -700, 700)))
    return calibrated, offset


@implements("Dst5")
def dst5(inputs) -> IdeaOutput:
    import pyarrow.parquet as pq

    seed = inputs.config["seeds"]["Dst5"]
    path = inputs.workspace / "ideas" / "Dst4.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"Dst5 requires the Dst4 base estimator: {path}")
    table = pq.read_table(path, columns=["GEOID", "region", "channel", "value", "defined"])
    records = {row["GEOID"]: row for row in table.to_pylist()
               if row["region"] == inputs.region and row["channel"] == "gap"}
    values = np.zeros(len(inputs.geoids), dtype=np.float64)
    defined = np.zeros(len(inputs.geoids), dtype=np.bool_)
    positions = [i for i, geoid in enumerate(inputs.geoids)
                 if geoid in records and records[geoid]["defined"]]
    if positions:
        base = np.asarray([float(records[inputs.geoids[i]]["value"]) for i in positions], dtype=np.float64)
        calibrated, offset = calibrate_probability_mean(base, float(seed["undefined_share_by_region"][inputs.region]),
                                                        float(seed["epsilon"]), int(seed["iterations"]))
        values[positions] = calibrated
        defined[positions] = True
    else:
        offset = None
    return IdeaOutput(
        idea_id="Dst5", region=inputs.region, geoids=list(inputs.geoids),
        emissions=[Emission(component="C", channel="gap", values=values, defined=defined,
                            metadata={"calibration": "monotone logit-intercept to organizer undefined-share moment"})],
        diagnostics={"rows": int(len(inputs.geoids)), "defined": int(defined.sum()),
                     "undefined_reasons": {"base_dst4_undefined": int((~defined).sum())},
                     "target_undefined_share": float(seed["undefined_share_by_region"][inputs.region]),
                     "calibrated_mean": float(values[defined].mean()) if defined.any() else None,
                     "logit_offset": offset, "gap_distribution": quantiles(values[defined]),
                     "source": "organizer-published per-region undefined-component share only",
                     "no_labels_or_reference_values": True})
