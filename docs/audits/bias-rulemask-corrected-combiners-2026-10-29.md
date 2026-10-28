# Rule-grounded V2 recombination — pre-upload audit

## Preregistration and tolerance

The recipes and gates were committed before the new candidate outputs were built:
`d0495aa` pre-registers the candidates and selection rule; `11aac97` corrects a
transcription error in the tolerance arithmetic, before the candidate builder
produced any output. The final tolerance is the **mean absolute regional
share disagreement between the two independent existing hard-mask systems,
V2 and C2**, averaged over the four regions: `13.191740616025816` percentage
points. It is one scalar used for every region and both candidates. This is a
predictor-disagreement tolerance, not a rounding interval. The earlier A1
experiment is explicitly excluded from its derivation and remains ineligible.

The predeclared candidate-B selector compares each existing mask family with
the four published shares by mean absolute regional error, choosing the lower
error (exact ties choose V2). Existing V2 masks score 4.3796 pp MAE; C2 masks
score 13.4111 pp, so the selected mask is V2. The preregistration, including
source hashes, zero-defined rule, and no-label forecast caveat, is
`config/bias-rulemask-corrected-combiners-preregistered-2026-10-29.json`.

## Candidate results

Both candidates retain V2's frozen transport/building/POI component values and
recompute only the composite as the arithmetic mean of defined top-level
components. No regional level adjustment is applied. The predeclared fallback
for a zero-defined row is that candidate's regional median among defined rows.

| Candidate mask | Maricopa undefined | Northern CA | Eastern OK | South-Central TX | Zero-defined rows | Result |
|---|---:|---:|---:|---:|---:|---|
| Provided-object presence (named-road km, building count, facility/establishment presence) | 2.07% | 1.18% | 2.35% | 1.07% | 2 | Fails the common tolerance in all regions |
| Existing-mask selector (V2 selected over C2) | 56.56% | 30.80% | 23.99% | 21.24% | 0 | Passes share tolerance, but is value-identical to already-scored `V2_model_cgs` |

The first candidate's diagnostic artifact uses the registered regional medians
for its two zero-defined rows, but its mask shares are too far from the
published rates to pass. The second output's SHA-256 is exactly the existing
V2 corrected-order artifact SHA (`5d0607d5531793008d8d5c86b1625f26e5bb8e9504a2b4a4a84f0224a0f5cef7`),
so it is not a distinct candidate and must not be uploaded again.

## Forecast, validation, and decision

No permitted target labels or holdout exist, so neither candidate has a numeric
RMSE forecast; this limitation is recorded in the pre-registration and both
manifests. The builder read only V2 component columns (not its stored composite),
GEOIDs, provided-data component features, and pre-existing definedness flags. It
did not read sample score placeholders, labels, removed references, public
scores, or credentials. No component value changed and no upload was attempted.

- `python3 -I scripts/build_bias_rulemask_candidates.py --workspace "$COMPETITION_WORKSPACE"`
  run twice; identical output hashes each time.
- `PYTHONPATH=$PWD/src python3 -m pytest -q tests/test_rules_composite.py` — **4 passed**.
- Candidate A SHA-256: `f609f671b1f7bff2f6ba19c92798119c616b9de3c6d2417870118c85cc15d41e`.
- Candidate B SHA-256: `5d0607d5531793008d8d5c86b1625f26e5bb8e9504a2b4a4a84f0224a0f5cef7` (duplicate V2 CGS).
- Full per-region masks, fallback medians, score distributions, source hashes,
  and gate results: `reports/bias-rulemask-corrected-combiners-audit-2026-10-29.json`.

**Decision: do not spend the 2026-10-29 spare slots on these outputs.** Candidate A
fails the predeclared mask check; candidate B is already scored and unchanged.
The provided object-presence mask is not a reliable predictor of whether the
organizer had a reference object. No further threshold selection or public-score
tuning is justified. Continue the authorized A3/B schedule independently; ask
for a new steward decision before attempting another mask family or any upload.
