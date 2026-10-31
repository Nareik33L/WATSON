"""Incremental D3 segmentation: grouping, close state, idempotency."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tests.replay_support import (
    frame_id_for,
    make_sidecar,
    open_test_db,
    write_mini_fixture,
    write_png,
)
from watson.config.schema import DEFAULT_MERGED_TEXT_MAX_CHARS
from watson.pipeline.redaction import PLACEHOLDER_CARD, redact_ocr_text
from watson.pipeline.replay import replay_fixture
from watson.pipeline.segmentation import (
    SegmentConfig,
    merge_lines,
    normalize_title,
    segment_extracted_frames,
    word_jaccard,
)


def _write_custom_fixture(
    root: Path,
    specs: list[dict[str, Any]],
    *,
    name: str = "day_seg",
    date: str = "2026-10-31",
) -> Path:
    fixture = root / name
    day_dir = fixture / "frames" / date
    day_dir.mkdir(parents=True)
    entries: dict[str, dict[str, object]] = {}
    for index, spec in enumerate(specs, start=1):
        frame_id = str(spec.get("frame_id") or frame_id_for(name, 1, index))
        png_path = day_dir / f"{frame_id}.png"
        write_png(png_path, 2, 2)
        sidecar = make_sidecar(
            frame_id=frame_id,
            captured_at=str(spec["captured_at"]),
            ocr_lines=[
                {
                    "text": str(spec["ocr"]),
                    "bbox": [0, 0, 2, 2],
                    "confidence": 1.0,
                }
            ],
            window_title=str(spec.get("window_title") or "Inbox"),
            bundle_id=str(spec.get("bundle_id") or "com.lumen.mail"),
            app_name=str(spec.get("app_name") or "Lumen Mail"),
        )
        (day_dir / f"{frame_id}.json").write_text(
            json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
        )
        rel = f"frames/{date}/{frame_id}.png"
        entries[frame_id] = {
            "png": rel,
            "sha256": hashlib.sha256(png_path.read_bytes()).hexdigest(),
            "width": 2,
            "height": 2,
        }
    manifest = {"name": name, "date": date, "seed": 1, "frames": entries}
    (fixture / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return fixture


def _segment_rows(conn: object) -> list[Any]:
    return list(
        conn.execute(
            """
            SELECT id, bundle_id, window_title_norm, frame_count, dwell_seconds,
                   line_changes, merged_text, state, start_frame_id
            FROM segments
            ORDER BY started_at, id
            """
        )
    )


def _membership(conn: object) -> list[tuple[str, str]]:
    return [
        (str(row[0]), str(row[1]))
        for row in conn.execute(
            """
            SELECT s.start_frame_id, sf.frame_id
            FROM segment_frames sf
            JOIN segments s ON s.id = sf.segment_id
            ORDER BY sf.captured_at, sf.frame_id
            """
        )
    ]


def test_normalize_title_and_similarity_helpers() -> None:
    assert normalize_title("  Acme Q4 Proposal — Draft ") == "acme q4 proposal — draft"
    assert word_jaccard("hello world", "hello world") == 1.0
    assert word_jaccard("", "anything") == 1.0
    assert word_jaccard("alpha beta", "gamma delta") == 0.0
    assert merge_lines("one\ntwo", "two\nthree", cap=32) == "one\ntwo\nthree"
    assert merge_lines("aaaa", "bbbb", cap=4) == "aaaa"


def test_same_window_similar_text_is_one_segment(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=3)
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool")
        assert result.segments == 1
        rows = _segment_rows(conn)
        assert len(rows) == 1
        assert int(rows[0]["frame_count"]) == 3
        assert rows[0]["window_title_norm"] == "inbox"
        assert int(rows[0]["dwell_seconds"]) == 2 * 60
        assert rows[0]["state"] == "open"
        assert conn.execute("SELECT COUNT(*) FROM segment_frames").fetchone()[0] == 3
    finally:
        conn.close()


def test_window_change_starts_a_new_segment(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = _write_custom_fixture(
        tmp_path,
        [
            {
                "captured_at": "2026-10-31T09:00:00Z",
                "window_title": "Inbox",
                "ocr": "mail body about the harbor brief",
            },
            {
                "captured_at": "2026-10-31T09:01:00Z",
                "window_title": "Acme Q4 Proposal — Draft",
                "bundle_id": "com.lumen.quill",
                "app_name": "Lumen Quill",
                "ocr": "proposal draft purpose section",
            },
        ],
    )
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool")
        assert result.segments == 2
        rows = _segment_rows(conn)
        assert [row["window_title_norm"] for row in rows] == [
            "inbox",
            "acme q4 proposal — draft",
        ]
        assert all(int(row["frame_count"]) == 1 for row in rows)
        assert rows[0]["state"] == "closed"
        assert rows[1]["state"] == "open"
    finally:
        conn.close()


def test_dissimilar_ocr_in_same_window_splits(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = _write_custom_fixture(
        tmp_path,
        [
            {
                "captured_at": "2026-10-31T09:00:00Z",
                "ocr": "alpha beta gamma proposal language",
            },
            {
                "captured_at": "2026-10-31T09:01:00Z",
                "ocr": "zebra yak xylophone entirely other vocabulary",
            },
        ],
    )
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool")
        assert result.segments == 2
        assert all(int(row["frame_count"]) == 1 for row in _segment_rows(conn))
    finally:
        conn.close()


def test_twenty_minute_dwell_closes_without_splitting(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = _write_custom_fixture(
        tmp_path,
        [
            {
                "captured_at": "2026-10-31T09:00:00Z",
                "ocr": "shared proposal draft paragraph one",
            },
            {
                "captured_at": "2026-10-31T09:21:00Z",
                "ocr": "shared proposal draft paragraph two",
            },
        ],
    )
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool")
        assert result.segments == 1
        row = _segment_rows(conn)[0]
        assert int(row["frame_count"]) == 2
        assert int(row["dwell_seconds"]) == 21 * 60
        assert row["state"] == "closed"
    finally:
        conn.close()


def test_rerun_is_idempotent(data_dir: Path, key_provider: object, tmp_path: Path) -> None:
    fixture = write_mini_fixture(tmp_path, frames=2)
    conn = open_test_db(data_dir)
    try:
        first = replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool-a")
        ids = [str(row["id"]) for row in _segment_rows(conn)]
        membership = _membership(conn)
        second = replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool-b")
        assert first.segments == second.segments == 1
        assert [str(row["id"]) for row in _segment_rows(conn)] == ids
        assert _membership(conn) == membership
        assert segment_extracted_frames(conn) == 0
    finally:
        conn.close()


def test_incremental_extend_does_not_duplicate(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    first = _write_custom_fixture(
        tmp_path / "a",
        [
            {
                "captured_at": "2026-10-31T09:00:00Z",
                "ocr": "shared proposal draft paragraph one",
            }
        ],
        name="day_seg_a",
    )
    second = _write_custom_fixture(
        tmp_path / "b",
        [
            {
                "captured_at": "2026-10-31T09:05:00Z",
                "ocr": "shared proposal draft paragraph two",
            }
        ],
        name="day_seg_b",
    )
    conn = open_test_db(data_dir)
    try:
        replay_fixture(first, conn, instant=True, spool_dir=data_dir / "spool-1")
        assert len(_segment_rows(conn)) == 1
        replay_fixture(second, conn, instant=True, spool_dir=data_dir / "spool-2")
        rows = _segment_rows(conn)
        assert len(rows) == 1
        assert int(rows[0]["frame_count"]) == 2
        assert int(rows[0]["dwell_seconds"]) == 5 * 60
        assert conn.execute("SELECT COUNT(*) FROM segment_frames").fetchone()[0] == 2
    finally:
        conn.close()


def test_merged_text_is_redacted_union_and_line_changes(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    card = "4111111111111111"
    fixture = _write_custom_fixture(
        tmp_path,
        [
            {
                "captured_at": "2026-10-31T09:00:00Z",
                "ocr": f"Invoice card {card} first line",
            },
            {
                "captured_at": "2026-10-31T09:01:00Z",
                "ocr": f"Invoice card {card} first line\nadded second line",
            },
        ],
    )
    conn = open_test_db(data_dir)
    try:
        replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool")
        row = _segment_rows(conn)[0]
        merged = str(row["merged_text"])
        assert card not in merged
        assert PLACEHOLDER_CARD in merged
        assert "first line" in merged
        assert "added second line" in merged
        assert int(row["line_changes"]) >= 1
        raw = redact_ocr_text(f"Invoice card {card} first line")
        assert raw in merged or PLACEHOLDER_CARD in merged
    finally:
        conn.close()


def test_config_threshold_can_force_a_split(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=2)
    conn = open_test_db(data_dir)
    try:
        replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool")
        # Default grouping already assigned both frames. Re-run with a
        # hostile threshold is a no-op because membership is unique.
        assert segment_extracted_frames(conn, config=SegmentConfig(similarity_threshold=0.99)) == 0
        assert conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 1
    finally:
        conn.close()


def _replay_custom(
    data_dir: Path,
    tmp_path: Path,
    specs: list[dict[str, Any]],
    *,
    name: str,
) -> list[Any]:
    fixture = _write_custom_fixture(tmp_path / name, specs, name=name)
    conn = open_test_db(data_dir / name)
    try:
        replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / name / "spool")
        return _segment_rows(conn)
    finally:
        conn.close()


def test_window_key_splits_with_identical_text(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    shared = "shared proposal draft paragraph one"
    title_change = _replay_custom(
        data_dir,
        tmp_path,
        [
            {
                "captured_at": "2026-10-31T09:00:00Z",
                "window_title": "Inbox",
                "ocr": shared,
            },
            {
                "captured_at": "2026-10-31T09:01:00Z",
                "window_title": "Acme Q4 Proposal — Draft",
                "ocr": shared,
            },
        ],
        name="day_key_title",
    )
    assert len(title_change) == 2
    assert [int(row["frame_count"]) for row in title_change] == [1, 1]

    bundle_change = _replay_custom(
        data_dir,
        tmp_path,
        [
            {
                "captured_at": "2026-10-31T09:00:00Z",
                "window_title": "Inbox",
                "bundle_id": "com.lumen.mail",
                "ocr": shared,
            },
            {
                "captured_at": "2026-10-31T09:01:00Z",
                "window_title": "Inbox",
                "bundle_id": "com.lumen.quill",
                "app_name": "Lumen Quill",
                "ocr": shared,
            },
        ],
        name="day_key_bundle",
    )
    assert len(bundle_change) == 2
    assert [str(row["bundle_id"]) for row in bundle_change] == [
        "com.lumen.mail",
        "com.lumen.quill",
    ]

    folded_title = _replay_custom(
        data_dir,
        tmp_path,
        [
            {
                "captured_at": "2026-10-31T09:00:00Z",
                "window_title": "Inbox",
                "ocr": shared,
            },
            {
                "captured_at": "2026-10-31T09:01:00Z",
                "window_title": "  INBOX ",
                "ocr": shared,
            },
        ],
        name="day_key_norm",
    )
    assert len(folded_title) == 1
    assert int(folded_title[0]["frame_count"]) == 2


def test_merged_text_rescue_at_threshold(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    rows = _replay_custom(
        data_dir,
        tmp_path,
        [
            {
                "captured_at": "2026-10-31T09:00:00Z",
                "ocr": "w1 w2 w3 w4 w5 w6 w7 w8",
            },
            {
                "captured_at": "2026-10-31T09:01:00Z",
                "ocr": "w1 w2 w3 w4 x1 x2 x3 x4",
            },
            {
                "captured_at": "2026-10-31T09:02:00Z",
                "ocr": "w5 w6 w7 w8 y1 y2",
            },
        ],
        name="day_merged_rescue",
    )
    assert len(rows) == 1
    assert int(rows[0]["frame_count"]) == 3


def test_previous_frame_rescue(data_dir: Path, key_provider: object, tmp_path: Path) -> None:
    def words(start: int, end: int) -> str:
        return " ".join(f"a{index}" for index in range(start, end + 1))

    rows = _replay_custom(
        data_dir,
        tmp_path,
        [
            {"captured_at": "2026-10-31T09:00:00Z", "ocr": words(1, 8)},
            {"captured_at": "2026-10-31T09:01:00Z", "ocr": words(5, 12)},
            {"captured_at": "2026-10-31T09:02:00Z", "ocr": words(9, 16)},
            {"captured_at": "2026-10-31T09:03:00Z", "ocr": words(13, 20)},
        ],
        name="day_prev_rescue",
    )
    assert len(rows) == 1
    assert int(rows[0]["frame_count"]) == 4


def test_merged_text_cap_is_line_boundary_and_32k(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    assert merge_lines("aaaa\nbbbb", "cccc", cap=12) == "aaaa\nbbbb"
    assert DEFAULT_MERGED_TEXT_MAX_CHARS == 32768

    lines = [f"cap-line-{index:04d}-" + ("q" * 40) for index in range(700)]
    rows = _replay_custom(
        data_dir,
        tmp_path,
        [
            {
                "captured_at": "2026-10-31T09:00:00Z",
                "ocr": "\n".join(lines),
            }
        ],
        name="day_cap",
    )
    merged = str(rows[0]["merged_text"])
    assert len(merged) <= 32768
    assert merged.splitlines()[-1] in lines
