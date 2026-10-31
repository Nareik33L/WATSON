"""Positive: + concatenation of a forbidden attribute."""


def example(log: object, seg: object) -> None:
    log.info("body " + seg.merged_text)
