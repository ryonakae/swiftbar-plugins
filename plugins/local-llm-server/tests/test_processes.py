from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

macos = import_module('scripts.macos')
from pathlib import Path

Manager = import_module('scripts.processes').Manager


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.manager = Manager(self.base / "state", self.base / "logs")
        self.addCleanup(self.stop_owned)

    def stop_owned(self):
        for engine in ("strata", "splash"):
            state = self.manager.status(engine)
            if state["owned"]:
                self.manager.stop(engine, state["nonce"])

    def wait_state(self, engine, wanted, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.manager.status(engine)
            if state["state"] in wanted:
                return state
            time.sleep(0.05)
        self.fail(f"Expected {wanted}, got {state}")

    def free_port(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def test_launch_handoff_preserves_owned_server_then_stops(self):
        started_at = time.monotonic()
        result = self.manager.start("strata", self.launch(self.free_port()))
        self.assertLess(time.monotonic() - started_at, 3)
        self.assertIn(result["state"], {"starting", "running"})
        state = self.wait_state("strata", {"running"})
        self.assertTrue(state["owned"])
        self.assertEqual(state["model_name"], "テスト")
        self.assertIsNotNone(state["root_pid"])
        recovered = Manager(self.base / "state", self.base / "logs")
        self.assertEqual(recovered.status("strata")["nonce"], state["nonce"])
        stopped = recovered.stop("strata", state["nonce"])
        self.assertEqual(stopped["state"], "stopped")
        self.assertFalse(stopped["owned"])

    def test_second_start_keeps_current_ownership_and_original_launch(self):
        self.manager.start("strata", self.launch(self.free_port()))
        current = self.wait_state("strata", {"running"})
        requested = self.launch(self.free_port())
        requested["model_id"] = "other"
        refused = self.manager.start("strata", requested)
        self.assertTrue(refused["owned"])
        self.assertEqual(refused["nonce"], current["nonce"])
        self.assertEqual(refused["model_id"], "test-model")
        self.assertIsNotNone(refused["error"])
        self.assertEqual(self.manager.status("strata")["root_pid"], current["root_pid"])

    def fixture_launch(self, mode="sleep", seconds=20, marker=None):
        launch = self.launch(self.free_port())
        launch["command"] = [sys.executable, str(Path(__file__).with_name("fixtures") / "fake_server.py"),
                             "--mode", mode, "--seconds", str(seconds)]
        if marker is not None:
            launch["command"] += ["--marker", str(marker)]
        return launch

    def wait_file(self, path, text=None, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists() and (text is None or text in path.read_text()):
                return
            time.sleep(0.02)
        self.fail(f"Missing expected fixture output at {path}")

    def test_wrong_nonce_never_stops_current_generation(self):
        state = self.manager.start("strata", self.launch(self.free_port()))
        refused = self.manager.stop("strata", "old-generation")
        self.assertIn("generation", refused["error"])
        current = self.manager.status("strata")
        self.assertEqual(current["nonce"], state["nonce"])
        self.assertEqual(current["state"], "running")

    def test_concurrent_starts_only_create_one_generation(self):
        launch = self.launch(self.free_port())
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.manager.start("strata", launch), range(6)))
        self.assertEqual(len({item["nonce"] for item in results}), 1)
        self.assertEqual(sum(item["error"] is None for item in results), 1)
        state = self.manager.status("strata")
        self.assertTrue(state["owned"])
        self.assertEqual(state["state"], "running")

    def test_engines_have_independent_generations(self):
        first = self.manager.start("strata", self.launch(self.free_port()))
        second = self.manager.start("splash", self.launch(self.free_port()))
        self.assertNotEqual(first["nonce"], second["nonce"])
        self.assertEqual(self.manager.stop("strata", first["nonce"])["state"], "stopped")
        self.assertEqual(self.manager.status("splash")["state"], "running")

    def test_launch_failure_keeps_error_across_refreshes(self):
        launch = self.launch(self.free_port())
        launch["command"] = [str(self.base / "nonexistent")]
        state = self.manager.start("strata", launch)
        self.assertEqual(state["state"], "error")
        self.assertIn("Server launch failed", state["error"])
        self.assertFalse(state["owned"])
        self.assertEqual(self.manager.status("strata")["error"], state["error"])

    def test_worker_survives_starting_parent_exit(self):
        code = (
            "import json; from pathlib import Path; from importlib import import_module; "
            "Manager = import_module('scripts.processes').Manager; "
            f"m=Manager(Path({str(self.base / 'state')!r}),Path({str(self.base / 'logs')!r})); "
            f"print(json.dumps(m.start('strata', {self.launch(self.free_port())!r})))"
        )
        parent = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
        self.assertEqual(parent.returncode, 0, parent.stderr)
        launched = json.loads(parent.stdout)
        state = self.manager.status("strata")
        self.assertTrue(state["owned"])
        self.assertEqual(state["nonce"], launched["nonce"])
        self.assertEqual(self.manager.stop("strata", state["nonce"])["state"], "stopped")

    def test_cwd_environment_and_engine_snapshot_without_environment_persistence(self):
        launch = self.fixture_launch()
        launch["env"] = {"PROCESS_TEST_SECRET": "test-secret-absent-from-state"}
        launch["engine_path"] = "/fixture/native-engine"
        state = self.manager.start("strata", launch)
        self.assertEqual(state["engine_path"], "/fixture/native-engine")
        log = Path(state["log"])
        self.wait_file(log, '"secret_received": true')
        self.assertIn(str(self.base), log.read_text())
        for path in (self.base / "state").iterdir():
            self.assertNotIn(b"test-secret-absent-from-state", path.read_bytes())
        self.assertNotIn("test-secret-absent-from-state", log.read_text())

    def test_identity_denial_blocks_stop_and_restart_without_signalling(self):
        state = self.manager.start("strata", self.launch(self.free_port()))
        with patch.object(macos, "identity", side_effect=PermissionError("fixture denial")):
            unknown = self.manager.status("strata")
            self.assertEqual(unknown["state"], "unknown")
            self.assertFalse(unknown["owned"])
            self.assertIn("denial", unknown["error"])
            self.assertEqual(self.manager.stop("strata", state["nonce"])["state"], "unknown")
            self.assertEqual(self.manager.start("strata", self.launch(self.free_port()))["nonce"], state["nonce"])
        self.assertEqual(self.manager.status("strata")["state"], "running")

    def test_orphan_record_never_adopts_pid_or_allows_start(self):
        record = {"state": "running", "engine": "strata", "nonce": "orphan",
                  "worker_pid": os.getpid(), "worker_start": 1, "boot_id": macos.boot_id(),
                  "root_pid": os.getpid(), "port": self.free_port()}
        state_dir = self.base / "state"
        state_dir.mkdir()
        (state_dir / "strata.json").write_text(json.dumps(record))
        self.assertEqual(self.manager.status("strata")["state"], "unknown")
        self.assertEqual(self.manager.stop("strata", "orphan")["state"], "unknown")
        refused = self.manager.start("strata", self.launch(self.free_port()))
        self.assertEqual(refused["nonce"], "orphan")
        self.assertFalse(refused["owned"])

    def test_stop_keeps_worker_when_direct_child_leaves_group(self):
        marker = self.base / "escaped-ready"
        state = self.manager.start("strata", self.fixture_launch("escape", seconds=2, marker=marker))
        self.wait_file(marker)
        stopped = self.manager.stop("strata", state["nonce"])
        self.assertEqual(stopped["state"], "stop_failed")
        self.assertTrue(stopped["owned"])
        self.assertEqual(self.manager.start("strata", self.launch(self.free_port()))["nonce"], state["nonce"])
        self.wait_state("strata", {"stopped"}, timeout=4)

    def test_stop_cleans_remaining_same_group_descendant_before_worker_exits(self):
        marker = self.base / "descendant"
        state = self.manager.start("strata", self.fixture_launch("descendants", marker=marker))
        self.wait_file(Path(state["log"]), "descendant=")
        stopped = self.manager.stop("strata", state["nonce"])
        self.assertEqual(stopped["state"], "stopped", stopped)
        self.assertEqual(marker.read_text(), "stopped")

    def test_term_ignoring_child_keeps_owned_worker_and_blocks_restart(self):
        marker = self.base / "ignore-ready"
        state = self.manager.start("strata", self.fixture_launch("ignore", seconds=10, marker=marker))
        self.wait_file(marker)
        started = time.monotonic()
        failed = self.manager.stop("strata", state["nonce"])
        self.assertEqual(failed["state"], "stop_failed", failed)
        self.assertTrue(failed["owned"])
        self.assertIn("no SIGKILL", failed["error"])
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(self.manager.start("strata", self.launch(self.free_port()))["nonce"], state["nonce"])
        self.wait_state("strata", {"stopped"}, timeout=4)

    def test_output_and_startup_rotation_are_bounded(self):
        marker = self.base / "output-done"
        state = self.manager.start("strata", self.fixture_launch("output", marker=marker))
        self.wait_file(marker)
        self.wait_file(Path(state["log"]), '"cwd"')
        files = list((self.base / "logs").iterdir())
        self.assertLessEqual(len(files), 3)
        self.assertTrue(all(path.stat().st_size <= 1024 * 1024 for path in files))
        self.assertEqual(self.manager.stop("strata", state["nonce"])["state"], "stopped")
        for _ in range(3):
            state = self.manager.start("strata", self.fixture_launch())
            self.wait_file(Path(state["log"]), '"cwd"')
            self.assertEqual(self.manager.stop("strata", state["nonce"])["state"], "stopped")
        self.assertLessEqual(len(list((self.base / "logs").iterdir())), 3)

    def test_unexpected_exit_preserves_log_and_error(self):
        self.manager.start("strata", self.fixture_launch("exit"))
        state = self.wait_state("strata", {"error"})
        self.assertFalse(state["owned"])
        self.assertIn("exit 7", state["error"])
        self.assertIn("fixture startup diagnostic", Path(state["log"]).read_text())
        self.assertEqual(self.manager.status("strata")["error"], state["error"])

    def test_status_and_repeated_stop_are_safe_under_concurrency(self):
        marker = self.base / "descendant-concurrent"
        state = self.manager.start("strata", self.fixture_launch("descendants", marker=marker))
        self.wait_file(Path(state["log"]), "descendant=")
        with ThreadPoolExecutor(max_workers=6) as pool:
            stopping = [pool.submit(self.manager.stop, "strata", state["nonce"]) for _ in range(3)]
            reading = [pool.submit(self.manager.status, "strata") for _ in range(12)]
            results = [future.result(timeout=12) for future in stopping + reading]
        self.assertTrue(all(item["nonce"] == state["nonce"] for item in results))
        self.assertEqual(self.manager.status("strata")["state"], "stopped")
        self.assertEqual(marker.read_text(), "stopped")

    def test_boot_mismatch_and_invalid_state_are_unknown_not_stopped(self):
        state = self.manager.start("strata", self.launch(self.free_port()))
        with patch.object(macos, "boot_id", return_value="different-boot"):
            self.assertEqual(self.manager.status("strata")["state"], "unknown")
            self.assertEqual(self.manager.stop("strata", state["nonce"])["state"], "unknown")
        self.assertEqual(self.manager.stop("strata", state["nonce"])["state"], "stopped")
        (self.base / "state" / "strata.json").write_text("{broken")
        unknown = self.manager.status("strata")
        self.assertEqual(unknown["state"], "unknown")
        self.assertFalse(unknown["owned"])
        self.assertEqual(self.manager.start("strata", self.launch(self.free_port()))["state"], "unknown")

    def test_launch_validation_never_spawns_for_invalid_port_or_command(self):
        for key, value in (("port", True), ("port", 0), ("command", "echo unsafe"), ("env", {"BAD=KEY": "x"}), ("cwd", "relative")):
            with self.subTest(key=key, value=value):
                launch = self.launch(self.free_port())
                launch[key] = value
                with self.assertRaises(ValueError):
                    self.manager.start("strata", launch)
                self.assertEqual(self.manager.status("strata")["state"], "stopped")

    def test_log_rotation_failure_does_not_abandon_owned_server(self):
        logs = self.base / "logs"
        (logs / "strata.log.2").mkdir(parents=True)
        (logs / "strata.log.2" / "blocker").write_text("rotation boundary failure")
        state = self.manager.start("strata", self.fixture_launch("output"))
        failed = self.wait_state("strata", {"stop_failed", "unknown"})
        self.assertEqual(failed["state"], "stop_failed")
        self.assertTrue(failed["owned"])
        self.assertIn("log", failed["error"].lower())
        stopped = self.manager.stop("strata", state["nonce"])
        self.assertEqual(stopped["state"], "stopped", stopped)

    def test_state_write_failure_does_not_abandon_remaining_descendant(self):
        marker = self.base / "persistence-child"
        launch = self.fixture_launch("descendants", marker=marker)
        launch["command"] += ["--parent-seconds", "0.3"]
        state = self.manager.start("strata", launch)
        self.wait_file(Path(state["log"]), "descendant=")
        record = self.base / "state" / "strata.json"
        saved = record.read_text()
        record.unlink()
        record.mkdir()
        try:
            time.sleep(0.7)
            self.assertEqual(self.manager.status("strata")["state"], "unknown")
        finally:
            record.rmdir()
            record.write_text(saved)
        owned = self.wait_state("strata", {"stop_failed", "running"})
        self.assertTrue(owned["owned"])
        stopped = self.manager.stop("strata", state["nonce"])
        self.assertEqual(stopped["state"], "stopped", stopped)
        self.assertEqual(marker.read_text(), "stopped")

    def launch(self, port):
        return {
            "command": [sys.executable, "-c", "import time; time.sleep(20)"],
            "cwd": str(self.base), "env": {}, "model_id": "test-model",
            "model_name": "テスト", "port": port, "engine_path": None,
        }

    def test_restart_can_use_port_after_server_closed_connection(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port)) as client:
                server, _ = listener.accept()
                server.close()
                self.assertEqual(client.recv(1), b"")
        state = self.manager.start("strata", self.launch(port))
        self.assertEqual(state["state"], "running", state)
        self.assertTrue(state["owned"])
        self.assertEqual(self.manager.stop("strata", state["nonce"])["state"], "stopped")

    def test_busy_port_refuses_launch_and_keeps_diagnostic(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            result = self.manager.start("strata", self.launch(listener.getsockname()[1]))
        self.assertEqual(result["state"], "error")
        self.assertFalse(result["owned"])
        self.assertIn("port", result["error"].lower())
        self.assertEqual(self.manager.status("strata")["error"], result["error"])

    def test_new_engine_is_stopped_without_ownership(self):
        state = self.manager.status("strata")
        self.assertEqual(state, {
            "state": "stopped", "owned": False, "nonce": None,
            "port": None, "model_id": None, "model_name": None,
            "root_pid": None, "engine_path": None, "error": None, "log": None,
        })
        with self.assertRaises(ValueError):
            self.manager.status("../other")


if __name__ == "__main__":
    unittest.main()
