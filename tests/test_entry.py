import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class EntryTests(unittest.TestCase):
    def test_shell_entry_is_independent_of_cwd_and_does_not_start_models(self):
        entry = Path(__file__).resolve().parents[1] / 'plugins/llm.5s.sh'
        with tempfile.TemporaryDirectory(prefix='llm 日本語 ') as tmp:
            home = Path(tmp)
            config = home / '.config/swiftbar-plugins/llm.json'
            config.parent.mkdir(parents=True)
            config.write_text('{}')
            before = config.read_bytes()
            result = subprocess.run([str(entry)], cwd=tmp,
                                    env={**os.environ, 'HOME': tmp, 'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'},
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count('sfimage='), 1)
            self.assertIn('Strata — 未設定', result.stdout)
            self.assertIn('Splash — 未設定', result.stdout)
            self.assertNotIn('param1=start', result.stdout)
            self.assertEqual(config.read_bytes(), before)
            self.assertFalse(list((home / 'Library/Application Support/swiftbar-plugins/llm').glob('*.json')))


if __name__ == '__main__':
    unittest.main()
