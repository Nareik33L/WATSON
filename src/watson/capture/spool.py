"""Spool of PNG + sidecar pairs. Live capture and replay share this reader."""

from __future__ import annotations

import hashlib
import shutil
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from watson.capture.sidecar import SidecarError, load_sidecar

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DIR_MODE = 0o700
FILE_MODE = 0o600


class SpoolError(ValueError):
    """Spool layout, permissions, or pair integrity failed."""


@dataclass(frozen=True)
class SpoolPair:
    frame_id: str
    png_path: Path
    sidecar_path: Path
    captured_at: datetime
    sidecar: dict[str, Any]


def png_dimensions(path: Path) -> tuple[int, int]:
    """Read width/height from the PNG IHDR. Does not decode pixels."""
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        raise SpoolError(f"{path.name}: not a PNG with IHDR")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise SpoolError(f"{path.name}: invalid PNG dimensions")
    return int(width), int(height)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def chmod_tree(root: Path) -> None:
    """Apply §2.1 spool permissions: directories 0700, files 0600."""
    root.chmod(DIR_MODE)
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(DIR_MODE)
        elif path.is_file():
            path.chmod(FILE_MODE)


def assert_outside_fixture(path: Path, fixture_dir: Path) -> None:
    """Refuse to treat the source fixture (or a parent/child of it) as a spool."""
    target = path.resolve()
    fixture = fixture_dir.resolve()
    if target == fixture:
        raise SpoolError("refusing to use the fixture directory as a spool")
    if fixture in target.parents:
        raise SpoolError("refusing to use a path inside the fixture as a spool")
    if target in fixture.parents:
        raise SpoolError("refusing to use a fixture ancestor as a spool")


def assert_not_fixture_file(path: Path, fixture_dir: Path) -> None:
    """Never unlink a file that lives under the source fixture."""
    target = path.resolve()
    fixture = fixture_dir.resolve()
    if target == fixture or fixture in target.parents:
        raise SpoolError(f"refusing to delete fixture file {path.name}")


def iter_pairs(spool_dir: Path) -> list[SpoolPair]:
    """Consume PNG+sidecar pairs from ``spool_dir/<date>/<frame-id>.{png,json}``."""
    if not spool_dir.is_dir():
        raise SpoolError(f"spool is not a directory: {spool_dir}")
    pairs: list[SpoolPair] = []
    json_paths = sorted(spool_dir.glob("*/*.json"))
    if not json_paths:
        raise SpoolError(f"spool {spool_dir} has no sidecar files")
    for sidecar_path in json_paths:
        png_path = sidecar_path.with_suffix(".png")
        if not png_path.is_file():
            raise SpoolError(f"sidecar without PNG: {sidecar_path.name}")
        try:
            sidecar = load_sidecar(sidecar_path)
        except SidecarError as exc:
            raise SpoolError(f"{sidecar_path.name}: {exc}") from exc
        frame_id = str(sidecar["frame_id"])
        if frame_id != sidecar_path.stem:
            raise SpoolError(f"{sidecar_path.name}: frame_id does not match filename")
        captured_at = datetime.fromisoformat(str(sidecar["captured_at"]).replace("Z", "+00:00"))
        pairs.append(
            SpoolPair(
                frame_id=frame_id,
                png_path=png_path,
                sidecar_path=sidecar_path,
                captured_at=captured_at,
                sidecar=sidecar,
            )
        )
    pairs.sort(key=lambda item: (item.captured_at, item.frame_id))
    return pairs


def copy_pairs_to_spool(
    fixture_dir: Path,
    entries: Iterable[tuple[str, dict[str, Any]]],
    dest: Path,
) -> None:
    """Copy PNG+sidecar pairs into ``dest`` with 0700/0600. Never writes back to fixture."""
    assert_outside_fixture(dest, fixture_dir)
    dest.mkdir(parents=True, exist_ok=True)
    dest.chmod(DIR_MODE)
    for frame_id, entry in entries:
        rel = Path(str(entry["png"]))
        src_png = fixture_dir / rel
        src_json = src_png.with_suffix(".json")
        if not src_png.is_file() or not src_json.is_file():
            raise SpoolError(f"{frame_id}: missing PNG or sidecar in fixture")
        day_dir = dest / rel.parent.name
        day_dir.mkdir(parents=True, exist_ok=True)
        day_dir.chmod(DIR_MODE)
        dst_png = day_dir / f"{frame_id}.png"
        dst_json = day_dir / f"{frame_id}.json"
        shutil.copy2(src_png, dst_png)
        shutil.copy2(src_json, dst_json)
        dst_png.chmod(FILE_MODE)
        dst_json.chmod(FILE_MODE)
    chmod_tree(dest)
