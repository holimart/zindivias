# Best Bias Discovery prize criteria audit

Row: `backlog:.langywrap/backlogs/competitions.jsonl:bias-discovery-prize-criteria-audit`. Lane: `comp-zindi-bias-bounty`. documentation screen of the Best Bias Discovery narrative; not a submission, not a model promotion, not a leaderboard probe.

Audited document: `docs/writeup/bias-discovery.md` (SHA-256 `30f644e9a946ce3236f567adf26f436090fe64bbe9083aea47d2b66bb3703630`).
Preregistration: `config/discovery-prize-criteria-preregistered.json` (SHA-256 `017fbd57fa18537bcf72098120b30a59e374abe93070e80e86a23aab94482adf`).

This is a documentation screen. It makes no submission, changes no scored candidate, reads no leaderboard feedback, and asserts nothing about the organisers' hidden target. The DINS comparison does not establish rebuilding, absence, or a hidden-target result.

## Organiser criteria and weights

| Criterion | Weight | What a pass requires |
|---|---:|---|
| Impact | 30% | a specific, responsibly qualified decision consequence with a named referent, never an unqualified operational claim |
| Novelty outside the published scorecard grid | 25% | an explicit comparison with the published Bias Scorecard dimensions showing the finding is not a restatement of a marginal scorecard cell |
| Quantified evidence | 25% | every quantitative statement in the section carries a hash-pinned evidence pointer that resolves on disk and is traceable in the claim inventory |
| Reproducibility | 20% | a recorded command that regenerates the deterministic report, the script exists, and a sampled claim verify command exits zero |

Organiser wording: "Entries for Best Bias Discovery are judged from the writeup by the organizing team against four criteria" (source: WORKSPACE:RULES.md).

## Criterion matrix

Verdicts are `pass` or `needs revision`. Each retained finding is supported only when all four criteria pass; the overall packet still requires operator review and a complete claim audit.

| Section | Declared status | Resolution | Impact | Novelty | Evidence | Reproducibility |
|---|---|---|---|---|---|---|
| Finding 2 — Paradise/Magalia building-count discrepancy | supported | supported | pass | pass | pass | pass |
| Finding 1 — Rural Overture buildings are less often OSM-sourced | supported | supported | pass | pass | pass | pass |
| Finding 3 — The tribal/non-tribal pooled comparison is region-confounded | supported | supported | pass | pass | pass | pass |

## Measured acceptance

- sections configured / covered: 3 / 3; unresolved: 0
- criteria evaluated / passed: 12 / 12
- evidence pointers verified: 15 of 15
- quantitative claims traceable: 9 of 9 (claim audit fraction 1.0)
- unlisted external URLs in the discovery document: 0
- hidden-target claim patterns: 0; unqualified operational patterns: 0
- prohibited-token hits needing disclosure: prose 0, scripts 0
- sampled claim verify commands executed with exit 0: 3 of 3

## Section detail

### Finding 2 — Paradise/Magalia building-count discrepancy

Declared `supported`, resolved `supported`, lines 19-62, 5396 characters. Claims: `bias-discovery-002`, `bias-discovery-003`, `bias-discovery-004`, `bias-discovery-005`, `bias-discovery-006`, `bias-discovery-007`, `bias-discovery-008`.

- **impact**: `pass` (headings: Impact and novelty)
- **novelty**: `pass` (headings: Impact and novelty)
- **evidence**: `pass` (headings: Evidence)
- **reproducibility**: `pass` (headings: Reproducibility)

  Evidence pointers:

  | Path | JSON pointer | SHA-256 (16) | Resolved |
  |---|---|---|---|
  | `reports/discovery-postfire.json` | `/focal_butte_2018_burn_geoids` | `cbf100bc833bf09c` | True |
  | `reports/discovery-postfire.json` | `/northern_ca_burn_class_table` | `cbf100bc833bf09c` | True |
  | `reports/discovery-postfire-county-bootstrap.json` | `/county_cluster_bootstrap/cluster_95_percentile_interval` | `f55774557f046187` | True |
  | `reports/discovery-dins-mechanism.json` | `/external_source` | `8af173a124f59ef7` | True |
  | `reports/discovery-dins-mechanism.json` | `/decision` | `8af173a124f59ef7` | True |
  | `reports/discovery-dins-mechanism.json` | `/spearman` | `8af173a124f59ef7` | True |
  | `reports/discovery-dins-mechanism.json` | `/focal_butte_tracts` | `8af173a124f59ef7` | True |
  | `reports/discovery-dins-mechanism.json` | `/opposite_direction` | `8af173a124f59ef7` | True |
  | `reports/discovery-postfire-map.png` | `-` | `916589e0d3dc765a` | True |

  Reproduction: `python scripts/discovery_dins_mechanism.py`; script(s): `scripts/discovery_postfire_stale_reference.py`, `scripts/audit_discovery_postfire_county_bootstrap.py`, `scripts/discovery_dins_mechanism.py`

### Finding 1 — Rural Overture buildings are less often OSM-sourced

Declared `supported`, resolved `supported`, lines 1-17, 1642 characters. Claims: `bias-discovery-001`.

- **impact**: `pass` (headings: Impact and novelty)
- **novelty**: `pass` (headings: Impact and novelty)
- **evidence**: `pass` (headings: Evidence)
- **reproducibility**: `pass` (headings: Reproducibility)

  Evidence pointers:

  | Path | JSON pointer | SHA-256 (16) | Resolved |
  |---|---|---|---|
  | `reports/source-provenance.json` | `/statistics` | `e80171d07d555e8e` | True |
  | `reports/source-provenance.json` | `/uncertainty_and_limitations` | `e80171d07d555e8e` | True |
  | `reports/source-provenance.json` | `/source_definitions` | `e80171d07d555e8e` | True |

  Reproduction: `python scripts/discovery_source_provenance.py`; script(s): `scripts/discovery_source_provenance.py`

### Finding 3 — The tribal/non-tribal pooled comparison is region-confounded

Declared `supported`, resolved `supported`, lines 64-80, 1502 characters. Claims: `bias-discovery-009`.

- **impact**: `pass` (headings: Impact and novelty)
- **novelty**: `pass` (headings: Impact and novelty)
- **evidence**: `pass` (headings: Evidence)
- **reproducibility**: `pass` (headings: Reproducibility)

  Evidence pointers:

  | Path | JSON pointer | SHA-256 (16) | Resolved |
  |---|---|---|---|
  | `reports/discovery-strata-confound.json` | `/stratum_composition` | `ce0f1307793d1c65` | True |
  | `reports/discovery-strata-confound.json` | `/regional_counts_and_prevalence` | `ce0f1307793d1c65` | True |
  | `reports/discovery-strata-confound.json` | `/missing` | `ce0f1307793d1c65` | True |

  Reproduction: `python scripts/discovery_strata_confound.py`; script(s): `scripts/discovery_strata_confound.py`

## Non-claims

- No proxy model score is presented as the organiser's hidden target anywhere in the discovery document (pattern scan: zero hits).
- No section asserts an unqualified operational outcome; every impact paragraph carries an explicit hedge.
- One public CAL FIRE DINS source is registered for the mechanism check only; its open licence, URL, retrieval date, filter, and limitations are documented, and it never enters scored predictions.
- The three findings pass the four narrative criteria. This packet remains a draft for operator review because the broader claim audit is incomplete and submission inputs are pending.

## Reproduction

```sh
COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" \
  /home/martin/hddProjects/langywrap/uv run --quiet \
  python scripts/audit_discovery_prize_criteria.py
```

The report is `reports/discovery-prize-criteria-audit.json` and this document is generated from it, so a repeated run is byte-identical and serialises no wall-clock value.
