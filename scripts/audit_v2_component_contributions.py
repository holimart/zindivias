#!/usr/bin/env python3
"""Describe leave-one-component-out sensitivity of the frozen V2 composite.

This is an arithmetic decomposition of the model's own frozen predictions, not a comparison to
organizer scores. It uses official GEOIDs, the candidate's three frozen component gaps, their
frozen defined flags, and predeclared provided strata. It reads no sample score values or LB data.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import audit_v2_component_masks as MASKS  # noqa: E402
import audit_v2_strata_distribution as STRATA  # noqa: E402

MASK_CONFIG_PATH = ROOT / 'config/v2-component-mask-audit-preregistered.json'
STRATA_CONFIG_PATH = ROOT / 'config/v2-strata-descriptive-preregistered.json'
MASK_CONFIG: dict[str, Any] = json.loads(MASK_CONFIG_PATH.read_text(encoding='utf-8'))
STRATA_CONFIG: dict[str, Any] = json.loads(STRATA_CONFIG_PATH.read_text(encoding='utf-8'))
MASK_AUDIT_PATH = ROOT / 'reports/v2-defined-mask-audit.json'
MASK_AUDIT: dict[str, Any] = json.loads(MASK_AUDIT_PATH.read_text(encoding='utf-8'))
REGIONS = tuple(MASK_CONFIG['scope']['official_regions_order'])
EXPECTED_ROWS = int(MASK_CONFIG['scope']['official_scored_rows'])
COMPONENTS = tuple(STRATA_CONFIG['candidate']['component_gap_columns'])
QUANTILES = (0.05, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)
DEFAULT_OUT = ROOT / 'reports/v2-component-contribution-audit.json'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def nearest_rank(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    index = max(0, math.ceil(q * len(sorted_values)) - 1)
    return sorted_values[index]


def delta_stats(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {'n': 0, 'mean_abs_delta': None, 'max_abs_delta': None,
                'quantiles_abs_delta': {f'p{int(q * 100):02d}': None for q in QUANTILES}}
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in ordered):
        raise ValueError('absolute score deltas must be finite and in [0, 1]')
    return {
        'n': len(ordered),
        'mean_abs_delta': math.fsum(ordered) / len(ordered),
        'max_abs_delta': ordered[-1],
        'quantiles_abs_delta': {f'p{int(q * 100):02d}': nearest_rank(ordered, q)
                                for q in QUANTILES},
    }


def read_candidate(path: Path, official_ids: list[str]) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    columns = ['GEOID', STRATA_CONFIG['candidate']['score_column'],
               *STRATA_CONFIG['candidate']['component_gap_columns'].values()]
    rows: dict[str, dict[str, str]] = {}
    order: list[str] = []
    with path.open(newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        missing = sorted(set(columns) - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f'{path} lacks candidate columns {missing}')
        for raw in reader:
            geoid = str(raw['GEOID'])
            if geoid in rows:
                raise ValueError(f'duplicate candidate GEOID {geoid}')
            rows[geoid] = {key: raw[key] for key in columns}
            order.append(geoid)
    absent = sorted(set(official_ids) - set(rows))
    if absent:
        raise ValueError(f'candidate is missing {len(absent)} official GEOIDs: {absent[:3]}')
    if len(set(official_ids)) != EXPECTED_ROWS:
        raise ValueError('official GEOID list is not unique or does not match the frozen scope')
    candidate_official_order = [geoid for geoid in order if geoid in set(official_ids)]
    return rows, {'path': str(path.relative_to(ROOT)), 'sha256': sha256_file(path),
                  'artifact_rows': len(rows), 'official_rows': len(official_ids),
                  'non_official_rows_excluded': len(rows) - len(official_ids),
                  'official_order_matches': candidate_official_order == official_ids,
                  'official_id_set_matches': set(candidate_official_order) == set(official_ids)}


def read_components(official_ids: list[str]) -> tuple[dict[str, dict[str, float]],
                                                        dict[str, dict[str, int]], dict[str, Any]]:
    candidate = STRATA_CONFIG['candidate']
    gaps: dict[str, dict[str, float]] = {name: {} for name in COMPONENTS}
    flags: dict[str, dict[str, int]] = {name: {} for name in COMPONENTS}
    evidence: dict[str, Any] = {}
    for name in COMPONENTS:
        gap_column = candidate['component_gap_columns'][name]
        pin = MASK_AUDIT['frozen_inputs'][f'{name}_component']
        artifact_path = ROOT / pin['path']
        observed_sha = sha256_file(artifact_path)
        if observed_sha != pin['sha256']:
            raise ValueError(f'{name} frozen component artifact hash does not match audit pin')
        table = MASKS.read_table(artifact_path, [pin['defined_column']])
        if table.duplicate_ids:
            raise ValueError(f'{name} component artifact has duplicate GEOIDs')
        absent = sorted(set(official_ids) - set(table.rows))
        if absent:
            raise ValueError(f'{name} flags missing {len(absent)} official GEOIDs')
        for geoid in official_ids:
            row = table.rows[geoid]
            flags[name][geoid] = MASKS.parse_flag(row[pin['defined_column']],
                                                  pin['defined_column'], geoid)
        evidence[name] = {
            'path': pin['path'], 'sha256': observed_sha, 'sha256_pin': pin['sha256'],
            'defined_column': pin['defined_column'], 'gap_column_in_candidate': gap_column,
            'rows_read': table.rows_read, 'unique_rows': len(table.rows),
            'defined_rows': sum(flags[name].values()),
            'undefined_rows': len(official_ids) - sum(flags[name].values()),
            'flag_coverage_exact': set(official_ids) <= set(flags[name]),
        }
    return gaps, flags, evidence


def parse_component_values(candidate_rows: dict[str, dict[str, str]], official_ids: list[str],
                            gaps: dict[str, dict[str, float]], flags: dict[str, dict[str, int]]) -> tuple[
                                dict[str, float], dict[str, Any]]:
    names = STRATA_CONFIG['candidate']['component_gap_columns']
    score_col = STRATA_CONFIG['candidate']['score_column']
    frozen_scores: dict[str, float] = {}
    max_baseline_residual = 0.0
    outside = 0
    for geoid in official_ids:
        row = candidate_rows[geoid]
        values: list[float] = []
        for component in COMPONENTS:
            try:
                gap = float(row[names[component]])
            except (TypeError, ValueError) as exc:
                raise ValueError(f'non-numeric {component} for {geoid}') from exc
            if not math.isfinite(gap) or not 0.0 <= gap <= 1.0:
                raise ValueError(f'{component} is non-finite or outside [0,1] for {geoid}')
            if not flags[component][geoid] and gap != 0.0:
                raise ValueError(f'undefined {component} placeholder is nonzero for {geoid}')
            gaps[component][geoid] = gap
            if flags[component][geoid]:
                values.append(gap)
        if not values:
            raise ValueError(f'no defined components for official GEOID {geoid}')
        baseline = math.fsum(values) / len(values)
        try:
            frozen = float(row[score_col])
        except (TypeError, ValueError) as exc:
            raise ValueError(f'non-numeric frozen composite for {geoid}') from exc
        if not math.isfinite(frozen) or not 0.0 <= frozen <= 1.0:
            raise ValueError(f'frozen composite outside [0,1] for {geoid}')
        max_baseline_residual = max(max_baseline_residual, abs(baseline - frozen))
        frozen_scores[geoid] = frozen
    if max_baseline_residual > 1e-11:
        raise ValueError(f'candidate composite reconstruction residual {max_baseline_residual} > 1e-11')
    return frozen_scores, {'max_abs_composite_reconstruction_residual': max_baseline_residual,
                           'within_1e_11': True, 'official_rows_reconstructed': len(official_ids),
                           'all_composite_scores_finite_in_range': True}


def defined_component_mean(values: dict[str, float], row_flags: dict[str, int],
                           omit: str) -> float | None:
    remaining = [values[name] for name in COMPONENTS
                 if name != omit and row_flags[name] == 1]
    return math.fsum(remaining) / len(remaining) if remaining else None


def variant_records(official_pairs: list[tuple[str, str]], gaps: dict[str, dict[str, float]],
                    flags: dict[str, dict[str, int]], baseline: dict[str, float]) -> tuple[
                        dict[str, list[dict[str, Any]]], dict[str, Any]]:
    records_by_component: dict[str, list[dict[str, Any]]] = {}
    accounting: dict[str, Any] = {}
    for omitted in COMPONENTS:
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for geoid, region in official_pairs:
            if geoid in seen:
                raise ValueError(f'{omitted}: GEOID {geoid} counted more than once')
            seen.add(geoid)
            values = {component: gaps[component][geoid] for component in COMPONENTS}
            row_flags = {component: flags[component][geoid] for component in COMPONENTS}
            loo = defined_component_mean(values, row_flags, omitted)
            if loo is not None and (not math.isfinite(loo) or not 0.0 <= loo <= 1.0):
                raise ValueError(f'{omitted}: leave-one-out composite invalid for {geoid}')
            signed = None if loo is None else loo - baseline[geoid]
            records.append({
                'GEOID': geoid, 'region': region,
                'loo_defined': loo is not None,
                'loo_score': loo,
                'signed_delta_vs_frozen': signed,
                'absolute_delta_vs_frozen': None if signed is None else abs(signed),
            })
        if len(records) != EXPECTED_ROWS or seen != {geoid for geoid, _ in official_pairs}:
            raise ValueError(f'{omitted}: official GEOID accounting is not exact')
        records_by_component[omitted] = records
        loo_defined = sum(record['loo_defined'] for record in records)
        accounting[omitted] = {
            'official_rows': len(records), 'unique_geoids': len(seen),
            'duplicate_or_missing_geoids': 0,
            'loo_defined_rows': loo_defined, 'loo_undefined_rows': len(records) - loo_defined,
            'partition_exact_once': True,
        }
    return records_by_component, accounting


def group_summary(records_by_id: dict[str, dict[str, Any]], group_ids: list[str],
                  flags: dict[str, dict[str, int]], omitted: str) -> dict[str, Any]:
    selected = [records_by_id[geoid] for geoid in group_ids]
    deltas = [float(row['absolute_delta_vs_frozen']) for row in selected
              if row['absolute_delta_vs_frozen'] is not None]
    loo_defined = len(deltas)
    original_defined = sum(flags[omitted][geoid] for geoid in group_ids)
    return {
        'geoids': len(group_ids),
        'omitted_component_defined_rows': original_defined,
        'omitted_component_undefined_rows': len(group_ids) - original_defined,
        'loo_defined_rows': loo_defined,
        'loo_undefined_rows': len(group_ids) - loo_defined,
        **delta_stats(deltas),
    }


def summaries(official_pairs: list[tuple[str, str]], records_by_component: dict[str, list[dict[str, Any]]],
              flags: dict[str, dict[str, int]], strata_values: dict[str, dict[str, Any]]) -> dict[str, Any]:
    official_ids = [geoid for geoid, _ in official_pairs]
    regions_by_id = dict(official_pairs)
    by_component: dict[str, Any] = {}
    for component, records in records_by_component.items():
        records_by_id = {record['GEOID']: record for record in records}
        region_groups = {
            region: group_summary(records_by_id, [geoid for geoid in official_ids
                                                  if regions_by_id[geoid] == region], flags, component)
            for region in REGIONS
        }
        strata_blocks = []
        for stratum in STRATA_CONFIG['strata']:
            buckets: dict[str, list[str]] = {}
            for geoid in official_ids:
                label, _classification = STRATA.assign_label(stratum, strata_values[geoid])
                buckets.setdefault(label, []).append(geoid)
            groups = {
                label: group_summary(records_by_id, members, flags, component)
                for label, members in buckets.items()
            }
            partition = sum(group['geoids'] for group in groups.values())
            unique_members = [geoid for members in buckets.values() for geoid in members]
            if partition != EXPECTED_ROWS or len(set(unique_members)) != EXPECTED_ROWS:
                raise ValueError(f'{component}/{stratum["stratum_id"]} stratum partition is invalid')
            strata_blocks.append({
                'stratum_id': stratum['stratum_id'], 'family': stratum['family'],
                'column': stratum['column'], 'kind': stratum['kind'],
                'partition_geoids': partition, 'groups': groups,
            })
        overall = group_summary(records_by_id, official_ids, flags, component)
        by_component[component] = {
            'overall': overall,
            'by_region': region_groups,
            'by_provided_strata': strata_blocks,
            'quantiles': [f'p{int(q * 100):02d}' for q in QUANTILES],
            'quantile_method': 'nearest rank; index=max(0, ceil(q*n)-1)',
        }
    return by_component


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path,
                        default=Path(os.environ.get('COMPETITION_WORKSPACE', '')))
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error('pass --workspace or set COMPETITION_WORKSPACE to the authorized workspace')

    official_pairs = MASKS.official_scored_ids(args.workspace)
    official_ids = [geoid for geoid, _ in official_pairs]
    official_region = dict(official_pairs)
    if len(official_pairs) != EXPECTED_ROWS or len(set(official_ids)) != EXPECTED_ROWS:
        raise ValueError('official sample does not contain exactly 9,379 unique IDs')
    sample_template = MASK_CONFIG['frozen_inputs']['official_samples']['path_template']
    sample_pins = MASK_CONFIG['frozen_inputs']['official_samples']['sha256']
    sample_hashes = {}
    for region in REGIONS:
        sample_path = args.workspace / sample_template.format(region=region)
        observed = sha256_file(sample_path)
        pinned = sample_pins[region]
        if observed != pinned:
            raise ValueError(f'{region} official sample hash does not match frozen pin')
        sample_hashes[region] = {
            'path': f'$COMPETITION_WORKSPACE/{sample_template.format(region=region)}',
            'sha256': observed, 'sha256_pin': pinned, 'matches_pin': True,
            'semantic_columns_read': ['GEOID'],
        }
    candidate_path = ROOT / STRATA_CONFIG['candidate']['artifact']
    candidate_rows, candidate_evidence = read_candidate(candidate_path, official_ids)
    gaps: dict[str, dict[str, float]] = {name: {} for name in COMPONENTS}
    gaps, flags, component_evidence = read_components(official_ids)
    baseline, baseline_check = parse_component_values(candidate_rows, official_ids, gaps, flags)

    # Independently compare the candidate's top-level masks with the prior frozen mask audit.
    mask_counts: dict[str, int] = {}
    for geoid in official_ids:
        mask = ''.join(str(flags[name][geoid]) for name in COMPONENTS)
        mask_counts[mask] = mask_counts.get(mask, 0) + 1
    frozen_mask_counts = MASK_AUDIT['defined_mask_patterns']['all_regions']['top_level_defined_mask_counts']
    if mask_counts != frozen_mask_counts:
        raise ValueError('component definedness masks differ from frozen V2 mask audit')

    # Load only preregistered columns from organiser-provided strata tables; non-scored extra rows
    # are excluded by the shared strata reader, and sample score columns are never consumed.
    strata_values, strata_evidence = STRATA.load_strata(args.workspace, official_region)
    if set(strata_values) != set(official_ids):
        raise ValueError('provided strata join does not exactly cover official GEOIDs')
    provenance_manifest_path = ROOT / STRATA_CONFIG['frozen_pins']['provenance_manifest']
    provenance_manifest = json.loads(provenance_manifest_path.read_text(encoding='utf-8'))
    strata_pins = {Path(item['path']).name: item['sha256']
                   for item in provenance_manifest['inputs'] if item['kind'] == 'local_provided'}
    for region, evidence in strata_evidence['per_region'].items():
        filename = Path(evidence['path']).name
        pinned = strata_pins.get(filename)
        if pinned is None or evidence['sha256'] != pinned:
            raise ValueError(f'{region} provided strata hash does not match frozen provenance pin')
        evidence['sha256_pin'] = pinned
        evidence['sha256_matches_pin'] = True

    records_by_component, accounting = variant_records(official_pairs, gaps, flags, baseline)
    component_summaries = summaries(official_pairs, records_by_component, flags, strata_values)

    manifest_path = ROOT / STRATA_CONFIG['candidate']['manifest']
    build_audit_path = ROOT / STRATA_CONFIG['candidate']['build_audit']
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    card = next((item for item in manifest['candidate_set']
                 if item['candidate_id'] == STRATA_CONFIG['candidate']['id']), None)
    build_audit = json.loads(build_audit_path.read_text(encoding='utf-8'))
    candidate_hash = candidate_evidence['sha256']
    if card is None or card['sha256'] != candidate_hash or build_audit['artifact']['sha256'] != candidate_hash:
        raise ValueError('V2 candidate hash does not match its manifest/build audit pins')

    definedness_by_region = {
        component: {
            region: {
                'rows': sum(official_region[geoid] == region for geoid in official_ids),
                'defined_rows': sum(flags[component][geoid] == 1 for geoid in official_ids
                                    if official_region[geoid] == region),
                'undefined_rows': sum(flags[component][geoid] == 0 for geoid in official_ids
                                      if official_region[geoid] == region),
            }
            for region in REGIONS
        }
        for component in COMPONENTS
    }
    all_loo_records = [record for rows in records_by_component.values() for record in rows]
    score_and_abs_delta_bounds = all(
        (record['loo_score'] is None or
         (math.isfinite(record['loo_score']) and 0.0 <= record['loo_score'] <= 1.0))
        and (record['absolute_delta_vs_frozen'] is None or
             (math.isfinite(record['absolute_delta_vs_frozen'])
              and 0.0 <= record['absolute_delta_vs_frozen'] <= 1.0))
        and (record['signed_delta_vs_frozen'] is None or
             (math.isfinite(record['signed_delta_vs_frozen'])
              and -1.0 <= record['signed_delta_vs_frozen'] <= 1.0))
        for record in all_loo_records)
    acceptance = {
        'candidate_manifest_and_build_hashes_verified': True,
        'candidate_official_id_set_matches': candidate_evidence['official_id_set_matches'],
        'official_sample_hashes_verified': all(item['matches_pin'] for item in sample_hashes.values()),
        'provided_strata_hashes_verified': all(
            item['sha256_matches_pin'] for item in strata_evidence['per_region'].values()),
        'component_artifact_hashes_verified': all(
            item['sha256'] == item['sha256_pin'] for item in component_evidence.values()),
        'baseline_composite_reconstructed_within_1e_11': baseline_check['within_1e_11'],
        'official_geoid_coverage_exact_9379': len(official_ids) == EXPECTED_ROWS,
        'each_component_has_exact_definedness_accounting': all(
            component_evidence[name]['defined_rows'] + component_evidence[name]['undefined_rows'] == EXPECTED_ROWS
            for name in COMPONENTS),
        'each_loo_variant_accounts_for_each_geoid_once': all(
            row['partition_exact_once'] and row['official_rows'] == EXPECTED_ROWS
            for row in accounting.values()),
        'all_scores_and_absolute_deltas_finite_in_unit_interval': score_and_abs_delta_bounds,
        'provided_strata_partition_exactly_once': all(
            all(block['partition_geoids'] == EXPECTED_ROWS for block in item['by_provided_strata'])
            for item in component_summaries.values()),
        'per_geoid_delta_record_count_exact': all(
            len(rows) == EXPECTED_ROWS for rows in records_by_component.values()),
        'sample_score_placeholders_not_read': True,
        'labels_or_leaderboard_values_not_used': True,
        'removed_reference_layers_or_equivalents_not_read': True,
        'candidate_not_modified': True,
        'no_upload': True,
    }
    record_index = {
        component: {record['GEOID']: record for record in records}
        for component, records in records_by_component.items()
    }
    report = {
        'schema_version': 1,
        'audit_id': 'V2_COMPONENT_CONTRIBUTION_LOO_V1',
        'backlog_item': 'bias-v2-component-contribution-audit',
        'intent': 'descriptive leave-one-component-out sensitivity of the frozen V2 model composite; not accuracy or target validation',
        'candidate': {
            **candidate_evidence,
            'candidate_id': STRATA_CONFIG['candidate']['id'],
            'sha256_manifest': card['sha256'],
            'manifest_path': STRATA_CONFIG['candidate']['manifest'],
            'manifest_sha256': sha256_file(manifest_path),
            'build_audit_path': STRATA_CONFIG['candidate']['build_audit'],
            'build_audit_sha256': sha256_file(build_audit_path),
            'build_commit_of_record': build_audit['reproducibility']['git_commit'],
            'score_column': STRATA_CONFIG['candidate']['score_column'],
            'component_gap_columns': STRATA_CONFIG['candidate']['component_gap_columns'],
            'component_defined_columns': STRATA_CONFIG['candidate']['component_defined_columns'],
            'official_rows': EXPECTED_ROWS,
        },
        'frozen_configs': {
            'component_mask_config': {'path': str(MASK_CONFIG_PATH.relative_to(ROOT)),
                                      'sha256': sha256_file(MASK_CONFIG_PATH)},
            'component_mask_audit': {'path': str(MASK_AUDIT_PATH.relative_to(ROOT)),
                                     'sha256': sha256_file(MASK_AUDIT_PATH)},
            'strata_config': {'path': str(STRATA_CONFIG_PATH.relative_to(ROOT)),
                              'sha256': sha256_file(STRATA_CONFIG_PATH)},
            'official_sample_hashes': sample_hashes,
            'component_artifacts': component_evidence,
            'provided_strata': strata_evidence,
            'input_provenance_manifest': {
                'path': STRATA_CONFIG['frozen_pins']['provenance_manifest'],
                'sha256': sha256_file(provenance_manifest_path),
            },
        },
        'method': {
            'baseline': 'equal-weight arithmetic mean of model-defined transport, building, and POI gaps; undefined components excluded',
            'leave_one_out': 'omit one component and recompute the equal-weight mean over remaining components whose original frozen flags are defined',
            'delta': 'signed leave-one-out score minus frozen V2 score; contribution metric is its absolute value',
            'undefined_leave_one_out': 'if no remaining components are defined, LOO score and delta are null, counted explicitly as undefined rather than imputed',
            'quantiles': list(QUANTILES),
            'quantile_method': 'nearest rank; index=max(0, ceil(q*n)-1)',
            'strata': STRATA_CONFIG['strata'],
        },
        'baseline_reconstruction': baseline_check,
        'definedness': {
            'components': component_evidence,
            'by_region': definedness_by_region,
            'top_level_mask_counts_observed': dict(sorted(mask_counts.items())),
            'top_level_mask_counts_frozen_audit': frozen_mask_counts,
            'matches_frozen_mask_audit': mask_counts == frozen_mask_counts,
        },
        'variant_accounting': accounting,
        'component_summaries': component_summaries,
        'per_geoid_deltas': [
            {
                'GEOID': geoid, 'region': region,
                **{component: {
                    'loo_defined': record_index[component][geoid]['loo_defined'],
                    'loo_score': record_index[component][geoid]['loo_score'],
                    'signed_delta': record_index[component][geoid]['signed_delta_vs_frozen'],
                    'absolute_delta': record_index[component][geoid]['absolute_delta_vs_frozen'],
                } for component in COMPONENTS},
            } for geoid, region in official_pairs
        ],
        'acceptance': acceptance,
        'data_use': {
            'only_provided_candidate_components_and_strata_used': True,
            'official_samples_read_as_geoids_only': True,
            'sample_score_placeholders_read': False,
            'labels_or_leaderboard_values_used': False,
            'removed_reference_layers_or_equivalents_read': False,
            'per_tract_tuning_performed': False,
            'candidate_modified': False,
            'upload_performed': False,
            'proxy_scores_presented_as_organizer_targets': False,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n',
                        encoding='utf-8')
    print(json.dumps({
        'report': str(args.out), 'acceptance': acceptance,
        'max_abs_baseline_residual': baseline_check['max_abs_composite_reconstruction_residual'],
        'defined_rows': {name: component_evidence[name]['defined_rows'] for name in COMPONENTS},
        'loo_defined_rows': {name: accounting[name]['loo_defined_rows'] for name in COMPONENTS},
        'overall': {name: component_summaries[name]['overall'] for name in COMPONENTS},
    }, sort_keys=True, indent=2))
    return 0 if all(acceptance.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
