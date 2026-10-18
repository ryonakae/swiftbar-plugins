"""専用セッションを保持し、保存済みPIDを停止権限にしない所有者。"""

import errno
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import subprocess
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "scripts.llm"

from . import macos
from .processes import _atomic, _lock, _port_free, _read, _snapshot


LOG_LIMIT = 1024 * 1024
LOG_BACKUPS = 2


class _Log:
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None
        self.rotate()

    def rotate(self):
        if self.handle is not None:
            self.handle.close()
        for index in range(LOG_BACKUPS, 0, -1):
            source = self.path if index == 1 else Path(f"{self.path}.{index - 1}")
            if source.exists():
                os.replace(source, Path(f"{self.path}.{index}"))
        self.handle = self.path.open("wb", buffering=0)
        self.size = 0

    def write(self, data):
        while data:
            if self.size >= LOG_LIMIT:
                self.rotate()
            count = min(len(data), LOG_LIMIT - self.size)
            self.handle.write(data[:count])
            self.size += count
            data = data[count:]

    def close(self):
        self.handle.close()


class _Worker:
    def __init__(self, payload):
        self.record = payload["record"]
        self.launch = payload["launch"]
        self.state_dir = Path(payload["state_dir"])
        self.engine = self.record["engine"]
        self.child = None
        self.child_identity = None
        self.log = None
        self.log_failed = False
        self.server = None
        self.selector = selectors.DefaultSelector()
        self.signalled = False
        self.stopping_at = None
        self.group_signalled = False
        self.direct_signalled = False
        self.direct_at = None
        self.terminal_state = "stopped"
        self.done = False
        self.known_descendants = {}
        self.persistence_failed = False
        self.retry_persist_at = 0
        self.error_before_persistence_failure = None

    def persist(self, initial=False):
        try:
            if initial:
                # 起動元がロックを保持している間だけ、workerが初期記録を書き込む。
                if _read(self.state_dir, self.engine).get("nonce") != self.record["nonce"]:
                    raise ValueError("Worker generation changed during handoff")
                _atomic(self.state_dir, self.engine, self.record)
            else:
                with _lock(self.state_dir, self.engine):
                    if _read(self.state_dir, self.engine).get("nonce") != self.record["nonce"]:
                        raise ValueError("Worker generation changed")
                    _atomic(self.state_dir, self.engine, self.record)
        except (OSError, ValueError) as exc:
            if not self.persistence_failed:
                self.error_before_persistence_failure = self.record.get("error")
            detail = f"Worker state cannot be saved: {exc}"
            if self.error_before_persistence_failure:
                detail = f"{self.error_before_persistence_failure}; {detail}"
            self.record.update(state="stop_failed", error=detail)
            self.persistence_failed = True
            self.stopping_at = None
            self.retry_persist_at = time.monotonic() + 0.5
            return False
        self.persistence_failed = False
        return True

    def signal(self, number, frame):
        self.signalled = True

    def own_group(self):
        pid = os.getpid()
        who = macos.identity(pid)
        if (who is None or who.start_time != self.record["worker_start"]
                or who.pgid != pid or os.getpgrp() != pid or os.getsid(0) != pid
                or who.uid != os.getuid()):
            raise OSError("Worker is not the verified session/group leader")
        members = macos.group_members(pid)
        if not any(member.pid == pid and member.start_time == who.start_time for member in members):
            raise OSError("Worker group inspection is incomplete")
        if any(member.pgid != pid or member.uid != os.getuid() for member in members):
            raise OSError("Worker group membership is uncertain")
        descendants = macos.descendants(pid)
        for member in members + descendants:
            if member.pid != pid:
                self.known_descendants[member.pid] = member.start_time
        alive = []
        for child_pid, start in list(self.known_descendants.items()):
            current = macos.identity(child_pid)
            if current is None or current.start_time != start:
                del self.known_descendants[child_pid]
            elif current.pgid != pid or current.uid != os.getuid():
                raise OSError("Known descendant remains outside the owned process group")
            else:
                alive.append(current)
        return alive

    def fail_stop(self, message):
        self.record.update(state="stop_failed", error=message)
        self.stopping_at = None
        self.persist()

    def begin_stop(self, *, unexpected=False):
        if self.stopping_at is not None:
            return
        self.terminal_state = "error" if unexpected else "stopped"
        self.stopping_at = time.monotonic()
        self.group_signalled = False
        self.direct_signalled = False
        self.direct_at = None
        self.record["state"] = "stopping"
        self.persist()
        try:
            self.own_group()
            self.signal_child()
        except OSError as exc:
            if exc.errno not in {errno.EAGAIN, errno.ESRCH}:
                self.fail_stop(f"Graceful stop cannot be verified: {exc}")

    def signal_child(self):
        if self.direct_signalled:
            return
        if self.child is not None and self.child.poll() is None:
            current = macos.identity(self.child.pid)
            expected = self.child_identity
            if (current is None or current.ppid != os.getpid()
                    or (expected is not None and current.start_time != expected.start_time)
                    or current.pgid != os.getpid() or current.uid != os.getuid()):
                raise OSError("Direct child identity cannot be verified")
            self.child.send_signal(signal.SIGTERM)
        self.direct_signalled = True
        self.direct_at = time.monotonic()

    def advance(self):
        exited = self.child.poll() if self.child is not None else 0
        if exited is not None and self.stopping_at is None and self.record["state"] == "running":
            self.record["error"] = f"Server exited unexpectedly (exit {exited})"
            self.begin_stop(unexpected=True)
        if self.record["state"] not in {"stopping", "stop_failed"}:
            return
        try:
            members = self.own_group()
            if not members:
                self.record["state"] = self.terminal_state
                if self.persist():
                    self.done = True
                return
            if self.stopping_at is None:
                return
            self.signal_child()
            elapsed = time.monotonic() - self.stopping_at
            if time.monotonic() - self.direct_at >= 2 and not self.group_signalled:
                self.own_group()
                os.killpg(os.getpgrp(), signal.SIGTERM)
                self.signalled = False
                self.group_signalled = True
            if elapsed >= 8:
                self.fail_stop("Graceful stop timed out; owned processes remain (no SIGKILL was sent)")
        except OSError as exc:
            if self.record["state"] != "stop_failed":
                if (exc.errno in {errno.EAGAIN, errno.ESRCH} and self.stopping_at is not None
                        and time.monotonic() - self.stopping_at < 8):
                    return
                self.fail_stop(f"Process group cannot be verified: {exc}")

    def request(self):
        connection, _ = self.server.accept()
        with connection:
            connection.settimeout(0.2)
            try:
                data = bytearray()
                while b"\n" not in data:
                    chunk = connection.recv(4096)
                    if not chunk or len(data) + len(chunk) > 16384:
                        return
                    data.extend(chunk)
                request = json.loads(data.split(b"\n", 1)[0])
                if not isinstance(request, dict) or request.get("nonce") != self.record["nonce"]:
                    return
                operation = request.get("operation")
                if operation not in {"status", "stop"}:
                    return
                if operation == "stop":
                    self.begin_stop()
                response = {"engine": self.engine, "nonce": self.record["nonce"],
                            "status": _snapshot(self.record, owned=True)}
                connection.sendall(json.dumps(response).encode() + b"\n")
            except (OSError, ValueError):
                return

    def acknowledge(self):
        try:
            os.write(1, json.dumps(_snapshot(self.record, owned=self.child is not None)).encode() + b"\n")
        except OSError:
            pass
        finally:
            os.close(1)

    def run(self):
        signal.signal(signal.SIGTERM, self.signal)
        signal.signal(signal.SIGINT, self.signal)
        signal.signal(signal.SIGHUP, self.signal)
        try:
            who = macos.identity(os.getpid())
            if who is None or who.pgid != os.getpid() or os.getsid(0) != os.getpid():
                raise OSError("Worker must own a new session")
            self.record.update(worker_pid=who.pid, worker_start=who.start_time, boot_id=macos.boot_id())
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server.bind(self.record["socket"])
            self.server.listen(16)
            self.selector.register(self.server, selectors.EVENT_READ, "request")
            self.log = _Log(self.record["log"])
            _port_free(self.record["port"])
            self.child = subprocess.Popen(
                self.launch["command"], cwd=self.launch["cwd"], env=self.launch["env"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            self.record["root_pid"] = self.child.pid
            self.child_identity = macos.identity(self.child.pid)
            self.record["state"] = "running"
            self.persist(initial=True)
        except (OSError, ValueError, KeyError) as exc:
            self.record.update(state="stop_failed" if self.child is not None else "error",
                               error=f"Server launch failed: {exc}")
            self.persist(initial=True)
        finally:
            self.launch.clear()
            self.acknowledge()
        if self.child is None:
            return
        os.set_blocking(self.child.stdout.fileno(), False)
        self.selector.register(self.child.stdout, selectors.EVENT_READ, "log")
        while not self.done:
            if self.persistence_failed and time.monotonic() >= self.retry_persist_at:
                self.persist()
            if self.signalled:
                self.signalled = False
                self.begin_stop()
            for key, _ in self.selector.select(0.05):
                if key.data == "request":
                    self.request()
                else:
                    data = os.read(self.child.stdout.fileno(), 65536)
                    if data and not self.log_failed:
                        try:
                            self.log.write(data)
                        except OSError as exc:
                            self.log_failed = True
                            self.terminal_state = "error"
                            self.record.update(state="stop_failed", error=f"Server log cannot be written: {exc}")
                            self.persist()
                    elif not data:
                        self.selector.unregister(self.child.stdout)
                        self.child.stdout.close()
            self.advance()

    def close(self):
        self.selector.close()
        if self.server is not None:
            self.server.close()
        if self.log is not None:
            self.log.close()
        endpoint = Path(self.record["socket"])
        try:
            endpoint.unlink(missing_ok=True)
            endpoint.parent.rmdir()
        except OSError:
            pass


def main():
    payload = json.loads(sys.stdin.buffer.read(1024 * 1024 + 1))
    worker = _Worker(payload)
    try:
        worker.run()
    finally:
        worker.close()


if __name__ == "__main__":
    main()
