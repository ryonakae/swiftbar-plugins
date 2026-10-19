from importlib import import_module
import unittest
Configuration = import_module('scripts.config').Configuration
render = import_module('scripts.menu').render


class MenuTests(unittest.TestCase):
    def test_icons_follow_server_state_and_prioritize_errors(self):
        cases = [
            ({}, {}, 'memorychip'),
            ({'strata': {'state': 'stopped'}, 'splash': {'state': 'starting'}}, {}, 'memorychip'),
            ({'splash': {'state': 'stopping'}}, {}, 'memorychip.fill'),
            ({'strata': {'state': 'starting'}, 'splash': {'state': 'running'}}, {}, 'memorychip.fill'),
            ({'splash': {'state': 'unloaded'}}, {}, 'memorychip.fill'),
            ({'splash': {'state': 'external'}}, {}, 'memorychip.fill'),
            ({'splash': {'state': 'unavailable'}}, {}, 'memorychip.fill'),
            ({'strata': {'state': 'error'}, 'splash': {'state': 'running'}}, {}, 'exclamationmark.circle.fill'),
            ({'splash': {'state': 'unknown'}}, {}, 'exclamationmark.circle.fill'),
            ({'splash': {'state': 'occupied'}}, {}, 'exclamationmark.circle.fill'),
            ({'splash': {'state': 'stop_failed'}}, {}, 'exclamationmark.circle.fill'),
            ({'splash': {'state': 'running'}}, {'strata': 'Invalid config'}, 'exclamationmark.circle.fill'),
        ]
        for observations, errors, icon in cases:
            with self.subTest(observations=observations, errors=errors):
                output = render(Configuration(errors=errors), observations, {}, '/tmp/local-llm-server.5s.sh')
                self.assertEqual(output.splitlines()[0], f' | sfimage={icon}')

    def test_broken_config_keeps_owned_stop_and_escapes_model_name(self):
        config = Configuration(errors={'strata': '設定エラー'})
        output = render(config, {'strata': {'owned': True, 'state': 'running',
                        'model_name': '危険\n---|bash=/bin/evil', 'port': 8080}}, {}, '/tmp/local-llm-server.5s.sh')
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
                self.assertEqual(output.splitlines()[0], ' | sfimage=exclamationmark.circle.fill')

    def test_actions_and_metrics_are_english_without_translating_user_names(self):
        configuration = Configuration(engines={'splash': {'port': 8000, 'models': [
            {'id': 'x', 'name': '私のモデル', 'model': 'example/model', 'args': []}]}})
        stopped = render(configuration, {'splash': {'state': 'stopped', 'port': 8000}},
                         {'splash': 'x'}, '/tmp/local-llm-server.5s.sh')
        for label in ['Splash — Stopped', '--Start server |',
                      'Running model:', 'Endpoint:', 'Generation speed:', 'Engine memory:',
                      'Engine PID:', 'Context limit:', '--Models', 'Open chat', 'Open log', 'Open config']:
            self.assertIn(label, stopped)
        self.assertIn('私のモデル', stopped)
        running = render(configuration, {'splash': {'state': 'running', 'owned': True}},
                         {'splash': 'x'}, '/tmp/local-llm-server.5s.sh')
        self.assertIn('Splash — Running', running)
        self.assertIn('--Stop server… |', running)
        self.assertIn('--Context limit: —\n-----\n--Start server |', stopped)
        self.assertIn('--Context limit: —\n-----\n--Stop server… |', running)
        self.assertEqual(stopped.splitlines().count('-----'), 2)

    def test_submenu_headings_have_refresh_actions_without_starting_servers(self):
        config = Configuration(engines={name: {'port': port, 'models': [
            {'id': 'x', 'name': 'Model', 'model': 'example/model'}]}
            for name, port in [('strata', 8080), ('splash', 8000)]})
        output = render(config, {}, {}, '/tmp/local-llm-server.5s.sh')
        self.assertIn('Strata — Stopped | refresh=true\n', output)
        self.assertIn('Splash — Stopped | refresh=true\n', output)
        self.assertEqual(output.splitlines().count('--Models | refresh=true'), 2)
        self.assertNotIn('param1=start', output)

    def test_strata_model_submenus_keep_selection_separate_from_config(self):
        config = Configuration(engines={'strata': {'port': 8080, 'models': [
            {'id': 'first', 'name': 'First', 'config': '/tmp/first.json'},
            {'id': 'second', 'name': 'Second', 'config': '/tmp/second.json'}]}})
        for state in ('stopped', 'running', 'external', 'starting', 'unknown'):
            with self.subTest(state=state):
                output = render(config, {'strata': {'state': state}}, {'strata': 'first'}, '/tmp/plugin.sh')
                lines = output.splitlines()
                for mid, name in [('first', 'First'), ('second', 'Second')]:
                    checked = ' checked=true' if mid == 'first' else ''
                    index = lines.index(f'----{name} | refresh=true{checked}')
                    if state in ('stopped', 'running'):
                        self.assertEqual(lines[index + 1],
                                         f'------Select model | bash=/tmp/plugin.sh param1=select param2=strata param3={mid} terminal=false refresh=true')
                    else:
                        self.assertEqual(lines[index + 1], '------Select model | disabled=true')
                    self.assertEqual(lines[index + 2],
                                     f'------Open model config | bash=/tmp/plugin.sh param1=model-config param2=strata param3={mid} terminal=false refresh=true')

    def test_running_details_are_inside_engine_submenu(self):
        for state, detail in [('unloaded', 'Model status: Unloaded (idle release)'),
                              ('unavailable', 'Monitoring: Unavailable')]:
            with self.subTest(state=state):
                output = render(Configuration(), {'splash': {'state': state, 'owned': True}},
                                {}, '/tmp/local-llm-server.5s.sh')
                self.assertIn('Splash — Running | refresh=true\n', output)
                self.assertIn(f'--{detail}\n', output)
                self.assertLess(output.index(f'--{detail}\n'), output.index('--Stop server…'))
                self.assertEqual(output.splitlines()[0], ' | sfimage=memorychip.fill')

    def test_chat_is_disabled_when_stopped_but_available_when_model_is_unloaded(self):
        config = Configuration(engines={'splash': {'port': 8000, 'models': []}})
        for state in ('stopped', 'unconfigured', 'error', 'running', 'unloaded', 'external'):
            with self.subTest(state=state):
                output = render(config, {'splash': {'state': state}}, {}, '/tmp/plugin.sh')
                if state in ('stopped', 'unconfigured', 'error'):
                    self.assertNotIn('param1=chat param2=splash', output)
                    self.assertIn('--Open chat | disabled=true', output)
                else:
                    self.assertIn('param1=chat param2=splash', output)

    def test_external_server_has_no_mutating_actions(self):
        config = Configuration(engines={'splash': {'port': 8000, 'models': [
            {'id': 'x', 'name': 'x', 'model': 'example/model', 'args': []}]}})
        output = render(config, {'splash': {'owned': False, 'state': 'external', 'port': 8000}},
                        {'splash': 'x'}, '/tmp/local-llm-server.5s.sh')
        for action in ['start', 'stop', 'select', 'download']:
            self.assertNotIn(f'param1={action} param2=splash', output)
        self.assertIn('Running externally', output)


if __name__ == '__main__':
    unittest.main()
