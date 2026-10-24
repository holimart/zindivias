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
