# Model-first submission policy

**Effective:** operator directive, 2026-10-24. This policy supersedes the
information-probe plans and all earlier probe-upload permissions. Competition
rules in `$COMPETITION_WORKSPACE/RULES.md` remain authoritative; the lane may
use only challenge-provided datasets in scored predictions. There are no
training labels, and removed reference layers or equivalent copies must never
be used to reconstruct the target.

## 1. No leaderboard information probes

Do not upload a candidate whose purpose is to infer target moments, public
composition, regional/cell means, a calibration constant, or any other hidden
score property. Cancelled registrations are not upload candidates. In
particular, do not upload:

- P5, P6, or any constant/transform probe; P5's rejected attempt remains only
  in the existing score/submission history.
- R-AZ, R-CA, R-OK, R-TX, or any other region-mean indicator.
- Region x cell, subset, facility-cell, composition, monotone-transform, or
  post-hoc region-blend probes (including region-means plus detail).

The former plan is retained verbatim as
[`lb-probe-plan-archive-2026-10-24.md`](lb-probe-plan-archive-2026-10-24.md)
for provenance only. Its preregistration tables, candidate files, and score
algebra are historical records, not an active upload queue. `P5`/`P6` and the
four R-* entries are disabled in `reports/candidates.json`; do not re-enable
them. Existing scored history (C0-C3, P4, actual submission IDs/scores) is
preserved unchanged. See `reports/probe-retirement-2026-10-24.json` for the
retirement record.

## 2. Only evaluate finished models

An upload may evaluate only a finished, reproducible model candidate built
from provided challenge features and documented organizer methodology or
public research. Published research may inform the method; additional datasets
or external target proxies may not be used in the scored CSV. No removed TIGER,
Microsoft footprint, USGS facility, or CBP data (including equivalent copies)
may enter a scored prediction. No per-GEOID tuning, score-derived fitting,
leaderboard-selected thresholds, or tuning on public scores is allowed.

Before an operator-authorized model evaluation, commit a candidate card/report
that states:

1. the candidate artifact, SHA-256, code commit, dependency/environment pins,
   and one-command reproduction;
2. a written, mechanism-based hypothesis for why this model should beat the
   current best successfully scored candidate, P4 (public RMSE `0.091440198`);
3. the model parameters and their provided-data/literature provenance, with no
   leaderboard-derived fit or target-moment estimate;
4. local invariant results: exact required IDs/order and schema, no missing or
   duplicate rows, finite `[0,1]` scores, defined-component handling, and a
   deterministic rerun/hash.

There is no labeled local holdout, so do not claim a local accuracy score or
invent a holdout. Local invariants establish correctness and reproducibility,
not predictive accuracy. A candidate that fails any invariant is not uploadable.

The operator-authorized ceiling is **two submissions per UTC day**, stricter
than the official platform's ten/day. The shared
`$ORCHESTRATOR_ROOT/scripts/zindi_submit.py` enforces the two/day gate, manifest
registration, artifact hash, and CSV checks. Never upload directly to Zindi;
never submit from this policy turn. At present there are no information-probe
uploads queued. Reserve two remaining total submission opportunities for the
final two model candidates; any earlier model evaluations consume the same
operator-authorized two/day budget.

## 3. Historical calibration is not a model parameter

P4's historical global scale `alpha_c1 = 0.3288136427728255` was derived from
leaderboard feedback and produced the recorded P4 score. Keep that value and
its score provenance in `config/calibration.json` solely to reproduce the
already-scored P4 artifact. Do not transfer it, refit it, or use it as a prior
in v2 or any new candidate. Do not use region means inferred from the LB.

Estimator v2 must derive each component's level from its mechanism and
challenge-provided inputs: e.g. route-reference features for transport,
facility-miss probabilities from the provided Overture/tract features, and a
building/establishments level justified by the organizer definition and
published methodology. State every fixed prior, formula, and sensitivity in
the model audit and methodology writeup. Any aggregation by region must be
computed from v2's model predictions/features, not from public scores. V2
removes the 0.33 multiplier and is not a disguised re-calibration of P4.

## 4. Work order

The open backlog is ranked so estimator/model construction comes before
writeup and discovery. The P0 core work is: `bias-est-transport-route-ref`
(route-reference transport); `bias-est-poi-category-audit` (challenge-data
category definitions); `bias-est-facility-miss-model`;
`bias-est-building-near-zero-memo`; `bias-est-establishments-half`;
`bias-est-tx-fm-roads-sensitivity`; and `bias-edge-case-water-zero-pop`.
All component levels are model-derived, not LB-calibrated.

P1 then contains the model-only v2 assembly/evaluation row and optional
provided-feature model sensitivity candidates. `bias-est-v2-assembly-probe` is
renamed/re-scoped in the backlog as a v2 model-candidate build/evaluation row;
it depends on the component work above, and means at most one finished-model
evaluation, never an information probe. The final-two protocol follows model
completion and applies only to finished model candidates.

P2 writeup/discovery follows: methodology rubric, source/strata discoveries,
prior art, scorecard reflection, and the final operator-reviewed writeup.
Optional estimator sensitivity audits remain below the core model rows. Rows
that require region means, cells, composition, or LB-derived blend coefficients
are retired or rewritten before selection. The active final-two rules are in
[`final-two-protocol.md`](final-two-protocol.md); they observe the official
requirement to select two submissions and do not create probe slots.

## 5. Status and references

No live submission was made while adopting this policy. Check this document
before any future candidate registration or upload. Historical probe
registrations and scores must not be interpreted as authorization to upload.
For background only, see the archived former plan and
`reports/lb-score-algebra.json`; neither is an active experimental schedule.
