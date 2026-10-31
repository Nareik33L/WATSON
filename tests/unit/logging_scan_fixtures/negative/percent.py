"""Negative: % formatting of a safe name or a ContentField wrap."""

from watson.privacy.logging import ContentField


def example(log: object, other: str, snippet: str) -> None:
    log.info("snip %s" % other)
    log.info("snip %s" % ContentField(snippet))
