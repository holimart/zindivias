# Staged external-lab build: reference-derived coverage-gap labels for lab regions, outside the four study regions

Staged build for `bias60-external-lab-build`. Config `04f327423b21397b…`, spec `f150649b34caebef…`, recipe variant `road-obs4cls-mtfcc-S1100S1200-5070-v1`.

## Retrieval

- 409 files, 4,406,070,003 bytes transferred (14,506,391,085 bytes of objects listed) from overturemaps-us-west-2.s3.us-west-2.amazonaws.com, www2.census.gov; no credentials; every URL matched a preregistered pattern
- Overture 2026-09-23.1: 768 objects listed, 23 read, about 3,861,256,473 compressed bytes read (bbox row-group pruning; the theme is never streamed whole)

## Measurement

| group | candidates | accepted | segments clipped | reference lines clipped | road defined | road ratio | nearest study km |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NV | 674 | 674 | 37893 | 2291 | 433/674 | 1.0134 | 32.637 |
| NM | 612 | 610 | 42816 | 5402 | 506/610 | 0.6546 | 34.971 |
| CO-UT | 2163 | 2163 | 109277 | 13087 | 1741/2163 | 0.8019 | 155.338 |
| LA | 1388 | 1388 | 91343 | 17799 | 1259/1388 | 0.5072 | 67.598 |
| AR-KS | 1652 | 1635 | 114150 | 18338 | 1465/1635 | 0.6452 | 8.293 |
| TX-W | 282 | 245 | 17734 | 1618 | 190/245 | 0.9905 | 9.301 |

## Recipe self-checks

- `NV` Overture/TIGER road ratio: **inside_band** against the organiser band [0.71, 1.59]
- `NM` Overture/TIGER road ratio: **within_tolerance** against the organiser band [0.71, 1.59]
- `CO-UT` Overture/TIGER road ratio: **inside_band** against the organiser band [0.71, 1.59]
- `LA` Overture/TIGER road ratio: **outside_band** against the organiser band [0.71, 1.59] — not counted as a fold; its bytes are still reported below
- `AR-KS` Overture/TIGER road ratio: **within_tolerance** against the organiser band [0.71, 1.59]
- `TX-W` Overture/TIGER road ratio: **inside_band** against the organiser band [0.71, 1.59]

### Groups staged but not counted as folds

| group | road ratio | inside band | within tolerance | accepted tracts | files | bytes counted |
| --- | --- | --- | --- | --- | --- | --- |
| LA | 0.507151 | False | False | 1388 | 68 | 328,618,317 |

These groups were retrieved and measured, so their cost is disclosed rather than hidden; they are excluded from acceptance because a group whose observed and reference road lengths disagree beyond tolerance cannot test a gap estimator.
- hospital ratio: **not_computable** — the independent reference hospital count comes from USGS National Structures, whose registered endpoints serve zero objects in this environment; reporting a ratio without the reference side would be a fabricated measurement

### Staging directories with no manifest

- AZ-R hold 8,401,752 bytes of cached retrieval from an attempt the build refused; they carry no tracts, no labels and no defineness, and are named here rather than left to be discovered

## Held back, and why

- **building** (not_built): the registered reference side is not retrievable: github.com/microsoft/USBuildingFootprints now serves a README-only tree (root entries .gitignore, LICENSE-DATA, README.md, SECURITY.md, images) and the registered geojson path returns 404
- **poi_facilities** (not_built): the registered reference side is not retrievable: the TNM Structures prefix lists zero objects and sciencebase.gov does not resolve in this environment
- **poi_establishments** (not_built): the 2022 CBP index publishes no tract-level file (cbp22cd, cbp22co, cbp22csa, cbp22msa, cbp22pr_ia_co, cbp22pr_ia_st, cbp22st, cbp22us) and the HUD-USPS BUS_RATIO crosswalk that the spec's allocation variants need answers 202 to an anonymous HEAD
- **estimators** (preregistered_analogues_configured): builder writes staging/<group>/estimators.parquet only for ideas with predeclared faithful raw-source analogues; other implemented ideas are explicitly listed as not_evaluable_in_lab; 6 groups have staged estimator artifacts

## Safety rails

- exclusion: 9794 study GEOIDs and 298 study counties excluded; nearest accepted tract 8.293 km from a study tract against an 8.0 km buffer
- lab root `/home/martin/hddProjects/competitionsall-workspace/zindi-bias-bounty/lab`, staged under `staging/<group>`; root artifacts present: none

## Gates

- 32 gates, all_passed: **True**
- `acceptance_recorded_not_claimed`: True
- `components_held_are_documented`: True
- `county_rule_enabled_for_retrieval`: True
- `cumulative_retrieval_within_the_frozen_cap`: True
- `estimator_analogue_config_hash_matches_pin`: True
- `every_group_above_the_tract_floor`: True
- `every_implemented_idea_has_a_declared_lab_disposition`: True
- `every_url_matches_a_preregistered_pattern`: True
- `groups_are_the_preregistered_ones`: True
- `lab_root_outside_repo`: True
- `lab_values_never_reached_a_submission_artifact`: True
- `manifest_provenance_complete`: True
- `nearest_distance_buffer_respected`: True
- `no_downloaded_code_was_executed`: True
- `no_fabricated_measurement`: True
- `no_lab_value_in_submission_paths`: True
- `no_measurement_without_bytes`: True
- `no_study_tract_was_measured`: True
- `planned_groups_are_staged_or_refused_with_a_reason`: True
- `prerequisite_report_pin_matches_disk`: True
- `recipe_self_checks_passed`: True
- `report_has_no_temporal_field`: True
- `retrieval_stayed_on_the_allowlist`: True
- `self_check_flags_agree_with_the_measured_ratio`: True
- `self_check_rejections_are_disclosed`: True
- `spec_hash_matches_pin`: True
- `staged_directories_are_manifested_or_refused`: True
- `staged_estimator_parquets_match_exact_lab_eval_contract`: True
- `staging_not_promoted`: True
- `study_county_never_appears_in_a_url`: True
- `study_geoid_never_appears_in_a_url`: True
- `undefined_never_scored_as_zero`: True
