"""Load a directory of PNG frames plus optional OCR sidecars."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Frame:
    frame_id: str
    image_path: Path
    sidecar: dict[str, Any] = field(default_factory=dict)
    ocr_text: str = ""

    @property
    def app_name(self) -> str:
        return str(self.sidecar.get("app_name") or "")

    @property
    def window_title(self) -> str:
        return str(self.sidecar.get("window_title") or "")

    @property
    def bundle_id(self) -> str:
        return str(self.sidecar.get("bundle_id") or "")


def load_frame_id_list(path: Path) -> list[str]:
    """Read a sample50.txt-style list of frame ids (one per line, # comments)."""
    if not path.is_file():
        raise FileNotFoundError(f"frame list not found: {path}")
    ids: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        ids.append(line)
    if not ids:
        raise ValueError(f"no frame ids in {path}")
    return ids


def load_frames(directory: Path, frame_ids: list[str] | None = None) -> list[Frame]:
    if not directory.is_dir():
        raise FileNotFoundError(f"frames directory not found: {directory}")
    pngs = sorted(directory.glob("*.png"))
    if not pngs:
        pngs = sorted(path for path in directory.glob("**/*.png") if path.is_file())
    frames = [_load_one(path) for path in pngs]
    if not frames:
        raise FileNotFoundError(f"no PNG frames in {directory}")
    if frame_ids is None:
        return frames
    by_id = {frame.frame_id: frame for frame in frames}
    missing = [frame_id for frame_id in frame_ids if frame_id not in by_id]
    if missing:
        preview = ", ".join(missing[:8])
        raise FileNotFoundError(f"frame ids not in {directory}: {preview}")
    return [by_id[frame_id] for frame_id in frame_ids]


def _load_one(image_path: Path) -> Frame:
    sidecar: dict[str, Any] = {}
    json_side = image_path.with_suffix(".json")
    txt_side = image_path.with_suffix(".txt")
    if json_side.is_file():
        loaded = json.loads(json_side.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            sidecar = loaded
    elif txt_side.is_file():
        sidecar = {"ocr_text": txt_side.read_text(encoding="utf-8")}
    ocr = _ocr_text(sidecar)
    frame_id = str(sidecar.get("frame_id") or image_path.stem)
    return Frame(frame_id=frame_id, image_path=image_path, sidecar=sidecar, ocr_text=ocr)


def _ocr_text(sidecar: dict[str, Any]) -> str:
    if sidecar.get("ocr_text"):
        return str(sidecar["ocr_text"])
    ocr = sidecar.get("ocr")
    if isinstance(ocr, dict):
        lines = ocr.get("lines")
        if isinstance(lines, list):
            texts: list[str] = []
            for line in lines:
                if isinstance(line, dict) and line.get("text"):
                    texts.append(str(line["text"]))
                elif isinstance(line, str):
                    texts.append(line)
            return "\n".join(texts)
    return ""
