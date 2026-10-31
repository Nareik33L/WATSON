"""Negative: subscript of a safe key, or ContentField-wrapped forbidden key."""

from watson.privacy.logging import ContentField


def example(log: object, row: dict[str, str]) -> None:
    log.info(row["id"])
    log.info(ContentField(row["frame_text"]))
