"""Negative: tuple / positional args of a safe name or a ContentField wrap."""

from watson.privacy.logging import ContentField


def example(log: object, other: str, frame_text: str) -> None:
    log.info("%s", (other,))
    log.info("%s", (ContentField(frame_text),))
