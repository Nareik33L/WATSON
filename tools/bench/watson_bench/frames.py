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


def load_frames(directory: Path) -> list[Frame]:
    if not directory.is_dir():
        raise FileNotFoundError(f"frames directory not found: {directory}")
    frames = [_load_one(path) for path in sorted(directory.glob("*.png"))]
    if not frames:
        raise FileNotFoundError(f"no PNG frames in {directory}")
    return frames


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
