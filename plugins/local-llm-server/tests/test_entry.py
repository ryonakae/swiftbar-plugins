import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest


class EntryTests(unittest.TestCase):
    def test_direct_and_symlink_entries_resolve_plugin_config_independently_of_cwd(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='llm 日本語 ') as tmp:
            home = Path(tmp)
            repo = home / 'repo 日本語'
            root = repo / 'plugins/local-llm-server'
            shutil.copytree(source / 'scripts', root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
            direct = root / 'local-llm-server.5s.sh'
            shutil.copy2(source / direct.name, direct)
            config = root / 'config/local-llm-server.json'
            config.parent.mkdir(parents=True)
            config.write_text('{}')
            before = config.read_bytes()
            enabled = repo / 'enabled'
            enabled.mkdir()
            linked = enabled / direct.name
            linked.symlink_to('../plugins/local-llm-server/local-llm-server.5s.sh')
            chained = home / 'linked.sh'
            chained.symlink_to(linked)
            for entry in (direct, linked, chained):
                with self.subTest(entry=entry):
                    result = subprocess.run([str(entry)], cwd=home,
                                            env={**os.environ, 'HOME': tmp, 'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'},
                                            capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.count('sfimage='), 1)
                    self.assertNotIn('Cannot read config', result.stdout)
                    self.assertIn('Strata — Not configured', result.stdout)
                    self.assertIn('Splash — Not configured', result.stdout)
                    self.assertNotIn('param1=start', result.stdout)
                    self.assertIn(str(direct.resolve()), result.stdout)
                    self.assertEqual(config.read_bytes(), before)
                    self.assertFalse(list((home / 'Library/Application Support/swiftbar-plugins/llm').glob('*.json')))


if __name__ == '__main__':
    unittest.main()
