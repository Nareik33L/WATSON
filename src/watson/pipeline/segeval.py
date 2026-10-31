"""Compare predicted segments to truth.yaml boundaries (M2 / §11.2)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def over_segmentation_rate(
    predicted: Sequence[Sequence[str]],
    truth: Sequence[Sequence[str]],
) -> float:
    """Extra predicted fragments per truth segment.

    A truth segment covered by K predicted segments contributes ``K - 1``
    extra splits. Predicted segments that miss every truth frame count as
    one extra each. ``0.0`` means no over-segmentation.
    """
    if not truth:
        return 0.0 if not predicted else 1.0
    pred_index = _frame_index(predicted)
    extra = 0
    used: set[int] = set()
    for frames in truth:
        overlapping = {pred_index[frame_id] for frame_id in frames if frame_id in pred_index}
        extra += max(0, len(overlapping) - 1)
        used.update(overlapping)
    extra += sum(1 for index in range(len(predicted)) if index not in used)
    return extra / len(truth)


def under_segmentation_rate(
    predicted: Sequence[Sequence[str]],
    truth: Sequence[Sequence[str]],
) -> float:
    """Truth fragments merged into one predicted segment, per predicted row."""
    if not predicted:
        return 0.0 if not truth else 1.0
    truth_index = _frame_index(truth)
    extra = 0
    used: set[int] = set()
    for frames in predicted:
        overlapping = {truth_index[frame_id] for frame_id in frames if frame_id in truth_index}
        extra += max(0, len(overlapping) - 1)
        used.update(overlapping)
    extra += sum(1 for index in range(len(truth)) if index not in used)
    return extra / len(predicted)


def frame_coverage(
    predicted: Sequence[Sequence[str]],
    truth: Sequence[Sequence[str]],
) -> float:
    """Fraction of truth frames that appear in any predicted segment."""
    truth_ids = {frame_id for frames in truth for frame_id in frames}
    if not truth_ids:
        return 1.0
    pred_ids = {frame_id for frames in predicted for frame_id in frames}
    return len(truth_ids & pred_ids) / len(truth_ids)


def score_segmentation(
    predicted: Sequence[Sequence[str]],
    truth: Sequence[Sequence[str]],
) -> dict[str, Any]:
    return {
        "n_truth": len(truth),
        "n_pred": len(predicted),
        "over_segmentation": over_segmentation_rate(predicted, truth),
        "under_segmentation": under_segmentation_rate(predicted, truth),
        "coverage": frame_coverage(predicted, truth),
    }


def _frame_index(segments: Sequence[Sequence[str]]) -> dict[str, int]:
    index: dict[str, int] = {}
    for position, frames in enumerate(segments):
        for frame_id in frames:
            index.setdefault(frame_id, position)
    return index
