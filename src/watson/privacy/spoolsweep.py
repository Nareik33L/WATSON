"""Tier-0 spool sweeper: stale and orphaned PNG/sidecar pairs (plan §5 / D4).

Raw PNGs are deleted at extraction. This is the crash safety net. It never
follows symlinks and never unlinks a path whose real location is outside
the spool directory. Unlink failures use ``frame_deletion_failed``.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlcipher

from watson.config.schema import DEFAULT_SPOOL_HARD_LIMIT_HOURS
from watson.memory.db import iso_now
from watson.privacy.audit import KIND_SPOOL_SWEPT, audit_error_class, record_audit

log = logging.getLogger("watson.privacy.spoolsweep")

_SPOOL_SUFFIXES = frozenset({".png", ".json"})
_LIVE_FRAME_STATES = frozenset({"captured", "failed"})


class SpoolSweepError(RuntimeError):
    """Sweeper refused a path or failed to unlink."""


@dataclass(frozen=True)
class SpoolSweepResult:
    png: int
    sidecar: int
    orphaned: int
    stale: int
    unprocessed: int
    refused: int


def sweep_spool(
    conn: sqlcipher.Connection,
    spool_dir: Path,
    *,
    now: datetime | None = None,
    hard_limit_hours: int = DEFAULT_SPOOL_HARD_LIMIT_HOURS,
) -> SpoolSweepResult:
    """Remove orphaned or stale spool files. Idempotent."""
    if hard_limit_hours < 1:
        raise ValueError("hard_limit_hours must be >= 1")
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    else:
        moment = moment.astimezone(UTC)
    cutoff = moment.timestamp() - timedelta(hours=hard_limit_hours).total_seconds()

    if not spool_dir.exists():
        return SpoolSweepResult(0, 0, 0, 0, 0, 0)
    try:
        root = _resolved_spool_root(spool_dir)
    except SpoolSweepError:
        return SpoolSweepResult(0, 0, 0, 0, 0, 0)

    png = sidecar = orphaned = stale = unprocessed = refused = 0
    for group in _iter_pairs(root):
        if not _group_is_safe(group, root):
            refused += 1
            continue
        decision = _decide(conn, group, cutoff=cutoff)
        if decision == "keep":
            continue
        removed = _unlink_group(conn, group, root)
        png += removed[0]
        sidecar += removed[1]
        if decision == "stale":
            stale += 1
            if _mark_unprocessed(conn, group.frame_id, moment):
                unprocessed += 1
        else:
            orphaned += 1
        conn.commit()
    if png or sidecar or orphaned or stale or unprocessed or refused:
        record_audit(
            conn,
            kind=KIND_SPOOL_SWEPT,
            detail={
                "png": png,
                "sidecar": sidecar,
                "orphaned": orphaned,
                "stale": stale,
                "unprocessed": unprocessed,
                "refused": refused,
            },
            commit=True,
        )
    _remove_empty_day_dirs(root)
    if png or sidecar:
        log.info(
            "swept spool",
            extra={
                "png": png,
                "sidecar": sidecar,
                "orphaned": orphaned,
                "stale": stale,
                "unprocessed": unprocessed,
                "refused": refused,
            },
        )
    return SpoolSweepResult(
        png=png,
        sidecar=sidecar,
        orphaned=orphaned,
        stale=stale,
        unprocessed=unprocessed,
        refused=refused,
    )


@dataclass(frozen=True)
class _FileGroup:
    frame_id: str | None
    png: Path | None
    sidecar: Path | None


def refuse_spool_path(path: Path, spool_dir: Path) -> None:
    """Raise if ``path`` is a symlink or would leave ``spool_dir``.

    Used by the sweeper and by unit tests that prove refusal.
    """
    root = _resolved_spool_root(spool_dir)
    _assert_safe_member(path, root)


def _resolved_spool_root(spool_dir: Path) -> Path:
    if spool_dir.is_symlink():
        # The configured root may itself be a link; operate inside the target.
        root = Path(os.path.realpath(spool_dir))
    else:
        root = spool_dir.resolve()
    if not root.is_dir():
        raise SpoolSweepError("spool is not a directory")
    return root


def _assert_safe_member(path: Path, root: Path) -> Path:
    if path.is_symlink():
        raise SpoolSweepError("refusing symlink")
    try:
        stat = path.lstat()
    except FileNotFoundError as exc:
        raise SpoolSweepError("path missing") from exc
    if os.path.islink(path):
        raise SpoolSweepError("refusing symlink")
    real = os.path.realpath(path)
    root_real = os.path.realpath(root)
    if real != root_real and not real.startswith(root_real + os.sep):
        raise SpoolSweepError("refusing path outside spool")
    if path == root or Path(real) == root:
        raise SpoolSweepError("refusing spool root")
    if stat.st_nlink < 1:
        raise SpoolSweepError("refusing path outside spool")
    return Path(real)


def _iter_pairs(root: Path) -> list[_FileGroup]:
    grouped: dict[tuple[str, str], dict[str, Path]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if not os.path.islink(os.path.join(dirpath, name))]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in _SPOOL_SUFFIXES:
                continue
            key = (dirpath, path.stem)
            slot = grouped.setdefault(key, {})
            if path.suffix.lower() == ".png":
                slot["png"] = path
            else:
                slot["sidecar"] = path
    groups: list[_FileGroup] = []
    for (_day, stem), files in sorted(grouped.items()):
        frame_id = stem if _is_uuid(stem) else None
        groups.append(
            _FileGroup(
                frame_id=frame_id,
                png=files.get("png"),
                sidecar=files.get("sidecar"),
            )
        )
    return groups


def _decide(conn: sqlcipher.Connection, group: _FileGroup, *, cutoff: float) -> str:
    # No row / non-UUID / unpaired / leftover states are orphans only once
    # the oldest file is at least spool_hard_limit_hours old (plan §5 Tier 0).
    stale = _group_is_stale(group, cutoff)
    if group.frame_id is None:
        return "orphan" if stale else "keep"
    row = conn.execute("SELECT state FROM frames WHERE id = ?", (group.frame_id,)).fetchone()
    if row is None:
        return "orphan" if stale else "keep"
    state = str(row["state"])
    if state in _LIVE_FRAME_STATES:
        return "stale" if stale else "keep"
    return "orphan" if stale else "keep"


def _group_is_stale(group: _FileGroup, cutoff: float) -> bool:
    mtimes: list[float] = []
    for path in (group.png, group.sidecar):
        if path is None:
            continue
        try:
            mtimes.append(path.lstat().st_mtime)
        except FileNotFoundError:
            continue
    if not mtimes:
        return True
    return min(mtimes) <= cutoff


def _group_is_safe(group: _FileGroup, root: Path) -> bool:
    for path in (group.png, group.sidecar):
        if path is None:
            continue
        try:
            _assert_safe_member(path, root)
        except SpoolSweepError:
            return False
    return True


def _unlink_group(conn: sqlcipher.Connection, group: _FileGroup, root: Path) -> tuple[int, int]:
    png_n = sidecar_n = 0
    for kind, path in (("png", group.png), ("sidecar", group.sidecar)):
        if path is None:
            continue
        try:
            _safe_unlink(path, root)
        except OSError as exc:
            from watson.pipeline.frames import record_delete_failure

            record_delete_failure(
                conn,
                frame_id=group.frame_id,
                error=audit_error_class(exc),
            )
            raise SpoolSweepError(f"spool delete failed for {path.name}") from exc
        except SpoolSweepError:
            raise
        if kind == "png":
            png_n += 1
        else:
            sidecar_n += 1
    return png_n, sidecar_n


def _safe_unlink(path: Path, root: Path) -> None:
    _assert_safe_member(path, root)
    if path.is_symlink() or os.path.islink(path):
        raise SpoolSweepError("refusing symlink")
    path.unlink()
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise SpoolSweepError(f"spool files still present after unlink for {path.name}")


def _mark_unprocessed(conn: sqlcipher.Connection, frame_id: str | None, moment: datetime) -> bool:
    if frame_id is None:
        return False
    stamp = iso_now(moment)
    cursor = conn.execute(
        """
        UPDATE frames
        SET state = 'deleted_unprocessed',
            deleted_at = COALESCE(deleted_at, ?),
            deletion_verified = 1,
            updated_at = ?
        WHERE id = ? AND state IN ('captured', 'failed')
        """,
        (stamp, stamp, frame_id),
    )
    return int(cursor.rowcount) == 1


def _remove_empty_day_dirs(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
        current = Path(dirpath)
        if current == root:
            continue
        if dirnames or filenames:
            continue
        if current.is_symlink() or os.path.islink(current):
            continue
        try:
            _assert_safe_member(current, root)
            current.rmdir()
        except (OSError, SpoolSweepError):
            continue


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True
