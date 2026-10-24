# Spatial validation audit — blocked pending authorized training package

Date: 2026-10-24. This is a measured readiness audit, not a completed split or
RMSE evaluation.

## Measure-before

Command run from the competition repository:

```sh
python scripts/audit_data.py --data-root "$COMPETITION_WORKSPACE" --out /tmp/bias-spatial-before.json
```

Observed output: `Inventoried 2 files under /home/martin/hddProjects/competitionsall-workspace/zindi-bias-bounty; report: /tmp/bias-spatial-before.json`.

The workspace contains only `SampleSubmission.csv` (919,898 bytes,
SHA-256 `32fb5c2984317d4abf8a98b88b7bb9d16e9eef1cda9a954c37da0a2cdd367f40`)
and `download-manifest.json` (393 bytes). The sample has 9,794 rows and
columns including `GEOID` and `region`. It does not provide training labels or
training-group counts. The pre-existing inventory likewise records no
challenge training/package files. `/tmp/bias-spatial-before.json` is a local,
reproducible measurement artifact; raw challenge data was not downloaded.

## Disposition and blockers

No spatial split, fold-size table, group-overlap result, baseline, or per-region
holdout RMSE can be computed without labeled training data. Sample-submission
rows are not labels and were not used as such. The presence of `GEOID` and
`region` in the sample does not establish which geography key defines
independent spatial groups; that must be verified against the authorized
training package and challenge documentation. Accordingly, this item is
blocked—not a negative model result—and the requested
`scripts/make_spatial_splits.py` is not claimed as delivered.

Prerequisite: locate/access and metadata-inventory the authorized GeoParquet
training package (including region and geographic identifiers) via the
approved package access path. Then count groups by region and assess whether
geography keys define meaningful disjoint units. Do not use removed reference
layers, reconstruct targets from them, use test labels, or substitute
random-row-only CV.

## Intended validation once data is available

Use an explicit spatial-group partition with zero group overlap across every
validation fold and a separately locked, untouched spatial holdout. Record the
split seed, input/package hashes, group counts, fold sizes, region membership,
and overlap assertion. Compute overall and per-region RMSE only on the locked
holdout after a training-only baseline is available. If the package has no
adequate geography key, document that limitation and use a conservative
coarser available region/group holdout; if even that cannot form disjoint
meaningful groups, stop and report the limitation rather than silently falling
back to random-row validation.
