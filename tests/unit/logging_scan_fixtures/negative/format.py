"""Negative: format() of a safe name or a ContentField wrap."""

from watson.privacy.logging import ContentField


def example(log: object, other: str, frame_text: str) -> None:
    log.info("extracted {}".format(other))
    log.info("extracted {}".format(ContentField(frame_text)))
