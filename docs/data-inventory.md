# Bias Bounty data inventory

Inventory date: 2026-10-23. Worktree: `zindi-bias-bounty`.

## Scope and access

The authorized workspace is `$COMPETITION_WORKSPACE` (currently
`/home/martin/hddProjects/competitionsall-workspace/zindi-bias-bounty`). Its
`download-manifest.json` lists only `SampleSubmission.csv`; no competition
training/package files are present locally. The challenge package is described
by the lane instructions as cloud-native GeoParquet, approximately 8 GB. This
inventory did not download or scan that package. The Zindi Data tab and its
tutorial could not be accessed using the available lane tools; consequently
package URL, exact file list, licensing text, GeoParquet columns/geometry, and
train/test target schema remain unverified. Do not infer those details from the
sample submission.

The manifest records the sample file as 919,898 bytes, SHA-256
`32fb5c2984317d4abf8a98b88b7bb9d16e9eef1cda9a954c37da0a2cdd367f40`, fetched
from the Zindi competition files API at `2026-10-23T20:33:56+00:00`.

## Verified sample schema

`SampleSubmission.csv` has 17 columns: `GEOID`, `coverage_gap_score`, `region`,
`transport_gap`, `transport_defined`, `building_gap`, `building_defined`,
`poi_gap`, `poi_defined`, `poi_gap_fire`, `poi_defined_fire`, `poi_gap_ems`,
`poi_defined_ems`, `poi_gap_schools`, `poi_defined_schools`, `poi_gap_cbp`,
and `poi_defined_cbp`. The first sample IDs are `04001944902`, `04001945100`,
and `04001970201`. GEOID must be preserved as a string (leading zeros matter).
The provided rows include numeric-looking score/gap fields and boolean-looking
`*_defined` values. Their semantic meaning and scoring target require confirmation
from the official Data tab/tutorial; no target or evaluation metric is asserted
here. The sample is 919,898 bytes; the manifest/checksum are the authoritative
file-integrity evidence.

## Reproduction

Run from the competition repository:

```sh
python scripts/audit_data.py --data-root "$COMPETITION_WORKSPACE" --out /tmp/bias-bounty-data-audit.json
```

The audit hashes each file; inspects CSV headers, row count, empty-cell counts,
and up to five GEOID strings; and attempts Parquet schema/row-group inspection
when `pyarrow` is installed. It is local-only and performs no downloads. Exact
observed command result: `Inventoried 2 files under
/home/martin/hddProjects/competitionsall-workspace/zindi-bias-bounty; report:
/tmp/bias-bounty-data-audit.json` (the two files are the manifest and sample).

## Limitations / next steps

This is a sample-only inventory, not acceptance evidence for every package file.
Before modeling, an authorized operator must read the Data tab/tutorial, obtain
the listed package manifest (without storing raw data in git), run the audit on
the accessible package, and document complete file hashes/schema and licensing.
No full 8 GB download was attempted. No external/reference layers were sought
or reconstructed.
