from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_t3_grade_separation_semantics.py"
spec = importlib.util.spec_from_file_location("audit_t3_grade_separation_semantics", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

FROZEN_FLAGS = {"bridge", "is_bridge", "tunnel", "is_tunnel"}


def test_level_rules_and_road_flags_are_unioned_once_per_source_row():
    assert module.grade_semantics(None, [{"value": 1}], FROZEN_FLAGS) == (True, False, True)
    assert module.grade_semantics([{"values": ["BRIDGE"]}], None, FROZEN_FLAGS) == (False, True, True)
    assert module.grade_semantics([{"values": ["tunnel"]}], [{"value": 2}], FROZEN_FLAGS) == (True, True, True)
    assert module.grade_semantics(None, [{"value": 0}], FROZEN_FLAGS) == (False, False, False)
    assert module.grade_semantics([{"values": ["railway"]}], None, FROZEN_FLAGS) == (False, False, False)


def test_exact_toy_network_matches_independent_tract_reference():
    result = module.run_toy_network_check()
    assert result["toy_source_rows"] == 10
    assert result["rows_accounted_once"] == 10
    assert result["eligible_flagged_rows"] == 7
    assert result["eligible_rows_marked_by_both_sources"] == 1
    assert result["exact_duplicate_geometry_groups"] == 1
    assert result["exact_duplicate_geometry_excess_rows"] == 1
    assert result["flagged_segment_tract_pairs"] == 8
    assert result["duplicate_segment_tract_pair_repeats"] == 0
    assert result["independent_reference_counts_by_tract"] == {"toy-a": 5, "toy-b": 3}
    assert result["production_counts_by_tract"] == {"toy-a": 5, "toy-b": 3}
    assert result["per_tract_count_discrepancy"] == {"toy-a": 0, "toy-b": 0}
    assert result["maximum_absolute_count_discrepancy"] == 0
    assert result["result"] == "pass_exact_match"


def test_reader_digest_canonicalizes_nested_values_and_nonfinite_numbers():
    import hashlib
    import math

    digest = hashlib.sha256()
    module.canonical_row_digest_update(digest, ["tertiary", [{"values": ["bridge"]}], None, b"abc"])
    assert len(digest.hexdigest()) == 64
    assert module._jsonable(float("nan")) == "nan"
