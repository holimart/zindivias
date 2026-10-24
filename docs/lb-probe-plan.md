# Bias Bounty public-LB probe plan (pre-registered)

**Scope:** public RMSE only; no training/reference labels exist. Official rules permit at most 10 submissions per day and 300 overall. The public leaderboard is about 30% of test; the remaining 70% is private. The steward authorized Bias-only probes on 2026-10-24, up to the Zindi daily cap, subject to the gates below. This plan intentionally spends at most four probes, not the reported ~70 remaining submissions.

## Candidate order and interpretation

Every candidate artifact must be added to `reports/candidates.json` with exact path, SHA-256, schema, row count, per-region means and defined rates before any upload. Following the steward's 2026-10-24 full-ID instruction, C0–C3 are now frozen against the 9,794-row Data-page template order. Their 9,379 official-region values are preserved; the 415 extra eastern-WA rows are template-only and use the documented fills in `reports/full-id-candidate-audit.json`. The template's score placeholders are never read. The official scored-region scope remains the four authoritative samples. Candidate hashes/rates are in the manifest and audit; no accuracy claim is made for any prediction or fill.

| Order | Candidate | Submission / RMSE interpretation | Gate |
|---:|---|---|---|
| 1 | **C0 all-zero** (`reports/candidates/C0_all_zero.csv`) | `RMSE = sqrt(mean(y²))` on the public slice: target RMS, not target mean. Anchors all later comparisons. | Dry-run clean; one scored probe only after verifying the account already joined. |
| 2 | **C1 per-region v0 constants** | In each official region, all four score columns equal that region's C2 v0 predicted composite mean. Eastern-WA template extras use the pooled C2 composite mean. Tests tract-level detail against smoothing; it does **not** estimate target region means. | Only after C2 is frozen; exact candidate hash in manifest before any upload. |
| 3 | **C2 component estimator v0** | Tests whether the provided-data component estimator carries public-slice signal relative to C0/C1. | Candidate comes from `bias-bounty-gap-estimator-v0`; the 9,379 official rows are preserved and full 9,794 template IDs validated. |
| 4 | **C3 denominator sensitivity ×1.25** | Multiplies each C2 expected-reference proxy by 1.25 while keeping observed features fixed; probes sensitivity to denominator calibration, not accuracy by component. | Only after C2; exact artifact hash and formula preregistered before upload. |

Per-region constants are derived from C2 prediction means, not target means. A combined RMSE alone cannot be read as four regional target means. Without per-region public scores and the evaluated public count per region, don't infer region target means from one mixed score. Do not add one-region-at-a-time probes without exact definitions, hashes, interpretation and a budget amendment here.

## Submission budget, stopping and final two

- Maximum planned total: **4 scored probes** (C0–C3), at most one per candidate, preferably one scored probe/day. The 10/day limit and estimated remaining slots are ceilings, not a target.
- Every upload requires (1) a frozen candidate manifest and artifact hash, (2) exact 9,794-ID template-order validation via `scripts/build_full_id_candidates.py`, including proof that the 9,379 official rows are unchanged and all values are finite/bounded, (3) the shared orchestrator dry-run, (4) the orchestrator-managed ledger record, and (5) existing competition membership. If membership is absent, stop and report; do not join. The local validator `scripts/submit_zindi.py` is not the full-template validator and has no live path.
- Stop on any schema/ID/range failure, ambiguous processing status, unexpected score change requiring candidate investigation, or after C3. No leaderboard-driven repeated tuning; no candidate variants outside the frozen set without a new written preregistration.
- Public-only selection is noisy because 70% is private. **Proposal for operator's final-two decision:** retain C0 as a stable anchor; choose the lowest-public-RMSE valid estimator candidate only if it improves on C0 by at least 1% relative RMSE and its documented behavior is coherent. Otherwise choose C0 and the simplest provided-data candidate. Treat this solely as a recommendation; final two selection belongs to the operator. Save the final choice and rationale with the ledger.

## Submission plumbing and safeguards

- `scripts/submit_zindi.py` remains a dry-run-only validator for the four official samples (9,379 rows) and refuses live submission. Do not use it to validate the expanded Data-page template.
- Live platform activity is permitted only through `$ORCHESTRATOR_ROOT/scripts/zindi_submit.py`, which reads the token from the configured path, never prints it, and appends its own ledger entries. Run without `--live` first; never upload outside that tool. The official sample score placeholders and the legacy template's score placeholders are never labels.
- The steward-recorded first C0 attempt `ggVY44Ui` failed processing because the 415 eastern-WA IDs were absent; the apparent 0.0 was corrected as a pre-scoring placeholder. The full-ID directive adds these only as template rows with documented pooled fills; they are not treated as official scored rows.

## Platform template follow-up (2026-10-24)

The Data-page template and the official scored samples differ: the template has 9,794 IDs (including 415 eastern-WA rows), while official regional samples have 9,379 scored IDs. The steward's dispatched `bias-bounty-full-id-candidates` item directs platform-format compatibility using the full template. `scripts/build_full_id_candidates.py` adds those IDs in template order, preserves official rows, and documents candidate-specific pooled eastern-WA fills. It reads only GEOID/region metadata from the template and does not read placeholder scores. This does not alter the official scored-region definition or create target evidence for eastern-WA. The previous C0 attempt `ggVY44Ui` failed processing and its 0.0 was not a score. All further platform attempts must use the shared orchestrator tool, after the full-ID audit and frozen manifest; stop if processing is ambiguous.

## Dry-run reproduction

Rebuild and locally verify the full template candidates (the legacy file's score placeholders are ignored):

```sh
python3 scripts/build_full_id_candidates.py --workspace "$COMPETITION_WORKSPACE"
python3 -I "$ORCHESTRATOR_ROOT/scripts/zindi_submit.py" \
  --competition bias-bounty-mapping-equity-challenge \
  --repo "$COMPETITION_REPO" --candidate C0
```

The shared command without `--live` performs the orchestrator dry-run and appends its own ledger row; the local 9,379-row validator must not be used on these 9,794-row artifacts. Only after the dry-run, frozen hashes and existing-membership check may an authorized probe use the same command with `--live`. Then poll `--status` for the final score; never treat an `initial` response's 0.0 as a score. Keep the original steward failure/correction rows in `reports/submissions.jsonl`.

## Platform probe results (2026-10-24)

All four pre-registered full-template candidates passed orchestrator dry-run and later reported `successful` status. Scores below are the public RMSE returned by the shared orchestrator `--status`; each is recorded with its upload and score-refresh rows in `reports/submissions.jsonl`.

| Candidate | Submission ID | Public RMSE | Candidate SHA-256 |
|---|---|---:|---|
| C0 | `uRRw9KpB` | 0.107246299 | `0edf8140123e2135489e5728fd71bce6f1e28a97f447ca411ba45fdbc02bc214` |
| C1 | `BvyKrR6v` | 0.146449112 | `dc2e368194b31408cb5c313fa8ce3f3fa59a1b5965bc2df8e4217bbc9e0c82e0` |
| C2 | `rTARuUaG` | 0.235358712 | `5728fbbaa2e062fbd14199190a260132c95b2b858da539d7a9580cb2abc62514` |
| C3 | `TM83xxU6` | 0.349409867 | `e5ee50f5c2cca149550f3408638d2b77431f6ed62cf104dfe8c07d8c8416f9e4` |

C0 had the lowest public RMSE among these four. This is public-only platform feedback, not a local accuracy estimate; no labels are available, the private 70% remains unobserved, and no claim is made that eastern-WA template extras were scored or ignored. The prior submission `ggVY44Ui` remains a separately recorded processing failure; its 0.0 was not a score. No further candidate variants or tuning are authorized by this probe set; final-two selection remains the operator's decision.
