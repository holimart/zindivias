from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_t2_orphan_ramp_support.py"
spec = importlib.util.spec_from_file_location("audit_t2_orphan_ramp_support", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_ruca_classification_preserves_supplied_codes_and_exposes_missingness():
    assert module.classify_ruca(1.0, True) == "1"
    assert module.classify_ruca(99.0, True) == "99"
    assert module.classify_ruca(2.5, True) == "missing_or_uncovered"
    assert module.classify_ruca(4.0, False) == "missing_or_uncovered"
    assert module.classify_ruca(None, True) == "missing_or_uncovered"
    assert module.classify_ruca(float("nan"), True) == "missing_or_uncovered"


def test_attached_support_requires_defined_positive_finite_length():
    support = module.positive_attached_support(
        [True, True, True, False, True], [0.0, 0.001, float("nan"), 0.5, -0.1])
    assert support.tolist() == [False, True, False, False, False]


def test_ruca_counts_use_full_stratum_denominators_and_report_masks():
    table = module.support_table(
        ["1", "1", "99", "missing_or_uncovered"],
        [True, False, True, True], [True, False, False, False],
        [False, False, True, False],
    )
    assert list(table) == ["1", "99", "missing_or_uncovered"]
    assert table["1"] == {
        "official_tracts": 2,
        "T2_defined": 1,
        "attached_support_positive": 1,
        "T2_orphan_link_length_positive": 0,
        "T2_defined_fraction": 0.5,
        "attached_support_fraction": 0.5,
        "orphan_link_length_positive_fraction": 0.0,
    }
    assert table["99"]["official_tracts"] == 1
    assert table["99"]["attached_support_fraction"] == 0.0
    assert table["missing_or_uncovered"]["T2_defined_fraction"] == 1.0


def test_support_backed_gate_requires_both_overall_and_every_region_threshold():
    blocked = module.support_backed_gate(4, 10, {"north": 0.2, "south": 0.6})
    assert blocked["overall_fraction"] == 0.4
    assert blocked["passes"] is False
    assert blocked["decision"] == "no_go"
    assert "do not fit" in blocked["reason"]

    passed = module.support_backed_gate(9, 10, {"north": 0.8, "south": 0.6})
    assert passed["passes"] is True
    assert passed["decision"] == "go"
