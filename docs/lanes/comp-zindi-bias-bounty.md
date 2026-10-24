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
