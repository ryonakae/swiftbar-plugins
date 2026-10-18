import subprocess
from pathlib import Path


SCRIPT = Path(__file__).with_name('dialog.applescript')


def confirm(message):
    result = subprocess.run(['/usr/bin/osascript', str(SCRIPT), 'confirm', message],
                            capture_output=True, text=True, timeout=3600)
    return result.returncode == 0 and result.stdout.strip() == 'continue'


def notice(message):
    subprocess.run(['/usr/bin/osascript', str(SCRIPT), 'notice', message],
                   capture_output=True, text=True, timeout=3600)
