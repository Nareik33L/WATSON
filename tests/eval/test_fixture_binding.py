from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from watson_fixturegen.phash import average_hash_hex

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"


def _days() -> list[Path]:
    return sorted(
        path
        for path in FIXTURES.iterdir()
        if path.is_dir() and (path / "manifest.json").is_file()
    )


def test_pngs_match_manifest_sidecars_and_phash() -> None:
    days = _days()
    assert days, "expected at least one fixture day with manifest.json"
    for day in days:
        manifest = json.loads((day / "manifest.json").read_text(encoding="utf-8"))
        frames = manifest["frames"]
        pngs = {path.stem: path for path in (day / "frames").glob("*/*.png")}
        sidecars = {path.stem: path for path in (day / "frames").glob("*/*.json")}
        assert set(pngs) == set(sidecars) == set(frames), (
            f"{day.name}: orphan PNG or sidecar vs manifest "
            f"png={set(pngs) - set(frames)} sidecar={set(sidecars) - set(frames)} "
            f"manifest={set(frames) - set(pngs)}"
        )
        for frame_id, entry in frames.items():
            png_path = day / entry["png"]
            assert png_path == pngs[frame_id]
            digest = hashlib.sha256(png_path.read_bytes()).hexdigest()
            assert digest == entry["sha256"], f"{day.name}/{frame_id} sha256 mismatch"
            with Image.open(png_path) as image:
                width, height = image.size
                rgb = image.convert("RGB")
                recomputed = average_hash_hex(rgb)
            sidecar = json.loads(sidecars[frame_id].read_text(encoding="utf-8"))
            assert (width, height) == (entry["width"], entry["height"])
            assert (width, height) == (sidecar["width"], sidecar["height"])
            assert recomputed == sidecar["phash"], f"{day.name}/{frame_id} phash mismatch"
