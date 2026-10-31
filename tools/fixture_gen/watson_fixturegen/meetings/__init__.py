"""Synthetic meeting fixtures from scripted fictional dialogues (V1.1 §8.1 / A0)."""

from __future__ import annotations

from watson_fixturegen.meetings.render import RenderResult, render_meeting, render_meeting_scripts
from watson_fixturegen.meetings.script import MeetingScript, load_meeting_script

__all__ = [
    "MeetingScript",
    "RenderResult",
    "load_meeting_script",
    "render_meeting",
    "render_meeting_scripts",
]
