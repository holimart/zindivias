"""Fast checks that the cold-reproduction serializer matches frozen CSVs."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import reproduce_candidates as reproduce  # noqa: E402


def _sample_ids(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        idx = header.index("GEOID")
        return [row[idx] for row in reader]


def test_pair_serializer_rebuilds_frozen_candidate_hashes(tmp_path: Path) -> None:
    workspace = os.environ.get("COMPETITION_WORKSPACE")
    if not workspace:
        pytest.skip("COMPETITION_WORKSPACE is required for frozen-artifact replay")
    workspace = Path(workspace)
    template_ids, template_regions = reproduce.read_template(
        REPO / "data/reproduction/template-geoid-region.csv")
    official_region: dict[str, str] = {}
    for region in reproduce.REGIONS:
        for geoid in _sample_ids(workspace / "samples" / f"{region}-sample-submission.csv"):
            assert geoid not in official_region
            official_region[geoid] = region
    with (REPO / "reports/candidates/C1_region_constants.csv").open(
            newline="", encoding="utf-8") as stream:
        c1 = {row["GEOID"]: float(row["coverage_gap_score"])
              for row in csv.DictReader(stream)}
    means = {}
    for region in reproduce.REGIONS:
        region_values = {c1[g] for g, assigned in official_region.items() if assigned == region}
        assert len(region_values) == 1  # C1 is a frozen per-region constant vector.
        means[region] = next(iter(region_values))
    config = json.loads((REPO / "config/calibration.json").read_text(encoding="utf-8"))
    rows = reproduce.make_pair_rows(
        template_ids, template_regions, official_region, means,
        config["lb_calibrated_constants"]["alpha_c1"]["value"],
    )
    for cid, candidate in (("C0", config["review_pair"][0]), ("P4", config["review_pair"][1])):
        path = Path(candidate["artifact"])
        digest = reproduce.write_candidate(tmp_path / path.name, rows[cid])
        assert digest == candidate["sha256"]
