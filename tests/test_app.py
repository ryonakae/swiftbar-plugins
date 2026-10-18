import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest

from scripts.llm.app import Controller, Paths


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


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

    def test_confirmed_switch_changes_worker_generation(self):
        old = self.start()
        new = self.app.action('select', 'splash', 'second')
        self.assertNotEqual(old['nonce'], new['nonce'])
        self.assertEqual(self.app.selection('splash'), 'second')
        self.assertEqual(new['model_id'], 'second')
        self.assertEqual(len(self.questions), 1)


if __name__ == '__main__':
    unittest.main()
