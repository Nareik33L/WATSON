"""Pydantic schema for understanding v1 (plan §3.5 / §7.2). Never DB ids."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class ActivityType(StrEnum):
    KNOWLEDGE_WORK = "knowledge_work"
    COMMUNICATION = "communication"
    MEETING = "meeting"
    PLANNING = "planning"
    NON_WORK = "non_work"


class ClaimKind(StrEnum):
    ACTIVITY = "activity"
    OUTCOME = "outcome"
    DECISION = "decision"
    COMMITMENT = "commitment"
    ACHIEVEMENT = "achievement"
    PERSON_INTERACTION = "person_interaction"


class Assertion(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    UNCONFIRMED = "unconfirmed"


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snippet: str = Field(..., min_length=1, max_length=300)
    line_start: int = Field(..., ge=1)
    line_end: int = Field(..., ge=1)

    @model_validator(mode="after")
    def ordered_lines(self) -> EvidenceCitation:
        if self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        return self


class CandidateClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ClaimKind
    text: str = Field(..., min_length=1)
    assertion: Assertion
    evidence: list[EvidenceCitation] = Field(..., min_length=1)


class UnderstandingOutput(BaseModel):
    """One observation for a segment. Candidates are free-text names, never ids."""

    model_config = ConfigDict(extra="forbid")
    activity_type: ActivityType
    description: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    claims: list[CandidateClaim]
    project_candidates: list[str]
    people_candidates: list[str]

    @field_validator("project_candidates", "people_candidates")
    @classmethod
    def names_not_db_ids(cls, values: list[str]) -> list[str]:
        for value in values:
            if _UUID.match(value.strip()):
                raise ValueError("candidates must be free-text names, never database ids")
        return values

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """Flattened schema for Ollama ``format`` (no $ref / $defs)."""
        raw = cls.model_json_schema()
        inlined = _inline_refs(raw, raw.get("$defs", {}))
        if not isinstance(inlined, dict):
            raise TypeError("understanding JSON schema must be an object")
        return inlined


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and len(node) == 1:
            return _inline_refs(defs[ref.rsplit("/", 1)[-1]], defs)
        return {key: _inline_refs(val, defs) for key, val in node.items() if key != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


UNDERSTANDING_JSON_SCHEMA = UnderstandingOutput.json_schema()
