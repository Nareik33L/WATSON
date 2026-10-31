"""Negative: f-string of a safe name or a ContentField wrap."""

from watson.privacy.logging import ContentField


def example(log: object, other: str, merged_text: str) -> None:
    log.info(f"merged {other}")
    log.info(f"merged {ContentField(merged_text)}")
