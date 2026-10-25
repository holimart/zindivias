# Bias Bounty lane journal

## 2026-10-24 — public GeoParquet inventory

- Item: `bias-bounty-geoparquet-public-bucket` (P0).
- Delivered: `docs/data-inventory.md`; enhanced `scripts/audit_data.py` with anonymous public-bucket listing, remote Parquet-footer metadata, and sample/ACS GEOID reconciliation.
- Exact measurement command:
  ```sh
  /home/martin/hddProjects/langywrap/uv run --with pyarrow python scripts/audit_data.py --public-bucket --data-root "$COMPETITION_WORKSPACE" --sample-submission "$COMPETITION_WORKSPACE/SampleSubmission.csv" --out /tmp/bias-bounty-public-audit.json
  ```
- Output: `Inventoried 214 files` (211 remote bucket objects and 3 local workspace files). 123 Parquet objects had footers read (rows, row groups, Arrow schema and GeoParquet metadata); 88 CSV companions listed by key/size. No full object was downloaded. Four requested region sample joins matched 9,379/9,379 GEOIDs using string key `GEOID`; 415 `eastern-wa` sample rows are outside the requested regions. Remote content SHA-256 is not exposed by the public listing, so no false checksums claimed; local sample SHA-256 is in the inventory.
- Validation commands/results:
  - `python -m py_compile scripts/audit_data.py` — passed.
  - `python scripts/audit_data.py --data-root "$COMPETITION_WORKSPACE" --out /tmp/bias-bounty-local-audit.json` — inventoried 3 workspace files.
  - Inventory assertions — passed: 211 objects, 123 complete Parquet footers, 88 CSV listings, and zero unmatched sample GEOIDs in each requested region.
- Raw metadata artifact: `/tmp/bias-bounty-public-audit.json`, SHA-256 `360d8aeeeedc69d0130599773a23a4a0944fb18704f75f98e7af24297e2c2413` (not committed; no raw data included).
- No prediction work, target reconstruction, bulk download, or submission performed.

## 2026-10-24 — defined-flag estimator screen

- Item: `bias-bounty-defined-flag-estimator` (P0).
- Deliverables: `scripts/estimate_defined_flags.py`, `reports/defined-flag-audit.json`, plus correction in `docs/data-inventory.md` documenting the authoritative per-region samples.
- Authorized small inputs copied into `$COMPETITION_WORKSPACE/defined-flags-inputs`: four official per-region sample CSVs and four strata Parquets. Exact sizes/SHA-256s are embedded in the report. No Overture geometry or removed reference data was read; source ablation compares strata demographic-only fallbacks against provided strata building proxies. This is an unvalidated, rule-based screen (population/urbanization thresholds); no accuracy claim. A neutral 0.5 is used only to test composite inclusion arithmetic, not as a scored estimate. Two rows use an explicitly documented unresolved-composite inclusion fallback. Missing urban fractions are not imputed to zero.
- Exact command:
  ```sh
  /home/martin/hddProjects/langywrap/uv run --with pyarrow python scripts/estimate_defined_flags.py --data-root "$COMPETITION_WORKSPACE/defined-flags-inputs" --out reports/defined-flag-audit.json
  ```
- Observed: 9,379 unique authoritative IDs covered exactly once (region counts: 1,593 / 591 / 1,192 / 6,003); all seven binary flag outputs and the composite had zero nulls; every component was within `{0,1}`; maximum composite inclusion error `0.0`; two repeated runs were byte-identical. Missing `pct_urban` values: 11 / 0 / 1 / 14 by region, handled without zero imputation. `python -m py_compile scripts/estimate_defined_flags.py` passed under the PyArrow uv environment.
- Report SHA-256: `c94d90dc9b8b676ea30a881446392483a87cb25f31e7d96b64d0767eec98bf9b`; script SHA-256: `361087228da0d033db3326d7b1c42b1aebe7f0ba490845d15592a39a548389fc`.
- Not submitted; the flags are estimates, not organiser definedness labels.

## 2026-10-24 — LB probe plan and C0 dry-run

- Item: `bias-bounty-lb-probe-plan` (P0).
- Deliverables: `docs/lb-probe-plan.md`, `scripts/build_c0_candidate.py`, `scripts/submit_zindi.py`, `reports/candidates.json`, `reports/candidates/C0_all_zero.csv`, and `reports/submissions.jsonl`.
- Candidate definitions and stop/final-two rules are pre-registered in the plan. The initial plumbing commit froze C0; the gap-estimator v0 turn below subsequently materialized C1–C3, refreshed `reports/candidates.json` with their hashes and validation statistics, and updated the plan. No scored upload occurred.
- Candidate source commit: `45bcb12`. Build command: `uv run python scripts/build_c0_candidate.py --sample-dir "$COMPETITION_WORKSPACE/defined-flags-inputs" --out reports/candidates/C0_all_zero.csv`; observed 9,379 rows, five columns. C0 SHA-256 `7546520ee671aaaa10fabe3fd4f6edaa7b88a1a424c9957bac29dcf7a0810f73`; refreshed C0–C3 candidate manifest SHA-256 `aeb7176f3a432a9ab7c8db42497286d13f2e240040002050b0b73b0451e944a5`.
- Exact dry-run command: `uv run python scripts/submit_zindi.py --candidate C0 --dry-run`. Output: `DRY RUN PASS C0: 9379 rows, 9379 unique IDs, SHA-256 7546520ee671aaaa10fabe3fd4f6edaa7b88a1a424c9957bac29dcf7a0810f73`. Ledger row SHA-256 `0c1c85e379d2401f18de5dbbe4cd06f4cbdf05f1c1d8b5f26c975cae2bd72256`; required submission fields present, platform ID/score/rank null, status `dry_run_validated`.
- `uv run python -m py_compile scripts/build_c0_candidate.py scripts/submit_zindi.py` passed. C0 rebuilt independently with an identical SHA. `--submit` guard test returned code 3, read no token, and attempted no upload by this lane builder. Account membership was not verified. Token content was never read or printed. Subsequent steward-attributed C0 platform attempts are preserved in the ledger and addressed by the stop note below.

## 2026-10-24 — provided-data gap estimator v0

- Item: `bias-bounty-gap-estimator-v0` (P0). Deliverables: `scripts/estimate_gaps.py`, `reports/gap-estimates.csv` (16 columns including GEOID, component gaps and defined flags), `reports/gap-estimator-audit.json`, candidate C1–C3 CSVs and refreshed `reports/candidates.json`. `docs/lb-probe-plan.md` now matches the frozen candidates and platform stop.
- No labels, sample score values, removed layers or external data were used. Remote reads used only provided Overture roads/buildings/POIs, ACS tract geometries (housing-unit values were not read), and strata ALAND/population/urbanity. ACS polygons absent from the authoritative sample were excluded from the spatial index. Only 216 KB of per-sample-tract summaries were cached in `$COMPETITION_WORKSPACE/gap-estimator/features`; no complete Overture object was downloaded. Named-road classes: motorway/trunk/primary/secondary; clipped lengths in EPSG:5070; building/POI counts use bbox-centre assignment. Uncategorized POIs use observed per-region category shares instead of a true-zero fill. Expected-reference formulas and fallbacks are documented in the audit.
- Run command:
  ```sh
  /home/martin/hddProjects/langywrap/uv run --with pyarrow --with shapely --with pyproj python /home/martin/hddProjects/competitions-repos/zindi-bias-bounty/scripts/estimate_gaps.py --data-root "$COMPETITION_WORKSPACE/defined-flags-inputs" --workspace "$COMPETITION_WORKSPACE" --out /home/martin/hddProjects/competitions-repos/zindi-bias-bounty/reports/gap-estimates.csv --audit-out /home/martin/hddProjects/competitions-repos/zindi-bias-bounty/reports/gap-estimator-audit.json --manifest /home/martin/hddProjects/competitions-repos/zindi-bias-bounty/reports/candidates.json
  ```
  Output: `Wrote 9379 estimator rows`; a repeated cached run produced an identical full estimate CSV SHA-256.
- Validation passed: exactly 9,379 authoritative IDs once each (1,593/591/1,192/6,003 by region); seven flags are binary; all score fields finite in `[0,1]`; no blanks; POI-halves and top-level composite checks within `1e-9` (maximum error `0.0`). C0–C3 each passed `scripts/submit_zindi.py --candidate C<n> --dry-run` against official IDs and their frozen manifests, with test ledgers only in `/tmp`. `py_compile` and `git diff --check` passed. No live upload from this turn.
- Candidate definitions: C0 all-zero, C1 per-region C2 prediction-mean constants (not target means), C2 component estimator v0, C3 expected-reference proxy scale ×1.25. All are 9,379-row/five-column candidates with per-region means and defined rates in the manifest. Manifest SHA-256 `aeb7176f3a432a9ab7c8db42497286d13f2e240040002050b0b73b0451e944a5`; C2 SHA-256 `0a2584b4a2d7c1c16bbce692accd9a47284669448b482a4b37a68cd68aff7d5c`; full estimate SHA-256 `0e2b94210fbd3e262c847dc4d2af364eeb4186f35361982bad51dd2e780c8852`; audit SHA-256 `a872e2dd62a5333037bc8e663278134c5b0a08493c7ec41768962e33859187f9`; estimator SHA-256 `f631061ec9b6214dcc957259fdf79391547ec824af6fa20f098f63cd210b74c6`.
- Definedness diagnostic vs official aggregate any-undefined reference fractions: Maricopa estimate `0.365348` vs `0.55`; Northern CA `0.304569` vs `0.37`; Eastern OK `0.406879` vs `0.21`; South-Central TX `0.369482` vs `0.28`. This material proxy mismatch is reported, not an accuracy estimate; there is no RMSE/label validation and no accuracy claim.
- Platform stop: steward-attributed C0 records in `reports/submissions.jsonl` say Zindi processed against the legacy 9,794-ID sample including 415 eastern-WA IDs, conflicting with the authoritative 9,379 four-region samples. The apparent initial/0.0 response was corrected to a processing failure, not a score. The conflict is documented in `docs/lb-probe-plan.md`; do not infer the extra scores or upload again until the steward resolves the contract.

## 2026-10-24 — gap estimator semantic invariants

- Item: `bias-bounty-gap-estimator-invariants` (P0). Added `tests/test_gap_estimator.py`, `scripts/audit_gap_invariants.py`, and `reports/gap-invariant-audit.json`; factored POI-half and defined-only composite helpers into `scripts/estimate_gaps.py`. All-undefined returns no composite (`None`), and the production writer rejects such a score rather than fabricating a mean. Exact GEOID coverage now uses a reusable ordered/no-duplicate assertion.
- Exact test command: `/home/martin/hddProjects/langywrap/uv run --with pytest --with pyarrow --with shapely --with pyproj pytest -q tests/test_gap_estimator.py` — `1 passed in 0.52s`. Audit command: `/home/martin/hddProjects/langywrap/uv run --with pytest --with pyarrow --with shapely --with pyproj python scripts/audit_gap_invariants.py --out reports/gap-invariant-audit.json` — `PASS 309/309 cases; exact coverage 9379/9379; deterministic rerun`.
- Matrix: each of seven component/parent flags has 16 defined and 16 undefined cases; POI facility/establishment half-weight combinations 64/64; composite defined-subset cases 16/16 including all-undefined; exact authoritative coverage and missing/extra/duplicate/reordered-ID rejection 5/5. The fixture checks all 9,379 production rows for exact ordered IDs, all required columns, null-free finite bounded values, binary flags, and the composite mean over only defined top-level components. No labels or leaderboard values are used.
- Invariant report SHA-256 `438c8a2915c62cdebab3f597ca050400b208c2cd65ca552c3b450aef9af7870f`; tests SHA-256 `6057952c633d86da79da8101cd0b5c80a57aae426d42c3c7ab0dd930d3cbe3b9`; audit runner SHA-256 `54cc37245516b57daa02758c9d324256d29a6c610155c4e7f4fe51120961f7d8`. `py_compile` and `git diff --check` passed.

## 2026-10-24 — official rules and authoritative sample contract

- Item: `bias-bounty-rules-and-region-samples` (P0). Rewrote `docs/competition.md` from `$COMPETITION_WORKSPACE/RULES.md`; downloaded official samples to `$COMPETITION_WORKSPACE/samples/`; added `scripts/audit_sample_contract.py`, `reports/sample-contract-audit.json`, `reports/sample-id-reconciliation.csv`, and the full 415-ID list in `reports/legacy-only-sample-ids.csv`. Refreshed `docs/data-inventory.md` and the probe-plan sample path. The audit reads only headers/GEOIDs; sample score placeholders are not used as labels.
- Reproduction: `uv run python scripts/audit_sample_contract.py --workspace "$COMPETITION_WORKSPACE" --out reports/sample-contract-audit.json --reconciliation reports/sample-id-reconciliation.csv --legacy-only-ids reports/legacy-only-sample-ids.csv` — `PASS official 9379 IDs; legacy 9794; overlap 9379; legacy-only 415`.
- Workspace sample artifacts (`samples/<region>-sample-submission.csv`) and SHA-256: Maricopa 44,664 bytes `1640b801d56ab308e68c65583b88cfb9e1555b05a6d0b922eaba22ac09960a31`; Northern CA 16,608 `f38017b5284667b80b451685fd146388c108e1b30308301289c7a96ecfe60902`; Eastern OK 33,436 `56cb40fba4008d646bc63c62c91e211871459224b3de8b3a886ff813803fd0d4`; South-Central TX 168,144 `afa59c0eb30c40be25ce82098f144447591b79cb9692e669b2f2fe7b7b1ee58e`. Total 262,852 bytes; each file has the exact five-column authoritative header and GEOID text.
- The ID table proves all 9,379 official IDs are in the legacy 9,794-row sample and the only 415 extras are eastern-wa; no official-only ID. The official sample columns are `GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score`; rules require GEOID and coverage score and allow components as optional. The legacy sample has 17 columns including `region`/defined flags. Prior steward processing reported the extra IDs expected, so actual portal contract remains unresolved and uploads remain stopped.
- `scripts/submit_zindi.py` now defaults to `$COMPETITION_WORKSPACE/samples`, enforces authoritative headers, columns, row order and exact IDs. C0–C3 dry-runs all passed against `samples/`, each with temporary `/tmp` ledger; the legacy 17-column sample header is rejected. Estimator invariants remain passing (`1 passed`). `py_compile` and `git diff --check` passed; no upload was attempted.
- Artifacts: audit JSON SHA-256 `e9a56f30ea7c5a09bf94ebc95d2bf6b1e7559456ee8cf1c16304d9137175dbd0`; reconciliation table `2951ca3597268db8beab54b9354936fb71f00b7cffe2e3381ec91ff5ea5c5f6b`; extra-ID CSV `f7c9079a0869b60d378c9602bda0b0bd9c1c8924c6876c6b5e3b2615c7454148`; audit script `9d3ff6687fb8bda75cf5a685b216aa1722e606543eb2d3eed20858dccec71c9b`.

## 2026-10-24 — Best Bias Discovery / Documentation draft

- Item: `bias-bounty-discovery-writeup` (P1). Added `docs/writeup/methodology.md`, `docs/writeup/bias-discovery.md`, `scripts/discovery_rural_svi_minority.py`, and `reports/discovery-rural-svi-minority.json`. The proposed subgroup is rural South-Central TX tracts (`pct_urban < 0.5`) in the upper quartile of provided `svi_minority` (cutoff 0.6549), a rural × SVI-domain intersection not specified in the official scorecard description. The script uses official sample GEOIDs, provided strata, C2 predictions and pre-registered C3 only; no extra public data or labels.
- Exact command: `/home/martin/hddProjects/langywrap/uv run --with pyarrow --with numpy python scripts/discovery_rural_svi_minority.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-rural-svi-minority.json` — `PASS 248 upper-quartile rural tracts vs 739 other rural tracts; C2 difference 0.034877 95% bootstrap CI [0.01675570256032317, 0.055088504164793564]`. Four of 991 rural tracts lack `svi_minority`. C3 sensitivity difference 0.04647, CI [0.02678, 0.06808]. Tract-bootstrap intervals do not cover spatial dependence or estimator/target error.
- Named highest C2 proxy GEOIDs include `48253020102`, `48001950402`, `48169980000`, `48001950401`, `48185180400`; building/POI estimates drive the subgroup difference, while transport CI includes zero. Consequences for dispatch/evacuation are explicitly framed as a hypothesis, not observed outcomes or true organiser gaps. No scored candidate CSV was changed and no upload occurred.
- Validation: repeated run produced byte-identical JSON; estimator invariant tests `1 passed`; `py_compile` and `git diff --check` passed. Report SHA-256 `da1a581ddb25347cc6a0ffd2bb06cb8a1811082d08973bbfefc04e0c5c08cbe0`; script SHA-256 `e8ae5c85e231f333d89aba6e4d2a24bea30c9eb901424e5bebb8d3bc2eb44a1b`; methodology SHA-256 `72f55714c94e1a689bc4186f1819c2884431affb07fd085ad3212a11a303298d`; discovery draft SHA-256 `b670f39a1dfa5c516248d3af49fe69a416e90fd8434225169b8da44261f55298`.

## 2026-10-24 — global vs regional component-rate sensitivity screen

- Item: `bias-bounty-estimator-sensitivity` (P1). Added `scripts/compare_estimator_variants.py`, `reports/estimator-sensitivity.json`, and bundle-local five-column candidates under `reports/estimator-sensitivity/`. Rates are unweighted means of C2's bounded retained component fractions (`1 - clipped gap`) over originally defined rows. C0 uses one global rate per component; C1 uses per-region rates; normalized gaps use `clip(1 - rate_i / rate_normalizer, 0, 1)` and the exact C2 defined-component masks. This is a sensitivity convention, not raw observed/reference ratios or accuracy evidence.
- The bundle measured C0 pooled-rate candidate hash `8d51483a168f0126cbfd4c72bc5259df0010b9fb68294a8d4b18ad5851d1c232` and C1 region-rate candidate hash `19de3ddc8186b71b20a4971374557b6f827fe782196691d33d77310a85193647`. Both had exact ordered 9,379/9,379 official IDs, no duplicates, and 100% rows with all four output scores finite and in [0,1]; regional row counts/validity are Maricopa 1,593/100%, Northern CA 591/100%, Eastern OK 1,192/100%, South-Central TX 6,003/100%. The frozen public-probe C0 all-zero and C1 region-constant candidates remain unchanged and are separately hashed/validated in the report.
- Paired C1−C0 composite delta over all rows: mean −0.001509, q05 −0.031767, median 0, q95 +0.021842. Regional mean/q05/median/q95: Maricopa −0.021049/−0.048685/−0.024147/0; Northern CA +0.005531/−0.003068/+0.003236/+0.020870; Eastern OK +0.014070/0/+0.006679/+0.034386; South-Central TX −0.000111/−0.006401/0/+0.005485. Report includes the corresponding row-wise and regional quantiles for every optional component column and input feature hashes/lineage.
- Reproduction/validation: `/home/martin/hddProjects/langywrap/uv run python scripts/compare_estimator_variants.py --workspace "$COMPETITION_WORKSPACE"` — `PASS C0/C1 sensitivity outputs: 9,379/9,379 exact IDs; validity rates 1.000000/1.000000`; the second run produced byte-identical JSON and both CSVs. Estimator invariant tests: `1 passed`; `py_compile` and `git diff --check` passed. No public leaderboard values, labels, removed references, external data, or sample placeholder scores used. No upload occurred in that sensitivity turn; at that time the platform-contract stop remained in force (superseded by the later steward full-ID directive documented below). Script SHA-256 `91c952e7f2620f15954f7c46085da609026b6eb9c62fb165f9f79dc7b68044b8`; report SHA-256 `19e08ef6f2a8be0c3c6c49d358b82a537b2b6f98081c30d170d661cdab6110cb`; C0/C1 candidate hashes as above.

## 2026-10-24 — full platform-template candidates and authorized probes

- Item: `bias-bounty-full-id-candidates` (P0). Per the dispatched steward directive, rebuilt frozen C0–C3 to the 9,794-unique-ID Data-page template order, preserving all 9,379 authoritative regional candidate values and adding 415 eastern-WA template-only rows. `scripts/build_full_id_candidates.py` reads only template GEOID/region metadata, validates exact row order and finite bounded outputs, and applies candidate-specific pooled fills to eastern-WA because no inputs for that region exist. The legacy score placeholders, labels, removed references, and external data were not read or used. The full audit explicitly keeps the official scored-region count at 9,379 and makes no accuracy claim for eastern-WA.
- Reproduction/validation: `python3 scripts/build_full_id_candidates.py --workspace "$COMPETITION_WORKSPACE"` — `PASS rebuilt C0-C3: exact 9,794 template GEOIDs once; 9,379 official rows preserved; 415 eastern-WA rows documented; all score outputs finite and bounded`. Repeated execution produced byte-identical audit, manifest, and candidate CSVs. `python3 -m py_compile scripts/build_full_id_candidates.py scripts/submit_zindi.py scripts/estimate_gaps.py`; estimator invariant tests `1 passed`; `git diff --check` passed. Audit SHA-256 `08d867aa5054d29ef62b2e05d7845151f8457525e083ce2d0ed4e0b291b71e3c`; manifest SHA-256 `3e51dd1e12ecc5bfaba4a47eb2cc3470ab47f695062dcc2b1f1dec1e4c45b974`. Candidate SHA-256: C0 `0edf8140123e2135489e5728fd71bce6f1e28a97f447ca411ba45fdbc02bc214`; C1 `dc2e368194b31408cb5c313fa8ce3f3fa59a1b5965bc2df8e4217bbc9e0c82e0`; C2 `5728fbbaa2e062fbd14199190a260132c95b2b858da539d7a9580cb2abc62514`; C3 `e5ee50f5c2cca149550f3408638d2b77431f6ed62cf104dfe8c07d8c8416f9e4`.
- Live probes C0–C3 were sent only via `$ORCHESTRATOR_ROOT/scripts/zindi_submit.py`, after shared-tool dry-runs and frozen manifest checks; all four statuses became `successful` and scores were refreshed from `--status` into `reports/submissions.jsonl`. Public RMSE and IDs: C0 `uRRw9KpB` 0.107246299; C1 `BvyKrR6v` 0.146449112; C2 `rTARuUaG` 0.235358712; C3 `TM83xxU6` 0.349409867. The older C0 `ggVY44Ui` remains a processing failure; its 0.0 placeholder is not a score. C0 has the lowest public RMSE among these four; private performance remains unknown, and no final-two choice is made. All four scores and ledger records were committed; latest ledger commit at this checkpoint is `7455057`.

## 2026-10-24 — phase-2 score algebra and calibration probes

- Item: `bias-bounty-lb-phase2-calibration` (P0). Added `scripts/lb_score_algebra.py` and `reports/lb-score-algebra.json`, deriving `E[py]` from committed phase-1 public RMSEs and `E[p²]` over the 9,379 official rows under the explicitly stated public-representativeness assumption. Reproduced C1 `E[py]=0.009551697`, uncentred correlation 0.5226, and optimal global scale 0.328813643; C2 correlation 0.1786; C3 `E[py]=-0.016690312` and correlation -0.5601, indicating the x1.25 denominator direction is harmful. No labels or removed references were used.
- Pre-registered P4–P6 in the manifest and probe plan before upload, three of four phase-2 slots: P4 alpha*C1, predicted RMSE 0.091438725; P5 range-constrained regional mean/residual transform, predicted 0.088921961; P6 constant c=0.056526890, assumption-based predicted 0.091139889. P4 `ySXi73rd` scored successfully at 0.091440198, only +0.000001473 from its forecast. P5 live attempt was rejected HTTP 400 with the platform message that only five submissions/day are allowed and to retry in about 14 hours; it has no submission ID or score. P6 passed shared-tool dry-run validation but was not sent live. No further uploads were attempted after this stricter platform limit; P5/P6 remain deferred until the platform permits them. The failure and actual-vs-predicted data are recorded in the ledger and algebra report.
- Also added two unregistered offline v1 sensitivities under `reports/estimator-v1/`: shrink building/POI gaps by alpha 0.328813643 while keeping transport unchanged (SHA-256 `9649f519765869ece13a42e2a977b1910bf90a751eecaf73bb853977126ee0da`) and recompute with denominator multiplier 0.75 (SHA-256 `8abfc98fdcab5c90d463a7f723e49f0b8c633cd713b3ffc9f60fe279c3604623`). Audit: `reports/estimator-v1-audit.json`. These are not preregistered or uploaded.
- Validation: phase-2 and offline-v1 scripts passed; repeated candidate/report builds were byte-identical; estimator invariant pytest `1 passed`; `py_compile` and `git diff --check` passed. All predictions remain aggregate calibration hypotheses; no accuracy claim is made for the private split or eastern-WA template rows.

## 2026-10-24 — Camp Fire stale-reference discovery

- Item: `bias-disc-campfire-stale-reference` (P0). Added `scripts/discovery_postfire_stale_reference.py`, `reports/discovery-postfire.json`, `reports/discovery-postfire-map.png`, and a top Best Bias Discovery section in `docs/writeup/bias-discovery.md` with Impact/Novelty/Evidence/Reproducibility. This uses only the official NorCal sample GEOIDs, provided Overture building summary, provided USFS WRC/MTBS strata fields, and challenge-provided ACS tract geometry. No external DINS data, labels, removed organiser references, score placeholders, or scored candidate edits were used.
- Results: Overture/provided-USFS building ratios for the six 2018-burn Butte/Paradise-Magalia focus GEOIDs are 0.620, 0.643, 0.662, 0.677, 0.720, and 0.747; Carr/Mendocino examples are 0.691/0.798. Among valid 0–100% burn bins, n and share below ratio 0.8 are 449/1.56% unburned, 28/0%, 32/0%, 32/0%, and 36/11.11% for 50–100% burned. Fourteen tracts with supplied burn percentage >100 are excluded from the bins and reported separately; no missing burn values. High-burn minus unburned difference is +9.55 percentage points, stratified bootstrap 95% CI [+0.55,+20.89] points. Butte has 54 tracts and six below the ratio cutoff; their provided `pop_total` sums to 6,977, not a measured affected-person count.
- Interpretation is a source-staleness hypothesis, not hidden-target evidence: the report explicitly does not claim the organiser used a 2018 Microsoft release or the provided USFS field. Emergency-planning consequences are stated as plausible risks only. Figure maps provided MTBS burn percentage and ratio flags by supplied tract geometry.
- Reproduction: `MPLCONFIGDIR=/tmp/zindi-bias-bounty-mplconfig /home/martin/hddProjects/langywrap/uv run --with pyarrow --with numpy --with matplotlib --with shapely --with pyproj python scripts/discovery_postfire_stale_reference.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-postfire.json --map reports/discovery-postfire-map.png` — `PASS post-fire audit: NorCal 591 official tracts; burn bins [449, 28, 32, 32, 36]; 50-100% below-0.8 share 4/36; unburned 7/449; Butte 6/54 population sum 6,977`. Difference 0.095521; CI `[0.005506062855728779, 0.2088591932689928]`. Repeated run produced byte-identical JSON and PNG; `py_compile` passed; estimator pytest `1 passed`; `git diff --check` passed. SHA-256: script `1a6e9582bf62f201f7b3a11758be636e3d064987274ec171edf40c1add65352f`; report `cbf100bc833bf09c02bad9f3c11a53d0da0736a7a249018c1ecc8dce55f3d49a`; map `916589e0d3dc765a77c133dbcfe454740906dabb26ab74fcc0db92f9ffa43da1`.

## 2026-10-24 — estimator v2 assembly probe gated on prerequisites

- Item: `bias-est-v2-assembly-probe` (P0). Blocked before implementation or upload because all three declared dependencies are still `open` in the orchestrator backlog: `bias-probe-region-means`, `bias-est-transport-route-ref`, and `bias-est-facility-miss-model`.
- Repository evidence: current candidate manifest contains only C0–C3/P4–P6; there is no `scripts/build_region_probes.py`, region-mean calibration, route-reference audit/cache, facility-miss POI implementation/audit, v2 candidate, v2 audit, or Phase 4 preregistration. `reports/lb-score-algebra.json` has only phase-1/phase-2 moments, so no measured `m_r` exists for the requested affine shrink. Building and registering a v2 candidate now would therefore invent missing mechanisms/means and violate the required preregistration.
- No candidate was assembled or uploaded, and no scored CSV or manifest was changed. Snapshot and baseline hashes are recorded in `reports/estimator-v2-prerequisite-gate.json` (SHA-256 `196563ca8373cc7897614792b5a1eb5cdff210381516ff83bbcce3fbe35ba6c9`). Resume only after all three dependency outputs are complete; then derive beta, preregister no more than two hashed forecasts in Phase 4, validate the 9,794 template / 9,379 official rows, and use only the shared orchestrator for any authorized upload.

## 2026-10-24 — region-mean probes preregistered; live probes deferred

- Item: `bias-probe-region-means` (P0). Added `scripts/build_region_probes.py`, four exact-template indicator candidates `reports/candidates/R-*_region_indicator.csv`, a five-probe Phase 3 registration in `reports/candidates.json` and `reports/lb-score-algebra.json`, plus `reports/region-probe-preregistration.json` and the Phase 3 section in `docs/lb-probe-plan.md`. The already-frozen P6 candidate is reused; R-AZ intentionally contains only `GEOID,coverage_gap_score` to test component-column optionality. Region indicators use `c_r=alpha*mean_r(C1)` and zero-fill the 415 eastern-WA template-only rows. No labels, removed layers, or template score placeholders were used.
- Forecasts were frozen before any live upload. Under the explicit prior `E[y|r]=P6 c` and representative public composition, official-scope predicted RMSEs are P6 0.091139889, R-AZ 0.104739325, R-CA 0.106313916, R-OK 0.105530049, R-TX 0.097245977. Alternative 9,794-row forecasts are separately recorded. These are assumptions, not target measurements; no inferred region means or actual-vs-predicted scores are yet available.
- Shared orchestrator dry-run command `python3 -I "$ORCHESTRATOR_ROOT/scripts/zindi_submit.py" --competition bias-bounty-mapping-equity-challenge --repo "$COMPETITION_REPO" --candidate <P6|R-AZ|R-CA|R-OK|R-TX>` passed for all five frozen artifacts. Every result reported `dry_run_validated`, `submissions_today_before=5`, `daily_cap=5`. The observed daily cap is exhausted (consistent with the prior P5 rejection); no live upload was attempted. Defer the five live probes until the limit resets, then upload only these preregistered artifacts through the shared orchestrator and refresh each score via `--status`.
- Reproduction/profile: `python -m py_compile scripts/build_region_probes.py`; `/usr/bin/time -f 'ELAPSED %e sec; MAX_RSS %M KB' uv run python scripts/build_region_probes.py --workspace "$COMPETITION_WORKSPACE"` — `PASS region probe preregistration: 9,794 exact template IDs; 9,379 official IDs; 415 WA rows zero`; elapsed `0.46 sec`, peak RSS `30,004 KB`. A second run produced byte-identical candidates, manifest, algebra, and audit. All five orchestrator dry-runs passed. No live submission or candidate edit outside the preregistered region artifacts was made.

## 2026-10-24 — subset-probe score algebra

- Item: `bias-probe-subset-engine` (P0). Added `scripts/lb_subset_algebra.py`, `tests/test_lb_subset_algebra.py`, `reports/region-subset-cell-definitions.json`, and `reports/lb-subset-moments.json`. Added `How a subset probe is read` to `docs/lb-probe-plan.md`, including the preregistration template, 250-official-row minimum, no-per-GEOID rule, and conditional composition bound. The tool reads only GEOID/region from the template and GEOID from official samples; it uses only candidate prediction CSVs and shared `score_refresh` rows, with no sample score placeholders, labels, removed references, or external data.
- The tool recomputed phase-1 C0–C3 moments and the phase-2 residual moments with exact equality to `reports/lb-score-algebra.json`; canonical digests match (`phase1` `00188596ec8a631c57c932421295cedc874749821abdc37e898c7067126b47e1`; P5 residual `4fc600b10ce09ba37eb56056e9167aacf6940033db7e3bb87fe158597912eed3`). It also measured P4's candidate moment from the actual `--status` score. The tool ran against all four preregistered region cells; they correctly remain `awaiting_public_score`, with `n=1593,591,1192,6003` and conditional eps=0.10 composition bounds `0.000549223210852`, `0.000159373908116`, `0.000245182940763`, `0.00182430327384`. These are sensitivity bounds, not measured public-share guarantees.
- Validation/reproduction: `uv run python -m py_compile scripts/lb_subset_algebra.py`; `uv run python scripts/lb_subset_algebra.py --workspace "$COMPETITION_WORKSPACE" --cells reports/region-subset-cell-definitions.json --out reports/lb-subset-moments.json` — `PASS subset algebra: legacy phase-1/2 moments exact; 4 cells checked; no uploads`; repeated report was byte-identical. `uv run --with pytest --with numpy --with pyarrow --with shapely --with pyproj pytest -q` — `7 passed`; `git diff --check` passed. No submission was attempted, as this row forbids uploads. Every subsequent subset-probe row must register cell/base/delta and use this tool after public score refresh.

## 2026-10-24 — cold code-review reproduction package

- Item: `bias-repro-code-review-package` (P0). Added `scripts/reproduce.sh` and its public-input fetch/build/report helpers, pinned `requirements.txt` and `.python-version`, `config/calibration.json` (sole LB-calibrated constant source), `config/reproduction-inputs.json`, a score-free GEOID/region template projection, a README Reproduce section, and `reports/reproduce-run.json`. Current review pair is C0/P4, the two best successfully scored candidates as of 2026-10-24; the report and README explicitly say this is not yet a final private-LB selection.
- The one-command pipeline fetches hash-pinned official sample IDs and strata from the anonymous public Source Cooperative HTTPS bucket, rebuilds Overture/ACS features with anonymous S3 access and forced cache regeneration, and reconstructs both 9,794-row files. The sample parser now accesses only GEOID; the 415 eastern-WA template IDs come from the committed GEOID/region-only projection and receive documented format fills. No labels, score placeholders, credentials/tokens, paid APIs, private packages, or uploads were used.
- Cold-run command: `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" REPRO_WORKSPACE="$COMPETITION_WORKSPACE/repro-code-review" UV_BIN=/home/martin/hddProjects/langywrap/uv scripts/reproduce.sh`. Output: C0 SHA-256 `0edf8140123e2135489e5728fd71bce6f1e28a97f447ca411ba45fdbc02bc214`; P4 SHA-256 `8b329802d882ea4bdff2bfd8c465c160d6ffd9269cfbe28dce8be9e702b07d37`; `PASS cold candidate reproduction: C0/P4 hashes match; official=9379; template=9794`. The empty reproduction workspace forced cold feature-cache builds; `/usr/bin/time -v` recorded wall time `11:17.13` and peak RSS `364,616 KB` in `reports/reproduce-run.json`.
- Validation: serializer pytest confirmed both frozen hashes; full suite `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest pytest -q` — `8 passed`; all new scripts passed `py_compile`; `git diff --check` passed. No submission was attempted. Under the later model-first directive, information probes are cancelled; a future finished model-candidate evaluation may update the private-selection pair only after the active policy gates pass.

## 2026-10-24 — model-first submission policy; information probes retired

- Item: `bias-model-first-plan-rewrite` (P0). Replaced the active policy at `docs/submission-policy.md`; archived the former `docs/lb-probe-plan.md` verbatim as `docs/lb-probe-plan-archive-2026-10-24.md` (archive SHA-256 `c31e62d1468c72ceede930005b0ee9d0a0896f5310ccbc1690cd27d1f5819be1`) and left a pointer at the old path. Updated README, competition rule summary, and plan to refer to the model-first policy.
- Cancelled the P5/P6 and R-AZ/R-CA/R-OK/R-TX information-probe registrations in `reports/candidates.json` by setting `pre_registered=false`, while retaining each artifact/hash and all scored-history files. `reports/probe-retirement-2026-10-24.json` records the cancellation. `reports/submissions.jsonl` remains byte-identical (SHA-256 `4bcb1ea00f5eaf71972a54385ff739043277eda7ccea25270ea1421608eb6650`); `reports/lb-score-algebra.json` and scored artifacts are untouched. P4's LB-derived alpha remains only for historical P4 reproduction; v2 and future models must use model-derived levels.
- Re-ranked open backlog: core estimator/model rows (transport, POI category/facility miss, building/establishment levels, road variants, edge cases) are P0; secondary provided-feature models/v2 build and final-two process are P1; writeup/discovery/literature/scorecard rows are P2. Revised the v2 and final-two rows to remove probe-derived dependencies and model-only evaluation gates; closed the region-mean/detail LB blend row as `superseded`. No information-probe uploads are queued.
- Validation: `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest pytest -q` — `8 passed`; manifest/report JSON parsed; checks confirmed all six cancelled candidate registrations are disabled, C0/P4 hashes still match, and the submission-history SHA is unchanged; `git diff --check` passed. No upload was attempted.

## 2026-10-24 — Overture POI category/source/confidence audit

- Item: `bias-est-poi-category-audit` (P0). Added `scripts/audit_poi_categories.py`, `config/poi-category-definitions.json`, `reports/poi-category-audit.json`, tests, and the POI numerator section in `docs/writeup/methodology.md`. The 75-category rectangular frequency table records per-region `primary` and alternate occurrences, categories seen, name-regex hits outside selected sets, overlapping `sources.dataset` counts, confidence q10/q50/q90, selected-facility confidence `<0.5`, and zero-tract sensitivity for exact vs broad category choices.
- Selected definitions retain the exact fire (`fire_department`, `fire_protection_service`), EMS (`ambulance_and_ems_services`), and ten canonical school categories already used by v0; hospitals remain excluded. The results show broad fire/rescue and school/education regexes sharply reduce zero-tract shares by adding unrelated classes; `emergency_medicine` and `day_care_preschool` alternatives shift EMS/school zero shares by under about 1 percentage point and remain excluded. `scripts/estimate_gaps.py` now loads the selected sets from the shared config; the test suite verifies that alignment. No LB feedback, target labels, removed layers, or external facility lists influenced the choice.
- Public data read: 1,924,646 provided POI rows; 1,924,643 assigned to official sample tracts and three Texas centers unmatched/outside. POI Parquet projected `bbox` (spatial assignment only), `categories`, `sources`, `confidence`, and `names`; it did not read the Overture `geometry` column or a full object. Provided ACS GEOID/tract geometry was used only for exact tract assignment required by the zero-share sensitivity. Score placeholder cells were not used. The report lists per-column compressed bytes (132,133,741 total POI selected-column bytes) and source URIs. Report SHA-256 `cd48fa40d0fb1b3cdd1e431734acb646791916df02ee021a60243020b17d9de8`; audit script SHA-256 `56682d59d20868895007e5db2c7d669425e6b2fe886f2823cf7788a24b35a514`; category config SHA-256 `8309a0bb08613b92621f4977eb78998e469951c0cd0ed012d93ea2f5cf59654f`.
- Reproduction: `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --with pyarrow==26.0.0 --with numpy==2.5.4 --with shapely==2.2.0 python scripts/audit_poi_categories.py --workspace "$COMPETITION_WORKSPACE" --out reports/poi-category-audit.json`. Validation: full suite `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest pytest -q` — `14 passed`; `py_compile`, report JSON parse/schema assertions, and `git diff --check` passed. No leaderboard information probe or submission was made.

## 2026-10-24 — POI confidence by SVI and urbanity

- Item: `bias-disc-confidence-by-stratum` (P2). Added `scripts/discovery_confidence_by_stratum.py`, `tests/test_discovery_confidence_by_stratum.py`, `reports/confidence-by-stratum.json`, and a concise interpretation in `docs/writeup/methodology.md`. The script also creates the per-tract confidence quantile feature cache outside the repo at `$COMPETITION_WORKSPACE/gap-estimator/features/poi-confidence-by-tract.parquet` (9,379 official GEOIDs; q10/q25/q50/q75/q90, low-confidence count/share, POI count, SVI quartile, and urbanity).
- The report has four-region `svi_overall` quartile × urban/rural place-weighted shares for `confidence < 0.5`, 95% tract-cluster bootstrap CIs (10,000 seeded replicates per cell), q4−q1 contrasts, all-place confidence quantiles, missingness, and input/column/byte manifests. Regional q10/q25/median are Maricopa 0.502/0.768/0.920, NorCal 0.489/0.770/0.920, Eastern OK 0.503/0.770/0.920, and South-Central TX 0.402/0.750/0.920. Q4−Q1 low-confidence-share differences are negative in seven of eight region × urbanity cells (−0.8 to −6.1 pp); NorCal rural is +0.5 pp with a CI spanning zero. This is the opposite of the initial hypothesized direction. It is descriptive only: confidence is not evidence of freshness/correctness or a Facebook-source signal, and the CIs do not model spatial dependence or multiple comparisons.
- Inputs: official sample GEOID cells only (no sample-score placeholders), local provided strata columns `GEOID,svi_overall,pct_urban,svi_covered`, provided Overture `bbox,confidence` columns, and provided ACS tract geometry only for assigning bbox centers. No Overture geometry, labels, removed reference layers, external datasets, or target scores were read. All 1,924,646 POI rows were scanned with projected columns; 1,924,643 assigned to official sample tracts and three Texas rows unmatched. No candidate/submission artifact changed.
- Reproduction: `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --with pyarrow==26.0.0 --with numpy==2.5.4 --with shapely==2.2.0 python scripts/discovery_confidence_by_stratum.py --workspace "$COMPETITION_WORKSPACE" --out reports/confidence-by-stratum.json`. A second full run produced byte-identical report JSON and cache. Report SHA-256 `ac759312e2b3e1384078c2d46dc386a9c2a2905ae5c6366d1247c13ee6916053`; script SHA-256 `35a30e0a87ad016d3e936be4437084cffc91f708ef5c6692886c38805026d9a1`; workspace cache SHA-256 `66eb8f093c8e02395cdab7e675b00bf304209da34362774c94c750ce6bf8b3cb`. Validation: `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest pytest -q` — `17 passed`; `py_compile`, report/cache hash/schema assertions, repeated-run `cmp`, and `git diff --check` passed. No leaderboard probe or submission was made.

## 2026-10-24 — Eastern Oklahoma rural tribal × drought POI screen

- Item: `bias-disc-ems-tribal-drought-ok` (P2). Added `scripts/discovery_ok_tribal_ems.py`, its tests, and `reports/discovery-ok-tribal-ems.json`. The analysis uses official Eastern OK GEOIDs; provided `pct_urban`, `tribal_any`, `pop_total`, and `usdm_summer_dsci`; and the cached Overture fire/EMS counts under the shared exact category config. No reference targets, sample score values, external data, or unstratified all-tract tribal comparison are used.
- Rural is `pct_urban <0.5`; the DSCI q75 threshold is computed over all 1,192 official Eastern OK tracts (`86.3364`), ties included in the upper group. In the rural upper-DSCI cell there are 85 tribal-overlap and 27 other tracts. The primary EMS-zero share is 37/85 (43.5%) vs 10/27 (37.0%), tribal-minus-nontribal +6.49 pp, Newcombe-Wilson 95% CI [−14.76, +25.27] pp. It spans zero, so no discovery/writeup section was added. The secondary fire-zero difference is −18.04 pp [−37.85, −0.74] pp (fewer zero-fire Overture counts in the tribal-overlap group); it runs opposite to the hypothesized deficit and is one of several exploratory endpoints, so it is not promoted as a service-gap finding. The report provides all rural/high-DSCI/lower-DSCI endpoint counts and intervals.
- The report names 12 rural upper-DSCI tracts with `pop_total >2000` and zero selected fire and EMS POIs (9 `tribal_any`, 3 other). It frames map-based dispatch/geocoding implications only as a local-validation hypothesis; no volunteer-agency roster, service ground truth, or dispatch outcomes were available. Overture category absence does not prove the station/service is absent. No broad unadjusted tribal/nontribal comparison is presented.
- Validation/reproduction: `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --with pyarrow==26.0.0 --with numpy==2.5.4 --with shapely==2.2.0 python scripts/discovery_ok_tribal_ems.py --workspace "$COMPETITION_WORKSPACE" --out reports/discovery-ok-tribal-ems.json`. Full suite `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest pytest -q` — `21 passed`; `py_compile`, report JSON assertions, deterministic repeat (`cmp`), and `git diff --check` passed. Report SHA-256 `42c85ca32d5ea1e7ef8507b0b1a7df8d6b570b0d002c8590dacc3965135331df`; script SHA-256 `099d4015cf0f902b76cd7e0144f30552fb97aba33ede0dbb59122be1c611f882`; test SHA-256 `1e98c3e53fad218f54c0830c59e588ef6ebb57315bdc16f8d6d36f365908bfef`. No candidate or submission changed; no leaderboard probe was made.

## 2026-10-25 — v2 defined-component mask and composite arithmetic audit

- Item: `bias-v2-defined-mask-audit` (P0, validation screen). Added `scripts/audit_v2_component_masks.py`, the frozen declaration `config/v2-component-mask-audit-preregistered.json`, `tests/test_audit_v2_component_masks.py` (10 tests) and `reports/v2-defined-mask-audit.json` (SHA-256 `fea33ef7daeeeac2fd187803481e050085698afd6f44ed182e9c5af5028054d2`). Nothing is promoted and no upload is attempted; this checks that the frozen model-only v2 composite honours each model-defined flag and averages defined components only.
- MEASURE-BEFORE: the v2 component artifacts existed (`road_topology_candidate.csv` route-reference transport, `building_morphology.csv` fixed 0.01 building prior, `poi_confidence_weighted.csv` raw v2 POI halves) but no consolidated per-tract mask/composite audit existed anywhere in the repo. MEASURE-AFTER over all 9,379 official scored GEOIDs (Maricopa 1,593, Northern CA 591, Eastern OK 1,192, South-Central TX 6,003; 100% exact ID coverage, zero duplicates, zero missing, zero extra, and every artifact's row order equals the official sample order).
- Mask checks: every component's undefined rows carry the documented numeric placeholder exactly — `undefined_rows_with_nonzero_placeholder = 0` for transport (2,644 undefined), building (2), POI (0), the facility half (0), the establishments half (2) and each fire/EMS/schools family; zero defined-flag mismatches against an independent recomputation of each frozen rule, and zero emitted values outside [0, 1]. Defined shares: transport 6,735/9,379 (71.81%), building 9,377/9,379 (99.98%), POI 9,379/9,379.
- Per-region defined-mask pattern counts (transport|building|POI): Maricopa `011` 901 / `111` 692; Northern CA `011` 182 / `111` 409; Eastern OK `011` 286 / `111` 906; South-Central TX `001` 2 / `011` 1,273 / `111` 4,728 — i.e. 6,735 all-defined, 2,642 transport-undefined and 2 POI-only tracts, with no other pattern present. POI half masks: `11` 9,377, `10` (facility half only) 2; facility-family mask `111` for all 9,379 because the fixed logistic presence prior never returns probability exactly zero. **Zero-defined composite rows: 0**, so the unregistered zero-defined fallback rule stays unused; the audit fails closed rather than emitting a placeholder if such a row ever appears.
- Composite arithmetic (the row's metric): the composite emitted by the repository's own frozen combiner `estimate_gaps.composite_mean` versus the audit's independent float mean and an exact rational mean agrees to **1.1102230246251565e-16** maximum absolute difference (gate 1e-12), and versus the mean of independently re-derived component values to 1.849e-12, the floor set by the 12-significant-digit decimal round-trip of the component artifacts and their inputs (largest single-component re-derivation residual 5.61e-12 on `route_reference_transport_gap`; building exactly 0). Per-component exact-at-printed-precision matches, last-digit near-misses and floor violations are all reported; no floor violation anywhere.
- Candidate mode: with no assembled v2 candidate yet (`bias-est-v2-assembly-probe` open) the report records `candidate_score_csv = null`. The `--candidate` path was exercised on two throwaway files: a faithful would-be candidate passed with residuals 5.0e-13 (vs emitted mean) / 2.18e-12 (vs re-derived mean), and a single-tract 1e-6 tamper made the audit exit 1 with `candidate_vs_recomputed_residual_within_gate = false` and residuals 9.999995e-07, so the screen demonstrably fires. The same command then re-runs unchanged after v2 candidate generation.
- Command/profile: `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/audit_v2_component_masks.py --workspace "$COMPETITION_WORKSPACE" --out reports/v2-defined-mask-audit.json` — `PASS v2 mask/composite audit: exact official ID coverage, masks honoured, composite arithmetic within gate`; wall time 3.56 s, peak RSS 183,088 KB. Two further runs produced byte-identical reports (`cmp` clean, same SHA-256). Script SHA-256 `fd8a49cdd460a2bbb6e731244c0941d2a4c27f060ec8b54693c977d1bf0eeb9a`; config SHA-256 `58b993e5e279e222a1f25eba99aece761e4dc062fda2fed100a3bb143fec37f4`. Suite: `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest pytest -q` — `136 passed`.
- Data use: official sample files read at the GEOID cell only (csv.reader on the GEOID column index; score placeholder cells never read), provided strata columns `GEOID,pop_total,pop_urban,pct_urban,ALAND`, and the three frozen provided-feature component CSVs, each SHA-256-pinned in the config with a fail-closed check. No labels, no sample score placeholders, no removed reference layer or equivalent, no leaderboard-derived constant (`config/calibration.json` is never opened by this script; `cbp_k = 0.024` and the three presence priors are the model-derived constants registered in the config), no test-score tuning, no upload.

## 2026-10-25 — v2 fixed-prior uncertainty envelope (sensitivity screen)

- Item: `bias-v2-prior-envelope-audit` (P1, sensitivity screen only). Added `scripts/audit_v2_prior_envelope.py`, the frozen declaration `config/v2-prior-envelope-preregistered.json`, `tests/test_audit_v2_prior_envelope.py` (18 tests) and `reports/v2-prior-envelope.json` (SHA-256 `c50569cf0f36ec3695e59dce68012422edd3baa09399bf1afe35b434f07d3cfd`). No promotion, no ranking, no winner, no submission, no upload; it reuses the frozen rule implementations from `scripts/audit_v2_component_masks.py` so both audits share one source of truth.
- MEASURE-BEFORE: the repo had component-local sensitivity screens (`reports/establishments-sensitivity.json` varies only the v2 establishments half, `reports/estimator-sensitivity.json` is a v0 C0/C1 rate-normalization screen, `reports/candidate-stability-envelope.json` is candidate-ensemble stability) but no composite-level perturbation envelope over the mechanism-derived priors, and no per-region/stratum prior-shift table. MEASURE-AFTER: 31 preregistered variants (baseline, single-prior +-10% on the building prior 0.01, the establishments `cbp_k` 0.024 and each of the three facility presence priors, joint all-priors +-10%, and +-10% on each of the 9 presence-prior coefficients), each evaluated on all 9,379 official scored GEOIDs.
- Inputs and rule declared before outputs: the report's first block is `preregistration` (central priors, the two-sided +-10% multiplicative rule, the full variant grid, strata definitions, gates, prohibitions, input SHAs), verified against the pinned artifacts before any variant is computed. Perturbations touch priors only: every provided-feature input, every model-defined flag, and the transport component (which carries no prior, so its delta is exactly zero) are frozen, and the combiners stay equal-weight means over defined components. `p_variant = clip(p_central x factor, 0, 1)`, and because the family gap is `p x clip(1 - n/(n+1), 0, 1)` the emitted family gap is scaled by `p_variant / p_central`; that transfer rule is verified on the zero-count tracts where the emitted gap equals `p_central` exactly (920 family-rows checked, max residual 4.998e-13 at `04019980400`).
- Max per-tract delta vs the frozen central estimate: 0.033632 (`all_priors.x0.90`, tract `06067005509`) and 0.031202 (`all_priors.x1.10`); `establishments_cbp_k` 0.024991 (x0.90) / 0.022716 (x1.10); presence priors 0.010157 (schools), 0.003139 (fire), 0.001828 (EMS); coefficient variants peak at 0.007679 (`presence_fire.intercept.x0.90`); the building prior alone moves a tract at most 0.0005. The baseline (unperturbed) variant reproduces the frozen composite to 4.167e-13, the 12-significant-digit artifact floor.
- Per-tract envelope width across all 31 variants: p50 0.006506, p90 0.032894, p95 0.039864, p99 0.051882, max 0.062758, mean 0.013140; widest tract `06067005509` spans [0.081775, 0.144533]; global score bounds 0.004737 to 0.532138 around a central mean of 0.060501 (variant means 0.053994 to 0.067072). Stratum mean shifts: Maricopa 0.008536, Northern CA 0.006711, South-Central TX 0.006326, Eastern OK 0.005207; urban 0.007134 vs rural 0.004064; `tribal_any` true 0.004973 vs false 0.006732 (tribal-within-rural 0.004396, tribal-within-urban 0.005616). The prior uncertainty is therefore concentrated in the urban, higher-population establishments half, and tribal tracts are the *less* prior-sensitive group - transparent before any authorized evaluation, and no accuracy claim.
- Independent verification: a second implementation (own CSV/parquet readers, alternate sigmoid form, priors rebuilt from the strata, gaps rebuilt from the config constants, exact `Fraction` combiners) recomputed all 31 variants x 9,379 tracts and agrees on `score_mean` to 1.03e-15, on `max_abs_per_tract_delta` to 4.17e-13 and on every reported delta quantile to 4.17e-13. Negative controls all fire: a declared SHA that no longer matches its artifact aborts fail-closed; a coverage gate weakened to 9,378 fails `every_variant_has_all_official_geoids`; an unregistered +-50% factor added to the grid aborts with `uses factor 0.5 outside the registered grid`; and emptying the no-winner policy fails `no_winner_selected`.
- Acceptance (12 gates, all true in `acceptance`): every variant carries exactly the 9,379 official GEOIDs, all 290,749 variant scores finite and inside [0,1], definedness masks identical to the frozen masks in every variant (0 flag mismatches, 0 zero-defined rows), all 31 preregistered variants reported in grid order with no ranking and `winner_selected: false`, baseline within the registered 1e-11 gate, max delta and every stratum shift quantified, inputs SHA-verified, prior-transfer rule verified on zero-count rows. Command/profile: `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/audit_v2_prior_envelope.py --workspace "$COMPETITION_WORKSPACE" --out reports/v2-prior-envelope.json` -> `PASS v2 prior envelope: all preregistered variants reported, finite bounded scores, frozen masks preserved, no winner selected`; 13.44 s wall, peak RSS 182,640 KB; two further runs byte-identical (`cmp` clean, same SHA-256). Suite `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest pytest -q` -> `154 passed`. Config SHA-256 `7c0bc11cc60aea331ecfc445f6a6fd7c4652d99c390d60affab257c4f515667b`; script SHA-256 `79cf35e2d8eca858c8102b72a9ef160d91c42d278fcc515acd5e6dfe5be46daf`.
- Data use: official sample files read at the GEOID cell only, provided strata columns `GEOID,pop_total,pop_urban,pct_urban,ALAND,tribal_any`, and the three SHA-pinned provided-feature component CSVs. No central prior was changed from any leaderboard score, no labels, no sample score placeholders, no removed reference layer or equivalent, no per-GEOID tuning, no upload. With no assembled v2 candidate yet (`bias-est-v2-assembly-probe` open) the envelope is measured on the frozen components' own composite - the same arithmetic a candidate is emitted with - and reruns unchanged after candidate generation.

## 2026-10-25 — model-only v2 assembled into one frozen, pre-registered candidate (no upload)

- Item: `bias-est-v2-assembly-probe` (P0). Added `scripts/build_estimator_v2_candidates.py`, the frozen scored artifact `reports/candidates/V2_model.csv` (SHA-256 `3193ee78a64004f007829f1f9c12d4aa8d4f7c15136771e8da9108fdcea06f12`, 9,794 rows = the 9,379 official GEOIDs in authoritative order plus the 415 eastern-WA template-only rows at the documented `0.0` fill from `b750a35`), the manifest card `V2_model` with `pre_registered: true` plus the top-level `model_only_v2_preregistration` block in `reports/candidates.json`, `reports/estimator-v2-audit.json`, the Phase 4 section in `docs/lb-probe-plan.md`, the refreshed `reports/estimator-v2-prerequisite-gate.json` (was a stale `blocked` waiting on probe region means), and `tests/test_build_estimator_v2_candidates.py` (9 tests). Registered in `reports/final-two.json` `candidate_registrations` with `upload_authorized: false`. Card commit `5c48f4f`.
- Steward/operator framing applied: no region-mean or cell-derived level, no `beta`, no `alpha_c1 = 0.3288136428`, no `E[qy]/E[q]` target moment. The composite is the equal-weight mean of the model-defined components only - transport route-reference gap, the fixed `0.01` building residual prior, and the POI component (fixed presence priors plus explicit `cbp_k = 0.024`) - with undefined components excluded, never zero-filled, exactly as the frozen combiner in `scripts/estimate_gaps.py` emits them.
- MEASURE-BEFORE (historical, quoted as background only): C2 (v0 detail) inferred `E[py] = 0.0043961016`, uncentred correlation proxy `0.1785851663`, and the P5 unconstrained detail `beta = -0.2181321596`, i.e. v0 tract detail was anti-informative after region-mean removal. Public anchors: C0 `0.107246299`, C1 per-region constants `0.146449112`, C2 `0.235358712`, C3 `0.349409867`, P4 `0.091440198`. No consolidated v2 composite or pre-registered forecast existed before this turn.
- MEASURE-AFTER (local only): the frozen artifact reproduces the frozen composite with maximum arithmetic residual 1.110e-16 against three independent implementations of the defined-component mean; model moments mean 0.060500636, sd 0.061356742, detail sd about region means 0.060564487, per-region means Maricopa 0.075213014, Northern CA 0.057067596, Eastern OK 0.038996906, South-Central TX 0.061204377; defined rates transport 0.7180936, building 0.9997868, POI 1.0; zero rows with a non-zero undefined placeholder, zero zero-defined rows, no `nan`/`inf`/`None`, no value outside [0,1].
- Pre-registered forecast, written before any upload: point prediction **0.091357196 public RMSE**, from the candidate's own measured moments with the single target-side anchor `E[y^2] = 0.107246299^2` taken from the platform score of the already-scored all-zero candidate C0 (predicting zero makes RMSE^2 = E[y^2]) and an assumed centred correlation of 0.30 - the midpoint between the only measured v0-detail informativeness (0.1786) and this row's 0.35 informativeness target. The audit carries the full sensitivity grid (assumed target means 0.5x-1.5x the model mean x centred correlations 0, 0.1786, 0.25, 0.30, 0.35, 0.5). Pre-registered breakevens at the model's own level: centred correlation `0.0096` to tie the all-zero baseline, `0.298604` to beat P4, and negative against the region-constant candidate C1 (so any non-negative correlation beats C1). With the protocol's pairwise public sampling guard the candidate must land at or below `0.089003419` to be called better than P4. Whether v2 actually beats P4 or the region-constant candidate is **not measurable locally** - Bias Bounty ships no labels and no platform score exists for this candidate - so the audit states the rule instead of a result: if the actual public RMSE exceeds `0.107246299`, the v2 tract detail is not informative at its current level and we stop adding detail.
- No upload performed. `docs/final-two-protocol.md` queues zero uploads, requires operator authorization through `$ORCHESTRATOR_ROOT/scripts/zindi_submit.py`, and reserves the two remaining uploads for the final A/B cohort; this row authorizes no queued upload. Manifest and audit both record `uploaded: false`, `live_upload_attempted: false`, `max_uploads_preregistered: 1`, comment-if-authorized `model-only v2`, and a second upload to adjust weights is forbidden.
- Anti-probe gate (measured, not asserted): every score cell of all 9,794 emitted rows (39,176 cells) and every region mean is scanned for the retired leaderboard constants `alpha_c1 = 0.3288136427728255`, P5 betas `-0.04967993197091435` / `-0.2181321596482996` and the P6 constant `0.05652688955988315`; offenders = none. The candidate is also verified not to be constant within any region, so it is not a disguised level-only candidate. Negative controls all fire: a mismatched frozen-artifact SHA pin aborts with `frozen component artifact changed`, `--wa-fill 0.05652688955988315` and `--wa-fill 0.3288136427728255` both fail `no_retired_lb_constant_in_the_scored_file`, and `--wa-fill 1.7` aborts on the range guard.
- Acceptance (17 gates, all true): frozen-input SHAs verified (3 component artifacts, 4 official samples, platform template, 4 frozen-rule modules, 9 prerequisite reports), 9,379 official rows exact with 0 duplicate/missing/extra IDs and authoritative order, 9,794 template rows with exactly 415 eastern-WA format-only rows at the deterministic fill, composite equals the defined-component mean within 1e-12, equal model-derived weights, deterministic rebuild (identical SHA across runs and to a temp directory), schema columns, forecast preregistered before upload, no upload. Command/profile: `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/build_estimator_v2_candidates.py --workspace "$COMPETITION_WORKSPACE"` -> `PASS v2 assembly: frozen candidate hashed, forecast preregistered before upload, no LB-derived constant, no upload performed`; 2.11 s wall, peak RSS 158,780 KB. Suite `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest pytest -q` -> `163 passed`. Script SHA-256 `069a6256920779fd0b739107d35df7e0c851cba994dabaead2bde2881c4c8034`; card SHA-256 `4355abd28a3960cbdd01dd2531132a55f3e6e134bfca51619c9ad847116778e4`.
- Operator decision requested: authorize exactly one finished-model evaluation of `V2_model` (comment `model-only v2`) under the 2/day gate, or hold the two reserved uploads for the final-two cohort. Then record actual vs predicted RMSE and apply the Phase 4 rule, including the stop rule. Dependency closure used: route-ref, road-topology variants, POI category audit, facility-miss, building level, establishments level, edge-case policy, plus the v2 mask audit and prior envelope; probe-derived region means are explicitly not a dependency.

## 2026-10-25 — Maricopa route-reference ratio isolated: definition + coverage, not an implementation bug (band still failed)

- Item: `bias-maricopa-route-ratio-diagnostic` (P1, validation screen). Added `scripts/audit_maricopa_route_ratio.py` (SHA-256 `8fbcadb94245796be09523135fe16c7a20802fb5495354bb0109f312cf7ec589`, embedded in the report and verified to match the committed file), the report `reports/maricopa-route-ratio-diagnostic.json` (195,002 bytes, SHA-256 `7043b4c27af9928125b5d2df111ba4df414952c57910a7fa98330e60dbe54752`) and `tests/test_audit_maricopa_route_ratio.py` (7 tests).
- MEASURE-BEFORE: `reports/transport-route-ref-audit.json` (SHA-256 pinned inside the new report) recorded Maricopa `sum(L_named)/sum(L_route) = 1.928540323621719`, outside the preregistered 0.6-1.7 band, with eastern-ok 1.160856228, northern-ca 1.503515380 and south-central-tx 1.183016822 passing, and listed the excluded network systems but did not resolve whether any implementation/interpretation mismatch remained.
- MEASURE-AFTER: an independent re-implementation (own network classifier, own EPSG:5070 clip/accumulate loop, own aggregation; the frozen module is imported only to compare the rule constant sets) reproduces all four regional aggregates **bit-identically** to the frozen audit and to the frozen per-tract feature cache (max per-tract absolute difference 0.0 km, aggregate differences 0.0, tolerance 1e-9), with **0 unexplained rule/implementation mismatches**. The ratio is decomposed into the three disjoint length classes that determine it: for Maricopa A (named-class and route-qualified) 11,146.389 km, B (named-class, no kept network entry) 10,967.324 km, C (route-qualified, not named-class) 320.165 km, so ratio = (A+B)/(A+C) = 1.928540324 and the ratio exceeds 1 exactly because B exceeds C by 10,647.158 km. Identity verified to 1e-12; the sole-drop-reason partition of B is additive to 1e-9.
- Root cause (measured): 9,895.260 km - 90.22% of B - sits on named-class segments whose `routes` array is empty altogether, and only 1,072.063 km of B comes from *rejected* route entries (historic/business/scenic 497.6, county-name and other unsupported US namespaces 319.2, multiple reasons 178.0, missing network 76.9, non-route highway systems 0.2, malformed 0.1). The same no-route-entry share is 87.7-90.2% in all four regions; what separates the pass from the fail is B/C (Maricopa 34.26, northern-ca 10.47, eastern-ok 17.82, south-central-tx 2.05) against the size of A. Verdict: the discrepancy is a consequence of the documented definition (numerator counts arterial class unconditionally, denominator requires a kept `routes[].network`) plus provided-data route-reference coverage in the Phoenix grid - not a coding or interpretation error, and not a Maricopa-only data defect.
- Not resolved, explicitly: Maricopa stays outside the frozen band. Diagnostic counterfactuals (all `adopted: false`) show that reinstating any single rejected category cannot reach the band - the best case (reinstating historic/business/scenic) still gives 1.821230037 - because the dominant excess is segments with no route reference at all, which a route-reference denominator cannot absorb without abandoning the component's meaning. The band stays `[0.6, 1.7]`, `relaxed: false`, `maricopa_status: unresolved_out_of_band`, and no validation of the Maricopa transport proxy is claimed.
- Downstream consequence recorded (no candidate change): of the 692 defined Maricopa tracts, 679 (98.12%) have L_named >= L_route and therefore clip to transport gap 0.0 (northern-ca 95.60%, eastern-ok 96.03%, south-central-tx 82.70%), which is why the Maricopa transport component is near-degenerate. Any future change to the route denominator must be its own pre-registered candidate with its own hypothesis and operator authorization; this screen adopts nothing.
- Membership/dedup documentation: every distinct `routes[].network` string in the provided layer is classified with its family or drop reason, segment count, entry count and ref-presence count, and cross-checked against the frozen audit's frequency table (0 mismatches, 0 strings missing on either side; Maricopa 38 strings, others 35-100). Deduplication is documented and measured: 0 duplicate segment ids in all four regions (494,492 / 191,836 / 520,550 / 1,937,009 rows scanned), one contribution per segment row, family columns overlap by design (Maricopa interstate 3,661.0 + us 2,823.3 + state 5,977.7 + county 0.0 + fm_rm 0.0 = 12,462.0 km against deduplicated L_route 11,466.6 km) and are used only for attribution. Rule-set agreement with the frozen module is exact for all five constant sets, and the independent classifier agrees with `estimate_gaps.classify_route_network` on the *exact* (family, reason) tuple for all 46 synthetic cases plus every observed string.
- Acceptance (15 gates, all true): aggregates reproducible to 1e-9, independent scan reproduces the frozen cache to 1e-9, disjoint-class identity to 1e-12, additive sole-reason partition, every route network string classified, zero unexplained mismatches, deduplication documented, band evaluated and not relaxed, Maricopa status recorded, no per-tract GEOID written (measured by scanning the serialized report for quoted 11-digit GEOIDs), inputs restricted to the provided layers (measured against forbidden-token patterns: no TIGER/MS-buildings/USGS/CBP/label/leaderboard/credential path), official order matches the frozen cache.
- Negative controls all fire: doctored prior aggregates fail `aggregates_reproducible_to_1e-9`; dropping one membership token (`LOOP`) shifts the Maricopa ratio to 2.024492485, raises the mismatch count to 1 and fails three gates at once; widening the band to `[0.6, 2.0]` flips Maricopa to "pass" but is detectable and the committed report still carries `[0.6, 1.7]` with `unresolved_out_of_band`; a missing frozen input aborts with `FileNotFoundError` before any measurement.
- Command/profile: `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/audit_maricopa_route_ratio.py` -> `PASS maricopa route-ratio diagnostic: aggregates reproduced to 1e-9, every route class documented, zero unexplained rule/implementation mismatches`; 233.38 s wall for all four regions (per-region roads scans 25.3 / 22.0 / 25.8 / 118.1 s), peak RSS 280,120 KB on the single-region profile. Two independent full runs are byte-identical. Suite `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest pytest -q` -> `170 passed`. FORBID respected: no removed reference layer or equivalent read, no labels, official sample read for the `GEOID` column only, no leaderboard input, no external roads added to any scored prediction, no per-tract target estimate, no upload, no secrets.

## 2026-10-25 — Overture source-update freshness screen: metadata is usable, 92.7% explicit instants, adjustment orthogonal to source mixture

- Item: `bias-model-source-freshness-candidate` (P1, intent screen only). Added `config/source-freshness-preregistered.json` (SHA-256 `e1b78219a53149a197f5383cef097ee3470dc07e25f616689569641548ab0cb2`), `scripts/build_source_freshness_candidate.py` (SHA-256 `ba930ee108d11a3b37fd240b1f2af41d6d8c6e90cf7b78fbc26c986e3b1f2635`), `reports/source-freshness-candidate.json` (SHA-256 `4a07aae2d0d7008f55661485114e7985717fd0b42dc34e2d7ca1a7484ee62610`), the analysis-only candidate `reports/candidates/source_freshness_candidate.csv` (SHA-256 `3c5cf30a8867676405bc6cdc2689e17d910c314e5e8db39606d5560b1e19cecf`, 9,379 rows) plus its manifest, and `tests/test_build_source_freshness_candidate.py` (22 tests; full suite `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest pytest -q` -> `192 passed`).
- MEASURE-BEFORE: the frozen source-mix feature schema (19 `overture_*_source_share` columns in `gap-estimator/features/<region>-overture-tract-summary.parquet`) is 100% finite in three regions and 99.967% in south-central-tx (exactly the two tracts with no features at all); `reports/building-source-mix.json` (SHA-256 pinned in the new report) described provenance but said nothing about time metadata. The provided `sources` struct is `list<struct<property, dataset, license, record_id, update_time, confidence, between, provider, resource, version>>` and only `dataset`, `update_time` and `version` were read, together with `bbox` for tract assignment.
- MEASURE-AFTER: 23,156,161 assigned features (buildings 18,088,441, roads 3,143,077, POIs 1,924,643) and 34,137,283 source entries, every one carrying exactly one declared timestamp status and one declared version status - closed-set accounting 34,137,283/34,137,283 = 1.000000 against the required 0.95. Timestamp statuses: `parsed_utc_z` 92.266%, `parsed_utc_offset` 0%, `naive_no_timezone` 0.408%, `missing_null` 7.326%, `missing_empty` 0%, `malformed` 0% (explicit-instant share 92.674%). Version metadata: `iso_date` 66.965%, `datetime_utc` 26.750%, `missing_null` 6.278%, `numeric` 0.0077% (the TomTom build number), `other` 0%. Zero primary timestamps lacked a timezone designator, in every region and theme.
- Reference epoch is derived from the provided metadata itself (the maximum explicitly-UTC `update_time` over assigned entries, `2026-08-14T19:46:07+00:00`), so no external clock, wall-clock read or external timestamp is involved and reruns are deterministic.
- Age distributions (entry-level days, p10/p50/p90/max): buildings Maricopa 790.7/1368.8/2887.3/9722.8, northern-ca 609.6/1573.8/2611.8, eastern-ok 368.8/1734.8/2703.8, south-central-tx 697.2/1608.8/3474.9; roads Maricopa 103.2/584.0/1630.1/6208.2, northern-ca 91.5/1197.2/4152.7, eastern-ok 176.2/1434.6/5973.3, south-central-tx 136.2/898.9/4704.5; POIs are dominated by the Overture wrapper refresh at p50 3.87 days in every region (max 6,855-6,872 days for the underlying upstream rows). Cumulative vintage at the frozen cutoffs 182/365/730/1095/1825 days: buildings 1.24/3.38/11.03/22.81/61.30%, roads 15.07/27.84/45.62/57.11/69.73%, POIs 93.46/95.31/96.79/97.23/97.88% - i.e. POI provenance metadata is days old while two thirds of building source records are more than five years old, which is exactly the stale-versus-recent contrast the row asked to isolate.
- Candidate: bounded fixed uncertainty adjustment `clip(0.05 * weighted_mean_theme(0.6 * stale_share + 0.4 * metadata_gap_share), 0, 1)` with frozen theme weights buildings 0.40 / roads 0.30 / pois 0.30 and a frozen 730-day stale cutoff. Distribution over the 9,377 defined tracts: mean 0.016803, sd 0.003155, min 0.003051, p50 0.017243, p95 0.021102, max 0.024821 (declared bound 0.05, never approached); region means 0.017682 / 0.016507 / 0.016342 / 0.016684. The two tracts with no feature in any theme (`48039990000`, `48057990000`, both south-central-tx) stay undefined at the documented placeholder and are never imputed from region means. A second arithmetic path in exact rationals agrees with the float path to 6.94e-18.
- Beyond source mixture (reported, not gated): OLS of the adjustment on the 19 frozen source-mixture shares gives R^2 0.2499 (residual-to-total sd 0.866, largest single-share |Pearson r| 0.346, design rank 11 of 20); adding per-theme log1p feature counts gives R^2 0.2984; counts alone give R^2 0.0791. So roughly three quarters of the adjustment variance is not carried by the source-mixture proportions, and virtually none by feature counts alone - the freshness term is largely new information rather than a restatement of provenance mix.
- Data-vintage sensitivity (every variant `adopted: false`): moving the stale cutoff to 365/1095/1825 days changes the mean to 0.019349/0.014214/0.008452 with Pearson r 0.712/0.676/0.301 against the primary and max absolute per-tract deltas 0.0158/0.0185/0.0206; the 730-day control run reproduces the primary exactly (r 1.0, delta 0). No cutoff, weight, status policy or bound was changed after seeing the distributions.
- Acceptance (16 gates, all true): every entry has exactly one timestamp status and one version status, closed-set accounting 1.0 >= 0.95, no timezone ambiguity in the primary path, 100% finite outputs inside [0,1] and inside the declared 0.05 bound, float/rational arithmetic agreement, undefined tracts on the documented placeholder, candidate rows equal the official 9,379-tract scored population in authoritative order with no region-mean imputation, ordered per-tract age quantiles, no target accuracy claim, no upload.
- Determinism: two independent full runs (1,120 s and 1,121 s wall, peak RSS 1.57/1.61 GB) produced a byte-identical candidate CSV (`3c5cf30a8867676405bc6cdc2689e17d910c314e5e8db39606d5560b1e19cecf`) and identical report measurement blocks; the only differing report fields are the declared output paths and the manifest hash of the alternate copy.
- Negative controls all fire: an undeclared timestamp status aborts with `KeyError: 'undeclared_status'`; reading naive timestamps as UTC produces 976 designator violations in the northern-ca POI stratum and flips `no_timezone_ambiguity_in_primary` to false with `all_passed` false; a missing workspace input aborts with `FileNotFoundError` before any measurement; a missing frozen source-mixture column aborts with `ValueError`; and inflating the bound to 0.6 is caught against the committed config (recorded bound 0.6 vs committed 0.05, and the CSV then exceeds the committed bound).
- Prohibitions respected: no historical OSM or reference edits pulled, no external timestamp source, no removed reference layer, source age never used as a target label, no leaderboard-tuned coefficient or probe, no sample score placeholder read (official sample read for the `GEOID` column only), no labels, no upload, and no candidate registered in `reports/candidates.json` - this is an intent screen, and the manifest records `analysis_only_not_a_submission_artifact: true` with `upload_performed: false`.

## 2026-10-25 — v2 input provenance audit: all 91 consumed fields trace to provided data or a documented fixed rule

- Item: `bias-v2-provided-input-provenance-audit` (P1, validation screen before any authorized candidate evaluation). Added `config/v2-input-provenance-preregistered.json` (SHA-256 `0c69997873eea0edbcfe935f8339aa0dbcfb819dd1f674ded5697b7efe882c41`), `scripts/audit_v2_input_provenance.py` (SHA-256 `0ae5494e1767e0fd437e2c311e7f5a7393701c7506914506dbc38e3236e6fc23`), `reports/v2-input-provenance.json` (SHA-256 `b52c3b1bcfee452e8d04c2af28a8c9abd1a0fb609b50c1ed9a9d56b9ec7f5d6a`) and the SHA-256 manifest tied to the candidate build, `reports/candidates/v2_input_provenance_manifest.json` (SHA-256 `26a21de53acbabda73e7e1d1acdcd61001b5979264db40174364a76a73ec88e4`, 46 inputs), plus `tests/test_audit_v2_input_provenance.py` (18 tests; full suite `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest pytest -q` -> `210 passed`).
- MEASURE-BEFORE: component audits (`reports/v2-defined-mask-audit.json` `fea33ef7…`, `reports/v2-prior-envelope.json` `c50569cf…`) and the writeup traceability row were separate efforts; the only earlier provenance artifacts were `reports/source-provenance.json`/`.png`, which document which Overture upstream datasets supplied features per tract and do not enumerate the scored candidate's input fields, hash its inputs, or scan its code dependencies. No consolidated executable input-source audit existed.
- MEASURE-AFTER, field lineage: 91 fields, all verified, `fraction_fields_with_manifest_entry = 1.0`, zero unclassified. Layers: 5 scored-artifact columns, 19 frozen component columns, 61 provided/derived source columns, 6 fixed rules. Provenance classes: 28 official hosted, 22 official local, 30 derived-from-allowed, 5 documented-public methodology, 5 reconciliation-control-only, 1 platform format metadata. The graph is *derived* from committed machine-readable pins (the v2 mask config `frozen_inputs`, `reports/candidates.json`, the three builder manifests) rather than typed by hand, and every edge is checked against the real file header or Parquet arrow schema it names.
- Local provided copies are byte-for-byte the organiser's objects: all four `samples/<region>-sample-submission.csv` and all four `defined-flags-inputs/<region>-strata-tract-table.parquet` hashes equal the hashes of the hosted `reference/...-sample-submission.csv` and `strata/...-strata-tract-table.parquet` objects (`byte_identical_to_hosted: true` in every case), and the consumed strata columns were re-read from the hosted objects and compared row by row: 1,593 / 591 / 1,192 / 6,010 tracts compared with `value_mismatches_vs_provided = 0`. `SampleSubmission.csv` ties to both the `reports/candidates.json` pin and the workspace `download-manifest.json` record (SHA-256 `32fb5c2984317d4abf8a98b88b7bb9d16e9eef1cda9a954c37da0a2cdd367f40`, `source_url` `https://api.zindi.world/v1/competitions/bias-bounty-mapping-equity-challenge/files/SampleSubmission.csv`).
- Hosted inputs are covered by hashes without bulk download: 16 provided objects (4 regions x `overture-roads`, `overture-buildings`, `overture-pois`, `census-acs-housing`; 3,143,887 / 18,088,443 / 1,924,646 / 9,386 rows) each carry a Parquet footer fingerprint over rows, row groups, schema and per-column compression/encodings, plus per-column compressed bytes for exactly the requested columns (for example Maricopa roads: `geometry` 29,036,826 B, `connectors` 20,231,561 B, `routes` 169,187 B, `class` 82,128 B). Every requested column was present in the arrow schema; `wanted_columns_missing` is empty for all 16.
- Provided-data-only rule enforced at column granularity, which is where it matters: the removed reference layers live as *columns* of the provided strata table, so the audit requires every read strata column to sit in the preregistered permitted subset (`GEOID, ALAND, pop_total, pop_urban, pct_urban, ur_class, tribal_any`) and outside 23 removed-reference column families (`tiger*, ms_*, usgs*, cbp*, usfs*, carbonplan*, mtbs*, nifc*, fod*, svi_*, cvi_*, ruca*, rucc*, nchs*, ghcn*, cdcw*, gehe*, uhe*, pmdi*, spi*, epht*, drought*, usdm*`). Both the declared `provided_strata.columns_read` and every module-level `STRATA_COLUMNS` constant found by AST in the chain (`['GEOID','pct_urban']`, `['GEOID','ALAND','pop_total','pop_urban','pct_urban','ur_class']`) satisfy this: zero violations.
- Code dependency scan: 8 chain scripts hashed and parsed. 34 column-projection requests were extracted from the AST (including module-level constants such as `ROAD_COLUMNS`, `BUILDING_COLUMNS`, `POI_COLUMNS`, `CONFIDENCE_CACHE_COLUMNS`) and all 34 are declared in committed builder manifests/audits, are columns of a hashed derived artifact, or sit in the permitted provided subset: `code_requested_but_undeclared = []`. No chain script imports a leaderboard or probe module (`prohibited_imports` empty everywhere; the v2 builder imports only stdlib plus `audit_v2_component_masks`, `estimate_gaps`, `estimator_v2_building`, `estimator_v2_establishments`).
- Prohibited-source dependency count is 0 under a preregistered classification rule: a token counts only when it sits in a string the script can hand to a file-access callable (`open`, `open_input_file`, `read_table`, `read_row_group`, `ParquetFile`, `read_text`, ...) or in a module-level path/URI constant. Six hits are disclosed as prose rather than dependencies: docstring mentions of TIGER/MS/USGS/CBP, the `cbp_k` parameter name that mirrors the organiser's establishments half, `labels` in an audit docstring, and the `reports/lb-score-algebra.json` citation in the v2 builder's historical-background block (measured as never opened).
- Retired leaderboard constants are isolated from the score path two ways: AST scan of the 21 declared score-path functions finds no reference to `alpha_c1_from_P4`, `beta_constrained_from_P5`, `beta_unconstrained_from_P5`, `global_constant_c_from_P6`, `c0_public_rmse`, `c1_public_rmse` or `p4_public_rmse` and no float literal equal to any of them; and a value scan of all 39,176 score cells plus the five region means in `reports/candidates/V2_model.csv` returns zero hits. The frozen composite was also re-derived independently: the defined-component mean recomputed from the three pinned component CSVs reproduces `coverage_gap_score` for all 9,794 rows with maximum absolute residual 0.0, with zero officially-defined rows carrying no defined component, and the 415 template-only rows at the documented `0.0` fill.
- Pins tie the audit to the build rather than to a parallel claim: the audited artifact hash equals both the `reports/candidates.json` card SHA-256 (`3193ee78…`) and the `reports/estimator-v2-audit.json` artifact hash; the build script hash equals the `reproducibility.script_sha256` pin `f1a21bd5854fcb97153e1fab9256b3a65dd7df21a15171a685ee2a4e9492b5dc`; and the commit of record `b45733aa31903c71ec4f3fb432c2bc9d519f3ff9` is read from that audit rather than from the live HEAD, so runs stay reproducible.
- Acceptance: 22 gates all true, including 100% field lineage, zero prohibited or unclassified sources, every listed input hashed, every local provided copy matched against its official object, strata reads inside the permitted subset, declared columns covering every code request, no prohibited module imported, retired constants absent from score path and score values, sample placeholder columns never read, composite equality, pin tie, downloader evidence, input hashes reproducible within the run, and no upload.
- Determinism: five runs (three to the repo path, two to alternate paths) produced byte-identical report and manifest (`b52c3b1b…`, `26a21de5…`); no wall-clock value is serialized and a test asserts no timestamp-like key ever appears.
- Command: `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/audit_v2_input_provenance.py` -> `PASS v2 input provenance: every consumed field traces to provided data or a documented fixed rule, zero prohibited or unclassified sources, every input hashed` (~30 s).
- Negative controls all fire with the exact expected gates: replacing one official score cell with the retired alpha `0.3288136427728255` fails `composite_equals_defined_component_mean`, `pins_tie_to_candidate_build` and `retired_constants_absent_from_score_values`; dropping `pct_urban` from the permitted subset fails `no_strata_column_outside_permitted_subset` and `strata_columns_inside_permitted_subset`; removing the provided POI layer from the hosted allowlist fails `no_prohibited_uri_allowed` and `field_lineage_complete`; declaring a really-opened path prohibited fails `prohibited_source_dependencies`; declaring `GEOID` a placeholder fails `sample_placeholder_columns_never_read`; and the unmutated run passes.
- FORBID respected: the audit reads no removed reference layer or equivalent copy (only the four allowlisted provided layers per region, verified by URI allowlist and footer schema), no sample score placeholder column (samples are read for `GEOID` only, template for `GEOID`/`region`), no labels, no leaderboard feedback, and nothing was added to scored predictions or uploaded; the candidate under audit is untouched and no upload was performed.

## 2026-10-25 — v2 strata descriptive audit: 25 provided strata, 113 groups, every official GEOID placed exactly once, contrasts reproduced to 1e-17

- Item: `bias-v2-strata-descriptive-audit` (P1, descriptive screen only). Added `config/v2-strata-descriptive-preregistered.json` (SHA-256 `ddcbf757c2a10a6c62c29f44722ea04036ddaac51a7527b1cbe52b121dff724c`), `scripts/audit_v2_strata_distribution.py` (SHA-256 `3c6bf9ddc0f47ed85bd126f8640b23c250009cff61dc6e95d1c5bbebf6b3170d`), `reports/v2-strata-descriptive-audit.json` (SHA-256 `2d117808b4cd58ccc9591ed1863c4d1d7994aaecf90d420defd88476ff7a7b4d`) and `tests/test_audit_v2_strata_distribution.py` (22 tests; full suite `uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest pytest -q` -> `232 passed`).
- Command: `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 python scripts/audit_v2_strata_distribution.py` -> `PASS v2 strata descriptive audit: every official GEOID placed exactly once per stratum from a non-null provided value, contrasts reproduced within tolerance, missing strata reported and never imputed` (15.39 s, 151,820 KB peak RSS).
- MEASURE-BEFORE: the four earlier strata reports analyse C2/C3-era predictors and provided-feature prevalence, not the frozen candidate — `reports/discovery-rural-svi-minority.json` (`da1a581d…`, rural x upper-quartile `svi_minority` in south-central-tx against C2-era estimates), `reports/discovery-strata-confound.json` (`ce0f1307…`, region composition of the tribal and rural strata, explicitly no target-score analysis), `reports/discovery-ok-tribal-ems.json` (`42c85ca3…`, eastern-ok fire/EMS POI-zero rates by tribal_any within rural and drought strata), `reports/confidence-by-stratum.json` (`ac759312…`, POI low-confidence share by SVI quartile x urbanity). The report records the measured fact that no prior v2 strata descriptive artifact existed: `existing_v2_strata_descriptive_reports: []`.
- MEASURE-AFTER: 25 predeclared strata across 9 families (urban_rural, population, tribal, svi, cvi, hazard_drought, hazard_wildfire, hazard_heat, hazard_flood) producing 113 groups, each with n, region counts, score mean (float and exact rational), min/max and p05/p25/p50/p75/p95 by the declared nearest-rank rule, per-component defined/undefined counts, gap mean over defined rows, mean defined components and zero-defined rows. Every group carries `is_missing_bucket`, and every stratum carries both `contrast_all_groups` and `contrast_excluding_missing`.
- Acceptance measured: 9,379 official GEOIDs are partitioned exactly once by every one of the 25 strata (`partition_is_exact_once` true, `partition_total = 9379`, `value + missing + undefined = 9379` per stratum); all 9,379 official candidate scores are finite and inside [0,1] (mean 0.06050063616206269, min 0.005262915381296667, max 0.528216780099, 9,374 distinct values); the 415 eastern-WA template rows are outside the official scope and all equal the documented `0.0` fill; 7 non-official strata rows (all south-central-tx, examples recorded: `48321990000, 48007990000, 48355990000, 48273990000, 48261990000, 48489990000, 48061990000`) are excluded and documented, not silently dropped; joins are 1,593 / 591 / 1,192 / 6,003 with zero duplicate official rows.
- Contrast reproduction: every max-minus-min group-mean contrast is computed twice, once in float64 and once in exact rationals over the emitted decimal text, and the worst absolute disagreement across all 50 contrast figures is `1.3877787807814457e-17`, far inside the predeclared `1e-12`.
- Descriptive patterns (not accuracy, not targets): tribal-land tracts (`tribal_any` true, n=858) average 0.03636 against 0.06293 for the 8,521 others; Rural tracts (n=1,712) average 0.05591 against 0.06161 for Urban; nonmetro RUCC 7-9 (n=462) 0.05071 against 0.06177 for metro 1-3; SVI overall is monotone decreasing in vulnerability, 0.07858 for SVI in [0,0.2) (n=1,450) down to 0.05286 for SVI >= 0.8 (n=2,667); the densest band (>=1,000 people/km2, n=4,829) averages 0.06930 against 0.03300 for the sparsest ([0,1), n=120); tracts with mapped MTBS wildfire history (n=798) average 0.04197 against 0.06222 without. Largest all-group contrast is 0.112428 on `population.density_band`, which the report attributes to the 2-row `density_undefined_land_area_zero` bucket (mean 0.145432, mean defined components 1.0 — the two `001`-mask tracts), and the largest contrast over real values only is 0.041499 on `svi.svi_housing_transport_band`.
- Missing-strata handling is explicit and never imputed: `uhe_wbgtmax28_days_per_yr` is null for 3,599 official tracts (38.37%), `fod_human_share` for 3,087 (32.91%), `svi_overall` for 85 (0.91%), `svi_housing_transport` for 84, `svi_minority` for 48, `ur_class`/`pct_urban` for 26, `cvi_overall` for 2, and density is undefined for the 2 tracts with `ALAND = 0`. Each falls into its own preregistered label; no mean fill, no zero fill, no dropped rows. A value that matches no declared band aborts the run rather than being dropped.
- Ties to the frozen build: the audited artifact hash equals both the `reports/candidates.json` card SHA-256 and the `reports/estimator-v2-audit.json` artifact hash, the build commit of record `b45733aa31903c71ec4f3fb432c2bc9d519f3ff9` is read from that audit, the three component artifacts hash-match their pins in `config/v2-component-mask-audit-preregistered.json`, the four official sample CSVs hash-match the mask-config pins with `columns_read: ["GEOID"]`, and the four provided strata tables hash-match the frozen input-provenance manifest.
- Component definedness cross-check: the observed top-level defined-mask pattern over official rows is exactly the frozen audit's (`111`: 6,735, `011`: 2,642, `001`: 2) with zero rows lacking a defined component, so the per-group definedness counts rest on the same frozen masks the v2 audits validated.
- Strata provenance and model separation are stated and measured: SVI, CVI, RUCA/RUCC, NCHS, tribal, USDM, USFS, MTBS, NIFC, FOD, GEHE, HWD/UHE, PMDI/SPI and CarbonPlan columns are organiser-provided equity/hazard strata inside `defined-flags-inputs/<region>-strata-tract-table.parquet` (documented column-by-column in `docs/data-inventory.md`), not the removed reference layers (TIGER roads, MS Buildings, USGS facilities, CBP establishments), which are absent from every provided file. Because the scored candidate may not read them, the report lists all 27 descriptive-only columns and measures zero occurrences of each in all eight model-chain scripts, while the frozen model's own strata reads stay inside the permitted seven-column subset (`GEOID, ALAND, pop_total, pop_urban, pct_urban, ur_class, tribal_any`).
- Forbidden-source scan: zero opened literals hit any label, public-score, accounting, calibration or credential token; this script opens no sample submission at all (official IDs come from the frozen `official_scored_ids` helper, whose declared read is `["GEOID"]` with `score_placeholders_read: false` and no placeholder literal in any file-access call); the only columns this audit reads from the artifact are its own emitted `coverage_gap_score`, `transport_gap`, `building_gap` and `poi_gap`.
- Acceptance: 17 gates all true (candidate SHA tie, component pins, official sample hashes, provided strata hashes, exact-once partition, one strata row per tract, finite unit-interval scores, contrasts reproduced within tolerance, missing strata reported never imputed, excluded rows documented, definedness tie, descriptive-only columns absent from the model chain, no forbidden source read, no accuracy claim, no upload, deterministic serialization, all_passed).
- Determinism: three runs (two to alternate paths, one to the repo path) produced byte-identical reports (`2d117808…`); no wall-clock value is serialized and a test asserts no timestamp-like key ever appears.
- Negative controls all fire with the expected gates: one official score pushed to `1.5` fails `candidate_scores_finite_and_in_unit_interval` and the SHA tie; a band set that cannot classify a provided `pct_urban` of 0.8639861007694217 aborts with `ValueError: provided value ... matches no preregistered band` instead of dropping the tract; a broken strata pin hash fails `provided_strata_hashes_verified`; altering the frozen mask-audit counts fails `definedness_matches_frozen_mask_audit`; a model-chain script containing `svi_overall`/`cvi_overall` fails `descriptive_only_columns_absent_from_model_chain`; a wrong declared template-row count fails `excluded_rows_documented`; and the unmutated run passes.
- FORBID respected: no sample score placeholder value, no label, no public leaderboard value, no removed reference layer or equivalent copy, no per-tract tuning (all bins are preregistered fixed edges, and nothing in the report feeds a model), and no proxy estimate is presented as an organiser target — the report carries `descriptive_only_not_an_accuracy_claim` and emits no RMSE or accuracy figure. No upload; the candidate is untouched.

### 2026-10-25 — row `bias-writeup-claim-evidence-audit` (documentation screen)

Row: `backlog:.langywrap/backlogs/competitions.jsonl:bias-writeup-claim-evidence-audit`.
Disposition `actioned`, close verdict `fixed`. Descriptive/verification screen only: no model
promotion, no accuracy assessment, no upload.

Built a claim-to-evidence auditor for the three writeup documents
(`docs/writeup/bias-discovery.md`, `docs/writeup/methodology.md`,
`docs/writeup/edge-cases.md`). Every block that carries a numeric token becomes a claim with a
verbatim anchor, named evidence pinned by SHA-256, an evidence pointer, a per-claim verify
command and a section-level reproduce command whose products are hash-verified.

Deliverables (SHA-256 first 16):

- `config/writeup-claim-evidence-preregistered.json` `2dfc00a70d8885c3`
- `scripts/audit_writeup_claim_evidence.py` `d459d67f66ae1cca`
- `scripts/verify_writeup_claim.py` `d6e724675a31095a`
- `docs/writeup/claim-evidence-audit.md` `d474612959cda2ec`
- `reports/writeup-claim-evidence-audit.json` `43bdc11dd128ab00`
- `reports/writeup-claim-inventory.json` `94bec394035fc7f4`
- `reports/writeup-claim-evidence-execution.json` `859af6f096de5904`
- `tests/test_audit_writeup_claim_evidence.py` `278c1e3f7ef5c226` (26 tests)

Measured: 84 quantitative claims, 100% traceable; 998/998 numeric tokens (100%) present in the
evidence named by the claim that states them; 84 verify commands and 16 reproduce commands, all
recorded exit code 0, zero undisclosed reproduce-product changes; zero unsupported accuracy
claims; zero removed-reference/label/leaderboard sources used as scored-model evidence.

Derivations were machine-checked rather than trusted: regional totals such as 9,379 official
tracts, 2,644 tracts with no named roads and 132 with no named-road segments are key sums over
`reports/edge-cases.json`; 3,143,887 road segments / 8,491,546 connector occurrences / 245,818
cross-tract adjacencies are key sums over `reports/road-topology-candidate.json`; the
`<0.5`-confidence shares 9.95/10.26/9.91/12.38% are ratios from
`reports/confidence-by-stratum.json`; the `pp` category-sensitivity figures are differences of
two `zero_share` values in `reports/poi-category-audit.json`; 132,133,741 is the sum of
`selected_column_compressed_bytes_total` in `reports/poi-category-audit.json`.

Findings worth keeping:

1. `scripts/lb_score_algebra.py` regenerates `reports/candidates.json` from its own candidate
   list and silently drops the newer cards (including `V2_model`), and rewrites
   `reports/lb-score-algebra.json` without `phase3_preregistration`, which breaks
   `tests/test_writeup_artifacts.py`. That command is no longer used as a reproduction path and
   both files are listed in `reproduce_never_disclosed_products`, so any change there fails the
   audit.
2. The derived Overture POI tract-summary cache is not byte-stable across pyarrow versions, so
   audits that record its hash legitimately differ in `/inputs/**/sha256`. Those paths, plus
   `/environment/**`, `/reproducibility/git_commit`, `/code_commit_at_run` and the rendered
   post-fire map, are the only reproduce differences allowed; every other changed leaf fails.
3. One `## Discovery screen` section in `methodology.md` stated proxy group means with no
   qualification; it now carries an explicit "not a claim about true organiser scores / not an
   observed real-world disparity" sentence, which the hedge gate requires.

Determinism: three audit runs produced byte-identical JSON (`43bdc11dd128ab00…`) and byte-identical
markdown (`d474612959cda2ec…`), including runs to alternate output paths; no wall-clock value is
serialized and a test rejects timestamp-like keys.

Negative controls (all fail closed, unmutated control passes): stale evidence hash →
`all_evidence_hashes_verified` + `every_quantitative_claim_has_evidence_pointer`; perturbed
derived aggregate → coverage/inventory gates; removed hedge phrases →
`hedged_language_present_where_required`; widened accuracy-pattern probe →
`no_unsupported_accuracy_claim`; missing reproduce execution log → both reproduce gates;
self-referential evidence (the audit's own inventory) → hash and pointer gates. The inventory and
the audit report are excluded from the evidence allowlist so the audit cannot certify itself.

Commands:

- `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --quiet python scripts/audit_writeup_claim_evidence.py`
  → `PASS v2 writeup claim audit: every quantitative claim in the writeup carries a hash-verified
  evidence pointer, a passing verify command and a hash-verified reproduction path, with no
  unsupported accuracy claim and no prohibited source used as scored-model evidence`
- `/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest python -m pytest -q`
  → `258 passed`

Limits: this is a documentation-integrity screen. It proves each stated number exists in, or is a
declared derivation from, a hash-pinned committed artifact, and that the listed commands exit 0.
It does not establish that the model is good, and it is not an accuracy claim; `V2_model` remains
un-uploaded and gated on operator authorization.

### 2026-10-25 — row `bias-writeup-form-submission` (documentation / prize deadline task)

Row: `backlog:.langywrap/backlogs/competitions.jsonl:bias-writeup-form-submission`.
Disposition `blocked`, close verdict `blocked`. Nothing was submitted to the organiser.

Assembled and machine-verified the full submission packet for the organiser writeup form, then
stopped at the operator gate as the row and the steward note require (FORBID submitting before the
operator reviews the discovery narrative; MEASURE-AFTER requires the operator to confirm the Zindi
username; steward: deliver the draft and mark the row blocked, do not act unilaterally).

Deliverables (SHA-256 first 16):

- `config/writeup-submission-preregistered.json` `0807403ff03c12fa` — preregistered identity/form/prize/
  external-source/prohibited-content rules, section order, and the never-invent list
- `scripts/build_final_writeup.py` `d0569cc3627edeb5` — assembles the writeup, renders the PDF, writes the record
- `scripts/verify_writeup_submission.py` `acb4bc4a30ecf75a` — independent packet checker (28 gate families)
- `docs/writeup/final-writeup.md` `029c95f7b67b1e6d` — the writeup: front matter, prize consideration,
  associated leaderboard submissions, Best Bias Discovery entry, methodology (Best Documentation
  entry), edge cases, external source register, reproducibility appendix, provenance statement
- `reports/writeup/final-writeup.pdf` `a013efe455c4737c` — exported PDF, `reportlab==4.4.10`, `invariant=1`
- `reports/writeup-submission.json` `10917b2f572e77ea` — the submission record (staged, all confirmation fields
  null)
- `tests/test_writeup_submission_packet.py` `d2b2865553392184` — 25 tests including tamper controls
- `docs/final-two-protocol.md` — writeup-form checklist line now points at the packet and names the
  blockers instead of implying a submission

Packet content that the rules require:

- Zindi username or team name: **`PENDING_OPERATOR_CONFIRMATION`** (never invented; the ledger
  records `team: null` for every Bias Bounty submission, so no identity is claimable from the repo).
- Leaderboard submissions associated with the methodology: the five successful uploads
  `uRRw9KpB` (C0, 0.107246299), `BvyKrR6v` (C1, 0.146449112), `rTARuUaG` (C2, 0.235358712),
  `TM83xxU6` (C3, 0.349409867), `ySXi73rd` (P4, 0.091440198), each tied by artifact SHA-256 to
  `reports/submissions.jsonl` and `reports/candidates.json`, plus the explicit statement that these
  are historical calibration/probe records excluded from the frozen final-two cohort and used as no
  evidence for the model or any discovery claim.
- Scored model candidate: `V2_model` at `reports/candidates/V2_model.csv` SHA-256
  `3193ee78a64004f007829f1f9c12d4aa8d4f7c15136771e8da9108fdcea06f12`, `uploaded: false`,
  `uploads_used_for_this_candidate: 0`, hash tied to the registry card and to
  `reports/estimator-v2-audit.json`.
- Both prizes stated with amounts and labelled sections, and the verbatim request line:
  "We request consideration for both Best Bias Discovery ($1,000) and Best Documentation ($500)."
  The discovery section is labelled `Best Bias Discovery entry` and quotes the four judging criteria
  with their weights (Impact 30%, Novelty 25%, Evidence 25%, Reproducibility 20%) plus the
  earlier-submission tie-break.
- External source register: 19 rows (18 prior-art URLs from `docs/literature/completeness-prior-art.md`
  accessed 2026-10-24, plus the organiser-hosted `SampleSubmission.csv` endpoint accessed 2026-10-23
  from the workspace download manifest). Every row carries URL + access date + role, satisfying the
  no-external-data-claim-without-URL-and-date prohibition; scored predictions use only provided data,
  tied to `reports/v2-input-provenance.json` (`prohibited_source_dependencies: 0`,
  `external_data_added_to_scored_predictions: false`, `target_labels_read: false`,
  `leaderboard_feedback_read_for_modeling: false`).
- Reproducibility appendix carries the claim-to-evidence audit figures (84/84 claims traceable,
  998/998 numeric tokens covered) and the exact commands.

Verification:

- `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --quiet --with reportlab==4.4.10 python scripts/build_final_writeup.py`
  → `PACKET ASSEMBLED, OPERATOR INPUTS MISSING (form_url_available,
  operator_identity_confirmation_recorded, submission_confirmation_recorded): packet is staged only;
  nothing was submitted to the organiser`
- `/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_submission.py`
  → `PASS writeup submission packet: assembled, hash-tied and honest about its pending operator
  inputs; staged only, nothing was submitted to the organiser`
- `/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest python -m pytest -q`
  → `283 passed`

`reports/writeup-submission.json` records `revision.git_commit` as the last commit
(`f55dbcb7d769d9f6c41aba42abb08f727955f274`) and `tree_clean: false`, measured honestly at
build time because the packet's own new files were still untracked; the checker does not
gate on that field and the packet hashes are pinned by content.

Determinism: three builds produced byte-identical markdown, PDF and record. PDF reproducibility
required `reportlab.rl_config.invariant = 1` plus a `canvasFactory` with `invariant=1`; without it
ReportLab embedded a wall-clock `/CreationDate` and the PDF hash changed every run. The record
serializes no wall-clock value and a test rejects timestamp-like keys.

Negative controls (checker fails closed; unmutated packet passes): forged `submitted_at_utc` +
confirmation reference; `ready_for_submission` flipped to true; an invented username without
operator confirmation; a tampered platform submission id; a tampered model-candidate SHA-256; an
external register row with a blank access date; an injected retired leaderboard constant value;
claim-audit figures made to disagree with the audit report; a broken recorded file hash.

Blocked on (all operator/steward-owned):

1. Operator review of the Best Bias Discovery narrative (the row forbids submitting before it, and
   ties on that prize break by earlier submission, so this is the critical path before the Oct 31
   close; target was Oct 29).
2. Operator confirmation of the Zindi username or team name (the ledger has `team: null` for every
   Bias Bounty submission, so the repo cannot state it).
3. The organiser writeup form URL: `$COMPETITION_WORKSPACE/RULES.md` preserves the sentence "submit
   your methodology writeup through this form" but not the hyperlink target.
4. Final-two selection: `reports/final-two.json` is still
   `protocol_frozen_waiting_for_eligible_candidates`, so the two candidate IDs whose submission IDs
   the form must name are not fixed yet; the packet records `candidate_A/B: null` rather than
   inventing them, and `V2_model` upload remains operator-gated.

### 2026-10-25 — row `bias-writeup-form-submission`, turn 2 (still blocked on the operator)

Disposition `blocked`, close verdict `blocked`. Nothing was submitted to the organiser and no
submission-capable action was taken.

The packet from the previous turn was blocked on four operator-owned inputs. This turn removed
everything the lane can legitimately remove, and turned the remaining operator work into one file
plus one command.

What changed:

1. **One-command operator completion.** `scripts/build_final_writeup.py --operator-inputs <file>`
   merges only the ten whitelisted operator paths (`identity.zindi_username_or_team`,
   `identity.operator_confirmation_recorded`, `form.url`,
   `submission_record.{submitted_at_utc,confirmation_reference,submitted_by,form_url_used,operator_review}`,
   `final_two.selected.{candidate_A,candidate_B}`). Anything else in that file is ignored: a test
   proves that tampering with `prizes` or `writeup_files` through the input file has no effect.
   When all `operator_input_schema.ready_requires` fields are present the packet becomes
   `submitted_confirmed_by_operator` with `ready_for_submission: true`; a partially filled file
   stays `prepared_pending_operator_review` and the checker rejects it ("submission confirmation
   must stay null until the operator submits the form", "operator review may only be recorded by
   the operator"). This is the pre-registration for the submission, not a way to submit.
2. **Form URL recovery attempted and recorded.** The official page was fetched twice
   (`.../bias-bounty-mapping-equity-challenge` and `.../rules`) plus an attempted supervisor
   extraction (unavailable: no offline extraction profile). The page is client-rendered and the
   fetched text ends before the writeup/prize section that carries the hyperlink target, so
   `form.url` stays null and `organiser_page.form_url_search` records the attempt (date, pages,
   outcome, reason). The checker fails if that documentation is missing while the URL is absent.
3. **Quoted official text.** The packet now quotes two official sentences verbatim, with the source
   URL and a documented-on date: the Maricopa membership note ("…1,592 Arizona tracts plus one New
   Mexico tract, 35023970000 (Hidalgo County, Tract 9700)…") and the prohibition on reference
   layers or equivalent copies. Both quotes are gated: they must appear verbatim in the writeup and
   their source URL must be a dated row in the external source register. The cross-state tract was
   re-verified against the provided sample this turn (`35023970000` present in the 1,593-row
   Maricopa official sample, which is the only non-`04` GEOID in it), matching
   `reports/edge-cases.json` `/regions/maricopa-az/new_mexico_geoid_35023970000_present: true`.
4. **Register and record.** The external source register grew to 21 dated rows (18 prior-art URLs,
   the organiser-hosted sample-submission endpoint, the official page, and the two pages attempted
   during the form-URL search). `reports/writeup-submission.json` now carries
   `pending_operator_inputs`, `blocking_prerequisites` (six, each naming the exact field to fill) and
   `operator_input_schema.fields` so the operator can see what to write.

Deliverables updated (SHA-256 first 16): `config/writeup-submission-preregistered.json`
`577035ddc58bcc47`, `scripts/build_final_writeup.py` `2e8a9377289cc848`,
`scripts/verify_writeup_submission.py` `75ddd0044d78dea7`, `docs/writeup/final-writeup.md`
`151418d0b8742b0e`, `reports/writeup/final-writeup.pdf` `377e0e0f5ee72e1b`,
`reports/writeup-submission.json` `2a860cbff3b1208c`,
`tests/test_writeup_submission_packet.py` `9ad79fc94a8f37b9` (29 tests).

Verification:

- `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --quiet --with reportlab==4.4.10 python scripts/build_final_writeup.py`
  → staged packet, `pending_operator_inputs: [form_url_available,
  operator_identity_confirmation_recorded, submission_confirmation_recorded]`
- `/home/martin/hddProjects/langywrap/uv run --quiet python scripts/verify_writeup_submission.py`
  → `PASS writeup submission packet: assembled, hash-tied and honest about its pending operator
  inputs; staged only, nothing was submitted to the organiser`
- `/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest python -m pytest -q`
  → `287 passed`
- determinism: two builds produced byte-identical markdown, PDF and record
- fill-mode rehearsal with synthetic operator inputs (in `/tmp`, never committed) produced
  `submitted_confirmed_by_operator`, `ready_for_submission: true`, empty
  `blocking_prerequisites`, and a passing checker; a partially filled file was rejected

Still blocked on, all operator/steward-owned:

1. Operator review of the Best Bias Discovery narrative (`submission_record.operator_review`).
2. Operator confirmation of the Zindi username or team name (`identity.*`); the ledger records
   `team: null` for every Bias Bounty submission, so the repo cannot state it.
3. The organiser writeup form URL (`form.url` / `submission_record.form_url_used`); recovery was
   attempted and documented, the page is client-rendered.
4. Final-two selection (`final_two.selected.candidate_A/B`): `reports/final-two.json` is still
   `protocol_frozen_waiting_for_eligible_candidates` and `V2_model` upload stays operator-gated.
5. Form submission evidence (`submitted_at_utc`, `confirmation_reference`, `submitted_by`).

Deadline note for the operator: the challenge closes 2026-10-31T23:59:00Z, discovery-prize ties
break by earlier submission, and the row's target was 2026-10-29. The packet is ready for one
review, one input file and one submission.

### 2026-10-25 — row `bias-discovery-prize-criteria-audit`, closed `fixed`

Delivered the consolidated criteria-by-section audit of the Best Bias Discovery narrative.
Documentation screen only: no submission, no candidate change, no leaderboard access.

New deliverables (SHA-256 first 16):

- `config/discovery-prize-criteria-preregistered.json` `9f907ed11b7c1cbd` — the four organiser
  criteria with weights (Impact 30, Novelty 25, Evidence 25, Reproducibility 20), the section map,
  hash-pinned evidence pointers, hedge/grid/negation vocabularies, prohibited-token rules, the
  external-dataset policy (empty allowlist because the discovery uses no data beyond the challenge
  package) and eight preregistered negative-control cases.
- `scripts/audit_discovery_prize_criteria.py` `04871218aca0486a` — parses
  `docs/writeup/bias-discovery.md` into sections, judges each of the four criteria mechanically,
  verifies every pointer (existence + pinned SHA-256 + JSON-pointer resolution), ties each section
  to its inventoried claims, and executes one sampled claim verify command per section.
- `reports/discovery-prize-criteria-audit.json` `673c39487e56197f` — deterministic report, no
  wall-clock value.
- `docs/writeup/bias-prize-criteria-audit.md` `0c07b2a13bfdb373` — the required deliverable:
  criteria table, section × criterion matrix, per-section evidence pointers with hashes,
  reproduction commands, measured acceptance, recommendations and an explicit non-claims list.
  Generated from the JSON report, so the two cannot disagree.
- `tests/test_audit_discovery_prize_criteria.py` `984496ec7bca7be0` — 22 tests.

Measured result: 4 sections configured and covered (0 uncovered headings in the document),
16/16 criteria judged as `pass`, 23/23 evidence pointers hash-verified and resolving, 28/28
quantitative claims in the discovery document traceable (claim audit fraction 1.0),
0 unlisted external URLs, 0 hidden-target patterns, 0 unqualified operational patterns, 0
prohibited-token hits in prose or scripts, 4/4 sampled claim commands exit 0, all 17 gates true.

Two findings the audit produced, and what I did about them (not cosmetic — both were real
criterion gaps in the narrative):

1. **Section 3 (Overture source mix) never compared itself to the published scorecard grid.**
   Novelty failed with "the section never names a published scorecard dimension". Added one
   digit-free sentence making the comparison explicit ("the scorecard publishes those marginals,
   whereas this finding concerns the provider composition *inside* the same rural cell, a dimension
   the scorecard does not publish").
2. **The same section had no qualified decision consequence.** Impact failed on "no named decision
   consequence" and "no named referent". Added one hedged sentence naming the four regions and a
   concrete audit-prioritisation consequence, explicitly "not evidence that any service is absent".

Honesty controls that stayed in place: the rural × SVI minority lead remains declared **draft**
("Draft for operator review") and is recorded as ineligible for the discovery prize until the
operator reviews it; negated sentences such as "not evidence of a true coverage gap" are *not*
counted as hidden-target claims (a plain pattern scan would have flagged them, so the scan is
negation-aware); `TIGER`, `USFS`, `Microsoft` and removed-reference mentions are allowed only where
the sentence carries a disclosure marker.

Verification commands and output:

- `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --quiet python scripts/audit_discovery_prize_criteria.py`
  → `PASS discovery prize criteria audit: every discovery section is supported on all four organiser
  criteria or explicitly marked draft, every evidence pointer reproduces, and no unqualified
  operational or hidden-target claim survives`; `--check-only` → `unchanged`
- determinism: two runs to alternate paths produced byte-identical JSON and markdown, equal to the
  committed files; `wall_clock_hits(report) == []`
- negative controls, all exit 1 with the expected failed gate: `drop_impact_hedge`
  (`no_unqualified_operational_impact`), `invent_external_url`
  (`zero_unreferenced_external_datasets`), `fake_pointer_hash`
  (`every_evidence_pointer_reproduces`), `untraceable_section_claim`
  (`every_section_claim_traceable`), `unqualified_operational_impact`
  (`no_unqualified_operational_impact`), `proxy_asserted_as_organiser_target`
  (`zero_proxy_as_organiser_target_claims`), `missing_reproduce_command`
  (`every_section_supported_or_marked`), `sampled_command_fails`
  (`sampled_verify_commands_exit_zero`); the unmutated control passes
- cascade after the discovery-document edit:
  `/home/martin/hddProjects/langywrap/uv run --quiet python scripts/audit_writeup_claim_evidence.py`
  → still `PASS` with 84 claims, 998/998 numeric tokens covered, no failed gates; the submission
  packet was rebuilt and `scripts/verify_writeup_submission.py` → `PASS ... staged only, nothing was
  submitted to the organiser`
- full suite: `/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with
  pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest python -m pytest -q`
  → `313 passed`

### 2026-10-25 — row `bias-lb-snapshot-and-scorecard`, closed `fixed`

Delivered the passive leaderboard snapshot and the per-submission Bias Scorecard record, plus the
organiser-encouraged scorecard reflection in the methodology writeup. No upload, no credential
stored, and no scorecard- or leaderboard-driven model change.

New deliverables (SHA-256 first 16):

- `config/lb-scorecard-snapshot-preregistered.json` `309516c02e76b4e2` — the passive scope: the
  retained submission fields, the deliberately dropped `url` field, the required scorecard rubric
  fields, the pinned ledger snapshot, the five required scorecard submission ids with the failed one
  excluded and why, the assembled POST markers, and four negative controls.
- `scripts/snapshot_lb_and_scorecards.py` `41a1163874441e44` — reads the shared orchestrator ledger
  read-only, makes exactly one read-only `GET` of the authenticated submission status path under
  `--fetch`, and derives every product. It has **no POST path**: the gate assembles POST markers from
  config fragments so the file cannot self-hit and cannot grow one silently.
- `reports/leaderboard/2026-10-25T051219+0000.json` `7f699892f4f38d8c` — the dated snapshot: top-20
  public RMSE table, our rank, participants, ranked teams, rank-10/25/50/100, median, perfect-score
  count, every one of our submissions with the number of teams strictly better, and the gaps.
- `reports/leaderboard/index.json` `1f35df77f881e097` — this snapshot plus the full 20-event rank
  history from the ledger.
- `reports/scorecards/<submission_id>.json` for all six platform submissions, e.g. P4
  `ySXi73rd` `0927d4b704da35c0` and the C0 anchor `uRRw9KpB` `bfa4c458866ba17b`, with
  `reports/scorecards/index.json` `5355a2acf86675e1` pinning every file hash.
- `reports/lb-scorecard-snapshot.json` `82e9515d5d4462fb` — the machine record: 19 gates.
- `tests/test_snapshot_lb_and_scorecards.py` `c0870060b50896f4` — 26 tests.
- `docs/writeup/methodology.md` `584abb80f94b9541` — new `## Scorecard reflection` section at the end.

Measured: 1,441 participants, 390 ranked teams, our rank 274, our best public 0.091440198 (P4),
rank-10 boundary 0.000429403, **gap to the top-10 boundary 0.091010795**, median 0.083887046 (we sit
0.007553152 above it), rank-50 gap 0.024401074, six exact-zero scores and a best positive score of
0.000091526. All 19 gates true, including `gap_to_top10_boundary_computed` (reproduced to 1e-12),
`every_successful_submission_has_a_scorecard`, `scorecard_dimensions_complete`,
`scorecard_fields_complete_per_dimension`, `no_secrets_in_committed_outputs`,
`ledger_access_is_read_only`, `no_upload_path_exists` and
`scorecard_never_influenced_parameters`.

What the scorecard actually said, and it is not flattering:

- The all-zero anchor `uRRw9KpB` returns all nine dimensions `undefined` with the organiser's reason
  "Reference group has zero mean gap; ratio is undefined."
- `BvyKrR6v` and `ySXi73rd` have **identical** scorecards (heat 1.141 / 14.1% larger, tribal 0.705 /
  29.5% smaller, everything else "Similar") even though their public RMSE differ 0.146449 against
  0.091440: the disparity ratios are ratios of group means, so shrinking the whole prediction scale
  moved the score and left the equity profile untouched.
- The component estimator `rTARuUaG` is flagged on rural (0.248, 75.2% smaller), tribal (0.556, 44.4%
  smaller), wildfire (0.621, 37.9% smaller) and heat (1.211, 21.1% larger): our own components assign
  systematically smaller predicted gaps to rural, tribal and high-wildfire tracts than to their
  reference groups, and the organiser rubric sees it even in a poor-RMSE submission.

Honesty controls that shaped the work:

- The presigned `url` field is dropped before anything is written because it embeds an AWS access-key
  id and a request signature; a test walks every committed JSON for a `url` key, scans all outputs for
  AKIA/X-Amz/signature/cookie/JWT shapes, and asserts the token file's content never appears.
- The snapshot is **pinned** to the ledger event `2026-10-25T05:12:19+00:00` rather than "latest", so
  the reproduce command stays byte-stable while the steward keeps appending snapshots; a missing pin
  fails closed.
- The reflection paragraph quotes scores to six decimals, because the claim tokenizer treats longer
  literals as identifiers; three claims (`methodology-050..052`) are registered in the claim-evidence
  config with the snapshot, scorecards and `reports/lb-score-algebra.json` as evidence, so the
  accuracy-asserting sentences are backed by the committed platform record rather than by prose.
- Two earlier placements of the new section were rejected and reverted: inserting it mid-document
  renumbered 43 existing methodology claim ids and broke their overrides. It now sits at the end and
  the 84 pre-existing claims are byte-identical to the `2aa0a57` inventory.

Verification commands and output:

- `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --quiet python scripts/snapshot_lb_and_scorecards.py`
  → `PASS lb snapshot and scorecards: passive ledger snapshot plus hash-pinned per-submission Bias
  Scorecards committed without secrets, uploads, or any leaderboard-driven model change`;
  `--check-only` → `unchanged`
- determinism: two independent runs produce byte-identical reports equal to the committed file; the
  scorecards and both index files rebuild identically from the ledger plus committed payloads
- negative controls (all exit non-zero with the expected gate): `inject_secret` and
  `secret_in_snapshot` → `no_secrets_in_committed_outputs`; `scorecard_field_dropped` →
  `scorecard_fields_complete_per_dimension`; `rank_without_timestamp` →
  `snapshot_carries_utc_timestamp`; a bogus pin → exit 2 `BLOCKED`; the unmutated control passes, and
  the tamper runs write only into a sandbox copy so the committed scorecards are provably untouched
- cascade after the methodology edit: `scripts/audit_writeup_claim_evidence.py --write-inventory` → 87
  claims; `--execute-reproduce --reproduce-filter snapshot_lb_and_scorecards` → 17/17 reproduce
  commands exit 0; full claim audit → `PASS` with 87/87 traceable and 1026/1026 numeric tokens
  covered; `scripts/audit_discovery_prize_criteria.py` → `PASS` (16/16 criteria, 23/23 pointers);
  `scripts/build_final_writeup.py` + `scripts/verify_writeup_submission.py` → `PASS ... staged only,
  nothing was submitted to the organiser`
- full suite: `/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with
  pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest python -m pytest -q` →
  `339 passed`

Note for the operator, from the same status path (not operator-confirmed, so the writeup still says
`PENDING_OPERATOR_CONFIRMATION`): the platform records the submitter handle `holi` on all six Bias
Bounty submissions.

### 2026-10-25 — row `bias-discovery-postfire-county-bootstrap`, closed `negative`

Rechecked the published post-fire mapping-disagreement finding for county-level dependence. The
preregistered robustness criterion **fails**: the tract-level interval survives only as a direction,
so the quantitative claim is now labelled exploratory in the writeup. No design was substituted after
seeing results, no county was dropped, the `<0.8` cutoff was never retuned, and nothing in the model
chain changed.

New deliverables (SHA-256 first 16):

- `config/discovery-postfire-county-bootstrap-preregistered.json` `43ba983db726e6e2` — the design
  frozen before any result: the joint county resample as primary (the row requires preserving *all*
  tract records of a selected county, which a within-group county resample cannot do), the three
  acceptance criteria, the two declared secondary designs that may not change the verdict, the
  reference control, the frozen definition pins, five negative controls, and the disclosure that the
  derived Overture feature cache was rebuilt after the frozen report was published.
- `scripts/audit_discovery_postfire_county_bootstrap.py` `1b83cae9cc6193cc` — 19 gates, `--check-only`,
  and five `--tamper` controls.
- `reports/discovery-postfire-county-bootstrap.json` `18123e36b1d95654` (report sha256
  `810bb79f3917c018…`) — point estimate, cluster interval, positive-sign and valid-replicate fractions,
  county/group counts, seed, input hashes, per-county table, LOO rows, secondary designs, and the
  writeup-qualification record.
- `tests/test_audit_discovery_postfire_county_bootstrap.py` `a4a4eaf32a29fc84` — 31 tests.
- `docs/writeup/bias-discovery.md` `342df0d2c677861c` — the finding is now qualified in its own
  Evidence block rather than deleted, plus the reproduction command in Reproducibility.

Measured, from provided data only (`overture_buildings`, `usfs_BuildingCount_sum`,
`mtbs_wildfire_burned_pct_land`, `COUNTYFP`; 591 official NorCal tracts, 577 in the mutually exclusive
bins, 14 excluded for a provided burned percentage above 100, none missing):

- point estimate unchanged at **+9.55209106656768 pp** (exact rational `386/4041`, agreeing to
  `0.0` against the frozen report), high-burn 4/36, unburned 7/449;
- 23 counties in the analysis population, high-burn tracts in 15 of them, unburned in 17, both focal
  classes in 11;
- **county-cluster bootstrap** (50,000 seeded replicates, seed 20261025, `multinomial` over all 23
  counties, every tract record of a selected county preserved): **95% percentile interval
  [-1.652893, +16.666667] percentage points**, valid-replicate fraction **1.0**, and **95.99%** of
  valid replicates positive;
- criteria: valid fraction ≥0.95 pass, positive-draw fraction ≥0.95 pass, **cluster lower bound > 0
  FAIL** → verdict `exploratory`, `quantitative_claim_retained: false`;
- concentration diagnostics (declared before results, never used to select): dropping any single
  county leaves the point difference between **+5.559 pp** (drop 033) and **+10.708 pp** (drop 067), so
  the widening comes from county clustering, not one influential county; the declared secondary
  within-group county resample gives group intervals [0.0, 0.176] and [0.0, 0.024] and may not change
  the verdict; the reference control re-runs the published tract bootstrap and reproduces its frozen
  interval **bit-exactly** (max deviation 0.0), which is what proves the machinery;
- 19/19 gates true, including `no_county_dropped`, `clusters_are_whole_counties` (cluster sizes equal an
  independently recomputed per-county census, and sampled replicates provably retain non-focal tracts),
  `cutoff_not_tuned`, `derived_input_regeneration_disclosed`, `no_forbidden_source_read`,
  `no_leaderboard_or_target_values_used`, `no_causal_language_in_report` and
  `writeup_qualification_matches_verdict`.

Honesty controls that shaped the work:

- The primary design was chosen from the row's own wording before any number was seen, and both
  alternative designs are declared secondary with `may_change_verdict: false`. When the interval
  crossed zero I qualified the writeup instead of re-running a friendlier design.
- The `writeup_qualification_matches_verdict` gate was strengthened mid-row: my first version only
  required the word "exploratory" anywhere in the document, which passed trivially because an unrelated
  section is already called exploratory. It now requires the marker **and** the exact interval bounds
  inside the Evidence block that carries the +9.55 pp claim, and fails closed if they are absent.
- Two gates initially failed for real reasons and were fixed rather than relaxed: the county-size check
  wrongly rejected the three legitimate single-tract counties (now it compares against an independent
  per-county census), and the derived Overture feature cache hash had drifted from the publication-time
  pin. The cache is a derived product rebuilt by `scripts/estimate_gaps.py`, so the gate now demands a
  content-equivalence proof instead of a stale byte pin: identical burn-class table, identical
  below-cutoff counts, identical per-tract Butte below-cutoff GEOID set, and the point difference
  reproduced to 1e-12.
- Adding a paragraph in the middle of `docs/writeup/bias-discovery.md` renumbered 22 positional
  `bias-discovery-*` claim ids and silently misdirected the claim overrides and the criteria audit's
  sampled claims. I re-keyed the overrides by matching claims on (doc, section, kind, text) rather than
  by id, made the affected hedge test select by section and provenance class for the same reason, and
  re-pinned the four new criteria-audit pointers.
- One reproduce gate was self-referential: the methodology's "the suite passes" claim is reproduced by
  running the suite, while one suite test asserts that claim audit's verdict. The runner now executes
  that command with the single circular test excluded and records the exclusion in
  `reports/writeup-claim-evidence-execution.json` under `self_referential_deselect`, with the reason in
  the config; every other test must still exit zero, and the excluded test still runs in a normal
  suite invocation.

Verification commands and output:

- `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 python scripts/audit_discovery_postfire_county_bootstrap.py`
  → `PASS post-fire county-cluster bootstrap: the published difference was rechecked by resampling
  whole counties, every county stayed in the pool, every tract record of a selected county was
  preserved, and the writeup states the robustness verdict`; `--check-only` → `unchanged`
- determinism: two independent runs byte-identical and equal to the committed report; the report
  serialises no wall-clock value (`wall_clock_hits == []`)
- negative controls, all exit non-zero with the expected gate: `drop_inconvenient_county` →
  `no_county_dropped`; `tract_resample_disguised_as_cluster` → `clusters_are_whole_counties`;
  `cutoff_retuned` → `cutoff_not_tuned` (plus the frozen-definition and point gates);
  `seed_or_replicates_changed` → `replicates_and_seed_as_preregistered`; `point_estimate_perturbed` →
  `point_estimate_matches_frozen_report`; the unmutated control passes, and a test asserts a tamper run
  never rewrites the committed report
- cascade after the writeup edit: `audit_writeup_claim_evidence.py --write-inventory` → 88 claims;
  `--execute-reproduce` → `17/17 reproduce commands exited 0` with no undisclosed product changes; full
  claim audit → `PASS` with 88/88 traceable and 1035/1035 numeric tokens covered;
  `audit_discovery_prize_criteria.py` → `PASS` (16/16 criteria, 27/27 pointers, up from 23 with the four
  county-bootstrap pointers added); `build_final_writeup.py` + `verify_writeup_submission.py` → `PASS …
  staged only, nothing was submitted to the organiser`
- full suite: `/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest python -m pytest -q` →
  `370 passed`

Consequence for the discovery prize narrative: the Butte/Camp Fire ratio examples and the burn-class
table remain published facts of the provided data, and the direction of the high-burn versus unburned
disagreement is repeatable in almost every county resample, but the lane no longer states its size as a
quantitatively established result. The section keeps its four-criteria support; only the strength of the
quantitative claim changed.

Final artefact hashes after the last regeneration pass (the row's own report carries
`code_commit_at_run`, so it is rewritten on each regeneration and `--check-only` compares everything
except that single field, which is why the numbers below differ from the first pass above):

- `config/discovery-postfire-county-bootstrap-preregistered.json` `43ba983db726e6e2` (unchanged)
- `scripts/audit_discovery_postfire_county_bootstrap.py` `7128e72ca71cde98`
- `reports/discovery-postfire-county-bootstrap.json` `feaf180e6637bafc` (report sha256
  `c3fc94f0b21a5fba…`, `code_commit_at_run` `b69fd1099136`)
- `tests/test_audit_discovery_postfire_county_bootstrap.py` `fd0ab18802fcc538` (31 tests)
- `docs/writeup/bias-discovery.md` `342df0d2c677861c` (unchanged since qualification)
- `reports/discovery-prize-criteria-audit.json` `384c8ea9f5eeb60e`,
  `reports/writeup-claim-evidence-audit.json` `2c4a68bf74addb60`,
  `reports/writeup-claim-inventory.json` `c818d6afa19c3f67` (88 claims, 1035/1035 tokens)
- full suite after the final pass: `371 passed`; claim audit `--execute-reproduce`:
  `17/17 reproduce commands exited 0`; criteria audit `--check-only`: `unchanged`;
  submission packet verifier: `PASS ... staged only, nothing was submitted to the organiser`

The `code_commit_at_run` exemption is the only comparison relaxation added for this row; the two-run
determinism test still requires byte-identical reports from the same commit and separately compares the
committed report with that field removed, and a test asserts the field is a 40-hex commit.

Second regeneration pass: `summary.report_sha256` is now computed with `code_commit_at_run`
excluded, which is the correct fix rather than exempting two fields, so the committed report differs
across commits in that one field only. Final hashes measured after the last build:
`scripts/audit_discovery_postfire_county_bootstrap.py` `2332550787682820`,
`reports/discovery-postfire-county-bootstrap.json` `942c39504dbcf3b2`,
`tests/test_audit_discovery_postfire_county_bootstrap.py` `4c6a7c6a14cec0f8` (32 tests). Final state:
claim audit `88/88` claims and `1035/1035` tokens with `17/17` reproduce commands exiting zero,
criteria audit `16/16` criteria and `27/27` pointers, packet verifier `PASS`, suite `372 passed`.

### 2026-10-25 — row `bias60-harness`, closed `fixed`

Built the common harness the other bias60 rows depend on: one interface (provided inputs in, per-tract
value for the idea's component out), one parquet per idea with a recorded SHA-256, a derived registry
for all 60 spec ideas, and the label-free orthogonality report on the study tracts. Nothing was
uploaded, no reference layer or label was read, and no lab geography is touched.

New deliverables (SHA-256 first 16):

- `src/bias60/` — `spec.py` `3b0cf558e723474d` (parses the operator-frozen memo into 60 idea records),
  `registry`-builder + runner in `harness.py` `5243910cb86ecaf4`, `inputs.py` `eb50dfe601dd42e6`
  (column-declared provided-input access), `contracts.py` `a804e797cc2d4945` (fixed
  `clip(1-O/E,0,1)` mapping plus the row-level output contract), `orthogonality.py` `59e8b7a610990cea`,
  `determinism.py` `216faf199f9e92cd`, and the three seed ideas `ideas/tm.py` `42eaf56d656af6fe`,
  `ideas/sc.py` `7b58e0993b55c264`, `ideas/mx.py` `a93fda7e62f3904b`.
- `scripts/build_ideas_registry.py` `fd56f56b65dd3a8d`, `scripts/run_bias60_ideas.py` `5635028ed97a072e`,
  `scripts/report_ideas_orthogonality.py` `d2812ab09c688b2f`.
- `config/bias60-harness-preregistered.json` `d688fae4c7a11542` — pins the spec SHA-256
  (`8521753accde328e…`), the vocabulary, the forbidden path/value/column tokens, the orthogonality
  thresholds, and the three seed definitions.
- `config/ideas.json` `4f753b1496910a44` — **generated, never hand-written**: 60 ids, 12 families × 5,
  each with id, family, component, kind, inputs, cost, plus recipe/signal/failure-mode carried from the
  spec. Derived counts: components R 15, Est 13, B 12, F 12, C 6, "all" 2; kinds 13 gap / 47 covariate;
  costs 38 cheap / 14 medium / 8 heavy.
- `reports/ideas-harness.json` `581798e9651c6f37`, `reports/ideas-orthogonality.json`
  `933a75086883e3e6`, `tests/test_bias60_harness.py` `83757cdbb18ed39c` (43 tests).

Design decisions worth keeping:

- **The registry is derived from the spec and hash-pinned.** `bias60.spec` parses section 2 of
  `$ORCHESTRATOR_ROOT/docs/research/bias-60-ideas-2026-10.md`; the build fails closed if its SHA-256
  differs from the pin. Classification rules (primary component = first token of the spec
  `*Component:*` line with the literal `all` preserved; kind = gap only when the per-tract recipe states
  an explicit clipped-ratio quantity, else covariate; cost = the heaviest token so "cheap to medium"
  budgets as medium) are recorded inside the registry under `derivation`.
- **One idea = one function, one parquet per idea.** `$COMPETITION_WORKSPACE/ideas/<id>.parquet` with
  columns `GEOID, region, idea, family, component, channel, value, defined`; one row per
  (GEOID, component, channel) so a multi-component idea like Mx4 can put the county reference-side share
  on F and the tract's own share on B.
- **Undefined is not zero.** `clip_one_minus_ratio` returns a `defined` mask and the contract requires
  every undefined row to carry exactly `0.0`, so an uncovered CarbonPlan cell or a zero-unfiltered-length
  tract cannot become a fabricated total gap. No mean fill, no zero fill, no dropped rows.
- **The official GEOID list comes only through the frozen reader** in
  `scripts/audit_v2_component_masks.py::official_scored_ids`; score placeholder cells are never opened.
  Every column read is recorded and must be in the preregistered declared list; every opened path is
  screened against the forbidden tokens.
- **Both road layers are measured on the same basis.** Sc5 clips named-class segments into the official
  tract polygons in EPSG:5070 for *both* `roads` and `roads-unfiltered`, and the filtered measurement
  must reconcile with the lane's cached `overture_named_road_km_5070` to a relative tolerance of 1e-6 —
  so a length-convention mismatch cannot masquerade as numerator exclusion.

Measured this turn, from provided data only (9,379 official tracts):

- **Sc5 is a measured null and the harness is what proved it.** Named-class rows and clipped lengths are
  identical between `roads` and `roads-unfiltered` in all four regions (rows_named filtered/unfiltered:
  Maricopa 57,516/57,516; Northern CA 26,050/26,050; Eastern OK 59,436/59,436; South-Central TX
  353,790/353,790), the gap is float noise (max 1.4e-15), and 0 tracts are excluded entirely. The
  organiser's pre-filter removes no named-class segment — the exact failure mode the spec predicted for
  its own top-10 idea. The orthogonality report declares both Sc5 columns
  `numerically_null_columns` rather than reporting noise as signal.
- **Tm1** (two-release building churn, gap-scale via the fixed mapping): defined 9,377/9,379, per-region
  share of tracts with a positive drop 56.8% (Maricopa), 68.2% (N. CA), 71.1% (E. OK), 66.6% (S. CTX);
  gap max 0.212, over-coverage covariate max 2.25.
- **Mx4** (Esri Community Maps presence): 297 counties aggregated, county Esri share 0–0.614, tract
  Esri-only share 0–0.998; the 2 zero-building tracts of the earlier mask audit stay undefined rather
  than being zero-filled.
- **Orthogonality** (label-free, spec 3.3): 6 columns evaluated, all included at ≥98.59% coverage;
  condition number 9.95; 5 clusters on 1-|rho| with average linkage; the only pair above |rho| > 0.8 is
  Sc5's own gap/covariate (0.816); 4 columns exceed VIF 10 and are flagged, not dropped; MTMM direction
  is right — mean |rho| within component 0.230 versus 0.077 between components.

Commands and output:

- `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with shapely==2.2.0 --with pyproj==3.8.0 python scripts/build_ideas_registry.py`
  → `PASS ideas registry: all 60 spec ids carry family, component, kind, inputs and cost, the three seed
  ideas are implemented across distinct components, and the spec hash matches its pin`; `--check-only` →
  `unchanged`
- same environment, `python scripts/run_bias60_ideas.py` → `PASS bias60 harness: every implemented idea
  produced one contract-valid parquet over the official scored tracts, no forbidden source or undeclared
  column was read, and the run is reproducible` (all 11 gates true; 18,758 rows = 9,379 × 2 emissions per
  idea; ~4.5 min cold, cached reruns seconds); `--check-only` → `unchanged`
- same environment, `python scripts/report_ideas_orthogonality.py` → `PASS ideas orthogonality: every
  implemented estimator was measured label-free on the study tracts with correlation, VIF, condition
  number and 1 - |rho| clustering, and the merge thresholds were applied exactly` (16 gates true);
  `--check-only` → `unchanged`
- negative controls, each exiting non-zero with the expected gate: harness `drop_idea` →
  `every_idea_wrote_one_parquet`, `duplicate_geoid` → `official_geoid_partition_exact`,
  `undeclared_column` → `no_undeclared_column_read`, `forbidden_source` → `no_forbidden_source_accessed`,
  `nondeterministic_rerun` → `deterministic_rerun`; orthogonality `drop_column` →
  `evaluated_columns_are_the_implemented_ideas`, `duplicate_column` → `no_duplicate_columns`,
  `break_symmetry` → `correlation_is_symmetric`, `low_coverage` → `coverage_threshold_enforced`;
  registry `drop_idea` → `registry_holds_all_60_spec_ids`, `wrong_spec_hash` → the pinned-hash refusal.
  Tampered parquets go to `/tmp` only, and the committed reports were rebuilt afterwards.
- full suite: `/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest python -m pytest -q`
  → `415 passed` (43 new).

Determinism: the run-time commit fields (`environment.git_commit`, `code_commit_at_run`) are isolated in
`bias60/determinism.py` and excluded from the comparison and from `summary.report_sha256`, exactly as in
the county-bootstrap row; everything else must be byte-identical, and no report carries a temporal key
(`wall_clock_hits == []`, asserted for all three committed JSON artifacts).

Follow-up filed with the shared command: `bias60-numerator-definition-screen` (P1) — screen Sc1 and Sc4
now that Sc5 shows the shipped filter is class-neutral, and record them as measured-null with numbers if
both shares stay below 1% in every region. Command output: `appended bias60-numerator-definition-screen
for comp-zindi-bias-bounty -> /home/martin/hddProjects/competitionsall/.langywrap/backlogs/competitions.jsonl`.

Next rows in the ladder can now register ideas by id: `bias60-family-*` rows add functions under
`src/bias60/ideas/`, and `bias60-track-a-combiner` consumes `reports/ideas-orthogonality.json` for the
pre-registered merge decisions.

### 2026-10-25 — row `bias-postfire-writeup-cluster-qualification`, closed `fixed`

Made the post-fire discovery claim communicate the county-cluster result wherever it is asserted, and
turned the row's metric ("count of unqualified inferential/high-confidence statements about the
high-burn versus unburned difference in the two main writeups") into a mechanical screen instead of an
opinion.

MEASURE-BEFORE, measured by the new classifier before any prose edit: the frozen county-cluster report
carries point difference +9.55209106656768 pp, 95% cluster interval [-1.6528925619834711,
+16.666666666666664] pp, positive-draw fraction 95.988%, verdict `exploratory`, quantitative claim
retained false. In **both** writeups exactly **one** inferential statement was unqualified — the first
mention of the finding: "The high-burn minus unburned difference is **+9.55 percentage points**, with a
stratified 50,000-draw tract-bootstrap 95% percentile interval of **[+0.55, +20.89] percentage points**."
A reader who stopped there took the tract interval for the interval, which is exactly the defect the row
named.

MEASURE-AFTER: **0** unqualified inferential statements in each document (12 inferential statements
examined over 80 sentences), both intervals and the word `exploratory` now at first mention, the two
resampling designs distinguished in one recorded sentence, and every quoted number derived from the
frozen reports rather than typed. The new paragraph states that the tract interval "measures sensitivity
to tract-level resampling of the binary ratio indicator at a fixed county composition … it is not the
interval to quote when counties rather than tracts are the independent units". Nothing was softened the
other way: the finding stays in the writeup, only its confidence level changed.

New deliverables (SHA-256 first 16):

- `config/postfire-writeup-cluster-qualification-preregistered.json` `323dd6b5bfe4986a` — pins the three
  frozen artifacts by SHA-256 (`reports/discovery-postfire-county-bootstrap.json`
  942c39504dbcf3b2…, `reports/discovery-postfire.json` cbf100bc833bf09c…, the county-bootstrap config
  43ba983db726e6e2…), the subject/cue/qualification/exemption pattern lists, the tracked statistics with
  their JSON pointers and precision, the language policy, the opened-path allowlist and the nine
  negative controls.
- `scripts/audit_postfire_writeup_cluster_qualification.py` `36592f102cec16bb`.
- `reports/postfire-writeup-cluster-qualification.json` `561cc05249e77778` (22 gates including `all_passed`, all true) and its
  generated companion `docs/writeup/postfire-cluster-qualification-audit.md` `5b2f48754516541e`.
- `tests/test_audit_postfire_writeup_cluster_qualification.py` `095530ed74253a94` (30 tests).
- Edited sources: `docs/writeup/bias-discovery.md` `b91d96f8a30d8194`; regenerated
  `docs/writeup/final-writeup.md` `9817471d5874b232` (assembled by `scripts/build_final_writeup.py`, never
  hand-edited).

Design decisions worth keeping:

- **Qualification is judged sentence-locally.** A sentence that both talks about the difference
  (subject patterns) and uses an inferential/confidence/robustness cue must carry the clustered result in
  that same sentence; a neighbouring sentence cannot lend it credibility. Window 0, preregistered.
- **Claims are classified only where claims live.** The Reproducibility subsection is machinery prose
  ("…if the writeup fails to state the robustness verdict…") and was a genuine false positive on the cue
  `robust\w*`; rather than narrowing cues until the detector was blind, the classifier scope now ends at
  the Reproducibility heading while the required numbers are still searched across the whole block. The
  gate `classifier_is_not_vacuous` (≥4 inferential statements per document) stops the scope from being
  widened until nothing is checked.
- **Numbers are derived, not quoted into the config.** Each tracked statistic names a JSON pointer in the
  frozen report plus a rounding/format rule, so `[-1.65, +16.67]` is recomputed at run time; a positional
  capture pattern additionally proves the number appears *in the place* the statistic belongs to
  ("high-burn minus unburned difference is **+9.55 percentage points**", "95.99% of valid replicates").
  `numbers_of` compares numeric tokens so a captured `95.99` agrees with the literal `95.99%` while
  `+0.55` and `-0.55` stay different claims.
- **Any interval literal outside the two frozen ones fails.** One regex over the block, so retuning a
  bound in prose is caught rather than argued about.
- **The prose can only be qualified to a design that did not move.** `preregistered_design_not_drifted`
  re-reads the frozen report's cutoff (0.8, permitted set [0.8]), cluster replicates 50,000, cluster seed
  20261025, cluster count 23, tract reference 50,000/20261024, verdict `exploratory` and
  `quantitative_claim_retained: false`; a tamper that claims the analysis used 0.75 or 25,000 draws fails.
- **Pinned hashes fail closed.** `load_frozen` raises if a frozen artifact no longer matches its pin, so
  the writeup cannot be qualified to a report that changed underneath it.
- **Prohibitions are checked at access, not in prose.** The audit funnels every read through
  `read_text`/`read_bytes` and gates that the opened set equals the five-file allowlist (two writeups,
  two frozen reports, one frozen config) and that no opened path carries a reference-layer/label/uploader
  token; leaderboard constants are scanned leaf-wise on the emitted report so `0.0955…` cannot self-hit
  the retired `0.0911`. An earlier line-based prose scan produced pure self-hits (it flagged this audit's
  own docstring for the word "leaderboard") and was replaced.
- **Negation is clause-scoped.** "It does **not** claim that the organiser used a 2018 Microsoft release
  … to construct its hidden target" is a disclosure, not a claim, so `is_affirmative` looks for a negation
  marker in the clause carrying the match; sentence-level negation was too permissive and let the causal
  tamper escape.
- **The generated writeup is verified, not trusted.** `generated_writeup_matches_source_block` compares
  the assembled block with the source block after collapsing heading depth, so assembly cannot quietly
  drop the qualification.

Commands and output, all from the competition repo:

- `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 python scripts/audit_postfire_writeup_cluster_qualification.py`
  → `PASS post-fire cluster qualification: every inferential statement about the high-burn versus
  unburned difference carries the county-cluster interval and the exploratory verdict at first mention,
  both writeups agree with the frozen reports, and the preregistered design is unchanged`; summary
  `documents_audited 2, statements_examined 80, inferential_statements 12,
  unqualified_inferential_statements 0, gates_all_passed true`.
- `... python scripts/audit_postfire_writeup_cluster_qualification.py --check-only` → `unchanged`
- `... python scripts/build_final_writeup.py` (reportlab 4.4.10) → regenerated markdown/PDF/record;
  `... python scripts/verify_writeup_submission.py` → `PASS writeup submission packet: assembled,
  hash-tied and honest about its pending operator inputs; staged only, nothing was submitted to the organiser`.
- Row's own delivery commands: `... python scripts/audit_writeup_claim_evidence.py --write-inventory` →
  `wrote reports/writeup-claim-inventory.json with 88 claims`; the affected claim re-verified with
  `... python scripts/verify_writeup_claim.py --claim bias-discovery-006` → `PASS claim
  bias-discovery-006: 7/7 numeric tokens covered by 2 named evidence artifact(s); anchor=True
  hashes=True pointer=True accuracy_ok=True prohibited_ok=True hedge_ok=True` (one changed claim, because
  the edit rewrote one paragraph without adding or removing any).
- Cascade, all green after the edit: claim audit `PASS v2 writeup claim audit` (88 claims, 1036/1036
  numeric tokens covered, `failed_gates: []`); discovery prize criteria `PASS discovery prize criteria
  audit`; county-bootstrap `--check-only` → `unchanged` (its own `writeup_qualification_matches_verdict`
  gate still holds with the new wording); full suite
  `/home/martin/hddProjects/langywrap/uv run --quiet --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pytest python -m pytest -q`
  → `446 passed, 33 subtests passed` (31 new).
- Negative controls, nine preregistered cases, each exiting non-zero with the expected gate:
  `strip_cluster_interval` → `frozen_interval_literals_present`; `strip_exploratory_verdict` →
  `exploratory_verdict_near_finding`; `unqualify_first_mention` → `unqualified_inferential_statements_zero`;
  `perturb_cluster_bound` → `no_unfrozen_interval_literal`; `drop_design_contrast` →
  `tract_and_cluster_designs_distinguished`; `generated_block_diverges` →
  `generated_writeup_matches_source_block`; `cutoff_retuned` and `seed_or_replicates_changed` →
  `preregistered_design_not_drifted`; `causal_claim` → `no_causal_or_hidden_target_claim`. An unknown
  tamper name is refused, and a test hashes all seven committed inputs/outputs before and after running
  every tamper to prove a tamper run never rewrites a committed file.

Determinism: two independent runs produced byte-identical JSON (`561cc05249e77778…`) and Markdown
(`5b2f48754516541e…`) equal to the committed files; the report serialises no wall-clock value
(`report_has_no_temporal_field`, plus a test asserting `wall_clock_hits == []`), is strict JSON with no
`NaN`, and `--check-only` also detects a mutated committed report without touching it.

One test from the earlier county-bootstrap row needed a principled correction, not a weakening: it
asserted that no script outside that audit mentions the frozen report at all, which the new
writeup-qualification consumer legitimately violates. It now enforces the promised invariant — the
reader set must be disjoint from the preregistered `model_chain_scripts` list in
`config/v2-strata-descriptive-preregistered.json`, and any consumer outside the model chain must be named
in an explicit allowlist with its reason — and adds a test that the consumer itself never re-runs the
bootstrap (`multinomial`, `default_rng` absent), never uploads, and must carry the
`preregistered_design_not_drifted` gate.

FORBID honoured: no leaderboard-driven edit (the only platform constants in the repo are named as
prohibited value tokens and scanned leaf-wise in the emitted report), no hidden-target or causal claim
(negation-aware clause scan, causal tamper fires), no removed reference layer or label (five-file access
allowlist), the preregistered cutoff/seed/replicates are unchanged and no resampling was re-run, and no
upload was made. The exploratory finding was kept, not deleted, because the interval crossing zero is a
limitation to state rather than a result to hide.

## 2026-10-25 — external lab frozen against the spec, its exclusion policy proved, and the operator decision that authorises it wired in (build not executed)

- Item: `bias60-external-lab-build` (claimed by the steward at 09:35Z this session). Added
  `config/bias60-lab-preregistered.json` (SHA-256 `e7106107d63c5bd8…`, plan id `bias60-external-lab`),
  `src/bias60/lab_plan.py` (`46a998ee80685f9f…`), `scripts/report_lab_readiness.py`
  (`d1a5147f810bde79…`), `reports/lab-readiness.json` (`1469242e3d0a662d…`, 40,787 bytes),
  `docs/decisions/bias60-lab-readiness.md` (`3ca707b2ead4e66f…`, 10,665 bytes) and
  `tests/test_bias60_lab_plan.py` (`c1e384a1f569e373…`, 41 tests / 58 subtests).
- Command: `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --with numpy==2.5.4 --with pyarrow==26.0.0 python scripts/report_lab_readiness.py` -> `PASS lab readiness: the operator decision of 2026-10-25 09:35Z is quoted verbatim from the spec and its four conditions are enforced by named mechanisms, the lab design is frozen against the spec, the study exclusion rejects every official GEOID, every template GEOID and every near-study candidate, no reference-layer byte exists, no model or submission path can read the lab, only the weight vector may cross into a submission, and the build itself has not been executed this turn`. `--check-only` reports `unchanged`, and a second independent run is byte-identical in both the JSON and the Markdown.
- MEASURE-BEFORE: the spec moved under the lane. Orchestrator commit `901d5e3c8ee89eb9995e2adb15a3fee6c48b49ee` added an operator decision block to the head of `docs/research/bias-60-ideas-2026-10.md`, changing its SHA-256 from `8521753accde328e…` to `f150649b34caebef…`. Both bias60 pins therefore failed closed, and the whole lab row had been designed against the pre-decision rules posture. The decision reads: "No question goes to the organisers. Track B is allowed: fit the weights in the external lab, on geographies outside the four study regions with every study tract and its neighbours excluded, then apply only the learned weight vector to estimators computed from the provided data. Reference layers or equivalent copies are never computed on, joined to, or used to approximate targets for study tracts. Disclose the lab method in the writeup."
- Spec re-pin, done as part of this row: `config/bias60-harness-preregistered.json` (`3987c41b6692cc1a…`) and the lab config now carry `spec.sha256 = f150649b34caebef…` plus a `spec_amendments` block recording the decision verbatim, the deciding commit, the superseded hash and the five consequences. `scripts/build_ideas_registry.py` -> `PASS ideas registry` (`config/ideas.json` `8669ca2b6551b502…`), `scripts/run_bias60_ideas.py` -> `PASS bias60 harness` (`reports/ideas-harness.json` `8b38d7a4ce14418c…`), `scripts/report_ideas_orthogonality.py` -> `PASS ideas orthogonality` (`reports/ideas-orthogonality.json` `4de5fc0faf3bdbbd…`). No stale `8521753a…` reference survives outside the two `supersedes_spec_sha256` fields.
- MEASURE-AFTER (design frozen and checked against a fresh parse of section 4, which is re-read every run): 9 lab groups with the spec's own CV units and approximate tract counts (TX-W 890, AZ-R 170, OK-R 130, NM 610, CA-F 2500, NV 700, AR-KS 1500, LA 1300, CO-UT 2100; `group_cv_units_match` and `approx_tract_counts_match` both true), 6 core groups, 10 registered sources each carrying URL, version pattern and null `retrieval_date_utc`/`sha256` with `downloaded: false`, 7 volume-table rows, USGS structure codes 73002-73007/74001/74026 covering the spec's set, recipe in EPSG:5070 with all four reference components declared and every gap written as `clip(1 - O/E, 0, 1)`, and the two RULES.md self-checks frozen (state road-ratio band `[0.71, 1.59]`, hospital Overture/reference ratio target 12.0) with `self_check_status: not_run_pending_ruling`.
- Study exclusion, measured from provided data only: 9,379 official scored GEOIDs and 9,794 platform-template GEOIDs (the 415 eastern-WA rows) are excluded through `scripts/audit_v2_component_masks.py::official_scored_ids` and the GEOID column of `SampleSubmission.csv`; 298 study counties across FIPS 04, 06, 35, 40 and 48 are enumerated from the four provided strata tables (Maricopa 1,593 rows / 16 counties, Northern CA 591 / 24, Eastern OK 1,192 / 73, South-Central TX 6,010 / 185); the neighbour rule is an 8 km centroid buffer in EPSG:5070 applied before any layer is loaded for a candidate. Self-test panel: an official GEOID and a WA template GEOID are rejected as `official_scored_or_template_geoid`, a study-county tract sitting on a study point and candidates 1 km and 5 km away are rejected as `within_neighbour_buffer_of_study_tract`, and only the 500 km candidate is accepted.
- Design decision the measurements forced: whole-county exclusion is **off** by default and recorded, not assumed, because the provided packages touch 298 counties and dropping them all would empty AZ-R (Apache, Coconino and Navajo are all study counties) and gut TX-W, OK-R and CA-F, contradicting spec 4.2 which asks for the tracts of those counties that are *not in the sample*. `county_rule_self_test` still proves the rule works when a build turns it on (`enabled_policy_rejects_study_county` true, `configured_policy_defers_to_the_buffer` true). Instead, every group whose state holds study tracts must be retrieved county-filtered: required for TX-W, AZ-R, OK-R, NM and CA-F and correctly absent for NV, AR-KS, LA and CO-UT.
- Authorization posture, reported as it now is: `operator_decision_present_verbatim_in_spec` re-parses the decision block from the memo each run and fails if the stored quote drifts; `external_use_recorded_as_authorized` requires `authorized_by_operator_decision`, a null blocking row, the withdrawn organiser question and the deciding commit; `operator_conditions_enforced` requires all four conditions with a named enforcement mechanism each; `track_b_crossing_surface_is_bounded` allows only `weights`, `intercept`, `shrinkage`, `component_weights`, `fit_provenance` and `operator_decision_quote` into a submission artifact and refuses anything carrying labels, GEOIDs, per-tract or region-mean values; `writeup_disclosure_required` names `docs/writeup/methodology.md` and `docs/writeup/bias-discovery.md` and requires the operator decision itself to be stated. The four RULES.md sentences that prohibit reference layers or equivalent copies and scope extra datasets to the Discovery prize stay quoted verbatim (`rules_citations_are_verbatim`) with the tension written down, because it is the operator decision, not a re-reading of the rule, that authorises the lab. 27 gates, all true.
- Negative controls: 12, all firing their preregistered gate -- `decision_fabricated` -> `operator_decision_present_verbatim_in_spec`, `conditions_dropped` -> `operator_conditions_enforced`, `weights_smuggle_labels` -> `track_b_crossing_surface_is_bounded`, `probe_reads_content` -> `availability_probe_stays_head_only`, `lab_data_present` -> `no_prohibited_content_on_disk`, `county_filter_flag_removed` -> `county_filtered_retrieval_required_recorded`, `buffer_removed` -> `neighbour_buffer_policy_rejects_study_and_adjacent_tracts`, `exclusion_emptied` -> `study_geoids_fully_excluded`, `source_field_dropped` -> `source_register_complete`, `lab_root_in_repo` -> `lab_root_outside_repo`, `model_reads_lab` -> `model_and_submission_paths_cannot_read_lab`, `question_drops_the_spec_question` -> `operator_question_covers_the_spec_question`. Tamper mutations run on an in-memory copy; a test hashes all five committed products before and after the whole tamper sweep.
- Firewall and access: 11 model-chain and submission scripts scanned (the preregistered `model_chain_scripts` list plus `scripts/submit_zindi.py`) against 12 lab tokens including `prisecroads`, `usbuildingfootprints`, `nationalmap`, `cbp23`, `zbp23`, `tl_2020_`, `tl_2024_` and `overturemaps-us-west-2`, zero hits; 19 opened paths disclosed in the report (RULES.md, the four strata tables, the platform template, the spec, the config and the scanned scripts) with no reference layer, label, score placeholder cell or platform endpoint among them.
- FORBID honoured and the row left open on purpose: `reference_layer_bytes_downloaded_in_this_row: 0`, `uploads_performed: 0`, `$COMPETITION_WORKSPACE/lab` absent and untracked in git, the HEAD-only availability probe is legitimate now but was not performed, no study tract is computable, and no lab value can reach a candidate CSV, `reports/candidates.json`, `reports/final-two.json` or the uploader. The row is **not** closed: the acceptance artefacts (lab labels for at least 5 groups with per-component definedness counts and the two recipe self-checks run) require the retrieval-and-compute execution, which needs several gigabytes and the study-tract geometry for the buffer; and the Track A pre-registration in `bias60-track-a-combiner` is still recorded as binding on sequencing, so the first lab number should land after it. Full suite after this row: `487 passed, 91 subtests passed`.

## 2026-10-25 — lab evaluation frozen as a fail-closed consumer contract: it recovers a known truth on a synthetic lab and publishes nothing when the real lab is absent

- Item: `bias60-lab-evaluation` (P1, raised to P0 by the steward at 09:35Z). Added `config/lab-evaluation-preregistered.json` (SHA-256 `efc69dda27952967…`), `src/bias60/lab_eval.py` (`b70b6b243cc02a8f…`), `scripts/evaluate_bias60_on_lab.py` (`e5bb5f9d3d9f1966…`), `reports/lab-evaluation.json` (`758d5a3158dc0530…`, 11,103 bytes), `docs/writeup/lab-evaluation.md` (`4294a24f6821d863…`, 4,883 bytes) plus `tests/lab_fixture.py` (`2ec10f85d355bdc3…`) and `tests/test_bias60_lab_evaluation.py` (`464d63f0e99d56f3…`, 40 tests / 79 subtests).
- Command: `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --with numpy==2.5.4 --with pyarrow==26.0.0 python scripts/evaluate_bias60_on_lab.py` -> `PASS lab evaluation contract: the evaluation is frozen and its rails are proved, and no lab number is published because the lab artifacts are absent`; `--check-only` -> `unchanged /home/martin/hddProjects/competitions-repos/zindi-bias-bounty/reports/lab-evaluation.json`.
- MEASURE-BEFORE: `$COMPETITION_WORKSPACE/lab` does not exist (`ls: cannot access '.../lab'`), and `bias60-external-lab-build` is still open, so there are no lab labels, no lab estimator emissions and therefore no honest lab RMSE to report. The row could have been marked blocked and left empty. Instead it became the consumer that pins what the build must deliver, because an evaluation written *after* a build gets to be whatever the build happened to produce.
- Consumer contract now frozen in code and config: `manifest.json`, `tracts.parquet` (`lab_tract_id, lab_group, state_fips, county_fips, centroid_x, centroid_y, nearest_study_tract_km`), `estimators.parquet` (`lab_tract_id, lab_group, idea, family, component, channel, value, defined`), `labels.parquet` (`lab_tract_id, lab_group, component, label, defined, recipe_variant_id`) and `defineness.json`; column sets are exact, so a lab with an extra column fails rather than being quietly accepted. Channels name their target: `composite` scores every implemented idea against the composite label, `target:<COMPONENT>` scores only that component's ideas, which is spec 3.2's two-level fit (component layer then a 3-weight composite layer, about 60 + 3 parameters).
- Spec 3.2-3.3 implemented as measured machinery: Lawson-Hanson NNLS on the augmented system so `w >= 0` with ridge-toward-equal `min Σ(y − ŷ)² + λ‖w − w0‖²` and the optional convex `Σw = 1` row; λ by county-blocked inner folds (group-blocked fallback recorded as `inner_fold_mode`, never silently); outer leave-one-state-group-out RMSE per held-out group plus worst case; partial correlation with the label given all other ideas; drop-column ΔRMSE; residual correlation matrix of `r_i = y − e_i` with its off-diagonal mass; Krogh-Vedelsby ambiguity; MTMM within/between-component mean |rho|; bootstrap stability selection over whole groups (200 draws, seed 20261025, keep if nonzero in ≥60%); Shimodaira `p/(1−p)` shift weights clipped at the 1st-99th percentiles; Meyer-Pebesma area-of-applicability outside share.
- Proof that the machinery is right, on a seeded synthetic lab whose truth is known (`tests/lab_fixture.py`, 6 groups x 40 tracts, ideas and components read from `config/ideas.json` so it cannot drift from the registry): with a lab present the report status becomes `measured`, all 10 measurement gates pass and acceptance is met; on the composite channel the fit recovers the injected weights — fitted `[0.5024, 0.3010, 0.1966]` against truth `Mx4 0.5, Sc5 0.3, Tm1 0.2` (test tolerance 0.03), grouped-CV RMSE `0.009537` against equal weights `0.042996`, win rate `1.0`, λ chosen `0.03`, residual off-diagonal mass `0.611212`, MTMM between-component mean |rho| `0.927032`, Krogh-Vedelsby `0.027989`, stability selection keeps all three; A1 and A2 are `scored`, while A3 and A4 are `not_runnable` naming the ideas they need (`X1, Sc2, Mx5, P3, S3, D2, X5` and `T5, Tm4, M1, M5`) because only Tm1/Mx4/Sc5 are implemented. Single-member component channels tie with equal weights and are recorded as `reported_as_not_beating_equal_weights`, not as wins.
- Fail-closed rails, 18 contract gates: the committed report carries `measurement_status: not_run_missing_lab`, `access.lab_rows_opened: 0`, `summary.weight_vectors_published: 0`, `acceptance.met: null`, all 10 measurement gates null, and `no_fabricated_measurement` fails the moment any float appears in the measurement block. `tests` also assert a half-built lab raises (deleted `labels.parquet` or `manifest.json` -> `ValueError`) and an unexpected column is rejected, rather than scoring missing rows as zero.
- Study-side rails are explicit, not implied: `weights_not_applied_to_study_tracts` requires `study_predictions_emitted == 0` and `study_idea_parquets_opened == 0`; the access log holds exactly three paths (`ORCHESTRATOR:docs/research/bias-60-ideas-2026-10.md`, `config/ideas.json`, `config/lab-evaluation-preregistered.json`) and no reference-layer token appears in it; `crossing_surface` allows only `weights, intercept, shrinkage, normalisation_constants, lab_commit, lab_manifest_sha256`, each weight vector carrying its own SHA-256; and while `bias60-track-a-combiner` has recorded no Track A pre-registration, `weight_vector_release` stays `withheld`.
- Negative controls: 13, all firing their preregistered gate — `study_geoid_in_lab` -> `study_geoid_exclusion_holds` (an official scored GEOID injected into the lab), `nearest_distance_understated` -> `nearest_distance_buffer_respected`, `self_check_fabricated` -> `recipe_self_checks_passed` (road-ratio `4.0` claimed as passing outside the frozen `[0.71, 1.59]` band), `undefined_rows_zero_filled` -> `undefined_never_scored_as_zero`, `missing_source_hash` -> `manifest_provenance_complete`, `too_few_groups` -> `groups_held_out_adequate`, `labels_outside_unit` -> `rows_consistent`, `weights_smuggle_lab_values` -> `crossing_surface_is_bounded`, `measurement_fabricated_without_lab` -> `no_fabricated_measurement`, `study_side_prediction_emitted` -> `weights_not_applied_to_study_tracts`, `release_before_track_a_preregistration` -> `track_a_release_gate_recorded`, `spec_pin_drift` -> `spec_hash_matches_pin`, `shift_claimed_without_allowlist` -> `shift_correction_status_declared`. Unknown tampers raise; a test hashes all five committed products before and after the whole sweep.
- Design decision recorded rather than smuggled: the Shimodaira shift correction and the AOA measure are implemented and unit-tested (shift weights give the expected effective sample size and clip bounds; AOA flags `>0.9` of far-away study tracts) but are **not wired into the report**, because they need study-side covariates and this row opens no study-side file. `shift_correction.status` is `not_wired` with `requires: study_side_label_free_feature_allowlist`, and the gate fails if anyone reports it as measured without that allowlist.
- FORBID honoured: no reference layer read (`reference_layer_bytes_read_in_this_row: 0`), no label or hidden target for any study tract, no upload (`uploads_performed: 0`), no leaderboard constant or `config/calibration.json` (scanned as forbidden value tokens in the emitted report), no per-tract tuning on study data, no wall-clock field, strict JSON, byte-identical repeat runs. The row is **not** closed: acceptance needs the implemented ideas evaluated on at least five *real* held-out groups, which is `bias60-external-lab-build`'s artifact set, and Track A pre-registration should land before the first lab number. Full suite after this row: `527 passed, 170 subtests passed`.

## 2026-10-25 — the lab evaluation measures its own prerequisite: 4,748 real candidate tracts run through the frozen exclusion, and the answer is that two planned lab groups cannot be folds

- Item: `bias60-lab-evaluation` (P1), second dispatch of the same row. Closed `lead`: the evaluation itself landed last turn (`ecadbad`); this turn landed what tells the build whether it can succeed at all. New `config/lab-prerequisites-preregistered.json` (`64e1c41f4684f53d…`), `scripts/probe_lab_prerequisites.py` (`5ced3bd943e2afa9…`), `reports/lab-prerequisites.json` (`3e6b9fb7fa6ad2b9…`, 112,538 bytes, self-reported `report_sha256` `432f1a9db0718de0…`), `docs/decisions/lab-prerequisites.md` (`b391361ad8c695c2…`, 6,831 bytes), `tests/test_probe_lab_prerequisites.py` (`5a1001703c1f1029…`, 18 tests / 32 subtests); amended `scripts/evaluate_bias60_on_lab.py` (`a056f2033a2d1436…`), `config/lab-evaluation-preregistered.json` (`a185192e244d6dea…`), `reports/lab-evaluation.json` (`90b8e02de54d2e65…`), `docs/writeup/lab-evaluation.md` (`c7d7756c56d84207…`), `src/bias60/lab_plan.py` (`e106db411a7289a3…`), `tests/test_bias60_lab_plan.py` (`c33bf189bd3e2e0f…`).
- Command: `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run --with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 --with pyshp python scripts/probe_lab_prerequisites.py` -> `PASS lab prerequisites: 5 of 10 registered sources answer anonymously, 3491 of 4748 candidate tracts survive the frozen exclusion across 5 planned groups (112428536 bytes of public census geography, no reference layer); findings: 1 group(s) short of the fold floor, 4 source(s) not answering, lab plan needs amendment: True`; the same command with `--check-only` -> `unchanged /home/martin/hddProjects/competitions-repos/zindi-bias-bounty/reports/lab-prerequisites.json`. The evaluation then prints `PASS lab evaluation contract: ... no lab number is published because the lab artifacts are absent` with 20 contract gates and 15 negative controls, `--check-only` unchanged.
- MEASURE-BEFORE: the lab still does not exist and the build had never been measured against reality — the nine planned state groups came from the spec's prose and the ten registered sources had never been asked whether they answer. That is how a lane spends 10-25 GB and then discovers two of its five cross-validation folds are empty.
- Real measurement, not a rehearsal. The probe downloads public Census tract geography only (`tl_2024_<ss>_tract.zip` for AZ, CA, OK, TX, WA as study geography and NV, CO, UT, NM as candidate geography: 112,428,536 bytes, per-file SHA-256 recorded), establishes 9,793 study interior points in EPSG:5070 from the official and template GEOID sets (1 excluded GEOID has no interior point in the census file, disclosed), and runs the frozen spec-4.1 exclusion — every official GEOID, every template GEOID, 8 km centroid buffer — over 4,748 real candidate tracts in 5 of the 9 planned groups: `NV` 674/674 accepted at a minimum of 33.7 km, `CO-UT` 2,163/2,163 at 162.9 km, `NM` 611/612 at 37.1 km, `AZ-R` 30/94 accepted (59 official, 5 inside the buffer) at 9.2 km, and `OK-R` **13/1,205** accepted because 1,192 of its candidates are the study sample itself.
- The finding that matters: `OK-R` cannot be a leave-one-group-out fold. 13 survivors against a floor of 30, and `AZ-R` survives only exactly at 30 against 170 planned (ratio 0.176). The frozen plan's tract numbers for those two groups came from the spec's prose rather than the tract inventory, and no earlier artifact could have caught it. The probe does not hide that behind a failed run: all 18 contract gates pass and the negative result is *required to be disclosed* — `shortfalls_disclosed` and `source_availability_disclosed` fail if a group below the floor or a dead source is dropped from the findings, and the evaluation re-derives the shortfalls from the probe's own per-group numbers so the consumer cannot be pacified by an edited summary.
- Four of ten registered sources do not answer, and the probe records what answers instead. `tiger_prisecroads_2024` 404 at TIGER2024, 2023 and 2022 for the probed state while the per-county `tl_2024_<ssccc>_roads.zip` answers 200, so the recipe's road reference must move to county ROADS files; `overture_package_release` 404 at the guessed release id while the bucket listing answers 200, so the release id has to be enumerated rather than assumed; `cbp_2023` 400 at the census.gov page while the `www2.census.gov` programs-surveys datasets directory answers 200; `hud_usps_crosswalk` returns 202 to an anonymous HEAD; `hospital_check` registers a source family with no URL at all. Rails: HEAD only, `content_bytes_read: false`, `credentials_used: false`, one allowlisted host, and every downloaded URL must equal the registered tract pattern exactly — the census file's TIGER-vintage prefix is the one reference-layer token exempted, and only for that exact filename.
- Speed change that had to be proved: the exclusion policy's buffer scan now uses a uniform grid with cell size equal to the buffer, built lazily, because 4,748 candidates against 9,793 study points is otherwise a 46-million-comparison Python loop. `tests/test_bias60_lab_plan.py::SpatialIndexTests` asserts the grid returns exactly what the brute-force scan returns on 2,000 random candidates against 400 study points, including a study point one cell away, so the index cannot open a hole in the neighbour rule.
- 12 negative controls on the probe fire their preregistered gate: a source probe that reads bytes, one that uses credentials, a skipped registered source, a download off the allowlist, a reference-layer file on disk, a cache relocated into the lab root, `buffer_removed` -> `no_accepted_tract_within_buffer`, `official_geoid_accepted` -> `no_official_geoid_accepted` (an official scored GEOID parked in the Nevada file survives and is caught), a study set with no interior points, a disclosed-shortfall list with the shorted group removed, an injected wall-clock field, and hidden source unavailability. An unknown tamper raises. A first draft of the cache tamper actually created `lab/prereq-cache` in the workspace; it was removed, the invariant tests still assert the lab root is absent, and the tamper now relabels the cache instead of writing to it.
- FORBID honoured: 0 reference-layer bytes fetched (`no_reference_layer_bytes_on_disk`, with the exact-pattern exemption documented), 0 study-region layer retrievals, 0 credentials used, 0 uploads, nothing written into the lab root, no wall-clock field, strict JSON, byte-identical repeat runs. Full suite after this row: `548 passed, 208 subtests passed in 304.07s` (was 527 passed, 170 subtests).
- Rows filed with the shared command, quoted for the steward: `python3 -I "$ORCHESTRATOR_ROOT/scripts/backlog_add.py" --owner comp-zindi-bias-bounty --slug bias60-lab-plan-amendment --priority P1 --task-type bugfix ...` -> `appended bias60-lab-plan-amendment for comp-zindi-bias-bounty`, carrying the OK-R and AZ-R numbers and the four dead source patterns; and `--slug bias60-lab-evaluation-run --priority P1 --task-type model` -> `appended bias60-lab-evaluation-run for comp-zindi-bias-bounty`, carrying the acceptance a synthetic lab cannot satisfy. This row closes `lead`: the machinery and the prerequisite measurement are delivered, and the lab numbers themselves still wait on `bias60-external-lab-build`.

## 2026-10-25 - bias60-external-lab-build (first staged build, 2 of 5 groups)

Row: `.langywrap/backlogs/competitions.jsonl:bias60-external-lab-build`, disposition
`actioned`, not self-closed: acceptance needs lab labels for `>= 5` groups and this turn staged
two, so `bias60-external-lab-build` stays open.

### What ran

* `COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" uv run --with numpy==2.5.4 --with pyarrow==26.0.0
  --with pyproj==3.8.0 --with shapely==2.2.0 --with pyshp python scripts/build_bias60_lab.py`
  -> `PASS lab build: 2 of 5 required groups staged from 42 retrieved files (828,814,965 bytes
  transferred) with road labels and the organiser road-ratio self-check reported; no study tract was
  measured and nothing was promoted to the lab root`
* `--check-only` -> `unchanged reports/lab-build.json`, `unchanged docs/decisions/lab-build.md`.
* `scripts/report_lab_readiness.py`, `scripts/probe_lab_prerequisites.py` and
  `scripts/evaluate_bias60_on_lab.py` all re-run: PASS, PASS, PASS and `--check-only` unchanged.

### Measured, from retrieved bytes

| group | candidates | accepted | rejected by reason | nearest study tract | road defined | Overture:TIGER ratio |
| --- | --- | --- | --- | --- | --- | --- |
| NV | 674 | 674 | - | 32.637 km | 433 (64.24%) | 1.013391 (inside [0.71, 1.59]) |
| NM | 612 | 610 | 1 official GEOID, 1 study county | 34.971 km | 506 (82.95%) | 0.654624 (below band, inside the 0.15 tolerance) |

* 94 files staged under `$COMPETITION_WORKSPACE/lab/staging/{NV,NM}`: per-group `manifest.json`,
  `tracts.parquet`, `labels.parquet`, `defineness.json` plus the retrieved census zips, TIGER county
  roads zips and Overture row-group slices with their provenance sidecars.
* Retrieval: 41 unique URLs, 2 hosts (`www2.census.gov`, `overturemaps-us-west-2.s3...`),
  828,814,965 bytes transferred, `credentials_used: false`. 4,189,898,173 bytes of Overture objects
  were **listed**; 733,420,392 compressed bytes were read from 6 shards.
* Overture pruning measured: of 90,869 NV segments in shard ranges, 37,893 segment-tract
  intersections were actually clipped, and only the seven preregistered columns of the
  bbox-overlapping row groups were read.
* Road recipe: observed = Overture segments with `class in {motorway, trunk, primary, secondary}`
  and `subtype == road`; reference = TIGER 2024 county `ROADS` filtered to
  `MTFCC in {S1100, S1200}`; label = `clip(1 - observed_km / reference_km, 0, 1)`, undefined when
  reference length is zero. NM 0.654624 is reported as **below** the organiser band, not smoothed.

### Two measured findings that changed the build

1. **`retrieved_files` was fabricated provenance.** The first version recorded one manifest entry per
   preregistered source, not per retrieved file: 12 entries, 6 of them for an Overture object whose
   real per-shard SHA-256 was never computed. The builder now records every file it fetched, with its
   own URL, version, retrieval date, size and SHA-256 (42 files), and the
   `retrieved_files_are_the_files_that_were_fetched` gate recomputes the Overture shard list from the
   cache directory, so a manifest that names fewer files than are on disk fails.
2. **A state-level download of a removed reference layer necessarily covers the study tracts inside
   it.** The readiness plan leaves whole-county exclusion off, because dropping all 298 study counties
   would empty the spec 4.2 groups; that is a statement about *candidates*, not a permission to
   download over a study region. The build now enables county exclusion for its own retrieval, and the
   NM group shows it working (1 study-county tract rejected). Recorded in
   `exclusion.county_rule` with `default_in_the_plan: false` and gated by
   `county_rule_enabled_for_retrieval`.

### Held components, with the measured reason

* `buildings`: Microsoft Building Footprints registered repo returns zero usable assets for CONUS
  (the repo is a documentation-only archive), so no building component was fabricated.
* `poi_facilities` / `poi_establishments`: every USGS National Structures endpoint
  (`nationalmap.gov`, `coast.noaa.gov`, `scenc.usgs.gov`, `databasin`, `sciencebase`) returns 403,
  404 or a SPA shell with zero objects, and CBP 2022 publishes no tract-level ZBP file.
* `estimators.parquet` is deliberately absent: Sc5 needs the provided filtered **and** unfiltered roads
  layers, Mx4 needs `buildings.sources`, Tm1 needs `strata.carbonplan_buildings`; none exists outside
  the study regions, and inventing the analogue after seeing the labels is the tuning this row forbids.
  Filed as `bias60-lab-estimator-analogues`.

### Rails

* `config/lab-build-preregistered.json` (file hash `064ac7d2d8a43613`) freezes the source pins,
  allowed hosts, allowed URL patterns, denied code suffixes, budget caps (4,000 files / 8 GB),
  component definitions, self-check bands and 10 negative controls.
* `reports/lab-build.json` (`6bb4b6653130ca1c`, 10,447 bytes): 24 gates, all true;
  `promoted: false`, `promote_blocked_reason` names the missing groups, `root_artifact_names` lists
  the five evaluator artifacts and none exists.
* Exclusion evidence: 9,794 official/template GEOIDs and 298 study counties excluded, 8 km buffer in
  EPSG:5070, measured nearest accepted lab tract 32.637 km.
* `estimator_reader_and_submitter_name_no_lab_token`: the 11 model-chain and uploader scripts contain
  no lab path, shard name or reference-layer token; `opened_paths` shows only the two preregistered
  configs, the prerequisite report and the two staged manifests.
* The 12 negative controls declared in `config/lab-build-preregistered.json` are the map the test
  reads (`EXPECTED = dict(CONFIG["negative_controls"])`), so a control cannot be added to the config
  without firing, and each one does: `study_geoid_in_url` -> `study_geoid_never_appears_in_a_url`,
  `study_county_in_url` -> `study_county_never_appears_in_a_url`, `off_allowlist_host` ->
  `retrieval_stayed_on_the_allowlist`, `downloaded_code_executed` ->
  `no_downloaded_code_was_executed`, `lab_value_reaches_submission` ->
  `lab_values_never_reached_a_submission_artifact`, `reference_computed_on_study_tract` ->
  `no_study_tract_was_measured`, `measurement_without_lab_bytes` -> `no_measurement_without_bytes`,
  `temporal_field_added` -> `report_has_no_temporal_field`, `self_check_fabricated` ->
  `recipe_self_checks_passed`, `undefined_rows_zero_filled` -> `undefined_never_scored_as_zero`,
  `missing_source_hash` -> `manifest_provenance_complete`, `accepted_tract_inside_buffer` ->
  `nearest_distance_buffer_respected`. An undeclared tamper (`invented_a_number`) is refused with
  `ValueError`, `groups_are_not_the_preregistered_ones` is accepted as a declared extra case and fires
  `groups_are_the_preregistered_ones`, and a report built without manifests carries no measurement
  block and fails `no_fabricated_measurement`.
* Promotion refuses with `promotion requires 5 ready groups; 2 are staged: NM, NV`.

### Tests

* `tests/test_bias60_lab_build.py` (`29c6413a9f3166c2`, 25 tests, 12 subtests) builds a synthetic lab
  (synthetic tract shapefile, synthetic TIGER roads shapefile, synthetic Overture Arrow tables injected
  through the same `lister`/`reader` seams the real S3 path uses) into
  `$COMPETITION_WORKSPACE/lab-test`, never the real lab root: exclusions by reason, the clip-to-tract
  maths (one tract half covered, one fully, so both ends of the recipe are exercised), the undefined
  rows never scored as zero, exact column sets, provenance completeness, budget enforcement before the
  bytes arrive, URL safety (host, allowlist, denied suffix, study GEOID/county), promote refusal,
  determinism, and a corrupt cache caught by its recorded hash.
* Two earlier test defects found by running them: the fixtures wrote `shapefile.Writer(...).bytes()`
  which pyshp 3 does not provide (they now write real shapefiles to a temp dir and zip them), and
  `HashVerificationTests` wrote into the real lab root (it now uses `TEST_CACHE` under `lab-test`).
* `shapely.ops.transform` (deprecated) replaced by a vectorised `shapely.transform` helper; the real
  build re-run afterwards produced byte-identical reports and `--check-only` unchanged, so the
  projection change moved no number.
* Row tests: `109 passed, 155 subtests passed` across `test_bias60_lab_plan.py`,
  `test_bias60_lab_build.py`, `test_bias60_lab_evaluation.py`.
* Full suite: `573 passed, 220 subtests passed` (`python -m pytest -q` under uv with
  numpy/pyarrow/pyproj/shapely/pyshp/pytest).

### Readiness semantics corrected for the post-build world

The readiness report had asserted "no reference-layer byte exists" and
`build_not_executed_this_turn`, both of which became false the moment this row legitimately built
bytes. Rather than leave a stale claim committed:

* `no_prohibited_content_on_disk` now means: lab root is staging-only, no promoted artifact, nothing
  keyed to a study region (checked against the 9,794 official/template GEOIDs and the four region
  slugs) - and it still fires on the `lab_data_present` tamper.
* `readiness_performed_no_retrieval` replaces `build_not_executed_this_turn`: readiness itself performs
  no GET and its report carries no retrieved-file provenance; `build_output_is_never_read_by_readiness`
  proves readiness never opens a staged file.
* `no_lab_number_exists` now scans the committed readiness report and Markdown for the lab measurement
  tokens (`"lab_tract_id":`, `"rmse":`, `"observed_km":`, `"reference_km":`, `"fit_provenance":`,
  `"stability_selection":`) instead of trusting a file count.
* `recipe.self_check_status` moved from `not_run_pending_ruling` to
  `road_ratio_measured_for_staged_groups` with a note pointing at `reports/lab-build.json`, and the
  gate accepts either status only when the note is present.

### Rows filed

* `bias60-lab-estimator-analogues` (P1, model) - preregister the raw-source analogue for each
  implemented idea, or record `not_evaluable_in_lab` with the reason, before any fit reads a label.

### Safety

* `$COMPETITION_WORKSPACE/lab` holds only `staging/` output for NV and NM; none of the five root
  artifacts exists, so the evaluator still reports `not_run_missing_lab` and publishes no number.
* No study-region reference-layer retrieval: URLs are checked for the 9,794 official/template GEOIDs
  and the 298 study counties before any request, and retrieval is county-filtered.
* No lab value crosses into a candidate, calibration or uploader path; only the weight vector may, and
  it does not exist yet. `reports/candidates/`, `reports/candidates.json`, `reports/final-two.json` and
  `config/calibration.json` untouched.
* No upload; no leaderboard access; no organiser form; no secret read.

### A reproducibility defect found while making the other tests pass

`tests/test_probe_lab_prerequisites.py::CommittedArtifactTests` began reporting `CHANGED` for
`reports/lab-prerequisites.json` even with no code change. Two consecutive CLI runs produced reports
that differed from each other:

```
.findings.sources_not_answered[2].status   None -> 520
.sources.probed[7].alternates[0].error     TimeoutError -> None
.sources.probed[7].status                  None -> 520
```

The cause was not a code bug in the exclusion or the maths: the availability probe asks the network
live, and the HUD endpoint answered `520` on one round and timed out on the next. A published status
line that can only be produced once is not a measurement the lane can keep, so:

* HEAD answers are now cached beside the census geography cache
  (`$COMPETITION_WORKSPACE/lab-prerequisites/source-probe.json`) and reused; `--reprobe` is the only
  way to ask again, so a re-probe is a deliberate act rather than a side effect of running the script.
* The report discloses how each published status line was obtained: `sources.probed_from` is
  `cache`, `network` or `mixed`, every entry carries its own `probed_from`, and a cached round is
  tied to its exact recorded answers by `sources.probed_from_cache_sha256`.
* A source that registers no URL stays `probe: skipped` with `probed_from: not_probed`; caching must
  not quietly turn "never asked" into "asked and answered".
* New gate `source_probe_answers_are_reusable` (the probe report now carries 20 gates) fails if a
  status line has no provenance or a cached round lacks its 64-hex tie.
* New test `HeadAnswerCacheTests.test_the_published_head_answers_survive_a_rerun` runs the probe
  against a deliberately flaky fake `head` twice on one cache directory and asserts the measurement
  content is identical, the second round asks the network zero further times, the mode label flips to
  `cache`, and skipped sources remain `not_probed`. The pre-existing two-run test now compares with
  the mode disclosure (and the body self-hash that follows it) removed, which is the honest form of
  the invariant: what must reproduce is the measurement, not the record of how it was obtained.

The prerequisite report hash moved twice more during this row (`3e6b9fb7fa6ad2b9` ->
`6c1b36fe960d4897` -> `a4121650a0ff...`), and each move is recorded as a `pin_amendments` entry in
`config/lab-build-preregistered.json` with its reason, rather than being tolerated silently by the
consumer.
