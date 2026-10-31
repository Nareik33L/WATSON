"""Dashboard output scanner (ContentField idea applied to responses)."""

from __future__ import annotations

from watson.privacy.contentscan import content_field_tokens, scan_output_source, scan_text
from watson.privacy.logging import ContentField


def test_scan_flags_raw_content_fields_but_allows_wrap() -> None:
    assert scan_output_source("page = frame_text") == ["<mem>:1 uses raw frame_text"]
    assert scan_output_source("log = row.merged_text") == ["<mem>:1 uses raw merged_text"]
    snippet_hits = scan_output_source('body = {"snippet": snippet}')
    assert any("snippet" in item for item in snippet_hits)
    assert scan_output_source("safe = ContentField(frame_text)") == []
    assert ContentField("x").redacted().startswith("content[")


def test_token_scan_finds_response_fields() -> None:
    assert content_field_tokens("queue depth 3") == []
    assert content_field_tokens('{"frame_text": "x", "ocr": []}') == ["frame_text", "ocr"]
    assert scan_text("no content here") == []
    assert scan_text("text_redacted leaked") == ["<mem>: content field text_redacted"]
