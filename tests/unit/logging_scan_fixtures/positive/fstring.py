"""Positive: f-string interpolating a forbidden name."""


def example(log: object, merged_text: str) -> None:
    log.info(f"merged {merged_text}")
