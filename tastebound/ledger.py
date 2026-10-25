"""File-backed JSON state shared by the spend cap and the usage limits.

Read-modify-write happens under an exclusive file lock, so limits hold across
Streamlit sessions, threads and processes on the same machine. If the block
inside raises, nothing is written. A corrupt file raises on read, which blocks
calls (fails closed).
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path


def state_dir() -> Path:
    return Path(os.environ.get("TASTEBOUND_STATE_DIR", Path.home() / ".tastebound"))


@contextmanager
def locked_json(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        raw = f.read()
        data = json.loads(raw) if raw.strip() else {}
        yield data
        f.seek(0)
        f.truncate()
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
