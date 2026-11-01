"""Pydantic schemas for day consolidation LLM steps (plan §3.7)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ActivityCluster(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    observation_indexes: list[int] = Field(..., min_length=1)


class ClusteringOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activities: list[ActivityCluster]

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _inline(cls.model_json_schema())


class EntityDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_name: str = Field(..., min_length=1)
    entity_type: Literal["project", "person"]
    delta_text: str = Field(..., min_length=1)
    claim_indexes: list[int] = Field(default_factory=list)


class ExtractedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., min_length=1)
    claim_indexes: list[int] = Field(..., min_length=1)
    status: Literal["open", "done", "dropped"] | None = None
    due_hint: str | None = None


class DayExtractOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_deltas: list[EntityDelta]
    decisions: list[ExtractedItem]
    commitments: list[ExtractedItem]

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _inline(cls.model_json_schema())


def _inline(raw: dict[str, Any]) -> dict[str, Any]:
    inlined = _inline_refs(raw, raw.get("$defs", {}))
    if not isinstance(inlined, dict):
        raise TypeError("consolidation JSON schema must be an object")
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


CLUSTERING_JSON_SCHEMA = ClusteringOutput.json_schema()
DAY_EXTRACT_JSON_SCHEMA = DayExtractOutput.json_schema()
