"""M2b eval: ≤ 5% over-segmentation on all five V1 day fixtures, plus §11.3 traps."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.replay_support import V1_DAY_FIXTURES, open_test_db
from watson.capture.exclusions import exclusion_reason
from watson.config.schema import ExclusionsConfig
from watson.pipeline.redaction import PLACEHOLDER_CARD, PLACEHOLDER_OTP, PLACEHOLDER_SECRET
from watson.pipeline.replay import replay_fixture
from watson.pipeline.segeval import score_segmentation

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
OVER_SEG_MAX = 0.05
PLANTED_CARD = "4111111111111111"
PLANTED_OTP = "482913"
PLANTED_PASSWORD = "cedar-trap-pass-9f3"
PLANTED_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"


def _truth_frame_ids(fixture: Path) -> list[list[str]]:
    truth = yaml.safe_load((fixture / "truth.yaml").read_text(encoding="utf-8"))
    return [list(segment["frame_ids"]) for segment in truth["expected_segments"]]


def _predicted_frame_ids(conn: object) -> list[list[str]]:
    segments = conn.execute(
        "SELECT id FROM segments ORDER BY started_at, id"
    ).fetchall()
    predicted: list[list[str]] = []
    for row in segments:
        frames = conn.execute(
            """
            SELECT frame_id FROM segment_frames
            WHERE segment_id = ?
            ORDER BY captured_at, frame_id
            """,
            (row[0],),
        ).fetchall()
        predicted.append([str(item[0]) for item in frames])
    return predicted


@pytest.mark.parametrize("name", V1_DAY_FIXTURES)
def test_fixture_over_segmentation_at_most_five_percent(
    data_dir: Path, key_provider: object, name: str
) -> None:
    fixture = FIXTURES / name
    truth_ids = _truth_frame_ids(fixture)
    assert truth_ids, f"{name}: truth.yaml is missing expected_segments"
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(
            fixture, conn, instant=True, spool_dir=data_dir / f"spool-{name}"
        )
        predicted = _predicted_frame_ids(conn)
        score = score_segmentation(predicted, truth_ids)
        assert result.segments == score["n_pred"]
        assert score["coverage"] == 1.0, f"{name}: missing truth frames {score}"
        assert score["over_segmentation"] <= OVER_SEG_MAX, (
            f"{name}: over-segmentation {score['over_segmentation']:.3f} "
            f"exceeds {OVER_SEG_MAX} ({score})"
        )
    finally:
        conn.close()


def test_sensitive_traps_exclude_and_redact(
    data_dir: Path, key_provider: object
) -> None:
    fixture = FIXTURES / "day_sensitive_traps"
    truth = yaml.safe_load((fixture / "truth.yaml").read_text(encoding="utf-8"))
    skipped_ids = {row["frame_id"] for row in truth["skipped"]}
    assert skipped_ids
    rules = ExclusionsConfig()

    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(
            fixture, conn, instant=True, spool_dir=data_dir / "spool-traps"
        )
        assert result.skipped_excluded == len(skipped_ids)

        stored = {
            str(row[0])
            for row in conn.execute("SELECT id FROM frames").fetchall()
        }
        assert stored.isdisjoint(skipped_ids)

        text_ids = {
            str(row[0])
            for row in conn.execute("SELECT frame_id FROM frame_text").fetchall()
        }
        assert text_ids.isdisjoint(skipped_ids)

        rows = conn.execute(
            """
            SELECT f.id, f.bundle_id, f.window_title, t.text_redacted
            FROM frames f
            JOIN frame_text t ON t.frame_id = f.id
            """
        ).fetchall()
        assert rows
        for row in rows:
            sidecar = {
                "bundle_id": row["bundle_id"],
                "app_name": "",
                "window_title": row["window_title"],
                "is_private_browsing": False,
            }
            assert exclusion_reason(sidecar, rules) is None
            text = str(row["text_redacted"])
            assert PLANTED_CARD not in text
            assert PLANTED_OTP not in text
            assert PLANTED_PASSWORD not in text
            assert PLANTED_KEY not in text

        blob = "\n".join(str(row["text_redacted"]) for row in rows)
        assert PLACEHOLDER_CARD in blob
        assert PLACEHOLDER_SECRET in blob
        assert PLACEHOLDER_OTP in blob
    finally:
        conn.close()
