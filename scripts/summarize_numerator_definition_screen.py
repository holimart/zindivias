#!/usr/bin/env python3
"""Summarize Sc1/Sc4 numerator-definition screen diagnostics without labels or references."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ('maricopa-az', 'northern-ca', 'eastern-ok', 'south-central-tx')
THRESHOLD = 0.01


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(screen: dict[str, Any], harness: dict[str, Any],
              screen_hash: str, harness_hash: str) -> dict[str, Any]:
    if not screen['gates']['all_passed'] or not screen['gates']['deterministic_rerun']:
        raise ValueError('Sc1/Sc4 harness gates or deterministic rerun failed')
    if screen['scope']['official_scored_rows'] != 9379:
        raise ValueError('screen did not cover all 9,379 official IDs')
    if not harness['gates']['all_passed']:
        raise ValueError('Sc5 reference harness gates failed')
    sc1 = screen['per_idea']['Sc1']
    sc4 = screen['per_idea']['Sc4']
    sc5 = harness['per_idea']['Sc5']
    if sc1['rows'] != 9379 or sc4['rows'] != 9379:
        raise ValueError('Sc1/Sc4 output row count mismatch')
    regions = {}
    for region in REGIONS:
        d1 = sc1['diagnostics'][region]
        d4 = sc4['diagnostics'][region]
        d5 = sc5['diagnostics'][region]
        unnamed, named = float(d1['unnamed_km']), float(d1['named_km'])
        candidate = float(d4['candidate_km'])
        unfiltered_named = float(d5['named_length_km']['roads-unfiltered'])
        if unnamed < 0 or named <= 0 or candidate < 0 or unfiltered_named <= 0:
            raise ValueError(f'invalid length denominator/value in {region}')
        sc1_share = unnamed / (unnamed + named)
        sc4_relative = candidate / unfiltered_named
        sc4_expanded_numerator_share = candidate / (unfiltered_named + candidate)
        regions[region] = {
            'sc1_unnamed_km': unnamed,
            'sc1_named_km': named,
            'sc1_unnamed_share_of_named_class_length': sc1_share,
            'sc1_below_0_01': sc1_share < THRESHOLD,
            'sc4_unknown_route_or_long_km': candidate,
            'sc4_unfiltered_named_class_km_denominator': unfiltered_named,
            'sc4_unknown_km_relative_to_named_class_km': sc4_relative,
            'sc4_share_of_expanded_named_plus_unknown_numerator': sc4_expanded_numerator_share,
            'sc4_below_0_01': sc4_relative < THRESHOLD,
        }
    sc1_all_null = all(x['sc1_below_0_01'] for x in regions.values())
    sc4_all_null = all(x['sc4_below_0_01'] for x in regions.values())
    joint = sc1_all_null and sc4_all_null
    return {
        'schema_version': 1,
        'screen': 'bias60-numerator-definition-screen',
        'regions': regions,
        'threshold': THRESHOLD,
        'share_definitions': {
            'Sc1': 'region-total unnamed named-class km / (unnamed + named named-class km), using Sc1 diagnostics',
            'Sc4': 'Sc4 unknown-class route/name-bearing or >2 km candidate km / Sc5 unfiltered named-class km; expanded-numerator share is also reported',
        },
        'summary': {
            'Sc1_below_0_01_in_every_region': sc1_all_null,
            'Sc4_below_0_01_in_every_region': sc4_all_null,
            'joint_measured_null_condition_met': joint,
            'registry_measured_null_update_applied': False,
            'reason': ('both ideas are below the threshold in every region' if joint else
                       'joint condition is not met; Sc1 has a material unnamed named-class share, so no measured-null edits were made'),
        },
        'evidence': {
            'harness_code_commit': screen['code_commit_at_run'],
            'screen_report': 'reports/numerator-definition-screen.json',
            'screen_report_sha256': screen_hash,
            'Sc1_parquet_sha256': sc1['sha256'],
            'Sc4_parquet_sha256': sc4['sha256'],
            'Sc1_rows': sc1['rows'],
            'Sc4_rows': sc4['rows'],
            'screen_gates': screen['gates'],
            'Sc5_report': 'reports/ideas-harness.json',
            'Sc5_report_sha256': harness_hash,
            'Sc5_idea_sha256': sc5['sha256'],
            'Sc5_filter_null_preserved': True,
            'prohibited_inputs_used': [],
            'uploads': 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screen', type=Path, default=ROOT / 'reports/numerator-definition-screen.json')
    parser.add_argument('--harness', type=Path, default=ROOT / 'reports/ideas-harness.json')
    parser.add_argument('--out', type=Path, default=ROOT / 'reports/numerator-definition-screen-summary.json')
    args = parser.parse_args()
    screen_hash, harness_hash = sha256(args.screen), sha256(args.harness)
    report = summarize(json.loads(args.screen.read_text()), json.loads(args.harness.read_text()),
                       screen_hash, harness_hash)
    args.out.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    for region, values in report['regions'].items():
        print(f"{region}: Sc1={values['sc1_unnamed_share_of_named_class_length']:.6%}; "
              f"Sc4={values['sc4_unknown_km_relative_to_named_class_km']:.6%}")
    print(f"joint_measured_null={report['summary']['joint_measured_null_condition_met']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
