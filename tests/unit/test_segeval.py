from watson.pipeline.segeval import (
    frame_coverage,
    over_segmentation_rate,
    score_segmentation,
    under_segmentation_rate,
)


def test_perfect_match_is_zero() -> None:
    truth = [["a", "b"], ["c"]]
    assert over_segmentation_rate(truth, truth) == 0.0
    assert under_segmentation_rate(truth, truth) == 0.0
    assert frame_coverage(truth, truth) == 1.0


def test_extra_split_counts_as_over_segmentation() -> None:
    truth = [["a", "b", "c"]]
    pred = [["a"], ["b", "c"]]
    assert over_segmentation_rate(pred, truth) == 1.0
    score = score_segmentation(pred, truth)
    assert score["n_pred"] == 2
    assert score["n_truth"] == 1


def test_merge_counts_as_under_segmentation() -> None:
    truth = [["a"], ["b"]]
    pred = [["a", "b"]]
    assert over_segmentation_rate(pred, truth) == 0.0
    assert under_segmentation_rate(pred, truth) == 1.0
