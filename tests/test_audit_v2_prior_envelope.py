from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from audit_v2_prior_envelope import (  # noqa: E402
    BASELINE,
    CONFIG,
    FAMILIES,
    PRIORS,
    evaluate,
    parse_variant,
    perturbations,
    precompute_probabilities,
    probability_with_overrides,
    quantiles,
    tribal_flag,
    variant_score,
)


def make_tract(geoid: str = "01001000100", region: str = "maricopa-az", *,
               transport: tuple[float, int] = (0.2, 1), building: tuple[float, int] = (0.01, 1),
               families: tuple[float, float, float] = (0.3, 0.2, 0.1),
               establishments: tuple[float, int] = (0.5, 1),
               poi_count: float = 24.0, pop_total: float = 1000.0,
               pct_urban: float = 0.9, tribal: float | None = 0.0) -> dict:
    strata = {"pop_total": pop_total, "pop_urban": pop_total * pct_urban,
              "pct_urban": pct_urban, "ALAND": 2_000_000.0, "tribal_any": tribal}
    from audit_v2_component_masks import composite_mean, presence_probability

    probability = {family: presence_probability(family, strata) for family in FAMILIES}
    facility = sum(families) / len(families)
    halves = [facility] + ([establishments[0]] if establishments[1] else [])
    poi = sum(halves) / len(halves)
    flags = (transport[1], building[1], poi and 1 or 0)
    score, defined = composite_mean([transport, building, (poi, flags[2])])
    return {
        "geoid": geoid, "region": region, "urban": pct_urban >= 0.5, "pct_urban_missing": False,
        "tribal": tribal_flag(tribal), "strata": strata,
        "transport_gap": transport[0], "transport_flag": transport[1],
        "building_gap": building[0], "building_flag": building[1],
        "family_gaps": dict(zip(FAMILIES, families)),
        "establishments_gap": establishments[0], "establishments_flag": establishments[1],
        "poi_flag": flags[2], "poi_count": poi_count, "pop_total": pop_total,
        "central_probability": probability,
        "central_score": score if defined else 0.0, "defined_count": defined,
        "defined_components": sum(flags), "frozen_flags": flags,
    }


def grid() -> list[str]:
    return list(CONFIG["variant_grid"])


def test_variant_ids_parse_into_registered_perturbation_kinds() -> None:
    assert parse_variant(BASELINE)["kind"] == BASELINE
    assert parse_variant("building_gap.x0.90")["kind"] == "building"
    assert parse_variant("establishments_cbp_k.x1.10")["kind"] == "establishments"
    assert parse_variant("all_priors.x1.10")["kind"] == "all"
    scale = parse_variant("presence_fire.x0.90")
    assert (scale["kind"], scale["family"], scale["coefficient"]) == ("presence_scale", "fire", None)
    coefficient = parse_variant("presence_schools.urban_fraction.x1.10")
    assert (coefficient["kind"], coefficient["family"], coefficient["coefficient"],
            coefficient["factor"]) == ("presence_coefficient", "schools", "urban_fraction", 1.1)
    assert sorted(CONFIG["perturbation_rule"]["factors"]) == [0.9, 1.1]


@pytest.mark.parametrize("bad", ["building_gap.0.90", "presence_fire.x0.9", "unknown_prior.x0.90",
                                "presence_ghost.x0.90", "presence_fire.nonsense.x0.90", "building_gap"])
def test_unregistered_variant_ids_abort(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_variant(bad)


def test_quantiles_are_nearest_rank_and_deterministic() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert quantiles(values) == {"p50": 2.0, "p90": 4.0, "p95": 4.0, "p99": 4.0, "max": 4.0}
    assert quantiles([]) == {level: None for level in ("p50", "p90", "p95", "p99", "max")}
    single = quantiles([0.25])
    assert all(value == 0.25 for value in single.values())


def test_baseline_variant_reproduces_the_frozen_composite() -> None:
    tract = make_tract()
    spec = parse_variant(BASELINE)
    score, defined = variant_score(tract, *perturbations(tract, spec, {}))
    assert defined == tract["defined_components"] == 3
    assert score == pytest.approx(tract["central_score"], abs=1e-15)
    assert score == pytest.approx((0.2 + 0.01 + ((0.3 + 0.2 + 0.1) / 3 + 0.5) / 2) / 3, abs=1e-15)


def test_building_prior_perturbation_is_exact_and_undefined_rows_are_immune() -> None:
    tract = make_tract()
    spec = parse_variant("building_gap.x1.10")
    score, _ = variant_score(tract, *perturbations(tract, spec, {}))
    expected = (0.2 + 0.011 + ((0.3 + 0.2 + 0.1) / 3 + 0.5) / 2) / 3
    assert score == pytest.approx(expected, abs=1e-15)
    assert abs(score - tract["central_score"]) == pytest.approx(0.001 / 3, abs=1e-15)

    undefined = make_tract(building=(0.0, 0))
    spec_low = parse_variant("building_gap.x0.90")
    score_undefined, _ = variant_score(undefined, *perturbations(undefined, spec_low, {}))
    assert score_undefined == pytest.approx(undefined["central_score"], abs=1e-15)


def test_establishments_prior_perturbation_follows_the_registered_formula() -> None:
    tract = make_tract(poi_count=12.0, pop_total=1000.0)  # central gap = 1 - 12/(0.024*1000) = 0.5
    assert tract["establishments_gap"] == pytest.approx(0.5, abs=1e-15)
    spec = parse_variant("establishments_cbp_k.x0.90")
    _, k_factor, _ = perturbations(tract, spec, {})
    score, defined = variant_score(tract, 1.0, k_factor, {family: 1.0 for family in FAMILIES})
    lowered = 1.0 - 12.0 / (0.024 * 0.9 * 1000.0)
    expected = (0.2 + 0.01 + ((0.3 + 0.2 + 0.1) / 3 + lowered) / 2) / 3
    assert defined == 3
    assert score == pytest.approx(expected, abs=1e-15)
    # a lower CBP rate expects fewer establishments per capita, so the same observed
    # Overture count leaves a smaller deficit and the component gap falls
    assert score < tract["central_score"]
    assert lowered < tract["establishments_gap"]


def test_presence_prior_enters_multiplicatively_and_clips_at_one() -> None:
    tract = make_tract()
    spec = parse_variant("presence_schools.x1.10")
    _, _, scales = perturbations(tract, spec, {})
    assert scales["schools"] == pytest.approx(1.1, rel=1e-12)
    assert scales["fire"] == 1.0 and scales["ems"] == 1.0
    score, _ = variant_score(tract, *perturbations(tract, spec, {}))
    scaled_facility = (0.3 + 0.2 + 0.11) / 3
    expected = (0.2 + 0.01 + (scaled_facility + 0.5) / 2) / 3
    assert score == pytest.approx(expected, abs=1e-15)

    saturated = make_tract()
    base = 0.95  # a presence prior above 1/1.1 makes the [0, 1] clip bind
    saturated["central_probability"]["schools"] = base
    _, _, clipped_scales = perturbations(saturated, parse_variant("presence_schools.x1.10"), {})
    assert clipped_scales["schools"] == pytest.approx(1.0 / base, rel=1e-12)
    assert clipped_scales["schools"] < 1.1  # the prior can never exceed 1 after perturbation


def test_coefficient_variant_uses_the_recomputed_prior_ratio() -> None:
    tract = make_tract(pop_total=5000.0, pct_urban=0.2)
    spec = parse_variant("presence_ems.log_density.x1.10")
    probabilities = {(("ems", "log_density", 1.1)):{tract["geoid"]: probability_with_overrides(
        "ems", tract["strata"], {"log_density": PRIORS["ems"]["log_density"] * 1.1})}}
    _, _, scales = perturbations(tract, spec, probabilities)
    expected_ratio = probabilities[("ems", "log_density", 1.1)][tract["geoid"]] / tract[
        "central_probability"]["ems"]
    assert scales["ems"] == pytest.approx(expected_ratio, rel=1e-15)
    assert scales["ems"] != 1.1  # a coefficient perturbation is not the same as scaling p


def test_definedness_masks_never_change_under_any_variant() -> None:
    tracts = [make_tract(), make_tract(geoid="01002", building=(0.0, 0)),
              make_tract(geoid="01003", establishments=(0.0, 0), poi_count=0.0, pop_total=0.0),
              make_tract(geoid="01004", transport=(0.0, 0))]
    probabilities = precompute_probabilities(tracts, [parse_variant(name) for name in grid()])
    for spec_name in grid():
        spec = parse_variant(spec_name)
        for tract in tracts:
            building_factor, k_factor, scales = perturbations(tract, spec, probabilities)
            _, defined = variant_score(tract, building_factor, k_factor, scales)
            assert defined == tract["defined_components"]
    result = evaluate(tracts, grid())
    assert len(result["variants"]) == len(grid())
    for row in result["variants"]:
        assert row["rows"] == len(tracts)
        assert row["definedness_flag_mismatches"] == 0
        assert row["nonfinite_scores"] == 0 and row["scores_outside_zero_one"] == 0
        assert row["delta_quantiles"]["max"] is not None
    assert result["variants"][0]["variant"] == BASELINE


def test_evaluate_reports_every_variant_in_grid_order_with_envelope_bounds() -> None:
    tracts = [make_tract(geoid=f"0100{i}", pop_total=float(200 * (i + 1))) for i in range(6)]
    result = evaluate(tracts, grid())
    assert [row["variant"] for row in result["variants"]] == grid()
    envelope = result["envelope"]
    assert envelope["variants_included"] == len(grid())
    assert envelope["global_score_lower"] <= min(t["central_score"] for t in tracts)
    assert envelope["global_score_upper"] >= max(t["central_score"] for t in tracts)
    assert envelope["widest_tract"]["width"] == pytest.approx(
        envelope["per_tract_envelope_width_quantiles"]["max"], abs=1e-15)
    dimensions = {key.split("|")[0] for row in result["variants"] for key in row["stratum_mean_delta"]}
    assert dimensions == {"region", "urban_rural", "tribal", "tribal_within_urban_rural"}
    assert set(envelope["stratum_max_abs_mean_delta"]) == set(
        result["variants"][1]["stratum_mean_delta"])


def test_transport_has_no_prior_and_undefined_components_stay_immune() -> None:
    assert all(parse_variant(name)["target"] != "transport" for name in grid())
    only_facility = make_tract(geoid="06001", transport=(0.0, 0), building=(0.0, 0),
                              establishments=(0.0, 0), poi_count=0.0, pop_total=0.0)
    assert only_facility["defined_components"] == 1
    probabilities = precompute_probabilities([only_facility],
                                            [parse_variant(name) for name in grid()])
    for name in ("building_gap.x1.10", "building_gap.x0.90",
                 "establishments_cbp_k.x1.10", "establishments_cbp_k.x0.90"):
        score, defined = variant_score(only_facility, *perturbations(only_facility,
                                                                     parse_variant(name), probabilities))
        assert defined == only_facility["defined_components"] == 1
        assert score == pytest.approx(only_facility["central_score"], abs=1e-15)
    score, _ = variant_score(only_facility, *perturbations(
        only_facility, parse_variant("presence_fire.x0.90"), probabilities))
    assert score < only_facility["central_score"]


def test_tribal_flag_domain_is_enforced() -> None:
    assert tribal_flag(1.0) is True and tribal_flag(0.0) is False and tribal_flag(None) is None
    for bad in (0.5, float("nan"), 2.0):
        with pytest.raises(ValueError):
            tribal_flag(bad)


def test_registered_preregistration_is_a_screen_only() -> None:
    assert CONFIG["variant_grid"][0] == BASELINE
    assert len(set(CONFIG["variant_grid"])) == len(CONFIG["variant_grid"]) == 31
    assert CONFIG["gates"]["official_geoids_per_variant"] == 9379
    assert CONFIG["gates"]["require_no_winner_selected"] is True
    assert CONFIG["measurement"]["no_winner_policy"]
    assert CONFIG["perturbation_rule"]["never_perturbed"]
    assert CONFIG["frozen_inputs"]["transport_component"]["perturbed"] is False
    assert CONFIG["frozen_inputs"]["official_samples"]["score_placeholders_read"] is False
    for key, value in CONFIG["prohibitions"].items():
        assert value is False, key
    assert json.dumps(CONFIG, sort_keys=True)  # serializable as declared
