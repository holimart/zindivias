# Final-two private-leaderboard selection protocol

**Frozen:** 2026-10-24 UTC, before any final-cohort candidate upload or private-score release. **Status:** protocol committed; candidate slots are intentionally pending. This replaces the historical C0-anchor recommendation and any selection based on region/cell information probes. The original document did not authorize uploads; see the dated steward amendment immediately below for the current schedule.

## Steward schedule amendment — 2026-10-28 06:25Z

The steward's latest P0 revision authorizes the already frozen column-corrected cohort now and raises the Bias cap to five/day **for this schedule only**. On 2026-10-28 upload `V2_model_cgs`, `A1_physical_equal_cgs`, then `A2_spectral_cgs` (three slots remain after the two earlier uploads). On 2026-10-29 upload `A3_dawid_skene_cgs`, apply the frozen A rule to the four `_cgs` scores only, then upload `B_from_A_cgs` if V2 wins; otherwise rebuild B from the winner, register it, and upload it. No other candidates, score-based value changes, or historical-score selection are authorized. This amendment supersedes the earlier timing/cap only; all model-selection and no-probe constraints below remain. Machine-readable authorization: `config/bias-cgs-upload-authorization-2026-10-28.json`.

## Steward authorization amendment — 2026-10-28 12:20Z

The steward relayed this operator authorization verbatim: **“bias yes let the lane upload as you write”** (operator, approximately 2026-10-28 12:20Z). This supersedes the 2026-10-29 timing in the 06:25Z schedule above and the prior machine-readable authorization. Three corrected-order `_cgs` candidates (V2, A1, A2) were already uploaded successfully on 2026-10-28; two of the five authorized daily slots remain. The remaining sequence for 2026-10-28 is: upload the already preregistered `A3_dawid_skene_cgs`; apply the frozen A rule using only successful corrected-order `_cgs` scores; then upload B (`B_from_A_cgs` if V2 wins, otherwise rebuild B from the winner and preregister it before upload). Artifact values/hashes and the A-selection constraints are unchanged. No other candidates, probes, or score-driven value edits are authorized. Machine-readable authorization: `config/bias-cgs-upload-authorization-2026-10-28.json` (revision `2026-10-28T12:20:00Z`).

## Scope and governing constraints

Zindi requires two submissions to be selected for the private leaderboard. The official scored sample has 9,379 tracts; the public split is approximately 30% (about 2,814 tracts) and the private split approximately 70% (about 6,565). The UI selection is due by **2026-10-31 20:00 UTC**. Both final submissions must be successfully uploaded and scored by **2026-10-30 12:00 UTC**, leaving 32 hours before the selection deadline.

The operator amendment controls over the earlier idea-mining text:

- No phase-3, regional-mean, cell, subset, constant, composition, or other information probes.
- Consider only finished model candidates with passing invariants and a mechanism-based hypothesis committed before their upload. Use only challenge-provided data in scored predictions; no labels, removed reference layers, equivalent copies, per-tract fitting, or leaderboard-derived coefficients.
- Evaluate public RMSE only as the platform evaluation of an already-finished candidate. Do not use scorecard cells or public results to fit, tune, or revise model parameters.
- Upload only through `$ORCHESTRATOR_ROOT/scripts/zindi_submit.py`, with operator authorization and all manifest/hash gates passed. The local competition helper's historical five-per-day limit is not permission to use those slots; the effective lane limit is **at most two submissions per UTC day**. Keep two total remaining submission opportunities reserved for the final two.

Historical C0 (public RMSE 0.107246299) is a baseline, not the anchor for final private selection. P4 (0.091440198) is a historical benchmark only: its `alpha_c1` was fit from leaderboard feedback and is forbidden in any new candidate. C0–P6 and the retired R-* probes are not grandfathered into this protocol's final cohort: their results predate this frozen eligibility rule, and probes/calibrated candidates are ineligible. A historic score is not authorization to upload or select a candidate.

There are no training labels or local target holdout. The earlier proposed requirement that a candidate's predicted-versus-actual error be below 1e-3 is therefore not measurable without target feedback and would invite prohibited probes; it is superseded by artifact correctness/reproducibility checks, not represented as an accuracy test.

## Freeze the candidate cohort before each upload

Before an eligible model candidate is uploaded, freeze its card in a Git commit and register it in `reports/final-two.json` and the candidate manifest. Record candidate ID, mechanism-based hypothesis, component formulas and parameter provenance, artifact path and SHA-256, code commit, environment, one-command reproduction, official-ID/template scope, and all local invariant results. Record the preregistration commit and SHA-256 of the canonical card; these frozen fields must predate the platform score. Add successful platform results append-only, without changing the frozen card. A candidate is eligible only if:

1. It is a completed model, not a probe, baseline, post-hoc transform, or unvalidated sensitivity.
2. Its hypothesis and parameters come from organizer methodology, public research, or challenge-provided features; it has no LB-derived coefficient or target-moment fit.
3. It passes exact ID/order/schema, no-duplicate/no-missing, finite `[0,1]`, definedness, deterministic rebuild and hash checks. The platform template's 415 eastern-WA format-only rows must use the already documented deterministic fill; they are not model evidence.
4. It is uploaded through the orchestrator and has a successful, non-placeholder public RMSE and submission ID. Failed/initial uploads are not scores.

A failure of any gate means the candidate is not eligible, irrespective of its displayed score. No local target accuracy claim is implied by passing the invariants.

## Deterministic choice of A and B

**Candidate A — public-score leader, noise-aware.** Rank only eligible, frozen-cohort model candidates by successful public RMSE (lower is better). Treat scores within the noise margin below as tied rather than selecting on a small difference. Among the tied minimum-score set, choose in this order: (1) fewer fitted/free parameters, (2) fewer region-/stratum-specific adjustments, (3) lexicographically smaller frozen candidate ID. No post-score parameter change is allowed. If one candidate is lower than all other eligible candidates by more than the margin, it is A. If the cohort has no eligible candidate, do not substitute C0/P4 or a probe; ask the operator for a decision.

**Candidate B — fixed robust companion.** Use the same A artifact and form a deterministic 50% shrinkage of its composite predictions toward A's own **model-predicted** official-tract mean within each region:

`B_i = 0.5 * A_i + 0.5 * mean(A_j for official tracts j in i's region)`.

These regional means are calculated from A's provided-feature predictions only—not from inferred/observed target means, LB scores, scorecard cells, or probe results. Clip only to the required `[0,1]` range and record whether clipping occurred. Keep GEOIDs/order unchanged; fill non-scored template-only rows with the established deterministic format fill. B must be rebuilt, hashed, and pass the same invariants before upload. Its rationale is lower within-region prediction variance as a predeclared robustness alternative, not a claim that it will score better. If A or B cannot pass the gates and be scored by the deadline, do not manufacture a substitute or upload late.

The protocol, including B's shrinkage factor, is fixed before candidate results. `reports/final-two.json` is a pending record now; fill A/B IDs, hashes, successful public RMSEs, submission IDs, and candidate-specific rationale from the actual platform status on October 30. Never fill a score from an initial placeholder.

## Public/private sampling-noise guard

With public fraction `f=0.30` and total scored count `N=9,379`, expected counts are `n_public=0.30N=2,813.7` (about 2,814) and `n_private=0.70N=6,565.3` (about 6,565). For a predeclared cell containing `n_cell` scored tracts, the requested sampling-scale formula for a cell mean loss is

`SE_cell(MSE) ≈ sd_cell / sqrt(0.30 * n_cell)`,

where `sd_cell` is the standard deviation of per-tract squared-error contributions in that cell. If converting a cell MSE to RMSE, use the delta-method scale `SE_cell(RMSE) ≈ SE_cell(MSE) / (2 * RMSE_cell)`. These quantities cannot be measured locally: no target labels exist, and region/cell leaderboard outcomes are prohibited. Do not invent `sd_cell` or use scorecard cell results.

For a transparent planning estimate only, assume independent approximately normal per-tract residuals, giving `SE_public(RMSE) ≈ RMSE / sqrt(2*n_public)` and `SE_private(RMSE) ≈ RMSE / sqrt(2*n_private)`. At the historical P4 RMSE scale of 0.091440198 (used only as a noise-scale example, never as a model coefficient), these are about 0.001219 and 0.000798; the corresponding public-vs-private score-shift scale is `sqrt(SE_public^2 + SE_private^2) ≈ 0.001457`.

For comparing two candidates A and C on the same public split, use the conservative pairwise guard `tau(A,C) = SE_public(A) + SE_public(C)` (about 0.002438 when both are at the P4 scale). If their public RMSE difference is at or below this guard, regard them as tied and apply the fixed simplicity/ID tie-break above. **Never select a candidate because of a single public-score difference smaller than this estimated sampling scale.** This is an uncertainty guard, not an LB-derived model parameter or a reason to make another submission. The normal-residual approximation is heuristic; the cell formula states the desired design-based scale, but its `sd_cell` remains unavailable without labels.

## UTC upload budget and dates

The steward-observed platform limit is five uploads/day; the operator's two/day limit is stricter. From October 25 through the October 30 noon cutoff, the platform ceiling is about 30 slots, but this lane protocol allows at most 12 in that six-day window, reserves two total for A/B, and does not authorize any upload by itself. Actual remaining total submissions must be checked by the operator through the approved gate; spend fewer if the account limit is lower.

| UTC date | Observed platform ceiling | Lane ceiling | Protocol budget |
|---|---:|---:|---|
| Oct 25 | 5 | 2 | Up to 2 finished-model evaluations only if independently authorized; no probes |
| Oct 26 | 5 | 2 | Up to 2 finished-model evaluations only if independently authorized; no probes |
| Oct 27 | 5 | 2 | Up to 2 finished-model evaluations only if independently authorized; no probes |
| Oct 28 | 5 | 2 | Up to 2 finished-model evaluations only if independently authorized; no probes |
| Oct 29 | 5 | 2 | Up to 2 finished-model evaluations only if independently authorized; no probes |
| Oct 30, by 12:00 | 5 | 2 | The two reserved final A/B uploads; no upload after noon absent a documented emergency |
| Oct 31 | 5 | 0 | UI selection and writeup only; no uploads |

The five pre-final days have a theoretical ceiling of ten lane submissions, not an instruction to use them. As of this protocol freeze, it queues **zero** uploads. Unused evaluation capacity remains unused; the two reserved final opportunities are not borrowed for probes or exploratory candidates.

## Operator checklist

- [ ] Confirm an eligible A model card and candidate artifact passed all local gates; verify no unauthorized data or LB-derived coefficient.
- [ ] Confirm B was generated with the frozen 50% model-prediction shrinkage and independently passed the same checks.
- [ ] Confirm account capacity and the two/day orchestrator gate; upload A and B only through the authorized orchestrator path by 2026-10-30 12:00 UTC.
- [ ] After each successful processing result, record its submission ID and actual public RMSE from the approved status source; reject initial placeholders and failed uploads.
- [ ] Complete `reports/final-two.json` with A/B candidate IDs, artifact hashes, scores, IDs, and mechanism-based rationale; keep the public/private-noise note and tie-break audit.
- [ ] In the Zindi UI, select exactly A and B for private scoring by 2026-10-31 20:00 UTC; record confirmation/screenshot reference or operator note in the report. Do not rely on the platform's implicit two-best-public fallback.
- [ ] Submit the reviewed methodology through the organiser writeup form before challenge close, include the selected submission IDs/team name, and request the relevant documentation/discovery prize consideration as directed.

If two eligible, scored submissions do not exist by the upload cutoff, do not violate the deadline or eligibility gates. Notify the operator and request an explicit fallback decision. Operator acknowledgement of this frozen protocol is requested in the lane closeout; no acknowledgement is assumed here.
