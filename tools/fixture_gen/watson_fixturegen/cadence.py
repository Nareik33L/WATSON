"""Expand a scenario timeline into a capture-cadence frame schedule (plan §3.6)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from watson_fixturegen.scenario import (
    ContentState,
    Scenario,
    TimelineBlock,
    WindowSpec,
    content_corpus,
)


@dataclass(frozen=True)
class ScheduledFrame:
    index: int
    frame_id: str
    captured_at: datetime
    window: WindowSpec
    content: dict[str, Any]

    @property
    def corpus(self) -> str:
        parts = [
            self.window.app_name,
            self.window.window_title,
            self.window.url or "",
            content_corpus(self.content),
        ]
        return "\n".join(parts)


def fixture_namespace(name: str, seed: int) -> uuid.UUID:
    """UUIDv5 namespace derived from the fixture name and seed."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"watson.fixture:{name}:{seed}")


def frame_id_for(name: str, seed: int, index: int) -> str:
    """Deterministic lowercase UUIDv5: namespace(name, seed), name=frame index."""
    return str(uuid.uuid5(fixture_namespace(name, seed), str(index)))


# Excluded-app frames use a parallel index so work-frame UUIDs stay stable
# when a helper-failed-to-filter window is inserted into the timeline.
EXCLUDED_INDEX_BASE = 10_001


def expand_schedule(scenario: Scenario) -> list[ScheduledFrame]:
    frames: list[ScheduledFrame] = []
    blocks = scenario.timeline
    cadence = scenario.cadence
    moment = blocks[0].start
    previous_key: str | None = None
    stable = 0
    index = 1
    excluded_index = EXCLUDED_INDEX_BASE
    emitted_excluded: set[tuple[str, datetime]] = set()
    steps = 0

    while True:
        block = _block_at(blocks, moment)
        if block is None:
            nxt = _next_start(blocks, moment)
            if nxt is None:
                break
            moment = nxt
            continue

        if block.window.excluded:
            marker = (block.window.key, block.start)
            if marker not in emitted_excluded:
                extra, excluded_index = _schedule_excluded_block(scenario, block, excluded_index)
                frames.extend(extra)
                emitted_excluded.add(marker)
            moment = block.end
            previous_key = None
            stable = 0
            continue

        content = _content_at(block, moment)
        frames.append(
            ScheduledFrame(
                index=index,
                frame_id=frame_id_for(scenario.name, scenario.seed, index),
                captured_at=moment,
                window=block.window,
                content=content,
            )
        )
        if previous_key != block.window.key:
            interval = cadence.tighten_seconds
            stable = 0
        else:
            stable += 1
            if stable >= cadence.stretch_after_stable:
                interval = cadence.stretch_seconds
            else:
                interval = cadence.default_seconds
        previous_key = block.window.key
        moment = moment + timedelta(seconds=interval)
        index += 1
        steps += 1
        if steps > 10_000:
            raise RuntimeError("schedule exceeded 10000 frames; check timeline/cadence")

    frames.sort(key=lambda frame: (frame.captured_at, frame.index))
    return frames


def _schedule_excluded_block(
    scenario: Scenario,
    block: TimelineBlock,
    start_index: int,
) -> tuple[list[ScheduledFrame], int]:
    """Emit frames for an excluded window without advancing the work index."""
    frames: list[ScheduledFrame] = []
    cadence = scenario.cadence
    moment = block.start
    stable = 0
    index = start_index
    first = True
    while moment < block.end:
        frames.append(
            ScheduledFrame(
                index=index,
                frame_id=frame_id_for(scenario.name, scenario.seed, index),
                captured_at=moment,
                window=block.window,
                content=_content_at(block, moment),
            )
        )
        if first:
            interval = cadence.tighten_seconds
            first = False
            stable = 0
        else:
            stable += 1
            if stable >= cadence.stretch_after_stable:
                interval = cadence.stretch_seconds
            else:
                interval = cadence.default_seconds
        moment = moment + timedelta(seconds=interval)
        index += 1
        if index - start_index > 1_000:
            raise RuntimeError("excluded block produced too many frames")
    return frames, index


def normalize_title(title: str) -> str:
    return " ".join(title.casefold().split())


def _block_at(blocks: tuple[TimelineBlock, ...], moment: datetime) -> TimelineBlock | None:
    for block in blocks:
        if block.start <= moment < block.end:
            return block
    return None


def _next_start(blocks: tuple[TimelineBlock, ...], moment: datetime) -> datetime | None:
    starts = [block.start for block in blocks if block.start > moment]
    return min(starts) if starts else None


def _content_at(block: TimelineBlock, moment: datetime) -> dict[str, Any]:
    chosen: ContentState = block.states[0]
    for state in block.states:
        if state.at <= moment:
            chosen = state
        else:
            break
    return chosen.content
