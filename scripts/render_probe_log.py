#!/usr/bin/env python3
"""Render a deterministic, transparent log of historical leaderboard experiments."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVENTS_PATH = ROOT / "reports/submissions.jsonl"
ALGEBRA_PATH = ROOT / "reports/lb-score-algebra.json"
MOMENTS_PATH = ROOT / "reports/lb-subset-moments.json"
REGION_PATH = ROOT / "reports/region-probe-preregistration.json"
RETIREMENT_PATH = ROOT / "reports/probe-retirement-2026-10-24.json"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_events() -> list[dict]:
    events = [json.loads(line) for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    required = {"candidate_id", "status", "live_upload", "sha256"}
    if any(not required.issubset(event) for event in events):
        raise ValueError("submission ledger event is missing required fields")
    return events


def load_region_constants(path: Path) -> dict[str, float]:
    values: dict[str, set[float]] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if "GEOID" not in reader.fieldnames or "coverage_gap_score" not in reader.fieldnames:
            raise ValueError(f"unexpected candidate schema: {path}")
        for row in reader:
            values.setdefault(row["GEOID"][:5], set()).add(float(row["coverage_gap_score"]))
    output = {}
    for prefix, scores in values.items():
        if len(scores) != 1:
            raise ValueError(f"C1 is not constant within region/prefix {prefix}")
    state_region = {"04": "maricopa-az", "35": "maricopa-az", "06": "northern-ca", "40": "eastern-ok", "48": "south-central-tx"}
    region_values: dict[str, set[float]] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            region = state_region.get(row["GEOID"][:2])
            if region:
                region_values.setdefault(region, set()).add(float(row["coverage_gap_score"]))
    if set(region_values) != set(state_region.values()) or any(len(values) != 1 for values in region_values.values()):
        raise ValueError("C1 artifact does not contain one constant for each official region")
    return {region: next(iter(values)) for region, values in region_values.items()}


def candidate_descriptions(algebra: dict, moments: dict, regions: dict) -> dict[str, tuple[str, str, str]]:
    phase1 = algebra["phase1"]["candidates"]
    scored = moments["scored_candidate_moments"]
    descriptions: dict[str, tuple[str, str, str]] = {}
    descriptions["C0"] = (
        "All four score columns set to 0. The first 9,379-row file was rejected for omitting the 415 platform-template rows; the later 9,794-row file was scored.",
        "E[p²]=0; E[p y]=0. C0 public RMSE² is the reported target second-moment proxy, not a label estimate beyond the public aggregate.",
        "reports/candidates/C0_all_zero.csv",
    )
    descriptions["C1"] = (
        "One constant per official region, copied from the v0 C2 model's region-mean predictions; values: " + ", ".join(f"{r}={v:.9f}" for r, v in regions.items()) + ".",
        "E[p²]={:.9f}; inferred E[p y]={:.9f}; uncentred association proxy={:.6f}.".format(phase1["C1"]["E_p2_all_official_rows"], phase1["C1"]["inferred_E_py"], phase1["C1"]["uncentred_correlation_proxy"]),
        "reports/candidates/C1_region_constants.csv",
    )
    formulas = "C2 v0 proxy denominators (global scale 1): road=.35+.55√(land_km²)+.0007·population+2·urban; building=max(.5,.30·population+4√land+5·urban); fire=.15+pop/30000+.45·urban; EMS=.15+pop/40000+.45·urban; schools=.20+pop/2500+.20·urban; establishments=.20+pop/70+.50·urban. Component gap=clip(1−observed/expected,0,1); definedness: road expectation≥6 km; building observed>0 or expected≥1; each facility observed>0 or expected≥.5; establishments observed>0 or expected≥1. POI equally averages defined facility and establishments halves."
    descriptions["C2"] = (
        formulas,
        "E[p²]={:.9f}; inferred E[p y]={:.9f}; uncentred association proxy={:.6f}.".format(phase1["C2"]["E_p2_all_official_rows"], phase1["C2"]["inferred_E_py"], phase1["C2"]["uncentred_correlation_proxy"]),
        "reports/candidates/C2_component_estimator_v0.csv",
    )
    descriptions["C3"] = (
        "Same v0 proxy formulas as C2, with every expected denominator multiplied by 1.25.",
        "E[p²]={:.9f}; inferred E[p y]={:.9f}; uncentred association proxy={:.6f}; unconstrained best global scale={:.6f} (negative; diagnostic only).".format(phase1["C3"]["E_p2_all_official_rows"], phase1["C3"]["inferred_E_py"], phase1["C3"]["uncentred_correlation_proxy"], phase1["C3"]["unconstrained_optimal_global_scale"]),
        "reports/candidates/C3_denominator_x125.csv",
    )
    p4 = algebra["phase2_preregistered"]["probes"]["P4"]
    descriptions["P4"] = (
        f"P4 = α·C1, α={p4['alpha']:.16f}; this value was fit from historical public score algebra and is retained only for reproduction of the scored artifact.",
        "E[p²]={:.9f}; inferred E[p y]={:.9f}.".format(scored["P4"]["E_p2_all_official_rows"], scored["P4"]["inferred_E_py"]),
        p4["artifact"],
    )
    p5 = algebra["phase2_preregistered"]["probes"]["P5"]
    residual = algebra["P5_residual_moments"]
    descriptions["P5"] = (
        f"Pre-registered α·region-mean(C2)+β·within-region residual; α={p5['alpha']:.16f}, β={p5['beta']:.16f} (unconstrained β optimum={p5['beta_unconstrained_optimum']:.6f}, constrained for [0,1]); forecast RMSE={p5['predicted_public_rmse']:.9f}. Never successfully scored.",
        "No P5 response moment: upload rejected before scoring. Forecast used earlier score-derived E[q y]={:.9f} and E[d y]={:.9f}; these are C1/C2-derived historical moments, not P5 results.".format(residual["inferred_E_qy"], residual["inferred_E_dy"]),
        p5["artifact"],
    )
    p6 = algebra["phase2_preregistered"]["probes"]["P6"]
    descriptions["P6"] = (
        f"Constant score c={p6['c']:.15f}; working hypothesis E[y]≈c was derived from C1's score-algebra moments; forecast RMSE={p6['predicted_public_rmse']:.9f}. Dry-run only; never uploaded.",
        "No P6 response moment; E[y]≈c is a pre-submission assumption, not a measured public/private target mean.",
        p6["artifact"],
    )
    region_entries = algebra["phase3_preregistration"]["region_indicator_candidates"]
    subset = {item["cell_id"]: item for item in moments["subset_probe_moments"]}
    for candidate, item in region_entries.items():
        params = item["parameters"]
        moment = subset.get(candidate, {}).get("E[(y-base)1_S]_public_estimate")
        descriptions[candidate] = (
            f"p=c_r={params['c_r']:.15f} in {params['region']} and 0 elsewhere; c_r=α·C1 region mean with α={params['alpha_from_P4']:.16f}. Pre-registered forecast RMSE={params['predicted_public_rmse']:.9f}; dry-run only, not uploaded.",
            "No observed subset moment; no public response exists." if moment is None else f"E[(y-base)1_S] estimate={moment}.",
            item["artifact"],
        )
    return descriptions


def score_display(event: dict) -> str:
    if event.get("status") == "successful" and event.get("our_score") is not None:
        return f"{float(event['our_score']):.9f}"
    if event.get("our_score") is not None:
        return "not a score (initial platform placeholder; see correction)"
    return "—"


def event_note(event: dict) -> str:
    text = event.get("status_description") or event.get("correction_of") or event.get("response") or ""
    return " ".join(str(text).replace("|", "\\|").split())


def render() -> str:
    events = load_events()
    algebra = json.loads(ALGEBRA_PATH.read_text(encoding="utf-8"))
    moments = json.loads(MOMENTS_PATH.read_text(encoding="utf-8"))
    descriptions = candidate_descriptions(algebra, moments, load_region_constants(ROOT / "reports/candidates/C1_region_constants.csv"))
    rows = []
    for number, event in enumerate(events, 1):
        ts = event.get("timestamp_utc", event.get("ts", ""))
        rows.append("| {} | {} | {} | {} | {} | {} | {} | `{}` | `{}` | {} |".format(
            number, ts, event["candidate_id"], event["status"], "yes" if event["live_upload"] else "no",
            event.get("platform_submission_id") or "—", score_display(event), event.get("sha256", ""),
            event.get("artifact", ""), event_note(event) or "—"))
    candidate_rows = []
    candidate_ids = []
    for event in events:
        cid = event["candidate_id"]
        if cid not in candidate_ids:
            candidate_ids.append(cid)
    for cid in candidate_ids:
        if cid not in descriptions:
            continue
        associated = [event for event in events if event["candidate_id"] == cid]
        successes = [event for event in associated if event.get("status") == "successful" and event.get("our_score") is not None]
        final = successes[-1] if successes else None
        score = f"{float(final['our_score']):.9f}; ID `{final.get('platform_submission_id')}`" if final else "No successful score; no submission ID"
        hashes = ", ".join(f"`{event['sha256'][:12]}…`" for event in associated)
        parameter, inferred, artifact = descriptions[cid]
        candidate_rows.append(f"| {cid} | {parameter} | {score} | {inferred} | `{artifact}`; event hash(es): {hashes} |")
    ledger_hash = file_hash(EVENTS_PATH)
    algebra_hash = file_hash(ALGEBRA_PATH)
    moments_hash = file_hash(MOMENTS_PATH)
    region_hash = file_hash(REGION_PATH)
    retirement_hash = file_hash(RETIREMENT_PATH)
    return f'''# Historical leaderboard experiment and calibration log

**Status:** historical record only. The operator retired information probes on 2026-10-24; no new leaderboard-information probes are authorized or queued. The candidate/status ledger is rendered locally from `{EVENTS_PATH.relative_to(ROOT)}`; this script makes no network requests and never reads sample score-placeholder columns. Do not interpret the event timestamps or old permissions as current authorization.

Public leaderboard RMSEs below are quoted only where the ledger records a successful Zindi status and a platform submission ID. Initial API values were not treated as scores; e.g. Zindi's initial `0.0` for `ggVY44Ui` was later corrected to a failed-processing record. Failed uploads and dry runs have no score and imply no target moment.

The score-algebra identity is `MSE(p)=E[y²]−2E[p·y]+E[p²]`. The reported inferred moments use the all-official-row candidate `E[p²]` as a proxy for the public split and assume representative composition; they are aggregate, noisy calibration diagnostics—not labels, per-tract evidence, or private-leaderboard estimates. The official domain contains 9,379 IDs; Zindi's platform template contains 9,794 rows including 415 format-only eastern-WA rows. Historical score algebra uses the 9,379 official IDs and explicitly records this composition assumption.

## Candidate-level experiment summary

| Candidate | Parameters / hypothesis | Successful public result | Score-derived/inferred moment and qualification | Artifact and ledger hashes |
|---|---|---|---|---|
{chr(10).join(candidate_rows)}

### Interpretation and retirement

- C0's first 9,379-row upload (`ggVY44Ui`) failed because Zindi required the full 9,794-row template; its initial `0.0` was an API placeholder, not a score. The later full-template C0 (`uRRw9KpB`) is the score-bearing all-zero anchor. The 415 eastern-WA fills were formatting-only.
- C1–C3 are historical model/probe uploads; their public scores yielded the displayed aggregate moments under the assumption above. These moments are not used to set any current model prior.
- P4's `α=0.3288136427728255` is a leaderboard-derived historical calibration constant. It exists only to reproduce the already-scored P4 artifact and is expressly prohibited from v2 or any new scored candidate.
- P5's upload was rejected by the platform's daily limit before scoring. P6 and R-AZ/R-CA/R-OK/R-TX were dry-run-only and subsequently retired. Their forecasts/assumptions are not observations.
- No leaderboard-derived target moments, calibration coefficients, or regional means may enter new scored predictions. Follow [`../submission-policy.md`](../submission-policy.md) and the model-first methodology.

## Complete submissions-ledger event history

Every event in `reports/submissions.jsonl` is listed below, including dry runs, upload failures, initial platform responses, and successful status refreshes. `Public RMSE` is populated only for events explicitly marked successful. A hash is the artifact hash recorded on that event; C0's rejected and successfully scored artifacts intentionally have different hashes.

| # | UTC timestamp | Candidate | Ledger status | Live upload | Platform ID | Public RMSE | Candidate SHA-256 | Artifact | Ledger note |
|---:|---|---|---|:---:|---|---|---|---|---|
{chr(10).join(rows)}

**Ledger events:** {len(events)}. **Source SHA-256:** `reports/submissions.jsonl` `{ledger_hash}`. Supporting frozen audits: `reports/lb-score-algebra.json` `{algebra_hash}`; `reports/lb-subset-moments.json` `{moments_hash}`; `reports/region-probe-preregistration.json` `{region_hash}`; `reports/probe-retirement-2026-10-24.json` `{retirement_hash}`. Re-render with `uv run --with-requirements requirements.txt python scripts/render_probe_log.py --out docs/writeup/probe-log.md`.
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "docs/writeup/probe-log.md")
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(), encoding="utf-8")


if __name__ == "__main__":
    main()
