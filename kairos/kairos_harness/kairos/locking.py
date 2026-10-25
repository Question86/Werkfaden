from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class WorkspaceLockError(TimeoutError):
    pass


_guard = threading.Lock()
_process_locks: dict[str, threading.RLock] = {}
_local = threading.local()


def _process_lock(key: str) -> threading.RLock:
    with _guard:
        return _process_locks.setdefault(key, threading.RLock())


def _acquire_os_lock(handle, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_NBLCK
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), mode, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise WorkspaceLockError("timed out waiting for the KAIROS workspace write lock")
                time.sleep(0.05)
    else:
        import fcntl

        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise WorkspaceLockError("timed out waiting for the KAIROS workspace write lock")
                time.sleep(0.05)


def _release_os_lock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def workspace_write_lock(workspace: Path, *, timeout: float = 30.0) -> Iterator[None]:
    workspace = workspace.resolve()
    key = str(workspace).casefold()
    lock = _process_lock(key)
    with lock:
        depths = getattr(_local, "depths", {})
        handles = getattr(_local, "handles", {})
        depth = int(depths.get(key, 0))
        if depth == 0:
            lock_path = workspace / ".kairos" / "workspace.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+b")
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            _acquire_os_lock(handle, timeout)
            handles[key] = handle
        depths[key] = depth + 1
        _local.depths = depths
        _local.handles = handles
        try:
            yield
        finally:
            depths[key] -= 1
            if depths[key] == 0:
                handle = handles.pop(key)
                try:
                    _release_os_lock(handle)
                finally:
                    handle.close()
                depths.pop(key, None)
