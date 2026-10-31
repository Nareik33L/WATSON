"""Counts for ``watson status`` and the dashboard status page."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.pipeline.frames import FRAME_STATES
from watson.pipeline.jobs import JobState
from watson.pipeline.replay import LAST_REPLAY_SETTING_ID

JOB_STATES = tuple(state.value for state in JobState)
LAST_FRAMES_LIMIT = 8
AUDIT_TAIL_LIMIT = 10


@dataclass(frozen=True)
class FrameMeta:
    """Content-free frame row for the status page. No titles, no OCR."""

    id: str
    captured_at: str
    app_name: str | None
    bundle_id: str | None
    state: str
    deletion_verified: bool


@dataclass(frozen=True)
class AuditItem:
    """Content-free audit row (ids, counts, hashes only)."""

    id: str
    created_at: str
    kind: str
    actor: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class StatusReport:
    frames_by_state: dict[str, int]
    jobs_by_state: dict[str, int]
    last_replay: dict[str, Any] | None
    capture_state: str = "idle"
    queue_depth: int = 0
    last_frames: tuple[FrameMeta, ...] = ()
    deletion_verified_count: int = 0
    deletion_unverified_count: int = 0
    audit_tail: tuple[AuditItem, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready payload with no screen-content fields."""
        return {
            "capture_state": self.capture_state,
            "queue_depth": self.queue_depth,
            "frames_by_state": dict(self.frames_by_state),
            "jobs_by_state": dict(self.jobs_by_state),
            "deletion_verified_count": self.deletion_verified_count,
            "deletion_unverified_count": self.deletion_unverified_count,
            "last_frames": [asdict(frame) for frame in self.last_frames],
            "last_replay": self.last_replay,
            "audit_tail": [
                {
                    "id": item.id,
                    "created_at": item.created_at,
                    "kind": item.kind,
                    "actor": item.actor,
                    "detail": item.detail,
                }
                for item in self.audit_tail
            ],
        }

    def lines(self) -> list[str]:
        out: list[str] = [
            f"capture.state: {self.capture_state}",
            f"queue.depth: {self.queue_depth}",
        ]
        for state in FRAME_STATES:
            out.append(f"frames.{state}: {self.frames_by_state.get(state, 0)}")
        out.append(f"deletion.verified: {self.deletion_verified_count}")
        out.append(f"deletion.unverified: {self.deletion_unverified_count}")
        for state in JOB_STATES:
            out.append(f"jobs.{state}: {self.jobs_by_state.get(state, 0)}")
        if self.last_replay is None:
            out.append("last_replay: none")
        else:
            out.append(f"last_replay.fixture: {self.last_replay.get('fixture', '-')}")
            out.append(f"last_replay.finished_at: {self.last_replay.get('finished_at', '-')}")
            out.append(f"last_replay.frame_count: {self.last_replay.get('frame_count', 0)}")
            out.append(
                "last_replay.deletion_verified: "
                f"{self.last_replay.get('deletion_verified', 0)}"
            )
        if not self.last_frames:
            out.append("last_frames: none")
        else:
            for index, frame in enumerate(self.last_frames):
                out.append(f"last_frame.{index}.id: {frame.id}")
                out.append(f"last_frame.{index}.captured_at: {frame.captured_at}")
                out.append(f"last_frame.{index}.app_name: {frame.app_name or '-'}")
                out.append(f"last_frame.{index}.bundle_id: {frame.bundle_id or '-'}")
                out.append(f"last_frame.{index}.state: {frame.state}")
                out.append(
                    f"last_frame.{index}.deletion_verified: {int(frame.deletion_verified)}"
                )
        if not self.audit_tail:
            out.append("audit: none")
        else:
            for index, item in enumerate(self.audit_tail):
                out.append(f"audit.{index}.kind: {item.kind}")
                out.append(f"audit.{index}.created_at: {item.created_at}")
                out.append(f"audit.{index}.actor: {item.actor}")
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

    verified_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN deletion_verified = 1 THEN 1 ELSE 0 END), 0) AS verified,
            COALESCE(SUM(CASE WHEN deletion_verified = 0 THEN 1 ELSE 0 END), 0) AS unverified
        FROM frames
        """
    ).fetchone()
    verified = int(verified_row["verified"]) if verified_row is not None else 0
    unverified = int(verified_row["unverified"]) if verified_row is not None else 0

    queue_row = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE state IN ('pending', 'claimed')"
    ).fetchone()
    queue_depth = int(queue_row["n"]) if queue_row is not None else 0

    last_frames = tuple(
        FrameMeta(
            id=str(row["id"]),
            captured_at=str(row["captured_at"]),
            app_name=None if row["app_name"] is None else str(row["app_name"]),
            bundle_id=None if row["bundle_id"] is None else str(row["bundle_id"]),
            state=str(row["state"]),
            deletion_verified=int(row["deletion_verified"]) == 1,
        )
        for row in conn.execute(
            """
            SELECT id, captured_at, app_name, bundle_id, state, deletion_verified
            FROM frames
            ORDER BY captured_at DESC
            LIMIT ?
            """,
            (LAST_FRAMES_LIMIT,),
        )
    )
    audit_tail = tuple(
        AuditItem(
            id=str(row["id"]),
            created_at=str(row["created_at"]),
            kind=str(row["kind"]),
            actor=str(row["actor"]),
            detail=_detail(row["detail_json"]),
        )
        for row in conn.execute(
            """
            SELECT id, created_at, kind, actor, detail_json
            FROM audit_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (AUDIT_TAIL_LIMIT,),
        )
    )
    capture_state = "backlog" if frames.get("captured", 0) > 0 else "idle"
    return StatusReport(
        frames_by_state=frames,
        jobs_by_state=jobs,
        last_replay=last,
        capture_state=capture_state,
        queue_depth=queue_depth,
        last_frames=last_frames,
        deletion_verified_count=verified,
        deletion_unverified_count=unverified,
        audit_tail=audit_tail,
    )


def _detail(raw: object) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    loaded = json.loads(str(raw))
    return loaded if isinstance(loaded, dict) else {}
