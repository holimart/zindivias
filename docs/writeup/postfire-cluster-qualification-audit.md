# County-cluster uncertainty must be visible wherever the high-burn versus unburned difference is claimed

- Audit: `postfire-writeup-cluster-qualification` (backlog item `bias-postfire-writeup-cluster-qualification`)
- Preregistered configuration: `config/postfire-writeup-cluster-qualification-preregistered.json` (SHA-256 `d4903f79410199da...`)
- Verdict: **PASS** — every inferential statement about the high-burn versus unburned difference carries the county-cluster result, and every quoted number agrees with the frozen reports

## Verdict legend

| Gate | Result |
|---|---|
| `documents_located` | pass |
| `unqualified_inferential_statements_zero` | pass |
| `classifier_is_not_vacuous` | pass |
| `frozen_interval_literals_present` | pass |
| `no_unfrozen_interval_literal` | pass |
| `near_finding_fragments_present` | pass |
| `exploratory_verdict_near_finding` | pass |
| `first_mention_carries_cluster_interval_and_verdict` | pass |
| `tract_and_cluster_designs_distinguished` | pass |
| `frozen_statistics_agree` | pass |
| `population_literals_present` | pass |
| `preregistered_design_not_drifted` | pass |
| `frozen_report_hashes_match_pin` | pass |
| `generated_writeup_matches_source_block` | pass |
| `no_causal_or_hidden_target_claim` | pass |
| `required_hedges_present` | pass |
| `only_allowlisted_files_opened` | pass |
| `no_forbidden_path_opened` | pass |
| `all_passed` | pass |
| `no_forbidden_value_in_report` | pass |
| `report_is_strict_json` | pass |
| `report_has_no_temporal_field` | pass |

## Measured qualification per document

| Document | Statements examined | Inferential statements | Unqualified |
|---|---:|---:|---:|
| `docs/writeup/bias-discovery.md` | 40 | 6 | 0 |
| `docs/writeup/final-writeup.md` | 40 | 6 | 0 |

## Frozen values the prose must agree with

- `point_difference_percentage_points` = **+9.55**
- `tract_bootstrap_interval_percentage_points` = **[+0.55, +20.89]**
- `county_cluster_interval_percentage_points` = **[-1.65, +16.67]**
- `positive_difference_fraction_percent` = **95.99%**
- `leave_one_county_out_minimum_percentage_points` = **+5.56**
- `leave_one_county_out_maximum_percentage_points` = **+10.71**

## Design integrity

- cutoff used: 0.8 (permitted: [0.8])
- cluster design: 50000 replicates, seed 20261025, 23 counties
- tract reference: 50000 replicates, seed 20261024
- preregistered verdict: `exploratory`, quantitative claim retained: False

## The sentence that distinguishes the two resampling designs

- `docs/writeup/bias-discovery.md`: The two intervals answer different questions and are not interchangeable: the tract interval measures sensitivity to tract-level resampling of the binary ratio indicator at a fixed county composition, and because tracts inside one county share Overture provider mix, terrain and reference vintage it understates county-level dependence, so it is not the interval to quote when counties rather than tracts are the independent units; it also captures none of the spatial dependence, measurement error, source-vintage uncertainty, or hidden-target error.
- `docs/writeup/final-writeup.md`: The two intervals answer different questions and are not interchangeable: the tract interval measures sensitivity to tract-level resampling of the binary ratio indicator at a fixed county composition, and because tracts inside one county share Overture provider mix, terrain and reference vintage it understates county-level dependence, so it is not the interval to quote when counties rather than tracts are the independent units; it also captures none of the spatial dependence, measurement error, source-vintage uncertainty, or hidden-target error.

## First mention of the difference

- `docs/writeup/bias-discovery.md`: The high-burn minus unburned difference is **+9.55 percentage points**, and its 95% percentile interval is **[+0.55, +20.89] percentage points** under the published stratified 50,000-draw tract bootstrap but **[-1.65, +16.67] percentage points** when whole counties are resampled instead of tracts; under the criterion preregistered before any result was seen, the difference is therefore **exploratory** and is not established at 95% confidence.
- `docs/writeup/final-writeup.md`: The high-burn minus unburned difference is **+9.55 percentage points**, and its 95% percentile interval is **[+0.55, +20.89] percentage points** under the published stratified 50,000-draw tract bootstrap but **[-1.65, +16.67] percentage points** when whole counties are resampled instead of tracts; under the criterion preregistered before any result was seen, the difference is therefore **exploratory** and is not established at 95% confidence.

