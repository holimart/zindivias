from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_track_a_weight_stability", ROOT / "scripts/audit_track_a_weight_stability.py"
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_equal_defined_support_reconstruction_and_ambiguity():
    components = {"transport_gap": 0.0, "building_gap": 0.3, "poi_gap": 0.5}
    assert mod.infer_equal_defined_supports(components, 0.4) == [
        ("building_gap", "poi_gap")
    ]
    ambiguous = {"transport_gap": 0.1, "building_gap": 0.2, "poi_gap": 0.3}
    supports = mod.infer_equal_defined_supports(ambiguous, 0.2)
    assert ("transport_gap", "building_gap", "poi_gap") in supports
    assert ("building_gap",) in supports
    with pytest.raises(ValueError, match="cannot be reproduced"):
        mod.infer_equal_defined_supports(components, 0.9)


def test_one_at_a_time_perturbation_renormalizes_and_preserves_unmodified_weights():
    values = {"transport_gap": 0.2, "building_gap": 0.6, "poi_gap": 0.4}
    base, plus, weights_plus = mod.perturb_weights(values, mod.COMPONENTS, "transport_gap", +1)
    assert base == pytest.approx(0.4)
    assert sum(weights_plus.values()) == pytest.approx(1.0)
    assert weights_plus["transport_gap"] == pytest.approx(1.1 / 3.1)
    assert weights_plus["building_gap"] == pytest.approx(1.0 / 3.1)
    assert plus == pytest.approx(sum(weights_plus[k] * values[k] for k in mod.COMPONENTS))
    _, minus, weights_minus = mod.perturb_weights(values, mod.COMPONENTS, "transport_gap", -1)
    assert sum(weights_minus.values()) == pytest.approx(1.0)
    assert weights_minus["transport_gap"] == pytest.approx(0.9 / 2.9)
    assert minus != pytest.approx(plus)


def test_perturbing_an_undefined_component_has_zero_effect():
    values = {"transport_gap": 0.0, "building_gap": 0.3, "poi_gap": 0.5}
    base, perturbed, weights = mod.perturb_weights(
        values, ("building_gap", "poi_gap"), "transport_gap", +1
    )
    assert base == pytest.approx(0.4)
    assert perturbed == pytest.approx(base)
    assert weights == {"building_gap": 0.5, "poi_gap": 0.5}


def test_quantile_and_absolute_delta_summaries_are_deterministic():
    assert mod.quantile([0.0, 1.0], 0.95) == pytest.approx(0.95)
    summary = mod.summarize_deltas([-0.2, 0.0, 0.1, -0.3])
    assert summary == {
        "n": 4,
        "p50_abs_change": pytest.approx(0.15),
        "p95_abs_change": pytest.approx(0.285),
        "max_abs_change": pytest.approx(0.3),
    }


def test_registered_candidate_constants_and_threshold():
    assert mod.CANDIDATES == ("A1_physical_equal", "A2_spectral", "A3_dawid_skene")
    assert mod.COMPONENTS == ("transport_gap", "building_gap", "poi_gap")
    assert mod.PERTURBATION == pytest.approx(0.10)
    assert mod.UNSTABLE_P95_THRESHOLD == pytest.approx(0.02)
