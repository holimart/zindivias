# Lab evaluation of the 60 estimators and their linear combination: leave-one-group-out RMSE, incremental value, MTMM and the Track B weight fit

- Plan: `bias60-lab-evaluation` (backlog item `bias60-lab-evaluation`)
- Preregistered configuration: `/home/martin/hddProjects/competitions-repos/zindi-bias-bounty/config/lab-evaluation-preregistered.json` (SHA-256 `efc69dda27952967...`)
- Spec: `/home/martin/hddProjects/competitionsall/docs/research/bias-60-ideas-2026-10.md` (SHA-256 `f150649b34caebef...`, matches its pin: True)
- Measurement status: **not_run_missing_lab**
- Verdict: **PASS**

## Operator decision this evaluation works under

- operator, recorded by the shared steward in orchestrator commit 901d5e3c8ee89eb9995e2adb15a3fee6c48b49ee at 2026-10-25T09:35Z
- the organiser-ruling precondition is replaced: lab-fitted weights may be used, but only the learned weight vector and its normalisation constants cross, recorded with SHA-256 and the lab commit, and the lab method must be disclosed in the writeup
  - still binding: no reference layer or equivalent copy is computed on, joined to, or used to approximate targets for any study-region tract or GEOID
  - still binding: only the weight vector and normalisation constants cross
  - still binding: disclose the lab method in the writeup

## What this evaluation measures, once the lab exists

- Leave-one-state-group-out RMSE for every implemented estimator and for the lab-fitted linear combination, per held-out group and worst case.
- Incremental value: each idea's partial correlation with the lab label given all the others, and its drop-column change in grouped-CV RMSE.
- Residual correlation matrix of `r_i = y - e_i`, its off-diagonal mass, and the Krogh-Vedelsby ambiguity of the member set.
- Multitrait-multimethod table: within-component and between-component mean absolute correlation, so an idea that measures urbanity instead of its component shows up.
- Track B weights: non-negative ridge-toward-equal NNLS with county-blocked inner folds for lambda, the Shimodaira shift-weighted variant, and bootstrap stability selection over whole groups.
- How each rule-clean Track A variant (A1 equal weights, A2 spectral, A3 Dawid-Skene, A4 self-consistency prior) scores on the same held-out groups.

## Acceptance, stated before any number exists

- at least 5 held-out state groups; every implemented idea scored
- Track B must beat equal weights in at least 75% of held-out groups, or be reported as not beating them

## Why no number appears here yet

- `bias60-external-lab-build` has not produced its artifacts: no lab artifact set under /home/martin/hddProjects/competitionsall-workspace/zindi-bias-bounty/lab
- the build must deliver `manifest.json`, `tracts.parquet`, `estimators.parquet`, `labels.parquet`, `defineness.json` with manifest fields lab_commit, recipe_variant_id, exclusion_policy_sha256, buffer_km, sources, self_checks, groups, study_polygon_source and both RULES.md recipe self-checks passing
- Until then this report publishes nulls. A lab number with no lab behind it would be the single most damaging thing this lane could put in a writeup.

## Rails that must hold before a number is published

- `spec_hash_matches_pin`: pass
- `lab_artifact_contract_declared`: pass
- `registry_implemented_ideas_declared`: pass
- `manifest_provenance_complete`: not run: no lab
- `recipe_self_checks_passed`: not run: no lab
- `study_geoid_exclusion_holds`: not run: no lab
- `nearest_distance_buffer_respected`: not run: no lab
- `rows_consistent`: not run: no lab
- `undefined_never_scored_as_zero`: not run: no lab
- `groups_held_out_adequate`: not run: no lab
- `crossing_surface_is_bounded`: pass
- `track_a_release_gate_recorded`: pass
- `weights_not_applied_to_study_tracts`: pass
- `no_fabricated_measurement`: pass
- `no_reference_layer_read_by_this_row`: pass
- `no_forbidden_value_in_report`: pass
- `shift_correction_status_declared`: pass
- `prohibitions_recorded`: pass

## Measurement gates (evaluated only when the lab is present)

- `per_idea_rmse_reported`: not run: no lab
- `incremental_value_reported`: not run: no lab
- `residual_correlation_reported`: not run: no lab
- `mtmm_reported`: not run: no lab
- `krogh_vedelsby_reported`: not run: no lab
- `track_b_win_rate_rule_applied`: not run: no lab
- `stability_selection_reported`: not run: no lab
- `weight_vector_recorded`: not run: no lab
- `track_a_variants_scored_or_documented`: not run: no lab
- `measurements_all_passed`: not run: no lab

## What is allowed to cross from the lab into a submission

- allowed fields: weights, intercept, shrinkage, normalisation_constants, lab_commit, lab_manifest_sha256
- weight vectors published in this report: 0
- Track A pre-registration status: `not_recorded`; weight vector release: `withheld`
- study-side predictions emitted: 0; study idea parquets opened: 0

