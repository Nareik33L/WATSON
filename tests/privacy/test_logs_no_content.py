"""Privacy: planted screen content must not appear in captured log output."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from tests.replay_support import (
    DAY_ACME_Q4,
    TEST_KEY,
    distinctive_ocr_texts,
    open_test_db,
    write_mini_fixture,
)
from watson.cli import main
from watson.intelligence.runtime.fake import FakeRuntime
from watson.pipeline.replay import replay_fixture
from watson.pipeline.segmentation import close_open_segments
from watson.pipeline.understanding import drain_understand_jobs, enqueue_eligible_understand_jobs
from watson.privacy.logging import ContentField, configure_logging

PLANTED = "PLANTED_SCREEN_CONTENT_UNIQUE_42_DO_NOT_EMIT"


def test_captured_logs_do_not_contain_planted_content() -> None:
    buf = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=buf)
    log = logging.getLogger("watson.privacytest")
    log.debug("frame %s", ContentField(PLANTED))
    log.info("merged %s", ContentField(PLANTED))
    log.warning("snippet %s", ContentField(PLANTED))
    output = buf.getvalue()
    assert PLANTED not in output
    assert "len=" in output
    assert "hash=" in output
    assert output.count(PLANTED) == 0


def test_replay_run_output_has_no_fixture_ocr(
    data_dir: Path,
    key_provider: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    planted = "PLANTED_REPLAY_OCR_MUST_NEVER_APPEAR_IN_LOGS"
    fixture = write_mini_fixture(tmp_path, frames=2, planted=planted)
    buf = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=buf)
    assert main(["replay", str(fixture), "--instant"]) == 0
    captured = capsys.readouterr()
    combined = buf.getvalue() + captured.out + captured.err
    assert planted not in combined
    assert TEST_KEY.hex() not in combined

    db_bytes = (data_dir / "watson.db").read_bytes()
    assert planted.encode("utf-8") not in db_bytes
    assert not db_bytes.startswith(b"SQLite format 3")


def test_day_acme_q4_replay_logs_have_no_ocr(
    data_dir: Path,
    key_provider: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    phrases = distinctive_ocr_texts(DAY_ACME_Q4, min_len=32)
    assert phrases
    buf = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=buf)
    assert main(["replay", str(DAY_ACME_Q4), "--instant"]) == 0
    captured = capsys.readouterr()
    combined = buf.getvalue() + captured.out + captured.err
    for phrase in phrases:
        assert phrase not in combined
    db_bytes = (data_dir / "watson.db").read_bytes()
    for phrase in phrases:
        assert phrase.encode("utf-8") not in db_bytes


def test_understand_warning_logs_have_no_ocr(
    data_dir: Path,
    key_provider: object,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """P2: drain_understand_jobs (not replay --instant) must not log OCR at WARNING+."""
    planted = "visible ocr line from the calendar app window"
    fixture = write_mini_fixture(tmp_path, frames=2, planted=planted)
    buf = io.StringIO()
    configure_logging(level=logging.WARNING, stream=buf)
    conn = open_test_db(data_dir)
    try:
        replay_fixture(fixture, conn, instant=True, spool_dir=tmp_path / "spool")
        close_open_segments(conn)
        enqueue_eligible_understand_jobs(conn)
        runtime = FakeRuntime()
        with caplog.at_level(logging.WARNING, logger="watson"):
            assert drain_understand_jobs(conn, runtime=runtime, embedding=runtime) >= 1
        combined = buf.getvalue() + caplog.text
        assert planted not in combined
        for record in caplog.records:
            if record.levelno < logging.WARNING:
                continue
            assert planted not in record.getMessage()
            assert planted not in str(record.__dict__)
    finally:
        conn.close()
