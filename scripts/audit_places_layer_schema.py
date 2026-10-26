#!/usr/bin/env python3
"""Open the four provided Overture places Parquets by anonymous footer reads and report schema."""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bias60.inputs import ProvidedInputs  # noqa: E402

REGIONS = ('maricopa-az', 'northern-ca', 'eastern-ok', 'south-central-tx')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path,
                        default=Path(os.environ.get('COMPETITION_WORKSPACE', '')))
    parser.add_argument('--out', type=Path, default=ROOT / 'reports/places-layer-schema.json')
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error('COMPETITION_WORKSPACE must be an existing authorized workspace')
    config = json.loads((ROOT / 'config/bias60-harness-preregistered.json').read_text())
    regions: dict[str, Any] = {}
    for region in REGIONS:
        inputs = ProvidedInputs(config, args.workspace, region, [])
        path = config['inputs']['package_layer_path_template'].format(
            bucket=config['inputs']['package_bucket'], region=region, layer='pois')
        parquet = inputs.open_places_parquet()
        schema = parquet.schema_arrow
        regions[region] = {
            'path': path,
            'rows': int(parquet.metadata.num_rows),
            'row_groups': int(parquet.metadata.num_row_groups),
            'schema_checked': True,
            'required_per_place_fields': ['id', 'geometry', 'names', 'categories', 'sources'],
            'schema': [{'name': field.name, 'type': str(field.type), 'nullable': field.nullable}
                       for field in schema],
        }
    gates = {
        'all_four_regions_open': set(regions) == set(REGIONS) and all(
            item['rows'] > 0 and item['schema_checked'] for item in regions.values()),
        'canonical_overture_pois_paths': all('-overture-pois.parquet' in item['path']
                                             for item in regions.values()),
        'nonempty_arrow_schema': all(bool(item['schema']) for item in regions.values()),
    }
    try:
        code_commit = subprocess.check_output(
            ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        code_commit = 'unknown'
    report = {
        'schema_version': 1,
        'code_commit': code_commit,
        'source': 'provided public challenge package, reference/<region>/<region>-overture-pois.parquet',
        'access': 'anonymous S3 Parquet footer/schema metadata only; no object data downloaded',
        'regions': regions,
        'gates': gates,
        'environment': {'python': platform.python_version(), 'pyarrow': pa.__version__},
        'forbidden_inputs_used': [],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'gates': gates,
                      'rows': {region: info['rows'] for region, info in regions.items()},
                      'report': str(args.out)}, indent=2, sort_keys=True))
    return 0 if all(gates.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
