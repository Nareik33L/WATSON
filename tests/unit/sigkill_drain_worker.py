"""Subprocess helper: drain until the resolver boundary, then sleep.

The parent SIGKILLs this process after the ready file appears so jobs stay
claimed for the understand lease. Not a pytest module.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

from watson.intelligence.runtime import FakeRuntime
from watson.memory.db import connect
from watson.pipeline import understanding as understanding_mod


def main() -> None:
    db_path = Path(sys.argv[1])
    key = bytes.fromhex(sys.argv[2])
    recordings = Path(sys.argv[3])
    ready = Path(sys.argv[4])
    now = datetime.fromisoformat(sys.argv[5])

    def hang(*_a: object, **_k: object) -> list[object]:
        ready.write_text("ready", encoding="utf-8")
        fd = os.open(ready, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        dir_fd = os.open(str(ready.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        time.sleep(600)
        return []

    understanding_mod.resolve_candidates = hang  # type: ignore[method-assign]
    conn = connect(db_path, key, load_vec=False)
    try:
        runtime = FakeRuntime(recordings=recordings)
        understanding_mod.drain_understand_jobs(conn, runtime=runtime, embedding=runtime, now=now)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
