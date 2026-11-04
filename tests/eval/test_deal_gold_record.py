"""Record-style run over a materialized deal_gold day issues all consolidate calls."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tests.replay_support import TEST_KEY
from watson.intelligence.eval.deals import (
    _ocr_lines,
    deal_gold_root,
    load_deal_day,
    materialize_deal_day,
)
from watson.intelligence.eval.run import _eval_one
from watson.intelligence.runtime import FakeRuntime

_GOLD_RECORDINGS = {
    "complete": [
        {
            "match": "Merged text",
            "response": {
                "activity_type": "knowledge_work",
                "description": "Working a deal thread.",
                "confidence": 0.8,
                "claims": [
                    {
                        "kind": "commitment",
                        "text": "Send revised pricing by Friday",
                        "assertion": "observed",
                        "evidence": [
                            {
                                "snippet": "Please send revised pricing by Friday.",
                                "line_start": 1,
                                "line_end": 2,
                            }
                        ],
                    }
                ],
                "project_candidates": ["Acme Q4"],
                "people_candidates": ["Priya Natarajan"],
            },
        },
        {
            "match": "Cluster these observations",
            "response": {
                "deals": [
                    {
                        "title": "Acme Q4 Proposal",
                        "summary": "pricing and demo",
                        "observation_indexes": [1, 2, 3, 4, 5],
                    }
                ]
            },
        },
        {
            "match": "Extract cross-segment",
            "response": {"entity_deltas": [], "decisions": [], "commitments": []},
        },
        {
            "match": "Write the daily narrative",
            "response": {
                "paragraphs": [
                    {
                        "text": "What happened: Send revised pricing by Friday [c:1].",
                        "claim_ids": ["1"],
                        "slot": "what_happened",
                    }
                ]
            },
        },
    ]
}


def test_ocr_lines_sentence_splits_folded_gold_text() -> None:
    lines = _ocr_lines(
        "Email from Priya Natarajan at Acme. Please send revised pricing by Friday. "
        "Mentioned the 14 Nov demo."
    )
    assert len(lines) >= 2
    assert lines[0].startswith("Email from Priya")
    assert _ocr_lines("") == ["empty"]


def test_materialized_deal_gold_has_multiline_ocr_and_unique_pngs(tmp_path: Path) -> None:
    gold = load_deal_day(deal_gold_root() / "day_focused")
    dest = materialize_deal_day(gold, tmp_path / "day_focused")
    sidecars = sorted((dest / "frames").rglob("*.json"))
    pngs = sorted((dest / "frames").rglob("*.png"))
    assert sidecars
    hashes = {path.read_bytes() for path in pngs}
    assert len(hashes) == len(pngs)
    rich = None
    for path in sidecars:
        payload = json.loads(path.read_text(encoding="utf-8"))
        texts = [str(line.get("text") or "") for line in payload["ocr"]["lines"]]
        if any("Priya Natarajan" in text for text in texts):
            rich = texts
            break
    assert rich is not None
    assert len(rich) >= 2


def test_record_style_deal_gold_day_issues_cluster_extract_summary(
    tmp_path: Path, key_provider: object
) -> None:
    gold = load_deal_day(deal_gold_root() / "day_focused")
    dest = materialize_deal_day(gold, tmp_path / "day_focused")
    captured: list[str] = []

    class SpyRuntime(FakeRuntime):
        def complete(self, messages, schema, *, num_predict=None):  # type: ignore[no-untyped-def]
            captured.append("\n".join(item["content"] for item in messages))
            return super().complete(messages, schema, num_predict=num_predict)

    runtime = SpyRuntime(recordings=_GOLD_RECORDINGS)
    day = _eval_one(
        dest,
        runtime=runtime,
        out_dir=tmp_path / "out",
        key=TEST_KEY,
        now_fn=lambda: datetime(2026, 10, 31, 23, 0, tzinfo=UTC),
    )
    kinds = {
        "cluster": sum("Cluster these observations" in blob for blob in captured),
        "extract": sum("Extract cross-segment" in blob for blob in captured),
        "summary": sum("Write the daily narrative" in blob for blob in captured),
    }
    # Idempotency rerun issues each consolidate call twice.
    assert kinds["cluster"] >= 1
    assert kinds["extract"] >= 1
    assert kinds["summary"] >= 1
    assert day.claims > 0
    assert day.predicted is not None
    assert day.predicted.segments
    summary_prompts = [blob for blob in captured if "Write the daily narrative" in blob]
    assert summary_prompts
    assert "Roster names:" in summary_prompts[0]
    assert "[c:" in summary_prompts[0]
