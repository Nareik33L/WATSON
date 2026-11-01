"""Score one consolidated working day against a fixture ``truth.yaml``."""

from __future__ import annotations

from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.intelligence.citations import CITATION_RE, citations_resolvable
from watson.intelligence.eval.metrics import (
    completed_accuracy,
    duplicate_rate,
    f1_score,
    names_match,
    precision,
    recall,
    unsupported_count,
)

try:
    import yaml
except ImportError:  # pragma: no cover - dev/CI always has PyYAML
    yaml = None  # type: ignore[assignment]


def load_truth(path: Any) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required to load fixture truth.yaml")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: truth.yaml must be a mapping")
    return loaded


def score_day(
    conn: sqlcipher.Connection,
    working_day_id: str,
    truth: dict[str, Any],
    *,
    pipeline_version: int = 1,
) -> dict[str, float]:
    predicted = collect_predicted(conn, working_day_id, pipeline_version=pipeline_version)
    projects = [str(item) for item in list(truth.get("projects") or [])]
    people = [str(item) for item in list(truth.get("people") or [])]
    decisions = [
        str(item["text"]) for item in list(truth.get("decisions") or []) if item.get("text")
    ]
    commitments = [
        str(item["text"]) for item in list(truth.get("commitments") or []) if item.get("text")
    ]
    completed = [str(item) for item in list(truth.get("completed") or [])]
    not_completed = [str(item) for item in list(truth.get("not_completed") or [])]
    noise = [str(item) for item in list(truth.get("noise") or [])]
    return {
        "project_f1": f1_score(predicted["projects"], projects),
        "people_f1": f1_score(predicted["people"], people),
        "completed_accuracy": completed_accuracy(predicted["completed"], completed, not_completed),
        "decisions_recall": recall(predicted["decisions"], decisions),
        "decisions_precision": precision(predicted["decisions"], decisions),
        "commitments_recall": recall(predicted["commitments"], commitments),
        "commitments_precision": precision(predicted["commitments"], commitments),
        "unsupported_achievements": float(unsupported_count(predicted["completed"], completed)),
        "duplicate_observation_rate": duplicate_rate(predicted["segment_ids"]),
        "noise_non_work_rate": _noise_rate(predicted["noise_rows"], noise),
        "claims_with_evidence": predicted["claims_with_evidence"],
        "citations_resolvable": predicted["citations_resolvable"],
    }


def collect_predicted(
    conn: sqlcipher.Connection, working_day_id: str, *, pipeline_version: int = 1
) -> dict[str, Any]:
    projects = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT canonical_name FROM entities
            WHERE type = 'project' AND merged_into_id IS NULL
            ORDER BY canonical_name, id
            """
        )
    ]
    people = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT canonical_name FROM entities
            WHERE type = 'person' AND merged_into_id IS NULL
            ORDER BY canonical_name, id
            """
        )
    ]
    decisions = _texts(
        conn,
        """
        SELECT text FROM claims
        WHERE working_day_id = ? AND pipeline_version = ? AND kind = 'decision'
        UNION
        SELECT text FROM decisions
        WHERE working_day_id = ? AND pipeline_version = ?
        """,
        (working_day_id, pipeline_version, working_day_id, pipeline_version),
    )
    commitments = _texts(
        conn,
        """
        SELECT text FROM claims
        WHERE working_day_id = ? AND pipeline_version = ? AND kind = 'commitment'
        UNION
        SELECT text FROM commitments
        WHERE working_day_id = ? AND pipeline_version = ?
        """,
        (working_day_id, pipeline_version, working_day_id, pipeline_version),
    )
    completed = _texts(
        conn,
        """
        SELECT text FROM claims
        WHERE working_day_id = ? AND pipeline_version = ?
          AND kind IN ('achievement', 'outcome') AND assertion = 'observed'
        UNION
        SELECT text FROM achievement_candidates
        WHERE working_day_id = ? AND pipeline_version = ?
        """,
        (working_day_id, pipeline_version, working_day_id, pipeline_version),
    )
    segment_ids = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT o.segment_id
            FROM observations o
            JOIN segments s ON s.id = o.segment_id
            WHERE s.working_day_id = ? AND o.pipeline_version = ?
            ORDER BY o.id
            """,
            (working_day_id, pipeline_version),
        )
    ]
    noise_rows = [
        {
            "activity_type": str(row[0]),
            "window": str(row[1] or ""),
            "merged": str(row[2] or ""),
        }
        for row in conn.execute(
            """
            SELECT o.activity_type, s.window_title_norm, s.merged_text
            FROM observations o
            JOIN segments s ON s.id = o.segment_id
            WHERE s.working_day_id = ? AND o.pipeline_version = ?
            """,
            (working_day_id, pipeline_version),
        )
    ]
    claims = int(
        conn.execute(
            "SELECT COUNT(*) FROM claims WHERE working_day_id = ? AND pipeline_version = ?",
            (working_day_id, pipeline_version),
        ).fetchone()[0]
    )
    evidenced = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM claims c
            WHERE c.working_day_id = ? AND c.pipeline_version = ?
              AND EXISTS (SELECT 1 FROM claim_evidence e WHERE e.claim_id = c.id)
            """,
            (working_day_id, pipeline_version),
        ).fetchone()[0]
    )
    narrative = ""
    summary = conn.execute(
        """
        SELECT narrative_md FROM daily_summaries
        WHERE working_day_id = ? AND pipeline_version = ?
        """,
        (working_day_id, pipeline_version),
    ).fetchone()
    if summary is not None:
        narrative = str(summary[0])
    claim_ids = {
        str(row[0])
        for row in conn.execute(
            "SELECT id FROM claims WHERE working_day_id = ? AND pipeline_version = ?",
            (working_day_id, pipeline_version),
        )
    }
    found = CITATION_RE.findall(narrative)
    if claims > 0 and not found:
        # Empty / uncited narrative is not 100% resolvable (non-vacuous).
        resolvable = 0.0
    else:
        resolvable = 1.0 if citations_resolvable(narrative, claim_ids) else 0.0
    return {
        "projects": projects,
        "people": people,
        "decisions": decisions,
        "commitments": commitments,
        "completed": completed,
        "segment_ids": segment_ids,
        "noise_rows": noise_rows,
        "claims_with_evidence": 1.0 if claims == 0 else evidenced / claims,
        "citations_resolvable": resolvable,
        "citation_count": len(found),
        "narrative_nonempty": bool(narrative.strip()),
        "summary_present": summary is not None,
        "claims": claims,
    }


def _texts(conn: sqlcipher.Connection, sql: str, params: tuple[object, ...]) -> list[str]:
    return [str(row[0]) for row in conn.execute(sql, params)]


def _noise_rate(rows: list[dict[str, str]], noise_labels: list[str]) -> float:
    """Score noise windows only. Merged-text chrome (nav 'Cedar Daily') is ignored."""
    if not noise_labels:
        return 1.0
    relevant = [
        row
        for row in rows
        if any(
            names_match(row["window"], label) or _window_tokens(row["window"], label)
            for label in noise_labels
        )
    ]
    if not relevant:
        return 0.0
    good = sum(1 for row in relevant if row["activity_type"] == "non_work")
    return good / len(relevant)


def _window_tokens(window: str, label: str) -> bool:
    blob = window.casefold()
    tokens = [part for part in label.replace("—", " ").replace("–", " ").split() if part.isalpha()]
    if len(tokens) < 2:
        return False
    return all(token.casefold() in blob for token in tokens[:2])
