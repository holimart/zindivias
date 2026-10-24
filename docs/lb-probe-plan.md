# Bias Bounty public-LB probe plan (pre-registered)

**Scope:** public RMSE only; no training/reference labels exist. Official rules permit at most 10 submissions per day and 300 overall. The public leaderboard is about 30% of test; the remaining 70% is private. The steward authorized Bias-only probes on 2026-10-24, up to the Zindi daily cap, subject to the gates below. This plan intentionally spends at most four probes, not the reported ~70 remaining submissions.

## Candidate order and interpretation

Every candidate artifact must be added to `reports/candidates.json` with exact path, SHA-256, schema and row count before any upload. The manifest currently freezes C0. C1–C3 are definitions reserved for the later provided-data estimator; they are not yet artifact-eligible and must be materialized and hash-registered before probing.

| Order | Candidate | Submission / RMSE interpretation | Gate |
|---:|---|---|---|
| 1 | **C0 all-zero** (`reports/candidates/C0_all_zero.csv`) | `RMSE = sqrt(mean(y²))` on the public slice: target RMS, not target mean. Anchors all later comparisons. | Dry-run clean; one scored probe only after verifying the account already joined. |
| 2 | **C1 global 0.5** | Tests calibration around the midpoint of the `[0,1]` score range. Its score plus C0 is an aggregate diagnostic; neither identifies target mean without extra information. | Only after C0 is processed and recorded; exact candidate hash preregistered. |
| 3 | **C2 estimator v0** | Tests whether the first accepted provided-data estimator carries public-slice signal relative to C0/C1. | Candidate comes from `bias-bounty-gap-estimator-v0`; same authoritative IDs and validator. |
| 4 | **C3 region-centred estimator v0** | Adds region-specific intercept shifts based only on v0 prediction means. A score change tests aggregate usefulness of region centering. A single combined public RMSE does **not** reveal each region's target mean or regional error. | Only if C2 is stable and its scorecard/validation supports testing calibration; preregister exact artifact before upload. |

Per-region constants may be useful probes, but a combined RMSE alone cannot be read as four regional means. Without per-region public scores and the evaluated public count per region, don't infer region target means from one mixed score. If later designing one-region-at-a-time probes, add exact definitions, hashes, expected interpretation and a budget amendment here before use.

## Submission budget, stopping and final two

- Maximum planned total: **4 scored probes** (C0–C3), at most one per candidate, preferably one scored probe/day. The 10/day limit and estimated remaining slots are ceilings, not a target.
- Every upload requires (1) a frozen candidate manifest and artifact hash, (2) dry-run validation against the four official per-region sample ID lists, (3) a complete ledger record written to `reports/submissions.jsonl` **before the next upload**, and (4) operator-confirmed existing competition membership. If not already joined, stop and report to the steward; do not join.
- Stop on any schema/ID/range failure, ambiguous processing status, unexpected score change requiring candidate investigation, or after C3. No leaderboard-driven repeated tuning; no candidate variants outside the frozen set without a new written preregistration.
- Public-only selection is noisy because 70% is private. **Proposal for operator's final-two decision:** retain C0 as a stable anchor; choose the lowest-public-RMSE valid estimator candidate only if it improves on C0 by at least 1% relative RMSE and its documented behavior is coherent. Otherwise choose C0 and the simplest provided-data candidate. Treat this solely as a recommendation; final two selection belongs to the operator. Save the final choice and rationale with the ledger.

## Submission plumbing and safeguards

- `scripts/submit_zindi.py` is dry-run by default. It validates the frozen manifest, artifact hash, exact five-column schema, all 9,379 unique authoritative GEOIDs in fixed region/sample order, finite `[0,1]` score fields, and C0's all-zero values. It appends a `dry_run_validated` record with no score, platform submission ID or leaderboard rank.
- The script reads no token in dry-run and never logs token contents. `ZINDI_AUTH_TOKEN_FILE` is reserved for future authenticated use; token values must never be printed or committed. The CLI deliberately refuses live upload because this turn is plumbing dry-run only; use the authorized official Zindi portal after checking membership, and record all fields before any subsequent upload.
- No scored upload occurred in this turn. A dry-run ledger row is not a platform submission and consumes no daily slot.

## Dry-run reproduction

From the competition repo (the authoritative small samples are in `$COMPETITION_WORKSPACE/defined-flags-inputs`):

```sh
/home/martin/hddProjects/langywrap/uv run python scripts/build_c0_candidate.py \
  --sample-dir "$COMPETITION_WORKSPACE/defined-flags-inputs" \
  --out reports/candidates/C0_all_zero.csv
/home/martin/hddProjects/langywrap/uv run python scripts/submit_zindi.py \
  --candidate C0 --dry-run
```

The ledger records artifact and manifest SHA-256, command, runtime environment and git commit, plus explicit nulls for platform ID, score, split, leaderboard position and score source. Check `reports/submissions.jsonl` before any later upload. No credentials are needed for dry-run.
