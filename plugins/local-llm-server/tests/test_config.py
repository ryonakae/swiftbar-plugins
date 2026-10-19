from importlib import import_module
import json
from pathlib import Path
import tempfile
import unittest

load = import_module('scripts.config').load


class ConfigTests(unittest.TestCase):
    def test_registered_models_are_local_and_one_bad_engine_is_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'llm.json'
            path.write_text(json.dumps({
                'strata': {'directory': 'relative', 'models': []},
                'splash': {'models': [{'id': 'test', 'name': '私のモデル',
                                      'model': 'example/model:Q4', 'args': ['--max-memory', '12G']}]},
            }))
            before = path.read_bytes()
            result = load(path)
            self.assertIn('strata', result.errors)
            self.assertEqual(result.engines['splash']['port'], 8000)
            self.assertEqual(result.engines['splash']['models'][0]['model'], 'example/model:Q4')
            self.assertEqual(path.read_bytes(), before)

    def test_managed_arguments_cannot_override_control_values(self):
        for arg in ['--host', '--host=0.0.0.0', '--ho', '--mod=x', '--port=5', '--offline', '--', '-h']:
            with self.subTest(arg=arg), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'llm.json'
                path.write_text(json.dumps({'splash': {'models': [
                    {'id': 'x', 'name': 'x', 'model': 'owner/model', 'args': [arg]}
                ]}}))
                self.assertIn('splash', load(path).errors)

    def test_duplicate_ids_bad_types_and_port_conflicts_are_rejected(self):
        model = {'id': 'x', 'name': 'x', 'model': 'owner/model'}
        cases = [
            {'splash': {'models': [model, model]}},
            {'splash': {'port': True}},
            {'splash': {'models': {}}},
            {'splash': {'models': [{**model, 'args': [12]}]}},
            {'splash': {'models': [{**model, 'name': '\u0000'}]}},
            {'splash': {'port': 8080}, 'strata': {'models': []}},
        ]
        for source in cases:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'llm.json'
                path.write_text(json.dumps(source))
                self.assertTrue(load(path).errors)

    def test_empty_config_does_not_register_any_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'llm.json'
            path.write_text('{}')
            self.assertEqual(load(path).engines, {})
            path.write_text('{')
            self.assertEqual(set(load(path).errors), {'strata', 'splash'})


if __name__ == '__main__':
    unittest.main()
