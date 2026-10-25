"""A synthetic lab fixture for testing the lab evaluator.

It exists only so the evaluator can be exercised end to end. Nothing in it is a measurement: the
tracts, their estimator values and their labels come from a seeded generator with a known weight
vector, so a test can assert both that the machinery runs and that it recovers the truth it was handed.
Components, ideas and channels are read from ``config/ideas.json`` so the fixture can never disagree
with the registry. It writes only into a caller-supplied temporary directory, and it is never used to
produce a committed artifact.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # noqa: F401
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[1]


def registry_view() -> dict[str, Any]:
    document = json.loads((REPO / "config/ideas.json").read_text(encoding="utf-8"))
    implemented = sorted(document["summary"]["implemented"])
    components = {idea["id"]: idea["component"] for idea in document["ideas"]
                  if idea["id"] in implemented}
    families = {idea["id"]: idea["family"] for idea in document["ideas"] if idea["id"] in implemented}
    return {"implemented": implemented, "components": components, "families": families,
            "targets": sorted({component for component in components.values()})}


def _clip(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def _weights(names: list[str]) -> dict[str, float]:
    """Deterministic, unequal, sum-to-one truth weights so a fit must recover a specific vector."""
    if len(names) == 1:
        return {names[0]: 1.0}
    steps = [0.5, 0.3, 0.2, 0.1, 0.05, 0.05, 0.05, 0.05][:len(names)]
    total = sum(steps[:len(names)])
    return {name: steps[index] / total for index, name in enumerate(names)}


def write_lab(root: Path, *, groups: list[str] | None = None, per_group: int = 40,
              counties_per_group: int = 4, seed: int = 7, undefined_share: float = 0.05,
              label_noise: float = 0.02, inject_official_geoid: str | None = None,
              buffer_km: float = 8.0, nearest_km: float = 25.0, road_ratio: float = 1.4,
              hospital_ratio: float = 12.0, source_sha: str = "a" * 64) -> dict[str, Any]:
    """Write a complete contract-valid lab artifact set and return the ground truth that was used."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    view = registry_view()
    ideas = view["implemented"]
    truth_weights = _weights(ideas)
    groups = groups or [f"GRP{i}" for i in range(1, 7)]
    channels = ["composite"] + [f"target:{component}" for component in view["targets"]]
    rng = np.random.default_rng(seed)

    tracts: dict[str, list[Any]] = {name: [] for name in
                                    ["lab_tract_id", "lab_group", "state_fips", "county_fips",
                                     "centroid_x", "centroid_y", "nearest_study_tract_km"]}
    estimators: dict[str, list[Any]] = {name: [] for name in
                                        ["lab_tract_id", "lab_group", "idea", "family", "component",
                                         "channel", "value", "defined"]}
    labels: dict[str, list[Any]] = {name: [] for name in
                                    ["lab_tract_id", "lab_group", "component", "label", "defined",
                                     "recipe_variant_id"]}
    truth_rows: list[dict[str, float]] = []
    drop_first_idea = ideas[0] if ideas else ""

    for group_index, group in enumerate(sorted(groups)):
        for index in range(per_group):
            tract = f"9{group_index}{index:06d}"
            county = f"9{group_index}{index % counties_per_group:03d}"
            base = float(rng.uniform(0.05, 0.95))
            evidence = {name: _clip(base * (0.4 + 0.2 * position)
                                    + float(rng.normal(0.02 * position, 0.04)))
                        for position, name in enumerate(ideas)}
            composite = _clip(sum(truth_weights[name] * evidence[name] for name in ideas)
                              + float(rng.normal(0.0, label_noise / 2.0)))
            tracts["lab_tract_id"].append(tract)
            tracts["lab_group"].append(group)
            tracts["state_fips"].append("99")
            tracts["county_fips"].append(county)
            tracts["centroid_x"].append(float(index) * 1000.0)
            tracts["centroid_y"].append(float(group_index) * 1000.0)
            tracts["nearest_study_tract_km"].append(nearest_km)

            undefined_this_tract = float(rng.uniform()) < undefined_share
            for name in ideas:
                component = view["components"][name]
                for channel in channels:
                    target = "composite" if channel == "composite" else channel.split("target:", 1)[1]
                    if channel != "composite" and target != component:
                        continue
                    undefined = undefined_this_tract and name == drop_first_idea
                    estimators["lab_tract_id"].append(tract)
                    estimators["lab_group"].append(group)
                    estimators["idea"].append(name)
                    estimators["family"].append(view["families"][name])
                    estimators["component"].append(component)
                    estimators["channel"].append(channel)
                    estimators["value"].append(0.0 if undefined else evidence[name])
                    estimators["defined"].append(not undefined)

            for component in view["targets"]:
                members = [name for name in ideas if view["components"][name] == component]
                representative = sum(evidence[name] for name in members) / len(members)
                labels["lab_tract_id"].append(tract)
                labels["lab_group"].append(group)
                labels["component"].append(component)
                labels["label"].append(_clip(representative + float(rng.normal(0.0, label_noise))))
                labels["defined"].append(True)
                labels["recipe_variant_id"].append("rv-fixture-1")
            labels["lab_tract_id"].append(tract)
            labels["lab_group"].append(group)
            labels["component"].append("composite")
            labels["label"].append(composite)
            labels["defined"].append(True)
            labels["recipe_variant_id"].append("rv-fixture-1")
            truth_rows.append({**evidence, "composite": composite})

    if inject_official_geoid is not None:
        tracts["lab_tract_id"][0] = inject_official_geoid

    pq.write_table(pa.table(tracts), root / "tracts.parquet")
    pq.write_table(pa.table(estimators), root / "estimators.parquet")
    pq.write_table(pa.table(labels), root / "labels.parquet")

    manifest = {
        "lab_commit": "fixture-commit-0000000",
        "recipe_variant_id": "rv-fixture-1",
        "exclusion_policy_sha256": "b" * 64,
        "buffer_km": buffer_km,
        "sources": [
            {"id": "tiger_prisecroads_2024", "url": "https://example.invalid/tl.zip",
             "version": "2024", "sha256": source_sha, "retrieved_utc": "2026-10-25"},
            {"id": "ms_us_building_footprints", "url": "https://example.invalid/footprints.zip",
             "version": "v2026", "sha256": "c" * 64, "retrieved_utc": "2026-10-25"},
        ],
        "self_checks": {
            "state_road_ratio_in_organiser_band": {"passed": True, "worst_ratio": road_ratio,
                                                   "tolerance": 0.15},
            "hospital_ratio_check": {"passed": True, "observed_ratio": hospital_ratio},
        },
        "groups": sorted(groups),
        "study_polygon_source": "WORKSPACE:defined-flags-inputs (provided tract polygons)",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True),
                                        encoding="utf-8")
    document = {"groups": {group: {component: {"tracts": per_group, "defined": per_group}
                                   for component in view["targets"] + ["composite"]}
                           for group in sorted(groups)}}
    (root / "defineness.json").write_text(json.dumps(document, indent=2, sort_keys=True),
                                          encoding="utf-8")
    return {"true_weights": truth_weights, "rows": len(tracts["lab_tract_id"]),
            "groups": sorted(groups), "truth_rows": truth_rows, "manifest": manifest,
            "channels": channels, "registry": view}
