"""Constrained-JSON prompts for the M0-A VLM and understanding bake-off."""

from __future__ import annotations

from watson_bench.frames import Frame

VLM_PROMPT_VERSION = "bench-vlm-v1"
UNDERSTANDING_PROMPT_VERSION = "bench-understanding-v1"

VLM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "visible_text": {"type": "string"},
        "ui_description": {"type": "string"},
        "app_guess": {"type": "string"},
        "visual_density": {
            "type": "string",
            "enum": ["sparse", "text", "diagram", "dense"],
        },
    },
    "required": ["visible_text", "ui_description", "app_guess", "visual_density"],
}

UNDERSTANDING_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "activity_type": {"type": "string"},
        "description": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "activity",
                            "outcome",
                            "decision",
                            "commitment",
                            "achievement",
                            "person_interaction",
                        ],
                    },
                    "text": {"type": "string"},
                    "assertion": {
                        "type": "string",
                        "enum": ["observed", "inferred", "unconfirmed"],
                    },
                    "evidence_snippet": {"type": "string"},
                },
                "required": ["kind", "text", "assertion", "evidence_snippet"],
            },
        },
    },
    "required": ["activity_type", "description", "claims"],
}


def vlm_prompt() -> str:
    return (
        "Describe only what is visibly present in this screenshot. "
        "Do not interpret intent, infer unseen facts, or invent UI chrome. "
        f"Return JSON matching this schema: {VLM_SCHEMA}"
    )


def understanding_prompt(frame: Frame) -> str:
    ocr = frame.ocr_text.strip() or "(no OCR sidecar)"
    return (
        "You are WATSON's understanding model. Produce one observation for this "
        "activity segment using only the supplied metadata and OCR text. "
        "Outcomes and achievements require visible evidence of completion. "
        "Tag each claim observed, inferred, or unconfirmed. "
        "Never invent people, projects, or decisions that are not in the text.\n\n"
        f"app_name: {frame.app_name or 'unknown'}\n"
        f"bundle_id: {frame.bundle_id or 'unknown'}\n"
        f"window_title: {frame.window_title or 'unknown'}\n"
        f"dwell_seconds: 45\n"
        f"line_changes: 2\n"
        f"ocr_text:\n{ocr}\n\n"
        f"Return JSON matching this schema: {UNDERSTANDING_SCHEMA}"
    )


def parse_json_object(text: str) -> dict:
    import json

    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        loaded = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        loaded = json.loads(cleaned[start : end + 1])
    if not isinstance(loaded, dict):
        raise ValueError("model output is not a JSON object")
    return loaded
