import ctypes
import errno
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    ppid: int
    pgid: int
    uid: int
    start_time: int
    path: str


class _RusageV0(ctypes.Structure):
    _fields_ = [('ri_uuid', ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            'ri_user_time', 'ri_system_time', 'ri_pkg_idle_wkups',
            'ri_interrupt_wkups', 'ri_pageins', 'ri_wired_size',
            'ri_resident_size', 'ri_phys_footprint', 'ri_proc_start_abstime',
            'ri_proc_exit_abstime',
        )
    ]


class _BsdInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'pbi_flags', 'pbi_status', 'pbi_xstatus', 'pbi_pid', 'pbi_ppid',
        'pbi_uid', 'pbi_gid', 'pbi_ruid', 'pbi_rgid', 'pbi_svuid',
        'pbi_svgid', 'rfu_1',
    )] + [('pbi_comm', ctypes.c_char * 16), ('pbi_name', ctypes.c_char * 32)] + [
        (name, ctypes.c_uint32) for name in (
            'pbi_nfiles', 'pbi_pgid', 'pbi_pjobc', 'e_tdev', 'e_tpgid',
        )
    ] + [('pbi_nice', ctypes.c_int32), ('pbi_start_tvsec', ctypes.c_uint64),
         ('pbi_start_tvusec', ctypes.c_uint64)]


assert ctypes.sizeof(_RusageV0) == 96
assert _RusageV0.ri_phys_footprint.offset == 72
assert _RusageV0.ri_proc_start_abstime.offset == 80
assert ctypes.sizeof(_BsdInfo) == 136

_libc = None
if sys.platform == 'darwin':
    _libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    for name, args in {
        'proc_pid_rusage': [ctypes.c_int, ctypes.c_int, ctypes.c_void_p],
        'proc_pidinfo': [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int],
        'proc_pidpath': [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32],
        'proc_listpgrppids': [ctypes.c_int, ctypes.c_void_p, ctypes.c_int],
        'proc_listchildpids': [ctypes.c_int, ctypes.c_void_p, ctypes.c_int],
        'sysctlbyname': [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t],
    }.items():
        function = getattr(_libc, name)
        function.argtypes = args
        function.restype = ctypes.c_int


def _api():
    if _libc is None:
        raise OSError(errno.ENOTSUP, 'macOS process APIs are unavailable')
    return _libc


def _failure(operation):
    code = ctypes.get_errno() or errno.EIO
    return OSError(code, operation + ': ' + os.strerror(code))


def boot_id() -> str:
    class Timeval(ctypes.Structure):
        _fields_ = [('tv_sec', ctypes.c_long), ('tv_usec', ctypes.c_int32)]

    value = Timeval()
    size = ctypes.c_size_t(ctypes.sizeof(value))
    ctypes.set_errno(0)
    if _api().sysctlbyname(b'kern.boottime', ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
        raise _failure('sysctl kern.boottime')
    if size.value != ctypes.sizeof(value) or value.tv_sec <= 0 or not 0 <= value.tv_usec < 1000000:
        raise OSError(errno.EIO, 'invalid kern.boottime result')
    return f'{value.tv_sec}.{value.tv_usec:06d}'


def _positive(value):
    if type(value) is not int or not 0 < value <= 2**31 - 1:
        raise ValueError('PID/PGID must be a positive pid_t')


def _rusage(pid):
    value = _RusageV0()
    ctypes.set_errno(0)
    if _api().proc_pid_rusage(pid, 0, ctypes.byref(value)) != 0:
        raise _failure('proc_pid_rusage')
    if not value.ri_proc_start_abstime or value.ri_proc_exit_abstime:
        raise OSError(errno.EAGAIN, 'process is not verifiably live')
    return value


def _bsd(pid):
    value = _BsdInfo()
    ctypes.set_errno(0)
    result = _api().proc_pidinfo(pid, 3, 0, ctypes.byref(value), ctypes.sizeof(value))
    if result != ctypes.sizeof(value):
        raise _failure('proc_pidinfo incomplete result')
    if value.pbi_pid != pid:
        raise OSError(errno.EAGAIN, 'process PID changed')
    return value


def _pids(function, value):
    capacity = 64
    for _ in range(12):
        buffer = (ctypes.c_int * capacity)()
        ctypes.set_errno(0)
        count = function(value, buffer, ctypes.sizeof(buffer))
        if count < 0 or (count == 0 and ctypes.get_errno()):
            raise _failure('process list')
        if count < capacity:
            return sorted({pid for pid in buffer[:count] if pid > 0})
        capacity *= 2
    raise OSError(errno.EAGAIN, 'process list did not stabilize')


def match_executable(processes: Iterable[ProcessIdentity], path: str) -> list[ProcessIdentity]:
    expected = os.path.realpath(path)
    return [process for process in processes if os.path.realpath(process.path) == expected]


def group_members(pgid: int) -> list[ProcessIdentity]:
    _positive(pgid)
    members = []
    for pid in _pids(_api().proc_listpgrppids, pgid):
        member = identity(pid)
        if member is None:
            continue
        if member.pgid != pgid:
            raise OSError(errno.EAGAIN, 'process group changed during inspection')
        members.append(member)
    return members


def descendants(root_pid: int) -> list[ProcessIdentity]:
    _positive(root_pid)
    root = identity(root_pid)
    if root is None:
        raise OSError(errno.ESRCH, 'descendant root is missing')
    found = {root_pid: root}
    pending = [root]
    while pending:
        parent = pending.pop()
        if identity(parent.pid) != parent:
            raise OSError(errno.EAGAIN, 'ancestor changed during inspection')
        for pid in _pids(_api().proc_listchildpids, parent.pid):
            child = identity(pid)
            if child is None or child.ppid != parent.pid or pid in found:
                raise OSError(errno.EAGAIN, 'descendant ancestry is ambiguous')
            found[pid] = child
            pending.append(child)
        if identity(parent.pid) != parent:
            raise OSError(errno.EAGAIN, 'ancestor changed during inspection')
    for member in found.values():
        if identity(member.pid) != member:
            raise OSError(errno.EAGAIN, 'process tree changed during inspection')
    if identity(root_pid) != root:
        raise OSError(errno.EAGAIN, 'descendant root changed during inspection')
    return [found[pid] for pid in sorted(found) if pid != root_pid]


def footprint(pid: int, expected_start: int | None = None) -> int | None:
    _positive(pid)
    _api()
    try:
        before = _rusage(pid)
        after = _rusage(pid)
        if (before.ri_proc_start_abstime != after.ri_proc_start_abstime
                or (expected_start is not None
                    and before.ri_proc_start_abstime != expected_start)):
            return None
        return after.ri_phys_footprint
    except OSError:
        return None


def identity(pid: int) -> ProcessIdentity | None:
    _positive(pid)
    try:
        before = _rusage(pid)
        bsd = _bsd(pid)
        path = ctypes.create_string_buffer(4096)
        ctypes.set_errno(0)
        length = _api().proc_pidpath(pid, path, len(path))
        if length <= 0 or length >= len(path) or not path.value:
            raise _failure('proc_pidpath')
        after_bsd = _bsd(pid)
        after = _rusage(pid)
        fields = ('pbi_pid', 'pbi_ppid', 'pbi_pgid', 'pbi_uid',
                  'pbi_start_tvsec', 'pbi_start_tvusec')
        if (before.ri_proc_start_abstime != after.ri_proc_start_abstime
                or any(getattr(bsd, field) != getattr(after_bsd, field) for field in fields)):
            raise OSError(errno.EAGAIN, 'process identity changed during inspection')
        return ProcessIdentity(pid, bsd.pbi_ppid, bsd.pbi_pgid, bsd.pbi_uid,
                               before.ri_proc_start_abstime, os.fsdecode(path.value))
    except OSError as error:
        if error.errno == errno.ESRCH:
            return None
        raise
