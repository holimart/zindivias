# Superseded: leaderboard probe plan

This path is retained as a compatibility pointer for historical lane-log links.
The former plan is archived verbatim at
[`lb-probe-plan-archive-2026-10-24.md`](lb-probe-plan-archive-2026-10-24.md).
Its information probes and upload schedule are cancelled; historical artifacts
and scores are not permission to submit.

The active policy is [`submission-policy.md`](submission-policy.md): only
finished, locally validated model candidates may be evaluated, with no
leaderboard information probing and no new LB-derived constants. Final private
selection is governed by [`final-two-protocol.md`](final-two-protocol.md), which
supersedes the archived C0-anchor recommendation and excludes probe-derived
candidate choices.

## Phase 4 — model-only v2 finished-model evaluation (no probe)

Added 2026-10-25 under the operator model-first directive. There is no phase-4
*probe*: the information-probe design above is cancelled and this section
describes a single pre-registered evaluation of a finished model candidate.

- **Candidate.** `V2_model`, artifact `reports/candidates/V2_model.csv`
  (9,794 rows = the 9,379 official scored GEOIDs in authoritative order plus the 415
  eastern-WA template-only rows filled with the documented deterministic `0.0`
  from `b750a35`). Built by `scripts/build_estimator_v2_candidates.py`;
  card and forecast live in `reports/candidates.json`
  (`model_only_v2_preregistration`) and `reports/estimator-v2-audit.json`.
- **Model.** Equal-weight mean of the model-defined components only: transport
  route-reference gap, the fixed `0.01` building residual prior, and the POI
  component (facility halves with the fixed presence priors plus the explicit
  `cbp_k = 0.024` establishments half). Undefined components are excluded, never
  zero-filled. Levels, weights and priors come from the closed component work and
  the organiser's published method.
- **Forbidden constants, explicitly absent.** `alpha_c1 = 0.3288136428`, the P5
  betas (`-0.0496799320`, unconstrained `-0.2181321596`), the P6 global constant
  `0.0565268896`, probe region means, and any `E[qy]/E[q]` target moment. The
  builder scans every emitted score, component value and region mean for those
  values and fails the build if any appears (`anti_probe_checks`).
- **Pre-registered forecast, written before any upload.** Point prediction
  `0.091357196` public RMSE, from the candidate's own measured moments
  (mean `0.060500636`, sd `0.061356742`, detail sd about region means
  `0.060564487`) plus the single target-side anchor `E[y^2] = 0.107246299^2`
  obtained from the platform score of the already-scored all-zero candidate C0
  (predicting zero makes RMSE^2 = E[y^2]). Assumed centred correlation
  `0.30` between the composite and the target; the audit carries the full
  sensitivity grid over assumed target means and
  `rho_c in {0, 0.1786, 0.25, 0.30, 0.35, 0.5}`.
- **Decision rule fixed in advance.** Breakeven centred correlations at the
  model's own level: `0.0096` to tie the all-zero baseline, `0.298604` to beat
  P4 `0.091440198`, and negative against the per-region-constant candidate C1
  (`0.146449112`), i.e. v2 should beat the region-constant candidate at any
  non-negative correlation. With the protocol's pairwise public sampling guard
  the candidate must land at or below `0.088944` to be called better than P4.
  If the actual public RMSE is above `0.107246299`, the v2 tract detail is not
  informative at its current level: report that and stop adding tract detail.
- **Upload.** At most one evaluation, comment `model-only v2`, only through
  `$ORCHESTRATOR_ROOT/scripts/zindi_submit.py` after operator authorization;
  `docs/final-two-protocol.md` queues zero uploads, and none has been performed.
  A second upload to adjust weights is forbidden.
- **Local self-consistency.** `reports/v2-defined-mask-audit.json` (composite
  arithmetic residual `1.110e-16`, zero undefined-placeholder leaks, zero
  defined-flag mismatches) and `reports/v2-prior-envelope.json` (all 31
  preregistered prior perturbations finite and bounded, frozen masks preserved).
  These are correctness invariants, not accuracy claims: Bias Bounty ships no
  labels, so no local RMSE exists.
