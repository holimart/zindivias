# Measure the preconditions of the external lab before the lab build spends its bandwidth: are the registered sources reachable, and do the planned lab groups still have tracts after the study exclusion runs on real census geography?

- Plan: `lab-prerequisites` (backlog item `bias60-lab-evaluation`)
- Preregistered configuration: `/home/martin/hddProjects/competitions-repos/zindi-bias-bounty/config/lab-prerequisites-preregistered.json` (SHA-256 `76fbc11014b67b85…`)
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

- Registered sources: 10; answering 200 anonymously: 10; not probeable as registered: 0
- Public census geography downloaded: 134365215 bytes across 12 files
- Study interior points established: 9793 from 9793 matched study tracts in EPSG:5070
- Planned lab groups: 7; probed: 7; with enough surviving tracts: 7
- Candidate tracts inspected: 9800; accepted by the frozen exclusion: 9483

## Source availability

| source | probe | status | meaning | bytes advertised | registered replacement |
| --- | --- | --- | --- | --- | --- |
| `tiger_tracts_2020` | attempted | 200 | reachable | 3182444 | None |
| `overture_package_release` | attempted | 200 | reachable | None | replaces `s3://overturemaps-us-west-2/release/<rel>/`: the guessed release-prefix URL returned 404; the public bucket listing with the pinned release prefix answers 200 without reading object content |
| `tiger_county_roads_check` | attempted | 200 | reachable | 10125154 | replaces `tiger_prisecroads_2024 state PRISECRROADS pattern`: the registered state PRISECRROADS paths returned 404; the county All Roads file answers 200 and is the reference used by the road recipe |
| `tiger_county_roads_validation` | attempted | 200 | reachable | 3189748 | None |
| `ms_us_building_footprints` | attempted | 200 | reachable | None | None |
| `ms_global_ml_building_footprints` | attempted | 200 | reachable | None | None |
| `usgs_national_structures` | attempted | 200 | reachable | 32508 | None |
| `cbp_2022` | attempted | 200 | reachable | None | replaces `cbp_2023 Census.gov HTML page`: the 2023 page returned HTTP 400 while the official 2022 dataset directory answers 200; this pins the actually reachable version rather than claiming 2023 availability |
| `acs5_tract_housing_units` | attempted | 200 | reachable | 8531 | replaces `hud_usps_crosswalk ZIP-to-tract BUS_RATIO endpoint`: HUD endpoints returned HTTP 202 to anonymous HEAD; this reachable public ACS endpoint supports the already registered county-total x tract housing-unit-share variant without using BUS_RATIO |
| `hospital_check` | attempted | 200 | reachable | 410 | replaces `unaddressable USGS Health/Medical structures or HIFLD source-family placeholder`: the previous source family had no URL and could not be probed; this exact public catalogue endpoint answers 200 without fetching dataset content |

## Candidate tracts after the frozen exclusion

| group | states | counties | planned | candidates | accepted | rejected | min km to a study tract | ready |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `NV` | NV | 2 | 700 | 674 | 674 | 0 | 33.707 | yes |
| `CO-UT` | CO, UT | 0 | 2100 | 2163 | 2163 | 0 | 162.964 | yes |
| `NM` | NM | 0 | 610 | 612 | 611 | 1 | 37.097 | yes |
| `LA` | LA | 0 | 1300 | 1388 | 1388 | 0 | 66.36 | yes |
| `AR-KS` | AR, KS | 0 | 1500 | 1652 | 1636 | 16 | 8.276 | yes |
| `TX-W` | TX | 8 | 890 | 282 | 274 | 8 | 9.105 | yes |
| `CA-F` | CA | 16 | 2500 | 3029 | 2737 | 292 | 8.026 | yes |

## Findings the build has to know

- Lab plan needs amendment: **False**
- Verdict: the frozen lab plan is buildable as written on the groups probed

Groups far below their planned tract count (plan numbers to correct):

- `TX-W`: planned 890, accepted 274 (ratio 0.308)

- Excluded study GEOIDs with no interior point in the census file: 1


## Rejections by reason

- `official_scored_or_template_geoid`: 213
- `within_neighbour_buffer_of_study_tract`: 104

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

- `active_cv_groups_match_amendment`: True
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
