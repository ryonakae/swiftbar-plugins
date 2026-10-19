from importlib import import_module
import json
from pathlib import Path
import tempfile
import unittest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

launch_plan = import_module('scripts.backends').launch_plan
observe = import_module('scripts.backends').observe


class LaunchTests(unittest.TestCase):
    def test_splash_start_allows_downloads_without_changing_parent_environment(self):
        cfg = {'port': 8000, 'executable': '/bin/echo', 'modelCacheDir': '/tmp/models'}
        model = {'id': 'x', 'name': 'モデル', 'model': 'example/model:Q4', 'args': ['--max-memory', '12G']}
        env = {'HOME': '/tmp', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
        plan = launch_plan('splash', cfg, model, environment=env)
        self.assertNotIn('--offline', plan['command'])
        self.assertNotIn('HF_HUB_OFFLINE', plan['env'])
        self.assertNotIn('TRANSFORMERS_OFFLINE', plan['env'])
        self.assertEqual(plan['env']['HF_HUB_CACHE'], '/tmp/models')
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')
        self.assertEqual(env['TRANSFORMERS_OFFLINE'], '1')
        self.assertEqual(plan['command'][plan['command'].index('--host') + 1], '127.0.0.1')

    def test_strata_uses_original_config_and_its_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.venv/bin').mkdir(parents=True)
            (root / '.venv/bin/python').symlink_to('/bin/echo')
            (root / 'serve').mkdir()
            (root / 'serve/server.py').write_text('')
            path = root / 'model.json'
            path.write_text(json.dumps({'exe': 'engine/strata', 'cwd': tmp, 'args': ['--spec', '2']}))
            original = path.read_bytes()
            plan = launch_plan('strata', {'directory': tmp, 'port': 8080},
                               {'id': 'x', 'name': 'X', 'config': str(path)}, environment={})
            self.assertEqual(plan['command'][3:5], ['strata', '--config'])
            self.assertEqual(plan['cwd'], tmp)
            self.assertIn(str(path), plan['command'])
            self.assertEqual(path.read_bytes(), original)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'schema_version': 6, 'instance': {'id': 'one', 'model': 'test', 'pid': 123},
                        'ready': True, 'metrics': {'decode_output_tokens': 100},
                        'maximum_context_tokens': 8192, 'scheduler': {'decoding': 0}}
        self.code = 200
        self.truncated = False
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(outer.code)
                if outer.truncated:
                    self.send_header('Transfer-Encoding', 'chunked')
                    self.end_headers()
                    self.wfile.write(b'10\r\nshort')
                    self.close_connection = True
                    return
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(outer.payload).encode())
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_idle_never_shows_previous_generation_rate(self):
        previous = {'instance': 'one', 'generation': 0, 'wake': '', 'count': 0, 'time': 10}
        observation = observe('splash', self.port, previous, now=15)
        self.assertIsNone(observation['tps'])
        self.assertEqual(observation['context'], 8192)

    def test_rate_is_delta_and_resets_on_restart_or_sleep(self):
        self.payload['scheduler'] = {'decoding': 1}
        previous = {'instance': 'one', 'generation': 0, 'wake': '', 'count': 0, 'time': 10}
        self.assertEqual(observe('splash', self.port, previous, now=15)['tps'], 20)
        self.assertIsNone(observe('splash', self.port, previous, now=15, wake='new')['tps'])
        self.payload['transport'] = {'restarts': 1}
        self.assertIsNone(observe('splash', self.port, previous, now=15)['tps'])

    def test_interrupted_http_and_huge_counter_degrade_without_exception(self):
        self.truncated = True
        observed = observe('splash', self.port)
        self.assertEqual(observed['state'], 'occupied')
        self.assertIsNone(observed['tps'])
        self.truncated = False
        self.payload['metrics']['decode_output_tokens'] = 10 ** 400
        self.assertIsNone(observe('splash', self.port)['tps'])

    def test_bad_service_and_auth_are_not_stopped(self):
        self.code = 401
        self.assertEqual(observe('splash', self.port)['state'], 'occupied')
        self.code = 200
        self.payload = {'hello': 'world'}
        self.assertEqual(observe('strata', self.port)['state'], 'occupied')


if __name__ == '__main__':
    unittest.main()
