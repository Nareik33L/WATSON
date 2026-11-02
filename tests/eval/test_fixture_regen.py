from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from watson_fixturegen.paths import scenarios_dir
from watson_fixturegen.phash import hamming_distance
from watson_fixturegen.render import render_scenario

REPO = Path(__file__).resolve().parents[2]
DAYS = (
    "day_acme_q4",
    "day_fragmented_multi",
    "day_meeting_heavy",
    "day_sensitive_traps",
    "day_near_empty",
)
BBOX_TOLERANCE_PX = 2
PHASH_HAMMING_MAX = 4
TEXT_FIELDS = (
    "frame_id",
    "captured_at",
    "display_id",
    "bundle_id",
    "app_name",
    "window_title",
    "window_id",
    "is_private_browsing",
    "url",
    "width",
    "height",
    "capture_mode",
)


def _assert_sidecar_close(generated: dict[str, Any], committed: dict[str, Any]) -> None:
    for key in TEXT_FIELDS:
        assert generated[key] == committed[key], f"{key} mismatch"
    assert len(generated["ocr"]["lines"]) == len(committed["ocr"]["lines"])
    for left, right in zip(generated["ocr"]["lines"], committed["ocr"]["lines"], strict=True):
        assert left["text"] == right["text"]
        assert left["confidence"] == right["confidence"]
        assert len(left["bbox"]) == 4
        for a, b in zip(left["bbox"], right["bbox"], strict=True):
            assert abs(a - b) <= BBOX_TOLERANCE_PX, f"bbox {left['bbox']} vs {right['bbox']}"
    assert hamming_distance(generated["phash"], committed["phash"]) <= PHASH_HAMMING_MAX


@pytest.mark.fixture_regen
@pytest.mark.parametrize("name", DAYS)
def test_regen_sidecars_and_truth_match_committed(tmp_path: Path, name: str) -> None:
    pytest.importorskip("playwright")
    fixture = REPO / "tests" / "fixtures" / name
    scenario = scenarios_dir() / f"{name}.yaml"
    out = tmp_path / name
    render_scenario(scenario, out)
    committed_truth = (fixture / "truth.yaml").read_text(encoding="utf-8")
    generated_truth = (out / "truth.yaml").read_text(encoding="utf-8")
    assert generated_truth == committed_truth
    assert yaml.safe_load(generated_truth) == yaml.safe_load(committed_truth)
    generated_sample = (out / "sample50.txt").read_text(encoding="utf-8")
    committed_sample = (fixture / "sample50.txt").read_text(encoding="utf-8")
    assert generated_sample == committed_sample
    generated = sorted((out / "frames").glob("*/*.json"))
    committed = sorted((fixture / "frames").glob("*/*.json"))
    assert [path.name for path in generated] == [path.name for path in committed]
    for left, right in zip(generated, committed, strict=True):
        _assert_sidecar_close(
            json.loads(left.read_text(encoding="utf-8")),
            json.loads(right.read_text(encoding="utf-8")),
        )


@pytest.mark.fixture_regen
def test_dashboard_pages_fit_1280px(data_dir: Path, key_provider: object) -> None:
    """Plan §12 M4: every dashboard page is usable at 1280 px (existing Chromium)."""
    pytest.importorskip("playwright")
    from fastapi.testclient import TestClient

    from tests.replay_support import open_test_db
    from tests.unit.test_consolidation import (
        _insert_claim,
        _insert_day,
        _insert_frame,
        _insert_segment,
    )
    from tests.unit.test_dashboard_pages import (
        _insert_entity,
        _insert_summary,
        _observation_with_candidates,
    )
    from watson.config import load_config
    from watson.dashboard.app import PACKAGE_DIR, create_app
    from watson_fixturegen.layout import VIEWPORT_WIDTH, assert_fits_viewport

    wide = "WideToken" + ("W" * 400)
    conn = open_test_db(data_dir)
    try:
        day_id = _insert_day(conn, date="2026-10-31", state="consolidated")
        frame_id = _insert_frame(conn, day_id)
        segment_id = _insert_segment(conn, day_id, frame_id)
        observation_id = _observation_with_candidates(
            conn,
            segment_id,
            description=wide,
            projects=[wide],
            people=[wide],
        )
        claim_id = _insert_claim(
            conn,
            observation_id,
            day_id,
            segment_id,
            frame_id,
            kind="activity",
            assertion="observed",
            text=wide,
        )
        _insert_summary(conn, day_id, f"{wide} [c:{claim_id}].", [claim_id])
        project_id = _insert_entity(conn, name=wide, alias=wide, typ="project")
        _insert_entity(conn, name=wide + "-person", alias=wide, typ="person")
    finally:
        conn.close()

    # Re-auth after seeding so the app sees the same WATSON_DATA_DIR database.
    app = create_app(token="layout-token-not-a-secret-for-ci", config=load_config(), port=8000)
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    code = app.state.launch_codes.mint()
    client.get(f"/auth?code={code}", follow_redirects=False)
    css = (PACKAGE_DIR / "static" / "status.css").read_text(encoding="utf-8")
    paths = (
        "/",
        "/today",
        "/history",
        "/projects",
        f"/projects/{project_id}",
        "/people",
        "/days?date=2026-10-31",
    )
    for path in paths:
        page = client.get(path)
        assert page.status_code == 200, path
        assert_fits_viewport(page.text, css=css, width=VIEWPORT_WIDTH)
