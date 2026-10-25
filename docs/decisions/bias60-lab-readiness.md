# External lab corpus for the 60 estimators: frozen design, hard study exclusion and a rules gate

- Plan: `bias60-external-lab` (backlog item `bias60-external-lab-build`)
- Preregistered configuration: `/home/martin/hddProjects/competitions-repos/zindi-bias-bounty/config/bias60-lab-preregistered.json` (SHA-256 `8d195030963130be...`)
- Spec: `/home/martin/hddProjects/competitionsall/docs/research/bias-60-ideas-2026-10.md` (SHA-256 `f150649b34caebef...`, matches its pin: True)
- Status: **authorized_by_operator_decision** by operator, recorded by the shared steward in orchestrator commit 901d5e3c8ee89eb9995e2adb15a3fee6c48b49ee at 2026-10-25T09:35Z; organiser question `withdrawn_by_operator_decision`; sequencing blocker `bias60-track-a-combiner` (still binding on sequencing; the operator decision lifts the organiser-ruling precondition, not the ordering)
- Readiness verdict: **PASS** -- the design is frozen, the exclusion policy is proved on a self-test panel, the lab root holds only staged build output (871 files, none promoted, none keyed to a study region), and no model or submission path can read a lab file

## Operator decision that authorises the lab

> **Operator decision 2026-10-25 09:35Z (overrides every "until the organiser ruling" below).** No question goes to the organisers. Track B is allowed: fit the weights in the external lab, on geographies outside the four study regions with every study tract and its neighbours excluded, then apply only the learned weight vector to estimators computed from the provided data. Reference layers or equivalent copies are never computed on, joined to, or used to approximate targets for study tracts. Disclose the lab method in the writeup.

Recorded verbatim in the spec: True. Conditions and their enforcement:

- the lab sits on geographies outside the four study regions with every study tract and its neighbours excluded
  - enforced by: exclusion_policy (official and template GEOID exclusion plus an 8 km centroid buffer in EPSG:5070), tested in tests/test_bias60_lab_plan.py
- reference layers or equivalent copies are never computed on, joined to, or used to approximate targets for study tracts
  - enforced by: exclusion_policy consulted before any layer is loaded for a candidate, model_and_submission_paths_cannot_read_lab firewall, and lab_data_may_not_reach
- only the learned weight vector crosses into the submission, applied to estimators computed from the provided data
  - enforced by: track_b_controls.allowed_fields_in_a_submission_artifact and track_b_controls.never_cross
- the lab method is disclosed in the writeup
  - enforced by: writeup_disclosure (required sections and the file that must carry them)

- Track B crossing surface: ['weights', 'intercept', 'shrinkage', 'component_weights', 'fit_provenance', 'operator_decision_quote']; never crosses: ['per-tract lab labels', 'lab GEOIDs', 'lab-derived per-region means', 'reference-layer measurements', 'any value keyed to a study tract']
- Writeup disclosure required: True in ['docs/writeup/methodology.md', 'docs/writeup/bias-discovery.md']

## The binding rule text the decision operates against

- "Participants must not use reference layers, or equivalent copies obtained elsewhere, to recreate or approximate the organiser-computed targets." -- verbatim in the saved official text
- "Participants are not expected or permitted to reproduce these targets using reference datasets that have been removed from the challenge package." -- verbatim in the saved official text
- "The scored coverage gap computation must use only the datasets provided for this challenge; additional data sources are permitted only for the Best Bias Discovery prize." -- verbatim in the saved official text
- "You may use only the datasets provided for this challenge, however you may use additional public datasets for the Best Bias Discovery prize only (documented and reproducible)." -- verbatim in the saved official text

Why the text is still quoted: the RULES.md sentences are quoted unchanged because it is the operator decision, not a re-reading of the rule, that authorises the lab; the mitigation the decision imposes is geographic separation with study tracts and neighbours excluded, and that no reference layer is ever computed on or joined to a study tract

## Retained organiser question (withdrawn_by_operator_decision)

> For the Bias Bounty Mapping Equity Challenge we would like to validate our estimators on an external lab: the same coverage-gap recipe applied to about 10,000 2020 census tracts in nine US state groups that contain none of the four study regions and no tract sharing an edge with one, using the reference layers that were removed from the participant package (TIGER/Line 2024 prisecroads, Microsoft US Building Footprints, USGS National Structures, CBP/ZBP 2023) plus the same Overture release. No lab value would ever be joined to a study GEOID or enter a submission artifact; the scored submission uses only provided data. Is building and reporting such an external lab permitted under the rule that removed reference layers or equivalent copies obtained elsewhere must not be used to recreate or approximate the targets, or is it permitted only as Best Bias Discovery documentation? May weights fitted on other US geographies using public TIGER/MS/USGS/CBP be applied to provided-data features? And are the provided `usfs_BuildingCount_sum` / `carbonplan_buildings` columns usable as features?

> retained verbatim for the record because the spec's own combined question also asks whether the provided usfs_BuildingCount_sum and carbonplan_buildings columns are usable as features; the operator answered the lab question and no question goes to the organisers

## Lab groups (spec 4.2)

| Group | States | Approx tracts | Role | Counties declared |
|---|---|---:|---|---|
| TX-W | TX | 890 | same state DOT conventions as the study, best transfer for the road ratio | 8 |
| AZ-R | AZ | 170 | tribal and heat analogue | 3 |
| OK-R | OK | 130 | Plains analogue of the tribal statistical areas | 0 |
| NM | NM | 610 | heat, drought, tribal (Navajo, Pueblos) | 0 |
| CA-F | CA | 2500 | post-fire analogue for the building-vintage hypothesis | 16 |
| NV | NV | 700 | extreme heat | 2 |
| AR-KS | AR/KS | 1500 | analogue of Eastern Oklahoma | 0 |
| LA | LA | 1300 | Gulf heat analogue of Houston | 0 |
| CO-UT | CO/UT | 2100 | front-range wildland-urban-interface fire | 0 |

## Source register (nothing retrieved yet)

| Source | Role | URL | Version pattern | Retrieved | SHA-256 |
|---|---|---|---|---|---|
| `tiger_tracts_2020` | lab_tract_polygons | https://www2.census.gov/geo/tiger/TIGER2020/TRACT/tl_2020_<ss>_tract.zip | tl_2020_<ss>_tract.zip | None | None |
| `overture_package_release` | observed_data | s3://overturemaps-us-west-2/release/<rel>/ | same release as the challenge package, identified by matching package building id/version pairs; candidates 2026-08 and 2026-09 | None | None |
| `tiger_prisecroads_2024` | reference_roads | https://www2.census.gov/geo/tiger/TIGER2024/PRISECRROADS/tl_2024_<ss>_prisecroads.zip | tl_2024_<ss>_prisecroads.zip (S1100/S1200) | None | None |
| `tiger_county_roads_check` | reference_roads_check | https://www2.census.gov/geo/tiger/TIGER2024/ROADS/tl_2024_<ssccc>_roads.zip | county All Roads release 2024-08-08, filtered to MTFCC in {S1100, S1200} | None | None |
| `ms_us_building_footprints` | reference_buildings | https://github.com/microsoft/USBuildingFootprints | state GeoJSON, 129.6M total, ODbL | None | None |
| `ms_global_ml_building_footprints` | reference_buildings_variant | https://github.com/microsoft/GlobalMLBuildingFootprints | US quadkeys | None | None |
| `usgs_national_structures` | reference_facilities | https://apps.nationalmap.gov/downloader/ | Struct_Point state downloads; record loaddate | None | None |
| `cbp_2023` | reference_establishments | https://www.census.gov/data/datasets/time-series/econ/cbp/2023.html | cbp23co county file; zbp23totals ZIP totals; released 2025-06-26 | None | None |
| `hud_usps_crosswalk` | allocation_helper | https://www.huduser.gov/portal/datasets/usps_crosswalk.html | ZIP to tract BUS_RATIO crosswalk | None | None |
| `hospital_check` | recipe_identification | USGS Health/Medical structures or HIFLD hospitals | compared against the Overture hospital category to reproduce the about 12x ratio | None | None |

## Study exclusion, measured

- official scored GEOIDs excluded: 9379
- platform template GEOIDs excluded: 9794 (union 9794)
- study counties excluded: 298 in states ['04', '06', '35', '40', '48']
- centroid buffer: 8.0 km in EPSG:5070

| Candidate | County | Centroid | Rejection reason |
|---|---|---|---|
| `04001944902` | 04001 | (0.0, 0.0) | official_scored_or_template_geoid |
| `53001950100` | 53033 | None | official_scored_or_template_geoid |
| `99001944902` | 04001 | (0.0, 0.0) | within_neighbour_buffer_of_study_tract |
| `98001944902` | 00000 | (1000.0, 0.0) | within_neighbour_buffer_of_study_tract |
| `97001944902` | 00000 | (5000.0, 0.0) | within_neighbour_buffer_of_study_tract |
| `96001944902` | 00000 | (500000.0, 0.0) | None |

## Gates

- `spec_hash_matches_pin`: pass
- `plan_parsed_from_spec`: pass
- `lab_root_outside_repo`: pass
- `no_prohibited_content_on_disk`: pass
- `study_geoids_fully_excluded`: pass
- `study_counties_enumerated`: pass
- `study_county_overlap_recorded`: pass
- `county_filtered_retrieval_required_recorded`: pass
- `neighbour_buffer_policy_rejects_study_and_adjacent_tracts`: pass
- `model_and_submission_paths_cannot_read_lab`: pass
- `source_register_complete`: pass
- `definedness_accounting_declared`: pass
- `recipe_self_checks_declared`: pass
- `operator_decision_present_verbatim_in_spec`: pass
- `external_use_recorded_as_authorized`: pass
- `operator_conditions_enforced`: pass
- `track_b_crossing_surface_is_bounded`: pass
- `writeup_disclosure_required`: pass
- `availability_probe_stays_head_only`: pass
- `readiness_performed_no_retrieval`: pass
- `lab_root_holds_staged_build_output_only`: pass
- `build_output_is_never_read_by_readiness`: pass
- `rules_citations_are_verbatim`: pass
- `operator_question_withdrawn_not_answered`: pass
- `operator_question_covers_the_spec_question`: pass
- `no_lab_number_exists`: pass
- `all_passed`: pass
- `report_is_strict_json`: pass
- `report_has_no_temporal_field`: pass

## Acceptance of the build, once allowed

- labelled groups required: 5 of 9
- per group and component: ['tracts', 'defined', 'undefined', 'undefined_share', 'gap_mean_over_defined', 'gap_p50_over_defined']
- per group: ['composite_defined', 'composite_undefined', 'recipe_variant_id', 'source_versions_used']
- recipe self-checks required: ['state_road_ratio_in_organiser_band', 'hospital_ratio_check']
- recipe self-check status: road_ratio_measured_for_staged_groups

