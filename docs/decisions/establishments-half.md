# Establishments-half sensitivity for estimator v2

**Status at memo date:** this was an isolated, CBP-data-free sensitivity analysis; it did not itself create or modify a v2 candidate or scored file. **Subsequent disposition:** the frozen `cbp_k=0.024` component is used in the assembled and submitted `V2_model_cgs` candidate (submission `hvLtPVmr`), as documented in `docs/writeup/methodology.md`. No leaderboard probe or calibration was performed for this analysis.

## Rule and model block

The official method defines the POI component as an equal-weight mean of (1) the facility half—defined fire, EMS, and school gaps—and (2) all Overture places compared with County Business Patterns (CBP) establishments. CBP is an establishment-statistics program, not a facility-point inventory; the methodology review documents that count/geography distinction in `docs/literature/completeness-prior-art.md` (entry 14). Removed CBP tables or equivalent copies remain prohibited.

The analysis-only v2 block in `scripts/estimator_v2_establishments.py` computes:

```text
establishments_gap = clip(1 - overture_poi_all / (cbp_k * pop_total), 0, 1)
```

Only the provided Overture all-place count, provided `pop_total`, and the explicit scalar `cbp_k` enter the establishments component. Positive population marks the component defined. At zero population, a positive observed POI count yields a clipped gap of zero; if both population and POI count are zero, the component is undefined (numeric placeholder zero, excluded by the flag). The POI gap averages whichever halves are defined, with equal weight when both exist, matching the published organizer method. This audit holds the v0 facility, transport, and building estimates fixed and replaces only the establishments half, so its table isolates the parameter's effect; it is not itself the assembled v2 composite.

### Default and constraint

The CLI `--cbp-k` default is **0.024 for sensitivity analysis only**. This is the rounded task-specified planning anchor `8,000,000 / 330,000,000 ≈ 0.02424`; it was not independently verified from a CBP file, and no CBP table or external numeric file was read. The analysis always reports `k ∈ {0.015, 0.024, 0.04}` and includes a user-supplied `--cbp-k` in the table if it is outside that set.

The operator amendment prohibits CBP or external numeric data in scored files and requires any scored establishments prior to be derived from challenge-provided features plus the organizer method/public methodological research. This report documents the planning ratio as a sensitivity assumption; the separately frozen model specification later set `cbp_k=0.024` using that allowed rationale and uses only provided Overture counts and population in the scored component. The report itself uses provided features and existing model estimates and produces no candidate predictions.

## Sensitivity results

The reproducible aggregate report is `reports/establishments-sensitivity.json`. It uses all 9,379 official sample GEOIDs, `overture_poi_all`, `pop_total`, and `reports/gap-estimates.csv`; the sample files are read for GEOIDs only. No sample score values, labels, removed references, external data, or CBP data are used. It writes no per-GEOID output.

The v0 CBP component is defined for 9,377 tracts, with mean gap **0.04922** and **85.87%** zero among defined rows (the MEASURE-BEFORE was 0.049 and 85.9%). Baseline v0 composite mean is **0.16898** and `E[p²]` is **0.052684**, where `E[p²]` is the unweighted mean of squared per-tract composite predictions. For each k, the table reports the new component mean and positive-gap share, then the recomputed composite moments and shifts from that v0 baseline:

| k | Establishments mean (defined) | Gap > 0 / all tracts | Composite mean | Mean shift | E[p²] | E[p²] shift |
|---:|---:|---:|---:|---:|---:|---:|
| 0.015 | 0.05277 | 15.03% (1,410 / 9,379) | 0.16965 | +0.00068 | 0.053073 | +0.000389 |
| **0.024 (analysis default)** | **0.11731** | **30.45% (2,856 / 9,379)** | **0.18214** | **+0.01316** | **0.060006** | **+0.007322** |
| 0.040 | 0.24208 | 54.01% (5,066 / 9,379) | 0.20610 | +0.03712 | 0.071957 | +0.019273 |

The default creates positive modelled establishment deficits in about 30% of tracts and leaves the term zero in about 69.5% of defined tracts. Higher k raises both composite moments monotonically in this feature-only sensitivity. These are changes in unvalidated model predictions, not changes in measured hidden scores, accuracy, or leaderboard performance. The report also includes region-specific moments and positive-gap shares, baseline-versus-new component statistics, input hashes, and the zero-population counts. It uses provided feature values, existing v0 model outputs, and only the task-specified k values; no external numeric file is read. In this sample, 9,377 tracts have a defined establishments term; 24 have zero population but positive POI counts and receive zero gap; two have zero population and zero POIs and are undefined.

## Reproduction and validation

```sh
/home/martin/hddProjects/langywrap/uv run \
  --with pyarrow==26.0.0 --with numpy==2.5.4 \
  python scripts/audit_establishments_sensitivity.py \
  --workspace "$COMPETITION_WORKSPACE" \
  --cbp-k 0.024 \
  --out reports/establishments-sensitivity.json
```

The script checks exact ordered feature-cache/official-ID coverage, joins only `GEOID` and `pop_total` from the provided strata, and verifies that v0 and sensitivity composites remain paired on the same defined GEOIDs. Tests cover the clipped formula, invalid inputs, zero-population behavior, equal-weight POI composition, and v0 composite replacement. The script is an analysis tool only; it produces an aggregate report, not candidate predictions.
