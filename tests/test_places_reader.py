from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bias60.inputs import ProvidedInputs  # noqa: E402
from bias60.ideas.sc_schema import normalize_names  # noqa: E402


class LocalFileSystem:
    def __init__(self, path: Path) -> None:
        self.path = path

    def open_input_file(self, _remote_path: str):
        return self.path.open('rb')


def place_schema() -> pa.Schema:
    names = pa.struct([
        pa.field('primary', pa.string()),
        pa.field('common', pa.map_(pa.string(), pa.string())),
        pa.field('rules', pa.list_(pa.struct([
            pa.field('variant', pa.string()), pa.field('value', pa.string())
        ]))),
    ])
    categories = pa.struct([
        pa.field('primary', pa.string()),
        pa.field('alternate', pa.list_(pa.string())),
    ])
    sources = pa.list_(pa.struct([pa.field('dataset', pa.string())]))
    return pa.schema([
        pa.field('id', pa.string()), pa.field('geometry', pa.binary()),
        pa.field('names', names), pa.field('categories', categories), pa.field('sources', sources),
    ])


class PlacesReaderTests(unittest.TestCase):
    def make_inputs(self, parquet_path: Path) -> ProvidedInputs:
        config = {
            'inputs': {
                'package_bucket': 'public-bucket',
                'package_layer_path_template': '{bucket}/reference/{region}/{region}-overture-{layer}.parquet',
            },
            'forbidden': {'path_tokens': ['tiger', 'usgs_facilit', 'cbp', 'microsoft_building']},
        }
        return ProvidedInputs(config, Path('/tmp'), 'eastern-ok', [],
                              filesystem=LocalFileSystem(parquet_path))

    def test_registered_path_uses_overture_pois_not_places(self) -> None:
        with tempfile.NamedTemporaryFile() as stream:
            inputs = self.make_inputs(Path(stream.name))
            path = inputs.layer_path('pois')
        self.assertEqual(path, 'public-bucket/reference/eastern-ok/eastern-ok-overture-pois.parquet')
        self.assertNotIn('overture-places', path)

    def test_wrong_registered_places_alias_fails_closed(self) -> None:
        with tempfile.NamedTemporaryFile() as stream:
            inputs = self.make_inputs(Path(stream.name))
            inputs.config['inputs']['package_layer_path_template'] = (
                '{bucket}/reference/{region}/{region}-overture-places.parquet')
            with self.assertRaisesRegex(ValueError, 'canonical overture-pois'):
                inputs.layer_path('pois')

    def test_checked_stream_supports_null_empty_and_list_name_values(self) -> None:
        schema = place_schema()
        table = pa.Table.from_pylist([
            {'id': 'p-null', 'geometry': b'\x01', 'names': None,
             'categories': {'primary': 'shop', 'alternate': []}, 'sources': [{'dataset': 'meta'}]},
            {'id': 'p-empty', 'geometry': b'\x02',
             'names': {'primary': '', 'common': {}, 'rules': []},
             'categories': {'primary': None, 'alternate': None}, 'sources': []},
            {'id': 'p-list', 'geometry': b'\x03',
             'names': {'primary': None, 'common': {'en': 'County Road'},
                       'rules': [{'variant': 'official', 'value': 'Route 12'}]},
             'categories': {'primary': 'transport', 'alternate': ['road', 'route']},
             'sources': [{'dataset': 'openstreetmap'}]},
        ], schema=schema)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'places.parquet'
            pq.write_table(table, path)
            inputs = self.make_inputs(path)
            rows = list(inputs.iter_place_records())
        self.assertEqual([row['id'] for row in rows], ['p-null', 'p-empty', 'p-list'])
        self.assertEqual(normalize_names(rows[0]['names']), ())
        self.assertEqual(normalize_names(rows[1]['names']), ())
        self.assertEqual(normalize_names(rows[2]['names']), ('County Road', 'Route 12'))
        self.assertEqual(rows[2]['categories']['alternate'], ['road', 'route'])
        self.assertEqual(rows[2]['sources'][0]['dataset'], 'openstreetmap')

    def test_schema_checks_required_nested_fields(self) -> None:
        good = place_schema()
        ProvidedInputs.validate_places_schema(good)
        bad = pa.schema([field for field in good if field.name != 'sources'])
        with self.assertRaisesRegex(ValueError, 'lacks columns'):
            ProvidedInputs.validate_places_schema(bad)
        malformed_categories = pa.struct([pa.field('primary', pa.string())])
        malformed = pa.schema([
            pa.field('id', pa.string()), pa.field('geometry', pa.binary()),
            pa.field('names', good.field('names').type),
            pa.field('categories', malformed_categories), pa.field('sources', good.field('sources').type),
        ])
        with self.assertRaisesRegex(ValueError, 'categories.alternate'):
            ProvidedInputs.validate_places_schema(malformed)

    def test_all_region_open_report_is_complete(self) -> None:
        report_path = ROOT / 'reports/places-layer-schema.json'
        if not report_path.is_file():
            self.skipTest('remote four-region schema audit has not been run')
        report = json.loads(report_path.read_text())
        self.assertTrue(report['gates']['all_four_regions_open'])
        self.assertEqual(set(report['regions']), {
            'maricopa-az', 'northern-ca', 'eastern-ok', 'south-central-tx'
        })
        self.assertTrue(all(info['rows'] > 0 and info['schema_checked']
                            for info in report['regions'].values()))


if __name__ == '__main__':
    unittest.main()
