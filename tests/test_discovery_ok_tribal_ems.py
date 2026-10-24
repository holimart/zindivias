from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import discovery_ok_tribal_ems as audit  # noqa: E402


def test_wilson_interval_handles_boundaries() -> None:
    zero = audit.wilson_interval(0, 10)
    full = audit.wilson_interval(10, 10)
    assert zero is not None and zero[0] == 0.0 and 0 < zero[1] < 0.5
    assert full is not None and full[1] == pytest.approx(1.0) and 0.5 < full[0] < 1.0
    assert audit.wilson_interval(0, 0) is None


def test_newcombe_difference_has_correct_direction_and_excludes_zero() -> None:
    positive = audit.newcombe_difference(8, 10, 2, 10)
    negative = audit.newcombe_difference(2, 10, 8, 10)
    assert positive["difference"] == pytest.approx(0.6)
    assert positive["ci_excludes_zero"] is True
    assert positive["ci_95"][0] > 0
    assert negative["difference"] == pytest.approx(-0.6)
    assert negative["ci_95"][1] < 0
    assert negative["ci_95"] == pytest.approx([-positive["ci_95"][1], -positive["ci_95"][0]])


def test_newcombe_empty_group_is_explicit() -> None:
    result = audit.newcombe_difference(0, 0, 3, 10)
    assert result["difference"] is None
    assert result["ci_95"] is None
    assert "empty" in result["reason"]


def test_tribal_any_accepts_only_boolean_domain() -> None:
    assert audit.tribal_flag(True) is True
    assert audit.tribal_flag(0) is False
    assert audit.tribal_flag(1.0) is True
    assert audit.tribal_flag(None) is None
    with pytest.raises(ValueError):
        audit.tribal_flag(2)
