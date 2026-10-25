# Staged external-lab build: reference-derived coverage-gap labels for lab regions, outside the four study regions

Staged build for `bias60-external-lab-build`. Config `b6586b6aa2cce55c…`, spec `f150649b34caebef…`, recipe variant `road-obs4cls-mtfcc-S1100S1200-5070-v1`.

## Retrieval

- 42 files, 828,814,965 bytes transferred (4,189,898,173 bytes of objects listed) from overturemaps-us-west-2.s3.us-west-2.amazonaws.com, www2.census.gov; no credentials; every URL matched a preregistered pattern
- Overture 2026-09-23.1: 256 objects listed, 6 read, about 733,420,392 compressed bytes read (bbox row-group pruning; the theme is never streamed whole)

## Measurement

| group | candidates | accepted | segments clipped | reference lines clipped | road defined | road ratio | nearest study km |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NV | 674 | 674 | 37893 | 2291 | 433/674 | 1.0134 | 32.637 |
| NM | 612 | 610 | 42816 | 5402 | 506/610 | 0.6546 | 34.971 |

## Recipe self-checks

- `NV` Overture/TIGER road ratio: **inside_band** against the organiser band [0.71, 1.59]
- `NM` Overture/TIGER road ratio: **within_tolerance** against the organiser band [0.71, 1.59]
- hospital ratio: **not_computable** — the independent reference hospital count comes from USGS National Structures, whose registered endpoints serve zero objects in this environment; reporting a ratio without the reference side would be a fabricated measurement

## Held back, and why

- **building** (not_built): the registered reference side is not retrievable: github.com/microsoft/USBuildingFootprints now serves a README-only tree (root entries .gitignore, LICENSE-DATA, README.md, SECURITY.md, images) and the registered geojson path returns 404
- **poi_facilities** (not_built): the registered reference side is not retrievable: the TNM Structures prefix lists zero objects and sciencebase.gov does not resolve in this environment
- **poi_establishments** (not_built): the 2022 CBP index publishes no tract-level file (cbp22cd, cbp22co, cbp22csa, cbp22msa, cbp22pr_ia_co, cbp22pr_ia_st, cbp22st, cbp22us) and the HUD-USPS BUS_RATIO crosswalk that the spec's allocation variants need answers 202 to an anonymous HEAD
- **estimators** (not_defined_for_this_build): all three implemented ideas name a provided-package input in the registry: Sc5 needs both the filtered and the unfiltered roads layer as the organiser shipped them, Mx4 needs buildings.sources, Tm1 needs strata.carbonplan_buildings plus the package building count. None of those exist for a lab region, and inventing a substitute mapping after the labels exist would be fitting the estimator to the answer.

## Safety rails

- exclusion: 9794 study GEOIDs and 298 study counties excluded; nearest accepted tract 32.637 km from a study tract against an 8.0 km buffer
- lab root `/home/martin/hddProjects/competitionsall-workspace/zindi-bias-bounty/lab`, staged under `staging/<group>`; root artifacts present: none

## Gates

- 24 gates, all_passed: **True**
- `acceptance_recorded_not_claimed`: True
- `components_held_are_documented`: True
- `county_rule_enabled_for_retrieval`: True
- `every_group_above_the_tract_floor`: True
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
- `prerequisite_report_pin_matches_disk`: True
- `recipe_self_checks_passed`: True
- `report_has_no_temporal_field`: True
- `retrieval_stayed_on_the_allowlist`: True
- `spec_hash_matches_pin`: True
- `staging_not_promoted`: True
- `study_county_never_appears_in_a_url`: True
- `study_geoid_never_appears_in_a_url`: True
- `undefined_never_scored_as_zero`: True
