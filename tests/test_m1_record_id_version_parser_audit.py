from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit_m1-record-id-version-tokens.py"
spec=importlib.util.spec_from_file_location("m1_record_id_version_parser_audit",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_parser_fullmatches_only_the_registered_untrimmed_osm_way_version_form():
    assert module.parse_way_version("w123@7")==7
    assert module.parse_way_version("W123@007")==7
    for value in ("",None," w123@7","w123@7 ","r123@7","w123@","w123@7/8","prefix-w123@7","w123@1@2"):
        assert module.parse_way_version(value) is None


def test_source_inspection_separates_osm_tokens_other_sources_missing_and_malformed():
    result=module.inspect_sources([
        {"dataset":"OpenStreetMap","record_id":"w12@3"},
        {"dataset":"openstreetmap","record_id":"w99@3"},
        {"dataset":"OpenStreetMap","record_id":"r7"},
        {"dataset":"OpenStreetMap","record_id":None},
        {"dataset":"Esri","record_id":"w4@4"},
    ])
    assert result["osm_memberships"]==4
    assert result["non_osm_memberships"]==1
    assert result["missing_ids"]==1
    assert result["versions"]==[3]
    assert result["all_valid"] is False
    assert result["unique_version"] is False
    assert result["has_valid"] and result["has_malformed"]


def test_duplicate_same_version_is_unambiguous_but_distinct_valid_versions_are_reported():
    same=module.inspect_sources([{"dataset":"OpenStreetMap","record_id":"w12@3"},
                                 {"dataset":"OpenStreetMap","record_id":"w12@3"}])
    different=module.inspect_sources([{"dataset":"OpenStreetMap","record_id":"w12@3"},
                                      {"dataset":"OpenStreetMap","record_id":"w22@4"}])
    assert same["all_valid"] and same["unique_version"]
    assert different["all_valid"] and not different["unique_version"]
    assert different["versions"]==[3,4]


def test_record_id_shape_redaction_hides_literal_identifiers_and_versions():
    assert module.redact_token_shape("w123@45")=="w###@##"
    assert module.redact_token_shape("w123@x")=="w###@A"
    assert module.redact_token_shape("r123@45")=="A###@##"
    assert "123" not in module.redact_token_shape("w123@45")


def test_length_weighted_support_distinguishes_valid_malformed_and_ambiguous_geometry():
    stats=module._new_stats()
    module.add_segment(stats,module.inspect_sources([{"dataset":"OpenStreetMap","record_id":"w1@2"}]),100.)
    module.add_segment(stats,module.inspect_sources([{"dataset":"OpenStreetMap","record_id":"w2@2"},
                                                     {"dataset":"OpenStreetMap","record_id":"w3@3"}]),50.)
    module.add_segment(stats,module.inspect_sources([{"dataset":"OpenStreetMap","record_id":"r3"}]),25.)
    module.add_segment(stats,module.inspect_sources([{"dataset":"Esri","record_id":"e3"}]),10.)
    result=module._metrics(stats)
    assert result["clipped_road_length_m"]==185.
    assert result["osm_source_road_length_m"]==175.
    assert result["eligible_nonempty_record_id_length_m"]==175.
    assert result["single_distinct_version_length_m"]==100.
    assert result["multiple_distinct_versions_length_m"]==50.
    assert result["malformed_token_segment_length_m"]==25.
    assert result["single_distinct_version_coverage_of_eligible_length"]==pytest.approx(100/175)
    assert result["malformed_token_shapes_top20"][0]["redacted_shape"]=="A#"
