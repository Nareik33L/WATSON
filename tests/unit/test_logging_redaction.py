"""ContentField redaction and AST guard against raw screen-content logs."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest

from watson.privacy.logging import ContentField, configure_logging
from watson.privacy.logscan import scan_path, scan_paths, scan_source

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
FIXTURES = Path(__file__).resolve().parent / "logging_scan_fixtures"
PATTERNS = ("subscript", "format", "fstring", "str_call", "percent", "concat", "tuple_args")


def test_content_field_stringifies_to_length_and_hash() -> None:
    field = ContentField("hello screen")
    text = str(field)
    assert "hello screen" not in text
    assert "len=12" in text
    assert "hash=" in text
    assert "hello screen" not in repr(field)


def test_json_formatter_redacts_at_debug() -> None:
    buf = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=buf)
    log = logging.getLogger("watson.redact")
    planted = "VISIBLE_ON_SCREEN_xyz"
    log.debug("extracted %s", ContentField(planted))
    log.info("note", extra={"body": ContentField(planted)})
    output = buf.getvalue()
    assert planted not in output
    lines = [json.loads(line) for line in output.strip().splitlines()]
    assert any("len=" in line["msg"] and "hash=" in line["msg"] for line in lines)
    assert any("len=" in json.dumps(line.get("extra", {})) for line in lines)


def test_src_loggers_do_not_pass_raw_screen_fields() -> None:
    violations = scan_paths(SRC_ROOT.rglob("*.py"))
    assert violations == []


@pytest.mark.parametrize("pattern", PATTERNS)
def test_scan_flags_positive_fixture(pattern: str) -> None:
    path = FIXTURES / "positive" / f"{pattern}.py"
    violations = scan_path(path)
    assert violations, f"expected {pattern} to be flagged"
    assert any("passes raw" in item for item in violations)


@pytest.mark.parametrize("pattern", PATTERNS)
def test_scan_allows_negative_fixture(pattern: str) -> None:
    path = FIXTURES / "negative" / f"{pattern}.py"
    assert scan_path(path) == []


def test_scan_source_covers_each_pattern_inline() -> None:
    positives = {
        "subscript": 'log.info(row["frame_text"])',
        "format": 'log.info("{}".format(frame_text))',
        "fstring": 'log.info(f"{merged_text}")',
        "str": "log.info(str(snippet))",
        "percent": 'log.info("%s" % frame_text)',
        "concat": 'log.info("x" + seg.merged_text)',
        "tuple": 'log.info("%s", (frame_text,))',
        "positional": "log.debug(frame_text)",
    }
    negatives = {
        "subscript": 'log.info(row["id"])',
        "format": 'log.info("{}".format(other))',
        "fstring": 'log.info(f"{other}")',
        "str": "log.info(str(other))",
        "percent": 'log.info("%s" % other)',
        "concat": 'log.info("x" + seg.app_name)',
        "tuple": 'log.info("%s", (other,))',
        "positional": "log.debug(other)",
        "contentfield": "log.info(ContentField(frame_text))",
        "literal": 'log.info("frame_text")',
    }
    for name, src in positives.items():
        assert scan_source(src, filename=name), f"positive {name} should flag"
    for name, src in negatives.items():
        assert scan_source(src, filename=name) == [], f"negative {name} should pass"
