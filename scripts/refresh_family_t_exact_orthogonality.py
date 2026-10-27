#!/usr/bin/env python3
"""Refresh descriptive pairwise orthogonality among exact Family T v2 artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.inputs import official_scored_pairs  # noqa: E402

IDEAS = ("T1-v2", "T2-v2", "T3-v2")
CHANNELS = {
    "T1-v2": ("gap",),
    "T2-v2": ("covariate:orphan_link_segments", "covariate:attached_carriageway_length_km"),
    "T3-v2": ("covariate:grade_sep_per_km2",),
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_idea(path: Path, idea: str, official: list[tuple[str, str]]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    table = pq.read_table(path, columns=["GEOID", "region", "idea", "channel", "value", "defined"])
    expected_rows = len(official) * len(CHANNELS[idea])
    if table.num_rows != expected_rows:
        raise ValueError(f"{idea} has {table.num_rows} rows; expected {expected_rows}")
    locations = {pair: i for i, pair in enumerate(official)}
    seen: set[tuple[str, str, str]] = set()
    data = {channel: (np.full(len(official), np.nan), np.zeros(len(official), dtype=bool))
            for channel in CHANNELS[idea]}
    for row in table.to_pylist():
        if row["idea"] != idea:
            raise ValueError(f"artifact {path.name} contains unexpected idea {row['idea']}")
        pair = (str(row["GEOID"]), str(row["region"]))
        channel = str(row["channel"])
        key = (*pair, channel)
        if pair not in locations or channel not in data or key in seen:
            raise ValueError(f"{idea} contains unexpected or duplicate output {key}")
        seen.add(key)
        values, defined = data[channel]
        index = locations[pair]
        mask = bool(row["defined"])
        value = row["value"]
        if mask:
            if value is None or not np.isfinite(float(value)):
                raise ValueError(f"{idea}/{channel} defined value is null or nonfinite")
            values[index] = float(value)
            defined[index] = True
        elif value is None or float(value) != 0.:
            raise ValueError(f"{idea}/{channel} undefined value must use zero sentinel")
    if len(seen) != expected_rows:
        raise ValueError(f"{idea} artifact does not exactly cover every official pair and channel")
    for channel, (values, defined) in data.items():
        if int(defined.sum()) == 0 or np.any(defined & ~np.isfinite(values)) or np.any(~defined & np.isfinite(values)):
            raise ValueError(f"{idea}/{channel} mask/value consistency failed")
    return data


def pearson(x: np.ndarray, y: np.ndarray, mask: np.ndarray) -> float | None:
    a, b = x[mask], y[mask]
    if a.size < 2 or a.std(ddof=0) == 0. or b.std(ddof=0) == 0.:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def run(workspace: Path) -> dict:
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(official) != 9379 or len(set(official)) != 9379:
        raise ValueError("official sample partition must contain 9,379 unique GEOID-region pairs")
    artifacts = {idea: workspace / "ideas" / f"{idea}.parquet" for idea in IDEAS}
    arrays = {idea: load_idea(path, idea, official) for idea, path in artifacts.items()}
    coverage = {f"{idea}_{channel}": int(mask.sum())
                for idea in IDEAS for channel, (_, mask) in arrays[idea].items()}
    pairwise = []
    for i, left_idea in enumerate(IDEAS):
        for right_idea in IDEAS[i + 1:]:
            for left_channel, (left, left_mask) in arrays[left_idea].items():
                for right_channel, (right, right_mask) in arrays[right_idea].items():
                    joint = left_mask & right_mask
                    pairwise.append({"left": f"{left_idea}/{left_channel}",
                                     "right": f"{right_idea}/{right_channel}",
                                     "joint_defined_rows": int(joint.sum()),
                                     "pearson_r": pearson(left, right, joint)})
    report = {"family": "T", "purpose": "partial exact-recipe v2 pairwise orthogonality refresh; descriptive, not integration authorization",
              "scope": {"official_rows": len(official), "official_geoid_region_sha256": hashlib.sha256(
                  "\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest(),
                  "exact_pair_and_channel_alignment": True},
              "input_artifacts": {idea: {"path": str(path), "sha256": sha256_file(path)}
                                  for idea, path in artifacts.items()},
              "coverage": coverage, "pairwise": pairwise,
              "unavailable_recipe": {"T4-v2": "No permitted population-centroid point/coordinate input; not emitted or compared."},
              "limitations": "Pearson correlations are descriptive on pairwise complete defined rows. T4-v2 is specifically unavailable; these results do not authorize estimator integration."}
    out = ROOT / "reports/bias60-family-t-exact-orthogonality.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    reconciliation_path = ROOT / "reports/bias60-family-t-exact-reconciliation.json"
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    reconciliation.setdefault("measure_after", {}).update({
        "coverage_and_orthogonality_refreshed": True,
        "orthogonality_refresh_scope": "emitted exact recipes T1-v2/T2-v2/T3-v2 only; T4-v2 excluded because the required permitted population-centroid geometry is unavailable",
        "orthogonality_report_sha256": sha256_file(out),
    })
    reconciliation.setdefault("validation", {})["orthogonality_refresh"] = (
        "Updated exact-v2 pairwise Pearson report for all emitted recipes T1-v2/T2-v2/T3-v2; "
        "T4-v2 is specifically blocked by missing permitted population-centroid geometry and is not imputed or compared.")
    reconciliation_path.write_text(json.dumps(reconciliation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    result = run(workspace)
    print(json.dumps({"coverage": result["coverage"], "pairwise": result["pairwise"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
