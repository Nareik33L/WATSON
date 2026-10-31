"""Counts for ``watson status`` and the upcoming dashboard status page."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.pipeline.frames import FRAME_STATES
from watson.pipeline.jobs import JobState
from watson.pipeline.replay import LAST_REPLAY_SETTING_ID

JOB_STATES = tuple(state.value for state in JobState)


@dataclass(frozen=True)
class StatusReport:
    frames_by_state: dict[str, int]
    jobs_by_state: dict[str, int]
    last_replay: dict[str, Any] | None

    def lines(self) -> list[str]:
        out: list[str] = []
        for state in FRAME_STATES:
            out.append(f"frames.{state}: {self.frames_by_state.get(state, 0)}")
        for state in JOB_STATES:
            out.append(f"jobs.{state}: {self.jobs_by_state.get(state, 0)}")
        if self.last_replay is None:
            out.append("last_replay: none")
            return out
        out.append(f"last_replay.fixture: {self.last_replay.get('fixture', '-')}")
        out.append(f"last_replay.finished_at: {self.last_replay.get('finished_at', '-')}")
        out.append(f"last_replay.frame_count: {self.last_replay.get('frame_count', 0)}")
        out.append(f"last_replay.deletion_verified: {self.last_replay.get('deletion_verified', 0)}")
        return out


def collect_status(conn: sqlcipher.Connection) -> StatusReport:
    frames = {state: 0 for state in FRAME_STATES}
    for row in conn.execute("SELECT state, COUNT(*) AS n FROM frames GROUP BY state"):
        frames[str(row["state"])] = int(row["n"])
    jobs = {state: 0 for state in JOB_STATES}
    for row in conn.execute("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state"):
        jobs[str(row["state"])] = int(row["n"])
    last_row = conn.execute(
        "SELECT json FROM settings WHERE id = ?", (LAST_REPLAY_SETTING_ID,)
    ).fetchone()
    last: dict[str, Any] | None = None
    if last_row is not None:
        loaded = json.loads(str(last_row["json"]))
        if isinstance(loaded, dict):
            last = loaded
    return StatusReport(frames_by_state=frames, jobs_by_state=jobs, last_replay=last)
