# Best Bias Discovery prize criteria audit

Row: `backlog:.langywrap/backlogs/competitions.jsonl:bias-discovery-prize-criteria-audit`. Lane: `comp-zindi-bias-bounty`. documentation screen of the Best Bias Discovery narrative; not a submission, not a model promotion, not a leaderboard probe.

Audited document: `docs/writeup/bias-discovery.md` (SHA-256 `342df0d2c677861c7d4a2d540469adfe0f33b6ff4ffe74650b247113ad245cd2`).
Preregistration: `config/discovery-prize-criteria-preregistered.json` (SHA-256 `bfd91240b58301f7a781a68c2764bf8ed498eccffbf0a5b11bafd3747bfafc61`).

This is a documentation screen. It makes no submission, changes no scored candidate, reads no leaderboard feedback, and asserts nothing about the organisers' hidden target. Every score quoted from the proxy models remains a model-side estimate, not an organiser score.

## Organiser criteria and weights

| Criterion | Weight | What a pass requires |
|---|---:|---|
| Impact | 30% | a specific, responsibly qualified decision consequence with a named referent, never an unqualified operational claim |
| Novelty outside the published scorecard grid | 25% | an explicit comparison with the published Bias Scorecard dimensions showing the finding is not a restatement of a marginal scorecard cell |
| Quantified evidence | 25% | every quantitative statement in the section carries a hash-pinned evidence pointer that resolves on disk and is traceable in the claim inventory |
| Reproducibility | 20% | a recorded command that regenerates the deterministic report, the script exists, and a sampled claim verify command exits zero |

Organiser wording: "Entries for Best Bias Discovery are judged from the writeup by the organizing team against four criteria" (source: WORKSPACE:RULES.md).

## Criterion matrix

Verdicts are `pass` or `needs revision`. A section is accepted when all four criteria pass, or when it is explicitly marked draft and still passes evidence and reproducibility.

| Section | Declared status | Resolution | Impact | Novelty | Evidence | Reproducibility |
|---|---|---|---|---|---|---|
| Post-fire building-count disagreement may look like under-mapping | supported | supported | pass | pass | pass | pass |
| Scorecard strata are strongly region-composition confounded | supported | supported | pass | pass | pass | pass |
| Secondary exploratory lead — rural × SVI minority-domain screen in South-Central Texas | draft | draft | pass | pass | pass | pass |
| Overture source mix differs across tract strata | supported | supported | pass | pass | pass | pass |

## Measured acceptance

- sections configured / covered: 4 / 4; unresolved: 0
- criteria evaluated / passed: 16 / 16
- evidence pointers verified: 27 of 27
- quantitative claims traceable: 29 of 29 (claim audit fraction 1.0)
- unlisted external URLs in the discovery document: 0
- hidden-target claim patterns: 0; unqualified operational patterns: 0
- prohibited-token hits needing disclosure: prose 0, scripts 0
- sampled claim verify commands executed with exit 0: 4 of 4

## Section detail

### Post-fire building-count disagreement may look like under-mapping

Declared `supported`, resolved `supported`, lines 1-73, 8964 characters. Claims: `bias-discovery-001`, `bias-discovery-002`, `bias-discovery-003`, `bias-discovery-004`, `bias-discovery-005`, `bias-discovery-006`, `bias-discovery-007`, `bias-discovery-008`, `bias-discovery-009`.

- **impact**: `pass` (headings: Impact)
- **novelty**: `pass` (headings: Novelty)
- **evidence**: `pass` (headings: Evidence)
- **reproducibility**: `pass` (headings: Reproducibility)

  Evidence pointers:

  | Path | JSON pointer | SHA-256 (16) | Resolved |
  |---|---|---|---|
  | `reports/discovery-postfire.json` | `/focal_butte_2018_burn_geoids` | `cbf100bc833bf09c` | True |
  | `reports/discovery-postfire.json` | `/northern_ca_burn_class_table` | `cbf100bc833bf09c` | True |
  | `reports/discovery-postfire.json` | `/high_burn_vs_unburned_below_0_8` | `cbf100bc833bf09c` | True |
  | `reports/discovery-postfire.json` | `/no_labels_or_removed_references_used` | `cbf100bc833bf09c` | True |
  | `reports/discovery-postfire-map.png` | `-` | `916589e0d3dc765a` | True |
  | `reports/discovery-postfire-county-bootstrap.json` | `/robustness` | `feaf180e6637bafc` | True |
  | `reports/discovery-postfire-county-bootstrap.json` | `/county_cluster_bootstrap/cluster_95_percentile_interval` | `feaf180e6637bafc` | True |
  | `reports/discovery-postfire-county-bootstrap.json` | `/county_cluster_bootstrap/tract_preservation_checks` | `feaf180e6637bafc` | True |
  | `reports/discovery-postfire-county-bootstrap.json` | `/derived_input_content_equivalence` | `feaf180e6637bafc` | True |

  Reproduction: `python scripts/discovery_postfire_stale_reference.py`; script(s): `scripts/discovery_postfire_stale_reference.py`, `scripts/audit_discovery_postfire_county_bootstrap.py`

### Scorecard strata are strongly region-composition confounded

Declared `supported`, resolved `supported`, lines 75-114, 5017 characters. Claims: `bias-discovery-010`, `bias-discovery-011`, `bias-discovery-012`, `bias-discovery-013`, `bias-discovery-014`, `bias-discovery-015`, `bias-discovery-016`.

- **impact**: `pass` (headings: Impact)
- **novelty**: `pass` (headings: Novelty)
- **evidence**: `pass` (headings: Evidence)
- **reproducibility**: `pass` (headings: Reproducibility)

  Evidence pointers:

  | Path | JSON pointer | SHA-256 (16) | Resolved |
  |---|---|---|---|
  | `reports/discovery-strata-confound.json` | `/stratum_composition` | `ce0f1307793d1c65` | True |
  | `reports/discovery-strata-confound.json` | `/regional_counts_and_prevalence` | `ce0f1307793d1c65` | True |
  | `reports/discovery-strata-confound.json` | `/unique_official_geoid_count` | `ce0f1307793d1c65` | True |
  | `reports/discovery-strata-confound.json` | `/missing` | `ce0f1307793d1c65` | True |
  | `reports/transport-route-ref-audit.json` | `/regions` | `bf904336963f553c` | True |
  | `reports/transport-route-ref-audit.json` | `/limitations` | `bf904336963f553c` | True |

  Reproduction: `python scripts/discovery_strata_confound.py`; script(s): `scripts/discovery_strata_confound.py`

### Secondary exploratory lead — rural × SVI minority-domain screen in South-Central Texas

Declared `draft`, resolved `draft`, lines 116-173, 5400 characters. Claims: `bias-discovery-017`, `bias-discovery-018`, `bias-discovery-019`, `bias-discovery-020`, `bias-discovery-021`, `bias-discovery-022`. Marked in the document: 'Draft for operator review'

- **impact**: `pass` (headings: Why this may matter; what it does not show)
- **novelty**: `pass` (headings: Proposed pattern outside the published scorecard grid)
- **evidence**: `pass` (headings: Quantified proxy evidence)
- **reproducibility**: `pass` (headings: Reproduction)

  Evidence pointers:

  | Path | JSON pointer | SHA-256 (16) | Resolved |
  |---|---|---|---|
  | `reports/discovery-rural-svi-minority.json` | `/group_definition` | `da1a581ddb25347c` | True |
  | `reports/discovery-rural-svi-minority.json` | `/composite_results` | `da1a581ddb25347c` | True |
  | `reports/discovery-rural-svi-minority.json` | `/C2_component_results_defined_only` | `da1a581ddb25347c` | True |
  | `reports/discovery-rural-svi-minority.json` | `/named_high_group_tracts_sorted_by_C2_estimate` | `da1a581ddb25347c` | True |
  | `reports/discovery-rural-svi-minority.json` | `/fixed_scorecard_novelty` | `da1a581ddb25347c` | True |
  | `reports/discovery-rural-svi-minority.json` | `/causal_and_accuracy_limitations` | `da1a581ddb25347c` | True |

  Reproduction: `python scripts/discovery_rural_svi_minority.py`; script(s): `scripts/discovery_rural_svi_minority.py`

  Recommendations:
  - Keep the section marked draft until the operator reviews the discovery narrative: the group means are proxy model scores, the transport component interval includes zero, and the impact paragraph is hypothetical. Either promote it with a stakeholder-checked cutpoint or remove the operational sentence and keep it as a follow-up screen.

### Overture source mix differs across tract strata

Declared `supported`, resolved `supported`, lines 175-226, 6897 characters. Claims: `bias-discovery-023`, `bias-discovery-024`, `bias-discovery-025`, `bias-discovery-026`, `bias-discovery-027`, `bias-discovery-028`, `bias-discovery-029`. Marked in the document: 'descriptive discovery lead, not a measured map-completeness or hidden-score finding'

- **impact**: `pass` (headings: Interpretation and limits)
- **novelty**: `pass` (headings: Method and source definitions, Interpretation and limits)
- **evidence**: `pass` (headings: Findings)
- **reproducibility**: `pass` (headings: Reproduction)

  Evidence pointers:

  | Path | JSON pointer | SHA-256 (16) | Resolved |
  |---|---|---|---|
  | `reports/source-provenance.json` | `/statistics` | `e80171d07d555e8e` | True |
  | `reports/source-provenance.json` | `/source_definitions` | `e80171d07d555e8e` | True |
  | `reports/source-provenance.json` | `/regions` | `e80171d07d555e8e` | True |
  | `reports/source-provenance.json` | `/data_and_restrictions` | `e80171d07d555e8e` | True |
  | `reports/source-provenance.json` | `/uncertainty_and_limitations` | `e80171d07d555e8e` | True |
  | `reports/source-provenance.png` | `-` | `2d45a9d06baa5e0a` | True |

  Reproduction: `python scripts/discovery_source_provenance.py`; script(s): `scripts/discovery_source_provenance.py`

## Non-claims

- No proxy model score is presented as the organiser's hidden target anywhere in the discovery document (pattern scan: zero hits).
- No section asserts an unqualified operational outcome; every impact paragraph carries an explicit hedge.
- No public dataset beyond the challenge package is introduced (zero URLs in the discovery document), so no licence or retrieval record is applicable.
- The draft section stays marked draft and is not eligible for the discovery prize until the operator reviews it.

## Reproduction

```sh
COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" \
  /home/martin/hddProjects/langywrap/uv run --quiet \
  python scripts/audit_discovery_prize_criteria.py
```

The report is `reports/discovery-prize-criteria-audit.json` and this document is generated from it, so a repeated run is byte-identical and serialises no wall-clock value.
