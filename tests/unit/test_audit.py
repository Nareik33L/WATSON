"""Audit detail is an allowlist per kind; keys are normalised and values constrained."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from tests.replay_support import open_test_db, write_mini_fixture
from watson.capture.spool import copy_pairs_to_spool, iter_pairs
from watson.pipeline.frames import ensure_working_day, ingest_pair
from watson.pipeline.replay import load_and_validate_manifest
from watson.privacy.audit import (
    ERROR_PERMISSION,
    ERROR_STILL_PRESENT,
    ERROR_UNLINK_FAILED,
    KIND_DEDUPED,
    KIND_DELETE_FAILED,
    KIND_DELETED,
    KIND_INGESTED,
    KIND_REPLAY,
    KIND_SKIPPED,
    KIND_SPOOL_SWEPT,
    KIND_TIER2_PURGED,
    KIND_VERIFIED,
    SCOPE_EXCLUDED,
    AuditError,
    audit_error_class,
    iter_audit_keys,
    normalize_audit_key,
    record_audit,
)

SAMPLE_FRAME_ID = "11111111-1111-4111-8111-111111111111"
SAMPLE_PNG_SHA256 = "ab" * 32


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
        with pytest.raises(AuditError, match="content fields|not allowed|scalars"):
            record_audit(conn, kind=KIND_SKIPPED, detail=detail)
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


def test_deduped_audit_rejects_hash_and_content_keys(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="not allowed|content"):
            record_audit(
                conn,
                kind=KIND_DEDUPED,
                detail={
                    "frame_id": SAMPLE_FRAME_ID,
                    "previous_frame_id": "22222222-2222-4222-8222-222222222222",
                    "distance": 0,
                    "threshold": 4,
                    "phash": "0123456789abcdef",
                },
            )
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
        (KIND_INGESTED, {"frame_id": SAMPLE_FRAME_ID, "png_sha256": SAMPLE_PNG_SHA256}),
        (KIND_DELETED, {"frame_id": SAMPLE_FRAME_ID, "png": 1, "sidecar": 1}),
        (KIND_VERIFIED, {"frame_id": SAMPLE_FRAME_ID}),
        (
            KIND_DELETE_FAILED,
            {"error": ERROR_PERMISSION, "reason": "excluded_app", "scope": SCOPE_EXCLUDED},
        ),
        (
            KIND_DELETE_FAILED,
            {
                "error": ERROR_STILL_PRESENT,
                "reason": "excluded_title",
                "scope": SCOPE_EXCLUDED,
            },
        ),
        (
            KIND_DELETE_FAILED,
            {
                "frame_id": SAMPLE_FRAME_ID,
                "error": ERROR_UNLINK_FAILED,
            },
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
                "deduped": 0,
                "segments": 0,
            },
        ),
        (
            KIND_DEDUPED,
            {
                "frame_id": SAMPLE_FRAME_ID,
                "previous_frame_id": "22222222-2222-4222-8222-222222222222",
                "distance": 2,
                "threshold": 4,
            },
        ),
        (KIND_TIER2_PURGED, {"texts": 1, "visuals": 0, "segments": 1}),
        (
            KIND_SPOOL_SWEPT,
            {
                "png": 1,
                "sidecar": 1,
                "orphaned": 1,
                "stale": 0,
                "unprocessed": 0,
                "refused": 0,
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


@pytest.mark.parametrize(
    "value",
    ["bundle_id_leak", "excluded_bundle", "PermissionError", "", "EXCLUDED_APP"],
    ids=["bundle_id_leak", "excluded_bundle", "exception_type", "empty", "wrong_case"],
)
def test_invalid_reason_raises_audit_error(
    data_dir: Path, key_provider: object, value: str
) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="invalid audit reason"):
            record_audit(conn, kind=KIND_SKIPPED, detail={"reason": value})
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(
    "value",
    ["PermissionError", "denied", "OSError", "FileNotFoundError", "error"],
    ids=["exception_type", "exception_message", "OSError", "FileNotFoundError", "generic"],
)
def test_invalid_error_raises_audit_error(data_dir: Path, key_provider: object, value: str) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="invalid audit error"):
            record_audit(
                conn,
                kind=KIND_DELETE_FAILED,
                detail={"error": value, "reason": "excluded_app", "scope": SCOPE_EXCLUDED},
            )
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(
    "value",
    ["spool", "all", "EXCLUDED", ""],
    ids=["spool", "all", "case", "empty"],
)
def test_invalid_scope_raises_audit_error(data_dir: Path, key_provider: object, value: str) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="invalid audit scope"):
            record_audit(
                conn,
                kind=KIND_DELETE_FAILED,
                detail={"error": ERROR_PERMISSION, "reason": "excluded_app", "scope": value},
            )
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(
    "value",
    [
        {"nested": 1},
        [1, 2],
        (1, 2),
        {1, 2},
        frozenset({1}),
        None,
        b"bytes",
    ],
    ids=["dict", "list", "tuple", "set", "frozenset", "none", "bytes"],
)
def test_non_scalar_reason_raises_audit_error(
    data_dir: Path, key_provider: object, value: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="scalars"):
            record_audit(conn, kind=KIND_SKIPPED, detail={"reason": value})
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


def test_set_value_on_allowed_key_raises_audit_error_not_typeerror(
    data_dir: Path, key_provider: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="scalars"):
            record_audit(
                conn,
                kind=KIND_DELETE_FAILED,
                detail={
                    "error": {ERROR_PERMISSION},
                    "reason": "excluded_app",
                    "scope": SCOPE_EXCLUDED,
                },
            )
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(
    "frame_id",
    ["abc", "not-a-uuid", "", "123", "11111111111141118111111111111111x"],
    ids=["abc", "not-a-uuid", "empty", "digits", "almost_hex"],
)
def test_invalid_frame_id_raises_audit_error(
    data_dir: Path, key_provider: object, frame_id: str
) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="must be a UUID"):
            record_audit(conn, kind=KIND_VERIFIED, detail={"frame_id": frame_id})
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(
    "digest",
    ["def", "ab" * 31, "gg" * 32, "ab" * 32 + "0", 1],
    ids=["short", "63_hex", "non_hex", "65_hex", "int"],
)
def test_invalid_png_sha256_raises_audit_error(
    data_dir: Path, key_provider: object, digest: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="64-char hex"):
            record_audit(
                conn,
                kind=KIND_INGESTED,
                detail={"frame_id": SAMPLE_FRAME_ID, "png_sha256": digest},
            )
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(
    "exc, expected",
    [
        (PermissionError("denied"), ERROR_PERMISSION),
        (OSError("disk"), ERROR_UNLINK_FAILED),
        (RuntimeError("files still present after unlink"), ERROR_STILL_PRESENT),
    ],
    ids=["permission", "oserror", "still_present"],
)
def test_audit_error_class_maps_exceptions_to_codes(exc: BaseException, expected: str) -> None:
    assert audit_error_class(exc) == expected


def test_audit_error_class_walks_cause_for_permission() -> None:
    wrapped = RuntimeError("excluded spool delete failed")
    wrapped.__cause__ = PermissionError("denied")
    assert audit_error_class(wrapped) == ERROR_PERMISSION


@pytest.mark.parametrize(
    "value",
    [True, False, 1.0, "1", -1, math.nan, math.inf, -math.inf],
    ids=["bool_true", "bool_false", "float", "str", "negative", "nan", "inf", "neg_inf"],
)
def test_count_keys_reject_non_natural_ints(
    data_dir: Path, key_provider: object, value: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="non-negative int"):
            record_audit(
                conn,
                kind=KIND_REPLAY,
                detail={
                    "fixture": "day_mini",
                    "frame_count": value,
                    "ingested": 0,
                    "extracted": 0,
                    "deleted": 0,
                    "deletion_verified": 0,
                    "skipped_excluded": 0,
                },
            )
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(
    "key",
    ["png", "sidecar"],
    ids=["png", "sidecar"],
)
def test_deleted_count_keys_reject_bool(data_dir: Path, key_provider: object, key: str) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="non-negative int"):
            record_audit(
                conn,
                kind=KIND_DELETED,
                detail={"frame_id": SAMPLE_FRAME_ID, "png": 1, "sidecar": 1, key: True},
            )
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(
    "value",
    [
        "Day_Mini",
        "day mini",
        "../secret",
        "a" * 65,
        "",
        "1day",
        "/tmp/day_mini",
    ],
    ids=["case", "space", "path", "too_long", "empty", "leading_digit", "abs_path"],
)
def test_invalid_fixture_raises_audit_error(
    data_dir: Path, key_provider: object, value: str
) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="invalid audit fixture"):
            record_audit(
                conn,
                kind=KIND_REPLAY,
                detail={
                    "fixture": value,
                    "frame_count": 1,
                    "ingested": 1,
                    "extracted": 1,
                    "deleted": 1,
                    "deletion_verified": 1,
                    "skipped_excluded": 0,
                },
            )
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


class _ExactStr(str):
    """str subclass whose content is already a valid id/digest.

    ``isinstance(v, str)`` is true and format checks pass; only ``type(v) is str``
    rejects it. Loosening either check back to ``isinstance`` must fail these tests.
    """


def test_str_subclass_frame_id_is_rejected(data_dir: Path, key_provider: object) -> None:
    leaked = _ExactStr(SAMPLE_FRAME_ID)
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="must be a UUID"):
            record_audit(conn, kind=KIND_VERIFIED, detail={"frame_id": leaked})
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


def test_str_subclass_png_sha256_is_rejected(data_dir: Path, key_provider: object) -> None:
    leaked = _ExactStr(SAMPLE_PNG_SHA256)
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="64-char hex"):
            record_audit(
                conn,
                kind=KIND_INGESTED,
                detail={"frame_id": SAMPLE_FRAME_ID, "png_sha256": leaked},
            )
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()


def test_ingest_rolls_back_frame_when_audit_fails(
    data_dir: Path, key_provider: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=1)
    spool = tmp_path / "spool"
    conn = open_test_db(data_dir)
    try:
        manifest = load_and_validate_manifest(fixture)
        frames_map = manifest["frames"]
        assert isinstance(frames_map, dict)
        copy_pairs_to_spool(fixture, frames_map.items(), spool)
        pair = iter_pairs(spool)[0]
        day_id = ensure_working_day(conn, str(manifest["date"]))

        monkeypatch.setattr(
            "watson.pipeline.frames.sha256_file",
            lambda path: "not-a-digest",
        )
        with pytest.raises(AuditError):
            ingest_pair(conn, pair, working_day_id=day_id)
        assert conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()
