# Bias Bounty Mapping Equity Challenge

Local-only competition worktree for the `competitionsall` orchestrator.

- Competition: https://zindi.africa/competitions/bias-bounty-mapping-equity-challenge
- Prize: $10000
- Deadline: 2026-10-31T23:59:00+00:00
- Lane: `comp-zindi-bias-bounty`
- Orchestrator: `../competitionsall`
- Status: model-first policy active; no leaderboard information probes

This repo is data/work for the lane. Do not copy `.langywrap`, `langywrap`, or
`slurmhelps` here; the orchestrator owns those.

## Reproduce (current code-review pair)

With CPython 3.12 and a writable authorized workspace, run from this repository:

```sh
COMPETITION_WORKSPACE=/path/to/bias-bounty-workspace scripts/reproduce.sh
```

The command uses public `uv` when available (override with `UV_BIN`), or a
standard `python3.12 -m venv` plus pip, and installs only the exact open-source
versions in `requirements.txt`. It fetches the four authoritative sample-ID
files and required strata Parquets from the public Source Cooperative bucket
(hash checked, no credentials), rebuilds
all four Overture/ACS feature summaries with an anonymous public S3 client and
an empty/overwritten feature cache, then regenerates the **currently best two
successfully scored** candidates C0 and P4. It prints both SHA-256 values and
fails unless they match `reports/candidates.json`. The run profile, inputs,
package versions, cache rebuild times, artifact hashes, and raw
`/usr/bin/time -v` output are recorded in `reports/reproduce-run.json`.

`config/calibration.json` is the sole source for LB-calibrated constants used
by this pair. It documents P4's alpha, its P4/C1 public-score provenance, and
the C0/P4 artifact hashes. `data/reproduction/template-geoid-region.csv` is a
small, score-free projection of only `GEOID,region` from the provided platform
template; the 415 eastern-WA rows are format-only fills, not target evidence.
The official scored scope remains 9,379 IDs. Sample score placeholders are
never consumed. The pair reflects the best available successful public scores
as of 2026-10-24 and is **not** a final private-leaderboard selection. The
model-first final selection protocol is in
[`docs/final-two-protocol.md`](docs/final-two-protocol.md). This pair's
historical P4 scale is for reproduction only; new models must use model-derived
component levels. Active upload rules and cancelled probes are
in [`docs/submission-policy.md`](docs/submission-policy.md); no information
probes are queued. No tokens, credentials, paid APIs, or private packages are
used.
