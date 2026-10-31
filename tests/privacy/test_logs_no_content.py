"""Privacy: planted screen content must not appear in captured log output."""

from __future__ import annotations

import io
import logging

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
