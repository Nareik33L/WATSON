"""Negative: + concatenation of a safe attribute or a ContentField wrap."""

from watson.privacy.logging import ContentField


def example(log: object, seg: object) -> None:
    log.info("body " + seg.app_name)
    log.info("body " + str(ContentField(seg.merged_text)))
