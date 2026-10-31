"""Frame ingest, fixture-only extract stub, and D4 deletion verification."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.capture.exclusions import exclusion_reason
from watson.capture.sidecar import load_sidecar
from watson.capture.spool import SpoolPair, assert_not_fixture_file, sha256_file
from watson.config.schema import ExclusionsConfig
from watson.memory.db import iso_now
from watson.memory.ids import new_id
from watson.pipeline.jobs import enqueue
from watson.privacy.audit import (
    KIND_DELETE_FAILED,
    KIND_DELETED,
    KIND_INGESTED,
    KIND_SKIPPED,
    KIND_VERIFIED,
    record_audit,
)

log = logging.getLogger("watson.pipeline.frames")

# M1 extract stub only. Real OCR/VLM extraction is M2.
FIXTURE_TEXT_SOURCE = "fixture_sidecar"
EXTRACT_JOB_KIND = "extract"
PIPELINE_VERSION = 1
FRAME_STATES = (
    "captured",
    "extracted",
    "deleted",
    "failed",
    "deleted_unprocessed",
    "skipped",
)


class FrameError(RuntimeError):
    """Ingest, extract, or deletion verification failed."""


def ensure_working_day(conn: sqlcipher.Connection, date: str) -> str:
    """Return the working_days.id for ``date``, creating an open day if needed."""
    row = conn.execute("SELECT id FROM working_days WHERE date = ?", (date,)).fetchone()
    if row is not None:
        return str(row[0])
    stamp = iso_now()
    day_id = new_id()
    conn.execute(
        """
        INSERT INTO working_days (
            id, created_at, updated_at, date, started_at, ended_at,
            state, consolidation_error, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, NULL, 'open', NULL, ?)
        """,
        (day_id, stamp, stamp, date, stamp, PIPELINE_VERSION),
    )
    conn.commit()
    return day_id


def ingest_pair(
    conn: sqlcipher.Connection,
    pair: SpoolPair,
    *,
    working_day_id: str,
    exclusions: ExclusionsConfig | None = None,
    fixture_dir: Path | None = None,
) -> str:
    """Insert a captured frame and enqueue extract. Idempotent on frame id.

    Returns ``ingested``, ``existing``, or ``excluded``. Excluded frames are
    unlinked from the spool and verified gone; they never write ``frames``
    or content rows.
    """
    rules = exclusions if exclusions is not None else ExclusionsConfig()
    reason = exclusion_reason(pair.sidecar, rules)
    if reason is not None:
        reject_excluded_pair(conn, pair, reason=reason, fixture_dir=fixture_dir)
        return "excluded"

    existing = conn.execute("SELECT id FROM frames WHERE id = ?", (pair.frame_id,)).fetchone()
    if existing is not None:
        enqueue(
            conn,
            kind=EXTRACT_JOB_KIND,
            payload=_extract_payload(pair),
            idempotency_key=f"{EXTRACT_JOB_KIND}:{pair.frame_id}",
        )
        return "existing"

    title = str(pair.sidecar.get("window_title") or "")
    title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest() if title else None
    png_sha256 = sha256_file(pair.png_path)
    stamp = iso_now()
    conn.execute(
        """
        INSERT INTO frames (
            id, created_at, updated_at, working_day_id, captured_at,
            bundle_id, app_name, window_title_hash, window_title,
            url_domain, phash, state, failure_count, deleted_at, deletion_verified
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'captured', 0, NULL, 0)
        """,
        (
            pair.frame_id,
            stamp,
            stamp,
            working_day_id,
            str(pair.sidecar["captured_at"]),
            str(pair.sidecar["bundle_id"]),
            str(pair.sidecar["app_name"]),
            title_hash,
            title or None,
            str(pair.sidecar["phash"]),
        ),
    )
    record_audit(
        conn,
        kind=KIND_INGESTED,
        detail={"frame_id": pair.frame_id, "png_sha256": png_sha256},
        commit=False,
    )
    conn.commit()
    enqueue(
        conn,
        kind=EXTRACT_JOB_KIND,
        payload=_extract_payload(pair),
        idempotency_key=f"{EXTRACT_JOB_KIND}:{pair.frame_id}",
    )
    log.info("ingested frame", extra={"frame_id": pair.frame_id, "png_sha256": png_sha256})
    return "ingested"


def reject_excluded_pair(
    conn: sqlcipher.Connection,
    pair: SpoolPair,
    *,
    reason: str,
    fixture_dir: Path | None = None,
) -> None:
    """Delete an excluded spool pair and record a content-free audit event."""
    unlink_spool_pair(pair.png_path, pair.sidecar_path, fixture_dir=fixture_dir)
    record_audit(
        conn,
        kind=KIND_SKIPPED,
        detail={"reason": reason},
        commit=True,
    )
    log.info("skipped excluded frame", extra={"reason": reason})


def unlink_spool_pair(
    png_path: Path,
    sidecar_path: Path,
    *,
    fixture_dir: Path | None = None,
) -> None:
    """Unlink PNG + sidecar and re-stat. Never touches a source fixture."""
    if fixture_dir is not None:
        assert_not_fixture_file(png_path, fixture_dir)
        assert_not_fixture_file(sidecar_path, fixture_dir)
    try:
        if png_path.is_file():
            png_path.unlink()
        if sidecar_path.is_file():
            sidecar_path.unlink()
    except OSError as exc:
        raise FrameError(f"excluded spool delete failed for {png_path.name}") from exc
    if not (_path_gone(png_path) and _path_gone(sidecar_path)):
        raise FrameError(f"excluded spool files still present after unlink for {png_path.name}")


def extract_fixture_stub(
    conn: sqlcipher.Connection,
    job_payload: dict[str, Any],
    *,
    fixture_dir: Path,
) -> None:
    """Record sidecar OCR as fixture-only ``frame_text``, then delete spool files.

    This is not a real extractor. It exists so M1 can exercise D4 on golden
    fixtures before Apple Vision / VLM land in M2. Sidecar JSON is Tier 0
    (raw OCR) and is removed with the PNG once extraction is persisted.
    """
    if not job_payload.get("fixture_only"):
        raise FrameError("M1 extract stub only accepts fixture_only jobs")
    frame_id = str(job_payload["frame_id"])
    sidecar_path = Path(str(job_payload["sidecar_path"]))
    png_path = Path(str(job_payload["png_path"]))
    assert_not_fixture_file(png_path, fixture_dir)
    assert_not_fixture_file(sidecar_path, fixture_dir)

    sidecar = load_sidecar(sidecar_path)
    if str(sidecar["frame_id"]) != frame_id:
        raise FrameError("extract payload frame_id does not match sidecar")

    lines = list(sidecar["ocr"]["lines"])
    ocr_joined = "\n".join(str(line["text"]) for line in lines)
    confidences = [float(line["confidence"]) for line in lines]
    ocr_confidence = (sum(confidences) / len(confidences)) if confidences else None
    ocr_sha256 = hashlib.sha256(ocr_joined.encode("utf-8")).hexdigest()
    stamp = iso_now()

    existing_text = conn.execute(
        "SELECT id FROM frame_text WHERE frame_id = ?", (frame_id,)
    ).fetchone()
    if existing_text is None:
        captured_at = datetime.fromisoformat(str(sidecar["captured_at"]).replace("Z", "+00:00"))
        expires_at = iso_now(captured_at.astimezone(UTC) + timedelta(days=7))
        conn.execute(
            """
            INSERT INTO frame_text (
                id, created_at, updated_at, frame_id, text_redacted,
                ocr_confidence, expires_at, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                stamp,
                stamp,
                frame_id,
                ocr_joined,
                ocr_confidence,
                expires_at,
                FIXTURE_TEXT_SOURCE,
            ),
        )

    conn.execute(
        """
        UPDATE frames
        SET state = CASE WHEN state = 'captured' THEN 'extracted' ELSE state END,
            updated_at = ?
        WHERE id = ? AND state IN ('captured', 'extracted')
        """,
        (stamp, frame_id),
    )
    conn.commit()
    log.info(
        "fixture extract stub wrote frame_text",
        extra={
            "frame_id": frame_id,
            "source": FIXTURE_TEXT_SOURCE,
            "ocr_sha256": ocr_sha256,
            "ocr_line_count": len(lines),
        },
    )
    delete_and_verify(
        conn,
        frame_id,
        png_path,
        fixture_dir=fixture_dir,
        sidecar_path=sidecar_path,
    )


def delete_and_verify(
    conn: sqlcipher.Connection,
    frame_id: str,
    png_path: Path,
    *,
    fixture_dir: Path,
    sidecar_path: Path | None = None,
) -> None:
    """Unlink spool PNG + sidecar, re-stat both, set deletion_verified=1.

    Never touches the source fixture. On permission errors or a file that
    is still there after unlink, the frame stays extracted with
    ``deletion_verified=0`` and a content-free failure audit is written.
    """
    sidecar = sidecar_path if sidecar_path is not None else png_path.with_suffix(".json")
    assert_not_fixture_file(png_path, fixture_dir)
    assert_not_fixture_file(sidecar, fixture_dir)
    row = conn.execute(
        "SELECT state, deletion_verified FROM frames WHERE id = ?", (frame_id,)
    ).fetchone()
    if row is None:
        if _path_gone(png_path) and _path_gone(sidecar):
            return
        raise FrameError(f"frame {frame_id} missing during deletion")
    already_verified = int(row["deletion_verified"]) == 1

    png_existed = png_path.is_file()
    sidecar_existed = sidecar.is_file()
    try:
        if png_existed:
            png_path.unlink()
        if sidecar_existed:
            sidecar.unlink()
    except OSError as exc:
        _record_delete_failure(conn, frame_id, type(exc).__name__)
        raise FrameError(f"spool delete failed for frame {frame_id}") from exc

    png_gone = _path_gone(png_path)
    sidecar_gone = _path_gone(sidecar)
    if not (png_gone and sidecar_gone):
        _record_delete_failure(conn, frame_id, "still_present")
        raise FrameError(f"spool files still present after unlink for frame {frame_id}")

    if already_verified:
        conn.commit()
        return

    if png_existed or sidecar_existed:
        record_audit(
            conn,
            kind=KIND_DELETED,
            detail={"frame_id": frame_id, "png": 1, "sidecar": 1},
            commit=False,
        )
        log.info("deleted spool png and sidecar", extra={"frame_id": frame_id})

    stamp = iso_now()
    conn.execute(
        """
        UPDATE frames
        SET state = 'deleted',
            deleted_at = COALESCE(deleted_at, ?),
            deletion_verified = 1,
            updated_at = ?
        WHERE id = ?
        """,
        (stamp, stamp, frame_id),
    )
    record_audit(
        conn,
        kind=KIND_VERIFIED,
        detail={"frame_id": frame_id},
        commit=False,
    )
    conn.commit()
    log.info("verified spool png and sidecar gone", extra={"frame_id": frame_id})


def _path_gone(path: Path) -> bool:
    try:
        path.stat()
    except FileNotFoundError:
        return True
    return False


def _record_delete_failure(conn: sqlcipher.Connection, frame_id: str, error: str) -> None:
    stamp = iso_now()
    conn.execute(
        """
        UPDATE frames
        SET failure_count = failure_count + 1, updated_at = ?
        WHERE id = ?
        """,
        (stamp, frame_id),
    )
    record_audit(
        conn,
        kind=KIND_DELETE_FAILED,
        detail={"frame_id": frame_id, "error": error},
        commit=False,
    )
    conn.commit()
    log.info("spool delete failed", extra={"frame_id": frame_id, "error": error})


def _extract_payload(pair: SpoolPair) -> dict[str, Any]:
    return {
        "frame_id": pair.frame_id,
        "png_path": str(pair.png_path),
        "sidecar_path": str(pair.sidecar_path),
        "fixture_only": True,
    }
