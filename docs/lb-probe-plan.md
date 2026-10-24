# Bias Bounty public-LB probe plan (pre-registered)

**Scope:** public RMSE only; no training/reference labels exist. Official rules permit at most 10 submissions per day and 300 overall. The public leaderboard is about 30% of test; the remaining 70% is private. The steward authorized Bias-only calibration probes on 2026-10-24, subject to the gates below. Phase 1 used four probes; phase 2 preregisters three additional global-transform probes (one of four slots unused), not the reported ~70 remaining submissions.

## Candidate order and interpretation

Every candidate artifact must be added to `reports/candidates.json` with exact path, SHA-256, schema, row count, per-region means and defined rates before any upload. Following the steward's 2026-10-24 full-ID instruction, C0–C3 are now frozen against the 9,794-row Data-page template order. Their 9,379 official-region values are preserved; the 415 extra eastern-WA rows are template-only and use the documented fills in `reports/full-id-candidate-audit.json`. The template's score placeholders are never read. The official scored-region scope remains the four authoritative samples. Candidate hashes/rates are in the manifest and audit; no accuracy claim is made for any prediction or fill.

| Order | Candidate | Submission / RMSE interpretation | Gate |
|---:|---|---|---|
| 1 | **C0 all-zero** (`reports/candidates/C0_all_zero.csv`) | `RMSE = sqrt(mean(y²))` on the public slice: target RMS, not target mean. Anchors all later comparisons. | Completed successfully; exact public result is recorded below. |
| 2 | **C1 per-region v0 constants** | In each official region, all four score columns equal that region's C2 v0 predicted composite mean. Eastern-WA template extras use the pooled C2 composite mean. Tests tract-level detail against smoothing; it does **not** estimate target region means. | Only after C2 is frozen; exact candidate hash in manifest before any upload. |
| 3 | **C2 component estimator v0** | Tests whether the provided-data component estimator carries public-slice signal relative to C0/C1. | Candidate comes from `bias-bounty-gap-estimator-v0`; the 9,379 official rows are preserved and full 9,794 template IDs validated. |
| 4 | **C3 denominator sensitivity ×1.25** | Multiplies each C2 expected-reference proxy by 1.25 while keeping observed features fixed; probes sensitivity to denominator calibration, not accuracy by component. | Only after C2; exact artifact hash and formula preregistered before upload. |

Per-region constants are derived from C2 prediction means, not target means. A combined RMSE alone cannot be read as four regional target means. Without per-region public scores and the evaluated public count per region, don't infer region target means from one mixed score. Do not add one-region-at-a-time probes without exact definitions, hashes, interpretation and a budget amendment here.

## Submission budget, stopping and final two

- Phase 1 used four pre-registered probes (C0–C3). Phase 2 registers only **three of four allowed probes** (P4–P6), for a seven-probe planned total; P7 remains unused. Probes are global transforms and must stay within the official 10/day and 300-total limits. The daily cap is a ceiling, not a target.
- Every upload requires (1) a frozen candidate manifest and artifact hash, (2) exact 9,794-ID template-order validation via `scripts/build_full_id_candidates.py`, including proof that the 9,379 official rows are unchanged and all values are finite/bounded, (3) the shared orchestrator dry-run, (4) the orchestrator-managed ledger record, and (5) existing competition membership. If membership is absent, stop and report; do not join. The local validator `scripts/submit_zindi.py` is not the full-template validator and has no live path.
- Stop on any schema/ID/range failure, ambiguous processing status, unexpected score change requiring candidate investigation, or after P6. No leaderboard-driven repeated tuning; no candidate variants outside the frozen phase-2 set without a new written preregistration.
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

C0 had the lowest public RMSE among the phase-1 four. This is public-only platform feedback, not a local accuracy estimate; no labels are available, the private 70% remains unobserved, and no claim is made that eastern-WA template extras were scored or ignored. The prior submission `ggVY44Ui` remains a separately recorded processing failure; its 0.0 was not a score. Final-two selection remains the operator's decision.

## Phase 2 preregistration — score-algebra calibration (2026-10-24)

The phase-1 RMSE decomposition is reproducible with `scripts/lb_score_algebra.py --workspace "$COMPETITION_WORKSPACE"` and committed in `reports/lb-score-algebra.json`. It uses `MSE(p)=E[y²]-2E[py]+E[p²]`; C0 gives `E[y²]≈0.011501769`. `E[p²]` is measured on all 9,379 official rows and used as a proxy for the public 30% composition. Thus inferred `E[py]`, uncentred correlations, and predicted scores are aggregate assumptions, not labels or tract-level measurements. Results: C1 `E[py]=0.009551697`, uncentred correlation `0.5226`, global optimum scale `alpha=0.328813643`, predicted scaled RMSE `0.091438725`; C2 `E[py]=0.004396102`, correlation `0.1786`; C3 `E[py]=-0.016690312`, correlation `-0.5601`. C3's x1.25 denominator shift points the wrong way. P4–P6 below were frozen from this algebra before any phase-2 upload. No per-tract or per-GEOID tuning is allowed.

| Order | Candidate / transform | Global parameters | Predicted public RMSE | Candidate SHA-256 |
|---:|---|---|---:|---|
| 1 | **P4** `alpha * C1` | `alpha=0.328813643` | 0.091438725 | `8b329802d882ea4bdff2bfd8c465c160d6ffd9269cfbe28dce8be9e702b07d37` |
| 2 | **P5** `alpha * region_mean(C2) + beta * (C2 - region_mean(C2))` | `alpha=0.328813643`, range-constrained `beta=-0.049679932` | 0.088921961 | `9d5ad1d05bedfe5da23a39d9a4f6902217c2711da8fa0b408d992f196bb93b4b` |
| 3 | **P6** constant `c` in all score columns | `c=0.056526890` | 0.091139889* | `68851dd2fee88f229bee08466d342f14313fa75e84bf42867baf28995445f6b3` |

P4/P5 forecasts assume public composition represents all official rows. P5's unconstrained beta optimum is −0.218132160; beta is restricted to keep the composite in `[0,1]`, with optional component outputs clipped to `[0,1]`. P6's forecast is assumption-based: it assumes `E[y]≈c`, where `c=E[qy]/E[q]` from C1; its actual score will estimate the public target mean through the score identity. Three probes are registered, one of four phase-2 slots is deliberately unused. These artifacts have 9,794 ordered IDs; 415 eastern-WA rows use documented pooled fills and are not target evidence. The phase-2 outputs and exact transforms are in `reports/candidates.json`. Do not submit until this section, manifest, and artifacts are committed, the shared-tool dry-run passes, and the 10/day check permits the upload. Upload only via the shared orchestrator tool; no local held-out score exists because labels are unavailable.

* P6 numerical forecast under the stated mean assumption; not an empirically identified prediction.

## Offline estimator-v1 candidates (not registered or uploaded)

`scripts/build_estimator_v1_candidates.py --workspace "$COMPETITION_WORKSPACE"` produced two bounded 9,794-row sensitivities for a later explicitly preregistered phase. `reports/estimator-v1-audit.json` records lineage and eastern-WA fills. V1-A shrinks building and POI gaps by the phase-1 C1 scale `alpha=0.328813643`, leaves transport unchanged, and recomputes composites over the v0 defined-component masks (SHA-256 `9649f519765869ece13a42e2a977b1910bf90a751eecaf73bb853977126ee0da`). V1-B reruns the provided-feature estimator with expected-reference denominators scaled to `0.75`, opposite the harmful x1.25 sensitivity (SHA-256 `8abfc98fdcab5c90d463a7f723e49f0b8c633cd713b3ffc9f60fe279c3604623`). These are not scored, accuracy claims, or phase-2 registrations; eastern-WA is pooled template completion only.

## Phase-2 actual vs predicted

| Candidate | Predicted public RMSE | Actual public RMSE | Difference / status |
|---|---:|---:|---|
| P4 | 0.091438725 | 0.091440198 | +0.000001473; successful, ID `ySXi73rd` |
| P5 | 0.088921961 | — | Upload rejected HTTP 400 by platform's stricter 5-submissions/day limit; no submission ID or score |
| P6 | 0.091139889* | — | Not uploaded; held until platform rate limit resets |

P4's actual closely matches the representative-public algebra forecast. The platform returned `You can only submit 5 times a day, please try again in about 14 hours` for P5, despite the official page's 10/day limit. No more uploads are attempted in this window; P6 and a retry of P5 are deferred until the platform permits them. The failure and response are recorded in `reports/submissions.jsonl` and `reports/lb-score-algebra.json`; P5 did not receive a Zindi submission ID or score. This is not a score for P5. The phase-2 budget remains capped at four probes, with P4 as the only scored phase-2 result so far.

* P6 forecast assumes `E[y]≈c`; it is not an empirically identified prediction.

## Phase 3 preregistration — region target means (2026-10-24)

**Purpose:** estimate `E[y|region]` for the four authoritative regions and the global mean from five low-dimensional, already defined score probes. The five probe order is P6, R-AZ, R-CA, R-OK, R-TX. P6 is the existing frozen phase-2 constant (`c=0.05652688955988315`, predicted public RMSE `0.091139889`, SHA-256 `68851dd2fee88f229bee08466d342f14313fa75e84bf42867baf28995445f6b3`). It has not been uploaded. The four new indicators use `c_r = alpha * mean_r(C1 coverage_gap_score)`, with the already frozen P4 `alpha=0.3288136427728255`; prediction values are generated from official sample GEOIDs, not per-tract targets.

| Probe | Region / transform | `c_r` | Primary predicted RMSE (9,379 denominator) | 9,794-row alternative | Candidate SHA-256 | Columns |
|---|---|---:|---:|---:|---|---|
| P6 | Global constant, all template rows | 0.056526890 | 0.091139889 | same constant | `68851dd2fee88f229bee08466d342f14313fa75e84bf42867baf28995445f6b3` | five |
| R-AZ | `maricopa-az`, zero elsewhere | 0.064672498 | 0.104739325 | 0.104846769 | `ff493d94f17fa9977be69113b4c4cca7cd0d207e3d65a8b7f68b9c4ca7f0373d` | **GEOID, coverage_gap_score only** |
| R-CA | `northern-ca`, zero elsewhere | 0.050584362 | 0.106313916 | 0.106353590 | `3e3d171e2c676a3cc7bc462acc67a46f1ca084903e26b3ccb0f5b6faba227189` | five |
| R-OK | `eastern-ok`, zero elsewhere | 0.038583403 | 0.105530049 | 0.105603338 | `49a1b8527caca4054d1b05c1a0bc3383af8b510aa973392f60d6b0552e0c0713` | five |
| R-TX | `south-central-tx`, zero elsewhere | 0.057005299 | 0.097245977 | 0.097690492 | `b7046ce5e169e8a8fe81e8f7cbb2993a9cd6749f074ac137390d9158be701b35` | five |

The four new CSVs have exact 9,794 template order, preserve the 9,379 official-region scope, and set all 415 eastern-WA template-only scores to zero. `scripts/build_region_probes.py` preregisters their hashes and forecasts in `reports/candidates.json` and this algebra report before any live upload. It also writes `reports/region-probe-preregistration.json`. The primary forecasts use the score identity with `E[y²]=0.011501768649197401`, assume `E[y|r]` equals P6's working global-mean prior, and use official 9,379-row region shares. The alternative forecasts use region counts divided by 9,794. Both assume the public 30% has representative composition; neither is a measured target or accuracy claim.

After successful status refreshes, infer `E[y] = (E[y²] + c² - RMSE(P6)²)/(2c)` and, for each region under each denominator scenario, `E[y 1_r] = (E[y²] + c_r² P(r) - RMSE(R-r)²)/(2c_r)` and `E[y|r] = E[y 1_r]/P(r)`. Compare the four region contributions with the P6 global mean: near-zero closure under the official-only denominator supports the 9,379 scope; an appreciable residual under the all-template denominator is evidence of a nonzero eastern-WA contribution, not proof of its exact component values. Report the closure residual and all assumptions in `reports/lb-score-algebra.json`. R-AZ is intentionally the only score-column-only probe; its processing status tests whether optional component columns are needed.

**Upload gate / current disposition:** the shared orchestrator dry-run validated P6 and all four indicators, but reported `submissions_today_before=5` and `daily_cap=5` for each. Five prior submissions have already reached the platform's observed daily limit; P5 was explicitly rejected with the same 5/day message. No live upload was attempted. Keep these preregistered files frozen and defer the five calibration uploads until the limit resets; upload only through `$ORCHESTRATOR_ROOT/scripts/zindi_submit.py`, then poll `--status` for actual public RMSEs. No local held-out score exists because the competition has no labels.
