#!/usr/bin/env python3
"""Build preregistered, label-free Track A candidates from provided-data idea outputs.

Reads only official GEOID identifiers, GEOID/region template metadata, the idea registry,
its harness hashes, and provided-feature estimator Parquets. It never reads template score
placeholders, labels, reference layers, external-lab data, leaderboard values, or credentials.
No upload is performed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))
from bias60.inputs import ProvidedInputs  # noqa: E402
from bias60.ideas.dst_meta import dst4 as run_dst4, calibrate_probability_mean  # noqa: E402
from bias60.ideas.sca_extra import sca5 as run_sca5  # noqa: E402
WORKSPACE = Path(__import__('os').environ.get('COMPETITION_WORKSPACE', ''))
CONFIG_PATH = ROOT / 'config/track-a-preregistered.json'
REGISTRY_PATH = ROOT / 'config/ideas.json'
HARNESS_PATH = ROOT / 'reports/ideas-harness.json'
OUTPUT_DIR = ROOT / 'reports/candidates'
COLUMNS = ['GEOID', 'transport_gap', 'building_gap', 'poi_gap', 'coverage_gap_score']
REGIONS = ('maricopa-az', 'northern-ca', 'eastern-ok', 'south-central-tx')
TEMPLATE_REGIONS = (*REGIONS, 'eastern-wa')
FIELD_FOR = {'R': 'transport_gap', 'B': 'building_gap'}
COMPONENTS = ('R', 'B', 'F', 'Est')


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def git_commit() -> str:
    """Pin the last committed builder revision, independent of later artifact-only commits."""
    try:
        return subprocess.check_output(
            ['git', '-C', str(ROOT), 'log', '-1', '--format=%H', '--', 'scripts/build_track_a.py'],
            text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return 'unknown'


def official_ids() -> tuple[list[str], dict[str, str]]:
    ids: list[str] = []
    region_of: dict[str, str] = {}
    for region in REGIONS:
        path = WORKSPACE / 'samples' / f'{region}-sample-submission.csv'
        with path.open(newline='', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            header = next(reader)
            if 'GEOID' not in header:
                raise ValueError(f'{path}: missing GEOID')
            idx = header.index('GEOID')
            current = [row[idx] for row in reader]
        if len(current) != len(set(current)):
            raise ValueError(f'{path}: duplicate official GEOID')
        for geoid in current:
            if geoid in region_of:
                raise ValueError(f'duplicate official GEOID across regions: {geoid}')
            region_of[geoid] = region
        ids.extend(current)
    if len(ids) != 9379:
        raise ValueError(f'expected 9,379 official IDs, got {len(ids)}')
    return ids, region_of


def template_ids() -> tuple[list[str], dict[str, str]]:
    path = WORKSPACE / 'SampleSubmission.csv'
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        header = next(reader)
        if not {'GEOID', 'region'} <= set(header):
            raise ValueError('template requires GEOID and region columns')
        gi, ri = header.index('GEOID'), header.index('region')
        pairs = [(row[gi], row[ri]) for row in reader]
    ids = [g for g, _ in pairs]
    regions = dict(pairs)
    if len(ids) != 9794 or len(ids) != len(set(ids)):
        raise ValueError(f'expected 9,794 unique template IDs, got {len(ids)}')
    if set(regions.values()) != set(TEMPLATE_REGIONS):
        raise ValueError(f'unexpected template regions {sorted(set(regions.values()))}')
    return ids, regions


def load_ideas(ids: list[str], region_of: dict[str, str], registry: dict[str, Any],
               harness: dict[str, Any], ideas_dir: Path) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, str]]:
    implemented = {item['id']: item for item in registry['ideas'] if item['status'] == 'implemented'}
    report = harness.get('per_idea', {})
    results: dict[str, dict[str, dict[str, Any]]] = {}
    hashes: dict[str, str] = {}
    idset = set(ids)
    for idea, entry in sorted(implemented.items()):
        if idea in {'X2', 'Tm1', 'Dst4', 'Dst5', 'Sca5'}:
            continue
        path = ideas_dir / f'{idea}.parquet'
        expected = report.get(idea, {}).get('sha256')
        if not expected or not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f'{idea}: missing parquet or mismatch with harness hash')
        table = pq.read_table(path, columns=['GEOID', 'region', 'idea', 'family', 'component',
                                             'channel', 'value', 'defined'])
        data: dict[str, dict[str, Any]] = {}
        for row in table.to_pylist():
            geoid = str(row['GEOID'])
            if geoid not in idset or row['idea'] != idea or row['region'] != region_of[geoid]:
                raise ValueError(f'{idea}: unexpected row or region for {geoid}')
            key = f"{row['component']}:{row['channel']}"
            if key in data.setdefault(geoid, {}):
                raise ValueError(f'{idea}: duplicate emission for {geoid}/{key}')
            value = float(row['value'])
            if not math.isfinite(value):
                raise ValueError(f'{idea}: non-finite value for {geoid}/{key}')
            if row['channel'] == 'gap' and not 0 <= value <= 1:
                raise ValueError(f'{idea}: gap value outside [0,1] for {geoid}/{key}')
            if not row['defined'] and value != 0:
                raise ValueError(f'{idea}: undefined row must carry 0 placeholder')
            data[geoid][key] = {'value': value, 'defined': bool(row['defined']),
                                'family': str(row['family']), 'component': str(row['component']),
                                'channel': str(row['channel'])}
        if set(data) != idset:
            raise ValueError(f'{idea}: GEOID coverage mismatch: {len(idset-set(data))} missing')
        results[idea] = data
        hashes[idea] = expected
    return results, hashes


def eligible_gap_ideas(registry: dict[str, Any], ideas: dict[str, Any]) -> dict[str, list[str]]:
    selected = {c: [] for c in COMPONENTS}
    forbidden = {'X2', 'Tm1'}
    for entry in registry['ideas']:
        idea, component = entry['id'], entry['component']
        if (idea in forbidden or entry['status'] != 'implemented' or component not in selected
                or idea not in ideas):
            continue
        # Channel values are structurally consistent for a registered idea. Inspect first GEOID.
        first = next(iter(ideas[idea].values()))
        if any(emission['channel'] == 'gap' and emission['component'] == component
               for emission in first.values()):
            selected[component].append(idea)
    return selected


def component_estimates(ids: list[str], ideas: dict[str, Any], selected: dict[str, list[str]],
                        family_weights: dict[str, dict[str, float]] | None = None
                        ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, dict[str, float]]]:
    n = len(ids)
    values: dict[str, np.ndarray] = {}
    masks: dict[str, np.ndarray] = {}
    used_weights: dict[str, dict[str, float]] = {}
    for component, idea_ids in selected.items():
        per_idea: dict[str, np.ndarray] = {}
        per_mask: dict[str, np.ndarray] = {}
        family_of: dict[str, str] = {}
        for idea in idea_ids:
            first = next(iter(ideas[idea].values()))
            family_of[idea] = next(iter(first.values()))['family']
            for geo_idx, geoid in enumerate(ids):
                emission = ideas[idea][geoid].get(f'{component}:gap')
                if emission is None:
                    raise ValueError(f'{idea}: missing {component}:gap')
                per_idea.setdefault(idea, np.zeros(n, dtype=float))[geo_idx] = emission['value']
                per_mask.setdefault(idea, np.zeros(n, dtype=bool))[geo_idx] = emission['defined']
        families = sorted(set(family_of.values()))
        fam_values: dict[str, np.ndarray] = {}
        fam_masks: dict[str, np.ndarray] = {}
        for family in families:
            members = [idea for idea in idea_ids if family_of[idea] == family]
            stack = np.vstack([per_idea[i] for i in members])
            mask_stack = np.vstack([per_mask[i] for i in members])
            count = mask_stack.sum(axis=0)
            fam_values[family] = np.divide((stack * mask_stack).sum(axis=0), count,
                                           out=np.zeros(n, dtype=float), where=count > 0)
            fam_masks[family] = count > 0
        weights = (family_weights or {}).get(component)
        if not weights:
            weights = {family: 1.0 / len(families) for family in families} if families else {}
        numerator = np.zeros(n, dtype=float)
        denominator = np.zeros(n, dtype=float)
        for family, weight in weights.items():
            if family not in fam_values:
                continue
            valid = fam_masks[family]
            numerator[valid] += weight * fam_values[family][valid]
            denominator[valid] += weight
        values[component] = np.divide(numerator, denominator, out=np.zeros(n), where=denominator > 0)
        masks[component] = denominator > 0
        used_weights[component] = {k: float(v) for k, v in weights.items() if k in fam_values}
    return values, masks, used_weights


def spectral_weights(ids: list[str], ideas: dict[str, Any], selected: dict[str, list[str]]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    n = len(ids)
    for component, idea_ids in selected.items():
        family_members: dict[str, list[str]] = {}
        for idea in idea_ids:
            first = next(iter(ideas[idea].values()))
            family = next(iter(first.values()))['family']
            family_members.setdefault(family, []).append(idea)
        family_values: dict[str, np.ndarray] = {}
        family_mask: dict[str, np.ndarray] = {}
        for family, members in sorted(family_members.items()):
            stacks, masks = [], []
            for idea in members:
                stacks.append(np.array([ideas[idea][g][f'{component}:gap']['value'] for g in ids]))
                masks.append(np.array([ideas[idea][g][f'{component}:gap']['defined'] for g in ids]))
            stack, mask = np.vstack(stacks), np.vstack(masks)
            count = mask.sum(axis=0)
            family_values[family] = np.divide((stack * mask).sum(axis=0), count,
                                               out=np.zeros(n), where=count > 0)
            family_mask[family] = count > 0
        families = sorted(family_values)
        if len(families) < 2:
            result[component] = {f: 1.0 / len(families) for f in families} if families else {}
            continue
        matrix = np.column_stack([family_values[f] for f in families])
        mask = np.column_stack([family_mask[f] for f in families]).all(axis=1)
        matrix = matrix[mask]
        if matrix.shape[0] < max(10, 2 * len(families)):
            result[component] = {f: 1.0 / len(families) for f in families}
            continue
        std = matrix.std(axis=0)
        keep = std > 1e-12
        if keep.sum() < 2:
            result[component] = {f: 1.0 / len(families) for f in families}
            continue
        z = (matrix[:, keep] - matrix[:, keep].mean(axis=0)) / std[keep]
        corr = np.corrcoef(z, rowvar=False)
        off = corr.copy(); np.fill_diagonal(off, 0.0)
        try:
            eigenvalues, eigenvectors = np.linalg.eigh(off)
            scores = z @ eigenvectors[:, -1]
            loadings = np.array([np.corrcoef(z[:, j], scores)[0, 1] for j in range(z.shape[1])])
        except (np.linalg.LinAlgError, FloatingPointError):
            result[component] = {f: 1.0 / len(families) for f in families}
            continue
        if not np.isfinite(loadings).all() or np.max(np.abs(loadings)) < 1e-8:
            result[component] = {f: 1.0 / len(families) for f in families}
            continue
        # Orient the latent axis toward the equal-family physical anchor; Bartlett precision weights.
        anchor = z.mean(axis=1)
        if np.corrcoef(scores, anchor)[0, 1] < 0:
            loadings *= -1
        raw = np.maximum(loadings, 0.0) / np.maximum(1.0 - loadings ** 2, 0.05)
        if raw.sum() <= 0 or not np.isfinite(raw).all():
            raw = np.ones_like(raw)
        raw /= raw.sum()
        weights = {f: 0.0 for f in families}
        for family, weight, retained in zip(np.asarray(families)[keep], raw, [True] * int(keep.sum())):
            weights[str(family)] = float(weight)
        result[component] = weights
    return result


def dawid_skene(observations: np.ndarray, alpha: float = 1.0, max_iter: int = 500,
                tolerance: float = 1e-10) -> tuple[np.ndarray, dict[str, Any]]:
    """Binary Dawid-Skene EM. NaNs are missing annotations, not negative votes."""
    x = np.asarray(observations, dtype=float)
    if x.ndim != 2 or x.shape[1] < 2 or np.any((x[~np.isnan(x)] != 0) & (x[~np.isnan(x)] != 1)):
        raise ValueError('Dawid-Skene needs an n-by-at-least-2 binary/missing matrix')
    observed = ~np.isnan(x)
    if not observed.any():
        return np.zeros(x.shape[0]), {'iterations': 0, 'prevalence': 0.0}
    prevalence = float(np.clip(np.nanmean(x), 1e-4, 1 - 1e-4))
    sensitivity = np.full(x.shape[1], 0.75)
    specificity = np.full(x.shape[1], 0.75)
    posterior = np.full(x.shape[0], prevalence)
    for iteration in range(1, max_iter + 1):
        log1 = np.full(x.shape[0], math.log(prevalence))
        log0 = np.full(x.shape[0], math.log1p(-prevalence))
        for j in range(x.shape[1]):
            m = observed[:, j]
            bit = x[m, j]
            log1[m] += np.where(bit == 1, math.log(sensitivity[j]), math.log1p(-sensitivity[j]))
            log0[m] += np.where(bit == 1, math.log1p(-specificity[j]), math.log(specificity[j]))
        newer = 1.0 / (1.0 + np.exp(np.clip(log0 - log1, -700, 700)))
        prev_new = float((newer.sum() + alpha) / (len(newer) + 2 * alpha))
        se_new = np.empty(x.shape[1]); sp_new = np.empty(x.shape[1])
        for j in range(x.shape[1]):
            m = observed[:, j]
            one = x[m, j] == 1
            zero = ~one
            truth = newer[m]
            se_new[j] = (truth[one].sum() + alpha) / (truth[one].sum() + (truth[zero]).sum() + 2 * alpha)
            sp_new[j] = ((1.0 - truth[zero]).sum() + alpha) / ((1.0 - truth[zero]).sum() + (1.0 - truth[one]).sum() + 2 * alpha)
        delta = max(abs(prev_new - prevalence), float(np.max(np.abs(se_new - sensitivity))),
                    float(np.max(np.abs(sp_new - specificity))))
        posterior, prevalence, sensitivity, specificity = newer, prev_new, se_new, sp_new
        if delta < tolerance:
            break
    return posterior, {'iterations': iteration, 'prevalence': prevalence,
                       'sensitivity': sensitivity.tolist(), 'specificity': specificity.tolist()}


def recompute_published_anchors(workspace: Path, ids: list[str], region_of: dict[str, str],
                                registry: dict[str, Any]) -> tuple[dict[str, float], dict[str, float], dict[str, Any]]:
    """Recompute Sca5/Dst5 without the forbidden Tm1 input or transitive artifacts."""
    from bias60.inputs import load_config
    harness_config = load_config(ROOT / 'config/bias60-harness-preregistered.json')
    filtered = [entry for entry in registry['ideas']
                if entry['status'] == 'implemented' and entry['id'] not in {'X2', 'Tm1'}]
    harness_config['__registry__'] = {'ideas': filtered}
    harness_config['seeds']['Dst4']['source_ideas'] = [
        idea for idea in harness_config['seeds']['Dst4']['source_ideas'] if idea not in {'X2', 'Tm1'}]
    sca_by_id: dict[str, float] = {}
    dst_by_id: dict[str, float] = {}
    diagnostics: dict[str, Any] = {'tm1_excluded': True, 'regions': {},
                                   'derived_parquets_consumed': False}
    for region in REGIONS:
        region_ids = [geoid for geoid in ids if region_of[geoid] == region]
        inputs = ProvidedInputs(harness_config, workspace, region, region_ids)
        inputs.idea_output_dir = workspace / 'ideas'
        sca = run_sca5(inputs).emissions[0]
        dst4 = run_dst4(inputs).emissions[0]
        seed = harness_config['seeds']['Dst5']
        defined = np.asarray(dst4.defined, dtype=bool)
        calibrated = np.zeros(len(region_ids), dtype=float)
        offset = None
        if defined.any():
            values, offset = calibrate_probability_mean(
                np.asarray(dst4.values, dtype=float)[defined],
                float(seed['undefined_share_by_region'][region]),
                float(seed['epsilon']), int(seed['iterations']))
            calibrated[defined] = values
        for geoid, value in zip(region_ids, np.asarray(sca.values, dtype=float)):
            sca_by_id[geoid] = float(value)
        for geoid, value in zip(region_ids, calibrated):
            dst_by_id[geoid] = float(value)
        diagnostics['regions'][region] = {
            'sca5_defined': int(np.asarray(sca.defined, dtype=bool).sum()),
            'dst4_defined': int(defined.sum()),
            'dst5_published_undefined_share': float(seed['undefined_share_by_region'][region]),
            'dst5_logit_offset': offset,
            'accessed_inputs': {key: sorted(set(value)) for key, value in inputs.accessed.items()},
        }
    return sca_by_id, dst_by_id, diagnostics


def candidate_components(ids: list[str], region_of: dict[str, str], ideas: dict[str, Any],
                          selected: dict[str, list[str]], method: str,
                          anchors: tuple[dict[str, float], dict[str, float], dict[str, Any]] | None = None
                          ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, Any]]:
    if method in {'A2', 'A3'}:
        weights = spectral_weights(ids, ideas, selected)
    else:
        # Equal vote per idea: family mean × family member share is exactly the pooled idea mean.
        weights = {}
        for component, members in selected.items():
            family_sizes: dict[str, int] = {}
            for idea in members:
                first = next(iter(ideas[idea].values()))
                family = next(iter(first.values()))['family']
                family_sizes[family] = family_sizes.get(family, 0) + 1
            total = sum(family_sizes.values())
            weights[component] = {family: size / total for family, size in family_sizes.items()} if total else {}
    values, masks, component_weights = component_estimates(ids, ideas, selected, weights)
    diagnostics: dict[str, Any] = {'family_weights': component_weights}
    if method == 'A3':
        witness_ids = ['X1', 'Sc2', 'Mx5', 'P3', 'S3', 'D2', 'X5']
        available = [idea for idea in witness_ids if idea in ideas]
        observations = []
        for idea in available:
            # Witnesses may emit more than one channel; prefer the registered primary component row.
            rows = []
            for geoid in ids:
                emissions = ideas[idea][geoid]
                row = next((x for x in emissions.values() if x['component'] == 'F'),
                           next(iter(emissions.values())))
                rows.append(float(row['value']) if row['defined'] else np.nan)
            observations.append(np.array(rows))
        available_columns = [col for col in observations if np.isfinite(col).any()]
        if len(available_columns) >= 2:
            bits = np.column_stack([np.where(np.isfinite(col), (col >= 0.5).astype(float), np.nan)
                                    for col in available_columns])
            posterior, ds_meta = dawid_skene(bits)
            values['F'] = posterior
            masks['F'] = np.isfinite(bits).sum(axis=1) > 0
            diagnostics['dawid_skene'] = {'witnesses': available[:len(available_columns)], **ds_meta}
    # Map component estimates to the required challenge output schema. F and Est are POI halves.
    field_values: dict[str, np.ndarray] = {}
    field_masks: dict[str, np.ndarray] = {}
    for component, field in FIELD_FOR.items():
        field_values[field] = values.get(component, np.zeros(len(ids)))
        field_masks[field] = masks.get(component, np.zeros(len(ids), dtype=bool))
    poi_numerator = np.zeros(len(ids)); poi_denominator = np.zeros(len(ids))
    for component in ('F', 'Est'):
        valid = masks.get(component, np.zeros(len(ids), dtype=bool))
        poi_numerator[valid] += values.get(component, np.zeros(len(ids)))[valid]
        poi_denominator[valid] += 1.0
    field_values['poi_gap'] = np.divide(poi_numerator, poi_denominator,
                                        out=np.zeros(len(ids)), where=poi_denominator > 0)
    field_masks['poi_gap'] = poi_denominator > 0
    if method == 'A1':
        if anchors is None:
            raise ValueError('A1 requires recomputed filtered Sca5/Dst5 anchors')
        sca_map, dst_map, anchor_meta = anchors
        sca = np.array([sca_map[g] for g in ids])
        dst = np.array([dst_map[g] for g in ids])
        if not np.isfinite(sca).all() or not np.isfinite(dst).all():
            raise ValueError('filtered Sca5/Dst5 anchor is non-finite')
        composite = sca.copy()
        for region in sorted(set(region_of.values())):
            idx = np.array([region_of[g] == region for g in ids])
            if idx.any():
                composite[idx] = np.clip(sca[idx] + (dst[idx].mean() - sca[idx].mean()), 0.0, 1.0)
        diagnostics['a1_region_level_anchor'] = 'region-wise Dst5 mean; additive offset to filtered Sca5 expected-definedness composite'
        diagnostics['filtered_published_anchors'] = anchor_meta
    else:
        cols = [field_values['transport_gap'], field_values['building_gap'], field_values['poi_gap']]
        masks3 = [field_masks['transport_gap'], field_masks['building_gap'], field_masks['poi_gap']]
        matrix = np.column_stack(cols); mask = np.column_stack(masks3)
        count = mask.sum(axis=1)
        composite = np.divide((matrix * mask).sum(axis=1), count,
                              out=np.zeros(len(ids)), where=count > 0)
        diagnostics['composite_rule'] = 'equal mean over defined R, B, and POI estimates'
    field_values['coverage_gap_score'] = composite
    for field, data in field_values.items():
        if not np.isfinite(data).all() or np.any((data < 0) | (data > 1)):
            raise ValueError(f'{method} {field} outside finite [0,1]')
    return field_values, field_masks, diagnostics


def write_csv(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator='\n')
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def build(workspace: Path, ideas_dir: Path | None = None) -> dict[str, Any]:
    if not workspace.is_dir():
        raise ValueError(f'workspace is required: {workspace}')
    config = json.loads(CONFIG_PATH.read_text())
    registry = json.loads(REGISTRY_PATH.read_text())
    harness = json.loads(HARNESS_PATH.read_text())
    ids, region_of = official_ids()
    template, template_region = template_ids()
    if set(ids) - set(template) or sum(1 for g in template if g not in region_of) != 415:
        raise ValueError('platform template must contain 9,379 official IDs plus 415 extras')
    data, idea_hashes = load_ideas(ids, region_of, registry, harness,
                                   ideas_dir or workspace / 'ideas')
    selected = eligible_gap_ideas(registry, data)
    anchors = recompute_published_anchors(workspace, ids, region_of, registry)
    if not any(selected['R']) or not any(selected['B']) or not (selected['F'] or selected['Est']):
        raise ValueError(f'gap estimators do not cover all components: {selected}')
    config_hash = sha256_file(CONFIG_PATH)
    if hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest() != config_hash:
        raise AssertionError('preregistration file hash is unstable')
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    official_position = {geoid: index for index, geoid in enumerate(ids)}
    outputs = {}
    for candidate in config['candidates']:
        method = candidate['method']
        values, masks, diagnostics = candidate_components(
            ids, region_of, data, selected, method, anchors if method == 'A1' else None)
        rows: list[dict[str, str]] = []
        for geoid in template:
            if geoid not in region_of:
                row = {'GEOID': geoid, **{c: '0' for c in COLUMNS[1:]}}
            else:
                row = {'GEOID': geoid}
                for col in COLUMNS[1:]:
                    row[col] = format(float(values[col][official_position[geoid]]), '.17g')
            rows.append(row)
        if [r['GEOID'] for r in rows] != template:
            raise AssertionError('candidate order differs from template')
        payload = write_csv(rows)
        path = OUTPUT_DIR / f"{candidate['id']}.csv"
        path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        manifest = {
            'schema_version': 1, 'candidate_id': candidate['id'], 'method': method,
            'description': candidate['purpose'], 'preregistered_config_sha256': config_hash,
            'preregistered_config_path': 'config/track-a-preregistered.json',
            'config_commit': subprocess.check_output(['git','-C',str(ROOT),'log','-1','--format=%H','--','config/track-a-preregistered.json'], text=True).strip(),
            'build_commit': git_commit(), 'artifact': str(path.relative_to(ROOT)), 'sha256': digest,
            'rows': len(rows), 'official_rows': len(ids), 'template_only_rows': 415,
            'columns': COLUMNS, 'idea_input_sha256': idea_hashes,
            'selected_gap_ideas': selected, 'diagnostics': diagnostics,
            'anchor_recompute': anchors[2] if method == 'A1' else None,
            'forecast': {'status':'not_claimed_no_labels','predicted_score':None},
            'eastern_wa_fill': {'value':0.0,'rows':415,'scored_target_claim':False},
            'uploads': 0, 'uses_lab_values': False, 'uses_reference_layers': False,
            'uses_leaderboard_values': False,
        }
        (OUTPUT_DIR / f"{candidate['id']}_manifest.json").write_bytes(canonical_bytes(manifest))
        outputs[candidate['id']] = manifest
    report = {'schema_version':1,'preregistered_config_sha256':config_hash,
              'preregistered_config_commit':outputs[config['candidates'][0]['id']]['config_commit'],
              'build_commit':git_commit(),'input_harness_sha256':sha256_file(HARNESS_PATH),
              'idea_registry_sha256':sha256_file(REGISTRY_PATH),'candidate_set':outputs,
              'validation':{'official_ids':len(ids),'template_ids':len(template),
                            'all_finite_bounded':True,'deterministic_rerun':None,
                            'scores_are_forecasts':False,'uploads':0}}
    report_path = ROOT / 'reports/track-a-build.json'
    report_path.write_bytes(canonical_bytes(report))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=WORKSPACE)
    parser.add_argument('--ideas-dir', type=Path, default=None)
    args = parser.parse_args()
    first = build(args.workspace, args.ideas_dir)
    first_hashes = {name: (item['sha256'], sha256_file(ROOT / item['artifact']))
                    for name, item in first['candidate_set'].items()}
    second = build(args.workspace, args.ideas_dir)
    second_hashes = {name: (item['sha256'], sha256_file(ROOT / item['artifact']))
                     for name, item in second['candidate_set'].items()}
    if first_hashes != second_hashes:
        raise ValueError(f'Track A rerun is not byte-identical: {first_hashes} != {second_hashes}')
    second['validation']['deterministic_rerun'] = True
    (ROOT / 'reports/track-a-build.json').write_bytes(canonical_bytes(second))
    for candidate, item in second['candidate_set'].items():
        print(f"PASS {candidate}: rows={item['rows']} sha256={item['sha256']}")
    print('PASS Track A: 3 candidates are byte-identical on rerun; no upload or accuracy claim')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
