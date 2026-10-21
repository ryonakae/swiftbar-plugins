"""Test ownership lifetime and shutdown without real engines or external traffic."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("sleep", "descendants", "child", "ignore", "escape", "isolated", "output", "exit"), default="sleep")
    parser.add_argument("--marker")
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--parent-seconds", type=float)
    options = parser.parse_args()
    marker = Path(options.marker) if options.marker else None
    if options.mode == "escape":
        os.setsid()
    if options.mode == "ignore":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    elif options.mode == "child":
        def stopped(number, frame):
            marker.write_text("stopped")
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, stopped)
    elif options.mode == "descendants":
        child = subprocess.Popen([sys.executable, __file__, "--mode", "child", "--marker", str(marker), "--seconds", str(options.seconds)])
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        print(f"descendant={child.pid}", flush=True)
    elif options.mode == "isolated":
        # Strata starts its engine and image reader in their own process groups and ends them itself.
        child = subprocess.Popen([sys.executable, __file__, "--mode", "child", "--marker", str(marker), "--seconds", str(options.seconds)],
                                 start_new_session=True)

        def forward(number, frame):
            child.terminate()
            child.wait()
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, forward)
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        print(f"descendant={child.pid}", flush=True)
    elif options.mode == "output":
        print("x" * (4 * 1024 * 1024), flush=True)
    elif options.mode == "exit":
        print("fixture startup diagnostic", flush=True)
        return 7
    if marker is not None and options.mode not in ("descendants", "isolated"):
        marker.write_text("ready")
    print(json.dumps({"cwd": os.getcwd(), "secret_received": os.environ.get("PROCESS_TEST_SECRET") == "test-secret-absent-from-state"}), flush=True)
    time.sleep(options.parent_seconds if options.parent_seconds is not None else options.seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
