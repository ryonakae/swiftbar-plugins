from pathlib import Path
from subprocess import CompletedProcess
import unittest
from unittest.mock import patch

from scripts.llm import dialogs


class DialogTests(unittest.TestCase):
    def test_message_is_argument_to_fixed_script_and_cancel_is_not_approval(self):
        message = '" & do shell script "touch /tmp/not-executed"\n停止しますか？'
        with patch('subprocess.run', return_value=CompletedProcess([], 0, 'cancel\n', '')) as run:
            self.assertFalse(dialogs.confirm(message))
            argv = run.call_args.args[0]
            self.assertEqual(argv[0], '/usr/bin/osascript')
            self.assertEqual(argv[2:], ['confirm', message])
            self.assertNotIn(message, Path(argv[1]).read_text())
        with patch('subprocess.run', return_value=CompletedProcess([], 0, 'continue\n', '')):
            self.assertTrue(dialogs.confirm(message))
        with patch('subprocess.run', return_value=CompletedProcess([], 1, '', 'permission denied')):
            self.assertFalse(dialogs.confirm(message))


if __name__ == '__main__':
    unittest.main()
