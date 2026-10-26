#!/usr/bin/env python3
"""Measure the aggregate named-road/route-length ratio sensitivity to Maricopa's NM tract.

Reads only the official sample GEOID column, the frozen derived route-length cache columns, and
hashes of protected artifacts. It never reads sample score values or writes per-tract output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
REGION = 'maricopa-az'
EXCLUDED_GEOID = '35023970000'
NAMED_COLUMN = 'overture_named_road_km_5070'
ROUTE_COLUMN = 'overture_route_km'
EXPECTED_SAMPLE_SHA256 = '1640b801d56ab308e68c65583b88cfb9e1555b05a6d0b922eaba22ac09960a31'
EXPECTED_FEATURE_SHA256 = 'e6e4064ea3827b8262cf2c3fe96beaeecd40592a7ce9bf0976a37cfbca1bbc22'
PROTECTED_REPO_FILES = (
    'reports/candidates.json',
    'reports/candidates/C0_all_zero.csv',
    'reports/candidates/P4_c1_shrink_alpha.csv',
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_geoid_column(path: Path) -> list[str]:
    """Parse only field zero; do not interpret included sample score placeholders."""
    with path.open('r', encoding='utf-8', newline='') as stream:
        header = stream.readline().rstrip('\r\n').split(',')
        if not header or header[0] != 'GEOID':
            raise ValueError(f'{path} must start with GEOID')
        geoids = []
        for line_number, line in enumerate(stream, start=2):
            geoid = line.partition(',')[0].strip()
            if not geoid:
                raise ValueError(f'empty GEOID at {path}:{line_number}')
            geoids.append(geoid)
    if len(set(geoids)) != len(geoids):
        raise ValueError('official sample contains duplicate GEOIDs')
    return geoids


def ratio_sensitivity(rows: list[dict[str, Any]], excluded_geoid: str) -> dict[str, Any]:
    """Aggregate route lengths and compute the same ratio with one row removed."""
    by_id = {str(row['GEOID']): row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError('route feature rows contain duplicate GEOIDs')
    if excluded_geoid not in by_id:
        raise ValueError(f'excluded GEOID {excluded_geoid} is absent')

    named_values = [float(row[NAMED_COLUMN]) for row in rows]
    route_values = [float(row[ROUTE_COLUMN]) for row in rows]
    all_named, all_route = math.fsum(named_values), math.fsum(route_values)
    excluded = by_id[excluded_geoid]
    excluded_named, excluded_route = float(excluded[NAMED_COLUMN]), float(excluded[ROUTE_COLUMN])
    without_named = all_named - excluded_named
    without_route = all_route - excluded_route
    if all_route <= 0 or without_route <= 0:
        raise ValueError('aggregate route denominator must remain positive')
    all_ratio = all_named / all_route
    without_ratio = without_named / without_route
    return {
        'with_tract': {
            'tracts': len(rows), 'L_named_km': all_named, 'L_route_km': all_route,
            'sum_L_named_over_sum_L_route': all_ratio,
        },
        'excluded_tract': {
            'GEOID': excluded_geoid, 'L_named_km': excluded_named,
            'L_route_km': excluded_route,
            'share_of_with_tract_L_named': excluded_named / all_named,
            'share_of_with_tract_L_route': excluded_route / all_route,
        },
        'without_tract': {
            'tracts': len(rows) - 1, 'L_named_km': without_named,
            'L_route_km': without_route,
            'sum_L_named_over_sum_L_route': without_ratio,
        },
        'absolute_ratio_delta': abs(all_ratio - without_ratio),
        'ratio_delta_without_minus_with': without_ratio - all_ratio,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path,
                        default=Path(os.environ.get('COMPETITION_WORKSPACE', '')))
    parser.add_argument('--out', type=Path,
                        default=ROOT / 'reports/maricopa-cross-state-route-influence.json')
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error('pass --workspace or set COMPETITION_WORKSPACE to the authorized workspace')

    frozen_path = ROOT / 'reports/transport-route-ref-audit.json'
    frozen = json.loads(frozen_path.read_text(encoding='utf-8'))
    frozen_region = frozen['regions'][REGION]
    official = args.workspace / 'defined-flags-inputs' / f'{REGION}-sample-submission.csv'
    feature_path = args.workspace / 'gap-estimator' / 'features' / f'{REGION}-overture-tract-summary.parquet'
    official_hash = sha256_file(official)
    feature_hash = sha256_file(feature_path)
    if official_hash != EXPECTED_SAMPLE_SHA256:
        raise ValueError(f'official sample hash changed: {official_hash}')
    if official_hash != frozen['inputs'][REGION]['official_sample']['sha256']:
        raise ValueError('official sample hash does not match the frozen route audit')
    if feature_hash != EXPECTED_FEATURE_SHA256:
        raise ValueError(f'route feature cache hash changed: {feature_hash}')
    if feature_hash != frozen['inputs'][REGION]['feature_cache']['sha256_after_route_extension']:
        raise ValueError('route feature cache hash does not match the frozen route audit')

    geoids = read_geoid_column(official)
    if len(geoids) != 1593 or geoids.count(EXCLUDED_GEOID) != 1:
        raise ValueError('expected exactly 1,593 official GEOIDs including the NM tract once')
    table = pq.read_table(feature_path, columns=['GEOID', NAMED_COLUMN, ROUTE_COLUMN])
    rows = table.to_pylist()
    if [str(row['GEOID']) for row in rows] != geoids:
        raise ValueError('feature rows do not exactly match official GEOID order')
    if table.num_rows != len(set(geoids)):
        raise ValueError('feature row count does not equal unique official GEOID count')

    protected_paths = {name: ROOT / name for name in PROTECTED_REPO_FILES}
    protected_paths.update({
        'workspace/defined-flags-inputs/maricopa-az-sample-submission.csv': official,
        'workspace/gap-estimator/features/maricopa-az-overture-tract-summary.parquet': feature_path,
        'reports/transport-route-ref-audit.json': frozen_path,
    })
    protected = {name: sha256_file(path) for name, path in protected_paths.items()}
    result = ratio_sensitivity(rows, EXCLUDED_GEOID)
    baseline_named = float(frozen_region['L_named_km_sum_epsg5070'])
    baseline_route = float(frozen_region['L_route_km_sum_epsg5070'])
    named_error = abs(result['with_tract']['L_named_km'] - baseline_named)
    route_error = abs(result['with_tract']['L_route_km'] - baseline_route)
    if named_error > 1e-8 or route_error > 1e-8:
        raise ValueError(f'aggregate does not reconstruct frozen totals: {named_error=}, {route_error=}')
    if not math.isclose(result['with_tract']['sum_L_named_over_sum_L_route'],
                        float(frozen_region['sum_L_named_over_sum_L_route']), rel_tol=0, abs_tol=1e-12):
        raise ValueError('aggregate ratio does not reconstruct the frozen ratio')
    if any(sha256_file(protected_paths[name]) != digest for name, digest in protected.items()):
        raise RuntimeError('a protected candidate artifact changed during this read-only audit')

    report = {
        'schema_version': 1,
        'analysis': 'Maricopa cross-state tract contribution to aggregate route-reference diagnostic',
        'scope': 'aggregate-only diagnostic; not a candidate score, model promotion, or target estimate',
        'interpretation': {
            'absolute_ratio_delta': result['absolute_ratio_delta'],
            'relative_change_from_with_tract_ratio': (
                result['absolute_ratio_delta'] / abs(result['with_tract']['sum_L_named_over_sum_L_route'])),
            'finding': 'Removing the NM tract raises the aggregate ratio by about 0.039 (about 2.0% of the with-tract ratio); this is a modest descriptive influence, not a claim of statistical or target significance. No materiality cutoff was preregistered.',
        },
        'method': {
            'ratio': 'sum(L_named) / sum(L_route)',
            'L_named': NAMED_COLUMN + ' (km), derived from challenge-provided Overture named road features and provided tract geometry',
            'L_route': ROUTE_COLUMN + ' (km), derived from challenge-provided Overture route features and provided tract geometry',
            'official_sample_read': 'GEOID column only; all sample score fields ignored',
            'per_tract_output_written': False,
        },
        'accounting': {
            **result,
            'official_sample_unique_geoids': len(set(geoids)),
            'official_sample_rows': len(geoids),
            'tracts_without_excluded_tract': len(geoids) - 1,
        },
        'verification': {
            'frozen_L_named_km': baseline_named,
            'frozen_L_route_km': baseline_route,
            'reconstructed_L_named_absolute_error_km': named_error,
            'reconstructed_L_route_absolute_error_km': route_error,
            'totals_reconstructed_within_1e_8_km': named_error <= 1e-8 and route_error <= 1e-8,
            'frozen_ratio': float(frozen_region['sum_L_named_over_sum_L_route']),
            'ratio_reconstructed_within_1e_12': True,
            'excluded_GEOID_present_exactly_once': True,
        },
        'source_hashes': {
            'official_sample_csv': {
                'path': 'workspace/defined-flags-inputs/maricopa-az-sample-submission.csv',
                'sha256': official_hash,
                'semantic_fields_read': ['GEOID'],
            },
            'route_feature_cache': {
                'path': 'workspace/gap-estimator/features/maricopa-az-overture-tract-summary.parquet',
                'sha256': feature_hash,
                'columns_read': ['GEOID', NAMED_COLUMN, ROUTE_COLUMN],
            },
            'frozen_route_audit': {
                'path': 'reports/transport-route-ref-audit.json',
                'sha256': sha256_file(frozen_path),
            },
        },
        'protected_artifact_hashes': {
            path: {'sha256_before': digest, 'sha256_after': digest, 'unchanged': True}
            for path, digest in protected.items()
        },
        'data_use': {
            'challenge_provided_features_and_geometry_only': True,
            'removed_reference_layers_or_equivalents_read': False,
            'labels_or_sample_score_values_used': False,
            'leaderboard_values_used': False,
            'candidate_or_submission_csv_written_or_modified': False,
            'uploads': 0,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({
        'geoids': len(geoids), 'with_tract': result['with_tract'],
        'excluded_tract': result['excluded_tract'], 'without_tract': result['without_tract'],
        'absolute_ratio_delta': result['absolute_ratio_delta'],
        'named_total_error_km': named_error, 'route_total_error_km': route_error,
        'report': str(args.out),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
