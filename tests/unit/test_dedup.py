"""pHash/dHash of spool PNGs; near-duplicate decisions stay content-free."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from tests.replay_support import open_test_db, write_mini_fixture, write_png
from watson.capture.spool import copy_pairs_to_spool, iter_pairs
from watson.pipeline.dedup import (
    HASH_HEX_LEN,
    DedupError,
    decide_duplicate,
    difference_hash_hex,
    hash_png,
    perceptual_hash_hex,
)
from watson.pipeline.frames import ensure_working_day, extract_fixture_stub, ingest_pair
from watson.pipeline.replay import load_and_validate_manifest, replay_fixture
from watson.privacy.audit import KIND_DEDUPED, KIND_DELETED, KIND_VERIFIED


def _solid(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (64, 48)) -> None:
    Image.new("RGB", size, color).save(path, format="PNG")


def _pattern(path: Path, kind: str, size: tuple[int, int] = (64, 48)) -> None:
    """Structured images so pHash/dHash can tell them apart (flat fills collide)."""
    image = Image.new("RGB", size, (240, 240, 244))
    if kind == "left":
        image.paste((20, 40, 120), (2, 2, size[0] // 2, size[1] - 2))
    elif kind == "right":
        image.paste((200, 40, 20), (size[0] // 2, 2, size[0] - 2, size[1] - 2))
    else:
        image.paste((20, 180, 40), (8, 8, size[0] - 8, size[1] // 3))
    image.save(path, format="PNG")


def test_hashes_are_deterministic_64_bit() -> None:
    image = Image.new("RGB", (64, 48), (240, 240, 244))
    image.paste((30, 80, 140), (8, 8, 40, 40))
    phash = perceptual_hash_hex(image)
    dhash = difference_hash_hex(image)
    assert phash == perceptual_hash_hex(image.copy())
    assert dhash == difference_hash_hex(image.copy())
    assert len(phash) == HASH_HEX_LEN
    assert len(dhash) == HASH_HEX_LEN
    int(phash, 16)
    int(dhash, 16)


def test_identical_images_distance_zero_and_near_duplicate(tmp_path: Path) -> None:
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    _solid(first, (20, 40, 80))
    _solid(second, (20, 40, 80))
    left = hash_png(first)
    right = hash_png(second)
    assert left.phash == right.phash
    assert left.dhash == right.dhash
    decision = decide_duplicate(
        left,
        right,
        frame_id="11111111-1111-4111-8111-111111111111",
        previous_frame_id="22222222-2222-4222-8222-222222222222",
        threshold=4,
    )
    assert decision.duplicate is True
    assert decision.distance == 0
    assert decision.threshold == 4


def test_different_images_are_not_near_duplicates(tmp_path: Path) -> None:
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    _pattern(first, "left")
    _pattern(second, "right")
    decision = decide_duplicate(
        hash_png(first),
        hash_png(second),
        frame_id="11111111-1111-4111-8111-111111111111",
        previous_frame_id="22222222-2222-4222-8222-222222222222",
        threshold=4,
    )
    assert decision.distance > 4
    assert decision.duplicate is False


def test_threshold_is_configurable(tmp_path: Path) -> None:
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    _solid(first, (10, 10, 10), size=(32, 32))
    Image.new("RGB", (32, 32), (10, 10, 10)).save(second, format="PNG")
    # One bright pixel changes the downscaled sample only slightly — or not at
    # all. Force a known pair of hashes and vary the threshold.
    left = hash_png(first)
    right = hash_png(second)
    far = decide_duplicate(
        left,
        right,
        frame_id="11111111-1111-4111-8111-111111111111",
        previous_frame_id="22222222-2222-4222-8222-222222222222",
        threshold=0,
    )
    # Identical solids: still a duplicate at threshold 0.
    assert far.duplicate is True
    assert far.distance == 0


def test_hash_is_not_the_image_and_cannot_reconstruct(tmp_path: Path) -> None:
    path = tmp_path / "frame.png"
    image = Image.new("RGB", (128, 96), (12, 64, 200))
    image.putpixel((3, 5), (255, 0, 0))
    image.save(path, format="PNG")
    png_bytes = path.read_bytes()
    hashes = hash_png(path)
    packed = bytes.fromhex(hashes.phash + hashes.dhash)
    assert len(packed) == 16  # two 64-bit hashes
    assert len(png_bytes) > 200
    assert packed not in png_bytes
    assert hashes.phash.encode() not in png_bytes
    # 128 bits cannot invert 128×96×24 bits of colour.
    assert len(packed) * 8 < image.size[0] * image.size[1] * 8


def test_bad_file_is_dedup_error(tmp_path: Path) -> None:
    path = tmp_path / "nope.png"
    path.write_bytes(b"not-a-png")
    with pytest.raises(DedupError):
        hash_png(path)


def test_replay_dedupes_identical_consecutive_pngs_and_still_deletes(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=2)
    spool = data_dir / "spool-dedup"
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(fixture, conn, instant=True, spool_dir=spool)
        assert result.ingested == 2
        assert result.extracted == 2
        assert result.deleted == 2
        assert result.deletion_verified == 2
        assert result.deduped == 1
        assert list(spool.rglob("*.png")) == []

        rows = conn.execute(
            "SELECT id, dhash, duplicate_of, duplicate_count, deletion_verified "
            "FROM frames ORDER BY captured_at"
        ).fetchall()
        assert rows[0]["duplicate_of"] is None
        assert int(rows[0]["duplicate_count"]) == 1
        assert rows[1]["duplicate_of"] == rows[0]["id"]
        assert rows[0]["dhash"] and len(str(rows[0]["dhash"])) == HASH_HEX_LEN
        assert all(int(row["deletion_verified"]) == 1 for row in rows)

        details = [
            str(row[0])
            for row in conn.execute(
                "SELECT detail_json FROM audit_events WHERE kind = ?", (KIND_DEDUPED,)
            ).fetchall()
        ]
        assert len(details) == 1
        assert '"distance": 0' in details[0]
        assert '"threshold": 4' in details[0]
        assert "ocr" not in details[0]
        assert "text" not in details[0]
        assert "window_title" not in details[0]
        assert "phash" not in details[0]
        assert "dhash" not in details[0]
    finally:
        conn.close()


def test_distinct_pngs_are_not_deduped(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=2)
    pngs = sorted(fixture.glob("frames/*/*.png"))
    _pattern(pngs[0], "left", size=(64, 48))
    _pattern(pngs[1], "right", size=(64, 48))
    # Manifest sha256 still matches the original 2×2 write_png — rewrite manifest.
    manifest_path = fixture / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for frame_id, entry in manifest["frames"].items():
        png_path = fixture / str(entry["png"])
        blob = png_path.read_bytes()
        entry["sha256"] = hashlib.sha256(blob).hexdigest()
        with Image.open(png_path) as image:
            entry["width"], entry["height"] = image.size
        sidecar_path = png_path.with_suffix(".json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar["width"], sidecar["height"] = entry["width"], entry["height"]
        sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool")
        assert result.deduped == 0
        assert result.deletion_verified == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_DEDUPED,)
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_extract_hashes_before_delete(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=1)
    write_png(next(fixture.glob("frames/*/*.png")))
    spool = tmp_path / "spool"
    conn = open_test_db(data_dir)
    try:
        manifest = load_and_validate_manifest(fixture)
        frames_map = manifest["frames"]
        assert isinstance(frames_map, dict)
        copy_pairs_to_spool(fixture, frames_map.items(), spool)
        pair = iter_pairs(spool)[0]
        day_id = ensure_working_day(conn, str(manifest["date"]))
        ingest_pair(conn, pair, working_day_id=day_id)
        extract_fixture_stub(
            conn,
            {
                "frame_id": pair.frame_id,
                "png_path": str(pair.png_path),
                "sidecar_path": str(pair.sidecar_path),
                "fixture_only": True,
            },
            fixture_dir=fixture,
        )
        assert not pair.png_path.exists()
        row = conn.execute(
            "SELECT phash, dhash, deletion_verified FROM frames WHERE id = ?",
            (pair.frame_id,),
        ).fetchone()
        assert row is not None
        assert row["phash"] and row["dhash"]
        assert int(row["deletion_verified"]) == 1
        deleted = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_DELETED,)
        ).fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_VERIFIED,)
        ).fetchone()[0]
        assert deleted == 1
        assert verified == 1
    finally:
        conn.close()
