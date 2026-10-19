from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.llm.app import Controller, Paths


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@contextmanager
def external_splash():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'schema_version': 6, 'instance': {'id': 'external', 'model': 'external-model'},
                                        'ready': True, 'metrics': {'decode_output_tokens': 0}}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths(Path(self.tmp.name))
        self.paths.config.parent.mkdir(parents=True)
        self.binary = Path(self.tmp.name) / 'fake-splash'
        self.binary.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(120)\n')
        self.binary.chmod(0o700)
        self.source = {'splash': {'port': free_port(), 'executable': str(self.binary), 'models': [
            {'id': 'first', 'name': '最初', 'model': 'example/first'},
            {'id': 'second', 'name': '次', 'model': 'example/second'}]}}
        self.paths.config.write_text(json.dumps(self.source))
        self.questions = []
        self.answer = True
        def confirm(message):
            self.questions.append(message)
            return self.answer
        self.app = Controller(self.paths, confirm=confirm)
        self.addCleanup(self.stop_if_owned)

    def stop_if_owned(self):
        status = self.app.manager.status('splash')
        if status.get('owned'):
            self.app.manager.stop('splash', status['nonce'])

    def start(self):
        self.app.action('select', 'splash', 'first')
        return self.app.action('start', 'splash')

    def test_stopped_selection_never_starts_and_missing_selection_is_not_defaulted(self):
        with self.assertRaises(ValueError):
            self.app.action('start', 'splash')
        self.app.action('select', 'splash', 'second')
        self.assertEqual(self.app.selection('splash'), 'second')
        self.assertFalse(self.app.manager.status('splash')['owned'])
        self.assertEqual(self.questions, [])

    def test_cancelled_switch_and_stop_preserve_running_server_and_selection(self):
        status = self.start()
        self.assertTrue(status['owned'])
        self.answer = False
        self.app.action('select', 'splash', 'second')
        self.app.action('stop', 'splash')
        self.assertEqual(self.app.selection('splash'), 'first')
        self.assertEqual(self.app.manager.status('splash')['nonce'], status['nonce'])
        self.assertEqual(len(self.questions), 2)

    def test_stop_works_after_config_is_removed_and_always_confirms(self):
        self.start()
        self.paths.config.unlink()
        stopped = self.app.action('stop', 'splash')
        self.assertEqual(stopped['state'], 'stopped')
        self.assertEqual(len(self.questions), 1)

    def test_confirmation_revalidates_settings_before_stopping(self):
        status = self.start()
        def mutate_and_confirm(message):
            self.paths.config.write_text('{}')
            return True
        self.app.confirm = mutate_and_confirm
        with self.assertRaisesRegex(ValueError, '確認中'):
            self.app.action('stop', 'splash')
        self.assertEqual(self.app.manager.status('splash')['nonce'], status['nonce'])

    def test_online_start_cancel_does_not_start_or_change_selection(self):
        self.app.action('select', 'splash', 'first')
        self.answer = False
        self.app.action('download', 'splash')
        self.assertFalse(self.app.manager.status('splash')['owned'])
        self.assertEqual(self.app.selection('splash'), 'first')
        self.assertEqual(len(self.questions), 1)

    def test_failed_stop_does_not_change_selection_or_start_next_model(self):
        marker = Path(self.tmp.name) / 'ready'
        self.binary.write_text(f'#!{sys.executable}\nimport signal,time\nfrom pathlib import Path\n'
                               f'signal.signal(signal.SIGTERM, signal.SIG_IGN)\nPath({str(marker)!r}).touch()\ntime.sleep(10)\n')
        old = self.start()
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists())
        with self.assertRaisesRegex(ValueError, '停止が完了'):
            self.app.action('select', 'splash', 'second')
        self.assertEqual(self.app.selection('splash'), 'first')
        self.assertEqual(self.app.manager.status('splash')['nonce'], old['nonce'])

    def test_launch_error_remains_visible_until_next_successful_action(self):
        self.app.action('select', 'splash', 'first')
        self.binary.unlink()
        with self.assertRaises(ValueError):
            self.app.action('start', 'splash')
        output = self.app.render('/tmp/llm.5s.sh')
        self.assertIn('実行可能ファイルが見つかりません', output)
        self.assertFalse(self.app.manager.status('splash')['owned'])

    def test_chat_after_stop_uses_current_configured_port(self):
        self.start()
        self.assertEqual(self.app.action('stop', 'splash')['state'], 'stopped')
        port = free_port()
        self.source['splash']['port'] = port
        self.paths.config.write_text(json.dumps(self.source))
        self.assertIn(f'接続先: 127.0.0.1:{port}', self.app.render('/tmp/llm.5s.sh'))
        with patch('subprocess.run') as opened:
            self.app.open_target('chat', 'splash')
        self.assertEqual(opened.call_args.args[0], ['/usr/bin/open', f'http://127.0.0.1:{port}/'])

    def test_managed_chat_keeps_launch_port_after_config_changes_or_removal(self):
        original = self.start()['port']
        self.source['splash']['port'] = free_port()
        self.source['splash']['models'][0]['name'] = '設定だけの名前'
        self.paths.config.write_text(json.dumps(self.source))
        for removed in (False, True):
            with self.subTest(config_removed=removed):
                if removed:
                    self.paths.config.unlink()
                output = self.app.render('/tmp/llm.5s.sh')
                self.assertIn(f'接続先: 127.0.0.1:{original}', output)
                self.assertIn('稼働モデル: 最初', output)
                with patch('subprocess.run') as opened:
                    self.app.open_target('chat', 'splash')
                self.assertEqual(opened.call_args.args[0][-1], f'http://127.0.0.1:{original}/')

    def test_unknown_ownership_chat_keeps_launch_port(self):
        original = self.start()['port']
        self.source['splash']['port'] = free_port()
        self.paths.config.write_text(json.dumps(self.source))
        with patch('scripts.llm.macos._libc.proc_pid_rusage', side_effect=PermissionError('fixture denial')):
            output = self.app.render('/tmp/llm.5s.sh')
            self.assertIn('Splash — 所有権不明', output)
            self.assertIn(f'接続先: 127.0.0.1:{original}', output)
            with patch('subprocess.run') as opened:
                self.app.open_target('chat', 'splash')
            self.assertEqual(opened.call_args.args[0][-1], f'http://127.0.0.1:{original}/')

    def test_stopped_chat_without_configuration_does_not_open_stale_endpoint(self):
        self.start()
        self.app.action('stop', 'splash')
        self.paths.config.unlink()
        with patch('subprocess.run') as opened:
            with self.assertRaisesRegex(ValueError, '未設定'):
                self.app.open_target('chat', 'splash')
            opened.assert_not_called()

    def test_model_name_is_not_running_after_stop(self):
        self.start()
        self.app.action('stop', 'splash')
        output = self.app.render('/tmp/llm.5s.sh')
        self.assertNotIn('稼働モデル: 最初', output)
        self.assertIn('Splash — 停止中\n--稼働モデル: —', output)
        self.assertEqual(self.app.selection('splash'), 'first')

    def test_external_model_name_replaces_stopped_generation(self):
        self.start()
        self.app.action('stop', 'splash')
        with external_splash() as port:
            self.source['splash']['port'] = port
            self.paths.config.write_text(json.dumps(self.source))
            output = self.app.render('/tmp/llm.5s.sh')
        self.assertIn('Splash — 外部で稼働中\n--稼働モデル: external-model', output)
        self.assertNotIn('稼働モデル: 最初', output)
        self.assertNotIn('param1=stop param2=splash', output)

    def test_confirmed_switch_changes_worker_generation(self):
        old = self.start()
        new = self.app.action('select', 'splash', 'second')
        self.assertNotEqual(old['nonce'], new['nonce'])
        self.assertEqual(self.app.selection('splash'), 'second')
        self.assertEqual(new['model_id'], 'second')
        self.assertEqual(len(self.questions), 1)


if __name__ == '__main__':
    unittest.main()
