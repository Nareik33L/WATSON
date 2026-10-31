"""Replay a golden fixture through the live spool ingest path."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.capture.sidecar import SidecarError, load_sidecar
from watson.capture.spool import (
    SpoolError,
    assert_outside_fixture,
    copy_pairs_to_spool,
    iter_pairs,
    png_dimensions,
    sha256_file,
)
from watson.config.schema import ExclusionsConfig
from watson.memory.db import iso_now
from watson.pipeline.dedup import DEFAULT_DEDUP_THRESHOLD
from watson.pipeline.frames import (
    EXTRACT_JOB_KIND,
    delete_and_verify,
    ensure_working_day,
    extract_fixture_stub,
    ingest_pair,
)
from watson.pipeline.jobs import JobLeaseError, claim, complete, fail
from watson.pipeline.segmentation import SegmentConfig, segment_extracted_frames
from watson.privacy.audit import KIND_REPLAY, record_audit

log = logging.getLogger("watson.pipeline.replay")

LAST_REPLAY_SETTING_ID = "last_replay"
WORKER_ID = "watson-replay"


class ReplayError(ValueError):
    """Fixture validation or replay orchestration failed."""


@dataclass(frozen=True)
class ReplayResult:
    fixture_name: str
    fixture_dir: Path
    spool_dir: Path
    date: str
    frame_count: int
    ingested: int
    extracted: int
    deleted: int
    deletion_verified: int
    skipped_existing: int
    skipped_excluded: int
    deduped: int
    segments: int
    finished_at: str
    instant: bool
    speed: float


def load_and_validate_manifest(fixture_dir: Path) -> dict[str, Any]:
    """Validate manifest.json sha256/dims and every sidecar against the schema."""
    manifest_path = fixture_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ReplayError(f"{fixture_dir}: manifest.json is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReplayError(f"{fixture_dir}: manifest.json is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ReplayError("manifest.json must be an object")
    frames = manifest.get("frames")
    if not isinstance(frames, dict) or not frames:
        raise ReplayError("manifest.json has no frames")

    sidecar_stems = {path.stem for path in fixture_dir.glob("frames/*/*.json")}
    png_stems = {path.stem for path in fixture_dir.glob("frames/*/*.png")}
    manifest_ids = set(frames)
    if sidecar_stems != manifest_ids or png_stems != manifest_ids:
        raise ReplayError("manifest frames do not match PNG/sidecar files")

    for frame_id, raw_entry in frames.items():
        if not isinstance(raw_entry, dict):
            raise ReplayError(f"{frame_id}: manifest entry must be an object")
        rel = raw_entry.get("png")
        if not isinstance(rel, str):
            raise ReplayError(f"{frame_id}: manifest png path missing")
        png_path = fixture_dir / rel
        sidecar_path = png_path.with_suffix(".json")
        if not png_path.is_file():
            raise ReplayError(f"{frame_id}: PNG missing")
        if not sidecar_path.is_file():
            raise ReplayError(f"{frame_id}: sidecar missing")
        digest = sha256_file(png_path)
        expected_sha = raw_entry.get("sha256")
        if digest != expected_sha:
            raise ReplayError(f"{frame_id}: sha256 mismatch")
        width, height = png_dimensions(png_path)
        if width != raw_entry.get("width") or height != raw_entry.get("height"):
            raise ReplayError(f"{frame_id}: dimension mismatch vs manifest")
        try:
            sidecar = load_sidecar(sidecar_path)
        except SidecarError as exc:
            raise ReplayError(f"{frame_id}: {exc}") from exc
        if str(sidecar["frame_id"]) != frame_id:
            raise ReplayError(f"{frame_id}: sidecar frame_id mismatch")
        if int(sidecar["width"]) != width or int(sidecar["height"]) != height:
            raise ReplayError(f"{frame_id}: dimension mismatch vs sidecar")
    return manifest


def replay_fixture(
    fixture_dir: Path,
    conn: sqlcipher.Connection,
    *,
    instant: bool = False,
    speed: float = 1.0,
    spool_dir: Path | None = None,
    exclusions: ExclusionsConfig | None = None,
    dedup_threshold: int | None = None,
) -> ReplayResult:
    """Validate, copy into a 0700/0600 spool, ingest, extract stub, delete PNGs."""
    if speed <= 0:
        raise ReplayError("--speed must be > 0")
    fixture = fixture_dir.resolve()
    if not fixture.is_dir():
        raise ReplayError(f"fixture directory does not exist: {fixture_dir}")

    manifest = load_and_validate_manifest(fixture)
    name = str(manifest.get("name") or fixture.name)
    date = str(manifest.get("date") or "")
    if not date:
        raise ReplayError("manifest.json is missing date")

    temp: TemporaryDirectory[str] | None = None
    if spool_dir is None:
        temp = TemporaryDirectory(prefix="watson-spool-")
        dest = Path(temp.name)
    else:
        dest = spool_dir
    try:
        assert_outside_fixture(dest, fixture)
        frames_map = manifest["frames"]
        assert isinstance(frames_map, dict)
        copy_pairs_to_spool(fixture, frames_map.items(), dest)
        try:
            pairs = iter_pairs(dest)
        except SpoolError as exc:
            raise ReplayError(str(exc)) from exc

        working_day_id = ensure_working_day(conn, date)
        rules = exclusions if exclusions is not None else _default_exclusions()
        threshold = (
            _default_dedup_threshold() if dedup_threshold is None else dedup_threshold
        )
        ingested = 0
        skipped = 0
        skipped_excluded = 0
        previous_at: datetime | None = None
        for pair in pairs:
            _pace(previous_at, pair.captured_at, instant=instant, speed=speed)
            previous_at = pair.captured_at
            outcome = ingest_pair(
                conn,
                pair,
                working_day_id=working_day_id,
                exclusions=rules,
                fixture_dir=fixture,
            )
            if outcome == "ingested":
                ingested += 1
            elif outcome == "excluded":
                skipped_excluded += 1
            else:
                skipped += 1

        extracted = _drain_extract_jobs(
            conn, fixture_dir=fixture, dedup_threshold=threshold
        )
        segment_extracted_frames(
            conn,
            working_day_id=working_day_id,
            config=_segment_config(),
        )
        for pair in pairs:
            delete_and_verify(
                conn,
                pair.frame_id,
                pair.png_path,
                fixture_dir=fixture,
                sidecar_path=pair.sidecar_path,
            )
        remaining_png = list(dest.rglob("*.png"))
        remaining_json = list(dest.rglob("*.json"))
        if remaining_png or remaining_json:
            raise ReplayError(
                f"{len(remaining_png)} PNG(s) and {len(remaining_json)} sidecar(s) "
                "remain in the spool after replay"
            )

        verified = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM frames
                WHERE working_day_id = ? AND deletion_verified = 1
                """,
                (working_day_id,),
            ).fetchone()[0]
        )
        deleted = int(
            conn.execute(
                "SELECT COUNT(*) FROM frames WHERE working_day_id = ? AND state = 'deleted'",
                (working_day_id,),
            ).fetchone()[0]
        )
        deduped = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM frames
                WHERE working_day_id = ? AND duplicate_of IS NOT NULL
                """,
                (working_day_id,),
            ).fetchone()[0]
        )
        segments = int(
            conn.execute(
                "SELECT COUNT(*) FROM segments WHERE working_day_id = ?",
                (working_day_id,),
            ).fetchone()[0]
        )
        finished_at = iso_now()
        result = ReplayResult(
            fixture_name=name,
            fixture_dir=fixture,
            spool_dir=dest,
            date=date,
            frame_count=len(pairs),
            ingested=ingested,
            extracted=extracted,
            deleted=deleted,
            deletion_verified=verified,
            skipped_existing=skipped,
            skipped_excluded=skipped_excluded,
            deduped=deduped,
            segments=segments,
            finished_at=finished_at,
            instant=instant,
            speed=speed,
        )
        _store_last_replay(conn, result)
        log.info(
            "replay complete",
            extra={
                "fixture": name,
                "frame_count": result.frame_count,
                "ingested": ingested,
                "extracted": extracted,
                "deleted": deleted,
                "deletion_verified": verified,
                "skipped_excluded": skipped_excluded,
                "deduped": deduped,
                "segments": segments,
            },
        )
        return result
    finally:
        if temp is not None:
            temp.cleanup()


def _default_exclusions() -> ExclusionsConfig:
    from watson.config import load_config

    return load_config().exclusions


def _default_dedup_threshold() -> int:
    from watson.config import load_config

    return load_config().capture.dedup_threshold


def _segment_config() -> SegmentConfig:
    from watson.config import load_config

    return SegmentConfig.from_settings(load_config().segmentation)


def _pace(
    previous: datetime | None,
    current: datetime,
    *,
    instant: bool,
    speed: float,
) -> None:
    if instant or previous is None:
        return
    delay = (current - previous).total_seconds() / speed
    if delay > 0:
        time.sleep(delay)


def _drain_extract_jobs(
    conn: sqlcipher.Connection,
    *,
    fixture_dir: Path,
    dedup_threshold: int = DEFAULT_DEDUP_THRESHOLD,
) -> int:
    completed = 0
    while True:
        job = claim(conn, worker_id=WORKER_ID)
        if job is None:
            break
        if job.kind != EXTRACT_JOB_KIND:
            fail(conn, job.id, f"unexpected job kind {job.kind}", worker_id=WORKER_ID)
            continue
        try:
            extract_fixture_stub(
                conn,
                job.payload,
                fixture_dir=fixture_dir,
                dedup_threshold=dedup_threshold,
            )
            complete(conn, job.id, worker_id=WORKER_ID)
            completed += 1
        except (JobLeaseError, OSError, ValueError, RuntimeError) as exc:
            fail(conn, job.id, type(exc).__name__, worker_id=WORKER_ID)
            raise ReplayError(f"extract failed for job {job.id}") from exc
    return completed


def _store_last_replay(conn: sqlcipher.Connection, result: ReplayResult) -> None:
    payload = {
        "fixture": result.fixture_name,
        "date": result.date,
        "finished_at": result.finished_at,
        "frame_count": result.frame_count,
        "ingested": result.ingested,
        "extracted": result.extracted,
        "deleted": result.deleted,
        "deletion_verified": result.deletion_verified,
        "skipped_excluded": result.skipped_excluded,
        "deduped": result.deduped,
        "segments": result.segments,
        "instant": result.instant,
        "speed": result.speed,
    }
    stamp = result.finished_at
    conn.execute(
        """
        INSERT INTO settings (id, created_at, updated_at, json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET json = excluded.json, updated_at = excluded.updated_at
        """,
        (LAST_REPLAY_SETTING_ID, stamp, stamp, json.dumps(payload, sort_keys=True)),
    )
    record_audit(
        conn,
        kind=KIND_REPLAY,
        detail={
            "fixture": result.fixture_name,
            "frame_count": result.frame_count,
            "ingested": result.ingested,
            "extracted": result.extracted,
            "deleted": result.deleted,
            "deletion_verified": result.deletion_verified,
            "skipped_excluded": result.skipped_excluded,
            "deduped": result.deduped,
            "segments": result.segments,
        },
        commit=False,
    )
    conn.commit()
