#!/usr/bin/env python3
"""Audit frozen V2 score distributions across preregistered intersections of provided strata.

No organizer target/label, leaderboard value, removed reference layer, external source, or
per-cell fitting is used. This is a descriptive model-output screen only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MASKS_SPEC = importlib.util.spec_from_file_location(
    'v2_masks_intersections', ROOT / 'scripts/audit_v2_component_masks.py')
STRATA_SPEC = importlib.util.spec_from_file_location(
    'v2_strata_intersections', ROOT / 'scripts/audit_v2_strata_distribution.py')
assert MASKS_SPEC and MASKS_SPEC.loader and STRATA_SPEC and STRATA_SPEC.loader
MASKS = importlib.util.module_from_spec(MASKS_SPEC)
MASKS_SPEC.loader.exec_module(MASKS)
STRATA = importlib.util.module_from_spec(STRATA_SPEC)
STRATA_SPEC.loader.exec_module(STRATA)

CONFIG_PATH = ROOT / 'config/v2-intersectional-descriptive-preregistered.json'
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
MASK_CONFIG_PATH = ROOT / CONFIG['frozen_pins']['mask_config']
MASK_CONFIG: dict[str, Any] = json.loads(MASK_CONFIG_PATH.read_text(encoding='utf-8'))
STRATA_CONFIG_PATH = ROOT / CONFIG['frozen_pins']['strata_config']
STRATA_CONFIG: dict[str, Any] = json.loads(STRATA_CONFIG_PATH.read_text(encoding='utf-8'))
MASK_AUDIT_PATH = ROOT / CONFIG['frozen_pins']['mask_audit']
MASK_AUDIT: dict[str, Any] = json.loads(MASK_AUDIT_PATH.read_text(encoding='utf-8'))
DEFAULT_OUT = ROOT / 'reports/v2-intersectional-descriptive-audit.json'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def load_candidate(path: Path, official_ids: list[str]) -> tuple[dict[str, float], dict[str, Any]]:
    score_column = CONFIG['candidate']['score_column']
    scores: dict[str, float] = {}
    official_set = set(official_ids)
    rows_read = 0
    duplicate_ids = 0
    with path.open(newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        if 'GEOID' not in (reader.fieldnames or []) or score_column not in (reader.fieldnames or []):
            raise ValueError(f'{path} must contain GEOID and {score_column}')
        for row in reader:
            rows_read += 1
            geoid = str(row['GEOID'])
            if geoid in scores:
                duplicate_ids += 1
                raise ValueError(f'duplicate GEOID {geoid} in frozen V2 candidate')
            if geoid not in official_set:
                continue
            try:
                value = float(row[score_column])
            except (TypeError, ValueError) as exc:
                raise ValueError(f'non-numeric {score_column} for {geoid}') from exc
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f'{score_column} non-finite or outside [0,1] for {geoid}')
            scores[geoid] = value
    missing = sorted(set(official_ids) - set(scores))
    if missing or len(scores) != len(official_ids):
        raise ValueError(f'candidate official coverage mismatch: missing={len(missing)}')
    return scores, {
        'path': str(path.relative_to(ROOT)), 'sha256': sha256_file(path),
        'artifact_rows': rows_read, 'official_rows': len(scores),
        'non_official_rows_excluded': rows_read - len(scores),
        'duplicate_ids': duplicate_ids, 'official_id_coverage_exact': True,
    }


def load_definedness(official_ids: list[str]) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
    flags: dict[str, dict[str, int]] = {}
    evidence: dict[str, Any] = {}
    for component in ('transport', 'building', 'poi'):
        pin = MASK_AUDIT['frozen_inputs'][f'{component}_component']
        path = ROOT / pin['path']
        observed = sha256_file(path)
        if observed != pin['sha256']:
            raise ValueError(f'{component} component artifact does not match frozen hash')
        table = MASKS.read_table(path, [pin['defined_column']])
        flags[component] = {}
        for geoid in official_ids:
            if geoid not in table.rows:
                raise ValueError(f'{component} flags missing official GEOID {geoid}')
            flags[component][geoid] = MASKS.parse_flag(
                table.rows[geoid][pin['defined_column']], pin['defined_column'], geoid)
        evidence[component] = {
            'path': pin['path'], 'sha256': observed, 'sha256_pin': pin['sha256'],
            'defined_column': pin['defined_column'], 'rows_read': table.rows_read,
            'defined_rows': sum(flags[component].values()),
            'undefined_rows': len(official_ids) - sum(flags[component].values()),
            'duplicate_ids': table.duplicate_ids,
        }
    patterns: dict[str, int] = {}
    for geoid in official_ids:
        pattern = ''.join(str(flags[name][geoid]) for name in ('transport', 'building', 'poi'))
        patterns[pattern] = patterns.get(pattern, 0) + 1
    frozen = MASK_AUDIT['defined_mask_patterns']['all_regions']['top_level_defined_mask_counts']
    if patterns != frozen:
        raise ValueError('observed component masks do not match frozen V2 mask audit')
    if any(sum(flags[name][geoid] for name in flags) == 0 for geoid in official_ids):
        raise ValueError('at least one official score has no defined component')
    evidence['mask_patterns'] = {
        'observed': dict(sorted(patterns.items())), 'frozen_audit': frozen,
        'matches_frozen_audit': True, 'zero_defined_rows': 0,
    }
    return flags, evidence


def sample_hashes(workspace: Path) -> dict[str, Any]:
    template = MASK_CONFIG['frozen_inputs']['official_samples']['path_template']
    pins = MASK_CONFIG['frozen_inputs']['official_samples']['sha256']
    result: dict[str, Any] = {}
    for region in MASK_CONFIG['scope']['official_regions_order']:
        path = workspace / template.format(region=region)
        observed = sha256_file(path)
        if observed != pins[region]:
            raise ValueError(f'{region} official sample file hash differs from frozen pin')
        result[region] = {
            'path': f'$COMPETITION_WORKSPACE/{template.format(region=region)}',
            'sha256': observed, 'sha256_pin': pins[region],
            'matches_pin': True, 'semantic_columns_read': ['GEOID'],
        }
    return result


def sample_sd(values: list[float]) -> float:
    if len(values) < 2:
        raise ValueError('sample standard deviation requires at least two values')
    mean = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1))


def cell_statistics(geoids: list[str], scores: dict[str, float]) -> dict[str, Any]:
    values = [scores[geoid] for geoid in geoids]
    mean = math.fsum(values) / len(values)
    return {
        'n': len(values), 'mean_score': mean,
        'sample_sd_score': sample_sd(values),
        'min_score': min(values), 'max_score': max(values),
    }


def standardized_mean_difference(a: dict[str, Any], b: dict[str, Any]) -> float:
    pooled_variance = (
        (a['n'] - 1) * a['sample_sd_score'] ** 2
        + (b['n'] - 1) * b['sample_sd_score'] ** 2
    ) / (a['n'] + b['n'] - 2)
    if pooled_variance == 0.0:
        if a['mean_score'] == b['mean_score']:
            return 0.0
        raise ValueError('distinct cell means have zero pooled sample variance')
    return abs(a['mean_score'] - b['mean_score']) / math.sqrt(pooled_variance)


def make_cells(family: dict[str, Any], official_pairs: list[tuple[str, str]],
               scores: dict[str, float], labels: dict[str, dict[str, str]]) -> tuple[
                   dict[str, Any], dict[str, list[str]]]:
    axes = family['axes']
    buckets: dict[tuple[str, ...], list[str]] = {}
    for geoid, region in official_pairs:
        cell_key = tuple(region if axis == 'region' else labels[geoid][axis] for axis in axes)
        buckets.setdefault(cell_key, []).append(geoid)
    if sum(map(len, buckets.values())) != len(official_pairs):
        raise ValueError(f'{family["id"]}: partition count differs from official score count')
    members_seen = [geoid for group in buckets.values() for geoid in group]
    if len(set(members_seen)) != len(official_pairs) or set(members_seen) != {g for g, _ in official_pairs}:
        raise ValueError(f'{family["id"]}: scored GEOIDs are not assigned exactly once')

    threshold = int(CONFIG['cell_threshold'])
    eligible: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    stats_by_key: dict[tuple[str, ...], dict[str, Any]] = {}
    for key in sorted(buckets):
        geoids = buckets[key]
        dimensions = {axis: value for axis, value in zip(axes, key)}
        if len(geoids) >= threshold:
            stats = cell_statistics(geoids, scores)
            stats_by_key[key] = stats
            eligible.append({'dimensions': dimensions, **stats, 'status': 'eligible'})
        else:
            suppressed.append({'dimensions': dimensions, 'n': len(geoids),
                               'status': 'suppressed_below_n_threshold'})

    best: dict[str, Any] | None = None
    pair_count = 0
    sorted_keys = sorted(stats_by_key)
    for i, key_a in enumerate(sorted_keys):
        a = stats_by_key[key_a]
        for key_b in sorted_keys[i + 1:]:
            b = stats_by_key[key_b]
            smd = standardized_mean_difference(a, b)
            pair_count += 1
            if best is None or smd > best['absolute_smd']:
                best = {
                    'cell_a': {axis: value for axis, value in zip(axes, key_a)},
                    'cell_b': {axis: value for axis, value in zip(axes, key_b)},
                    'n_a': a['n'], 'n_b': b['n'],
                    'mean_a': a['mean_score'], 'mean_b': b['mean_score'],
                    'sample_sd_a': a['sample_sd_score'], 'sample_sd_b': b['sample_sd_score'],
                    'absolute_smd': smd,
                }

    observed_domains: dict[str, set[str]] = {}
    for axis in axes:
        observed_domains[axis] = (set(CONFIG['candidate']['official_regions']) if axis == 'region'
                                  else {labels[geoid][axis] for geoid, _ in official_pairs})
    cartesian_size = math.prod(len(domain) for domain in observed_domains.values())
    observed = len(buckets)
    return {
        'intersection_id': family['id'], 'axes': axes,
        'axis_value_counts': {axis: len(domain) for axis, domain in observed_domains.items()},
        'observed_cells': observed,
        'eligible_cell_count': len(eligible), 'suppressed_cell_count': len(suppressed),
        'unobserved_cartesian_combinations_count': cartesian_size - observed,
        'cell_threshold': threshold,
        'eligible_cells': eligible,
        'suppressed_cells': suppressed,
        'pairwise_comparisons': pair_count,
        'maximum_absolute_smd': best,
        'score_statistics_disclosed_only_for_eligible_cells': True,
        'observed_cell_partition_exact_once': True,
    }, buckets


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
    expected = int(CONFIG['candidate']['official_scored_rows'])
    if len(official_ids) != expected or len(set(official_ids)) != expected:
        raise ValueError(f'official scored GEOIDs must be {expected} unique IDs')
    candidate_path = ROOT / CONFIG['candidate']['artifact']
    scores, candidate_evidence = load_candidate(candidate_path, official_ids)
    flags, definedness_evidence = load_definedness(official_ids)
    samples = sample_hashes(args.workspace)

    manifest_path = ROOT / STRATA_CONFIG['candidate']['manifest']
    build_audit_path = ROOT / STRATA_CONFIG['candidate']['build_audit']
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    card = next((entry for entry in manifest['candidate_set']
                 if entry['candidate_id'] == CONFIG['candidate']['id']), None)
    build_audit = json.loads(build_audit_path.read_text(encoding='utf-8'))
    candidate_hash = candidate_evidence['sha256']
    if (card is None or card['sha256'] != candidate_hash
            or build_audit['artifact']['sha256'] != candidate_hash):
        raise ValueError('V2 candidate hash does not match manifest/build audit')

    strata_values, strata_evidence = STRATA.load_strata(args.workspace, official_region)
    if set(strata_values) != set(official_ids):
        raise ValueError('provided strata do not join exactly to official scored GEOIDs')
    provenance_path = ROOT / CONFIG['frozen_pins']['provenance_manifest']
    provenance = json.loads(provenance_path.read_text(encoding='utf-8'))
    strata_pins = {Path(entry['path']).name: entry['sha256']
                   for entry in provenance['inputs'] if entry['kind'] == 'local_provided'}
    for region, evidence in strata_evidence['per_region'].items():
        pinned = strata_pins.get(Path(evidence['path']).name)
        if pinned is None or pinned != evidence['sha256']:
            raise ValueError(f'{region} strata hash does not match frozen provenance manifest')
        evidence['sha256_pin'] = pinned
        evidence['sha256_matches_pin'] = True

    strata_by_id = {entry['stratum_id']: entry for entry in STRATA_CONFIG['strata']}
    required_ids = sorted({axis for family in CONFIG['intersection_families']
                           for axis in family['axes'] if axis != 'region'})
    missing_definitions = sorted(set(required_ids) - set(strata_by_id))
    if missing_definitions:
        raise ValueError(f'intersection config references undefined strata: {missing_definitions}')
    labels: dict[str, dict[str, str]] = {}
    for geoid in official_ids:
        labels[geoid] = {}
        for stratum_id in required_ids:
            label, _classification = STRATA.assign_label(strata_by_id[stratum_id],
                                                          strata_values[geoid])
            labels[geoid][stratum_id] = label

    intersection_results = []
    for family in CONFIG['intersection_families']:
        result, _buckets = make_cells(family, official_pairs, scores, labels)
        intersection_results.append(result)
    all_comparisons = [item for item in intersection_results
                       if item['maximum_absolute_smd'] is not None]
    overall = max(all_comparisons, key=lambda item: item['maximum_absolute_smd']['absolute_smd']) \
        if all_comparisons else None

    before_path = ROOT / CONFIG['frozen_pins']['strata_report']
    before = json.loads(before_path.read_text(encoding='utf-8'))
    if not before.get('acceptance', {}).get('all_passed'):
        raise ValueError('one-way V2 strata report is not passing its acceptance')
    all_assigned = all(item['observed_cell_partition_exact_once']
                       and sum(cell['n'] for cell in item['eligible_cells'])
                       + sum(cell['n'] for cell in item['suppressed_cells']) == expected
                       for item in intersection_results)
    all_cells_classified = all(item['observed_cells'] == item['eligible_cell_count']
                               + item['suppressed_cell_count'] for item in intersection_results)
    comparisons_complete = all(item['pairwise_comparisons'] ==
                               item['eligible_cell_count'] * (item['eligible_cell_count'] - 1) // 2
                               for item in intersection_results)
    acceptance = {
        'candidate_matches_manifest_and_build_hashes': True,
        'candidate_scores_finite_in_unit_interval': all(
            math.isfinite(value) and 0.0 <= value <= 1.0 for value in scores.values()),
        'all_scored_ids_covered_exactly_once': candidate_evidence['official_id_coverage_exact'],
        'every_score_has_at_least_one_defined_component': definedness_evidence['mask_patterns'][
            'zero_defined_rows'] == 0,
        'official_sample_hashes_verified': all(item['matches_pin'] for item in samples.values()),
        'provided_strata_hashes_verified': all(
            item['sha256_matches_pin'] for item in strata_evidence['per_region'].values()),
        'all_scored_ids_assigned_once_per_intersection_family': all_assigned,
        'all_observed_cells_eligible_or_explicitly_suppressed': all_cells_classified,
        'eligible_threshold_respected': all(
            cell['n'] >= CONFIG['cell_threshold'] for result in intersection_results
            for cell in result['eligible_cells']),
        'suppressed_cells_below_threshold': all(
            0 < cell['n'] < CONFIG['cell_threshold'] for result in intersection_results
            for cell in result['suppressed_cells']),
        'all_pairs_of_eligible_cells_compared': comparisons_complete,
        'no_forbidden_data_or_target_claims': True,
        'candidate_not_modified': True,
        'no_upload': True,
        'deterministic_serialization': True,
    }
    acceptance['all_passed'] = all(acceptance.values())
    report = {
        'schema_version': 1,
        'audit_id': CONFIG['audit_id'],
        'backlog_item': CONFIG['backlog_item'],
        'intent': CONFIG['intent'],
        'measure_before': {
            'report': CONFIG['frozen_pins']['strata_report'],
            'sha256': sha256_file(before_path),
            'one_way_strata_measured': before['summary']['strata_measured'],
            'statement': 'The frozen one-way V2 score distribution audit predates this report and contains no joint region-by-rural/urban-by-SVI/CVI/tribal/hazard cell analysis.',
        },
        'candidate': {
            **candidate_evidence, 'candidate_id': CONFIG['candidate']['id'],
            'sha256_manifest': card['sha256'], 'manifest_path': str(manifest_path.relative_to(ROOT)),
            'manifest_sha256': sha256_file(manifest_path),
            'build_audit_path': str(build_audit_path.relative_to(ROOT)),
            'build_audit_sha256': sha256_file(build_audit_path),
            'build_commit_of_record': build_audit['reproducibility']['git_commit'],
            'score_column': CONFIG['candidate']['score_column'],
            'score_min': min(scores.values()), 'score_max': max(scores.values()),
            'score_mean': math.fsum(scores.values()) / len(scores),
            'official_scored_rows': len(scores),
        },
        'frozen_inputs': {
            'audit_script': {'path': str(Path(__file__).resolve().relative_to(ROOT)),
                             'sha256': sha256_file(Path(__file__))},
            'audit_config': {'path': str(CONFIG_PATH.relative_to(ROOT)),
                             'sha256': sha256_file(CONFIG_PATH)},
            'mask_config': {'path': str(MASK_CONFIG_PATH.relative_to(ROOT)),
                            'sha256': sha256_file(MASK_CONFIG_PATH)},
            'mask_audit': {'path': str(MASK_AUDIT_PATH.relative_to(ROOT)),
                           'sha256': sha256_file(MASK_AUDIT_PATH)},
            'strata_config': {'path': str(STRATA_CONFIG_PATH.relative_to(ROOT)),
                              'sha256': sha256_file(STRATA_CONFIG_PATH)},
            'candidate_component_definedness': definedness_evidence,
            'official_samples': samples,
            'provided_strata': strata_evidence,
            'input_provenance_manifest': {'path': str(provenance_path.relative_to(ROOT)),
                                          'sha256': sha256_file(provenance_path)},
        },
        'method': {
            **CONFIG['statistics'],
            'cell_threshold': CONFIG['cell_threshold'],
            'stratum_axis_labels': {key: {'column': strata_by_id[key]['column'],
                                          'kind': strata_by_id[key]['kind'],
                                          'bands': strata_by_id[key].get('bands'),
                                          'missing_label': strata_by_id[key]['missing_label']}
                                    for key in required_ids},
            'cells_total_observed': sum(item['observed_cells'] for item in intersection_results),
            'cells_total_eligible': sum(item['eligible_cell_count'] for item in intersection_results),
            'cells_total_suppressed': sum(item['suppressed_cell_count'] for item in intersection_results),
            'overall_maximum_absolute_smd': {
                'intersection_id': overall['intersection_id'],
                **overall['maximum_absolute_smd'],
            } if overall else None,
        },
        'intersection_families': intersection_results,
        'accounting': {
            'official_scored_geoids': expected,
            'candidate_official_rows': len(scores),
            'unique_official_geoids': len(set(official_ids)),
            'assigned_geoids_per_intersection_family': {
                result['intersection_id']: expected for result in intersection_results},
            'score_defined_geoids': len(scores),
            'zero_defined_component_geoids': definedness_evidence['mask_patterns']['zero_defined_rows'],
        },
        'acceptance': acceptance,
        'data_use': {
            'organizer_targets_or_labels_used': False,
            'leaderboard_values_used': False,
            'removed_reference_layers_or_equivalents_used': False,
            'external_data_used': False,
            'per_cell_fitting_or_tuning_performed': False,
            'causal_or_operational_claims_made': False,
            'candidate_modified': False,
            'upload_performed': False,
            'sample_score_placeholders_read': False,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n',
                        encoding='utf-8')
    print(json.dumps({
        'report': str(args.out), 'acceptance': acceptance,
        'candidate_score_range': [report['candidate']['score_min'], report['candidate']['score_max']],
        'overall_maximum_absolute_smd': report['method']['overall_maximum_absolute_smd'],
        'families': [{key: result[key] for key in ('intersection_id', 'observed_cells',
                     'eligible_cell_count', 'suppressed_cell_count', 'pairwise_comparisons',
                     'maximum_absolute_smd')} for result in intersection_results],
    }, sort_keys=True, indent=2))
    return 0 if acceptance['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
