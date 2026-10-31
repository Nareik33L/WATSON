"""Audit detail is an allowlist per kind; keys are normalised and nested."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.replay_support import open_test_db
from watson.privacy.audit import (
    KIND_DELETE_FAILED,
    KIND_DELETED,
    KIND_INGESTED,
    KIND_REPLAY,
    KIND_SKIPPED,
    KIND_VERIFIED,
    AuditError,
    iter_audit_keys,
    normalize_audit_key,
    record_audit,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("bundle_id", "bundleid"),
        ("Bundle_ID", "bundleid"),
        ("bundleId", "bundleid"),
        ("bundle-id", "bundleid"),
        ("  app_name ", "appname"),
        ("Window_Title", "windowtitle"),
        ("ocr_text", "ocrtext"),
        ("ocrText", "ocrtext"),
    ],
)
def test_normalize_audit_key_collapses_case_and_separators(raw: str, expected: str) -> None:
    assert normalize_audit_key(raw) == expected


def test_iter_audit_keys_walks_nested_dicts_and_lists() -> None:
    keys = set(
        iter_audit_keys(
            {
                "meta": {"bundle_id": "x", "items": [{"ocr_text": "y"}, {"Window_Title": "z"}]},
                "alt": [{"app_name": "a"}],
            }
        )
    )
    assert keys == {"meta", "bundle_id", "items", "ocr_text", "Window_Title", "alt", "app_name"}


@pytest.mark.parametrize(
    "detail",
    [
        {"reason": "excluded_app", "Bundle_ID": "com.apple.Passwords"},
        {"reason": "excluded_app", "bundleId": "com.apple.Passwords"},
        {"reason": "excluded_app", "app_name ": "Passwords"},
        {"reason": "excluded_app", "Window_Title": "Vault"},
        {"reason": "excluded_app", "ocr_text": "secret"},
        {"reason": "excluded_app", "text": "secret"},
        {"reason": "excluded_app", "content": "secret"},
        {"reason": "excluded_app", "ocrText": "secret"},
        {"reason": "excluded_app", "frame_text": "secret"},
        {"reason": "excluded_app", "merged_text": "secret"},
        {"reason": "excluded_app", "snippet": "secret"},
        {"reason": "excluded_app", "description": "secret"},
        {"reason": "excluded_app", "meta": {"bundle_id": "com.apple.Passwords"}},
        {"reason": "excluded_app", "items": [{"ocr_text": "secret"}]},
        {"reason": "excluded_app", "items": [{"Window_Title": "Vault"}]},
    ],
    ids=[
        "Bundle_ID",
        "bundleId",
        "app_name_padded",
        "Window_Title",
        "ocr_text",
        "text",
        "content",
        "ocrText",
        "frame_text",
        "merged_text",
        "snippet",
        "description",
        "nested_bundle_id",
        "list_ocr_text",
        "list_Window_Title",
    ],
)
def test_skipped_audit_rejects_content_key_variants(
    data_dir: Path, key_provider: object, detail: dict[str, object]
) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="content fields|not allowed"):
            record_audit(conn, kind=KIND_SKIPPED, detail=detail)
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


def test_skipped_audit_rejects_unknown_key(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="not allowed"):
            record_audit(conn, kind=KIND_SKIPPED, detail={"reason": "excluded_app", "extra": 1})
    finally:
        conn.close()


def test_unknown_kind_is_rejected(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="unknown audit kind"):
            record_audit(conn, kind="frame_mystery", detail={"reason": "excluded_app"})
    finally:
        conn.close()


@pytest.mark.parametrize(
    "kind, detail",
    [
        (KIND_SKIPPED, {"reason": "excluded_app"}),
        (KIND_INGESTED, {"frame_id": "abc", "png_sha256": "def"}),
        (KIND_DELETED, {"frame_id": "abc", "png": 1, "sidecar": 1}),
        (KIND_VERIFIED, {"frame_id": "abc"}),
        (
            KIND_DELETE_FAILED,
            {"error": "PermissionError", "reason": "excluded_app", "scope": "excluded"},
        ),
        (
            KIND_REPLAY,
            {
                "fixture": "day_mini",
                "frame_count": 1,
                "ingested": 1,
                "extracted": 1,
                "deleted": 1,
                "deletion_verified": 1,
                "skipped_excluded": 0,
            },
        ),
    ],
)
def test_allowed_payloads_per_kind_are_accepted(
    data_dir: Path, key_provider: object, kind: str, detail: dict[str, object]
) -> None:
    conn = open_test_db(data_dir)
    try:
        event_id = record_audit(conn, kind=kind, detail=detail)
        assert event_id
        row = conn.execute("SELECT kind FROM audit_events WHERE id = ?", (event_id,)).fetchone()
        assert row is not None
        assert str(row["kind"]) == kind
    finally:
        conn.close()
