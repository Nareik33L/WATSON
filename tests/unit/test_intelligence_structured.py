"""Prompt loader and §7.2 validation + one retry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from watson.intelligence.prompts import PromptError, load_prompt
from watson.intelligence.runtime import FakeRuntime, Message, StructuredOutputError
from watson.intelligence.schemas import UNDERSTANDING_JSON_SCHEMA, UnderstandingOutput
from watson.intelligence.structured import complete_structured

_VALID = {
    "activity_type": "communication",
    "description": "Reading mail. Pricing send is requested, not completed.",
    "confidence": 0.8,
    "claims": [
        {
            "kind": "commitment",
            "text": "Send revised pricing by Friday",
            "assertion": "unconfirmed",
            "evidence": [
                {
                    "snippet": "Please send revised pricing by Friday.",
                    "line_start": 1,
                    "line_end": 1,
                }
            ],
        }
    ],
    "project_candidates": ["Acme Q4 Proposal"],
    "people_candidates": [],
}


def test_load_understanding_v1_returns_prompt_version() -> None:
    prompt = load_prompt("understanding", "v1")
    assert prompt.prompt_version == "understanding/v1"
    text = prompt.text.casefold()
    assert "visible completion evidence" in text
    assert "unconfirmed" in text
    assert "database ids" in text
    assert "sent" in text and "approved" in text and "agreed" in text
    with pytest.raises(PromptError, match="not found"):
        load_prompt("understanding", "v99")
    with pytest.raises(PromptError, match="invalid"):
        load_prompt("understanding", "../v1")


class _RecordingRuntime:
    def __init__(self) -> None:
        self.user_messages: list[str] = []
        self._calls = 0

    def complete(
        self, messages: Sequence[Message], schema: Mapping[str, Any]
    ) -> dict[str, Any]:
        del schema
        self._calls += 1
        if self._calls == 1:
            return {"nope": True}
        self.user_messages.append(messages[-1]["content"])
        return _VALID


def test_complete_structured_retries_once_then_succeeds() -> None:
    runtime = FakeRuntime(complete_queue=[{"nope": True}, _VALID])
    out = complete_structured(
        runtime,
        [{"role": "user", "content": "segment"}],
        UnderstandingOutput,
        schema=UNDERSTANDING_JSON_SCHEMA,
    )
    assert out.claims[0].assertion.value == "unconfirmed"
    assert runtime.complete_calls == 2


def test_retry_message_includes_pydantic_validation_error() -> None:
    runtime = _RecordingRuntime()
    complete_structured(
        runtime,
        [{"role": "user", "content": "segment"}],
        UnderstandingOutput,
    )
    assert runtime.user_messages
    retry = runtime.user_messages[0]
    assert "failed validation" in retry
    assert "activity_type" in retry
    assert "Field required" in retry


def test_complete_structured_raises_typed_failure_after_retry() -> None:
    runtime = FakeRuntime(complete_queue=[{"nope": True}, {"still": "bad"}])
    with pytest.raises(StructuredOutputError, match="one retry") as raised:
        complete_structured(
            runtime,
            [{"role": "user", "content": "segment"}],
            UnderstandingOutput,
        )
    assert raised.value.retryable is False
    assert runtime.complete_calls == 2


def test_understanding_schema_rejects_db_ids_and_accepts_valid() -> None:
    UnderstandingOutput.model_validate(_VALID)
    bad = dict(_VALID)
    bad["project_candidates"] = ["01900000-0000-7000-8000-000000000001"]
    with pytest.raises(Exception, match="database ids"):
        UnderstandingOutput.model_validate(bad)
    first_try = complete_structured(
        FakeRuntime(complete_queue=[_VALID]),
        [{"role": "user", "content": "ok"}],
        UnderstandingOutput,
    )
    assert first_try.activity_type.value == "communication"
