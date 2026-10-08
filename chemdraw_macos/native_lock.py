"""Cooperative, per-user native session gate shared by CLI and MCP processes."""
import math
import os
from pathlib import Path
import stat
import sys
import threading
import time
from contextlib import nullcontext
from functools import wraps

WINDOWS = sys.platform == 'win32'
if WINDOWS:
    import errno
    import msvcrt
else:
    import fcntl


class NativeBusy(RuntimeError):
    """The gate was unavailable; no native operation was dispatched by this call."""


def _open_posix(path):
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
    info=os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1:
        os.close(fd)
        raise OSError('Native lock must be an owned regular file with one link')
    return fd


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info,'st_file_attributes',0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _owned_by_current_user(path):
    """Compare the file owner SID with this process's user SID."""
    import win32api
    import win32security
    descriptor=win32security.GetFileSecurity(str(path),win32security.OWNER_SECURITY_INFORMATION)
    token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32security.TOKEN_QUERY)
    user=win32security.GetTokenInformation(token,win32security.TokenUser)[0]
    return descriptor.GetSecurityDescriptorOwner()==user


def _open_windows(path):
    # Windows os.open follows links: refuse links/reparse points before and after opening.
    try:before=os.lstat(path)
    except FileNotFoundError:before=None
    if before is not None and _is_link(before):
        raise OSError('Native lock must not be a link or reparse point')
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOINHERIT|os.O_BINARY,0o600)
    try:
        info=os.fstat(fd);after=os.lstat(path)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or _is_link(after)
                or (after.st_ino,after.st_dev)!=(info.st_ino,info.st_dev)
                or not _owned_by_current_user(path)):
            raise OSError('Native lock must be an owned regular file with one link')
    except BaseException:
        os.close(fd);raise
    return fd


def _try_lock(fd):
    """Non-blocking exclusive lock; False when another holder owns it."""
    if not WINDOWS:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return False
        return True
    os.lseek(fd,0,os.SEEK_SET)
    try:msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
    except OSError as exc:
        if exc.errno in (errno.EACCES,errno.EDEADLOCK):return False
        raise
    return True


def _unlock(fd):
    if not WINDOWS:
        fcntl.flock(fd,fcntl.LOCK_UN);return
    os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_UNLCK,1)


class NativeSessionLock:
    def __init__(self,path,timeout=2):
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<=timeout<=60:
            raise ValueError('Native lock wait must be finite and between 0 and 60 seconds')
        self.path=Path(path);self.timeout=timeout
        self._thread=threading.RLock();self._depth=0;self._fd=None;self._pid=os.getpid()

    def _busy(self):
        return NativeBusy('ChemDraw is busy in another cooperating client. No native operation was dispatched by this call; try again after that workflow finishes.')

    def __enter__(self):
        if os.getpid()!=self._pid:raise RuntimeError('Recreate the native lock after forking')
        deadline=time.monotonic()+self.timeout
        if not self._thread.acquire(timeout=self.timeout):raise self._busy()
        try:
            if self._depth:
                self._depth+=1
                return self
            self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            self._fd=(_open_windows if WINDOWS else _open_posix)(self.path)
            while not _try_lock(self._fd):
                remaining=deadline-time.monotonic()
                if remaining<=0:raise self._busy()
                time.sleep(min(.025,remaining))
            self._depth=1
            return self
        except BaseException:
            if self._fd is not None:os.close(self._fd);self._fd=None
            self._thread.release()
            raise

    def __exit__(self,*exc):
        try:
            self._depth-=1
            if not self._depth:
                try:_unlock(self._fd)
                finally:os.close(self._fd);self._fd=None
        finally:self._thread.release()


_shared=None
_guard=threading.Lock()


def _lock_path():
    if WINDOWS:
        base=os.environ.get('LOCALAPPDATA') or str(Path.home()/'AppData/Local')
        return Path(base)/'chemdraw-mcp-macos'/'native.lock'
    return Path.home()/'Library/Caches/chemdraw-mcp-macos/native.lock'


def shared_native_lock():
    global _shared
    with _guard:
        if _shared is None:
            _shared=NativeSessionLock(_lock_path())
        return _shared


def _after_fork():
    global _shared,_guard
    if _shared is not None and _shared._fd is not None:os.close(_shared._fd)
    _shared=None;_guard=threading.Lock()


if hasattr(os,'register_at_fork'):  # Windows has no fork.
    os.register_at_fork(after_in_child=_after_fork)


def native_transaction(fn):
    """Keep snapshot/import, inner workflow and final cleanup in one session."""
    @wraps(fn)
    def locked(bridge,*args,**kwargs):
        with getattr(bridge,'lock',nullcontext()):return fn(bridge,*args,**kwargs)
    return locked
