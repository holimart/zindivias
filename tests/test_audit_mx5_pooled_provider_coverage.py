from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
from audit_mx5_pooled_provider_coverage import aggregate_batches, parquet_bytes  # noqa: E402

CONFIG = json.loads((ROOT / "config/bias60-harness-preregistered.json").read_text())
SEED = CONFIG["seeds"]["Mx5"]


def _bbox(x=-99.95, y=40.05):
    return {"xmin": x - 0.001, "xmax": x + 0.001,
            "ymin": y - 0.001, "ymax": y + 0.001}


def test_multi_source_education_place_counts_once_in_pooled_denominator():
    rows = [
        {"bbox": _bbox(), "sources": [{"dataset": "Meta"}, {"dataset": "Microsoft"},
                                           {"dataset": "Overture"}],
         "names": {"primary": "North Academy"}, "categories": {"primary": "education"}},
        {"bbox": _bbox(), "sources": [{"dataset": "Overture"}],
         "names": {"primary": "West School"}, "categories": {"primary": "school"}},
        {"bbox": _bbox(), "sources": [{"dataset": "Meta"}],
         "names": {"primary": "West Market"}, "categories": {"primary": "retail"}},
        {"bbox": _bbox(0.0, 0.0), "sources": [{"dataset": "Meta"}],
         "names": {"primary": "Outside Academy"}, "categories": {"primary": "education"}},
    ]
    batch = pa.Table.from_pylist(rows)
    tree = shapely.STRtree([Polygon([(-100, 40), (-99.9, 40), (-99.9, 40.1), (-100, 40.1)])])
    result = aggregate_batches([batch], tree, ["00000000001"], SEED)

    assert result["eligible_places"].tolist() == [2]
    assert result["category_mismatches"].tolist() == [1]
    assert result["pooled_share"].tolist() == [0.5]
    assert result["defined"].tolist() == [True]
    assert result["diagnostics"]["multisource_education_places"] == 1
    assert result["diagnostics"]["pooled_places_counted_once"] == 2
    assert result["diagnostics"]["provider_bucket_memberships"] == 3
    assert result["provider_denominator"]["meta"].tolist() == [1]
    assert result["provider_denominator"]["microsoft"].tolist() == [1]
    assert result["provider_denominator"]["other"].tolist() == [1]


def test_zero_denominator_remains_undefined_and_null_in_output():
    tree = shapely.STRtree([Polygon([(-100, 40), (-99.9, 40), (-99.9, 40.1), (-100, 40.1)])])
    result = aggregate_batches([], tree, ["00000000001", "00000000002"], SEED)
    assert result["defined"].tolist() == [False, False]
    assert result["pooled_share"].shape == (2,)
    assert result["pooled_share"].tolist() != [0.0, 0.0]
    payload = parquet_bytes(["00000000001", "00000000002"], ["maricopa-az"] * 2, result)
    table = pq.read_table(pa.BufferReader(payload))
    assert table["defined"].to_pylist() == [False, False]
    assert table["pooled_taxonomy_mismatch_share"].to_pylist() == [None, None]


def test_rows_with_multi_sources_are_not_provider_fanned_out_in_pooled_metric():
    row = {"bbox": _bbox(), "sources": [{"dataset": "Foursquare"}, {"dataset": "Meta"}],
           "names": {"primary": "Community School"}, "categories": {"primary": "elementary_school"}}
    batch = pa.Table.from_pylist([row])
    tree = shapely.STRtree([Polygon([(-100, 40), (-99.9, 40), (-99.9, 40.1), (-100, 40.1)])])
    result = aggregate_batches([batch], tree, ["00000000001"], SEED)
    assert int(result["eligible_places"].sum()) == 1
    assert int(result["category_mismatches"].sum()) == 0
    assert int(result["provider_denominator"]["meta"].sum()) == 1
    assert int(result["provider_denominator"]["foursquare"].sum()) == 1
