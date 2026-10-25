# Measure the preconditions of the external lab before the lab build spends its bandwidth: are the registered sources reachable, and do the planned lab groups still have tracts after the study exclusion runs on real census geography?

- Plan: `lab-prerequisites` (backlog item `bias60-lab-evaluation`)
- Preregistered configuration: `/home/martin/hddProjects/competitions-repos/zindi-bias-bounty/config/lab-prerequisites-preregistered.json` (SHA-256 `64e1c41f4684f53d…`)
- Lab plan consumed: `/home/martin/hddProjects/competitions-repos/zindi-bias-bounty/config/bias60-lab-preregistered.json`
- Spec: `/home/martin/hddProjects/competitionsall/docs/research/bias-60-ideas-2026-10.md` (SHA-256 `f150649b34caebef…`, matches its pin: True)
- Verdict: **PASS**

## Operator authorisation this probe works under

- operator decision 2026-10-25 09:35Z, recorded in orchestrator commit 901d5e3c8ee89eb9995e2adb15a3fee6c48b49ee: the external lab may be built; no reference layer or equivalent copy is ever computed on, joined to, or used to approximate targets for any study-region tract or GEOID

## What this probe is allowed to touch

- downloads: public census tract geography only from www2.census.gov
- source probes: HEAD only, content bytes read: False, credentials used: False
- cache: `/home/martin/hddProjects/competitionsall-workspace/zindi-bias-bounty/lab-prerequisites` (outside the lab root `/home/martin/hddProjects/competitionsall-workspace/zindi-bias-bounty/lab`: True)

## Measured

- Registered sources: 10; answering 200 anonymously: 5; not probeable as registered: 1
- Public census geography downloaded: 112428536 bytes across 9 files
- Study interior points established: 9793 from 9793 matched study tracts in EPSG:5070
- Planned lab groups: 9; probed: 5; with enough surviving tracts: 4
- Candidate tracts inspected: 4748; accepted by the frozen exclusion: 3491

## Source availability

| source | probe | status | meaning | bytes advertised | alternate that answers |
| --- | --- | --- | --- | --- | --- |
| `tiger_tracts_2020` | attempted | 200 | reachable | 3182444 | None |
| `overture_package_release` | attempted | 404 | not found at this instantiation, so the registered version pattern needs correcting before the build | None | https://overturemaps-us-west-2.s3.amazonaws.com/?list-type=2&max-keys=1 |
| `tiger_prisecroads_2024` | attempted | 404 | not found at this instantiation, so the registered version pattern needs correcting before the build | None | https://www2.census.gov/geo/tiger/TIGER2024/ROADS/tl_2024_32003_roads.zip |
| `tiger_county_roads_check` | attempted | 200 | reachable | 10125154 | None |
| `ms_us_building_footprints` | attempted | 200 | reachable | None | https://github.com/microsoft/USBuildingFootprints/releases/latest |
| `ms_global_ml_building_footprints` | attempted | 200 | reachable | None | None |
| `usgs_national_structures` | attempted | 200 | reachable | 32508 | https://apps.nationalmap.gov/tnmaccess/api/datasets?output=json&formats=Shapefile&q=structures |
| `cbp_2023` | attempted | 400 | unexpected | None | https://www2.census.gov/programs-surveys/cbp/datasets/2022/ |
| `hud_usps_crosswalk` | attempted | 202 | unexpected | 0 | None |
| `hospital_check` | skipped | None | the registry records a source family (USGS Health/Medical structures or HIFLD hospitals), not a URL; the build must name the exact service and version before it can be probed | None | None |

## Candidate tracts after the frozen exclusion

| group | states | counties | planned | candidates | accepted | rejected | min km to a study tract | ready |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `AZ-R` | AZ | 3 | 170 | 94 | 30 | 64 | 9.23 | yes |
| `OK-R` | OK | 0 | 130 | 1205 | 13 | 1192 | 26.189 | no |
| `NV` | NV | 2 | 700 | 674 | 674 | 0 | 33.707 | yes |
| `CO-UT` | CO, UT | 0 | 2100 | 2163 | 2163 | 0 | 162.964 | yes |
| `NM` | NM | 0 | 610 | 612 | 611 | 1 | 37.097 | yes |

## Findings the build has to know

- Lab plan needs amendment: **True**
- Verdict: the frozen lab plan is not yet buildable as written: OK-R cannot be a fold and cbp_2023, hud_usps_crosswalk, overture_package_release, tiger_prisecroads_2024 did not answer an anonymous request at its registered pattern

| group | planned | accepted | floor | consequence |
| --- | --- | --- | --- | --- |
| `OK-R` | 130 | 13 | 30 | this group cannot be a leave-one-group-out fold as frozen; the plan must widen its county list or the group must be merged or dropped |

Groups far below their planned tract count (plan numbers to correct):

- `AZ-R`: planned 170, accepted 30 (ratio 0.176)
- `OK-R`: planned 130, accepted 13 (ratio 0.1)

| source | status | meaning | alternate that answers |
| --- | --- | --- | --- |
| `overture_package_release` | 404 | not found at this instantiation, so the registered version pattern needs correcting before the build | https://overturemaps-us-west-2.s3.amazonaws.com/?list-type=2&max-keys=1 |
| `tiger_prisecroads_2024` | 404 | not found at this instantiation, so the registered version pattern needs correcting before the build | https://www2.census.gov/geo/tiger/TIGER2024/ROADS/tl_2024_32003_roads.zip |
| `cbp_2023` | 400 | unexpected | https://www2.census.gov/programs-surveys/cbp/datasets/2022/ |
| `hud_usps_crosswalk` | 202 | unexpected | None |

- `hospital_check` is not probeable as registered: the registry records a source family (USGS Health/Medical structures or HIFLD hospitals), not a URL; the build must name the exact service and version before it can be probed

- Excluded study GEOIDs with no interior point in the census file: 1


## Rejections by reason

- `official_scored_or_template_geoid`: 1252
- `within_neighbour_buffer_of_study_tract`: 5

## Exclusion policy as executed

- `buffer_crs`: EPSG:5070
- `centroid_buffer_km`: 8.0
- `fail_closed`: True
- `official_geoids_excluded`: 9379
- `study_counties_excluded`: 298
- `study_points_available_for_buffer_check`: 9793
- `study_states_excluded`: ['04', '06', '35', '40', '48']
- `template_geoids_excluded`: 9794
- `union_geoids_excluded`: 9794

## Gates

- `all_passed`: True
- `all_registered_sources_probed`: True
- `cache_outside_lab_root`: True
- `every_planned_group_measured`: True
- `negative_controls_declared`: True
- `no_accepted_tract_within_buffer`: True
- `no_forbidden_value_in_report`: True
- `no_lab_artifact_written`: True
- `no_official_geoid_accepted`: True
- `no_reference_layer_bytes_on_disk`: True
- `only_public_census_geography_downloaded`: True
- `report_has_no_temporal_field`: True
- `report_is_strict_json`: True
- `shortfalls_disclosed`: True
- `source_availability_disclosed`: True
- `source_probe_answers_are_reusable`: True
- `source_probe_credentials_unused`: True
- `sources_probed_without_content_bytes`: True
- `spec_hash_matches_pin`: True
- `study_points_measured_from_public_geography`: True
