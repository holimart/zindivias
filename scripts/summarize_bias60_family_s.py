#!/usr/bin/env python3
"""Summarize Family S outputs and their full label-free orthogonality diagnostics."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))
from bias60.determinism import canonical_bytes  # noqa: E402
from bias60.inputs import official_scored_pairs  # noqa: E402
from bias60.orthogonality import load_columns  # noqa: E402

IDEAS = ["S1", "S2", "S3", "S4", "S5"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def distribution(values: np.ndarray) -> dict[str, Any]:
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if not finite.size:
        return {"defined_values": 0}
    return {
        "defined_values": int(finite.size),
        "min": float(np.min(finite)),
        "q25": float(np.quantile(finite, 0.25)),
        "median": float(np.median(finite)),
        "mean": float(np.mean(finite)),
        "standard_deviation": float(np.std(finite)),
        "q75": float(np.quantile(finite, 0.75)),
        "max": float(np.max(finite)),
        "distinct_values": int(np.unique(finite).size),
    }


def summarize(*, workspace: Path, harness_path: Path, orthogonality_path: Path,
              output_path: Path) -> dict[str, Any]:
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    orth = json.loads(orthogonality_path.read_text(encoding="utf-8"))
    if not harness.get("gates", {}).get("all_passed"):
        raise ValueError("Family S summary requires a passing harness report")
    if not orth.get("gates", {}).get("all_passed"):
        raise ValueError("Family S summary requires a passing orthogonality report")
    pairs = official_scored_pairs(workspace)
    expected = [(geoid, region) for geoid, region in pairs]
    expected_geoids = [geoid for geoid, _ in expected]
    idea_paths = {}
    for idea, entry in harness["per_idea"].items():
        path = Path(entry["path"])
        idea_paths[idea] = path if path.is_absolute() else workspace / path
        if not idea_paths[idea].is_file() or sha256(idea_paths[idea]) != entry["sha256"]:
            raise ValueError(f"{idea} input for the orthogonality summary is missing or hash-mismatched")
    _, all_values_by_column, all_columns = load_columns(idea_paths)
    orth_columns = set(orth["correlation"]["columns"])
    vif_by_column = {row["column"]: row for row in orth["variance_inflation_factors"]}
    excluded = {row["column"]: row for row in orth["excluded_columns"]}
    high_pairs = [row for row in orth["pairs_exceeding_merge_threshold"]
                  if row["left"].split("|", 1)[0] in IDEAS
                  or row["right"].split("|", 1)[0] in IDEAS]
    outputs: dict[str, Any] = {}
    for idea in IDEAS:
        entry = harness["per_idea"].get(idea)
        if entry is None:
            raise ValueError(f"{idea} is absent from the current harness report")
        path = Path(entry["path"])
        path = path if path.is_absolute() else workspace / path
        if not path.is_file() or sha256(path) != entry["sha256"]:
            raise ValueError(f"{idea} output missing or hash-mismatched: {path}")
        table = pq.read_table(path, columns=["GEOID", "region", "idea", "component", "channel", "value", "defined"])
        if table.num_rows != entry["rows"]:
            raise ValueError(f"{idea} row count differs from harness report")
        frame = {name: table.column(name).to_pylist() for name in table.column_names}
        channels: dict[str, Any] = {}
        grouped: dict[tuple[str, str], list[int]] = {}
        for index, (component, channel) in enumerate(zip(frame["component"], frame["channel"])):
            grouped.setdefault((component, channel), []).append(index)
        for (component, channel), indices in sorted(grouped.items()):
            actual = [(frame["GEOID"][i], frame["region"][i]) for i in indices]
            if actual != expected:
                raise ValueError(f"{idea} {component}/{channel} does not cover the official GEOID partition")
            values = np.asarray([frame["value"][i] for i in indices], dtype=np.float64)
            defined = np.asarray([frame["defined"][i] for i in indices], dtype=np.bool_)
            name = f"{idea}|{component}|{channel}"
            correlations = []
            left = np.asarray([all_values_by_column[name].get(geoid, np.nan)
                               for geoid in expected_geoids], dtype=np.float64)
            for other in all_columns:
                if other == name:
                    continue
                right = np.asarray([all_values_by_column[other].get(geoid, np.nan)
                                    for geoid in expected_geoids], dtype=np.float64)
                valid = np.isfinite(left) & np.isfinite(right)
                overlap = int(valid.sum())
                pearson = None
                if overlap >= 3:
                    centred_left = left[valid] - left[valid].mean()
                    centred_right = right[valid] - right[valid].mean()
                    denominator = float(np.sqrt((centred_left ** 2).sum() *
                                                (centred_right ** 2).sum()))
                    if denominator > 0:
                        pearson = float((centred_left * centred_right).sum() / denominator)
                    else:
                        pearson = 0.0
                correlations.append({"column": other, "pearson_r": pearson,
                                     "pairwise_defined_rows": overlap,
                                     "included_in_preregistered_matrix": other in orth_columns})
            channels[name] = {
                "rows": int(len(values)), "defined_rows": int(defined.sum()),
                "coverage": float(defined.mean()),
                "distribution_defined_only": distribution(values[defined]),
                "artifact_sha256": entry["sha256"],
                "orthogonality": {
                    "included": name in orth_columns,
                    "correlations_to_every_idea_column": correlations,
                    "excluded_reason": excluded.get(name),
                    "vif": vif_by_column.get(name, {}).get("vif"),
                    "vif_flagged": bool(vif_by_column.get(name, {}).get("vif", 0) >
                                         orth["preregistration"]["thresholds"]["merge_vif"])
                    if vif_by_column.get(name, {}).get("vif") is not None else False,
                    "degenerate": vif_by_column.get(name, {}).get("degenerate", False),
                },
            }
        outputs[idea] = {"path": entry["path"], "sha256": entry["sha256"],
                         "rows": entry["rows"], "channels": channels,
                         "diagnostics": entry["diagnostics"]}
    report = {
        "report_type": "bias60-family-s-study-tract-summary",
        "method": "label-free summary of Family S per-tract artifacts and the orthogonality report; official GEOIDs and emitted estimators only",
        "official_scored_rows": len(expected),
        "family_ideas": IDEAS,
        "harness_report_sha256": sha256(harness_path),
        "orthogonality_report_sha256": sha256(orthogonality_path),
        "all_ideas_cover_official_partition": True,
        "outputs": outputs,
        "redundancy_flags": {
            "threshold_abs_rho": orth["preregistration"]["thresholds"]["merge_abs_rho"],
            "pairs_exceeding_threshold_with_family_s": high_pairs,
            "handling": "Retain every preregistered channel and mark these pairs as redundancy/merge groups for any downstream combiner; no channel was silently dropped or fitted here.",
        },
        "family_s_flagged_vif_columns": [row for row in orth["columns_exceeding_vif_threshold"]
                                         if row["column"].split("|", 1)[0] in IDEAS],
        "orthogonality_gate_passed": orth["gates"]["all_passed"],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(report)
    output_path.write_bytes(payload)
    report["output_sha256"] = hashlib.sha256(payload).hexdigest()
    print(json.dumps({"out": str(output_path), "sha256": report["output_sha256"],
                      "ideas": len(outputs), "channels": sum(len(item["channels"]) for item in outputs.values()),
                      "high_correlation_pairs_involving_S": len(high_pairs),
                      "vif_flags_involving_S": len(report["family_s_flagged_vif_columns"]),
                      "all_ideas_cover_official_partition": report["all_ideas_cover_official_partition"]},
                     indent=2, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--harness-report", type=Path, default=ROOT / "reports/ideas-harness.json")
    parser.add_argument("--orthogonality-report", type=Path, default=ROOT / "reports/ideas-orthogonality.json")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/bias60-family-s-summary.json")
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace or COMPETITION_WORKSPACE is required")
    report = summarize(workspace=args.workspace, harness_path=args.harness_report,
                       orthogonality_path=args.orthogonality_report, output_path=args.out)
    print(f"PASS Family S summary: {report['official_scored_rows']} GEOIDs, five ideas, exact artifact hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
