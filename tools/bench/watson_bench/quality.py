"""Per-frame outputs and a blank hand-scoring sheet (up to 50 sampled frames)."""

from __future__ import annotations

import csv
import json
import random
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from watson_bench.frames import Frame

SCORE_COLUMNS = (
    "frame_id",
    "candidate_id",
    "role",
    "visible_ok",
    "hallucination",
    "useful_for_work_memory",
    "notes",
)


def sample_frames(frames: list[Frame], limit: int = 50, seed: int = 0) -> list[Frame]:
    if limit <= 0 or len(frames) <= limit:
        return list(frames)
    rng = random.Random(seed)
    return rng.sample(frames, limit)


def write_frame_output(
    directory: Path, frame_id: str, candidate_id: str, payload: dict[str, Any]
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{frame_id}__{candidate_id}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_scoring_csv(
    path: Path,
    *,
    frames: Iterable[Frame],
    candidate_id: str,
    role: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.is_file()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCORE_COLUMNS)
        if new_file:
            writer.writeheader()
        for frame in frames:
            writer.writerow(
                {
                    "frame_id": frame.frame_id,
                    "candidate_id": candidate_id,
                    "role": role,
                    "visible_ok": "",
                    "hallucination": "",
                    "useful_for_work_memory": "",
                    "notes": "",
                }
            )
