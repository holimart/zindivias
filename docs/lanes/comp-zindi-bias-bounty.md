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
