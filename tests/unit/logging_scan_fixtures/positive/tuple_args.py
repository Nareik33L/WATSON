"""Positive: tuple / positional args carrying a forbidden name."""


def example(log: object, frame_text: str) -> None:
    log.info("%s", (frame_text,))
