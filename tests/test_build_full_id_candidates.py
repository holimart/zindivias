from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import build_full_id_candidates as builder  # noqa: E402


def test_wa_fill_defaults_to_zero_for_template_only_rows() -> None:
    args = builder.build_parser().parse_args(["--workspace", "/tmp/workspace"])
    assert args.wa_fill == 0.0
    assert builder.wa_score_values(args.wa_fill) == {
        "transport_gap": 0.0,
        "building_gap": 0.0,
        "poi_gap": 0.0,
        "coverage_gap_score": 0.0,
    }


def test_explicit_platform_fill_is_uniform_across_score_columns() -> None:
    value = builder.parse_wa_fill("0.25")
    assert builder.wa_score_values(value) == {
        "transport_gap": 0.25,
        "building_gap": 0.25,
        "poi_gap": 0.25,
        "coverage_gap_score": 0.25,
    }


@pytest.mark.parametrize("value", ["-0.01", "1.01", "nan", "inf", "not-a-number"])
def test_wa_fill_rejects_invalid_values(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        builder.parse_wa_fill(value)
