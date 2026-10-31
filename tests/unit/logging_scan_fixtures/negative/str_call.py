"""Negative: str() of a safe name or a ContentField wrap."""

from watson.privacy.logging import ContentField


def example(log: object, other: str, frame_text: str) -> None:
    log.debug(str(other))
    log.debug(str(ContentField(frame_text)))
