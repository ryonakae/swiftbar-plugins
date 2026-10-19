from importlib import import_module
import ctypes
import errno
import os
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch

macos = import_module('scripts.macos')


class FakeLibc:
    def __init__(self):
        self.processes = {42: (1, 42, 501, 12345, '/bin/engine')}
        self.error = None
        self.short = False
        self.starts = []
        self.lists = {}
        self.list_error = None
        self.reuse_root = False
        self.boot_short = False
        self.bsd_error = None
        self.path_error = None
        self.nfiles = 0
        self.change_files = False

    def sysctlbyname(self, name, buffer, size, new, newsize):
        if self.error:
            ctypes.set_errno(self.error)
            return -1
        ctypes.memmove(buffer, struct.pack('@qi4x', 1800000000, 123456), 16)
        ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t))[0] = 8 if self.boot_short else 16
        return 0

    def proc_pid_rusage(self, pid, flavor, buffer):
        if self.error or pid not in self.processes:
            ctypes.set_errno(self.error or errno.ESRCH)
            return -1
        start = self.starts.pop(0) if self.starts else self.processes[pid][3]
        raw = bytearray(96)
        struct.pack_into('=QQQ', raw, 64, 999999, 456789, start)
        ctypes.memmove(buffer, bytes(raw), 96)
        return 0

    def proc_pidinfo(self, pid, flavor, arg, buffer, size):
        if self.bsd_error is not None:
            ctypes.set_errno(self.bsd_error)
            return 0
        if self.error or pid not in self.processes:
            ctypes.set_errno(self.error or errno.ESRCH)
            return 0
        ppid, pgid, uid, start, path = self.processes[pid]
        raw = bytearray(136)
        struct.pack_into('=III', raw, 12, pid, ppid, uid)
        struct.pack_into('=II', raw, 96, self.nfiles, pgid)
        if self.change_files:
            self.nfiles += 1
        ctypes.memmove(buffer, bytes(raw), 136)
        return 135 if self.short else 136

    def _list(self, kind, value, buffer, size):
        if self.list_error:
            ctypes.set_errno(self.list_error)
            return 0
        pids = self.lists.get((kind, value), [])
        if self.reuse_root:
            self.processes[42] = (1, 42, 501, 88888, '/bin/engine')
        if buffer is not None:
            pids = pids[:size // 4]
            if pids:
                ctypes.memmove(buffer, struct.pack('=' + 'i' * len(pids), *pids), len(pids) * 4)
        return len(pids)

    def proc_listpgrppids(self, pgid, buffer, size):
        return self._list('group', pgid, buffer, size)

    def proc_listchildpids(self, pid, buffer, size):
        return self._list('children', pid, buffer, size)

    def proc_pidpath(self, pid, buffer, size):
        if self.path_error is not None:
            ctypes.set_errno(self.path_error)
            return 0
        path = self.processes[pid][4].encode()
        ctypes.memmove(buffer, path + b'\0', len(path) + 1)
        return len(path)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.libc = FakeLibc()
        self.boundary = patch.object(macos, '_libc', self.libc)
        self.boundary.start()
        self.addCleanup(self.boundary.stop)

    def test_identity_uses_start_abstime_and_verified_fields(self):
        self.assertEqual(macos.identity(42), macos.ProcessIdentity(42, 1, 42, 501, 12345, '/bin/engine'))

    def test_only_esrch_proves_missing(self):
        self.assertIsNone(macos.identity(99))
        for error in (errno.EPERM, errno.EACCES, errno.EIO):
            with self.subTest(error=error):
                self.libc.error = error
                with self.assertRaises(OSError) as caught:
                    macos.identity(42)
                self.assertEqual(caught.exception.errno, error)

    def test_footprint_is_physical_not_rss_and_rejects_reuse(self):
        self.assertEqual(macos.footprint(42, 12345), 456789)
        self.assertIsNone(macos.footprint(42, 777))
        self.libc.starts = [12345, 99999]
        self.assertIsNone(macos.footprint(42))
        self.libc.error = errno.EPERM
        self.assertIsNone(macos.footprint(42))
        self.assertIsNone(macos.footprint(99))

    def test_group_members_distinguishes_empty_denied_and_missing(self):
        self.libc.lists[('group', 42)] = [42, 99]
        self.assertEqual([p.pid for p in macos.group_members(42)], [42])
        self.assertEqual(macos.group_members(100), [])
        self.libc.list_error = errno.EPERM
        with self.assertRaises(PermissionError):
            macos.group_members(42)

    def test_group_list_expands_instead_of_truncating(self):
        for pid in range(100, 230):
            self.libc.processes[pid] = (42, 42, 501, pid * 1000, '/bin/helper')
        self.libc.lists[('group', 42)] = list(range(100, 230))
        self.assertEqual(len(macos.group_members(42)), 130)

    def test_identity_ignores_changing_nonidentity_fields(self):
        self.libc.change_files = True
        self.assertEqual(macos.identity(42).start_time, 12345)

    def test_identity_denied_and_unknown_at_each_boundary_are_not_missing(self):
        for field in ('bsd_error', 'path_error'):
            for code in (0, errno.EPERM, errno.EACCES, errno.EIO):
                with self.subTest(field=field, code=code):
                    setattr(self.libc, field, code)
                    with self.assertRaises(OSError) as caught:
                        macos.identity(42)
                    self.assertEqual(caught.exception.errno, code or errno.EIO)
            setattr(self.libc, field, errno.ESRCH)
            self.assertIsNone(macos.identity(42))
            setattr(self.libc, field, None)

    def test_group_members_rejects_wrong_group(self):
        self.libc.lists[('group', 100)] = [42]
        with self.assertRaises(OSError):
            macos.group_members(100)

    def test_descendants_excludes_root_and_checks_ancestry(self):
        self.libc.processes[43] = (42, 42, 501, 13000, '/bin/native')
        self.libc.processes[44] = (43, 42, 501, 14000, '/bin/helper')
        self.libc.lists[('children', 42)] = [43]
        self.libc.lists[('children', 43)] = [44]
        self.assertEqual([p.pid for p in macos.descendants(42)], [43, 44])
        self.libc.processes[44] = (1, 42, 501, 14000, '/bin/helper')
        with self.assertRaises(OSError):
            macos.descendants(42)

    def test_descendants_rejects_missing_or_changed_root(self):
        with self.assertRaises(OSError):
            macos.descendants(99)
        self.libc.reuse_root = True
        with self.assertRaises(OSError):
            macos.descendants(42)

    def test_boot_id_is_stable_and_rejects_incomplete_sysctl(self):
        self.assertEqual(macos.boot_id(), '1800000000.123456')
        self.assertEqual(macos.boot_id(), macos.boot_id())
        self.libc.boot_short = True
        with self.assertRaises(OSError):
            macos.boot_id()
        self.libc.boot_short = False
        self.libc.error = errno.EPERM
        with self.assertRaises(PermissionError):
            macos.boot_id()

    def test_match_executable_preserves_ambiguity_for_caller(self):
        one = macos.identity(42)
        two = macos.ProcessIdentity(43, 42, 42, 501, 12400, '/bin/engine')
        other = macos.ProcessIdentity(44, 42, 42, 501, 12500, '/bin/python')
        self.assertEqual(macos.match_executable([one, two, other], '/bin/engine'), [one, two])
        self.assertEqual(macos.match_executable([one], '/bin/missing'), [])

    def test_unsupported_platform_never_emulates_process_queries(self):
        with patch.object(macos, '_libc', None):
            for operation in (lambda: macos.identity(42), lambda: macos.footprint(42),
                              lambda: macos.group_members(42), lambda: macos.descendants(42),
                              macos.boot_id):
                with self.assertRaises(OSError) as caught:
                    operation()
                self.assertEqual(caught.exception.errno, errno.ENOTSUP)

    def test_short_bsd_and_reused_pid_raise(self):
        self.libc.short = True
        with self.assertRaises(OSError):
            macos.identity(42)
        self.libc.short = False
        self.libc.starts = [12345, 99999]
        with self.assertRaises(OSError):
            macos.identity(42)


@unittest.skipUnless(sys.platform == 'darwin', 'requires the real macOS process API')
class RealMacOSTests(unittest.TestCase):
    def test_harmless_child_identity_group_descendants_and_physical_memory(self):
        source = 'import sys; memory = bytearray(8_000_000); print("ready", flush=True); sys.stdin.read()'
        with subprocess.Popen([sys.executable, '-c', source], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True, start_new_session=True) as child:
            try:
                self.assertEqual(child.stdout.readline().strip(), 'ready')
                process = macos.identity(child.pid)
                self.assertIsNotNone(process)
                self.assertEqual((process.pid, process.ppid, process.pgid, process.uid),
                                 (child.pid, os.getpid(), child.pid, os.getuid()))
                self.assertEqual(os.path.realpath(process.path), os.path.realpath(sys.executable))
                self.assertGreater(process.start_time, 0)
                self.assertIn(process, macos.group_members(child.pid))
                self.assertIn(process, macos.descendants(os.getpid()))
                self.assertEqual(macos.descendants(child.pid), [])
                measured = macos.footprint(child.pid, process.start_time)
                self.assertIsNotNone(measured, 'real physical footprint unavailable; check OS permissions')
                self.assertGreater(measured, 0)
                self.assertIsNone(macos.footprint(child.pid, process.start_time + 1))
                self.assertEqual(macos.boot_id(), macos.boot_id())
            finally:
                child.communicate(timeout=5)
        self.assertIsNone(macos.identity(child.pid))
