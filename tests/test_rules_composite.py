from __future__ import annotations

import pytest

from bias60.rules_composite import composite_gap, deficit_gap, defined_mean, poi_gap


def test_deficit_is_one_sided_and_bounded():
    assert deficit_gap(0, 10, True) == 1
    assert deficit_gap(4, 10, True) == pytest.approx(0.6)
    assert deficit_gap(10, 10, True) == 0
    assert deficit_gap(14, 10, True) == 0
    assert deficit_gap(999, 0, False) == 0
    with pytest.raises(ValueError):
        deficit_gap(1, 0, True)


def test_undefined_values_are_not_in_the_denominator():
    score, count = defined_mean(((0.9, False), (0.2, True), (0.6, True)))
    assert score == pytest.approx(0.4)
    assert count == 2
    assert defined_mean(((1.0, False), (0.0, False))) == (None, 0)


def test_poi_averages_defined_facility_types_then_defined_halves():
    score, defined = poi_gap(((0.2, True), (0.8, True), (0.99, False)), 0.6, True)
    assert score == pytest.approx(0.55)  # facility half=.5; equal POI halves
    assert defined == 1
    assert poi_gap(((0.2, False), (0.8, False)), 0.6, True) == (0.6, 1)
    assert poi_gap(((0.2, False),), 0.6, False) == (None, 0)


def test_top_level_composite_uses_only_defined_components():
    score, count = composite_gap((0.9, False), (0.3, True), (0.6, True))
    assert score == pytest.approx(0.45)
    assert count == 2
    assert composite_gap((0, False), (0, False), (0, False)) == (None, 0)
