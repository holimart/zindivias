# Bias Bounty public-LB probe plan (pre-registered)

**Scope:** public RMSE only; no training/reference labels exist. Official rules permit at most 10 submissions per day and 300 overall. The public leaderboard is about 30% of test; the remaining 70% is private. The steward authorized Bias-only probes on 2026-10-24, up to the Zindi daily cap, subject to the gates below. This plan intentionally spends at most four probes, not the reported ~70 remaining submissions.

## Candidate order and interpretation

Every candidate artifact must be added to `reports/candidates.json` with exact path, SHA-256, schema, row count, per-region means and defined rates before any upload. The manifest now freezes C0–C3; C1–C3 artifacts were materialized from estimator v0 and pass the same sample-ID/domain dry-run validation. Candidate hashes and measured rates are in the manifest and audit; these estimates are unvalidated and have no accuracy claims.

| Order | Candidate | Submission / RMSE interpretation | Gate |
|---:|---|---|---|
| 1 | **C0 all-zero** (`reports/candidates/C0_all_zero.csv`) | `RMSE = sqrt(mean(y²))` on the public slice: target RMS, not target mean. Anchors all later comparisons. | Dry-run clean; one scored probe only after verifying the account already joined. |
| 2 | **C1 per-region v0 constants** | Each region's four score columns equal the C2 v0 predicted composite mean for that region. Tests tract-level detail against region-level smoothing; it does **not** estimate target region means. | Only after C2 is frozen; exact candidate hash in manifest before any upload. |
| 3 | **C2 component estimator v0** | Tests whether the provided-data component estimator carries public-slice signal relative to C0/C1. | Candidate comes from `bias-bounty-gap-estimator-v0`; same authoritative IDs and validator. |
| 4 | **C3 denominator sensitivity ×1.25** | Multiplies each C2 expected-reference proxy by 1.25 while keeping observed features fixed; probes sensitivity to denominator calibration, not accuracy by component. | Only after C2; exact artifact hash and formula preregistered before upload. |

Per-region constants are derived from C2 prediction means, not target means. A combined RMSE alone cannot be read as four regional target means. Without per-region public scores and the evaluated public count per region, don't infer region target means from one mixed score. Do not add one-region-at-a-time probes without exact definitions, hashes, interpretation and a budget amendment here.

## Submission budget, stopping and final two

- Maximum planned total: **4 scored probes** (C0–C3), at most one per candidate, preferably one scored probe/day. The 10/day limit and estimated remaining slots are ceilings, not a target.
- Every upload requires (1) a frozen candidate manifest and artifact hash, (2) dry-run validation against the four official per-region sample ID lists, (3) a complete ledger record written to `reports/submissions.jsonl` **before the next upload**, and (4) operator-confirmed existing competition membership. If not already joined, stop and report to the steward; do not join.
- Stop on any schema/ID/range failure, ambiguous processing status, unexpected score change requiring candidate investigation, or after C3. No leaderboard-driven repeated tuning; no candidate variants outside the frozen set without a new written preregistration.
- Public-only selection is noisy because 70% is private. **Proposal for operator's final-two decision:** retain C0 as a stable anchor; choose the lowest-public-RMSE valid estimator candidate only if it improves on C0 by at least 1% relative RMSE and its documented behavior is coherent. Otherwise choose C0 and the simplest provided-data candidate. Treat this solely as a recommendation; final two selection belongs to the operator. Save the final choice and rationale with the ledger.

## Submission plumbing and safeguards

- `scripts/submit_zindi.py` is dry-run by default. It validates the frozen manifest, artifact hash, exact five-column schema, all 9,379 unique authoritative GEOIDs in fixed region/sample order, finite `[0,1]` score fields, and C0's all-zero values. It appends a `dry_run_validated` record with no score, platform submission ID or leaderboard rank.
- The script reads no token in dry-run and never logs token contents. `ZINDI_AUTH_TOKEN_FILE` is checked only as a path in the refused live mode; token values are never opened, printed or committed. The CLI deliberately refuses live upload; use the authorized official Zindi portal only after checking membership, and record all fields before any subsequent upload.
- No scored upload occurred in the original plumbing turn. Its dry-run ledger row was not a platform submission; the later steward C0 attempts are separately recorded below and failed processing.

## Platform contract stop (2026-10-24)

The steward's entries in `reports/submissions.jsonl` record a C0 upload attempt using the 9,379 official-region IDs. Zindi processing reported that it instead expected the legacy Data-page sample's 9,794 IDs, including 415 `eastern-wa` GEOIDs outside the official scored regions. The apparent `initial`/0.0 response was subsequently corrected in the ledger as a failed processing placeholder, not a valid score. This conflicts with the authoritative rules and four per-region samples. Stop all further uploads and report to the steward; do not add or infer scores for the 415 extra IDs and do not bypass the official contract. C0–C3 remain locally validator-clean against the authoritative 9,379 IDs, but are not platform-upload-ready until the steward resolves the mismatch.

## Dry-run reproduction

From the competition repo (the authoritative downloaded samples are in `$COMPETITION_WORKSPACE/samples`; the workspace-level `SampleSubmission.csv` is legacy and must not be used):

```sh
/home/martin/hddProjects/langywrap/uv run python scripts/build_c0_candidate.py \
  --sample-dir "$COMPETITION_WORKSPACE/samples" \
  --out reports/candidates/C0_all_zero.csv
/home/martin/hddProjects/langywrap/uv run python scripts/submit_zindi.py \
  --candidate C0 --dry-run
```

The original C0 dry-run record is preserved in `reports/submissions.jsonl`; later steward-attributed C0 live-attempt entries and the processing-failure correction are separate records documented in the platform stop above. For C1–C3 validator-only repeats without adding ledger rows, pass a temporary ledger, for example `--candidate C2 --ledger /tmp/c2-dry-run.jsonl`. Each dry-run records artifact/manifest SHA-256, command, runtime environment and git commit, plus nulls for platform ID, score, split, leaderboard position and score source. Inspect the ledger before any future authorized operation. No credentials are needed for dry-run.
