"""Replay deal gold and V1 overlays; score output; after-replay privacy bait."""

from __future__ import annotations

import io
import logging
import uuid
from pathlib import Path

from tests.replay_support import open_test_db, write_deal_day_fixture
from watson.intelligence.eval.deals import (
    collect_predicted_deals,
    load_deal_corpus,
    load_deal_day,
    load_v1_deal_corpus,
    privacy_leak,
    score_deals,
)
from watson.pipeline.replay import replay_fixture
from watson.privacy.logging import configure_logging

REPO = Path(__file__).resolve().parents[2]
DEAL_GOLD = REPO / "tests" / "fixtures" / "deal_gold"
FIXTURES = REPO / "tests" / "fixtures"
BAIT_MARKERS = ("privacybait", "hunter2")


def test_deal_gold_ids_are_replay_uuids() -> None:
    corpus = load_deal_corpus(DEAL_GOLD)
    for day in corpus.days:
        for segment in day.segments:
            uuid.UUID(segment.id)
            for frame_id in segment.frame_ids:
                assert uuid.UUID(frame_id).version == 5


def test_replay_deal_gold_and_score(data_dir: Path, key_provider: object, tmp_path: Path) -> None:
    from watson.intelligence.eval.deals import DealCorpus, PredictedCorpus

    gold_day = load_deal_day(DEAL_GOLD / "day_focused")
    fixture = write_deal_day_fixture(gold_day, tmp_path / "day_focused")
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(
            fixture, conn, instant=True, spool_dir=data_dir / "spool-day-focused"
        )
        assert result.ingested >= 1
        predicted = collect_predicted_deals(conn, gold_day.name)
        gold_frames = {
            frame_id
            for segment in gold_day.segments
            if not segment.privacy_bait
            for frame_id in segment.frame_ids
        }
        pred_frames = {frame_id for segment in predicted.segments for frame_id in segment.frame_ids}
        assert gold_frames <= pred_frames
        score = score_deals(DealCorpus(days=[gold_day]), PredictedCorpus(days=[predicted]))
        assert score.grouping.segments == len(
            [segment for segment in gold_day.segments if not segment.privacy_bait]
        )
        assert score.grouping.deal_segment_recall == 0.0
        assert not score.privacy_fail
    finally:
        conn.close()


def test_replay_v1_overlay_and_score(data_dir: Path, key_provider: object) -> None:
    from watson.intelligence.eval.deals import DealCorpus, PredictedCorpus

    overlays = load_v1_deal_corpus(FIXTURES)
    day = next(item for item in overlays.days if item.name == "day_acme_q4")
    conn = open_test_db(data_dir)
    try:
        replay_fixture(
            FIXTURES / "day_acme_q4",
            conn,
            instant=True,
            spool_dir=data_dir / "spool-v1-acme",
        )
        predicted = collect_predicted_deals(conn, day.name)
        gold_frames = {
            frame_id
            for segment in day.segments
            if not segment.privacy_bait
            for frame_id in segment.frame_ids
        }
        pred_frames = {frame_id for segment in predicted.segments for frame_id in segment.frame_ids}
        assert gold_frames <= pred_frames
        score = score_deals(DealCorpus(days=[day]), PredictedCorpus(days=[predicted]))
        assert score.grouping.segments == len(
            [segment for segment in day.segments if not segment.privacy_bait]
        )
        assert not score.privacy_fail
    finally:
        conn.close()


def test_after_replay_privacy_bait_absent(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    from watson.intelligence.eval.deals import DealCorpus, PredictedCorpus

    gold_day = load_deal_day(DEAL_GOLD / "day_sensitive")
    fixture = write_deal_day_fixture(gold_day, tmp_path / "day_sensitive")
    buf = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=buf)
    conn = open_test_db(data_dir)
    try:
        replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool-sensitive")
        predicted = collect_predicted_deals(conn, gold_day.name)
        gold = DealCorpus(days=[gold_day])
        pred_corpus = PredictedCorpus(days=[predicted])
        score = score_deals(gold, pred_corpus)
        assert not score.privacy_fail
        assert not privacy_leak(gold, pred_corpus)
        blob = "\n".join(predicted.stored_texts)
        logs = buf.getvalue()
        for marker in BAIT_MARKERS:
            assert marker not in blob.casefold()
            assert marker not in logs.casefold()
            for claim in predicted.claims:
                assert marker not in claim.text.casefold()
        bait_frames = {
            frame_id
            for segment in gold_day.segments
            if segment.privacy_bait
            for frame_id in segment.frame_ids
        }
        stored_frames = {str(row[0]) for row in conn.execute("SELECT id FROM frames")}
        assert stored_frames.isdisjoint(bait_frames)
        text_frames = {str(row[0]) for row in conn.execute("SELECT frame_id FROM frame_text")}
        assert text_frames.isdisjoint(bait_frames)
    finally:
        conn.close()


def test_after_replay_v1_traps_have_no_vault_bait(data_dir: Path, key_provider: object) -> None:
    from watson.intelligence.eval.deals import DealCorpus, PredictedCorpus

    overlays = load_v1_deal_corpus(FIXTURES)
    day = next(item for item in overlays.days if item.name == "day_sensitive_traps")
    buf = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=buf)
    conn = open_test_db(data_dir)
    try:
        replay_fixture(
            FIXTURES / "day_sensitive_traps",
            conn,
            instant=True,
            spool_dir=data_dir / "spool-v1-traps",
        )
        predicted = collect_predicted_deals(conn, day.name)
        gold = DealCorpus(days=[day])
        pred_corpus = PredictedCorpus(days=[predicted])
        assert not privacy_leak(gold, pred_corpus)
        bait_frames = {
            frame_id
            for segment in day.segments
            if segment.privacy_bait
            for frame_id in segment.frame_ids
        }
        assert bait_frames
        stored = {str(row[0]) for row in conn.execute("SELECT id FROM frames")}
        assert stored.isdisjoint(bait_frames)
        blob = "\n".join(predicted.stored_texts)
        logs = buf.getvalue()
        for marker in ("privacybait", "Personal vault", "hunter2"):
            assert marker not in blob
            assert marker not in logs
    finally:
        conn.close()
