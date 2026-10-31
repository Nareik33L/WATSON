"""Positive: str.format of a forbidden name."""


def example(log: object, frame_text: str) -> None:
    log.info("extracted {}".format(frame_text))
