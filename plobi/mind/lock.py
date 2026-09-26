"""Cross-process write lock for the Mind vault.

L2 agents run as separate processes, so an in-process mutex is not enough:
two of them committing to the same git repo at once is exactly the failure
ADR-0011 sends through a serial writer. Implemented with atomic file creation
so it works identically on Windows and POSIX without extra dependencies.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# A holder that dies mid-write must not wedge the queue forever.
STALE_AFTER_SECONDS = 120.0
POLL_SECONDS = 0.05


class MindLockTimeout(TimeoutError):
    pass


def _is_stale(path: Path) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age > STALE_AFTER_SECONDS


@contextmanager
def mind_write_lock(lock_path: Path, *, timeout: float = 30.0) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    handle: Optional[int] = None

    while True:
        try:
            handle = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if _is_stale(lock_path):
                logger.warning("mind: breaking stale write lock at %s", lock_path)
                # TOCTOU mitigation: try O_EXCL create first, only unlink
                # if the file still exists. This avoids a race where two
                # processes both detect stale, both unlink, and both create.
                try:
                    # Attempt to unlink only if the file is still stale —
                    # another process may have already replaced it.
                    if _is_stale(lock_path):
                        lock_path.unlink()
                except OSError:
                    pass
                continue
            if time.time() >= deadline:
                raise MindLockTimeout(f"could not acquire {lock_path} within {timeout}s")
            time.sleep(POLL_SECONDS)

    try:
        os.write(handle, str(os.getpid()).encode("ascii"))
        os.close(handle)
        handle = None
        yield
    finally:
        if handle is not None:
            try:
                os.close(handle)
            except OSError:
                pass
        try:
            lock_path.unlink()
        except OSError:
            logger.debug("mind: lock file already gone: %s", lock_path)
