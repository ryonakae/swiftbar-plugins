"""Keep ownership tied to a launch generation through a dedicated worker."""

import contextlib
import fcntl
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import time
import uuid

from . import macos


ENGINES = {"strata", "splash"}
ACTIVE = {"starting", "running", "stopping", "stop_failed", "unknown"}
FIELDS = ("nonce", "port", "model_id", "model_name", "root_pid", "engine_path", "error", "log")
_WORKERS = []


def _engine(engine):
    if engine not in ENGINES:
        raise ValueError("Unknown engine")
    return engine


def _snapshot(record=None, *, state=None, owned=False, error=None):
    record = record or {}
    result = {"state": state or record.get("state", "stopped"), "owned": owned}
    result.update({key: record.get(key) for key in FIELDS})
    if error is not None:
        result["error"] = error
    return result


@contextlib.contextmanager
def _lock(state_dir, engine):
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / f"{_engine(engine)}.lock").open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _read(state_dir, engine):
    try:
        with (state_dir / f"{_engine(engine)}.json").open() as handle:
            record = json.load(handle)
        if not isinstance(record, dict) or record.get("state") not in ACTIVE | {"error", "stopped"}:
            raise ValueError("Invalid state record")
        return record
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        return {"state": "unknown", "error": f"State cannot be read: {exc}"}


def _atomic(state_dir, engine, record):
    fd, name = tempfile.mkstemp(prefix=f".{engine}-", dir=state_dir)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(record, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, state_dir / f"{engine}.json")
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # TIME_WAIT after shutdown does not mean a server still owns the port.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))


def _rpc(record, operation, timeout=1.0):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(record["socket"])
        client.sendall(json.dumps({"operation": operation, "nonce": record["nonce"]}).encode() + b"\n")
        data = bytearray()
        while b"\n" not in data:
            chunk = client.recv(8192)
            if not chunk or len(data) + len(chunk) > 65536:
                raise ValueError("Incomplete worker response")
            data.extend(chunk)
        response = json.loads(data.split(b"\n", 1)[0])
        if response.get("nonce") != record["nonce"] or response.get("engine") != record["engine"]:
            raise ValueError("Worker generation mismatch")
        return response["status"]


def _verified(record):
    if record.get("boot_id") != macos.boot_id():
        return False
    pid = record.get("worker_pid")
    if type(pid) is not int or pid <= 0:
        return False
    who = macos.identity(pid)
    return (who is not None and who.start_time == record.get("worker_start")
            and who.uid == os.getuid() and who.pgid == pid)


def _launch_valid(launch):
    if not isinstance(launch, dict):
        raise ValueError("Launch must be an object")
    command = launch.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x or "\0" in x for x in command):
        raise ValueError("Launch command must be a nonempty string array")
    cwd, env = launch.get("cwd"), launch.get("env")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or "\0" in cwd:
        raise ValueError("Launch cwd must be absolute")
    if not isinstance(env, dict) or any(not isinstance(k, str) or not k or "=" in k or "\0" in k or not isinstance(v, str) or "\0" in v for k, v in env.items()):
        raise ValueError("Invalid launch environment")
    port = launch.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid launch port")
    for key in ("model_id", "model_name"):
        if not isinstance(launch.get(key), str) or not launch[key] or "\0" in launch[key]:
            raise ValueError("Invalid launch model")
    if launch.get("engine_path") is not None and not isinstance(launch["engine_path"], str):
        raise ValueError("Invalid engine path")


class Manager:
    def __init__(self, state_dir: Path, log_dir: Path):
        self.state_dir = Path(state_dir).resolve()
        self.log_dir = Path(log_dir).resolve()

    def start(self, engine: str, launch: dict) -> dict:
        result = self._start_once(engine, launch)
        if result is not None:
            return result
        result = self.status(engine)
        result["error"] = result["error"] or "Start refused: previous ownership must be resolved before starting"
        return result

    def _start_once(self, engine, launch):
        with _lock(self.state_dir, engine):
            record = _read(self.state_dir, engine)
            if record.get("state") in ACTIVE:
                return None
            _launch_valid(launch)
            try:
                _port_free(launch["port"])
            except OSError as exc:
                record = {"state": "error", "port": launch["port"], "error": f"Requested port is unavailable: {exc}"}
                _atomic(self.state_dir, engine, record)
                return _snapshot(record)
            self.log_dir.mkdir(parents=True, exist_ok=True)
            private_dir = tempfile.mkdtemp(prefix="llm-", dir="/tmp")
            endpoint = str(Path(private_dir) / "control")
            if len(os.fsencode(endpoint)) >= 104:
                os.rmdir(private_dir)
                raise ValueError("Worker socket path is too long")
            record = {
                "engine": engine, "state": "starting", "nonce": uuid.uuid4().hex,
                "port": launch["port"], "model_id": launch["model_id"],
                "model_name": launch["model_name"], "engine_path": launch.get("engine_path"),
                "root_pid": None, "error": None, "socket": endpoint,
                "log": str(self.log_dir / f"{engine}.log"),
            }
            _atomic(self.state_dir, engine, record)
            worker = None
            try:
                worker = subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name("worker.py"))],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    start_new_session=True, cwd=str(Path(__file__).resolve().parents[1]),
                )
                _WORKERS.append(worker)
                worker.stdin.write(json.dumps({"state_dir": str(self.state_dir), "record": record, "launch": launch}).encode())
                worker.stdin.close()
                if select.select([worker.stdout], [], [], 3.0)[0]:
                    reply = worker.stdout.readline(65536)
                    if reply:
                        result = json.loads(reply)
                        if result.get("nonce") == record["nonce"]:
                            return result
                return _snapshot(record, state="unknown", error="Worker launch handoff could not be verified")
            except (OSError, ValueError) as exc:
                if worker is not None:
                    return _snapshot(record, state="unknown", error=f"Worker launch handoff failed: {exc}")
                record.update(state="error", error=f"Worker could not start: {exc}")
                _atomic(self.state_dir, engine, record)
                os.rmdir(private_dir)
                return _snapshot(record)
            finally:
                if worker is not None:
                    if not worker.stdin.closed:
                        worker.stdin.close()
                    worker.stdout.close()

    def status(self, engine: str) -> dict:
        _WORKERS[:] = [worker for worker in _WORKERS if worker.poll() is None]
        with _lock(self.state_dir, engine):
            record = _read(self.state_dir, engine)
        if not record or record.get("state") in {"stopped", "error"}:
            return _snapshot(record)
        try:
            if _verified(record):
                return _rpc(record, "status")
            reason = "Worker identity is missing or changed; ownership is unknown"
        except (OSError, ValueError, KeyError, TypeError) as exc:
            reason = f"Worker ownership could not be verified: {exc}"
        return _snapshot(record, state="unknown", error=record.get("error") or reason)

    def stop(self, engine: str, expected_nonce: str) -> dict:
        with _lock(self.state_dir, engine):
            record = _read(self.state_dir, engine)
        if not expected_nonce or record.get("nonce") != expected_nonce:
            return _snapshot(record, state="error", error="Stop refused: worker generation changed")
        state = self.status(engine)
        if not state["owned"] or state["nonce"] != expected_nonce:
            return _snapshot(record, state=state["state"], error="Stop refused: ownership cannot be verified")
        try:
            result = _rpc(record, "stop")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return _snapshot(record, state="unknown", error=f"Stop request could not be verified: {exc}")
        deadline = time.monotonic() + 10
        while result["state"] in {"starting", "running", "stopping"} and time.monotonic() < deadline:
            time.sleep(0.05)
            result = self.status(engine)
            if result["nonce"] != expected_nonce:
                return _snapshot(record, state="unknown", error="Worker generation changed while waiting for stop")
        return result
