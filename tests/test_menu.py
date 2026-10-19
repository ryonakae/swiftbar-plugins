import unittest
from scripts.llm.config import Configuration
from scripts.llm.menu import render


class MenuTests(unittest.TestCase):
    def test_broken_config_keeps_owned_stop_and_escapes_model_name(self):
        config = Configuration(errors={'strata': '設定エラー'})
        output = render(config, {'strata': {'owned': True, 'state': 'running',
                        'model_name': '危険\n---|bash=/bin/evil', 'port': 8080}}, {}, '/tmp/llm.5s.sh')
        self.assertIn('param1=stop param2=strata', output)
        self.assertNotIn('\n---|bash=/bin/evil', output)
        self.assertIn('Engine memory: —', output)
        self.assertTrue(output.startswith(' | sfimage='))

    def test_entry_path_cannot_add_menu_lines_or_parameters(self):
        for path in ['/tmp/a\n--Injected | bash=/bin/echo/x.sh', '/tmp/a|bash=/bin/echo/x.sh']:
            with self.subTest(path=path):
                output = render(Configuration(), {}, {}, path)
                self.assertNotIn('bash=', output)
                self.assertNotIn('Injected', output)
                self.assertIn('Plugin path', output)

    def test_actions_and_metrics_are_english_without_translating_user_names(self):
        configuration = Configuration(engines={'splash': {'port': 8000, 'models': [
            {'id': 'x', 'name': '私のモデル', 'model': 'example/model', 'args': []}]}})
        stopped = render(configuration, {'splash': {'state': 'stopped', 'port': 8000}},
                         {'splash': 'x'}, '/tmp/llm.5s.sh')
        for label in ['Splash — Stopped', '--Start |', 'Download / update and start…',
                      'Running model:', 'Endpoint:', 'Generation speed:', 'Engine memory:',
                      'Engine PID:', 'Context limit:', '--Models', 'Open chat', 'Open log', 'Open config']:
            self.assertIn(label, stopped)
        self.assertIn('私のモデル', stopped)
        running = render(configuration, {'splash': {'state': 'running', 'owned': True}},
                         {'splash': 'x'}, '/tmp/llm.5s.sh')
        self.assertIn('Splash — Running', running)
        self.assertIn('--Stop… |', running)

    def test_external_server_has_no_mutating_actions(self):
        config = Configuration(engines={'splash': {'port': 8000, 'models': [
            {'id': 'x', 'name': 'x', 'model': 'example/model', 'args': []}]}})
        output = render(config, {'splash': {'owned': False, 'state': 'external', 'port': 8000}},
                        {'splash': 'x'}, '/tmp/llm.5s.sh')
        for action in ['start', 'stop', 'select', 'download']:
            self.assertNotIn(f'param1={action} param2=splash', output)
        self.assertIn('Running externally', output)


if __name__ == '__main__':
    unittest.main()
