from __future__ import annotations

import importlib.util
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_shrink_s2_assumption_bound", ROOT / "scripts/build_shrink_s2_assumption_bound.py")
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_rule_counterfactual_applies_building_share_and_drops_zero_facility_type():
    c2 = {"transport": 0.2, "building": 0.8, "poi": 0.4}
    estimates = {
        "transport_defined": "1", "building_defined": "1",
        "fire_gap": "0.6", "fire_defined": "1",
        "ems_gap": "0.4", "ems_defined": "1",
        "schools_gap": "0.9", "schools_defined": "1",
        "cbp_gap": "0.2", "cbp_defined": "1",
    }
    feature = {"overture_building_microsoft_source_share": 0.25,
               "overture_poi_fire": 0, "overture_poi_ems": 1, "overture_poi_schools": 2}
    components, flags = mod.adjusted_components(c2, estimates, feature)
    assert math.isclose(components["building"], 0.6)
    # Mean of retained EMS/schools is .65; mean of facility and CBP halves is .425.
    assert math.isclose(components["poi"], 0.425)
    assert components["transport"] == 0.2
    assert flags == {"transport": 1, "building": 1, "poi": 1}
    assert math.isclose(mod.component_composite(components, flags), (0.2 + 0.6 + 0.425) / 3)


def test_missing_microsoft_share_preserves_c2_building_component():
    c2 = {"transport": 0.0, "building": 0.37, "poi": 0.0}
    estimates = {"transport_defined": "0", "building_defined": "1",
                 "fire_gap": "0", "fire_defined": "0", "ems_gap": "0", "ems_defined": "0",
                 "schools_gap": "0", "schools_defined": "0", "cbp_gap": "0", "cbp_defined": "0"}
    feature = {"overture_building_microsoft_source_share": None,
               "overture_poi_fire": 0, "overture_poi_ems": 0, "overture_poi_schools": 0}
    components, flags = mod.adjusted_components(c2, estimates, feature)
    assert components["building"] == 0.37
    assert flags == {"transport": 0, "building": 1, "poi": 0}
    assert mod.component_composite(components, flags) == 0.37


def test_facility_drop_respects_existing_defined_flags_and_undefined_halves():
    c2 = {"transport": 0.1, "building": 0.2, "poi": 0.0}
    estimates = {"transport_defined": "1", "building_defined": "1",
                 "fire_gap": "0.7", "fire_defined": "1",
                 "ems_gap": "0.4", "ems_defined": "0",
                 "schools_gap": "0.9", "schools_defined": "0",
                 "cbp_gap": "0.3", "cbp_defined": "0"}
    feature = {"overture_building_microsoft_source_share": 0.0,
               "overture_poi_fire": 1, "overture_poi_ems": 2, "overture_poi_schools": 3}
    components, flags = mod.adjusted_components(c2, estimates, feature)
    assert components["poi"] == 0.7
    assert flags["poi"] == 1


def test_candidate_registry_insertion_is_surgical_and_idempotent():
    original = '{\n  "candidate_set": [\n    {"candidate_id": "existing", "opaque": 19}\n  ],\n  "tail": true\n}\n'
    card = {"candidate_id": mod.CANDIDATE_ID, "purpose": "improvement", "upload_authorized": False}
    once = mod.insert_candidate_card(original, card)
    parsed = __import__("json").loads(once)
    assert [x["candidate_id"] for x in parsed["candidate_set"]] == ["existing", mod.CANDIDATE_ID]
    assert parsed["tail"] is True
    assert mod.insert_candidate_card(once, card) == once
