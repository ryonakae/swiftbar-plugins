import json
from pathlib import Path
import tempfile
import unittest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scripts.llm.backends import launch_plan, observe


class LaunchTests(unittest.TestCase):
    def test_splash_offline_and_online_do_not_change_parent_environment(self):
        cfg = {'port': 8000, 'executable': '/bin/echo', 'modelCacheDir': '/tmp/models'}
        model = {'id': 'x', 'name': 'モデル', 'model': 'example/model:Q4', 'args': ['--max-memory', '12G']}
        env = {'HOME': '/tmp', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
        offline = launch_plan('splash', cfg, model, False, env)
        online = launch_plan('splash', cfg, model, True, env)
        self.assertIn('--offline', offline['command'])
        self.assertNotIn('--offline', online['command'])
        self.assertNotIn('HF_HUB_OFFLINE', online['env'])
        self.assertEqual(online['env']['HF_HUB_CACHE'], '/tmp/models')
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')
        self.assertEqual(offline['command'][offline['command'].index('--host') + 1], '127.0.0.1')

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
                               {'id': 'x', 'name': 'X', 'config': str(path)}, False, {})
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
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(outer.code)
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

    def test_bad_service_and_auth_are_not_stopped(self):
        self.code = 401
        self.assertEqual(observe('splash', self.port)['state'], 'occupied')
        self.code = 200
        self.payload = {'hello': 'world'}
        self.assertEqual(observe('strata', self.port)['state'], 'occupied')


if __name__ == '__main__':
    unittest.main()
