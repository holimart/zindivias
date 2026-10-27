from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_t2_orphan_ramp_support_radius.py"
spec = importlib.util.spec_from_file_location("audit_t2_orphan_ramp_support_radius", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_only_registered_sensitivity_radii_are_used_in_ascending_order():
    assert module.RADII_M == (2000.0, 5000.0, 10000.0)
    assert [module.RADIUS_NAMES[r] for r in module.RADII_M] == ["2km", "5km", "10km"]


def test_pair_provenance_digest_is_stable_and_deduplicated():
    one = module.provenance_digest([[17, 4], [3, 8], [17, 4]])
    two = module.provenance_digest([[3, 8], [17, 4]])
    assert one == two
    assert one != module.provenance_digest([[17, 4], [3, 9]])


def test_distance_distribution_handles_empty_and_reports_frozen_quantiles():
    assert module.distance_summary([]) == {
        "n_unique_in_tract_contributions": 0,
        "min_m": None,
        "median_m": None,
        "q90_m": None,
        "q95_m": None,
        "max_m": None,
    }
    summary = module.distance_summary([0.0, 10.0, 20.0, 30.0])
    assert summary["n_unique_in_tract_contributions"] == 4
    assert summary["min_m"] == 0.0
    assert summary["median_m"] == 15.0
    assert summary["max_m"] == 30.0


def test_prior_2km_support_gate_remains_no_go_and_radius_inputs_are_not_labels():
    prior = module.baseline_audit.support_backed_gate(
        2325, 9379,
        {"maricopa-az": 300 / 1593, "northern-ca": 132 / 591,
         "eastern-ok": 187 / 1192, "south-central-tx": 1706 / 6003},
    )
    assert prior["passes"] is False
    assert prior["decision"] == "no_go"
    assert prior["overall_fraction"] < 0.25
    assert module.ROAD_COLUMNS == ["class", "subclass", "connectors", "geometry"]
