# RULES combiner audit — 2026-10-28

## Scope and decision

This is a method audit, not a leaderboard optimization. The corrected-order scores
(V2 `0.11102814`, A1 `0.309628459`, A2 `0.262578881`) are recorded only as the
steward's diagnostic signal that the composed outputs merit an audit. They were
not used to choose components, weights, masks, thresholds, or values. No historic
misordered score was used. No upload was attempted.

The challenge recipe requires bounded [0,1] deficit gaps; undefined components
must be omitted from means; POI is the equal mean of its defined facility and
establishment halves; and the score is the arithmetic mean of defined top-level
transport, building, and POI components. A score-level regional offset is not a
substitute for those component operations.

## Findings and correction

1. **A1 level anchoring is not the challenge combiner.** The frozen A1 builder
   replaced its component composite with a region-wise additive Sca5/Dst5 level
   offset. Its output therefore does not, in general, equal the mean of its
   defined component gaps. The diagnostic candidate
   `reports/candidates/A1_rules_composite_20261028.csv` retains the three frozen
   component columns and recomposes only by the defined-component mean. The
   component values are unchanged; the maximum difference from the old A1
   composite is reported by region in the JSON audit.
2. **The A1 component masks do not reproduce published undefined shares.** The
   per-GEOID Track-A mask audit gives hard `any top-level component undefined`
   shares of 1.4438% (Maricopa), 0.8460% (Northern CA), 1.4262% (Eastern OK),
   and 0.6330% (South-Central TX), against published 55%, 37%, 21%, and 28%.
   There are also two A1 rows with no defined top-level component. Those rows
   cannot be assigned a score under the recipe.
3. **Other available masks also fail the preregistered share check.** V2 hard
   mask shares are 56.5599%, 30.7953%, 23.9933%, and 21.2394%; C2 shares are
   36.5348%, 30.4569%, 40.6879%, and 36.9482%, in Maricopa, Northern CA,
   Eastern OK, and South-Central TX order. The acceptance tolerance was fixed
   before measurement at ±0.5 percentage points (the rounding interval for the
   published whole-percent figures); neither set passes every region. A Dst5
   probability mean calibrated to the published shares is not an independent
   validation of any hard component mask and was not substituted for one.
4. **Deficit direction is covered by recipe-level toy tests, but not proved for
   every upstream estimator.** The frozen A1 artifact contains gap columns, not
   raw observed/reference pairs for every predictor. The audit therefore does
   not overclaim that every upstream A1 signal can be rederived as a literal
   `max(0, 1 - observed/expected)` deficit. A small pure helper and toy tests
   enforce zero for surplus, bounded outputs, undefined omission, the POI
   two-half rule, and the top-level divisor rule.

## Validation and disposition

- `PYTHONPATH=$PWD/src python3 -m pytest -q tests/test_rules_composite.py` — **4 passed**.
- `python3 -I scripts/build_rules_corrected_combiner.py --workspace "$COMPETITION_WORKSPACE"`
  rerun twice with identical candidate SHA-256:
  `04d05d47565b63fb1b3a3f47669bfa1b6a89aff54c9da59c54bafff06503223a`.
- Candidate output: 9,794 template-order rows (9,379 official plus 415
  formatting-only rows), `GEOID,coverage_gap_score`; source component values
  unchanged; diagnostic zeros are written for the two zero-defined rows only
  to preserve file shape, not as valid predictions.
- **Upload eligibility: false.** Hard undefined-share and zero-defined-row gates
  fail. The artifact is diagnostic only and is not added to the final-two cohort.
  Do not upload it or use its public score as a selection signal. No corrected
  candidate from this audit is eligible for the 2026-10-29 spare slots.
- The local prediction means/quantiles and all per-region comparisons are in
  `reports/bias-rules-corrected-combiner-audit.json`; the hash-pinned source
  description and pre-build rule contract are in
  `config/bias-rules-corrected-combiner-preregistered.json`.

## Follow-up

Repair component-level definedness against the published regional shares using
only a predeclared, rule-grounded prediction mechanism. Do not force-fit a hard
mask to the four aggregate percentages, and do not use public scores to calibrate
it. Re-open upload eligibility only after each-region mask checks pass, no
zero-defined rows remain, the candidate forecast status is documented, and the
artifact is independently rerun.
